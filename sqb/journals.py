"""Journal entries plus generic transaction view / delete."""
from . import ledger
from .router import route
from .util import ApiError, as_int, as_str, money, parse_date, to_cents


def _lines(conn, body):
    raw = body.get('lines')
    if not isinstance(raw, list) or not all(isinstance(x, dict) for x in raw):
        raise ApiError(400, 'lines are required')
    out = []
    for i, ln in enumerate(raw, 1):
        dr, cr = to_cents(ln.get('debit'), 'debit'), to_cents(ln.get('credit'), 'credit')
        if not dr and not cr and not ln.get('account_id'):
            continue    # blank row
        if dr < 0 or cr < 0:
            raise ApiError(400, 'Line %d: amounts cannot be negative' % i)
        if dr and cr:
            raise ApiError(400, 'Line %d: enter a debit or a credit, not both' % i)
        if not dr and not cr:
            raise ApiError(400, 'Line %d: enter an amount' % i)
        acct = conn.execute('SELECT * FROM accounts WHERE id=?',
                            (as_int(ln.get('account_id'), 'Line %d account' % i),)).fetchone()
        if not acct:
            raise ApiError(400, 'Line %d: choose an account' % i)
        if not acct['active']:
            raise ApiError(400, 'Line %d: account "%s" is inactive' % (i, acct['name']))
        name_id = as_int(ln.get('name_id'), 'name', required=False)
        name = conn.execute('SELECT * FROM names WHERE id=?', (name_id,)).fetchone() if name_id else None
        if name_id and not name:
            raise ApiError(400, 'Line %d: unknown name' % i)
        if acct['type'] == 'Accounts Receivable' and (not name or name['kind'] != 'customer'):
            raise ApiError(400, 'Line %d: choose a customer in the Name field when you use Accounts Receivable' % i)
        if acct['type'] == 'Accounts Payable' and (not name or name['kind'] != 'vendor'):
            raise ApiError(400, 'Line %d: choose a vendor in the Name field when you use Accounts Payable' % i)
        out.append({'account_id': acct['id'], 'debit': dr, 'credit': cr,
                    'memo': as_str(ln.get('memo'), 'memo'), 'name_id': name_id})
    return out


def _check_duplicate(conn, ref, body, self_id=0):
    """Like QuickBooks' 'warn if duplicate journal number': refuse unless the user confirms."""
    if body.get('allow_duplicate') in (True, 1, '1', 'true'):
        return
    if conn.execute("SELECT 1 FROM transactions WHERE type='Journal' AND ref=? AND id<>?", (ref, self_id)).fetchone():
        raise ApiError(409, 'Journal no. %s is already used. Save anyway?' % ref)


@route('GET', '/api/journals')
def list_journals(ctx):
    sql = "SELECT t.id FROM transactions t WHERE t.type='Journal'"
    args = []
    for key, op in (('start', '>='), ('end', '<=')):
        v = parse_date(ctx.q.get(key), key, required=False)
        if v:
            sql += ' AND t.date %s ?' % op
            args.append(v)
    out = []
    for r in ctx.conn.execute(sql + ' ORDER BY t.date DESC, t.id DESC', args).fetchall():
        t = ledger.txn_json(ctx.conn, r['id'])
        t['accounts'] = ', '.join(dict.fromkeys(l['account'] for l in t['lines']))
        out.append(t)
    return out


@route('POST', '/api/journals')
def create_journal(ctx):
    b = ctx.body
    ref = as_str(b.get('ref'), 'ref') or ledger.next_ref(ctx.conn, 'Journal')
    _check_duplicate(ctx.conn, ref, b)
    tid = ledger.post_txn(ctx.conn, b.get('date'), 'Journal', _lines(ctx.conn, b), ref=ref,
                          memo=as_str(b.get('memo'), 'memo'))
    return ledger.txn_json(ctx.conn, tid)


@route('PUT', '/api/journals/<id>')
def update_journal(ctx):
    t = ctx.conn.execute('SELECT * FROM transactions WHERE id=?', (ctx.p['id'],)).fetchone()
    if not t:
        raise ApiError(404, 'Journal entry not found')
    if t['type'] != 'Journal':
        raise ApiError(400, 'Only journal entries can be edited here')
    b = ctx.body
    ref = as_str(b.get('ref'), 'ref') or t['ref']
    memo = as_str(b['memo'], 'memo') if 'memo' in b else t['memo']
    _check_duplicate(ctx.conn, ref, b, t['id'])
    ledger.post_txn(ctx.conn, b.get('date') or t['date'], 'Journal', _lines(ctx.conn, b), ref=ref,
                    memo=memo, txn_id=t['id'])
    return ledger.txn_json(ctx.conn, t['id'])


@route('GET', '/api/journals/next_ref')
def next_journal_ref(ctx):
    return {'ref': ledger.next_ref(ctx.conn, 'Journal')}


@route('GET', '/api/transactions/<id>')
def get_txn(ctx):
    return ledger.txn_json(ctx.conn, ctx.p['id'])


@route('DELETE', '/api/transactions/<id>')
def delete_txn(ctx):
    ledger.delete_txn(ctx.conn, ctx.p['id'])
    return {'deleted': True}
