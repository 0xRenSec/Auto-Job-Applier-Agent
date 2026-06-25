"""Best-effort sync of tracker records to a Notion database.

Every tracker.record() upserts a row in the Notion database (matched on the
Job ID property). Failures are logged and never break an application run —
the SQLite tracker remains the source of truth.
"""
from __future__ import annotations

import json
import urllib.request

from . import secrets
from .utils import log

_API = "https://api.notion.com/v1"
_token_cache: str | None = None


def sync_record(cfg: dict, *, job_id: str, title: str, company: str, location: str,
                url: str, status: str, reason: str, applied_at: str) -> None:
    ncfg = cfg.get("notion", {})
    if not ncfg.get("enabled"):
        return
    try:
        token = _token(ncfg)
        db = ncfg["database_id"]
        props = {
            "Title": {"title": [{"text": {"content": (title or job_id)[:200]}}]},
            "Company": {"rich_text": [{"text": {"content": (company or "")[:200]}}]},
            "Location": {"rich_text": [{"text": {"content": (location or "")[:200]}}]},
            "Status": {"select": {"name": status}},
            "Reason": {"rich_text": [{"text": {"content": (reason or "")[:500]}}]},
            "URL": {"url": url or None},
            "Applied At": {"date": {"start": applied_at}},
            "Job ID": {"rich_text": [{"text": {"content": job_id}}]},
        }
        for attempt in (1, 2):  # one retry on transient network errors
            try:
                existing = _request("POST", f"/databases/{db}/query", token, {
                    "filter": {"property": "Job ID", "rich_text": {"equals": job_id}},
                    "page_size": 1,
                })
                if existing.get("results"):
                    _request("PATCH", f"/pages/{existing['results'][0]['id']}", token,
                             {"properties": props})
                else:
                    _request("POST", "/pages", token,
                             {"parent": {"database_id": db}, "properties": props})
                break
            except Exception:
                if attempt == 2:
                    raise
    except Exception as exc:
        log.warning("Notion sync failed for %s: %s", job_id, exc)


def _token(ncfg: dict) -> str:
    global _token_cache
    if _token_cache is None:
        _token_cache = secrets.op_read(ncfg["token_ref"])
    return _token_cache


def _request(method: str, path: str, token: str, body: dict | None = None) -> dict:
    url = _API + path
    # _API is a fixed https endpoint; refuse anything else so urlopen can never
    # be steered to file:/ or another scheme.
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-https Notion URL: {url!r}")
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=20) as r:  # nosec B310 - https-only, guarded above
        return json.load(r)
