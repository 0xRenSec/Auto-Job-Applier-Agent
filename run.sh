#!/usr/bin/env bash
# Runs the bot. Examples:
#   ./run.sh --check          check your config and files (sends nothing)
#   ./run.sh --login          sign in to LinkedIn once (session is remembered)
#   ./run.sh                  dry run: fills forms, screenshots, submits NOTHING
#   ./run.sh --live --max 3   submit for real, at most 3 applications
#   ./run.sh --live           submit for real, using the caps in config.yaml
cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python
[ -x "$PY" ] || PY=.venv/Scripts/python.exe
if [ ! -x "$PY" ]; then
  echo "Please run ./setup.sh first."
  exit 1
fi
exec "$PY" -m src.main "$@"
