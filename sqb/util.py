"""Shared helpers: errors, money (integer cents), dates."""
import calendar
import datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

MAX_AMOUNT = Decimal('99999999999.99')   # per-amount ceiling (dollars)
MAX_ID = 2 ** 53
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def to_cents(v, field='amount'):
    """Parse a dollar amount (number or string) into integer cents."""
    if v is None or v == '':
        return 0
    if isinstance(v, bool):
        raise ApiError(400, '%s: invalid amount' % field)
    if not isinstance(v, (str, int, float)):
        raise ApiError(400, '%s: invalid amount' % field)
    try:
        if isinstance(v, str):
            v = v.replace(',', '').replace('$', '').strip()
            if v.startswith('(') and v.endswith(')'):
                v = '-' + v[1:-1]
        d = Decimal(str(v))
        if not d.is_finite() or abs(d) > MAX_AMOUNT:
            raise ApiError(400, '%s: invalid amount or amount too large' % field)
        return int((d * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        raise ApiError(400, '%s: invalid amount' % field)


def money(c):
    return None if c is None else round(c / 100.0, 2)


def parse_date(s, field='date', required=True):
    if s is None or s == '':
        if required:
            raise ApiError(400, '%s is required' % field)
        return None
    try:
        if not isinstance(s, str) or len(s.strip()) != 10:
            raise ValueError
        dt = datetime.date.fromisoformat(s.strip())
    except ValueError:
        raise ApiError(400, '%s: invalid date (use YYYY-MM-DD)' % field)
    if not 1900 <= dt.year <= 2999:
        raise ApiError(400, '%s: year must be between 1900 and 2999' % field)
    return dt.isoformat()


def D(s):
    return datetime.date.fromisoformat(s)


def today():
    return datetime.date.today().isoformat()


def month_end(dt):
    return dt.replace(day=calendar.monthrange(dt.year, dt.month)[1])


def add_months(dt, n):
    m = dt.month - 1 + n
    y = dt.year + m // 12
    m = m % 12 + 1
    return datetime.date(y, m, min(dt.day, calendar.monthrange(y, m)[1]))


def add_days(s, n):
    return (D(s) + datetime.timedelta(days=n)).isoformat()


def fmt_day(dt):
    return '%s %d, %d' % (MONTHS[dt.month - 1], dt.day, dt.year)


def range_label(s, e):
    """Human label for an ISO date range."""
    s, e = D(s), D(e)
    if s.day == 1 and e == month_end(e):
        if (s.year, s.month) == (e.year, e.month):
            return '%s %d' % (MONTHS[s.month - 1], s.year)
        if s.year == e.year and s.month == 1 and e.month == 12:
            return str(s.year)
        if s.year == e.year:
            return '%s - %s %d' % (MONTHS[s.month - 1], MONTHS[e.month - 1], s.year)
        return '%s %d - %s %d' % (MONTHS[s.month - 1], s.year, MONTHS[e.month - 1], e.year)
    if s == e:
        return fmt_day(s)
    return '%s - %s' % (fmt_day(s), fmt_day(e))


def as_int(v, field, required=True):
    if v is None or v == '':
        if required:
            raise ApiError(400, '%s is required' % field)
        return None
    try:
        if isinstance(v, bool) or isinstance(v, (list, dict)):
            raise ValueError
        if isinstance(v, float) and v != int(v):
            raise ValueError
        n = int(v)
    except (TypeError, ValueError, OverflowError):
        raise ApiError(400, '%s: invalid value' % field)
    if abs(n) > MAX_ID:
        raise ApiError(400, '%s: invalid value' % field)
    return n


def as_str(v, field, default=''):
    """Text field from JSON: None -> default, numbers -> text, lists/objects rejected."""
    if v is None:
        return default
    if isinstance(v, bool) or isinstance(v, (list, dict)):
        raise ApiError(400, '%s: text expected' % field)
    return str(v).strip()


def as_bool(v, field):
    if v in (True, 1, '1', 'true', 'True'):
        return True
    if v in (False, 0, '0', 'false', 'False'):
        return False
    raise ApiError(400, '%s must be true or false' % field)


def as_list(v, field, of=None):
    if not isinstance(v, list) or (of and not all(isinstance(x, of) for x in v)):
        raise ApiError(400, '%s: a list is required' % field)
    return v
