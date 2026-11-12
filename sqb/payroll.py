"""Simple payroll: employees, paychecks with tax calculations, payroll liability payments.

Social Security, Medicare, FUTA and SUTA are calculated from the rates and wage bases in Company settings.
Federal and state income tax withholding is a percentage of gross set per employee (or typed per paycheck);
the app does not include IRS or state withholding tables, so check those amounts against Publication 15-T
or your payroll provider.
"""
from decimal import Decimal

from . import db, items, ledger
from .router import route
from .util import ApiError, as_bool, as_int, as_str, money, parse_date, to_cents, today

AMOUNTS = ('gross', 'fed_wh', 'state_wh', 'ss_ee', 'med_ee', 'other_ded', 'ss_er', 'med_er', 'futa', 'suta')
EMPLOYER = ('ss_er', 'med_er', 'futa', 'suta')
WITHHELD = ('fed_wh', 'state_wh', 'ss_ee', 'med_ee', 'other_ded')
LABELS = {'gross': 'Gross pay', 'fed_wh': 'Federal income tax', 'state_wh': 'State income tax',
          'ss_ee': 'Social Security (employee)', 'med_ee': 'Medicare (employee)', 'other_ded': 'Other deductions',
          'net': 'Net pay', 'ss_er': 'Social Security (employer)', 'med_er': 'Medicare (employer)',
          'futa': 'FUTA', 'suta': 'State unemployment (SUTA)'}


def _pct(conn, key):
    return Decimal(db.get_setting(conn, key)) / 100


def _base(conn, key):
    return int(Decimal(db.get_setting(conn, key)) * 100)


def wage_account(conn):
    aid = db.get_setting(conn, 'payroll_wage_account', '')
    if aid and conn.execute('SELECT 1 FROM accounts WHERE id=?', (aid,)).fetchone():
        return int(aid)
    return db.system_account(conn, 'wages')     # "Payroll Expenses" (follows renames)


# ---------------------------------------------------------------- employees

EMP_TEXT = ('name', 'email', 'phone', 'address')


def _emp(conn, eid):
    e = conn.execute('SELECT * FROM employees WHERE id=?', (as_int(eid, 'employee'),)).fetchone()
    if not e:
        raise ApiError(404, 'Employee not found')
    return e


def _emp_json(conn, e):
    out = {k: e[k] for k in ('id', 'name', 'email', 'phone', 'address', 'pay_type')}
    year = today()[:4]
    ytd = conn.execute('SELECT COALESCE(SUM(gross),0), COALESCE(SUM(net),0) FROM paychecks WHERE employee_id=? '
                       'AND substr(pay_date,1,4)=?', (e['id'], year)).fetchone()
    out.update(pay_rate=money(e['pay_rate']), fed_pct=float(e['fed_pct']), state_pct=float(e['state_pct']),
               active=bool(e['active']), ytd_gross=money(ytd[0]), ytd_net=money(ytd[1]))
    return out


def _save_emp(conn, b, ex=None):
    get = lambda k, d='': b[k] if k in b else (ex[k] if ex else d)   # noqa: E731
    vals = {k: as_str(get(k), k) for k in EMP_TEXT}
    if not vals['name']:
        raise ApiError(400, 'Name is required')
    if conn.execute('SELECT 1 FROM employees WHERE name=? COLLATE NOCASE AND id<>?',
                    (vals['name'], ex['id'] if ex else 0)).fetchone():
        raise ApiError(409, 'An employee named "%s" already exists' % vals['name'])
    vals['pay_type'] = get('pay_type', 'hourly')
    if vals['pay_type'] not in ('hourly', 'salary'):
        raise ApiError(400, 'Pay type must be hourly or salary')
    vals['pay_rate'] = to_cents(b['pay_rate'], 'pay_rate') if 'pay_rate' in b else (ex['pay_rate'] if ex else 0)
    if vals['pay_rate'] < 0:
        raise ApiError(400, 'Pay rate cannot be negative')
    for k in ('fed_pct', 'state_pct'):
        vals[k] = format(items.dec(get(k, '0'), k, lo=Decimal(0), hi=Decimal(100)).normalize(), 'f')
    vals['active'] = 1 if as_bool(get('active', True), 'active') else 0
    return vals


@route('GET', '/api/employees')
def list_emps(ctx):
    inc = ctx.q.get('include_inactive') in ('1', 'true')
    return [_emp_json(ctx.conn, e) for e in ctx.conn.execute('SELECT * FROM employees ORDER BY name COLLATE NOCASE')
            if inc or e['active']]


@route('GET', '/api/employees/<id>')
def read_emp(ctx):
    return _emp_json(ctx.conn, _emp(ctx.conn, ctx.p['id']))


@route('POST', '/api/employees')
def create_emp(ctx):
    v = _save_emp(ctx.conn, ctx.body)
    eid = ctx.conn.execute('INSERT INTO employees(%s) VALUES (%s)' % (','.join(v), ','.join('?' * len(v))),
                           tuple(v.values())).lastrowid
    return _emp_json(ctx.conn, _emp(ctx.conn, eid))


@route('PUT', '/api/employees/<id>')
def update_emp(ctx):
    ex = _emp(ctx.conn, ctx.p['id'])
    v = _save_emp(ctx.conn, ctx.body, ex)
    ctx.conn.execute('UPDATE employees SET %s WHERE id=?' % ','.join('%s=?' % k for k in v), tuple(v.values()) + (ex['id'],))
    return _emp_json(ctx.conn, _emp(ctx.conn, ex['id']))


@route('DELETE', '/api/employees/<id>')
def delete_emp(ctx):
    ex = _emp(ctx.conn, ctx.p['id'])
    if ctx.conn.execute('SELECT 1 FROM paychecks WHERE employee_id=? LIMIT 1', (ex['id'],)).fetchone():
        raise ApiError(409, 'This employee has paychecks and cannot be deleted. Make them inactive instead.')
    ctx.conn.execute('DELETE FROM employees WHERE id=?', (ex['id'],))
    return {'deleted': True}


# ---------------------------------------------------------------- paychecks

def calculate(conn, e, pay_date, gross, overrides, exclude=None):
    """Taxes for one paycheck. overrides: amounts (cents) the user typed, which win over the calculation."""
    year = pay_date[:4]
    # Year-to-date wages before this paycheck (earlier dates, or same date and entered earlier).
    ytd = conn.execute('SELECT COALESCE(SUM(gross),0) FROM paychecks WHERE employee_id=? AND substr(pay_date,1,4)=? '
                       'AND id<>? AND (pay_date < ? OR (pay_date = ? AND id < ?))',
                       (e['id'], year, exclude or -1, pay_date, pay_date, exclude or 2 ** 62)).fetchone()[0]
    r = lambda d: items.cents_of(d)   # noqa: E731

    def capped(base_key):
        return max(0, min(gross, _base(conn, base_key) - ytd))
    over = max(0, ytd + gross - max(_base(conn, 'addl_medicare_threshold'), ytd))
    calc = {
        'gross': gross,
        'fed_wh': r(Decimal(gross) * Decimal(e['fed_pct']) / 100),
        'state_wh': r(Decimal(gross) * Decimal(e['state_pct']) / 100),
        'ss_ee': r(capped('ss_wage_base') * _pct(conn, 'ss_rate')),
        'med_ee': r(gross * _pct(conn, 'medicare_rate')) + r(over * _pct(conn, 'addl_medicare_rate')),
        'other_ded': 0,
        'ss_er': r(capped('ss_wage_base') * _pct(conn, 'ss_rate')),
        'med_er': r(gross * _pct(conn, 'medicare_rate')),
        'futa': r(capped('futa_wage_base') * _pct(conn, 'futa_rate')),
        'suta': r(capped('suta_wage_base') * _pct(conn, 'suta_rate')),
    }
    calc.update({k: v for k, v in overrides.items() if v is not None})
    calc['net'] = calc['gross'] - sum(calc[k] for k in WITHHELD)
    calc['ytd_gross_before'] = ytd
    return calc


def _inputs(conn, b, e):
    hours = items.dec(b.get('hours'), 'hours', lo=Decimal(0), hi=Decimal(10000))
    if b.get('gross') not in (None, ''):
        gross = to_cents(b['gross'], 'gross')
    elif e['pay_type'] == 'hourly':
        gross = items.cents_of(hours * e['pay_rate'])
    else:
        gross = e['pay_rate']
    if gross <= 0:
        raise ApiError(400, 'Enter hours or gross pay')
    over = {k: (to_cents(b[k], k) if b.get(k) not in (None, '') else None) for k in AMOUNTS if k != 'gross'}
    if any(v is not None and v < 0 for v in over.values()):
        raise ApiError(400, 'Amounts cannot be negative')
    return hours, gross, over


@route('POST', '/api/payroll/calculate')
def calc_route(ctx):
    e = _emp(ctx.conn, ctx.body.get('employee_id'))
    date = parse_date(ctx.body.get('pay_date') or today(), 'pay_date')
    hours, gross, over = _inputs(ctx.conn, ctx.body, e)
    c = calculate(ctx.conn, e, date, gross, over, as_int(ctx.body.get('paycheck_id'), 'paycheck_id', required=False))
    out = {k: money(v) for k, v in c.items()}
    if c['net'] < 0:
        out['warning'] = 'Withholdings are more than gross pay'
    return out


def save_paycheck(conn, b, pid=None):
    ex = None
    if pid:
        ex = conn.execute('SELECT * FROM paychecks WHERE id=?', (pid,)).fetchone()
        if not ex:
            raise ApiError(404, 'Paycheck not found')
        # Fields left out of an edit keep their saved values. If the pay itself changes (employee, date,
        # hours or gross), taxes not typed in this edit are calculated again.
        keep = {'employee_id': ex['employee_id'], 'pay_date': ex['pay_date'], 'period_start': ex['period_start'],
                'period_end': ex['period_end'], 'bank_account_id': ex['bank_account_id'], 'check_no': ex['check_no'],
                'memo': ex['memo'], 'hours': ex['hours']}
        pay_changed = any(k in b and str(b[k]) != str(keep.get(k, '')) for k in ('employee_id', 'pay_date', 'hours')) \
            or ('gross' in b and to_cents(b['gross'], 'gross') != ex['gross'])
        if not ('hours' in b and 'gross' not in b):
            keep['gross'] = money(ex['gross'])
        if not pay_changed:
            keep.update({k: money(ex[k]) for k in AMOUNTS if k != 'gross'})
        keep.update(b)
        b = keep
    e = _emp(conn, b.get('employee_id') or (ex and ex['employee_id']))
    if not e['active'] and not (ex and ex['employee_id'] == e['id']):
        raise ApiError(400, 'This employee is inactive')
    date = parse_date(b.get('pay_date') or (ex and ex['pay_date']) or today(), 'pay_date')
    ps = parse_date(b.get('period_start'), 'period_start', required=False) or ''
    pe = parse_date(b.get('period_end'), 'period_end', required=False) or ''
    if ps and pe and ps > pe:
        raise ApiError(400, 'Pay period start must be before its end')
    hours, gross, over = _inputs(conn, b, e)
    c = calculate(conn, e, date, gross, over, pid)
    if c['net'] < 0:
        raise ApiError(400, 'Withholdings (%.2f) are more than gross pay (%.2f)'
                       % ((c['gross'] - c['net']) / 100.0, c['gross'] / 100.0))
    bank = conn.execute('SELECT * FROM accounts WHERE id=?', (as_int(b.get('bank_account_id'), 'bank account'),)).fetchone()
    if not bank or bank['type'] != 'Bank':
        raise ApiError(400, 'Pay from a bank account')
    check_no, memo = as_str(b.get('check_no'), 'check_no'), as_str(b.get('memo'), 'memo')
    label = 'Paycheck: %s' % e['name']
    employer = sum(c[k] for k in EMPLOYER)
    liab = sum(c[k] for k in WITHHELD) + employer
    tid = ledger.post_txn(conn, date, 'Paycheck', [
        {'account_id': wage_account(conn), 'debit': c['gross'], 'memo': label},
        {'account_id': db.system_account(conn, 'ptx'), 'debit': employer, 'memo': 'Employer payroll taxes'},
        {'account_id': bank['id'], 'credit': c['net'], 'memo': label},
        {'account_id': db.system_account(conn, 'pl'), 'credit': liab, 'memo': 'Withholdings and employer taxes'},
    ], ref=check_no, memo=memo or label, txn_id=ex['txn_id'] if ex else None)
    cols = ('employee_id', 'pay_date', 'period_start', 'period_end', 'hours') + AMOUNTS + ('net', 'bank_account_id',
                                                                                          'check_no', 'memo')
    vals = (e['id'], date, ps, pe, float(hours)) + tuple(c[k] for k in AMOUNTS) + (c['net'], bank['id'], check_no, memo)
    if ex:
        conn.execute('UPDATE paychecks SET %s WHERE id=?' % ','.join('%s=?' % k for k in cols), vals + (pid,))
        return pid
    return conn.execute('INSERT INTO paychecks(txn_id, %s) VALUES (?,%s)' % (','.join(cols), ','.join('?' * len(cols))),
                        (tid,) + vals).lastrowid


def paycheck_json(conn, pid):
    p = conn.execute('SELECT p.*, e.name AS employee, a.name AS bank FROM paychecks p '
                     'JOIN employees e ON e.id = p.employee_id JOIN accounts a ON a.id = p.bank_account_id '
                     'WHERE p.id=?', (pid,)).fetchone()
    if not p:
        raise ApiError(404, 'Paycheck not found')
    out = {k: p[k] for k in ('id', 'txn_id', 'employee_id', 'employee', 'pay_date', 'period_start', 'period_end',
                             'hours', 'bank_account_id', 'bank', 'check_no', 'memo')}
    out.update({k: money(p[k]) for k in AMOUNTS + ('net',)})
    out['employer_taxes'] = money(sum(p[k] for k in EMPLOYER))
    out['reconciled'] = bool(conn.execute("SELECT 1 FROM lines WHERE txn_id=? AND recon_status='R'",
                                          (p['txn_id'],)).fetchone())
    return out


@route('GET', '/api/paychecks')
def list_paychecks(ctx):
    sql, args = 'SELECT id FROM paychecks WHERE 1=1', []
    if ctx.q.get('employee_id'):
        sql += ' AND employee_id=?'
        args.append(as_int(ctx.q['employee_id'], 'employee_id'))
    for key, op in (('start', '>='), ('end', '<=')):
        v = parse_date(ctx.q.get(key), key, required=False)
        if v:
            sql += ' AND pay_date %s ?' % op
            args.append(v)
    return [paycheck_json(ctx.conn, r[0]) for r in ctx.conn.execute(sql + ' ORDER BY pay_date DESC, id DESC', args)]


@route('GET', '/api/paychecks/<id>')
def read_paycheck(ctx):
    return paycheck_json(ctx.conn, ctx.p['id'])


@route('POST', '/api/paychecks')
def create_paycheck(ctx):
    return paycheck_json(ctx.conn, save_paycheck(ctx.conn, ctx.body))


@route('PUT', '/api/paychecks/<id>')
def update_paycheck(ctx):
    return paycheck_json(ctx.conn, save_paycheck(ctx.conn, ctx.body, ctx.p['id']))


@route('DELETE', '/api/paychecks/<id>')
def delete_paycheck(ctx):
    p = paycheck_json(ctx.conn, ctx.p['id'])
    ledger.delete_txn(ctx.conn, p['txn_id'])
    return {'deleted': True}


@route('GET', '/api/payroll/liability_payments')
def list_liab(ctx):
    return items.liability_payments(ctx.conn, 'Payroll Liability Payment')


@route('POST', '/api/payroll/liability_payments')
def pay_liab(ctx):
    return items.liability_payment(ctx.conn, ctx.body, 'Payroll Liability Payment', 'pl')


@route('GET', '/api/payroll/settings')
def get_payroll_settings(ctx):
    keys = ('ss_rate', 'ss_wage_base', 'medicare_rate', 'addl_medicare_rate', 'addl_medicare_threshold',
            'futa_rate', 'futa_wage_base', 'suta_rate', 'suta_wage_base')
    out = {k: float(db.get_setting(ctx.conn, k)) for k in keys}
    out['wage_account_id'] = wage_account(ctx.conn)
    return out


@route('PUT', '/api/payroll/settings')
def put_payroll_settings(ctx):
    for k, v in ctx.body.items():
        if k == 'wage_account_id':
            a = ctx.conn.execute('SELECT type FROM accounts WHERE id=?', (as_int(v, k),)).fetchone()
            if not a or a['type'] not in ('Expense', 'Cost of Goods Sold'):
                raise ApiError(400, 'Wages must post to an expense account')
            db.set_setting(ctx.conn, 'payroll_wage_account', as_int(v, k))
        elif k in db.DEFAULTS and k != 'home_currency':
            if v in (None, '') or isinstance(v, str) and not v.strip():
                raise ApiError(400, '%s cannot be blank' % k)
            hi = Decimal(100) if k.endswith('_rate') else Decimal(10 ** 8)
            db.set_setting(ctx.conn, k, format(items.dec(v, k, lo=Decimal(0), hi=hi).normalize(), 'f'))
        else:
            raise ApiError(400, 'Unknown setting %s' % k)
    return get_payroll_settings(ctx)
