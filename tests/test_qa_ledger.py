"""QA tests: chart of accounts, customers/vendors, journal entries.

Conventions used in this file
  * A plain failing test    = confirmed bug (see the docstring: "BUG ...").
  * @unittest.expectedFailure = QuickBooks-behaviour gap ("GAP ..."); the suite stays green, the
    assertion documents the behaviour QuickBooks Online has and this app does not.
  * Everything else         = behaviour verified to work.
"""
import contextlib
import io
import unittest

from helpers import ApiCase

BS_TYPES_DEBIT = ['Bank', 'Other Current Asset', 'Fixed Asset', 'Other Asset']
BS_TYPES_CREDIT = ['Credit Card', 'Other Current Liability', 'Long Term Liability', 'Equity']
PL_TYPES = ['Income', 'Cost of Goods Sold', 'Expense', 'Other Income', 'Other Expense']


class QaCase(ApiCase):
    def quiet(self, method, path, body=None, **query):
        """Call the API with the server traceback (printed on a 500) suppressed. Returns (status, data)."""
        with contextlib.redirect_stderr(io.StringIO()):
            return self.app.handle(method, path, {k: str(v) for k, v in query.items()}, body)

    def assertNo500(self, method, path, body=None, allowed=(400, 404, 409), **query):
        status, data = self.quiet(method, path, body, **query)
        self.assertIn(status, allowed, '%s %s %r -> %s %s' % (method, path, body, status, data))
        return status, data

    def account(self, aid):
        return self.get('/api/accounts/%d' % aid)

    def balance(self, name):
        return self.account(self.acct(name))['balance']

    def tb(self, end='2026-12-31'):
        return self.get('/api/reports/trial_balance', end=end)

    def tb_row(self, name, end='2026-12-31'):
        for r in self.tb(end)['rows']:
            if r['label'] == name:
                return r['values']
        return None

    def assertBooksBalance(self, end='2026-12-31'):
        total = self.row(self.tb(end), 'TOTAL')['values']
        self.assertEqual(total[0], total[1], 'trial balance out of balance')
        bs = self.get('/api/reports/balance_sheet', end=end)
        self.assertEqual(self.val(bs, 'TOTAL ASSETS'), self.val(bs, 'TOTAL LIABILITIES AND EQUITY'),
                         'balance sheet out of balance')

    def lines(self, *specs):
        """specs: (account name, debit, credit[, name_id[, memo]])"""
        return [{'account_id': self.acct(s[0]), 'debit': s[1], 'credit': s[2],
                 'name_id': s[3] if len(s) > 3 else None, 'memo': s[4] if len(s) > 4 else ''} for s in specs]


# ===================================================================== A) Chart of accounts

class AccountsCreate(QaCase):
    def test_create_returns_all_fields(self):
        a = self.post('/api/accounts', {'name': '  Petty Cash ', 'type': 'Bank', 'number': ' 1020 ',
                                        'description': ' drawer '})
        self.assertEqual((a['name'], a['number'], a['description'], a['active'], a['parent_id'], a['system']),
                         ('Petty Cash', '1020', 'drawer', True, None, None))
        self.assertEqual(a['balance'], 0.0)
        self.assertFalse(a['has_transactions'])
        self.assertEqual(self.account(a['id'])['name'], 'Petty Cash')

    def test_every_account_type_can_be_created(self):
        for i, t in enumerate(BS_TYPES_DEBIT + BS_TYPES_CREDIT + PL_TYPES +
                              ['Accounts Receivable', 'Accounts Payable']):
            a = self.post('/api/accounts', {'name': 'T%d' % i, 'type': t})
            self.assertEqual(a['type'], t)

    def test_name_required(self):
        for body in ({'type': 'Bank'}, {'name': '', 'type': 'Bank'}, {'name': '   ', 'type': 'Bank'},
                     {'name': None, 'type': 'Bank'}):
            self.post('/api/accounts', body, expect=400)

    def test_type_required_and_valid(self):
        for t in (None, '', 'bank', 'Asset', 5):
            self.post('/api/accounts', {'name': 'X', 'type': t}, expect=400)
        self.post('/api/accounts', {'name': 'X'}, expect=400)

    def test_duplicate_name_rejected_case_and_whitespace_insensitive(self):
        self.post('/api/accounts', {'name': 'checking', 'type': 'Bank'}, expect=409)
        self.post('/api/accounts', {'name': '  CHECKING  ', 'type': 'Expense'}, expect=409)

    def test_duplicate_name_rejected_even_when_existing_is_inactive(self):
        self.put('/api/accounts/%d' % self.acct('Savings'), {'active': False})
        self.post('/api/accounts', {'name': 'Savings', 'type': 'Bank'}, expect=409)

    def test_duplicate_number_rejected(self):
        self.post('/api/accounts', {'name': 'Dup', 'type': 'Bank', 'number': '1000'}, expect=409)
        self.post('/api/accounts', {'name': 'Dup', 'type': 'Bank', 'number': 1000}, expect=409)
        self.post('/api/accounts', {'name': 'Dup', 'type': 'Bank', 'number': ' 1000 '}, expect=409)
        # blank numbers never clash
        self.post('/api/accounts', {'name': 'NoNum1', 'type': 'Bank'})
        self.post('/api/accounts', {'name': 'NoNum2', 'type': 'Bank', 'number': ''})

    def test_colon_in_name_rejected(self):
        self.post('/api/accounts', {'name': 'Utilities:Gas', 'type': 'Expense'}, expect=400)

    def test_failed_create_leaves_nothing_behind(self):
        before = len(self.get('/api/accounts', include_inactive=1))
        self.post('/api/accounts', {'name': 'Bad OB', 'type': 'Bank', 'opening_balance': 'abc'}, expect=400)
        self.post('/api/accounts', {'name': 'Bad OB date', 'type': 'Bank', 'opening_balance': 5,
                                    'opening_date': '2026-13-45'}, expect=400)
        self.post('/api/accounts', {'name': 'PL OB', 'type': 'Income', 'opening_balance': 5}, expect=400)
        self.assertEqual(len(self.get('/api/accounts', include_inactive=1)), before)
        self.assertEqual(self.get('/api/journals'), [])

    def test_client_cannot_set_system_flag_or_id(self):
        a = self.post('/api/accounts', {'name': 'Sneaky', 'type': 'Bank', 'system': 'ar', 'id': 1})
        self.assertIsNone(a['system'])
        self.assertNotEqual(a['id'], 1)
        self.delete('/api/accounts/%d' % a['id'])

    def test_bad_active_value_is_rejected_not_silently_inactive(self):
        """BUG (low): any 'active' value other than True/1/'1'/'true' silently creates the account INACTIVE,
        so it disappears from the default list. e.g. 'yes' or 'True'."""
        status, a = self.quiet('POST', '/api/accounts', {'name': 'Yes Acct', 'type': 'Bank', 'active': 'yes'})
        self.assertTrue(status == 400 or a.get('active') is True, 'active="yes" -> %s %s' % (status, a))


class AccountsOpeningBalance(QaCase):
    def test_debit_normal_types_post_debit_to_account_credit_to_obe(self):
        for i, t in enumerate(BS_TYPES_DEBIT):
            name = 'DrOB%d' % i
            a = self.post('/api/accounts', {'name': name, 'type': t, 'opening_balance': 100 + i,
                                            'opening_date': '2026-01-01'})
            self.assertEqual(a['balance'], 100.0 + i, t)
            self.assertTrue(a['has_transactions'])
            self.assertEqual(self.tb_row(name), [100.0 + i, None], t)
        self.assertEqual(self.tb_row('Opening Balance Equity'), [None, 406.0])
        self.assertBooksBalance()

    def test_credit_normal_types_post_credit_to_account_debit_to_obe(self):
        for i, t in enumerate(BS_TYPES_CREDIT):
            name = 'CrOB%d' % i
            a = self.post('/api/accounts', {'name': name, 'type': t, 'opening_balance': '200.50',
                                            'opening_date': '2026-01-01'})
            self.assertEqual(a['balance'], 200.5, t)
            self.assertEqual(self.tb_row(name), [None, 200.5], t)
        self.assertEqual(self.tb_row('Opening Balance Equity'), [802.0, None])
        self.assertEqual(self.balance('Opening Balance Equity'), -802.0)
        self.assertBooksBalance()

    def test_opening_balance_journal_shape(self):
        a = self.post('/api/accounts', {'name': 'Loan', 'type': 'Long Term Liability', 'opening_balance': 1000,
                                        'opening_date': '2026-02-15'})
        j = self.get('/api/journals')[0]
        self.assertEqual((j['date'], j['type'], j['memo'], j['total'], j['ref']),
                         ('2026-02-15', 'Journal', 'Opening Balance', 1000.0, '1'))
        by = {l['account']: l for l in j['lines']}
        self.assertEqual((by['Loan']['debit'], by['Loan']['credit']), (0.0, 1000.0))
        self.assertEqual((by['Opening Balance Equity']['debit'], by['Opening Balance Equity']['credit']),
                         (1000.0, 0.0))
        bs = self.get('/api/reports/balance_sheet', end='2026-02-28')
        self.assertEqual(self.val(bs, 'Loan'), 1000.0)
        self.assertEqual(self.val(bs, 'Opening Balance Equity'), -1000.0)
        # before the opening date nothing shows
        self.assertIsNone(self.tb_row('Loan', end='2026-02-14'))
        self.assertEqual(a['balance'], 1000.0)

    def test_negative_opening_balance_overdrawn_bank(self):
        a = self.post('/api/accounts', {'name': 'Overdrawn', 'type': 'Bank', 'opening_balance': -75,
                                        'opening_date': '2026-01-01'})
        self.assertEqual(a['balance'], -75.0)
        self.assertEqual(self.tb_row('Overdrawn'), [None, 75.0])
        self.assertBooksBalance()

    def test_zero_or_blank_opening_balance_posts_nothing(self):
        for i, ob in enumerate((0, '0', '', None, '0.00')):
            self.post('/api/accounts', {'name': 'Z%d' % i, 'type': 'Bank', 'opening_balance': ob})
        self.assertEqual(self.get('/api/journals'), [])

    def test_opening_balance_not_allowed_on_profit_and_loss_types(self):
        for i, t in enumerate(PL_TYPES):
            self.post('/api/accounts', {'name': 'PL%d' % i, 'type': t, 'opening_balance': 10}, expect=400)

    def test_opening_balance_defaults_to_today(self):
        import datetime
        self.post('/api/accounts', {'name': 'Today', 'type': 'Bank', 'opening_balance': 10})
        self.assertEqual(self.get('/api/journals')[0]['date'], datetime.date.today().isoformat())

    def test_opening_balance_then_delete_journal_frees_account(self):
        a = self.post('/api/accounts', {'name': 'Tmp', 'type': 'Bank', 'opening_balance': 10,
                                        'opening_date': '2026-01-01'})
        self.delete('/api/accounts/%d' % a['id'], expect=409)
        self.delete('/api/transactions/%d' % self.get('/api/journals')[0]['id'])
        self.delete('/api/accounts/%d' % a['id'])

    def test_gap_opening_balance_on_new_ar_ap_account_is_not_tied_to_a_name(self):
        """GAP: journals insist that A/R lines carry a customer and A/P lines a vendor, but the opening-balance
        path posts to a new A/R or A/P account with no name. The receivable then belongs to no customer
        (customer balances do not add up to A/R). QuickBooks does not offer an opening balance on A/R / A/P
        accounts; they are built from customer/vendor transactions."""
        self.post('/api/accounts', {'name': 'AR Two', 'type': 'Accounts Receivable', 'opening_balance': 100,
                                    'opening_date': '2026-01-01'}, expect=400)


class AccountsUpdate(QaCase):
    def setUp(self):
        super().setUp()
        self.parent = self.acct('Utilities')
        self.a = self.post('/api/accounts', {'name': 'Electric', 'type': 'Expense', 'number': '6111',
                                             'parent_id': self.parent, 'description': 'power'})

    def url(self, a=None):
        return '/api/accounts/%d' % (a or self.a)['id']

    def test_partial_update_keeps_other_fields(self):
        for body in ({'name': 'Electricity'}, {'number': '6112'}, {'description': 'kWh'}, {'active': False},
                     {'active': True}, {}):
            a = self.put(self.url(), body)
        self.assertEqual((a['name'], a['number'], a['description'], a['type'], a['parent_id'], a['active']),
                         ('Electricity', '6112', 'kWh', 'Expense', self.parent, True))
        self.assertEqual(a['full_name'], 'Utilities:Electricity')

    def test_rename_to_own_name_other_case_ok_but_clash_rejected(self):
        self.put(self.url(), {'name': 'ELECTRIC'})
        self.put(self.url(), {'name': 'Travel'}, expect=409)
        self.put(self.url(), {'name': ' travel '}, expect=409)
        self.put(self.url(), {'name': ''}, expect=400)
        self.put(self.url(), {'name': None}, expect=400)
        self.put(self.url(), {'number': '6110'}, expect=409)      # Utilities' number
        self.assertEqual(self.account(self.a['id'])['name'], 'ELECTRIC')

    def test_update_missing_account_404(self):
        self.put('/api/accounts/99999', {'name': 'X'}, expect=404)
        self.get('/api/accounts/99999', expect=404)
        self.delete('/api/accounts/99999', expect=404)

    def test_clear_number_and_detach_parent(self):
        a = self.put(self.url(), {'number': '', 'parent_id': None})
        self.assertEqual((a['number'], a['parent_id'], a['depth'], a['full_name']), ('', None, 0, 'Electric'))

    def test_subaccount_type_must_match_parent(self):
        self.put(self.url(), {'type': 'Income'}, expect=400)                       # still under Utilities
        self.put(self.url(), {'parent_id': self.acct('Sales')}, expect=400)        # Income parent
        a = self.put(self.url(), {'type': 'Income', 'parent_id': self.acct('Sales')})
        self.assertEqual(a['full_name'], 'Sales:Electric')
        self.put('/api/accounts/%d' % self.acct('Sales'), {'type': 'Other Income'}, expect=400)   # has a child

    def test_parent_must_exist(self):
        self.put(self.url(), {'parent_id': 99999}, expect=400)
        self.put(self.url(), {'parent_id': 'abc'}, expect=400)

    def test_parent_cycles_rejected(self):
        self.put(self.url(), {'parent_id': self.a['id']}, expect=400)              # self
        self.put('/api/accounts/%d' % self.parent, {'parent_id': self.a['id']}, expect=400)   # 2-cycle
        c = self.post('/api/accounts', {'name': 'Solar', 'type': 'Expense', 'parent_id': self.a['id']})
        self.assertEqual((c['depth'], c['full_name']), (2, 'Utilities:Electric:Solar'))
        self.put('/api/accounts/%d' % self.parent, {'parent_id': c['id']}, expect=400)        # 3-cycle
        self.put(self.url(), {'parent_id': c['id']}, expect=400)
        # every account still listed exactly once
        ids = [x['id'] for x in self.get('/api/accounts', include_inactive=1)]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn(c['id'], ids)

    def test_system_accounts_protected(self):
        for name in ('Accounts Receivable', 'Undeposited Funds', 'Opening Balance Equity', 'Retained Earnings'):
            aid = self.acct(name)
            self.delete('/api/accounts/%d' % aid, expect=400)
            self.put('/api/accounts/%d' % aid, {'active': False}, expect=400)
            self.put('/api/accounts/%d' % aid, {'type': 'Bank'}, expect=400)
            a = self.put('/api/accounts/%d' % aid, {'description': 'ok'})     # harmless edits allowed
            self.assertEqual((a['description'], a['system'] is not None), ('ok', True))

    def test_type_change_flips_balance_sign_and_books_stay_balanced(self):
        self.journal('2026-01-05', ('Prepaid Expenses', 300, 0), ('Checking', 0, 300))
        aid = self.acct('Prepaid Expenses')
        self.assertEqual(self.account(aid)['balance'], 300.0)
        a = self.put('/api/accounts/%d' % aid, {'type': 'Other Current Liability'})
        self.assertEqual(a['balance'], -300.0)
        self.assertBooksBalance()

    def test_null_active_on_update_does_not_deactivate(self):
        """BUG (low): PUT {'active': null} makes the account inactive (anything not True/1/'1'/'true' counts as
        inactive). A null field in a partial update should mean "leave unchanged" or be a 400."""
        status, a = self.quiet('PUT', '/api/accounts/%d' % self.acct('Savings'), {'active': None})
        self.assertTrue(status == 400 or a.get('active') is True, 'active=None -> %s %s' % (status, a))

    def test_gap_type_change_to_ar_with_unnamed_postings(self):
        """GAP: an account with ordinary postings (no customer) can be re-typed to Accounts Receivable, which
        leaves A/R lines without a customer - the exact state the journal validation forbids.
        QuickBooks does not allow changing an account's type to or from A/R or A/P."""
        self.journal('2026-01-05', ('Prepaid Expenses', 300, 0), ('Checking', 0, 300))
        self.put('/api/accounts/%d' % self.acct('Prepaid Expenses'), {'type': 'Accounts Receivable'}, expect=400)


class AccountsDeleteInactive(QaCase):
    def test_unused_account_deletes(self):
        a = self.post('/api/accounts', {'name': 'Temp', 'type': 'Expense'})
        self.assertEqual(self.delete('/api/accounts/%d' % a['id']), {'deleted': True})
        self.get('/api/accounts/%d' % a['id'], expect=404)
        self.delete('/api/accounts/%d' % a['id'], expect=404)
        self.post('/api/accounts', {'name': 'Temp', 'type': 'Expense'})     # name is free again

    def test_account_with_transactions_cannot_be_deleted_only_inactivated(self):
        self.journal('2026-01-01', ('Checking', 100, 0), ('Sales', 0, 100))
        for name in ('Checking', 'Sales'):
            aid = self.acct(name)
            r = self.delete('/api/accounts/%d' % aid, expect=409)
            self.assertIn('inactive', r['error'].lower())
            a = self.put('/api/accounts/%d' % aid, {'active': False})
            self.assertFalse(a['active'])
        names = [x['name'] for x in self.get('/api/accounts')]
        self.assertNotIn('Checking', names)
        allnames = {x['name']: x for x in self.get('/api/accounts', include_inactive=1)}
        self.assertFalse(allnames['Checking']['active'])
        # history stays on the reports and the books still balance
        self.assertEqual(self.tb_row('Checking'), [100.0, None])
        self.assertEqual(self.tb_row('Sales'), [None, 100.0])
        self.assertBooksBalance()
        pl = self.get('/api/reports/profit_loss', start='2026-01-01', end='2026-01-31')
        self.assertEqual(self.val(pl, 'Net Income'), 100.0)
        # and it can be reactivated
        self.assertTrue(self.put('/api/accounts/%d' % self.acct('Checking'), {'active': True})['active'])

    def test_delete_allowed_again_after_its_only_journal_is_deleted(self):
        a = self.post('/api/accounts', {'name': 'Temp', 'type': 'Expense'})
        j = self.journal('2026-01-01', ('Temp', 10, 0), ('Checking', 0, 10))
        self.delete('/api/accounts/%d' % a['id'], expect=409)
        self.delete('/api/transactions/%d' % j['id'])
        self.delete('/api/accounts/%d' % a['id'])

    def test_parent_with_subaccounts_cannot_be_deleted(self):
        p = self.acct('Utilities')
        c = self.post('/api/accounts', {'name': 'Gas', 'type': 'Expense', 'parent_id': p})
        self.delete('/api/accounts/%d' % p, expect=409)
        self.delete('/api/accounts/%d' % c['id'])
        self.delete('/api/accounts/%d' % p)

    def test_include_inactive_flag(self):
        aid = self.acct('Travel')
        self.put('/api/accounts/%d' % aid, {'active': False})
        self.assertNotIn(aid, [a['id'] for a in self.get('/api/accounts')])
        self.assertIn(aid, [a['id'] for a in self.get('/api/accounts', include_inactive=1)])
        self.assertFalse(self.account(aid)['active'])

    def test_gap_default_accounts_payable_can_be_deleted(self):
        """GAP: the default 'Accounts Payable' account is not protected (A/R is), so it can be deleted.
        QuickBooks treats the default A/R and A/P accounts alike: neither can be deleted."""
        self.delete('/api/accounts/%d' % self.acct('Accounts Payable'), expect=400)

    def test_gap_inactive_account_still_accepts_new_postings(self):
        """GAP: a journal entry can post to an inactive account. QuickBooks hides inactive accounts from
        transaction forms; you must reactivate the account first."""
        aid = self.acct('Travel')
        self.put('/api/accounts/%d' % aid, {'active': False})
        self.post('/api/journals', {'date': '2026-01-01', 'lines': self.lines(
            ('Travel', 10, 0), ('Checking', 0, 10))}, expect=400)

    def test_gap_parent_can_go_inactive_while_subaccounts_stay_active(self):
        """GAP: deactivating a parent leaves its active sub-account orphaned in the default list
        (depth 1, full name 'Utilities:Gas', parent row missing). QuickBooks blocks/handles this."""
        p = self.acct('Utilities')
        self.post('/api/accounts', {'name': 'Gas', 'type': 'Expense', 'parent_id': p})
        self.put('/api/accounts/%d' % p, {'active': False}, expect=400)


class AccountBalanceSigns(QaCase):
    def test_balance_is_positive_on_the_normal_side_for_each_type(self):
        cust = self.post('/api/customers', {'name': 'C'})['id']
        vend = self.post('/api/vendors', {'name': 'V'})['id']
        j = self.journal
        j('2026-01-01', ('Checking', 1000, 0), ("Owner's Investment", 0, 1000))
        j('2026-01-02', ('Accounts Receivable', 200, 0, cust), ('Sales', 0, 200))
        j('2026-01-03', ('Rent and Lease', 150, 0), ('Accounts Payable', 0, 150, vend))
        j('2026-01-04', ('Furniture and Equipment', 400, 0), ('Credit Card', 0, 400))
        j('2026-01-05', ('Checking', 500, 0), ('Notes Payable', 0, 500))
        j('2026-01-06', ('Depreciation Expense', 40, 0), ('Accumulated Depreciation', 0, 40))
        j('2026-01-07', ("Owner's Draw", 60, 0), ('Checking', 0, 60))
        want = {'Checking': 1440.0, 'Accounts Receivable': 200.0, 'Accounts Payable': 150.0,
                'Furniture and Equipment': 400.0, 'Credit Card': 400.0, 'Notes Payable': 500.0,
                'Accumulated Depreciation': -40.0, "Owner's Investment": 1000.0, "Owner's Draw": -60.0,
                'Sales': None, 'Rent and Lease': None, 'Depreciation Expense': None}
        listed = {a['name']: a for a in self.get('/api/accounts')}
        for name, bal in want.items():
            self.assertEqual(listed[name]['balance'], bal, name)
            self.assertEqual(self.balance(name), bal, name)
            self.assertTrue(listed[name]['has_transactions'], name)
        self.assertFalse(listed['Savings']['has_transactions'])
        self.assertBooksBalance()
        bs = self.get('/api/reports/balance_sheet', end='2026-01-31')
        self.assertEqual(self.val(bs, 'TOTAL ASSETS'), 2000.0)
        self.assertEqual(self.val(bs, 'Total Liabilities'), 1050.0)
        self.assertEqual(self.val(bs, 'Net Income'), 10.0)
        self.assertEqual(self.val(bs, 'Total Equity'), 950.0)


class AccountsMalformedInput(QaCase):
    """None of these may produce an HTTP 500."""

    def test_type_wrong_json_type(self):
        """BUG: POST /api/accounts {"name":"x","type":["Bank"]} -> 500 (unhashable type)."""
        for t in (['Bank'], {'a': 1}):
            self.assertNo500('POST', '/api/accounts', {'name': 'x', 'type': t})

    def test_type_wrong_json_type_on_update(self):
        """BUG: same crash through PUT."""
        self.assertNo500('PUT', '/api/accounts/%d' % self.acct('Savings'), {'type': ['Bank']})

    def test_huge_parent_id(self):
        """BUG: parent_id beyond 64 bits -> 500 (SQLite integer overflow)."""
        self.assertNo500('POST', '/api/accounts', {'name': 'x', 'type': 'Bank', 'parent_id': 10 ** 30})

    def test_infinite_parent_id(self):
        """BUG: parent_id 1e999 (parsed by json as inf) -> 500 (OverflowError in as_int)."""
        self.assertNo500('POST', '/api/accounts', {'name': 'x', 'type': 'Bank', 'parent_id': float('inf')})

    def test_huge_opening_balance(self):
        """BUG: opening_balance 1e20 -> 500 (SQLite overflow); 1e30 -> 500 (decimal.InvalidOperation)."""
        for ob in (1e20, 1e30, '1e400', 10 ** 40):
            self.assertNo500('POST', '/api/accounts', {'name': 'x', 'type': 'Bank', 'opening_balance': ob})

    def test_huge_path_id(self):
        """BUG: PUT/DELETE /api/accounts/<25-digit id> -> 500 (SQLite overflow)."""
        big = '/api/accounts/9999999999999999999999999'
        self.assertNo500('GET', big)
        self.assertNo500('PUT', big, {'name': 'x'})
        self.assertNo500('DELETE', big)

    def test_other_bad_values_are_400(self):
        for body in ({'name': 'x', 'type': 'Bank', 'parent_id': 'abc'},
                     {'name': 'x', 'type': 'Bank', 'parent_id': [1]},
                     {'name': 'x', 'type': 'Bank', 'opening_balance': 'abc'},
                     {'name': 'x', 'type': 'Bank', 'opening_balance': [1]},
                     {'name': 'x', 'type': 'Bank', 'opening_balance': True},
                     {'name': 'x', 'type': 'Bank', 'opening_balance': 'NaN'},
                     {'name': 'x', 'type': 'Bank', 'opening_balance': float('inf')},
                     {'name': 'x', 'type': 'Bank', 'opening_balance': 5, 'opening_date': 12345},
                     {}):
            status, data = self.quiet('POST', '/api/accounts', body)
            self.assertEqual(status, 400, '%r -> %s %s' % (body, status, data))

    def test_non_string_name_is_rejected(self):
        """BUG (low): a JSON array/object/number sent as the name is stringified and saved, e.g. an account
        literally called "['x']". Should be 400."""
        status, data = self.quiet('POST', '/api/accounts', {'name': ['x'], 'type': 'Bank'})
        self.assertEqual(status, 400, data)


# ===================================================================== B) Customers and vendors

class NamesCrud(QaCase):
    KINDS = (('customer', '/api/customers'), ('vendor', '/api/vendors'))
    FULL = {'name': ' Acme Corp ', 'company': 'Acme Corporation', 'email': 'ap@acme.test', 'phone': '555-1212',
            'address': '1 Main St\nSpringfield', 'notes': 'net 30'}

    def test_create_read_update_delete(self):
        for kind, path in self.KINDS:
            body = dict(self.FULL, name=' %s One ' % kind)
            n = self.post(path, body)
            self.assertEqual(n['kind'], kind)
            self.assertEqual(n['name'], '%s One' % kind)
            for f in ('company', 'email', 'phone', 'address', 'notes'):
                self.assertEqual(n[f], self.FULL[f])
            self.assertEqual((n['active'], n['balance']), (True, 0.0))
            self.assertEqual(self.get('%s/%d' % (path, n['id'])), n)
            self.assertEqual([x['id'] for x in self.get(path)], [n['id']])
            self.assertEqual(self.delete('%s/%d' % (path, n['id'])), {'deleted': True})
            self.get('%s/%d' % (path, n['id']), expect=404)
            self.delete('%s/%d' % (path, n['id']), expect=404)
            self.assertEqual(self.get(path), [])

    def test_partial_update_keeps_other_fields(self):
        for kind, path in self.KINDS:
            n = self.post(path, dict(self.FULL, name=kind + ' full'))
            url = '%s/%d' % (path, n['id'])
            for body in ({'phone': '999'}, {'name': kind + ' renamed'}, {'active': False}, {'active': True}, {},
                         {'email': None}):
                got = self.put(url, body)
            want = dict(n, phone='999', name=kind + ' renamed')
            self.assertEqual(got, want)
            # a field can be cleared with an empty string
            self.assertEqual(self.put(url, {'notes': ''})['notes'], '')

    def test_name_required(self):
        for kind, path in self.KINDS:
            for body in ({}, {'name': ''}, {'name': '   '}, {'name': None}, {'company': 'only company'}):
                self.post(path, body, expect=400)
            n = self.post(path, {'name': kind})
            self.put('%s/%d' % (path, n['id']), {'name': '  '}, expect=400)
            self.assertEqual(self.get('%s/%d' % (path, n['id']))['name'], kind)

    def test_duplicate_names_rejected_within_and_across_lists(self):
        c = self.post('/api/customers', {'name': 'Acme'})
        v = self.post('/api/vendors', {'name': 'Staples'})
        self.post('/api/customers', {'name': 'ACME'}, expect=409)
        self.post('/api/customers', {'name': '  acme '}, expect=409)
        self.post('/api/vendors', {'name': 'acme'}, expect=409)             # QBO: unique across name lists
        self.put('/api/vendors/%d' % v['id'], {'name': 'Acme'}, expect=409)
        self.put('/api/customers/%d' % c['id'], {'name': 'staples'}, expect=409)
        self.assertEqual(self.put('/api/customers/%d' % c['id'], {'name': 'ACME'})['name'], 'ACME')   # own name

    def test_duplicate_check_includes_inactive_names(self):
        c = self.post('/api/customers', {'name': 'Acme'})
        self.put('/api/customers/%d' % c['id'], {'active': False})
        self.post('/api/customers', {'name': 'Acme'}, expect=409)

    def test_customer_and_vendor_ids_do_not_cross(self):
        c = self.post('/api/customers', {'name': 'Cust'})
        v = self.post('/api/vendors', {'name': 'Vend'})
        self.get('/api/vendors/%d' % c['id'], expect=404)
        self.put('/api/vendors/%d' % c['id'], {'name': 'Hijack'}, expect=404)
        self.delete('/api/vendors/%d' % c['id'], expect=404)
        self.get('/api/customers/%d' % v['id'], expect=404)
        self.delete('/api/customers/%d' % v['id'], expect=404)
        self.assertEqual(self.get('/api/customers/%d' % c['id'])['name'], 'Cust')

    def test_inactive_hidden_unless_requested_and_list_sorted(self):
        for n in ('zeta', 'Alpha', 'beta'):
            self.post('/api/customers', {'name': n})
        b = [x for x in self.get('/api/customers') if x['name'] == 'beta'][0]
        self.put('/api/customers/%d' % b['id'], {'active': False})
        self.assertEqual([x['name'] for x in self.get('/api/customers')], ['Alpha', 'zeta'])
        self.assertEqual([x['name'] for x in self.get('/api/customers', include_inactive=1)],
                         ['Alpha', 'beta', 'zeta'])
        self.assertFalse(self.get('/api/customers/%d' % b['id'])['active'])

    def test_null_active_on_update_does_not_deactivate(self):
        """BUG (low): PUT /api/customers/<id> {'active': null} makes the customer inactive."""
        c = self.post('/api/customers', {'name': 'Acme'})
        status, got = self.quiet('PUT', '/api/customers/%d' % c['id'], {'active': None})
        self.assertTrue(status == 400 or got.get('active') is True, 'active=None -> %s %s' % (status, got))


class NamesDeleteRules(QaCase):
    def test_name_used_on_a_journal_line_cannot_be_deleted_only_inactivated(self):
        for kind, path, acct, dr, cr in (('customer', '/api/customers', 'Accounts Receivable', 100, 0),
                                         ('vendor', '/api/vendors', 'Accounts Payable', 0, 100)):
            n = self.post(path, {'name': kind + ' used'})
            other = (('Sales', 0, 100) if dr else ('Rent and Lease', 100, 0))
            j = self.journal('2026-01-01', (acct, dr, cr, n['id']), other)
            r = self.delete('%s/%d' % (path, n['id']), expect=409)
            self.assertIn('inactive', r['error'].lower())
            got = self.put('%s/%d' % (path, n['id']), {'active': False})
            self.assertEqual((got['active'], got['balance']), (False, 100.0))    # balance survives
            self.assertEqual(self.get(path), [])
            self.assertEqual(self.get(path, include_inactive=1)[0]['balance'], 100.0)
            # the journal still shows the name
            self.assertIn(kind + ' used', [l['name'] for l in self.get('/api/transactions/%d' % j['id'])['lines']])
            # once the journal is gone the name can be deleted
            self.delete('/api/transactions/%d' % j['id'])
            self.delete('%s/%d' % (path, n['id']))

    def test_name_used_on_non_ar_line_also_blocks_delete(self):
        v = self.post('/api/vendors', {'name': 'Landlord'})
        self.journal('2026-01-01', ('Rent and Lease', 100, 0, v['id']), ('Checking', 0, 100))
        self.delete('/api/vendors/%d' % v['id'], expect=409)
        self.assertEqual(self.get('/api/vendors/%d' % v['id'])['balance'], 0.0)   # not A/P, so no open balance


class NameBalances(QaCase):
    def test_customer_balance_is_open_ar_and_vendor_balance_is_open_ap(self):
        c1 = self.post('/api/customers', {'name': 'C1'})['id']
        c2 = self.post('/api/customers', {'name': 'C2'})['id']
        v1 = self.post('/api/vendors', {'name': 'V1'})['id']
        j = self.journal
        j('2026-01-01', ('Accounts Receivable', 500, 0, c1), ('Sales', 0, 500))
        j('2026-01-02', ('Accounts Receivable', '120.55', 0, c2), ('Sales', 0, '120.55'))
        j1 = j('2026-01-03', ('Checking', 200, 0), ('Accounts Receivable', 0, 200, c1))
        j('2026-01-04', ('Rent and Lease', 80, 0), ('Accounts Payable', 0, 80, v1))
        j('2026-01-05', ('Accounts Payable', 30, 0, v1), ('Checking', 0, 30))
        cust = {x['id']: x['balance'] for x in self.get('/api/customers')}
        self.assertEqual(cust, {c1: 300.0, c2: 120.55})
        self.assertEqual(self.get('/api/customers/%d' % c1)['balance'], 300.0)
        self.assertEqual(self.get('/api/vendors/%d' % v1)['balance'], 50.0)
        self.assertEqual(round(sum(cust.values()), 2), self.balance('Accounts Receivable'))
        self.assertEqual(self.balance('Accounts Payable'), 50.0)
        # editing / deleting the journal moves the customer balance
        self.put('/api/journals/%d' % j1['id'], {'date': '2026-01-03', 'lines': self.lines(
            ('Checking', 450, 0), ('Accounts Receivable', 0, 450, c1))})
        self.assertEqual(self.get('/api/customers/%d' % c1)['balance'], 50.0)
        self.delete('/api/transactions/%d' % j1['id'])
        self.assertEqual(self.get('/api/customers/%d' % c1)['balance'], 500.0)
        self.assertBooksBalance()


class NamesMalformedInput(QaCase):
    def test_huge_path_id(self):
        """BUG: GET/PUT/DELETE /api/customers/<25-digit id> -> 500 (SQLite overflow)."""
        for path in ('/api/customers/', '/api/vendors/'):
            big = path + '9999999999999999999999999'
            self.assertNo500('GET', big)
            self.assertNo500('PUT', big, {'name': 'x'})
            self.assertNo500('DELETE', big)

    def test_non_string_fields_are_rejected(self):
        """BUG (low): arrays/objects sent for text fields are stringified and saved
        (customer named "['a']", email "{'a': 1}"). Should be 400."""
        s1, d1 = self.quiet('POST', '/api/customers', {'name': ['a']})
        s2, d2 = self.quiet('POST', '/api/customers', {'name': 'ok', 'email': {'a': 1}})
        self.assertEqual((s1, s2), (400, 400), '%s / %s' % (d1, d2))

    def test_odd_but_valid_input(self):
        n = self.post('/api/customers', {'name': "O'Brien & Sons <b>é中</b>", 'notes': 'x' * 5000})
        self.assertEqual(self.get('/api/customers/%d' % n['id'])['name'], "O'Brien & Sons <b>é中</b>")
        self.assertEqual(self.put('/api/customers/%d' % n['id'], {'unknown_field': 1})['name'], n['name'])


# ===================================================================== C) Journal entries

class JournalCreate(QaCase):
    def test_basic_entry_round_trip(self):
        v = self.post('/api/vendors', {'name': 'Landlord'})['id']
        j = self.post('/api/journals', {'date': '2026-03-05', 'ref': ' ADJ-1 ', 'memo': '  March rent ',
                                        'lines': self.lines(('Rent and Lease', '1,250.00', '', v, 'office'),
                                                            ('Checking', None, 1250, None, 'chk 101'))})
        self.assertEqual((j['date'], j['type'], j['ref'], j['memo'], j['total']),
                         ('2026-03-05', 'Journal', 'ADJ-1', 'March rent', 1250.0))
        l1, l2 = j['lines']
        self.assertEqual((l1['account'], l1['debit'], l1['credit'], l1['memo'], l1['name'], l1['name_kind']),
                         ('Rent and Lease', 1250.0, 0.0, 'office', 'Landlord', 'vendor'))
        self.assertEqual((l2['account'], l2['debit'], l2['credit'], l2['memo'], l2['name_id']),
                         ('Checking', 0.0, 1250.0, 'chk 101', None))
        self.assertEqual(self.get('/api/transactions/%d' % j['id']), j)
        self.assertEqual(self.balance('Checking'), -1250.0)
        self.assertBooksBalance()

    def test_validation(self):
        chk, sal = self.acct('Checking'), self.acct('Sales')
        D, C = {'account_id': chk, 'debit': 100}, {'account_id': sal, 'credit': 100}
        bad = [
            ('unbalanced', {'date': '2026-01-01', 'lines': [D, {'account_id': sal, 'credit': 99.99}]}),
            ('negative', {'date': '2026-01-01', 'lines': [{'account_id': chk, 'debit': -100},
                                                          {'account_id': sal, 'credit': -100}]}),
            ('negative paren', {'date': '2026-01-01', 'lines': [{'account_id': chk, 'debit': '(100)'},
                                                                {'account_id': sal, 'debit': 100}]}),
            ('both sides', {'date': '2026-01-01', 'lines': [{'account_id': chk, 'debit': 100, 'credit': 100}, C]}),
            ('single line', {'date': '2026-01-01', 'lines': [D]}),
            ('zero lines', {'date': '2026-01-01', 'lines': []}),
            ('no lines key', {'date': '2026-01-01'}),
            ('lines null', {'date': '2026-01-01', 'lines': None}),
            ('lines dict', {'date': '2026-01-01', 'lines': {}}),
            ('zero amounts', {'date': '2026-01-01', 'lines': [{'account_id': chk, 'debit': 0},
                                                              {'account_id': sal, 'credit': 0}]}),
            ('sub-cent amounts', {'date': '2026-01-01', 'lines': [{'account_id': chk, 'debit': 0.004},
                                                                  {'account_id': sal, 'credit': 0.004}]}),
            ('missing account', {'date': '2026-01-01', 'lines': [{'debit': 100}, C]}),
            ('unknown account', {'date': '2026-01-01', 'lines': [{'account_id': 99999, 'debit': 100}, C]}),
            ('account string', {'date': '2026-01-01', 'lines': [{'account_id': 'abc', 'debit': 100}, C]}),
            ('unknown name', {'date': '2026-01-01', 'lines': [dict(D, name_id=99999), C]}),
            ('name string', {'date': '2026-01-01', 'lines': [dict(D, name_id='abc'), C]}),
            ('bad amount', {'date': '2026-01-01', 'lines': [{'account_id': chk, 'debit': 'ten'}, C]}),
            ('amount list', {'date': '2026-01-01', 'lines': [{'account_id': chk, 'debit': [100]}, C]}),
            ('amount bool', {'date': '2026-01-01', 'lines': [{'account_id': chk, 'debit': True}, C]}),
            ('amount NaN', {'date': '2026-01-01', 'lines': [{'account_id': chk, 'debit': 'NaN'}, C]}),
            ('amount inf', {'date': '2026-01-01', 'lines': [{'account_id': chk, 'debit': float('inf')}, C]}),
            ('no date', {'lines': [D, C]}),
            ('date null', {'date': None, 'lines': [D, C]}),
            ('date blank', {'date': '', 'lines': [D, C]}),
            ('date feb 30', {'date': '2026-02-30', 'lines': [D, C]}),
            ('date month 13', {'date': '2026-13-01', 'lines': [D, C]}),
            ('date us format', {'date': '01/05/2026', 'lines': [D, C]}),
            ('date int', {'date': 20260101, 'lines': [D, C]}),
            ('date list', {'date': ['2026-01-01'], 'lines': [D, C]}),
            ('date words', {'date': 'yesterday', 'lines': [D, C]}),
        ]
        for label, body in bad:
            status, data = self.quiet('POST', '/api/journals', body)
            self.assertEqual(status, 400, '%s -> %s %s' % (label, status, data))
        self.assertEqual(self.get('/api/journals'), [])
        self.assertEqual(self.tb()['rows'][-1]['values'], [0.0, 0.0])

    def test_blank_rows_are_ignored(self):
        j = self.post('/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': None, 'debit': '', 'credit': ''},
            {'account_id': self.acct('Checking'), 'debit': 10, 'credit': ''},
            {},
            {'account_id': self.acct('Sales'), 'debit': '', 'credit': '10.00'},
            {'account_id': '', 'debit': 0, 'credit': 0, 'memo': 'stray memo'}]})
        self.assertEqual([l['account'] for l in j['lines']], ['Checking', 'Sales'])

    def test_row_with_account_but_no_amount_is_an_error(self):
        self.post('/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': self.acct('Checking'), 'debit': 10}, {'account_id': self.acct('Sales'), 'credit': 10},
            {'account_id': self.acct('Travel')}]}, expect=400)

    def test_rounding(self):
        chk, sal = self.acct('Checking'), self.acct('Sales')
        # 0.1 + 0.2 (float 0.30000000000000004) balances against 0.30
        j = self.post('/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': chk, 'debit': 0.1}, {'account_id': chk, 'debit': 0.2},
            {'account_id': sal, 'credit': 0.1 + 0.2}]})
        self.assertEqual((j['total'], j['lines'][2]['credit']), (0.3, 0.3))
        # 3-decimal inputs round half-up to cents, per line
        j = self.post('/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': chk, 'debit': '10.005'}, {'account_id': sal, 'credit': 10.01}]})
        self.assertEqual(j['lines'][0]['debit'], 10.01)
        j = self.post('/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': chk, 'debit': '10.004'}, {'account_id': sal, 'credit': '10.00'}]})
        self.assertEqual(j['lines'][0]['debit'], 10.0)
        # thirds do not silently balance: 33.333 x 3 = 99.99 after rounding, not 100.00
        r = self.post('/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': chk, 'debit': '33.333'}, {'account_id': chk, 'debit': '33.333'},
            {'account_id': chk, 'debit': '33.333'}, {'account_id': sal, 'credit': 100}]}, expect=400)
        self.assertIn('99.99', r['error'])
        # many small float lines
        j = self.post('/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': chk, 'debit': 0.07} for _ in range(100)] + [{'account_id': sal, 'credit': 7}]})
        self.assertEqual(j['total'], 7.0)
        self.assertEqual(self.balance('Checking'), 27.31)
        self.assertBooksBalance()

    def test_amount_string_formats(self):
        chk, sal = self.acct('Checking'), self.acct('Sales')
        j = self.post('/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': chk, 'debit': ' $1,234.50 '}, {'account_id': sal, 'credit': '1234.5'}]})
        self.assertEqual(j['total'], 1234.5)
        j = self.post('/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': chk, 'debit': 999999999.99}, {'account_id': sal, 'credit': '999,999,999.99'}]})
        self.assertEqual(j['total'], 999999999.99)
        self.assertBooksBalance()

    def test_ar_needs_customer_and_ap_needs_vendor(self):
        c = self.post('/api/customers', {'name': 'C'})['id']
        v = self.post('/api/vendors', {'name': 'V'})['id']
        post = lambda lines, expect=200: self.post('/api/journals', {'date': '2026-01-01', 'lines': lines}, expect)
        post(self.lines(('Accounts Receivable', 100, 0), ('Sales', 0, 100)), 400)
        post(self.lines(('Accounts Receivable', 100, 0, v), ('Sales', 0, 100)), 400)       # vendor on A/R
        post(self.lines(('Rent and Lease', 100, 0), ('Accounts Payable', 0, 100)), 400)
        post(self.lines(('Rent and Lease', 100, 0), ('Accounts Payable', 0, 100, c)), 400)  # customer on A/P
        # the name on the *other* line does not satisfy the rule
        post(self.lines(('Accounts Receivable', 100, 0), ('Sales', 0, 100, c)), 400)
        post(self.lines(('Accounts Receivable', 100, 0, c), ('Sales', 0, 100)))
        post(self.lines(('Rent and Lease', 100, 0), ('Accounts Payable', 0, 100, v)))
        # same rule for user-created A/R and A/P accounts
        self.post('/api/accounts', {'name': 'AR Two', 'type': 'Accounts Receivable'})
        post(self.lines(('AR Two', 100, 0), ('Sales', 0, 100)), 400)
        post(self.lines(('AR Two', 100, 0, c), ('Sales', 0, 100)))

    def test_same_account_on_both_sides_and_many_lines(self):
        j = self.journal('2026-01-01', ('Checking', 5, 0), ('Checking', 0, 5))
        self.assertEqual(self.balance('Checking'), 0.0)
        j = self.journal('2026-01-02', ('Checking', 100, 0), ('Savings', 50, 0), ('Sales', 0, 90),
                         ('Service Income', 0, 35), ('Travel', 0, 25))
        self.assertEqual((len(j['lines']), j['total']), (5, 150.0))
        self.assertEqual([l['account'] for l in j['lines']],
                         ['Checking', 'Savings', 'Sales', 'Service Income', 'Travel'])
        self.assertBooksBalance()


class JournalNumbering(QaCase):
    def one(self, **kw):
        return self.journal('2026-01-01', ('Checking', 1, 0), ('Sales', 0, 1), **kw)

    def test_auto_numbering(self):
        self.assertEqual(self.get('/api/journals/next_ref'), {'ref': '1'})
        self.assertEqual(self.one()['ref'], '1')
        self.assertEqual(self.one()['ref'], '2')
        self.assertEqual(self.one(ref='')['ref'], '3')
        self.assertEqual(self.one(ref='   ')['ref'], '4')
        self.assertEqual(self.one(ref=None)['ref'], '5')
        self.assertEqual(self.one(ref=100)['ref'], '100')
        self.assertEqual(self.get('/api/journals/next_ref'), {'ref': '101'})
        self.assertEqual(self.one(ref='ADJ')['ref'], 'ADJ')            # text refs do not disturb the sequence
        self.assertEqual(self.one()['ref'], '101')

    def test_next_ref_is_not_consumed_by_peeking_or_failed_posts(self):
        self.one()
        for _ in range(3):
            self.assertEqual(self.get('/api/journals/next_ref'), {'ref': '2'})
        self.post('/api/journals', {'date': '2026-01-01', 'lines': []}, expect=400)
        self.assertEqual(self.one()['ref'], '2')

    def test_numbering_after_delete_and_edit(self):
        a, b = self.one(), self.one()
        self.delete('/api/transactions/%d' % b['id'])
        self.assertEqual(self.one()['ref'], '2')
        # editing keeps the ref when none is supplied, and accepts a new one
        lines = self.lines(('Checking', 2, 0), ('Sales', 0, 2))
        self.assertEqual(self.put('/api/journals/%d' % a['id'], {'date': '2026-01-01', 'lines': lines})['ref'], '1')
        self.assertEqual(self.put('/api/journals/%d' % a['id'],
                                  {'date': '2026-01-01', 'ref': '50', 'lines': lines})['ref'], '50')
        self.assertEqual(self.get('/api/journals/next_ref'), {'ref': '51'})

    def test_opening_balance_entries_share_the_journal_sequence(self):
        self.post('/api/accounts', {'name': 'MM', 'type': 'Bank', 'opening_balance': 5,
                                    'opening_date': '2026-01-01'})
        self.assertEqual(self.one()['ref'], '2')

    def test_unicode_digit_ref_does_not_break_numbering(self):
        """BUG (high impact, rare trigger): a journal saved with ref '²' (or any non-ASCII "digit" such as
        '①') makes str.isdigit() true but int() fail in ledger.next_ref. From then on EVERY journal
        without an explicit ref, GET /api/journals/next_ref, and every new account with an opening balance
        returns HTTP 500 until that journal is edited/deleted."""
        self.one(ref='²')
        self.assertNo500('GET', '/api/journals/next_ref', allowed=(200,))
        self.assertNo500('POST', '/api/journals', {'date': '2026-01-01', 'lines': self.lines(
            ('Checking', 1, 0), ('Sales', 0, 1))}, allowed=(200,))
        self.assertNo500('POST', '/api/accounts', {'name': 'MM', 'type': 'Bank', 'opening_balance': 5},
                         allowed=(200,))

    @unittest.expectedFailure
    def test_gap_duplicate_journal_number_is_accepted_silently(self):
        """GAP: two journals can share the same ref with no warning. QuickBooks (with 'warn on duplicate
        journal number', and always for auto-numbering) flags this."""
        self.one(ref='7')
        status, _ = self.quiet('POST', '/api/journals', {'date': '2026-01-01', 'ref': '7', 'lines': self.lines(
            ('Checking', 1, 0), ('Sales', 0, 1))})
        self.assertEqual(status, 409)

    def test_gap_auto_number_does_not_continue_prefixed_refs(self):
        """GAP: after 'JE-9' QuickBooks suggests 'JE-10' (it increments the trailing number of the last
        entry). Here prefixed refs are ignored and numbering restarts at 1."""
        self.one(ref='JE-9')
        self.assertEqual(self.get('/api/journals/next_ref'), {'ref': 'JE-10'})


class JournalEditDelete(QaCase):
    def setUp(self):
        super().setUp()
        self.j1 = self.journal('2026-01-10', ('Checking', 1000, 0), ('Sales', 0, 1000), memo='January sales')
        self.j2 = self.journal('2026-01-20', ('Rent and Lease', 400, 0), ('Checking', 0, 400))

    def pl(self, start='2026-01-01', end='2026-01-31'):
        return self.get('/api/reports/profit_loss', start=start, end=end)

    def test_edit_replaces_lines_and_reports_follow(self):
        j = self.put('/api/journals/%d' % self.j1['id'], {
            'date': '2026-02-03', 'ref': 'R1', 'memo': 'moved',
            'lines': self.lines(('Savings', 700, 0), ('Service Income', 0, 700))})
        self.assertEqual((j['id'], j['date'], j['ref'], j['memo'], j['total']),
                         (self.j1['id'], '2026-02-03', 'R1', 'moved', 700.0))
        self.assertEqual([l['account'] for l in j['lines']], ['Savings', 'Service Income'])
        self.assertEqual(self.balance('Checking'), -400.0)
        self.assertEqual(self.balance('Savings'), 700.0)
        self.assertEqual(self.val(self.pl(), 'Net Income'), -400.0)                       # Jan lost the sale
        self.assertEqual(self.val(self.pl('2026-02-01', '2026-02-28'), 'Net Income'), 700.0)
        self.assertIsNone(self.tb_row('Sales'))
        self.assertEqual(self.tb_row('Service Income'), [None, 700.0])
        self.assertEqual(len(self.get('/api/journals')), 2)
        self.assertBooksBalance()
        self.assertBooksBalance(end='2026-01-31')

    def test_invalid_edit_changes_nothing(self):
        chk, sal = self.acct('Checking'), self.acct('Sales')
        url = '/api/journals/%d' % self.j1['id']
        for body in ({'date': '2026-01-10', 'lines': [{'account_id': chk, 'debit': 5},
                                                      {'account_id': sal, 'credit': 4}]},
                     {'date': '2026-01-10', 'lines': [{'account_id': chk, 'debit': 5}]},
                     {'date': '2026-01-10', 'lines': []},
                     {'date': '2026-01-10'},
                     {'date': 'bad', 'lines': [{'account_id': chk, 'debit': 5}, {'account_id': sal, 'credit': 5}]},
                     {'date': '2026-01-10', 'lines': [{'account_id': 99999, 'debit': 5},
                                                      {'account_id': sal, 'credit': 5}]}):
            self.put(url, body, expect=400)
        self.assertEqual(self.get('/api/transactions/%d' % self.j1['id']), self.j1)
        self.assertEqual(self.balance('Checking'), 600.0)
        self.assertBooksBalance()

    def test_edit_missing_or_wrong_kind(self):
        ok = {'date': '2026-01-10', 'lines': self.lines(('Checking', 5, 0), ('Sales', 0, 5))}
        self.put('/api/journals/99999', ok, expect=404)
        c = self.post('/api/customers', {'name': 'C'})
        inv = self.post('/api/invoices', {'customer_id': c['id'], 'date': '2026-01-01', 'lines': [
            {'description': 'Work', 'qty': 1, 'rate': 100, 'account_id': self.acct('Sales')}]})
        self.put('/api/journals/%d' % inv['txn_id'], ok, expect=400)
        self.assertEqual(self.get('/api/transactions/%d' % inv['txn_id'])['type'], 'Invoice')

    def test_edit_without_memo_keeps_the_memo(self):
        """BUG (low): PUT /api/journals/<id> keeps the old ref when 'ref' is omitted but wipes the memo when
        'memo' is omitted. Partial update semantics are inconsistent; the memo is silently lost."""
        j = self.put('/api/journals/%d' % self.j1['id'], {
            'date': '2026-01-10', 'lines': self.lines(('Checking', 1000, 0), ('Sales', 0, 1000))})
        self.assertEqual(j['ref'], self.j1['ref'])
        self.assertEqual(j['memo'], 'January sales')

    def test_delete_removes_effect_everywhere(self):
        self.assertEqual(self.delete('/api/transactions/%d' % self.j1['id']), {'deleted': True})
        self.get('/api/transactions/%d' % self.j1['id'], expect=404)
        self.delete('/api/transactions/%d' % self.j1['id'], expect=404)
        self.put('/api/journals/%d' % self.j1['id'], {'date': '2026-01-10', 'lines': self.lines(
            ('Checking', 5, 0), ('Sales', 0, 5))}, expect=404)
        self.assertEqual([j['id'] for j in self.get('/api/journals')], [self.j2['id']])
        self.assertEqual(self.balance('Checking'), -400.0)
        self.assertEqual(self.val(self.pl(), 'Net Income'), -400.0)
        self.assertIsNone(self.tb_row('Sales'))
        self.assertBooksBalance()
        self.delete('/api/transactions/%d' % self.j2['id'])
        self.assertEqual(self.tb()['rows'], [{'label': 'TOTAL', 'level': 0, 'kind': 'grand',
                                              'values': [0.0, 0.0], 'drill': [None, None]}])
        self.assertEqual(self.balance('Checking'), 0.0)
        self.assertFalse(self.account(self.acct('Checking'))['has_transactions'])

    def test_list_filters_and_order(self):
        j3 = self.journal('2026-01-20', ('Travel', 1, 0), ('Checking', 0, 1))
        ids = [j['id'] for j in self.get('/api/journals')]
        self.assertEqual(ids, [j3['id'], self.j2['id'], self.j1['id']])              # newest first
        self.assertEqual([j['id'] for j in self.get('/api/journals', start='2026-01-11')],
                         [j3['id'], self.j2['id']])
        self.assertEqual([j['id'] for j in self.get('/api/journals', end='2026-01-10')], [self.j1['id']])
        self.assertEqual(self.get('/api/journals', start='2026-02-01'), [])
        self.assertEqual(self.get('/api/journals')[2]['accounts'], 'Checking, Sales')
        self.get('/api/journals', start='nope', expect=400)

    def test_transaction_detail_report(self):
        td = self.get('/api/reports/transaction_detail', start='2026-01-01', end='2026-01-31',
                      account_id=self.acct('Checking'))
        g = td['groups'][0]
        self.assertEqual((g['beginning_balance'], g['total_debit'], g['total_credit'], g['ending_balance']),
                         (0.0, 1000.0, 400.0, 600.0))
        self.assertEqual([r['balance'] for r in g['rows']], [1000.0, 600.0])
        self.assertEqual([r['split'] for r in g['rows']], ['Sales', 'Rent and Lease'])
        # a later period carries the balance forward and shows no rows
        g = self.get('/api/reports/transaction_detail', start='2026-02-01', end='2026-02-28',
                     account_id=self.acct('Checking'))['groups'][0]
        self.assertEqual((g['beginning_balance'], g['rows'], g['ending_balance']), (600.0, [], 600.0))
        # after an edit and a delete the report follows
        self.put('/api/journals/%d' % self.j2['id'], {'date': '2026-01-20', 'lines': self.lines(
            ('Rent and Lease', 250, 0), ('Checking', 0, 250))})
        self.delete('/api/transactions/%d' % self.j1['id'])
        everything = self.get('/api/reports/transaction_detail', start='2026-01-01', end='2026-01-31')
        self.assertEqual({g['account']: g['ending_balance'] for g in everything['groups']},
                         {'Checking': -250.0, 'Rent and Lease': 250.0})

    def test_prior_year_income_rolls_into_retained_earnings(self):
        self.journal('2025-06-01', ('Checking', 300, 0), ('Sales', 0, 300))
        bs = self.get('/api/reports/balance_sheet', end='2026-01-31')
        self.assertEqual(self.val(bs, 'Retained Earnings'), 300.0)
        self.assertEqual(self.val(bs, 'Net Income'), 600.0)
        self.assertEqual(self.tb_row('Retained Earnings'), [None, 300.0])
        self.assertBooksBalance()
        self.assertBooksBalance(end='2025-12-31')


class JournalMalformedInput(QaCase):
    """None of these may produce an HTTP 500."""

    def setUp(self):
        super().setUp()
        self.chk, self.sal = self.acct('Checking'), self.acct('Sales')
        self.C = {'account_id': self.sal, 'credit': 100}

    def post_lines(self, *lines):
        return self.assertNo500('POST', '/api/journals', {'date': '2026-01-01', 'lines': list(lines)})

    def test_line_that_is_not_an_object(self):
        """BUG: lines: ["x", {...}] or [null, {...}] -> 500 (AttributeError .get)."""
        for junk in ('x', None, 5, [1]):
            self.post_lines(junk, self.C)

    def test_non_string_line_memo(self):
        """BUG: a numeric line memo ({"memo": 5}) -> 500 ('int' object has no attribute 'strip')."""
        status, data = self.quiet('POST', '/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': self.chk, 'debit': 100, 'memo': 5}, self.C]})
        self.assertIn(status, (200, 400), data)

    def test_huge_amounts(self):
        """BUG: debit/credit 1e20 -> 500 (SQLite overflow); 1e30 or "1e400" -> 500 (decimal.InvalidOperation
        raised by quantize outside the try block in util.to_cents)."""
        for amt in (1e20, 1e30, '1e400', 10 ** 40):
            self.post_lines({'account_id': self.chk, 'debit': amt}, {'account_id': self.sal, 'credit': amt})

    def test_huge_account_id(self):
        """BUG: account_id 10**30 -> 500 (SQLite overflow)."""
        self.post_lines({'account_id': 10 ** 30, 'debit': 100}, self.C)

    def test_infinite_account_id(self):
        """BUG: account_id 1e999 (json -> inf) -> 500 (OverflowError not caught by util.as_int)."""
        self.post_lines({'account_id': float('inf'), 'debit': 100}, self.C)

    def test_huge_name_id(self):
        """BUG: name_id 10**30 -> 500 (SQLite overflow)."""
        self.post_lines({'account_id': self.chk, 'debit': 100, 'name_id': 10 ** 30}, self.C)

    def test_huge_path_id(self):
        """BUG: GET/DELETE /api/transactions/<25-digit id>, PUT /api/journals/<25-digit id> -> 500."""
        self.assertNo500('GET', '/api/transactions/9999999999999999999999999')
        self.assertNo500('DELETE', '/api/transactions/9999999999999999999999999')
        self.assertNo500('PUT', '/api/journals/9999999999999999999999999',
                         {'date': '2026-01-01', 'lines': [{'account_id': self.chk, 'debit': 100}, self.C]})

    def test_fractional_account_id_is_not_truncated(self):
        """BUG (low): account_id 1.9 is truncated to account 1 (Checking) and posted, instead of a 400.
        Same for parent_id on accounts."""
        status, data = self.quiet('POST', '/api/journals', {'date': '2026-01-01', 'lines': [
            {'account_id': self.chk + 0.9, 'debit': 100}, self.C]})
        self.assertEqual(status, 400, data)

    def test_date_with_trailing_garbage(self):
        """BUG (low): date '2026-01-01XYZ' is accepted (only the first 10 characters are parsed)."""
        status, data = self.quiet('POST', '/api/journals', {'date': '2026-01-01XYZ', 'lines': [
            {'account_id': self.chk, 'debit': 100}, self.C]})
        self.assertEqual(status, 400, data)

    def test_nothing_was_posted_by_any_rejected_request(self):
        for body in ({'date': '2026-01-01', 'lines': ['x', self.C]},
                     {'date': '2026-01-01', 'lines': [{'account_id': self.chk, 'debit': 1e20},
                                                      {'account_id': self.sal, 'credit': 1e20}]},
                     {'date': '2026-01-01', 'lines': [{'account_id': 10 ** 30, 'debit': 100}, self.C]}):
            status, _ = self.quiet('POST', '/api/journals', body)
            self.assertNotEqual(status, 200)
        self.assertEqual(self.get('/api/journals'), [])
        self.assertEqual(self.balance('Checking'), 0.0)

    def test_gap_implausible_year_accepted(self):
        """GAP: a typo such as year 0206 or 0001 is accepted and posted (and then sits before every report
        range). QuickBooks rejects dates outside a sane range."""
        status, data = self.quiet('POST', '/api/journals', {'date': '0206-01-01', 'lines': [
            {'account_id': self.chk, 'debit': 100}, self.C]})
        self.assertEqual(status, 400, data)


if __name__ == '__main__':
    unittest.main()
