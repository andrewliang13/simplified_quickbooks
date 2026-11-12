"""QA round 2: vendor bills, bill payments, A/P aging, bank-feed bill matching and edit-categories."""
import unittest

from helpers import ApiCase


class Base(ApiCase):
    def setUp(self):
        super().setUp()
        self.vend = self.post('/api/vendors', {'name': 'Supplier'})['id']
        self.vend2 = self.post('/api/vendors', {'name': 'Other Vendor'})['id']
        self.chk = self.acct('Checking')
        self.sav = self.acct('Savings')
        self.cc = self.acct('Credit Card')
        self.rm = self.acct('Repairs and Maintenance')
        self.office = self.acct('Office Supplies and Software')
        self.travel = self.acct('Travel')

    # ---- helpers
    def bill(self, amount, date='2026-01-10', vendor=None, expect=200, **kw):
        body = {'vendor_id': vendor or self.vend, 'date': date, 'terms': 'Net 30',
                'lines': [{'description': 'Parts', 'amount': amount, 'account_id': self.rm}]}
        body.update(kw)
        return self.post('/api/bills', body, expect=expect)

    def pay(self, amount, date='2026-02-01', vendor=None, acct=None, expect=200, **kw):
        body = {'vendor_id': vendor or self.vend, 'date': date, 'amount': amount,
                'pay_account_id': acct or self.chk}
        body.update(kw)
        return self.post('/api/bill_payments', body, expect=expect)

    def bs(self, label, end='2026-12-31', **q):
        r = self.get('/api/reports/balance_sheet', end=end, **q)
        try:
            return self.val(r, label)
        except KeyError:
            return 0.0

    def pl(self, label, start='2026-01-01', end='2026-12-31', **q):
        r = self.get('/api/reports/profit_loss', start=start, end=end, **q)
        try:
            return self.val(r, label)
        except KeyError:
            return 0.0

    def aging_total(self, end, label='TOTAL'):
        r = self.get('/api/reports/ap_aging', end=end)
        try:
            return self.val(r, label, 5)
        except KeyError:
            return 0.0

    def vbal(self, vid=None):
        return self.get('/api/vendors/%d' % (vid or self.vend))['balance']

    def balanced(self, end='2026-12-31', **q):
        r = self.get('/api/reports/balance_sheet', end=end, **q)
        self.assertEqual(self.val(r, 'TOTAL ASSETS'), self.val(r, 'TOTAL LIABILITIES AND EQUITY'))
        tb = self.get('/api/reports/trial_balance', end=end, **q)
        t = self.row(tb, 'TOTAL')['values']
        self.assertEqual(t[0], t[1])

    def tie(self, end):
        """Balance-sheet A/P == sum of vendor balances (as of today) == A/P aging total, as of `end`."""
        ap = self.bs('Accounts Payable', end=end)
        self.assertEqual(ap, self.aging_total(end), 'A/P vs aging as of %s' % end)
        return ap

    def feed(self, acct, rows):
        csv = 'Date,Description,Amount\n' + ''.join('%s,%s,%s\n' % r for r in rows)
        self.post('/api/feed/import', {'account_id': acct, 'content': csv})
        return self.get('/api/feed', account_id=acct)['rows']

    def txn(self, tid):
        return self.get('/api/transactions/%d' % tid)


# ======================================================================== ledger postings
class Postings(Base):
    def test_bill_and_payment_postings(self):
        b = self.bill(500, number='A1')
        t = self.txn(b['txn_id'])
        ap = [l for l in t['lines'] if l['account'] == 'Accounts Payable']
        ex = [l for l in t['lines'] if l['account'] == 'Repairs and Maintenance']
        self.assertEqual((ap[0]['credit'], ap[0]['name_id']), (500.0, self.vend))
        self.assertEqual(ex[0]['debit'], 500.0)
        p = self.pay(200, applications=[{'bill_id': b['id'], 'amount': 200}])
        t = self.txn(p['txn_id'])
        self.assertEqual(t['type'], 'Bill Payment')
        d = {l['account']: (l['debit'], l['credit']) for l in t['lines']}
        self.assertEqual(d['Accounts Payable'], (200.0, None if d['Accounts Payable'][1] is None else 0.0))
        self.assertEqual(d['Checking'][1], 200.0)
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['status'], 'Overdue')
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['balance'], 300.0)
        self.balanced()

    def test_pay_by_card(self):
        b = self.bill(80)
        self.pay(80, acct=self.cc)
        self.assertEqual(self.bs('Credit Card'), 80.0)
        self.assertEqual(self.bs('Accounts Payable'), 0.0)
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['status'], 'Paid')
        self.balanced()

    def test_pay_from_non_bank_rejected(self):
        self.bill(80)
        for a in ('Accounts Payable', 'Undeposited Funds', 'Travel', 'Accounts Receivable'):
            self.pay(10, acct=self.acct(a), expect=400)
        self.pay(10, acct=None, expect=400) if False else None
        self.post('/api/bill_payments', {'vendor_id': self.vend, 'amount': 10}, expect=400)

    def test_bill_line_to_ap_or_ar_rejected(self):
        for a in ('Accounts Payable', 'Accounts Receivable'):
            self.bill(10, lines=[{'amount': 10, 'account_id': self.acct(a)}], expect=400)


# ======================================================================== payments / application
class Payments(Base):
    def test_one_payment_several_bills_and_over_application(self):
        b1 = self.bill(100, number='1')
        b2 = self.bill(200, number='2', date='2026-01-15')
        b3 = self.bill(50, number='3', vendor=self.vend2)
        # over-apply on one bill
        self.pay(150, applications=[{'bill_id': b1['id'], 'amount': 150}], expect=400)
        # applied more than payment amount
        self.pay(100, applications=[{'bill_id': b1['id'], 'amount': 60}, {'bill_id': b2['id'], 'amount': 60}],
                 expect=400)
        # duplicate entries for the same bill that together exceed its balance
        self.pay(200, applications=[{'bill_id': b1['id'], 'amount': 60}, {'bill_id': b1['id'], 'amount': 60}],
                 expect=400)
        # another vendor's bill
        self.pay(50, applications=[{'bill_id': b3['id'], 'amount': 50}], expect=400)
        # negative application
        self.pay(50, applications=[{'bill_id': b1['id'], 'amount': -10}], expect=400)
        p = self.pay(250, applications=[{'bill_id': b1['id'], 'amount': 100}, {'bill_id': b2['id'], 'amount': 150}])
        self.assertEqual((p['applied'], p['unapplied']), (250.0, 0.0))
        self.assertEqual(self.get('/api/bills/%d' % b1['id'])['status'], 'Paid')
        self.assertEqual(self.get('/api/bills/%d' % b2['id'])['balance'], 50.0)
        self.assertEqual(self.vbal(), 50.0)
        self.assertEqual(self.tie('2026-12-31'), 100.0)   # 50 Supplier + 50 Other Vendor
        self.balanced()

    def test_unapplied_payment_is_vendor_credit(self):
        self.pay(300, date='2026-01-05')
        self.assertEqual(self.vbal(), -300.0)
        self.assertEqual(self.tie('2026-01-31'), -300.0)
        b = self.bill(200, date='2026-02-10')
        # A new bill does not consume the credit automatically; aging still nets.
        self.assertEqual(self.tie('2026-02-28'), -100.0)
        self.assertEqual(self.vbal(), -100.0)
        p = self.get('/api/bill_payments')[0]
        self.put('/api/bill_payments/%d' % p['id'], {'vendor_id': self.vend, 'date': '2026-01-05', 'amount': 300,
                                                     'pay_account_id': self.chk,
                                                     'applications': [{'bill_id': b['id'], 'amount': 200}]})
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['status'], 'Paid')
        # as of a date before the bill, the payment is still a prepayment
        self.assertEqual(self.tie('2026-01-31'), -300.0)
        self.assertEqual(self.tie('2026-02-28'), -100.0)
        self.assertEqual(self.vbal(), -100.0)
        self.balanced()
        self.balanced(basis='cash')

    def test_ap_ties_at_many_dates(self):
        b1 = self.bill(100, date='2026-01-10', number='X1')
        b2 = self.bill(250, date='2026-02-10', number='X2')
        self.bill(70, date='2026-03-01', vendor=self.vend2)
        self.pay(120, date='2026-02-15')                     # b1 100 + b2 20
        self.pay(30, date='2026-03-05', applications=[{'bill_id': b2['id'], 'amount': 30}])
        self.pay(500, date='2026-03-20', vendor=self.vend2)    # 70 + 430 unapplied
        self.journal('2026-03-25', ('Accounts Payable', 0, 40, self.vend), ('Travel', 40, 0))
        expected = {'2026-01-09': 0.0, '2026-01-10': 100.0, '2026-02-10': 350.0, '2026-02-15': 230.0,
                    '2026-03-01': 300.0, '2026-03-05': 270.0, '2026-03-20': -230.0, '2026-03-25': -190.0}
        for d, v in expected.items():
            self.assertEqual(self.tie(d), v, d)
        self.assertEqual(self.vbal(), 240.0)
        self.assertEqual(self.vbal(self.vend2), -430.0)
        self.assertEqual(self.get('/api/bills/%d' % b2['id'])['balance'], 200.0)
        self.assertEqual(self.get('/api/bills/%d' % b1['id'])['status'], 'Paid')
        self.balanced()

    def test_aging_buckets(self):
        self.bill(10, date='2026-01-01', number='a')      # due Jan 31
        self.bill(20, date='2026-03-01', number='b')      # due Mar 31
        self.bill(40, date='2026-04-20', number='c', terms='Due on receipt')
        r = self.get('/api/reports/ap_aging', end='2026-04-20')
        v = self.row(r, 'Supplier')['values']
        # Jan 31 -> 79 days (61-90); Mar 31 -> 20 days (1-30); Apr 20 -> current
        self.assertEqual(v, [40.0, 20.0, None if v[2] is None else 0.0, 10.0, v[4], 70.0])
        self.assertEqual(self.drill_total(r, 'Supplier', 5), 70.0)

    def test_payment_edit_and_delete(self):
        b1 = self.bill(100, number='1')
        b2 = self.bill(100, number='2')
        p = self.pay(150)
        # edit to a smaller amount than currently applied -> refused rather than silently breaking
        self.put('/api/bill_payments/%d' % p['id'], {'vendor_id': self.vend, 'amount': 50,
                                                     'pay_account_id': self.chk, 'date': '2026-02-01'}, expect=400)
        # explicit re-application
        self.put('/api/bill_payments/%d' % p['id'], {'vendor_id': self.vend, 'amount': 50, 'pay_account_id': self.chk,
                                                     'date': '2026-02-01',
                                                     'applications': [{'bill_id': b2['id'], 'amount': 50}]})
        self.assertEqual(self.get('/api/bills/%d' % b1['id'])['balance'], 100.0)
        self.assertEqual(self.get('/api/bills/%d' % b2['id'])['balance'], 50.0)
        self.assertEqual(self.tie('2026-12-31'), 150.0)
        # move the payment to another vendor (no bills) -> unapplied credit for them
        self.put('/api/bill_payments/%d' % p['id'], {'vendor_id': self.vend2, 'amount': 50, 'pay_account_id': self.chk,
                                                     'date': '2026-02-01'})
        self.assertEqual((self.vbal(), self.vbal(self.vend2)), (200.0, -50.0))
        self.assertEqual(self.tie('2026-12-31'), 150.0)
        self.delete('/api/bill_payments/%d' % p['id'])
        self.assertEqual(self.tie('2026-12-31'), 200.0)
        self.delete('/api/bills/%d' % b1['id'])
        self.delete('/api/bills/%d' % b1['id'], expect=404)
        self.delete('/api/bill_payments/%d' % p['id'], expect=404)
        self.balanced()

    def test_failed_payment_edit_keeps_applications(self):
        b = self.bill(100)
        p = self.pay(100)
        self.put('/api/bill_payments/%d' % p['id'], {'vendor_id': self.vend, 'amount': 100, 'pay_account_id': self.chk,
                                                     'applications': [{'bill_id': 999, 'amount': 100}]}, expect=400)
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['status'], 'Paid')

    def test_reconciled_payment_locked(self):
        self.bill(100)
        p = self.pay(100, date='2026-01-20')
        line = [l for l in self.txn(p['txn_id'])['lines'] if l['account'] == 'Checking'][0]
        rec = self.post('/api/reconciliations', {'account_id': self.chk, 'statement_date': '2026-01-31',
                                                 'ending_balance': -100})
        self.post('/api/reconciliations/%d/toggle' % rec['id'], {'line_ids': [line['id']], 'cleared': True})
        self.post('/api/reconciliations/%d/finish' % rec['id'])
        self.delete('/api/bill_payments/%d' % p['id'], expect=409)
        self.put('/api/bill_payments/%d' % p['id'], {'vendor_id': self.vend, 'amount': 90,
                                                     'pay_account_id': self.chk, 'date': '2026-01-20',
                                                     'applications': [{'bill_id': p['applications'][0]['bill_id'],
                                                                       'amount': 90}]}, expect=409)
        self.assertEqual(self.get('/api/bill_payments/%d' % p['id'])['amount'], 100.0)


# ======================================================================== bills: numbers, dates, edits
class BillDocs(Base):
    def test_number_unique_per_vendor(self):
        self.bill(10, number='INV-7')
        self.bill(10, number='INV-7', vendor=self.vend2)          # other vendor ok
        self.bill(10, number='INV-7', expect=409)
        self.bill(10, number=' INV-7 ', expect=409)               # whitespace trimmed
        self.bill(10)
        self.bill(10)                                              # blank numbers may repeat
        b = self.bill(10, number='INV-8')
        # editing to a number already used by this vendor
        self.put('/api/bills/%d' % b['id'], {'vendor_id': self.vend, 'date': '2026-01-10', 'number': 'INV-7',
                                             'lines': [{'amount': 10, 'account_id': self.rm}]}, expect=409)
        # moving it to the other vendor that already has INV-7... with its own number INV-7
        self.put('/api/bills/%d' % b['id'], {'vendor_id': self.vend2, 'date': '2026-01-10', 'number': 'INV-7',
                                             'lines': [{'amount': 10, 'account_id': self.rm}]}, expect=409)
        # number kept when omitted on edit
        r = self.put('/api/bills/%d' % b['id'], {'vendor_id': self.vend, 'date': '2026-01-10',
                                                 'lines': [{'amount': 12, 'account_id': self.rm}]})
        self.assertEqual(r['number'], 'INV-8')

    def test_due_dates_and_terms(self):
        self.assertEqual(self.bill(10, terms='Net 15')['due_date'], '2026-01-25')
        self.assertEqual(self.bill(10, terms='Due on receipt')['due_date'], '2026-01-10')
        self.assertEqual(self.bill(10, terms='Net 45')['due_date'], '2026-02-24')
        self.assertEqual(self.bill(10, terms='')['due_date'], '2026-02-09')
        self.assertEqual(self.bill(10, terms='Weird', due_date='2026-03-01')['due_date'], '2026-03-01')
        self.bill(10, terms='Weird', expect=400)
        self.bill(10, due_date='2026-01-01', expect=400)

    def test_edit_bill_with_payments(self):
        b = self.bill(100)
        self.pay(60)
        body = {'vendor_id': self.vend, 'date': '2026-01-10', 'terms': 'Net 30',
                'lines': [{'amount': 50, 'account_id': self.rm}]}
        self.put('/api/bills/%d' % b['id'], body, expect=400)              # below paid
        body['vendor_id'] = self.vend2
        body['lines'][0]['amount'] = 100
        self.put('/api/bills/%d' % b['id'], body, expect=400)              # vendor change after payment
        body['vendor_id'] = self.vend
        body['lines'] = [{'amount': 70, 'account_id': self.office}, {'amount': 50, 'account_id': self.travel}]
        r = self.put('/api/bills/%d' % b['id'], body)
        self.assertEqual((r['total'], r['balance']), (120.0, 60.0))
        self.assertEqual((self.pl('Repairs and Maintenance'), self.pl('Office Supplies and Software'),
                          self.pl('Travel')), (0.0, 70.0, 50.0))
        self.assertEqual(self.tie('2026-12-31'), 60.0)
        self.delete('/api/bills/%d' % b['id'], expect=409)
        self.balanced()

    def test_bill_date_moved_after_payment(self):
        b = self.bill(100, date='2026-01-10')
        self.pay(100, date='2026-01-20')
        self.put('/api/bills/%d' % b['id'], {'vendor_id': self.vend, 'date': '2026-02-05',
                                             'lines': [{'amount': 100, 'account_id': self.rm}]})
        for d, v in (('2026-01-15', 0.0), ('2026-01-25', -100.0), ('2026-02-05', 0.0)):
            self.assertEqual(self.tie(d), v, d)

    def test_statuses_future_due(self):
        b = self.bill(100, date='2099-01-01')
        self.assertEqual(b['status'], 'Open')
        self.pay(30, date='2099-01-05')
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['status'], 'Partial')
        self.pay(70, date='2099-01-06')
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['status'], 'Paid')
        self.assertEqual([x['status'] for x in self.get('/api/bills', status='partial')], [])

    def test_list_filters(self):
        b1 = self.bill(100, number='1')
        self.bill(100, number='2', vendor=self.vend2)
        self.pay(100, applications=[{'bill_id': b1['id'], 'amount': 100}])
        self.assertEqual([b['number'] for b in self.get('/api/bills', status='paid')], ['1'])
        self.assertEqual([b['number'] for b in self.get('/api/bills', status='open')], ['2'])
        self.assertEqual([b['number'] for b in self.get('/api/bills', vendor_id=self.vend2)], ['2'])
        self.assertEqual(len(self.get('/api/bill_payments', vendor_id=self.vend)), 1)


# ======================================================================== P&L and cash basis
class Reports(Base):
    def test_pl_by_period_accrual_and_cash(self):
        b1 = self.bill(100, date='2026-01-10', number='1')
        self.bill(40, date='2026-02-10', number='2', lines=[{'amount': 40, 'account_id': self.office}])
        self.pay(60, date='2026-02-20', applications=[{'bill_id': b1['id'], 'amount': 60}])
        self.pay(40, date='2026-03-05', applications=[{'bill_id': b1['id'], 'amount': 40}])
        jan = dict(start='2026-01-01', end='2026-01-31')
        feb = dict(start='2026-02-01', end='2026-02-28')
        mar = dict(start='2026-03-01', end='2026-03-31')
        self.assertEqual(self.pl('Repairs and Maintenance', **jan), 100.0)
        self.assertEqual(self.pl('Repairs and Maintenance', **feb), 0.0)
        self.assertEqual(self.pl('Office Supplies and Software', **feb), 40.0)
        self.assertEqual(self.pl('Repairs and Maintenance', basis='cash', **jan), 0.0)
        self.assertEqual(self.pl('Repairs and Maintenance', basis='cash', **feb), 60.0)
        self.assertEqual(self.pl('Repairs and Maintenance', basis='cash', **mar), 40.0)
        self.assertEqual(self.pl('Office Supplies and Software', basis='cash'), 0.0)
        self.assertEqual(self.bs('Accounts Payable', basis='cash'), 0.0)
        self.assertEqual(self.bs('Accounts Payable'), 40.0)
        for d in ('2026-01-31', '2026-02-28', '2026-12-31'):
            self.balanced(end=d)
            self.balanced(end=d, basis='cash')

    def test_pl_month_columns(self):
        b = self.bill(100, date='2026-01-10')
        self.pay(100, date='2026-02-10')
        for basis, want in (('accrual', [100.0, 0.0]), ('cash', [0.0, 100.0])):
            r = self.get('/api/reports/profit_loss', start='2026-01-01', end='2026-02-28', columns='month',
                         basis=basis)
            v = [x or 0.0 for x in self.row(r, 'Repairs and Maintenance')['values'][:2]]
            self.assertEqual(v, want, basis)
            self.assertEqual(self.drill_total(r, 'Repairs and Maintenance', 1 if basis == 'cash' else 0), 100.0)
        self.assertEqual(b['balance'], 100.0)

    def test_foreign_currency_bill(self):
        ev = self.post('/api/vendors', {'name': 'Euro', 'currency': 'EUR'})['id']
        b = self.bill(100, vendor=ev, exchange_rate=1.1)
        self.assertEqual((b['total_home'], b['currency']), (110.0, 'EUR'))
        self.bill(100, vendor=ev, expect=400)                              # rate required
        self.pay(40, vendor=ev, exchange_rate=1.2)
        self.assertEqual((self.tie('2026-12-31'), self.vbal(ev), self.pl('Exchange Gain or Loss')), (66.0, 66.0, 4.0))
        p2 = self.pay(80, vendor=ev, exchange_rate=1.3, date='2026-03-01')
        self.assertEqual((p2['applied'], p2['unapplied']), (60.0, 20.0))
        self.assertEqual((self.tie('2026-12-31'), self.vbal(ev), self.pl('Exchange Gain or Loss')),
                         (-26.0, -26.0, 16.0))
        self.balanced()
        self.balanced(basis='cash')
        rows = self.feed(self.chk, [('01/20/2026', 'EURO', '-66.00')])
        self.assertEqual(rows[0]['matches'], [])
        self.post('/api/feed/%d/match' % rows[0]['id'], {'bill_id': b['id']}, expect=400)

    def test_cash_basis_split_bill_rounding(self):
        b = self.bill(0, lines=[{'amount': 33.33, 'account_id': self.rm}, {'amount': 33.33, 'account_id': self.office},
                                {'amount': 33.34, 'account_id': self.travel}])
        self.pay(10, applications=[{'bill_id': b['id'], 'amount': 10}])
        tot = sum(self.pl(a, basis='cash') for a in
                  ('Repairs and Maintenance', 'Office Supplies and Software', 'Travel'))
        self.assertAlmostEqual(tot, 10.0, places=2)
        self.balanced(basis='cash')

    def test_cash_basis_card_paid_bill(self):
        self.bill(100)
        self.pay(100, date='2026-03-01', acct=self.cc)
        self.assertEqual(self.pl('Repairs and Maintenance', basis='cash', start='2026-03-01', end='2026-03-31'), 100.0)
        self.balanced(basis='cash')


# ======================================================================== feed: match bills
class FeedBills(Base):
    def test_match_partial_then_undo(self):
        b = self.bill(100, date='2026-01-10')
        rows = self.feed(self.chk, [('01/20/2026', 'SUPPLIER', '-40.00'), ('01/25/2026', 'SUPPLIER', '-60.00')])
        # suggestions only show exact open-balance matches
        self.assertEqual([m for r in rows for m in r['matches'] if m['kind'] == 'bill'], [])
        r40 = [r for r in rows if r['amount'] == -40][0]
        r60 = [r for r in rows if r['amount'] == -60][0]
        self.post('/api/feed/%d/match' % r40['id'], {'bill_id': b['id']})
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['balance'], 60.0)
        # now -60 is suggested
        r60 = [r for r in self.get('/api/feed', account_id=self.chk)['rows'] if r['id'] == r60['id']][0]
        self.assertEqual(len([m for m in r60['matches'] if m['kind'] == 'bill']), 1)
        self.post('/api/feed/%d/match' % r60['id'], {'bill_id': b['id']})
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['status'], 'Paid')
        self.assertEqual(self.tie('2026-01-22'), 60.0)
        self.assertEqual(self.tie('2026-01-31'), 0.0)
        self.post('/api/feed/%d/undo' % r40['id'])
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['balance'], 40.0)
        self.assertEqual(self.bs('Checking'), -60.0)
        self.assertEqual(len(self.get('/api/bill_payments')), 1)
        self.balanced()

    def test_match_errors(self):
        b = self.bill(50)
        rows = self.feed(self.chk, [('01/20/2026', 'SUPPLIER', '-80.00'), ('01/21/2026', 'REFUND', '50.00')])
        big = [r for r in rows if r['amount'] == -80][0]
        dep = [r for r in rows if r['amount'] == 50][0]
        self.post('/api/feed/%d/match' % big['id'], {'bill_id': b['id']}, expect=400)
        self.post('/api/feed/%d/match' % dep['id'], {'bill_id': b['id']}, expect=400)
        self.post('/api/feed/%d/match' % big['id'], {'bill_id': 99999}, expect=404)
        self.post('/api/feed/%d/match' % big['id'], {'bill_id': 'abc'}, expect=400)
        self.post('/api/feed/%d/match' % big['id'], {'bill_id': [1]}, expect=400)
        self.post('/api/feed/99999/match', {'bill_id': b['id']}, expect=404)
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['balance'], 50.0)

    def test_card_charge_matched_to_bill(self):
        b = self.bill(25)
        rows = self.feed(self.cc, [('01/20/2026', 'SUPPLIER', '-25.00')])
        self.assertEqual(len([m for m in rows[0]['matches'] if m['kind'] == 'bill']), 1)
        t = self.post('/api/feed/%d/match' % rows[0]['id'], {'bill_id': b['id']})
        self.assertEqual(t['type'], 'Bill Payment')
        self.assertEqual(self.bs('Credit Card'), 25.0)
        self.balanced()

    def test_matched_bill_payment_edited_elsewhere(self):
        b = self.bill(100)
        rows = self.feed(self.chk, [('01/20/2026', 'SUPPLIER', '-100.00')])
        t = self.post('/api/feed/%d/match' % rows[0]['id'], {'bill_id': b['id']})
        pid = t['bill_payment_id']
        # change the amount -> the statement line goes back to review
        self.put('/api/bill_payments/%d' % pid, {'vendor_id': self.vend, 'amount': 90, 'pay_account_id': self.chk,
                                                 'date': '2026-01-20',
                                                 'applications': [{'bill_id': b['id'], 'amount': 90}]})
        f = self.get('/api/feed', account_id=self.chk)
        self.assertEqual(f['counts']['review'], 1)
        # deleting the payment from the feed perspective
        self.delete('/api/bill_payments/%d' % pid)
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['balance'], 100.0)

    def test_feed_categorize_to_accounts_payable_refused(self):
        """edit-categories refuses A/P ('Use ... Pay Bills'), and QBO does not let a bank-feed line be
        categorized to A/P. The feed's categorize only blocks A/R, so a withdrawal posts straight to A/P as an
        'Expense' that is not a bill payment and can never be applied to the vendor's open bills."""
        b = self.bill(25)
        rows = self.feed(self.chk, [('01/21/2026', 'SUPPLIER', '-25.00')])
        st, d = self.app.handle('POST', '/api/feed/%d/categorize' % rows[0]['id'], {},
                                {'account_id': self.acct('Accounts Payable'), 'name_id': self.vend})
        self.assertEqual(st, 400, 'categorized to A/P as %s; bill still open %.2f, vendor balance %.2f'
                         % (d.get('type'), self.get('/api/bills/%d' % b['id'])['balance'], self.vbal()))

    def test_bill_payment_not_in_category_history(self):
        b = self.bill(100)
        rows = self.feed(self.chk, [('01/20/2026', 'SUPPLIER INC', '-100.00')])
        self.post('/api/feed/%d/match' % rows[0]['id'], {'bill_id': b['id']})
        rows = self.feed(self.chk, [('02/20/2026', 'SUPPLIER INC', '-30.00')])
        self.assertIsNone(rows[0]['suggestion'])


# ======================================================================== edit categories
class EditCategories(Base):
    def expense(self, acct=None, amount='-100.00', desc='OFFICE DEPOT #1', date='01/15/2026', cat=None):
        acct = acct or self.chk
        rows = self.feed(acct, [(date, desc, amount)])
        row = [r for r in rows if r['description'] == desc and r['date'] == '2026-%s-%s' % (date[:2], date[3:5])][0]
        return self.post('/api/feed/%d/categorize' % row['id'], {'account_id': cat or self.office}), row

    def test_change_category_and_split(self):
        t, row = self.expense()
        r = self.put('/api/transactions/%d/categories' % t['id'],
                     {'splits': [{'account_id': self.travel, 'amount': 70}, {'account_id': self.rm, 'amount': 30}],
                      'name_id': self.vend, 'memo': 'fixed', 'date': '2026-01-16'})
        self.assertEqual((r['date'], r['memo'], r['name_id'], r['type']), ('2026-01-16', 'fixed', self.vend, 'Expense'))
        self.assertEqual((self.pl('Office Supplies and Software'), self.pl('Travel'),
                          self.pl('Repairs and Maintenance')), (0.0, 70.0, 30.0))
        self.assertEqual(self.bs('Checking'), -100.0)
        posted = self.get('/api/feed', account_id=self.chk, status='posted')['rows'][0]
        self.assertEqual((posted['category'], posted['txn_id']), ('-Split-', t['id']))
        self.balanced()

    def test_validation(self):
        t, _ = self.expense()
        u = '/api/transactions/%d/categories' % t['id']
        self.put(u, {'splits': [{'account_id': self.travel, 'amount': 99}]}, expect=400)
        self.put(u, {'splits': []}, expect=400)
        self.put(u, {'splits': 'x'}, expect=400)
        self.put(u, {}, expect=400)
        self.put(u, {'splits': [{'account_id': self.chk, 'amount': 100}]}, expect=400)
        self.put(u, {'splits': [{'account_id': self.acct('Accounts Payable'), 'amount': 100}]}, expect=400)
        self.put(u, {'splits': [{'account_id': 99999, 'amount': 100}]}, expect=400)
        self.put(u, {'splits': [{'account_id': 'x', 'amount': 100}]}, expect=400)
        self.put(u, {'splits': [{'account_id': self.travel, 'amount': 1e30}]}, expect=400)
        self.put(u, {'splits': [{'account_id': self.travel, 'amount': 100}], 'name_id': 99999}, expect=400)
        self.put(u, {'splits': [{'account_id': self.travel, 'amount': 100}], 'date': '2026-13-01'}, expect=400)
        self.put(u, {'splits': [{'account_id': self.travel, 'amount': 100}], 'memo': {}}, expect=400)
        self.put('/api/transactions/99999/categories', {'splits': [{'account_id': self.travel, 'amount': 100}]},
                 expect=404)
        self.assertEqual(self.pl('Office Supplies and Software'), 100.0)

    def test_not_for_bills_or_bill_payments_or_journals(self):
        b = self.bill(100)
        p = self.pay(100)
        j = self.journal('2026-01-05', ('Travel', 10, 0), ('Checking', 0, 10))
        for tid in (b['txn_id'], p['txn_id'], j['id']):
            self.put('/api/transactions/%d/categories' % tid,
                     {'splits': [{'account_id': self.travel, 'amount': 100}]}, expect=400)

    def test_reconciled_expense_category_can_change(self):
        t, row = self.expense()
        rec = self.post('/api/reconciliations', {'account_id': self.chk, 'statement_date': '2026-01-31',
                                                 'ending_balance': -100})
        self.post('/api/reconciliations/%d/finish' % rec['id'])
        self.assertTrue(self.txn(t['id'])['reconciled'])
        u = '/api/transactions/%d/categories' % t['id']
        self.put(u, {'splits': [{'account_id': self.travel, 'amount': 100}], 'date': '2026-01-20'}, expect=409)
        self.put(u, {'splits': [{'account_id': self.travel, 'amount': 100}], 'memo': 'm'})
        self.assertEqual(self.pl('Travel'), 100.0)
        self.assertTrue(self.txn(t['id'])['reconciled'])
        self.assertEqual(self.get('/api/reconciliations/%d' % rec['id'])['difference'], 0.0)
        self.balanced()

    def test_transfer_other_side_reconciled(self):
        t, _ = self.expense(cat=self.sav, amount='-200.00', desc='XFER TO SAV')
        self.assertEqual(t['type'], 'Transfer')
        sav_line = [l for l in t['lines'] if l['account_id'] == self.sav][0]
        rec = self.post('/api/reconciliations', {'account_id': self.sav, 'statement_date': '2026-01-31',
                                                 'ending_balance': 200})
        self.post('/api/reconciliations/%d/toggle' % rec['id'], {'line_ids': [sav_line['id']], 'cleared': True})
        self.post('/api/reconciliations/%d/finish' % rec['id'])
        self.put('/api/transactions/%d/categories' % t['id'],
                 {'splits': [{'account_id': self.travel, 'amount': 200}]}, expect=409)
        self.assertEqual(self.bs('Savings'), 200.0)

    def test_cleared_date_moved_past_statement_uncleared(self):
        t, _ = self.expense()
        rec = self.post('/api/reconciliations', {'account_id': self.chk, 'statement_date': '2026-01-31',
                                                 'ending_balance': -100})
        self.assertEqual(rec['difference'], 0.0)
        self.put('/api/transactions/%d/categories' % t['id'],
                 {'splits': [{'account_id': self.travel, 'amount': 100}], 'date': '2026-02-10'})
        self.assertEqual(self.get('/api/reconciliations/%d' % rec['id'])['difference'], -100.0)

    def test_history_follows_edit(self):
        t, _ = self.expense(desc='COFFEE SHOP 1')
        self.put('/api/transactions/%d/categories' % t['id'],
                 {'splits': [{'account_id': self.travel, 'amount': 100}]})
        rows = self.feed(self.chk, [('02/01/2026', 'COFFEE SHOP 2', '-12.00')])
        self.assertEqual(rows[0]['suggestion']['account_id'], self.travel)

    def test_card_payment_matched_on_card_feed_then_edited(self):
        """Bank -500 categorized to the credit card; the card statement's +500 payment is matched to that entry.
        Changing the category of the bank-side entry removes the card line; the card's statement line must not
        stay 'posted' pointing at a line that no longer exists."""
        t, _ = self.expense(cat=self.cc, amount='-500.00', desc='CARD PAYMENT')
        self.assertEqual(t['type'], 'Credit Card Payment')
        rows = self.feed(self.cc, [('01/16/2026', 'PAYMENT THANK YOU', '500.00')])
        m = [x for x in rows[0]['matches'] if x['kind'] == 'transaction']
        self.assertEqual(len(m), 1)
        self.post('/api/feed/%d/match' % rows[0]['id'], {'line_id': m[0]['line_id']})
        # The edit is refused while the card side is on the card's statement (decision: refuse, not unlink).
        self.put('/api/transactions/%d/categories' % t['id'],
                 {'splits': [{'account_id': self.office, 'amount': 500}]}, expect=409)
        cc_feed = self.get('/api/feed', account_id=self.cc, status='posted')
        self.assertEqual(self.bs('Credit Card'), -500.0)
        self.assertEqual(cc_feed['counts']['posted'], 1)
        self.assertEqual(cc_feed['rows'][0]['txn_type'], 'Credit Card Payment')

    def test_transfer_matched_on_other_bank_then_edited(self):
        t, _ = self.expense(cat=self.sav, amount='-200.00', desc='XFER TO SAV')
        rows = self.feed(self.sav, [('01/15/2026', 'XFER FROM CHK', '200.00')])
        m = [x for x in rows[0]['matches'] if x['kind'] == 'transaction']
        self.post('/api/feed/%d/match' % rows[0]['id'], {'line_id': m[0]['line_id']})
        self.put('/api/transactions/%d/categories' % t['id'],
                 {'splits': [{'account_id': self.travel, 'amount': 200}]}, expect=409)
        f = self.get('/api/feed', account_id=self.sav, status='posted')
        self.assertEqual(self.bs('Savings'), 200.0)
        self.assertEqual(f['counts']['posted'], 1)

    def test_negative_split_line_stored_as_credit(self):
        t, _ = self.expense()
        r = self.put('/api/transactions/%d/categories' % t['id'],
                     {'splits': [{'account_id': self.travel, 'amount': 150}, {'account_id': self.rm, 'amount': -50}]})
        rm = [l for l in r['lines'] if l['account_id'] == self.rm][0]
        self.assertEqual(self.pl('Repairs and Maintenance'), -50.0)
        # Every other posting path stores a negative debit as a credit (ledger._clean).
        self.assertTrue((rm['debit'] or 0) >= 0 and rm['credit'] == 50.0, rm)
        self.assertEqual(r['total'], 150.0)


# ======================================================================== malformed input
class Malformed(Base):
    def test_bills_bad_input(self):
        bad = [
            {'vendor_id': 'x'}, {'vendor_id': [1]}, {'vendor_id': 1e300}, {'vendor_id': 10 ** 30},
            {'vendor_id': self.vend2 + 99},
            {'vendor_id': self.vend, 'lines': 'abc'}, {'vendor_id': self.vend, 'lines': [1, 2]},
            {'vendor_id': self.vend, 'lines': []},
            {'vendor_id': self.vend, 'lines': [{'amount': 'abc', 'account_id': self.rm}]},
            {'vendor_id': self.vend, 'lines': [{'amount': 1e300, 'account_id': self.rm}]},
            {'vendor_id': self.vend, 'lines': [{'amount': 'NaN', 'account_id': self.rm}]},
            {'vendor_id': self.vend, 'lines': [{'amount': 'Infinity', 'account_id': self.rm}]},
            {'vendor_id': self.vend, 'lines': [{'qty': '1e999999', 'rate': 1, 'account_id': self.rm}]},
            {'vendor_id': self.vend, 'lines': [{'qty': 1e9, 'rate': 1e9, 'account_id': self.rm}]},
            {'vendor_id': self.vend, 'lines': [{'amount': 5, 'account_id': 'x'}]},
            {'vendor_id': self.vend, 'lines': [{'amount': 5, 'account_id': 99999}]},
            {'vendor_id': self.vend, 'lines': [{'amount': 5, 'account_id': self.rm, 'item_id': 'x'}]},
            {'vendor_id': self.vend, 'lines': [{'amount': 5, 'account_id': self.rm, 'item_id': 99999}]},
            {'vendor_id': self.vend, 'lines': [{'amount': 5, 'account_id': self.rm}], 'date': 20260101},
            {'vendor_id': self.vend, 'lines': [{'amount': 5, 'account_id': self.rm}], 'date': '2026-02-30'},
            {'vendor_id': self.vend, 'lines': [{'amount': 5, 'account_id': self.rm}], 'due_date': ['x']},
            {'vendor_id': self.vend, 'lines': [{'amount': 5, 'account_id': self.rm}], 'terms': {'a': 1}},
            {'vendor_id': self.vend, 'lines': [{'amount': 5, 'account_id': self.rm}], 'number': [1]},
            {'vendor_id': self.vend, 'lines': [{'amount': 5, 'account_id': self.rm}], 'memo': {}},
            {'vendor_id': self.vend, 'lines': [{'amount': 5, 'account_id': self.rm}], 'terms': 'Net 9999'},
            {'vendor_id': self.vend, 'lines': [{'amount': -5, 'account_id': self.rm}]},
            {'vendor_id': self.vend, 'lines': [{'amount': 5, 'account_id': self.rm, 'description': ['x']}]},
            {'vendor_id': self.vend, 'lines': [{'amount': 5, 'rate': 2, 'qty': 1, 'account_id': self.rm}]},
        ]
        for body in bad:
            status, data = self.app.handle('POST', '/api/bills', {}, body)
            self.assertIn(status, (400, 404, 409), '%s -> %s %s' % (body, status, data))
        self.assertEqual(self.get('/api/bills'), [])

    def test_many_huge_lines_total(self):
        # each line under the per-line ceiling, but the sum is enormous
        lines = [{'amount': '99999999999.99', 'account_id': self.rm}] * 200
        status, data = self.app.handle('POST', '/api/bills', {}, {'vendor_id': self.vend, 'date': '2026-01-10',
                                                                  'lines': lines})
        self.assertNotEqual(status, 500, data)

    def test_payments_bad_input(self):
        b = self.bill(10)
        base = {'vendor_id': self.vend, 'amount': 5, 'pay_account_id': self.chk}
        bad = [
            dict(base, amount='x'), dict(base, amount=0), dict(base, amount=-5), dict(base, amount=1e300),
            dict(base, amount=[5]), dict(base, pay_account_id='x'), dict(base, pay_account_id=99999),
            dict(base, pay_account_id=10 ** 40), dict(base, applications='x'), dict(base, applications=[1]),
            dict(base, applications=[{}]), dict(base, applications=[{'bill_id': b['id'], 'amount': 'x'}]),
            dict(base, applications=[{'bill_id': 1e300, 'amount': 1}]),
            dict(base, applications=[{'bill_id': b['id'] + 100, 'amount': 1}]),
            dict(base, date='yesterday'), dict(base, ref=['a']), dict(base, method={}),
            dict(base, vendor_id=None), dict(base, vendor_id='abc'),
        ]
        for body in bad:
            status, data = self.app.handle('POST', '/api/bill_payments', {}, body)
            self.assertIn(status, (400, 404, 409), '%s -> %s %s' % (body, status, data))
        for path in ('/api/bills/abc', '/api/bills/99999999999999999999', '/api/bill_payments/0'):
            status, data = self.app.handle('GET', path, {}, None)
            self.assertIn(status, (404,), '%s -> %s %s' % (path, status, data))
        for q in ({'end': 'x'}, {'end': '2026-02-31'}, {'as_of': '99999-01-01'}):
            status, data = self.app.handle('GET', '/api/reports/ap_aging', q, None)
            self.assertEqual(status, 400, '%s -> %s' % (q, data))
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['balance'], 10.0)

    def test_put_on_missing(self):
        self.put('/api/bills/9999', {'vendor_id': self.vend, 'lines': [{'amount': 5, 'account_id': self.rm}]},
                 expect=404)
        self.put('/api/bill_payments/9999', {'vendor_id': self.vend, 'amount': 5, 'pay_account_id': self.chk},
                 expect=404)

    def test_customer_cannot_get_bill(self):
        c = self.post('/api/customers', {'name': 'Cust'})['id']
        self.bill(10, vendor=c, expect=400)
        self.pay(10, vendor=c, expect=400)


if __name__ == '__main__':
    unittest.main()
