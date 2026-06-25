"""Pure-python unit tests for src/linkedin/external_apply.py — no browser.

Covers: ATS classification by URL, label cleaning, and the answer-resolution
fallback chain over FieldSpec descriptors (config maps -> llm -> default_positive
-> always_fill), with the LLM stubbed out.

Run:   .venv/bin/python -m pytest tests/ -q     (if pytest is installed)
or:    .venv/bin/python tests/test_external_apply.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.linkedin.external_apply import (  # noqa: E402
    FieldSpec,
    classify_ats,
    clean_label,
    resolve_answer,
)


def _cfg(**answer_overrides) -> dict:
    answers = {
        "experience_years": {"default": 5, "overrides": {"python": 3}},
        "yes_no": {"require sponsorship": True, "background check": True},
        "salary": {"annual_eur": 90000, "hourly_eur": 60, "sek_monthly": 50000,
                   "overrides": {"usd": 120000}},
        "text": {"notice period": "1 month",
                 "level of english": "Full professional"},
        "dropdown_default": "",
        "default_positive": True,
        "always_fill": True,
        "text_fallback": "Covered in my CV.",
        "llm_fallback": {"enabled": False},
    }
    answers.update(answer_overrides)
    return {
        "answers": answers,
        "applicant": {
            "first_name": "Jane", "last_name": "Doe",
            "email": "jane.doe@example.com", "phone": "5551234567",
            "phone_country_code": "United States (+1)", "city": "Remote",
            "country": "United States", "address": "Remote", "postal_code": "",
            "linkedin_url": "https://www.linkedin.com/in/jane-doe",
        },
        "safety": {"dry_run": True},
    }


def _no_llm(question, options, cfg):
    return None


def _llm_must_not_be_called(question, options, cfg):
    raise AssertionError(f"llm called for {question!r} — should not happen here")


# --- ATS classification --------------------------------------------------------
def test_classify_tractable():
    cases = {
        "https://boards.greenhouse.io/acme/jobs/123": "greenhouse",
        "https://job-boards.greenhouse.io/acme/jobs/123?gh_src=x": "greenhouse",
        "https://jobs.lever.co/acme/uuid-here/apply": "lever",
        "https://jobs.ashbyhq.com/acme/uuid/application": "ashby",
        "https://acme.teamtailor.com/jobs/123-appsec-engineer": "teamtailor",
        "https://acme.recruitee.com/o/security-engineer": "recruitee",
        "https://apply.workable.com/acme/j/ABC123/": "workable",
    }
    for url, ats in cases.items():
        kind, name = classify_ats(url)
        assert kind == "tractable", f"{url} -> {kind}"
        assert name == ats, f"{url} -> {name}"


def test_classify_deferred():
    cases = {
        "https://acme.wd3.myworkdayjobs.com/en-US/careers/job/x": "workday",
        "https://acme.workday.com/careers": "workday",
        "https://jobs.smartrecruiters.com/Acme/123-security-engineer": "smartrecruiters",
        "https://careers-acme.icims.com/jobs/123/job": "icims",
        "https://career5.successfactors.com/sfcareer/jobreqcareer?x=1": "successfactors",
        "https://acme.taleo.net/careersection/jobdetail.ftl": "taleo",
    }
    for url, ats in cases.items():
        kind, name = classify_ats(url)
        assert kind == "deferred", f"{url} -> {kind}"
        assert name == ats, f"{url} -> {name}"


def test_classify_unknown():
    kind, name = classify_ats("https://careers.example.com/jobs/42/apply")
    assert kind == "unknown"
    assert name == "careers.example.com"


def test_classify_no_lookalike_false_positives():
    # suffix matching is anchored at a dot — lookalike domains stay unknown
    assert classify_ats("https://notgreenhouse.io/x")[0] == "unknown"
    assert classify_ats("https://xboards.greenhouse.io.evil.com/x")[0] == "unknown"
    assert classify_ats("https://jobs.lever.co.evil.com/x")[0] == "unknown"
    assert classify_ats("")[0] == "unknown"


# --- Label cleaning --------------------------------------------------------------
def test_clean_label_dedup_and_required_line():
    raw = "First name\nFirst name\nRequired"
    assert clean_label(raw) == "First name"


def test_clean_label_suffix_noise():
    assert clean_label("Email address *") == "Email address"
    assert clean_label("Phone (optional)") == "Phone"
    assert clean_label("Cover letter (Optional) *") == "Cover letter"
    assert clean_label("") == ""


# --- Answer chain: tier 1 (config maps / applicant profile) ----------------------
def test_applicant_fields():
    cfg = _cfg()
    cases = {
        "First name": "Jane",
        "Last name": "Doe",
        "E-mail address": "jane.doe@example.com",
        "Phone number": "5551234567",
        "Current city": "Remote",
        "LinkedIn profile": "https://www.linkedin.com/in/jane-doe",
    }
    for label, want in cases.items():
        spec = FieldSpec(label=label, kind="text", required=True)
        got = resolve_answer(spec, cfg, llm=_llm_must_not_be_called)
        assert got == want, f"{label}: {got!r} != {want!r}"


def test_full_name_country_address_resolution():
    # Regression: these external-ATS labels used to skip jobs (2026-06-24 run).
    cfg = _cfg()
    cases = {
        "Full name": "Jane Doe",
        "Full Name": "Jane Doe",
        "Legal name": "Jane Doe",
        "Name": "Jane Doe",
        "Country": "United States",
        "Address": "Remote",
    }
    for label, want in cases.items():
        spec = FieldSpec(label=label, kind="text", required=True)
        got = resolve_answer(spec, cfg, llm=_llm_must_not_be_called)
        assert got == want, f"{label}: {got!r} != {want!r}"


def test_name_mapping_no_false_positives():
    # "Company name" / "Email address" must NOT resolve to the candidate's name.
    cfg = _cfg()
    spec = FieldSpec(label="Company name", kind="text", required=False)
    assert resolve_answer(spec, cfg, llm=_no_llm) is None
    spec = FieldSpec(label="Email address", kind="text", required=True)
    assert resolve_answer(spec, cfg, llm=_llm_must_not_be_called) == "jane.doe@example.com"


def test_type_hint_beats_unknown_label():
    cfg = _cfg()
    spec = FieldSpec(label="Din e-postadress", kind="text", input_type="email", required=True)
    assert resolve_answer(spec, cfg, llm=_llm_must_not_be_called) == "jane.doe@example.com"
    spec = FieldSpec(label="", kind="text", input_type="tel", required=True)
    assert resolve_answer(spec, cfg, llm=_llm_must_not_be_called) == "5551234567"


def test_salary_answer_sek_monthly():
    cfg = _cfg()
    spec = FieldSpec(label="Expected salary (SEK per month)", kind="text", required=True)
    assert resolve_answer(spec, cfg, llm=_llm_must_not_be_called) == "50000"


def test_numeric_experience_from_config():
    cfg = _cfg()
    spec = FieldSpec(label="How many years of experience do you have with Python?",
                     kind="text", input_type="number", required=True)
    assert resolve_answer(spec, cfg, llm=_llm_must_not_be_called) == "3"


# --- Answer chain: tier 2 (LLM for required unknowns) -----------------------------
def test_llm_tier_used_for_required_unknown():
    cfg = _cfg()
    spec = FieldSpec(label="Describe your SAST tooling philosophy", kind="textarea", required=True)
    got = resolve_answer(spec, cfg, llm=lambda q, o, c: "Shift-left, low-noise gates.")
    assert got == "Shift-left, low-noise gates."


def test_llm_not_consulted_for_optional_fields():
    cfg = _cfg()
    spec = FieldSpec(label="Anything completely unmatchable here", kind="text", required=False)
    # optional + unknown -> None (skip), even with always_fill on; llm untouched
    assert resolve_answer(spec, cfg, llm=_llm_must_not_be_called) is None


# --- Answer chain: tier 3/4 (default_positive, always_fill) -----------------------
def test_default_positive_for_required_yes_no_select():
    cfg = _cfg()
    spec = FieldSpec(label="Are you OK with periodic on-call?", kind="select",
                     options=["Select an option", "Yes", "No"], required=True)
    assert resolve_answer(spec, cfg, llm=_no_llm) == "Yes"


def test_always_fill_first_option_when_no_positive():
    cfg = _cfg()
    spec = FieldSpec(label="Preferred office", kind="select",
                     options=["Välj...", "Stockholm", "Göteborg"], required=True)
    assert resolve_answer(spec, cfg, llm=_no_llm) == "Stockholm"


def test_required_select_skipped_when_rails_off():
    cfg = _cfg(default_positive=False, always_fill=False)
    spec = FieldSpec(label="Preferred office", kind="select",
                     options=["Stockholm", "Göteborg"], required=True)
    assert resolve_answer(spec, cfg, llm=_no_llm) is None


def test_always_fill_text_fallback():
    cfg = _cfg()
    spec = FieldSpec(label="Quantum basket weaving certification ID", kind="text", required=True)
    assert resolve_answer(spec, cfg, llm=_no_llm) == "Covered in my CV."


def test_number_input_never_gets_text_fallback():
    cfg = _cfg(experience_years={})   # no numeric default available
    spec = FieldSpec(label="Employee referral code", kind="text",
                     input_type="number", required=True)
    # nothing numeric to say -> None (defer), never "Covered in my CV." into a number box
    assert resolve_answer(spec, cfg, llm=_no_llm) is None


# --- Choice fields (radio/select details) ------------------------------------------
def test_radio_yes_no_from_config_map():
    cfg = _cfg()
    spec = FieldSpec(label="Will you require sponsorship for employment?", kind="radio",
                     options=["Yes", "No"], required=True)
    assert resolve_answer(spec, cfg, llm=_llm_must_not_be_called) == "Yes"


def test_language_level_select():
    cfg = _cfg()
    spec = FieldSpec(label="What is your proficiency in English?", kind="select",
                     options=["Basic", "Conversational", "Full professional", "Native"],
                     required=False)
    assert resolve_answer(spec, cfg, llm=_llm_must_not_be_called) == "Full professional"


def test_phone_country_code_select():
    cfg = _cfg()
    spec = FieldSpec(label="Country code", kind="select",
                     options=["Select", "United Kingdom (+44)", "Germany (+49)", "United States (+1)"],
                     required=True)
    assert resolve_answer(spec, cfg, llm=_no_llm) == "United States (+1)"


def test_placeholder_options_never_chosen():
    cfg = _cfg()
    spec = FieldSpec(label="Totally unknown dropdown", kind="select",
                     options=["Select an option", "-- choose --"], required=True)
    assert resolve_answer(spec, cfg, llm=_no_llm) is None


# --- Checkboxes ----------------------------------------------------------------------
def test_checkbox_required_ticked_optional_skipped():
    cfg = _cfg()
    req = FieldSpec(label="I agree to the privacy policy", kind="checkbox", required=True)
    opt = FieldSpec(label="Subscribe to job alerts", kind="checkbox", required=False)
    assert resolve_answer(req, cfg, llm=_llm_must_not_be_called) is True
    assert resolve_answer(opt, cfg, llm=_llm_must_not_be_called) is None


# --- __main__ smoke runner (pytest not required) ---------------------------------------
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
