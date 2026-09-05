"""Best-effort sync of tracker records to a Notion database.

Every tracker.record() upserts a row in the Notion database (matched on the
Job ID property). Failures are logged and never break an application run —
the SQLite tracker remains the source of truth.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

from . import secrets
from .utils import log

_API = "https://api.notion.com/v1"
_token_cache: dict[str, str] = {}


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
                time.sleep(2)  # brief backoff before the single retry
    except Exception as exc:
        log.warning("Notion sync failed for %s: %s", job_id, exc)


_outcome_props_ok = False


def sync_outcome(cfg: dict, *, job_id: str, outcome: str, outcome_at: str,
                 note: str) -> None:
    """Stamp an outcome (rejection / interview / ...) on the Notion row for
    job_id. Creates the Outcome properties on the database on first use.
    Best-effort like sync_record — never raises."""
    ncfg = cfg.get("notion", {})
    if not ncfg.get("enabled"):
        return
    try:
        token = _token(ncfg)
        db = ncfg["database_id"]
        _ensure_outcome_props(db, token)
        props = {
            "Outcome": {"select": {"name": outcome}},
            "Outcome At": {"date": {"start": outcome_at}},
            "Outcome Note": {"rich_text": [{"text": {"content": (note or "")[:500]}}]},
        }
        existing = _request("POST", f"/databases/{db}/query", token, {
            "filter": {"property": "Job ID", "rich_text": {"equals": job_id}},
            "page_size": 1,
        })
        if existing.get("results"):
            _request("PATCH", f"/pages/{existing['results'][0]['id']}", token,
                     {"properties": props})
        else:
            props["Title"] = {"title": [{"text": {"content": job_id[:200]}}]}
            props["Job ID"] = {"rich_text": [{"text": {"content": job_id}}]}
            props["Status"] = {"select": {"name": "lead"}}
            _request("POST", "/pages", token,
                     {"parent": {"database_id": db}, "properties": props})
    except Exception as exc:
        log.warning("Notion outcome sync failed for %s: %s", job_id, exc)


def _ensure_outcome_props(db: str, token: str) -> None:
    global _outcome_props_ok
    if _outcome_props_ok:
        return
    schema = _request("GET", f"/databases/{db}", token)
    have = schema.get("properties", {})
    want = {
        "Outcome": {"select": {}},
        "Outcome At": {"date": {}},
        "Outcome Note": {"rich_text": {}},
    }
    missing = {k: v for k, v in want.items() if k not in have}
    if missing:
        _request("PATCH", f"/databases/{db}", token, {"properties": missing})
    _outcome_props_ok = True


def _token(ncfg: dict) -> str:
    """The Notion integration token, from whichever is configured:
    notion.token_env (the name of a variable in .env, e.g. NOTION_TOKEN) or
    notion.token_ref (a 1Password op:// reference read via the op CLI).
    Cached per source, so two configs never share a token."""
    global _token_cache
    if not isinstance(_token_cache, dict):
        _token_cache = {}
    env_name, ref = ncfg.get("token_env"), ncfg.get("token_ref")
    if env_name and os.environ.get(env_name):
        source = f"env:{env_name}"
        if source not in _token_cache:
            _token_cache[source] = os.environ[env_name]
    elif ref:
        source = f"op:{ref}"
        if source not in _token_cache:
            _token_cache[source] = secrets.op_read(ref)
    else:
        raise RuntimeError(
            "notion.enabled is true but no token is configured — set notion.token_env "
            "(and put the token in .env) or notion.token_ref (1Password)."
        )
    return _token_cache[source]


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
