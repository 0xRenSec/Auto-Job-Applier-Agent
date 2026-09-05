"""Unit tests for how notion_sync finds its integration token — no network.

Run:  python tests/test_notion_token.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from src import notion_sync  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    notion_sync._token_cache = {}
    yield
    notion_sync._token_cache = {}


def test_tokens_are_cached_per_source(monkeypatch):
    monkeypatch.setenv("TOKEN_A", "aaa")
    monkeypatch.setenv("TOKEN_B", "bbb")
    assert notion_sync._token({"token_env": "TOKEN_A"}) == "aaa"
    assert notion_sync._token({"token_env": "TOKEN_B"}) == "bbb"
    assert notion_sync._token({"token_env": "TOKEN_A"}) == "aaa"


def test_token_from_env_var(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_from_env")
    assert notion_sync._token({"token_env": "NOTION_TOKEN"}) == "secret_from_env"


def test_env_var_wins_over_1password_ref(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_from_env")
    calls = []
    monkeypatch.setattr(notion_sync.secrets, "op_read", lambda ref: calls.append(ref) or "x")
    assert notion_sync._token({"token_env": "NOTION_TOKEN",
                               "token_ref": "op://v/i/f"}) == "secret_from_env"
    assert calls == []


def test_falls_back_to_1password_ref(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.setattr(notion_sync.secrets, "op_read", lambda ref: f"from-op:{ref}")
    assert notion_sync._token({"token_env": "NOTION_TOKEN",
                               "token_ref": "op://v/i/f"}) == "from-op:op://v/i/f"


def test_no_token_source_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="token_env"):
        notion_sync._token({"enabled": True})


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
