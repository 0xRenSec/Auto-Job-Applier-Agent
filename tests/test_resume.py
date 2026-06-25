"""Unit tests for src/resume.py — no browser, no network (LLM stubbed).

Run:  .venv/bin/python tests/test_resume.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import resume as resume_mod  # noqa: E402


class _Job:
    job_id = "123"
    title = "Application Security Engineer"
    company = "Acme"
    description = "We want SAST, DAST and CI/CD security."


def _cfg(tmp: Path, mode: str, profile: str = "## Skills\nSAST, DAST, CI/CD") -> dict:
    resume_path = tmp / "static_cv.pdf"
    resume_path.write_bytes(b"%PDF-1.4 static")
    prof = tmp / "profile.md"
    prof.write_text(profile, encoding="utf-8")
    return {
        "applicant": {"resume_path": str(resume_path)},
        "cover_letter": {"profile_path": str(prof)},
        "resume": {"mode": mode, "output_dir": str(tmp / "resumes")},
    }


def test_static_mode_returns_static_path():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cfg = _cfg(tmp, "static")
        assert resume_mod.resume_for_job(_Job(), cfg) == str(tmp / "static_cv.pdf")


def test_tailored_falls_back_when_llm_unavailable(monkeypatch=None):
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cfg = _cfg(tmp, "tailored")
        orig = resume_mod.llm_client.complete
        resume_mod.llm_client.complete = lambda *a, **k: None   # no key / failure
        try:
            assert resume_mod.resume_for_job(_Job(), cfg) == str(tmp / "static_cv.pdf")
        finally:
            resume_mod.llm_client.complete = orig


def test_tailored_renders_pdf_when_llm_answers():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cfg = _cfg(tmp, "tailored")
        orig = resume_mod.llm_client.complete
        resume_mod.llm_client.complete = lambda *a, **k: "SUMMARY\n- AppSec engineer\nSKILLS\n- SAST, DAST"
        try:
            out = resume_mod.resume_for_job(_Job(), cfg)
        finally:
            resume_mod.llm_client.complete = orig
        assert out is not None and out.endswith("123.pdf"), out
        assert Path(out).exists() and Path(out).read_bytes()[:4] == b"%PDF"


def test_tailored_with_empty_profile_falls_back():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cfg = _cfg(tmp, "tailored", profile="   ")
        # LLM must NOT be called with no profile facts; should use static file.
        called = {"n": 0}
        orig = resume_mod.llm_client.complete

        def _spy(*a, **k):
            called["n"] += 1
            return "should not be used"
        resume_mod.llm_client.complete = _spy
        try:
            out = resume_mod.resume_for_job(_Job(), cfg)
        finally:
            resume_mod.llm_client.complete = orig
        assert out == str(tmp / "static_cv.pdf")
        assert called["n"] == 0


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
