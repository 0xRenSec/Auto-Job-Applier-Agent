# Auto Job Applier Agent

A bot that applies to jobs on LinkedIn for you. You tell it who you are, which
job titles to look for and where; it searches LinkedIn, opens the **Easy
Apply** forms, fills them in from your details, attaches your CV, answers the
screening questions from what you wrote, adds a cover letter and keeps a
record so it never applies to the same job twice.

Your details live in two files you edit on your own computer: `config.yaml`
(who you are, what to search for, your standard answers) and
`data/profile.md` (your background, in your own words). The bot types those
details into application forms. Only if you enable an AI provider does it
send your profile text, together with the job posting, to that provider;
nothing else leaves your computer.
Your LinkedIn password is never stored anywhere: you sign in once in a normal
browser window.

No programming needed. If you can edit a text file, you can run this.

---

## ⚠️ Read this first

- **Automating LinkedIn violates LinkedIn's Terms of Service.** LinkedIn detects
  automation and can restrict or close your account. Use at your own risk. The
  bot behaves as human-like as it can (one saved session, random pauses, a daily
  limit, a visible browser window), which reduces the risk but cannot remove it.
- **The first runs are dry runs.** The bot fills every form and takes a
  screenshot but does **not** press the final Submit, so you can check its
  answers first. It does still type into LinkedIn's forms, so a dry run is for
  checking answers, not for staying invisible.
- **With the default settings it never guesses.** If a required question can't
  be answered truthfully from what you wrote, it skips that job and tells you
  why, so you can add the answer and try again.

---

## Quick start

Setup installs everything, opens your two files, checks them, signs you in to
LinkedIn once, and runs one test that submits nothing.

**Windows**

1. Right-click the downloaded ZIP → **Extract All**, and open the extracted folder.
2. Install Python: [Windows steps](GETTING_STARTED.md#windows).
3. Double-click `setup.bat` and follow the window.

**Mac**

1. Double-click the downloaded ZIP to extract it.
2. Install Python: [Mac steps](GETTING_STARTED.md#mac).
3. Open **Terminal** (Command + Space, type `Terminal`), type `bash ` with the
   space, drag `setup.sh` from the extracted folder into the window, press
   Enter, and follow the window.

**In the setup window** you replace the examples in the three **START HERE**
parts of `config.yaml`, write your background in `data/profile.md`, copy your
CV to `data/resume.pdf`, and press a key. Setup checks the files, opens a
browser for you to sign in to LinkedIn (the password is never stored), and
runs the test. A finished test ends with `1 applications would-submit (dry
run)`; the picture ending in `-dryrun.png` in `data/screenshots` shows the
form.

A test still types your details into LinkedIn's form; it just never clicks
Submit. Every click, and what to do when something stops:
[GETTING_STARTED.md](GETTING_STARTED.md).

---

## More commands

Open a command window in the project folder first. Windows: open the folder in
File Explorer, click the address bar, type `cmd`, press Enter. Mac: open
Terminal, type `cd ` with the space, drag the project folder into the window,
press Enter.

| What to do | Windows | Mac / Linux |
|---|---|---|
| Check your files | `run.bat --check` | `bash run.sh --check` |
| Sign in again | `run.bat --login` | `bash run.sh --login` |
| Test one form (submits nothing) | `run.bat --max 1` | `bash run.sh --max 1` |
| Test again after fixing answers | `run.bat --max 1 --retry-skipped --retry-dry-run` | `bash run.sh --max 1 --retry-skipped --retry-dry-run` |
| Submit up to three real applications | `run.bat --live --max 3` | `bash run.sh --live --max 3` |
| Everyday use (limits from `config.yaml`) | `run.bat --live` | `bash run.sh --live` |
| Also submit the jobs you already tested | `run.bat --live --retry-dry-run` | `bash run.sh --live --retry-dry-run` |
| Retry jobs that failed half-way | `run.bat --recover` | `bash run.sh --recover` |
| Every option | `run.bat --help` | `bash run.sh --help` |

Without `--live`, a run submits nothing as long as `dry_run: true` stays in
`config.yaml`. The retry options let previously skipped or tested jobs be
attempted again if the search finds them; they don't limit the run to those
jobs. Tests count towards the daily limit. Stop a run with **Ctrl + C**. The
newest file in `logs` lists every answer given and every job skipped, with
the reason.

---

## Where things are

| Path | What |
|---|---|
| `config.yaml` | Who you are, what to search for, your answers (personal, never uploaded) |
| `data/resume.pdf` | Your CV |
| `data/profile.md` | Your background for cover letters and AI answers |
| `data/screenshots/` | A picture of the final step of each completed form |
| `data/cover_letters/` | The cover letters it wrote |
| `data/applied.db` | The record of every job: applied, skipped (and why), failed |
| `logs/` | A log file per run |
| `browser_profile/` | Your saved LinkedIn session — keep private |

---

## Changing what it searches for

Everything is in the `search:` section of `config.yaml`:

```yaml
search:
  keywords:                      # one LinkedIn search per line
    - "Project Manager"
    - "Product Owner"
  locations:                     # LinkedIn place names, searched in order
    - "Sweden"
    - "European Union"
  workplace_types: [remote]      # remote | hybrid | on_site
  date_posted: past_week         # past_24h | past_week | past_month | any
  experience_levels: [mid_senior]   # internship | entry | associate | mid_senior | director | executive
  required_title_keywords: ["project", "product"]   # the title must contain one of these
  blocklist_keywords: ["clearance", "internship"]   # skip if title/description contains one
  blocklist_title_keywords: ["director"]            # skip if the TITLE contains this word
```

Change the role by changing `keywords` and `required_title_keywords`; change
the place by changing `locations`. Run `--check` afterwards to see what the
bot understood.

---

## Optional extras

Explained in `config.yaml`; apart from the template cover letter they are off
by default:

- **AI help** (`llm:`) — any provider: OpenAI, Claude, a local model (Ollama),
  or the Codex CLI with a ChatGPT login. Enables tailored cover letters
  (`cover_letter.mode: llm`), AI answers to screening questions the lists miss
  (`answers.llm_fallback`), a CV re-ordered per job (`resume.mode: tailored`),
  and remote-work screening (`jd_screen`). The AI is only ever allowed to use
  facts from `data/profile.md`; when it can't answer truthfully it says so and
  the job is skipped. Put your API key in a file called `.env`:
  `OPENAI_API_KEY=sk-...`
- **Remote-work screening** (`jd_screen:`) — skip postings that say "must be
  based in Texas", "US residents only" or "3 office days a week" when you
  live somewhere else. Set `home_country` and `allowed_regions`.
- **Company websites** (`external_apply:`) — when a job has no Easy Apply
  button, try the company's own application site (Greenhouse, Lever, Ashby,
  Teamtailor, Recruitee, Workable and simple upload forms). Turn on by setting
  `search.easy_apply_only: false`.
- **Notion mirror** (`notion:`) — copy every record into a Notion database.
- **1Password auto-login** (`onepassword:`) — hands-free sign-in on an
  unattended server. Everyone else: `run.bat --login` once is all it takes.
- **Run every morning** — `scripts/daily_run.sh` is a ready-made Bash script
  for cron or launchd. **It submits for real** (it passes `--live
  --retry-skipped`), so only schedule it once your dry runs look right. On
  Windows, schedule `run.bat --live --retry-skipped` in Task Scheduler instead.

---

## How it answers screening questions

Questions are matched against the words in your `answers:` lists (not
case-sensitive, longest match wins). Right-to-work and sponsorship questions
are worked out from the job's country against your
`work_authorization.countries`. If a **required** question matches nothing,
the AI (if enabled) may answer it from `data/profile.md` only; if it can't, the
job is **skipped** and the reason is logged. The bot never answers "Yes" just
to get through a form — that is how you avoid telling an employer something
untrue about languages, clearances or citizenship.

Measured over several hundred real applications with an earlier version, 86%
of skips were bespoke technical questions that should stay unanswered, and
most of the rest were plain facts missing from the profile (notice period,
start date, whether you drive). Adding those to `data/profile.md` lets the AI
answer them next time.

---

## Something not working?

See **[GETTING_STARTED.md](GETTING_STARTED.md)** for a slower walk-through and
the common problems: Python not found, LinkedIn asking to verify it's you, the
bot skipping everything, forms that changed.

LinkedIn changes its pages regularly. If applications suddenly start failing,
the page selectors at the top of `src/linkedin/easy_apply.py` and
`src/linkedin/search.py` are the usual culprit — please open an issue.

---

## For developers

- Python 3.10+, [Playwright](https://playwright.dev/python/) for the browser,
  SQLite for records. Entry point `python -m src.main`; modules under `src/`.
- Unit tests: `python -m pytest tests -q` (no LinkedIn or network; the
  modal-locator tests start a headless Chromium against local HTML).
- Config is `config.yaml` → `src/config.py`; screening-question logic is in
  `src/linkedin/answers.py`; the LLM transport in `src/llm_client.py` speaks
  OpenAI-compatible, Anthropic, or the Codex CLI.
- Nothing here phones home. The only network calls are LinkedIn, the company
  application sites you enable, and the LLM / Notion endpoints you configure.

## License & disclaimer

Released under the **MIT License** — see [LICENSE](LICENSE).

For personal, educational use. You are responsible for complying with
LinkedIn's Terms of Service and all applicable laws. The authors accept no
liability for account restrictions or any other consequence of use.
