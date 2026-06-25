"""Provider-agnostic LLM client — works with *any* LLM.

Two providers are built in and selected by `llm.provider` in config.yaml:

  * ``openai``    — any OpenAI-compatible Chat Completions API. This covers
                    OpenAI itself plus local and hosted gateways that speak the
                    same protocol: Ollama, LM Studio, vLLM, OpenRouter, Together,
                    Groq, Azure OpenAI, etc. Point ``llm.base_url`` at the
                    endpoint (e.g. ``http://localhost:11434/v1`` for Ollama).
  * ``anthropic`` — the native Anthropic Messages API (Claude).

Example config:

    llm:
      provider: openai
      model: gpt-4o-mini
      base_url: ""                 # blank = api.openai.com; set for local/gateways
      api_key_env: OPENAI_API_KEY  # name of the env var holding the key
      # api_key_ref: "op://Vault/Item/credential"   # optional 1Password ref

Key resolution order: ``llm.api_key_ref`` (1Password) → the env var named by
``llm.api_key_env`` → None. When no key is available, :func:`complete` returns
None and callers degrade gracefully (template cover letters, skipped screening
questions). Nothing here ever logs a key or a secret value.
"""
from __future__ import annotations

import os

from . import secrets
from .utils import log

_DEFAULT_KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
_DEFAULT_MODEL = {"openai": "gpt-4o-mini", "anthropic": "claude-opus-4-8"}


def _cfg(cfg: dict) -> dict:
    return cfg.get("llm", {}) or {}


def provider(cfg: dict) -> str:
    return (_cfg(cfg).get("provider") or "openai").lower()


def resolve_key(cfg: dict) -> str | None:
    """Return the API key from a 1Password ref or env var, or None."""
    lc = _cfg(cfg)
    ref = lc.get("api_key_ref")
    if ref:
        try:
            key = secrets.op_read(ref)
            if key:
                return key
        except secrets.SecretError as exc:
            log.warning("[llm] could not read api_key_ref from 1Password: %s", exc)
    env_name = lc.get("api_key_env") or _DEFAULT_KEY_ENV.get(provider(cfg), "OPENAI_API_KEY")
    return os.environ.get(env_name) or None


def is_configured(cfg: dict) -> bool:
    return resolve_key(cfg) is not None


def complete(cfg: dict, system: str, user: str, max_tokens: int = 512) -> str | None:
    """Run one chat completion. Returns the assistant text, or None on any
    failure / missing key (callers fall back gracefully)."""
    key = resolve_key(cfg)
    if not key:
        log.warning("[llm] no API key configured (llm.api_key_env / api_key_ref) — skipping.")
        return None
    prov = provider(cfg)
    model = _cfg(cfg).get("model") or _DEFAULT_MODEL.get(prov, "gpt-4o-mini")
    try:
        if prov == "anthropic":
            return _anthropic(key, model, system, user, max_tokens)
        return _openai(key, model, system, user, max_tokens, _cfg(cfg).get("base_url") or None)
    except Exception as exc:  # network, auth, SDK-missing — never crash the run
        log.warning("[llm] completion failed (%s/%s): %s", prov, model, exc)
        return None


def _openai(key, model, system, user, max_tokens, base_url) -> str | None:
    try:
        from openai import OpenAI
    except ImportError:
        log.warning("[llm] openai SDK not installed; run pip install -r requirements.txt")
        return None
    client = OpenAI(api_key=key, base_url=base_url)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return (resp.choices[0].message.content or "").strip() or None


def _anthropic(key, model, system, user, max_tokens) -> str | None:
    try:
        import anthropic
    except ImportError:
        log.warning("[llm] anthropic SDK not installed; run pip install -r requirements.txt")
        return None
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip() or None
