# LiJAB — LinkedIn Job Applier Bot

Automates LinkedIn **Easy Apply** (and, optionally, external company ATSes) for a
search you define — fills the multi-step forms from your config, answers
screening questions truthfully, generates per-job cover letters, and tracks
everything so it never applies twice.

Plug-and-play: everything personal lives in `config.yaml` + `data/profile.md`
(both gitignored). Works with **any LLM** — or none at all.

---

## ⚠️ Read this first

- **Automating LinkedIn Easy Apply violates LinkedIn's Terms of Service.** LinkedIn
  detects automation and can **restrict or ban your account**. Use at your own risk.
  The bot includes anti-detection rails (session reuse, human-like delays, a daily
  cap, a visible browser) to *reduce* — not eliminate — that risk.
- **Always run with `dry_run: true` first** (the default). It fills every form and
  screenshots it but does **not** submit, so you can verify answers before going live.
- **Start with a small cap** and watch the browser the first few runs.
- It **never guesses on required questions.** If it can't answer truthfully from your
  config/profile, it skips the job and logs it for manual follow-up.

---

## Features

- LinkedIn Easy Apply automation across multiple keywords / locations / filters
- Optional **external ATS** apply (Greenhouse, Lever, Ashby, Teamtailor, Recruitee,
  Workable, and simple upload forms)
- **Any LLM** for cover letters + hard screening questions — OpenAI, Anthropic,
  or local (Ollama / LM Studio / vLLM) and gateways (OpenRouter, Together, Azure)
- Truthful answering: config maps → LLM (profile-grounded) → **skip** (never fabricate)
- Per-job cover letters (offline template or LLM), optional PDF render for upload fields
- **Per-job tailored CV** (optional): the LLM re-orders/re-emphasises your profile
  to match each job description and uploads a tailored PDF — never inventing facts
- SQLite tracking with **role-level dedup** (catches aggregator reposts under new IDs)
- Optional 1Password auto-login with TOTP 2FA
- Safety rails: dry-run, per-run + per-day caps, randomized human-like delays

---

## Quickstart

```bash
# 1. Install
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 2. Configure
cp config.example.yaml config.yaml      # then edit it (see below)
cp profile.example.md data/profile.md   # your background, for letters + LLM answers
#   put your CV at the path in applicant.resume_path (default: data/resume.pdf)

# 3. Log in once (saves the session; handles 2FA in the window)
python -m src.main --login

# 4. DRY RUN — fills forms, screenshots, does NOT submit. Review data/screenshots/.
python -m src.main

# 5. Go live with a small cap once you're happy
python -m src.main --live --max 5

# 6. Normal live run (uses caps from config.yaml)
python -m src.main --live
```

The first live run: keep `headless: false` and **watch the window**. If LinkedIn
shows a captcha or "is this you?" check, solve it in the window — the bot waits.

---

## Configure it

Everything is in `config.yaml` (copied from `config.example.yaml`, which is fully
commented). The essentials:

- **`search`** — keywords, locations, remote/date/experience filters, title
  allow/block lists.
- **`applicant`** — name, email, phone, location, CV path, LinkedIn URL.
- **`answers`** — map screening-question substrings to answers
  (`experience_years`, `yes_no`, `text`, salary). Anything unmatched and required
  is skipped unless the LLM can answer it from your profile.
- **`safety`** — `dry_run`, caps, delays, `dedupe_by_role`.

### Using any LLM (optional)

The bot is fully functional without an LLM. Add one to write tailored cover
letters (`cover_letter.mode: llm`) and answer screening questions the maps miss
(`answers.llm_fallback.enabled: true`). Configure the `llm:` block:

```yaml
# OpenAI
llm: { provider: openai, model: gpt-4o-mini, api_key_env: OPENAI_API_KEY }

# Local Ollama (no key needed)
llm: { provider: openai, model: llama3.1, base_url: "http://localhost:11434/v1", api_key_env: OLLAMA_KEY }

# Anthropic (Claude)  —  pip install anthropic
llm: { provider: anthropic, model: claude-3-5-sonnet-latest, api_key_env: ANTHROPIC_API_KEY }
```

Put the key in a `.env` file (gitignored): `OPENAI_API_KEY=sk-...`. The LLM is
instructed to answer **only** from `data/profile.md` and to decline (→ skip the
job) when it can't answer truthfully.

### Tailored CV per job (optional)

With `resume.mode: tailored`, the LLM rewrites your CV from `data/profile.md` for
each job — leading with the most relevant experience and mirroring the job's
terminology — renders it to a PDF, and uploads that instead of the static file.
It may only **re-order, re-weight and rephrase facts already in your profile**;
it never invents experience, employers, dates or numbers. No LLM configured (or
any failure) → it falls back to your static `applicant.resume_path`. Keep
`mode: static` to always upload your fixed CV.

### 1Password auto-login (optional)

For hands-free login you can store your LinkedIn credentials (+ a TOTP field) in
1Password and let the bot read them via the `op` CLI. Set the `onepassword:`
block and put `OP_SERVICE_ACCOUNT_TOKEN` in `.env`. Otherwise just use
`python -m src.main --login` and ignore that section.

---

## Where things go

| Path | What |
|---|---|
| `config.yaml` | your config (gitignored) |
| `data/profile.md` | your background, for cover letters + LLM answers (gitignored) |
| `data/applied.db` | SQLite log of every job (applied / dry_run / skipped / external / failed) |
| `data/cover_letters/` | generated cover letters |
| `data/resumes/` | per-job tailored CVs (when `resume.mode: tailored`) |
| `data/screenshots/` | one screenshot per completed application |
| `logs/` | per-run logs |
| `browser_profile/` | persistent Chrome profile = your saved LinkedIn session |

Inspect what happened:

```bash
sqlite3 data/applied.db \
  "SELECT applied_at,status,title,company,reason FROM applications ORDER BY applied_at DESC LIMIT 20;"
```

---

## Tests

Browser-free unit tests (no network, no LinkedIn):

```bash
.venv/bin/python tests/test_external_apply.py
.venv/bin/python tests/test_tracker_dedupe.py
.venv/bin/python tests/test_resume.py
```

---

## How it answers screening questions

`config.yaml → answers` maps question substrings to answers. For a required
question with no match, the LLM (if enabled) may answer it **from your profile
only**; if it can't, the whole job is **skipped** and logged. The bot never
guesses the affirmative on required questions — that's how you avoid sending
untrue claims (languages, clearances, citizenship) to employers.

---

## When it breaks

LinkedIn changes its HTML regularly. If applications start failing, the CSS
selectors are the usual culprit — they're centralized at the top of
`src/linkedin/easy_apply.py` and `src/linkedin/search.py`.

---

## License & disclaimer

For personal, educational use. You are responsible for complying with LinkedIn's
Terms of Service and all applicable laws. The authors accept no liability for
account restrictions or any other consequence of use.
