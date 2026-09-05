"""Unit tests for src/llm_answers.py answer validation and caching — no network.

Run:  .venv/bin/python tests/test_llm_answers.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import llm_answers  # noqa: E402


def test_exact_option_match_wins():
    assert llm_answers._match_option("yes", ["Yes", "No"]) == "Yes"
    assert llm_answers._match_option("  NO ", ["Yes", "No"]) == "No"


def test_no_does_not_match_none_of_the_above():
    # The old substring fallback picked "None of the above" for "No".
    assert llm_answers._match_option("No", ["None of the above", "Maybe"]) is None


def test_blank_answer_never_selects_an_option():
    # "" is a substring of everything — must not select the first option.
    assert llm_answers._match_option("", ["Yes", "No"]) is None
    assert llm_answers._match_option("   ", ["Yes", "No"]) is None


def test_word_match_only_when_unambiguous():
    # Unique whole-word hit is accepted...
    assert llm_answers._match_option("5", ["3-4 years", "5+ years"]) == "5+ years"
    # ...an ambiguous one is not.
    assert llm_answers._match_option("5", ["3-5 years", "5-7 years"]) is None


def test_parse_json_requires_an_object():
    assert llm_answers._parse_json('{"answer": "x"}') == {"answer": "x"}
    assert llm_answers._parse_json('["x"]') is None
    assert llm_answers._parse_json('"unknown"') is None
    assert llm_answers._parse_json('prose {"answer": null} more') == {"answer": None}


def _run_with_stubs(complete_returns, tmp: Path):
    """Drive llm_answer with a stubbed LLM and an isolated cache file."""
    calls = {"n": 0}

    def _stub(cfg, system, user, max_tokens=400):
        calls["n"] += 1
        return complete_returns

    profile = tmp / "profile.md"
    profile.write_text("5 years of AppSec experience.", encoding="utf-8")
    cfg = {"answers": {"llm_fallback": {"enabled": True}},
           "cover_letter": {"profile_path": str(profile)}}

    orig_complete = llm_answers.llm_client.complete
    orig_cache = llm_answers._CACHE_PATH
    llm_answers.llm_client.complete = _stub
    llm_answers._CACHE_PATH = tmp / "cache.json"
    try:
        first = llm_answers.llm_answer("Years of AppSec experience?", None, cfg)
        second = llm_answers.llm_answer("Years of AppSec experience?", None, cfg)
    finally:
        llm_answers.llm_client.complete = orig_complete
        llm_answers._CACHE_PATH = orig_cache
    return first, second, calls["n"]


def test_transient_failure_is_not_cached():
    with tempfile.TemporaryDirectory() as d:
        first, second, n = _run_with_stubs(None, Path(d))  # LLM down
        assert first is None and second is None
        assert n == 2, f"failure was cached — LLM called {n} time(s), expected 2"


def test_success_is_cached():
    with tempfile.TemporaryDirectory() as d:
        first, second, n = _run_with_stubs('{"answer": "5", "reason": "profile"}', Path(d))
        assert first == "5" and second == "5"
        assert n == 1, f"success not cached — LLM called {n} time(s), expected 1"


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
