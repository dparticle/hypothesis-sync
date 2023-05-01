#!/usr/local/bin/python3
import os.path
import traceback
import json
import random
import re
from enum import Enum
from utils import get_format_datetime_beijing, escape_filename

import requests
import time

# config
USER = '<hypothesis_username>'  # hypothesis username
TOKEN = '<hypothesis_token>'  # hypothesis token
BACKUP_DIR = '<backup_dictionary>'  # dictionary of backup
SYNC_INFO_FILE = BACKUP_DIR + "sync_info.json"
MD_TEMPLATE = '''---
文章标题: {title}
原文链接: <{url}>
分组 ID：{group}
创建时间: {created}
更新时间: {updated}
---

# 文章笔记
{page_notes}
# 高亮标注
{annotations}'''  # notice ``` newline problem
PAGE_NOTE_TEMPLATE = '''
- 笔记：{mark}

  标签：{tags}
'''
ANNOTATION_TEMPLATE = '''
- 高亮文本：{text}
  
  笔记：{mark}
  
  标签：{tags}
'''
groups = {}


class ANNOTATION_TYPE(Enum):
    PAGE = 1
    ANNOTATION = 2


# get annotations by hypothesis api, retry up to 10 times because of the instability of api
def get_annotations(newest_timestamp=None, url='', limit=200):
    params = {
        'limit': limit,
        'sort': 'updated',
        'order': 'asc',
        'user': USER,
    }
    if newest_timestamp:
        params.setdefault('search_after', newest_timestamp)  # > updated
    if url:
        params.setdefault('url', url)

    for i in range(10):
        try:
            response = requests.get(
                url='https://api.hypothes.is/api/search',
                params=params,
                headers={
                    'Authorization': "Bearer " + TOKEN,
                    'Content-Type': 'application/json;charset=utf-8',
                },
            )
            return response
        except requests.exceptions.RequestException:
            print('retry getting annotations')
            time.sleep(random.randint(2, 9))

    print('get annotations failure')


def get_groups():
    for i in range(10):
        try:
            response = requests.get(
                url='https://api.hypothes.is/api/groups',
                headers={
                    'Authorization': "Bearer " + TOKEN,
                    'Content-Type': 'application/json;charset=utf-8',
                },
            )
            return response
        except requests.exceptions.RequestException:
            print('retry getting groups')
            time.sleep(random.randint(2, 9))

    print('get groups failure')


def parse_annotation(annotation):
    annotation_text = ''
    annotation_offset = '0'
    if 'selector' not in annotation['target'][0]:
        annotation_type = ANNOTATION_TYPE.PAGE
    else:
        annotation_type = ANNOTATION_TYPE.ANNOTATION
        for selector in annotation['target'][0]['selector']:
            if selector['type'] == 'TextQuoteSelector':
                # context
                annotation_text = str(selector['prefix']).strip() + "**" + str(selector['exact']) + "**" + \
                                  str(selector['suffix']).strip()
            if selector['type'] == 'TextPositionSelector':
                annotation_offset = str(selector['start'])

    tags = ''
    for tag in annotation['tags']:
        tags += '#%s ' % tag

    return {
        'id': annotation['id'],
        'created': annotation['created'],
        'updated': annotation['updated'],
        'title': annotation['document']['title'][0],
        'url': annotation['uri'],
        'group': annotation['group'],
        'mark': re.sub(r'\n|\t', ' ', annotation['text'].strip()),
        'tags': tags.strip(),
        'type': annotation_type,
        'text': re.sub(r'\n|\t', ' ', annotation_text.strip()),
        'offset': annotation_offset
    }


def get_page_hls_markdown(url='', newest_timestamp=None, total=0):
    # judge this page whether to delete the previous annotations
    if newest_timestamp is not None:
        page_hls_json = json.loads(get_annotations(newest_timestamp, url, 1).text)
        if len(page_hls_json['rows']) == 0 and page_hls_json['total'] == total:
            # print('[INFO] no changed in %s page' % url)
            return None, False
        # print('[INFO] changed in %s page' % url)
        newest_timestamp = None  # reset

    hls = []
    while True:
        page_hls_json = json.loads(get_annotations(newest_timestamp, url).text)
        if len(page_hls_json['rows']) == 0:
            total = page_hls_json['total']
            break
        newest_timestamp = page_hls_json['rows'][-1]['updated']
        for row in page_hls_json['rows']:
            hls.append(parse_annotation(row))

    # without annotations
    if len(hls) == 0:
        return {'total': 0}, False

    # sort by position
    hls = sorted(hls, key=lambda hl: hl['offset'].rjust(10, '0'))

    createds = []
    updateds = []
    page_notes = ''
    annotations = ''
    for hl in hls:
        createds.append(hl['created'])
        updateds.append(hl['updated'])
        if hl['type'] == ANNOTATION_TYPE.PAGE:
            page_notes += PAGE_NOTE_TEMPLATE.format(mark=hl['mark'], tags=hl['tags'])
        else:
            annotations += ANNOTATION_TEMPLATE.format(text=hl['text'], mark=hl['mark'], tags=hl['tags'])
    created = min(createds)
    updated = max(updateds)

    return {
        'title': hls[0]['title'],
        'url': hls[0]['url'],
        'created': created,
        'updated': updated,
        'total': total,
        'content': MD_TEMPLATE.format(title=hls[0]['title'],
                                      url=hls[0]['url'],
                                      group=groups[hls[0]['group']],
                                      created=get_format_datetime_beijing(created),
                                      updated=get_format_datetime_beijing(updated),
                                      page_notes=page_notes,
                                      annotations=annotations)
    }, True


def sync(sync_info):
    urls = []
    newest_timestamp = sync_info['timestamp']
    details = sync_info['details']

    # get all sync urls
    while True:
        annotations_json = json.loads(get_annotations(newest_timestamp=newest_timestamp).text)
        if len(annotations_json['rows']) == 0:
            # append to urls, because it is impossible to judge when delete and add
            urls.extend(details.keys())
            total = annotations_json['total']
            break
        newest_timestamp = annotations_json['rows'][-1]['updated']
        for annotation in annotations_json['rows']:
            urls.append(annotation['uri'])
    urls = list(set(urls))  # deduplicate

    # sync every page
    for url in urls:
        if url in details:
            page_hls, is_changed = get_page_hls_markdown(url, details[url]['timestamp'], details[url]['total'])
        else:
            page_hls, is_changed = get_page_hls_markdown(url=url)

        if is_changed:
            # write file
            file_name = page_hls['created'][2:10].replace('-', '') + '_' + escape_filename(page_hls['title']) + '.md'
            with open(BACKUP_DIR + file_name, 'w+') as f:
                f.write(page_hls['content'])
            details[url] = {
                'timestamp': page_hls['updated'],
                'filename': file_name,
                'total': page_hls['total']
            }
            print('[INFO] %s page sync complete' % url)
        elif page_hls is not None and page_hls['total'] == 0:
            # delete file
            print('[INFO] %s page had been deleted' % url)
            os.remove(BACKUP_DIR + details[url]['filename'])
            del details[url]

    return generate_sync_info(newest_timestamp, total, details)


def generate_sync_info(timestamp=None, total=0, details=None):
    if details is None:
        details = {}
    return {'timestamp': timestamp, 'total': total, 'details': details}


if __name__ == '__main__':
    # load the latest sync info
    sync_info = generate_sync_info()
    if os.path.exists(SYNC_INFO_FILE):
        sync_info_file = open(SYNC_INFO_FILE, 'r+')
        sync_info = json.loads(sync_info_file.read())

    try:
        # get group dict, key is group id, value is group name and links
        groups_json = json.loads(get_groups().text)
        for group in groups_json:
            groups[group['id']] = '[%s](%s)' % (group['name'], group['links']['html'])

        # sync, return sync info to save
        new_sync_info = sync(sync_info)
        with open(SYNC_INFO_FILE, 'w+') as f:
            f.write(json.dumps(new_sync_info))
    except Exception as e:
        traceback.print_exc()
