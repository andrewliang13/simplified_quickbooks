"""Customer invoices and received payments (accounts receivable)."""
import re
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from . import db, ledger
from .router import route
from .util import ApiError, add_days, as_int, as_list, as_str, money, parse_date, to_cents, today

TERMS = {'Due on receipt': 0, 'Net 15': 15, 'Net 30': 30, 'Net 60': 60}


def _customer(conn, cid):
    c = conn.execute("SELECT * FROM names WHERE id=? AND kind='customer'", (as_int(cid, 'customer'),)).fetchone()
    if not c:
        raise ApiError(400, 'Choose a customer')
    return c


def _applied(conn, invoice_id, as_of=None):
    sql = ('SELECT COALESCE(SUM(pa.amount), 0) FROM payment_apps pa JOIN payments p ON p.id = pa.payment_id '
           'WHERE pa.invoice_id=?')
    args = [invoice_id]
    if as_of:
        sql += ' AND p.date <= ?'
        args.append(as_of)
    return conn.execute(sql, args).fetchone()[0]


def _status(total, applied, due_date):
    bal = total - applied
    if bal <= 0:
        return 'Paid'
    if due_date < today():
        return 'Overdue'
    return 'Partial' if applied else 'Open'


def get_invoice(conn, invoice_id, detail=True):
    inv = conn.execute('SELECT i.*, n.name AS customer FROM invoices i JOIN names n ON n.id = i.customer_id '
                       'WHERE i.id=?', (invoice_id,)).fetchone()
    if not inv:
        raise ApiError(404, 'Invoice not found')
    applied = _applied(conn, inv['id'])
    out = {k: inv[k] for k in ('id', 'txn_id', 'customer_id', 'customer', 'number', 'date', 'due_date',
                               'terms', 'memo')}
    out.update(total=money(inv['total']), applied=money(applied), balance=money(inv['total'] - applied),
               balance_cents=inv['total'] - applied, status=_status(inv['total'], applied, inv['due_date']))
    if detail:
        out['lines'] = [{
            'description': l['description'], 'qty': l['qty'], 'rate': l['rate'], 'amount': money(l['amount']),
            'account_id': l['account_id'], 'account': l['account'],
        } for l in conn.execute('SELECT il.*, a.name AS account FROM invoice_lines il '
                                'JOIN accounts a ON a.id = il.account_id WHERE il.invoice_id=? ORDER BY il.seq',
                                (inv['id'],))]
        out['payments'] = [{
            'payment_id': p['id'], 'date': p['date'], 'amount': money(p['amount']), 'ref': p['ref'],
            'method': p['method'],
        } for p in conn.execute('SELECT p.id, p.date, pa.amount, p.ref, p.method FROM payment_apps pa '
                                'JOIN payments p ON p.id = pa.payment_id WHERE pa.invoice_id=? ORDER BY p.date, p.id',
                                (inv['id'],))]
    return out


def open_invoices(conn, customer_id=None):
    sql = 'SELECT id FROM invoices'
    args = []
    if customer_id:
        sql += ' WHERE customer_id=?'
        args.append(customer_id)
    out = [get_invoice(conn, r['id'], detail=False)
           for r in conn.execute(sql + ' ORDER BY date, id', args).fetchall()]
    return [i for i in out if i['balance_cents'] > 0]


def _dec(v, field):
    if isinstance(v, (bool, list, dict)):
        raise ApiError(400, '%s: invalid number' % field)
    try:
        d = Decimal(str(v if v not in (None, '') else 0).replace(',', ''))
    except InvalidOperation:
        raise ApiError(400, '%s: invalid number' % field)
    if not d.is_finite() or abs(d) > 10 ** 9:
        raise ApiError(400, '%s: invalid number' % field)
    return d


def save_invoice(conn, body, invoice_id=None):
    cust = _customer(conn, body.get('customer_id'))
    date = parse_date(body.get('date') or today())
    terms = as_str(body.get('terms'), 'terms')
    due = parse_date(body.get('due_date'), 'due_date', required=False)
    if not due:
        net = re.fullmatch(r'net\s*(\d{1,3})', terms.lower())
        if terms in TERMS or not terms:
            due = add_days(date, TERMS.get(terms, 30))
        elif net:
            due = add_days(date, int(net.group(1)))
        else:
            raise ApiError(400, 'Enter a due date for the terms "%s"' % terms)
    if due < date:
        raise ApiError(400, 'Due date cannot be before the invoice date')
    lines = []
    for i, ln in enumerate(as_list(body.get('lines') or [], 'lines', dict), 1):
        qty = _dec(ln.get('qty') if ln.get('qty') not in (None, '') else 1, 'qty')
        has_rate = ln.get('rate') not in (None, '')
        rate = _dec(ln.get('rate'), 'rate')
        calc = int((qty * rate * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        if ln.get('amount') not in (None, ''):
            amt = to_cents(ln['amount'], 'line amount')
            if has_rate and amt != calc:
                raise ApiError(400, 'Line %d: amount %.2f does not equal quantity x rate (%.2f)'
                               % (i, amt / 100.0, calc / 100.0))
            if not has_rate and qty:
                rate = Decimal(amt) / 100 / qty
        else:
            amt = calc
        if abs(amt) > 10 ** 13:
            raise ApiError(400, 'Line %d: amount too large' % i)
        desc = as_str(ln.get('description'), 'description')
        if amt == 0 and not desc:
            continue
        acct = conn.execute('SELECT * FROM accounts WHERE id=?',
                            (as_int(ln.get('account_id'), 'Line %d income account' % i),)).fetchone()
        if not acct:
            raise ApiError(400, 'Line %d: choose an income account' % i)
        if acct['type'] in ('Accounts Receivable', 'Accounts Payable', 'Bank', 'Credit Card'):
            raise ApiError(400, 'Line %d: choose an income account, not a bank, card, A/R or A/P account' % i)
        lines.append({'description': desc, 'qty': float(qty), 'rate': float(rate), 'amount': amt,
                      'account_id': acct['id']})
    if not lines:
        raise ApiError(400, 'Add at least one line to the invoice')
    total = sum(l['amount'] for l in lines)
    if total <= 0:
        raise ApiError(400, 'Invoice total must be greater than zero')
    number = as_str(body.get('number'), 'number')
    existing = None
    if invoice_id:
        existing = conn.execute('SELECT * FROM invoices WHERE id=?', (invoice_id,)).fetchone()
        if not existing:
            raise ApiError(404, 'Invoice not found')
        applied = _applied(conn, invoice_id)
        if total < applied:
            raise ApiError(400, 'Invoice total (%.2f) cannot be less than the payments already applied (%.2f)'
                           % (total / 100.0, applied / 100.0))
        if applied and existing['customer_id'] != cust['id']:
            raise ApiError(400, 'The customer cannot change after payments have been applied')
        number = number or existing['number']
    if not number:
        number = ledger.next_ref(conn, 'Invoice', start=1001)
    if conn.execute('SELECT 1 FROM invoices WHERE number=? AND id<>?', (number, invoice_id or 0)).fetchone():
        raise ApiError(409, 'Invoice number %s is already in use' % number)
    memo = as_str(body.get('memo'), 'memo')
    ar = db.system_account(conn, 'ar')
    post = [{'account_id': ar, 'debit': total, 'name_id': cust['id'], 'memo': memo}]
    post += [{'account_id': l['account_id'], 'credit': l['amount'], 'name_id': cust['id'],
              'memo': l['description']} for l in lines]
    tid = ledger.post_txn(conn, date, 'Invoice', post, ref=number, memo=memo, name_id=cust['id'],
                          txn_id=existing['txn_id'] if existing else None)
    if existing:
        conn.execute('UPDATE invoices SET customer_id=?, number=?, date=?, due_date=?, terms=?, memo=?, total=? '
                     'WHERE id=?', (cust['id'], number, date, due, terms, memo, total, invoice_id))
        conn.execute('DELETE FROM invoice_lines WHERE invoice_id=?', (invoice_id,))
    else:
        invoice_id = conn.execute(
            'INSERT INTO invoices(txn_id, customer_id, number, date, due_date, terms, memo, total) '
            'VALUES (?,?,?,?,?,?,?,?)', (tid, cust['id'], number, date, due, terms, memo, total)).lastrowid
    for i, l in enumerate(lines):
        conn.execute('INSERT INTO invoice_lines(invoice_id, seq, description, qty, rate, amount, account_id) '
                     'VALUES (?,?,?,?,?,?,?)',
                     (invoice_id, i, l['description'], l['qty'], l['rate'], l['amount'], l['account_id']))
    return invoice_id


def save_payment(conn, data, payment_id=None):
    """data amounts are cents: amount_cents, applications[{invoice_id, amount_cents}] (None = oldest first)."""
    cust = _customer(conn, data.get('customer_id'))
    date = parse_date(data.get('date') or today())
    amount = data['amount_cents']
    if amount <= 0:
        raise ApiError(400, 'Payment amount must be greater than zero')
    dep = conn.execute('SELECT * FROM accounts WHERE id=?',
                       (data.get('deposit_account_id') or db.system_account(conn, 'udf'),)).fetchone()
    if not dep or not (dep['type'] == 'Bank' or dep['system'] == 'udf'):
        raise ApiError(400, 'Deposit to a bank account or Undeposited Funds')
    existing = None
    if payment_id:
        existing = conn.execute('SELECT * FROM payments WHERE id=?', (payment_id,)).fetchone()
        if not existing:
            raise ApiError(404, 'Payment not found')
        if data.get('applications') is None and existing['customer_id'] == cust['id']:
            # Editing other fields keeps the invoices this payment was applied to.
            data = dict(data, applications=[
                {'invoice_id': a['invoice_id'], 'amount_cents': a['amount']} for a in conn.execute(
                    'SELECT invoice_id, amount FROM payment_apps WHERE payment_id=?', (payment_id,))])
        conn.execute('DELETE FROM payment_apps WHERE payment_id=?', (payment_id,))
    apps = data.get('applications')
    if apps is None:
        apps, left = [], amount
        for inv in open_invoices(conn, cust['id']):
            if left <= 0:
                break
            use = min(left, inv['balance_cents'])
            apps.append({'invoice_id': inv['id'], 'amount_cents': use})
            left -= use
    merged = {}
    for a in apps:
        if a['amount_cents'] < 0:
            raise ApiError(400, 'Applied amounts cannot be negative')
        if a['amount_cents']:
            merged[a['invoice_id']] = merged.get(a['invoice_id'], 0) + a['amount_cents']
    if sum(merged.values()) > amount:
        raise ApiError(400, 'You applied more (%.2f) than the payment amount (%.2f)'
                       % (sum(merged.values()) / 100.0, amount / 100.0))
    for inv_id, amt in merged.items():
        try:
            inv = get_invoice(conn, inv_id, detail=False)
        except ApiError:
            raise ApiError(400, 'Unknown invoice in the applied amounts')
        if inv['customer_id'] != cust['id']:
            raise ApiError(400, 'Invoice #%s belongs to a different customer' % inv['number'])
        if amt > inv['balance_cents']:
            raise ApiError(400, 'Invoice #%s: applied amount (%.2f) is more than the open balance (%.2f)'
                           % (inv['number'], amt / 100.0, inv['balance_cents'] / 100.0))
    memo, ref = as_str(data.get('memo'), 'memo'), as_str(data.get('ref'), 'ref')
    method = as_str(data.get('method'), 'method')
    ar = db.system_account(conn, 'ar')
    tid = ledger.post_txn(conn, date, 'Payment', [
        {'account_id': dep['id'], 'debit': amount, 'name_id': cust['id'], 'memo': memo},
        {'account_id': ar, 'credit': amount, 'name_id': cust['id'], 'memo': memo},
    ], ref=ref, memo=memo, name_id=cust['id'], txn_id=existing['txn_id'] if existing else None)
    if existing:
        conn.execute('UPDATE payments SET customer_id=?, date=?, amount=?, deposit_account_id=?, method=?, ref=?, '
                     'memo=? WHERE id=?', (cust['id'], date, amount, dep['id'], method, ref, memo, payment_id))
    else:
        payment_id = conn.execute(
            'INSERT INTO payments(txn_id, customer_id, date, amount, deposit_account_id, method, ref, memo) '
            'VALUES (?,?,?,?,?,?,?,?)', (tid, cust['id'], date, amount, dep['id'], method, ref, memo)).lastrowid
    for inv_id, amt in merged.items():
        conn.execute('INSERT INTO payment_apps(payment_id, invoice_id, amount) VALUES (?,?,?)',
                     (payment_id, inv_id, amt))
    return payment_id


def get_payment(conn, payment_id):
    p = conn.execute('SELECT p.*, n.name AS customer, a.name AS deposit_account FROM payments p '
                     'JOIN names n ON n.id = p.customer_id JOIN accounts a ON a.id = p.deposit_account_id '
                     'WHERE p.id=?', (payment_id,)).fetchone()
    if not p:
        raise ApiError(404, 'Payment not found')
    apps = conn.execute('SELECT pa.invoice_id, pa.amount, i.number, i.date FROM payment_apps pa '
                        'JOIN invoices i ON i.id = pa.invoice_id WHERE pa.payment_id=? ORDER BY i.date, i.id',
                        (payment_id,)).fetchall()
    applied = sum(a['amount'] for a in apps)
    out = {k: p[k] for k in ('id', 'txn_id', 'customer_id', 'customer', 'date', 'deposit_account_id',
                             'deposit_account', 'method', 'ref', 'memo')}
    out.update(amount=money(p['amount']), applied=money(applied), unapplied=money(p['amount'] - applied),
               applications=[{'invoice_id': a['invoice_id'], 'number': a['number'], 'date': a['date'],
                              'amount': money(a['amount'])} for a in apps])
    return out


def _payment_data(body):
    apps = body.get('applications')
    if apps is not None:
        apps = [{'invoice_id': as_int(a.get('invoice_id'), 'invoice_id'),
                 'amount_cents': to_cents(a.get('amount'), 'applied amount')}
                for a in as_list(apps, 'applications', dict)]
    return {'customer_id': body.get('customer_id'), 'date': body.get('date'),
            'amount_cents': to_cents(body.get('amount'), 'amount'),
            'deposit_account_id': as_int(body.get('deposit_account_id'), 'deposit_account_id', required=False),
            'method': body.get('method'), 'ref': body.get('ref'), 'memo': body.get('memo'), 'applications': apps}


# ---------------------------------------------------------------- routes

@route('GET', '/api/invoices')
def list_invoices(ctx):
    sql, args = 'SELECT id FROM invoices WHERE 1=1', []
    if ctx.q.get('customer_id'):
        sql += ' AND customer_id=?'
        args.append(as_int(ctx.q['customer_id'], 'customer_id'))
    for key, op in (('start', '>='), ('end', '<=')):
        v = parse_date(ctx.q.get(key), key, required=False)
        if v:
            sql += ' AND date %s ?' % op
            args.append(v)
    out = [get_invoice(ctx.conn, r['id'], detail=False)
           for r in ctx.conn.execute(sql + ' ORDER BY date DESC, id DESC', args).fetchall()]
    st = (ctx.q.get('status') or 'all').lower()
    if st == 'open':
        out = [i for i in out if i['balance_cents'] > 0]
    elif st in ('paid', 'overdue', 'partial'):
        out = [i for i in out if i['status'].lower() == st]
    return out


@route('GET', '/api/invoices/next_number')
def next_number(ctx):
    return {'number': ledger.next_ref(ctx.conn, 'Invoice', start=1001)}


@route('GET', '/api/invoices/<id>')
def read_invoice(ctx):
    return get_invoice(ctx.conn, ctx.p['id'])


@route('POST', '/api/invoices')
def create_invoice(ctx):
    return get_invoice(ctx.conn, save_invoice(ctx.conn, ctx.body))


@route('PUT', '/api/invoices/<id>')
def update_invoice(ctx):
    return get_invoice(ctx.conn, save_invoice(ctx.conn, ctx.body, ctx.p['id']))


@route('DELETE', '/api/invoices/<id>')
def delete_invoice(ctx):
    inv = get_invoice(ctx.conn, ctx.p['id'], detail=False)
    ledger.delete_txn(ctx.conn, inv['txn_id'])
    return {'deleted': True}


@route('GET', '/api/payments')
def list_payments(ctx):
    sql, args = 'SELECT id FROM payments WHERE 1=1', []
    if ctx.q.get('customer_id'):
        sql += ' AND customer_id=?'
        args.append(as_int(ctx.q['customer_id'], 'customer_id'))
    return [get_payment(ctx.conn, r['id'])
            for r in ctx.conn.execute(sql + ' ORDER BY date DESC, id DESC', args).fetchall()]


@route('GET', '/api/payments/<id>')
def read_payment(ctx):
    return get_payment(ctx.conn, ctx.p['id'])


@route('POST', '/api/payments')
def create_payment(ctx):
    return get_payment(ctx.conn, save_payment(ctx.conn, _payment_data(ctx.body)))


@route('PUT', '/api/payments/<id>')
def update_payment(ctx):
    return get_payment(ctx.conn, save_payment(ctx.conn, _payment_data(ctx.body), ctx.p['id']))


@route('DELETE', '/api/payments/<id>')
def delete_payment(ctx):
    p = get_payment(ctx.conn, ctx.p['id'])
    ledger.delete_txn(ctx.conn, p['txn_id'])
    return {'deleted': True}
