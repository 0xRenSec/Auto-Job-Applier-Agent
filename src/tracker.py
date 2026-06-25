"""SQLite tracker so we never apply to the same job twice."""
from __future__ import annotations

import sqlite3
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


class Tracker:
    def __init__(self, db_path: str, cfg: dict | None = None, retry_skipped: bool = False):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg or {}
        self.retry_skipped = retry_skipped
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    # Literal queries (no string interpolation) so there is no SQL-injection
    # surface. 'failed' is always retried (transient errors). With retry_skipped,
    # 'skipped' jobs also get another chance — e.g. after new answers were
    # configured or the LLM fallback was enabled.
    _SEEN_RETRY_SKIPPED = (
        "SELECT 1 FROM applications WHERE job_id = ? "
        "AND status IN ('applied','dry_run','external')"
    )
    _SEEN_DEFAULT = (
        "SELECT 1 FROM applications WHERE job_id = ? "
        "AND status IN ('applied','dry_run','external','skipped')"
    )

    def already_seen(self, job_id: str) -> bool:
        query = self._SEEN_RETRY_SKIPPED if self.retry_skipped else self._SEEN_DEFAULT
        cur = self.conn.execute(query, (job_id,))
        return cur.fetchone() is not None

    def already_applied_role(self, title: str, company: str) -> bool:
        """True if we've already *submitted* to this exact title @ company.

        Aggregators (e.g. Jobgether) repost the same role under fresh LinkedIn
        job_ids, so job_id dedup misses them. This catches the repost and stops
        us burning the daily cap on (and spamming) the same opening. Only blocks
        on real submissions — 'applied'/'dry_run' — so a role we merely skipped
        or couldn't complete externally can still be retried via another posting.
        """
        if not title.strip() or not company.strip():
            return False
        cur = self.conn.execute(
            "SELECT 1 FROM applications WHERE LOWER(TRIM(title)) = LOWER(TRIM(?)) "
            "AND LOWER(TRIM(company)) = LOWER(TRIM(?)) "
            "AND status IN ('applied','dry_run') LIMIT 1",
            (title, company),
        )
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
        self.conn.execute(
            "INSERT OR REPLACE INTO applications "
            "(job_id, title, company, location, url, status, reason, applied_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, title, company, location, url, status, reason, applied_at),
        )
        self.conn.commit()
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
