"""Shared test harness: a fresh company file per test, API calls without HTTP."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqb.server import App  # noqa: E402


class ApiCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.remove(self.db_path)
        self.app = App(self.db_path)
        self._accts = None

    def tearDown(self):
        for ext in ('', '-wal', '-shm', '-journal'):
            if os.path.exists(self.db_path + ext):
                os.remove(self.db_path + ext)

    def call(self, method, path, body=None, expect=200, **query):
        status, data = self.app.handle(method, path, {k: str(v) for k, v in query.items()}, body)
        self.assertEqual(status, expect, '%s %s -> %s %s' % (method, path, status, data))
        return data

    def get(self, path, **q):
        return self.call('GET', path, **q)

    def post(self, path, body=None, expect=200):
        return self.call('POST', path, body or {}, expect)

    def put(self, path, body=None, expect=200):
        return self.call('PUT', path, body or {}, expect)

    def delete(self, path, expect=200):
        return self.call('DELETE', path, None, expect)

    def acct(self, name):
        """Account id by name."""
        for a in self.get('/api/accounts', include_inactive=1):
            if a['name'] == name:
                return a['id']
        raise KeyError(name)

    def journal(self, date, *lines, **kw):
        """lines: (account name, debit, credit[, name_id])"""
        body = {'date': date, 'lines': [
            {'account_id': self.acct(l[0]), 'debit': l[1], 'credit': l[2],
             'name_id': l[3] if len(l) > 3 else None} for l in lines]}
        body.update(kw)
        return self.post('/api/journals', body)

    def row(self, report, label):
        for r in report['rows']:
            if r['label'] == label and 'values' in r:
                return r
        raise KeyError('%s not in %s' % (label, [r['label'] for r in report['rows']]))

    def val(self, report, label, col=0):
        return self.row(report, label)['values'][col]

    def drill_total(self, report, label, col=0):
        d = self.row(report, label)['drill'][col]
        if 'items' in d:
            return round(sum(i['amount'] for i in d['items']), 2)
        return self.post('/api/reports/drill', d)['total']
