"""Backfill tracker rows that never reached the Notion mirror.

Every tracker.record() syncs to Notion best-effort; runs where the `op` CLI
was off PATH (or the network hiccupped) left rows behind. This lists the Job
IDs already in the Notion database, diffs them against data/applied.db, and
writes the missing rows through the same src.notion_sync calls the bot uses.

    python scripts/notion_backfill.py               # dry-run: prints what it would write
    python scripts/notion_backfill.py --apply       # write, then re-query Notion to verify
    python scripts/notion_backfill.py --apply --statuses applied,external --limit 50

Skipped rows are excluded by default (thousands, low value) — pass
--statuses ...,skipped to include them. Authored by Codex, 2026-08-29; the
verification step was added because sync_record swallows API errors.
"""
import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # notion_sync / secrets resolve relative paths from the repo root
sys.path.insert(0, str(ROOT))
from src import notion_sync  # noqa: E402
from src.utils import load_dotenv  # noqa: E402


ALL_STATUSES = ["applied", "external", "lead", "dry_run", "failed", "skipped"]
DEFAULT_STATUSES = ALL_STATUSES[:-1]


def notion_job_ids(cfg):
    ncfg = cfg.get("notion", {})
    if not ncfg.get("enabled"):
        raise RuntimeError("Notion sync is disabled in config")
    token = notion_sync._token(ncfg)
    db = ncfg["database_id"]
    ids = set()
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        result = notion_sync._request("POST", f"/databases/{db}/query", token, body)
        for page in result.get("results", []):
            prop = page.get("properties", {}).get("Job ID", {})
            for item in prop.get("rich_text", []):
                value = item.get("plain_text")
                if value:
                    ids.add(value)
        if not result.get("has_more"):
            return ids
        cursor = result.get("next_cursor")
        if not cursor:
            return ids


def main():
    load_dotenv()  # same .env as the bot, so notion.token_env works here too
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write to Notion (default: dry-run)")
    parser.add_argument("--statuses", default=",".join(DEFAULT_STATUSES),
                        help="comma-separated statuses to backfill")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    statuses = [s.strip() for s in args.statuses.split(",") if s.strip()]
    invalid = [s for s in statuses if s not in ALL_STATUSES]
    if invalid:
        parser.error("invalid status: " + ", ".join(invalid))

    with open("config.yaml", encoding="utf-8") as stream:
        cfg = yaml.safe_load(stream) or {}

    existing = notion_job_ids(cfg)
    conn = sqlite3.connect("data/applied.db")
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"""
        SELECT job_id, title, company, location, url, status, reason,
               applied_at, outcome, outcome_at, outcome_note
        FROM applications
        WHERE status IN ({placeholders})
        ORDER BY CASE status
          WHEN 'applied' THEN 1
          WHEN 'external' THEN 2
          WHEN 'lead' THEN 3
          WHEN 'dry_run' THEN 4
          WHEN 'failed' THEN 5
          WHEN 'skipped' THEN 6
        END, applied_at DESC
        """,
        statuses,
    ).fetchall()
    conn.close()

    missing = [row for row in rows if row["job_id"] not in existing]
    if args.limit is not None:
        missing = missing[: max(0, args.limit)]

    totals = {status: {"missing": 0, "written": 0, "failed": 0} for status in ALL_STATUSES}

    for row in missing:
        status = row["status"]
        totals[status]["missing"] += 1
        if not args.apply:
            print(f"WOULD WRITE {row['job_id']} [{status}] {row['title']}")
            continue
        try:
            notion_sync.sync_record(
                cfg,
                job_id=row["job_id"],
                title=row["title"] or "",
                company=row["company"] or "",
                location=row["location"] or "",
                url=row["url"] or "",
                status=status,
                reason=row["reason"] or "",
                applied_at=row["applied_at"],
            )
            if row["outcome"]:
                notion_sync.sync_outcome(
                    cfg,
                    job_id=row["job_id"],
                    outcome=row["outcome"],
                    outcome_at=row["outcome_at"],
                    note=row["outcome_note"] or "",
                )
            totals[status]["written"] += 1
        except Exception as exc:
            print(f"FAILED {row['job_id']}: {exc}")
            totals[status]["failed"] += 1
        time.sleep(0.35)

    if args.apply and missing:
        # sync_record swallows API errors (logs a warning, returns None), so
        # "written" only means "attempted" — verify against Notion itself.
        after = notion_job_ids(cfg)
        still = [r for r in missing if r["job_id"] not in after]
        print(f"VERIFIED: {len(missing) - len(still)} of {len(missing)} now present in Notion;"
              f" still missing: {len(still)}")
        for r in still[:20]:
            print(f"  STILL MISSING {r['job_id']} [{r['status']}] {r['title']}")
    print("Summary:")
    for status in ALL_STATUSES:
        item = totals[status]
        print(f"{status}: missing={item['missing']} written={item['written']} failed={item['failed']}")


if __name__ == "__main__":
    main()
