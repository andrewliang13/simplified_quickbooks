"""End-to-end tests of the main workflows."""
import unittest

from helpers import ApiCase

BANK_CSV = """Date,Description,Amount
01/05/2026,OFFICE DEPOT #123,-150.25
01/06/2026,CUSTOMER DEPOSIT,2000.00
01/10/2026,SHELL OIL 5551,-45.00
01/15/2026,CC PAYMENT THANK YOU,-300.00
01/20/2026,OFFICE DEPOT #987,-20.00
"""

CC_CSV = """Transaction Date,Post Date,Description,Category,Type,Amount
01/03/2026,01/04/2026,DELTA AIR LINES,Travel,Sale,-420.00
01/08/2026,01/09/2026,STAPLES,Office,Sale,-80.00
01/15/2026,01/15/2026,PAYMENT THANK YOU,,Payment,300.00
"""


class ChartOfAccounts(ApiCase):
    def test_crud(self):
        a = self.post('/api/accounts', {'name': 'Software', 'type': 'Expense', 'number': '6500'})
        self.assertEqual(a['type'], 'Expense')
        a = self.put('/api/accounts/%d' % a['id'], {'name': 'Software Subscriptions'})
        self.assertEqual(a['name'], 'Software Subscriptions')
        self.post('/api/accounts', {'name': 'software subscriptions', 'type': 'Expense'}, expect=409)
        self.post('/api/accounts', {'name': 'X', 'type': 'Nope'}, expect=400)
        self.delete('/api/accounts/%d' % a['id'])
        self.assertNotIn('Software Subscriptions', [x['name'] for x in self.get('/api/accounts')])

    def test_used_account_cannot_be_deleted_but_can_be_inactive(self):
        self.journal('2026-01-01', ('Checking', 100, 0), ('Sales', 0, 100))
        sid = self.acct('Sales')
        self.delete('/api/accounts/%d' % sid, expect=409)
        self.put('/api/accounts/%d' % sid, {'active': False})
        self.assertNotIn('Sales', [x['name'] for x in self.get('/api/accounts')])
        self.assertIn('Sales', [x['name'] for x in self.get('/api/accounts', include_inactive=1)])

    def test_system_account_protected(self):
        self.delete('/api/accounts/%d' % self.acct('Accounts Receivable'), expect=400)
        self.put('/api/accounts/%d' % self.acct('Retained Earnings'), {'type': 'Expense'}, expect=400)

    def test_subaccount_and_opening_balance(self):
        parent = self.acct('Utilities')
        self.post('/api/accounts', {'name': 'Electric', 'type': 'Expense', 'parent_id': parent})
        self.post('/api/accounts', {'name': 'Bad', 'type': 'Income', 'parent_id': parent}, expect=400)
        a = self.post('/api/accounts', {'name': 'Money Market', 'type': 'Bank', 'opening_balance': '5000',
                                        'opening_date': '2026-01-01'})
        self.assertEqual(a['balance'], 5000.0)
        bs = self.get('/api/reports/balance_sheet', end='2026-01-31')
        self.assertEqual(self.val(bs, 'Money Market'), 5000.0)
        self.assertEqual(self.val(bs, 'Opening Balance Equity'), 5000.0)


class Names(ApiCase):
    def test_customers_and_vendors(self):
        c = self.post('/api/customers', {'name': 'Acme Corp', 'email': 'ap@acme.test'})
        v = self.post('/api/vendors', {'name': 'Office Depot'})
        self.post('/api/vendors', {'name': 'acme corp'}, expect=409)
        c = self.put('/api/customers/%d' % c['id'], {'phone': '555-1212'})
        self.assertEqual((c['name'], c['phone'], c['email']), ('Acme Corp', '555-1212', 'ap@acme.test'))
        self.assertEqual(len(self.get('/api/customers')), 1)
        self.assertEqual(len(self.get('/api/vendors')), 1)
        self.delete('/api/vendors/%d' % v['id'])
        self.assertEqual(self.get('/api/vendors'), [])
        self.post('/api/customers', {'name': ''}, expect=400)

    def test_used_customer_cannot_be_deleted(self):
        c = self.post('/api/customers', {'name': 'Acme'})
        self.post('/api/invoices', {'customer_id': c['id'], 'date': '2026-01-01',
                                    'lines': [{'description': 'Work', 'qty': 1, 'rate': 100,
                                               'account_id': self.acct('Sales')}]})
        self.delete('/api/customers/%d' % c['id'], expect=409)
        self.put('/api/customers/%d' % c['id'], {'active': False})
        self.assertEqual(self.get('/api/customers'), [])


class Journals(ApiCase):
    def test_balanced_entry_required(self):
        self.post('/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': self.acct('Checking'), 'debit': 100},
            {'account_id': self.acct('Sales'), 'credit': 90}]}, expect=400)
        self.post('/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': self.acct('Checking'), 'debit': 100, 'credit': 100},
            {'account_id': self.acct('Sales'), 'credit': 100}]}, expect=400)
        self.post('/api/journals', {'lines': []}, expect=400)

    def test_create_edit_delete(self):
        j = self.journal('2026-01-01', ('Checking', '1,000.00', 0), ('Sales', 0, 600), ('Service Income', 0, 400))
        self.assertEqual((j['ref'], j['total']), ('1', 1000.0))
        j2 = self.journal('2026-01-02', ('Rent and Lease', 250, 0), ('Checking', 0, 250))
        self.assertEqual(j2['ref'], '2')
        self.put('/api/journals/%d' % j['id'], {'date': '2026-01-03', 'lines': [
            {'account_id': self.acct('Checking'), 'debit': 500},
            {'account_id': self.acct('Sales'), 'credit': 500}]})
        pl = self.get('/api/reports/profit_loss', start='2026-01-01', end='2026-01-31')
        self.assertEqual(self.val(pl, 'Net Income'), 250.0)
        self.delete('/api/transactions/%d' % j2['id'])
        self.assertEqual(len(self.get('/api/journals')), 1)

    def test_ar_line_needs_customer(self):
        self.post('/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': self.acct('Accounts Receivable'), 'debit': 100},
            {'account_id': self.acct('Sales'), 'credit': 100}]}, expect=400)


class Banking(ApiCase):
    def imp(self, account, csv, **kw):
        body = {'account_id': self.acct(account), 'filename': 'stmt.csv', 'content': csv}
        body.update(kw)
        return self.post('/api/feed/import', body)

    def test_import_categorize_and_duplicates(self):
        chk = self.acct('Checking')
        pv = self.post('/api/feed/preview', {'account_id': chk, 'content': BANK_CSV})
        self.assertEqual((pv['count'], pv['money_in'], pv['money_out']), (5, 2000.0, 515.25))
        self.assertEqual(self.imp('Checking', BANK_CSV)['imported'], 5)
        again = self.imp('Checking', BANK_CSV)
        self.assertEqual((again['imported'], again['duplicates']), (0, 5))
        feed = self.get('/api/feed', account_id=chk)
        self.assertEqual(feed['counts']['review'], 5)
        rows = {r['description']: r for r in feed['rows']}
        self.post('/api/feed/%d/categorize' % rows['OFFICE DEPOT #123']['id'],
                  {'account_id': self.acct('Office Supplies and Software'), 'rule_text': 'office depot'})
        feed = self.get('/api/feed', account_id=chk)
        rows = {r['description']: r for r in feed['rows']}
        # rule now suggests the same category for the other Office Depot line
        self.assertEqual(rows['OFFICE DEPOT #987']['suggestion']['account_id'],
                         self.acct('Office Supplies and Software'))
        r = self.post('/api/feed/batch', {'action': 'accept', 'ids': [x['id'] for x in feed['rows']]})
        self.assertEqual((r['done'], r['skipped']), (1, 3))
        self.post('/api/feed/%d/categorize' % rows['CUSTOMER DEPOSIT']['id'], {'account_id': self.acct('Sales')})
        self.post('/api/feed/%d/categorize' % rows['SHELL OIL 5551']['id'], {'splits': [
            {'account_id': self.acct('Travel'), 'amount': 30},
            {'account_id': self.acct('Meals and Entertainment'), 'amount': 15}]})
        self.post('/api/feed/%d/exclude' % rows['CC PAYMENT THANK YOU']['id'])
        pl = self.get('/api/reports/profit_loss', start='2026-01-01', end='2026-01-31')
        self.assertEqual(self.val(pl, 'Office Supplies and Software'), 170.25)
        self.assertEqual(self.val(pl, 'Travel'), 30.0)
        self.assertEqual(self.val(pl, 'Net Income'), 2000 - 170.25 - 45)
        bs = self.get('/api/reports/balance_sheet', end='2026-01-31')
        self.assertEqual(self.val(bs, 'Checking'), 2000 - 170.25 - 45)
        # undo puts the line back in review and removes the ledger entry
        posted = self.get('/api/feed', account_id=chk, status='posted')['rows']
        self.assertEqual(len(posted), 4)
        dep = [p for p in posted if p['description'] == 'CUSTOMER DEPOSIT'][0]
        self.assertEqual(dep['category'], 'Sales')
        self.post('/api/feed/%d/undo' % dep['id'])
        bs = self.get('/api/reports/balance_sheet', end='2026-01-31')
        self.assertEqual(self.val(bs, 'Checking'), -215.25)

    def test_split_must_add_up(self):
        self.imp('Checking', BANK_CSV)
        row = self.get('/api/feed', account_id=self.acct('Checking'))['rows'][0]
        self.post('/api/feed/%d/categorize' % row['id'], {'splits': [
            {'account_id': self.acct('Travel'), 'amount': 1}]}, expect=400)

    def test_credit_card_and_transfer_match(self):
        chk, cc = self.acct('Checking'), self.acct('Credit Card')
        self.imp('Checking', BANK_CSV)
        self.assertEqual(self.imp('Credit Card', CC_CSV)['imported'], 3)
        rows = {r['description']: r for r in self.get('/api/feed', account_id=cc)['rows']}
        self.assertEqual(rows['DELTA AIR LINES']['amount'], -420.0)
        self.assertEqual(rows['DELTA AIR LINES']['date'], '2026-01-04')
        self.post('/api/feed/%d/categorize' % rows['DELTA AIR LINES']['id'], {'account_id': self.acct('Travel')})
        self.post('/api/feed/%d/categorize' % rows['STAPLES']['id'],
                  {'account_id': self.acct('Office Supplies and Software')})
        # pay the card from checking: categorize the bank side as a transfer to the card...
        brow = [r for r in self.get('/api/feed', account_id=chk)['rows'] if r['amount'] == -300.0][0]
        t = self.post('/api/feed/%d/categorize' % brow['id'], {'account_id': cc})
        self.assertEqual(t['type'], 'Credit Card Payment')
        # ...then the card side is offered as a match instead of creating a duplicate.
        crow = [r for r in self.get('/api/feed', account_id=cc)['rows'] if r['amount'] == 300.0][0]
        self.assertEqual(len(crow['matches']), 1)
        self.post('/api/feed/%d/match' % crow['id'], {'line_id': crow['matches'][0]['line_id']})
        bs = self.get('/api/reports/balance_sheet', end='2026-01-31')
        self.assertEqual(self.val(bs, 'Credit Card'), 200.0)
        self.assertEqual(self.val(bs, 'Checking'), -300.0)
        self.assertEqual(self.val(bs, 'TOTAL ASSETS'), self.val(bs, 'TOTAL LIABILITIES AND EQUITY'))

    def test_debit_credit_columns_and_ofx(self):
        csv = 'Date,Description,Debit,Credit,Balance\n2026-02-01,RENT,"1,200.00",,800.00\n2026-02-02,DEPOSIT,,500.00,1300.00\n'
        pv = self.post('/api/feed/preview', {'account_id': self.acct('Checking'), 'content': csv})
        self.assertEqual([r['amount'] for r in pv['rows']], [-1200.0, 500.0])
        ofx = ('OFXHEADER:100\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>\n'
               '<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260203120000<TRNAMT>-12.34<FITID>A1<NAME>COFFEE &amp; CO\n'
               '</STMTTRN><STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260204<TRNAMT>100.00<FITID>A2<NAME>REFUND<MEMO>ORDER 5\n'
               '</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>')
        pv = self.post('/api/feed/preview', {'account_id': self.acct('Checking'), 'filename': 'x.qfx', 'content': ofx})
        self.assertEqual(pv['format'], 'ofx')
        self.assertEqual([(r['date'], r['description'], r['amount']) for r in pv['rows']],
                         [('2026-02-03', 'COFFEE & CO', -12.34), ('2026-02-04', 'REFUND ORDER 5', 100.0)])

    def test_flip_sign(self):
        csv = 'Date,Description,Amount\n01/03/2026,AIRLINE,420.00\n'
        pv = self.post('/api/feed/preview', {'account_id': self.acct('Credit Card'), 'content': csv, 'flip_sign': True})
        self.assertEqual(pv['rows'][0]['amount'], -420.0)


class Invoicing(ApiCase):
    def setUp(self):
        super().setUp()
        self.cust = self.post('/api/customers', {'name': 'Acme'})['id']

    def invoice(self, date, amount, **kw):
        body = {'customer_id': self.cust, 'date': date, 'terms': 'Net 30',
                'lines': [{'description': 'Consulting', 'qty': 2, 'rate': amount / 2.0,
                           'account_id': self.acct('Service Income')}]}
        body.update(kw)
        return self.post('/api/invoices', body)

    def test_invoice_and_payment(self):
        inv = self.invoice('2026-01-10', 1000)
        self.assertEqual((inv['number'], inv['total'], inv['balance'], inv['due_date']),
                         ('1001', 1000.0, 1000.0, '2026-02-09'))
        inv2 = self.invoice('2026-01-20', 500)
        self.assertEqual(inv2['number'], '1002')
        bs = self.get('/api/reports/balance_sheet', end='2026-01-31')
        self.assertEqual(self.val(bs, 'Accounts Receivable'), 1500.0)
        self.assertEqual(self.get('/api/customers/%d' % self.cust)['balance'], 1500.0)
        # partial payment applied to a chosen invoice
        p = self.post('/api/payments', {'customer_id': self.cust, 'date': '2026-01-25', 'amount': 400,
                                        'deposit_account_id': self.acct('Checking'),
                                        'applications': [{'invoice_id': inv['id'], 'amount': 400}]})
        self.assertEqual(p['unapplied'], 0)
        self.assertEqual(self.get('/api/invoices/%d' % inv['id'])['balance'], 600.0)
        # over-application is rejected
        self.post('/api/payments', {'customer_id': self.cust, 'date': '2026-01-26', 'amount': 700,
                                    'applications': [{'invoice_id': inv['id'], 'amount': 700}]}, expect=400)
        # auto-apply oldest first; goes to Undeposited Funds by default
        p2 = self.post('/api/payments', {'customer_id': self.cust, 'date': '2026-01-28', 'amount': 800})
        self.assertEqual([(a['number'], a['amount']) for a in p2['applications']], [('1001', 600.0), ('1002', 200.0)])
        self.assertEqual(self.get('/api/invoices/%d' % inv['id'])['status'], 'Paid')
        self.assertEqual(self.get('/api/invoices/%d' % inv2['id'])['balance'], 300.0)
        bs = self.get('/api/reports/balance_sheet', end='2026-01-31')
        self.assertEqual(self.val(bs, 'Accounts Receivable'), 300.0)
        self.assertEqual(self.val(bs, 'Undeposited Funds'), 800.0)
        self.assertEqual(self.val(bs, 'Checking'), 400.0)
        # invoice with payments cannot be deleted; deleting the payment reopens it
        self.delete('/api/invoices/%d' % inv['id'], expect=409)
        self.delete('/api/payments/%d' % p2['id'])
        self.assertEqual(self.get('/api/invoices/%d' % inv['id'])['balance'], 600.0)
        aging = self.get('/api/reports/ar_aging', end='2026-03-15')
        self.assertEqual(self.val(aging, 'Acme', 5), 1100.0)
        self.assertEqual(self.val(aging, 'Acme', 2), 600.0)     # due Feb 9  -> 34 days
        self.assertEqual(self.val(aging, 'Acme', 1), 500.0)     # due Feb 19 -> 24 days
        self.assertEqual(self.drill_total(aging, 'Acme', 5), 1100.0)

    def test_edit_invoice(self):
        inv = self.invoice('2026-01-10', 1000)
        self.post('/api/payments', {'customer_id': self.cust, 'date': '2026-01-25', 'amount': 400})
        body = {'customer_id': self.cust, 'date': '2026-01-10',
                'lines': [{'description': 'Less', 'amount': 300, 'account_id': self.acct('Sales')}]}
        self.put('/api/invoices/%d' % inv['id'], body, expect=400)
        body['lines'][0]['amount'] = 900
        inv = self.put('/api/invoices/%d' % inv['id'], body)
        self.assertEqual((inv['total'], inv['balance'], inv['status']), (900.0, 500.0, 'Overdue'))
        pl = self.get('/api/reports/profit_loss', start='2026-01-01', end='2026-01-31')
        self.assertEqual(self.val(pl, 'Sales'), 900.0)

    def test_match_bank_deposit_to_invoice(self):
        inv = self.invoice('2026-01-02', 2000)
        chk = self.acct('Checking')
        self.post('/api/feed/import', {'account_id': chk, 'content': BANK_CSV})
        row = [r for r in self.get('/api/feed', account_id=chk)['rows'] if r['amount'] == 2000.0][0]
        m = [x for x in row['matches'] if x['kind'] == 'invoice']
        self.assertEqual(len(m), 1)
        self.post('/api/feed/%d/match' % row['id'], {'invoice_id': inv['id']})
        self.assertEqual(self.get('/api/invoices/%d' % inv['id'])['status'], 'Paid')
        bs = self.get('/api/reports/balance_sheet', end='2026-01-31')
        self.assertEqual(self.val(bs, 'Checking'), 2000.0)
        with self.assertRaises(KeyError):
            self.row(bs, 'Accounts Receivable')   # zero balance rows are hidden


class Reconciliation(ApiCase):
    def setUp(self):
        super().setUp()
        self.chk = self.acct('Checking')
        self.journal('2026-01-01', ('Checking', 1000, 0), ("Owner's Investment", 0, 1000))
        self.journal('2026-01-10', ('Rent and Lease', 400, 0), ('Checking', 0, 400))
        self.journal('2026-01-20', ('Checking', 250, 0), ('Sales', 0, 250))
        self.journal('2026-01-30', ('Utilities', 75, 0), ('Checking', 0, 75))   # outstanding check
        self.journal('2026-02-05', ('Checking', 10, 0), ('Sales', 0, 10))       # next period

    def test_reconcile_flow(self):
        rec = self.post('/api/reconciliations', {'account_id': self.chk, 'statement_date': '2026-01-31',
                                                 'ending_balance': 850})
        self.assertEqual((rec['beginning_balance'], rec['difference'], len(rec['lines'])), (0, 850.0, 4))
        self.post('/api/reconciliations', {'account_id': self.chk, 'statement_date': '2026-01-31',
                                           'ending_balance': 850}, expect=409)
        ids = [l['line_id'] for l in rec['lines'] if l['date'] <= '2026-01-20']
        rec = self.post('/api/reconciliations/%d/toggle' % rec['id'], {'line_ids': ids[:2], 'cleared': True})
        self.assertEqual(rec['difference'], 250.0)
        self.post('/api/reconciliations/%d/finish' % rec['id'], expect=400)
        rec = self.post('/api/reconciliations/%d/toggle' % rec['id'], {'line_ids': ids[2:], 'cleared': True})
        self.assertEqual((rec['difference'], rec['cleared_deposits'], rec['cleared_payments']), (0, 1250.0, 400.0))
        done = self.post('/api/reconciliations/%d/finish' % rec['id'])
        self.assertEqual((done['status'], done['register_balance'], done['uncleared_total']), ('done', 775.0, -75.0))
        # reconciled entries are locked
        rent = [l for l in done['lines'] if l['payment'] == 400.0][0]
        self.delete('/api/transactions/%d' % rent['txn_id'], expect=409)
        # next statement starts from the reconciled balance
        rec2 = self.post('/api/reconciliations', {'account_id': self.chk, 'statement_date': '2026-02-28',
                                                  'ending_balance': 785})
        self.assertEqual((rec2['beginning_balance'], len(rec2['lines'])), (850.0, 2))
        self.post('/api/reconciliations', {'account_id': self.chk, 'statement_date': '2026-01-15',
                                           'ending_balance': 1}, expect=409)
        self.delete('/api/reconciliations/%d' % rec2['id'])          # cancel in-progress
        self.delete('/api/reconciliations/%d' % done['id'])          # undo finished
        self.delete('/api/transactions/%d' % rent['txn_id'])         # unlocked again

    def test_credit_card_reconcile(self):
        cc = self.acct('Credit Card')
        self.journal('2026-01-05', ('Travel', 300, 0), ('Credit Card', 0, 300))
        self.journal('2026-01-15', ('Credit Card', 100, 0), ('Checking', 0, 100))
        rec = self.post('/api/reconciliations', {'account_id': cc, 'statement_date': '2026-01-31',
                                                 'ending_balance': 200})
        rec = self.post('/api/reconciliations/%d/toggle' % rec['id'],
                        {'line_ids': [l['line_id'] for l in rec['lines']], 'cleared': True})
        self.assertEqual((rec['difference'], rec['cleared_payments'], rec['cleared_deposits']), (0, 300.0, 100.0))
        self.post('/api/reconciliations/%d/finish' % rec['id'])


class Reports(ApiCase):
    def setUp(self):
        super().setUp()
        j = self.journal
        j('2025-06-01', ('Checking', 10000, 0), ("Owner's Investment", 0, 10000))
        j('2025-07-01', ('Checking', 3000, 0), ('Sales', 0, 3000))
        j('2025-08-01', ('Rent and Lease', 1000, 0), ('Checking', 0, 1000))
        j('2026-01-15', ('Checking', 5000, 0), ('Sales', 0, 5000))
        j('2026-01-20', ('Rent and Lease', 1200, 0), ('Checking', 0, 1200))
        j('2026-02-10', ('Checking', 4000, 0), ('Sales', 0, 4000))
        j('2026-02-12', ('Cost of Goods Sold', 1500, 0), ('Checking', 0, 1500))
        j('2026-02-20', ('Furniture and Equipment', 2000, 0), ('Checking', 0, 2000))
        j('2026-03-01', ('Checking', 6000, 0), ('Notes Payable', 0, 6000))
        j('2026-03-05', ('Utilities', 300, 0), ('Credit Card', 0, 300))
        j('2026-03-10', ('Checking', 50, 0), ('Interest Income', 0, 50))
        j('2026-03-31', ('Depreciation Expense', 100, 0), ('Accumulated Depreciation', 0, 100))

    def test_profit_and_loss(self):
        pl = self.get('/api/reports/profit_loss', start='2026-01-01', end='2026-03-31')
        self.assertEqual(self.val(pl, 'Total Income'), 9000.0)
        self.assertEqual(self.val(pl, 'Gross Profit'), 7500.0)
        self.assertEqual(self.val(pl, 'Total Expenses'), 1600.0)
        self.assertEqual(self.val(pl, 'Net Operating Income'), 5900.0)
        self.assertEqual(self.val(pl, 'Net Other Income'), 50.0)
        self.assertEqual(self.val(pl, 'Net Income'), 5950.0)

    def test_every_cell_drills_to_its_total(self):
        for name, q in (('profit_loss', {'columns': 'month'}), ('balance_sheet', {'columns': 'month'}),
                        ('cash_flow', {'columns': 'quarter'}), ('profit_loss', {'compare': 'py'}),
                        ('balance_sheet', {'compare': 'pp'}), ('trial_balance', {})):
            rep = self.get('/api/reports/' + name, start='2026-01-01', end='2026-03-31', **q)
            checked = 0
            for row in rep['rows']:
                for i, d in enumerate(row.get('drill') or []):
                    if d:
                        got = self.post('/api/reports/drill', d)['total']
                        self.assertEqual(got, row['values'][i], '%s / %s / col %d' % (name, row['label'], i))
                        checked += 1
            self.assertGreater(checked, 5)

    def test_trending_columns_and_total(self):
        pl = self.get('/api/reports/profit_loss', start='2026-01-01', end='2026-03-31', columns='month')
        self.assertEqual([c['label'] for c in pl['columns']], ['Jan 2026', 'Feb 2026', 'Mar 2026', 'Total'])
        self.assertEqual(self.row(pl, 'Net Income')['values'], [3800.0, 2500.0, -350.0, 5950.0])

    def test_comparison(self):
        pl = self.get('/api/reports/profit_loss', start='2026-02-01', end='2026-02-28', compare='pp')
        self.assertEqual([c['kind'] for c in pl['columns']], ['amount', 'amount', 'change', 'pct'])
        self.assertEqual(pl['columns'][1]['label'], 'Jan 2026 (PP)')
        self.assertEqual(self.row(pl, 'Sales')['values'], [4000.0, 5000.0, -1000.0, -20.0])
        py = self.get('/api/reports/profit_loss', start='2026-07-01', end='2026-07-31', compare='py')
        self.assertEqual(self.row(py, 'Sales')['values'][:2], [0.0, 3000.0])

    def test_balance_sheet(self):
        bs = self.get('/api/reports/balance_sheet', end='2026-03-31')
        self.assertEqual(self.val(bs, 'Checking'), 22350.0)
        self.assertEqual(self.val(bs, 'Total Fixed Assets'), 1900.0)
        self.assertEqual(self.val(bs, 'TOTAL ASSETS'), 24250.0)
        self.assertEqual(self.val(bs, 'Total Liabilities'), 6300.0)
        self.assertEqual(self.val(bs, 'Retained Earnings'), 2000.0)   # 2025 net income rolled forward
        self.assertEqual(self.val(bs, 'Net Income'), 5950.0)
        self.assertEqual(self.val(bs, 'Total Equity'), 17950.0)
        self.assertEqual(self.val(bs, 'TOTAL LIABILITIES AND EQUITY'), 24250.0)
        prior = self.get('/api/reports/balance_sheet', end='2025-12-31')
        self.assertEqual(self.val(prior, 'Net Income'), 2000.0)
        self.assertEqual(self.val(prior, 'Retained Earnings'), 0.0)

    def test_cash_flow(self):
        cf = self.get('/api/reports/cash_flow', start='2026-01-01', end='2026-03-31')
        self.assertEqual(self.val(cf, 'Net Income'), 5950.0)
        self.assertEqual(self.val(cf, 'Accumulated Depreciation'), 100.0)
        self.assertEqual(self.val(cf, 'Credit Card'), 300.0)
        self.assertEqual(self.val(cf, 'Net cash provided by operating activities'), 6350.0)
        self.assertEqual(self.val(cf, 'Net cash provided by investing activities'), -2000.0)
        self.assertEqual(self.val(cf, 'Net cash provided by financing activities'), 6000.0)
        self.assertEqual(self.val(cf, 'NET CASH INCREASE FOR PERIOD'), 10350.0)
        self.assertEqual(self.val(cf, 'Cash at beginning of period'), 12000.0)
        self.assertEqual(self.val(cf, 'CASH AT END OF PERIOD'), 22350.0)

    def test_transaction_detail(self):
        td = self.get('/api/reports/transaction_detail', start='2026-01-01', end='2026-03-31',
                      account_id=self.acct('Checking'))
        g = td['groups'][0]
        self.assertEqual((g['beginning_balance'], g['ending_balance'], len(g['rows'])), (12000.0, 22350.0, 7))
        self.assertEqual(g['rows'][0]['balance'], 17000.0)
        self.assertEqual(g['rows'][0]['split'], 'Sales')
        everything = self.get('/api/reports/transaction_detail', start='2026-01-01', end='2026-03-31')
        self.assertEqual(round(sum(x['total_debit'] for x in everything['groups']), 2),
                         round(sum(x['total_credit'] for x in everything['groups']), 2))

    def test_trial_balance_balances(self):
        tb = self.get('/api/reports/trial_balance', end='2026-03-31')
        total = self.row(tb, 'TOTAL')['values']
        self.assertEqual(total[0], total[1])

    def test_subaccounts_roll_up(self):
        self.post('/api/accounts', {'name': 'Electric', 'type': 'Expense', 'parent_id': self.acct('Utilities')})
        self.journal('2026-03-15', ('Electric', 80, 0), ('Checking', 0, 80))
        pl = self.get('/api/reports/profit_loss', start='2026-03-01', end='2026-03-31')
        self.assertEqual(self.val(pl, 'Utilities'), 300.0)
        self.assertEqual(self.val(pl, 'Electric'), 80.0)
        self.assertEqual(self.val(pl, 'Total Utilities'), 380.0)
        self.assertEqual(self.drill_total(pl, 'Total Utilities'), 380.0)

    def test_fiscal_year_setting(self):
        self.put('/api/settings', {'fiscal_year_start_month': 7})
        bs = self.get('/api/reports/balance_sheet', end='2026-03-31')
        # FY started 2025-07-01: June 2025 had no P&L activity, so all net income is current-year
        self.assertEqual(self.val(bs, 'Net Income'), 7950.0)
        self.assertEqual(self.val(bs, 'Retained Earnings'), 0.0)


class Backups(ApiCase):
    def test_backup_and_restore(self):
        import base64
        from sqb import backup
        self.post('/api/customers', {'name': 'Before'})
        made = self.post('/api/backups')['file']
        self.post('/api/customers', {'name': 'After'})
        self.assertEqual(len(self.get('/api/customers')), 2)
        r = self.post('/api/backups/restore', {'file': made})
        self.assertEqual([c['name'] for c in self.get('/api/customers')], ['Before'])
        self.assertIn('before-restore', r['safety_backup'])        # current books were kept
        # restore from an uploaded copy
        data = backup.download_bytes(self.db_path)
        self.post('/api/customers', {'name': 'Later'})
        self.post('/api/backups/restore', {'content_b64': base64.b64encode(data).decode()})
        self.assertEqual([c['name'] for c in self.get('/api/customers')], ['Before'])
        # junk and path tricks are refused, books untouched
        self.post('/api/backups/restore', {'content_b64': base64.b64encode(b'not a db').decode()}, expect=400)
        self.post('/api/backups/restore', {'file': '../' + made}, expect=404)
        self.assertEqual(len(self.get('/api/customers')), 1)

    def test_auto_backup_once_a_day(self):
        from sqb import backup
        self.assertIsNotNone(backup.auto_backup(self.db_path))
        self.assertIsNone(backup.auto_backup(self.db_path))
        self.assertEqual(len([b for b in self.get('/api/backups') if b['file'].endswith('-auto.db')]), 1)

    def tearDown(self):
        import glob, os
        from sqb import backup
        for f in glob.glob(os.path.join(backup.backup_dir(self.db_path), os.path.basename(self.db_path)[:-3] + '-*.db')):
            os.remove(f)
        super().tearDown()


if __name__ == '__main__':
    unittest.main()


class EditCategory(ApiCase):
    def test_recategorize_after_reconcile(self):
        chk = self.acct('Checking')
        self.post('/api/feed/import', {'account_id': chk, 'content': 'Date,Description,Amount\n01/05/2026,STAPLES,-100.00\n'})
        row = self.get('/api/feed', account_id=chk)['rows'][0]
        t = self.post('/api/feed/%d/categorize' % row['id'], {'account_id': self.acct('Travel')})
        rec = self.post('/api/reconciliations', {'account_id': chk, 'statement_date': '2026-01-31', 'ending_balance': -100})
        self.post('/api/reconciliations/%d/finish' % rec['id'])
        v = self.put('/api/transactions/%d/categories' % t['id'], {'splits': [
            {'account_id': self.acct('Office Supplies and Software'), 'amount': 70},
            {'account_id': self.acct('Meals and Entertainment'), 'amount': 30}], 'memo': 'fixed'})
        self.assertEqual((v['memo'], v['reconciled'], len(v['lines'])), ('fixed', True, 3))
        pl = self.get('/api/reports/profit_loss', start='2026-01-01', end='2026-01-31')
        self.assertEqual(self.val(pl, 'Office Supplies and Software'), 70.0)
        with self.assertRaises(KeyError):
            self.row(pl, 'Travel')
        self.put('/api/transactions/%d/categories' % t['id'], {'splits': [
            {'account_id': self.acct('Travel'), 'amount': 99}]}, expect=400)          # must add up
        self.put('/api/transactions/%d/categories' % t['id'], {'date': '2026-02-01', 'splits': [
            {'account_id': self.acct('Travel'), 'amount': 100}]}, expect=409)         # reconciled date locked
        self.assertEqual(self.get('/api/feed', account_id=chk, status='posted')['rows'][0]['category'], 'Office Supplies and Software'
                         if False else '-Split-')
