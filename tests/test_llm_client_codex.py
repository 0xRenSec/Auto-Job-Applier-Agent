"""Unit tests for the codex provider in llm_client — subprocess is stubbed.

Run:  python tests/test_llm_client_codex.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import llm_client  # noqa: E402


class FakeProc:
    def __init__(self, returncode=0, stderr=b""):
        self.returncode = returncode
        self.stderr = stderr


def _stub_run(reply: str | None, returncode: int = 0):
    """Stub subprocess.run: writes `reply` to the --output-last-message file."""
    calls = []

    def fake(cmd, input=None, capture_output=None, timeout=None, shell=None):
        calls.append({"cmd": cmd, "input": input, "timeout": timeout})
        out = cmd[cmd.index("--output-last-message") + 1]
        if reply is not None:
            Path(out).write_text(reply, encoding="utf-8")
        return FakeProc(returncode=returncode, stderr=b"boom\n")

    subprocess.run = fake
    return calls


_REAL_RUN = subprocess.run
CFG = {"llm": {"provider": "codex", "model": "gpt-5.6-sol", "effort": "ultra",
               "timeout_s": 240}}


def test_happy_path_returns_last_message_and_passes_flags():
    try:
        calls = _stub_run('{"apply": true, "reason": "ok"}')
        text = llm_client.complete(CFG, "SYSTEM", "USER")
        assert text == '{"apply": true, "reason": "ok"}'
        cmd = calls[0]["cmd"]
        assert cmd[:2] == ["codex", "exec"]
        assert "--ephemeral" in cmd and "read-only" in cmd
        assert cmd[cmd.index("--model") + 1] == "gpt-5.6-sol"
        assert 'model_reasoning_effort="ultra"' in cmd
        assert cmd[-1] == "-"                      # prompt via stdin
        assert b"SYSTEM" in calls[0]["input"] and b"USER" in calls[0]["input"]
        assert calls[0]["timeout"] == 240.0
    finally:
        subprocess.run = _REAL_RUN


def test_nonzero_exit_returns_none():
    try:
        _stub_run("ignored", returncode=1)
        assert llm_client.complete(CFG, "s", "u") is None
    finally:
        subprocess.run = _REAL_RUN


def test_empty_last_message_returns_none():
    try:
        _stub_run("")
        assert llm_client.complete(CFG, "s", "u") is None
    finally:
        subprocess.run = _REAL_RUN


def test_no_model_and_no_effort_omits_flags():
    try:
        calls = _stub_run("hi")
        assert llm_client.complete({"llm": {"provider": "codex"}}, "s", "u") == "hi"
        cmd = calls[0]["cmd"]
        assert "--model" not in cmd and not any("model_reasoning_effort" in c for c in cmd)
    finally:
        subprocess.run = _REAL_RUN


if __name__ == "__main__":
    import traceback
    failed = 0
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
