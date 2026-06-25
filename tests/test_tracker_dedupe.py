"""Unit tests for Tracker role-level dedup — no browser, temp sqlite.

Run:  .venv/bin/python tests/test_tracker_dedupe.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tracker import Tracker  # noqa: E402


def _tracker() -> Tracker:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    # cfg without notion so sync_record is a no-op.
    return Tracker(tmp.name, cfg={"notion": {"enabled": False}})


def test_repost_under_new_job_id_is_caught():
    t = _tracker()
    t.record(job_id="111", title="Senior DevSecOps Engineer", company="Jobgether",
             status="applied")
    # Same role, different job_id (aggregator repost).
    assert t.already_applied_role("Senior DevSecOps Engineer", "Jobgether") is True
    # Case/space insensitive.
    assert t.already_applied_role(" senior devsecops engineer ", "JOBGETHER") is True


def test_different_role_or_company_not_caught():
    t = _tracker()
    t.record(job_id="111", title="Senior DevSecOps Engineer", company="Jobgether",
             status="applied")
    assert t.already_applied_role("AI Architect", "Jobgether") is False
    assert t.already_applied_role("Senior DevSecOps Engineer", "Acme") is False


def test_only_real_submissions_block():
    t = _tracker()
    # A skipped/external-blocked prior attempt must NOT block a later posting.
    t.record(job_id="111", title="Cloud Security Engineer", company="Mesh",
             status="skipped", reason="required field unanswered")
    assert t.already_applied_role("Cloud Security Engineer", "Mesh") is False
    t.record(job_id="222", title="Cloud Security Engineer", company="Mesh",
             status="dry_run")
    assert t.already_applied_role("Cloud Security Engineer", "Mesh") is True


def test_blank_title_or_company_never_blocks():
    t = _tracker()
    t.record(job_id="111", title="", company="", status="applied")
    assert t.already_applied_role("", "") is False
    assert t.already_applied_role("   ", "Jobgether") is False


if __name__ == "__main__":
    import traceback
    failed = 0
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
