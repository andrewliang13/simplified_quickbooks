"""Bills, products/inventory, sales tax, payroll, multi-currency and cash-basis reports."""
import unittest

from helpers import ApiCase


class Base(ApiCase):
    def setUp(self):
        super().setUp()
        self.cust = self.post('/api/customers', {'name': 'Acme'})['id']
        self.vend = self.post('/api/vendors', {'name': 'Supplier'})['id']
        self.chk = self.acct('Checking')

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
        self.assertEqual(self.val(r, 'TOTAL ASSETS'), self.val(r, 'TOTAL LIABILITIES AND EQUITY'))
        tb = self.get('/api/reports/trial_balance', end=end, **q)
        t = self.row(tb, 'TOTAL')['values']
        self.assertEqual(t[0], t[1])

    def all_cells_drill(self, name, **q):
        rep = self.get('/api/reports/' + name, **q)
        for row in rep['rows']:
            for i, d in enumerate(row.get('drill') or []):
                if d and 'segments' in d:
                    self.assertEqual(self.post('/api/reports/drill', d)['total'], row['values'][i],
                                     '%s %s col %d' % (name, row['label'], i))


class Bills(Base):
    def bill(self, amount, date='2026-01-10', **kw):
        body = {'vendor_id': self.vend, 'date': date, 'terms': 'Net 30',
                'lines': [{'description': 'Parts', 'amount': amount, 'account_id': self.acct('Repairs and Maintenance')}]}
        body.update(kw)
        return self.post('/api/bills', body)

    def test_bill_and_pay(self):
        b1 = self.bill(500, number='V-1')
        b2 = self.bill(300, date='2026-01-20')
        self.assertEqual((b1['total'], b1['due_date'], b1['status']), (500.0, '2026-02-09', 'Overdue'))
        self.post('/api/bills', {'vendor_id': self.vend, 'date': '2026-01-10', 'number': 'V-1',
                                 'lines': [{'amount': 1, 'account_id': self.acct('Travel')}]}, expect=409)
        self.assertEqual(self.bs('Accounts Payable'), 800.0)
        self.assertEqual(self.get('/api/vendors/%d' % self.vend)['balance'], 800.0)
        self.assertEqual(self.pl('Repairs and Maintenance'), 800.0)
        # pay oldest first from checking
        p = self.post('/api/bill_payments', {'vendor_id': self.vend, 'date': '2026-02-01', 'amount': 600,
                                             'pay_account_id': self.chk})
        self.assertEqual([(a['number'], a['amount']) for a in p['applications']], [('V-1', 500.0), ('', 100.0)])
        self.assertEqual(self.get('/api/bills/%d' % b2['id'])['balance'], 200.0)
        self.assertEqual((self.bs('Accounts Payable'), self.bs('Checking')), (200.0, -600.0))
        aging = self.get('/api/reports/ap_aging', end='2026-03-15')
        self.assertEqual(self.val(aging, 'Supplier', 5), 200.0)
        self.assertEqual(self.val(aging, 'Supplier', 1), 200.0)    # due Feb 19 -> 24 days
        self.assertEqual(self.drill_total(aging, 'Supplier', 5), 200.0)
        self.delete('/api/bills/%d' % b1['id'], expect=409)
        self.delete('/api/bill_payments/%d' % p['id'])
        self.delete('/api/bills/%d' % b1['id'])
        self.assertEqual(self.bs('Accounts Payable'), 300.0)
        self.balanced()

    def test_pay_by_card_and_feed_match(self):
        b = self.bill(80)
        self.post('/api/feed/import', {'account_id': self.chk, 'content': 'Date,Description,Amount\n01/25/2026,SUPPLIER INC,-80.00\n'})
        row = self.get('/api/feed', account_id=self.chk)['rows'][0]
        m = [x for x in row['matches'] if x['kind'] == 'bill']
        self.assertEqual(len(m), 1)
        t = self.post('/api/feed/%d/match' % row['id'], {'bill_id': b['id']})
        self.assertEqual(t['type'], 'Bill Payment')
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['status'], 'Paid')
        self.post('/api/feed/%d/undo' % row['id'])
        self.assertEqual(self.get('/api/bills/%d' % b['id'])['balance'], 80.0)
        p = self.post('/api/bill_payments', {'vendor_id': self.vend, 'date': '2026-01-26', 'amount': 80,
                                             'pay_account_id': self.acct('Credit Card')})
        self.assertEqual(p['pay_account'], 'Credit Card')
        self.assertEqual(self.bs('Credit Card'), 80.0)
        self.post('/api/bill_payments', {'vendor_id': self.vend, 'amount': 1,
                                         'pay_account_id': self.acct('Undeposited Funds')}, expect=400)


class Inventory(Base):
    def setUp(self):
        super().setUp()
        self.widget = self.post('/api/items', {'name': 'Widget', 'type': 'inventory', 'sales_price': 25,
                                               'purchase_cost': 10, 'qty_on_hand': 10, 'as_of': '2025-12-31',
                                               'taxable': True})
        self.svc = self.post('/api/items', {'name': 'Install', 'type': 'service', 'sales_price': 50,
                                            'income_account_id': self.acct('Service Income')})

    def test_buy_sell_value(self):
        self.assertEqual((self.widget['qty_on_hand'], self.widget['value']), (10, 100.0))
        self.assertEqual(self.bs('Inventory Asset', end='2025-12-31'), 100.0)
        # buy 10 more at 13 -> 20 on hand, value 230, average 11.50
        self.post('/api/bills', {'vendor_id': self.vend, 'date': '2026-01-05',
                                 'lines': [{'item_id': self.widget['id'], 'qty': 10, 'rate': 13}]})
        w = self.get('/api/items/%d' % self.widget['id'])
        self.assertEqual((w['qty_on_hand'], w['value'], w['avg_cost']), (20, 230.0, 11.5))
        # sell 4 widgets + 1 install: income 100 + 50, COGS 4 x 11.50 = 46
        inv = self.post('/api/invoices', {'customer_id': self.cust, 'date': '2026-01-10', 'lines': [
            {'item_id': self.widget['id'], 'qty': 4}, {'item_id': self.svc['id']}]})
        self.assertEqual(inv['total'], 150.0)
        self.assertEqual((self.pl('Sales'), self.pl('Service Income'), self.pl('Cost of Goods Sold')), (100.0, 50.0, 46.0))
        self.assertEqual(self.bs('Inventory Asset'), 184.0)
        val = self.get('/api/reports/inventory_valuation', end='2026-12-31')
        self.assertEqual(self.row(val, 'Widget')['values'], [16, 184.0, 11.5])
        self.assertEqual(self.drill_total(val, 'Widget', 1), 184.0)
        # editing the invoice re-costs it; deleting puts the stock back
        self.put('/api/invoices/%d' % inv['id'], {'customer_id': self.cust, 'date': '2026-01-10',
                                                  'lines': [{'item_id': self.widget['id'], 'qty': 2}]})
        self.assertEqual((self.pl('Cost of Goods Sold'), self.bs('Inventory Asset')), (23.0, 207.0))
        self.delete('/api/invoices/%d' % inv['id'])
        self.assertEqual(self.get('/api/items/%d' % self.widget['id'])['qty_on_hand'], 20)
        # count adjustment: 2 lost
        w = self.post('/api/items/%d/adjust' % self.widget['id'], {'date': '2026-02-01', 'qty_change': -2})
        self.assertEqual((w['qty_on_hand'], w['value']), (18, 207.0))
        self.balanced()
        self.delete('/api/items/%d' % self.widget['id'], expect=409)
        self.put('/api/items/%d' % self.widget['id'], {'type': 'service'}, expect=400)

    def test_items_crud(self):
        self.post('/api/items', {'name': 'widget', 'type': 'service'}, expect=409)
        self.post('/api/items', {'name': 'X', 'type': 'gadget'}, expect=400)
        it = self.post('/api/items', {'name': 'Consult', 'sales_price': 120})
        it = self.put('/api/items/%d' % it['id'], {'sales_price': 150})
        self.assertEqual((it['sales_price'], it['type']), (150.0, 'service'))
        self.delete('/api/items/%d' % it['id'])


class SalesTax(Base):
    def test_tax_on_invoice_and_payment(self):
        rate = self.post('/api/tax_rates', {'name': 'CA 8.25%', 'rate': '8.25', 'agency': 'CDTFA'})
        inv = self.post('/api/invoices', {'customer_id': self.cust, 'date': '2026-03-01', 'tax_rate_id': rate['id'],
                                          'lines': [{'description': 'Goods', 'amount': 200, 'account_id': self.acct('Sales'),
                                                     'taxable': True},
                                                    {'description': 'Labor', 'amount': 100,
                                                     'account_id': self.acct('Service Income'), 'taxable': False}]})
        self.assertEqual((inv['subtotal'], inv['tax'], inv['total']), (300.0, 16.5, 316.5))
        self.assertEqual(self.bs('Sales Tax Payable'), 16.5)
        self.assertEqual(self.bs('Accounts Receivable'), 316.5)
        self.assertEqual(self.pl('Total Income'), 300.0)
        rep = self.get('/api/reports/sales_tax', start='2026-01-01', end='2026-03-31')
        self.assertEqual(self.row(rep, 'CA 8.25%')['values'], [200.0, 100.0, 16.5])
        self.post('/api/sales_tax/payments', {'date': '2026-04-15', 'amount': 16.5, 'bank_account_id': self.chk})
        self.assertEqual(self.bs('Sales Tax Payable', end='2026-04-30'), 0.0)
        rep = self.get('/api/reports/sales_tax', start='2026-04-01', end='2026-04-30')
        self.assertEqual(self.val(rep, 'Sales tax paid in this period', 2), 16.5)
        self.assertEqual(self.val(rep, 'Sales tax owed as of Apr 30, 2026', 2), 0.0)
        self.put('/api/tax_rates/%d' % rate['id'], {'rate': 9}, expect=400)   # used: rate locked
        self.delete('/api/tax_rates/%d' % rate['id'], expect=409)
        self.balanced()


class Payroll(Base):
    def test_paycheck_math_and_postings(self):
        e = self.post('/api/employees', {'name': 'Pat Lee', 'pay_type': 'hourly', 'pay_rate': 25,
                                         'fed_pct': 10, 'state_pct': 4})
        c = self.post('/api/payroll/calculate', {'employee_id': e['id'], 'pay_date': '2026-01-15', 'hours': 80})
        # gross 2000; SS 6.2% = 124; Medicare 1.45% = 29; fed 200; state 80; net 1567
        self.assertEqual((c['gross'], c['ss_ee'], c['med_ee'], c['fed_wh'], c['state_wh'], c['net']),
                         (2000.0, 124.0, 29.0, 200.0, 80.0, 1567.0))
        # employer: SS 124 + Medicare 29 + FUTA 0.6% = 12 + SUTA 2.7% = 54
        self.assertEqual((c['ss_er'], c['med_er'], c['futa'], c['suta']), (124.0, 29.0, 12.0, 54.0))
        p = self.post('/api/paychecks', {'employee_id': e['id'], 'pay_date': '2026-01-15', 'hours': 80,
                                         'bank_account_id': self.chk, 'check_no': '1001'})
        self.assertEqual((p['net'], p['employer_taxes']), (1567.0, 219.0))
        self.assertEqual(self.bs('Checking'), -1567.0)
        self.assertEqual(self.bs('Payroll Liabilities'), 433.0 + 219.0)
        self.assertEqual((self.pl('Payroll Expenses'), self.pl('Payroll Tax Expense')), (2000.0, 219.0))
        # FUTA/SUTA stop at the 7,000 wage base: 3 more 2,000 paychecks -> 4th taxed on 1,000 only
        for d in ('2026-01-29', '2026-02-12'):
            self.post('/api/paychecks', {'employee_id': e['id'], 'pay_date': d, 'hours': 80, 'bank_account_id': self.chk})
        c = self.post('/api/payroll/calculate', {'employee_id': e['id'], 'pay_date': '2026-02-26', 'hours': 80})
        self.assertEqual((c['futa'], c['suta']), (6.0, 27.0))
        # override a withholding and pay the liabilities
        p2 = self.post('/api/paychecks', {'employee_id': e['id'], 'pay_date': '2026-02-26', 'hours': 80,
                                          'fed_wh': 150, 'bank_account_id': self.chk})
        self.assertEqual(p2['fed_wh'], 150.0)
        owed = self.bs('Payroll Liabilities')
        self.post('/api/payroll/liability_payments', {'date': '2026-03-01', 'amount': owed, 'bank_account_id': self.chk})
        self.assertEqual(self.bs('Payroll Liabilities'), 0.0)
        summ = self.get('/api/reports/payroll_summary', start='2026-01-01', end='2026-03-31')
        self.assertEqual(self.val(summ, 'Pat Lee', 0), 8000.0)
        self.assertEqual(self.drill_total(summ, 'Pat Lee', 6), self.val(summ, 'Pat Lee', 6))
        self.delete('/api/employees/%d' % e['id'], expect=409)
        self.delete('/api/paychecks/%d' % p2['id'])
        self.balanced()

    def test_withholding_cannot_exceed_gross(self):
        e = self.post('/api/employees', {'name': 'Sam', 'pay_type': 'salary', 'pay_rate': 1000})
        self.post('/api/paychecks', {'employee_id': e['id'], 'pay_date': '2026-01-15', 'fed_wh': 2000,
                                     'bank_account_id': self.chk}, expect=400)


class Currency(Base):
    def test_foreign_invoice_with_exchange_gain(self):
        eu = self.post('/api/customers', {'name': 'Euro GmbH', 'currency': 'eur'})
        self.assertEqual(eu['currency'], 'EUR')
        self.post('/api/invoices', {'customer_id': eu['id'], 'date': '2026-01-10',
                                    'lines': [{'amount': 1000, 'account_id': self.acct('Sales')}]}, expect=400)
        inv = self.post('/api/invoices', {'customer_id': eu['id'], 'date': '2026-01-10', 'exchange_rate': '1.10',
                                          'lines': [{'amount': 1000, 'account_id': self.acct('Sales')}]})
        self.assertEqual((inv['currency'], inv['total'], inv['total_home']), ('EUR', 1000.0, 1100.0))
        self.assertEqual((self.pl('Sales'), self.bs('Accounts Receivable')), (1100.0, 1100.0))
        # paid 400 EUR at 1.20 then 600 EUR at 1.05
        self.post('/api/payments', {'customer_id': eu['id'], 'date': '2026-02-01', 'amount': 400,
                                    'exchange_rate': '1.20', 'deposit_account_id': self.chk})
        self.assertEqual((self.bs('Checking'), self.bs('Accounts Receivable')), (480.0, 660.0))
        self.assertEqual(self.pl('Exchange Gain or Loss'), -40.0)      # gain shows as negative expense
        self.post('/api/payments', {'customer_id': eu['id'], 'date': '2026-03-01', 'amount': 600,
                                    'exchange_rate': '1.05', 'deposit_account_id': self.chk})
        self.assertEqual((self.bs('Checking'), self.bs('Accounts Receivable')), (1110.0, 0.0))
        self.assertEqual(self.pl('Exchange Gain or Loss'), -10.0)      # +40 gain, -30 loss
        self.assertEqual(self.get('/api/invoices/%d' % inv['id'])['status'], 'Paid')
        self.put('/api/customers/%d' % eu['id'], {'currency': 'USD'}, expect=400)
        self.balanced()


class CashBasis(Base):
    def test_cash_basis_follows_payments(self):
        rate = self.post('/api/tax_rates', {'name': 'Tax 10', 'rate': 10})
        inv = self.post('/api/invoices', {'customer_id': self.cust, 'date': '2026-01-10', 'tax_rate_id': rate['id'],
                                          'lines': [{'amount': 600, 'account_id': self.acct('Sales')},
                                                    {'amount': 400, 'account_id': self.acct('Service Income'),
                                                     'taxable': False}]})
        self.assertEqual(inv['total'], 1060.0)
        self.post('/api/bills', {'vendor_id': self.vend, 'date': '2026-01-12',
                                 'lines': [{'amount': 300, 'account_id': self.acct('Rent and Lease')}]})
        self.assertEqual(self.pl('Net Income', basis='cash'), 0.0)
        self.assertEqual(self.pl('Net Income'), 700.0)
        # customer pays half (530) in February
        self.post('/api/payments', {'customer_id': self.cust, 'date': '2026-02-05', 'amount': 530,
                                    'deposit_account_id': self.chk})
        self.assertEqual(self.pl('Sales', basis='cash'), 300.0)
        self.assertEqual(self.pl('Service Income', basis='cash'), 200.0)
        self.assertEqual(self.pl('Total Income', start='2026-01-01', end='2026-01-31', basis='cash'), 0.0)
        self.assertEqual(self.bs('Sales Tax Payable', basis='cash'), 30.0)
        self.assertEqual(self.bs('Accounts Receivable', basis='cash'), 0.0)
        self.assertEqual(self.bs('Accounts Payable', basis='cash'), 0.0)
        # unapplied prepayment stays on A/R as a credit
        self.post('/api/payments', {'customer_id': self.cust, 'date': '2026-02-10', 'amount': 1000,
                                    'deposit_account_id': self.chk,
                                    'applications': [{'invoice_id': inv['id'], 'amount': 530}]})
        self.assertEqual(self.bs('Accounts Receivable', basis='cash'), -470.0)
        for basis in ('cash', 'accrual'):
            self.balanced(basis=basis)
            for name in ('profit_loss', 'balance_sheet', 'cash_flow'):
                self.all_cells_drill(name, start='2026-01-01', end='2026-12-31', basis=basis, columns='month')
        cf = self.get('/api/reports/cash_flow', start='2026-01-01', end='2026-12-31', basis='cash')
        self.assertEqual(self.val(cf, 'CASH AT END OF PERIOD'), 1530.0)
        td = self.get('/api/reports/transaction_detail', start='2026-01-01', end='2026-12-31', basis='cash',
                      account_id=self.acct('Sales'))
        self.assertEqual(td['groups'][0]['ending_balance'], 600.0)


class PdfImport(Base):
    def test_builtin_pdf_text_and_edited_rows(self):
        from sqb import pdftext
        pdf = make_pdf(['Statement period December 13, 2025 through January 14, 2026',
                        'DEPOSITS AND ADDITIONS', '12/20 Payroll deposit 1,500.00',
                        'WITHDRAWALS', '01/05 Office Depot 150.25'])
        self.assertIn('Office Depot', pdftext.extract_text(pdf))
        import base64
        pv = self.post('/api/feed/preview', {'account_id': self.chk, 'filename': 's.pdf',
                                             'content_b64': base64.b64encode(pdf).decode()})
        self.assertEqual([(r['date'], r['amount']) for r in pv['rows']], [('2025-12-20', 1500.0), ('2026-01-05', -150.25)])
        # the user corrects a row in the preview and imports exactly what they see
        rows = pv['rows']
        rows[1]['amount'] = -150.52
        r = self.post('/api/feed/import', {'account_id': self.chk, 'filename': 's.pdf', 'rows': rows})
        self.assertEqual(r['imported'], 2)
        feed = self.get('/api/feed', account_id=self.chk)['rows']
        self.assertIn(-150.52, [x['amount'] for x in feed])
        self.post('/api/feed/import', {'account_id': self.chk, 'rows': [{'date': 'bad', 'description': 'x', 'amount': 1}]},
                  expect=400)

    def test_real_browser_pdf(self):
        """A statement printed to PDF by Chrome: flipped coordinates, embedded fonts with width tables."""
        import os
        from sqb import parsers, pdftext
        data = open(os.path.join(os.path.dirname(__file__), 'fixtures', 'chrome_statement.pdf'), 'rb').read()
        text = pdftext.extract_text(data)
        self.assertIn('12/20 Payroll deposit ACME 1,500.00 2,500.00', text)
        rows = parsers.parse_pdf_text(text)
        self.assertEqual([(r['date'], r['amount']) for r in rows],
                         [('2025-12-20', 150000), ('2026-01-05', -15025), ('2026-01-09', -50000)])


def make_pdf(lines):
    """A small but real PDF (compressed content stream, standard font) for the parser tests."""
    import zlib
    ops = ['BT', '/F1 10 Tf', '72 720 Td']
    for i, ln in enumerate(lines):
        ops.append('(%s) Tj' % ln.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)'))
        ops.append('0 -14 Td')
    ops.append('ET')
    stream = zlib.compress('\n'.join(ops).encode('latin-1'))
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R '
            b'/Resources << /Font << /F1 5 0 R >> >> >>',
            b'<< /Length %d /Filter /FlateDecode >>\nstream\n' % len(stream) + stream + b'\nendstream',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    out, offsets = b'%PDF-1.4\n', []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % i + o + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1)
    out += b''.join(b'%010d 00000 n \n' % o for o in offsets)
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, xref)
    return out


if __name__ == '__main__':
    unittest.main()
