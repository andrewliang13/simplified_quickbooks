"""QA tests for the financial reports (P&L, Balance Sheet, Cash Flows, Trial Balance, A/R Aging,
Transaction Detail, drill-down, comparison and trending columns).

A realistic multi-year set of books is posted through the API while a *shadow ledger* is kept in the
test (date, account, debit - credit in cents).  Expected figures are computed from the shadow ledger,
independently of sqb/reports.py.
"""
import calendar
import datetime
import unittest

from helpers import ApiCase

ASSET_T = ('Bank', 'Accounts Receivable', 'Other Current Asset', 'Fixed Asset', 'Other Asset')
LIAB_T = ('Accounts Payable', 'Credit Card', 'Other Current Liability', 'Long Term Liability')
PL_T = ('Income', 'Cost of Goods Sold', 'Expense', 'Other Income', 'Other Expense')
CREDIT_NORMAL = LIAB_T + ('Equity', 'Income', 'Other Income')
REPORTS = ('profit_loss', 'balance_sheet', 'cash_flow')


def d(s):
    return datetime.date.fromisoformat(s)


def mend(y, m):
    return '%04d-%02d-%02d' % (y, m, calendar.monthrange(y, m)[1])


def c2m(cents):
    return round(cents / 100.0, 2)


class Books(ApiCase):
    """Multi-year books + shadow ledger."""
    FY = 1

    def setUp(self):
        super().setUp()
        self.led = []          # (date, account name, debit - credit cents)
        self._ids = {}
        self._types = {}
        if self.FY != 1:
            self.put('/api/settings', {'fiscal_year_start_month': self.FY})
        self.build()

    # ---- posting helpers -------------------------------------------------
    def aid(self, name):
        if name not in self._ids:
            for a in self.get('/api/accounts', include_inactive=1):
                self._ids[a['name']] = a['id']
                self._types[a['name']] = a['type']
        return self._ids[name]

    def atype(self, name):
        self.aid(name)
        return self._types[name]

    def mk(self, name, type_, parent=None, number=''):
        body = {'name': name, 'type': type_, 'number': number}
        if parent:
            body['parent_id'] = self.aid(parent)
        a = self.post('/api/accounts', body)
        self._ids[name] = a['id']
        self._types[name] = type_
        return a['id']

    def je(self, date, *lines):
        """lines: (account, debit dollars, credit dollars[, name_id])"""
        body = {'date': date, 'lines': [
            {'account_id': self.aid(l[0]), 'debit': l[1] or None, 'credit': l[2] or None,
             'name_id': l[3] if len(l) > 3 else None} for l in lines]}
        t = self.post('/api/journals', body)
        for l in lines:
            self.led.append((date, l[0], int(round(l[1] * 100)) - int(round(l[2] * 100))))
        return t

    def invoice(self, cust, date, *lines, **kw):
        """lines: (income account, amount)"""
        body = {'customer_id': cust, 'date': date, 'lines': [
            {'description': 'Work', 'qty': 1, 'rate': amt, 'account_id': self.aid(acct)} for acct, amt in lines]}
        body.update(kw)
        inv = self.post('/api/invoices', body)
        tot = 0
        for acct, amt in lines:
            self.led.append((date, acct, -int(round(amt * 100))))
            tot += int(round(amt * 100))
        self.led.append((date, 'Accounts Receivable', tot))
        return inv

    def payment(self, cust, date, amount, deposit='Checking', **kw):
        body = {'customer_id': cust, 'date': date, 'amount': amount, 'deposit_account_id': self.aid(deposit)}
        body.update(kw)
        p = self.post('/api/payments', body)
        self.led.append((date, deposit, int(round(amount * 100))))
        self.led.append((date, 'Accounts Receivable', -int(round(amount * 100))))
        return p

    # ---- shadow ledger maths --------------------------------------------
    def names_of(self, *types):
        self.aid('Checking')
        return [n for n, t in self._types.items() if t in types]

    def net(self, names, start=None, end=None):
        """debit - credit cents for accounts in [start, end] (None = unbounded)."""
        names = set([names] if isinstance(names, str) else names)
        return sum(c for dt, n, c in self.led
                   if n in names and (start is None or dt >= start) and (end is None or dt <= end))

    def bal(self, name, end):
        """Balance in the account's natural sign, dollars."""
        sign = -1 if self.atype(name) in CREDIT_NORMAL else 1
        return c2m(sign * self.net(name, None, end))

    def net_income(self, start, end):
        return c2m(-self.net(self.names_of(*PL_T), start, end))

    def fy_start(self, date):
        dt = d(date)
        y = dt.year if dt.month >= self.FY else dt.year - 1
        return '%04d-%02d-01' % (y, self.FY)

    # ---- the books -------------------------------------------------------
    def build(self):
        mk, je = self.mk, self.je
        mk('Payroll Checking', 'Bank', parent='Checking', number='1005')
        mk('Old Bank', 'Bank', number='1090')
        mk('Inventory', 'Other Current Asset', number='1400')
        mk('Vehicles', 'Fixed Asset', number='1550')
        mk('Security Deposits', 'Other Asset', number='1900')
        mk('Vehicle Loan', 'Long Term Liability', number='2600')
        mk('Consulting', 'Income', parent='Service Income', number='4110')
        mk('Electric', 'Expense', parent='Utilities', number='6111')
        mk('Gas', 'Expense', parent='Utilities', number='6112')
        mk('Propane', 'Expense', parent='Gas', number='6113')
        mk('Old Promo', 'Expense', number='6005')
        mk('Gain on Asset Sale', 'Other Income', number='7100')
        self.c1 = self.post('/api/customers', {'name': 'Acme Corp'})['id']
        self.c2 = self.post('/api/customers', {'name': 'Zenith LLC'})['id']
        self.v1 = self.post('/api/vendors', {'name': 'Parts Supply Co'})['id']

        # 2023 start-up (mid year)
        je('2023-03-01', ('Checking', 50000, 0), ("Owner's Investment", 0, 50000))
        je('2023-03-02', ('Old Bank', 1500, 0), ("Owner's Investment", 0, 1500))
        je('2023-03-05', ('Furniture and Equipment', 12000, 0), ('Checking', 0, 12000))
        je('2023-03-10', ('Security Deposits', 3000, 0), ('Checking', 0, 3000))
        je('2023-05-01', ('Vehicles', 30000, 0), ('Vehicle Loan', 0, 25000), ('Checking', 0, 5000))
        je('2023-05-15', ('Old Promo', 640.5, 0), ('Old Bank', 0, 640.5))
        je('2023-06-01', ('Payroll Checking', 8000, 0), ('Checking', 0, 8000))
        je('2023-06-30', ('Prepaid Expenses', 2400, 0), ('Checking', 0, 2400))

        i = 0
        y, m = 2023, 3
        while (y, m) <= (2026, 6):
            i += 1
            ym = '%04d-%02d-' % (y, m)
            last = mend(y, m)
            sales = 6000 + 137.25 * i
            je(ym + '03', ('Checking', sales, 0), ('Sales', 0, sales))
            je(ym + '05', ('Rent and Lease', 1800, 0), ('Checking', 0, 1800))
            je(ym + '09', ('Inventory', 900 + 10 * i, 0), ('Accounts Payable', 0, 900 + 10 * i, self.v1))
            je(ym + '12', ('Cost of Goods Sold', 700 + 9.5 * i, 0), ('Inventory', 0, 700 + 9.5 * i))
            je(ym + '14', ('Electric', 210.1 + i, 0), ('Gas', 95 + 2 * i, 0), ('Credit Card', 0, 305.1 + 3 * i))
            if m in (12, 1, 2):
                je(ym + '16', ('Propane', 60 + i, 0), ('Credit Card', 0, 60 + i))
            if m % 2 == 0:
                je(ym + '17', ('Utilities', 45, 0), ('Checking', 0, 45))
            je(ym + '18', ('Payroll Expenses', 2500, 0), ('Payroll Checking', 0, 1900),
               ('Payroll Liabilities', 0, 600))
            je(ym + '20', ('Payroll Liabilities', 550, 0), ('Checking', 0, 550))
            je(ym + '21', ('Checking', 1500, 0), ('Payroll Checking', 0, 1500)) if m % 3 == 0 else None
            je(ym + '22', ('Payroll Checking', 2100, 0), ('Checking', 0, 2100))
            je(ym + '24', ('Accounts Payable', 850 + 10 * i, 0, self.v1), ('Checking', 0, 850 + 10 * i))
            je(ym + '25', ('Credit Card', 280 + 3 * i, 0), ('Checking', 0, 280 + 3 * i))
            if (y, m) >= (2023, 6):
                je(ym + '27', ('Vehicle Loan', 400, 0), ('Interest Expense', 110 - i, 0), ('Checking', 0, 510 - i))
            je(last, ('Checking', 4.17 + i / 100.0, 0), ('Interest Income', 0, 4.17 + i / 100.0))
            if m % 3 == 0:
                je(last, ('Depreciation Expense', 1050, 0), ('Accumulated Depreciation', 0, 1050))
                je(last, ('Insurance', 600, 0), ('Prepaid Expenses', 0, 600)) if (y, m) > (2023, 6) else None
            if m in (6, 12):
                je(ym + '28', ("Owner's Draw", 3000 + 100 * i, 0), ('Checking', 0, 3000 + 100 * i))
            if m == 7:
                je(ym + '01', ('Prepaid Expenses', 2400, 0), ('Checking', 0, 2400)) if y > 2023 else None
            # invoices: Acme monthly, Zenith every other month; paid partly later
            inv = self.invoice(self.c1, ym + '08', ('Service Income', 1200 + 25 * i), ('Consulting', 300.75))
            if i % 4:
                self.payment(self.c1, ym + '26', 1000 + 25 * i, deposit='Checking' if i % 2 else 'Undeposited Funds',
                             applications=[{'invoice_id': inv['id'], 'amount': 1000 + 25 * i}])
                if i % 2 == 0:
                    je(ym + '27', ('Checking', 1000 + 25 * i, 0), ('Undeposited Funds', 0, 1000 + 25 * i))
            if i % 2 == 0:
                self.invoice(self.c2, ym + '15', ('Sales', 800 + i))
            m += 1
            if m > 12:
                y, m = y + 1, 1

        # specials
        je('2024-01-01', ('Retained Earnings', 1250, 0), ('Accounts Payable', 0, 1250, self.v1))  # prior-period adj.
        je('2024-02-29', ('Checking', 2929.29, 0), ('Consulting', 0, 2929.29))                     # leap day
        je('2024-07-01', ('Checking', 5000, 0), ("Owner's Investment", 0, 5000))
        je('2024-12-31', ('Travel', 777.77, 0), ('Credit Card', 0, 777.77))
        je('2025-01-01', ('Meals and Entertainment', 111.11, 0), ('Credit Card', 0, 111.11))
        je('2025-06-30', ('Legal and Professional Services', 2000, 0), ('Checking', 0, 2000))
        je('2025-07-01', ('Advertising and Marketing', 333.33, 0), ('Checking', 0, 333.33))
        je('2025-09-15', ('Checking', 9000, 0), ('Accumulated Depreciation', 2000, 0),
           ('Furniture and Equipment', 0, 10000), ('Gain on Asset Sale', 0, 1000))
        je('2025-11-02', ('Retained Earnings', 0, 400), ('Payroll Liabilities', 400, 0))
        je('2026-02-28', ('Sales', 500, 0), ('Checking', 0, 500))                                   # refund
        self.payment(self.c2, '2026-03-20', 5000, deposit='Checking')                               # oldest first
        # inactive accounts that still carry balances / activity
        self.put('/api/accounts/%d' % self.aid('Old Bank'), {'active': False})
        self.put('/api/accounts/%d' % self.aid('Old Promo'), {'active': False})

    # ---- generic report checks ------------------------------------------
    def rep(self, name, **q):
        return self.get('/api/reports/' + name, **q)

    def amount_cols(self, rep):
        return [i for i, c in enumerate(rep['columns']) if c['kind'] == 'amount']

    def check_cells(self, rep, tag):
        """Every amount cell drills to its value; change / pct cells are arithmetically right."""
        cols = rep['columns']
        bad = []
        n = 0
        for row in rep['rows']:
            if 'values' not in row:
                continue
            vals = row['values']
            self.assertEqual(len(vals), len(cols), tag)
            for i, c in enumerate(cols):
                if c['kind'] == 'amount':
                    dr = row['drill'][i]
                    if dr is None:
                        if row['kind'] != 'grand' or rep['title'] != 'Trial Balance':
                            if vals[i] not in (None,):
                                bad.append('%s / %s / col %d: no drill for %s' % (tag, row['label'], i, vals[i]))
                        continue
                    got = self.post('/api/reports/drill', dr)
                    n += 1
                    if got['total'] != vals[i]:
                        bad.append('%s / %s / col %d (%s): cell %s drill %s' % (
                            tag, row['label'], i, c['label'], vals[i], got['total']))
                    gsum = round(sum(g['total'] for g in got['groups']), 2)
                    if gsum != got['total']:
                        bad.append('%s / %s / col %d: drill groups %s != drill total %s' % (
                            tag, row['label'], i, gsum, got['total']))
                    for g in got['groups']:
                        if round(sum(r['amount'] for r in g['rows']), 2) != g['total']:
                            bad.append('%s / %s: drill group %s rows do not add up' % (tag, row['label'], g['account']))
                elif c['kind'] == 'change':
                    j = i - 2
                    if vals[i] != round(vals[j] - vals[j + 1], 2):
                        bad.append('%s / %s: change %s != %s - %s' % (tag, row['label'], vals[i], vals[j], vals[j + 1]))
                elif c['kind'] == 'pct':
                    j = i - 3
                    b, p = vals[j], vals[j + 1]
                    exp = None if p == 0 else round((b - p) * 100.0 / abs(p), 1)
                    if vals[i] is None or exp is None:
                        if vals[i] != exp:
                            bad.append('%s / %s: pct %s expected %s' % (tag, row['label'], vals[i], exp))
                    elif abs(vals[i] - exp) > 0.11:
                        bad.append('%s / %s: pct %s expected %s' % (tag, row['label'], vals[i], exp))
        self.assertEqual(bad, [], '\n'.join(bad[:25]))
        return n

    def check_sums(self, rep, tag):
        """Each 'Total X' row equals the sum of the rows displayed beneath header X."""
        rows = rep['rows']
        acols = self.amount_cols(rep)
        bad = []
        for ti, t in enumerate(rows):
            if t['kind'] not in ('total', 'subtotal') or not t['label'].startswith('Total '):
                continue
            name = t['label'][6:]
            hi = None
            for k in range(ti - 1, -1, -1):
                if rows[k]['level'] == t['level'] and rows[k]['label'] == name and rows[k]['kind'] in ('header', 'account'):
                    hi = k
                    break
                if rows[k]['level'] < t['level']:
                    break
            if hi is None:
                continue
            sums = [0.0] * len(rep['columns'])
            if rows[hi]['kind'] == 'account':
                for i in acols:
                    sums[i] += rows[hi]['values'][i]
            for k in range(hi + 1, ti):
                r = rows[k]
                if r['level'] != t['level'] + 1 or 'values' not in r:
                    continue
                is_parent = k + 1 < ti and rows[k + 1]['level'] > r['level']
                if r['kind'] in ('subtotal', 'total') or (r['kind'] == 'account' and not is_parent):
                    for i in acols:
                        sums[i] += r['values'][i]
            for i in acols:
                if round(sums[i], 2) != t['values'][i]:
                    bad.append('%s / %s / col %d: shown rows add to %s, total says %s' % (
                        tag, t['label'], i, round(sums[i], 2), t['values'][i]))
        self.assertEqual(bad, [], '\n'.join(bad[:25]))

    def v(self, rep, label, col=0):
        return self.val(rep, label, col)

    def has(self, rep, label):
        return any(r['label'] == label for r in rep['rows'])

    def check_bs(self, bs, tag):
        for i in self.amount_cols(bs):
            end = bs['columns'][i]['end']
            t = '%s col %d (%s)' % (tag, i, end)
            self.assertEqual(self.v(bs, 'TOTAL ASSETS', i), self.v(bs, 'TOTAL LIABILITIES AND EQUITY', i), t)
            self.assertEqual(round(self.v(bs, 'Total Liabilities', i) + self.v(bs, 'Total Equity', i), 2),
                             self.v(bs, 'TOTAL LIABILITIES AND EQUITY', i), t)
            exp_assets = c2m(self.net(self.names_of(*ASSET_T), None, end))
            exp_liab = c2m(-self.net(self.names_of(*LIAB_T), None, end))
            self.assertEqual(self.v(bs, 'TOTAL ASSETS', i), exp_assets, t)
            self.assertEqual(self.v(bs, 'Total Liabilities', i), exp_liab, t)
            fs = self.fy_start(end)
            prior_end = (d(fs) - datetime.timedelta(days=1)).isoformat()
            exp_ni = self.net_income(fs, end)
            exp_re = round(c2m(-self.net('Retained Earnings', None, end)) + self.net_income(None, prior_end), 2)
            self.assertEqual(self.v(bs, 'Net Income', i), exp_ni, t + ' Net Income')
            self.assertEqual(self.v(bs, 'Retained Earnings', i), exp_re, t + ' Retained Earnings')
            # every account row = shadow balance
            for r in bs['rows']:
                if r['kind'] == 'account' and r.get('account_id') and r['label'] not in ('Retained Earnings',):
                    self.assertEqual(r['values'][i], self.bal(r['label'], end), '%s %s' % (t, r['label']))
            cur = sum(c2m(self.net(self.names_of(tp), None, end))
                      for tp in ('Bank', 'Accounts Receivable', 'Other Current Asset'))
            self.assertEqual(self.v(bs, 'Total Current Assets', i), round(cur, 2), t)
            cl = c2m(-self.net(self.names_of('Accounts Payable', 'Credit Card', 'Other Current Liability'), None, end))
            self.assertEqual(self.v(bs, 'Total Current Liabilities', i), cl, t)

    def check_cf(self, cf, tag):
        for i in self.amount_cols(cf):
            s, e = cf['columns'][i]['start'], cf['columns'][i]['end']
            t = '%s col %d (%s..%s)' % (tag, i, s, e)
            op = self.v(cf, 'Net cash provided by operating activities', i)
            inv = self.v(cf, 'Net cash provided by investing activities', i)
            fin = self.v(cf, 'Net cash provided by financing activities', i)
            chg = self.v(cf, 'NET CASH INCREASE FOR PERIOD', i)
            beg = self.v(cf, 'Cash at beginning of period', i)
            end = self.v(cf, 'CASH AT END OF PERIOD', i)
            self.assertEqual(round(op + inv + fin, 2), chg, t)
            self.assertEqual(round(beg + chg, 2), end, t)
            banks = self.names_of('Bank')
            day_before = (d(s) - datetime.timedelta(days=1)).isoformat()
            self.assertEqual(end, c2m(self.net(banks, None, e)), t)
            self.assertEqual(beg, c2m(self.net(banks, None, day_before)), t)
            self.assertEqual(self.v(cf, 'Net Income', i), self.net_income(s, e), t)
            adj = self.v(cf, 'Total Adjustments to reconcile Net Income to Net Cash provided by operations', i)
            self.assertEqual(round(self.v(cf, 'Net Income', i) + adj, 2), op, t)
            # independent section figures
            exp_inv = c2m(-self.net(['Furniture and Equipment', 'Vehicles', 'Security Deposits'], s, e))
            exp_fin = c2m(-self.net(self.names_of('Long Term Liability', 'Equity'), s, e))
            self.assertEqual(inv, exp_inv, t + ' investing')
            self.assertEqual(fin, exp_fin, t + ' financing')
            bs = self.rep('balance_sheet', end=e)
            bs_bank = self.v(bs, 'Total Bank Accounts') if self.has(bs, 'Total Bank Accounts') else 0.0
            self.assertEqual(bs_bank, end, t + ' vs balance sheet')


# =============================================================================== Profit and Loss

class ProfitAndLoss(Books):
    def test_layout_like_quickbooks(self):
        pl = self.rep('profit_loss', start='2024-01-01', end='2024-12-31')
        labels = [r['label'] for r in pl['rows']]
        order = ['Income', 'Total Income', 'Cost of Goods Sold', 'Total Cost of Goods Sold', 'Gross Profit',
                 'Expenses', 'Total Expenses', 'Net Operating Income', 'Other Income', 'Total Other Income',
                 'Other Expenses', 'Total Other Expenses', 'Net Other Income', 'Net Income']
        pos = [labels.index(x) for x in order]
        self.assertEqual(pos, sorted(pos), labels)
        self.assertEqual(labels[-1], 'Net Income')
        self.assertEqual(pl['title'], 'Profit and Loss')
        self.assertEqual(pl['subtitle'], '2024')

    def test_figures_for_calendar_2024(self):
        s, e = '2024-01-01', '2024-12-31'
        pl = self.rep('profit_loss', start=s, end=e)
        inc = c2m(-self.net(self.names_of('Income'), s, e))
        cogs = c2m(self.net(self.names_of('Cost of Goods Sold'), s, e))
        exp = c2m(self.net(self.names_of('Expense'), s, e))
        oi = c2m(-self.net(self.names_of('Other Income'), s, e))
        oe = c2m(self.net(self.names_of('Other Expense'), s, e))
        self.assertEqual(self.v(pl, 'Total Income'), inc)
        self.assertEqual(self.v(pl, 'Total Cost of Goods Sold'), cogs)
        self.assertEqual(self.v(pl, 'Gross Profit'), round(inc - cogs, 2))
        self.assertEqual(self.v(pl, 'Total Expenses'), exp)
        self.assertEqual(self.v(pl, 'Net Operating Income'), round(inc - cogs - exp, 2))
        self.assertEqual(self.v(pl, 'Total Other Income'), oi)
        self.assertEqual(self.v(pl, 'Total Other Expenses'), oe)
        self.assertEqual(self.v(pl, 'Net Other Income'), round(oi - oe, 2))
        self.assertEqual(self.v(pl, 'Net Income'), round(inc - cogs - exp + oi - oe, 2))
        for r in pl['rows']:
            if r['kind'] == 'account':
                sign = -1 if self.atype(r['label']) in CREDIT_NORMAL else 1
                self.assertEqual(r['values'][0], c2m(sign * self.net(r['label'], s, e)), r['label'])
        # positive income, positive expenses
        self.assertGreater(self.v(pl, 'Sales'), 0)
        self.assertGreater(self.v(pl, 'Rent and Lease'), 0)
        self.assertGreater(self.v(pl, 'Interest Expense'), 0)

    def test_subaccount_rollups(self):
        s, e = '2024-01-01', '2024-12-31'
        pl = self.rep('profit_loss', start=s, end=e)
        labels = [r['label'] for r in pl['rows']]
        i = labels.index('Utilities')
        self.assertEqual(labels[i:i + 7], ['Utilities', 'Electric', 'Gas', 'Propane', 'Total Gas', 'Total Utilities',
                                           'Depreciation Expense'][:6] + [labels[i + 6]])
        self.assertEqual(self.v(pl, 'Utilities'), c2m(self.net('Utilities', s, e)))       # parent's own postings
        self.assertEqual(self.v(pl, 'Total Gas'), c2m(self.net(['Gas', 'Propane'], s, e)))
        self.assertEqual(self.v(pl, 'Total Utilities'), c2m(self.net(['Utilities', 'Electric', 'Gas', 'Propane'], s, e)))
        self.assertEqual(self.v(pl, 'Total Service Income'), c2m(-self.net(['Service Income', 'Consulting'], s, e)))
        lv = {r['label']: r['level'] for r in pl['rows']}
        self.assertEqual((lv['Utilities'], lv['Gas'], lv['Propane'], lv['Total Gas'], lv['Total Utilities']),
                         (1, 2, 3, 2, 1))
        self.check_sums(pl, 'pl 2024')

    def test_month_columns_leap_year_and_total(self):
        pl = self.rep('profit_loss', start='2024-01-01', end='2024-12-31', columns='month')
        cols = pl['columns']
        self.assertEqual(len(cols), 13)
        self.assertEqual(cols[-1]['label'], 'Total')
        self.assertEqual((cols[1]['start'], cols[1]['end'], cols[1]['label']), ('2024-02-01', '2024-02-29', 'Feb 2024'))
        for i in range(12):
            self.assertEqual((cols[i]['start'], cols[i]['end']), ('2024-%02d-01' % (i + 1), mend(2024, i + 1)))
        for r in pl['rows']:
            if 'values' in r:
                self.assertEqual(round(sum(r['values'][:12]), 2), r['values'][12], r['label'])
        for i in range(12):
            self.assertEqual(self.v(pl, 'Net Income', i), self.net_income(cols[i]['start'], cols[i]['end']))
        self.assertEqual(self.v(pl, 'Net Income', 12), self.net_income('2024-01-01', '2024-12-31'))
        self.check_sums(pl, 'pl months')

    def test_partial_range_columns_are_clipped(self):
        pl = self.rep('profit_loss', start='2024-01-15', end='2024-03-10', columns='month')
        cols = pl['columns']
        self.assertEqual([(c['start'], c['end']) for c in cols],
                         [('2024-01-15', '2024-01-31'), ('2024-02-01', '2024-02-29'), ('2024-03-01', '2024-03-10'),
                          ('2024-01-15', '2024-03-10')])
        self.assertEqual([c['label'] for c in cols],
                         ['Jan 15, 2024 - Jan 31, 2024', 'Feb 2024', 'Mar 1, 2024 - Mar 10, 2024', 'Total'])
        for r in pl['rows']:
            if 'values' in r:
                self.assertEqual(round(sum(r['values'][:3]), 2), r['values'][3], r['label'])
        self.assertEqual(self.v(pl, 'Net Income', 3), self.net_income('2024-01-15', '2024-03-10'))

    def test_quarter_and_year_columns_span_years(self):
        pl = self.rep('profit_loss', start='2023-11-01', end='2025-02-28', columns='quarter')
        cols = pl['columns']
        self.assertEqual([(c['start'], c['end']) for c in cols[:-1]],
                         [('2023-11-01', '2023-12-31'), ('2024-01-01', '2024-03-31'), ('2024-04-01', '2024-06-30'),
                          ('2024-07-01', '2024-09-30'), ('2024-10-01', '2024-12-31'), ('2025-01-01', '2025-02-28')])
        for i, c in enumerate(cols):
            self.assertEqual(self.v(pl, 'Net Income', i), self.net_income(c['start'], c['end']), c['label'])
        py = self.rep('profit_loss', start='2023-03-01', end='2026-06-30', columns='year')
        self.assertEqual([(c['start'], c['end'], c['label']) for c in py['columns']],
                         [('2023-03-01', '2023-12-31', 'Mar - Dec 2023'), ('2024-01-01', '2024-12-31', '2024'),
                          ('2025-01-01', '2025-12-31', '2025'), ('2026-01-01', '2026-06-30', 'Jan - Jun 2026'),
                          ('2023-03-01', '2026-06-30', 'Total')])
        for i, c in enumerate(py['columns']):
            self.assertEqual(self.v(py, 'Net Income', i), self.net_income(c['start'], c['end']), c['label'])
            self.assertEqual(self.v(py, 'Total Expenses', i),
                             c2m(self.net(self.names_of('Expense'), c['start'], c['end'])), c['label'])

    def test_inactive_account_with_activity_still_reported(self):
        pl = self.rep('profit_loss', start='2023-01-01', end='2023-12-31')
        self.assertEqual(self.v(pl, 'Old Promo'), 640.5)

    def test_zero_activity_rows_hidden(self):
        pl = self.rep('profit_loss', start='2024-03-01', end='2024-03-31')
        self.assertFalse(self.has(pl, 'Propane'))
        self.assertFalse(self.has(pl, 'Travel'))
        self.assertFalse(self.has(pl, 'Old Promo'))
        self.assertTrue(self.has(pl, 'Gas'))
        # a month column brings a row in if ANY column has activity
        pl = self.rep('profit_loss', start='2024-01-01', end='2024-03-31', columns='month')
        self.assertEqual(self.row(pl, 'Propane')['values'][2], 0.0)

    def test_refund_only_period_shows_negative_income(self):
        pl = self.rep('profit_loss', start='2026-02-28', end='2026-02-28')
        self.assertEqual(self.v(pl, 'Sales'), -500.0)
        self.assertEqual(self.v(pl, 'Total Income'), -500.0)

    def test_empty_period(self):
        pl = self.rep('profit_loss', start='2020-01-01', end='2020-12-31')
        self.assertEqual(self.v(pl, 'Net Income'), 0.0)
        self.assertEqual(self.v(pl, 'Gross Profit'), 0.0)

    def test_every_cell_drills(self):
        n = 0
        for q in ({'start': '2024-01-01', 'end': '2024-12-31'},
                  {'start': '2024-01-01', 'end': '2024-06-30', 'columns': 'month'},
                  {'start': '2023-03-01', 'end': '2026-06-30', 'columns': 'year'},
                  {'start': '2024-02-01', 'end': '2024-02-29', 'compare': 'pp'},
                  {'start': '2024-02-29', 'end': '2024-02-29', 'compare': 'py'},
                  {'start': '2025-01-01', 'end': '2025-06-30', 'columns': 'quarter', 'compare': 'py'}):
            rep = self.rep('profit_loss', **q)
            n += self.check_cells(rep, 'pl %s' % q)
            self.check_sums(rep, 'pl %s' % q)
        self.assertGreater(n, 300)


# =============================================================================== comparison columns

class Comparison(Books):
    def cmp_cols(self, name='profit_loss', **q):
        rep = self.rep(name, **q)
        return rep, [(c['start'], c['end']) for c in rep['columns'] if c['kind'] == 'amount']

    def test_previous_period_whole_month(self):
        rep, cols = self.cmp_cols(start='2024-03-01', end='2024-03-31', compare='pp')
        self.assertEqual(cols, [('2024-03-01', '2024-03-31'), ('2024-02-01', '2024-02-29')])
        self.assertEqual([c['kind'] for c in rep['columns']], ['amount', 'amount', 'change', 'pct'])
        self.assertEqual(rep['columns'][1]['label'], 'Feb 2024 (PP)')
        cur, prev = self.net_income(*cols[0]), self.net_income(*cols[1])
        row = self.row(rep, 'Net Income')['values']
        self.assertEqual(row[:3], [cur, prev, round(cur - prev, 2)])
        self.assertAlmostEqual(row[3], (cur - prev) * 100 / abs(prev), delta=0.06)

    def test_previous_period_whole_quarter_and_year(self):
        _, cols = self.cmp_cols(start='2024-04-01', end='2024-06-30', compare='pp')
        self.assertEqual(cols[1], ('2024-01-01', '2024-03-31'))
        _, cols = self.cmp_cols(start='2024-01-01', end='2024-12-31', compare='pp')
        self.assertEqual(cols[1], ('2023-01-01', '2023-12-31'))
        _, cols = self.cmp_cols(start='2024-03-01', end='2024-03-31', compare='pp')
        self.assertEqual(cols[1], ('2024-02-01', '2024-02-29'))
        _, cols = self.cmp_cols(start='2025-03-01', end='2025-03-31', compare='pp')
        self.assertEqual(cols[1], ('2025-02-01', '2025-02-28'))

    def test_previous_period_arbitrary_days_same_length(self):
        _, cols = self.cmp_cols(start='2024-03-10', end='2024-03-19', compare='pp')
        self.assertEqual(cols[1], ('2024-02-29', '2024-03-09'))
        _, cols = self.cmp_cols(start='2024-03-01', end='2024-03-01', compare='pp')
        self.assertEqual(cols[1], ('2024-02-29', '2024-02-29'))
        _, cols = self.cmp_cols(start='2024-01-15', end='2024-02-14', compare='pp')
        self.assertEqual(cols[1], ('2023-12-15', '2024-01-14'))

    def test_previous_year(self):
        _, cols = self.cmp_cols(start='2024-02-01', end='2024-02-29', compare='py')
        self.assertEqual(cols[1], ('2023-02-01', '2023-02-28'))
        _, cols = self.cmp_cols(start='2025-02-01', end='2025-02-28', compare='py')
        self.assertEqual(cols[1], ('2024-02-01', '2024-02-29'))        # whole month stays a whole month
        _, cols = self.cmp_cols(start='2024-02-29', end='2024-02-29', compare='py')
        self.assertEqual(cols[1], ('2023-02-28', '2023-02-28'))
        _, cols = self.cmp_cols(start='2025-01-01', end='2025-12-31', compare='py')
        self.assertEqual(cols[1], ('2024-01-01', '2024-12-31'))
        _, cols = self.cmp_cols(start='2025-03-05', end='2025-04-17', compare='py')
        self.assertEqual(cols[1], ('2024-03-05', '2024-04-17'))

    def test_previous_year_single_day_feb_28_stays_one_day(self):
        # Feb 28, 2025 compared with the same day last year must be Feb 28, 2024 - not Feb 28..29.
        _, cols = self.cmp_cols(start='2025-02-28', end='2025-02-28', compare='py')
        self.assertEqual(cols[1], ('2024-02-28', '2024-02-28'))

    def test_previous_year_values(self):
        rep, cols = self.cmp_cols(start='2025-01-01', end='2025-12-31', compare='py')
        for label, names, sign in (('Total Income', self.names_of('Income'), -1),
                                   ('Total Expenses', self.names_of('Expense'), 1)):
            cur, prev = c2m(sign * self.net(names, *cols[0])), c2m(sign * self.net(names, *cols[1]))
            row = self.row(rep, label)['values']
            self.assertEqual(row[:3], [cur, prev, round(cur - prev, 2)])
            self.assertAlmostEqual(row[3], (cur - prev) * 100 / abs(prev), delta=0.06)

    def test_columns_with_compare(self):
        rep = self.rep('profit_loss', start='2024-01-01', end='2024-03-31', columns='month', compare='py')
        kinds = [c['kind'] for c in rep['columns']]
        self.assertEqual(kinds[:12], ['amount', 'amount', 'change', 'pct'] * 3)
        am = [(c['start'], c['end']) for c in rep['columns'] if c['kind'] == 'amount']
        self.assertEqual(am[:6], [('2024-01-01', '2024-01-31'), ('2023-01-01', '2023-01-31'),
                                  ('2024-02-01', '2024-02-29'), ('2023-02-01', '2023-02-28'),
                                  ('2024-03-01', '2024-03-31'), ('2023-03-01', '2023-03-31')])
        self.check_cells(rep, 'pl month+py')

    @unittest.expectedFailure   # known gap: no Total column when period columns are combined with a comparison
    def test_gap_columns_with_compare_has_total(self):
        # QuickBooks keeps the Total column when comparison columns are added to a trended report.
        rep = self.rep('profit_loss', start='2024-01-01', end='2024-03-31', columns='month', compare='py')
        self.assertIn('Total', [c['label'] for c in rep['columns']])

    def test_pct_change_edge_cases(self):
        # zero base -> no percentage; negative base -> sign follows the direction of the change
        self.je('2022-01-10', ('Checking', 100, 0), ('Uncategorized Income', 0, 100))       # Jan: +100
        self.je('2022-02-10', ('Uncategorized Income', 150, 0), ('Checking', 0, 150))       # Feb: -150
        self.je('2022-03-10', ('Checking', 50, 0), ('Uncategorized Income', 0, 50))         # Mar: +50
        r = self.row(self.rep('profit_loss', start='2022-01-01', end='2022-01-31', compare='pp'),
                     'Uncategorized Income')['values']
        self.assertEqual(r, [100.0, 0.0, 100.0, None])
        r = self.row(self.rep('profit_loss', start='2022-02-01', end='2022-02-28', compare='pp'),
                     'Uncategorized Income')['values']
        self.assertEqual(r, [-150.0, 100.0, -250.0, -250.0])
        r = self.row(self.rep('profit_loss', start='2022-03-01', end='2022-03-31', compare='pp'),
                     'Uncategorized Income')['values']
        self.assertEqual(r, [50.0, -150.0, 200.0, 133.3])      # improvement from a negative base is positive
        r = self.row(self.rep('profit_loss', start='2022-04-01', end='2022-04-30', compare='pp'),
                     'Uncategorized Income')['values']
        self.assertEqual(r, [0.0, 50.0, -50.0, -100.0])

    def test_balance_sheet_compare(self):
        bs = self.rep('balance_sheet', start='2025-01-01', end='2025-12-31', compare='py')
        ends = [c['end'] for c in bs['columns'] if c['kind'] == 'amount']
        self.assertEqual(ends, ['2025-12-31', '2024-12-31'])
        self.assertEqual([c['label'] for c in bs['columns']][:2], ['As of Dec 31, 2025', 'As of Dec 31, 2024 (PY)'])
        self.check_bs(bs, 'bs py')
        row = self.row(bs, 'TOTAL ASSETS')['values']
        self.assertEqual(row[2], round(row[0] - row[1], 2))
        bs = self.rep('balance_sheet', start='2025-06-01', end='2025-06-30', compare='pp')
        self.assertEqual([c['end'] for c in bs['columns'] if c['kind'] == 'amount'], ['2025-06-30', '2025-05-31'])
        self.check_bs(bs, 'bs pp')

    def test_balance_sheet_compare_without_start(self):
        # Only an as-of date (what a balance sheet normally gets): PY must be one year earlier.
        bs = self.rep('balance_sheet', end='2025-08-15', compare='py')
        self.assertEqual([c['end'] for c in bs['columns'] if c['kind'] == 'amount'], ['2025-08-15', '2024-08-15'])
        self.check_bs(bs, 'bs py asof')

    def test_cash_flow_compare(self):
        for q in ({'start': '2025-01-01', 'end': '2025-12-31', 'compare': 'py'},
                  {'start': '2025-04-01', 'end': '2025-06-30', 'compare': 'pp'},
                  {'start': '2024-02-29', 'end': '2024-03-05', 'compare': 'py'}):
            cf = self.rep('cash_flow', **q)
            self.check_cf(cf, 'cf %s' % q)
            self.check_cells(cf, 'cf %s' % q)


# =============================================================================== Balance Sheet

class BalanceSheet(Books):
    DATES = ('2023-02-28', '2023-03-01', '2023-12-31', '2024-01-01', '2024-02-29', '2024-06-30', '2024-07-01',
             '2024-12-31', '2025-01-01', '2025-06-30', '2025-07-01', '2025-09-15', '2025-12-31', '2026-01-01',
             '2026-06-30', '2030-01-01')

    def test_layout_like_quickbooks(self):
        bs = self.rep('balance_sheet', end='2025-12-31')
        labels = [r['label'] for r in bs['rows']]
        order = ['ASSETS', 'Current Assets', 'Bank Accounts', 'Total Bank Accounts', 'Accounts Receivable',
                 'Total Accounts Receivable', 'Other Current Assets', 'Total Other Current Assets',
                 'Total Current Assets', 'Fixed Assets', 'Total Fixed Assets', 'Other Assets', 'Total Other Assets',
                 'TOTAL ASSETS', 'LIABILITIES AND EQUITY', 'Liabilities', 'Current Liabilities',
                 'Total Accounts Payable', 'Credit Cards', 'Total Credit Cards', 'Other Current Liabilities',
                 'Total Other Current Liabilities', 'Total Current Liabilities', 'Long-Term Liabilities',
                 'Total Long-Term Liabilities', 'Total Liabilities', 'Equity', 'Retained Earnings', 'Net Income',
                 'Total Equity', 'TOTAL LIABILITIES AND EQUITY']
        pos = []
        at = 0
        for x in order:
            at = labels.index(x, at)
            pos.append(at)
            at += 1
        self.assertEqual(bs['subtitle'], 'As of Dec 31, 2025')
        # natural signs
        self.assertLess(self.v(bs, 'Accumulated Depreciation'), 0)     # contra asset shown negative
        self.assertLess(self.v(bs, "Owner's Draw"), 0)                 # draws reduce equity
        self.assertGreater(self.v(bs, 'Vehicle Loan'), 0)
        self.assertGreater(self.v(bs, 'Credit Card'), 0)
        self.assertGreater(self.v(bs, "Owner's Investment"), 0)

    def test_balances_on_many_dates(self):
        for dt in self.DATES:
            bs = self.rep('balance_sheet', end=dt)
            self.check_bs(bs, 'bs ' + dt)
            self.check_sums(bs, 'bs ' + dt)

    def test_retained_earnings_rollover_at_year_end(self):
        dec = self.rep('balance_sheet', end='2024-12-31')
        jan = self.rep('balance_sheet', end='2025-01-01')
        ni24 = self.net_income('2024-01-01', '2024-12-31')
        self.assertEqual(self.v(dec, 'Net Income'), ni24)
        self.assertEqual(self.v(jan, 'Net Income'), -111.11)           # only the Jan 1 expense
        self.assertEqual(round(self.v(jan, 'Retained Earnings') - self.v(dec, 'Retained Earnings'), 2), ni24)
        # direct posting to Retained Earnings (debit 1,250 on 2024-01-01) reduces it
        d23 = self.rep('balance_sheet', end='2023-12-31')
        j24 = self.rep('balance_sheet', end='2024-01-01')
        self.assertEqual(self.v(d23, 'Retained Earnings'), 0.0)
        self.assertEqual(self.v(j24, 'Retained Earnings'), round(self.net_income(None, '2023-12-31') - 1250, 2))

    def test_month_columns_balance_every_column(self):
        bs = self.rep('balance_sheet', start='2024-11-01', end='2025-02-28', columns='month')
        self.assertEqual([c['label'] for c in bs['columns']],
                         ['As of Nov 30, 2024', 'As of Dec 31, 2024', 'As of Jan 31, 2025', 'As of Feb 28, 2025'])
        self.check_bs(bs, 'bs months')
        self.check_sums(bs, 'bs months')
        bs = self.rep('balance_sheet', start='2023-03-01', end='2026-06-30', columns='quarter')
        self.check_bs(bs, 'bs quarters')
        bs = self.rep('balance_sheet', start='2023-03-01', end='2026-06-30', columns='year')
        self.assertEqual([c['end'] for c in bs['columns']], ['2023-12-31', '2024-12-31', '2025-12-31', '2026-06-30'])
        self.check_bs(bs, 'bs years')

    def test_subaccounts_and_inactive(self):
        bs = self.rep('balance_sheet', end='2025-12-31')
        e = '2025-12-31'
        self.assertEqual(self.v(bs, 'Total Checking'), c2m(self.net(['Checking', 'Payroll Checking'], None, e)))
        self.assertEqual(self.v(bs, 'Checking'), c2m(self.net('Checking', None, e)))
        self.assertEqual(self.v(bs, 'Payroll Checking'), c2m(self.net('Payroll Checking', None, e)))
        self.assertEqual(self.v(bs, 'Old Bank'), 859.5)                # inactive, but still has money in it
        self.assertEqual(self.v(bs, 'Total Bank Accounts'), c2m(self.net(self.names_of('Bank'), None, e)))

    def test_ar_matches_open_invoices(self):
        for dt in ('2023-12-31', '2024-06-30', '2025-12-31', '2026-03-19', '2026-03-20', '2026-06-30'):
            bs = self.rep('balance_sheet', end=dt)
            ag = self.rep('ar_aging', end=dt)
            tot = self.row(ag, 'TOTAL')['values']
            self.assertEqual(tot[5], self.v(bs, 'Accounts Receivable'), dt)
            self.assertEqual(round(sum(tot[:5]), 2), tot[5], dt)
            for r in ag['rows']:
                self.assertEqual(round(sum(r['values'][:5]), 2), r['values'][5])
                for i, cell in enumerate(r['drill']):
                    got = round(sum(x['amount'] for x in cell['items']), 2) if cell else 0.0
                    self.assertEqual(got, r['values'][i], '%s %s col %d' % (dt, r['label'], i))

    def test_every_cell_drills(self):
        n = 0
        for q in ({'end': '2025-12-31'}, {'end': '2024-02-29'},
                  {'start': '2024-10-01', 'end': '2025-03-31', 'columns': 'month'},
                  {'start': '2023-03-01', 'end': '2026-06-30', 'columns': 'year'},
                  {'start': '2025-01-01', 'end': '2025-12-31', 'compare': 'py'},
                  {'start': '2025-03-01', 'end': '2025-03-31', 'compare': 'pp'},
                  {'start': '2024-07-01', 'end': '2025-06-30', 'columns': 'quarter', 'compare': 'pp'}):
            rep = self.rep('balance_sheet', **q)
            n += self.check_cells(rep, 'bs %s' % q)
            self.check_sums(rep, 'bs %s' % q)
        self.assertGreater(n, 300)


class BalanceSheetFiscalJuly(Books):
    FY = 7

    def test_rollover_at_june_30(self):
        for dt in ('2023-06-30', '2023-07-01', '2024-06-30', '2024-07-01', '2024-12-31', '2025-01-01',
                   '2025-06-30', '2025-07-01', '2026-06-30'):
            bs = self.rep('balance_sheet', end=dt)
            self.check_bs(bs, 'bs fy7 ' + dt)
        jun = self.rep('balance_sheet', end='2025-06-30')
        jul = self.rep('balance_sheet', end='2025-07-01')
        ni = self.net_income('2024-07-01', '2025-06-30')
        self.assertEqual(self.v(jun, 'Net Income'), ni)
        self.assertEqual(self.v(jul, 'Net Income'), -333.33)
        self.assertEqual(round(self.v(jul, 'Retained Earnings') - self.v(jun, 'Retained Earnings'), 2), ni)
        # calendar year end is NOT a roll-over any more
        dec, jan = self.rep('balance_sheet', end='2024-12-31'), self.rep('balance_sheet', end='2025-01-01')
        self.assertEqual(self.v(dec, 'Retained Earnings'), self.v(jan, 'Retained Earnings'))

    def test_default_start_is_fiscal_year_start(self):
        pl = self.rep('profit_loss', end='2025-03-31')
        self.assertEqual((pl['columns'][0]['start'], pl['columns'][0]['end']), ('2024-07-01', '2025-03-31'))
        self.assertEqual(self.v(pl, 'Net Income'), self.net_income('2024-07-01', '2025-03-31'))
        pl = self.rep('profit_loss', end='2025-07-01')
        self.assertEqual(pl['columns'][0]['start'], '2025-07-01')

    def test_fiscal_quarters_and_years(self):
        pl = self.rep('profit_loss', start='2024-07-01', end='2025-06-30', columns='quarter')
        self.assertEqual([(c['start'], c['end']) for c in pl['columns']],
                         [('2024-07-01', '2024-09-30'), ('2024-10-01', '2024-12-31'), ('2025-01-01', '2025-03-31'),
                          ('2025-04-01', '2025-06-30'), ('2024-07-01', '2025-06-30')])
        pl = self.rep('profit_loss', start='2023-03-01', end='2026-06-30', columns='year')
        self.assertEqual([(c['start'], c['end']) for c in pl['columns']],
                         [('2023-03-01', '2023-06-30'), ('2023-07-01', '2024-06-30'), ('2024-07-01', '2025-06-30'),
                          ('2025-07-01', '2026-06-30'), ('2023-03-01', '2026-06-30')])
        for i, c in enumerate(pl['columns']):
            self.assertEqual(self.v(pl, 'Net Income', i), self.net_income(c['start'], c['end']))
        self.assertEqual(pl['columns'][1]['label'], 'Jul 2023 - Jun 2024')
        bs = self.rep('balance_sheet', start='2023-03-01', end='2026-06-30', columns='year')
        self.assertEqual([c['end'] for c in bs['columns']], ['2023-06-30', '2024-06-30', '2025-06-30', '2026-06-30'])
        self.check_bs(bs, 'bs fy7 years')
        self.check_cells(bs, 'bs fy7 years')

    def test_trial_balance_fiscal(self):
        for dt in ('2024-06-30', '2024-07-01', '2025-03-31'):
            self.check_tb(dt)

    def check_tb(self, dt):
        return TrialBalance.check_tb(self, dt)

    def test_cash_flow_fiscal(self):
        cf = self.rep('cash_flow', start='2024-07-01', end='2025-06-30', columns='quarter')
        self.check_cf(cf, 'cf fy7')


class BalanceSheetFiscalApril(Books):
    FY = 4

    def test_rollover_and_quarters(self):
        for dt in ('2024-03-31', '2024-04-01', '2025-02-28', '2025-03-31', '2025-04-01'):
            self.check_bs(self.rep('balance_sheet', end=dt), 'bs fy4 ' + dt)
        pl = self.rep('profit_loss', start='2024-02-10', end='2024-08-20', columns='quarter')
        self.assertEqual([(c['start'], c['end']) for c in pl['columns'][:-1]],
                         [('2024-02-10', '2024-03-31'), ('2024-04-01', '2024-06-30'), ('2024-07-01', '2024-08-20')])
        bs = self.rep('balance_sheet', start='2024-01-01', end='2025-06-30', columns='quarter', compare='py')
        self.check_bs(bs, 'bs fy4 q py')
        self.check_cells(bs, 'bs fy4 q py')


# =============================================================================== Cash Flow

class CashFlow(Books):
    def test_layout_like_quickbooks(self):
        cf = self.rep('cash_flow', start='2025-01-01', end='2025-12-31')
        labels = [r['label'] for r in cf['rows']]
        order = ['OPERATING ACTIVITIES', 'Net Income',
                 'Adjustments to reconcile Net Income to Net Cash provided by operations:',
                 'Total Adjustments to reconcile Net Income to Net Cash provided by operations',
                 'Net cash provided by operating activities', 'INVESTING ACTIVITIES',
                 'Net cash provided by investing activities', 'FINANCING ACTIVITIES',
                 'Net cash provided by financing activities', 'NET CASH INCREASE FOR PERIOD',
                 'Cash at beginning of period', 'CASH AT END OF PERIOD']
        pos = [labels.index(x) for x in order]
        self.assertEqual(pos, sorted(pos))
        sect = {}
        cur = None
        for r in cf['rows']:
            if r['label'].endswith('ACTIVITIES'):
                cur = r['label'].split()[0]
            elif r['kind'] == 'account' and r.get('account_id'):
                sect[r['label']] = cur
        for name in ('Accounts Receivable', 'Inventory', 'Accounts Payable',
                     'Credit Card', 'Payroll Liabilities', 'Accumulated Depreciation'):
            self.assertEqual(sect.get(name), 'OPERATING', name)
        self.assertEqual(sect.get('Furniture and Equipment'), 'INVESTING')
        for name in ('Vehicle Loan', "Owner's Draw", 'Retained Earnings'):
            self.assertEqual(sect.get(name), 'FINANCING', name)
        for b in self.names_of('Bank'):
            self.assertNotIn(b, sect)

    def test_signs(self):
        s, e = '2025-01-01', '2025-12-31'
        cf = self.rep('cash_flow', start=s, end=e)
        # asset increase uses cash (negative); liability increase provides cash (positive)
        self.assertEqual(self.v(cf, 'Accounts Receivable'), c2m(-self.net('Accounts Receivable', s, e)))
        self.assertEqual(self.v(cf, 'Inventory'), c2m(-self.net('Inventory', s, e)))
        self.assertEqual(self.v(cf, 'Accounts Payable'), c2m(-self.net('Accounts Payable', s, e)))
        self.assertEqual(self.v(cf, 'Vehicle Loan'), -4800.0)                 # principal repaid
        self.assertEqual(self.v(cf, "Owner's Draw"), c2m(-self.net("Owner's Draw", s, e)))
        self.assertLess(self.v(cf, "Owner's Draw"), 0)
        # depreciation added back net of the 2,000 removed on disposal; sale of equipment +10,000 investing
        self.assertEqual(self.v(cf, 'Accumulated Depreciation'), 2200.0)
        self.assertEqual(self.v(cf, 'Furniture and Equipment'), 10000.0)

    def test_reconciles_in_every_configuration(self):
        for q in ({'start': '2023-01-01', 'end': '2023-12-31'},
                  {'start': '2024-01-01', 'end': '2024-12-31', 'columns': 'month'},
                  {'start': '2023-03-01', 'end': '2026-06-30', 'columns': 'year'},
                  {'start': '2024-02-15', 'end': '2024-09-03', 'columns': 'quarter'},
                  {'start': '2024-02-29', 'end': '2024-02-29'},
                  {'start': '2025-09-15', 'end': '2025-09-15'},
                  {'start': '2020-01-01', 'end': '2020-12-31'}):
            cf = self.rep('cash_flow', **q)
            self.check_cf(cf, 'cf %s' % q)

    def test_total_column(self):
        cf = self.rep('cash_flow', start='2024-01-01', end='2024-12-31', columns='quarter')
        self.assertEqual(cf['columns'][-1]['label'], 'Total')
        for r in cf['rows']:
            if 'values' not in r:
                continue
            v = r['values']
            if r['label'] == 'Cash at beginning of period':
                self.assertEqual(v[4], v[0])
            elif r['label'] == 'CASH AT END OF PERIOD':
                self.assertEqual(v[4], v[3])
            else:
                self.assertEqual(round(sum(v[:4]), 2), v[4], r['label'])
        for i in range(1, 4):
            self.assertEqual(self.v(cf, 'Cash at beginning of period', i), self.v(cf, 'CASH AT END OF PERIOD', i - 1))

    def test_every_cell_drills(self):
        n = 0
        for q in ({'start': '2025-01-01', 'end': '2025-12-31'},
                  {'start': '2024-01-01', 'end': '2024-06-30', 'columns': 'month'},
                  {'start': '2023-03-01', 'end': '2026-06-30', 'columns': 'year'},
                  {'start': '2025-07-01', 'end': '2025-09-30', 'compare': 'pp'},
                  {'start': '2025-01-01', 'end': '2025-06-30', 'columns': 'quarter', 'compare': 'py'}):
            rep = self.rep('cash_flow', **q)
            n += self.check_cells(rep, 'cf %s' % q)
        self.assertGreater(n, 200)

    def test_adjustment_rows_sum_to_total(self):
        cf = self.rep('cash_flow', start='2025-01-01', end='2025-12-31', columns='quarter')
        rows = cf['rows']
        idx = {r['label']: k for k, r in enumerate(rows)}
        groups = (('Adjustments to reconcile Net Income to Net Cash provided by operations:',
                   'Total Adjustments to reconcile Net Income to Net Cash provided by operations'),
                  ('INVESTING ACTIVITIES', 'Net cash provided by investing activities'),
                  ('FINANCING ACTIVITIES', 'Net cash provided by financing activities'))
        for head, tot in groups:
            for i in self.amount_cols(cf):
                s = round(sum(r['values'][i] for r in rows[idx[head] + 1:idx[tot]]), 2)
                self.assertEqual(s, rows[idx[tot]]['values'][i], '%s col %d' % (tot, i))


# =============================================================================== Trial Balance

class TrialBalance(Books):
    def check_tb(self, dt):
        tb = self.rep('trial_balance', end=dt)
        tot = self.row(tb, 'TOTAL')['values']
        self.assertEqual(tot[0], tot[1], dt)
        fs = self.fy_start(dt)
        prior_end = (d(fs) - datetime.timedelta(days=1)).isoformat()
        sd = sc = 0.0
        for r in tb['rows'][:-1]:
            name = r['label'].split(':')[-1]
            if name == 'Retained Earnings':
                exp = self.net(name, None, dt) + self.net(self.names_of(*PL_T), None, prior_end)
            elif self.atype(name) in PL_T:
                exp = self.net(name, fs, dt)
            else:
                exp = self.net(name, None, dt)
            dv, cv = r['values']
            self.assertTrue((dv is None) != (cv is None), r['label'])
            self.assertEqual((dv or 0) - (cv or 0), c2m(exp), '%s %s' % (dt, r['label']))
            sd += dv or 0
            sc += cv or 0
            for i in (0, 1):
                if r['values'][i] is not None:
                    self.assertEqual(self.post('/api/reports/drill', r['drill'][i])['total'], r['values'][i],
                                     '%s %s' % (dt, r['label']))
        self.assertEqual((round(sd, 2), round(sc, 2)), (tot[0], tot[1]))
        return tb

    def test_balances_and_matches_ledger(self):
        for dt in ('2023-12-31', '2024-01-01', '2024-02-29', '2025-06-30', '2026-06-30'):
            self.check_tb(dt)

    def test_natural_sides(self):
        tb = self.check_tb('2025-12-31')
        side = {r['label']: (0 if r['values'][0] is not None else 1) for r in tb['rows']}
        for n in ('Checking', 'Accounts Receivable', "Owner's Draw", 'Rent and Lease', 'Furniture and Equipment'):
            self.assertEqual(side[n], 0, n)
        for n in ('Accumulated Depreciation', 'Vehicle Loan', "Owner's Investment", 'Sales', 'Credit Card'):
            self.assertEqual(side[n], 1, n)
        self.assertIn('Utilities:Gas:Propane', side)

    def test_agrees_with_balance_sheet_and_pl(self):
        tb = self.rep('trial_balance', end='2025-12-31')
        bs = self.rep('balance_sheet', end='2025-12-31')
        v = {r['label']: (r['values'][0] or 0) - (r['values'][1] or 0) for r in tb['rows']}
        self.assertEqual(v['Checking'], self.v(bs, 'Checking'))
        self.assertEqual(-v['Vehicle Loan'], self.v(bs, 'Vehicle Loan'))
        self.assertEqual(-v['Retained Earnings'], self.v(bs, 'Retained Earnings'))
        pl = self.rep('profit_loss', start='2025-01-01', end='2025-12-31')
        self.assertEqual(-v['Sales'], self.v(pl, 'Sales'))

    def test_start_parameter_is_irrelevant(self):
        # the trial balance is an as-of report; a stray start date must not break it
        a = self.rep('trial_balance', end='2025-12-31')
        b = self.rep('trial_balance', start='2025-06-01', end='2025-12-31')
        self.assertEqual(a['rows'], b['rows'])


# =============================================================================== Transaction Detail

class TransactionDetail(Books):
    def test_each_account_type(self):
        s, e = '2025-01-01', '2025-06-30'
        before = '2024-12-31'
        td = self.rep('transaction_detail', start=s, end=e)
        seen = set()
        tdr = tcr = 0.0
        for g in td['groups']:
            name = g['account'].split(':')[-1]
            seen.add(self.atype(name))
            sign = -1 if self.atype(name) in CREDIT_NORMAL else 1
            is_pl = self.atype(name) in PL_T
            exp_begin = None if is_pl else c2m(sign * self.net(name, None, before))
            self.assertEqual(g['beginning_balance'], exp_begin, name)
            run = 0.0 if is_pl else exp_begin
            last = ''
            for r in g['rows']:
                self.assertGreaterEqual(r['date'], last)
                self.assertTrue(s <= r['date'] <= e)
                last = r['date']
                amt = sign * ((r['debit'] or 0) - (r['credit'] or 0))
                self.assertEqual(r['amount'], round(amt, 2))
                run = round(run + amt, 2)
                self.assertEqual(r['balance'], run, '%s %s' % (name, r['date']))
            self.assertEqual(g['ending_balance'], run, name)
            self.assertEqual(g['total_debit'], round(sum(r['debit'] or 0 for r in g['rows']), 2))
            self.assertEqual(g['total_credit'], round(sum(r['credit'] or 0 for r in g['rows']), 2))
            self.assertEqual(g['net_change'], c2m(sign * self.net(name, s, e)), name)
            if is_pl:
                self.assertEqual(g['ending_balance'], c2m(sign * self.net(name, s, e)), name)
            else:
                self.assertEqual(g['ending_balance'], c2m(sign * self.net(name, None, e)), name)
            tdr += g['total_debit']
            tcr += g['total_credit']
        self.assertEqual(round(tdr, 2), round(tcr, 2))
        for t in ASSET_T + LIAB_T + ('Equity',) + PL_T:
            self.assertIn(t, seen)

    def test_ending_balances_match_balance_sheet(self):
        td = self.rep('transaction_detail', start='2025-03-15', end='2025-10-20')
        bs = self.rep('balance_sheet', end='2025-10-20')
        got = {g['account'].split(':')[-1]: g for g in td['groups']}
        n = 0
        for r in bs['rows']:
            if r['kind'] == 'account' and r.get('account_id') and r['label'] != 'Retained Earnings':
                self.assertEqual(got[r['label']]['ending_balance'], r['values'][0], r['label'])
                n += 1
        self.assertGreater(n, 15)

    def test_single_account_filter_and_split(self):
        aid = self.aid('Vehicle Loan')
        td = self.rep('transaction_detail', start='2023-01-01', end='2023-12-31', account_id=aid)
        self.assertEqual(len(td['groups']), 1)
        g = td['groups'][0]
        self.assertEqual((g['beginning_balance'], g['ending_balance']), (0.0, 25000 - 400 * 7.0))
        self.assertEqual(g['rows'][0]['split'], '-Split-')
        self.assertEqual(g['rows'][0]['credit'], 25000.0)
        self.assertEqual(g['rows'][0]['balance'], 25000.0)
        # account with no activity in range but a balance still appears with its beginning balance
        td = self.rep('transaction_detail', start='2026-01-01', end='2026-01-31')
        g = [x for x in td['groups'] if x['account'] == 'Security Deposits'][0]
        self.assertEqual((g['beginning_balance'], g['rows'], g['ending_balance']), (3000.0, [], 3000.0))
        # filter on an account with nothing at all still returns the (empty) group
        td = self.rep('transaction_detail', start='2026-01-01', end='2026-01-31', account_id=self.aid('Savings'))
        self.assertEqual((len(td['groups']), td['groups'][0]['rows']), (1, []))

    def test_bad_account(self):
        self.call('GET', '/api/reports/transaction_detail', expect=404, account_id=999999)
        self.call('GET', '/api/reports/transaction_detail', expect=400, account_id='abc')

    def test_matches_drill_down(self):
        s, e = '2024-01-01', '2024-03-31'
        pl = self.rep('profit_loss', start=s, end=e)
        td = self.rep('transaction_detail', start=s, end=e, account_id=self.aid('Rent and Lease'))
        dr = self.post('/api/reports/drill', self.row(pl, 'Rent and Lease')['drill'][0])
        self.assertEqual(dr['count'], len(td['groups'][0]['rows']))
        self.assertEqual(dr['total'], td['groups'][0]['ending_balance'])


# =============================================================================== A/R aging

class ARAging(ApiCase):
    def setUp(self):
        super().setUp()
        self.c = self.post('/api/customers', {'name': 'Acme'})['id']

    def inv(self, date, amt, **kw):
        body = {'customer_id': self.c, 'date': date,
                'lines': [{'description': 'x', 'qty': 1, 'rate': amt, 'account_id': self.acct('Sales')}]}
        body.update(kw)
        return self.post('/api/invoices', body)

    def test_buckets(self):
        self.inv('2025-01-01', 100, due_date='2025-06-30')     # current (due today)
        self.inv('2025-01-02', 200, due_date='2025-06-29')     # 1 day
        self.inv('2025-01-03', 300, due_date='2025-05-31')     # 30 days
        self.inv('2025-01-04', 400, due_date='2025-05-30')     # 31
        self.inv('2025-01-05', 500, due_date='2025-05-01')     # 60
        self.inv('2025-01-06', 600, due_date='2025-04-30')     # 61
        self.inv('2025-01-07', 700, due_date='2025-04-01')     # 90
        self.inv('2025-01-08', 800, due_date='2025-03-31')     # 91
        self.inv('2025-07-01', 999)                            # after the as-of date
        ag = self.get('/api/reports/ar_aging', end='2025-06-30')
        self.assertEqual(self.row(ag, 'Acme')['values'], [100.0, 500.0, 900.0, 1300.0, 800.0, 3600.0])
        self.assertEqual(self.row(ag, 'TOTAL')['values'], [100.0, 500.0, 900.0, 1300.0, 800.0, 3600.0])

    def test_as_of_ignores_later_payments(self):
        i = self.inv('2025-01-01', 1000, due_date='2025-01-31')
        self.post('/api/payments', {'customer_id': self.c, 'date': '2025-03-10', 'amount': 400,
                                    'deposit_account_id': self.acct('Checking'),
                                    'applications': [{'invoice_id': i['id'], 'amount': 400}]})
        self.assertEqual(self.val(self.get('/api/reports/ar_aging', end='2025-02-28'), 'TOTAL', 5), 1000.0)
        self.assertEqual(self.val(self.get('/api/reports/ar_aging', end='2025-03-10'), 'TOTAL', 5), 600.0)

    def test_unapplied_payment_is_a_credit(self):
        self.post('/api/payments', {'customer_id': self.c, 'date': '2025-03-10', 'amount': 250,
                                    'deposit_account_id': self.acct('Checking'), 'applications': []})
        ag = self.get('/api/reports/ar_aging', end='2025-03-31')
        self.assertEqual(self.val(ag, 'TOTAL', 5), -250.0)
        self.assertEqual(self.val(self.get('/api/reports/balance_sheet', end='2025-03-31'), 'Accounts Receivable'),
                         -250.0)

    def test_journal_entry_to_ar_appears_in_aging(self):
        # A journal entry to A/R with a customer changes the customer's balance; QuickBooks lists it on
        # the aging report so that A/R Aging total == Balance Sheet A/R.
        self.inv('2025-01-01', 1000)
        self.journal('2025-02-01', ('Accounts Receivable', 300, 0, self.c), ('Sales', 0, 300))
        bs = self.get('/api/reports/balance_sheet', end='2025-03-31')
        ag = self.get('/api/reports/ar_aging', end='2025-03-31')
        self.assertEqual(self.val(bs, 'Accounts Receivable'), 1300.0)
        self.assertEqual(self.val(ag, 'TOTAL', 5), 1300.0)

    def test_payment_applied_to_later_dated_invoice(self):
        # prepayment on Mar 1, later applied to an invoice dated Apr 1.  As of Mar 31 the customer
        # has a 500 credit (the ledger says A/R = -500).
        p = self.post('/api/payments', {'customer_id': self.c, 'date': '2025-03-01', 'amount': 500,
                                        'deposit_account_id': self.acct('Checking'), 'applications': []})
        i = self.inv('2025-04-01', 500)
        self.put('/api/payments/%d' % p['id'], {'customer_id': self.c, 'date': '2025-03-01', 'amount': 500,
                                               'deposit_account_id': self.acct('Checking'),
                                               'applications': [{'invoice_id': i['id'], 'amount': 500}]})
        bs = self.get('/api/reports/balance_sheet', end='2025-03-31')
        ag = self.get('/api/reports/ar_aging', end='2025-03-31')
        self.assertEqual(self.val(bs, 'Accounts Receivable'), -500.0)
        self.assertEqual(self.val(ag, 'TOTAL', 5), -500.0)


# =============================================================================== roll-up / visibility edge cases

class Visibility(ApiCase):
    def test_parent_without_own_postings_is_header(self):
        p = self.acct('Utilities')
        self.post('/api/accounts', {'name': 'Electric', 'type': 'Expense', 'parent_id': p})
        self.journal('2025-01-10', ('Electric', 80, 0), ('Checking', 0, 80))
        pl = self.get('/api/reports/profit_loss', start='2025-01-01', end='2025-01-31')
        rows = {r['label']: r for r in pl['rows']}
        self.assertEqual(rows['Utilities']['kind'], 'header')
        self.assertEqual(rows['Total Utilities']['values'], [80.0])
        self.assertEqual(rows['Total Expenses']['values'], [80.0])

    def test_offsetting_subaccounts_do_not_hide_rows(self):
        # Gas has +100, its sub-account Propane has -100 (a reclass).  Both have activity and both
        # must be listed; the sub-tree netting to zero is not a reason to hide real balances.
        p = self.acct('Utilities')
        g = self.post('/api/accounts', {'name': 'Gas', 'type': 'Expense', 'parent_id': p})['id']
        self.post('/api/accounts', {'name': 'Propane', 'type': 'Expense', 'parent_id': g})
        self.journal('2025-01-10', ('Gas', 100, 0), ('Propane', 0, 100))
        self.journal('2025-01-11', ('Utilities', 40, 0), ('Checking', 0, 40))
        pl = self.get('/api/reports/profit_loss', start='2025-01-01', end='2025-01-31')
        labels = [r['label'] for r in pl['rows']]
        self.assertIn('Gas', labels)
        self.assertIn('Propane', labels)

    def test_section_whose_accounts_net_to_zero_still_listed(self):
        # two bank accounts +500 / -500: the section total is 0 but the accounts must be shown
        self.journal('2025-01-10', ('Checking', 500, 0), ('Savings', 0, 500))
        bs = self.get('/api/reports/balance_sheet', end='2025-01-31')
        self.assertEqual(self.val(bs, 'Checking'), 500.0)
        self.assertEqual(self.val(bs, 'Savings'), -500.0)
        self.assertEqual(self.val(bs, 'Total Bank Accounts'), 0.0)

    def test_subaccount_of_accumulated_depreciation_is_operating(self):
        # "Accumulated Depreciation" with per-asset sub-accounts is a common chart of accounts layout.
        ad = self.acct('Accumulated Depreciation')
        self.post('/api/accounts', {'name': 'Vehicles AD', 'type': 'Fixed Asset', 'parent_id': ad})
        self.journal('2025-01-31', ('Depreciation Expense', 300, 0), ('Vehicles AD', 0, 300))
        cf = self.get('/api/reports/cash_flow', start='2025-01-01', end='2025-01-31')
        self.assertEqual(self.val(cf, 'Net cash provided by operating activities'), 0.0)
        self.assertEqual(self.val(cf, 'Net cash provided by investing activities'), 0.0)

    def test_equity_subaccounts_and_obe(self):
        oi = self.acct("Owner's Investment")
        self.post('/api/accounts', {'name': 'Partner A', 'type': 'Equity', 'parent_id': oi})
        self.post('/api/accounts', {'name': 'Truck', 'type': 'Fixed Asset', 'opening_balance': 9000,
                                    'opening_date': '2025-01-01'})
        self.post('/api/accounts', {'name': 'Bank Loan', 'type': 'Long Term Liability', 'opening_balance': 4000,
                                    'opening_date': '2025-01-01'})
        self.journal('2025-01-05', ('Checking', 700, 0), ('Partner A', 0, 700))
        bs = self.get('/api/reports/balance_sheet', end='2025-01-31')
        self.assertEqual(self.val(bs, 'Opening Balance Equity'), 5000.0)
        self.assertEqual(self.val(bs, 'Bank Loan'), 4000.0)
        self.assertEqual(self.val(bs, "Total Owner's Investment"), 700.0)
        self.assertEqual(self.val(bs, 'Total Equity'), 5700.0)
        self.assertEqual(self.val(bs, 'TOTAL ASSETS'), self.val(bs, 'TOTAL LIABILITIES AND EQUITY'))

    def test_retained_earnings_subaccount_counts_in_equity(self):
        re_ = self.acct('Retained Earnings')
        self.post('/api/accounts', {'name': 'Appropriated', 'type': 'Equity', 'parent_id': re_})
        self.journal('2025-01-05', ('Checking', 700, 0), ('Appropriated', 0, 700))
        bs = self.get('/api/reports/balance_sheet', end='2025-01-31')
        self.assertEqual(self.val(bs, 'Total Equity'), 700.0)
        self.assertEqual(self.val(bs, 'TOTAL ASSETS'), self.val(bs, 'TOTAL LIABILITIES AND EQUITY'))

    def test_empty_books(self):
        for name in REPORTS + ('trial_balance', 'ar_aging', 'transaction_detail'):
            self.get('/api/reports/' + name, start='2025-01-01', end='2025-12-31')
            self.get('/api/reports/' + name)
        bs = self.get('/api/reports/balance_sheet', end='2025-12-31')
        self.assertEqual(self.val(bs, 'TOTAL ASSETS'), 0.0)


# =============================================================================== bad input never crashes

class BadInput(ApiCase):
    ALL = REPORTS + ('trial_balance', 'ar_aging', 'transaction_detail')

    def setUp(self):
        super().setUp()
        self.journal('2025-01-10', ('Checking', 500, 0), ('Sales', 0, 500))

    def status(self, name, **q):
        st, data = self.app.handle('GET', '/api/reports/' + name, {k: str(v) for k, v in q.items()}, None)
        return st

    def expect_all(self, want, names=None, **q):
        got = {n: self.status(n, **q) for n in (names or self.ALL)}
        bad = {n: s for n, s in got.items() if s not in want}
        self.assertEqual(bad, {}, 'query %s -> %s (wanted %s)' % (q, bad, want))

    def test_malformed_dates(self):
        for bad in ('garbage', '2025-13-01', '2025-02-30', '2023-02-29', '01/31/2025', '2025-1-5', '20250101'):
            self.expect_all((400,), start='2025-01-01', end=bad)
            self.expect_all((400,), names=REPORTS + ('transaction_detail',), start=bad, end='2025-01-31')

    def test_start_after_end(self):
        self.expect_all((400,), names=REPORTS + ('transaction_detail',), start='2025-02-01', end='2025-01-31')

    def test_unknown_columns_and_compare(self):
        for n in REPORTS:
            self.assertEqual(self.status(n, start='2025-01-01', end='2025-01-31', columns='week'), 400, n)
            self.assertEqual(self.status(n, start='2025-01-01', end='2025-01-31', compare='yoy'), 400, n)
            self.assertEqual(self.status(n, start='2025-01-01', end='2025-01-31', columns='total', compare='none'), 200)

    def test_too_many_columns_is_a_400(self):
        for n in REPORTS:
            self.assertEqual(self.status(n, start='2015-01-01', end='2025-12-31', columns='month'), 400, n)
            self.assertEqual(self.status(n, start='2021-01-01', end='2025-12-31', columns='month'), 200, n)

    def test_huge_range_total(self):
        self.expect_all((200,), start='1900-01-01', end='2999-12-31')

    def test_huge_range_with_columns(self):
        for cols in ('month', 'quarter', 'year'):
            self.expect_all((200, 400), names=REPORTS, start='1900-01-01', end='9999-12-31', columns=cols)

    def test_extreme_dates_with_compare(self):
        for cmp_ in ('pp', 'py'):
            self.expect_all((200, 400), names=REPORTS, start='0001-01-01', end='0001-12-31', compare=cmp_)
            self.expect_all((200, 400), names=REPORTS, start='9999-01-01', end='9999-12-31', compare=cmp_)

    def test_earliest_date(self):
        self.expect_all((200, 400), start='0001-01-01', end='0001-01-31')
        self.expect_all((200, 400), start='0001-01-01', end='2025-01-31')

    def test_latest_date(self):
        self.expect_all((200, 400), start='2025-01-01', end='9999-12-31')
        self.expect_all((200, 400), names=REPORTS, start='9999-01-01', end='9999-12-31', columns='month')

    def test_earliest_date_non_january_fiscal_year(self):
        self.put('/api/settings', {'fiscal_year_start_month': 7})
        self.expect_all((200, 400), end='0001-03-31')
        self.expect_all((200, 400), start='0001-01-01', end='0001-03-31', columns='quarter')

    def test_drill_bad_bodies(self):
        for body in ({}, {'segments': 'x'}, {'segments': [{'accounts': ['abc']}]},
                     {'segments': [{'accounts': [1], 'start': 'nope'}]},
                     {'segments': [{'accounts': [1], 'end': '2025-99-99'}]}):
            st, _ = self.app.handle('POST', '/api/reports/drill', {}, body)
            self.assertEqual(st, 400, body)
        for body in ({'segments': []}, {'segments': [{'accounts': []}]},
                     {'segments': [{'accounts': [999999], 'start': None, 'end': None, 'sign': 1}]}):
            st, data = self.app.handle('POST', '/api/reports/drill', {}, body)
            self.assertEqual((st, data.get('total')), (200, 0.0), body)

    def test_drill_malformed_segment_types(self):
        for body in ({'segments': [None]}, {'segments': ['x']}, {'segments': [{'accounts': 5}]},
                     {'segments': [{'accounts': [[1]]}]}):
            st, _ = self.app.handle('POST', '/api/reports/drill', {}, body)
            self.assertIn(st, (200, 400), body)

    def test_bad_fiscal_month(self):
        for m in (0, 13, 'x', None, 1.5):
            st, _ = self.app.handle('PUT', '/api/settings', {}, {'fiscal_year_start_month': m})
            self.assertIn(st, (200, 400), m)
        self.assertIn(self.get('/api/meta')['fiscal_year_start_month'], range(1, 13))


if __name__ == '__main__':
    unittest.main()
