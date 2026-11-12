"""Double-entry posting engine. Every transaction in the system goes through post_txn."""
import re

from .util import ApiError, parse_date, money


def ensure_editable(conn, txn_id):
    r = conn.execute("SELECT 1 FROM lines WHERE txn_id=? AND recon_status='R' LIMIT 1", (txn_id,)).fetchone()
    if r:
        raise ApiError(409, 'This transaction has been reconciled. Undo the reconciliation '
                            'before changing or deleting it.')


def next_ref(conn, ttype, start=1):
    """Next document number. Like QuickBooks, continue from the most recent entry when it ends
    in digits behind a prefix (JE-9 -> JE-10); otherwise highest plain number + 1."""
    last = conn.execute("SELECT ref FROM transactions WHERE type=? AND ref<>'' ORDER BY id DESC LIMIT 1",
                        (ttype,)).fetchone()
    if last:
        m = re.fullmatch(r'(.*[^0-9])([0-9]{1,12})', last[0])
        if m and last[0].isascii():
            return m.group(1) + str(int(m.group(2)) + 1).zfill(len(m.group(2)))
    mx = start - 1
    for r in conn.execute('SELECT ref FROM transactions WHERE type=?', (ttype,)):
        if r[0].isascii() and r[0].isdigit() and len(r[0]) <= 12:
            mx = max(mx, int(r[0]))
    return str(mx + 1)


def _clean(conn, lines):
    out = []
    for ln in lines:
        dr, cr = int(ln.get('debit') or 0), int(ln.get('credit') or 0)
        net = dr - cr
        if net == 0:
            continue
        acct = ln.get('account_id')
        row = conn.execute('SELECT id FROM accounts WHERE id=?', (acct,)).fetchone() if acct else None
        if not row:
            raise ApiError(400, 'Every line needs a valid account')
        name_id = ln.get('name_id') or None
        if name_id and not conn.execute('SELECT 1 FROM names WHERE id=?', (name_id,)).fetchone():
            raise ApiError(400, 'Unknown customer/vendor on a line')
        out.append({'account_id': int(acct), 'debit': net if net > 0 else 0,
                    'credit': -net if net < 0 else 0,
                    'memo': str(ln.get('memo') or '').strip(), 'name_id': name_id})
    if len(out) < 2:
        raise ApiError(400, 'A transaction needs at least two lines with amounts')
    td, tc = sum(l['debit'] for l in out), sum(l['credit'] for l in out)
    if td != tc:
        raise ApiError(400, 'Debits (%.2f) must equal credits (%.2f)' % (td / 100.0, tc / 100.0))
    return out


def post_txn(conn, date, ttype, lines, ref='', memo='', name_id=None, txn_id=None):
    """Create (or replace, when txn_id is given) a balanced transaction. Amounts are cents."""
    date = parse_date(date)
    lines = _clean(conn, lines)
    if name_id and not conn.execute('SELECT 1 FROM names WHERE id=?', (name_id,)).fetchone():
        raise ApiError(400, 'Unknown customer/vendor')
    links, marks = [], []
    if txn_id:
        if not conn.execute('SELECT 1 FROM transactions WHERE id=?', (txn_id,)).fetchone():
            raise ApiError(404, 'Transaction not found')
        ensure_editable(conn, txn_id)
        links = conn.execute(
            'SELECT f.id, l.account_id, l.debit - l.credit AS net FROM feed f '
            'JOIN lines l ON l.id = f.line_id WHERE l.txn_id=?', (txn_id,)).fetchall()
        marks = conn.execute("SELECT account_id, debit - credit AS net, reconciliation_id FROM lines "
                             "WHERE txn_id=? AND recon_status='C'", (txn_id,)).fetchall()
        conn.execute('DELETE FROM lines WHERE txn_id=?', (txn_id,))
        conn.execute('UPDATE transactions SET date=?, type=?, ref=?, memo=?, name_id=? WHERE id=?',
                     (date, ttype, ref or '', memo or '', name_id or None, txn_id))
    else:
        cur = conn.execute('INSERT INTO transactions(date, type, ref, memo, name_id) VALUES (?,?,?,?,?)',
                           (date, ttype, ref or '', memo or '', name_id or None))
        txn_id = cur.lastrowid
    new_ids = []
    for i, l in enumerate(lines):
        cur = conn.execute(
            'INSERT INTO lines(txn_id, seq, account_id, debit, credit, memo, name_id) VALUES (?,?,?,?,?,?,?)',
            (txn_id, i, l['account_id'], l['debit'], l['credit'], l['memo'], l['name_id']))
        new_ids.append((cur.lastrowid, l['account_id'], l['debit'] - l['credit']))
    # An edit that leaves the bank-side line alone keeps its in-progress reconcile check mark.
    marked = set()
    for mk in marks:
        hit = next((i for i, a, n in new_ids if a == mk['account_id'] and n == mk['net'] and i not in marked), None)
        rec = conn.execute("SELECT statement_date FROM reconciliations WHERE id=? AND status='open'",
                           (mk['reconciliation_id'],)).fetchone()
        if hit and rec and date <= rec['statement_date']:
            marked.add(hit)
            conn.execute("UPDATE lines SET recon_status='C', reconciliation_id=? WHERE id=?",
                         (mk['reconciliation_id'], hit))
    # Keep statement rows tied to the edited transaction where the bank-side line still exists.
    used = set()
    for f in links:
        hit = next((i for i, a, n in new_ids if a == f['account_id'] and n == f['net'] and i not in used), None)
        if hit:
            used.add(hit)
            conn.execute('UPDATE feed SET line_id=? WHERE id=?', (hit, f['id']))
        else:
            conn.execute("UPDATE feed SET status='review', line_id=NULL, created_txn=0 WHERE id=?", (f['id'],))
    return txn_id


def delete_txn(conn, txn_id):
    t = conn.execute('SELECT * FROM transactions WHERE id=?', (txn_id,)).fetchone()
    if not t:
        raise ApiError(404, 'Transaction not found')
    ensure_editable(conn, txn_id)
    for doc, lines, fk, apps, label in (('invoices', 'invoice_lines', 'invoice_id', 'payment_apps', 'invoice'),
                                        ('bills', 'bill_lines', 'bill_id', 'bill_payment_apps', 'bill')):
        d = conn.execute('SELECT id FROM %s WHERE txn_id=?' % doc, (txn_id,)).fetchone()
        if d:
            if conn.execute('SELECT 1 FROM %s WHERE %s=? LIMIT 1' % (apps, fk), (d['id'],)).fetchone():
                raise ApiError(409, 'This %s has payments applied. Delete or un-apply the payments first.' % label)
            conn.execute('DELETE FROM %s WHERE %s=?' % (lines, fk), (d['id'],))
            conn.execute('DELETE FROM %s WHERE id=?' % doc, (d['id'],))
    for pay, apps in (('payments', 'payment_apps'), ('bill_payments', 'bill_payment_apps')):
        p = conn.execute('SELECT id FROM %s WHERE txn_id=?' % pay, (txn_id,)).fetchone()
        if p:
            conn.execute('DELETE FROM %s WHERE payment_id=?' % apps, (p['id'],))
            conn.execute('DELETE FROM %s WHERE id=?' % pay, (p['id'],))
    conn.execute('DELETE FROM paychecks WHERE txn_id=?', (txn_id,))
    conn.execute('DELETE FROM inv_moves WHERE txn_id=?', (txn_id,))
    conn.execute("UPDATE feed SET status='review', line_id=NULL, created_txn=0 "
                 'WHERE line_id IN (SELECT id FROM lines WHERE txn_id=?)', (txn_id,))
    conn.execute('DELETE FROM lines WHERE txn_id=?', (txn_id,))
    conn.execute('DELETE FROM transactions WHERE id=?', (txn_id,))


def txn_json(conn, txn_id):
    t = conn.execute('SELECT t.*, n.name AS name FROM transactions t '
                     'LEFT JOIN names n ON n.id = t.name_id WHERE t.id=?', (txn_id,)).fetchone()
    if not t:
        raise ApiError(404, 'Transaction not found')
    rows = conn.execute(
        'SELECT l.*, a.name AS account, a.type AS account_type, n.name AS name, n.kind AS name_kind '
        'FROM lines l JOIN accounts a ON a.id = l.account_id '
        'LEFT JOIN names n ON n.id = l.name_id WHERE l.txn_id=? ORDER BY l.seq, l.id', (txn_id,)).fetchall()
    one = lambda table: conn.execute('SELECT id FROM %s WHERE txn_id=?' % table, (txn_id,)).fetchone()  # noqa: E731
    inv, pay = one('invoices'), one('payments')
    bill, bpay, chk = one('bills'), one('bill_payments'), one('paychecks')
    from_feed = conn.execute(
        'SELECT f.id FROM feed f JOIN lines l ON l.id=f.line_id WHERE l.txn_id=? AND f.created_txn=1',
        (txn_id,)).fetchone()
    return {
        'id': t['id'], 'date': t['date'], 'type': t['type'], 'ref': t['ref'], 'memo': t['memo'],
        'name_id': t['name_id'], 'name': t['name'],
        'total': money(sum(r['debit'] for r in rows)),
        'reconciled': any(r['recon_status'] == 'R' for r in rows),
        'invoice_id': inv['id'] if inv else None,
        'payment_id': pay['id'] if pay else None,
        'bill_id': bill['id'] if bill else None,
        'bill_payment_id': bpay['id'] if bpay else None,
        'paycheck_id': chk['id'] if chk else None,
        'feed_id': from_feed['id'] if from_feed else None,
        'lines': [{
            'id': r['id'], 'account_id': r['account_id'], 'account': r['account'],
            'account_type': r['account_type'],
            'debit': money(r['debit']), 'credit': money(r['credit']), 'memo': r['memo'],
            'name_id': r['name_id'], 'name': r['name'], 'name_kind': r['name_kind'],
            'recon_status': r['recon_status'],
        } for r in rows],
    }


def account_net(conn, account_id, end=None, start=None):
    """Sum of debit - credit for an account, optionally bounded by dates (inclusive)."""
    sql = ('SELECT COALESCE(SUM(l.debit - l.credit), 0) FROM lines l '
           'JOIN transactions t ON t.id = l.txn_id WHERE l.account_id=?')
    args = [account_id]
    if start:
        sql += ' AND t.date >= ?'
        args.append(start)
    if end:
        sql += ' AND t.date <= ?'
        args.append(end)
    return conn.execute(sql, args).fetchone()[0]
