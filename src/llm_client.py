"""Provider-agnostic LLM client — works with *any* LLM.

Three providers are built in and selected by `llm.provider` in config.yaml:

  * ``openai``    — any OpenAI-compatible Chat Completions API. This covers
                    OpenAI itself plus local and hosted gateways that speak the
                    same protocol: Ollama, LM Studio, vLLM, OpenRouter, Together,
                    Groq, Azure OpenAI, etc. Point ``llm.base_url`` at the
                    endpoint (e.g. ``http://localhost:11434/v1`` for Ollama).
  * ``anthropic`` — the native Anthropic Messages API (Claude).
  * ``codex``     — the OpenAI Codex CLI (``codex exec``), using its own
                    ChatGPT-plan login instead of an API key. ``llm.model``
                    (e.g. gpt-5.6-sol) and optional ``llm.effort`` (low | medium
                    | high | xhigh | ultra) are passed through. Runs read-only,
                    ephemeral, prompt on stdin, answer via --output-last-message.

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
    if provider(cfg) == "codex":
        import shutil
        return shutil.which("codex") is not None
    if provider(cfg) == "openai" and _cfg(cfg).get("base_url"):
        return True  # local / self-hosted endpoints need no key (see complete())
    return resolve_key(cfg) is not None


def describe_config(cfg: dict) -> str:
    """One line for --check: the provider and where its key would come from,
    WITHOUT resolving any secret, running any CLI, or touching the network."""
    prov, lc = provider(cfg), _cfg(cfg)
    model = lc.get("model") or _DEFAULT_MODEL.get(prov, "")
    if prov == "codex":
        import shutil
        return ("codex CLI (found on PATH)" if shutil.which("codex")
                else "codex CLI selected but not found on PATH")
    if prov not in ("openai", "anthropic"):
        return f"provider {prov!r} is not supported (openai | anthropic | codex)"
    if lc.get("api_key_ref"):
        return f"{prov} / {model} - key from 1Password reference {lc['api_key_ref']} (not checked here)"
    env_name = lc.get("api_key_env") or _DEFAULT_KEY_ENV.get(prov, "OPENAI_API_KEY")
    if os.environ.get(env_name):
        return f"{prov} / {model} - key found in {env_name}"
    if prov == "openai" and lc.get("base_url"):
        return f"{prov} / {model} at {lc['base_url']} (no key; local endpoint)"
    return (f"not configured - {env_name} is not set in .env "
            "(template cover letters; unanswerable questions are skipped)")


def complete(cfg: dict, system: str, user: str, max_tokens: int = 512) -> str | None:
    """Run one chat completion. Returns the assistant text, or None on any
    failure / missing key (callers fall back gracefully)."""
    prov = provider(cfg)
    if prov not in ("openai", "anthropic", "codex"):
        # Fail closed: never route the profile to a default endpoint on a typo.
        log.warning("[llm] unknown provider %r — skipping (use openai | anthropic | codex).", prov)
        return None
    if prov == "codex":
        model = _cfg(cfg).get("model") or None
        effort = _cfg(cfg).get("effort") or None
        timeout_s = float(_cfg(cfg).get("timeout_s") or 240)
        try:
            return _codex(model, effort, system, user, timeout_s)
        except Exception as exc:  # CLI missing, timeout, non-zero exit — never crash the run
            log.warning("[llm] codex exec failed (%s): %s", model, exc)
            return None
    base_url = _cfg(cfg).get("base_url") or None
    key = resolve_key(cfg)
    if not key:
        if prov == "openai" and base_url:
            # Local/self-hosted gateways (Ollama, LM Studio, vLLM) need no key,
            # but the SDK requires a non-empty string.
            key = "no-key-local"
        else:
            log.warning("[llm] no API key configured (llm.api_key_env / api_key_ref) — skipping.")
            return None
    model = _cfg(cfg).get("model") or _DEFAULT_MODEL.get(prov, "gpt-4o-mini")
    timeout_s = float(_cfg(cfg).get("timeout_s") or 60)
    try:
        if prov == "anthropic":
            return _anthropic(key, model, system, user, max_tokens, timeout_s)
        return _openai(key, model, system, user, max_tokens, base_url, timeout_s)
    except Exception as exc:  # network, auth, SDK-missing — never crash the run
        log.warning("[llm] completion failed (%s/%s): %s", prov, model, exc)
        return None


def _openai(key, model, system, user, max_tokens, base_url, timeout_s) -> str | None:
    try:
        from openai import OpenAI
    except ImportError:
        log.warning("[llm] openai SDK not installed; run pip install -r requirements.txt")
        return None
    # Without an explicit timeout the SDK default (~10 min) can stall the whole
    # browser loop on a hung provider.
    client = OpenAI(api_key=key, base_url=base_url, timeout=timeout_s, max_retries=2)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return (resp.choices[0].message.content or "").strip() or None


def _codex(model, effort, system, user, timeout_s) -> str | None:
    """One-shot `codex exec` call. The prompt goes in on stdin (no Windows
    arg-length/quoting issues) and the final assistant message comes back via
    --output-last-message; stdout (agent event noise) is discarded."""
    import subprocess
    import tempfile
    from pathlib import Path

    prompt = f"{system}\n\n{user}"
    fd, out_path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check",
           "--sandbox", "read-only", "--color", "never",
           "--output-last-message", out_path]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    cmd += ["-"]  # read the prompt from stdin
    try:
        proc = subprocess.run(cmd, input=prompt.encode("utf-8"),
                              capture_output=True, timeout=timeout_s, shell=False)
        if proc.returncode != 0:
            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
            log.warning("[llm] codex exec exited %d: %s", proc.returncode, tail[0][:200])
            return None
        return Path(out_path).read_text(encoding="utf-8").strip() or None
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _anthropic(key, model, system, user, max_tokens, timeout_s) -> str | None:
    try:
        import anthropic
    except ImportError:
        log.warning("[llm] anthropic SDK not installed; run pip install -r requirements.txt")
        return None
    client = anthropic.Anthropic(api_key=key, timeout=timeout_s, max_retries=2)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip() or None
