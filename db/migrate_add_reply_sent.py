"""
Migration: add 'reply_sent' to shipments status CHECK constraint.
SQLite doesn't support ALTER TABLE to modify CHECK constraints,
so we recreate the shipments table with the updated constraint.

Run once:
    python -m db.migrate_add_reply_sent
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from db.database import get_connection, _migrate_status_constraint

def migrate():
    """
    Add 'reply_sent' to the shipments status CHECK constraint.

    Note: init_db() now performs this same migration automatically on every
    startup (it's idempotent), so running this by hand is rarely necessary —
    it's kept for explicit/manual use.
    """
    with get_connection() as conn:
        _migrate_status_constraint(conn)
    print("[Migration] Done — 'reply_sent' is a valid shipment status.")

if __name__ == "__main__":
    migrate()