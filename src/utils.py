"""Shared helpers: human-like delays, logging, filenames, and the run lock."""
from __future__ import annotations

import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lijab")


def sanitize_filename(name: str, max_len: int = 80) -> str:
    """Make an arbitrary string (job_id etc.) safe to use as a filename."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(name)).strip("._") or "unnamed"
    return safe[:max_len]


# Characters LLMs love that FPDF's latin-1 core fonts can't render. Mapping them
# to ASCII beats the '?' that encode(..., "replace") would produce.
_PDF_PUNCT = {
    "–": "-", "—": " - ", "―": " - ", "−": "-",
    "‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"',
    "„": '"', "…": "...", "•": "-", "·": "-",
    " ": " ", "​": "", "﻿": "", "→": "->", "✓": "+",
}


def normalize_for_pdf(text: str) -> str:
    """Replace common non-latin-1 punctuation before rendering with core fonts."""
    for src, dst in _PDF_PUNCT.items():
        text = text.replace(src, dst)
    return text


def acquire_run_lock(lock_path: str = "data/.lijaa.lock"):
    """OS-level single-instance lock so two runs can't double-apply.

    Returns an open file handle that must be kept alive for the whole run
    (the lock dies with the process, even on a crash), or None if another
    instance already holds it.
    """
    p = Path(lock_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = open(p, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def setup_logging(log_dir: str) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logfile = Path(log_dir) / f"run-{datetime.now():%Y%m%d-%H%M%S}.log"
    fmt = "%(asctime)s  %(levelname)-7s  %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[logging.StreamHandler(), logging.FileHandler(logfile, encoding="utf-8")],
    )
    log.info("Logging to %s", logfile)


def human_delay(bounds: list[float] | tuple[float, float]) -> None:
    """Sleep a random amount within ``bounds`` (seconds) to look human."""
    lo, hi = bounds
    time.sleep(random.uniform(lo, hi))


def jitter(seconds: float, spread: float = 0.4) -> None:
    """Sleep ``seconds`` +/- a random fraction."""
    time.sleep(max(0.1, seconds + random.uniform(-spread, spread)))


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines) so OP_SERVICE_ACCOUNT_TOKEN is set."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)
