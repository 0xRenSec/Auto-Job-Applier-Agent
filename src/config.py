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
    for section in ("search", "safety", "applicant", "answers", "storage"):
        if section not in cfg:
            raise SystemExit(f"config.yaml is missing the required '{section}:' section.")

    resume = cfg["applicant"].get("resume_path")
    if not resume or not Path(resume).exists():
        raise SystemExit(f"applicant.resume_path '{resume}' does not exist.")

    if not cfg["search"].get("keywords"):
        raise SystemExit("search.keywords must list at least one keyword.")
