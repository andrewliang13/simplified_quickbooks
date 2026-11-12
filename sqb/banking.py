"""Bank / credit card statement import, categorization, matching and rules."""
import base64
import binascii
import hashlib
import re

from . import db, invoices, ledger, parsers, reconcile
from .router import route
from .util import ApiError, add_days, as_int, as_list, as_str, money, to_cents


def _feed_account(conn, account_id):
    a = conn.execute('SELECT * FROM accounts WHERE id=?', (as_int(account_id, 'account_id'),)).fetchone()
    if not a or a['type'] not in ('Bank', 'Credit Card'):
        raise ApiError(400, 'Choose a bank or credit card account')
    return a


def _row(conn, feed_id):
    r = conn.execute('SELECT * FROM feed WHERE id=?', (feed_id,)).fetchone()
    if not r:
        raise ApiError(404, 'Statement line not found')
    return r


def _norm(desc):
    """Key used to recognise the same payee again: the description without numbers and punctuation
    (so "OFFICE DEPOT #123" and "OFFICE DEPOT #987" are the same payee)."""
    return ' '.join(re.findall(r'[A-Z]{2,}', desc.upper()))


# ---------------------------------------------------------------- import

def _parse_payload(ctx):
    b = ctx.body
    acct = _feed_account(ctx.conn, b.get('account_id'))
    if b.get('content_b64'):
        try:
            data = base64.b64decode(as_str(b['content_b64'], 'content_b64'))
        except (binascii.Error, ValueError):
            raise ApiError(400, 'File content is not valid base64')
    else:
        data = as_str(b.get('content'), 'content').encode('utf-8')
    if not data.strip():
        raise ApiError(400, 'The file is empty')
    rows, meta = parsers.parse_statement(data, as_str(b.get('filename'), 'filename'),
                                         acct['type'] == 'Credit Card',
                                         as_int(b.get('year'), 'year', required=False))
    rows = [r for r in rows if r['amount'] != 0]
    if b.get('flip_sign'):
        for r in rows:
            r['amount'] = -r['amount']
    # A row is a duplicate when the bank's own id (FITID) was imported before, or when the same
    # date + amount + description was (the n-th identical row of a day only matches the n-th).
    existing, fitids = set(), set()
    for k, f in ctx.conn.execute('SELECT key, fitid FROM feed WHERE account_id=?', (acct['id'],)):
        existing.add(k)
        if f:
            fitids.add(f)
    seen = {}
    for r in rows:
        base = '%s|%d|%s' % (r['date'], r['amount'], re.sub(r'\s+', ' ', r['description'].upper()).strip())
        n = seen.get(base, 0)
        seen[base] = n + 1
        r['key'] = 'H:' + hashlib.sha1(('%s|%d' % (base, n)).encode('utf-8')).hexdigest()
        r['duplicate'] = r['key'] in existing or bool(r.get('fitid') and r['fitid'] in fitids)
    return acct, rows, meta


def _preview_json(acct, rows, meta):
    new = [r for r in rows if not r['duplicate']]
    return {
        'account_id': acct['id'], 'format': meta['format'], 'warnings': meta['warnings'],
        'count': len(rows), 'new_count': len(new), 'duplicate_count': len(rows) - len(new),
        'money_in': money(sum(r['amount'] for r in new if r['amount'] > 0)),
        'money_out': money(-sum(r['amount'] for r in new if r['amount'] < 0)),
        'start': min((r['date'] for r in rows), default=None),
        'end': max((r['date'] for r in rows), default=None),
        'rows': [{'date': r['date'], 'description': r['description'], 'amount': money(r['amount']),
                  'duplicate': r['duplicate']} for r in rows],
    }


@route('POST', '/api/feed/preview')
def preview(ctx):
    return _preview_json(*_parse_payload(ctx))


@route('POST', '/api/feed/import')
def do_import(ctx):
    acct, rows, meta = _parse_payload(ctx)
    new = [r for r in rows if not r['duplicate']]
    if not new:
        return {'imported': 0, 'duplicates': len(rows), 'import_id': None, 'format': meta['format'],
                'warnings': meta['warnings']}
    cur = ctx.conn.execute('INSERT INTO imports(account_id, filename, format, count) VALUES (?,?,?,?)',
                           (acct['id'], str(ctx.body.get('filename') or ''), meta['format'], len(new)))
    for r in new:
        ctx.conn.execute('INSERT INTO feed(account_id, import_id, date, description, amount, key, fitid) '
                         'VALUES (?,?,?,?,?,?,?)', (acct['id'], cur.lastrowid, r['date'], r['description'],
                                                    r['amount'], r['key'], r.get('fitid')))
    return {'imported': len(new), 'duplicates': len(rows) - len(new), 'import_id': cur.lastrowid,
            'format': meta['format'], 'warnings': meta['warnings']}


# ---------------------------------------------------------------- suggestions / listing

def _history(conn):
    """Most recent category used for each normalised description (single-category entries only)."""
    rows = conn.execute(
        'SELECT f.id, f.description, f.amount, l2.account_id, t.name_id, '
        '(SELECT COUNT(*) FROM lines x WHERE x.txn_id = t.id) AS n '
        'FROM feed f JOIN lines l ON l.id = f.line_id JOIN transactions t ON t.id = l.txn_id '
        'JOIN lines l2 ON l2.txn_id = t.id AND l2.id <> l.id '
        'JOIN accounts a2 ON a2.id = l2.account_id '
        "WHERE f.status='posted' AND f.created_txn=1 AND t.type <> 'Payment' "
        "AND a2.type <> 'Accounts Receivable' AND a2.system IS NOT 'udf' ORDER BY t.id").fetchall()
    hist = {}
    for r in rows:
        if r['n'] == 2 and _norm(r['description']):
            hist[(_norm(r['description']), r['amount'] > 0)] = (r['account_id'], r['name_id'])
    return hist


def _suggest(row, rules, hist, active):
    desc = row['description'].lower()
    for r in rules:
        if r['match_text'].lower() in desc and (
                r['direction'] == 'any' or (r['direction'] == 'in') == (row['amount'] > 0)):
            if r['account_id'] in active and r['account_id'] != row['account_id']:
                return {'account_id': r['account_id'], 'name_id': r['name_id'], 'source': 'rule', 'rule_id': r['id']}
    h = hist.get((_norm(row['description']), row['amount'] > 0))
    if h and h[0] in active and h[0] != row['account_id']:
        return {'account_id': h[0], 'name_id': h[1], 'source': 'history'}
    return None


def _matches(conn, row, acct_type):
    """Existing ledger entries (and open invoices) this statement line could be matched to."""
    out = []
    lines = conn.execute(
        'SELECT l.id, l.txn_id, t.date, t.type, t.ref, t.memo, n.name FROM lines l '
        'JOIN transactions t ON t.id = l.txn_id '
        'LEFT JOIN names n ON n.id = COALESCE(l.name_id, t.name_id) '
        "WHERE l.account_id=? AND l.debit - l.credit = ? AND l.recon_status <> 'R' "
        'AND t.date BETWEEN ? AND ? AND NOT EXISTS (SELECT 1 FROM feed f WHERE f.line_id = l.id) '
        'ORDER BY ABS(julianday(t.date) - julianday(?)), l.id',
        (row['account_id'], row['amount'], add_days(row['date'], -90), add_days(row['date'], 10),
         row['date'])).fetchall()
    for l in lines:
        out.append({'kind': 'transaction', 'line_id': l['id'], 'txn_id': l['txn_id'], 'date': l['date'],
                    'label': ' '.join(x for x in (l['type'], l['ref'] and '#' + l['ref'], l['name'], l['memo']) if x)})
    if acct_type == 'Bank' and row['amount'] > 0:
        for inv in invoices.open_invoices(conn):
            if inv['balance_cents'] == row['amount'] and inv['date'] <= row['date']:
                out.append({'kind': 'invoice', 'invoice_id': inv['id'], 'date': inv['date'],
                            'label': 'Invoice #%s %s (open %.2f)' % (inv['number'], inv['customer'],
                                                                      inv['balance_cents'] / 100.0)})
        # Payments received into Undeposited Funds that this bank deposit could be.
        udf = db.system_account(conn, 'udf')
        held = ledger.account_net(conn, udf, end=row['date']) if udf else 0
        pays = conn.execute('SELECT p.*, n.name FROM payments p JOIN names n ON n.id = p.customer_id '
                            'WHERE p.deposit_account_id=? AND p.amount=? AND p.date <= ? ORDER BY p.date DESC',
                            (udf, row['amount'], row['date'])).fetchall()
        if held >= row['amount'] and (pays or held == row['amount']):
            who = 'payment from %s on %s' % (pays[0]['name'], pays[0]['date']) if pays else 'all payments held'
            out.append({'kind': 'undeposited', 'date': pays[0]['date'] if pays else row['date'],
                        'label': 'Deposit of Undeposited Funds (%s)' % who})
    return out


def _posted_info(conn, row):
    if not row['line_id']:
        return {}
    l = conn.execute('SELECT txn_id FROM lines WHERE id=?', (row['line_id'],)).fetchone()
    if not l:
        return {}
    t = ledger.txn_json(conn, l['txn_id'])
    others = list(dict.fromkeys(x['account'] for x in t['lines'] if x['id'] != row['line_id']))
    name = t['name'] or next((x['name'] for x in t['lines'] if x['name']), None)
    return {'txn_id': t['id'], 'txn_type': t['type'], 'category': others[0] if len(others) == 1 else '-Split-',
            'name': name, 'matched': not row['created_txn'], 'reconciled': t['reconciled']}


@route('GET', '/api/feed')
def list_feed(ctx):
    conn = ctx.conn
    acct = _feed_account(conn, ctx.q.get('account_id'))
    status = ctx.q.get('status') or 'review'
    if status not in ('review', 'posted', 'excluded'):
        raise ApiError(400, 'Unknown status')
    rows = conn.execute('SELECT * FROM feed WHERE account_id=? AND status=? ORDER BY date DESC, id DESC',
                        (acct['id'], status)).fetchall()
    rules = conn.execute('SELECT * FROM rules ORDER BY id').fetchall() if status == 'review' else []
    hist = _history(conn) if status == 'review' else {}
    active = {r[0] for r in conn.execute('SELECT id FROM accounts WHERE active=1')}
    out = []
    for r in rows:
        item = {'id': r['id'], 'account_id': r['account_id'], 'date': r['date'], 'description': r['description'],
                'amount': money(r['amount']), 'status': r['status']}
        if status == 'review':
            item['suggestion'] = _suggest(r, rules, hist, active)
            item['matches'] = _matches(conn, r, acct['type'])
        elif status == 'posted':
            item.update(_posted_info(conn, r))
        out.append(item)
    counts = dict(conn.execute('SELECT status, COUNT(*) FROM feed WHERE account_id=? GROUP BY status',
                               (acct['id'],)).fetchall())
    sign = 1 if acct['type'] == 'Bank' else -1
    return {'account_id': acct['id'], 'account_type': acct['type'], 'status': status, 'rows': out,
            'counts': {k: counts.get(k, 0) for k in ('review', 'posted', 'excluded')},
            'book_balance': money(sign * ledger.account_net(conn, acct['id']))}


@route('GET', '/api/feed/summary')
def feed_summary(ctx):
    rows = ctx.conn.execute(
        "SELECT a.id, a.name, a.type, (SELECT COUNT(*) FROM feed f WHERE f.account_id=a.id AND f.status='review') n "
        "FROM accounts a WHERE a.type IN ('Bank','Credit Card') AND a.active=1 ORDER BY a.type, a.number, a.name"
    ).fetchall()
    return [{'account_id': r['id'], 'name': r['name'], 'type': r['type'], 'review_count': r['n'],
             'balance': money((1 if r['type'] == 'Bank' else -1) * ledger.account_net(ctx.conn, r['id']))}
            for r in rows]


# ---------------------------------------------------------------- categorize / match / exclude / undo

def categorize(conn, row, splits, name_id=None, memo=''):
    """Post a statement line to the ledger. splits: [{'account_id', 'amount' (cents), 'memo'}];
    split amounts are in the direction of the statement line and must add up to it."""
    if row['status'] != 'review':
        raise ApiError(409, 'This statement line has already been handled')
    acct = conn.execute('SELECT * FROM accounts WHERE id=?', (row['account_id'],)).fetchone()
    total = abs(row['amount'])
    if not splits:
        raise ApiError(400, 'Choose a category')
    if sum(s['amount'] for s in splits) != total:
        raise ApiError(400, 'Split amounts (%.2f) must add up to the transaction amount (%.2f)'
                       % (sum(s['amount'] for s in splits) / 100.0, total / 100.0))
    money_in = row['amount'] > 0
    cats = []
    for s in splits:
        c = conn.execute('SELECT * FROM accounts WHERE id=?', (s['account_id'],)).fetchone()
        if not c:
            raise ApiError(400, 'Choose a category')
        if c['id'] == acct['id']:
            raise ApiError(400, 'The category cannot be the same account as the statement')
        if c['type'] == 'Accounts Receivable':
            raise ApiError(400, 'To record a customer payment, match the deposit to an invoice '
                                'or use Receive Payment')
        cats.append(c)
    if len(cats) == 1 and cats[0]['type'] in ('Bank', 'Credit Card'):
        ttype = 'Credit Card Payment' if 'Credit Card' in (cats[0]['type'], acct['type']) and \
            cats[0]['type'] != acct['type'] else 'Transfer'
    elif money_in:
        ttype = 'Deposit' if acct['type'] == 'Bank' else 'Credit Card Credit'
    else:
        ttype = 'Expense'
    bank_line = {'account_id': acct['id'], 'memo': row['description'],
                 'debit' if money_in else 'credit': total}
    lines = [bank_line]
    for s in splits:
        lines.append({'account_id': s['account_id'], 'memo': s.get('memo') or row['description'],
                      'credit' if money_in else 'debit': s['amount']})
    tid = ledger.post_txn(conn, row['date'], ttype, lines, memo=memo or row['description'], name_id=name_id)
    line = conn.execute('SELECT id FROM lines WHERE txn_id=? AND account_id=? ORDER BY seq LIMIT 1',
                        (tid, acct['id'])).fetchone()
    conn.execute("UPDATE feed SET status='posted', line_id=?, created_txn=1 WHERE id=?", (line['id'], row['id']))
    reconcile.auto_clear(conn, line['id'], row['date'])
    return tid


def _splits_from_body(body, row):
    if body.get('splits'):
        out = []
        for s in as_list(body['splits'], 'splits', dict):
            amt = to_cents(s.get('amount'), 'split amount')
            if amt == 0:
                continue
            out.append({'account_id': as_int(s.get('account_id'), 'category'), 'amount': amt,
                        'memo': as_str(s.get('memo'), 'memo')})
        return out
    return [{'account_id': as_int(body.get('account_id'), 'category'), 'amount': abs(row['amount'])}]


def _add_rule(conn, text, direction, account_id, name_id):
    text = as_str(text, 'match_text')
    if not text:
        raise ApiError(400, 'Rule text is required')
    if direction not in ('any', 'in', 'out'):
        raise ApiError(400, 'Rule direction must be any, in or out')
    if not conn.execute('SELECT 1 FROM accounts WHERE id=?', (account_id,)).fetchone():
        raise ApiError(400, 'Choose a category for the rule')
    if name_id and not conn.execute('SELECT 1 FROM names WHERE id=?', (name_id,)).fetchone():
        raise ApiError(400, 'Unknown customer/vendor')
    return conn.execute('INSERT INTO rules(match_text, direction, account_id, name_id) VALUES (?,?,?,?)',
                        (text, direction, account_id, name_id or None)).lastrowid


@route('POST', '/api/feed/<id>/categorize')
def categorize_one(ctx):
    row = _row(ctx.conn, ctx.p['id'])
    name_id = as_int(ctx.body.get('name_id'), 'name', required=False)
    splits = _splits_from_body(ctx.body, row)
    tid = categorize(ctx.conn, row, splits, name_id, as_str(ctx.body.get('memo'), 'memo'))
    if ctx.body.get('rule_text') and len(splits) == 1:
        _add_rule(ctx.conn, ctx.body['rule_text'], 'in' if row['amount'] > 0 else 'out',
                  splits[0]['account_id'], name_id)
    return ledger.txn_json(ctx.conn, tid)


@route('POST', '/api/feed/<id>/match')
def match_one(ctx):
    conn = ctx.conn
    row = _row(conn, ctx.p['id'])
    if row['status'] != 'review':
        raise ApiError(409, 'This statement line has already been handled')
    acct = conn.execute('SELECT * FROM accounts WHERE id=?', (row['account_id'],)).fetchone()
    if ctx.body.get('undeposited'):
        udf = db.system_account(conn, 'udf')
        if acct['type'] != 'Bank' or row['amount'] <= 0:
            raise ApiError(400, 'Only bank deposits can be matched to Undeposited Funds')
        if ledger.account_net(conn, udf, end=row['date']) < row['amount']:
            raise ApiError(400, 'The deposit is larger than the amount held in Undeposited Funds')
        tid = categorize(conn, row, [{'account_id': udf, 'amount': row['amount']}], memo='Bank deposit')
        return ledger.txn_json(conn, tid)
    if ctx.body.get('invoice_id'):
        if acct['type'] != 'Bank':
            raise ApiError(400, 'Only bank deposits can be matched to invoices')
        if row['amount'] <= 0:
            raise ApiError(400, 'Only deposits can be matched to invoices')
        inv = invoices.get_invoice(conn, as_int(ctx.body['invoice_id'], 'invoice_id'))
        if inv['balance_cents'] < row['amount']:
            raise ApiError(400, 'The deposit is larger than the open balance of this invoice')
        pid = invoices.save_payment(conn, {
            'customer_id': inv['customer_id'], 'date': row['date'], 'amount_cents': row['amount'],
            'deposit_account_id': row['account_id'], 'memo': row['description'],
            'applications': [{'invoice_id': inv['id'], 'amount_cents': row['amount']}]})
        tid = conn.execute('SELECT txn_id FROM payments WHERE id=?', (pid,)).fetchone()[0]
        line = conn.execute('SELECT id FROM lines WHERE txn_id=? AND account_id=?', (tid, row['account_id'])).fetchone()
        conn.execute("UPDATE feed SET status='posted', line_id=?, created_txn=1 WHERE id=?", (line['id'], row['id']))
        reconcile.auto_clear(conn, line['id'], row['date'])
        return ledger.txn_json(conn, tid)
    line_id = as_int(ctx.body.get('line_id'), 'line_id')
    line = conn.execute('SELECT * FROM lines WHERE id=?', (line_id,)).fetchone()
    if not line or line['account_id'] != row['account_id']:
        raise ApiError(400, 'That transaction is not in this account')
    if line['debit'] - line['credit'] != row['amount']:
        raise ApiError(400, 'The amounts do not match')
    if conn.execute('SELECT 1 FROM feed WHERE line_id=?', (line_id,)).fetchone():
        raise ApiError(409, 'That transaction is already matched to another statement line')
    if line['recon_status'] == 'R':
        raise ApiError(409, 'That transaction is already reconciled')
    conn.execute("UPDATE feed SET status='posted', line_id=?, created_txn=0 WHERE id=?", (line_id, row['id']))
    reconcile.auto_clear(conn, line_id, row['date'])
    return ledger.txn_json(conn, line['txn_id'])


def _exclude(conn, row):
    if row['status'] != 'review':
        raise ApiError(409, 'Only lines waiting for review can be excluded')
    conn.execute("UPDATE feed SET status='excluded' WHERE id=?", (row['id'],))


def _undo(conn, row):
    if row['status'] == 'posted' and row['created_txn'] and row['line_id']:
        l = conn.execute('SELECT txn_id FROM lines WHERE id=?', (row['line_id'],)).fetchone()
        if l:
            ledger.delete_txn(conn, l['txn_id'])
    elif row['status'] == 'posted' and row['line_id']:
        l = conn.execute('SELECT recon_status FROM lines WHERE id=?', (row['line_id'],)).fetchone()
        if l and l['recon_status'] == 'R':
            raise ApiError(409, 'This transaction has been reconciled. Undo the reconciliation first.')
    conn.execute("UPDATE feed SET status='review', line_id=NULL, created_txn=0 WHERE id=?", (row['id'],))


@route('POST', '/api/feed/<id>/exclude')
def exclude_one(ctx):
    _exclude(ctx.conn, _row(ctx.conn, ctx.p['id']))
    return {'ok': True}


@route('POST', '/api/feed/<id>/undo')
def undo_one(ctx):
    _undo(ctx.conn, _row(ctx.conn, ctx.p['id']))
    return {'ok': True}


@route('POST', '/api/feed/batch')
def batch(ctx):
    conn, b = ctx.conn, ctx.body
    action = b.get('action')
    ids = [as_int(i, 'id') for i in as_list(b.get('ids') or [], 'ids')]
    if action not in ('accept', 'exclude', 'undo'):
        raise ApiError(400, 'Unknown action')
    done = skipped = 0
    rules = conn.execute('SELECT * FROM rules ORDER BY id').fetchall()
    active = {r[0] for r in conn.execute('SELECT id FROM accounts WHERE active=1')}
    for fid in ids:
        row = _row(conn, fid)
        if action == 'exclude':
            if row['status'] == 'review':
                _exclude(conn, row)
                done += 1
            else:
                skipped += 1
        elif action == 'undo':
            if row['status'] != 'review':
                _undo(conn, row)
                done += 1
            else:
                skipped += 1
        else:
            if row['status'] != 'review':
                skipped += 1
                continue
            account_id, name_id = as_int(b.get('account_id'), 'category', required=False), None
            if not account_id:
                # A possible match to an entry already in the books always wins over a suggestion:
                # leave it for the user so nothing is counted twice.
                atype = conn.execute('SELECT type FROM accounts WHERE id=?', (row['account_id'],)).fetchone()[0]
                s = None if _matches(conn, row, atype) else _suggest(row, rules, _history(conn), active)
                if not s:
                    skipped += 1
                    continue
                account_id, name_id = s['account_id'], s['name_id']
            try:
                categorize(conn, row, [{'account_id': account_id, 'amount': abs(row['amount'])}], name_id)
                done += 1
            except ApiError:
                skipped += 1
    return {'done': done, 'skipped': skipped}


# ---------------------------------------------------------------- rules

def _rule_json(conn, r):
    a = conn.execute('SELECT name FROM accounts WHERE id=?', (r['account_id'],)).fetchone()
    n = conn.execute('SELECT name FROM names WHERE id=?', (r['name_id'],)).fetchone() if r['name_id'] else None
    return {'id': r['id'], 'match_text': r['match_text'], 'direction': r['direction'],
            'account_id': r['account_id'], 'account': a['name'] if a else '', 'name_id': r['name_id'],
            'name': n['name'] if n else None}


@route('GET', '/api/rules')
def list_rules(ctx):
    return [_rule_json(ctx.conn, r) for r in ctx.conn.execute('SELECT * FROM rules ORDER BY id').fetchall()]


@route('POST', '/api/rules')
def create_rule(ctx):
    b = ctx.body
    rid = _add_rule(ctx.conn, b.get('match_text'), b.get('direction') or 'any',
                    as_int(b.get('account_id'), 'category'), as_int(b.get('name_id'), 'name', required=False))
    return _rule_json(ctx.conn, ctx.conn.execute('SELECT * FROM rules WHERE id=?', (rid,)).fetchone())


@route('PUT', '/api/rules/<id>')
def update_rule(ctx):
    if not ctx.conn.execute('SELECT 1 FROM rules WHERE id=?', (ctx.p['id'],)).fetchone():
        raise ApiError(404, 'Rule not found')
    b = ctx.body
    rid = _add_rule(ctx.conn, b.get('match_text'), b.get('direction') or 'any',
                    as_int(b.get('account_id'), 'category'), as_int(b.get('name_id'), 'name', required=False))
    new = ctx.conn.execute('SELECT * FROM rules WHERE id=?', (rid,)).fetchone()
    ctx.conn.execute('DELETE FROM rules WHERE id=?', (rid,))
    ctx.conn.execute('UPDATE rules SET match_text=?, direction=?, account_id=?, name_id=? WHERE id=?',
                     (new['match_text'], new['direction'], new['account_id'], new['name_id'], ctx.p['id']))
    return _rule_json(ctx.conn, ctx.conn.execute('SELECT * FROM rules WHERE id=?', (ctx.p['id'],)).fetchone())


@route('DELETE', '/api/rules/<id>')
def delete_rule(ctx):
    if not ctx.conn.execute('SELECT 1 FROM rules WHERE id=?', (ctx.p['id'],)).fetchone():
        raise ApiError(404, 'Rule not found')
    ctx.conn.execute('DELETE FROM rules WHERE id=?', (ctx.p['id'],))
    return {'deleted': True}
