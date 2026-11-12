"""Shared engine for sales (invoices / received payments, A/R) and purchases (bills / bill payments, A/P).

Amounts on documents and payments are in the document currency (cents). The ledger is always in the home
currency: each document stores its exchange rate (home units per 1 foreign unit) and its home total, and each
payment application stores the home amount it took off A/R or A/P. Differences between the rate of a document
and the rate of its payment are posted to Exchange Gain or Loss.
"""
import re
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from . import db, items, ledger
from .util import ApiError, add_days, as_int, as_list, as_str, money, parse_date, to_cents, today

TERMS = {'Due on receipt': 0, 'Net 15': 15, 'Net 30': 30, 'Net 60': 60}


class Side:
    def __init__(self, **kw):
        self.__dict__.update(kw)


SALES = Side(kind='customer', doc='invoices', lines='invoice_lines', fk='invoice_id', name_col='customer_id',
             pay='payments', apps='payment_apps', acct_col='deposit_account_id', ctrl='ar',
             doc_type='Invoice', pay_type='Payment', label='Invoice', sign=1, start=1001,
             acct_label='deposit_account')
PURCHASE = Side(kind='vendor', doc='bills', lines='bill_lines', fk='bill_id', name_col='vendor_id',
                pay='bill_payments', apps='bill_payment_apps', acct_col='pay_account_id', ctrl='ap',
                doc_type='Bill', pay_type='Bill Payment', label='Bill', sign=-1, start=None,
                acct_label='pay_account')


def home_currency(conn):
    return db.get_setting(conn, 'home_currency', 'USD')


def to_home(amount, rate):
    return int((Decimal(amount) * rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def parse_rate(v, currency, home):
    if currency == home:
        return Decimal(1)
    if v in (None, ''):
        raise ApiError(400, 'Enter the exchange rate: how many %s for 1 %s' % (home, currency))
    if isinstance(v, (bool, list, dict)):
        raise ApiError(400, 'exchange_rate: invalid number')
    sv = str(v).strip()
    if ',' in sv and '.' not in sv:
        raise ApiError(400, 'Use a dot for decimals in the exchange rate (for example 1.10)')
    try:
        r = Decimal(sv.replace(',', ''))
    except InvalidOperation:
        raise ApiError(400, 'exchange_rate: invalid number')
    if not r.is_finite() or r <= 0 or r > 10 ** 6:
        raise ApiError(400, 'exchange_rate must be greater than zero')
    return r


def rate_str(r):
    return format(r.normalize(), 'f')


def name_row(conn, side, nid):
    n = conn.execute('SELECT * FROM names WHERE id=? AND kind=?', (as_int(nid, side.kind), side.kind)).fetchone()
    if not n:
        raise ApiError(400, 'Choose a %s' % side.kind)
    return n


def currency_of(conn, name):
    return name['currency'] or home_currency(conn)


def applied(conn, side, doc_id, as_of=None, exclude_payment=None):
    """(document-currency amount, home amount) applied to a document."""
    sql = ('SELECT COALESCE(SUM(a.amount), 0), COALESCE(SUM(a.home), 0) FROM %s a JOIN %s p ON p.id = a.payment_id '
           'WHERE a.%s=?' % (side.apps, side.pay, side.fk))
    args = [doc_id]
    if as_of:
        sql += ' AND p.date <= ?'
        args.append(as_of)
    if exclude_payment:
        sql += ' AND a.payment_id <> ?'
        args.append(exclude_payment)
    return tuple(conn.execute(sql, args).fetchone())


def status(total, paid, due_date):
    if total - paid <= 0:
        return 'Paid'
    if due_date < today():
        return 'Overdue'
    return 'Partial' if paid else 'Open'


def get_doc(conn, side, doc_id, detail=True):
    d = conn.execute('SELECT d.*, n.name AS nm FROM %s d JOIN names n ON n.id = d.%s WHERE d.id=?'
                     % (side.doc, side.name_col), (doc_id,)).fetchone()
    if not d:
        raise ApiError(404, '%s not found' % side.label)
    paid, paid_home = applied(conn, side, d['id'])
    home = home_currency(conn)
    out = {k: d[k] for k in ('id', 'txn_id', 'number', 'date', 'due_date', 'terms', 'memo', 'tax_rate_id')}
    out[side.name_col], out[side.kind] = d[side.name_col], d['nm']
    out.update(total=money(d['total']), applied=money(paid), balance=money(d['total'] - paid),
               balance_cents=d['total'] - paid, status=status(d['total'], paid, d['due_date']),
               currency=d['currency'] or home, exchange_rate=float(d['exchange_rate']),
               total_home=money(d['total_home']), balance_home=money(d['total_home'] - paid_home),
               balance_home_cents=d['total_home'] - paid_home, tax=money(d['tax']),
               subtotal=money(d['total'] - d['tax']))
    if detail:
        out['lines'] = [{
            'description': l['description'], 'qty': l['qty'], 'rate': l['rate'], 'amount': money(l['amount']),
            'account_id': l['account_id'], 'account': l['account'], 'item_id': l['item_id'], 'item': l['item'],
            'taxable': bool(l['taxable']),
        } for l in conn.execute('SELECT x.*, a.name AS account, i.name AS item FROM %s x '
                                'JOIN accounts a ON a.id = x.account_id LEFT JOIN items i ON i.id = x.item_id '
                                'WHERE x.%s=? ORDER BY x.seq' % (side.lines, side.fk), (d['id'],))]
        out['payments'] = [{
            'payment_id': p['id'], 'date': p['date'], 'amount': money(p['amount']), 'ref': p['ref'],
            'method': p['method'],
        } for p in conn.execute('SELECT p.id, p.date, a.amount, p.ref, p.method FROM %s a JOIN %s p '
                                'ON p.id = a.payment_id WHERE a.%s=? ORDER BY p.date, p.id'
                                % (side.apps, side.pay, side.fk), (d['id'],))]
    return out


def open_docs(conn, side, name_id=None):
    sql, args = 'SELECT id FROM %s' % side.doc, []
    if name_id:
        sql += ' WHERE %s=?' % side.name_col
        args.append(name_id)
    out = [get_doc(conn, side, r['id'], detail=False) for r in conn.execute(sql + ' ORDER BY date, id', args)]
    return [d for d in out if d['balance_cents'] > 0]


def list_docs(conn, side, q):
    sql, args = 'SELECT id FROM %s WHERE 1=1' % side.doc, []
    nid = q.get(side.name_col)
    if nid:
        sql += ' AND %s=?' % side.name_col
        args.append(as_int(nid, side.name_col))
    for key, op in (('start', '>='), ('end', '<=')):
        v = parse_date(q.get(key), key, required=False)
        if v:
            sql += ' AND date %s ?' % op
            args.append(v)
    out = [get_doc(conn, side, r['id'], detail=False)
           for r in conn.execute(sql + ' ORDER BY date DESC, id DESC', args).fetchall()]
    st = (q.get('status') or 'all').lower()
    if st == 'open':
        out = [d for d in out if d['balance_cents'] > 0]
    elif st in ('paid', 'overdue', 'partial'):
        out = [d for d in out if d['status'].lower() == st]
    return out


def _due(body, date, side):
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
        raise ApiError(400, 'Due date cannot be before the %s date' % side.label.lower())
    return terms, due


def _lines(conn, side, body, existing_items):
    out = []
    for i, ln in enumerate(as_list(body.get('lines') or [], 'lines', dict), 1):
        item = None
        if ln.get('item_id') not in (None, ''):
            item = items.get_item(conn, as_int(ln['item_id'], 'Line %d product/service' % i))
            if not item['active'] and item['id'] not in existing_items:
                raise ApiError(400, 'Line %d: "%s" is inactive' % (i, item['name']))
        qty = items.dec(ln.get('qty') if ln.get('qty') not in (None, '') else 1, 'qty')
        has_rate = ln.get('rate') not in (None, '')
        has_amt = ln.get('amount') not in (None, '')
        if has_rate:
            rate = items.dec(ln.get('rate'), 'rate')
        elif item and not has_amt:
            rate = Decimal(item['sales_price'] if side is SALES else item['purchase_cost']) / 100
        else:
            rate = Decimal(0)
        calc = items.cents_of(qty * rate * 100)
        if has_amt:
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
        desc = as_str(ln.get('description'), 'description') or (item and (item['description'] or item['name'])) or ''
        if amt == 0 and not desc and not item:
            continue
        aid = ln.get('account_id')
        if aid in (None, '') and item:
            if side is SALES:
                aid = item['income_account_id']
            else:
                aid = item['asset_account_id'] if item['type'] == 'inventory' else item['expense_account_id']
            if not aid:
                raise ApiError(400, 'Line %d: "%s" has no %s account. Edit the item or choose an account.'
                               % (i, item['name'], 'income' if side is SALES else 'expense'))
        acct = conn.execute('SELECT * FROM accounts WHERE id=?',
                            (as_int(aid, 'Line %d account' % i),)).fetchone()
        if not acct:
            raise ApiError(400, 'Line %d: choose an account' % i)
        bad = ('Accounts Receivable', 'Accounts Payable', 'Bank', 'Credit Card') if side is SALES \
            else ('Accounts Receivable', 'Accounts Payable')
        if acct['type'] in bad:
            raise ApiError(400, 'Line %d: choose an %s account, not a bank, card, A/R or A/P account'
                           % (i, 'income' if side is SALES else 'expense'))
        if side is SALES and (acct['system'] == 'inv' or conn.execute(
                "SELECT 1 FROM items WHERE type='inventory' AND asset_account_id=? LIMIT 1", (acct['id'],)).fetchone()):
            raise ApiError(400, 'Line %d: sales cannot be posted to an inventory asset account' % i)
        if item and item['type'] == 'inventory' and side is PURCHASE and acct['id'] != item['asset_account_id']:
            raise ApiError(400, 'Line %d: inventory purchases go to the item\'s inventory asset account' % i)
        if item and item['type'] == 'inventory' and qty <= 0 and amt > 0:
            raise ApiError(400, 'Line %d: enter the quantity for inventory item "%s"' % (i, item['name']))
        taxable = ln['taxable'] if 'taxable' in ln and ln['taxable'] is not None else \
            (bool(item['taxable']) if item else True)
        out.append({'description': desc, 'qty': qty, 'rate': rate, 'amount': amt, 'account_id': acct['id'],
                    'item': item, 'taxable': 1 if taxable in (True, 1, '1', 'true') else 0})
    return out


def save_doc(conn, side, body, doc_id=None):
    existing = None
    if doc_id:
        existing = conn.execute('SELECT * FROM %s WHERE id=?' % side.doc, (doc_id,)).fetchone()
        if not existing:
            raise ApiError(404, '%s not found' % side.label)
        body = dict(body)
        for k in ('date', 'memo', side.name_col):
            body.setdefault(k, existing[k])
        if 'terms' not in body and 'due_date' not in body:
            body['terms'], body['due_date'] = existing['terms'], existing['due_date']
    name = name_row(conn, side, body.get(side.name_col))
    date = parse_date(body.get('date') or today())
    terms, due = _due(body, date, side)
    used_items = set()
    if existing:
        used_items = {r[0] for r in conn.execute('SELECT item_id FROM %s WHERE %s=?' % (side.lines, side.fk),
                                                  (doc_id,)) if r[0]}
    lines = _lines(conn, side, body, used_items)
    if not lines:
        raise ApiError(400, 'Add at least one line to the %s' % side.label.lower())
    subtotal = sum(l['amount'] for l in lines)
    tax, tax_rate_id = 0, None
    if side is SALES and body.get('tax_rate_id') not in (None, ''):
        tr = items.tax_rate(conn, body['tax_rate_id'])
        if not tr['active'] and not (existing and existing['tax_rate_id'] == tr['id']):
            raise ApiError(400, 'That tax rate is inactive')
        tax_rate_id = tr['id']
        taxable = sum(l['amount'] for l in lines if l['taxable'])
        tax = items.cents_of(Decimal(taxable) * Decimal(tr['rate']) / 100)
    total = subtotal + tax
    if total <= 0:
        raise ApiError(400, '%s total must be greater than zero' % side.label)
    home = home_currency(conn)
    currency = currency_of(conn, name)
    if body.get('currency') not in (None, '') and str(body['currency']).upper() != currency:
        raise ApiError(400, '%s %s uses %s' % (side.kind.capitalize(), name['name'], currency))
    same_cur = existing and (existing['currency'] or home) == currency
    rate = parse_rate(body.get('exchange_rate') if 'exchange_rate' in body or not same_cur
                      else existing['exchange_rate'], currency, home)
    number = as_str(body.get('number'), 'number')
    if existing:
        paid, _ = applied(conn, side, doc_id)
        if total < paid:
            raise ApiError(400, '%s total (%.2f) cannot be less than the payments already applied (%.2f)'
                           % (side.label, total / 100.0, paid / 100.0))
        if paid and existing[side.name_col] != name['id']:
            raise ApiError(400, 'The %s cannot change after payments have been applied' % side.kind)
        if paid and Decimal(existing['exchange_rate']) != rate:
            raise ApiError(400, 'The exchange rate cannot change after payments have been applied')
        if 'number' not in body:
            number = existing['number']
    if side is SALES:
        if not number:
            number = ledger.next_ref(conn, 'Invoice', start=1001)
        if conn.execute('SELECT 1 FROM invoices WHERE number=? AND id<>?', (number, doc_id or 0)).fetchone():
            raise ApiError(409, 'Invoice number %s is already in use' % number)
    elif number and conn.execute('SELECT 1 FROM bills WHERE number=? AND vendor_id=? AND id<>?',
                                 (number, name['id'], doc_id or 0)).fetchone():
        raise ApiError(409, 'Bill number %s from this vendor is already entered' % number)
    memo = as_str(body.get('memo'), 'memo')
    ctrl = db.system_account(conn, side.ctrl)
    s = side.sign
    post = []
    for l in lines:
        post.append({'account_id': l['account_id'], 'debit' if s < 0 else 'credit': to_home(l['amount'], rate),
                     'name_id': name['id'], 'memo': l['description']})
    if tax:
        post.append({'account_id': db.system_account(conn, 'tax'), 'credit': to_home(tax, rate), 'name_id': name['id'],
                     'memo': 'Sales tax'})
    # The home total is the document total converted once (as QuickBooks does); any rounding difference
    # from converting line by line goes on the largest line.
    home_total = to_home(total, rate)
    key = 'debit' if s < 0 else 'credit'
    diff = home_total - sum(p[key] for p in post)
    if diff:
        max(post, key=lambda p: abs(p[key]))[key] += diff
    post.insert(0, {'account_id': ctrl, 'debit' if s > 0 else 'credit': home_total, 'name_id': name['id'],
                    'memo': memo})
    txn_id = existing['txn_id'] if existing else None
    # Inventory: what the items cost goes from Inventory Asset to Cost of Goods Sold when sold.
    moves = []
    for l in lines:
        it = l['item']
        if not it or it['type'] != 'inventory':
            continue
        if side is SALES:     # cost is worked out by items.recost(), in date order
            moves.append((it['id'], -l['qty'], 0, 'out', it['expense_account_id'], it['asset_account_id']))
        else:
            moves.append((it['id'], l['qty'], to_home(l['amount'], rate), 'in', None, None))
    before = {r[0] for r in conn.execute('SELECT item_id FROM inv_moves WHERE txn_id=?', (txn_id,))} if txn_id else set()
    tid = ledger.post_txn(conn, date, side.doc_type, post, ref=number, memo=memo, name_id=name['id'],
                          txn_id=txn_id)
    conn.execute('DELETE FROM inv_moves WHERE txn_id=?', (tid,))
    for iid, q, v, kind, cogs, asset in moves:
        items.add_move(conn, iid, tid, date, q, v, kind, cogs, asset)
    for iid in before | {m[0] for m in moves}:
        items.recost(conn, iid)
    vals = (name['id'], number, date, due, terms, memo, total, home_total, currency if currency != home else '',
            rate_str(rate), tax_rate_id, tax)
    cols = (side.name_col, 'number', 'date', 'due_date', 'terms', 'memo', 'total', 'total_home', 'currency',
            'exchange_rate', 'tax_rate_id', 'tax')
    if existing:
        conn.execute('UPDATE %s SET %s WHERE id=?' % (side.doc, ','.join('%s=?' % c for c in cols)), vals + (doc_id,))
        conn.execute('DELETE FROM %s WHERE %s=?' % (side.lines, side.fk), (doc_id,))
    else:
        doc_id = conn.execute('INSERT INTO %s(txn_id, %s) VALUES (?,%s)' % (side.doc, ','.join(cols),
                                                                           ','.join('?' * len(cols))),
                              (tid,) + vals).lastrowid
    if existing and currency != home:
        for (pid,) in conn.execute('SELECT DISTINCT payment_id FROM %s WHERE %s=?' % (side.apps, side.fk),
                                   (doc_id,)).fetchall():
            p = conn.execute('SELECT * FROM %s WHERE id=?' % side.pay, (pid,)).fetchone()
            save_pay(conn, side, {'name_id': p[side.name_col], 'date': p['date'], 'amount_cents': p['amount'],
                                  'account_id': p[side.acct_col], 'exchange_rate': p['exchange_rate'],
                                  'method': p['method'], 'ref': p['ref'], 'memo': p['memo'], 'applications': None}, pid)
    for i, l in enumerate(lines):
        conn.execute('INSERT INTO %s(%s, seq, description, qty, rate, amount, account_id, item_id, taxable) '
                     'VALUES (?,?,?,?,?,?,?,?,?)' % (side.lines, side.fk),
                     (doc_id, i, l['description'], float(l['qty']), float(l['rate']), l['amount'], l['account_id'],
                      l['item']['id'] if l['item'] else None, l['taxable']))
    return doc_id


# ---------------------------------------------------------------- payments

def save_pay(conn, side, data, pay_id=None):
    """data: name_id, date, amount_cents, account_id, exchange_rate, method, ref, memo,
    applications [{doc_id, amount_cents}] or None (oldest first)."""
    name = name_row(conn, side, data.get('name_id'))
    date = parse_date(data.get('date') or today())
    amount = data['amount_cents']
    if amount <= 0:
        raise ApiError(400, 'Payment amount must be greater than zero')
    if side is SALES:
        acct = conn.execute('SELECT * FROM accounts WHERE id=?',
                            (data.get('account_id') or db.system_account(conn, 'udf'),)).fetchone()
        if not acct or not (acct['type'] == 'Bank' or acct['system'] == 'udf'):
            raise ApiError(400, 'Deposit to a bank account or Undeposited Funds')
    else:
        acct = conn.execute('SELECT * FROM accounts WHERE id=?', (data.get('account_id'),)).fetchone() \
            if data.get('account_id') else None
        if not acct or acct['type'] not in ('Bank', 'Credit Card'):
            raise ApiError(400, 'Pay from a bank or credit card account')
    home = home_currency(conn)
    currency = currency_of(conn, name)
    existing = None
    if pay_id:
        existing = conn.execute('SELECT * FROM %s WHERE id=?' % side.pay, (pay_id,)).fetchone()
        if not existing:
            raise ApiError(404, 'Payment not found')
        if data.get('applications') is None and existing[side.name_col] == name['id']:
            data = dict(data, applications=[{'doc_id': a[0], 'amount_cents': a[1]} for a in conn.execute(
                'SELECT %s, amount FROM %s WHERE payment_id=?' % (side.fk, side.apps), (pay_id,))])
        if data.get('exchange_rate') in (None, '') and currency != home:
            data = dict(data, exchange_rate=existing['exchange_rate'])
        conn.execute('DELETE FROM %s WHERE payment_id=?' % side.apps, (pay_id,))
    rate = parse_rate(data.get('exchange_rate'), currency, home)
    apps = data.get('applications')
    if apps is None:
        apps, left = [], amount
        for d in open_docs(conn, side, name['id']):
            if left <= 0:
                break
            use = min(left, d['balance_cents'])
            apps.append({'doc_id': d['id'], 'amount_cents': use})
            left -= use
    merged = {}
    for a in apps:
        if a['amount_cents'] < 0:
            raise ApiError(400, 'Applied amounts cannot be negative')
        if a['amount_cents']:
            merged[a['doc_id']] = merged.get(a['doc_id'], 0) + a['amount_cents']
    if sum(merged.values()) > amount:
        raise ApiError(400, 'You applied more (%.2f) than the payment amount (%.2f)'
                       % (sum(merged.values()) / 100.0, amount / 100.0))
    homes = {}
    for doc_id, amt in merged.items():
        d = conn.execute('SELECT * FROM %s WHERE id=?' % side.doc, (doc_id,)).fetchone()
        if not d:
            raise ApiError(400, 'Unknown %s in the applied amounts' % side.label.lower())
        if d[side.name_col] != name['id']:
            raise ApiError(400, '%s #%s belongs to a different %s' % (side.label, d['number'], side.kind))
        other, other_home = applied(conn, side, doc_id)
        if amt > d['total'] - other:
            raise ApiError(400, '%s #%s: applied amount (%.2f) is more than the open balance (%.2f)'
                           % (side.label, d['number'], amt / 100.0, (d['total'] - other) / 100.0))
        # The payment that settles a document takes off exactly what is left of it in home currency.
        homes[doc_id] = d['total_home'] - other_home if other + amt == d['total'] \
            else to_home(amt, Decimal(d['exchange_rate']))
    ctrl_home = sum(homes.values()) + to_home(amount - sum(merged.values()), rate)
    bank_home = to_home(amount, rate)
    memo, ref = as_str(data.get('memo'), 'memo'), as_str(data.get('ref'), 'ref')
    method = as_str(data.get('method'), 'method')
    ctrl = db.system_account(conn, side.ctrl)
    if side is SALES:
        post = [{'account_id': acct['id'], 'debit': bank_home, 'name_id': name['id'], 'memo': memo},
                {'account_id': ctrl, 'credit': ctrl_home, 'name_id': name['id'], 'memo': memo},
                {'account_id': db.system_account(conn, 'fx'), 'credit': bank_home - ctrl_home, 'memo': 'Exchange gain or loss'}]
    else:
        post = [{'account_id': acct['id'], 'credit': bank_home, 'name_id': name['id'], 'memo': memo},
                {'account_id': ctrl, 'debit': ctrl_home, 'name_id': name['id'], 'memo': memo},
                {'account_id': db.system_account(conn, 'fx'), 'debit': bank_home - ctrl_home, 'memo': 'Exchange gain or loss'}]
    tid = ledger.post_txn(conn, date, side.pay_type, post, ref=ref, memo=memo, name_id=name['id'],
                          txn_id=existing['txn_id'] if existing else None)
    cols = (side.name_col, 'date', 'amount', side.acct_col, 'method', 'ref', 'memo', 'exchange_rate', 'ctrl_home')
    vals = (name['id'], date, amount, acct['id'], method, ref, memo, rate_str(rate), ctrl_home)
    if existing:
        conn.execute('UPDATE %s SET %s WHERE id=?' % (side.pay, ','.join('%s=?' % c for c in cols)), vals + (pay_id,))
    else:
        pay_id = conn.execute('INSERT INTO %s(txn_id, %s) VALUES (?,%s)' % (side.pay, ','.join(cols),
                                                                           ','.join('?' * len(cols))),
                              (tid,) + vals).lastrowid
    for doc_id, amt in merged.items():
        conn.execute('INSERT INTO %s(payment_id, %s, amount, home) VALUES (?,?,?,?)' % (side.apps, side.fk),
                     (pay_id, doc_id, amt, homes[doc_id]))
    return pay_id


def get_pay(conn, side, pay_id):
    p = conn.execute('SELECT p.*, n.name AS nm, a.name AS acct FROM %s p JOIN names n ON n.id = p.%s '
                     'JOIN accounts a ON a.id = p.%s WHERE p.id=?' % (side.pay, side.name_col, side.acct_col),
                     (pay_id,)).fetchone()
    if not p:
        raise ApiError(404, 'Payment not found')
    apps = conn.execute('SELECT a.%s AS doc_id, a.amount, d.number, d.date FROM %s a JOIN %s d ON d.id = a.%s '
                        'WHERE a.payment_id=? ORDER BY d.date, d.id' % (side.fk, side.apps, side.doc, side.fk),
                        (pay_id,)).fetchall()
    total_applied = sum(a['amount'] for a in apps)
    out = {k: p[k] for k in ('id', 'txn_id', 'date', 'method', 'ref', 'memo')}
    out[side.name_col], out[side.kind] = p[side.name_col], p['nm']
    out[side.acct_col], out[side.acct_label] = p[side.acct_col], p['acct']
    out.update(amount=money(p['amount']), applied=money(total_applied), unapplied=money(p['amount'] - total_applied),
               exchange_rate=float(p['exchange_rate']),
               currency=conn.execute('SELECT currency FROM names WHERE id=?', (p[side.name_col],)).fetchone()[0]
               or home_currency(conn),
               applications=[{side.fk: a['doc_id'], 'number': a['number'], 'date': a['date'],
                              'amount': money(a['amount'])} for a in apps])
    return out


def pay_data(side, body):
    apps = body.get('applications')
    if apps is not None:
        apps = [{'doc_id': as_int(a.get(side.fk), side.fk), 'amount_cents': to_cents(a.get('amount'), 'applied amount')}
                for a in as_list(apps, 'applications', dict)]
    return {'name_id': body.get(side.name_col), 'date': body.get('date'),
            'amount_cents': to_cents(body.get('amount'), 'amount'),
            'account_id': as_int(body.get(side.acct_col), side.acct_col, required=False),
            'exchange_rate': body.get('exchange_rate'),
            'method': body.get('method'), 'ref': body.get('ref'), 'memo': body.get('memo'), 'applications': apps}


def list_pays(conn, side, q):
    sql, args = 'SELECT id FROM %s WHERE 1=1' % side.pay, []
    if q.get(side.name_col):
        sql += ' AND %s=?' % side.name_col
        args.append(as_int(q[side.name_col], side.name_col))
    return [get_pay(conn, side, r['id']) for r in conn.execute(sql + ' ORDER BY date DESC, id DESC', args).fetchall()]
