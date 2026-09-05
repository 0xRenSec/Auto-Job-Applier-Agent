"""Optional 1Password integration for hands-free login.

This module is only used if you configure the `onepassword:` section in
config.yaml. It reads your LinkedIn credentials (and an optional TOTP code) from
1Password via the `op` CLI, authenticated non-interactively by an
OP_SERVICE_ACCOUNT_TOKEN in .env. If you skip 1Password entirely, log in once
with `python -m src.main --login` and this module is never called.

No secret value is ever logged or printed — only the `op` references you
configure are used to fetch values at runtime.
"""
from __future__ import annotations

import os
import subprocess

from .utils import log


class SecretError(RuntimeError):
    pass


def _ensure_token() -> None:
    if not os.environ.get("OP_SERVICE_ACCOUNT_TOKEN"):
        raise SecretError(
            "OP_SERVICE_ACCOUNT_TOKEN is not set. Put it in .env "
            "(see README) so the bot can read LinkedIn credentials from 1Password."
        )


def _op(args: list[str]) -> str:
    """Run an `op` command and return stdout, never logging the output."""
    _ensure_token()
    try:
        result = subprocess.run(
            ["op", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",     # op emits UTF-8; the Windows default is cp1252
            errors="strict",
            timeout=30,
            check=True,
        )
    except FileNotFoundError as exc:  # op not installed
        raise SecretError("The 1Password CLI `op` is not installed or not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise SecretError("op command timed out after 30s.") from exc
    except subprocess.CalledProcessError as exc:
        # stderr may contain the path but not the secret value — safe-ish to surface.
        raise SecretError(f"op command failed: {(exc.stderr or '').strip()}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise SecretError(f"op command failed: {exc}") from exc
    # Strip only the trailing newline — a secret may legitimately contain spaces.
    return result.stdout.rstrip("\r\n")


def op_read(reference: str) -> str:
    """Resolve an op:// secret reference to its value."""
    if not str(reference).startswith("op://"):
        raise SecretError(f"not an op:// secret reference: {reference!r}")
    return _op(["read", reference])


def get_credentials(cfg: dict) -> tuple[str, str]:
    """Return (email, password) for LinkedIn from 1Password.

    onepassword.email_override in config.yaml wins over the 1Password username
    field — for when the vault item holds a different/outdated email.
    """
    op = cfg.get("onepassword") or {}
    if not op.get("password_ref") or not (op.get("email_ref") or op.get("email_override")):
        # Missing/incomplete section must raise SecretError (not KeyError) so the
        # caller can fall back to manual login in the browser window.
        raise SecretError(
            "The `onepassword:` section is not configured (email_ref/password_ref)."
        )
    email = op.get("email_override") or op_read(op["email_ref"])
    password = op_read(op["password_ref"])
    if not password:
        raise SecretError(
            f"The 1Password field {op['password_ref']} is empty — add your LinkedIn "
            "password to the item (or log in manually once with `python -m src.main --login`)."
        )
    log.info("Fetched LinkedIn credentials from 1Password (%s), login email: %s",
             op.get("item", "?"), _mask_email(email))
    return email, password


def _mask_email(email: str) -> str:
    """PII hygiene for logs: keep just enough to recognise the account."""
    local, _, domain = (email or "").partition("@")
    if not domain:
        return "***"
    return f"{local[:2]}***@{domain}"


def get_otp(cfg: dict) -> str | None:
    """Return a current TOTP 2FA code if the item has one configured, else None."""
    op = cfg.get("onepassword") or {}
    item = op.get("item")
    vault = op.get("vault")
    if not item:
        return None
    args = ["item", "get", item, "--otp"]
    if vault:  # passing --vault None would crash subprocess with a TypeError
        args[3:3] = ["--vault", vault]
    try:
        code = _op(args)
    except SecretError:
        # No OTP field on the item — that's fine; LinkedIn may not prompt for 2FA.
        return None
    return code or None
