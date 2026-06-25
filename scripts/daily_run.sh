#!/bin/bash
# Daily LiJAB run: live submit + retry previously skipped jobs.
cd "$(dirname "$0")/.." || exit 1
if pgrep -f "src.main" >/dev/null; then
  echo "$(date) — bot already running, skipping" >> logs/launchd.log
  exit 0
fi
exec .venv/bin/python -m src.main --live --retry-skipped >> logs/launchd.log 2>&1
