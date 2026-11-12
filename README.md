# Simplified QuickBooks

Small double-entry accounting app. Python 3.9+, standard library only, SQLite data file.

## Run

    python3 run.py            # http://127.0.0.1:8000, data in books.db
    python3 run.py --port 8080 --db client_a.db

## Features

1. Import bank and credit card statements (CSV, QFX/OFX/QBO, best-effort PDF with `pip3 install pdfplumber`); categorize, split, match, exclude, undo; bank rules; duplicate detection.
2. Chart of accounts: add, edit, delete (unused) or make inactive; sub-accounts; opening balances.
3. Customers and vendors: add, edit, delete (unused) or make inactive.
4. Journal entries.
5. Reports with custom period: Profit and Loss, Balance Sheet, Statement of Cash Flows, Transaction Detail by Account, Trial Balance, A/R Aging. Columns by month/quarter/year; compare to previous period or previous year.
6. Customer invoices and received payments (partial, multi-invoice, unapplied credit).
7. Bank and credit card reconciliation with report and undo.
8. Click any report amount for the transactions that add up to it.

Sample statements: `sample_data/`.

## Tests

    python3 -m unittest discover -s tests

## Known gaps

- Accrual basis only. No sales tax, bills/A-P workflow, payroll, inventory, multi-currency.
- No Total column when period columns are combined with a comparison.
- Reconciliation report is live, not a frozen snapshot.
- First reconciliation starts at 0.00; tick the opening balance entry.
- PDF statement import is heuristic. Check signs in the preview.
