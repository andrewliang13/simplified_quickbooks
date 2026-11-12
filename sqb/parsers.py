"""Statement parsers: CSV, OFX/QFX/QBO, and best-effort PDF.

Every parser returns a list of {'date': 'YYYY-MM-DD', 'description': str, 'amount': cents, 'fitid': str|None}
where amount > 0 is money into the account (deposit / card payment or refund) and
amount < 0 is money out (withdrawal / card charge).
"""
import csv
import datetime
import html
import io
import re
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from .util import ApiError

DATE_FORMATS = ['%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%m-%d-%Y', '%m-%d-%y', '%Y/%m/%d', '%d-%b-%Y', '%d-%b-%y',
                '%b %d, %Y', '%B %d, %Y', '%b %d %Y', '%B %d %Y', '%d %b %Y', '%d %B %Y', '%Y%m%d', '%m.%d.%Y']


def parse_any_date(s, default_year=None):
    s = (s or '').strip().strip('"')
    if not s:
        return None
    s = re.sub(r'[T ]\d{1,2}:\d{2}.*$', '', s).strip()
    for f in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(s, f).date().isoformat()
        except (ValueError, OverflowError):
            pass
    m = re.fullmatch(r'(\d{1,2})[/-](\d{1,2})', s)
    if m and default_year:
        try:
            return datetime.date(int(default_year), int(m.group(1)), int(m.group(2))).isoformat()
        except (ValueError, OverflowError):
            return None
    return None


def parse_amount(s):
    """'$1,234.56', '(12.00)', '-5', '5.00-', '12.00 CR', '12.00 DR' -> cents (None if not a number)."""
    u = (s or '').strip().upper()
    if not u:
        return None
    neg = False
    if u.endswith('CR'):
        u = u[:-2]
    elif u.endswith('DR'):
        u, neg = u[:-2], True
    u = u.replace('USD', '').replace('$', '').replace(',', '').replace(' ', '')
    if u.startswith('(') and u.endswith(')'):
        u, neg = u[1:-1], not neg
    if u.endswith('-'):
        u, neg = u[:-1], not neg
    if u.startswith('-'):
        u, neg = u[1:], not neg
    elif u.startswith('+'):
        u = u[1:]
    if not re.fullmatch(r'\d+\.?\d*|\.\d+', u):
        return None
    try:
        c = int((Decimal(u) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None
    return -c if neg else c


# ---------------------------------------------------------------- CSV

_COLS = {
    'date': (['posting date', 'post date', 'posted date', 'date posted', 'date', 'transaction date', 'trans date',
              'trans. date'], ['post', 'date']),
    'description': (['description', 'transaction description', 'payee', 'merchant', 'name', 'memo', 'details',
                     'narrative'], ['desc', 'payee', 'merchant', 'memo', 'narrat', 'name', 'detail']),
    'amount': (['amount', 'transaction amount', 'amount (usd)', 'amt'], ['amount']),
    'debit': (['debit', 'debits', 'withdrawal', 'withdrawals', 'debit amount', 'withdrawal amount', 'money out',
               'paid out', 'charge', 'charges'], ['debit', 'withdraw', 'paid out', 'money out']),
    'credit': (['credit', 'credits', 'deposit', 'deposits', 'credit amount', 'deposit amount', 'money in',
                'paid in', 'payment', 'payments'], ['credit', 'deposit', 'paid in', 'money in']),
    'type': (['debit/credit', 'credit/debit', 'dr/cr', 'cr/dr', 'credit or debit', 'debit or credit',
              'transaction type', 'type', 'details'], []),
    'fitid': (['fitid', 'transaction id', 'reference number', 'reference', 'ref', 'id'], []),
}


def _find(header, key, taken):
    exact, contains = _COLS[key]
    for name in exact:
        for i, h in enumerate(header):
            if h == name and i not in taken:
                return i
    for frag in contains:
        for i, h in enumerate(header):
            if frag in h and i not in taken and 'balance' not in h:
                return i
    return None


def parse_csv(text):
    text = text.lstrip('﻿')
    sample = [l for l in text.splitlines() if l.strip()][:8]
    if not sample:
        raise ApiError(400, 'The file is empty')
    delim = max([',', '\t', ';', '|'], key=lambda d: sum(l.count(d) for l in sample))
    try:
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]
    except csv.Error:
        raise ApiError(400, 'This file could not be read as CSV')
    rows = [r for r in rows if any(r)]
    warnings = []

    hdr_idx = None
    for i, r in enumerate(rows[:40]):
        low = [c.lower() for c in r]
        if any('date' in c for c in low) and any(
                k in c for c in low for k in ('amount', 'debit', 'credit', 'withdraw', 'deposit', 'paid')):
            hdr_idx = i
            break

    col = {}
    if hdr_idx is not None:
        header = [c.lower().strip() for c in rows[hdr_idx]]
        taken = set()
        for key in ('date', 'amount', 'type', 'debit', 'credit', 'description', 'fitid'):
            idx = _find(header, key, taken)
            if idx is not None:
                col[key] = idx
                taken.add(idx)
        if 'description' not in col and 'type' in col:
            col['description'] = col.pop('type')
        data = rows[hdr_idx + 1:]
    else:
        # No header row: sniff columns from the first data row that has a date and an amount.
        data = rows
        for r in rows[:10]:
            d_idx = next((i for i, c in enumerate(r) if parse_any_date(c)), None)
            if d_idx is None:
                continue
            a_idx = next((i for i, c in enumerate(r) if i != d_idx and parse_amount(c) is not None
                          and re.search(r'\d', c)), None)
            if a_idx is None:
                continue
            texts = [(len(c), i) for i, c in enumerate(r) if i not in (d_idx, a_idx) and parse_amount(c) is None]
            col = {'date': d_idx, 'amount': a_idx}
            if texts:
                col['description'] = max(texts)[1]
            break
    if 'date' not in col or not ('amount' in col or 'debit' in col or 'credit' in col):
        raise ApiError(400, 'Could not find Date and Amount columns in this CSV file')

    def cell(r, key):
        i = col.get(key)
        return r[i] if i is not None and i < len(r) else ''

    out, skipped = [], 0
    for r in data:
        date = parse_any_date(cell(r, 'date'))
        if not date:
            skipped += 1
            continue
        if 'debit' in col or 'credit' in col:
            dr, cr = parse_amount(cell(r, 'debit')), parse_amount(cell(r, 'credit'))
            if dr is None and cr is None:
                amt = parse_amount(cell(r, 'amount')) if 'amount' in col else None
            else:
                amt = abs(cr or 0) - abs(dr or 0)
        else:
            amt = parse_amount(cell(r, 'amount'))
        if amt is None:
            skipped += 1
            continue
        out.append({'date': date, 'description': re.sub(r'\s+', ' ', cell(r, 'description')).strip(),
                    'amount': amt, 'fitid': cell(r, 'fitid') or None, '_type': cell(r, 'type').lower()})
    # Unsigned amounts with a Debit/Credit indicator column.
    # (Only an explicit debit/withdrawal marker flips a row; words like "payment" or "sale" are ambiguous.)
    if 'amount' in col and 'type' in col and out and all(o['amount'] >= 0 for o in out):
        for o in out:
            if set(re.findall(r'[a-z]+', o['_type'])) & {'debit', 'dr', 'withdrawal', 'withdraw'}:
                o['amount'] = -o['amount']
    for o in out:
        del o['_type']
    if skipped:
        warnings.append('%d row(s) skipped (no valid date or amount)' % skipped)
    return out, warnings


# ---------------------------------------------------------------- OFX / QFX / QBO

def parse_ofx(text):
    out = []

    def tag(block, name):
        m = re.search(r'<%s>([^<\r\n]*)' % name, block, re.I)
        return html.unescape(m.group(1).strip()) if m else ''

    for block in re.findall(r'<STMTTRN>(.*?)(?=<STMTTRN>|</STMTTRN>|</BANKTRANLIST>|\Z)', text, re.S | re.I):
        amt = parse_amount(tag(block, 'TRNAMT'))
        date = parse_any_date(tag(block, 'DTPOSTED')[:8])
        if amt is None or not date:
            continue
        name, memo = tag(block, 'NAME'), tag(block, 'MEMO')
        desc = name if not memo or memo == name else ('%s %s' % (name, memo)).strip()
        out.append({'date': date, 'description': re.sub(r'\s+', ' ', desc), 'amount': amt,
                    'fitid': tag(block, 'FITID') or None})
    if not out:
        raise ApiError(400, 'No transactions found in this OFX/QFX file')
    return out, []


# ---------------------------------------------------------------- PDF statement text -> rows

_MON = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?'
_D = r'(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\*?|' + _MON + r'\s+\d{1,2}\*?)'   # 12/05, 12/05/25*, Dec 5
_AMT = r'(?:-\s?\$?|\$\s?-?|\(\$?|\+\s?\$?)?[\d,]*\d?\.\d{2}\)?-?(?:\s?CR)?'
_PDF_LINE = re.compile(
    r'^\s*(' + _D + r')\s+'                    # transaction date
    r'(?:' + _D + r'\s+)?'                      # optional posting date
    r'(.+?)\s+'                                 # description
    r'(' + _AMT + r')'                          # amount
    r'(?:\s+(-?\$?[\d,]*\.\d{2}-?))?\s*$', re.I)  # optional running balance
_TWO = re.compile(r'^\s*(' + _D + r'\s+\S.*?\s+' + _AMT + r')\s+(' + _D + r'\s+\S.*?\s+' + _AMT + r')\s*$', re.I)
_CHECK = re.compile(r'^\s*(\d{3,7})\s*[\^*]?\s+(' + _D + r')\s+(' + _AMT + r')\s*$', re.I)
_POS = ('deposit', 'credit', 'addition', 'interest paid', 'interest earned', 'refund')
_NEG = ('withdrawal', 'debit', 'check', 'subtraction', 'purchase', 'fee', 'charge', 'payment', 'transactions')
# Section headings start with one of these (so a wrapped description such as "Security deposit" is not one)
_HEADINGS = re.compile(
    r'^(deposits?|other credits?|credits?|additions?|electronic deposits?|atm (and|&) debit card (deposits?|withdrawals?)|'
    r'payments?(,| and| &)? (other )?credits?|withdrawals?|other debits?|'
    r'electronic withdrawals?|other withdrawals?|checks?|checks paid|card purchases?|purchases?|new charges?|charges?|'
    r'fees?|service fees?|interest charged|transactions?|subtractions?|atm|debit card|cash advances?|other charges?)\b')
_BAL = re.compile(r'(?:beginning|previous|opening|starting)\s+balance.*?(-?\$?[\d,]+\.\d{2})', re.I)
_FULL_DATE = r'(\d{1,2}/\d{1,2}/\d{2,4}|' + _MON + r'\s+\d{1,2},?\s+\d{4})'


def _statement_end(text):
    """End date of the statement period, from 'X - Y', 'X through Y', 'X to Y' or 'Closing Date X'."""
    head = '\n'.join(text.splitlines()[:40])
    for m in re.finditer(_FULL_DATE + r'\s*(?:-|–|through|thru|to)\s*' + _FULL_DATE, head, re.I):
        d = parse_any_date(_norm_month(m.group(2)))
        if d:
            return d
    m = re.search(r'(?:closing|statement|period ending|ending)\s+date:?\s*' + _FULL_DATE, head, re.I)
    if m:
        return parse_any_date(_norm_month(m.group(1)))
    return None


def _norm_month(s):
    return re.sub(r'\b(Sept)\b', 'Sep', re.sub(r'(?<=[A-Za-z])\.', '', s.strip()))


def _short_date(s, end, default_year):
    """A date without a year (12/05, Dec 5): the year of the statement end, or the year before for months
    after the end month (a December line on a statement that ends in January)."""
    s = _norm_month(s.rstrip('*'))
    full = parse_any_date(s.replace('-', '/'))
    if full and re.search(r'\d{2,4}$', s) and s.replace('-', '/').count('/') == 2:
        return full
    if default_year:
        year = int(default_year)
        return parse_any_date(s.replace('-', '/'), year) or parse_any_date('%s %d' % (s, year))
    if end:
        ey, em = int(end[:4]), int(end[5:7])
    else:
        t = datetime.date.today()
        ey, em = t.year, t.month
    d = parse_any_date(s.replace('-', '/'), ey) or parse_any_date('%s %d' % (s, ey))
    if d and int(d[5:7]) > em:
        d = parse_any_date(s.replace('-', '/'), ey - 1) or parse_any_date('%s %d' % (s, ey - 1))
    return d


def parse_pdf_text(text, is_cc=False, default_year=None):
    """Pull dated lines ending in an amount out of statement text (best effort).
    Sign comes from, in order: the running-balance column (also when a bank prints the balance only on the
    last line of each day), a 'CR' marker, the section heading ('Deposits and other credits' /
    'Withdrawals'), then the printed sign."""
    end = None if default_year else _statement_end(text)
    out, section, skip, prev_bal, one_table = [], 0, False, None, False
    pending = []                       # rows whose sign is settled by the next running balance

    def settle(bal):
        nonlocal prev_bal
        if prev_bal is not None and pending:
            want = bal - prev_bal
            hits = [signs for signs in _sign_options(len(pending))
                    if sum(s * abs(r['amount']) for s, r in zip(signs, pending)) == want * (-1 if is_cc else 1)]
            if len(hits) == 1:
                for s, r in zip(hits[0], pending):
                    r['amount'] = s * abs(r['amount'])
        pending.clear()
        prev_bal = bal

    def add(date_s, desc, amt_s, bal_s):
        date = _short_date(date_s, end, default_year)
        amt = parse_amount(amt_s.replace(' ', ''))
        if not date or amt is None:
            return
        bal = parse_amount(bal_s) if bal_s else None
        if amt_s.upper().rstrip().endswith('CR'):
            amt = abs(amt)
        elif section:
            amt = section * abs(amt)
        elif one_table and not is_cc:
            amt = -abs(amt)            # until a running balance says otherwise
        elif is_cc:
            amt = -amt                 # card statements list charges as positive numbers
        row = {'date': date, 'description': re.sub(r'\s+', ' ', desc).strip(), 'amount': amt, 'fitid': None}
        out.append(row)
        if not section and not amt_s.upper().rstrip().endswith('CR'):
            pending.append(row)
        if bal is not None:
            settle(bal)

    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        bm = _BAL.search(line)
        if bm and not _PDF_LINE.match(line):
            prev_bal = parse_amount(bm.group(1))
            pending.clear()
            continue
        two = _TWO.match(line)
        m = _PDF_LINE.match(line)
        ck = _CHECK.match(line)
        if not m and not ck:
            if 'deposit' in low and 'withdrawal' in low and len(low) < 140 and not re.search(r'[\d$]', low):
                skip, one_table, section = False, True, 0      # one table with both columns
                continue
            if len(low) < 70 and not re.search(r'[\d:$]', low) and not low.startswith('total'):
                if 'summary' in low or ('balance' in low and ('daily' in low or 'ending' in low or 'ledger' in low)):
                    skip = True        # summaries and daily balance tables are not transactions
                    continue
                if _HEADINGS.match(low):
                    pos, neg = any(w in low for w in _POS), any(w in low for w in _NEG)
                    skip, one_table = False, False
                    section = 1 if pos else -1 if neg else 0
            continue
        if skip:
            continue
        if ck and not m:
            add(ck.group(2), 'Check ' + ck.group(1), ck.group(3), None)
            continue
        if two:
            for half in (two.group(1), two.group(2)):
                h = _PDF_LINE.match(half)
                if h:
                    add(h.group(1), h.group(2), h.group(3), None)
            continue
        if re.match(r'^-?\$?[\d,]*\.\d{2}\s+' + _D, m.group(2)):
            continue
        add(m.group(1), m.group(2), m.group(3), m.group(4))
    return out


def _sign_options(n):
    if n > 12:
        return []
    return [[1 if (k >> i) & 1 else -1 for i in range(n)] for k in range(2 ** n)]


def pdf_to_text(data):
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return '\n'.join((p.extract_text() or '') for p in pdf.pages)
    except ImportError:
        pass
    from . import pdftext       # built in: no extra package needed
    text = pdftext.extract_text(data)
    if not text.strip():
        raise ApiError(400, 'This PDF has no readable text (it may be a scanned image). '
                            'Download the statement as CSV or QFX/OFX instead.')
    return text


def parse_statement(data, filename='', is_cc=False, year=None):
    """data: bytes. Returns (rows, meta)."""
    name = filename.lower() if isinstance(filename, str) else ''
    if data[:5] == b'%PDF-' or name.endswith('.pdf'):
        rows = parse_pdf_text(pdf_to_text(data), is_cc, year)
        if not rows:
            raise ApiError(400, 'No transactions could be read from this PDF. Try a CSV or QFX/OFX download.')
        return rows, {'format': 'pdf', 'warnings': [
            'PDF statements are read from the page layout. Check each row below; you can fix a date, '
            'description or amount, flip a sign, or remove a row before importing.']}
    try:
        if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
            text = data.decode('utf-16')          # Excel "Unicode Text"
        else:
            text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = data.decode('latin-1')
    text = text.replace('\x00', '')
    if '<OFX' in text.upper() or 'OFXHEADER' in text.upper() or name.endswith(('.ofx', '.qfx', '.qbo')):
        rows, warnings = parse_ofx(text)
        return rows, {'format': 'ofx', 'warnings': warnings}
    rows, warnings = parse_csv(text)
    if not rows:
        raise ApiError(400, 'No transactions found in this file')
    return rows, {'format': 'csv', 'warnings': warnings}
