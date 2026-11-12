"""Chart of accounts: list, add, edit, delete / make inactive."""
from . import db, ledger
from .router import route
from .util import ApiError, as_bool, as_int, as_str, money, parse_date, to_cents, today

# (type, group) in balance sheet / P&L order
TYPES = [
    ('Bank', 'asset'), ('Accounts Receivable', 'asset'), ('Other Current Asset', 'asset'),
    ('Fixed Asset', 'asset'), ('Other Asset', 'asset'),
    ('Accounts Payable', 'liability'), ('Credit Card', 'liability'),
    ('Other Current Liability', 'liability'), ('Long Term Liability', 'liability'),
    ('Equity', 'equity'),
    ('Income', 'income'), ('Cost of Goods Sold', 'expense'), ('Expense', 'expense'),
    ('Other Income', 'income'), ('Other Expense', 'expense'),
]
GROUP = dict(TYPES)
TYPE_ORDER = {t: i for i, (t, _) in enumerate(TYPES)}
PL_TYPES = ('Income', 'Cost of Goods Sold', 'Expense', 'Other Income', 'Other Expense')


def normal_sign(acct_type):
    """+1 when the account normally carries a debit balance, else -1."""
    return 1 if GROUP[acct_type] in ('asset', 'expense') else -1


def ordered(conn, include_inactive=True):
    """All accounts in report order, sub-accounts directly under their parents."""
    rows = [dict(r) for r in conn.execute('SELECT * FROM accounts')]
    rows.sort(key=lambda a: (TYPE_ORDER.get(a['type'], 99), a['number'] == '', a['number'], a['name'].lower()))
    by_id = {a['id']: a for a in rows}
    kids = {}
    for a in rows:
        if a['parent_id'] in by_id:
            kids.setdefault(a['parent_id'], []).append(a)
    out = []

    def walk(a, depth, prefix):
        a['depth'] = depth
        a['full_name'] = prefix + a['name']
        out.append(a)
        for k in kids.get(a['id'], []):
            walk(k, depth + 1, a['full_name'] + ':')
    for a in rows:
        if a['parent_id'] not in by_id:
            walk(a, 0, '')
    if not include_inactive:
        out = [a for a in out if a['active']]
    return out


def _used(conn, account_id):
    checks = [
        ('SELECT 1 FROM lines WHERE account_id=? LIMIT 1', 'transactions'),
        ('SELECT 1 FROM feed WHERE account_id=? LIMIT 1', 'imported statement lines'),
        ('SELECT 1 FROM invoice_lines WHERE account_id=? LIMIT 1', 'invoices'),
        ('SELECT 1 FROM rules WHERE account_id=? LIMIT 1', 'bank rules'),
        ('SELECT 1 FROM reconciliations WHERE account_id=? LIMIT 1', 'reconciliations'),
        ('SELECT 1 FROM accounts WHERE parent_id=? LIMIT 1', 'sub-accounts'),
    ]
    for sql, what in checks:
        if conn.execute(sql, (account_id,)).fetchone():
            return what
    return None


def _validate(conn, body, existing=None):
    name = as_str(body.get('name') if 'name' in body else (existing or {}).get('name'), 'name')
    if not name:
        raise ApiError(400, 'Account name is required')
    if ':' in name:
        raise ApiError(400, 'Account names cannot contain a colon')
    atype = body.get('type') if 'type' in body else (existing or {}).get('type')
    if not isinstance(atype, str) or atype not in GROUP:
        raise ApiError(400, 'Choose a valid account type')
    number = as_str(body.get('number') if 'number' in body else (existing or {}).get('number'), 'number')
    self_id = existing['id'] if existing else 0
    if conn.execute('SELECT 1 FROM accounts WHERE name=? COLLATE NOCASE AND id<>?', (name, self_id)).fetchone():
        raise ApiError(409, 'An account named "%s" already exists' % name)
    if number and conn.execute('SELECT 1 FROM accounts WHERE number=? AND id<>?', (number, self_id)).fetchone():
        raise ApiError(409, 'Account number %s is already in use' % number)
    parent_id = body.get('parent_id') if 'parent_id' in body else (existing or {}).get('parent_id')
    parent_id = as_int(parent_id, 'parent_id', required=False)
    if parent_id:
        parent = conn.execute('SELECT * FROM accounts WHERE id=?', (parent_id,)).fetchone()
        if not parent:
            raise ApiError(400, 'Parent account not found')
        if parent['type'] != atype:
            raise ApiError(400, 'A sub-account must have the same type as its parent account')
        p = parent
        while p:
            if p['id'] == self_id:
                raise ApiError(400, 'An account cannot be a sub-account of itself')
            p = conn.execute('SELECT * FROM accounts WHERE id=?', (p['parent_id'],)).fetchone() \
                if p['parent_id'] else None
    active = 1 if as_bool(body['active'] if 'active' in body else (existing or {}).get('active', 1), 'active') else 0
    if existing:
        if existing['system'] and (atype != existing['type'] or not active):
            raise ApiError(400, '"%s" is a system account; its type cannot change and it cannot be made inactive'
                           % existing['name'])
        if atype != existing['type']:
            if conn.execute('SELECT 1 FROM accounts WHERE parent_id=? LIMIT 1', (self_id,)).fetchone():
                raise ApiError(400, 'Change the type of the sub-accounts first')
            special = ('Accounts Receivable', 'Accounts Payable')
            if (atype in special or existing['type'] in special) and conn.execute(
                    'SELECT 1 FROM lines WHERE account_id=? LIMIT 1', (self_id,)).fetchone():
                raise ApiError(400, 'An account with transactions cannot change to or from '
                                    'Accounts Receivable / Accounts Payable')
            if existing['type'] in ('Bank', 'Credit Card') and (
                    conn.execute('SELECT 1 FROM feed WHERE account_id=? LIMIT 1', (self_id,)).fetchone() or
                    conn.execute('SELECT 1 FROM reconciliations WHERE account_id=? LIMIT 1', (self_id,)).fetchone()):
                raise ApiError(400, 'This account has statement imports or reconciliations; its type cannot change')
        if not active and existing['active'] and conn.execute(
                'SELECT 1 FROM accounts WHERE parent_id=? AND active=1 LIMIT 1', (self_id,)).fetchone():
            raise ApiError(400, 'Make the sub-accounts inactive first')
    return {'name': name, 'type': atype, 'number': number, 'parent_id': parent_id,
            'description': as_str(body.get('description') if 'description' in body
                                  else (existing or {}).get('description'), 'description'),
            'active': active}


def account_json(a, balances=None, used=None):
    is_bs = a['type'] not in PL_TYPES
    out = {k: a[k] for k in ('id', 'number', 'name', 'type', 'parent_id', 'description', 'system')}
    out.update(active=bool(a['active']), group=GROUP[a['type']], full_name=a.get('full_name', a['name']),
               depth=a.get('depth', 0))
    if balances is not None:
        out['balance'] = money(normal_sign(a['type']) * balances.get(a['id'], 0)) if is_bs else None
    if used is not None:
        out['has_transactions'] = a['id'] in used
    return out


@route('GET', '/api/accounts')
def list_accounts(ctx):
    inc = ctx.q.get('include_inactive') in ('1', 'true')
    bal = dict(ctx.conn.execute('SELECT account_id, SUM(debit - credit) FROM lines GROUP BY account_id').fetchall())
    used = set(bal)
    return [account_json(a, bal, used) for a in ordered(ctx.conn, inc)]


@route('GET', '/api/accounts/<id>')
def get_account(ctx):
    for a in ordered(ctx.conn):
        if a['id'] == ctx.p['id']:
            bal = {a['id']: ledger.account_net(ctx.conn, a['id'])}
            return account_json(a, bal, set(bal) if _used(ctx.conn, a['id']) else set())
    raise ApiError(404, 'Account not found')


@route('POST', '/api/accounts')
def create_account(ctx):
    v = _validate(ctx.conn, ctx.body)
    cur = ctx.conn.execute(
        'INSERT INTO accounts(number, name, type, parent_id, description, active) VALUES (?,?,?,?,?,?)',
        (v['number'], v['name'], v['type'], v['parent_id'], v['description'], v['active']))
    aid = cur.lastrowid
    ob = to_cents(ctx.body.get('opening_balance'), 'opening_balance')
    if ob:
        if v['type'] in PL_TYPES:
            raise ApiError(400, 'Only balance sheet accounts can have an opening balance')
        if v['type'] in ('Accounts Receivable', 'Accounts Payable'):
            raise ApiError(400, 'Enter opening A/R or A/P balances as invoices or journal entries '
                                'with a customer or vendor name')
        obe = db.system_account(ctx.conn, 'obe')
        date = parse_date(ctx.body.get('opening_date') or today(), 'opening_date')
        amt = ob * normal_sign(v['type'])   # + means debit the new account
        tid = ledger.post_txn(ctx.conn, date, 'Journal', [
            {'account_id': aid, 'debit': amt, 'memo': 'Opening Balance'},
            {'account_id': obe, 'credit': amt, 'memo': 'Opening Balance'},
        ], ref=ledger.next_ref(ctx.conn, 'Journal'), memo='Opening Balance')
        if v['type'] in ('Bank', 'Credit Card'):
            from . import reconcile
            line = ctx.conn.execute('SELECT id FROM lines WHERE txn_id=? AND account_id=?', (tid, aid)).fetchone()
            reconcile.reconcile_opening_balance(ctx.conn, aid, line['id'], date, amt)
    ctx.p['id'] = aid
    return get_account(ctx)


@route('PUT', '/api/accounts/<id>')
def update_account(ctx):
    ex = ctx.conn.execute('SELECT * FROM accounts WHERE id=?', (ctx.p['id'],)).fetchone()
    if not ex:
        raise ApiError(404, 'Account not found')
    v = _validate(ctx.conn, ctx.body, dict(ex))
    ctx.conn.execute('UPDATE accounts SET number=?, name=?, type=?, parent_id=?, description=?, active=? WHERE id=?',
                     (v['number'], v['name'], v['type'], v['parent_id'], v['description'], v['active'], ex['id']))
    return get_account(ctx)


@route('DELETE', '/api/accounts/<id>')
def delete_account(ctx):
    ex = ctx.conn.execute('SELECT * FROM accounts WHERE id=?', (ctx.p['id'],)).fetchone()
    if not ex:
        raise ApiError(404, 'Account not found')
    if ex['system']:
        raise ApiError(400, '"%s" is a system account and cannot be deleted' % ex['name'])
    what = _used(ctx.conn, ex['id'])
    if what:
        raise ApiError(409, 'This account is used by %s and cannot be deleted. Make it inactive instead.' % what)
    ctx.conn.execute('DELETE FROM accounts WHERE id=?', (ex['id'],))
    return {'deleted': True}
