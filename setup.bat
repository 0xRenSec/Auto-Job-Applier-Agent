@echo off
rem Guided setup for Windows. Double-click this file.
rem It installs what the bot needs, opens your two files, checks them, signs you
rem in to LinkedIn once, and runs one test that submits nothing.
setlocal EnableExtensions
cd /d "%~dp0" || exit /b 1
echo.
echo === Auto Job Applier Agent - setup ===
echo Keep this window open.
echo.

if not exist "src\main.py" (
  echo This folder is missing the bot's files. Extract the whole ZIP first
  echo ^(right-click the ZIP, Extract All^), then run setup.bat from the extracted folder.
  pause
  exit /b 1
)

rem --- Find a working Python 3.10+. The "py" launcher first; the Microsoft
rem --- Store shortcut (which only opens the Store) is skipped, not run.
set "PYEXE="
set "PYARG="
py -3 -c "import sys, venv; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PYEXE=py"
  set "PYARG=-3"
)
if not defined PYEXE (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYEXE (
      echo %%P | findstr /i /c:"\Microsoft\WindowsApps\" >nul || (
        "%%P" -c "import sys, venv; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PYEXE=%%P"
      )
    )
  )
)
if not defined PYEXE (
  echo Python 3.10 or newer was not found ^(or only the Microsoft Store shortcut is installed^).
  echo Follow the Windows Python steps in GETTING_STARTED.md, then run setup.bat again.
  pause
  exit /b 1
)

rem --- Private Python environment in .venv (only a broken one is ever removed) ---
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys, pip; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo The existing .venv folder is broken - recreating it.
    rmdir /s /q ".venv"
  )
)
if not exist ".venv\Scripts\python.exe" (
  if exist ".venv" (
    echo A .venv folder exists but is not a Python environment. Delete or rename it, then run setup.bat again.
    pause
    exit /b 1
  )
  echo Creating a private Python environment in .venv ...
  "%PYEXE%" %PYARG% -m venv ".venv"
  if errorlevel 1 (
    echo Could not create the environment. Run setup.bat again; if it keeps failing, reinstall Python.
    pause
    exit /b 1
  )
)

echo Installing the required packages ^(this can take a few minutes^) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Package installation failed. Check your internet connection and run setup.bat again.
  pause
  exit /b 1
)
echo Installing the browser the bot drives ...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 (
  echo Browser installation failed. Run setup.bat again.
  pause
  exit /b 1
)

if not exist "data" mkdir "data"
if not exist "config.yaml" copy "config.example.yaml" "config.yaml" >nul
if not exist "data\profile.md" copy "profile.example.md" "data\profile.md" >nul

rem Used by the project's own tests to stop here.
if defined AJ_SETUP_INSTALL_ONLY exit /b 0

start "" notepad.exe "config.yaml"
start "" notepad.exe "data\profile.md"
echo.
echo Two files opened in Notepad:
echo   1. config.yaml      - replace the examples in the three START HERE parts, then save.
echo   2. data\profile.md  - replace the example person with your own background, then save.
echo   3. Copy your CV ^(a PDF^) into the data folder and name it resume.pdf.
echo Keep dry_run: true and headless: false for this test.
echo.

:check
echo When your files are saved, press any key to check them ^(Ctrl+C to stop^).
pause >nul
echo.
".venv\Scripts\python.exe" -m src.main --check
if errorlevel 1 (
  echo.
  echo Fix what is listed above, save the file, then press a key to check again.
  goto check
)
".venv\Scripts\python.exe" -c "import sys; from src.config import load_config; s = load_config()['safety']; sys.exit(0 if s.get('dry_run', True) is True and s.get('headless', False) is False else 'For this test keep dry_run: true and headless: false in config.yaml, then save it.')"
if errorlevel 1 goto check

echo.
echo Sign in to LinkedIn in the browser window that opens ^(you have 10 minutes^).
".venv\Scripts\python.exe" -m src.main --login
if errorlevel 1 (
  echo Sign-in did not complete. Run setup.bat again to retry.
  pause
  exit /b 1
)

echo.
echo Test run: fills at most one form and submits NOTHING. Do not click Submit yourself.
".venv\Scripts\python.exe" -m src.main --max 1
echo.
echo Read the "Run finished" line above. "1 applications would-submit" means one form
echo was completed without submitting; check the picture ending in -dryrun.png in
echo data\screenshots. If it says 0, see "No completed form" in GETTING_STARTED.md.
echo From now on use run.bat ^(see README.md, "More commands"^).
pause
exit /b 0
