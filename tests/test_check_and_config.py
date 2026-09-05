"""Unit tests for config validation (src/config.py) and the --check readiness
report (src/main.check_problems) — no browser, no network.

Run:  python -m pytest tests/test_check_and_config.py -q
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config as config_mod  # noqa: E402
from src import main as main_mod  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def example_cfg(tmp_path):
    """The shipped example config, with a real (placeholder) PDF and profile."""
    cfg = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%placeholder\n")
    profile = tmp_path / "profile.md"
    profile.write_text("Name: Jane Doe\n", encoding="utf-8")
    cfg["applicant"]["resume_path"] = str(pdf)
    cfg["cover_letter"]["profile_path"] = str(profile)
    return cfg


def _validate(cfg):
    config_mod._validate(copy.deepcopy(cfg))


def test_example_config_validates(example_cfg):
    _validate(example_cfg)


def test_quoted_false_in_yes_no_is_rejected(example_cfg):
    example_cfg["answers"]["yes_no"]["willing to relocate"] = "false"
    with pytest.raises(SystemExit, match="yes_no"):
        _validate(example_cfg)


def test_bare_string_lists_are_rejected(example_cfg):
    for path, value in (("search.locations", "Germany"),
                        ("jd_screen.allowed_regions", "EU"),
                        ("cover_letter.claim_skills", "Java")):
        cfg = copy.deepcopy(example_cfg)
        section, key = path.split(".")
        cfg[section][key] = value
        with pytest.raises(SystemExit, match=path):
            _validate(cfg)


def test_yaml_off_becomes_the_off_mode(example_cfg):
    example_cfg["cover_letter"]["mode"] = False   # what YAML makes of a bare `off`
    cfg = copy.deepcopy(example_cfg)
    config_mod._validate(cfg)
    assert cfg["cover_letter"]["mode"] == "off"
    example_cfg["cover_letter"]["mode"] = "letter"
    with pytest.raises(SystemExit, match="cover_letter.mode"):
        _validate(example_cfg)


def test_wrong_types_are_rejected_with_the_key_named(example_cfg):
    cases = (
        (("browser", "timezone"), 123, "browser.timezone"),
        (("safety", "max_applications_per_day"), "20", "max_applications_per_day"),
        (("safety", "dry_run"), "yes", "safety.dry_run"),
        (("answers", "languages"), {"english": 5}, "languages"),
        (("answers", "experience_years"), {"default": "five"}, "experience_years.default"),
    )
    for keys, value, needle in cases:
        cfg = copy.deepcopy(example_cfg)
        cfg[keys[0]][keys[1]] = value
        with pytest.raises(SystemExit, match=needle):
            _validate(cfg)


def test_missing_cv_is_a_clear_error(example_cfg):
    example_cfg["applicant"]["resume_path"] = "nowhere/cv.pdf"
    with pytest.raises(SystemExit, match="CV was not found"):
        _validate(example_cfg)


# --- --check -------------------------------------------------------------------------
def test_check_flags_every_unfilled_example_value(example_cfg):
    problems = "\n".join(main_mod.check_problems(example_cfg))
    for needle in ("first_name", "last_name", "email", "phone", "linkedin_url",
                   "Jane Doe", "work_authorization"):
        assert needle in problems, needle
    example_cfg["answers"]["languages"] = {}
    assert any("languages" in p for p in main_mod.check_problems(example_cfg))


def test_check_passes_a_filled_in_config(example_cfg, tmp_path):
    a = example_cfg["applicant"]
    a.update(first_name="Alex", last_name="Smith", email="alex@smith.example",
             phone="4791234567", linkedin_url="https://www.linkedin.com/in/alex-smith")
    Path(example_cfg["cover_letter"]["profile_path"]).write_text(
        "Name: Alex Smith\nProject manager with nine years in construction, "
        "leading teams of up to forty people.\n", encoding="utf-8")
    example_cfg["answers"]["work_authorization"]["countries"] = ["Norway"]
    example_cfg["answers"]["languages"] = {"english": "fluent"}
    assert main_mod.check_problems(example_cfg) == []


def test_check_rejects_a_cv_that_is_not_a_pdf(example_cfg):
    Path(example_cfg["applicant"]["resume_path"]).write_bytes(b"not a pdf")
    assert any("not a PDF" in p for p in main_mod.check_problems(example_cfg))


def test_check_needs_each_name_field_separately(example_cfg):
    example_cfg["applicant"]["first_name"] = "Alex"
    example_cfg["applicant"]["last_name"] = ""
    assert any("last_name" in p for p in main_mod.check_problems(example_cfg))


def test_a_real_jane_or_doe_is_not_flagged(example_cfg, tmp_path):
    """Only the exact example person is an unfilled value."""
    a = example_cfg["applicant"]
    a.update(first_name="Jane", last_name="Smith", email="jane@smith.example",
             phone="4791234567", linkedin_url="https://www.linkedin.com/in/jane-smith")
    Path(example_cfg["cover_letter"]["profile_path"]).write_text(
        "Name: Jane Smith\nNurse with twelve years in intensive care, team lead since 2019.\n",
        encoding="utf-8")
    example_cfg["answers"]["work_authorization"]["countries"] = ["Norway"]
    problems = main_mod.check_problems(example_cfg)
    assert not any("first_name" in p or "last_name" in p for p in problems), problems


def test_nearly_empty_profile_is_flagged(example_cfg):
    Path(example_cfg["cover_letter"]["profile_path"]).write_text("# Headline\n\n# Skills\n", encoding="utf-8")
    assert any("nearly empty" in p for p in main_mod.check_problems(example_cfg))
