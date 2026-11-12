"""Products and services, inventory quantities, sales tax rates and sales tax payments."""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from . import db, ledger
from .router import route
from .util import ApiError, as_bool, as_int, as_str, money, parse_date, to_cents, today

TYPES = ('service', 'non_inventory', 'inventory')
TYPE_LABEL = {'service': 'Service', 'non_inventory': 'Non-inventory', 'inventory': 'Inventory'}


def dec(v, field, lo=None, hi=Decimal(10 ** 9)):
    if isinstance(v, (bool, list, dict)):
        raise ApiError(400, '%s: invalid number' % field)
    try:
        d = Decimal(str(v if v not in (None, '') else 0).replace(',', '').strip())
    except InvalidOperation:
        raise ApiError(400, '%s: invalid number' % field)
    if not d.is_finite() or abs(d) > hi or (lo is not None and d < lo):
        raise ApiError(400, '%s: invalid number' % field)
    return d


def cents_of(d):
    return int(d.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------- inventory

def on_hand(conn, item_id, as_of=None, exclude_txn=None):
    """(quantity, value in cents) from inventory movements, optionally up to a date and leaving one
    transaction out (the one being saved)."""
    sql, args = 'SELECT COALESCE(SUM(qty), 0), COALESCE(SUM(value), 0) FROM inv_moves WHERE item_id=?', [item_id]
    if as_of:
        sql += ' AND date <= ?'
        args.append(as_of)
    if exclude_txn:
        sql += ' AND txn_id <> ?'
        args.append(exclude_txn)
    q, v = conn.execute(sql, args).fetchone()
    return round(q, 6), v


def unit_cost(conn, item, as_of, exclude_txn=None):
    """Average cost per unit (cents, Decimal) on a date. Falls back to the item's purchase cost when
    nothing is on hand."""
    q, v = on_hand(conn, item['id'], as_of, exclude_txn)
    if q > 0 and v > 0:
        return Decimal(v) / Decimal(str(q))
    return Decimal(item['purchase_cost'])


def add_move(conn, item_id, txn_id, date, qty, value):
    conn.execute('INSERT INTO inv_moves(item_id, txn_id, date, qty, value) VALUES (?,?,?,?,?)',
                 (item_id, txn_id, date, float(qty), int(value)))


def get_item(conn, item_id):
    it = conn.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
    if not it:
        raise ApiError(400, 'Unknown product or service')
    return it


# ---------------------------------------------------------------- items

def _acct(conn, aid, field, types):
    if not aid:
        return None
    a = conn.execute('SELECT * FROM accounts WHERE id=?', (as_int(aid, field),)).fetchone()
    if not a or a['type'] not in types:
        raise ApiError(400, '%s: choose a %s account' % (field, ' / '.join(types)))
    return a['id']


def _first(conn, types):
    r = conn.execute('SELECT id FROM accounts WHERE type IN (%s) AND active=1 ORDER BY number, name LIMIT 1'
                     % ','.join('?' * len(types)), types).fetchone()
    return r[0] if r else None


def _item_json(conn, it):
    out = {k: it[k] for k in ('id', 'name', 'sku', 'type', 'description', 'income_account_id',
                              'expense_account_id', 'asset_account_id')}
    out.update(type_label=TYPE_LABEL[it['type']], sales_price=money(it['sales_price']),
               purchase_cost=money(it['purchase_cost']), taxable=bool(it['taxable']), active=bool(it['active']))
    if it['type'] == 'inventory':
        q, v = on_hand(conn, it['id'])
        out.update(qty_on_hand=q, value=money(v), avg_cost=money(v / q) if q > 0 else None)
    return out


def _item_used(conn, item_id):
    return any(conn.execute(sql, (item_id,)).fetchone() for sql in (
        'SELECT 1 FROM inv_moves WHERE item_id=? LIMIT 1', 'SELECT 1 FROM invoice_lines WHERE item_id=? LIMIT 1',
        'SELECT 1 FROM bill_lines WHERE item_id=? LIMIT 1'))


def _save_item(conn, b, ex=None):
    get = lambda k, d=None: b[k] if k in b else (ex[k] if ex else d)   # noqa: E731
    name = as_str(get('name'), 'name')
    if not name:
        raise ApiError(400, 'Name is required')
    if conn.execute('SELECT 1 FROM items WHERE name=? COLLATE NOCASE AND id<>?',
                    (name, ex['id'] if ex else 0)).fetchone():
        raise ApiError(409, 'A product or service named "%s" already exists' % name)
    itype = get('type', 'service')
    if itype not in TYPES:
        raise ApiError(400, 'Type must be service, non_inventory or inventory')
    if ex and itype != ex['type'] and 'inventory' in (itype, ex['type']) and _item_used(conn, ex['id']):
        raise ApiError(400, 'An item with transactions cannot change to or from Inventory')
    price = to_cents(b['sales_price'], 'sales_price') if 'sales_price' in b else (ex['sales_price'] if ex else 0)
    cost = to_cents(b['purchase_cost'], 'purchase_cost') if 'purchase_cost' in b else (ex['purchase_cost'] if ex else 0)
    if price < 0 or cost < 0:
        raise ApiError(400, 'Prices cannot be negative')
    income = _acct(conn, get('income_account_id'), 'Income account', ('Income', 'Other Income')) or \
        _first(conn, ('Income',))
    if itype == 'inventory':
        expense = _acct(conn, get('expense_account_id'), 'Cost of goods sold account',
                        ('Cost of Goods Sold', 'Expense')) or _first(conn, ('Cost of Goods Sold',))
        asset = _acct(conn, get('asset_account_id'), 'Inventory asset account', ('Other Current Asset',)) or \
            db.system_account(conn, 'inv')
    else:
        expense = _acct(conn, get('expense_account_id'), 'Expense account',
                        ('Expense', 'Cost of Goods Sold', 'Other Expense', 'Other Current Asset', 'Fixed Asset'))
        asset = None
    active = 1 if as_bool(get('active', True), 'active') else 0
    return (name, as_str(get('sku'), 'sku'), itype, as_str(get('description'), 'description'), price, cost,
            income, expense, asset, 1 if as_bool(get('taxable', False), 'taxable') else 0, active)


COLS = ('name', 'sku', 'type', 'description', 'sales_price', 'purchase_cost', 'income_account_id',
        'expense_account_id', 'asset_account_id', 'taxable', 'active')


@route('GET', '/api/items')
def list_items(ctx):
    inc = ctx.q.get('include_inactive') in ('1', 'true')
    return [_item_json(ctx.conn, it) for it in ctx.conn.execute('SELECT * FROM items ORDER BY name COLLATE NOCASE')
            if inc or it['active']]


@route('GET', '/api/items/<id>')
def read_item(ctx):
    it = ctx.conn.execute('SELECT * FROM items WHERE id=?', (ctx.p['id'],)).fetchone()
    if not it:
        raise ApiError(404, 'Product or service not found')
    return _item_json(ctx.conn, it)


@route('POST', '/api/items')
def create_item(ctx):
    vals = _save_item(ctx.conn, ctx.body)
    iid = ctx.conn.execute('INSERT INTO items(%s) VALUES (%s)' % (','.join(COLS), ','.join('?' * len(COLS))),
                           vals).lastrowid
    qty = dec(ctx.body.get('qty_on_hand'), 'qty_on_hand', lo=Decimal(0))
    if qty:
        if vals[2] != 'inventory':
            raise ApiError(400, 'Only inventory items have a quantity on hand')
        it = get_item(ctx.conn, iid)
        date = parse_date(ctx.body.get('as_of') or today(), 'as_of')
        value = cents_of(qty * Decimal(it['purchase_cost']))
        memo = 'Opening quantity: %s' % it['name']
        ref = ledger.next_ref(ctx.conn, 'Inventory Qty Adjust')
        if value:
            tid = ledger.post_txn(ctx.conn, date, 'Inventory Qty Adjust', [
                {'account_id': it['asset_account_id'], 'debit': value, 'memo': memo},
                {'account_id': db.system_account(ctx.conn, 'obe'), 'credit': value, 'memo': memo}],
                ref=ref, memo=memo)
        else:   # zero-cost stock: record the quantity without a ledger amount
            tid = ctx.conn.execute('INSERT INTO transactions(date, type, ref, memo) VALUES (?,?,?,?)',
                                   (date, 'Inventory Qty Adjust', ref, memo)).lastrowid
        add_move(ctx.conn, iid, tid, date, qty, value)
    ctx.p['id'] = iid
    return read_item(ctx)


@route('PUT', '/api/items/<id>')
def update_item(ctx):
    ex = ctx.conn.execute('SELECT * FROM items WHERE id=?', (ctx.p['id'],)).fetchone()
    if not ex:
        raise ApiError(404, 'Product or service not found')
    vals = _save_item(ctx.conn, ctx.body, ex)
    ctx.conn.execute('UPDATE items SET %s WHERE id=?' % ','.join('%s=?' % c for c in COLS), vals + (ex['id'],))
    return read_item(ctx)


@route('DELETE', '/api/items/<id>')
def delete_item(ctx):
    if not ctx.conn.execute('SELECT 1 FROM items WHERE id=?', (ctx.p['id'],)).fetchone():
        raise ApiError(404, 'Product or service not found')
    if _item_used(ctx.conn, ctx.p['id']):
        raise ApiError(409, 'This item has transactions and cannot be deleted. Make it inactive instead.')
    ctx.conn.execute('DELETE FROM items WHERE id=?', (ctx.p['id'],))
    return {'deleted': True}


@route('POST', '/api/items/<id>/adjust')
def adjust_qty(ctx):
    """Inventory quantity adjustment (count correction, shrinkage, damage). Increases are valued at the
    given unit cost (default: current average cost); decreases at the average cost."""
    conn, b = ctx.conn, ctx.body
    it = get_item(conn, ctx.p['id'])
    if it['type'] != 'inventory':
        raise ApiError(400, 'Only inventory items can be adjusted')
    date = parse_date(b.get('date') or today())
    change = dec(b.get('qty_change'), 'qty_change')
    if not change:
        raise ApiError(400, 'Enter the change in quantity (negative to reduce)')
    avg = unit_cost(conn, it, date)
    unit = Decimal(to_cents(b['unit_cost'], 'unit_cost')) if b.get('unit_cost') not in (None, '') and change > 0 else avg
    value = cents_of(change * unit)
    offset = as_int(b.get('account_id'), 'account', required=False) or it['expense_account_id']
    if not conn.execute('SELECT 1 FROM accounts WHERE id=?', (offset,)).fetchone():
        raise ApiError(400, 'Choose the adjustment account')
    memo = as_str(b.get('memo'), 'memo') or 'Quantity adjustment: %s' % it['name']
    if value:
        tid = ledger.post_txn(conn, date, 'Inventory Qty Adjust', [
            {'account_id': it['asset_account_id'], 'debit': value, 'memo': memo},
            {'account_id': offset, 'credit': value, 'memo': memo}],
            ref=ledger.next_ref(conn, 'Inventory Qty Adjust'), memo=memo)
    else:
        tid = conn.execute('INSERT INTO transactions(date, type, ref, memo) VALUES (?,?,?,?)',
                           (date, 'Inventory Qty Adjust', ledger.next_ref(conn, 'Inventory Qty Adjust'), memo)).lastrowid
    add_move(conn, it['id'], tid, date, change, value)
    return _item_json(conn, it)


# ---------------------------------------------------------------- sales tax

def tax_rate(conn, rid):
    r = conn.execute('SELECT * FROM tax_rates WHERE id=?', (as_int(rid, 'tax rate'),)).fetchone()
    if not r:
        raise ApiError(400, 'Unknown sales tax rate')
    return r


def _rate_json(r):
    return {'id': r['id'], 'name': r['name'], 'rate': float(r['rate']), 'agency': r['agency'],
            'active': bool(r['active'])}


def _save_rate(conn, b, ex=None):
    name = as_str(b['name'] if 'name' in b else (ex['name'] if ex else ''), 'name')
    if not name:
        raise ApiError(400, 'Name is required')
    if conn.execute('SELECT 1 FROM tax_rates WHERE name=? COLLATE NOCASE AND id<>?',
                    (name, ex['id'] if ex else 0)).fetchone():
        raise ApiError(409, 'A tax rate named "%s" already exists' % name)
    rate = dec(b['rate'], 'rate', lo=Decimal(0), hi=Decimal(100)) if 'rate' in b else Decimal(ex['rate'] if ex else 0)
    if ex and 'rate' in b and str(rate.normalize()) != str(Decimal(ex['rate']).normalize()) and \
            conn.execute('SELECT 1 FROM invoices WHERE tax_rate_id=? LIMIT 1', (ex['id'],)).fetchone():
        raise ApiError(400, 'This rate is used on invoices. Make it inactive and add a new rate instead.')
    agency = as_str(b['agency'], 'agency') if 'agency' in b else (ex['agency'] if ex else '')
    active = 1 if as_bool(b['active'] if 'active' in b else (ex['active'] if ex else True), 'active') else 0
    return name, format(rate.normalize(), 'f'), agency, active


@route('GET', '/api/tax_rates')
def list_rates(ctx):
    inc = ctx.q.get('include_inactive') in ('1', 'true')
    return [_rate_json(r) for r in ctx.conn.execute('SELECT * FROM tax_rates ORDER BY name') if inc or r['active']]


@route('POST', '/api/tax_rates')
def create_rate(ctx):
    rid = ctx.conn.execute('INSERT INTO tax_rates(name, rate, agency, active) VALUES (?,?,?,?)',
                           _save_rate(ctx.conn, ctx.body)).lastrowid
    return _rate_json(tax_rate(ctx.conn, rid))


@route('PUT', '/api/tax_rates/<id>')
def update_rate(ctx):
    ex = ctx.conn.execute('SELECT * FROM tax_rates WHERE id=?', (ctx.p['id'],)).fetchone()
    if not ex:
        raise ApiError(404, 'Tax rate not found')
    ctx.conn.execute('UPDATE tax_rates SET name=?, rate=?, agency=?, active=? WHERE id=?',
                     _save_rate(ctx.conn, ctx.body, ex) + (ex['id'],))
    return _rate_json(tax_rate(ctx.conn, ex['id']))


@route('DELETE', '/api/tax_rates/<id>')
def delete_rate(ctx):
    if not ctx.conn.execute('SELECT 1 FROM tax_rates WHERE id=?', (ctx.p['id'],)).fetchone():
        raise ApiError(404, 'Tax rate not found')
    if ctx.conn.execute('SELECT 1 FROM invoices WHERE tax_rate_id=? LIMIT 1', (ctx.p['id'],)).fetchone():
        raise ApiError(409, 'This rate is used on invoices. Make it inactive instead.')
    ctx.conn.execute('DELETE FROM tax_rates WHERE id=?', (ctx.p['id'],))
    return {'deleted': True}


# ---------------------------------------------------------------- tax / liability payments

def liability_payment(conn, b, ttype, code):
    """Pay a tax liability (sales tax or payroll taxes) from a bank account."""
    date = parse_date(b.get('date') or today())
    amount = to_cents(b.get('amount'), 'amount')
    if amount <= 0:
        raise ApiError(400, 'Payment amount must be greater than zero')
    bank = conn.execute('SELECT * FROM accounts WHERE id=?', (as_int(b.get('bank_account_id'), 'bank account'),)).fetchone()
    if not bank or bank['type'] not in ('Bank', 'Credit Card'):
        raise ApiError(400, 'Pay from a bank or credit card account')
    memo = as_str(b.get('memo'), 'memo') or ttype
    tid = ledger.post_txn(conn, date, ttype, [
        {'account_id': db.system_account(conn, code), 'debit': amount, 'memo': memo},
        {'account_id': bank['id'], 'credit': amount, 'memo': memo}],
        ref=as_str(b.get('ref'), 'ref'), memo=memo)
    return ledger.txn_json(conn, tid)


def liability_payments(conn, ttype):
    return [ledger.txn_json(conn, r[0]) for r in conn.execute(
        'SELECT id FROM transactions WHERE type=? ORDER BY date DESC, id DESC', (ttype,))]


@route('GET', '/api/sales_tax/payments')
def list_tax_payments(ctx):
    return liability_payments(ctx.conn, 'Sales Tax Payment')


@route('POST', '/api/sales_tax/payments')
def pay_sales_tax(ctx):
    return liability_payment(ctx.conn, ctx.body, 'Sales Tax Payment', 'tax')
