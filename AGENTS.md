# Setting up Simplified QuickBooks (instructions for AI assistants)

You are helping someone install and run Simplified QuickBooks on their own computer. There is nothing to
build or compile: the repository *is* the app. It is a local web app written in Python using only the standard
library, with a SQLite data file. Follow these steps in order and report the result to the person.

## 1. Check Python

Python 3.9 or newer is the only requirement.

- macOS / Linux: `python3 --version`
- Windows: `py -3 --version` (or `python --version`)

If it is missing or older than 3.9, ask the person before installing anything:
- macOS: `xcode-select --install` (installs Apple's command line tools, which include Python 3), or the installer from https://www.python.org/downloads/
- Windows: `winget install -e --id Python.Python.3.12`, or the installer from python.org with "Add python.exe to PATH" ticked
- Linux: the package manager, e.g. `sudo apt install python3`

Do **not** `pip install` anything. No packages are needed. (`pdfplumber` is optional and not required for PDF import.)

## 2. Install

Either use the one-line installer (it downloads the app to `~/SimplifiedQuickBooks`, adds a Desktop launcher and starts it):

- macOS / Linux:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/andrewliang13/simplified_quickbooks/main/install.sh | bash
  ```
- Windows (PowerShell):
  ```powershell
  irm https://raw.githubusercontent.com/andrewliang13/simplified_quickbooks/main/install.ps1 | iex
  ```

Or clone it yourself:

```bash
git clone https://github.com/andrewliang13/simplified_quickbooks.git ~/SimplifiedQuickBooks
```

## 3. Verify (recommended)

From the install folder:

```bash
python3 -m unittest discover -s tests
```

Expect the last line to be `OK` after about a minute (650+ tests). On Windows use `py -3` instead of `python3`.
If anything fails, show the person the failing test names; do not edit the code to make tests pass.

## 4. Start

```bash
cd ~/SimplifiedQuickBooks
python3 run.py
```

The browser opens at `http://localhost:8000` (the next free port is used if 8000 is taken; the address is printed).
The terminal window must stay open while the app is in use; Ctrl+C stops it. For the person's daily use:
- macOS: double-click `Simplified QuickBooks.command` on the Desktop (made by the installer) or `Start Simplified QuickBooks.command` in the folder. If macOS blocks it the first time: right-click > Open > Open, or run `xattr -d com.apple.quarantine "Start Simplified QuickBooks.command"`.
- Windows: double-click the `Simplified QuickBooks` Desktop shortcut or `start_windows.bat`.

Useful options: `--port 8080`, `--no-browser`, `--db path/to/client.db` (a separate company file per client).

## 5. Where the data is (do not delete)

- `books.db` in the install folder: the person's books. Never delete, overwrite or commit it.
- `backups/`: automatic daily copies (last 30). Company settings > Backups can download, back up and restore.

## Updating to a newer version

Run the installer again, or `git pull` in the install folder. Both replace only program files; `books.db` and
`backups/` are not in the repository and are left alone. Make a backup first (Company settings > Backups > Back up now).

## Important

- The app has no logins. It listens only on `127.0.0.1` (this computer). Do not expose it to a network or the
  internet (no `--host 0.0.0.0`, port forwarding or tunnels) unless the person understands that anyone who can
  reach it can read and change their books.
- It is one person's bookkeeping on one computer. To move to another computer, copy `books.db` (or restore a backup).
- Payroll income tax withholding is a percentage the person sets per employee (no IRS/state tables); remind them
  to check it. Exchange rates are typed in.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `python3: command not found` / Microsoft Store opens | Install Python (step 1); on Windows use `py -3` |
| `Address already in use` | Another copy is running; use its address, or `python3 run.py --port 8080` |
| Browser does not open | Open the address printed in the terminal |
| PDF statement "password protected" or "no readable text" | Download the statement from the bank as CSV or QFX/OFX instead |
| Want to start over with empty books | Stop the app, rename `books.db` (keep it), start again |
