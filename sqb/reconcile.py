"""Bank and credit card reconciliation."""
from . import ledger
from .router import route
from .util import ApiError, as_bool, as_int, as_list, money, parse_date, to_cents


def _sign(acct_type):
    # Bank balance = debits - credits. Credit card balance (amount owed) = credits - debits.
    return 1 if acct_type == 'Bank' else -1


def _account(conn, account_id):
    a = conn.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
    if not a or a['type'] not in ('Bank', 'Credit Card'):
        raise ApiError(400, 'Choose a bank or credit card account')
    return a


def _rec(conn, rec_id):
    r = conn.execute('SELECT * FROM reconciliations WHERE id=?', (rec_id,)).fetchone()
    if not r:
        raise ApiError(404, 'Reconciliation not found')
    return r


def _reconciled_balance(conn, account_id, sign, exclude_rec=None):
    sql = "SELECT COALESCE(SUM(debit - credit), 0) FROM lines WHERE account_id=? AND recon_status='R'"
    args = [account_id]
    if exclude_rec:
        sql += ' AND reconciliation_id <> ?'
        args.append(exclude_rec)
    return sign * conn.execute(sql, args).fetchone()[0]


def _last_done(conn, account_id):
    return conn.execute("SELECT * FROM reconciliations WHERE account_id=? AND status='done' "
                        'ORDER BY statement_date DESC, id DESC LIMIT 1', (account_id,)).fetchone()


def _line_json(l, sign):
    raw = l['debit'] - l['credit']
    return {'line_id': l['id'], 'txn_id': l['txn_id'], 'date': l['date'], 'type': l['type'], 'ref': l['ref'],
            'name': l['name'], 'memo': l['lmemo'] or l['tmemo'],
            'amount': money(sign * raw),
            # 'deposit' = debit side (bank deposit / card payment or credit);
            # 'payment' = credit side (bank payment or withdrawal / card charge)
            'deposit': money(raw) if raw > 0 else None,
            'payment': money(-raw) if raw < 0 else None,
            'cleared': l['recon_status'] in ('C', 'R'), 'from_statement': bool(l['from_feed'])}


_LINE_SQL = (
    'SELECT l.id, l.txn_id, l.debit, l.credit, l.recon_status, l.memo AS lmemo, t.memo AS tmemo, t.date, '
    't.type, t.ref, n.name, EXISTS(SELECT 1 FROM feed f WHERE f.line_id = l.id) AS from_feed '
    'FROM lines l JOIN transactions t ON t.id = l.txn_id '
    'LEFT JOIN names n ON n.id = COALESCE(l.name_id, t.name_id) ')


def rec_json(conn, rec):
    acct = _account(conn, rec['account_id'])
    sign = _sign(acct['type'])
    if rec['status'] == 'open':
        rows = conn.execute(_LINE_SQL + "WHERE l.account_id=? AND t.date <= ? AND "
                            "(l.recon_status='' OR l.reconciliation_id=?) ORDER BY t.date, l.id",
                            (acct['id'], rec['statement_date'], rec['id'])).fetchall()
        beginning = _reconciled_balance(conn, acct['id'], sign)
    else:
        rows = conn.execute(_LINE_SQL + 'WHERE l.reconciliation_id=? ORDER BY t.date, l.id', (rec['id'],)).fetchall()
        beginning = rec['beginning_balance']
    lines = [_line_json(l, sign) for l in rows]
    cleared = [l for l in rows if l['recon_status'] in ('C', 'R')]
    cl_in = sum(l['debit'] - l['credit'] for l in cleared if l['debit'] > l['credit'])
    cl_out = sum(l['credit'] - l['debit'] for l in cleared if l['credit'] > l['debit'])
    cleared_balance = beginning + sign * (cl_in - cl_out)
    out = {
        'id': rec['id'], 'account_id': acct['id'], 'account': acct['name'], 'account_type': acct['type'],
        'statement_date': rec['statement_date'], 'status': rec['status'], 'completed_at': rec['completed_at'],
        'beginning_balance': money(beginning), 'ending_balance': money(rec['ending_balance']),
        'cleared_balance': money(cleared_balance),
        'difference': money(rec['ending_balance'] - cleared_balance),
        # money in to a bank account / payments made to a card
        'cleared_deposits': money(cl_in), 'cleared_deposit_count': sum(1 for l in cleared if l['debit'] > l['credit']),
        # money out of a bank account / charges on a card
        'cleared_payments': money(cl_out), 'cleared_payment_count': sum(1 for l in cleared if l['credit'] > l['debit']),
        'lines': lines,
    }
    if rec['status'] == 'done':
        unc = conn.execute(_LINE_SQL + "WHERE l.account_id=? AND t.date <= ? AND "
                           "(l.reconciliation_id IS NULL OR l.reconciliation_id > ?) ORDER BY t.date, l.id",
                           (acct['id'], rec['statement_date'], rec['id'])).fetchall()
        out['uncleared'] = [dict(_line_json(l, sign), cleared=False) for l in unc]
        out['uncleared_total'] = money(sign * sum(l['debit'] - l['credit'] for l in unc))
        out['register_balance'] = money(sign * ledger.account_net(conn, acct['id'], end=rec['statement_date']))
    return out


@route('GET', '/api/reconcile/accounts')
def recon_accounts(ctx):
    conn = ctx.conn
    out = []
    for a in conn.execute("SELECT * FROM accounts WHERE type IN ('Bank','Credit Card') AND active=1 "
                          'ORDER BY type, number, name').fetchall():
        sign = _sign(a['type'])
        last = _last_done(conn, a['id'])
        open_rec = conn.execute("SELECT id, statement_date FROM reconciliations WHERE account_id=? AND status='open'",
                                (a['id'],)).fetchone()
        out.append({
            'account_id': a['id'], 'name': a['name'], 'type': a['type'],
            'balance': money(sign * ledger.account_net(conn, a['id'])),
            'last_statement_date': last['statement_date'] if last else None,
            'last_ending_balance': money(last['ending_balance']) if last else None,
            'beginning_balance': money(_reconciled_balance(conn, a['id'], sign)),
            'open_reconciliation_id': open_rec['id'] if open_rec else None,
        })
    return out


@route('GET', '/api/reconciliations')
def list_recs(ctx):
    sql, args = 'SELECT r.*, a.name AS account FROM reconciliations r JOIN accounts a ON a.id = r.account_id', []
    if ctx.q.get('account_id'):
        sql += ' WHERE r.account_id=?'
        args.append(as_int(ctx.q['account_id'], 'account_id'))
    rows = ctx.conn.execute(sql + ' ORDER BY r.statement_date DESC, r.id DESC', args).fetchall()
    out = []
    for r in rows:
        last = _last_done(ctx.conn, r['account_id'])
        out.append({'id': r['id'], 'account_id': r['account_id'], 'account': r['account'],
                    'statement_date': r['statement_date'], 'status': r['status'],
                    'beginning_balance': money(
                        r['beginning_balance'] if r['status'] == 'done' else _reconciled_balance(
                            ctx.conn, r['account_id'],
                            _sign(_account(ctx.conn, r['account_id'])['type']))),
                    'ending_balance': money(r['ending_balance']),
                    'completed_at': r['completed_at'],
                    'can_undo': r['status'] == 'open' or (last is not None and last['id'] == r['id'])})
    return out


@route('POST', '/api/reconciliations')
def start_rec(ctx):
    conn, b = ctx.conn, ctx.body
    acct = _account(conn, as_int(b.get('account_id'), 'account_id'))
    date = parse_date(b.get('statement_date'), 'statement_date')
    if b.get('ending_balance') in (None, ''):
        raise ApiError(400, 'Enter the statement ending balance')
    ending = to_cents(b.get('ending_balance'), 'ending_balance')
    if conn.execute("SELECT 1 FROM reconciliations WHERE account_id=? AND status='open'", (acct['id'],)).fetchone():
        raise ApiError(409, 'A reconciliation is already in progress for this account')
    last = _last_done(conn, acct['id'])
    if last and date <= last['statement_date']:
        raise ApiError(400, 'Statement date must be after the last reconciled statement (%s)' % last['statement_date'])
    sign = _sign(acct['type'])
    rid = conn.execute('INSERT INTO reconciliations(account_id, statement_date, beginning_balance, ending_balance) '
                       'VALUES (?,?,?,?)', (acct['id'], date, _reconciled_balance(conn, acct['id'], sign), ending)
                       ).lastrowid
    _precheck(conn, rid, acct['id'], date)
    return rec_json(conn, _rec(conn, rid))


def _precheck(conn, rec_id, account_id, date):
    """Like QuickBooks: entries that came from (or were matched to) the statement start out checked off."""
    conn.execute("UPDATE lines SET recon_status='C', reconciliation_id=? WHERE account_id=? AND recon_status='' "
                 'AND EXISTS (SELECT 1 FROM feed f WHERE f.line_id = lines.id AND f.date <= ?) '
                 'AND (SELECT date FROM transactions t WHERE t.id = lines.txn_id) <= ?',
                 (rec_id, account_id, date, date))


def auto_clear(conn, line_id, feed_date):
    """A statement line was just added/matched: tick it in the reconciliation in progress, if any."""
    l = conn.execute('SELECT l.account_id, l.recon_status, t.date FROM lines l '
                     'JOIN transactions t ON t.id = l.txn_id WHERE l.id=?', (line_id,)).fetchone()
    if not l or l['recon_status']:
        return
    rec = conn.execute("SELECT * FROM reconciliations WHERE account_id=? AND status='open'",
                       (l['account_id'],)).fetchone()
    if rec and l['date'] <= rec['statement_date'] and feed_date <= rec['statement_date']:
        conn.execute("UPDATE lines SET recon_status='C', reconciliation_id=? WHERE id=?", (rec['id'], line_id))


@route('GET', '/api/reconciliations/<id>')
def get_rec(ctx):
    return rec_json(ctx.conn, _rec(ctx.conn, ctx.p['id']))


@route('PUT', '/api/reconciliations/<id>')
def update_rec(ctx):
    conn, b = ctx.conn, ctx.body
    rec = _rec(conn, ctx.p['id'])
    if rec['status'] != 'open':
        raise ApiError(409, 'This reconciliation is finished. Undo it to make changes.')
    date = parse_date(b.get('statement_date') or rec['statement_date'], 'statement_date')
    ending = to_cents(b['ending_balance'], 'ending_balance') if b.get('ending_balance') not in (None, '') \
        else rec['ending_balance']
    last = _last_done(conn, rec['account_id'])
    if last and date <= last['statement_date']:
        raise ApiError(400, 'Statement date must be after the last reconciled statement (%s)' % last['statement_date'])
    conn.execute('UPDATE reconciliations SET statement_date=?, ending_balance=? WHERE id=?', (date, ending, rec['id']))
    # Un-check anything dated after the new statement date.
    conn.execute("UPDATE lines SET recon_status='', reconciliation_id=NULL WHERE reconciliation_id=? AND "
                 '(SELECT date FROM transactions t WHERE t.id = lines.txn_id) > ?', (rec['id'], date))
    if date > rec['statement_date']:
        _precheck(conn, rec['id'], rec['account_id'], date)
    return rec_json(conn, _rec(conn, rec['id']))


@route('POST', '/api/reconciliations/<id>/toggle')
def toggle(ctx):
    conn, b = ctx.conn, ctx.body
    rec = _rec(conn, ctx.p['id'])
    if rec['status'] != 'open':
        raise ApiError(409, 'This reconciliation is finished')
    cleared = as_bool(b.get('cleared'), 'cleared')
    for lid in as_list(b.get('line_ids'), 'line_ids'):
        l = conn.execute('SELECT l.*, t.date FROM lines l JOIN transactions t ON t.id = l.txn_id WHERE l.id=?',
                         (as_int(lid, 'line_id'),)).fetchone()
        if not l or l['account_id'] != rec['account_id']:
            raise ApiError(400, 'Line %s is not in this account' % lid)
        if l['recon_status'] == 'R':
            raise ApiError(409, 'Line %s is already reconciled' % lid)
        if l['date'] > rec['statement_date']:
            raise ApiError(400, 'Line %s is dated after the statement date' % lid)
        if cleared:
            conn.execute("UPDATE lines SET recon_status='C', reconciliation_id=? WHERE id=?", (rec['id'], l['id']))
        else:
            conn.execute("UPDATE lines SET recon_status='', reconciliation_id=NULL WHERE id=?", (l['id'],))
    return rec_json(conn, rec)


@route('POST', '/api/reconciliations/<id>/finish')
def finish(ctx):
    conn = ctx.conn
    rec = _rec(conn, ctx.p['id'])
    if rec['status'] != 'open':
        raise ApiError(409, 'This reconciliation is already finished')
    j = rec_json(conn, rec)
    if round(j['difference'] * 100) != 0:
        raise ApiError(400, 'The difference is %.2f. It must be 0.00 before you can finish.' % j['difference'])
    conn.execute("UPDATE lines SET recon_status='R' WHERE reconciliation_id=?", (rec['id'],))
    conn.execute("UPDATE reconciliations SET status='done', beginning_balance=?, completed_at=CURRENT_TIMESTAMP "
                 'WHERE id=?', (round(j['beginning_balance'] * 100), rec['id']))
    return rec_json(conn, _rec(conn, rec['id']))


@route('DELETE', '/api/reconciliations/<id>')
def undo_rec(ctx):
    """Cancel an in-progress reconciliation, or undo the most recent finished one."""
    conn = ctx.conn
    rec = _rec(conn, ctx.p['id'])
    if rec['status'] == 'done':
        last = _last_done(conn, rec['account_id'])
        if last['id'] != rec['id']:
            raise ApiError(409, 'Only the most recent reconciliation can be undone. Undo later ones first.')
    conn.execute("UPDATE lines SET recon_status='', reconciliation_id=NULL WHERE reconciliation_id=?", (rec['id'],))
    conn.execute('DELETE FROM reconciliations WHERE id=?', (rec['id'],))
    return {'deleted': True}
