# Simplified QuickBooks

A small bookkeeping app that covers the everyday parts of QuickBooks: bank and credit card statement import and categorization, chart of accounts, customers and vendors, journal entries, invoices and payments, reconciliation, and financial reports where every amount can be opened to show the transactions behind it.

It runs on your own computer. Your books stay in one file on your disk; nothing is sent anywhere.

## Start it

You need Python 3.9 or newer (macOS includes it; Windows: install from python.org). Nothing else is required.

- **Mac:** double-click `Start Simplified QuickBooks.command`. The first time, macOS may ask you to confirm: right-click the file, choose Open, then Open.
- **Windows:** double-click `start_windows.bat`.
- **Any system, from a terminal:**

  ```bash
  python3 run.py
  ```

Your browser opens the app. Keep the small terminal window open while you work; close it (or press Ctrl+C) to stop.

Options: `--port 8080`, `--no-browser`, and `--db path/to/client.db` to keep a separate company file per client.

## First steps

1. **Company settings** (bottom left): set the company name and the first month of your fiscal year.
2. **Chart of Accounts**: rename or add accounts. For each bank and credit card account, enter the opening balance from the last statement before you start. That balance counts as already reconciled.
3. **Customers / Vendors**: add the names you use.
4. **Banking**: click **Import statement**, pick the account and the file your bank exported (CSV or QFX/OFX/QBO work best). Check the preview, then import.
5. In **For review**, choose a category for each line and click **Add**, or **Match** it to an invoice or entry already in your books. Use **Split** for one charge spread over several categories, and **Rule** so the same payee is filled in next time.
6. **Reconcile** each account monthly against the statement.
7. **Reports**: pick a report and period. Click any amount to see the transactions that add up to it.

## What it does

| Area | Features |
| --- | --- |
| Banking | CSV, QFX/OFX/QBO, PDF (best effort) import; duplicate detection; categorize, split, match to existing entries, invoices or Undeposited Funds; exclude; undo; rules; suggestions from past choices |
| Chart of accounts | Add, edit, delete (if unused) or make inactive; sub-accounts; account numbers; opening balances |
| Customers and vendors | Add, edit, delete (if unused) or make inactive; open balances |
| Journal entries | Multi-line entries with names, auto-numbering, duplicate-number warning, edit and delete |
| Invoices and payments | Terms and due dates, printable invoice, partial payments, one payment across several invoices, unapplied credits, deposit to bank or Undeposited Funds |
| Reconciliation | Bank and credit card; statement lines start checked; finish only at 0.00 difference; reconciled entries are locked; report frozen at finish time; undo the latest |
| Reports | Profit and Loss, Balance Sheet, Statement of Cash Flows, Transaction Detail by Account, Trial Balance, A/R Aging. Any date range; columns by month, quarter or year; compare to previous period or previous year; CSV export and print |
| Breakdown | Every report amount, including totals, opens the list of transactions that adds up to it |

## Backups

- A copy of your books is saved automatically once a day when the app starts. The last 30 are kept in the `backups` folder next to the company file.
- **Company settings > Backups**: download a copy, back up now, or restore. A restore first saves your current books, so it can be undone.
- To move to another computer, copy the company file (`books.db` by default) or a downloaded backup.

## PDF statements

PDF import reads the statement text and guesses signs and dates, so always check the preview. It needs one extra package:

```bash
pip3 install pdfplumber
```

CSV or QFX/OFX downloads from your bank are more reliable.

## Limits

- Accrual basis only (no cash-basis toggle).
- No vendor bills / accounts payable workflow, sales tax, payroll, inventory, or multiple currencies. Record those with bank categorization or journal entries.
- Single user on one computer. The app listens only on this computer (`127.0.0.1`).

## For developers

Python standard library only; SQLite storage; plain HTML/JS front end in `static/`.

```bash
python3 -m unittest discover -s tests
```

`tests/test_core.py` covers the main workflows. The `tests/test_qa_*.py` files are the per-area acceptance suites (banking, ledger, invoices, reconciliation, reports).
