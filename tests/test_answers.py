"""Unit tests for src/linkedin/answers.py — the config-map question resolver.

Two silent misfires these tests pin down (found by replaying real
"unanswered required question" skips against a config):

* salary_answer matched "rate" as a bare substring, so a free-text label like
  "how you integrate security into CI/CD" or "years you have operated a SOC"
  produced the salary figure instead of falling through.
* text_answer routed ANY label mentioning "experience" (in eight languages) to
  numeric_answer, which returns the years default for everything — so a
  descriptive text field ("Describe your experience with SAST tools") was
  answered with a number. Only a genuine quantity cue (how many / years /
  años / Jahre / år / lat / …) should route to the years map.

All figures below are synthetic fixtures.

Run:  python -m pytest tests/test_answers.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.linkedin import answers as A  # noqa: E402


ANS = {
    "experience_years": {
        "default": 7,
        "overrides": {"python": 6, "aws": 4, "azure": 4, "threat model": 9,
                      "devsecops": 8},
    },
    "salary": {"annual_eur": 120000, "hourly_eur": 80, "sek_monthly": 70000,
               "overrides": {"gbp": 110000, "usd": 160000}},
    "yes_no": {},
    "text": {"notice period": "1 month"},
}


# --- salary: "rate" must be a whole word -----------------------------------------
def test_salary_ignores_rate_inside_other_words():
    for label in (
        "Describe how you integrate security into CI/CD pipelines",
        "How many years have you operated a SOC?",
        "Have you personally designed or helped mature a VM program (not just operated one)?",
        "Which corporate security frameworks have you worked with?",
        "Please demonstrate your approach to threat modelling",
    ):
        assert A.salary_answer(label, ANS) is None, label


def test_salary_still_matches_rate_as_a_word():
    assert A.salary_answer("What is your hourly rate (EUR)?", ANS) == "80"
    # GBP is pinned at 110000/yr -> the hourly/daily figures scale by 110/120.
    assert A.salary_answer("Expected daily rate in GBP", ANS) == "600"
    assert A.salary_answer("Your rates?", ANS) == "120000"
    assert A.salary_answer("Salary expectations (USD per year)", ANS) == "160000"


def test_salary_day_words_are_whole_words():
    # "tag" (German: Tagessatz / pro Tag) must not fire on "stage"/"advantage".
    assert A.salary_answer("Salary expectation for this stage of your career", ANS) == "120000"
    assert A.salary_answer("Gehalt pro Tag", ANS) == "650"


# --- text: a bare mention of "experience" is not a numeric question ---------------
def test_text_answer_does_not_treat_experience_mention_as_numeric():
    for label in (
        "Describe your experience with SAST tools",
        "Do you have experience with on-premises infrastructure?",
        "Tell us about a security product you built, and your experience releasing it",
        "Beschreiben Sie Ihre Erfahrung mit Kubernetes",
        "Descreva a sua experiência com pentesting",
    ):
        assert A.text_answer(label, ANS) is None, label
        assert not A.looks_numeric_question(label), label


def test_numeric_questions_still_routed_to_years_map():
    cases = {
        "How many years of work experience do you have with Python?": "6",
        "Years of experience with threat modelling": "9",
        "Experience with AWS (years)": "4",
        "How long have you worked with DevSecOps?": "8",
        "¿Cuántos años de experiencia tienes con AWS?": "4",
        "Wie viele Jahre Erfahrung haben Sie mit Azure?": "4",
        "Quanti anni di esperienza hai con AWS?": "4",
        "Quantos anos de experiência tem com Python?": "6",
        "Combien d'années d'expérience avec Python ?": "6",
        "Hoeveel jaar ervaring heb je met Python?": "6",
        "Hur många års erfarenhet har du av Python?": "6",
        "Ile lat doświadczenia masz w DevSecOps?": "8",
        "Anything at all with no override, how many years?": "7",
    }
    for label, want in cases.items():
        assert A.looks_numeric_question(label), label
        assert A.text_answer(label, ANS) == want, label


def test_calendar_year_is_not_a_years_of_experience_question():
    # A whole-word "year" cue used to match graduation/start year labels, so
    # the years default went in — and dropdown_answer's substring match then
    # picked a calendar year containing it. Such labels must not route to the
    # years map.
    for label in (
        "Graduation year",
        "What year did you graduate?",
        "Year of graduation",
        "Start year",
        "Which year did you complete your degree?",
        "In welchem Jahr haben Sie Ihren Abschluss gemacht?",
        "Vilket år tog du examen?",
        "In welk jaar ben je afgestudeerd?",
        "¿En qué año te graduaste?",
    ):
        assert not A.looks_numeric_question(label), label
        assert A.text_answer(label, ANS) is None, label
    assert A.dropdown_answer("Graduation year", ["2009", "2011", "2015"], ANS) is None


def test_how_many_without_years_or_experience_is_not_numeric():
    # A count question is not a years question — the years default is a
    # fabrication there. (Spanish case seen in the LLM cache; the English one
    # is the same shape.)
    for label in (
        "How many certifications do you currently hold?",
        "¿Cuántas certificaciones de este lote tienes en vigor actualmente?",
        "¿Cuánto conocimiento tienes en herramientas de inteligencia artificial?",
        "For how long have you been an AI security architect?",
        "How long would you describe your leadership style?",
    ):
        assert not A.looks_numeric_question(label), label
    # …but "how long have you used/worked with X" is one.
    assert A.text_answer("How long have you used Python?", ANS) == "6"


def test_no_experience_default_means_no_number():
    """With experience_years.default left empty, an unknown skill is not
    answered with a made-up number (the LLM / skip chain applies)."""
    ans = {"experience_years": {"default": None, "overrides": {"python": 6}}, "text": {}}
    assert A.text_answer("How many years of experience do you have with neurosurgery?", ans) is None
    assert A.text_answer("How many years of experience with Python?", ans) == "6"


# --- languages: only what answers.languages states -------------------------------
LANGS = {"languages": {"english": "fluent", "german": "basic", "french": "none"}}
LEVELS = ["Native or bilingual", "Full professional", "Professional working",
          "Limited working", "Elementary", "None"]


def test_language_level_never_overclaims():
    assert A.language_level_answer("English proficiency", LEVELS, LANGS) == "Full professional"
    assert A.language_level_answer("German level?", LEVELS, LANGS) == "Elementary"
    assert A.language_level_answer("Französisch", LEVELS, LANGS) == "None"
    # Options that only exist far above/below the configured level -> None.
    assert A.language_level_answer("English", ["Native", "None"], LANGS) is None
    assert A.language_level_answer("English", ["Native", "Intermediate", "None"], LANGS) is None


def test_language_not_configured_is_unanswered():
    assert A.language_level_answer("Spanish level", LEVELS, LANGS) is None
    assert A.language_level_answer("English level", LEVELS, {}) is None
    assert A.language_level_answer("English level", LEVELS, None) is None


# --- checkboxes: consent yes, declarations only when explicitly answered ---------
def test_checkbox_consent_is_ticked_declarations_are_not():
    yes_no = {"yes_no": {"driving licence": True, "willing to relocate": False}}
    assert A.checkbox_answer("I agree to the privacy policy", yes_no) is True
    assert A.checkbox_answer("I consent to the processing of my personal data", yes_no) is True
    assert A.checkbox_answer("", yes_no) is True                       # unlabelled box
    assert A.checkbox_answer("I am a United States citizen", yes_no) is None
    assert A.checkbox_answer("I am authorized to work in Canada", yes_no) is None
    assert A.checkbox_answer("I confirm I hold a security clearance", yes_no) is None
    assert A.checkbox_answer("I hold a valid driving licence", yes_no) is True
    assert A.checkbox_answer("I am willing to relocate", yes_no) is False
    assert A.checkbox_answer("Some unknown statement", yes_no) is None


# --- country normalisation and regional rights -----------------------------------
def test_canonical_country_and_demonyms():
    assert A.canonical_country("Sverige") == "sweden"
    assert A.canonical_country("USA") == "united states"
    assert A.canonical_country("Swedish") == "sweden"
    assert A.canonical_country("Narnia") == "narnia"
    assert A.demonyms_in("Only US citizens or Canadian residents") == {"united states", "canada"}


def test_work_authorization_accepts_aliases_and_regions():
    q = "Are you legally authorized to work in the United States?"
    assert A.work_authorization_answer(q, {"work_authorization": {"countries": ["USA"]}}, "Remote") is True
    eu = {"work_authorization": {"countries": [], "regions": ["European Union"]}}
    assert A.work_authorization_answer("Are you authorised to work in the EU?", eu, "Remote") is True
    assert A.work_authorization_answer("Are you authorised to work in Germany?", eu, "Remote") is True
    assert A.work_authorization_answer("Are you authorised to work in the United States?", eu, "Remote") is False
    one = {"work_authorization": {"countries": ["Sweden"]}}
    assert A.work_authorization_answer("Are you authorised to work in the EU?", one, "Remote") is False


def test_years_of_experience_phrasings_still_numeric():
    cases = {
        "Years' experience in application security": "7",
        "Experience (in years)": "7",
        "Python experience in years": "6",
        "Number of years using AWS": "4",
        "Years: Azure": "4",
        "Antal års erfarenhet av Python": "6",
        "Hvor mange års erfaring har du med AWS?": "4",
        "Liczba lat doświadczenia w DevSecOps": "8",
        "Années d'expérience avec Python": "6",
        "Jahre Erfahrung mit Azure": "4",
    }
    for label, want in cases.items():
        assert A.looks_numeric_question(label), label
        assert A.text_answer(label, ANS) == want, label


def test_text_map_still_wins_for_mapped_labels():
    assert A.text_answer("If you are currently working, what is your notice period?", ANS) == "1 month"


def test_explicit_text_mapping_beats_numeric_routing():
    # "How long" is a quantity cue, but the user mapped this label explicitly.
    assert A.text_answer("How long is your notice period?", ANS) == "1 month"


# --- work authorisation: deterministic, from the job's country -------------------
# Design chosen 2026-08-29 after a Codex/Grok split: the LLM never sees the job
# location (prompt-injection surface); the answer is computed from the parsed
# country against answers.work_authorization.countries, and left alone when the
# country is unknown.
AUTH = {**ANS, "yes_no": {"authorized to work": True, "require sponsorship": True},
        "work_authorization": {"countries": ["Sweden"]}}

POSITIVE_Q = "Do you currently have the legal right to work in the country where you are applying without requiring visa sponsorship now or in the future?"
NEGATIVE_Q = "Will you now or in the future require employment visa sponsorship to work in the country in which the job you're applying for is located?"


def test_work_auth_positive_form_follows_job_country():
    assert A.work_authorization_answer(POSITIVE_Q, AUTH, "Stockholm, Stockholm County, Sweden") is True
    assert A.work_authorization_answer(POSITIVE_Q, AUTH, "Sweden") is True
    assert A.work_authorization_answer(POSITIVE_Q, AUTH, "United States") is False
    assert A.work_authorization_answer(POSITIVE_Q, AUTH, "Warsaw, Mazowieckie, Poland") is False
    assert A.work_authorization_answer("Are you eligible to work in the country you're applying to?", AUTH, "Berlin, Germany") is False


def test_work_auth_negative_form_is_inverted():
    assert A.work_authorization_answer(NEGATIVE_Q, AUTH, "Sweden") is False
    assert A.work_authorization_answer(NEGATIVE_Q, AUTH, "United States") is True
    assert A.work_authorization_answer("Do you require a sponsorship for this role?", AUTH, "Lisbon, Portugal") is True
    assert A.work_authorization_answer("Você precisa ou precisará de patrocínio para obter um visto de trabalho?", AUTH, "Lisboa, Portugal") is True
    assert A.work_authorization_answer("Do you need a work permit to work in Sweden?", AUTH, "Remote") is False


def test_work_auth_country_named_in_question_beats_job_location():
    assert A.work_authorization_answer("Are you legally authorized to work in the United States?", AUTH, "Sweden") is False
    assert A.work_authorization_answer("Are you authorised to work in the UK?", AUTH, "Malmö, Sweden") is False
    assert A.work_authorization_answer("Do you have a valid work permit for Sweden?", AUTH, "United States") is True
    # A region is not a country: the permit is Sweden-specific (profile), so EU-wide rights are a No.
    assert A.work_authorization_answer("Are you authorised to work in the EU without sponsorship?", AUTH, "Sweden") is False
    # Two countries with different answers -> ambiguous -> None.
    assert A.work_authorization_answer("Are you authorised to work in Sweden or Germany?", AUTH, "Sweden") is None


def test_work_auth_unknown_country_and_off_topic_return_none():
    for loc in ("Remote", "European Union", "EMEA", "", None, "Nordics"):
        assert A.work_authorization_answer(POSITIVE_Q, AUTH, loc) is None, loc
    assert A.work_authorization_answer("Are you comfortable working in a hybrid setting?", AUTH, "United States") is None
    assert A.work_authorization_answer("Do you have experience with SAST?", AUTH, "United States") is None
    # Feature off when no countries are configured.
    assert A.work_authorization_answer(POSITIVE_Q, ANS, "United States") is None
    # A pronoun is not a country code.
    assert A.work_authorization_answer("Are you authorised to work where you'd be joining us?", AUTH, "Sweden") is True


def test_work_auth_refuses_negated_compound_and_mixed_scope_questions():
    # Codex review 2026-08-29: each of these would otherwise send a false Yes.
    assert A.work_authorization_answer("Are you not authorised to work in Sweden?", AUTH, "Sweden") is None
    assert A.work_authorization_answer("Do you lack the right to work in Sweden?", AUTH, "Sweden") is None
    assert A.work_authorization_answer(
        "Do you have the right to work in the United States, or would you require sponsorship?", AUTH, "Sweden") is None
    assert A.work_authorization_answer("Are you authorised to work in Sweden and anywhere in the EU?", AUTH, "Sweden") is None
    # …while the common "without requiring sponsorship" tail stays positive.
    assert A.work_authorization_answer(POSITIVE_Q, AUTH, "Sweden") is True
    # A negation elsewhere in the sentence is not a negated question.
    assert A.work_authorization_answer(
        "Applicants must already have the legal right to work in the UK, as we are unable to offer sponsorship.",
        AUTH, "Remote") is False
    assert A.work_authorization_answer(
        "Are you legally authorised to work in Sweden? We do not sponsor visas.", AUTH, "Remote") is True
    assert A.work_authorization_answer("Are you authorized to work in non-EU countries?", AUTH, "Sweden") is False
    assert A.countries_in("Are you authorised to work in the indie games industry?") == set()


def test_work_auth_country_aliases_are_conservative():
    assert A.countries_in("Seoul, South Korea") == {"south korea"}
    assert A.countries_in("Remote — Korea") == set()
    assert A.countries_in("Pyongyang, North Korea") == set()
    assert A.countries_in("Contact us about the role") == set()
    assert A.countries_in("Stockholm, Sverige") == {"sweden"}
    # Lower-case "eu" is a Portuguese/French word, not the European Union.
    assert A.work_authorization_answer("Eu tenho autorização de trabalho em Portugal?", AUTH, "Lisboa, Portugal") is False
    assert A.work_authorization_answer("Avez-vous eu un permis de travail en Suède ?", AUTH, "Remote") is True
    assert A.countries_in("Berlin, Deutschland") == {"germany"}


def test_work_auth_other_languages():
    assert A.work_authorization_answer("Avez-vous le droit de travailler aux États-Unis sans parrainage de visa ?", AUTH, "Sweden") is False
    assert A.work_authorization_answer("Har du rätt att arbeta i Sverige?", AUTH, "Remote") is True
    assert A.work_authorization_answer("Hai il diritto di lavorare in Italia?", AUTH, "Milano, Italia") is False
    assert A.work_authorization_answer("Heb je een werkvergunning voor Nederland?", AUTH, "Amsterdam, Nederland") is False
    assert A.work_authorization_answer("Czy masz prawo do pracy w Polsce?", AUTH, "Warszawa, Polska") is False
    assert A.work_authorization_answer("Benötigen Sie ein Visum oder Sponsoring, um in Deutschland zu arbeiten?", AUTH, "Berlin, Deutschland") is True


def test_country_of_location_resolves_linkedins_country_less_forms():
    # LinkedIn drops the country for many listings ("Lisbon (Hybrid)",
    # "Greater Stockholm Metropolitan Area", "Pittsburgh, PA (Remote)"); these
    # are geography, not candidate facts, so they may be resolved deterministically.
    cases = {
        "Stockholm, Stockholm County, Sweden": "sweden",
        "Greater Stockholm Metropolitan Area": "sweden",
        "Malmö (Hybrid)": "sweden",
        "Lisbon (Hybrid)": "portugal",
        "Oeiras (Hybrid)": "portugal",
        "Frankfurt am Main (Hybrid)": "germany",
        "Frankfurt Rhine-Main Metropolitan Area": "germany",
        "Amersfoort (Hybrid)": "netherlands",
        "Lodz Metropolitan Area (Hybrid)": "poland",
        "Belfast (Remote)": "united kingdom",
        "Pittsburgh, PA (Remote)": "united states",
        "Toronto, ON (Hybrid)": "canada",
        "Greater Rome Metropolitan Area (Remote)": "italy",
        "Bengaluru, Karnataka": "india",
    }
    for loc, want in cases.items():
        assert A.country_of_location(loc) == want, loc
    for loc in ("European Union (Remote)", "European Economic Area (Remote)", "EMEA (Remote)",
                "Remote", "", None, "Cambridge (Hybrid)", "Santiago (Remote)", "Nordics"):
        assert A.country_of_location(loc) is None, loc


def test_work_auth_uses_city_resolved_country():
    assert A.work_authorization_answer(POSITIVE_Q, AUTH, "Lisbon (Hybrid)") is False
    assert A.work_authorization_answer(POSITIVE_Q, AUTH, "Greater Stockholm Metropolitan Area") is True
    assert A.work_authorization_answer(NEGATIVE_Q, AUTH, "Pittsburgh, PA (Remote)") is True
    assert A.work_authorization_answer(POSITIVE_Q, AUTH, "Cambridge (Hybrid)") is None


def test_yes_no_answer_uses_job_country_before_the_blanket_map():
    assert A.yes_no_answer("Are you authorized to work in the country of this job?", AUTH, "United States") is False
    assert A.yes_no_answer("Are you authorized to work in the country of this job?", AUTH, "Sweden") is True
    # Unknown country: the user's explicit map still applies (documented behaviour).
    assert A.yes_no_answer("Are you authorized to work in the country of this job?", AUTH, "Remote") is True
    assert A.yes_no_answer("Are you authorized to work in the country of this job?", AUTH) is True
    assert A.dropdown_answer(NEGATIVE_Q, ["Yes", "No"], AUTH, job_location="Sweden") == "No"
    assert A.dropdown_answer(NEGATIVE_Q, ["Yes", "No"], AUTH, job_location="United States") == "Yes"


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
