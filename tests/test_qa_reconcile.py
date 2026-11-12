"""QA tests for bank / credit card reconciliation.

Tests that fail expose reproduced bugs (each has a BUG: docstring).
Tests marked @unittest.expectedFailure document QuickBooks-behaviour gaps (GAP: docstring);
they do not count as failures.
"""
import unittest

from helpers import ApiCase

BANK_CSV = """Date,Description,Amount
01/05/2026,OFFICE DEPOT #123,-150.25
01/06/2026,CUSTOMER DEPOSIT,2000.00
01/10/2026,SHELL OIL 5551,-45.00
02/03/2026,FEB CHARGE,-20.00
"""

CC_CSV = """Transaction Date,Post Date,Description,Category,Type,Amount
01/03/2026,01/04/2026,DELTA AIR LINES,Travel,Sale,-420.00
01/08/2026,01/09/2026,STAPLES,Office,Sale,-80.00
"""


class RecCase(ApiCase):
    def setUp(self):
        super().setUp()
        self.chk = self.acct('Checking')
        self.sav = self.acct('Savings')
        self.cc = self.acct('Credit Card')

    def start(self, account_id, date, ending, expect=200):
        return self.post('/api/reconciliations', {'account_id': account_id, 'statement_date': date,
                                                  'ending_balance': ending}, expect=expect)

    def rec(self, rid):
        return self.get('/api/reconciliations/%d' % rid)

    def toggle(self, rec, line_ids, cleared=True, expect=200):
        return self.post('/api/reconciliations/%d/toggle' % rec['id'],
                         {'line_ids': line_ids, 'cleared': cleared}, expect=expect)

    def finish(self, rec, expect=200):
        return self.post('/api/reconciliations/%d/finish' % rec['id'], expect=expect)

    def ids(self, rec, pred=lambda l: True):
        return [l['line_id'] for l in rec['lines'] if pred(l)]

    def clear_all_and_finish(self, rec):
        rec = self.toggle(rec, self.ids(rec))
        self.assertEqual(rec['difference'], 0)
        return self.finish(rec)

    def bank_line(self, txn, account_id):
        return [l['id'] for l in txn['lines'] if l['account_id'] == account_id][0]

    def recon_acct(self, account_id):
        return [a for a in self.get('/api/reconcile/accounts') if a['account_id'] == account_id][0]

    def std(self):
        """Jan: +1000, -400, +250, -75 (outstanding); Feb: +10."""
        self.t_inv = self.journal('2026-01-01', ('Checking', 1000, 0), ("Owner's Investment", 0, 1000))
        self.t_rent = self.journal('2026-01-10', ('Rent and Lease', 400, 0), ('Checking', 0, 400))
        self.t_sale = self.journal('2026-01-20', ('Checking', 250, 0), ('Sales', 0, 250))
        self.t_util = self.journal('2026-01-30', ('Utilities', 75, 0), ('Checking', 0, 75))
        self.t_feb = self.journal('2026-02-05', ('Checking', 10, 0), ('Sales', 0, 10))

    def std_jan_done(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', 850)
        rec = self.toggle(rec, self.ids(rec, lambda l: l['date'] <= '2026-01-20'))
        return self.finish(rec)


# ------------------------------------------------------------------ bank math

class BankMath(RecCase):
    def test_beginning_cleared_difference(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', '850.00')
        self.assertEqual((rec['beginning_balance'], rec['cleared_balance'], rec['difference'], rec['ending_balance']),
                         (0, 0, 850.0, 850.0))
        self.assertEqual([(l['deposit'], l['payment'], l['amount'], l['cleared']) for l in rec['lines']],
                         [(1000.0, None, 1000.0, False), (None, 400.0, -400.0, False),
                          (250.0, None, 250.0, False), (None, 75.0, -75.0, False)])
        rec = self.toggle(rec, self.ids(rec, lambda l: l['deposit'] == 1000.0))
        self.assertEqual((rec['cleared_balance'], rec['difference'], rec['cleared_deposit_count'],
                          rec['cleared_payment_count']), (1000.0, -150.0, 1, 0))
        rec = self.toggle(rec, self.ids(rec, lambda l: l['payment'] == 400.0))
        self.assertEqual((rec['cleared_balance'], rec['difference'], rec['cleared_payments']), (600.0, 250.0, 400.0))
        # un-check
        rec = self.toggle(rec, self.ids(rec, lambda l: l['payment'] == 400.0), cleared=False)
        self.assertEqual((rec['cleared_balance'], rec['difference']), (1000.0, -150.0))
        # the GET view agrees with the toggle response
        self.assertEqual(self.rec(rec['id']), rec)

    def test_cannot_finish_with_difference(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', 850)
        rec = self.toggle(rec, self.ids(rec))                       # everything incl. outstanding check: 775
        self.assertEqual(rec['difference'], 75.0)
        self.finish(rec, expect=400)
        self.assertEqual(self.rec(rec['id'])['status'], 'open')
        rec = self.put('/api/reconciliations/%d' % rec['id'], {'ending_balance': 775.01})
        self.assertEqual(rec['difference'], 0.01)
        self.finish(rec, expect=400)                                # one cent off is still off
        rec = self.put('/api/reconciliations/%d' % rec['id'], {'ending_balance': 775})
        self.assertEqual(self.finish(rec)['status'], 'done')

    def test_overdrawn_bank_account(self):
        self.journal('2026-01-02', ('Checking', 100, 0), ('Sales', 0, 100))
        self.journal('2026-01-05', ('Rent and Lease', 300, 0), ('Checking', 0, 300))
        self.journal('2026-02-02', ('Checking', 50, 0), ('Sales', 0, 50))
        rec = self.start(self.chk, '2026-01-31', '-200.00')
        self.assertEqual((rec['ending_balance'], rec['difference']), (-200.0, -200.0))
        done = self.clear_all_and_finish(rec)
        self.assertEqual((done['cleared_balance'], done['difference'], done['register_balance'],
                          done['uncleared_total']), (-200.0, 0, -200.0, 0))
        rec2 = self.start(self.chk, '2026-02-28', -150)
        self.assertEqual((rec2['beginning_balance'], rec2['difference']), (-200.0, 50.0))
        done2 = self.clear_all_and_finish(rec2)
        self.assertEqual((done2['beginning_balance'], done2['cleared_balance']), (-200.0, -150.0))

    def test_cents_precision(self):
        for i, amt in enumerate(['0.10', '0.20', '1234.56', '0.07', '19.99']):
            self.journal('2026-01-%02d' % (i + 1), ('Checking', amt, 0), ('Sales', 0, amt))
        self.journal('2026-01-09', ('Utilities', '0.30', 0), ('Checking', 0, '0.30'))
        rec = self.start(self.chk, '2026-01-31', '1,254.62')
        rec = self.toggle(rec, self.ids(rec))
        self.assertEqual((rec['cleared_balance'], rec['difference']), (1254.62, 0))
        self.finish(rec)

    def test_zero_difference_nothing_cleared(self):
        """QuickBooks lets you finish when the difference is 0.00 even with nothing checked."""
        self.std()
        rec = self.start(self.chk, '2026-01-31', 0)
        self.assertEqual(rec['difference'], 0)
        done = self.finish(rec)
        self.assertEqual((done['status'], done['lines'], done['cleared_balance']), ('done', [], 0))
        self.assertEqual((done['register_balance'], done['uncleared_total'], len(done['uncleared'])), (775.0, 775.0, 4))
        # nothing got locked, and the next one still starts at 0 with every item available
        self.delete('/api/transactions/%d' % self.t_util['id'])
        rec2 = self.start(self.chk, '2026-02-28', 860)
        self.assertEqual((rec2['beginning_balance'], len(rec2['lines'])), (0, 4))
        self.clear_all_and_finish(rec2)

    def test_two_lines_same_account_in_one_transaction(self):
        t = self.journal('2026-01-03', ('Checking', 500, 0), ('Checking', 0, 200), ('Sales', 0, 300))
        rec = self.start(self.chk, '2026-01-31', 300)
        self.assertEqual(len(rec['lines']), 2)
        rec = self.toggle(rec, self.ids(rec, lambda l: l['deposit'] == 500.0))
        self.assertEqual(rec['difference'], -200.0)
        rec = self.toggle(rec, self.ids(rec, lambda l: l['payment'] == 200.0))
        self.finish(rec)
        self.delete('/api/transactions/%d' % t['id'], expect=409)

    def test_reconcile_accounts_listing(self):
        done = self.std_jan_done()
        a = self.recon_acct(self.chk)
        self.assertEqual((a['balance'], a['last_statement_date'], a['last_ending_balance'], a['beginning_balance'],
                          a['open_reconciliation_id']), (785.0, '2026-01-31', 850.0, 850.0, None))
        rec2 = self.start(self.chk, '2026-02-28', 785)
        self.assertEqual(self.recon_acct(self.chk)['open_reconciliation_id'], rec2['id'])
        s = self.recon_acct(self.sav)
        self.assertEqual((s['balance'], s['last_statement_date'], s['beginning_balance']), (0, None, 0))
        lst = self.get('/api/reconciliations', account_id=self.chk)
        self.assertEqual([(r['id'], r['status'], r['can_undo']) for r in lst],
                         [(rec2['id'], 'open', True), (done['id'], 'done', True)])
        self.assertEqual(self.get('/api/reconciliations', account_id=self.sav), [])

    def test_only_bank_and_card_accounts(self):
        self.start(self.acct('Sales'), '2026-01-31', 0, expect=400)
        self.start(self.acct('Accounts Receivable'), '2026-01-31', 0, expect=400)
        self.start(999999, '2026-01-31', 0, expect=400)


# ------------------------------------------------------------------ chaining / dates / undo

class Chaining(RecCase):
    def three_months(self):
        self.std()
        self.journal('2026-02-15', ('Rent and Lease', 400, 0), ('Checking', 0, 400))
        self.journal('2026-03-10', ('Checking', 2000, 0), ('Sales', 0, 2000))
        jan = self.start(self.chk, '2026-01-31', 850)
        jan = self.finish(self.toggle(jan, self.ids(jan, lambda l: l['date'] <= '2026-01-20')))
        feb = self.start(self.chk, '2026-02-28', 385)       # 850 - 75 + 10 - 400
        self.assertEqual(feb['beginning_balance'], 850.0)
        self.assertEqual(len(feb['lines']), 3)              # Jan outstanding check carried over + 2 Feb items
        feb = self.clear_all_and_finish(feb)
        mar = self.start(self.chk, '2026-03-31', 2385)
        self.assertEqual((mar['beginning_balance'], len(mar['lines']), mar['difference']), (385.0, 1, 2000.0))
        mar = self.clear_all_and_finish(mar)
        return jan, feb, mar

    def test_consecutive_months_chain(self):
        jan, feb, mar = self.three_months()
        self.assertEqual([(r['beginning_balance'], r['ending_balance'], r['cleared_balance'], r['difference'])
                          for r in (self.rec(jan['id']), self.rec(feb['id']), self.rec(mar['id']))],
                         [(0, 850.0, 850.0, 0), (850.0, 385.0, 385.0, 0), (385.0, 2385.0, 2385.0, 0)])
        lst = self.get('/api/reconciliations', account_id=self.chk)
        self.assertEqual([(r['statement_date'], r['beginning_balance'], r['ending_balance'], r['can_undo'])
                          for r in lst],
                         [('2026-03-31', 385.0, 2385.0, True), ('2026-02-28', 850.0, 385.0, False),
                          ('2026-01-31', 0, 850.0, False)])
        self.assertEqual(self.recon_acct(self.chk)['beginning_balance'], 2385.0)

    def test_undo_latest_first(self):
        jan, feb, mar = self.three_months()
        self.delete('/api/reconciliations/%d' % jan['id'], expect=409)
        self.delete('/api/reconciliations/%d' % feb['id'], expect=409)
        self.assertEqual(self.rec(jan['id'])['status'], 'done')
        self.delete('/api/reconciliations/%d' % mar['id'])
        self.get('/api/reconciliations/%d' % mar['id'], expect=404)
        self.assertEqual(self.recon_acct(self.chk)['beginning_balance'], 385.0)
        self.delete('/api/reconciliations/%d' % feb['id'])
        self.delete('/api/reconciliations/%d' % jan['id'])
        self.assertEqual(self.recon_acct(self.chk)['beginning_balance'], 0)
        self.assertEqual(self.get('/api/reconciliations', account_id=self.chk), [])
        # everything is unlocked and available again
        rec = self.start(self.chk, '2026-03-31', 2385)
        self.assertEqual((len(rec['lines']), [l['cleared'] for l in rec['lines']]), (7, [False] * 7))
        self.delete('/api/transactions/%d' % self.t_rent['id'])

    def test_undo_restores_previous_state_exactly(self):
        jan, feb, mar = self.three_months()
        self.delete('/api/reconciliations/%d' % mar['id'])
        again = self.start(self.chk, '2026-03-31', 2385)
        self.assertEqual((again['beginning_balance'], len(again['lines']), again['difference']), (385.0, 1, 2000.0))
        # the earlier finished reports are untouched
        self.assertEqual(self.rec(feb['id'])['cleared_balance'], 385.0)

    def test_statement_date_rules(self):
        done = self.std_jan_done()
        self.start(self.chk, '2026-01-31', 785, expect=400)     # same as last
        self.start(self.chk, '2026-01-15', 785, expect=400)     # before last
        self.start(self.chk, 'not-a-date', 785, expect=400)
        self.start(self.chk, '', 785, expect=400)
        self.start(self.chk, '2026-02-30', 785, expect=400)
        self.start(self.chk, '2026-02-28', '', expect=400)      # ending balance required
        self.start(self.chk, '2026-02-28', None, expect=400)
        self.start(self.chk, '2026-02-28', 'abc', expect=400)
        self.assertEqual(self.recon_acct(self.chk)['open_reconciliation_id'], None)
        rec = self.start(self.chk, '2026-02-01', 775)           # the day after is fine
        self.put('/api/reconciliations/%d' % rec['id'], {'statement_date': '2026-01-31'}, expect=400)
        self.put('/api/reconciliations/%d' % rec['id'], {'statement_date': '2026-01-01'}, expect=400)
        self.put('/api/reconciliations/%d' % rec['id'], {'statement_date': 'garbage'}, expect=400)
        self.assertEqual(self.rec(rec['id'])['statement_date'], '2026-02-01')
        # other accounts are not constrained by Checking's history
        self.start(self.sav, '2026-01-15', 0)
        self.assertEqual(self.rec(done['id'])['status'], 'done')

    def test_lines_after_statement_date(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', 850)
        self.assertTrue(all(l['date'] <= '2026-01-31' for l in rec['lines']))
        feb_line = self.bank_line(self.t_feb, self.chk)
        self.assertNotIn(feb_line, self.ids(rec))
        self.toggle(rec, [feb_line], expect=400)
        self.assertEqual(self.get('/api/transactions/%d' % self.t_feb['id'])['lines'][0]['recon_status'], '')
        # a transaction dated on the statement date is included
        on = self.journal('2026-01-31', ('Checking', 5, 0), ('Sales', 0, 5))
        self.assertIn(self.bank_line(on, self.chk), self.ids(self.rec(rec['id'])))

    def test_only_one_in_progress_per_account(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', 850)
        self.start(self.chk, '2026-02-28', 785, expect=409)
        sav = self.start(self.sav, '2026-01-31', 0)             # a different account is independent
        cc = self.start(self.cc, '2026-01-31', 0)
        self.assertEqual(len({rec['id'], sav['id'], cc['id']}), 3)
        self.assertEqual(len([r for r in self.get('/api/reconciliations') if r['status'] == 'open']), 3)
        self.finish(sav)
        self.start(self.sav, '2026-02-28', 0)                   # after finishing, the next one can start


class Toggle(RecCase):
    def test_reject_line_from_another_account(self):
        self.std()
        other = self.journal('2026-01-12', ('Savings', 300, 0), ('Sales', 0, 300))
        rec = self.start(self.chk, '2026-01-31', 850)
        self.toggle(rec, [self.bank_line(other, self.sav)], expect=400)
        # the non-bank side of a Checking transaction is not a Checking line either
        sales_line = [l['id'] for l in self.t_sale['lines'] if l['account_id'] != self.chk][0]
        self.toggle(rec, [sales_line], expect=400)
        self.toggle(rec, [987654], expect=400)
        self.toggle(rec, ['x'], expect=400)
        self.assertEqual(self.rec(rec['id'])['cleared_balance'], 0)

    def test_bad_id_in_batch_rolls_back_whole_toggle(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', 850)
        good = self.ids(rec)[:2]
        self.toggle(rec, good + [987654], expect=400)
        self.assertEqual([l['cleared'] for l in self.rec(rec['id'])['lines']], [False] * 4)

    def test_already_reconciled_line(self):
        done = self.std_jan_done()
        rec2 = self.start(self.chk, '2026-02-28', 785)
        reconciled = self.ids(done)[0]
        self.assertNotIn(reconciled, self.ids(rec2))            # not offered again
        self.toggle(rec2, [reconciled], cleared=True, expect=409)
        self.toggle(rec2, [reconciled], cleared=False, expect=409)   # and cannot be un-reconciled this way
        self.assertEqual(self.rec(done['id'])['cleared_balance'], 850.0)
        self.assertEqual(self.rec(rec2['id'])['beginning_balance'], 850.0)

    def test_toggle_finished_reconciliation(self):
        done = self.std_jan_done()
        self.toggle(done, self.ids(done), cleared=False, expect=409)
        self.put('/api/reconciliations/%d' % done['id'], {'ending_balance': 1}, expect=409)
        self.finish(done, expect=409)
        self.assertEqual(self.rec(done['id'])['ending_balance'], 850.0)

    def test_toggle_idempotent_and_empty(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', 850)
        one = self.ids(rec)[:1]
        self.toggle(rec, one)
        r = self.toggle(rec, one)
        self.assertEqual(r['cleared_balance'], 1000.0)
        r = self.toggle(rec, [])
        self.assertEqual(r['cleared_balance'], 1000.0)
        r = self.toggle(rec, one + one, cleared=False)
        self.assertEqual(r['cleared_balance'], 0)

    def test_not_found(self):
        self.get('/api/reconciliations/4242', expect=404)
        self.put('/api/reconciliations/4242', {'ending_balance': 1}, expect=404)
        self.post('/api/reconciliations/4242/toggle', {'line_ids': [1], 'cleared': True}, expect=404)
        self.post('/api/reconciliations/4242/finish', expect=404)
        self.delete('/api/reconciliations/4242', expect=404)


class ChangeMidway(RecCase):
    def test_change_ending_balance(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', 800)
        rec = self.toggle(rec, self.ids(rec, lambda l: l['date'] <= '2026-01-20'))
        self.assertEqual(rec['difference'], -50.0)
        rec = self.put('/api/reconciliations/%d' % rec['id'], {'ending_balance': '850'})
        self.assertEqual((rec['ending_balance'], rec['difference'], rec['statement_date']), (850.0, 0, '2026-01-31'))
        rec = self.put('/api/reconciliations/%d' % rec['id'], {'ending_balance': 0})     # zero is a real value
        self.assertEqual((rec['ending_balance'], rec['difference']), (0, -850.0))
        self.put('/api/reconciliations/%d' % rec['id'], {'ending_balance': 'abc'}, expect=400)
        rec = self.put('/api/reconciliations/%d' % rec['id'], {})                         # nothing sent: no change
        self.assertEqual((rec['ending_balance'], rec['cleared_balance']), (0, 850.0))

    def test_move_statement_date_earlier_unchecks_later_lines(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', 775)
        rec = self.toggle(rec, self.ids(rec))
        self.assertEqual(rec['difference'], 0)
        rec = self.put('/api/reconciliations/%d' % rec['id'], {'statement_date': '2026-01-15'})
        self.assertEqual((len(rec['lines']), rec['cleared_balance'], rec['difference']), (2, 600.0, 175.0))
        # the dropped lines are really un-checked, not silently still attached
        for t in (self.t_sale, self.t_util):
            st = [l['recon_status'] for l in self.get('/api/transactions/%d' % t['id'])['lines']]
            self.assertEqual(st, ['', ''])
        self.finish(rec, expect=400)
        rec = self.put('/api/reconciliations/%d' % rec['id'], {'ending_balance': 600})
        done = self.finish(rec)
        self.assertEqual((done['register_balance'], done['uncleared_total']), (600.0, 0))

    def test_move_statement_date_later_shows_more_lines(self):
        self.std()
        rec = self.start(self.chk, '2026-01-15', 600)
        self.assertEqual(len(rec['lines']), 2)
        rec = self.toggle(rec, self.ids(rec))
        rec = self.put('/api/reconciliations/%d' % rec['id'], {'statement_date': '2026-02-28', 'ending_balance': 785})
        self.assertEqual((len(rec['lines']), rec['cleared_balance'], rec['difference']), (5, 600.0, 185.0))
        self.assertEqual(sum(1 for l in rec['lines'] if l['cleared']), 2)


class CancelVsUndo(RecCase):
    def test_cancel_in_progress(self):
        done = self.std_jan_done()
        rec2 = self.start(self.chk, '2026-02-28', 785)
        rec2 = self.toggle(rec2, self.ids(rec2))
        self.assertEqual(rec2['difference'], 0)
        self.delete('/api/reconciliations/%d' % rec2['id'])
        # January stays finished and locked
        self.assertEqual(self.rec(done['id'])['status'], 'done')
        self.delete('/api/transactions/%d' % self.t_rent['id'], expect=409)
        # the Feb check marks are gone
        for t in (self.t_util, self.t_feb):
            self.assertFalse(self.get('/api/transactions/%d' % t['id'])['reconciled'])
            self.assertEqual({l['recon_status'] for l in self.get('/api/transactions/%d' % t['id'])['lines']}, {''})
        again = self.start(self.chk, '2026-02-28', 785)
        self.assertEqual((again['beginning_balance'], [l['cleared'] for l in again['lines']]), (850.0, [False, False]))

    def test_undo_finished_unlocks(self):
        done = self.std_jan_done()
        self.assertTrue(self.get('/api/transactions/%d' % self.t_rent['id'])['reconciled'])
        self.delete('/api/reconciliations/%d' % done['id'])
        self.assertFalse(self.get('/api/transactions/%d' % self.t_rent['id'])['reconciled'])
        self.assertEqual(self.recon_acct(self.chk)['last_statement_date'], None)
        self.put('/api/journals/%d' % self.t_rent['id'], {'date': '2026-01-10', 'lines': [
            {'account_id': self.acct('Rent and Lease'), 'debit': 450},
            {'account_id': self.chk, 'credit': 450}]})
        # and the same statement date can be reconciled again
        rec = self.start(self.chk, '2026-01-31', 800)
        self.assertEqual(len(rec['lines']), 4)

    def test_undo_finished_while_later_one_in_progress(self):
        """Undoing the last finished reconciliation while a newer one is open must leave a consistent open one."""
        done = self.std_jan_done()
        rec2 = self.start(self.chk, '2026-02-28', 785)
        rec2 = self.toggle(rec2, self.ids(rec2))
        self.assertEqual(rec2['difference'], 0)
        self.delete('/api/reconciliations/%d' % done['id'])
        r = self.rec(rec2['id'])
        self.assertEqual((r['beginning_balance'], r['cleared_balance'], r['difference'], len(r['lines'])),
                         (0, -65.0, 850.0, 5))
        self.finish(r, expect=400)
        r = self.toggle(r, self.ids(r))
        done2 = self.finish(r)
        self.assertEqual((done2['beginning_balance'], done2['cleared_balance']), (0, 785.0))

    def test_list_beginning_balance_matches_detail_for_open(self):
        """BUG: the list shows the beginning balance frozen when the open reconciliation was started; the
        detail view computes it live. After the prior reconciliation is undone they disagree."""
        done = self.std_jan_done()
        rec2 = self.start(self.chk, '2026-02-28', 785)
        self.delete('/api/reconciliations/%d' % done['id'])
        detail = self.rec(rec2['id'])
        listed = [r for r in self.get('/api/reconciliations', account_id=self.chk) if r['id'] == rec2['id']][0]
        self.assertEqual(detail['beginning_balance'], 0)
        self.assertEqual(listed['beginning_balance'], detail['beginning_balance'])


# ------------------------------------------------------------------ edits of cleared / reconciled

class ClearedNotFinished(RecCase):
    def test_delete_cleared_transaction(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', 775)
        rec = self.toggle(rec, self.ids(rec))
        self.assertEqual(rec['difference'], 0)
        self.delete('/api/transactions/%d' % self.t_util['id'])     # allowed: not yet reconciled
        r = self.rec(rec['id'])
        self.assertEqual((len(r['lines']), r['cleared_balance'], r['difference'], r['cleared_payment_count']),
                         (3, 850.0, -75.0, 1))
        self.finish(r, expect=400)
        r = self.put('/api/reconciliations/%d' % rec['id'], {'ending_balance': 850})
        done = self.finish(r)
        self.assertEqual((done['register_balance'], done['uncleared_total']), (850.0, 0))

    def test_edit_amount_of_cleared_transaction_keeps_state_consistent(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', 775)
        rec = self.toggle(rec, self.ids(rec))
        self.put('/api/journals/%d' % self.t_util['id'], {'date': '2026-01-30', 'lines': [
            {'account_id': self.acct('Utilities'), 'debit': 80}, {'account_id': self.chk, 'credit': 80}]})
        r = self.rec(rec['id'])
        self.assertEqual(len(r['lines']), 4)
        # whichever way the edited line ends up, the totals must agree with the check marks
        cleared = round(sum(l['amount'] for l in r['lines'] if l['cleared']), 2)
        self.assertEqual(r['cleared_balance'], cleared)
        self.assertEqual(r['difference'], round(775 - cleared, 2))
        self.assertNotEqual(r['difference'], 0)
        self.finish(r, expect=400)

    def test_edit_cleared_transaction_moved_after_statement_date(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', 775)
        rec = self.toggle(rec, self.ids(rec))
        self.put('/api/journals/%d' % self.t_util['id'], {'date': '2026-02-02', 'lines': [
            {'account_id': self.acct('Utilities'), 'debit': 75}, {'account_id': self.chk, 'credit': 75}]})
        r = self.rec(rec['id'])
        self.assertEqual((len(r['lines']), r['cleared_balance'], r['difference']), (3, 850.0, -75.0))
        r = self.put('/api/reconciliations/%d' % rec['id'], {'ending_balance': 850})
        done = self.finish(r)
        self.assertTrue(all(l['date'] <= '2026-01-31' for l in done['lines']))
        self.assertEqual((done['register_balance'], done['uncleared_total']), (850.0, 0))

    def test_edit_cleared_transaction_moved_to_another_account(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', 775)
        rec = self.toggle(rec, self.ids(rec))
        self.put('/api/journals/%d' % self.t_util['id'], {'date': '2026-01-30', 'lines': [
            {'account_id': self.acct('Utilities'), 'debit': 75}, {'account_id': self.sav, 'credit': 75}]})
        r = self.rec(rec['id'])
        self.assertEqual((len(r['lines']), r['cleared_balance']), (3, 850.0))
        sav = self.start(self.sav, '2026-01-31', -75)
        self.assertEqual([l['cleared'] for l in sav['lines']], [False])     # no check mark leaked across accounts

    def test_memo_only_edit_keeps_check_mark(self):
        """GAP: in QuickBooks a checked-off (C) transaction stays checked when you edit something that does not
        change the bank side (memo, category). Here any edit silently drops the check mark."""
        self.std()
        rec = self.start(self.chk, '2026-01-31', 775)
        rec = self.toggle(rec, self.ids(rec))
        self.assertEqual(rec['difference'], 0)
        self.put('/api/journals/%d' % self.t_util['id'], {'date': '2026-01-30', 'memo': 'electric bill', 'lines': [
            {'account_id': self.acct('Utilities'), 'debit': 75}, {'account_id': self.chk, 'credit': 75}]})
        r = self.rec(rec['id'])
        self.assertEqual((r['difference'], sum(1 for l in r['lines'] if l['cleared'])), (0, 4))


class Locking(RecCase):
    def test_journal_edit_and_delete_blocked(self):
        done = self.std_jan_done()
        body = {'date': '2026-01-10', 'lines': [{'account_id': self.acct('Rent and Lease'), 'debit': 999},
                                                 {'account_id': self.chk, 'credit': 999}]}
        self.put('/api/journals/%d' % self.t_rent['id'], body, expect=409)
        self.delete('/api/transactions/%d' % self.t_rent['id'], expect=409)
        t = self.get('/api/transactions/%d' % self.t_rent['id'])
        self.assertEqual((t['total'], t['reconciled']), (400.0, True))
        self.assertEqual(self.rec(done['id'])['cleared_balance'], 850.0)
        # un-reconciled transactions (outstanding check, next month) stay editable
        self.put('/api/journals/%d' % self.t_util['id'], {'date': '2026-01-30', 'lines': [
            {'account_id': self.acct('Utilities'), 'debit': 70}, {'account_id': self.chk, 'credit': 70}]})
        self.delete('/api/transactions/%d' % self.t_feb['id'])

    def test_customer_payment_edit_and_delete_blocked(self):
        c = self.post('/api/customers', {'name': 'Acme'})
        inv = self.post('/api/invoices', {'customer_id': c['id'], 'date': '2026-01-02', 'lines': [
            {'description': 'Work', 'qty': 1, 'rate': 500, 'account_id': self.acct('Sales')}]})
        pay = self.post('/api/payments', {'customer_id': c['id'], 'date': '2026-01-08', 'amount': 500,
                                          'deposit_account_id': self.chk,
                                          'applications': [{'invoice_id': inv['id'], 'amount': 500}]})
        rec = self.start(self.chk, '2026-01-31', 500)
        self.assertEqual([(l['type'], l['deposit'], l['name']) for l in rec['lines']], [('Payment', 500.0, 'Acme')])
        self.clear_all_and_finish(rec)
        self.put('/api/payments/%d' % pay['id'], {'customer_id': c['id'], 'date': '2026-01-08', 'amount': 400,
                                                  'deposit_account_id': self.chk,
                                                  'applications': [{'invoice_id': inv['id'], 'amount': 400}]},
                 expect=409)
        self.put('/api/payments/%d' % pay['id'], {'customer_id': c['id'], 'date': '2026-01-08', 'amount': 500,
                                                  'deposit_account_id': self.sav, 'applications': []}, expect=409)
        self.delete('/api/payments/%d' % pay['id'], expect=409)
        self.delete('/api/transactions/%d' % pay['txn_id'], expect=409)
        # nothing was half-applied by the rejected edits
        p = self.get('/api/payments/%d' % pay['id'])
        self.assertEqual((p['amount'], p['applied'], p['deposit_account_id']), (500.0, 500.0, self.chk))
        self.assertEqual(self.get('/api/invoices/%d' % inv['id'])['status'], 'Paid')
        self.delete('/api/invoices/%d' % inv['id'], expect=409)
        # the invoice itself never touched the bank; it stays editable
        self.put('/api/invoices/%d' % inv['id'], {'customer_id': c['id'], 'date': '2026-01-02', 'memo': 'x', 'lines': [
            {'description': 'Work', 'qty': 1, 'rate': 500, 'account_id': self.acct('Service Income')}]})

    def test_transfer_locked_by_either_side(self):
        tr = self.journal('2026-01-05', ('Savings', 300, 0), ('Checking', 0, 300))
        self.journal('2026-01-01', ('Checking', 1000, 0), ('Sales', 0, 1000))
        rec = self.start(self.chk, '2026-01-31', 700)
        self.clear_all_and_finish(rec)
        self.delete('/api/transactions/%d' % tr['id'], expect=409)
        # the Savings side is still un-reconciled and can be reconciled on its own
        sav = self.start(self.sav, '2026-01-31', 300)
        self.assertEqual([(l['deposit'], l['cleared']) for l in sav['lines']], [(300.0, False)])
        sav_done = self.clear_all_and_finish(sav)
        # undoing Checking alone does not unlock the transfer (Savings still has it reconciled)
        self.delete('/api/reconciliations/%d' % rec['id'])
        self.delete('/api/transactions/%d' % tr['id'], expect=409)
        self.delete('/api/reconciliations/%d' % sav_done['id'])
        self.delete('/api/transactions/%d' % tr['id'])


class Feed(RecCase):
    def imp(self, account_id, csv):
        self.post('/api/feed/import', {'account_id': account_id, 'filename': 's.csv', 'content': csv})
        return {r['description']: r for r in self.get('/api/feed', account_id=account_id)['rows']}

    def setup_feed(self):
        rows = self.imp(self.chk, BANK_CSV)
        self.t_od = self.post('/api/feed/%d/categorize' % rows['OFFICE DEPOT #123']['id'],
                              {'account_id': self.acct('Office Supplies and Software')})
        self.t_dep = self.post('/api/feed/%d/categorize' % rows['CUSTOMER DEPOSIT']['id'],
                               {'account_id': self.acct('Sales')})
        self.t_febc = self.post('/api/feed/%d/categorize' % rows['FEB CHARGE']['id'],
                                {'account_id': self.acct('Utilities')})
        # a manually entered cheque later matched to the SHELL statement line
        self.t_shell = self.journal('2026-01-09', ('Travel', 45, 0), ('Checking', 0, 45))
        self.post('/api/feed/%d/match' % rows['SHELL OIL 5551']['id'],
                  {'line_id': self.bank_line(self.t_shell, self.chk)})
        # a manual entry that never came from a statement
        self.t_manual = self.journal('2026-01-25', ('Utilities', 60, 0), ('Checking', 0, 60))
        return rows

    def test_statement_lines_start_checked(self):
        self.setup_feed()
        rec = self.start(self.chk, '2026-01-31', '1804.75')
        got = {l['amount']: (l['cleared'], l['from_statement']) for l in rec['lines']}
        self.assertEqual(got, {-150.25: (True, True), 2000.0: (True, True), -45.0: (True, True),
                               -60.0: (False, False)})
        self.assertEqual((rec['cleared_balance'], rec['difference'], rec['cleared_deposits'], rec['cleared_payments']),
                         (1804.75, 0, 2000.0, 195.25))
        # Feb statement line is outside the statement period: not checked, not listed
        self.assertEqual(self.get('/api/transactions/%d' % self.t_febc['id'])['lines'][0]['recon_status'], '')
        # a pre-checked line can be un-checked like any other
        r = self.toggle(rec, self.ids(rec, lambda l: l['amount'] == -45.0), cleared=False)
        self.assertEqual(r['difference'], -45.0)
        # cancel removes the automatic check marks too
        self.delete('/api/reconciliations/%d' % rec['id'])
        for t in (self.t_od, self.t_dep, self.t_shell):
            self.assertEqual({l['recon_status'] for l in self.get('/api/transactions/%d' % t['id'])['lines']}, {''})

    def test_prechecked_lines_do_not_leak_into_other_account(self):
        self.setup_feed()
        sav = self.start(self.sav, '2026-01-31', 0)
        self.assertEqual(sav['lines'], [])
        self.assertEqual(self.get('/api/transactions/%d' % self.t_od['id'])['lines'][0]['recon_status'], '')

    def test_feed_undo_blocked_after_reconcile(self):
        rows = self.setup_feed()
        rec = self.start(self.chk, '2026-01-31', '1804.75')
        done = self.finish(rec)
        self.post('/api/feed/%d/undo' % rows['OFFICE DEPOT #123']['id'], expect=409)    # created from the feed
        self.post('/api/feed/%d/undo' % rows['SHELL OIL 5551']['id'], expect=409)       # matched
        self.post('/api/feed/batch', {'action': 'undo', 'ids': [rows['CUSTOMER DEPOSIT']['id']]}, expect=409)
        posted = self.get('/api/feed', account_id=self.chk, status='posted')
        self.assertEqual(posted['counts'], {'review': 0, 'posted': 4, 'excluded': 0})
        self.assertTrue(all(r['reconciled'] for r in posted['rows'] if r['date'] <= '2026-01-31'))
        self.assertEqual(self.rec(done['id'])['cleared_balance'], 1804.75)
        self.assertEqual(self.get('/api/feed', account_id=self.chk)['book_balance'], 1724.75)
        # un-reconciled statement lines can still be undone
        self.post('/api/feed/%d/undo' % rows['FEB CHARGE']['id'])
        # after undoing the reconciliation the feed undo works again
        self.delete('/api/reconciliations/%d' % done['id'])
        self.post('/api/feed/%d/undo' % rows['OFFICE DEPOT #123']['id'])
        self.post('/api/feed/%d/undo' % rows['SHELL OIL 5551']['id'])

    def test_feed_undo_of_cleared_not_finished(self):
        rows = self.setup_feed()
        rec = self.start(self.chk, '2026-01-31', '1804.75')
        self.post('/api/feed/%d/undo' % rows['OFFICE DEPOT #123']['id'])    # deletes the created transaction
        self.post('/api/feed/%d/undo' % rows['SHELL OIL 5551']['id'])       # un-matches; the cheque stays
        r = self.rec(rec['id'])
        got = {l['amount']: (l['cleared'], l['from_statement']) for l in r['lines']}
        self.assertEqual(got, {2000.0: (True, True), -45.0: (True, False), -60.0: (False, False)})
        self.assertEqual((r['cleared_balance'], r['difference']), (1955.0, -150.25))

    def test_statement_line_accepted_during_reconciliation_is_checked(self):
        """GAP: QuickBooks checks off bank-feed transactions automatically. Here only the ones already accepted
        when the reconciliation was started are checked; a statement line accepted afterwards (inside the
        statement period) shows up un-checked."""
        rows = self.imp(self.chk, BANK_CSV)
        rec = self.start(self.chk, '2026-01-31', '-150.25')
        self.post('/api/feed/%d/categorize' % rows['OFFICE DEPOT #123']['id'],
                  {'account_id': self.acct('Office Supplies and Software')})
        r = self.rec(rec['id'])
        self.assertEqual([(l['from_statement'], l['cleared']) for l in r['lines']], [(True, True)])

    def test_credit_card_statement_lines(self):
        rows = self.imp(self.cc, CC_CSV)
        self.post('/api/feed/%d/categorize' % rows['DELTA AIR LINES']['id'], {'account_id': self.acct('Travel')})
        self.post('/api/feed/%d/categorize' % rows['STAPLES']['id'],
                  {'account_id': self.acct('Office Supplies and Software')})
        rec = self.start(self.cc, '2026-01-31', 500)
        self.assertEqual((rec['difference'], rec['cleared_payments'], rec['cleared_payment_count']), (0, 500.0, 2))
        self.finish(rec)


# ------------------------------------------------------------------ finished report

class Report(RecCase):
    def test_report_right_after_finishing(self):
        done = self.std_jan_done()
        self.assertEqual((done['beginning_balance'], done['ending_balance'], done['cleared_balance'],
                          done['difference'], done['cleared_deposits'], done['cleared_payments'],
                          done['cleared_deposit_count'], done['cleared_payment_count']),
                         (0, 850.0, 850.0, 0, 1250.0, 400.0, 2, 1))
        self.assertEqual(len(done['lines']), 3)
        self.assertTrue(all(l['cleared'] for l in done['lines']))
        self.assertEqual([(l['date'], l['payment'], l['cleared']) for l in done['uncleared']],
                         [('2026-01-30', 75.0, False)])
        self.assertEqual((done['uncleared_total'], done['register_balance']), (-75.0, 775.0))
        self.assertEqual(done['register_balance'], done['ending_balance'] + done['uncleared_total'])
        self.assertIsNotNone(done['completed_at'])

    def test_report_after_next_reconciliation_clears_outstanding_item(self):
        jan = self.std_jan_done()
        feb = self.start(self.chk, '2026-02-28', 785)
        feb = self.clear_all_and_finish(feb)
        # the January report still shows the cheque as outstanding as of Jan 31
        r = self.rec(jan['id'])
        self.assertEqual(([l['payment'] for l in r['uncleared']], r['uncleared_total'], r['register_balance'],
                          r['cleared_balance'], len(r['lines'])), ([75.0], -75.0, 775.0, 850.0, 3))
        self.assertEqual((feb['beginning_balance'], feb['uncleared'], feb['uncleared_total'], feb['register_balance']),
                         (850.0, [], 0, 785.0))
        # undoing February puts January's report back exactly as it was
        self.delete('/api/reconciliations/%d' % feb['id'])
        self.assertEqual(self.rec(jan['id']), jan)

    def test_report_with_later_open_reconciliation(self):
        jan = self.std_jan_done()
        feb = self.start(self.chk, '2026-02-28', 785)
        self.toggle(feb, self.ids(feb))
        r = self.rec(jan['id'])
        keys = ('beginning_balance', 'cleared_balance', 'difference', 'lines', 'uncleared_total', 'register_balance')
        self.assertEqual([r[k] for k in keys], [jan[k] for k in keys])
        self.assertEqual([l['line_id'] for l in r['uncleared']], [l['line_id'] for l in jan['uncleared']])

    def test_uncleared_item_keeps_uncleared_flag_in_old_report(self):
        """BUG: an item listed under "uncleared" in January's finished report flips to cleared:true as soon as
        it is checked off (or reconciled) in February's reconciliation, although it is still listed as
        uncleared as of the January statement."""
        jan = self.std_jan_done()
        self.assertEqual([l['cleared'] for l in jan['uncleared']], [False])
        feb = self.start(self.chk, '2026-02-28', 785)
        self.toggle(feb, self.ids(feb))
        self.assertEqual([l['cleared'] for l in self.rec(jan['id'])['uncleared']], [False])

    def test_report_ignores_later_period_activity(self):
        jan = self.std_jan_done()
        before = self.rec(jan['id'])
        self.journal('2026-02-10', ('Checking', 999, 0), ('Sales', 0, 999))
        self.journal('2026-03-01', ('Rent and Lease', 5, 0), ('Checking', 0, 5))
        self.assertEqual(self.rec(jan['id']), before)

    @unittest.expectedFailure
    def test_report_is_a_snapshot(self):
        """GAP: the QuickBooks reconciliation report is frozen at the time of reconciling ("changes made after
        this date aren't included"). Here register_balance / uncleared are recomputed live, so a transaction
        entered or deleted later with a date inside the old period silently changes an old report."""
        jan = self.std_jan_done()
        self.journal('2026-01-28', ('Checking', 40, 0), ('Sales', 0, 40))     # back-dated, entered later
        self.delete('/api/transactions/%d' % self.t_util['id'])               # outstanding cheque voided later
        r = self.rec(jan['id'])
        self.assertEqual((r['register_balance'], r['uncleared_total'], len(r['uncleared'])), (775.0, -75.0, 1))

    def test_live_report_still_ties_out_after_backdated_activity(self):
        jan = self.std_jan_done()
        self.journal('2026-01-28', ('Checking', 40, 0), ('Sales', 0, 40))
        r = self.rec(jan['id'])
        self.assertEqual((r['cleared_balance'], r['difference']), (850.0, 0))
        self.assertEqual(r['register_balance'], round(r['ending_balance'] + r['uncleared_total'], 2))


# ------------------------------------------------------------------ credit card

class CreditCard(RecCase):
    def charges(self):
        self.c1 = self.journal('2026-01-05', ('Travel', 300, 0), ('Credit Card', 0, 300))
        self.c2 = self.journal('2026-01-12', ('Meals and Entertainment', '45.50', 0), ('Credit Card', 0, '45.50'))
        self.p1 = self.journal('2026-01-20', ('Credit Card', 100, 0), ('Checking', 0, 100))
        self.c3 = self.journal('2026-01-29', ('Travel', 20, 0), ('Credit Card', 0, 20))          # not on statement
        self.p2 = self.journal('2026-02-10', ('Credit Card', '245.50', 0), ('Checking', 0, '245.50'))

    def test_math_and_columns(self):
        self.charges()
        rec = self.start(self.cc, '2026-01-31', '245.50')
        self.assertEqual((rec['account_type'], rec['beginning_balance'], rec['difference']), ('Credit Card', 0, 245.5))
        self.assertEqual([(l['deposit'], l['payment'], l['amount']) for l in rec['lines']],
                         [(None, 300.0, 300.0), (None, 45.5, 45.5), (100.0, None, -100.0), (None, 20.0, 20.0)])
        rec = self.toggle(rec, self.ids(rec, lambda l: l['payment'] == 300.0))
        self.assertEqual((rec['cleared_balance'], rec['difference']), (300.0, -54.5))
        rec = self.toggle(rec, self.ids(rec, lambda l: l['deposit'] == 100.0))
        self.assertEqual((rec['cleared_balance'], rec['difference']), (200.0, 45.5))
        rec = self.toggle(rec, self.ids(rec, lambda l: l['payment'] == 45.5))
        self.assertEqual((rec['cleared_balance'], rec['difference'], rec['cleared_payments'], rec['cleared_deposits'],
                          rec['cleared_payment_count'], rec['cleared_deposit_count']), (245.5, 0, 345.5, 100.0, 2, 1))
        done = self.finish(rec)
        self.assertEqual(([l['amount'] for l in done['uncleared']], done['uncleared_total'], done['register_balance']),
                         ([20.0], 20.0, 265.5))
        self.assertEqual(self.recon_acct(self.cc)['balance'], 20.0)
        self.delete('/api/transactions/%d' % self.p1['id'], expect=409)

    def test_chain_and_credit_balance(self):
        self.charges()
        rec = self.start(self.cc, '2026-01-31', '245.50')
        rec = self.finish(self.toggle(rec, self.ids(rec, lambda l: l['date'] <= '2026-01-20')))
        # February: 245.50 + 20 - 245.50 = 20, then an over-payment makes the card carry a credit balance
        self.journal('2026-02-20', ('Credit Card', 100, 0), ('Checking', 0, 100))
        feb = self.start(self.cc, '2026-02-28', '-80.00')
        self.assertEqual((feb['beginning_balance'], feb['ending_balance'], feb['difference'], len(feb['lines'])),
                         (245.5, -80.0, -325.5, 3))
        feb = self.toggle(feb, self.ids(feb))
        self.assertEqual((feb['cleared_balance'], feb['difference']), (-80.0, 0))
        feb = self.finish(feb)
        self.assertEqual((feb['register_balance'], feb['uncleared_total']), (-80.0, 0))
        acct = self.recon_acct(self.cc)
        self.assertEqual((acct['balance'], acct['beginning_balance'], acct['last_ending_balance']),
                         (-80.0, -80.0, -80.0))
        # March starts from the credit balance
        self.journal('2026-03-03', ('Travel', 30, 0), ('Credit Card', 0, 30))
        mar = self.start(self.cc, '2026-03-31', -50)
        self.assertEqual((mar['beginning_balance'], mar['difference']), (-80.0, 30.0))
        mar = self.clear_all_and_finish(mar)
        self.assertEqual((mar['beginning_balance'], mar['cleared_balance']), (-80.0, -50.0))
        # undo order applies to cards as well
        self.delete('/api/reconciliations/%d' % rec['id'], expect=409)
        self.delete('/api/reconciliations/%d' % mar['id'])
        self.assertEqual(self.recon_acct(self.cc)['beginning_balance'], -80.0)

    def test_wrong_sign_statement_balance_cannot_finish(self):
        self.charges()
        rec = self.start(self.cc, '2026-01-31', '-245.50')
        rec = self.toggle(rec, self.ids(rec, lambda l: l['date'] <= '2026-01-20'))
        self.assertEqual(rec['difference'], -491.0)
        self.finish(rec, expect=400)

    def test_card_payment_reconciles_independently_on_both_accounts(self):
        self.charges()
        self.journal('2026-01-01', ('Checking', 1000, 0), ('Sales', 0, 1000))
        card = self.start(self.cc, '2026-01-31', '245.50')
        bank = self.start(self.chk, '2026-01-31', 900)
        card = self.toggle(card, self.ids(card, lambda l: l['date'] <= '2026-01-20'))
        bank = self.toggle(bank, self.ids(bank))
        self.assertEqual((card['difference'], bank['difference']), (0, 0))
        self.finish(bank)
        self.assertEqual(self.rec(card['id'])['difference'], 0)       # finishing the bank did not disturb the card
        self.finish(card)


# ------------------------------------------------------------------ opening balances

class OpeningBalance(RecCase):
    def test_bank_opening_balance(self):
        a = self.post('/api/accounts', {'name': 'Money Market', 'type': 'Bank', 'opening_balance': '5000',
                                        'opening_date': '2025-12-31'})
        self.journal('2026-01-10', ('Bank Charges and Fees', 12, 0), ('Money Market', 0, 12))
        rec = self.start(a['id'], '2026-01-31', 4988)
        # the opening balance is an ordinary item to check off in the first reconciliation
        self.assertEqual((rec['beginning_balance'], [l['amount'] for l in rec['lines']]), (0, [5000.0, -12.0]))
        done = self.clear_all_and_finish(rec)
        self.assertEqual((done['cleared_balance'], done['register_balance']), (4988.0, 4988.0))
        self.assertEqual(self.recon_acct(a['id'])['beginning_balance'], 4988.0)

    def test_card_opening_balance(self):
        a = self.post('/api/accounts', {'name': 'Amex', 'type': 'Credit Card', 'opening_balance': '750',
                                        'opening_date': '2025-12-31'})
        self.assertEqual(a['balance'], 750.0)
        rec = self.start(a['id'], '2026-01-31', 750)
        self.assertEqual([(l['payment'], l['amount']) for l in rec['lines']], [(750.0, 750.0)])
        done = self.clear_all_and_finish(rec)
        self.assertEqual(done['cleared_balance'], 750.0)

    @unittest.expectedFailure
    def test_first_beginning_balance_is_opening_balance(self):
        """GAP: in QuickBooks Online the opening balance entered when the account is created is the beginning
        balance of the first reconciliation (the entry is already marked R). Here the first beginning balance
        is 0.00 and the opening balance has to be checked off by hand."""
        a = self.post('/api/accounts', {'name': 'Money Market', 'type': 'Bank', 'opening_balance': '5000',
                                        'opening_date': '2025-12-31'})
        rec = self.start(a['id'], '2026-01-31', 5000)
        self.assertEqual((rec['beginning_balance'], rec['difference']), (5000.0, 0))


# ------------------------------------------------------------------ account changes

class AccountChanges(RecCase):
    def test_type_cannot_leave_bank_or_card(self):
        self.std_jan_done()
        self.put('/api/accounts/%d' % self.chk, {'type': 'Expense'}, expect=400)
        self.delete('/api/accounts/%d' % self.chk, expect=409)

    def test_bank_to_card_type_change_keeps_finished_reconciliation_intact(self):
        """BUG: an account with finished reconciliations can be switched Bank <-> Credit Card. All reconciled
        amounts flip sign but the stored statement balances do not, so the finished reconciliation now shows a
        non-zero difference and the next beginning balance is the negative of the last ending balance."""
        done = self.std_jan_done()
        status, _ = self.app.handle('PUT', '/api/accounts/%d' % self.chk, {}, {'type': 'Credit Card'})
        r = self.rec(done['id'])
        if status == 200:
            self.assertEqual((r['cleared_balance'], r['difference']), (r['ending_balance'], 0))
            self.assertEqual(self.recon_acct(self.chk)['beginning_balance'], r['ending_balance'])
        else:
            self.assertIn(status, (400, 409))
            self.assertEqual((r['cleared_balance'], r['difference']), (850.0, 0))

    def test_inactive_account_with_open_reconciliation(self):
        self.std()
        rec = self.start(self.chk, '2026-01-31', 850)
        status, _ = self.app.handle('PUT', '/api/accounts/%d' % self.chk, {}, {'active': False})
        if status == 200:
            # still reachable and cancellable
            self.assertEqual(self.rec(rec['id'])['status'], 'open')
            self.delete('/api/reconciliations/%d' % rec['id'])


# ------------------------------------------------------------------ malformed input

class Malformed(RecCase):
    def setUp(self):
        super().setUp()
        self.std()

    def no_500(self, method, path, body=None, **q):
        status, data = self.app.handle(method, path, {k: str(v) for k, v in q.items()}, body)
        self.assertNotEqual(status, 500, '%s %s %r -> %s' % (method, path, body, data))
        return status, data

    def test_start_bad_values(self):
        for body in ({}, {'account_id': 'abc'}, {'account_id': None}, {'account_id': [1]}, {'account_id': {}},
                     {'account_id': self.chk}, {'account_id': self.chk, 'statement_date': 20260131},
                     {'account_id': self.chk, 'statement_date': ['2026-01-31'], 'ending_balance': 1},
                     {'account_id': self.chk, 'statement_date': '2026-01-31', 'ending_balance': [1]},
                     {'account_id': self.chk, 'statement_date': '2026-01-31', 'ending_balance': {'a': 1}},
                     {'account_id': self.chk, 'statement_date': '2026-01-31', 'ending_balance': True},
                     {'account_id': self.chk, 'statement_date': '2026-01-31', 'ending_balance': 'NaN'},
                     {'account_id': self.chk, 'statement_date': '2026-01-31', 'ending_balance': 'Infinity'},
                     {'account_id': self.chk, 'statement_date': '2026-01-31', 'ending_balance': '1.2.3'}):
            status, data = self.no_500('POST', '/api/reconciliations', body)
            self.assertEqual(status, 400, '%r -> %s' % (body, data))
        self.assertEqual(self.get('/api/reconciliations'), [])

    def test_start_huge_ending_balance(self):
        """BUG: an absurdly large ending balance (1e30) crashes to_cents with decimal.InvalidOperation: HTTP 500."""
        status, _ = self.no_500('POST', '/api/reconciliations', {
            'account_id': self.chk, 'statement_date': '2026-01-31', 'ending_balance': '1e30'})
        self.assertEqual(status, 400)

    def test_update_huge_ending_balance(self):
        """BUG: a 25-digit ending balance overflows SQLite's 64-bit integer: HTTP 500."""
        rec = self.start(self.chk, '2026-01-31', 850)
        status, _ = self.no_500('PUT', '/api/reconciliations/%d' % rec['id'], {'ending_balance': '9' * 25})
        self.assertEqual(status, 400)
        self.assertEqual(self.rec(rec['id'])['ending_balance'], 850.0)

    def test_toggle_line_ids_not_a_list(self):
        """BUG: line_ids given as a number crashes with HTTP 500."""
        rec = self.start(self.chk, '2026-01-31', 850)
        status, _ = self.no_500('POST', '/api/reconciliations/%d/toggle' % rec['id'], {'line_ids': 5, 'cleared': True})
        self.assertEqual(status, 400)

    def test_toggle_line_ids_as_string(self):
        """BUG: line_ids given as a string is iterated character by character, so "12" checks off line 1 and
        line 2 instead of being rejected."""
        rec = self.start(self.chk, '2026-01-31', 850)
        first = self.ids(rec)[0]
        self.assertLess(first, 10)
        status, _ = self.no_500('POST', '/api/reconciliations/%d/toggle' % rec['id'],
                                {'line_ids': '%d' % first, 'cleared': True})
        self.assertEqual(status, 400)
        self.assertEqual(self.rec(rec['id'])['cleared_balance'], 0)

    def test_toggle_cleared_string_false(self):
        """BUG: cleared:"false" (a string) is treated as true and checks the line off."""
        rec = self.start(self.chk, '2026-01-31', 850)
        status, _ = self.no_500('POST', '/api/reconciliations/%d/toggle' % rec['id'],
                                {'line_ids': self.ids(rec)[:1], 'cleared': 'false'})
        if status == 200:
            self.assertEqual(self.rec(rec['id'])['cleared_balance'], 0)
        else:
            self.assertEqual(status, 400)

    def test_toggle_bad_elements(self):
        rec = self.start(self.chk, '2026-01-31', 850)
        for ids in ([None], [{}], [[1]], ['abc'], [-1], [0]):
            status, data = self.no_500('POST', '/api/reconciliations/%d/toggle' % rec['id'],
                                       {'line_ids': ids, 'cleared': True})
            self.assertEqual(status, 400, '%r -> %s' % (ids, data))
        status, _ = self.no_500('POST', '/api/reconciliations/%d/toggle' % rec['id'], {})
        self.assertEqual(status, 400)
        self.assertEqual(self.rec(rec['id'])['cleared_balance'], 0)

    def test_list_bad_query(self):
        status, _ = self.no_500('GET', '/api/reconciliations', account_id='abc')
        self.assertEqual(status, 400)
        status, data = self.no_500('GET', '/api/reconciliations', account_id=987654)
        self.assertEqual((status, data), (200, []))

    def test_huge_path_id(self):
        """BUG: an id larger than a 64-bit integer in the URL returns HTTP 500 instead of 404."""
        for method, path in (('GET', '/api/reconciliations/%s'), ('DELETE', '/api/reconciliations/%s'),
                             ('POST', '/api/reconciliations/%s/finish')):
            status, _ = self.no_500(method, path % ('9' * 30), {})
            self.assertEqual(status, 404)

    def test_put_bad_values(self):
        rec = self.start(self.chk, '2026-01-31', 850)
        for body in ({'statement_date': 12345}, {'statement_date': ['x']}, {'ending_balance': [1]},
                     {'ending_balance': 'x'}, {'ending_balance': True}):
            status, data = self.no_500('PUT', '/api/reconciliations/%d' % rec['id'], body)
            self.assertEqual(status, 400, '%r -> %s' % (body, data))
        r = self.rec(rec['id'])
        self.assertEqual((r['statement_date'], r['ending_balance']), ('2026-01-31', 850.0))


if __name__ == '__main__':
    unittest.main()
