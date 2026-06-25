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
            timeout=30,
            check=True,
        )
    except FileNotFoundError as exc:  # op not installed
        raise SecretError("The 1Password CLI `op` is not installed or not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        # stderr may contain the path but not the secret value — safe-ish to surface.
        raise SecretError(f"op command failed: {exc.stderr.strip()}") from exc
    return result.stdout.strip()


def op_read(reference: str) -> str:
    """Resolve an op:// secret reference to its value."""
    return _op(["read", reference])


def get_credentials(cfg: dict) -> tuple[str, str]:
    """Return (email, password) for LinkedIn from 1Password.

    onepassword.email_override in config.yaml wins over the 1Password username
    field — for when the vault item holds a different/outdated email.
    """
    op = cfg.get("onepassword", {})
    email = op.get("email_override") or op_read(op["email_ref"])
    password = op_read(op["password_ref"])
    if not password:
        raise SecretError(
            f"The 1Password field {op['password_ref']} is empty — add your LinkedIn "
            "password to the item (or log in manually once with `python -m src.main --login`)."
        )
    log.info("Fetched LinkedIn credentials from 1Password (%s), login email: %s",
             op.get("item", "?"), email)
    return email, password


def get_otp(cfg: dict) -> str | None:
    """Return a current TOTP 2FA code if the item has one configured, else None."""
    op = cfg.get("onepassword", {})
    item = op.get("item")
    vault = op.get("vault")
    if not item:
        return None
    try:
        code = _op(["item", "get", item, "--vault", vault, "--otp"])
    except SecretError:
        # No OTP field on the item — that's fine; LinkedIn may not prompt for 2FA.
        return None
    return code or None
