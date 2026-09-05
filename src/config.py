"""Load and lightly validate config.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml


def load_config(path: str = "config.yaml") -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(
            f"Config file '{path}' not found. Copy config.example.yaml to config.yaml and edit it."
        )
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    if not isinstance(cfg, dict):
        raise SystemExit("config.yaml is empty or not a YAML mapping — "
                         "copy config.example.yaml to config.yaml and edit it.")
    for section in ("search", "safety", "applicant", "answers", "storage"):
        if not isinstance(cfg.get(section), dict):
            raise SystemExit(f"config.yaml is missing the required '{section}:' section.")

    resume = cfg["applicant"].get("resume_path")
    if not resume or not Path(resume).is_file():
        raise SystemExit(
            f"Your CV was not found at applicant.resume_path = '{resume}'. "
            "Copy your CV (PDF) to that location, or change the path in config.yaml."
        )

    keywords = cfg["search"].get("keywords")
    # A bare string would be iterated character-by-character by the search loop.
    if not keywords or not isinstance(keywords, list) \
            or not all(isinstance(k, str) and k.strip() for k in keywords):
        raise SystemExit("search.keywords must be a YAML list of at least one job title, e.g.\n"
                         "  keywords:\n    - \"Project Manager\"")

    jd = cfg.get("jd_screen") or {}
    if jd.get("enabled") and not str(jd.get("home_country") or "").strip():
        raise SystemExit("jd_screen.enabled is true but jd_screen.home_country is empty — "
                         "set the country you live and may work in, or set enabled: false.")

    # --- Types. YAML is forgiving; a wrong type here silently becomes a wrong
    # answer on a form ("false" in quotes is a non-empty string, i.e. Yes), so
    # refuse early with the exact key.
    safety = cfg["safety"]
    for path in ("search.locations", "search.workplace_types", "search.experience_levels",
                 "search.required_title_keywords", "search.blocklist_keywords",
                 "search.blocklist_title_keywords", "jd_screen.allowed_regions",
                 "cover_letter.claim_skills", "answers.work_authorization.countries",
                 "answers.work_authorization.regions", "answers.citizenships"):
        _str_list(cfg, path)
    answers = cfg["answers"]
    for key, val in (answers.get("yes_no") or {}).items():
        if not isinstance(val, bool):
            raise SystemExit(f"answers.yes_no[{key!r}] must be true or false without quotes, "
                             f"not {val!r}.")
    for key, val in (answers.get("languages") or {}).items():
        if not isinstance(val, str) or not val.strip():
            raise SystemExit(f"answers.languages[{key!r}] must be a level in quotes, "
                             f"e.g. \"fluent\" or \"none\", not {val!r}.")
    default_years = (answers.get("experience_years") or {}).get("default")
    if default_years is not None and not isinstance(default_years, (int, float)):
        raise SystemExit("answers.experience_years.default must be a number or left empty.")
    for key in ("dry_run", "headless", "dedupe_by_role"):
        if key in safety and not isinstance(safety[key], bool):
            raise SystemExit(f"safety.{key} must be true or false without quotes.")
    daily = safety.get("max_applications_per_day")
    if daily is not None and (not isinstance(daily, int) or daily < 0):
        raise SystemExit("safety.max_applications_per_day must be a whole number or left empty.")
    tz = (cfg.get("browser") or {}).get("timezone")
    if tz is not None and not isinstance(tz, str):
        raise SystemExit("browser.timezone must be text in quotes, e.g. \"Europe/Berlin\".")
    cc = cfg.get("cover_letter")
    if cc is not None:
        if not isinstance(cc, dict):
            raise SystemExit("cover_letter: must be a section with mode / profile_path ...")
        mode = cc.get("mode", "template")
        if mode is False:      # YAML reads a bare `off` as the boolean false
            mode = "off"
        if not isinstance(mode, str) or mode.strip().lower() not in ("template", "llm", "off"):
            raise SystemExit(f"cover_letter.mode must be template, llm or \"off\", not {mode!r}.")
        cc["mode"] = mode.strip().lower()
    rmode = (cfg.get("resume") or {}).get("mode", "static")
    if not isinstance(rmode, str) or rmode.strip().lower() not in ("static", "tailored"):
        raise SystemExit(f"resume.mode must be static or tailored, not {rmode!r}.")


def _str_list(cfg: dict, path: str) -> None:
    """A dotted config path must be absent, empty, or a YAML list of non-empty
    strings — never a bare string (which code would iterate letter by letter)."""
    node: object = cfg
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return
    if node == []:
        return
    if not isinstance(node, list) or not all(isinstance(x, str) and x.strip() for x in node):
        leaf = path.split(".")[-1]
        raise SystemExit(f"{path} must be a list with one entry per line, e.g.\n"
                         f"  {leaf}:\n    - \"value\"\n(got {node!r})")

    safety = cfg["safety"]
    cap = safety.get("max_applications_per_run")
    if not isinstance(cap, int) or cap < 0:
        raise SystemExit("safety.max_applications_per_run must be a non-negative integer.")
    for key in ("delay_between_actions", "delay_between_jobs"):
        pair = safety.get(key)
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(isinstance(v, (int, float)) for v in pair) or pair[0] > pair[1]):
            raise SystemExit(f"safety.{key} must be [min, max] seconds with min <= max.")

    provider = (cfg.get("llm") or {}).get("provider")
    if provider and str(provider).strip().lower() not in ("openai", "anthropic", "codex"):
        raise SystemExit(f"llm.provider {provider!r} is not supported (openai | anthropic | codex).")
