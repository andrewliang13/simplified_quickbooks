"""QA: customer invoices, received payments, A/R aging, feed matching.

Tests whose docstring starts with "BUG" expose a defect and are expected to fail until it is fixed.
"""
import datetime
import unittest

from helpers import ApiCase

TODAY = datetime.date.today()


def day(offset):
    return (TODAY + datetime.timedelta(days=offset)).isoformat()


class ARCase(ApiCase):
    def setUp(self):
        super().setUp()
        self.c1 = self.post('/api/customers', {'name': 'Acme'})['id']
        self.c2 = self.post('/api/customers', {'name': 'Beta'})['id']
        self.sales = self.acct('Sales')
        self.chk = self.acct('Checking')
        self.udf = self.acct('Undeposited Funds')
        self.ar_id = self.acct('Accounts Receivable')

    # -- helpers
    def inv(self, amount, date='2026-01-10', cust=None, expect=200, **kw):
        body = {'customer_id': cust or self.c1, 'date': date,
                'lines': [{'description': 'Work', 'qty': 1, 'rate': amount, 'account_id': self.sales}]}
        body.update(kw)
        return self.post('/api/invoices', body, expect=expect)

    def pay(self, amount, date='2026-01-20', cust=None, expect=200, **kw):
        body = {'customer_id': cust or self.c1, 'date': date, 'amount': amount}
        body.update(kw)
        return self.post('/api/payments', body, expect=expect)

    def invoice(self, inv_id):
        return self.get('/api/invoices/%d' % inv_id)

    def bs(self, label, end='2030-12-31'):
        try:
            return self.val(self.get('/api/reports/balance_sheet', end=end), label)
        except KeyError:
            return 0.0

    def pl(self, label, start, end):
        try:
            return self.val(self.get('/api/reports/profit_loss', start=start, end=end), label)
        except KeyError:
            return 0.0

    def aging(self, end='2030-12-31'):
        return self.get('/api/reports/ar_aging', end=end)

    def aging_total(self, end='2030-12-31'):
        return self.val(self.aging(end), 'TOTAL', 5)

    def cust_bal(self, cid):
        return self.get('/api/customers/%d' % cid)['balance']

    def txn_lines(self, txn_id):
        t = self.get('/api/transactions/%d' % txn_id)
        return sorted((l['account'], l['debit'], l['credit']) for l in t['lines'])

    def assert_ties(self, end='2030-12-31'):
        """Customer balances == A/R on the balance sheet == A/R aging total; books balanced."""
        ar = self.bs('Accounts Receivable', end)
        self.assertEqual(self.aging_total(end), ar, 'aging total vs balance sheet A/R as of %s' % end)
        if end == '2030-12-31':
            total = round(sum(c['balance'] for c in self.get('/api/customers', include_inactive=1)), 2)
            self.assertEqual(total, ar, 'customer balances vs balance sheet A/R')
        bsr = self.get('/api/reports/balance_sheet', end=end)
        self.assertEqual(self.val(bsr, 'TOTAL ASSETS'), self.val(bsr, 'TOTAL LIABILITIES AND EQUITY'))
        tb = self.get('/api/reports/trial_balance', end=end)
        self.assertEqual(self.val(tb, 'TOTAL', 0), self.val(tb, 'TOTAL', 1))


# ============================================================ postings

class Postings(ARCase):
    def test_invoice_debits_ar_credits_income(self):
        inv = self.inv(250)
        self.assertEqual(self.txn_lines(inv['txn_id']),
                         [('Accounts Receivable', 250.0, 0), ('Sales', 0, 250.0)])
        t = self.get('/api/transactions/%d' % inv['txn_id'])
        self.assertEqual((t['type'], t['ref'], t['name'], t['invoice_id']), ('Invoice', '1001', 'Acme', inv['id']))
        self.assert_ties()

    def test_payment_defaults_to_undeposited_funds(self):
        self.inv(250)
        p = self.pay(250)
        self.assertEqual(p['deposit_account'], 'Undeposited Funds')
        self.assertEqual(self.txn_lines(p['txn_id']),
                         [('Accounts Receivable', 0, 250.0), ('Undeposited Funds', 250.0, 0)])
        self.assertEqual(self.bs('Undeposited Funds'), 250.0)
        self.assertEqual(self.bs('Accounts Receivable'), 0.0)
        self.assert_ties()

    def test_payment_to_bank(self):
        self.inv(250)
        p = self.pay(100, deposit_account_id=self.chk)
        self.assertEqual(self.txn_lines(p['txn_id']), [('Accounts Receivable', 0, 100.0), ('Checking', 100.0, 0)])
        self.assertEqual(self.bs('Checking'), 100.0)
        self.assertEqual(self.bs('Accounts Receivable'), 150.0)

    def test_multi_account_invoice(self):
        svc = self.acct('Service Income')
        inv = self.inv(0, lines=[{'description': 'Goods', 'qty': 2, 'rate': 50, 'account_id': self.sales},
                                 {'description': 'Labor', 'qty': 3, 'rate': 40, 'account_id': svc}])
        self.assertEqual(inv['total'], 220.0)
        self.assertEqual(self.txn_lines(inv['txn_id']),
                         [('Accounts Receivable', 220.0, 0), ('Sales', 0, 100.0), ('Service Income', 0, 120.0)])
        self.assertEqual(self.pl('Sales', '2026-01-01', '2026-01-31'), 100.0)
        self.assertEqual(self.pl('Service Income', '2026-01-01', '2026-01-31'), 120.0)

    def test_income_recognised_on_invoice_date_not_payment_date(self):
        self.inv(300, date='2026-01-10')
        self.pay(300, date='2026-02-10')
        self.assertEqual(self.pl('Sales', '2026-01-01', '2026-01-31'), 300.0)
        self.assertEqual(self.pl('Sales', '2026-02-01', '2026-02-28'), 0.0)
        self.assertEqual(self.pl('Sales', '2026-01-01', '2026-12-31'), 300.0)

    def test_invoice_date_edit_moves_income_period(self):
        inv = self.inv(300, date='2026-01-10')
        self.put('/api/invoices/%d' % inv['id'], {
            'customer_id': self.c1, 'date': '2026-02-05',
            'lines': [{'description': 'Work', 'qty': 1, 'rate': 300, 'account_id': self.sales}]})
        self.assertEqual(self.pl('Sales', '2026-01-01', '2026-01-31'), 0.0)
        self.assertEqual(self.pl('Sales', '2026-02-01', '2026-02-28'), 300.0)
        self.assertEqual(self.bs('Accounts Receivable', '2026-01-31'), 0.0)
        self.assertEqual(self.bs('Accounts Receivable', '2026-02-28'), 300.0)


# ============================================================ totals and rounding

class Totals(ARCase):
    def line_total(self, qty, rate):
        return self.inv(0, lines=[{'description': 'x', 'qty': qty, 'rate': rate, 'account_id': self.sales}])

    def test_fraction_rounds_half_up(self):
        self.assertEqual(self.line_total(3, 33.335)['total'], 100.01)       # 100.005
        self.assertEqual(self.line_total(0.1, 0.15)['total'], 0.02)         # 0.015
        self.assertEqual(self.line_total(1.5, 19.99)['total'], 29.99)       # 29.985
        self.assertEqual(self.line_total(7, 0.07)['total'], 0.49)
        self.assertEqual(self.line_total(0.333, 100)['total'], 33.3)
        self.assertEqual(self.line_total('2.5', '1,000.10')['total'], 2500.25)

    def test_total_is_sum_of_rounded_lines(self):
        lines = [{'description': 'L%d' % i, 'qty': 0.1, 'rate': 0.35, 'account_id': self.sales}
                 for i in range(60)]                                         # each 0.035 -> 0.04
        inv = self.inv(0, lines=lines)
        self.assertEqual(len(inv['lines']), 60)
        self.assertTrue(all(l['amount'] == 0.04 for l in inv['lines']))
        self.assertEqual(inv['total'], 2.4)
        self.assertEqual(self.bs('Accounts Receivable'), 2.4)
        self.assertEqual(self.pl('Sales', '2026-01-01', '2026-01-31'), 2.4)
        self.assert_ties()

    def test_default_qty_is_one(self):
        inv = self.inv(0, lines=[{'description': 'x', 'rate': 12.34, 'account_id': self.sales}])
        self.assertEqual((inv['total'], inv['lines'][0]['qty']), (12.34, 1.0))

    def test_negative_discount_line(self):
        inv = self.inv(0, lines=[{'description': 'Work', 'qty': 1, 'rate': 100, 'account_id': self.sales},
                                 {'description': 'Discount', 'qty': 1, 'rate': -10, 'account_id': self.sales}])
        self.assertEqual((inv['total'], inv['balance']), (90.0, 90.0))
        self.assertEqual([l['amount'] for l in inv['lines']], [100.0, -10.0])
        self.assertEqual(self.txn_lines(inv['txn_id']),
                         [('Accounts Receivable', 90.0, 0), ('Sales', 0, 100.0), ('Sales', 10.0, 0)])
        self.assertEqual(self.pl('Sales', '2026-01-01', '2026-01-31'), 90.0)
        self.assert_ties()

    def test_discount_cannot_make_invoice_negative(self):
        self.inv(0, expect=400, lines=[{'description': 'Work', 'qty': 1, 'rate': 100, 'account_id': self.sales},
                                       {'description': 'Disc', 'qty': 1, 'rate': -150, 'account_id': self.sales}])
        self.assertEqual(self.get('/api/invoices'), [])
        self.assertEqual(self.bs('Accounts Receivable'), 0.0)

    def test_blank_lines_ignored_and_no_lines_rejected(self):
        inv = self.inv(0, lines=[{'description': '', 'qty': '', 'rate': '', 'account_id': ''},
                                 {'description': 'Work', 'qty': 1, 'rate': 5, 'account_id': self.sales}])
        self.assertEqual(len(inv['lines']), 1)
        self.inv(0, expect=400, lines=[])
        self.inv(0, expect=400, lines=[{'description': '', 'qty': 1, 'rate': 0}])

    def test_line_needs_account_and_not_ar(self):
        self.inv(0, expect=400, lines=[{'description': 'x', 'qty': 1, 'rate': 5}])
        self.inv(0, expect=400, lines=[{'description': 'x', 'qty': 1, 'rate': 5, 'account_id': 99999}])
        self.inv(0, expect=400, lines=[{'description': 'x', 'qty': 1, 'rate': 5, 'account_id': self.ar_id}])

    def test_line_account_cannot_be_a_bank_account(self):
        """BUG: error text says 'choose an income account' but a Bank account is accepted: the invoice then
        debits A/R and credits Checking, with no income."""
        self.inv(0, expect=400, lines=[{'description': 'x', 'qty': 1, 'rate': 5, 'account_id': self.chk}])

    def test_amount_is_qty_times_rate(self):
        """BUG: a posted 'amount' that disagrees with qty x rate is trusted (QBO: amount = qty x rate)."""
        self.inv(0, lines=[{'description': 'x', 'qty': 3, 'rate': 10, 'amount': 999,
                            'account_id': self.sales}], expect=400)   # mismatch is rejected


# ============================================================ numbering

class Numbering(ARCase):
    def test_sequence(self):
        self.assertEqual(self.get('/api/invoices/next_number')['number'], '1001')
        self.assertEqual(self.inv(10)['number'], '1001')
        self.assertEqual(self.inv(10)['number'], '1002')
        self.assertEqual(self.get('/api/invoices/next_number')['number'], '1003')

    def test_custom_number_continues_sequence(self):
        self.assertEqual(self.inv(10, number='2500')['number'], '2500')
        self.assertEqual(self.inv(10)['number'], '2501')

    def test_duplicate_number_rejected(self):
        self.inv(10, number='A-1')
        self.inv(10, number='A-1', expect=409)
        self.inv(10, cust=self.c2, number='A-1', expect=409)
        self.assertEqual(len(self.get('/api/invoices')), 1)

    def test_edit_keeps_number_and_can_change_it(self):
        a = self.inv(10)
        b = self.inv(10)
        body = {'customer_id': self.c1, 'date': '2026-01-10',
                'lines': [{'description': 'W', 'qty': 1, 'rate': 10, 'account_id': self.sales}]}
        self.assertEqual(self.put('/api/invoices/%d' % a['id'], body)['number'], '1001')
        self.assertEqual(self.put('/api/invoices/%d' % a['id'], dict(body, number='1001'))['number'], '1001')
        self.put('/api/invoices/%d' % a['id'], dict(body, number=b['number']), expect=409)
        self.assertEqual(self.put('/api/invoices/%d' % a['id'], dict(body, number='X9'))['number'], 'X9')
        self.assertEqual(self.get('/api/transactions/%d' % a['txn_id'])['ref'], 'X9')

    def test_alphanumeric_number_increments(self):
        """BUG (QBO gap): QBO continues from the last number used (INV-007 -> INV-008)."""
        self.inv(10, number='INV-007')
        self.assertEqual(self.get('/api/invoices/next_number')['number'], 'INV-008')

    def test_unicode_digit_number_does_not_break_numbering(self):
        """BUG: a number like '²' passes str.isdigit() but int() fails -> every later auto-number is a 500."""
        self.inv(10, number='²')
        self.get('/api/invoices/next_number')
        self.inv(10)


# ============================================================ due dates / terms

class DueDates(ARCase):
    def test_terms(self):
        for terms, due in (('Due on receipt', '2026-01-10'), ('Net 15', '2026-01-25'),
                           ('Net 30', '2026-02-09'), ('Net 60', '2026-03-11')):
            inv = self.inv(10, terms=terms)
            self.assertEqual((inv['terms'], inv['due_date']), (terms, due))

    def test_terms_across_leap_day_and_year_end(self):
        self.assertEqual(self.inv(10, date='2028-01-31', terms='Net 30')['due_date'], '2028-03-01')
        self.assertEqual(self.inv(10, date='2027-01-31', terms='Net 30')['due_date'], '2027-03-02')
        self.assertEqual(self.inv(10, date='2026-12-20', terms='Net 15')['due_date'], '2027-01-04')

    def test_no_terms_defaults_to_net_30(self):
        self.assertEqual(self.inv(10)['due_date'], '2026-02-09')

    def test_explicit_due_date_wins(self):
        inv = self.inv(10, terms='Net 30', due_date='2026-01-17')
        self.assertEqual(inv['due_date'], '2026-01-17')

    def test_due_before_invoice_date_rejected(self):
        self.inv(10, due_date='2026-01-09', expect=400)

    def test_edit_date_recomputes_due_from_terms(self):
        inv = self.inv(10, terms='Net 15')
        inv = self.put('/api/invoices/%d' % inv['id'], {
            'customer_id': self.c1, 'date': '2026-03-01', 'terms': 'Net 15',
            'lines': [{'description': 'W', 'qty': 1, 'rate': 10, 'account_id': self.sales}]})
        self.assertEqual(inv['due_date'], '2026-03-16')

    def test_unknown_terms_rejected_or_net_n_honoured(self):
        """BUG: unknown terms ('Net 45') are stored but the due date silently becomes the invoice date."""
        status, data = self.app.handle('POST', '/api/invoices', {}, {
            'customer_id': self.c1, 'date': '2026-01-10', 'terms': 'Net 45',
            'lines': [{'description': 'W', 'qty': 1, 'rate': 10, 'account_id': self.sales}]})
        if status == 200:
            self.assertEqual(data['due_date'], '2026-02-24')
        else:
            self.assertEqual(status, 400)


# ============================================================ status

class Status(ARCase):
    def test_open_partial_paid(self):
        inv = self.inv(100, date=day(-5), terms='Net 30')
        self.assertEqual(inv['status'], 'Open')
        self.pay(40, date=day(-1))
        self.assertEqual(self.invoice(inv['id'])['status'], 'Partial')
        self.pay(60, date=day(0))
        i = self.invoice(inv['id'])
        self.assertEqual((i['status'], i['balance'], i['applied']), ('Paid', 0.0, 100.0))

    def test_overdue(self):
        inv = self.inv(100, date=day(-40), terms='Net 30')
        self.assertEqual(inv['status'], 'Overdue')
        self.pay(40, date=day(-1))
        self.assertEqual(self.invoice(inv['id'])['status'], 'Overdue')
        self.pay(60, date=day(0))
        self.assertEqual(self.invoice(inv['id'])['status'], 'Paid')

    def test_due_today_is_not_overdue(self):
        self.assertEqual(self.inv(100, date=day(0), terms='Due on receipt')['status'], 'Open')
        self.assertEqual(self.inv(100, date=day(-1), terms='Due on receipt')['status'], 'Overdue')

    def test_list_filters(self):
        a = self.inv(100, date=day(-40), terms='Net 30')                    # overdue
        b = self.inv(100, date=day(-5), terms='Net 30')                     # open
        c = self.inv(100, date=day(-4), terms='Net 30')                     # partial
        d = self.inv(100, date=day(-3), terms='Net 30')                     # paid
        e = self.inv(100, date=day(-2), terms='Net 30', cust=self.c2)       # other customer, open
        self.pay(40, date=day(0), applications=[{'invoice_id': c['id'], 'amount': 40}])
        self.pay(100, date=day(0), applications=[{'invoice_id': d['id'], 'amount': 100}])

        def ids(**q):
            return sorted(i['id'] for i in self.get('/api/invoices', **q))
        self.assertEqual(ids(), sorted(x['id'] for x in (a, b, c, d, e)))
        self.assertEqual(ids(status='all'), ids())
        self.assertEqual(ids(status='open'), sorted(x['id'] for x in (a, b, c, e)))
        self.assertEqual(ids(status='paid'), [d['id']])
        self.assertEqual(ids(status='overdue'), [a['id']])
        self.assertEqual(ids(status='partial'), [c['id']])
        self.assertEqual(ids(customer_id=self.c2), [e['id']])
        self.assertEqual(ids(start=day(-5), end=day(-4)), sorted([b['id'], c['id']]))
        self.assertEqual(ids(customer_id=self.c1, status='open'), sorted(x['id'] for x in (a, b, c)))

    def test_list_bad_params(self):
        self.call('GET', '/api/invoices', expect=400, customer_id='abc')
        self.call('GET', '/api/invoices', expect=400, start='nope')
        self.call('GET', '/api/payments', expect=400, customer_id='abc')


# ============================================================ payments

class Payments(ARCase):
    def test_partial_then_rest(self):
        inv = self.inv(100)
        p1 = self.pay(30)
        self.assertEqual((p1['applied'], p1['unapplied']), (30.0, 0.0))
        i = self.invoice(inv['id'])
        self.assertEqual((i['balance'], i['applied'], len(i['payments'])), (70.0, 30.0, 1))
        self.pay(70)
        i = self.invoice(inv['id'])
        self.assertEqual((i['balance'], len(i['payments'])), (0.0, 2))
        self.assertEqual(self.cust_bal(self.c1), 0.0)
        self.assert_ties()

    def test_one_payment_several_invoices_explicit(self):
        a, b, c = self.inv(100), self.inv(200, date='2026-01-11'), self.inv(300, date='2026-01-12')
        p = self.pay(450, applications=[{'invoice_id': c['id'], 'amount': 300},
                                        {'invoice_id': a['id'], 'amount': 50},
                                        {'invoice_id': b['id'], 'amount': 100}])
        self.assertEqual((p['applied'], p['unapplied']), (450.0, 0.0))
        self.assertEqual([self.invoice(x['id'])['balance'] for x in (a, b, c)], [50.0, 100.0, 0.0])
        self.assertEqual(self.cust_bal(self.c1), 150.0)
        self.assertEqual(len(self.get('/api/transactions/%d' % p['txn_id'])['lines']), 2)
        self.assert_ties()

    def test_auto_apply_oldest_first(self):
        b = self.inv(200, date='2026-01-15')
        a = self.inv(100, date='2026-01-05')                                 # older but created later
        p = self.pay(250)
        self.assertEqual([(x['invoice_id'], x['amount']) for x in p['applications']],
                         [(a['id'], 100.0), (b['id'], 150.0)])
        self.assertEqual(self.invoice(b['id'])['balance'], 50.0)

    def test_auto_apply_ignores_other_customers(self):
        other = self.inv(100, cust=self.c2, date='2026-01-01')
        mine = self.inv(100, date='2026-01-05')
        p = self.pay(100)
        self.assertEqual([x['invoice_id'] for x in p['applications']], [mine['id']])
        self.assertEqual(self.invoice(other['id'])['balance'], 100.0)

    def test_over_application_rejected(self):
        inv = self.inv(100)
        self.pay(150, applications=[{'invoice_id': inv['id'], 'amount': 150}], expect=400)
        self.pay(60, applications=[{'invoice_id': inv['id'], 'amount': 60}])
        self.pay(41, applications=[{'invoice_id': inv['id'], 'amount': 41}], expect=400)
        self.pay(100, applications=[{'invoice_id': inv['id'], 'amount': 30},
                                    {'invoice_id': inv['id'], 'amount': 30}], expect=400)   # merged 60 > 40
        self.assertEqual(self.invoice(inv['id'])['balance'], 40.0)
        self.assertEqual(self.bs('Accounts Receivable'), 40.0)

    def test_applications_cannot_exceed_payment(self):
        a, b = self.inv(100), self.inv(100)
        self.pay(150, applications=[{'invoice_id': a['id'], 'amount': 100},
                                    {'invoice_id': b['id'], 'amount': 100}], expect=400)
        self.pay(50, applications=[{'invoice_id': a['id'], 'amount': -10},
                                   {'invoice_id': b['id'], 'amount': 60}], expect=400)
        self.assertEqual(self.get('/api/payments'), [])

    def test_cannot_apply_to_other_customers_invoice(self):
        other = self.inv(100, cust=self.c2)
        self.pay(100, applications=[{'invoice_id': other['id'], 'amount': 100}], expect=400)
        self.assertEqual(self.invoice(other['id'])['balance'], 100.0)
        self.assertEqual(self.get('/api/payments'), [])
        self.assertEqual(self.bs('Undeposited Funds'), 0.0)

    def test_overpayment_leaves_unapplied_credit(self):
        inv = self.inv(100)
        p = self.pay(130)
        self.assertEqual((p['applied'], p['unapplied']), (100.0, 30.0))
        self.assertEqual(self.invoice(inv['id'])['status'], 'Paid')
        self.assertEqual(self.cust_bal(self.c1), -30.0)
        self.assertEqual(self.bs('Accounts Receivable'), -30.0)
        self.assertEqual(self.val(self.aging(), 'Acme', 5), -30.0)
        self.assert_ties()

    def test_payment_with_no_invoices_is_credit(self):
        p = self.pay(75)
        self.assertEqual((p['applied'], p['unapplied'], p['applications']), (0.0, 75.0, []))
        self.assertEqual(self.cust_bal(self.c1), -75.0)
        self.assert_ties()

    def test_unapplied_credit_can_be_applied_later_by_editing(self):
        p = self.pay(75, date='2026-01-05')
        inv = self.inv(100, date='2026-01-10')
        p = self.put('/api/payments/%d' % p['id'], {
            'customer_id': self.c1, 'date': '2026-01-05', 'amount': 75,
            'applications': [{'invoice_id': inv['id'], 'amount': 75}]})
        self.assertEqual(p['unapplied'], 0.0)
        self.assertEqual(self.invoice(inv['id'])['balance'], 25.0)
        self.assertEqual(self.cust_bal(self.c1), 25.0)
        self.assert_ties()

    def test_amount_validation(self):
        self.inv(100)
        for bad in (0, -5, '', None, 'abc', 'NaN', 'Infinity', True, [1], {'a': 1}):
            self.pay(bad, expect=400)
        self.assertEqual(self.pay('1,000.005')['amount'], 1000.01)

    def test_customer_and_deposit_account_validation(self):
        v = self.post('/api/vendors', {'name': 'Vend'})['id']
        self.pay(10, cust=v, expect=400)
        self.post('/api/payments', {'date': '2026-01-01', 'amount': 10}, expect=400)
        self.post('/api/payments', {'customer_id': 9999, 'date': '2026-01-01', 'amount': 10}, expect=400)
        self.pay(10, deposit_account_id=self.sales, expect=400)
        self.pay(10, deposit_account_id=self.ar_id, expect=400)
        self.pay(10, deposit_account_id=99999, expect=400)
        self.pay(10, deposit_account_id='x', expect=400)
        self.pay(10, date='01/02/2026', expect=400)

    def test_deposit_account_must_be_bank_or_undeposited_funds(self):
        """BUG: any Other Current Asset (e.g. Prepaid Expenses) is accepted as the deposit account."""
        self.inv(100)
        self.pay(10, deposit_account_id=self.acct('Prepaid Expenses'), expect=400)

    def test_list_and_read(self):
        inv = self.inv(100)
        p = self.pay(40, method='Check', ref='1234', memo='thanks')
        self.pay(10, cust=self.c2)
        got = self.get('/api/payments/%d' % p['id'])
        self.assertEqual((got['method'], got['ref'], got['memo'], got['customer']),
                         ('Check', '1234', 'thanks', 'Acme'))
        self.assertEqual(got['applications'][0]['invoice_id'], inv['id'])
        self.assertEqual(len(self.get('/api/payments')), 2)
        self.assertEqual([x['id'] for x in self.get('/api/payments', customer_id=self.c1)], [p['id']])
        self.get('/api/payments/9999', expect=404) if False else self.call('GET', '/api/payments/9999', expect=404)
        self.call('GET', '/api/invoices/9999', expect=404)


# ============================================================ editing

class EditInvoice(ARCase):
    def body(self, amount, **kw):
        b = {'customer_id': self.c1, 'date': '2026-01-10',
             'lines': [{'description': 'W', 'qty': 1, 'rate': amount, 'account_id': self.sales}]}
        b.update(kw)
        return b

    def test_edit_unpaid_invoice_reposts_ledger(self):
        inv = self.inv(100)
        svc = self.acct('Service Income')
        new = self.put('/api/invoices/%d' % inv['id'], {
            'customer_id': self.c2, 'date': '2026-01-12',
            'lines': [{'description': 'W', 'qty': 2, 'rate': 80, 'account_id': svc}]})
        self.assertEqual((new['id'], new['txn_id'], new['total'], new['customer']),
                         (inv['id'], inv['txn_id'], 160.0, 'Beta'))
        self.assertEqual(self.txn_lines(inv['txn_id']),
                         [('Accounts Receivable', 160.0, 0), ('Service Income', 0, 160.0)])
        self.assertEqual((self.cust_bal(self.c1), self.cust_bal(self.c2)), (0.0, 160.0))
        self.assertEqual(self.pl('Sales', '2026-01-01', '2026-01-31'), 0.0)
        self.assertEqual(len(new['lines']), 1)
        self.assert_ties()

    def test_cannot_reduce_below_applied(self):
        inv = self.inv(100)
        self.pay(60)
        self.put('/api/invoices/%d' % inv['id'], self.body(59.99), expect=400)
        i = self.invoice(inv['id'])
        self.assertEqual((i['total'], i['balance']), (100.0, 40.0))
        i = self.put('/api/invoices/%d' % inv['id'], self.body(60))
        self.assertEqual((i['total'], i['balance'], i['status']), (60.0, 0.0, 'Paid'))
        self.assert_ties()

    def test_increase_after_full_payment_reopens(self):
        inv = self.inv(100, date=day(-3), terms='Net 30')
        self.pay(100, date=day(-1))
        i = self.put('/api/invoices/%d' % inv['id'], self.body(150, date=day(-3), terms='Net 30'))
        self.assertEqual((i['total'], i['balance'], i['status']), (150.0, 50.0, 'Partial'))
        self.assertEqual(self.cust_bal(self.c1), 50.0)
        self.assert_ties()

    def test_cannot_change_customer_after_payment(self):
        inv = self.inv(100)
        self.pay(60)
        self.put('/api/invoices/%d' % inv['id'], self.body(100, customer_id=self.c2), expect=400)
        self.assertEqual(self.invoice(inv['id'])['customer_id'], self.c1)
        self.assertEqual((self.cust_bal(self.c1), self.cust_bal(self.c2)), (40.0, 0.0))

    def test_edit_missing_invoice(self):
        self.put('/api/invoices/9999', self.body(10), expect=404)
        self.delete('/api/invoices/9999', expect=404)


class EditPayment(ARCase):
    def setUp(self):
        super().setUp()
        self.a = self.inv(100, date='2026-01-05')
        self.b = self.inv(200, date='2026-01-06')
        self.p = self.pay(100, date='2026-01-20', applications=[{'invoice_id': self.b['id'], 'amount': 100}])

    def edit(self, expect=200, **kw):
        body = {'customer_id': self.c1, 'date': '2026-01-20', 'amount': 100,
                'applications': [{'invoice_id': self.b['id'], 'amount': 100}]}
        body.update(kw)
        return self.put('/api/payments/%d' % self.p['id'], body, expect=expect)

    def bal(self):
        return [self.invoice(self.a['id'])['balance'], self.invoice(self.b['id'])['balance']]

    def test_change_amount_and_applications(self):
        p = self.edit(amount=250, applications=[{'invoice_id': self.b['id'], 'amount': 200},
                                                {'invoice_id': self.a['id'], 'amount': 50}])
        self.assertEqual((p['id'], p['txn_id'], p['amount'], p['unapplied']),
                         (self.p['id'], self.p['txn_id'], 250.0, 0.0))
        self.assertEqual(self.bal(), [50.0, 0.0])
        self.assertEqual(self.bs('Undeposited Funds'), 250.0)
        self.assertEqual(self.bs('Accounts Receivable'), 50.0)
        self.assert_ties()

    def test_can_reapply_own_amount(self):
        # the payment's existing application must not count against the invoice's open balance
        self.edit(amount=200, applications=[{'invoice_id': self.b['id'], 'amount': 200}])
        self.assertEqual(self.bal(), [100.0, 0.0])

    def test_lower_amount_below_applications_rejected(self):
        self.edit(amount=50, expect=400)
        self.assertEqual(self.get('/api/payments/%d' % self.p['id'])['amount'], 100.0)
        self.assertEqual(self.bal(), [100.0, 100.0])                        # applications restored (rollback)

    def test_over_apply_on_edit_rejected(self):
        self.edit(amount=500, applications=[{'invoice_id': self.b['id'], 'amount': 201}], expect=400)
        self.assertEqual(self.bal(), [100.0, 100.0])

    def test_move_application_to_other_invoice(self):
        self.edit(applications=[{'invoice_id': self.a['id'], 'amount': 100}])
        self.assertEqual(self.bal(), [0.0, 200.0])
        self.assertEqual(self.invoice(self.b['id'])['payments'], [])

    def test_unapply_everything(self):
        p = self.edit(applications=[])
        self.assertEqual((p['applied'], p['unapplied']), (0.0, 100.0))
        self.assertEqual(self.bal(), [100.0, 200.0])
        self.assertEqual(self.cust_bal(self.c1), 200.0)
        self.assert_ties()

    def test_change_deposit_account(self):
        self.edit(deposit_account_id=self.chk)
        self.assertEqual((self.bs('Undeposited Funds'), self.bs('Checking')), (0.0, 100.0))
        self.assertEqual(self.txn_lines(self.p['txn_id']),
                         [('Accounts Receivable', 0, 100.0), ('Checking', 100.0, 0)])

    def test_change_date(self):
        self.edit(date='2026-02-20')
        self.assertEqual(self.bs('Accounts Receivable', '2026-01-31'), 300.0)
        self.assertEqual(self.bs('Accounts Receivable', '2026-02-28'), 200.0)
        self.assert_ties('2026-01-31')
        self.assert_ties('2026-02-28')

    def test_change_customer_with_old_applications_rejected(self):
        self.edit(customer_id=self.c2, expect=400)
        self.assertEqual(self.bal(), [100.0, 100.0])
        self.assertEqual(self.get('/api/payments/%d' % self.p['id'])['customer_id'], self.c1)

    def test_change_customer(self):
        other = self.inv(80, cust=self.c2)
        p = self.edit(customer_id=self.c2, applications=[{'invoice_id': other['id'], 'amount': 80}])
        self.assertEqual((p['customer'], p['unapplied']), ('Beta', 20.0))
        self.assertEqual(self.bal(), [100.0, 200.0])
        self.assertEqual((self.cust_bal(self.c1), self.cust_bal(self.c2)), (300.0, -20.0))
        self.assert_ties()

    def test_edit_without_applications_keeps_existing_applications(self):
        """BUG: PUT without 'applications' (e.g. only fixing the memo) silently re-applies oldest-first."""
        self.put('/api/payments/%d' % self.p['id'], {
            'customer_id': self.c1, 'date': '2026-01-20', 'amount': 100, 'memo': 'fixed memo'})
        self.assertEqual(self.bal(), [100.0, 100.0])

    def test_edit_missing_payment(self):
        self.put('/api/payments/9999', {'customer_id': self.c1, 'date': '2026-01-20', 'amount': 1}, expect=404)
        self.delete('/api/payments/9999', expect=404)


# ============================================================ deleting

class Deleting(ARCase):
    def test_delete_payment_reopens_invoices(self):
        a, b = self.inv(100), self.inv(50)
        p = self.pay(150)
        self.assertEqual(self.cust_bal(self.c1), 0.0)
        self.delete('/api/payments/%d' % p['id'])
        self.assertEqual([self.invoice(x['id'])['balance'] for x in (a, b)], [100.0, 50.0])
        self.assertEqual(self.invoice(a['id'])['payments'], [])
        self.call('GET', '/api/transactions/%d' % p['txn_id'], expect=404)
        self.assertEqual(self.bs('Undeposited Funds'), 0.0)
        self.assertEqual(self.get('/api/payments'), [])
        self.assert_ties()

    def test_delete_unpaid_invoice(self):
        inv = self.inv(100)
        self.delete('/api/invoices/%d' % inv['id'])
        self.call('GET', '/api/invoices/%d' % inv['id'], expect=404)
        self.call('GET', '/api/transactions/%d' % inv['txn_id'], expect=404)
        self.assertEqual(self.bs('Accounts Receivable'), 0.0)
        self.assertEqual(self.pl('Sales', '2026-01-01', '2026-12-31'), 0.0)
        self.assertEqual(self.cust_bal(self.c1), 0.0)
        self.assertEqual(self.aging()['rows'][-1]['values'][5], 0.0)

    def test_invoice_with_payment_cannot_be_deleted_until_payment_removed(self):
        inv = self.inv(100)
        p = self.pay(10)
        self.delete('/api/invoices/%d' % inv['id'], expect=409)
        self.delete('/api/transactions/%d' % inv['txn_id'], expect=409)
        self.assertEqual(self.invoice(inv['id'])['balance'], 90.0)
        self.delete('/api/payments/%d' % p['id'])
        self.delete('/api/invoices/%d' % inv['id'])
        self.assert_ties()

    def test_delete_via_generic_transaction_endpoint(self):
        inv = self.inv(100)
        p = self.pay(40)
        self.delete('/api/transactions/%d' % p['txn_id'])
        self.assertEqual(self.get('/api/payments'), [])
        self.assertEqual(self.invoice(inv['id'])['balance'], 100.0)
        self.delete('/api/transactions/%d' % inv['txn_id'])
        self.assertEqual(self.get('/api/invoices'), [])
        self.assert_ties()

    def test_invoice_txn_cannot_be_edited_as_journal(self):
        inv = self.inv(100)
        self.put('/api/journals/%d' % inv['txn_id'], {'date': '2026-01-01', 'lines': [
            {'account_id': self.chk, 'debit': 5, 'credit': 0},
            {'account_id': self.sales, 'debit': 0, 'credit': 5}]}, expect=400)
        self.assertEqual(self.bs('Accounts Receivable'), 100.0)


# ============================================================ tie-out and aging

class TieOut(ARCase):
    def test_full_scenario_ties_at_every_step_and_date(self):
        dates = ('2025-12-31', '2026-01-15', '2026-01-31', '2026-02-15', '2026-03-31', '2030-12-31')

        def check():
            for d in dates:
                self.assert_ties(d)
        a = self.inv(1000, date='2026-01-05', terms='Net 15')
        b = self.inv(333.33, date='2026-01-20', terms='Net 30')
        c = self.inv(250, date='2026-02-01', cust=self.c2)
        check()
        p1 = self.pay(400, date='2026-01-25', applications=[{'invoice_id': a['id'], 'amount': 400}])
        check()
        p2 = self.pay(1000, date='2026-02-10')                              # 600 -> a, 333.33 -> b, 66.67 credit
        self.assertEqual(self.get('/api/payments/%d' % p2['id'])['unapplied'], 66.67)
        check()
        self.pay(100, date='2026-02-12', cust=self.c2, deposit_account_id=self.chk)
        check()
        self.assertEqual((self.cust_bal(self.c1), self.cust_bal(self.c2)), (-66.67, 150.0))
        self.assertEqual(self.bs('Accounts Receivable'), 83.33)
        self.put('/api/payments/%d' % p1['id'], {'customer_id': self.c1, 'date': '2026-03-01', 'amount': 300,
                                                 'applications': [{'invoice_id': a['id'], 'amount': 300}]})
        check()
        self.delete('/api/payments/%d' % p2['id'])
        check()
        self.delete('/api/invoices/%d' % b['id'])
        check()
        self.assertEqual((self.cust_bal(self.c1), self.cust_bal(self.c2)), (700.0, 150.0))
        self.assertEqual(self.invoice(c['id'])['balance'], 150.0)

    def test_many_small_payments_no_drift(self):
        inv = self.inv(10)
        for _ in range(100):
            self.pay(0.1)
        i = self.invoice(inv['id'])
        self.assertEqual((i['balance'], i['status'], i['applied']), (0.0, 'Paid', 10.0))
        self.assertEqual(self.cust_bal(self.c1), 0.0)
        self.assert_ties()


class Aging(ARCase):
    def buckets(self, end, label='Acme'):
        return self.row(self.aging(end), label)['values']

    def test_buckets_by_days_past_due(self):
        self.inv(100, date='2026-01-01', terms='Net 30')                    # due 2026-01-31
        self.assertEqual(self.buckets('2026-01-01'), [100.0, 0, 0, 0, 0, 100.0])
        self.assertEqual(self.buckets('2026-01-31'), [100.0, 0, 0, 0, 0, 100.0])
        self.assertEqual(self.buckets('2026-02-01'), [0, 100.0, 0, 0, 0, 100.0])    # 1 day
        self.assertEqual(self.buckets('2026-03-02'), [0, 100.0, 0, 0, 0, 100.0])    # 30 days
        self.assertEqual(self.buckets('2026-03-03'), [0, 0, 100.0, 0, 0, 100.0])    # 31
        self.assertEqual(self.buckets('2026-04-01'), [0, 0, 100.0, 0, 0, 100.0])    # 60
        self.assertEqual(self.buckets('2026-04-02'), [0, 0, 0, 100.0, 0, 100.0])    # 61
        self.assertEqual(self.buckets('2026-05-01'), [0, 0, 0, 100.0, 0, 100.0])    # 90
        self.assertEqual(self.buckets('2026-05-02'), [0, 0, 0, 0, 100.0, 100.0])    # 91
        self.assertEqual(self.aging('2025-12-31')['rows'][-1]['values'][5], 0.0)    # before the invoice

    def test_as_of_before_payment_shows_full_balance(self):
        self.inv(100, date='2026-01-01', terms='Net 30')
        self.pay(40, date='2026-02-15')
        self.assertEqual(self.buckets('2026-02-10'), [0, 100.0, 0, 0, 0, 100.0])
        self.assertEqual(self.buckets('2026-02-15'), [0, 60.0, 0, 0, 0, 60.0])
        self.assert_ties('2026-02-10')
        self.assert_ties('2026-02-15')
        self.pay(60, date='2026-03-10')
        self.assertEqual(self.buckets('2026-03-09'), [0, 0, 60.0, 0, 0, 60.0])
        self.assertEqual(self.aging('2026-03-10')['rows'][-1]['values'][5], 0.0)
        self.assert_ties('2026-03-09')
        self.assert_ties('2026-03-10')

    def test_two_customers_and_drill(self):
        self.inv(100, date='2026-01-01', terms='Net 30')
        self.inv(70, date='2026-02-20', terms='Net 30')
        self.inv(55, date='2026-01-01', terms='Due on receipt', cust=self.c2)
        self.pay(20, date='2026-01-15', cust=self.c2)
        r = self.aging('2026-03-03')
        self.assertEqual(self.row(r, 'Acme')['values'], [70.0, 0, 100.0, 0, 0, 170.0])
        self.assertEqual(self.row(r, 'Beta')['values'], [0, 0, 0, 35.0, 0, 35.0])
        self.assertEqual(self.row(r, 'TOTAL')['values'], [70.0, 0, 100.0, 35.0, 0, 205.0])
        for label in ('Acme', 'Beta', 'TOTAL'):
            self.assertEqual(self.drill_total(r, label, 5), self.val(r, label, 5))
        self.assert_ties('2026-03-03')

    def test_unapplied_credit_shown(self):
        self.inv(100, date='2026-01-01')
        self.pay(130, date='2026-01-10')
        self.assertEqual(self.buckets('2026-01-31')[5], -30.0)
        self.assertEqual(self.buckets('2026-01-05')[5], 100.0)
        self.assert_ties('2026-01-05')
        self.assert_ties('2026-01-31')

    def test_prepayment_applied_to_later_invoice_ties_between_the_dates(self):
        """BUG: payment dated before the invoice it is applied to vanishes from aging in between."""
        p = self.pay(100, date='2026-01-05')
        inv = self.inv(100, date='2026-02-01')
        self.put('/api/payments/%d' % p['id'], {'customer_id': self.c1, 'date': '2026-01-05', 'amount': 100,
                                                'applications': [{'invoice_id': inv['id'], 'amount': 100}]})
        self.assert_ties('2026-02-28')
        self.assertEqual(self.bs('Accounts Receivable', '2026-01-15'), -100.0)
        self.assertEqual(self.aging_total('2026-01-15'), -100.0)

    def test_journal_entry_to_ar_appears_in_aging(self):
        """BUG: A/R journal entries (allowed with a customer name) are missing from the aging report."""
        self.inv(100, date='2026-01-01')
        self.journal('2026-01-15', ('Accounts Receivable', 40, 0, self.c1), ('Sales', 0, 40))
        self.assertEqual(self.cust_bal(self.c1), 140.0)
        self.assertEqual(self.bs('Accounts Receivable'), 140.0)
        self.assertEqual(self.aging_total(), 140.0)

    def test_bad_date(self):
        self.call('GET', '/api/reports/ar_aging', expect=400, end='2026-13-01')


# ============================================================ reconciliation locks

class ReconLock(ARCase):
    def reconcile(self, ending):
        rec = self.post('/api/reconciliations', {'account_id': self.chk, 'statement_date': '2026-01-31',
                                                 'ending_balance': ending})
        ids = [l['line_id'] for l in rec['lines']]
        self.post('/api/reconciliations/%d/toggle' % rec['id'], {'line_ids': ids, 'cleared': True})
        return self.post('/api/reconciliations/%d/finish' % rec['id'])

    def test_reconciled_payment_is_locked_and_books_unchanged(self):
        inv = self.inv(100)
        p = self.pay(100, deposit_account_id=self.chk)
        rec = self.reconcile(100)
        body = {'customer_id': self.c1, 'date': '2026-01-20', 'amount': 60, 'deposit_account_id': self.chk,
                'applications': [{'invoice_id': inv['id'], 'amount': 60}]}
        self.put('/api/payments/%d' % p['id'], body, expect=409)
        self.delete('/api/payments/%d' % p['id'], expect=409)
        self.delete('/api/transactions/%d' % p['txn_id'], expect=409)
        # nothing changed, including the applications that save_payment deletes up-front
        got = self.get('/api/payments/%d' % p['id'])
        self.assertEqual((got['amount'], got['applied']), (100.0, 100.0))
        self.assertEqual(self.invoice(inv['id'])['status'], 'Paid')
        self.assertEqual(self.bs('Checking'), 100.0)
        self.delete('/api/invoices/%d' % inv['id'], expect=409)
        self.assert_ties()
        # after undoing the reconciliation it can be changed again
        self.delete('/api/reconciliations/%d' % rec['id'])
        self.put('/api/payments/%d' % p['id'], body)
        self.assertEqual(self.invoice(inv['id'])['balance'], 40.0)
        self.delete('/api/payments/%d' % p['id'])
        self.assert_ties()

    def test_invoice_still_editable_when_its_payment_is_reconciled(self):
        inv = self.inv(100)
        self.pay(100, deposit_account_id=self.chk)
        self.reconcile(100)
        i = self.put('/api/invoices/%d' % inv['id'], {
            'customer_id': self.c1, 'date': '2026-01-10', 'memo': 'edited',
            'lines': [{'description': 'W', 'qty': 1, 'rate': 120, 'account_id': self.sales}]})
        self.assertEqual((i['total'], i['balance'], i['memo']), (120.0, 20.0, 'edited'))
        self.assertEqual(self.bs('Checking'), 100.0)
        self.assert_ties()

    def test_undeposited_payment_not_affected_by_bank_reconciliation(self):
        inv = self.inv(100)
        p = self.pay(100)                                                    # Undeposited Funds
        self.journal('2026-01-21', ('Checking', 100, 0), ('Undeposited Funds', 0, 100))   # bank deposit
        self.reconcile(100)
        self.put('/api/payments/%d' % p['id'], {'customer_id': self.c1, 'date': '2026-01-20', 'amount': 100,
                                                'memo': 'x', 'applications': [{'invoice_id': inv['id'],
                                                                               'amount': 100}]})
        self.assertEqual(self.invoice(inv['id'])['status'], 'Paid')


# ============================================================ bank feed matching

CSV = """Date,Description,Amount
01/20/2026,DEPOSIT ACME,500.00
01/22/2026,DEPOSIT SMALL,200.00
01/23/2026,CHECK 101,-500.00
"""


class FeedMatch(ARCase):
    def setUp(self):
        super().setUp()
        self.post('/api/feed/import', {'account_id': self.chk, 'content': CSV})
        self.rows = {r['description']: r for r in self.get('/api/feed', account_id=self.chk)['rows']}

    def feed(self):
        out = {}
        for st in ('review', 'posted', 'excluded'):
            for r in self.get('/api/feed', account_id=self.chk, status=st)['rows']:
                out[r['description']] = dict(r, status=st)
        return out

    def test_match_creates_payment_and_pays_invoice(self):
        inv = self.inv(500, date='2026-01-10')
        row = self.get('/api/feed', account_id=self.chk)['rows']
        row = [r for r in row if r['description'] == 'DEPOSIT ACME'][0]
        self.assertEqual([m['invoice_id'] for m in row['matches'] if m['kind'] == 'invoice'], [inv['id']])
        t = self.post('/api/feed/%d/match' % row['id'], {'invoice_id': inv['id']})
        self.assertEqual((t['type'], t['date']), ('Payment', '2026-01-20'))
        self.assertEqual(sorted((l['account'], l['debit'], l['credit']) for l in t['lines']),
                         [('Accounts Receivable', 0, 500.0), ('Checking', 500.0, 0)])
        i = self.invoice(inv['id'])
        self.assertEqual((i['status'], i['balance']), ('Paid', 0.0))
        p = self.get('/api/payments')[0]
        self.assertEqual((p['customer'], p['amount'], p['deposit_account'], p['unapplied']),
                         ('Acme', 500.0, 'Checking', 0.0))
        self.assertEqual(self.bs('Checking'), 500.0)
        self.assert_ties()
        self.post('/api/feed/%d/match' % row['id'], {'invoice_id': inv['id']}, expect=409)   # already handled

    def test_partial_match_and_too_large(self):
        inv = self.inv(300, date='2026-01-10')
        self.post('/api/feed/%d/match' % self.rows['DEPOSIT ACME']['id'], {'invoice_id': inv['id']}, expect=400)
        self.assertEqual(self.get('/api/payments'), [])
        self.post('/api/feed/%d/match' % self.rows['DEPOSIT SMALL']['id'], {'invoice_id': inv['id']})
        i = self.invoice(inv['id'])
        self.assertEqual((i['balance'], i['applied']), (100.0, 200.0))
        self.assert_ties()

    def test_withdrawal_and_bad_invoice(self):
        inv = self.inv(500, date='2026-01-10')
        self.post('/api/feed/%d/match' % self.rows['CHECK 101']['id'], {'invoice_id': inv['id']}, expect=400)
        self.post('/api/feed/%d/match' % self.rows['DEPOSIT ACME']['id'], {'invoice_id': 9999}, expect=404)
        self.post('/api/feed/%d/match' % self.rows['DEPOSIT ACME']['id'], {'invoice_id': 'x'}, expect=400)
        self.assertEqual(self.invoice(inv['id'])['balance'], 500.0)

    def test_undo_match_removes_payment(self):
        inv = self.inv(500, date='2026-01-10')
        rid = self.rows['DEPOSIT ACME']['id']
        self.post('/api/feed/%d/match' % rid, {'invoice_id': inv['id']})
        self.post('/api/feed/%d/undo' % rid)
        self.assertEqual(self.get('/api/payments'), [])
        self.assertEqual(self.invoice(inv['id'])['balance'], 500.0)
        self.assertEqual(self.bs('Checking'), 0.0)
        self.assert_ties()
        self.post('/api/feed/%d/match' % rid, {'invoice_id': inv['id']})      # and can be matched again
        self.assertEqual(self.invoice(inv['id'])['status'], 'Paid')

    def test_delete_matched_payment_returns_feed_row_to_review(self):
        inv = self.inv(500, date='2026-01-10')
        rid = self.rows['DEPOSIT ACME']['id']
        self.post('/api/feed/%d/match' % rid, {'invoice_id': inv['id']})
        p = self.get('/api/payments')[0]
        self.delete('/api/payments/%d' % p['id'])
        self.assertEqual(self.invoice(inv['id'])['balance'], 500.0)
        self.assertEqual(self.feed()['DEPOSIT ACME']['status'], 'review')

    def test_editing_matched_payment_amount_releases_feed_row(self):
        inv = self.inv(500, date='2026-01-10')
        rid = self.rows['DEPOSIT ACME']['id']
        self.post('/api/feed/%d/match' % rid, {'invoice_id': inv['id']})
        p = self.get('/api/payments')[0]
        body = {'customer_id': self.c1, 'date': '2026-01-20', 'amount': 500, 'deposit_account_id': self.chk,
                'memo': 'note', 'applications': [{'invoice_id': inv['id'], 'amount': 500}]}
        self.put('/api/payments/%d' % p['id'], body)                         # same amount: stays matched
        self.assertEqual(self.feed()['DEPOSIT ACME']['status'], 'posted')
        self.put('/api/payments/%d' % p['id'], dict(body, amount=450,
                                                    applications=[{'invoice_id': inv['id'], 'amount': 450}]))
        self.assertEqual(self.feed()['DEPOSIT ACME']['status'], 'review')
        self.assert_ties()

    def test_undeposited_payment_can_be_matched_to_bank_deposit(self):
        """BUG (QBO gap): a payment received into Undeposited Funds (the default) cannot be matched to the
        bank deposit, so the only choices are a duplicate payment or a manual journal entry."""
        inv = self.inv(500, date='2026-01-10')
        self.pay(500, date='2026-01-19')                                     # default: Undeposited Funds
        row = [r for r in self.get('/api/feed', account_id=self.chk)['rows']
               if r['description'] == 'DEPOSIT ACME'][0]
        self.assertTrue(row['matches'], 'no way to match the deposit to the undeposited payment')


# ============================================================ malformed input must not 500

class Malformed(ARCase):
    def status(self, method, path, body):
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()):
            return self.app.handle(method, path, {}, body)[0]

    def inv_body(self, **kw):
        b = {'customer_id': self.c1, 'date': '2026-01-10',
             'lines': [{'description': 'W', 'qty': 1, 'rate': 10, 'account_id': self.sales}]}
        b.update(kw)
        return b

    def check(self, method, path, bodies):
        bad = []
        for name, body in bodies:
            st = self.status(method, path, body)
            if st >= 500 or st == 200:
                bad.append('%s -> %s' % (name, st))
        self.assertEqual(bad, [])

    def test_invoice_garbage_fields_are_400(self):
        line = {'description': 'W', 'qty': 1, 'rate': 10, 'account_id': self.sales}
        self.check('POST', '/api/invoices', [
            ('empty body', {}),
            ('customer str', self.inv_body(customer_id='abc')),
            ('customer list', self.inv_body(customer_id=[1])),
            ('date garbage', self.inv_body(date='not-a-date')),
            ('date int', self.inv_body(date=20260110)),
            ('due garbage', self.inv_body(due_date='soon')),
            ('qty text', self.inv_body(lines=[dict(line, qty='two')])),
            ('rate text', self.inv_body(lines=[dict(line, rate='ten')])),
            ('amount text', self.inv_body(lines=[dict(line, amount='ten')])),
            ('account text', self.inv_body(lines=[dict(line, account_id='sales')])),
            ('lines missing', self.inv_body(lines=None)),
        ])
        self.assertEqual(self.get('/api/invoices'), [])

    def test_invoice_lines_wrong_shape(self):
        """BUG: 'lines' that is not a list of objects crashes with HTTP 500."""
        self.check('POST', '/api/invoices', [
            ('lines string', self.inv_body(lines='abc')),
            ('lines list of str', self.inv_body(lines=['abc'])),
            ('lines list of None', self.inv_body(lines=[None])),
            ('lines number', self.inv_body(lines=5)),
            ('lines dict', self.inv_body(lines={'a': 1})),
        ])

    def test_invoice_nan_and_infinite_numbers(self):
        """BUG: qty/rate of NaN or Infinity crashes with HTTP 500 (decimal.InvalidOperation)."""
        line = {'description': 'W', 'qty': 1, 'rate': 10, 'account_id': self.sales}
        self.check('POST', '/api/invoices', [
            ('qty NaN', self.inv_body(lines=[dict(line, qty='NaN')])),
            ('rate NaN', self.inv_body(lines=[dict(line, rate='NaN')])),
            ('rate Infinity', self.inv_body(lines=[dict(line, rate='Infinity')])),
            ('qty float nan', self.inv_body(lines=[dict(line, qty=float('nan'))])),
            ('rate float inf', self.inv_body(lines=[dict(line, rate=float('inf'))])),
        ])

    def test_invoice_huge_numbers(self):
        """BUG: absurdly large qty/rate/amount crashes with HTTP 500 (quantize / SQLite integer overflow)."""
        line = {'description': 'W', 'qty': 1, 'rate': 10, 'account_id': self.sales}
        self.check('POST', '/api/invoices', [
            ('rate 1e30', self.inv_body(lines=[dict(line, rate='1e30')])),
            ('qty x rate 1e20', self.inv_body(lines=[dict(line, qty=1e10, rate=1e10)])),
            ('amount 1e20', self.inv_body(lines=[dict(line, amount='1e20')])),
        ])

    def test_payment_huge_amount(self):
        """BUG: absurdly large payment amount crashes with HTTP 500 (SQLite integer overflow)."""
        self.check('POST', '/api/payments', [
            ('amount 1e20', {'customer_id': self.c1, 'date': '2026-01-10', 'amount': '1e20'}),
        ])

    def test_payment_garbage_fields_are_400(self):
        inv = self.inv(100)
        base = {'customer_id': self.c1, 'date': '2026-01-10', 'amount': 10}
        self.check('POST', '/api/payments', [
            ('empty body', {}),
            ('customer str', dict(base, customer_id='abc')),
            ('date garbage', dict(base, date='x')),
            ('amount str', dict(base, amount='ten')),
            ('app invoice str', dict(base, applications=[{'invoice_id': 'x', 'amount': 10}])),
            ('app invoice missing', dict(base, applications=[{'amount': 10}])),
            ('app amount str', dict(base, applications=[{'invoice_id': inv['id'], 'amount': 'x'}])),
            ('app amount NaN', dict(base, applications=[{'invoice_id': inv['id'], 'amount': 'NaN'}])),
            ('deposit list', dict(base, deposit_account_id=[1])),
        ])
        self.assertEqual(self.get('/api/payments'), [])

    def test_payment_unknown_invoice_is_client_error(self):
        st = self.status('POST', '/api/payments', {'customer_id': self.c1, 'date': '2026-01-10', 'amount': 10,
                                                   'applications': [{'invoice_id': 9999, 'amount': 10}]})
        self.assertIn(st, (400, 404))
        self.assertEqual(self.get('/api/payments'), [])

    def test_payment_applications_wrong_shape(self):
        """BUG: 'applications' that is not a list of objects crashes with HTTP 500."""
        base = {'customer_id': self.c1, 'date': '2026-01-10', 'amount': 10}
        self.check('POST', '/api/payments', [
            ('apps string', dict(base, applications='abc')),
            ('apps list of int', dict(base, applications=[1])),
            ('apps list of None', dict(base, applications=[None])),
            ('apps number', dict(base, applications=5)),
            ('apps dict', dict(base, applications={'invoice_id': 1})),
        ])

    def test_failed_requests_leave_no_trace(self):
        self.inv(100)
        before = (self.bs('Accounts Receivable'), len(self.get('/api/invoices')), len(self.get('/api/payments')))
        self.status('POST', '/api/invoices', self.inv_body(lines='abc'))
        self.status('POST', '/api/payments', {'customer_id': self.c1, 'date': '2026-01-10', 'amount': 10,
                                              'applications': [None]})
        self.pay(500, applications=[{'invoice_id': 1, 'amount': 500}], expect=400)
        after = (self.bs('Accounts Receivable'), len(self.get('/api/invoices')), len(self.get('/api/payments')))
        self.assertEqual(before, after)
        self.assert_ties()


if __name__ == '__main__':
    unittest.main()
