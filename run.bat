@echo off
rem Runs the bot. Examples:
rem   run.bat --check          check your config and files (sends nothing)
rem   run.bat --login          sign in to LinkedIn once (session is remembered)
rem   run.bat                  dry run: fills forms, screenshots, submits NOTHING
rem   run.bat --live --max 3   submit for real, at most 3 applications
rem   run.bat --live           submit for real, using the caps in config.yaml
cd /d "%~dp0" || exit /b 1
if not exist .venv\Scripts\python.exe (
  echo Please run setup.bat first.
  exit /b 1
)
.venv\Scripts\python.exe -m src.main %*
