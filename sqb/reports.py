"""Financial reports. Every amount cell carries the ledger 'segments' it was computed from,
so the drill-down (transaction breakdown) always adds up to the number shown."""
import datetime

from . import db, documents as docs, invoices, ledger
from .accounts import GROUP, PL_TYPES, TYPE_ORDER, TYPES, normal_sign, ordered
from .router import route
from .util import (ApiError, D, add_days, add_months, as_int, fmt_day, money, month_end, parse_date,
                   range_label, today)

ONE_DAY = datetime.timedelta(days=1)


def fy_month(conn):
    try:
        m = int(db.get_setting(conn, 'fiscal_year_start_month', '1'))
    except ValueError:
        m = 1
    return m if 1 <= m <= 12 else 1


def fy_start(date_str, m0):
    d = D(date_str)
    return datetime.date(d.year if d.month >= m0 else d.year - 1, m0, 1).isoformat()


def periods(start, end, columns, m0):
    """Split [start, end] into month / quarter / year columns (clipped to the range)."""
    if columns in (None, '', 'total'):
        return [(start, end)]
    if columns not in ('month', 'quarter', 'year'):
        raise ApiError(400, 'columns must be total, month, quarter or year')
    step = {'month': 1, 'quarter': 3, 'year': 12}[columns]
    s, e = D(start), D(end)
    b = datetime.date(s.year, s.month, 1)
    while (b.month - m0) % step != 0:
        b = add_months(b, -1)
    out = []
    while b <= e:
        nb = add_months(b, step)
        out.append((max(b, s).isoformat(), min(nb - ONE_DAY, e).isoformat()))
        b = nb
        if len(out) > 60:
            raise ApiError(400, 'Too many columns. Choose a shorter period or larger columns.')
    return out


def shift(start, end, mode):
    """The comparison range for [start, end]: previous period (pp) or same period last year (py)."""
    s, e = D(start), D(end)
    if mode == 'py':
        ns, ne = add_months(s, -12), add_months(e, -12)
        if s.day == 1 and e == month_end(e):
            ne = month_end(ne)          # whole months: Feb 2025 compares to all of Feb 2024
        return ns.isoformat(), ne.isoformat()
    if s.day == 1 and e == month_end(e):
        n = (e.year - s.year) * 12 + e.month - s.month + 1
        return add_months(s, -n).isoformat(), (s - ONE_DAY).isoformat()
    days = (e - s).days
    ne = s - ONE_DAY
    return (ne - datetime.timedelta(days=days)).isoformat(), ne.isoformat()


def make_cols(start, end, columns, compare, m0, point=False):
    if compare not in (None, '', 'none', 'pp', 'py'):
        raise ApiError(400, 'compare must be none, pp or py')
    lbl = (lambda s, e: 'As of ' + fmt_day(D(e))) if point else range_label
    ps = periods(start, end, columns, m0)
    cols = []
    for s, e in ps:
        base = len(cols)
        cols.append({'kind': 'amount', 'label': lbl(s, e), 'start': s, 'end': e})
        if compare in ('pp', 'py'):
            cs, ce = shift(s, e, compare)
            cols.append({'kind': 'amount', 'label': lbl(cs, ce) + (' (PP)' if compare == 'pp' else ' (PY)'),
                         'start': cs, 'end': ce})
            cols.append({'kind': 'change', 'label': 'Change', 'base': base, 'cmp': base + 1})
            cols.append({'kind': 'pct', 'label': '% Change', 'base': base, 'cmp': base + 1})
    if len(ps) > 1 and not point:
        base = len(cols)
        cols.append({'kind': 'amount', 'label': 'Total', 'start': start, 'end': end})
        if compare in ('pp', 'py'):
            # Comparison total covers the same periods as the comparison columns.
            cmp_cols = [c for c in cols[:base] if c['label'].endswith((' (PP)', ' (PY)'))]
            cs, ce = cmp_cols[0]['start'], cmp_cols[-1]['end']
            cols.append({'kind': 'amount', 'label': 'Total' + (' (PP)' if compare == 'pp' else ' (PY)'),
                         'start': cs, 'end': ce})
            cols.append({'kind': 'change', 'label': 'Change', 'base': base, 'cmp': base + 1})
            cols.append({'kind': 'pct', 'label': '% Change', 'base': base, 'cmp': base + 1})
    return cols


def flow(ids, sign):
    """Activity during the column's period."""
    return lambda s, e: [(tuple(ids), s, e, sign)]


def point(ids, sign):
    """Balance as of the column's end date."""
    return lambda s, e: [(tuple(ids), None, e, sign)]


def cat(*fns):
    return lambda s, e: [seg for f in fns for seg in f(s, e)]


# ---------------------------------------------------------------- accounting basis

def basis_of(params):
    b = (params.get('basis') or 'accrual').lower()
    if b not in ('accrual', 'cash'):
        raise ApiError(400, 'basis must be accrual or cash')
    return b


def source(conn, basis):
    """Name of a temporary table/view of ledger lines with dates, for the chosen basis.
    Accrual: the ledger as posted. Cash: invoices and bills count only when (and as far as) they are paid,
    on the payment date, split over their lines in proportion; unpaid ones are left out."""
    if basis != 'cash':
        conn.execute('CREATE TEMP VIEW IF NOT EXISTS al AS SELECT l.id, l.txn_id, l.seq, l.account_id, l.debit, '
                     'l.credit, l.memo, l.name_id, l.recon_status, t.date FROM lines l '
                     'JOIN transactions t ON t.id = l.txn_id')
        return 'al'
    conn.execute('CREATE TEMP TABLE IF NOT EXISTS cl(id INTEGER PRIMARY KEY, txn_id INTEGER, seq INTEGER, '
                 'account_id INTEGER, debit INTEGER, credit INTEGER, memo TEXT, name_id INTEGER, '
                 "recon_status TEXT DEFAULT '', date TEXT)")
    conn.execute('DELETE FROM cl')
    doc_txn, pay_txn = {}, {}
    for side in (docs.SALES, docs.PURCHASE):
        ctrl = db.system_account(conn, side.ctrl)
        for d in conn.execute('SELECT * FROM %s' % side.doc):
            doc_txn[d['txn_id']] = (side, d, ctrl)
        for p in conn.execute('SELECT * FROM %s' % side.pay):
            pay_txn[p['txn_id']] = (side, p, ctrl)
    rows = []
    lines_by_txn = {}
    for l in conn.execute('SELECT l.*, t.date FROM lines l JOIN transactions t ON t.id = l.txn_id ORDER BY l.id'):
        lines_by_txn.setdefault(l['txn_id'], []).append(l)
        if l['txn_id'] in doc_txn:
            continue
        if l['txn_id'] in pay_txn and l['account_id'] == pay_txn[l['txn_id']][2]:
            continue
        rows.append((l['id'], l['txn_id'], l['seq'], l['account_id'], l['debit'], l['credit'], l['memo'],
                     l['name_id'], l['recon_status'], l['date']))
    next_id = (conn.execute('SELECT COALESCE(MAX(id), 0) FROM lines').fetchone()[0] or 0) + 1
    docs_by_id = {(side.doc, d['id']): (d, ctrl) for side, d, ctrl in doc_txn.values()}
    for ptid, (side, p, ctrl) in pay_txn.items():
        apps = conn.execute('SELECT * FROM %s WHERE payment_id=?' % side.apps, (p['id'],)).fetchall()
        unapplied = p['ctrl_home'] - sum(a['home'] for a in apps)
        seq = 100
        if unapplied:     # a prepayment stays a customer credit / vendor prepayment until applied
            net = -side.sign * unapplied
            rows.append((next_id, ptid, seq, ctrl, max(net, 0), max(-net, 0), p['memo'], p[side.name_col], '', p['date']))
            next_id += 1
        for a in apps:
            d, _ = docs_by_id[(side.doc, a[side.fk])]
            src = [l for l in lines_by_txn.get(d['txn_id'], []) if l['account_id'] != ctrl]
            if not d['total_home'] or not src:
                continue
            target = -side.sign * a['home']        # what these lines must add up to (debit - credit)
            share = [int(round((l['debit'] - l['credit']) * a['home'] / float(d['total_home']))) for l in src]
            if share:
                big = max(range(len(src)), key=lambda i: abs(src[i]['debit'] - src[i]['credit']))
                share[big] += target - sum(share)
            for l, net in zip(src, share):
                if net:
                    seq += 1
                    rows.append((next_id, ptid, seq, l['account_id'], max(net, 0), max(-net, 0), l['memo'],
                                 l['name_id'], '', p['date']))
                    next_id += 1
    conn.executemany('INSERT INTO cl VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
    return 'cl'


class Builder:
    def __init__(self, conn, cols, basis='accrual'):
        self.conn, self.cols, self.rows, self._cache = conn, cols, [], {}
        self.basis = basis
        self.src = source(conn, basis)
        self.accts = ordered(conn)
        self.by_id = {a['id']: a for a in self.accts}
        self.kids = {}
        for a in self.accts:
            if a['parent_id'] in self.by_id:
                self.kids.setdefault(a['parent_id'], []).append(a['id'])
        self.m0 = fy_month(conn)
        self.pl_ids = self.ids(*PL_TYPES)

    def ids(self, *types, exclude=()):
        return tuple(a['id'] for a in self.accts if a['type'] in types and a['id'] not in exclude)

    def net(self, s, e):
        """{account_id: debit - credit} for the date range (None = unbounded)."""
        key = (s, e)
        if key not in self._cache:
            sql = 'SELECT l.account_id, SUM(l.debit - l.credit) FROM %s l WHERE 1=1' % self.src
            args = []
            if s:
                sql += ' AND l.date >= ?'
                args.append(s)
            if e:
                sql += ' AND l.date <= ?'
                args.append(e)
            self._cache[key] = dict(self.conn.execute(sql + ' GROUP BY l.account_id', args).fetchall())
        return self._cache[key]

    def seg_value(self, segs):
        total = 0
        for ids, s, e, sign in segs:
            n = self.net(s, e)
            total += sign * sum(n.get(i, 0) for i in ids)
        return total

    def values(self, segfn):
        vals, drills = [], []
        for c in self.cols:
            if c['kind'] == 'amount':
                segs = segfn(c['start'], c['end'])
                vals.append(self.seg_value(segs))
                drills.append({'basis': self.basis,
                               'segments': [{'accounts': list(i), 'start': s, 'end': e, 'sign': sg}
                                            for i, s, e, sg in segs if i]})
            elif c['kind'] == 'change':
                vals.append(vals[c['base']] - vals[c['cmp']])
                drills.append(None)
            else:
                b, p = vals[c['base']], vals[c['cmp']]
                vals.append(None if p == 0 else round((b - p) * 100.0 / abs(p), 1))
                drills.append(None)
        return vals, drills

    def nonzero(self, segfn):
        return any(self.seg_value(segfn(c['start'], c['end'])) for c in self.cols if c['kind'] == 'amount')

    def add(self, label, level, kind, segfn=None, account_id=None, force=True):
        row = {'label': label, 'level': level, 'kind': kind}
        if segfn:
            vals, drills = self.values(segfn)
            if not force and not any(v for v, c in zip(vals, self.cols) if c['kind'] == 'amount'):
                return False
            row['values'] = [v if c['kind'] == 'pct' else money(v) for v, c in zip(vals, self.cols)]
            row['drill'] = drills
        if account_id:
            row['account_id'] = account_id
        self.rows.append(row)
        return True

    def tree(self, ids, level, mk, flat=False):
        """Emit account rows; parents with visible sub-accounts get a header and a 'Total' row."""
        idset = set(ids)
        if flat:
            for i in ids:
                self.add(self.by_id[i]['full_name'], level, 'account', mk((i,)), account_id=i, force=False)
            return

        def sub(i):
            out = [i]
            for k in self.kids.get(i, []):
                if k in idset:
                    out += sub(k)
            return out

        def emit(i, lvl):
            a = self.by_id[i]
            ks = [k for k in self.kids.get(i, []) if k in idset and any(self.nonzero(mk((x,))) for x in sub(k))]
            if not ks:
                self.add(a['name'], lvl, 'account', mk((i,)), account_id=i, force=False)
                return
            own = mk((i,))
            if self.nonzero(own):
                self.add(a['name'], lvl, 'account', own, account_id=i)
            else:
                self.add(a['name'], lvl, 'header')
            for k in ks:
                emit(k, lvl + 1)
            self.add('Total ' + a['name'], lvl, 'subtotal', mk(tuple(sub(i))))
        for i in ids:
            if self.by_id[i]['parent_id'] not in idset:
                emit(i, level)

    def section(self, label, ids, level, mk, total_label=None):
        if not any(self.nonzero(mk((i,))) for i in ids):
            return False
        self.add(label, level, 'header')
        self.tree(ids, level + 1, mk)
        self.add(total_label or 'Total ' + label, level, 'total', mk(ids))
        return True

    def result(self, title, subtitle):
        return {'title': title, 'company': db.get_setting(self.conn, 'company_name', ''), 'subtitle': subtitle,
                'basis': 'Cash basis' if self.basis == 'cash' else 'Accrual basis',
                'columns': [{k: c.get(k) for k in ('label', 'kind', 'start', 'end')} for c in self.cols],
                'rows': self.rows}


def _range(ctx):
    end = parse_date(ctx.q.get('end') or ctx.q.get('as_of') or today(), 'end')
    m0 = fy_month(ctx.conn)
    start = parse_date(ctx.q.get('start') or fy_start(end, m0), 'start')
    if start > end:
        raise ApiError(400, 'Start date must be on or before the end date')
    return start, end, m0


# ---------------------------------------------------------------- Profit and Loss

@route('GET', '/api/reports/profit_loss')
def profit_loss(ctx):
    start, end, m0 = _range(ctx)
    b = Builder(ctx.conn, make_cols(start, end, ctx.q.get('columns'), ctx.q.get('compare'), m0), basis_of(ctx.q))
    inc, cogs, exp = b.ids('Income'), b.ids('Cost of Goods Sold'), b.ids('Expense')
    oinc, oexp = b.ids('Other Income'), b.ids('Other Expense')
    credit = lambda ids: flow(ids, -1)   # noqa: E731  income shows positive
    debit = lambda ids: flow(ids, 1)     # noqa: E731  expenses show positive
    b.section('Income', inc, 0, credit)
    has_cogs = b.section('Cost of Goods Sold', cogs, 0, debit)
    b.add('Gross Profit', 0, 'total', flow(inc + cogs, -1))
    b.section('Expenses', exp, 0, debit)
    b.add('Net Operating Income', 0, 'total', flow(inc + cogs + exp, -1))
    has_oi = b.section('Other Income', oinc, 0, credit)
    has_oe = b.section('Other Expenses', oexp, 0, debit)
    if has_oi or has_oe:
        b.add('Net Other Income', 0, 'total', flow(oinc + oexp, -1))
    b.add('Net Income', 0, 'grand', flow(b.pl_ids, -1))
    return b.result('Profit and Loss', range_label(start, end))


# ---------------------------------------------------------------- Balance Sheet

@route('GET', '/api/reports/balance_sheet')
def balance_sheet(ctx):
    start, end, m0 = _range(ctx)
    b = Builder(ctx.conn, make_cols(start, end, ctx.q.get('columns'), ctx.q.get('compare'), m0, point=True),
                basis_of(ctx.q))
    re_id = db.system_account(ctx.conn, 're')
    pl = b.pl_ids
    dr = lambda ids: point(ids, 1)     # noqa: E731
    cr = lambda ids: point(ids, -1)    # noqa: E731
    cur_assets = b.ids('Bank', 'Accounts Receivable', 'Other Current Asset')
    assets = b.ids('Bank', 'Accounts Receivable', 'Other Current Asset', 'Fixed Asset', 'Other Asset')
    cur_liab = b.ids('Accounts Payable', 'Credit Card', 'Other Current Liability')
    liab = cur_liab + b.ids('Long Term Liability')
    equity = b.ids('Equity', exclude=(re_id,))

    def retained(s, e):
        # Retained Earnings = postings to the account + net income of all prior fiscal years
        return [((re_id,), None, e, -1), (pl, None, add_days(fy_start(e, m0), -1), -1)]

    def net_income(s, e):
        return [(pl, fy_start(e, m0), e, -1)]
    total_equity = cat(cr(equity), retained, net_income)

    b.add('ASSETS', 0, 'header')
    b.add('Current Assets', 1, 'header')
    b.section('Bank Accounts', b.ids('Bank'), 2, dr)
    b.section('Accounts Receivable', b.ids('Accounts Receivable'), 2, dr)
    b.section('Other Current Assets', b.ids('Other Current Asset'), 2, dr)
    b.add('Total Current Assets', 1, 'total', dr(cur_assets))
    b.section('Fixed Assets', b.ids('Fixed Asset'), 1, dr)
    b.section('Other Assets', b.ids('Other Asset'), 1, dr)
    b.add('TOTAL ASSETS', 0, 'grand', dr(assets))
    b.add('LIABILITIES AND EQUITY', 0, 'header')
    b.add('Liabilities', 1, 'header')
    b.add('Current Liabilities', 2, 'header')
    b.section('Accounts Payable', b.ids('Accounts Payable'), 3, cr)
    b.section('Credit Cards', b.ids('Credit Card'), 3, cr)
    b.section('Other Current Liabilities', b.ids('Other Current Liability'), 3, cr)
    b.add('Total Current Liabilities', 2, 'total', cr(cur_liab))
    b.section('Long-Term Liabilities', b.ids('Long Term Liability'), 2, cr)
    b.add('Total Liabilities', 1, 'total', cr(liab))
    b.add('Equity', 1, 'header')
    b.tree(equity, 2, cr)
    b.add('Retained Earnings', 2, 'account', retained, account_id=re_id)
    b.add('Net Income', 2, 'account', net_income)
    b.add('Total Equity', 1, 'total', total_equity)
    b.add('TOTAL LIABILITIES AND EQUITY', 0, 'grand', cat(cr(liab), total_equity))
    return b.result('Balance Sheet', 'As of ' + fmt_day(D(end)))


# ---------------------------------------------------------------- Statement of Cash Flows

def _is_noncash_fixed(a):
    n = a['full_name'].lower()      # includes parent names, so sub-accounts follow their parent
    return a['type'] == 'Fixed Asset' and ('depreciation' in n or 'amortization' in n)


@route('GET', '/api/reports/cash_flow')
def cash_flow(ctx):
    start, end, m0 = _range(ctx)
    b = Builder(ctx.conn, make_cols(start, end, ctx.q.get('columns'), ctx.q.get('compare'), m0), basis_of(ctx.q))
    pl, bank = b.pl_ids, b.ids('Bank')
    accum = tuple(a['id'] for a in b.accts if _is_noncash_fixed(a))
    oper = accum + b.ids('Accounts Receivable', 'Other Current Asset', 'Accounts Payable', 'Credit Card',
                         'Other Current Liability')
    invest = b.ids('Fixed Asset', 'Other Asset', exclude=accum)
    fin = b.ids('Long Term Liability', 'Equity')
    chg = lambda ids: flow(ids, -1)    # noqa: E731  credits increase cash

    b.add('OPERATING ACTIVITIES', 0, 'header')
    b.add('Net Income', 1, 'account', chg(pl))
    b.add('Adjustments to reconcile Net Income to Net Cash provided by operations:', 1, 'header')
    b.tree(oper, 2, chg, flat=True)
    b.add('Total Adjustments to reconcile Net Income to Net Cash provided by operations', 1, 'total', chg(oper))
    b.add('Net cash provided by operating activities', 0, 'total', chg(pl + oper))
    b.add('INVESTING ACTIVITIES', 0, 'header')
    b.tree(invest, 1, chg, flat=True)
    b.add('Net cash provided by investing activities', 0, 'total', chg(invest))
    b.add('FINANCING ACTIVITIES', 0, 'header')
    b.tree(fin, 1, chg, flat=True)
    b.add('Net cash provided by financing activities', 0, 'total', chg(fin))
    b.add('NET CASH INCREASE FOR PERIOD', 0, 'grand', chg(pl + oper + invest + fin))
    b.add('Cash at beginning of period', 0, 'total', lambda s, e: [(bank, None, add_days(s, -1), 1)])
    b.add('CASH AT END OF PERIOD', 0, 'grand', lambda s, e: [(bank, None, e, 1)])
    return b.result('Statement of Cash Flows', range_label(start, end))


# ---------------------------------------------------------------- Trial Balance

@route('GET', '/api/reports/trial_balance')
def trial_balance(ctx):
    end = parse_date(ctx.q.get('end') or ctx.q.get('as_of') or today(), 'end')
    m0 = fy_month(ctx.conn)
    b = Builder(ctx.conn, [], basis_of(ctx.q))
    fs = fy_start(end, m0)
    prior_end = add_days(fs, -1)
    re_id = db.system_account(ctx.conn, 're')
    rows, td, tc = [], 0, 0
    for a in b.accts:
        if a['id'] == re_id:
            segs = [((a['id'],), None, end, 1), (b.pl_ids, None, prior_end, 1)]
        elif a['type'] in PL_TYPES:
            segs = [((a['id'],), fs, end, 1)]
        else:
            segs = [((a['id'],), None, end, 1)]
        v = b.seg_value(segs)
        if not v:
            continue
        sign = 1 if v > 0 else -1
        drill = {'segments': [{'accounts': list(i), 'start': s, 'end': e, 'sign': sign} for i, s, e, _ in segs]}
        rows.append({'label': a['full_name'], 'level': 0, 'kind': 'account', 'account_id': a['id'],
                     'values': [money(v) if v > 0 else None, money(-v) if v < 0 else None],
                     'drill': [drill if v > 0 else None, drill if v < 0 else None]})
        td += max(v, 0)
        tc += max(-v, 0)
    rows.append({'label': 'TOTAL', 'level': 0, 'kind': 'grand', 'values': [money(td), money(tc)],
                 'drill': [None, None]})
    return {'title': 'Trial Balance', 'company': db.get_setting(ctx.conn, 'company_name', ''),
            'subtitle': 'As of ' + fmt_day(D(end)), 'basis': b.result('', '')['basis'],
            'columns': [{'label': 'Debit', 'kind': 'amount'}, {'label': 'Credit', 'kind': 'amount'}], 'rows': rows}


# ---------------------------------------------------------------- A/R and A/P Aging

def _aging(conn, side, as_of, title, ctrl_type):
    """Open balances by customer or vendor, in home currency, so the total ties to A/R or A/P."""
    labels = ['Current', '1 - 30', '31 - 60', '61 - 90', '91 and over', 'Total']
    data = {}
    sg = side.sign      # A/R: +debit is owed to us. A/P: +credit is owed by us.

    def bucket(days):
        return 0 if days <= 0 else 1 if days <= 30 else 2 if days <= 60 else 3 if days <= 90 else 4

    def put(name, idx, item):
        data.setdefault(name, [[] for _ in range(5)])[idx].append(item)
    for d in conn.execute('SELECT d.*, n.name AS nm FROM %s d JOIN names n ON n.id=d.%s WHERE d.date <= ? '
                          'ORDER BY d.date, d.id' % (side.doc, side.name_col), (as_of,)).fetchall():
        bal = d['total_home'] - docs.applied(conn, side, d['id'], as_of)[1]
        if not bal:
            continue
        put(d['nm'], bucket((D(as_of) - D(d['due_date'])).days),
            {'txn_id': d['txn_id'], 'date': d['date'], 'type': side.doc_type, 'ref': d['number'], 'name': d['nm'],
             'memo': 'Due ' + d['due_date'], 'amount': bal})
    for p in conn.execute('SELECT p.*, n.name AS nm, (SELECT COALESCE(SUM(a.home),0) FROM %s a JOIN %s d '
                          'ON d.id=a.%s WHERE a.payment_id=p.id AND d.date <= ?) AS used FROM %s p '
                          'JOIN names n ON n.id=p.%s WHERE p.date <= ?'
                          % (side.apps, side.doc, side.fk, side.pay, side.name_col), (as_of, as_of)).fetchall():
        left = p['ctrl_home'] - p['used']
        if left > 0:
            put(p['nm'], 0, {'txn_id': p['txn_id'], 'date': p['date'], 'type': side.pay_type, 'ref': p['ref'],
                             'name': p['nm'], 'memo': 'Unapplied payment', 'amount': -left})
    # Anything else posted to A/R or A/P (journal entries) ages from its own date, so the report ties to the books.
    for l in conn.execute(
            'SELECT l.txn_id, l.debit - l.credit AS net, t.date, t.type, t.ref, t.memo, n.name FROM lines l '
            'JOIN transactions t ON t.id = l.txn_id JOIN accounts a ON a.id = l.account_id '
            'LEFT JOIN names n ON n.id = COALESCE(l.name_id, t.name_id) '
            'WHERE a.type = ? AND t.date <= ? '
            'AND t.id NOT IN (SELECT txn_id FROM %s) AND t.id NOT IN (SELECT txn_id FROM %s)' % (side.doc, side.pay),
            (ctrl_type, as_of)).fetchall():
        amt = sg * l['net']
        put(l['name'] or '(No name)', 0 if amt < 0 else bucket((D(as_of) - D(l['date'])).days),
            {'txn_id': l['txn_id'], 'date': l['date'], 'type': l['type'], 'ref': l['ref'], 'name': l['name'],
             'memo': l['memo'], 'amount': amt})
    rows, totals, all_items = [], [0] * 6, [[] for _ in range(6)]

    def cell(its):
        return {'items': [dict(i, amount=money(i['amount'])) for i in its]} if its else None
    for nm in sorted(data, key=str.lower):
        buckets = data[nm]
        vals = [sum(i['amount'] for i in bk) for bk in buckets]
        everything = [i for bk in buckets for i in bk]
        vals.append(sum(vals))
        for k in range(5):
            all_items[k] += buckets[k]
        all_items[5] += everything
        totals = [t + v for t, v in zip(totals, vals)]
        rows.append({'label': nm, 'level': 0, 'kind': 'account', 'values': [money(v) for v in vals],
                     'drill': [cell(bk) for bk in buckets] + [cell(everything)]})
    rows.append({'label': 'TOTAL', 'level': 0, 'kind': 'grand', 'values': [money(v) for v in totals],
                 'drill': [cell(i) for i in all_items]})
    return {'title': title, 'company': db.get_setting(conn, 'company_name', ''),
            'subtitle': 'As of ' + fmt_day(D(as_of)), 'basis': '',
            'columns': [{'label': l, 'kind': 'amount'} for l in labels], 'rows': rows}


@route('GET', '/api/reports/ar_aging')
def ar_aging(ctx):
    as_of = parse_date(ctx.q.get('end') or ctx.q.get('as_of') or today(), 'end')
    return _aging(ctx.conn, docs.SALES, as_of, 'A/R Aging Summary', 'Accounts Receivable')


@route('GET', '/api/reports/ap_aging')
def ap_aging(ctx):
    as_of = parse_date(ctx.q.get('end') or ctx.q.get('as_of') or today(), 'end')
    return _aging(ctx.conn, docs.PURCHASE, as_of, 'A/P Aging Summary', 'Accounts Payable')


# ---------------------------------------------------------------- transaction-level detail

def _line_rows(conn, where, args, src='al'):
    rows = conn.execute(
        'SELECT l.id, l.txn_id, l.account_id, l.debit, l.credit, l.memo AS lmemo, l.recon_status, '
        't.memo AS tmemo, l.date, t.type, t.ref, n.name FROM %s l '
        'JOIN transactions t ON t.id = l.txn_id LEFT JOIN names n ON n.id = COALESCE(l.name_id, t.name_id) '
        'WHERE ' % src + where + ' ORDER BY l.date, t.id, l.seq, l.id', args).fetchall()
    tids = list({r['txn_id'] for r in rows})
    others = {}
    for i in range(0, len(tids), 500):
        chunk = tids[i:i + 500]
        for x in conn.execute('SELECT l.id, l.txn_id, a.name FROM %s l JOIN accounts a ON a.id = l.account_id '
                              'WHERE l.txn_id IN (%s) ORDER BY l.seq' % (src, ','.join('?' * len(chunk))), chunk):
            others.setdefault(x['txn_id'], []).append((x['id'], x['name']))
    out = []
    for r in rows:
        names = list(dict.fromkeys(nm for lid, nm in others.get(r['txn_id'], []) if lid != r['id']))
        out.append({'line_id': r['id'], 'txn_id': r['txn_id'], 'account_id': r['account_id'], 'date': r['date'],
                    'type': r['type'], 'ref': r['ref'], 'name': r['name'], 'memo': r['lmemo'] or r['tmemo'],
                    'split': names[0] if len(names) == 1 else '-Split-', 'recon_status': r['recon_status'],
                    'debit_cents': r['debit'], 'credit_cents': r['credit']})
    return out


def _date_where(where, args, start, end):
    if start:
        where += ' AND l.date >= ?'
        args.append(start)
    if end:
        where += ' AND l.date <= ?'
        args.append(end)
    return where, args


def _finish(r, amount, balance=None):
    r['debit'] = money(r.pop('debit_cents')) or None
    r['credit'] = money(r.pop('credit_cents')) or None
    r['amount'] = money(amount)
    if balance is not None:
        r['balance'] = money(balance)
    return r


@route('POST', '/api/reports/drill')
def drill(ctx):
    """Transactions behind a report cell, grouped by account. The grand total equals the cell."""
    conn = ctx.conn
    segs = ctx.body.get('segments')
    if not isinstance(segs, list):
        raise ApiError(400, 'segments are required')
    src = source(conn, basis_of(ctx.body))
    groups, grand = {}, 0
    for sg in segs:
        if not isinstance(sg, dict) or not isinstance(sg.get('accounts') or [], list):
            raise ApiError(400, 'segments are malformed')
        ids = [as_int(i, 'account') for i in (sg.get('accounts') or [])]
        if not ids:
            continue
        sign = -1 if sg.get('sign') == -1 else 1
        where, args = _date_where('l.account_id IN (%s)' % ','.join('?' * len(ids)), list(ids),
                                  parse_date(sg.get('start'), 'start', required=False),
                                  parse_date(sg.get('end'), 'end', required=False))
        for r in _line_rows(conn, where, args, src):
            amt = sign * (r['debit_cents'] - r['credit_cents'])
            g = groups.setdefault(r['account_id'], {'rows': [], 'total': 0})
            g['rows'].append(_finish(r, amt))
            g['total'] += amt
            grand += amt
    out = []
    for a in ordered(conn):
        g = groups.get(a['id'])
        if g:
            g['rows'].sort(key=lambda r: (r['date'], r['txn_id'], r['line_id']))
            out.append({'account_id': a['id'], 'account': a['full_name'], 'type': a['type'],
                        'rows': g['rows'], 'total': money(g['total'])})
    return {'groups': out, 'total': money(grand), 'count': sum(len(g['rows']) for g in out)}


@route('GET', '/api/reports/transaction_detail')
def transaction_detail(ctx):
    """Transaction Detail by Account (general ledger): every posting for each account in the period,
    with beginning balance, running balance and totals."""
    conn = ctx.conn
    start, end, m0 = _range(ctx)
    only = as_int(ctx.q.get('account_id'), 'account_id', required=False)
    accts = [a for a in ordered(conn) if not only or a['id'] == only]
    if only and not accts:
        raise ApiError(404, 'Account not found')
    where, args = _date_where('1=1' if not only else 'l.account_id=%d' % only, [], start, end)
    basis = basis_of(ctx.q)
    b = Builder(conn, [], basis)
    by_acct = {}
    for r in _line_rows(conn, where, args, b.src):
        by_acct.setdefault(r['account_id'], []).append(r)
    before = b.net(None, add_days(start, -1))
    groups = []
    for a in accts:
        sign = normal_sign(a['type'])
        is_bs = a['type'] not in PL_TYPES
        begin = sign * before.get(a['id'], 0) if is_bs else 0
        rows = by_acct.get(a['id'], [])
        if not rows and not begin and not only:
            continue
        bal, td, tc, out = begin, 0, 0, []
        for r in rows:
            amt = sign * (r['debit_cents'] - r['credit_cents'])
            bal += amt
            td += r['debit_cents']
            tc += r['credit_cents']
            out.append(_finish(r, amt, bal))
        groups.append({'account_id': a['id'], 'account': a['full_name'], 'type': a['type'],
                       'beginning_balance': money(begin) if is_bs else None, 'rows': out,
                       'total_debit': money(td), 'total_credit': money(tc), 'net_change': money(bal - begin),
                       'ending_balance': money(bal)})
    return {'title': 'Transaction Detail by Account', 'company': db.get_setting(conn, 'company_name', ''),
            'subtitle': range_label(start, end), 'basis': b.result('', '')['basis'], 'start': start, 'end': end,
            'groups': groups}


# ---------------------------------------------------------------- sales tax, inventory, payroll

def _simple(conn, title, subtitle, columns, rows, basis=''):
    return {'title': title, 'company': db.get_setting(conn, 'company_name', ''), 'subtitle': subtitle,
            'basis': basis, 'columns': [{'label': c, 'kind': 'amount'} for c in columns], 'rows': rows}


def _cell(its):
    return {'items': [dict(i, amount=money(i['amount'])) for i in its]} if its else None


@route('GET', '/api/reports/sales_tax')
def sales_tax_liability(ctx):
    """Sales Tax Liability: taxable and non-taxable sales and tax collected per rate (invoice date),
    tax paid in the period, and what is owed at the end."""
    conn = ctx.conn
    start, end, m0 = _range(ctx)
    data = {}
    for inv in conn.execute('SELECT i.*, n.name AS nm, r.name AS rate_name FROM invoices i '
                            'JOIN names n ON n.id = i.customer_id LEFT JOIN tax_rates r ON r.id = i.tax_rate_id '
                            'WHERE i.date BETWEEN ? AND ? ORDER BY i.date, i.id', (start, end)).fetchall():
        rate = docs.Decimal(inv['exchange_rate'])
        key = inv['rate_name'] or '(No sales tax)'
        cols = data.setdefault(key, [[], [], []])
        taxable = sum(l['amount'] for l in conn.execute('SELECT amount, taxable FROM invoice_lines WHERE invoice_id=?',
                                                        (inv['id'],)) if l['taxable'] and inv['tax_rate_id'])
        base = {'txn_id': inv['txn_id'], 'date': inv['date'], 'type': 'Invoice', 'ref': inv['number'],
                'name': inv['nm'], 'memo': ''}
        sub = inv['total'] - inv['tax']
        if taxable:
            cols[0].append(dict(base, amount=docs.to_home(taxable, rate)))
        if sub - taxable:
            cols[1].append(dict(base, amount=docs.to_home(sub - taxable, rate)))
        if inv['tax']:
            cols[2].append(dict(base, amount=docs.to_home(inv['tax'], rate)))
    rows, totals = [], [[], [], []]
    for key in sorted(data, key=lambda k: (k.startswith('('), k.lower())):
        cols = data[key]
        rows.append({'label': key, 'level': 0, 'kind': 'account',
                     'values': [money(sum(i['amount'] for i in c)) for c in cols], 'drill': [_cell(c) for c in cols]})
        for k in range(3):
            totals[k] += cols[k]
    rows.append({'label': 'TOTAL', 'level': 0, 'kind': 'grand',
                 'values': [money(sum(i['amount'] for i in c)) for c in totals], 'drill': [_cell(c) for c in totals]})
    tax_acct = db.system_account(conn, 'tax')
    paid = [{'txn_id': t['id'], 'date': t['date'], 'type': t['type'], 'ref': t['ref'], 'name': '', 'memo': t['memo'],
             'amount': t['amt']} for t in conn.execute(
        "SELECT t.*, l.debit - l.credit AS amt FROM transactions t JOIN lines l ON l.txn_id = t.id "
        "WHERE t.type='Sales Tax Payment' AND l.account_id=? AND t.date BETWEEN ? AND ? ORDER BY t.date",
        (tax_acct, start, end))]
    rows.append({'label': 'Sales tax paid in this period', 'level': 0, 'kind': 'total',
                 'values': [None, None, money(sum(i['amount'] for i in paid))], 'drill': [None, None, _cell(paid)]})
    owed = -ledger.account_net(conn, tax_acct, end=end)
    rows.append({'label': 'Sales tax owed as of ' + fmt_day(D(end)), 'level': 0, 'kind': 'grand',
                 'values': [None, None, money(owed)],
                 'drill': [None, None, {'segments': [{'accounts': [tax_acct], 'start': None, 'end': end, 'sign': -1}]}]})
    return _simple(conn, 'Sales Tax Liability', range_label(start, end),
                   ['Taxable sales', 'Non-taxable sales', 'Tax collected'], rows, 'Accrual basis')


@route('GET', '/api/reports/inventory_valuation')
def inventory_valuation(ctx):
    conn = ctx.conn
    end = parse_date(ctx.q.get('end') or ctx.q.get('as_of') or today(), 'end')
    rows, tq, tv, all_moves = [], 0, 0, []
    for it in conn.execute("SELECT * FROM items WHERE type='inventory' ORDER BY name COLLATE NOCASE").fetchall():
        moves = conn.execute('SELECT m.*, t.type, t.ref, t.memo, n.name FROM inv_moves m JOIN transactions t '
                             'ON t.id = m.txn_id LEFT JOIN names n ON n.id = t.name_id '
                             'WHERE m.item_id=? AND m.date <= ? ORDER BY m.date, m.id', (it['id'], end)).fetchall()
        if not moves:
            continue
        q = round(sum(m['qty'] for m in moves), 6)
        v = sum(m['value'] for m in moves)
        its = [{'txn_id': m['txn_id'], 'date': m['date'], 'type': m['type'], 'ref': m['ref'], 'name': m['name'],
                'memo': 'Qty %s' % ('%g' % m['qty']), 'amount': m['value']} for m in moves]
        all_moves += its
        rows.append({'label': it['name'], 'level': 0, 'kind': 'account',
                     'values': [q, money(v), money(v / q) if q > 0 else None], 'drill': [None, _cell(its), None]})
        tq += q
        tv += v
    rows.append({'label': 'TOTAL', 'level': 0, 'kind': 'grand', 'values': [round(tq, 6), money(tv), None],
                 'drill': [None, _cell(all_moves), None]})
    rep = _simple(conn, 'Inventory Valuation Summary', 'As of ' + fmt_day(D(end)),
                  ['Quantity on hand', 'Asset value', 'Average cost'], rows)
    rep['columns'][0]['kind'] = 'number'
    return rep


@route('GET', '/api/reports/payroll_summary')
def payroll_summary(ctx):
    from .payroll import LABELS
    conn = ctx.conn
    start, end, m0 = _range(ctx)
    keys = ('gross', 'fed_wh', 'state_wh', 'ss_ee', 'med_ee', 'other_ded', 'net', 'employer')
    rows, totals = [], [[] for _ in keys]
    emps = {}
    for p in conn.execute('SELECT p.*, e.name AS emp FROM paychecks p JOIN employees e ON e.id = p.employee_id '
                          'WHERE p.pay_date BETWEEN ? AND ? ORDER BY p.pay_date, p.id', (start, end)).fetchall():
        cols = emps.setdefault(p['emp'], [[] for _ in keys])
        for k, key in enumerate(keys):
            amt = p['ss_er'] + p['med_er'] + p['futa'] + p['suta'] if key == 'employer' else p[key]
            if amt:
                cols[k].append({'txn_id': p['txn_id'], 'date': p['pay_date'], 'type': 'Paycheck', 'ref': p['check_no'],
                                'name': p['emp'], 'memo': '', 'amount': amt})
    for emp in sorted(emps, key=str.lower):
        cols = emps[emp]
        rows.append({'label': emp, 'level': 0, 'kind': 'account',
                     'values': [money(sum(i['amount'] for i in c)) for c in cols], 'drill': [_cell(c) for c in cols]})
        for k in range(len(keys)):
            totals[k] += cols[k]
    rows.append({'label': 'TOTAL', 'level': 0, 'kind': 'grand',
                 'values': [money(sum(i['amount'] for i in c)) for c in totals], 'drill': [_cell(c) for c in totals]})
    labels = [LABELS[k] if k != 'employer' else 'Employer taxes' for k in keys]
    return _simple(conn, 'Payroll Summary', range_label(start, end), labels, rows)


# ---------------------------------------------------------------- dashboard / settings

@route('GET', '/api/dashboard')
def dashboard(ctx):
    conn = ctx.conn
    t = today()
    m0 = fy_month(conn)
    b = Builder(conn, [])
    month_start = t[:8] + '01'
    ni = lambda s, e: -sum(b.net(s, e).get(i, 0) for i in b.pl_ids)   # noqa: E731
    inc_ids, exp_ids = b.ids('Income', 'Other Income'), b.ids('Cost of Goods Sold', 'Expense', 'Other Expense')
    fs = fy_start(t, m0)
    ytd = b.net(fs, t)
    open_inv = invoices.open_invoices(conn)
    open_bills = invoices.open_bills(conn)
    banks = []
    for a in b.accts:
        if a['type'] in ('Bank', 'Credit Card') and a['active']:
            n = conn.execute("SELECT COUNT(*) FROM feed WHERE account_id=? AND status='review'", (a['id'],)).fetchone()[0]
            banks.append({'account_id': a['id'], 'name': a['name'], 'type': a['type'], 'review_count': n,
                          'balance': money(normal_sign(a['type']) * ledger.account_net(conn, a['id']))})
    return {
        'company': db.get_setting(conn, 'company_name', ''), 'today': t,
        'accounts': banks,
        'net_income_month': money(ni(month_start, t)), 'net_income_ytd': money(ni(fs, t)),
        'income_ytd': money(-sum(ytd.get(i, 0) for i in inc_ids)),
        'expenses_ytd': money(sum(ytd.get(i, 0) for i in exp_ids)),
        'open_invoices': len(open_inv), 'open_invoice_total': money(sum(i['balance_home_cents'] for i in open_inv)),
        'overdue_invoices': sum(1 for i in open_inv if i['status'] == 'Overdue'),
        'overdue_total': money(sum(i['balance_home_cents'] for i in open_inv if i['status'] == 'Overdue')),
        'open_bills': len(open_bills), 'open_bill_total': money(sum(b['balance_home_cents'] for b in open_bills)),
    }


@route('GET', '/api/meta')
def meta(ctx):
    return {'company_name': db.get_setting(ctx.conn, 'company_name', ''),
            'fiscal_year_start_month': fy_month(ctx.conn),
            'account_types': [{'type': t, 'group': g} for t, g in TYPES],
            'terms': list(invoices.TERMS), 'today': today(),
            'home_currency': docs.home_currency(ctx.conn)}


@route('PUT', '/api/settings')
def settings(ctx):
    b = ctx.body
    if 'company_name' in b:
        name = str(b['company_name'] or '').strip()
        if not name:
            raise ApiError(400, 'Company name is required')
        db.set_setting(ctx.conn, 'company_name', name)
    if 'home_currency' in b:
        cur = str(b['home_currency'] or '').strip().upper()
        if len(cur) != 3 or not cur.isalpha():
            raise ApiError(400, 'Home currency must be a 3-letter code such as USD')
        if cur != docs.home_currency(ctx.conn) and ctx.conn.execute('SELECT 1 FROM transactions LIMIT 1').fetchone():
            raise ApiError(400, 'The home currency can only be set before any transactions are entered')
        db.set_setting(ctx.conn, 'home_currency', cur)
    if 'fiscal_year_start_month' in b:
        m = as_int(b['fiscal_year_start_month'], 'fiscal_year_start_month')
        if not 1 <= m <= 12:
            raise ApiError(400, 'Fiscal year start month must be 1-12')
        db.set_setting(ctx.conn, 'fiscal_year_start_month', m)
    return meta(ctx)
