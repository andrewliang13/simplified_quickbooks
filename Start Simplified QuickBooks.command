#!/bin/bash
# Double-click this file (macOS) to start Simplified QuickBooks in your browser.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Return to close."
  exit 1
fi
python3 run.py "$@"
