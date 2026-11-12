"""Customers and vendors."""
import re

from . import db
from .router import route
from .util import ApiError, as_bool, as_str, money

FIELDS = ('name', 'company', 'email', 'phone', 'address', 'notes')


def _json(r, balance=None):
    out = {k: r[k] for k in ('id', 'kind') + FIELDS}
    out['active'] = bool(r['active'])
    out['currency'] = r['currency'] or None   # None = home currency
    if balance is not None:
        out['balance'] = money(balance)
    return out


def _balances(conn, kind):
    """Customers: open A/R. Vendors: open A/P."""
    acct_type, sign = ('Accounts Receivable', 1) if kind == 'customer' else ('Accounts Payable', -1)
    rows = conn.execute(
        'SELECT COALESCE(l.name_id, t.name_id) AS nid, SUM(l.debit - l.credit) AS net '
        'FROM lines l JOIN transactions t ON t.id = l.txn_id JOIN accounts a ON a.id = l.account_id '
        'WHERE a.type=? GROUP BY nid', (acct_type,)).fetchall()
    return {r['nid']: sign * r['net'] for r in rows}


def _used(conn, name_id):
    for sql in ('SELECT 1 FROM transactions WHERE name_id=? LIMIT 1',
                'SELECT 1 FROM lines WHERE name_id=? LIMIT 1',
                'SELECT 1 FROM invoices WHERE customer_id=? LIMIT 1',
                'SELECT 1 FROM payments WHERE customer_id=? LIMIT 1',
                'SELECT 1 FROM bills WHERE vendor_id=? LIMIT 1',
                'SELECT 1 FROM bill_payments WHERE vendor_id=? LIMIT 1',
                'SELECT 1 FROM rules WHERE name_id=? LIMIT 1'):
        if conn.execute(sql, (name_id,)).fetchone():
            return True
    return False


def _clean(conn, body, existing=None):
    vals = {}
    for f in FIELDS:
        vals[f] = as_str(body[f], f) if f in body and body[f] is not None else (existing[f] if existing else '')
    if not vals['name']:
        raise ApiError(400, 'Name is required')
    clash = conn.execute('SELECT kind FROM names WHERE name=? COLLATE NOCASE AND id<>?',
                         (vals['name'], existing['id'] if existing else 0)).fetchone()
    if clash:
        raise ApiError(409, 'Another %s is already using the name "%s"' % (clash['kind'], vals['name']))
    home = db.get_setting(conn, 'home_currency', 'USD')
    cur = body.get('currency') if 'currency' in body else (existing['currency'] if existing else '')
    cur = as_str(cur, 'currency').upper()
    if cur and not re.fullmatch(r'[A-Z]{3}', cur):
        raise ApiError(400, 'Currency must be a 3-letter code such as USD, EUR or CAD')
    if existing and (cur or home) != (existing['currency'] or home) and _used(conn, existing['id']):
        raise ApiError(400, 'The currency cannot change once this name has transactions')
    vals['currency'] = cur
    vals['active'] = 1 if as_bool(body['active'] if 'active' in body
                                  else (existing['active'] if existing else 1), 'active') else 0
    return vals


def _register(path, kind):
    label = kind.capitalize()

    def get_row(ctx):
        r = ctx.conn.execute('SELECT * FROM names WHERE id=? AND kind=?', (ctx.p['id'], kind)).fetchone()
        if not r:
            raise ApiError(404, '%s not found' % label)
        return r

    @route('GET', path)
    def list_names(ctx):
        inc = ctx.q.get('include_inactive') in ('1', 'true')
        bal = _balances(ctx.conn, kind)
        rows = ctx.conn.execute('SELECT * FROM names WHERE kind=? ORDER BY name COLLATE NOCASE', (kind,)).fetchall()
        return [_json(r, bal.get(r['id'], 0)) for r in rows if inc or r['active']]

    @route('GET', path + '/<id>')
    def get_name(ctx):
        r = get_row(ctx)
        return _json(r, _balances(ctx.conn, kind).get(r['id'], 0))

    @route('POST', path)
    def create_name(ctx):
        v = _clean(ctx.conn, ctx.body)
        cur = ctx.conn.execute(
            'INSERT INTO names(kind, name, company, email, phone, address, notes, active, currency) '
            'VALUES (?,?,?,?,?,?,?,?,?)', (kind,) + tuple(v[f] for f in FIELDS) + (v['active'], v['currency']))
        ctx.p['id'] = cur.lastrowid
        return get_name(ctx)

    @route('PUT', path + '/<id>')
    def update_name(ctx):
        r = get_row(ctx)
        v = _clean(ctx.conn, ctx.body, r)
        ctx.conn.execute(
            'UPDATE names SET name=?, company=?, email=?, phone=?, address=?, notes=?, active=?, currency=? WHERE id=?',
            tuple(v[f] for f in FIELDS) + (v['active'], v['currency'], r['id']))
        return get_name(ctx)

    @route('DELETE', path + '/<id>')
    def delete_name(ctx):
        r = get_row(ctx)
        if _used(ctx.conn, r['id']):
            raise ApiError(409, 'This %s has transactions and cannot be deleted. Make it inactive instead.' % kind)
        ctx.conn.execute('DELETE FROM names WHERE id=?', (r['id'],))
        return {'deleted': True}


_register('/api/customers', 'customer')
_register('/api/vendors', 'vendor')
