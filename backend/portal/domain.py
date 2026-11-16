import calendar
import datetime as dt
import re
import unicodedata
from zoneinfo import ZoneInfo

MANAGED_ROLES = {'HR', 'ADMIN'}

def normalize(value):
    value = unicodedata.normalize('NFKC', str(value or '')).lower()
    value = re.sub('[‘’ʻʼ`´]', "'", value)
    return ' '.join(value.replace("o'", 'ö').replace("g'", 'ğ').replace('sh', 'ş').replace('ch', 'ç').split())

def search_fold(value):
    # A search alias only: original extracted text and downloaded bytes stay unchanged.
    # Ø/⌀ (diameter on drawings) fold to o together with the Uzbek ö, so a query typed
    # either way finds text that OCR rendered as Ø, ⌀, O or Ö.
    return normalize(value).translate(str.maketrans({'ö':'o','ğ':'g','ş':'s','ç':'c','ў':'у','қ':'к','ғ':'г','ҳ':'х','ø':'o','⌀':'o'}))

def initials(value):
    return ''.join(x[0] for x in str(value).split()[:2]).upper()

def search_date(value, privileged=False):
    value = str(value)
    for pattern, format_ in [(r'\d{1,2}[./]\d{1,2}', '%d.%m')]:
        if re.fullmatch(pattern, value):
            try:
                d = dt.datetime.strptime(value.replace('/', '.') + '.2000', '%d.%m.%Y').date()
                return {'day': d.day, 'month': d.month}
            except ValueError:
                return None
    if privileged:
        for format_ in ['%Y-%m-%d', '%d.%m.%Y']:
            try:
                d = dt.datetime.strptime(value, format_).date()
                return {'day': d.day, 'month': d.month, 'year': d.year}
            except ValueError:
                pass
    return None

def upcoming_birthday(birthday, now=None):
    today = (now or dt.datetime.now(ZoneInfo('Asia/Tashkent'))).astimezone(ZoneInfo('Asia/Tashkent')).date()
    def occurrence(year):
        # The displayed date stays 29 February; sorting uses 1 March in non-leap years.
        if birthday.month == 2 and birthday.day == 29 and not calendar.isleap(year):
            return dt.date(year, 3, 1)
        return birthday.replace(year=year)
    target = occurrence(today.year)
    if target < today:
        target = occurrence(today.year + 1)
    return {'day': birthday.day, 'month': birthday.month, 'days': (target - today).days}

FORMULA_START = r'^\s*[=+@\-\t\r]'
PLAIN_PHONE = r'^\+\d[\d ()\-]*$'

def spreadsheet_safe(value):
    value = str(value or '')
    # Plain phone numbers contain no formula syntax and must survive export -> import unchanged.
    return "'" + value if re.match(FORMULA_START, value) and not re.fullmatch(PLAIN_PHONE, value) else value

def spreadsheet_restore(value):
    # Inverse of the neutralising apostrophe (also for files exported before phones were exempted).
    return value[1:] if value.startswith("'") and re.match(FORMULA_START, value[1:]) else value
