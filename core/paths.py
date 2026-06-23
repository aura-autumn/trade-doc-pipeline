"""
Project-root-anchored path resolution.

The pipeline stores data under ./data and ./logs. Those defaults are RELATIVE,
which means they resolve against the process's current working directory — so
launching `uvicorn` (or any entry point) from a subdirectory silently opens a
DIFFERENT, empty SQLite file and an empty RAG store. That looked like "the
customer DB and past runs aren't seeded".

`resolve_data_path` anchors a relative path to the project root (the parent of
this `core/` package) so every entry point — Streamlit, FastAPI, the inbox
triggers, eval — reads and writes the SAME files regardless of where they were
launched. Absolute paths are returned untouched.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_data_path(path: str | os.PathLike) -> str:
    """Return `path` as-is if absolute, else anchored to the project root."""
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str((PROJECT_ROOT / p).resolve())
