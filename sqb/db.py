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

CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  sku TEXT NOT NULL DEFAULT '',
  type TEXT NOT NULL DEFAULT 'service',   -- service | non_inventory | inventory
  description TEXT NOT NULL DEFAULT '',
  sales_price INTEGER NOT NULL DEFAULT 0, -- cents
  purchase_cost INTEGER NOT NULL DEFAULT 0,
  income_account_id INTEGER,
  expense_account_id INTEGER,             -- expense, or Cost of Goods Sold for inventory
  asset_account_id INTEGER,               -- inventory only
  taxable INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS inv_moves(     -- inventory quantity and value changes (home currency cents)
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL,
  txn_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  qty REAL NOT NULL,
  value INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_moves_item ON inv_moves(item_id);

CREATE TABLE IF NOT EXISTS tax_rates(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  rate TEXT NOT NULL,                     -- percent, e.g. '8.25'
  agency TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS bills(
  id INTEGER PRIMARY KEY,
  txn_id INTEGER NOT NULL,
  vendor_id INTEGER NOT NULL,
  number TEXT NOT NULL DEFAULT '',
  date TEXT NOT NULL,
  due_date TEXT NOT NULL,
  terms TEXT NOT NULL DEFAULT '',
  memo TEXT NOT NULL DEFAULT '',
  total INTEGER NOT NULL DEFAULT 0,       -- document currency cents
  total_home INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT '',
  exchange_rate TEXT NOT NULL DEFAULT '1',
  tax_rate_id INTEGER,
  tax INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bill_lines(
  id INTEGER PRIMARY KEY,
  bill_id INTEGER NOT NULL,
  seq INTEGER NOT NULL DEFAULT 0,
  description TEXT NOT NULL DEFAULT '',
  qty REAL NOT NULL DEFAULT 1,
  rate REAL NOT NULL DEFAULT 0,
  amount INTEGER NOT NULL DEFAULT 0,
  account_id INTEGER NOT NULL,
  item_id INTEGER,
  taxable INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bill_payments(
  id INTEGER PRIMARY KEY,
  txn_id INTEGER NOT NULL,
  vendor_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  amount INTEGER NOT NULL,
  pay_account_id INTEGER NOT NULL,
  method TEXT NOT NULL DEFAULT '',
  ref TEXT NOT NULL DEFAULT '',
  memo TEXT NOT NULL DEFAULT '',
  exchange_rate TEXT NOT NULL DEFAULT '1',
  ctrl_home INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bill_payment_apps(
  id INTEGER PRIMARY KEY,
  payment_id INTEGER NOT NULL,
  bill_id INTEGER NOT NULL,
  amount INTEGER NOT NULL,
  home INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS employees(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '',
  pay_type TEXT NOT NULL DEFAULT 'hourly',  -- hourly | salary
  pay_rate INTEGER NOT NULL DEFAULT 0,      -- cents per hour, or per paycheck for salary
  fed_pct TEXT NOT NULL DEFAULT '0',        -- federal income tax withholding, % of gross
  state_pct TEXT NOT NULL DEFAULT '0',
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS paychecks(
  id INTEGER PRIMARY KEY,
  txn_id INTEGER NOT NULL,
  employee_id INTEGER NOT NULL,
  pay_date TEXT NOT NULL,
  period_start TEXT NOT NULL DEFAULT '',
  period_end TEXT NOT NULL DEFAULT '',
  hours REAL NOT NULL DEFAULT 0,
  gross INTEGER NOT NULL DEFAULT 0,
  fed_wh INTEGER NOT NULL DEFAULT 0,
  state_wh INTEGER NOT NULL DEFAULT 0,
  ss_ee INTEGER NOT NULL DEFAULT 0,
  med_ee INTEGER NOT NULL DEFAULT 0,
  other_ded INTEGER NOT NULL DEFAULT 0,
  ss_er INTEGER NOT NULL DEFAULT 0,
  med_er INTEGER NOT NULL DEFAULT 0,
  futa INTEGER NOT NULL DEFAULT 0,
  suta INTEGER NOT NULL DEFAULT 0,
  net INTEGER NOT NULL DEFAULT 0,
  bank_account_id INTEGER NOT NULL,
  check_no TEXT NOT NULL DEFAULT '',
  memo TEXT NOT NULL DEFAULT ''
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
    ('1350', 'Inventory Asset', 'Other Current Asset', 'inv'),
    ('1500', 'Furniture and Equipment', 'Fixed Asset', None),
    ('1590', 'Accumulated Depreciation', 'Fixed Asset', None),
    ('2000', 'Accounts Payable', 'Accounts Payable', 'ap'),
    ('2100', 'Credit Card', 'Credit Card', None),
    ('2200', 'Payroll Liabilities', 'Other Current Liability', 'pl'),
    ('2300', 'Sales Tax Payable', 'Other Current Liability', 'tax'),
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
    ('6060', 'Payroll Expenses', 'Expense', 'wages'),
    ('6065', 'Payroll Tax Expense', 'Expense', 'ptx'),
    ('6070', 'Rent and Lease', 'Expense', None),
    ('6080', 'Repairs and Maintenance', 'Expense', None),
    ('6090', 'Taxes and Licenses', 'Expense', None),
    ('6100', 'Travel', 'Expense', None),
    ('6110', 'Utilities', 'Expense', None),
    ('6120', 'Depreciation Expense', 'Expense', None),
    ('6900', 'Uncategorized Expense', 'Expense', None),
    ('7000', 'Interest Income', 'Other Income', None),
    ('8000', 'Interest Expense', 'Other Expense', None),
    ('8100', 'Exchange Gain or Loss', 'Other Expense', 'fx'),
]

# Accounts the app posts to automatically: (code, number, name, type). Added to older company files.
SYSTEM_ACCOUNTS = [('ap', '2000', 'Accounts Payable', 'Accounts Payable'),
                   ('inv', '1350', 'Inventory Asset', 'Other Current Asset'),
                   ('pl', '2200', 'Payroll Liabilities', 'Other Current Liability'),
                   ('tax', '2300', 'Sales Tax Payable', 'Other Current Liability'),
                   ('ptx', '6065', 'Payroll Tax Expense', 'Expense'),
                   ('wages', '6060', 'Payroll Expenses', 'Expense'),
                   ('fx', '8100', 'Exchange Gain or Loss', 'Other Expense')]

# Columns added after the first release: (table, column, declaration)
COLUMNS = [('feed', 'fitid', 'TEXT'), ('reconciliations', 'snapshot', 'TEXT'),
           ('names', 'currency', "TEXT NOT NULL DEFAULT ''"),
           ('invoices', 'currency', "TEXT NOT NULL DEFAULT ''"),
           ('invoices', 'exchange_rate', "TEXT NOT NULL DEFAULT '1'"),
           ('invoices', 'total_home', 'INTEGER'),
           ('invoices', 'tax_rate_id', 'INTEGER'), ('invoices', 'tax', 'INTEGER NOT NULL DEFAULT 0'),
           ('invoice_lines', 'item_id', 'INTEGER'), ('invoice_lines', 'taxable', 'INTEGER NOT NULL DEFAULT 0'),
           ('payments', 'exchange_rate', "TEXT NOT NULL DEFAULT '1'"), ('payments', 'ctrl_home', 'INTEGER'),
           ('payment_apps', 'home', 'INTEGER'),
           ('inv_moves', 'kind', "TEXT NOT NULL DEFAULT 'in'"), ('inv_moves', 'cogs_account_id', 'INTEGER'),
           ('inv_moves', 'asset_account_id', 'INTEGER'), ('lines', 'kind', "TEXT NOT NULL DEFAULT ''")]


def connect(path):
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init(path):
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        for table, col, decl in COLUMNS:
            if col not in [r[1] for r in conn.execute('PRAGMA table_info(%s)' % table)]:
                conn.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, col, decl))
        # Home-currency amounts for documents written before multi-currency existed.
        conn.execute('UPDATE invoices SET total_home = total WHERE total_home IS NULL')
        conn.execute('UPDATE payment_apps SET home = amount WHERE home IS NULL')
        conn.execute('UPDATE payments SET ctrl_home = amount WHERE ctrl_home IS NULL')
        if conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0] == 0:
            conn.executemany(
                'INSERT INTO accounts(number, name, type, system) VALUES (?,?,?,?)', DEFAULT_ACCOUNTS)
            conn.executemany('INSERT OR IGNORE INTO settings(key, value) VALUES (?,?)',
                             [('company_name', 'My Company'), ('fiscal_year_start_month', '1')])
        for code, number, name, atype in SYSTEM_ACCOUNTS:
            if conn.execute('SELECT 1 FROM accounts WHERE system=?', (code,)).fetchone():
                continue
            hit = conn.execute('SELECT id FROM accounts WHERE name=? COLLATE NOCASE AND type=? AND system IS NULL',
                               (name, atype)).fetchone()
            if hit:
                conn.execute('UPDATE accounts SET system=? WHERE id=?', (code, hit[0]))
            else:
                taken = conn.execute('SELECT 1 FROM accounts WHERE number=?', (number,)).fetchone()
                n = 2
                while conn.execute('SELECT 1 FROM accounts WHERE name=? COLLATE NOCASE',
                                   (name if n == 2 else '%s %d' % (name, n - 1),)).fetchone():
                    n += 1
                conn.execute('INSERT INTO accounts(number, name, type, system) VALUES (?,?,?,?)',
                             ('' if taken else number, name if n == 2 else '%s %d' % (name, n - 1), atype, code))
        if get_setting(conn, 'inventory_costing', '') != '2':
            _upgrade_inventory(conn)
            set_setting(conn, 'inventory_costing', '2')
        conn.commit()
    finally:
        conn.close()


def _upgrade_inventory(conn):
    """Earlier versions fixed the cost of each sale when it was saved. Convert those sales to moves whose
    cost is recalculated in date order, then recalculate every item."""
    from . import items
    for m in conn.execute("SELECT m.*, i.expense_account_id AS ex, i.asset_account_id AS ast FROM inv_moves m "
                          "JOIN items i ON i.id = m.item_id JOIN transactions t ON t.id = m.txn_id "
                          "WHERE t.type = 'Invoice' AND m.kind = 'in'").fetchall():
        conn.execute("UPDATE inv_moves SET kind='out', cogs_account_id=?, asset_account_id=? WHERE id=?",
                     (m['ex'], m['ast'], m['id']))
        conn.execute("DELETE FROM lines WHERE txn_id=? AND kind='' AND ((account_id=? AND debit>0) OR "
                     "(account_id=? AND credit>0)) AND memo IN (SELECT description FROM invoice_lines il JOIN "
                     "invoices v ON v.id = il.invoice_id WHERE v.txn_id=? AND il.item_id=?)",
                     (m['txn_id'], m['ex'], m['ast'], m['txn_id'], m['item_id']))
    for (iid,) in conn.execute('SELECT id FROM items').fetchall():
        items.recost(conn, iid)


DEFAULTS = {'home_currency': 'USD', 'ss_rate': '6.2', 'ss_wage_base': '184500', 'medicare_rate': '1.45',
            'addl_medicare_rate': '0.9', 'addl_medicare_threshold': '200000', 'futa_rate': '0.6',
            'futa_wage_base': '7000', 'suta_rate': '2.7', 'suta_wage_base': '7000'}


def get_setting(conn, key, default=None):
    if default is None:
        default = DEFAULTS.get(key)
    r = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return r[0] if r else default


def set_setting(conn, key, value):
    conn.execute('INSERT INTO settings(key, value) VALUES (?,?) '
                 'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))


def system_account(conn, code):
    r = conn.execute('SELECT id FROM accounts WHERE system=?', (code,)).fetchone()
    return r[0] if r else None
