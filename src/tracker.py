"""SQLite tracker so we never apply to the same job twice."""
from __future__ import annotations

import sqlite3
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from . import notion_sync
from .utils import log

_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    job_id      TEXT PRIMARY KEY,
    title       TEXT,
    company     TEXT,
    location    TEXT,
    url         TEXT,
    status      TEXT,           -- applied | dry_run | skipped | external | failed
    reason      TEXT,           -- why skipped/failed
    applied_at  TEXT
);
"""


def _fold(s):
    """Unicode-aware casefold for dedupe — SQLite's LOWER() is ASCII-only, so
    'Åsa' vs 'ÅSA' would otherwise evade role dedupe."""
    return unicodedata.normalize("NFKC", s).casefold().strip() if isinstance(s, str) else s


class Tracker:
    def __init__(self, db_path: str, cfg: dict | None = None,
                 retry_skipped: bool = False, retry_dry_run: bool = False):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg or {}
        self.retry_skipped = retry_skipped
        self.retry_dry_run = retry_dry_run
        # timeout: wait for a concurrent holder instead of failing instantly
        # with "database is locked".
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.create_function("pyfold", 1, _fold, deterministic=True)
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    # Literal queries built from a fixed status set (no user input reaches the
    # SQL text) so there is no SQL-injection surface. 'failed' is always retried
    # (transient errors). With retry_skipped, 'skipped' jobs get another chance —
    # e.g. after new answers were configured. With retry_dry_run, jobs previewed
    # during a dry run can be submitted for real by a later --live run.
    def _seen_statuses(self) -> tuple[str, ...]:
        seen = {"applied", "dry_run", "external", "skipped"}
        if self.retry_skipped:
            seen.discard("skipped")
        if self.retry_dry_run:
            seen.discard("dry_run")
        return tuple(sorted(seen))

    def already_seen(self, job_id: str) -> bool:
        statuses = self._seen_statuses()
        query = ("SELECT 1 FROM applications WHERE job_id = ? AND (status IN (%s)"
                 % ",".join("?" * len(statuses)))
        params: list = [job_id, *statuses]
        tag = self._jd_screen_tag()
        if self.retry_skipped and tag:
            # Screening skips made under the CURRENT policy are final verdicts
            # (the remote-work gate), not answer gaps — --retry-skipped must
            # not resurface them. A changed policy carries a different tag, so
            # its old verdicts become retryable.
            query += " OR (status = 'skipped' AND reason LIKE ?)"
            params.append(tag + "%")
        query += ")"
        cur = self.conn.execute(query, params)
        return cur.fetchone() is not None

    def _jd_screen_tag(self) -> str | None:
        """Reason prefix of screening skips under the current policy
        ('jd screen[abcd1234]'), or None when screening is switched off."""
        if not (self.cfg.get("jd_screen") or {}).get("enabled"):
            return None
        from . import jd_screen
        return jd_screen.policy_tag(self.cfg)

    def recoverable_jobs(self) -> list[dict]:
        """Jobs the bot reached but couldn't finish (status external/failed) that
        are SAFE to re-attempt with newer code. Excludes:
          - deferred / manual-only ATSes (Workday, iCIMS, ...),
          - submissions that may already have gone through ('not confirmed' /
            retry-protected) — re-clicking Submit would double-apply.
        Also excludes 'external' jobs when external_apply is disabled: re-visiting
        one can only re-record it as external, so it burns a LinkedIn navigation
        (and looks like bot volume) without ever being able to apply.

        Oldest-attempted first within each bucket: every attempt refreshes
        applied_at, so failed re-attempts go to the back of the queue instead of
        starving older jobs. Used by the --recover URL-targeted re-attempt mode.
        """
        statuses = ("external", "failed")
        if not self.cfg.get("external_apply", {}).get("enabled"):
            statuses = ("failed",)
        # Final policy decisions from the remote-work gate (current policy) —
        # never worth another page load.
        tag = self._jd_screen_tag()
        jd_clause = "AND reason NOT LIKE ? " if tag else ""
        params = [*statuses] + ([tag + "%"] if tag else [])
        cur = self.conn.execute(
            "SELECT job_id, title, company, location, url FROM applications "
            "WHERE status IN (%s) " % ",".join("?" * len(statuses)) +
            "AND reason NOT LIKE '%deferred%' "
            "AND reason NOT LIKE '%apply manually%' "
            "AND reason NOT LIKE '%not confirmed%' "
            "AND reason NOT LIKE '%retry-protected%' "
            + jd_clause +
            # Process the buckets the newer code most reliably fixes first:
            #   0) Easy Apply modal missed behind hidden Video.js dialog decoys
            #      (fixed 2026-08-20 — these now go through reliably),
            #   1) Zoho/Ceipal "no CV upload" (now tractable) + oversized forms,
            #   2) "no form found" (helps Ashby/SPA; not formless boards like adesso),
            #   3) everything else (transient timeouts, etc.).
            "ORDER BY CASE WHEN reason LIKE '%modal did not open%' THEN 0 "
            "              WHEN reason LIKE '%without a CV upload%' "
            "                OR reason LIKE '%form too large%' THEN 1 "
            "              WHEN reason LIKE '%no application form found%' THEN 2 "
            "              ELSE 3 END, "
            "         applied_at ASC",
            params,
        )
        cols = ("job_id", "title", "company", "location", "url")
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def already_applied_role(self, title: str, company: str) -> bool:
        """True if we've already *submitted* to this exact title @ company.

        Aggregators (e.g. Jobgether) repost the same role under fresh LinkedIn
        job_ids, so job_id dedup misses them. This catches the repost and stops
        us burning the daily cap on (and spamming) the same opening. Only blocks
        on real submissions — 'applied'/'dry_run' — so a role we merely skipped
        or couldn't complete externally can still be retried via another posting.
        With retry_dry_run, dry-run previews don't block a real submission.
        """
        if not title.strip() or not company.strip():
            return False
        statuses = ("applied",) if self.retry_dry_run else ("applied", "dry_run")
        query = ("SELECT 1 FROM applications WHERE pyfold(title) = pyfold(?) "
                 "AND pyfold(company) = pyfold(?) AND status IN (%s) LIMIT 1"
                 % ",".join("?" * len(statuses)))
        cur = self.conn.execute(query, (title, company, *statuses))
        return cur.fetchone() is not None

    def record(
        self,
        job_id: str,
        title: str = "",
        company: str = "",
        location: str = "",
        url: str = "",
        status: str = "applied",
        reason: str = "",
    ) -> None:
        applied_at = datetime.now().isoformat(timespec="seconds")
        for attempt in (1, 2):
            try:
                self.conn.execute(
                    "INSERT OR REPLACE INTO applications "
                    "(job_id, title, company, location, url, status, reason, applied_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, title, company, location, url, status, reason, applied_at),
                )
                self.conn.commit()
                break
            except sqlite3.Error as exc:
                if attempt == 2:
                    # A live submission just happened — losing this row silently
                    # would allow a double-apply later. Log everything needed to
                    # reconstruct it by hand.
                    log.critical(
                        "TRACKER WRITE FAILED (%s): job_id=%r status=%r title=%r "
                        "company=%r url=%r reason=%r — record it manually!",
                        exc, job_id, status, title, company, url, reason)
                    break
                time.sleep(1.0)
        log.info("[%s] %s @ %s %s", status, title or job_id, company, f"({reason})" if reason else "")
        notion_sync.sync_record(
            self.cfg, job_id=job_id, title=title, company=company, location=location,
            url=url, status=status, reason=reason, applied_at=applied_at,
        )

    def count_today(self, status: str = "applied") -> int:
        today = datetime.now().date().isoformat()
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status = ? AND applied_at LIKE ?",
            (status, f"{today}%"),
        )
        return cur.fetchone()[0]

    def close(self) -> None:
        self.conn.close()
