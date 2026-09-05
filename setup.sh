#!/usr/bin/env bash
# Guided setup for macOS / Linux (also Git Bash on Windows). Run:  bash setup.sh
# It installs what the bot needs, opens your two files, checks them, signs you
# in to LinkedIn once, and runs one test that submits nothing.
cd "$(dirname "$0")" || exit 1
echo
echo "=== Auto Job Applier Agent - setup ==="
echo "Keep this window open."
echo

if [ ! -f src/main.py ]; then
  echo "This folder is missing the bot's files. Extract the whole ZIP first, then run setup.sh from the extracted folder."
  exit 1
fi

# --- Find a working Python 3.10+ ---
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 \
     && "$c" -c 'import sys, venv; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PY="$c"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer was not found. Follow the Python steps in GETTING_STARTED.md, then run setup.sh again."
  exit 1
fi

# --- Private Python environment in .venv (only a broken one is ever removed) ---
venv_python() {
  if [ -x .venv/bin/python ]; then echo .venv/bin/python
  elif [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe
  fi
}
VPY="$(venv_python)"
if [ -n "$VPY" ] && ! "$VPY" -c 'import sys, pip; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  echo "The existing .venv folder is broken - recreating it."
  rm -rf .venv
  VPY=""
fi
if [ -z "$VPY" ]; then
  if [ -e .venv ]; then
    echo "A .venv folder exists but is not a Python environment. Delete or rename it, then run setup.sh again."
    exit 1
  fi
  echo "Creating a private Python environment in .venv ..."
  "$PY" -m venv .venv || { echo "Could not create the environment."; exit 1; }
  VPY="$(venv_python)"
fi
if [ -z "$VPY" ]; then
  echo "The Python environment did not start. Run setup.sh again."
  exit 1
fi

echo "Installing the required packages (this can take a few minutes) ..."
"$VPY" -m pip install --upgrade pip --quiet
"$VPY" -m pip install -r requirements.txt \
  || { echo "Package installation failed. Check your internet connection and run setup.sh again."; exit 1; }
echo "Installing the browser the bot drives ..."
"$VPY" -m playwright install chromium \
  || { echo "Browser installation failed. Run setup.sh again."; exit 1; }

mkdir -p data
[ -f config.yaml ] || cp config.example.yaml config.yaml
[ -f data/profile.md ] || cp profile.example.md data/profile.md
chmod +x run.sh 2>/dev/null || true

# Used by the project's own tests to stop here.
[ -n "${AJ_SETUP_INSTALL_ONLY:-}" ] && exit 0

if [ "$(uname -s)" = "Darwin" ]; then
  open -a TextEdit config.yaml data/profile.md 2>/dev/null || open -t config.yaml 2>/dev/null || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open config.yaml >/dev/null 2>&1 || true
fi
cat <<'TEXT'

Two files to fill in (opened in your text editor if possible):
  1. config.yaml      - replace the examples in the three START HERE parts, then save.
  2. data/profile.md  - replace the example person with your own background, then save.
  3. Copy your CV (a PDF) into the data folder and name it resume.pdf.
Keep dry_run: true and headless: false for this test.
Mac: in TextEdit choose Format > Make Plain Text and turn off Edit > Substitutions > Smart Quotes.
TEXT

while true; do
  echo
  echo "When your files are saved, press Enter to check them (Ctrl+C to stop)."
  IFS= read -r _ || exit 1
  echo
  if ! "$VPY" -m src.main --check; then
    echo
    echo "Fix what is listed above, save the file, then press Enter to check again."
    continue
  fi
  "$VPY" -c "import sys; from src.config import load_config; s = load_config()['safety']; sys.exit(0 if s.get('dry_run', True) is True and s.get('headless', False) is False else 'For this test keep dry_run: true and headless: false in config.yaml, then save it.')" || continue
  break
done

echo
echo "Sign in to LinkedIn in the browser window that opens (you have 10 minutes)."
"$VPY" -m src.main --login || { echo "Sign-in did not complete. Run setup.sh again to retry."; exit 1; }

echo
echo "Test run: fills at most one form and submits NOTHING. Do not click Submit yourself."
"$VPY" -m src.main --max 1
cat <<'TEXT'

Read the "Run finished" line above. "1 applications would-submit" means one form
was completed without submitting; check the picture ending in -dryrun.png in
data/screenshots. If it says 0, see "No completed form" in GETTING_STARTED.md.
From now on use ./run.sh (see README.md, "More commands").
TEXT
