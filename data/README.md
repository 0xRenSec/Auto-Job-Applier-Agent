# `data/` — your personal files live here (gitignored)

Everything in this folder is ignored by git **except this README**. Put your own
files here; the bot also writes its output here.

## You provide

| File | What | Set in `config.yaml` |
|------|------|----------------------|
| `data/resume.pdf` | Your CV / résumé (any path is fine) | `applicant.resume_path` |
| `data/profile.md` | Your background — source for cover letters, LLM answers, and tailored CVs. Copy from `../profile.example.md` | `cover_letter.profile_path` |

```bash
cp ../profile.example.md data/profile.md   # then edit with your real details
cp /path/to/your/cv.pdf   data/resume.pdf
```

## The bot generates (auto-created, gitignored)

- `data/applied.db` — SQLite log of every job (applied / skipped / external / failed)
- `data/cover_letters/` — generated cover letters
- `data/resumes/` — per-job tailored CVs (when `resume.mode: tailored`)
- `data/screenshots/` — one screenshot per application
- `data/llm_answers.json` — cached LLM answers (audit log)

None of these are committed — they contain personal data.
