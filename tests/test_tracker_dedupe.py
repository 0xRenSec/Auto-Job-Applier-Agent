"""Unit tests for Tracker role-level dedup — no browser, temp sqlite.

Run:  .venv/bin/python tests/test_tracker_dedupe.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tracker import Tracker  # noqa: E402


def _tracker(**kwargs) -> Tracker:
    # In-memory DB: fast and leaves nothing behind in the temp dir.
    # cfg without notion so sync_record is a no-op.
    return Tracker(":memory:", cfg={"notion": {"enabled": False}}, **kwargs)


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


def test_recoverable_jobs_filter():
    # external_apply on, so this stays a test of REASON-based exclusion;
    # the status-based rule has its own tests below.
    t = _recover_tracker(external_enabled=True)
    t.record(job_id="1", title="AppSec Eng", company="Zoho Co", status="external",
             reason="unknown ATS without a CV upload (x.zohorecruit.com)")
    t.record(job_id="2", title="Sec Eng", company="Ashby Co", status="external",
             reason="no application form found (ashby)")
    t.record(job_id="3", title="DevSecOps", company="Timeout Co", status="failed",
             reason="exception: Timeout")
    t.record(job_id="4", title="Sec Arch", company="WD Co", status="external",
             reason="deferred ATS (workday) — apply manually")        # excluded
    t.record(job_id="5", title="Sec Eng", company="RF Co", status="external",
             reason="submitted but not confirmed — verify manually (recruiterflow.com) [retry-protected]")  # excluded
    t.record(job_id="6", title="Sec Eng", company="Done Co", status="applied")  # excluded (already applied)
    ids = {r["job_id"] for r in t.recoverable_jobs()}
    assert ids == {"1", "2", "3"}, ids


def test_role_dedupe_is_unicode_case_insensitive():
    t = _tracker()
    t.record(job_id="111", title="Säkerhetsingenjör", company="Åsa AB", status="applied")
    # SQLite LOWER() would miss these — pyfold must catch them.
    assert t.already_applied_role("SÄKERHETSINGENJÖR", "ÅSA AB") is True
    assert t.already_applied_role("säkerhetsingenjör", "åsa ab") is True


def test_retry_dry_run_reopens_previews_for_live():
    t = _tracker(retry_dry_run=True)
    t.record(job_id="111", title="AppSec Eng", company="Acme", status="dry_run")
    # The previewed job must be re-attemptable and not blocked by role dedupe...
    assert t.already_seen("111") is False
    assert t.already_applied_role("AppSec Eng", "Acme") is False
    # ...but a REAL submission still blocks both.
    t.record(job_id="222", title="Sec Eng", company="Mesh", status="applied")
    assert t.already_seen("222") is True
    assert t.already_applied_role("Sec Eng", "Mesh") is True


def test_recovery_orders_least_recently_attempted_first():
    t = _tracker()
    t.record(job_id="new", title="A", company="X", status="failed", reason="exception: t")
    t.record(job_id="old", title="B", company="Y", status="failed", reason="exception: t")
    t.conn.execute("UPDATE applications SET applied_at='2026-01-01T00:00:00' "
                   "WHERE job_id='old'")
    ids = [r["job_id"] for r in t.recoverable_jobs()]
    assert ids.index("old") < ids.index("new"), ids


# --- recoverable_jobs(): what --recover is allowed to re-attempt --------------

def _recover_tracker(external_enabled: bool) -> Tracker:
    return Tracker(":memory:", cfg={"notion": {"enabled": False},
                                    "external_apply": {"enabled": external_enabled}})


def test_recovery_skips_external_when_external_apply_is_off():
    """With external_apply disabled, re-visiting an 'external' job can only
    re-record it as external — a wasted LinkedIn navigation, not a retry."""
    t = _recover_tracker(external_enabled=False)
    t.record(job_id="ext", title="AppSec Engineer", company="Example Labs",
             status="external", reason="no Easy Apply (external application)")
    t.record(job_id="mod", title="Group CISO", company="Acme Holdings",
             status="failed", reason="Easy Apply modal did not open")
    ids = [r["job_id"] for r in t.recoverable_jobs()]
    assert ids == ["mod"], ids


def test_recovery_keeps_external_when_external_apply_is_on():
    t = _recover_tracker(external_enabled=True)
    t.record(job_id="ext", title="AppSec Engineer", company="Example Labs",
             status="external", reason="no Easy Apply (external application)")
    ids = [r["job_id"] for r in t.recoverable_jobs()]
    assert ids == ["ext"], ids


def test_recovery_prioritises_the_freshly_fixed_modal_failures():
    """The modal-decoy fix (2026-08-20) makes these reliably retryable, so they
    must come before generic transient failures."""
    t = _recover_tracker(external_enabled=True)
    t.record(job_id="other", title="X", company="Y", status="failed",
             reason="exception: Page.goto: Timeout 20000ms exceeded")
    t.record(job_id="noform", title="X2", company="Y2", status="external",
             reason="no application form found (ashby)")
    t.record(job_id="modal", title="Group CISO", company="Acme Holdings",
             status="failed", reason="Easy Apply modal did not open")
    ids = [r["job_id"] for r in t.recoverable_jobs()]
    assert ids.index("modal") < ids.index("noform") < ids.index("other"), ids


def test_recovery_still_excludes_possible_double_applies():
    t = _recover_tracker(external_enabled=True)
    t.record(job_id="maybe", title="X", company="Y", status="failed",
             reason="submitted but not confirmed")
    t.record(job_id="deferred", title="X", company="Y", status="external",
             reason="deferred — apply manually (Workday)")
    assert t.recoverable_jobs() == []


# --- 'jd screen[...]' skips are FINAL verdicts under the policy that made them ---
from src import jd_screen  # noqa: E402

JD_CFG = {"notion": {"enabled": False},
          "jd_screen": {"enabled": True, "home_country": "Sweden",
                        "allowed_regions": ["European Union"]}}


def test_retry_skipped_never_resurfaces_jd_screen_skips():
    t = Tracker(":memory:", cfg=JD_CFG, retry_skipped=True)
    tag = jd_screen.policy_tag(JD_CFG)
    t.record(job_id="pol", title="X", company="Y", status="skipped",
             reason=f"{tag}: must reside in the United States")
    t.record(job_id="ans", title="X", company="Y", status="skipped",
             reason="unanswered required question: 'Passport?'")
    assert t.already_seen("pol") is True   # policy verdict stays final
    assert t.already_seen("ans") is False  # answer-gap skips do get retried


def test_changed_policy_makes_old_jd_screen_skips_retryable():
    """A verdict carries the hash of the policy that made it; a new policy
    (different home country) or screening switched off re-attempts the job."""
    t = Tracker(":memory:", cfg=JD_CFG, retry_skipped=True)
    t.record(job_id="old", title="X", company="Y", status="skipped",
             reason="jd screen[deadbeef]: must reside in the United States")
    assert t.already_seen("old") is False
    off = Tracker(":memory:", cfg={"notion": {"enabled": False}}, retry_skipped=True)
    off.record(job_id="pol", title="X", company="Y", status="skipped",
               reason=f"{jd_screen.policy_tag(JD_CFG)}: hybrid")
    assert off.already_seen("pol") is False


def test_recovery_excludes_jd_screen_rows():
    cfg = dict(JD_CFG, external_apply={"enabled": True})
    t = Tracker(":memory:", cfg=cfg)
    t.record(job_id="pol", title="X", company="Y", status="failed",
             reason=f"{jd_screen.policy_tag(cfg)}: US residents only")
    t.record(job_id="old", title="X", company="Y", status="failed",
             reason="jd screen[deadbeef]: US residents only")
    # Only the CURRENT policy's verdicts are final; an old policy's are retried.
    assert [r["job_id"] for r in t.recoverable_jobs()] == ["old"]

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
