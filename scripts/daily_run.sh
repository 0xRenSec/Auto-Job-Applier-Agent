#!/bin/bash
# Daily run for cron / launchd: SUBMITS FOR REAL (--live) and retries previously
# skipped jobs. It overrides dry_run in config.yaml on purpose — only schedule it
# once your dry runs look right. Windows: schedule `run.bat --live --retry-skipped`
# in Task Scheduler instead (this file needs Bash).
# (main.py also holds an OS-level single-instance lock, so an overlapping start
# exits cleanly even where pgrep is unavailable.)
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
PY=".venv/bin/python"
[ -x "$PY" ] || PY=".venv/Scripts/python.exe"   # Windows-style venv layout
if command -v pgrep >/dev/null 2>&1 && pgrep -f "src.main" >/dev/null; then
  echo "$(date) — bot already running, skipping" >> logs/launchd.log
  exit 0
fi
exec "$PY" -m src.main --live --retry-skipped >> logs/launchd.log 2>&1
