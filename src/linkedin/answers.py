"""Resolve a screening-question label to an answer using config.

The golden rule: if we cannot confidently answer a REQUIRED question, we return
None and the caller skips the whole application. The bot never guesses on
required fields — that's how you end up sending nonsense to employers.
"""
from __future__ import annotations

import re

from ..utils import log


def _match(label: str, mapping: dict):
    """Return the value whose key is a substring of label (longest key wins)."""
    label_l = label.lower()
    best_key = None
    for key in mapping:
        if key.lower() in label_l and (best_key is None or len(key) > len(best_key)):
            best_key = key
    return mapping[best_key] if best_key is not None else None


def numeric_answer(label: str, answers: dict) -> str | None:
    exp = answers.get("experience_years", {})
    val = _match(label, exp.get("overrides", {}))
    if val is None:
        val = exp.get("default")
    return str(val) if val is not None else None


def yes_no_answer(label: str, answers: dict) -> bool | None:
    return _match(label, answers.get("yes_no", {}))


# "How many years of experience…" in the languages LinkedIn localises forms to.
_NUMERIC_Q = re.compile(
    r"how many|how long|years? of|years'?\s|experien|experiên|expérien|erfahrung"
    r"|esperienza|ervaring|erfarenhet|doświadczen|años|\banos\b|jahre|\bår\b",
    re.I,
)


def looks_numeric_question(label: str) -> bool:
    return bool(_NUMERIC_Q.search(label))


def text_answer(label: str, answers: dict) -> str | None:
    # Numeric-looking questions ("how many years…") route to the experience map.
    if looks_numeric_question(label):
        n = numeric_answer(label, answers)
        if n is not None:
            return n
    return _match(label, answers.get("text", {}))


# Salary computation: derive the number in the currency/period the question
# asks for, from the anchors in config answers.salary. Approximate EUR rates.
_EUR_RATES = {
    "eur": 1.0, "usd": 1.08, "gbp": 0.85, "chf": 0.94,
    "sek": 11.0, "nok": 11.5, "dkk": 7.45,
    "pln": 4.3, "ron": 5.0, "czk": 25.0, "huf": 395.0,
}
_CUR_HINTS = [  # order matters: explicit codes before symbols
    ("sek", ("sek",)), ("nok", ("nok",)), ("dkk", ("dkk",)),
    ("pln", ("pln", "zł", "zloty")), ("ron", ("ron", "lei")),
    ("czk", ("czk",)), ("huf", ("huf", "forint")), ("chf", ("chf",)),
    ("gbp", ("gbp", "£", "pound")), ("usd", ("usd", "$", "dollar")),
    ("eur", ("eur", "€")),
]
_SALARY_WORDS = ("salary", "salar", "compensation", "remuneration", "wage",
                 "rate", "gehalt", "lön", "wynagrodzenie", "stipendio", "sueldo")
_HOUR_WORDS = ("hour", "stunde", "hora", "ora", "godz", "timme", "/h")
_DAY_WORDS = ("per day", "/day", "daily", "per diem", "tag", "/d")
_MONTH_WORDS = ("month", "mensu", "mensil", "monat", "miesi", "månad", "lunar", "/mo")


def salary_answer(label: str, answers: dict) -> str | None:
    """Compute a salary answer in the label's currency and period, or None."""
    sal = answers.get("salary", {})
    annual, hourly = sal.get("annual_eur"), sal.get("hourly_eur")
    if not annual:
        return None
    l = label.lower()
    if "current" in l:  # never disclose current salary as a number
        return None
    if not any(w in l for w in _SALARY_WORDS):
        return None
    cur = next((c for c, hints in _CUR_HINTS if any(h in l for h in hints)), "eur")
    rate = _EUR_RATES[cur]
    # Per-currency pinned annual (e.g. gbp: 150000 = ask 150k GBP, not a
    # conversion). Scales the hourly/daily/monthly answers proportionally.
    pinned = (sal.get("overrides") or {}).get(cur)
    if pinned:
        rate = pinned / annual
    # Sweden is a fixed exception: the user's anchor there is 90k SEK/month.
    if cur == "sek" and sal.get("sek_monthly"):
        if any(w in l for w in _MONTH_WORDS):
            return str(sal["sek_monthly"])
        if not any(w in l for w in _HOUR_WORDS + _DAY_WORDS):
            return str(sal["sek_monthly"] * 12)  # annual SEK
    if any(w in l for w in _HOUR_WORDS):
        val, step = (hourly or 100) * rate, 5
    elif any(w in l for w in _DAY_WORDS):
        val, step = (hourly or 100) * 8 * rate, 50
    elif any(w in l for w in _MONTH_WORDS):
        val, step = annual / 12 * rate, 100
    else:
        val, step = annual * rate, 1000
    return str(int(round(val / step) * step))


# "English" across form languages; level options ordered best-first. The
# profile states full-professional English, so picking the highest truthful
# level beats the last-resort first option (often "None"/"Ninguno").
_ENGLISH_WORDS = ("english", "inglés", "ingles", "inglês", "inglese", "anglais",
                  "englisch", "engelska", "angielski")
_LEVEL_PREFERENCE = ("full professional", "professional", "native", "fluent",
                     "advanced", "profissional", "profesional", "professionnel",
                     "nativo", "fluente", "fluido", "courant", "avançado",
                     "avanzado", "avancé", "verhandlungssicher", "fließend",
                     "flytande", "c2", "c1")


def language_level_answer(label: str, options: list[str]) -> str | None:
    """Pick the best English-proficiency option; None for other languages."""
    l = label.lower()
    if not any(w in l for w in _ENGLISH_WORDS):
        return None
    for level in _LEVEL_PREFERENCE:
        for opt in options:
            if level in opt.lower():
                return opt
    return None


def dropdown_answer(label: str, options: list[str], answers: dict) -> str | None:
    """Pick a dropdown option. Prefer a Yes/No match, else config default."""
    yn = yes_no_answer(label, answers)
    if yn is not None:
        want = "yes" if yn else "no"
        for opt in options:
            if opt.strip().lower() == want:
                return opt
    txt = text_answer(label, answers)
    if txt:
        for opt in options:
            if txt.lower() in opt.lower():
                return opt
    default = answers.get("dropdown_default") or ""
    if default:
        for opt in options:
            if default.lower() in opt.lower():
                return opt
    return None
