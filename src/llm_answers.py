"""Real-time LLM answers for screening questions the config maps can't cover.

When a REQUIRED question has no config answer, the configured LLM (any provider —
see src/llm_client.py) is asked to answer it — in any language — using ONLY the
facts in data/profile.md. If it can't answer truthfully and confidently it
returns None and the job is skipped, exactly as before. The golden rule (never
fabricate to an employer) is enforced in the prompt and by the caller validating
dropdown/radio answers against the real options.

Every decision is cached in data/llm_answers.json so the same question is
never paid for twice, and the file doubles as an audit log you can review and
promote into config.yaml answers.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import llm_client
from .utils import log

_CACHE_PATH = Path("data/llm_answers.json")

_SYSTEM = (
    "You answer job-application screening questions on behalf of a candidate.\n"
    "Rules, in order of priority:\n"
    "1. NEVER fabricate. Use ONLY facts stated or directly implied by the candidate "
    "profile below. If the profile does not support a truthful answer, return null.\n"
    "2. Questions may be in any language — answer in the language the options use, "
    "or the question's language for free-text.\n"
    "3. If options are listed, the answer MUST be exactly one of them, verbatim.\n"
    "4. For numeric experience questions, answer with a plain number.\n"
    "5. For salary/notice-period style questions, answer 'Negotiable' / '1 month' "
    "per the profile; never invent specific salary figures.\n"
    "6. Answer like a human typing into a form: just the number or a few words. "
    "Never templated sentences, policy statements, or spelled-out alternatives.\n"
    "Return JSON: {\"answer\": <string or null>, \"reason\": <short justification>}."
)


def llm_answer(question: str, options: list[str] | None, cfg: dict) -> str | None:
    """Answer a screening question from the profile, or None to skip the job."""
    llm_cfg = cfg.get("answers", {}).get("llm_fallback", {})
    if not llm_cfg.get("enabled"):
        return None

    cache = _load_cache()
    key = json.dumps({"q": " ".join(question.lower().split()), "o": sorted(options or [])},
                     ensure_ascii=False, sort_keys=True)
    if key in cache:
        return cache[key]

    answer = _ask_llm(question, options, cfg)
    # Validate option answers against the real options (defends rule 3).
    if answer is not None and options:
        match = next((o for o in options if o.strip().lower() == answer.strip().lower()), None)
        if match is None:
            match = next((o for o in options if answer.strip().lower() in o.lower()), None)
        if match is None:
            log.warning("[llm] answer %r is not one of the options — skipping.", answer)
        answer = match

    cache[key] = answer
    _save_cache(cache)
    return answer


def _ask_llm(question: str, options: list[str] | None, cfg: dict) -> str | None:
    profile_path = cfg.get("cover_letter", {}).get("profile_path", "data/profile.md")
    profile = Path(profile_path).read_text(encoding="utf-8") if Path(profile_path).exists() else ""

    system = _SYSTEM + f"\n\n=== CANDIDATE PROFILE ===\n{profile}"
    user = f"Screening question:\n{question.strip()}\n"
    if options:
        user += "\nOptions (answer must be one of these, verbatim):\n" + "\n".join(f"- {o}" for o in options)
    user += ('\n\nReply with ONLY a JSON object: '
             '{"answer": <string, or null if it cannot be answered truthfully from the profile>, '
             '"reason": <short justification>}.')

    text = llm_client.complete(cfg, system, user, max_tokens=400)
    if text is None:
        return None
    data = _parse_json(text)
    if data is None:
        log.warning("[llm] could not parse answer from: %.120r", text)
        return None

    answer = data.get("answer")
    log.info("[llm] %r -> %r (%s)", question[:90], answer, (data.get("reason") or "")[:90])
    return answer if answer is None else str(answer)


def _parse_json(text: str) -> dict | None:
    """Lenient JSON extraction — tolerates code fences / prose around the object."""
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
