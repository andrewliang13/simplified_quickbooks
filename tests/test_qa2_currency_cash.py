"""QA round 2: multi-currency and cash-basis reports."""
import time
import unittest

from helpers import ApiCase


class Base(ApiCase):
    def setUp(self):
        super().setUp()
        self.cust = self.post('/api/customers', {'name': 'Acme'})['id']
        self.vend = self.post('/api/vendors', {'name': 'Supplier'})['id']
        self.eu = self.post('/api/customers', {'name': 'Euro GmbH', 'currency': 'EUR'})['id']
        self.euv = self.post('/api/vendors', {'name': 'Euro Parts', 'currency': 'EUR'})['id']
        self.chk = self.acct('Checking')
        self.sales = self.acct('Sales')

    # ---- helpers
    def inv(self, cid, amount, date='2026-01-10', rate=None, **kw):
        body = {'customer_id': cid, 'date': date, 'lines': [{'amount': amount, 'account_id': self.sales}]}
        if rate is not None:
            body['exchange_rate'] = rate
        body.update(kw)
        return self.post('/api/invoices', body)

    def bill(self, vid, amount, date='2026-01-10', rate=None, **kw):
        body = {'vendor_id': vid, 'date': date,
                'lines': [{'amount': amount, 'account_id': self.acct('Repairs and Maintenance')}]}
        if rate is not None:
            body['exchange_rate'] = rate
        body.update(kw)
        return self.post('/api/bills', body)

    def pay(self, cid, amount, date, rate=None, apps=None, expect=200, **kw):
        body = {'customer_id': cid, 'date': date, 'amount': amount, 'deposit_account_id': self.chk}
        if rate is not None:
            body['exchange_rate'] = rate
        if apps is not None:
            body['applications'] = [{'invoice_id': i, 'amount': a} for i, a in apps]
        body.update(kw)
        return self.post('/api/payments', body, expect=expect)

    def bpay(self, vid, amount, date, rate=None, apps=None, expect=200):
        body = {'vendor_id': vid, 'date': date, 'amount': amount, 'pay_account_id': self.chk}
        if rate is not None:
            body['exchange_rate'] = rate
        if apps is not None:
            body['applications'] = [{'bill_id': i, 'amount': a} for i, a in apps]
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

    def balanced(self, end='2026-12-31', **q):
        r = self.get('/api/reports/balance_sheet', end=end, **q)
        self.assertEqual(self.val(r, 'TOTAL ASSETS'), self.val(r, 'TOTAL LIABILITIES AND EQUITY'), (end, q))
        tb = self.get('/api/reports/trial_balance', end=end, **q)
        t = self.row(tb, 'TOTAL')['values']
        self.assertEqual(t[0], t[1], (end, q))

    def all_cells_drill(self, name, **q):
        rep = self.get('/api/reports/' + name, **q)
        for row in rep['rows']:
            for i, d in enumerate(row.get('drill') or []):
                if d and 'segments' in d:
                    self.assertEqual(self.post('/api/reports/drill', d)['total'], row['values'][i],
                                     '%s %s col %d %s' % (name, row['label'], i, q))
                elif d and 'items' in d:
                    self.assertEqual(round(sum(x['amount'] for x in d['items']), 2), row['values'][i])

    def ar_ties(self, end='2026-12-31'):
        """Balance sheet A/R == sum of customer balances == A/R aging total (accrual)."""
        ar = self.bs('Accounts Receivable', end=end)
        aging = self.get('/api/reports/ar_aging', end=end)
        tot = self.val(aging, 'TOTAL', 5)
        self.assertEqual(ar, tot, 'BS A/R vs aging at %s' % end)
        if end == '2026-12-31':
            custs = round(sum(c['balance'] for c in self.get('/api/customers', include_inactive=1)), 2)
            self.assertEqual(ar, custs, 'BS A/R vs customer balances')
        return ar

    def ap_ties(self, end='2026-12-31'):
        ap = self.bs('Accounts Payable', end=end)
        aging = self.get('/api/reports/ap_aging', end=end)
        self.assertEqual(ap, self.val(aging, 'TOTAL', 5), 'BS A/P vs aging at %s' % end)
        if end == '2026-12-31':
            vs = round(sum(v['balance'] for v in self.get('/api/vendors', include_inactive=1)), 2)
            self.assertEqual(ap, vs)
        return ap


# ====================================================================== multi-currency

class FxSettlement(Base):
    def test_odd_rate_partial_payments_settle_exactly(self):
        i = self.inv(self.eu, 1000.01, rate='0.912345')
        self.assertEqual(i['total_home'], 912.35)
        homes = 0
        for amt, rate, d in ((333.33, '0.95', '2026-02-01'), (333.33, '0.881111', '2026-02-15'),
                             (333.35, '1.0123', '2026-03-01')):
            p = self.pay(self.eu, amt, d, rate=rate)
            homes += round(amt * float(rate), 2)
            self.assertEqual(p['unapplied'], 0.0)
        doc = self.get('/api/invoices/%d' % i['id'])
        self.assertEqual((doc['status'], doc['balance'], doc['balance_home']), ('Paid', 0.0, 0.0))
        self.assertEqual(self.ar_ties(), 0.0)
        bank = self.bs('Checking')
        # FX gain = cash received - invoice home value
        self.assertEqual(round(-self.pl('Exchange Gain or Loss'), 2), round(bank - 912.35, 2))
        self.assertEqual(self.pl('Sales'), 912.35)
        for d in ('2026-02-01', '2026-02-20', '2026-03-01'):
            self.ar_ties(d)
            self.balanced(d)

    def test_large_rate_jpy_style(self):
        self.put('/api/customers/%d' % self.eu, {'currency': 'EUR'})
        jp = self.post('/api/customers', {'name': 'Nippon', 'currency': 'JPY'})['id']
        i = self.inv(jp, 12345.67, rate='147.3')
        self.assertEqual(i['total_home'], round(12345.67 * 147.3, 2))
        self.pay(jp, 0.01, '2026-02-01', rate='150')
        self.pay(jp, 12345.66, '2026-02-02', rate='146.9')
        self.assertEqual(self.ar_ties(), 0.0)
        self.balanced()

    def test_purchase_side_mirror(self):
        b = self.bill(self.euv, 999.99, rate='0.912345')
        self.assertEqual(b['total_home'], 912.34)
        self.bpay(self.euv, 400, '2026-02-01', rate='0.95')
        self.assertEqual(self.ap_ties(), round(912.34 - 364.94, 2))   # 400 * 0.912345 = 364.938 -> 364.94
        self.bpay(self.euv, 599.99, '2026-03-01', rate='0.9')
        self.assertEqual(self.ap_ties(), 0.0)
        bank = -self.bs('Checking')            # paid out 380 + 539.99
        self.assertEqual(bank, 919.99)
        # paid more home than booked -> loss (positive expense)
        self.assertEqual(self.pl('Exchange Gain or Loss'), round(919.99 - 912.34, 2))
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['status'], 'Paid')
        self.balanced()

    def test_overpayment_credit_applied_later(self):
        i1 = self.inv(self.eu, 100, rate='1.1')
        p = self.pay(self.eu, 150, '2026-02-01', rate='1.2')
        self.assertEqual(p['unapplied'], 50.0)
        # 100 EUR at 1.1 off A/R, 50 EUR credit at 1.2 = -60
        self.assertEqual(self.ar_ties(), -60.0)
        self.assertEqual(self.pl('Exchange Gain or Loss'), -10.0)
        i2 = self.inv(self.eu, 50, date='2026-03-01', rate='1.3')
        self.assertEqual(self.ar_ties(), 5.0)          # 65 - 60
        self.put('/api/payments/%d' % p['id'], {'customer_id': self.eu, 'date': '2026-02-01', 'amount': 150,
                                                'deposit_account_id': self.chk,
                                                'applications': [{'invoice_id': i1['id'], 'amount': 100},
                                                                 {'invoice_id': i2['id'], 'amount': 50}]})
        self.assertEqual(self.get('/api/invoices/%d' % i2['id'])['status'], 'Paid')
        self.assertEqual(self.ar_ties(), 0.0)
        self.assertEqual(self.pl('Sales'), 175.0)
        self.assertEqual(self.pl('Exchange Gain or Loss'), -5.0)      # bank 180 - 175
        self.balanced()
        for basis in ('cash', 'accrual'):
            self.balanced(basis=basis)

    def test_delete_and_edit_payments_reopen_exactly(self):
        i = self.inv(self.eu, 100, rate='1.123457')
        p1 = self.pay(self.eu, 33.33, '2026-02-01', rate='1.2')
        p2 = self.pay(self.eu, 66.67, '2026-02-02', rate='1.0')
        self.assertEqual(self.ar_ties(), 0.0)
        self.delete('/api/payments/%d' % p1['id'])
        doc = self.get('/api/invoices/%d' % i['id'])
        self.assertEqual(doc['balance'], 33.33)
        self.assertEqual(self.ar_ties(), doc['balance_home'])
        self.pay(self.eu, 33.33, '2026-02-03', rate='1.3')
        self.assertEqual(self.ar_ties(), 0.0)
        # edit p2 down, then back up
        self.put('/api/payments/%d' % p2['id'], {'customer_id': self.eu, 'date': '2026-02-02', 'amount': 10,
                                                 'deposit_account_id': self.chk,
                                                 'applications': [{'invoice_id': i['id'], 'amount': 10}]})
        self.assertEqual(self.ar_ties(), self.get('/api/invoices/%d' % i['id'])['balance_home'])
        self.put('/api/payments/%d' % p2['id'], {'customer_id': self.eu, 'date': '2026-02-02', 'amount': 66.67,
                                                 'deposit_account_id': self.chk,
                                                 'applications': [{'invoice_id': i['id'], 'amount': 66.67}]})
        self.assertEqual(self.ar_ties(), 0.0)
        self.balanced()

    def test_edit_fully_paid_foreign_invoice_leaves_no_home_residue(self):
        """Lines are converted one by one, so re-splitting a paid invoice changes total_home by a cent while
        the stored home amounts of its payments stay as they were."""
        i = self.post('/api/invoices', {'customer_id': self.eu, 'date': '2026-01-10', 'exchange_rate': '1.5',
                                        'lines': [{'amount': 0.01, 'account_id': self.sales},
                                                  {'amount': 99.99, 'account_id': self.sales}]})
        self.pay(self.eu, 100, '2026-02-01', rate='1.5')
        self.assertEqual(self.ar_ties(), 0.0)
        self.put('/api/invoices/%d' % i['id'], {'customer_id': self.eu, 'date': '2026-01-10',
                                                'lines': [{'amount': 100, 'account_id': self.sales}]})
        doc = self.get('/api/invoices/%d' % i['id'])
        self.assertEqual(doc['status'], 'Paid')
        self.assertEqual(doc['balance_home'], 0.0, 'paid invoice still has a home balance')
        self.assertEqual(self.ar_ties(), 0.0, 'stray cent left on A/R')

    def test_rate_locked_after_payment_and_changes_before(self):
        i = self.inv(self.eu, 100, rate='1.1')
        body = {'customer_id': self.eu, 'date': '2026-01-10', 'exchange_rate': '1.2',
                'lines': [{'amount': 100, 'account_id': self.sales}]}
        self.assertEqual(self.put('/api/invoices/%d' % i['id'], body)['total_home'], 120.0)
        self.assertEqual(self.ar_ties(), 120.0)
        self.pay(self.eu, 10, '2026-02-01', rate='1.3')
        body['exchange_rate'] = '1.25'
        self.put('/api/invoices/%d' % i['id'], body, expect=400)
        body['exchange_rate'] = '1.20'          # same value, different spelling: allowed
        self.put('/api/invoices/%d' % i['id'], body)
        self.delete('/api/invoices/%d' % i['id'], expect=409)
        self.ar_ties()

    def test_switch_home_invoice_to_foreign_customer_requires_rate(self):
        i = self.inv(self.cust, 100)
        r = self.app.handle('PUT', '/api/invoices/%d' % i['id'], {},
                            {'customer_id': self.eu, 'date': '2026-01-10',
                             'lines': [{'amount': 100, 'account_id': self.sales}]})
        self.assertEqual(r[0], 400, 'EUR invoice silently got rate 1 from the old USD invoice: %s' % (r,))

    def test_foreign_invoice_to_home_customer(self):
        i = self.inv(self.eu, 100, rate='1.1')
        d = self.put('/api/invoices/%d' % i['id'], {'customer_id': self.cust, 'date': '2026-01-10',
                                                    'lines': [{'amount': 100, 'account_id': self.sales}]})
        self.assertEqual((d['currency'], d['exchange_rate'], d['total_home']), ('USD', 1.0, 100.0))
        self.ar_ties()

    def test_payment_currency_and_rate_rules(self):
        self.inv(self.eu, 100, rate='1.1')
        self.pay(self.eu, 10, '2026-02-01', expect=400)            # rate required
        for bad in ('0', '-1', 'abc', 'NaN', 'Infinity', '1e400', {'x': 1}, [1], True):
            self.pay(self.eu, 10, '2026-02-01', rate=bad, expect=400)
        p = self.pay(self.cust, 10, '2026-02-01', rate='5')       # ignored for home currency
        self.assertEqual(p['exchange_rate'], 1.0)
        r = self.app.handle('POST', '/api/invoices', {}, {'customer_id': self.eu, 'date': '2026-01-10',
                                                          'exchange_rate': '1.1', 'currency': 'USD',
                                                          'lines': [{'amount': 1, 'account_id': self.sales}]})
        self.assertEqual(r[0], 400)
        self.balanced()


class CurrencyValidation(Base):
    def test_name_currency_codes(self):
        for bad in ('EURO', 'E1R', 'EU', ['EUR'], {'c': 1}, True):
            self.post('/api/customers', {'name': 'X%s' % len(str(bad)), 'currency': bad}, expect=400)
        c = self.post('/api/vendors', {'name': 'Lower', 'currency': ' cad '})
        self.assertEqual(c['currency'], 'CAD')
        c = self.post('/api/customers', {'name': 'Home', 'currency': 'usd'})
        self.assertEqual(c['currency'], 'USD')     # kept as chosen, even if the home currency changes later
        # currency locked once used
        self.inv(self.eu, 1, rate='1')
        self.put('/api/customers/%d' % self.eu, {'currency': 'GBP'}, expect=400)
        self.put('/api/customers/%d' % self.eu, {'name': 'Euro AG'})     # other edits still fine

    def test_home_currency_rules(self):
        self.assertEqual(self.get('/api/meta')['home_currency'], 'USD')
        for bad in ('US', 'USDX', '123', None, ''):
            self.put('/api/settings', {'home_currency': bad}, expect=400)
        self.put('/api/settings', {'home_currency': 'cad'})
        self.assertEqual(self.get('/api/meta')['home_currency'], 'CAD')
        self.inv(self.cust, 10)
        self.put('/api/settings', {'home_currency': 'USD'}, expect=400)
        self.put('/api/settings', {'home_currency': 'CAD'})

    def test_home_currency_accepts_non_ascii_letters(self):
        r = self.app.handle('PUT', '/api/settings', {}, {'home_currency': 'ÄÖÜ'})
        self.assertEqual(r[0], 400, 'non-ASCII home currency accepted (names reject it): %s' % (r,))

    def test_customer_set_to_usd_keeps_usd_when_home_changes(self):
        c = self.post('/api/customers', {'name': 'Yankee', 'currency': 'USD'})
        self.put('/api/settings', {'home_currency': 'EUR'})
        self.assertEqual(self.get('/api/customers/%d' % c['id'])['currency'], 'USD',
                         'a customer explicitly in USD silently became a home-currency (EUR) customer')


class FxBankFeed(Base):
    def test_feed_never_offers_or_matches_foreign_docs(self):
        i = self.inv(self.eu, 100, rate='1.1')
        b = self.bill(self.euv, 50, rate='1.1')
        self.post('/api/feed/import', {'account_id': self.chk,
                                       'content': 'Date,Description,Amount\n01/25/2026,EURO GMBH,100.00\n'
                                                  '01/25/2026,EURO GMBH,110.00\n01/26/2026,EURO PARTS,-50.00\n'
                                                  '01/26/2026,EURO PARTS,-55.00\n'})
        rows = self.get('/api/feed', account_id=self.chk)['rows']
        for r in rows:
            self.assertFalse([m for m in r['matches'] if m['kind'] in ('invoice', 'bill')], r)
        dep = [r for r in rows if r['amount'] == 100.0][0]
        self.post('/api/feed/%d/match' % dep['id'], {'invoice_id': i['id']}, expect=400)
        wd = [r for r in rows if r['amount'] == -50.0][0]
        self.post('/api/feed/%d/match' % wd['id'], {'bill_id': b['id']}, expect=400)
        self.assertEqual(self.get('/api/invoices/%d' % i['id'])['balance'], 100.0)

    def test_undeposited_suggestion_uses_home_amount(self):
        self.inv(self.eu, 100, rate='1.1')
        self.post('/api/payments', {'customer_id': self.eu, 'date': '2026-02-01', 'amount': 100,
                                    'exchange_rate': '1.1'})           # to Undeposited Funds: 110.00 home
        self.post('/api/feed/import', {'account_id': self.chk,
                                       'content': 'Date,Description,Amount\n02/03/2026,DEPOSIT,100.00\n'})
        row = self.get('/api/feed', account_id=self.chk)['rows'][0]
        labels = [m['label'] for m in row['matches'] if m['kind'] == 'undeposited']
        self.assertEqual(labels, [], 'a 100.00 deposit is suggested as the 110.00 (100 EUR) payment: %s' % labels)


class FxMalformed(Base):
    def test_no_500s(self):
        i = self.inv(self.eu, 100, rate='1.1')
        bodies = [
            ('POST', '/api/payments', {'customer_id': self.eu, 'amount': 'x', 'exchange_rate': '1'}),
            ('POST', '/api/payments', {'customer_id': self.eu, 'amount': 5, 'exchange_rate': '1',
                                       'applications': 'all'}),
            ('POST', '/api/payments', {'customer_id': self.eu, 'amount': 5, 'exchange_rate': '1',
                                       'applications': [{'invoice_id': 'x', 'amount': 1}]}),
            ('POST', '/api/payments', {'customer_id': self.eu, 'amount': 5, 'exchange_rate': '1',
                                       'applications': [{'invoice_id': i['id'], 'amount': -1}]}),
            ('POST', '/api/payments', {'customer_id': self.eu, 'amount': 5, 'exchange_rate': '1',
                                       'applications': [{'invoice_id': 99999, 'amount': 1}]}),
            ('POST', '/api/payments', {'customer_id': [1], 'amount': 5}),
            ('POST', '/api/invoices', {'customer_id': self.eu, 'exchange_rate': [], 'lines': [{'amount': 1}]}),
            ('POST', '/api/invoices', {'customer_id': self.eu, 'exchange_rate': '0.0000001',
                                       'lines': [{'amount': 1, 'account_id': self.sales}]}),
            ('POST', '/api/invoices', {'customer_id': self.eu, 'currency': ['EUR'], 'exchange_rate': '1',
                                       'lines': [{'amount': 1, 'account_id': self.sales}]}),
            ('POST', '/api/bill_payments', {'vendor_id': self.euv, 'amount': 5, 'pay_account_id': self.chk,
                                            'exchange_rate': {}}),
            ('PUT', '/api/settings', {'home_currency': ['USD']}),
            ('PUT', '/api/customers/%d' % self.eu, {'currency': 5}),
            ('POST', '/api/reports/drill', {'segments': [], 'basis': 'weird'}),
            ('POST', '/api/reports/drill', {'segments': [{'accounts': [self.sales]}], 'basis': None}),
        ]
        for m, path, body in bodies:
            st, data = self.app.handle(m, path, {}, body)
            self.assertLess(st, 500, '%s %s %s -> %s %s' % (m, path, body, st, data))
        for q in ({'basis': 'CASH '}, {'basis': 'x'}, {'basis': ''}):
            st, data = self.app.handle('GET', '/api/reports/profit_loss', q, None)
            self.assertLess(st, 500, (q, data))

    def test_drill_non_string_basis_is_400_not_500(self):
        for basis in (1, ['cash'], {'b': 1}, True):
            st, data = self.app.handle('POST', '/api/reports/drill', {}, {'segments': [], 'basis': basis})
            self.assertEqual(st, 400, 'basis=%r -> %s %s' % (basis, st, data))


# ====================================================================== cash basis

class CashBasisQA(Base):
    def scenario(self):
        tax = self.post('/api/tax_rates', {'name': 'T 8.25', 'rate': '8.25'})
        a = self.post('/api/invoices', {'customer_id': self.cust, 'date': '2026-01-10', 'tax_rate_id': tax['id'],
                                        'lines': [{'amount': 333.33, 'account_id': self.sales},
                                                  {'amount': 166.67, 'account_id': self.acct('Service Income'),
                                                   'taxable': False}]})
        self.pay(self.cust, 200, '2026-02-15')
        self.pay(self.cust, 100, '2026-04-03')
        self.inv(self.cust, 75, date='2026-03-05')                       # unpaid
        b = self.bill(self.vend, 400, date='2026-01-20')
        self.bpay(self.vend, 150, '2026-03-31')
        e = self.inv(self.eu, 500, date='2026-02-01', rate='1.0876')
        self.pay(self.eu, 250, '2026-03-15', rate='1.12')
        self.pay(self.eu, 250, '2026-05-20', rate='1.05')
        self.pay(self.cust, 90, '2026-06-01', apps=[])                   # unapplied
        later = self.inv(self.cust, 60, date='2026-08-01')
        self.pay(self.cust, 60, '2026-07-15', apps=[(later['id'], 60)])  # applied to a later-dated invoice
        self.journal('2026-06-30', ('Accounts Receivable', 25, 0, self.cust), ('Sales', 0, 25))
        self.journal('2026-09-30', ('Rent and Lease', 1200, 0), ('Checking', 0, 1200))
        self.bill(self.euv, 80, date='2026-10-01', rate='1.1')
        self.bpay(self.euv, 80, '2026-12-20', rate='1.07')
        return a, b, e

    def test_all_reports_consistent_and_drill_every_cell(self):
        self.scenario()
        for basis in ('cash', 'accrual'):
            for d in ('2026-01-31', '2026-02-15', '2026-03-31', '2026-06-30', '2026-07-20', '2026-12-31'):
                self.balanced(end=d, basis=basis)
            for cols, cmp_ in (('month', None), ('quarter', 'pp'), (None, 'py'), ('month', 'pp')):
                q = dict(start='2026-01-01', end='2026-12-31', basis=basis)
                if cols:
                    q['columns'] = cols
                if cmp_:
                    q['compare'] = cmp_
                for name in ('profit_loss', 'balance_sheet', 'cash_flow'):
                    self.all_cells_drill(name, **q)
            # P&L net income == BS net income; CF end cash == BS bank in every month
            pl = self.get('/api/reports/profit_loss', start='2026-01-01', end='2026-12-31', basis=basis,
                          columns='month')
            bs = self.get('/api/reports/balance_sheet', start='2026-01-01', end='2026-12-31', basis=basis,
                          columns='month')
            cf = self.get('/api/reports/cash_flow', start='2026-01-01', end='2026-12-31', basis=basis,
                          columns='month')
            ni = self.row(pl, 'Net Income')['values']
            run = 0
            for k in range(12):
                run = round(run + ni[k], 2)
                self.assertEqual(self.row(bs, 'Net Income')['values'][k], run, (basis, k))
                self.assertEqual(self.row(cf, 'CASH AT END OF PERIOD')['values'][k],
                                 self.row(bs, 'Total Bank Accounts')['values'][k], (basis, k))
            self.assertEqual(self.row(cf, 'Net Income')['values'][:12], ni[:12])

    def test_trial_balance_drill_on_cash_basis(self):
        self.scenario()
        tb = self.get('/api/reports/trial_balance', end='2026-12-31', basis='cash')
        for r in tb['rows']:
            for k, d in enumerate(r['drill']):
                if d:
                    self.assertEqual(self.post('/api/reports/drill', d)['total'], r['values'][k],
                                     'cash TB %s drill (drill object carries no basis)' % r['label'])

    def test_cash_totals_match_accrual_once_everything_is_paid(self):
        a, b, e = self.scenario()
        cash = self.get('/api/reports/profit_loss', start='2026-01-01', end='2026-12-31', basis='cash')
        self.assertEqual(self.val(cash, 'Repairs and Maintenance'), round(150 + 88.0, 2))   # 80 EUR at 1.1
        # the EUR invoice is fully paid: income at the invoice rate, the rest is FX
        # Acme invoice a: 300 of 527.50 paid -> 300/536.25 of each line
        exp_sales = round(333.33 * 300 / 527.50 + 543.80 + 60 + 25, 2)
        self.assertAlmostEqual(self.val(cash, 'Sales'), exp_sales, delta=0.011)
        self.assertEqual(self.bs('Accounts Receivable', basis='cash'), round(-90 + 25, 2))
        self.assertEqual(self.bs('Accounts Payable', basis='cash'), 0.0)

    def test_installments_leave_no_cents_on_tax_or_income(self):
        """A fully paid invoice must show exactly its own line amounts on cash basis."""
        tax = self.post('/api/tax_rates', {'name': 'T7', 'rate': '7'})
        self.post('/api/invoices', {'customer_id': self.cust, 'date': '2026-01-05', 'tax_rate_id': tax['id'],
                                    'lines': [{'amount': 0.10, 'account_id': self.acct('Service Income')},
                                              {'amount': 99.90, 'account_id': self.sales}]})
        # total 107.00: tax 7.00 (0.10 + 99.90 taxable)
        for d in ('2026-02-01', '2026-02-02', '2026-02-03'):
            self.pay(self.cust, 35.66, d)
        self.pay(self.cust, 0.02, '2026-02-04')
        self.assertEqual(self.bs('Accounts Receivable'), 0.0)
        self.post('/api/sales_tax/payments', {'date': '2026-03-01', 'amount': 7, 'bank_account_id': self.chk})
        self.assertEqual(self.bs('Sales Tax Payable'), 0.0)
        self.assertEqual(self.bs('Sales Tax Payable', basis='cash'), 0.0,
                         'tax fully collected and remitted, cash-basis Sales Tax Payable keeps a stray cent')
        cash = self.get('/api/reports/profit_loss', start='2026-01-01', end='2026-12-31', basis='cash')
        self.assertEqual(self.val(cash, 'Service Income'), 0.10)
        self.assertEqual(self.val(cash, 'Sales'), 99.90)

    def test_installment_rounding_across_many_payments(self):
        self.post('/api/invoices', {'customer_id': self.cust, 'date': '2026-01-05',
                                    'lines': [{'amount': 0.10, 'account_id': self.acct('Service Income')},
                                              {'amount': 9.90, 'account_id': self.sales}]})
        for k in range(3):
            self.pay(self.cust, 3.33, '2026-02-0%d' % (k + 1))
        self.pay(self.cust, 0.01, '2026-02-05')
        cash = self.get('/api/reports/profit_loss', start='2026-01-01', end='2026-12-31', basis='cash')
        self.assertEqual((self.val(cash, 'Service Income'), self.val(cash, 'Sales')), (0.10, 9.90),
                         'fully paid invoice: cash-basis line totals drift from the invoice lines')

    def test_inventory_on_cash_basis(self):
        w = self.post('/api/items', {'name': 'Widget', 'type': 'inventory', 'sales_price': 25, 'purchase_cost': 10,
                                     'qty_on_hand': 10, 'as_of': '2025-12-31'})
        inv = self.post('/api/invoices', {'customer_id': self.cust, 'date': '2026-01-10',
                                          'lines': [{'item_id': w['id'], 'qty': 4}]})
        self.assertEqual(self.pl('Cost of Goods Sold', basis='cash'), 0.0)
        self.assertEqual(self.bs('Inventory Asset', basis='cash'), 100.0)
        self.pay(self.cust, 50, '2026-02-01')
        self.assertEqual(self.pl('Cost of Goods Sold', basis='cash'), 20.0)
        self.assertEqual(self.pl('Sales', basis='cash'), 50.0)
        self.assertEqual(self.bs('Inventory Asset', basis='cash'), 80.0)
        self.pay(self.cust, 50, '2026-03-01')
        self.assertEqual(self.pl('Cost of Goods Sold', basis='cash'), 40.0)
        self.assertEqual(self.get('/api/invoices/%d' % inv['id'])['status'], 'Paid')
        for basis in ('cash', 'accrual'):
            self.balanced(basis=basis)
            self.all_cells_drill('profit_loss', start='2026-01-01', end='2026-12-31', basis=basis, columns='month')

    def test_payment_before_invoice_date(self):
        later = self.inv(self.cust, 60, date='2026-08-01')
        self.pay(self.cust, 60, '2026-07-15', apps=[(later['id'], 60)])
        # between payment and invoice: customer credit on accrual, income already on cash
        self.assertEqual(self.ar_ties('2026-07-20'), -60.0)
        self.assertEqual(self.bs('Accounts Receivable', end='2026-07-20', basis='cash'), 0.0)
        self.assertEqual(self.pl('Sales', end='2026-07-31', basis='cash'), 60.0)
        self.assertEqual(self.ar_ties(), 0.0)
        self.balanced('2026-07-20', basis='cash')
        self.balanced('2026-07-20')

    def test_fiscal_year_rollover_cash(self):
        self.put('/api/settings', {'fiscal_year_start_month': 4})
        self.inv(self.cust, 1000, date='2026-02-01')
        self.pay(self.cust, 400, '2026-03-15')
        self.pay(self.cust, 600, '2026-04-15')
        bs = self.get('/api/reports/balance_sheet', end='2026-05-31', basis='cash')
        self.assertEqual(self.val(bs, 'Retained Earnings'), 400.0)
        self.assertEqual(self.val(bs, 'Net Income'), 600.0)
        self.balanced('2026-05-31', basis='cash')
        self.balanced('2026-03-31', basis='cash')
        tb = self.get('/api/reports/trial_balance', end='2026-05-31', basis='cash')
        self.assertEqual(self.val(tb, 'Sales', 1), 600.0)
        self.all_cells_drill('balance_sheet', start='2026-01-01', end='2026-12-31', basis='cash', columns='quarter')
        self.all_cells_drill('profit_loss', start='2025-04-01', end='2027-03-31', basis='cash', columns='year')

    def test_transaction_detail_running_balances_cash(self):
        self.scenario()
        for basis in ('cash', 'accrual'):
            td = self.get('/api/reports/transaction_detail', start='2026-03-01', end='2026-10-31', basis=basis)
            bs = self.get('/api/reports/balance_sheet', end='2026-10-31', basis=basis)
            for g in td['groups']:
                begin = g['beginning_balance'] or 0
                run = begin
                for r in g['rows']:
                    run = round(run + r['amount'], 2)
                    self.assertEqual(r['balance'], run, (basis, g['account']))
                self.assertEqual(g['ending_balance'], run)
                if g['account'] in ('Checking', 'Accounts Receivable', 'Accounts Payable', 'Sales Tax Payable'):
                    self.assertEqual(g['ending_balance'], self.val(bs, g['account']), (basis, g['account']))
            # every cash-basis line with date in range
            for g in td['groups']:
                for r in g['rows']:
                    self.assertTrue('2026-03-01' <= r['date'] <= '2026-10-31')

    def test_performance_few_hundred_docs(self):
        rep = self.acct('Repairs and Maintenance')
        for k in range(150):
            d = '2026-%02d-%02d' % (k % 12 + 1, k % 27 + 1)
            i = self.post('/api/invoices', {'customer_id': self.cust if k % 3 else self.eu, 'date': d,
                                            'exchange_rate': '1.0876',
                                            'lines': [{'amount': 100 + k, 'account_id': self.sales},
                                                      {'amount': 7.77, 'account_id': self.acct('Service Income')}]})
            if k % 2:
                self.pay(self.cust if k % 3 else self.eu, 50, d, rate='1.1', apps=[(i['id'], 50)])
            b = self.post('/api/bills', {'vendor_id': self.vend, 'date': d,
                                         'lines': [{'amount': 40 + k, 'account_id': rep}]})
            if k % 4 == 0:
                self.bpay(self.vend, 40 + k, d, apps=[(b['id'], 40 + k)])
        t = time.time()
        self.get('/api/reports/profit_loss', start='2026-01-01', end='2026-12-31', basis='cash', columns='month',
                 compare='py')
        self.get('/api/reports/balance_sheet', start='2026-01-01', end='2026-12-31', basis='cash', columns='month')
        self.get('/api/reports/transaction_detail', start='2026-01-01', end='2026-12-31', basis='cash')
        self.get('/api/reports/ar_aging', end='2026-12-31')
        self.assertLess(time.time() - t, 5.0)
        self.balanced(basis='cash')
        self.ar_ties()

    def test_bad_basis(self):
        self.get('/api/reports/profit_loss', basis='cash')
        self.call('GET', '/api/reports/profit_loss', expect=400, basis='modified')
        self.post('/api/reports/drill', {'segments': [], 'basis': 'modified'}, expect=400)


if __name__ == '__main__':
    unittest.main()
