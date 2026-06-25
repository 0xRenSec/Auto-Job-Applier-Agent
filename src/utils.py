"""Shared helpers: human-like delays and logging."""
from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lijab")


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
