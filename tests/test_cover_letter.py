"""Unit tests for cover-letter template skill matching — no network, no PDF.

Run:  .venv/bin/python tests/test_cover_letter.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import cover_letter  # noqa: E402


class _Job:
    job_id = "123"
    title = "Security Engineer"
    company = "Acme"


_CFG = {"applicant": {"first_name": "Jane", "last_name": "Doe",
                      "email": "jane@example.com"},
        "cover_letter": {"claim_skills": ["Java", "JavaScript", "Python", "SCA"]}}


def test_cache_path_changes_when_inputs_change(tmp_path):
    """Editing the profile, the name or the skills must not reuse an old letter."""
    profile = tmp_path / "profile.md"
    profile.write_text("I am a nurse.", encoding="utf-8")
    cfg = {"applicant": dict(_CFG["applicant"]),
           "cover_letter": {"profile_path": str(profile), "output_dir": str(tmp_path),
                            "claim_skills": ["Java"]}}
    first = cover_letter._cache_path("123", cfg)
    profile.write_text("I am a pilot.", encoding="utf-8")
    assert cover_letter._cache_path("123", cfg) != first
    cfg["cover_letter"]["claim_skills"] = ["Rust"]
    third = cover_letter._cache_path("123", cfg)
    cfg["applicant"]["first_name"] = "Alex"
    assert cover_letter._cache_path("123", cfg) != third


def test_no_claim_skills_configured_claims_nothing():
    cfg = {"applicant": _CFG["applicant"]}
    letter = cover_letter._generate_template(
        _Job(), "We need SAST, DAST, Python and Kubernetes.", cfg)
    # Nothing in the letter may assert a skill the user never listed.
    for word in ("SAST", "DAST", "Python", "Kubernetes", "DevSecOps", "application security"):
        assert word not in letter
    assert "my CV has the details" in letter


def test_java_does_not_match_javascript():
    letter = cover_letter._generate_template(
        _Job(), "We need strong JavaScript and TypeScript skills.", _CFG)
    assert "JavaScript" in letter
    assert "Java," not in letter and "Java " not in letter


def test_sca_does_not_match_scalable():
    letter = cover_letter._generate_template(
        _Job(), "Highly scalable systems with Python.", _CFG)
    assert "SCA" not in letter
    assert "Python" in letter


def test_configured_claim_skills_override_default():
    cfg = dict(_CFG)
    cfg["cover_letter"] = {"claim_skills": ["Rust", "Go"]}
    letter = cover_letter._generate_template(_Job(), "We build in Rust.", cfg)
    assert "Rust" in letter
    # Built-in AppSec defaults must not leak in when the user configured a list.
    assert "DevSecOps" not in letter


def test_no_match_fallback_uses_configured_skills():
    cfg = dict(_CFG)
    cfg["cover_letter"] = {"claim_skills": ["Rust", "Go"]}
    letter = cover_letter._generate_template(_Job(), "A generic description.", cfg)
    assert "Rust" in letter and "Go" in letter


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
