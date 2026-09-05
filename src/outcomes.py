"""Record application OUTCOMES — what came BACK, not what was sent.

The bot's tracker records submissions; this records employer responses you
receive (rejections, interview invites, recruiter contacts) onto the matching
application rows in applied.db, mirrored to Notion best-effort. Rows are
matched by company (casefolded) and an optional title substring; a response
with no matching application (e.g. a recruiter approaching directly) gets a
new row with status 'lead' so it is tracked rather than lost.

CLI (run it by hand, or from whatever watches your inbox):
    python -m src.outcomes --company "Acme" --title "Data Analyst" \
        --outcome rejected --date 2026-01-15 --note "email from recruiter"
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import unicodedata
from datetime import datetime

from . import notion_sync
from .config import load_config
from .utils import load_dotenv, log

OUTCOMES = ("rejected", "interview", "offer", "recruiter_contact", "update")

_MIGRATION = (
    "ALTER TABLE applications ADD COLUMN outcome TEXT",
    "ALTER TABLE applications ADD COLUMN outcome_at TEXT",
    "ALTER TABLE applications ADD COLUMN outcome_note TEXT",
)


def _fold(s):
    return unicodedata.normalize("NFKC", s).casefold().strip() if isinstance(s, str) else s


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(applications)")}
    for stmt in _MIGRATION:
        if stmt.rsplit(" ", 2)[-2] not in cols:
            conn.execute(stmt)
    conn.commit()


def record_outcome(cfg: dict, *, company: str, title: str = "", outcome: str,
                   note: str = "", date: str = "") -> list[str]:
    """Stamp an outcome on every application row matching company (+ title
    substring). Returns the affected job_ids (a new 'lead' row id if none)."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    when = date or datetime.now().date().isoformat()
    conn = sqlite3.connect(cfg["storage"]["database_path"], timeout=30)
    conn.create_function("pyfold", 1, _fold, deterministic=True)
    _migrate(conn)

    rows = conn.execute(
        "SELECT job_id, title FROM applications WHERE pyfold(company) = pyfold(?)",
        (company,),
    ).fetchall()
    if title:
        rows = [r for r in rows if _fold(title) in _fold(r[1] or "")]

    if rows:
        ids = [r[0] for r in rows]
        conn.executemany(
            "UPDATE applications SET outcome = ?, outcome_at = ?, outcome_note = ? "
            "WHERE job_id = ?",
            [(outcome, when, note, jid) for jid in ids],
        )
    else:
        # No application row (e.g. direct recruiter approach) — track as a lead.
        slug = re.sub(r"[^a-z0-9]+", "-", _fold(f"{company}-{title}"))[:60].strip("-")
        ids = [f"lead-{when}-{slug}"]
        conn.execute(
            "INSERT OR REPLACE INTO applications "
            "(job_id, title, company, location, url, status, reason, applied_at, "
            " outcome, outcome_at, outcome_note) "
            "VALUES (?, ?, ?, '', '', 'lead', ?, ?, ?, ?, ?)",
            (ids[0], title, company, note, datetime.now().isoformat(timespec="seconds"),
             outcome, when, note),
        )
    conn.commit()

    for jid in ids:
        log.info("[outcome] %s @ %s -> %s (%s)", title or jid, company, outcome, when)
        notion_sync.sync_outcome(cfg, job_id=jid, outcome=outcome, outcome_at=when,
                                 note=note)
    conn.close()
    return ids


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--company", required=True)
    p.add_argument("--title", default="", help="title substring to narrow the match")
    p.add_argument("--outcome", required=True, choices=OUTCOMES)
    p.add_argument("--note", default="")
    p.add_argument("--date", default="", help="YYYY-MM-DD of the response (default today)")
    args = p.parse_args()
    load_dotenv()  # same .env as the bot, so notion.token_env works here too
    ids = record_outcome(load_config(args.config), company=args.company,
                         title=args.title, outcome=args.outcome,
                         note=args.note, date=args.date)
    print("updated:", ", ".join(ids))


if __name__ == "__main__":
    main()
