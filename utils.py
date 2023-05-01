import re
from datetime import datetime
from zoneinfo import ZoneInfo


def get_format_datetime_beijing(time):
    return datetime.fromisoformat(time).astimezone(ZoneInfo("Asia/Shanghai")).strftime('%Y-%m-%d %H:%M:%S')


# replace illegal character in filename
def escape_filename(filename):
    # filename = re.sub('[\/:*?"<>|]', ' ', filename)
    filename = re.sub('[\/"]', '_', filename)
    return filename
