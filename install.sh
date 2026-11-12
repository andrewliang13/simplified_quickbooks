#!/bin/bash
# Simplified QuickBooks installer for macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/andrewliang13/simplified_quickbooks/main/install.sh | bash
#
# Installs (or updates) the app in ~/SimplifiedQuickBooks, adds a desktop launcher on macOS and starts it.
# Updating never touches your books (books.db) or the backups folder.
# Options: --no-start (do not open the app), --test (run the test suite after installing).
# Environment: SQB_HOME (install folder), SQB_ZIP_URL (download location of the app's zip).
set -e

REPO_ZIP="${SQB_ZIP_URL:-https://codeload.github.com/andrewliang13/simplified_quickbooks/zip/refs/heads/main}"
REPO_ZIP_ALT="https://github.com/andrewliang13/simplified_quickbooks/archive/refs/heads/main.zip"
DEST="${SQB_HOME:-$HOME/SimplifiedQuickBooks}"
START=1
TEST=0
for arg in "$@"; do
  case "$arg" in
    --no-start) START=0 ;;
    --test) TEST=1 ;;
  esac
done

say() { printf '%s\n' "$*"; }

# 1. Python 3.9 or newer
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  say "Python 3.9 or newer is needed and was not found."
  if [ "$(uname)" = "Darwin" ]; then
    say "Install it with:  xcode-select --install   (or download it from https://www.python.org/downloads/)"
  else
    say "Install it with your package manager, for example:  sudo apt install python3"
  fi
  say "Then run this installer again."
  exit 1
fi
say "Using $($PY --version 2>&1)"

# 2. Download and unpack the app (code only; your data files are never in the download)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
say "Downloading Simplified QuickBooks..."
is_zip() { "$PY" -c 'import sys, zipfile; sys.exit(0 if zipfile.is_zipfile(sys.argv[1]) else 1)' "$1"; }
curl -fsSL "$REPO_ZIP" -o "$TMP/app.zip" || true
if ! is_zip "$TMP/app.zip"; then
  curl -fsSL "$REPO_ZIP_ALT" -o "$TMP/app.zip" || true
fi
if ! is_zip "$TMP/app.zip"; then
  say "Could not download the app. Check your internet connection and try again."
  exit 1
fi
"$PY" -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$TMP/app.zip" "$TMP/x"
SRC="$(find "$TMP/x" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [ ! -f "$SRC/run.py" ]; then
  say "The download did not contain the app (run.py missing). Check your internet connection and try again."
  exit 1
fi
mkdir -p "$DEST"
cp -R "$SRC"/. "$DEST"/
chmod +x "$DEST/Start Simplified QuickBooks.command" "$DEST/install.sh" 2>/dev/null || true
say "Installed in $DEST"

# 3. Desktop launcher (macOS)
if [ "$(uname)" = "Darwin" ] && [ -d "$HOME/Desktop" ]; then
  LAUNCHER="$HOME/Desktop/Simplified QuickBooks.command"
  cat > "$LAUNCHER" <<EOF
#!/bin/bash
cd "$DEST" && exec $PY run.py
EOF
  chmod +x "$LAUNCHER"
  xattr -d com.apple.quarantine "$LAUNCHER" "$DEST/Start Simplified QuickBooks.command" 2>/dev/null || true
  say "Added 'Simplified QuickBooks' to your Desktop. Double-click it to start the app."
fi

# 4. Optional self-test
if [ "$TEST" = "1" ]; then
  say "Running the test suite (about a minute)..."
  (cd "$DEST" && "$PY" -m unittest discover -s tests 2>&1 | tail -n 3)
fi

# 5. Start
if [ "$START" = "1" ]; then
  say "Starting the app. Keep this window open while you work; press Ctrl+C to stop."
  cd "$DEST"
  exec "$PY" run.py
else
  say "To start the app later:  cd \"$DEST\" && $PY run.py"
fi
