"""Unit tests for the remote-work JD screening gate — no browser, no network
(the LLM transport is stubbed). The policy here is a Sweden-based example;
the module itself takes everything from config.

Run:  python -m pytest tests/test_jd_screen.py -q
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import jd_screen  # noqa: E402


@dataclass
class FakeCard:
    job_id: str = "1"
    title: str = "Senior AppSec Engineer"
    company: str = "Acme"
    location: str = "Remote"
    url: str = ""
    description: str = ""


CFG = {"jd_screen": {
    "enabled": True,
    "home_country": "Sweden",
    "allowed_regions": ["European Union", "Europe", "Nordics", "EMEA"],
    "own_company": "Example Consulting AB",
}}


def _fresh_cache():
    """Point the module cache at a throwaway temp file."""
    d = tempfile.mkdtemp()
    jd_screen._CACHE_PATH = Path(d) / "jd_screen.json"


def _stub_llm(monkeypatch, reply):
    """Replace the LLM transport; records calls. reply: str | None | callable.

    Through monkeypatch, so it is put back. `jd_screen.llm_client` is the shared
    module object, so assigning to it directly replaced `complete` for every
    test that ran afterwards in the same process — which is why the four tests
    in test_llm_client_codex.py passed alone and failed in the suite: they were
    exercising this stub.
    """
    calls = []

    def fake(cfg, system, user, max_tokens=300):
        calls.append(user)
        return reply(user) if callable(reply) else reply

    monkeypatch.setattr(jd_screen.llm_client, "complete", fake)
    return calls


def test_disabled_gate_applies():
    _fresh_cache()
    ok, why = jd_screen.screen(FakeCard(description="On-site in Houston, W2 only"), {})
    assert ok is True and "disabled" in why


def test_hard_skip_w2_only_and_green_card(monkeypatch):
    _fresh_cache()
    calls = _stub_llm(monkeypatch, None)  # must not be reached
    ok, why = jd_screen.screen(
        FakeCard(description="Contract role. W2 only, no C2C."), CFG)
    assert ok is False and "W-2" in why
    ok, why = jd_screen.screen(
        FakeCard(description="Applicants must hold a valid Green Card to be considered."), CFG)
    assert ok is False and "green card" in why
    assert calls == []


def test_hard_skip_residency_foreign_but_not_sweden_friendly(monkeypatch):
    _fresh_cache()
    calls = _stub_llm(monkeypatch, None)
    ok, why = jd_screen.screen(
        FakeCard(description="Fully remote, but you must be based in the United States."), CFG)
    assert ok is False and "United States" in why
    ok, why = jd_screen.screen(
        FakeCard(description="Remote (US residents only)."), CFG)
    assert ok is False and "residents only" in why
    assert calls == []


def test_residency_in_sweden_friendly_regions_reaches_llm(monkeypatch):
    _fresh_cache()
    calls = _stub_llm(monkeypatch, '{"apply": true, "reason": "remote in EU, Sweden qualifies"}')
    for region in ("the EU", "Europe", "the Nordics", "Sweden", "EMEA"):
        ok, _ = jd_screen.screen(
            FakeCard(job_id=region, description=f"Remote role. You must reside in {region}."), CFG)
        assert ok is True, region
    assert len(calls) == 5  # allowlist let them through to the LLM


def test_llm_skip_verdict_and_cache(monkeypatch):
    _fresh_cache()
    calls = _stub_llm(monkeypatch, '{"apply": false, "reason": "hybrid, 3 office days"}')
    card = FakeCard(job_id="77", description="Hybrid: 3 days/week in our Berlin office.")
    ok, why = jd_screen.screen(card, CFG)
    assert ok is False and "hybrid" in why
    # Second call must come from the cache, not the LLM.
    ok2, _ = jd_screen.screen(card, CFG)
    assert ok2 is False
    assert len(calls) == 1


def test_llm_failure_fails_open_and_is_not_cached(monkeypatch):
    _fresh_cache()
    calls = _stub_llm(monkeypatch, None)
    card = FakeCard(job_id="88", description="Remote role, unclear residency wording.")
    ok, why = jd_screen.screen(card, CFG)
    assert ok is True and "fail-open" in why
    ok2, _ = jd_screen.screen(card, CFG)  # still not cached — asks again
    assert ok2 is True
    assert len(calls) == 2


def test_garbage_verdict_fails_open(monkeypatch):
    _fresh_cache()
    _stub_llm(monkeypatch, "I think you should apply, good luck!")
    ok, why = jd_screen.screen(FakeCard(job_id="99", description="Remote."), CFG)
    assert ok is True and "fail-open" in why


def test_empty_description_is_ambiguous_apply_without_llm(monkeypatch):
    _fresh_cache()
    calls = _stub_llm(monkeypatch, None)
    ok, why = jd_screen.screen(FakeCard(description=""), CFG)
    assert ok is True and "ambiguous" in why
    assert calls == []


def test_prompt_carries_policy_and_jd(monkeypatch):
    _fresh_cache()
    calls = _stub_llm(monkeypatch, '{"apply": true, "reason": "ok"}')
    jd_screen.screen(FakeCard(job_id="p", description="Some JD text here."), CFG)
    assert "Some JD text here." in calls[0]
    prompt = jd_screen.system_prompt(CFG["jd_screen"])
    assert "Example Consulting AB" in prompt
    assert "Sweden" in prompt
    # Occasional business travel must stay allowed.
    assert "occasional business travel" in prompt


def test_policy_comes_from_config(monkeypatch):
    """A different home country flips which residency demands are hard skips."""
    _fresh_cache()
    calls = _stub_llm(monkeypatch, '{"apply": true, "reason": "ok"}')
    cfg = {"jd_screen": {"enabled": True, "home_country": "Germany",
                         "allowed_regions": ["Europe"]}}
    ok, why = jd_screen.screen(
        FakeCard(job_id="a", description="Remote, but you must be based in Sweden."), cfg)
    assert ok is False and "Sweden" in why
    ok, _ = jd_screen.screen(
        FakeCard(job_id="b", description="Remote. You must reside in Germany."), cfg)
    assert ok is True
    ok, _ = jd_screen.screen(
        FakeCard(job_id="c", description="Remote (German residents only)."), cfg)
    assert ok is True
    assert len(calls) == 2
    prompt = jd_screen.system_prompt(cfg["jd_screen"])
    assert "Germany" in prompt and "Sweden" not in prompt
    # No own company configured -> no contracting clause in the policy.
    assert "B2B" not in prompt and "invoicing" not in prompt


def test_home_country_accepts_native_names_and_abbreviations():
    for spelling in ("Sverige", "SWEDEN", "swedish"):
        pol = jd_screen.policy({"enabled": True, "home_country": spelling})
        assert pol.allowed_places.search("Swedish residents only"), spelling
        assert pol.allowed_places.search("must be based in Sweden"), spelling
    pol = jd_screen.policy({"enabled": True, "home_country": "USA"})
    assert pol.allowed_places.search("United States residents only")
    assert pol.allowed_places.search("must be based in the US")


def test_short_demonyms_are_whole_words():
    """'us' for the United States must not make 'Austin' an allowed place."""
    cfg = {"jd_screen": {"enabled": True, "home_country": "United States"}}
    pol = jd_screen.policy(cfg["jd_screen"])
    assert pol.allowed_places.search("US residents only")
    assert pol.allowed_places.search("must be based in the U.S.")
    assert not pol.allowed_places.search("Austin, TX")
    assert not pol.allowed_places.search("campus")


def test_enabled_without_home_country_is_a_config_error():
    import pytest
    with pytest.raises(ValueError):
        jd_screen.policy({"enabled": True})


def test_us_candidate_is_not_hard_skipped_on_w2_or_green_card(monkeypatch):
    _fresh_cache()
    calls = _stub_llm(monkeypatch, '{"apply": true, "reason": "ok"}')
    us = {"jd_screen": {"enabled": True, "home_country": "United States"}}
    ok, _ = jd_screen.screen(FakeCard(job_id="w2", description="Contract role. W2 only."), us)
    assert ok is True and len(calls) == 1
    assert "W-2" not in jd_screen.system_prompt(us["jd_screen"])


def test_unresolvable_places_go_to_the_llm_not_a_hard_skip(monkeypatch):
    _fresh_cache()
    calls = _stub_llm(monkeypatch, '{"apply": true, "reason": "ok"}')
    de = {"jd_screen": {"enabled": True, "home_country": "Germany"}}
    # A city of the home country, and a place we cannot resolve: no hard skip.
    for jid, text in (("b", "You must be based in Berlin."),
                      ("hq", "You must be based in our HQ region.")):
        ok, why = jd_screen.screen(FakeCard(job_id=jid, description=text), de)
        assert ok is True, (jid, why)
    assert len(calls) == 2
    # A resolvable foreign city IS a hard skip.
    ok, why = jd_screen.screen(FakeCard(job_id="p", description="You must be based in Paris."), de)
    assert ok is False and "Paris" in why


def test_every_residency_clause_is_inspected_and_pronoun_us_is_ignored():
    de = jd_screen.policy({"enabled": True, "home_country": "Germany"})
    hit = jd_screen._hard_skip(
        "You must be based in Germany. Candidates must reside in France.", de)
    assert hit == "must reside in France"
    us = jd_screen.policy({"enabled": True, "home_country": "United States"})
    assert jd_screen._hard_skip("You must be based in Canada and work with us.", us) \
        == "must reside in Canada"


def test_cache_key_covers_the_location_and_policy_tag_format(monkeypatch):
    _fresh_cache()
    calls = _stub_llm(monkeypatch, '{"apply": true, "reason": "ok"}')
    card = FakeCard(job_id="loc", location="Germany", description="Remote role.")
    jd_screen.screen(card, CFG)
    card.location = "France"
    jd_screen.screen(card, CFG)
    assert len(calls) == 2   # a changed location is a new question, not a cache hit
    tag = jd_screen.policy_tag(CFG)
    assert tag.startswith("jd screen[") and tag.endswith("]") and len(tag) == len("jd screen[]") + 8
    assert jd_screen.policy_tag({}) == "jd screen"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
