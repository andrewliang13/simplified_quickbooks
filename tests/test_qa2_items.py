"""QA pass 2: products & services, inventory at average cost, sales tax.

Tests that fail here expose real bugs (see the docstring of each failing test)."""
import unittest

from helpers import ApiCase


class Base(ApiCase):
    def setUp(self):
        super().setUp()
        self.cust = self.post('/api/customers', {'name': 'Acme'})['id']
        self.vend = self.post('/api/vendors', {'name': 'Supplier'})['id']
        self.chk = self.acct('Checking')

    # -- helpers
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

    def item(self, name='Widget', **kw):
        body = {'name': name, 'type': 'inventory', 'sales_price': 25, 'purchase_cost': 10}
        body.update(kw)
        return self.post('/api/items', body)

    def bill(self, date, *lines, **kw):
        body = {'vendor_id': self.vend, 'date': date, 'lines': list(lines)}
        body.update(kw)
        return self.post('/api/bills', body)

    def inv(self, date, *lines, **kw):
        body = {'customer_id': self.cust, 'date': date, 'lines': list(lines)}
        body.update(kw)
        return self.post('/api/invoices', body)

    def it(self, iid):
        return self.get('/api/items/%d' % iid)

    def consistent(self, end='2026-12-31'):
        """Inventory Valuation total == balance sheet Inventory Asset (accrual), books balanced."""
        val = self.get('/api/reports/inventory_valuation', end=end)
        total = self.val(val, 'TOTAL', 1)
        self.assertEqual(total, self.bs('Inventory Asset', end=end))
        self.assertEqual(self.drill_total(val, 'TOTAL', 1), total)
        self.balanced(end)
        return total


# ======================================================================= items

class Items(Base):
    def test_defaults_and_description_flow_to_lines(self):
        svc = self.post('/api/items', {'name': 'Consulting', 'type': 'service', 'sales_price': 120,
                                       'description': 'Hourly consulting',
                                       'income_account_id': self.acct('Service Income')})
        self.assertEqual(svc['income_account_id'], self.acct('Service Income'))
        self.assertFalse(svc['taxable'])
        inv = self.inv('2026-02-01', {'item_id': svc['id'], 'qty': 2})
        d = self.get('/api/invoices/%d' % inv['id'])
        self.assertEqual(d['lines'][0]['description'], 'Hourly consulting')
        self.assertEqual(d['lines'][0]['account_id'], self.acct('Service Income'))
        self.assertEqual(d['total'], 240.0)
        # explicit rate and description win over the item defaults
        inv2 = self.inv('2026-02-01', {'item_id': svc['id'], 'qty': 1, 'rate': 99, 'description': 'Custom'})
        d2 = self.get('/api/invoices/%d' % inv2['id'])
        self.assertEqual((d2['lines'][0]['description'], d2['total']), ('Custom', 99.0))

    def test_noninventory_purchase_uses_expense_account(self):
        ni = self.post('/api/items', {'name': 'Paper', 'type': 'non_inventory', 'purchase_cost': 4,
                                      'expense_account_id': self.acct('Office Supplies and Software')})
        b = self.bill('2026-01-03', {'item_id': ni['id'], 'qty': 5})
        self.assertEqual(b['total'], 20.0)
        self.assertNotIn('qty_on_hand', self.it(ni['id']))
        self.assertEqual(self.pl('Office Supplies and Software'), 20.0)
        # without an expense account the bill line needs an explicit account (clear 400, no 500)
        bare = self.post('/api/items', {'name': 'Bare', 'type': 'non_inventory', 'purchase_cost': 1})
        self.post('/api/bills', {'vendor_id': self.vend, 'date': '2026-01-03',
                                 'lines': [{'item_id': bare['id']}]}, expect=400)
        self.balanced()

    def _has(self, name):
        try:
            self.acct(name)
            return True
        except KeyError:
            return False

    def test_type_change_rules(self):
        s = self.post('/api/items', {'name': 'Thing', 'type': 'service'})
        self.assertEqual(self.put('/api/items/%d' % s['id'], {'type': 'non_inventory'})['type'], 'non_inventory')
        self.assertEqual(self.put('/api/items/%d' % s['id'], {'type': 'inventory'})['type'], 'inventory')
        w = self.item('W2', qty_on_hand=3, as_of='2026-01-01')
        self.put('/api/items/%d' % w['id'], {'type': 'service'}, expect=400)
        self.delete('/api/items/%d' % w['id'], expect=409)
        self.put('/api/items/%d' % w['id'], {'type': 'bogus'}, expect=400)

    def test_qty_on_hand_rejected_for_service_and_not_created(self):
        self.post('/api/items', {'name': 'Svc', 'type': 'service', 'qty_on_hand': 5}, expect=400)
        names = [i['name'] for i in self.get('/api/items', include_inactive=1)]
        self.assertNotIn('Svc', names)

    def test_duplicate_name_case_insensitive(self):
        self.item('Widget')
        self.post('/api/items', {'name': 'WIDGET'}, expect=409)

    def test_inactive_item_blocked_on_new_docs_but_ok_on_existing(self):
        w = self.item(qty_on_hand=10, as_of='2026-01-01')
        inv = self.inv('2026-02-01', {'item_id': w['id'], 'qty': 1})
        self.put('/api/items/%d' % w['id'], {'active': False})
        self.assertNotIn(w['id'], [i['id'] for i in self.get('/api/items')])
        self.post('/api/invoices', {'customer_id': self.cust, 'date': '2026-02-02',
                                    'lines': [{'item_id': w['id'], 'qty': 1}]}, expect=400)
        self.post('/api/bills', {'vendor_id': self.vend, 'date': '2026-02-02',
                                 'lines': [{'item_id': w['id'], 'qty': 1}]}, expect=400)
        self.put('/api/invoices/%d' % inv['id'], {'customer_id': self.cust, 'date': '2026-02-01',
                                                  'lines': [{'item_id': w['id'], 'qty': 2}]})
        self.assertEqual(self.it(w['id'])['qty_on_hand'], 8)

    def test_malformed_item_input_no_500(self):
        bad = [
            {'name': 'A', 'sales_price': 'abc'}, {'name': 'A', 'sales_price': -1},
            {'name': 'A', 'type': ['inventory']}, {'name': 'A', 'type': {'x': 1}},
            {'name': ['A']}, {'name': ''}, {'name': 'A', 'taxable': 'maybe'},
            {'name': 'A', 'income_account_id': 'x'}, {'name': 'A', 'income_account_id': 999999},
            {'name': 'A', 'income_account_id': self.acct('Checking')},
            {'name': 'A', 'type': 'inventory', 'qty_on_hand': 'NaN'},
            {'name': 'A', 'type': 'inventory', 'qty_on_hand': -5},
            {'name': 'A', 'type': 'inventory', 'qty_on_hand': 'Infinity'},
            {'name': 'A', 'type': 'inventory', 'qty_on_hand': 5, 'as_of': '2026-13-45'},
            {'name': 'A', 'type': 'inventory', 'asset_account_id': self.acct('Checking')},
            {'name': 'A', 'purchase_cost': '1e400'},
        ]
        for b in bad:
            st, data = self.app.handle('POST', '/api/items', {}, b)
            self.assertEqual(st, 400 if b.get('name') != ['A'] else 400, (b, st, data))
        w = self.item(qty_on_hand=2, as_of='2026-01-01')
        for b in ({'qty_change': 'x'}, {'qty_change': 0}, {'qty_change': [1]}, {'qty_change': 1, 'date': 'nope'},
                  {'qty_change': 1, 'account_id': 'zz'}, {'qty_change': 1, 'account_id': 999999},
                  {'qty_change': 1, 'unit_cost': 'abc'}):
            st, data = self.app.handle('POST', '/api/items/%d/adjust' % w['id'], {}, b)
            self.assertEqual(st, 400, (b, st, data))
        svc = self.post('/api/items', {'name': 'S'})
        st, _ = self.app.handle('POST', '/api/items/%d/adjust' % svc['id'], {}, {'qty_change': 1})
        self.assertEqual(st, 400)
        for b in ({'lines': 'x'}, {'lines': ['x']}, {'lines': [{'item_id': 'abc'}]},
                  {'lines': [{'item_id': 999999}]}, {'lines': [{'item_id': w['id'], 'qty': 'abc'}]},
                  {'lines': [{'item_id': w['id'], 'qty': {'a': 1}}]},
                  {'lines': [{'item_id': w['id'], 'qty': 2, 'rate': 1, 'amount': 5}]},
                  {'lines': [{'item_id': w['id'], 'qty': 1}], 'tax_rate_id': 'abc'},
                  {'lines': [{'item_id': w['id'], 'qty': 1}], 'tax_rate_id': 99999},
                  {'lines': [{'item_id': w['id'], 'qty': 1}], 'tax_rate_id': [1]}):
            body = dict(b, customer_id=self.cust, date='2026-02-01')
            st, data = self.app.handle('POST', '/api/invoices', {}, body)
            self.assertEqual(st, 400, (b, st, data))
        self.consistent()

    def test_adjust_unknown_item_is_404(self):
        """Minor: adjusting a missing item answers 400 'Unknown product or service' instead of 404."""
        st, _ = self.app.handle('POST', '/api/items/99999/adjust', {}, {'qty_change': 1})
        self.assertEqual(st, 404)


# ======================================================================= inventory

class Inventory(Base):
    def test_average_cost_several_purchases(self):
        w = self.item(qty_on_hand=10, as_of='2025-12-31')                 # 10 @ 10 = 100
        self.bill('2026-01-05', {'item_id': w['id'], 'qty': 5, 'rate': 13})   # +65 -> 15 / 165 avg 11
        self.bill('2026-01-06', {'item_id': w['id'], 'qty': 2.5, 'rate': 4})  # +10 -> 17.5 / 175 avg 10
        x = self.it(w['id'])
        self.assertEqual((x['qty_on_hand'], x['value'], x['avg_cost']), (17.5, 175.0, 10.0))
        self.inv('2026-01-10', {'item_id': w['id'], 'qty': 7.5})                 # COGS 75
        self.assertEqual(self.pl('Cost of Goods Sold'), 75.0)
        self.assertEqual(self.consistent(), 100.0)
        self.bill('2026-01-11', {'item_id': w['id'], 'qty': 10, 'rate': 16})     # 20 / 260 avg 13
        self.inv('2026-01-12', {'item_id': w['id'], 'qty': 20})                  # all out: COGS 260
        x = self.it(w['id'])
        self.assertEqual((x['qty_on_hand'], x['value']), (0, 0.0))
        self.assertEqual(self.pl('Cost of Goods Sold'), 335.0)
        self.consistent()

    def test_thirds_rounding_clears_to_zero(self):
        w = self.item()
        self.bill('2026-01-01', {'item_id': w['id'], 'qty': 3, 'amount': 100})
        for d in ('2026-01-02', '2026-01-03', '2026-01-04'):
            self.inv(d, {'item_id': w['id'], 'qty': 1})
        x = self.it(w['id'])
        self.assertEqual((x['qty_on_hand'], x['value']), (0, 0.0))
        self.assertEqual(self.pl('Cost of Goods Sold'), 100.0)
        self.consistent()

    def test_fractional_quantities(self):
        w = self.item()
        self.bill('2026-01-01', {'item_id': w['id'], 'qty': 0.3, 'rate': 10})
        self.bill('2026-01-01', {'item_id': w['id'], 'qty': 0.1, 'rate': 10})
        self.inv('2026-01-02', {'item_id': w['id'], 'qty': 0.4})
        x = self.it(w['id'])
        self.assertEqual((x['qty_on_hand'], x['value']), (0, 0.0))
        self.consistent()

    def test_sale_back_dated_between_purchases(self):
        w = self.item()
        self.bill('2026-01-01', {'item_id': w['id'], 'qty': 10, 'rate': 10})
        self.bill('2026-01-20', {'item_id': w['id'], 'qty': 10, 'rate': 20})
        self.inv('2026-01-10', {'item_id': w['id'], 'qty': 5})   # average on Jan 10 = 10
        self.assertEqual(self.pl('Cost of Goods Sold'), 50.0)
        self.assertEqual(self.consistent('2026-01-15'), 50.0)
        self.assertEqual(self.consistent(), 250.0)

    def test_back_dated_purchase_recosts_later_sale(self):
        """BUG: a sale costed while nothing was on hand (falls back to the item's purchase cost) keeps that
        cost after an earlier-dated bill is entered. QuickBooks Online recalculates COGS of later sales."""
        w = self.item(purchase_cost=10)
        self.inv('2026-01-10', {'item_id': w['id'], 'qty': 5})
        self.bill('2026-01-05', {'item_id': w['id'], 'qty': 10, 'rate': 12})
        self.assertEqual(self.pl('Cost of Goods Sold'), 60.0)        # 5 x 12
        x = self.it(w['id'])
        self.assertEqual((x['qty_on_hand'], x['value'], x['avg_cost']), (5, 60.0, 12.0))
        self.consistent()

    def test_edit_bill_cost_after_sale_recosts_sale(self):
        """BUG: changing the cost on a bill dated before a sale does not re-cost the sale; the remaining
        units absorb the whole difference (average cost jumps from 20 to 30)."""
        w = self.item()
        b = self.bill('2026-01-01', {'item_id': w['id'], 'qty': 10, 'rate': 10})
        self.inv('2026-01-10', {'item_id': w['id'], 'qty': 5})
        self.put('/api/bills/%d' % b['id'], {'vendor_id': self.vend, 'date': '2026-01-01',
                                             'lines': [{'item_id': w['id'], 'qty': 10, 'rate': 20}]})
        x = self.it(w['id'])
        self.assertEqual((x['value'], x['avg_cost']), (100.0, 20.0))
        self.assertEqual(self.pl('Cost of Goods Sold'), 100.0)
        self.consistent()

    def test_negative_stock_then_purchase_leaves_no_value_at_zero_qty(self):
        """BUG: selling more than on hand and then receiving the shortfall at a different cost leaves
        quantity 0 with a dollar value stuck in Inventory Asset."""
        w = self.item(qty_on_hand=10, as_of='2025-12-31')     # 10 @ 10
        self.inv('2026-01-10', {'item_id': w['id'], 'qty': 15})   # 5 short; COGS 150 at avg 10
        x = self.it(w['id'])
        self.assertEqual((x['qty_on_hand'], x['value']), (-5, -50.0))
        self.consistent()
        self.bill('2026-01-15', {'item_id': w['id'], 'qty': 5, 'rate': 20})
        x = self.it(w['id'])
        self.assertEqual(x['qty_on_hand'], 0)
        self.assertEqual(x['value'], 0.0)          # actual: 50.00 with nothing on hand
        self.consistent()

    def test_zero_cost_stock_sold_at_zero_cost(self):
        """BUG: stock received at zero cost is costed at the item's purchase price when sold (unit_cost
        falls back to purchase_cost when value <= 0), driving Inventory Asset negative with stock on hand."""
        w = self.item(purchase_cost=0, qty_on_hand=10, as_of='2026-01-01')    # free samples
        self.assertEqual(self.it(w['id'])['value'], 0.0)
        self.put('/api/items/%d' % w['id'], {'purchase_cost': 8})
        self.inv('2026-01-10', {'item_id': w['id'], 'qty': 5})
        self.assertEqual(self.pl('Cost of Goods Sold'), 0.0)
        x = self.it(w['id'])
        self.assertEqual((x['qty_on_hand'], x['value']), (5, 0.0))
        self.consistent()

    def test_zero_cost_bill_line(self):
        """BUG (same root cause): a $0 bill line for inventory, then a sale, costs at purchase_cost."""
        w = self.item(purchase_cost=8)
        self.bill('2026-01-01', {'item_id': w['id'], 'qty': 10, 'rate': 0},
                  {'description': 'freight', 'amount': 5, 'account_id': self.acct('Cost of Goods Sold')})
        self.inv('2026-01-10', {'item_id': w['id'], 'qty': 5})
        x = self.it(w['id'])
        self.assertGreaterEqual(x['value'], 0.0)
        self.consistent()

    def test_delete_and_edit_docs_restore_stock(self):
        w = self.item(qty_on_hand=10, as_of='2025-12-31')
        b = self.bill('2026-01-05', {'item_id': w['id'], 'qty': 10, 'rate': 14})
        i = self.inv('2026-01-10', {'item_id': w['id'], 'qty': 4})        # avg 12 -> 48
        self.assertEqual(self.consistent(), 192.0)
        self.put('/api/invoices/%d' % i['id'], {'customer_id': self.cust, 'date': '2026-01-10',
                                                'lines': [{'item_id': w['id'], 'qty': 6}]})
        self.assertEqual(self.consistent(), 168.0)
        self.delete('/api/invoices/%d' % i['id'])
        self.assertEqual(self.consistent(), 240.0)
        self.delete('/api/bills/%d' % b['id'])
        self.assertEqual(self.consistent(), 100.0)
        self.assertEqual(self.it(w['id'])['qty_on_hand'], 10)
        # change an invoice from inventory item to a service line: stock comes back
        i = self.inv('2026-02-01', {'item_id': w['id'], 'qty': 3})
        self.put('/api/invoices/%d' % i['id'], {'customer_id': self.cust, 'date': '2026-02-01',
                                                'lines': [{'description': 'x', 'amount': 30,
                                                           'account_id': self.acct('Sales')}]})
        self.assertEqual(self.it(w['id'])['qty_on_hand'], 10)
        self.assertEqual(self.pl('Cost of Goods Sold'), 0.0)
        self.consistent()

    def test_delete_bill_after_sale_keeps_books_consistent(self):
        w = self.item()
        b = self.bill('2026-01-01', {'item_id': w['id'], 'qty': 10, 'rate': 10})
        self.inv('2026-01-10', {'item_id': w['id'], 'qty': 4})
        self.delete('/api/bills/%d' % b['id'])
        self.assertEqual(self.it(w['id'])['qty_on_hand'], -4)
        self.consistent()

    def test_return_line_on_invoice(self):
        w = self.item(qty_on_hand=10, as_of='2025-12-31')
        svc = self.post('/api/items', {'name': 'Fee', 'sales_price': 100})
        self.inv('2026-01-10', {'item_id': svc['id']}, {'item_id': w['id'], 'qty': -2})
        x = self.it(w['id'])
        self.assertEqual((x['qty_on_hand'], x['value']), (12, 120.0))
        self.assertEqual(self.pl('Cost of Goods Sold'), -20.0)
        self.assertEqual(self.pl('Total Income'), 50.0)
        self.consistent()

    def test_vendor_return_line_on_bill(self):
        w = self.item(qty_on_hand=10, as_of='2025-12-31')
        self.bill('2026-01-05', {'item_id': w['id'], 'qty': 5, 'rate': 10},
                  {'item_id': w['id'], 'qty': -2, 'rate': 10})
        self.assertEqual(self.it(w['id'])['qty_on_hand'], 13)
        self.consistent()

    def test_adjustments(self):
        w = self.item(qty_on_hand=10, as_of='2025-12-31')
        x = self.post('/api/items/%d/adjust' % w['id'], {'date': '2026-01-02', 'qty_change': 5, 'unit_cost': 16})
        self.assertEqual((x['qty_on_hand'], x['value'], x['avg_cost']), (15, 180.0, 12.0))
        x = self.post('/api/items/%d/adjust' % w['id'], {'date': '2026-01-03', 'qty_change': -3,
                                                         'unit_cost': 99})     # decreases use average cost
        self.assertEqual((x['qty_on_hand'], x['value']), (12, 144.0))
        self.assertEqual(self.pl('Cost of Goods Sold'), -80.0 + 36.0)   # offset defaults to COGS
        x = self.post('/api/items/%d/adjust' % w['id'], {'date': '2026-01-04', 'qty_change': 2})  # at average
        self.assertEqual((x['qty_on_hand'], x['value']), (14, 168.0))
        self.consistent()
        # deleting an adjustment transaction removes the quantity too
        adj = [t for t in self.get('/api/reports/inventory_valuation', end='2026-12-31')['rows'][0]['drill'][1]['items']
               if t['type'] == 'Inventory Qty Adjust' and t['date'] == '2026-01-04'][0]
        self.delete('/api/transactions/%d' % adj['txn_id'])
        self.assertEqual(self.it(w['id'])['qty_on_hand'], 12)
        self.consistent()

    def test_zero_value_adjustment_deletable(self):
        w = self.item(purchase_cost=0)
        self.post('/api/items/%d/adjust' % w['id'], {'date': '2026-01-02', 'qty_change': 5})
        self.assertEqual(self.it(w['id'])['qty_on_hand'], 5)
        val = self.get('/api/reports/inventory_valuation', end='2026-12-31')
        tid = self.row(val, 'Widget')['drill'][1]['items'][0]['txn_id']
        self.delete('/api/transactions/%d' % tid)
        self.assertEqual(self.it(w['id'])['qty_on_hand'], 0)

    def test_adjust_offset_account_must_not_be_inventory_asset(self):
        """BUG: an adjustment whose offset account is the Inventory Asset account itself posts Dr/Cr to
        the same account (GL unchanged) while the Inventory Valuation report gains the value."""
        w = self.item(qty_on_hand=10, as_of='2025-12-31')
        st, data = self.app.handle('POST', '/api/items/%d/adjust' % w['id'], {},
                                   {'date': '2026-01-02', 'qty_change': 5, 'unit_cost': 10,
                                    'account_id': self.acct('Inventory Asset')})
        if st == 200:
            self.consistent()       # fails: report 150 vs balance sheet 100
        else:
            self.assertEqual(st, 400)

    def test_invoice_line_cannot_post_income_to_inventory_asset(self):
        """BUG (validation): an invoice line may override its account to the Inventory Asset account, so the
        sale price is credited to inventory and the GL no longer matches the Inventory Valuation report.
        Bills already forbid the mirror case (documents.py _lines)."""
        w = self.item(qty_on_hand=10, as_of='2025-12-31')
        st, data = self.app.handle('POST', '/api/invoices', {}, {
            'customer_id': self.cust, 'date': '2026-01-10',
            'lines': [{'description': 'misc', 'amount': 40, 'account_id': self.acct('Inventory Asset')}]})
        if st == 200:
            self.consistent()
        else:
            self.assertEqual(st, 400)

    def test_adjust_offset_account_rejects_ar_ap(self):
        """BUG: adjustment offset can be Accounts Receivable / Payable (no customer/vendor), which the
        invoice/bill screens forbid."""
        w = self.item(qty_on_hand=10, as_of='2025-12-31')
        st, data = self.app.handle('POST', '/api/items/%d/adjust' % w['id'], {},
                                   {'date': '2026-01-02', 'qty_change': -1,
                                    'account_id': self.acct('Accounts Receivable (A/R)')
                                    if self._has('Accounts Receivable (A/R)') else self.acct('Accounts Receivable')})
        self.assertEqual(st, 400, data)

    def _has(self, name):
        try:
            self.acct(name)
            return True
        except KeyError:
            return False

    def test_cash_basis_balanced_with_inventory_and_tax(self):
        rate = self.post('/api/tax_rates', {'name': 'T', 'rate': 10})
        w = self.item(qty_on_hand=10, as_of='2025-12-31', taxable=True)
        b = self.bill('2026-01-05', {'item_id': w['id'], 'qty': 10, 'rate': 12})
        i = self.inv('2026-01-10', {'item_id': w['id'], 'qty': 4}, tax_rate_id=rate['id'])   # 100 + 10
        self.post('/api/payments', {'customer_id': self.cust, 'date': '2026-01-20', 'amount': 55,
                                    'deposit_account_id': self.chk})
        self.post('/api/bill_payments', {'vendor_id': self.vend, 'date': '2026-01-21', 'amount': 120,
                                         'pay_account_id': self.chk})
        self.balanced(basis='cash')
        self.balanced()
        self.assertEqual(self.bs('Sales Tax Payable', basis='cash'), 5.0)
        self.assertEqual(self.pl('Sales', basis='cash'), 50.0)
        self.assertEqual(self.pl('Cost of Goods Sold', basis='cash'), 22.0)   # half of 4 x 11


# ======================================================================= sales tax

class SalesTax(Base):
    def setUp(self):
        super().setUp()
        self.rate = self.post('/api/tax_rates', {'name': 'City 8.875%', 'rate': '8.875', 'agency': 'NYS'})
        self.sales = self.acct('Sales')

    def test_rounding_on_many_lines(self):
        lines = [{'description': 'L%d' % k, 'amount': 0.99, 'account_id': self.sales, 'taxable': True}
                 for k in range(7)]
        inv = self.inv('2026-03-01', *lines, tax_rate_id=self.rate['id'])
        # 6.93 x 8.875% = 0.6150375 -> 0.62 (tax on the taxable subtotal, like QBO)
        self.assertEqual((inv['subtotal'], inv['tax'], inv['total']), (6.93, 0.62, 7.55))
        self.assertEqual(self.bs('Sales Tax Payable'), 0.62)
        self.balanced()

    def test_half_cent_rounds_up(self):
        r = self.post('/api/tax_rates', {'name': 'Five', 'rate': 5})
        inv = self.inv('2026-03-01', {'description': 'x', 'amount': 0.10, 'account_id': self.sales,
                                      'taxable': True}, tax_rate_id=r['id'])
        self.assertEqual(inv['tax'], 0.01)   # 0.005 -> 0.01

    def test_item_taxable_default_and_override(self):
        tx = self.post('/api/items', {'name': 'Goods', 'type': 'non_inventory', 'sales_price': 100,
                                      'taxable': True})
        nt = self.post('/api/items', {'name': 'Labor', 'type': 'service', 'sales_price': 100})
        inv = self.inv('2026-03-01', {'item_id': tx['id']}, {'item_id': nt['id']},
                       {'item_id': tx['id'], 'taxable': False}, {'item_id': nt['id'], 'taxable': True},
                       tax_rate_id=self.rate['id'])
        self.assertEqual(inv['tax'], 17.75)   # 200 x 8.875%
        d = self.get('/api/invoices/%d' % inv['id'])
        self.assertEqual([l['taxable'] for l in d['lines']], [True, False, False, True])
        rep = self.get('/api/reports/sales_tax', start='2026-03-01', end='2026-03-31')
        self.assertEqual(self.row(rep, 'City 8.875%')['values'], [200.0, 200.0, 17.75])

    def test_taxable_string_false(self):
        inv = self.inv('2026-03-01', {'description': 'x', 'amount': 100, 'account_id': self.sales,
                                      'taxable': 'false'}, tax_rate_id=self.rate['id'])
        self.assertEqual(inv['tax'], 0.0)

    def test_no_rate_means_no_tax_and_nontaxable_in_report(self):
        self.inv('2026-03-01', {'description': 'x', 'amount': 100, 'account_id': self.sales, 'taxable': True})
        rep = self.get('/api/reports/sales_tax', start='2026-03-01', end='2026-03-31')
        self.assertEqual(self.row(rep, '(No sales tax)')['values'], [0.0, 100.0, 0.0])

    def test_rate_change_locked_after_use_and_inactive(self):
        inv = self.inv('2026-03-01', {'description': 'x', 'amount': 100, 'account_id': self.sales},
                       tax_rate_id=self.rate['id'])
        self.put('/api/tax_rates/%d' % self.rate['id'], {'rate': 9}, expect=400)
        self.put('/api/tax_rates/%d' % self.rate['id'], {'rate': '8.8750'})   # same value OK
        self.put('/api/tax_rates/%d' % self.rate['id'], {'name': 'Renamed', 'agency': 'X'})
        self.put('/api/tax_rates/%d' % self.rate['id'], {'active': False})
        self.assertEqual(self.get('/api/tax_rates'), [])
        body = {'customer_id': self.cust, 'date': '2026-03-02', 'tax_rate_id': self.rate['id'],
                'lines': [{'description': 'x', 'amount': 50, 'account_id': self.sales}]}
        self.post('/api/invoices', body, expect=400)
        # editing the existing invoice keeps its (now inactive) rate
        self.put('/api/invoices/%d' % inv['id'], dict(body, date='2026-03-01'))
        self.assertEqual(self.get('/api/invoices/%d' % inv['id'])['tax'], 4.44)   # 50 x 8.875% = 4.4375

    def test_unused_rate_can_change_and_delete(self):
        r = self.post('/api/tax_rates', {'name': 'Tmp', 'rate': 5})
        self.assertEqual(self.put('/api/tax_rates/%d' % r['id'], {'rate': 6})['rate'], 6.0)
        self.delete('/api/tax_rates/%d' % r['id'])
        for b in ({'name': 'X', 'rate': -1}, {'name': 'X', 'rate': 101}, {'name': 'X', 'rate': 'abc'},
                  {'name': ''}, {'name': 'city 8.875%', 'rate': 1}, {'name': 'Y', 'rate': [1]},
                  {'name': 'Y', 'active': 'perhaps'}):
            st, data = self.app.handle('POST', '/api/tax_rates', {}, b)
            self.assertIn(st, (400, 409), (b, st, data))

    def test_edit_invoice_rate_and_remove_tax(self):
        other = self.post('/api/tax_rates', {'name': 'County 5%', 'rate': 5})
        body = {'customer_id': self.cust, 'date': '2026-03-01', 'tax_rate_id': self.rate['id'],
                'lines': [{'description': 'x', 'amount': 200, 'account_id': self.sales}]}
        inv = self.post('/api/invoices', body)
        self.assertEqual(inv['tax'], 17.75)
        self.put('/api/invoices/%d' % inv['id'], dict(body, tax_rate_id=other['id']))
        self.assertEqual(self.bs('Sales Tax Payable'), 10.0)
        rep = self.get('/api/reports/sales_tax', start='2026-03-01', end='2026-03-31')
        self.assertEqual(self.row(rep, 'County 5%')['values'], [200.0, 0.0, 10.0])
        self.assertNotIn('City 8.875%', [r['label'] for r in rep['rows']])
        # now the first rate is unused again: it can change
        self.put('/api/tax_rates/%d' % self.rate['id'], {'rate': 9})
        self.put('/api/invoices/%d' % inv['id'], dict(body, tax_rate_id=None))
        self.assertEqual(self.bs('Sales Tax Payable'), 0.0)
        self.balanced()

    def test_payable_equals_collected_minus_paid_and_report(self):
        a = self.inv('2026-03-01', {'description': 'x', 'amount': 1000, 'account_id': self.sales},
                     tax_rate_id=self.rate['id'])      # 88.75
        self.inv('2026-03-15', {'description': 'y', 'amount': 333.33, 'account_id': self.sales},
                 tax_rate_id=self.rate['id'])          # 29.58
        self.post('/api/sales_tax/payments', {'date': '2026-04-10', 'amount': 50, 'bank_account_id': self.chk})
        self.post('/api/sales_tax/payments', {'date': '2026-04-20', 'amount': 20, 'bank_account_id': self.chk})
        self.assertEqual(self.bs('Sales Tax Payable', end='2026-04-30'), round(88.75 + 29.58 - 70, 2))
        rep = self.get('/api/reports/sales_tax', start='2026-03-01', end='2026-04-30')
        self.assertEqual(self.val(rep, 'TOTAL', 2), 118.33)
        self.assertEqual(self.val(rep, 'Sales tax paid in this period', 2), 70.0)
        owed = self.val(rep, 'Sales tax owed as of Apr 30, 2026', 2)
        self.assertEqual(owed, 48.33)
        for label in ('City 8.875%', 'TOTAL', 'Sales tax paid in this period', 'Sales tax owed as of Apr 30, 2026'):
            r = self.row(rep, label)
            for k, v in enumerate(r['values']):
                if v is not None and r['drill'][k]:
                    self.assertEqual(self.drill_total(rep, label, k), v, (label, k))
        # a payment cannot be negative / from a non-bank account
        self.post('/api/sales_tax/payments', {'date': '2026-04-20', 'amount': -5, 'bank_account_id': self.chk},
                  expect=400)
        self.post('/api/sales_tax/payments', {'date': '2026-04-20', 'amount': 5,
                                              'bank_account_id': self.acct('Sales')}, expect=400)
        self.post('/api/sales_tax/payments', {'date': '2026-04-20', 'amount': 'abc',
                                              'bank_account_id': self.chk}, expect=400)
        self.post('/api/sales_tax/payments', {'date': '2026-04-20', 'amount': 5, 'bank_account_id': 'x'},
                  expect=400)
        self.assertEqual(len(self.get('/api/sales_tax/payments')), 2)
        # deleting the invoice removes its tax from the report and from the liability
        self.delete('/api/invoices/%d' % a['id'])
        rep = self.get('/api/reports/sales_tax', start='2026-03-01', end='2026-04-30')
        self.assertEqual(self.val(rep, 'TOTAL', 2), 29.58)
        self.balanced()

    def test_partial_payment_cash_basis_tax(self):
        inv = self.inv('2026-03-01', {'description': 'x', 'amount': 1000, 'account_id': self.sales},
                       tax_rate_id=self.rate['id'])      # total 1088.75
        self.post('/api/payments', {'customer_id': self.cust, 'date': '2026-03-10', 'amount': 544.38,
                                    'deposit_account_id': self.chk})
        self.assertEqual(self.get('/api/invoices/%d' % inv['id'])['status'] in ('Partial', 'Overdue'), True)
        self.assertEqual(self.bs('Sales Tax Payable'), 88.75)
        self.assertEqual(self.bs('Sales Tax Payable', basis='cash'), 44.38)
        self.assertEqual(self.pl('Sales', basis='cash'), 500.0)
        self.balanced(basis='cash')

    def test_tax_payment_on_cash_basis_balanced(self):
        self.inv('2026-03-01', {'description': 'x', 'amount': 100, 'account_id': self.sales},
                 tax_rate_id=self.rate['id'])
        self.post('/api/sales_tax/payments', {'date': '2026-04-10', 'amount': 8.88, 'bank_account_id': self.chk})
        self.balanced(basis='cash')
        self.balanced()

    def test_taxable_returns_reduce_tax(self):
        inv = self.inv('2026-03-01', {'description': 'sale', 'amount': 300, 'account_id': self.sales},
                       {'description': 'return', 'qty': -1, 'rate': 100, 'account_id': self.sales},
                       tax_rate_id=self.rate['id'])
        self.assertEqual((inv['subtotal'], inv['tax']), (200.0, 17.75))
        self.balanced()

    def test_tax_on_inventory_sale(self):
        w = self.item(qty_on_hand=10, as_of='2025-12-31', taxable=True)
        inv = self.inv('2026-03-01', {'item_id': w['id'], 'qty': 3}, tax_rate_id=self.rate['id'])
        self.assertEqual((inv['subtotal'], inv['tax'], inv['total']), (75.0, 6.66, 81.66))
        self.assertEqual(self.pl('Total Income'), 75.0)
        self.consistent()


if __name__ == '__main__':
    unittest.main()
