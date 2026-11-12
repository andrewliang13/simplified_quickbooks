"""SQLite schema, connection and default chart of accounts."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  parent_id INTEGER,
  description TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  system TEXT                      -- ar | ap | udf | obe | re (protected accounts)
);

CREATE TABLE IF NOT EXISTS names(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,              -- customer | vendor
  name TEXT NOT NULL,
  company TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS transactions(
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  type TEXT NOT NULL,
  ref TEXT NOT NULL DEFAULT '',
  memo TEXT NOT NULL DEFAULT '',
  name_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_txn_date ON transactions(date);

CREATE TABLE IF NOT EXISTS lines(
  id INTEGER PRIMARY KEY,
  txn_id INTEGER NOT NULL,
  seq INTEGER NOT NULL DEFAULT 0,
  account_id INTEGER NOT NULL,
  debit INTEGER NOT NULL DEFAULT 0,     -- cents
  credit INTEGER NOT NULL DEFAULT 0,    -- cents
  memo TEXT NOT NULL DEFAULT '',
  name_id INTEGER,
  recon_status TEXT NOT NULL DEFAULT '',  -- '' | C (cleared, in progress) | R (reconciled)
  reconciliation_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_lines_txn ON lines(txn_id);
CREATE INDEX IF NOT EXISTS ix_lines_acct ON lines(account_id);

CREATE TABLE IF NOT EXISTS imports(
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL,
  filename TEXT NOT NULL DEFAULT '',
  format TEXT NOT NULL DEFAULT '',
  count INTEGER NOT NULL DEFAULT 0,
  imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feed(
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL,
  import_id INTEGER,
  date TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  amount INTEGER NOT NULL,              -- cents; + money in / card payment, - money out / card charge
  key TEXT NOT NULL,                    -- duplicate-detection key (hash of date, amount, description)
  fitid TEXT,                           -- bank's own transaction id (OFX/QFX), when present
  status TEXT NOT NULL DEFAULT 'review',-- review | posted | excluded
  line_id INTEGER,                      -- ledger line this statement row is tied to
  created_txn INTEGER NOT NULL DEFAULT 0,
  UNIQUE(account_id, key)
);

CREATE TABLE IF NOT EXISTS rules(
  id INTEGER PRIMARY KEY,
  match_text TEXT NOT NULL,
  direction TEXT NOT NULL DEFAULT 'any',  -- any | in | out
  account_id INTEGER NOT NULL,
  name_id INTEGER
);

CREATE TABLE IF NOT EXISTS invoices(
  id INTEGER PRIMARY KEY,
  txn_id INTEGER NOT NULL,
  customer_id INTEGER NOT NULL,
  number TEXT NOT NULL,
  date TEXT NOT NULL,
  due_date TEXT NOT NULL,
  terms TEXT NOT NULL DEFAULT '',
  memo TEXT NOT NULL DEFAULT '',
  total INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invoice_lines(
  id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL,
  seq INTEGER NOT NULL DEFAULT 0,
  description TEXT NOT NULL DEFAULT '',
  qty REAL NOT NULL DEFAULT 1,
  rate REAL NOT NULL DEFAULT 0,
  amount INTEGER NOT NULL DEFAULT 0,
  account_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY,
  txn_id INTEGER NOT NULL,
  customer_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  amount INTEGER NOT NULL,
  deposit_account_id INTEGER NOT NULL,
  method TEXT NOT NULL DEFAULT '',
  ref TEXT NOT NULL DEFAULT '',
  memo TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS payment_apps(
  id INTEGER PRIMARY KEY,
  payment_id INTEGER NOT NULL,
  invoice_id INTEGER NOT NULL,
  amount INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reconciliations(
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL,
  statement_date TEXT NOT NULL,
  beginning_balance INTEGER NOT NULL DEFAULT 0,
  ending_balance INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open',   -- open | done
  snapshot TEXT,                         -- JSON of the report at the moment it was finished
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT
);
"""

# (number, name, type, system)
DEFAULT_ACCOUNTS = [
    ('1000', 'Checking', 'Bank', None),
    ('1010', 'Savings', 'Bank', None),
    ('1200', 'Accounts Receivable', 'Accounts Receivable', 'ar'),
    ('1250', 'Undeposited Funds', 'Other Current Asset', 'udf'),
    ('1300', 'Prepaid Expenses', 'Other Current Asset', None),
    ('1500', 'Furniture and Equipment', 'Fixed Asset', None),
    ('1590', 'Accumulated Depreciation', 'Fixed Asset', None),
    ('2000', 'Accounts Payable', 'Accounts Payable', 'ap'),
    ('2100', 'Credit Card', 'Credit Card', None),
    ('2200', 'Payroll Liabilities', 'Other Current Liability', None),
    ('2300', 'Sales Tax Payable', 'Other Current Liability', None),
    ('2500', 'Notes Payable', 'Long Term Liability', None),
    ('3000', 'Opening Balance Equity', 'Equity', 'obe'),
    ('3100', "Owner's Investment", 'Equity', None),
    ('3200', "Owner's Draw", 'Equity', None),
    ('3900', 'Retained Earnings', 'Equity', 're'),
    ('4000', 'Sales', 'Income', None),
    ('4100', 'Service Income', 'Income', None),
    ('4900', 'Uncategorized Income', 'Income', None),
    ('5000', 'Cost of Goods Sold', 'Cost of Goods Sold', None),
    ('6000', 'Advertising and Marketing', 'Expense', None),
    ('6010', 'Bank Charges and Fees', 'Expense', None),
    ('6020', 'Insurance', 'Expense', None),
    ('6030', 'Legal and Professional Services', 'Expense', None),
    ('6040', 'Meals and Entertainment', 'Expense', None),
    ('6050', 'Office Supplies and Software', 'Expense', None),
    ('6060', 'Payroll Expenses', 'Expense', None),
    ('6070', 'Rent and Lease', 'Expense', None),
    ('6080', 'Repairs and Maintenance', 'Expense', None),
    ('6090', 'Taxes and Licenses', 'Expense', None),
    ('6100', 'Travel', 'Expense', None),
    ('6110', 'Utilities', 'Expense', None),
    ('6120', 'Depreciation Expense', 'Expense', None),
    ('6900', 'Uncategorized Expense', 'Expense', None),
    ('7000', 'Interest Income', 'Other Income', None),
    ('8000', 'Interest Expense', 'Other Expense', None),
]


def connect(path):
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init(path):
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        if 'fitid' not in [r[1] for r in conn.execute('PRAGMA table_info(feed)')]:
            conn.execute('ALTER TABLE feed ADD COLUMN fitid TEXT')
        if 'snapshot' not in [r[1] for r in conn.execute('PRAGMA table_info(reconciliations)')]:
            conn.execute('ALTER TABLE reconciliations ADD COLUMN snapshot TEXT')
        if conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0] == 0:
            conn.executemany(
                'INSERT INTO accounts(number, name, type, system) VALUES (?,?,?,?)', DEFAULT_ACCOUNTS)
            conn.executemany('INSERT OR IGNORE INTO settings(key, value) VALUES (?,?)',
                             [('company_name', 'My Company'), ('fiscal_year_start_month', '1')])
        conn.commit()
    finally:
        conn.close()


def get_setting(conn, key, default=None):
    r = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return r[0] if r else default


def set_setting(conn, key, value):
    conn.execute('INSERT INTO settings(key, value) VALUES (?,?) '
                 'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))


def system_account(conn, code):
    r = conn.execute('SELECT id FROM accounts WHERE system=?', (code,)).fetchone()
    return r[0] if r else None
