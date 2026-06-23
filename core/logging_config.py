"""
Central logging configuration for the whole pipeline.

Everything (agents, pipeline, RAG, DB, query, inbox triggers, FastAPI backend)
logs through `get_logger(__name__)`. One call to `setup_logging()` at process
start wires up:

  • a coloured console handler (human-friendly, level-coloured)
  • a rotating file handler at ./logs/trade_pipeline.log (full audit trail)

This replaces the scattered `print("[Tag] ...")` statements with structured,
filterable, timestamped logs — which is exactly what makes the agentic pipeline
debuggable when something fails three hops deep.

Usage
-----
    from core.logging_config import get_logger
    log = get_logger(__name__)
    log.info("Shipment %s extracted in %.2fs", sid, elapsed)

Set LOG_LEVEL=DEBUG in .env to see every step. Default is INFO.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR = Path(os.getenv("LOG_DIR", "./logs"))
LOG_FILE = LOG_DIR / "trade_pipeline.log"

_CONSOLE_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_FILE_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
_DATE_FMT = "%H:%M:%S"

_configured = False


def _build_console_handler() -> logging.Handler:
    """Coloured console handler if `colorlog` is available, else plain."""
    handler = logging.StreamHandler()
    try:
        from colorlog import ColoredFormatter

        handler.setFormatter(
            ColoredFormatter(
                "%(log_color)s%(asctime)s | %(levelname)-7s%(reset)s | "
                "%(cyan)s%(name)s%(reset)s | %(message)s",
                datefmt=_DATE_FMT,
                log_colors={
                    "DEBUG": "white",
                    "INFO": "green",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "bold_red",
                },
            )
        )
    except Exception:
        handler.setFormatter(logging.Formatter(_CONSOLE_FMT, datefmt=_DATE_FMT))
    return handler


def setup_logging(level: str | None = None) -> None:
    """
    Configure the root logger once per process. Safe to call multiple times —
    subsequent calls are no-ops (so importing it from many entry points is fine).
    """
    global _configured
    if _configured:
        return

    resolved = (level or LOG_LEVEL).upper()
    root = logging.getLogger()
    root.setLevel(resolved)

    # Console
    root.addHandler(_build_console_handler())

    # Rotating file — 2 MB x 3 backups, full audit trail
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(_FILE_FMT, datefmt="%Y-%m-%d %H:%M:%S"))
        root.addHandler(file_handler)
    except Exception as exc:  # pragma: no cover - file logging is best-effort
        root.warning("File logging disabled: %s", exc)

    # Quiet down noisy third-party libraries
    for noisy in ("httpx", "httpcore", "urllib3", "googleapiclient", "google",
                  "watchfiles", "faiss", "sklearn", "PIL", "pdfminer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    root.debug("Logging configured at level %s -> %s", resolved, LOG_FILE)


def get_logger(name: str) -> logging.Logger:
    """Return a module logger, ensuring logging is configured first."""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
