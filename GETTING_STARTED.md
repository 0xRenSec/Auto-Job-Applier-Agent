# Your first test run, click by click

You need the downloaded ZIP, your CV as a PDF, and a LinkedIn account.
Setup installs the bot's software, checks your files, guides you through
signing in, and tests one form. A test never clicks Submit, but it does type
your details and upload your CV into LinkedIn's form. LinkedIn can restrict
accounts that use automation.

## Windows

1. Right-click the downloaded ZIP and choose **Extract All**.
2. Open the extracted folder, the one containing `setup.bat`.
3. In File Explorer's **View** menu turn on **File name extensions**
   (Windows 11: **View → Show → File name extensions**), so you can see that
   your CV really ends in `.pdf`.
4. On [Python's Windows page](https://www.python.org/downloads/windows/)
   download **Windows installer (64-bit)** under the newest **Python 3.13**
   release.
5. Open the installer, tick **Add python.exe to PATH**, click **Install Now**.
6. Double-click `setup.bat` in the project folder.

Keep the setup window open and continue at **Your files** below when Notepad
opens.

## Mac

1. Double-click the downloaded ZIP to extract it.
2. On [Python's Mac page](https://www.python.org/downloads/macos/) download
   **macOS installer** under the newest **Python 3.13** release, open it and
   accept the defaults.
3. In Finder open **Applications → Python 3.13** and double-click
   **Install Certificates.command**. Wait for "update complete".
4. Open **Terminal**: press **Command + Space**, type `Terminal`, press Enter.
5. Type `bash ` (with the space) and do **not** press Enter yet.
6. Drag `setup.sh` from the extracted folder into the Terminal window.
7. Press Enter.

Keep Terminal open. When TextEdit opens the two files: choose **Format → Make
Plain Text** if offered, and turn off **Edit → Substitutions → Smart Quotes**
and **Smart Dashes**. Keep the file names exactly as they are (no added
`.txt` or `.rtf`).

## Linux

Install Python 3.10 or newer (`sudo apt install python3 python3-venv` on
Ubuntu), open a terminal in the extracted folder and run `bash setup.sh`.

## Your files

Setup opens `config.yaml` (your settings) and `data/profile.md` (your
background). Rules for `config.yaml`: keep the spaces at the start of lines,
keep text in `"quotes"`, leave numbers and `true` / `false` without quotes.
Lines starting with `#` are explanations.

1. **START HERE - 1**: replace the example name, email, phone, city, country
   and LinkedIn address with yours.
2. **START HERE - 2**: put in the job titles to search for and the place
   (`"London"`, `"Sweden"`, `"European Union"`, `"Worldwide"`). Set
   `required_title_keywords: []` if you don't want the extra title filter.
3. **START HERE - 3**: replace **every** example answer with your own facts:
   years of experience per skill, yes/no answers, notice period, languages.
   Fill in `work_authorization.countries`, e.g. `["Canada"]`, with the
   countries where you may work without a visa. Use `regions` only if you may
   work in every country of that region (EU citizens: `["European Union"]`).
4. Save `config.yaml` (**Ctrl + S** on Windows, **Command + S** on Mac).
5. In `data/profile.md`, replace all the example text with your own
   background under the same headings. Only write things that are true.
6. Save it.
7. Copy your CV into the project's `data` folder and name the copy
   `resume.pdf`. (Renaming a Word file does not make it a PDF; export it as
   PDF from Word first.)

Keep `dry_run: true` and `headless: false`. Leave the optional settings
alone; the first run needs no AI account or API key.

## Check, sign in, test

1. In the setup window press a key (Windows) or Enter (Mac). Setup checks
   your files. If it lists a problem: fix it, save, press again.
2. A browser window opens on LinkedIn. Sign in as you always do, including
   any code LinkedIn sends to your phone. You have ten minutes. The bot
   never sees or stores your password; the session is kept in the
   `browser_profile` folder, which you should never share.
3. Setup starts the test by itself: it searches, opens one job, fills the
   form and stops before Submit. Do not click Submit yourself.

## Review the test

When the window shows `Run finished`:

- `1 applications would-submit (dry run)` means one form was completed
  without submitting. Open the newest picture ending in `-dryrun.png` in
  `data/screenshots` and check the answers on the final step.
- The newest file in `logs` (open it with Notepad or TextEdit) lists every
  answer given and every job skipped, with the reason.

Wrong answer? Change it in `config.yaml`, save, and run the **Test again**
command from [More commands](README.md#more-commands).

## No completed form

`0 applications would-submit` means no form was completed: no matching job
was found, a required question had no answer, or a page failed. Open the
newest file in `logs`, read the reason, fix the answer or the search filter in
`config.yaml`, save, and run the **Test again** command. The test can visit
many jobs before it completes one form; stop it any time with **Ctrl + C**.
If the log says the daily limit was reached, try again tomorrow (tests count).

## If setup stops

- **Python was not found** — do the Python steps for your computer above,
  then start setup again.
- **The Microsoft Store opens** — install Python from the link above (not
  the Store), then start setup again.
- **Package or browser download failed** — check the internet connection,
  start setup again.
- **Mac certificate error** — run **Install Certificates.command** from
  **Applications → Python 3.13**, then start setup again.
- **No editor opened** — open `config.yaml` and `data/profile.md` yourself
  with Notepad (Windows) or TextEdit (Mac).
- **"Your CV was not found"** — the file must be inside the `data` folder and
  be named exactly `resume.pdf` (not `resume.pdf.pdf`).
- **A YAML error with a line number** — a formatting slip in `config.yaml`:
  compare that line and the one above it with `config.example.yaml`.
- **Sign-in did not complete** — start setup again and sign in inside the
  browser window it opens, not your usual browser.
- **"Another run is already in progress"** — an earlier run is still open;
  close it (Ctrl + C) and start again.
- **"A .venv folder exists but is not a Python environment"** — extract the
  ZIP into a fresh folder, copy your `config.yaml`, `data` and
  `browser_profile` (and `.env` if you made one) into it, and run setup there.

Anything else: [open an issue](https://github.com/0xRenSec/Auto-Job-Applier-Agent/issues)
with the error text, with your personal details removed.
