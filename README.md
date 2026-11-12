# Simplified QuickBooks

A small bookkeeping app that covers the everyday parts of QuickBooks: bank and credit card statement import and categorization, chart of accounts, customers and vendors, invoices and bills, products and inventory, sales tax, payroll, multiple currencies, journal entries, reconciliation, and financial reports (accrual or cash basis) where every amount can be opened to show the transactions behind it.

It runs on your own computer. Your books stay in one file on your disk; nothing is sent anywhere.

## Install

One line, then the app opens in your browser. It installs to `~/SimplifiedQuickBooks`, adds a Desktop shortcut, and updating later never touches your books.

- **Mac / Linux** (Terminal):

  ```bash
  curl -fsSL https://raw.githubusercontent.com/andrewliang13/simplified_quickbooks/main/install.sh | bash
  ```

- **Windows** (PowerShell):

  ```powershell
  irm https://raw.githubusercontent.com/andrewliang13/simplified_quickbooks/main/install.ps1 | iex
  ```

Setting it up with an AI assistant? Give it this repository link and ask it to follow [AGENTS.md](AGENTS.md).

## Start it

You need Python 3.9 or newer (macOS includes it; on Windows the installer offers to install it). Nothing else is required.

- **Mac:** double-click `Start Simplified QuickBooks.command`. The first time, macOS may ask you to confirm: right-click the file, choose Open, then Open.
- **Windows:** double-click `start_windows.bat`.
- **Any system, from a terminal:**

  ```bash
  python3 run.py
  ```

Your browser opens the app. Keep the small terminal window open while you work; close it (or press Ctrl+C) to stop.

Options: `--port 8080`, `--no-browser`, and `--db path/to/client.db` to keep a separate company file per client.

## First steps

1. **Company settings** (bottom left): set the company name, the first month of your fiscal year and, before entering anything, your home currency.
2. **Chart of Accounts**: rename or add accounts. For each bank and credit card account, enter the opening balance from the last statement before you start. That balance counts as already reconciled.
3. **Customers / Vendors**: add the names you use (and their currency if not your home currency). Add **Products & Services**, **Sales Tax** rates and **Payroll** employees if you use them.
4. **Banking**: click **Import statement**, pick the account and the file your bank exported (CSV, QFX/OFX/QBO or PDF). Check the preview, correct anything that looks wrong, then import.
5. In **For review**, choose a category for each line and click **Add**, or **Match** it to an invoice or entry already in your books. Use **Split** for one charge spread over several categories, and **Rule** so the same payee is filled in next time. In **Categorized**, **Edit** changes the category later.
6. **Reconcile** each account monthly against the statement.
7. **Reports**: pick a report and period. Click any amount to see the transactions that add up to it.

## What it does

| Area | Features |
| --- | --- |
| Banking | CSV, QFX/OFX/QBO and PDF statement import with an editable preview; duplicate detection; categorize, split, match to existing entries, invoices, bills or Undeposited Funds; exclude; undo; rules; suggestions from past choices; edit the category of a categorized transaction, even after reconciling |
| Invoices and payments | Products and services, sales tax, terms and due dates, printable invoice, partial payments, one payment across several invoices, unapplied credits, deposit to bank or Undeposited Funds |
| Bills and expenses | Vendor bills with categories or products, pay bills by check, bank transfer or credit card, partial payments, vendor credits, A/P aging |
| Products and services | Services, non-inventory and inventory items; default prices, accounts and tax; inventory quantity and value at average cost, cost of goods sold on each sale, quantity adjustments |
| Sales tax | Tax rates, taxable/non-taxable lines, Sales Tax Liability report, record tax payments |
| Payroll | Employees (hourly or salary), paychecks with Social Security, Medicare, FUTA and state unemployment calculated, withholding by percentage (editable), payroll liability payments, Payroll Summary |
| Multiple currencies | Customers and vendors in their own currency; invoices, bills and payments with exchange rates; realized exchange gain or loss |
| Chart of accounts | Add, edit, delete (if unused) or make inactive; sub-accounts; account numbers; opening balances |
| Customers and vendors | Add, edit, delete (if unused) or make inactive; open balances; currency |
| Journal entries | Multi-line entries with names, auto-numbering, duplicate-number warning, edit and delete |
| Reconciliation | Bank and credit card; statement lines start checked; finish only at 0.00 difference; reconciled entries are locked; report frozen at finish time; undo the latest |
| Reports | Profit and Loss, Balance Sheet, Statement of Cash Flows, Transaction Detail by Account, Trial Balance, A/R and A/P Aging, Sales Tax Liability, Inventory Valuation, Payroll Summary. Any date range; accrual or cash basis; columns by month, quarter or year; compare to previous period or previous year; CSV export and print |
| Breakdown | Every report amount, including totals, opens the list of transactions that adds up to it |

Works on desktop and tablet screens (on narrow screens the menu becomes a top bar and lists become cards).

## Backups

- A copy of your books is saved automatically once a day when the app starts. The last 30 are kept in the `backups` folder next to the company file.
- **Company settings > Backups**: download a copy, back up now, or restore. A restore first saves your current books, so it can be undone.
- To move to another computer, copy the company file (`books.db` by default) or a downloaded backup.

## PDF statements

PDF statements are read by the app itself; nothing extra to install (if `pdfplumber` happens to be installed it is used instead). The import preview shows every row, and you can fix a date, description or amount, move an amount between Spent and Received, or leave a row out before importing. Scanned (image-only) or password-protected PDFs cannot be read: download CSV or QFX/OFX from your bank instead.

## Payroll: what to check

Social Security, Medicare (plus the additional 0.9%), FUTA and state unemployment are calculated from the rates and wage bases under **Payroll > Liabilities > Tax rates**; check them each January. Federal and state income tax withholding is a percentage of gross per employee, because the app does not include IRS or state withholding tables: compare it with IRS Publication 15-T and your state's tables, and type the exact amount on the paycheck if needed. The app does not file payroll tax forms or W-2s.

## Limits

- Exchange rates are entered by you (no live rates); bank and card accounts are in the home currency; open foreign balances are not revalued at period end.
- Inventory uses average cost, recalculated in date order whenever a purchase, sale or adjustment changes.
- Password-protected PDFs (including ones that open without a password but restrict printing) cannot be read; use CSV or QFX/OFX for those.
- Single user on one computer. The app listens only on this computer (`127.0.0.1`).

## For developers

Python standard library only; SQLite storage; plain HTML/JS front end in `static/`.

```bash
python3 -m unittest discover -s tests
```

`tests/test_core.py` covers the main workflows. The `tests/test_qa_*.py` files are the per-area acceptance suites (banking, ledger, invoices, reconciliation, reports).

## License

MIT. See [LICENSE](LICENSE). This is not financial, tax or payroll advice; check figures with an accountant.
