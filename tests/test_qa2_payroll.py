"""QA pass 2: simple payroll (employees, paychecks, tax math, ledger, liabilities, reports, locks)."""
import contextlib
import io
import unittest

from helpers import ApiCase


class PayrollBase(ApiCase):
    def setUp(self):
        super().setUp()
        self.chk = self.acct('Checking')

    # -- helpers
    def emp(self, name='Pat', **kw):
        body = {'name': name, 'pay_type': 'salary', 'pay_rate': 1000}
        body.update(kw)
        return self.post('/api/employees', body)

    def check(self, e, date, expect=200, **kw):
        body = {'employee_id': e['id'], 'pay_date': date, 'bank_account_id': self.chk}
        body.update(kw)
        return self.post('/api/paychecks', body, expect=expect)

    def calc(self, e, date, **kw):
        body = {'employee_id': e['id'], 'pay_date': date}
        body.update(kw)
        return self.post('/api/payroll/calculate', body)

    def bs(self, label, end='2027-12-31'):
        r = self.get('/api/reports/balance_sheet', end=end)
        try:
            return self.val(r, label)
        except KeyError:
            return 0.0

    def pl(self, label, start='2026-01-01', end='2026-12-31'):
        r = self.get('/api/reports/profit_loss', start=start, end=end)
        try:
            return self.val(r, label)
        except KeyError:
            return 0.0

    def balanced(self, end='2027-12-31'):
        r = self.get('/api/reports/balance_sheet', end=end)
        self.assertEqual(self.val(r, 'TOTAL ASSETS'), self.val(r, 'TOTAL LIABILITIES AND EQUITY'))
        t = self.row(self.get('/api/reports/trial_balance', end=end), 'TOTAL')['values']
        self.assertEqual(t[0], t[1])

    def lines(self, pc):
        t = self.get('/api/transactions/%d' % pc['txn_id'])
        out = {}
        for l in t['lines']:
            out[l['account']] = round(out.get(l['account'], 0) + l['debit'] - l['credit'], 2)
        return out

    def set_settings(self, **kw):
        return self.put('/api/payroll/settings', kw)


class TaxMath(PayrollBase):
    def test_ss_wage_base_crossed_mid_paycheck(self):
        e = self.emp(pay_rate=20000)
        for m in range(1, 10):                          # 9 x 20,000 = 180,000
            self.check(e, '2026-%02d-15' % m)
        p = self.check(e, '2026-10-15')                 # only 4,500 left under 184,500
        self.assertEqual((p['ss_ee'], p['ss_er']), (279.0, 279.0))
        p = self.check(e, '2026-11-15')
        self.assertEqual((p['ss_ee'], p['ss_er']), (0.0, 0.0))
        total = sum(c['ss_ee'] for c in self.get('/api/paychecks', employee_id=e['id']))
        self.assertEqual(round(total, 2), 11439.0)      # 184,500 x 6.2%

    def test_futa_suta_cap_crossed_mid_paycheck(self):
        e = self.emp(pay_rate=4000)
        p1 = self.check(e, '2026-01-15')
        self.assertEqual((p1['futa'], p1['suta']), (24.0, 108.0))
        p2 = self.check(e, '2026-01-31')                # 3,000 under base
        self.assertEqual((p2['futa'], p2['suta']), (18.0, 81.0))
        p3 = self.check(e, '2026-02-15')
        self.assertEqual((p3['futa'], p3['suta']), (0.0, 0.0))

    def test_additional_medicare_over_threshold_employee_only(self):
        e = self.emp(pay_rate=95000)
        self.check(e, '2026-01-15')
        self.check(e, '2026-02-15')                     # ytd 190,000
        p = self.check(e, '2026-03-15', gross=20000)    # 10,000 over 200,000
        self.assertEqual(p['med_ee'], 290.0 + 90.0)
        self.assertEqual(p['med_er'], 290.0)
        p = self.check(e, '2026-04-15', gross=20000)    # fully over
        self.assertEqual((p['med_ee'], p['med_er']), (290.0 + 180.0, 290.0))

    def test_year_boundary_resets_wage_bases(self):
        e = self.emp(pay_rate=190000)
        p = self.check(e, '2026-12-31')
        self.assertEqual((p['ss_ee'], p['futa']), (11439.0, 42.0))
        p = self.check(e, '2027-01-01', gross=7000)
        self.assertEqual((p['ss_ee'], p['futa'], p['suta'], p['med_ee']), (434.0, 42.0, 189.0, 101.5))
        self.assertEqual(self.calc(e, '2027-01-02', gross=1)['ytd_gross_before'], 7000.0)

    def test_two_paychecks_same_date(self):
        e = self.emp(pay_rate=5000)
        a = self.check(e, '2026-03-01')
        b = self.check(e, '2026-03-01')
        self.assertEqual((a['futa'], b['futa']), (30.0, 12.0))
        # editing the first one: its YTD does not include the later-entered same-day check
        c = self.calc(e, '2026-03-01', gross=5000, paycheck_id=a['id'])
        self.assertEqual((c['ytd_gross_before'], c['futa']), (0.0, 30.0))
        c = self.calc(e, '2026-03-01', gross=5000, paycheck_id=b['id'])
        self.assertEqual((c['ytd_gross_before'], c['futa']), (5000.0, 12.0))

    def test_rounding_half_up(self):
        e = self.emp(pay_type='hourly', pay_rate='17.33', fed_pct='12.5', state_pct='3.07')
        p = self.check(e, '2026-01-15', hours='37.5')   # 649.875 -> 649.88
        self.assertEqual(p['gross'], 649.88)
        self.assertEqual((p['ss_ee'], p['med_ee'], p['fed_wh'], p['state_wh']), (40.29, 9.42, 81.24, 19.95))
        self.assertEqual(p['net'], round(649.88 - 40.29 - 9.42 - 81.24 - 19.95, 2))

    def test_custom_settings_apply(self):
        self.set_settings(ss_wage_base=1000, futa_rate='0.8', suta_rate=0, suta_wage_base=9000)
        e = self.emp(pay_rate=1500)
        p = self.check(e, '2026-01-15')
        self.assertEqual((p['ss_ee'], p['futa'], p['suta']), (62.0, 12.0, 0.0))


class OrderingAndEdits(PayrollBase):
    def test_out_of_order_entry_does_not_recalculate_later_checks(self):
        """Documented behavior (same as QuickBooks): a back-dated paycheck is calculated from the checks
        dated before it; later checks keep the taxes they were saved with, so FUTA can exceed the cap."""
        e = self.emp(pay_rate=7000)
        feb = self.check(e, '2026-02-15')
        jan = self.check(e, '2026-01-15')
        self.assertEqual((feb['futa'], jan['futa']), (42.0, 42.0))
        self.assertEqual(self.get('/api/paychecks/%d' % feb['id'])['futa'], 42.0)
        # recalculating Feb now (as the edit screen would) shows 0
        self.assertEqual(self.calc(e, '2026-02-15', gross=7000, paycheck_id=feb['id'])['futa'], 0.0)

    def test_edit_earlier_paycheck(self):
        e = self.emp(pay_rate=4000)
        p1 = self.check(e, '2026-01-15')
        p2 = self.check(e, '2026-02-15')
        self.assertEqual(p2['futa'], 18.0)
        # raise the first check: it is recalculated against its own YTD (0), excluding itself
        p1b = self.put('/api/paychecks/%d' % p1['id'], {'gross': 8000, 'bank_account_id': self.chk})
        self.assertEqual((p1b['futa'], p1b['suta']), (42.0, 189.0))
        # the later check is not recalculated (QuickBooks recalculates only the edited paycheck)
        self.assertEqual(self.get('/api/paychecks/%d' % p2['id'])['futa'], 18.0)
        # ledger follows the edit
        self.assertEqual(self.lines(p1b)['Payroll Expenses'], 8000.0)
        self.assertEqual(self.pl('Payroll Expenses'), 12000.0)
        self.balanced()

    def test_edit_moves_paycheck_to_new_year_and_employee(self):
        a = self.emp('A', pay_rate=7000)
        b = self.emp('B', pay_rate=7000)
        self.check(a, '2026-01-15')
        p = self.check(a, '2026-02-15')
        self.assertEqual(p['futa'], 0.0)
        p = self.put('/api/paychecks/%d' % p['id'], {'pay_date': '2027-01-15', 'bank_account_id': self.chk})
        self.assertEqual(p['futa'], 42.0)
        p = self.put('/api/paychecks/%d' % p['id'], {'employee_id': b['id'], 'pay_date': '2026-02-15',
                                                       'bank_account_id': self.chk})
        self.assertEqual((p['employee'], p['futa']), ('B', 42.0))
        self.balanced()

    def test_delete_paycheck(self):
        e = self.emp(pay_rate=2000)
        p = self.check(e, '2026-01-15')
        self.assertNotEqual(self.bs('Payroll Liabilities'), 0.0)
        self.delete('/api/employees/%d' % e['id'], expect=409)
        self.delete('/api/paychecks/%d' % p['id'])
        self.call('GET', '/api/paychecks/%d' % p['id'], expect=404)
        self.call('GET', '/api/transactions/%d' % p['txn_id'], expect=404)
        self.assertEqual((self.bs('Payroll Liabilities'), self.bs('Checking'), self.pl('Payroll Expenses')),
                         (0.0, 0.0, 0.0))
        self.delete('/api/employees/%d' % e['id'])

    def test_delete_via_transaction_endpoint_removes_paycheck(self):
        e = self.emp()
        p = self.check(e, '2026-01-15')
        self.delete('/api/transactions/%d' % p['txn_id'])
        self.assertEqual(self.get('/api/paychecks'), [])

    def test_partial_put_keeps_existing_amounts(self):
        """BUG: PUT /api/paychecks/<id> is not a partial update. Changing only the memo fails for an hourly
        check ('bank account is required' / 'Enter hours or gross pay'), and for a salaried one silently
        re-derives gross from the employee's *current* rate and drops amounts typed on the paycheck."""
        e = self.emp(pay_rate=1000)
        p = self.check(e, '2026-01-15', fed_wh=55)
        self.put('/api/employees/%d' % e['id'], {'pay_rate': 1200})
        q = self.call('PUT', '/api/paychecks/%d' % p['id'], {'memo': 'note', 'bank_account_id': self.chk},
                      expect=200)
        self.assertEqual((q['gross'], q['fed_wh'], q['net']), (p['gross'], 55.0, p['net']))

    def test_partial_put_hourly_memo_only(self):
        """BUG (same root cause): memo-only edit of an hourly paycheck is rejected."""
        e = self.emp(pay_type='hourly', pay_rate=20)
        p = self.check(e, '2026-01-15', hours=10)
        q = self.call('PUT', '/api/paychecks/%d' % p['id'], {'memo': 'note'}, expect=200)
        self.assertEqual((q['gross'], q['hours'], q['memo']), (200.0, 10.0, 'note'))


class OverridesAndNet(PayrollBase):
    def test_overrides_win_and_post(self):
        e = self.emp(pay_rate=2000, fed_pct=10, state_pct=5)
        p = self.check(e, '2026-01-15', fed_wh=150, state_wh=0, other_ded='25.50', suta=0, futa='1.23')
        self.assertEqual((p['fed_wh'], p['state_wh'], p['other_ded'], p['suta'], p['futa']),
                         (150.0, 0.0, 25.5, 0.0, 1.23))
        self.assertEqual(p['net'], round(2000 - 150 - 25.5 - 124 - 29, 2))
        self.assertEqual(p['employer_taxes'], round(124 + 29 + 1.23, 2))
        ln = self.lines(p)
        self.assertEqual(ln['Payroll Expenses'], 2000.0)
        self.assertEqual(ln['Payroll Tax Expense'], p['employer_taxes'])
        self.assertEqual(ln['Checking'], -p['net'])
        self.assertEqual(ln['Payroll Liabilities'], -round(150 + 25.5 + 124 + 29 + p['employer_taxes'], 2))
        self.balanced()

    def test_net_never_negative(self):
        e = self.emp(pay_rate=100, fed_pct=60, state_pct=40)    # 100% + FICA > gross
        self.check(e, '2026-01-15', expect=400)
        self.check(e, '2026-01-15', fed_wh=0, state_wh=0, other_ded=200, expect=400)
        self.check(e, '2026-01-15', ss_ee=-1, expect=400)
        self.check(e, '2026-01-15', gross=-5, expect=400)
        self.assertEqual(self.get('/api/paychecks'), [])

    def test_zero_net_and_zero_employer_paycheck_balances(self):
        e = self.emp(pay_rate=100)
        p = self.check(e, '2026-01-15', fed_wh=100, ss_ee=0, med_ee=0, ss_er=0, med_er=0, futa=0, suta=0)
        self.assertEqual((p['net'], p['employer_taxes']), (0.0, 0.0))
        self.assertEqual(self.lines(p), {'Payroll Expenses': 100.0, 'Payroll Liabilities': -100.0})
        self.balanced()

    def test_calculate_preview_reports_negative_net(self):
        """Gap: /api/payroll/calculate returns a negative net (no warning) where save would refuse."""
        e = self.emp(pay_rate=100)
        self.assertLess(self.calc(e, '2026-01-15', fed_wh=500)['net'], 0)


class LedgerAndLiabilities(PayrollBase):
    def test_postings_and_liability_payment(self):
        e = self.emp(pay_type='hourly', pay_rate=25, fed_pct=10, state_pct=4)
        p = self.check(e, '2026-01-15', hours=80)
        self.assertEqual(self.lines(p), {'Payroll Expenses': 2000.0, 'Payroll Tax Expense': 219.0,
                                         'Checking': -1567.0, 'Payroll Liabilities': -652.0})
        self.assertEqual(self.bs('Payroll Liabilities'), 652.0)
        self.post('/api/payroll/liability_payments', {'date': '2026-02-15', 'amount': 400, 'bank_account_id': self.chk})
        self.assertEqual(self.bs('Payroll Liabilities'), 252.0)
        self.assertEqual(self.bs('Payroll Liabilities', end='2026-02-14'), 652.0)
        self.post('/api/payroll/liability_payments', {'date': '2026-02-16', 'amount': 252, 'bank_account_id': self.chk})
        self.assertEqual(self.bs('Payroll Liabilities'), 0.0)
        self.assertEqual(self.bs('Checking'), -2219.0)
        self.assertEqual(len(self.get('/api/payroll/liability_payments')), 2)
        self.assertEqual(self.pl('Net Income'), -2219.0)        # payments do not hit P&L
        self.post('/api/payroll/liability_payments', {'date': '2026-02-16', 'amount': 0, 'bank_account_id': self.chk},
                  expect=400)
        self.post('/api/payroll/liability_payments', {'date': '2026-02-16', 'amount': 5,
                                                      'bank_account_id': self.acct('Payroll Liabilities')}, expect=400)
        self.balanced()

    def test_wage_account_setting(self):
        exp = self.acct('Office Supplies') if self._has('Office Supplies') else None
        self.put('/api/payroll/settings', {'wage_account_id': self.chk}, expect=400)
        if exp:
            s = self.set_settings(wage_account_id=exp)
            self.assertEqual(s['wage_account_id'], exp)
            p = self.check(self.emp(), '2026-01-15')
            self.assertIn('Office Supplies', self.lines(p))

    def _has(self, name):
        try:
            self.acct(name)
            return True
        except KeyError:
            return False

    def test_renamed_payroll_expense_account_still_used(self):
        """BUG: wage account is found by *name* ('Payroll Expenses' / 'Wages'). Renaming the default
        account makes the next paycheck (or even GET /api/payroll/settings) create a new 'Wages' account
        and post wages there."""
        pe = self.acct('Payroll Expenses')
        self.put('/api/accounts/%d' % pe, {'name': 'Salaries and Wages'})
        p = self.check(self.emp(), '2026-01-15')
        self.assertEqual(self.lines(p).get('Salaries and Wages'), 1000.0)
        self.assertEqual(self.get('/api/payroll/settings')['wage_account_id'], pe)


class Reports(PayrollBase):
    def test_payroll_summary_ties_to_ledger(self):
        a = self.emp('Alice', pay_rate=3000, fed_pct=10, state_pct=5)
        b = self.emp('bob', pay_type='hourly', pay_rate=20, fed_pct=8)
        self.check(a, '2026-01-15')
        self.check(a, '2026-02-15', other_ded=40)
        self.check(b, '2026-01-31', hours=40)
        self.check(b, '2025-12-31', hours=40)                    # outside the range
        self.check(a, '2026-04-01')                              # outside the range
        self.put('/api/employees/%d' % b['id'], {'active': False})
        s = self.get('/api/reports/payroll_summary', start='2026-01-01', end='2026-03-31')
        self.assertEqual([r['label'] for r in s['rows']], ['Alice', 'bob', 'TOTAL'])
        tot = self.row(s, 'TOTAL')['values']
        self.assertEqual(tot[0], 6800.0)
        # gross - withholdings = net
        self.assertEqual(round(tot[0] - sum(tot[1:6]), 2), tot[6])
        self.assertEqual(tot[0], self.pl('Payroll Expenses', '2026-01-01', '2026-03-31'))
        self.assertEqual(tot[7], self.pl('Payroll Tax Expense', '2026-01-01', '2026-03-31'))
        for r in s['rows']:
            for i, v in enumerate(r['values']):
                if v:
                    self.assertEqual(self.drill_total(s, r['label'], i), v, '%s col %d' % (r['label'], i))

    def test_payroll_summary_bad_range(self):
        self.call('GET', '/api/reports/payroll_summary', expect=400, start='2026-03-01', end='2026-01-01')
        self.call('GET', '/api/reports/payroll_summary', expect=400, start='nope')

    def test_employee_ytd(self):
        e = self.emp(pay_rate=1000)
        self.check(e, '2026-01-15')
        self.check(e, '2025-12-15')
        j = self.get('/api/employees/%d' % e['id'])
        self.assertEqual(j['ytd_gross'], 1000.0)


class Locks(PayrollBase):
    def test_reconciled_paycheck_is_locked(self):
        e = self.emp(pay_rate=1000)
        p = self.check(e, '2026-01-15')
        rec = self.post('/api/reconciliations', {'account_id': self.chk, 'statement_date': '2026-01-31',
                                                 'ending_balance': -p['net']})
        bank_line = [l for l in self.get('/api/transactions/%d' % p['txn_id'])['lines']
                     if l['account_id'] == self.chk][0]
        self.post('/api/reconciliations/%d/toggle' % rec['id'], {'line_ids': [bank_line['id']], 'cleared': True})
        self.post('/api/reconciliations/%d/finish' % rec['id'])
        self.assertTrue(self.get('/api/paychecks/%d' % p['id'])['reconciled'])
        self.put('/api/paychecks/%d' % p['id'], {'gross': 900, 'bank_account_id': self.chk}, expect=409)
        self.delete('/api/paychecks/%d' % p['id'], expect=409)
        self.delete('/api/transactions/%d' % p['txn_id'], expect=409)
        self.assertEqual(self.get('/api/paychecks/%d' % p['id'])['gross'], 1000.0)
        self.balanced()

    def test_inactive_employee(self):
        e = self.emp(pay_rate=1000)
        p = self.check(e, '2026-01-15')
        self.put('/api/employees/%d' % e['id'], {'active': False})
        self.assertEqual(self.get('/api/employees'), [])
        self.assertEqual(len(self.get('/api/employees', include_inactive=1)), 1)
        self.check(e, '2026-02-15', expect=400)
        q = self.put('/api/paychecks/%d' % p['id'], {'gross': 900, 'bank_account_id': self.chk})
        self.assertEqual(q['gross'], 900.0)
        other = self.emp('Other')
        p2 = self.check(other, '2026-01-20')
        self.put('/api/paychecks/%d' % p2['id'], {'employee_id': e['id'], 'bank_account_id': self.chk}, expect=400)


class Validation(PayrollBase):
    def test_employee_validation(self):
        self.post('/api/employees', {'name': ''}, expect=400)
        self.post('/api/employees', {'name': 'X', 'pay_type': 'weekly'}, expect=400)
        self.post('/api/employees', {'name': 'X', 'pay_rate': -1}, expect=400)
        self.post('/api/employees', {'name': 'X', 'fed_pct': 101}, expect=400)
        self.post('/api/employees', {'name': 'X', 'state_pct': -1}, expect=400)
        self.post('/api/employees', {'name': 'X', 'active': 'maybe'}, expect=400)
        self.emp('Dup')
        self.post('/api/employees', {'name': 'dup'}, expect=409)

    def test_paycheck_validation(self):
        e = self.emp()
        self.check(e, '2026-02-30', expect=400)
        self.check(e, '2026-01-15', period_start='2026-01-10', period_end='2026-01-01', expect=400)
        self.check(e, '2026-01-15', bank_account_id=self.acct('Payroll Liabilities'), expect=400)
        self.check(e, '2026-01-15', bank_account_id=self.acct('Credit Card'), expect=400)
        h = self.emp('Hourly', pay_type='hourly', pay_rate=10)
        self.check(h, '2026-01-15', expect=400)                  # no hours
        self.check(h, '2026-01-15', hours=-1, expect=400)

    def test_settings_validation(self):
        self.put('/api/payroll/settings', {'ss_rate': -1}, expect=400)
        self.put('/api/payroll/settings', {'ss_rate': 101}, expect=400)
        self.put('/api/payroll/settings', {'bogus': 1}, expect=400)
        self.put('/api/payroll/settings', {'home_currency': 'EUR'}, expect=400)
        self.put('/api/payroll/settings', {'futa_wage_base': 'abc'}, expect=400)
        # a failed PUT changes nothing (rolled back)
        self.put('/api/payroll/settings', {'futa_rate': 1, 'ss_rate': 'x'}, expect=400)
        self.assertEqual(self.get('/api/payroll/settings')['futa_rate'], 0.6)

    def test_blank_setting_rejected(self):
        """BUG: a blank value is stored as 0, silently turning off Social Security (or any tax)."""
        self.put('/api/payroll/settings', {'ss_wage_base': ''}, expect=400)
        self.assertEqual(self.get('/api/payroll/settings')['ss_wage_base'], 184500.0)

    def test_no_server_errors_on_malformed_input(self):
        e = self.emp(pay_rate=20)
        pc = self.check(e, '2026-01-15')
        bad = [None, '', [], {}, True, 'abc', -1, 1e308, '1e400', 'NaN', 'Infinity', 2 ** 70, '2026-02-30']
        eps = [('POST', '/api/employees', {'name': 'B'}), ('PUT', '/api/employees/%d' % e['id'], {}),
               ('POST', '/api/payroll/calculate', {'employee_id': e['id'], 'pay_date': '2026-01-15', 'gross': 5}),
               ('POST', '/api/paychecks', {'employee_id': e['id'], 'pay_date': '2026-01-20',
                                           'bank_account_id': self.chk}),
               ('PUT', '/api/paychecks/%d' % pc['id'], {'gross': 5, 'bank_account_id': self.chk}),
               ('POST', '/api/payroll/liability_payments', {'date': '2026-02-01', 'amount': 1,
                                                            'bank_account_id': self.chk}),
               ('PUT', '/api/payroll/settings', {})]
        fields = ['name', 'pay_type', 'pay_rate', 'fed_pct', 'active', 'employee_id', 'pay_date', 'period_start',
                  'hours', 'gross', 'fed_wh', 'other_ded', 'bank_account_id', 'check_no', 'paycheck_id', 'date',
                  'amount', 'ss_rate', 'ss_wage_base', 'wage_account_id']
        with contextlib.redirect_stderr(io.StringIO()):
            for m, p, base in eps:
                for f in fields:
                    for v in bad:
                        b = dict(base)
                        b[f] = v
                        status, _ = self.app.handle(m, p, {}, b)
                        self.assertLess(status, 500, '%s %s %s=%r' % (m, p, f, v))


if __name__ == '__main__':
    unittest.main()
