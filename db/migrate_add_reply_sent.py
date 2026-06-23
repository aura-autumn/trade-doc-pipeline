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

from db.database import get_connection

def migrate():
    with get_connection() as conn:
        # Check if migration already applied by checking if reply_sent is already valid
        try:
            conn.execute("UPDATE shipments SET status = 'reply_sent' WHERE 1=0")
            print("[Migration] reply_sent already supported — no changes needed.")
            return
        except Exception:
            pass

        print("[Migration] Adding 'reply_sent' to shipments status constraint...")

        conn.executescript("""
            PRAGMA foreign_keys = OFF;

            CREATE TABLE IF NOT EXISTS shipments_new (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                status TEXT DEFAULT 'processing'
                    CHECK(status IN ('processing', 'approved', 'flagged',
                                     'amendment_drafted', 'error', 'reply_sent')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            );

            INSERT INTO shipments_new SELECT * FROM shipments;

            DROP TABLE shipments;

            ALTER TABLE shipments_new RENAME TO shipments;

            PRAGMA foreign_keys = ON;
        """)

        print("[Migration] ✅ Done. 'reply_sent' is now a valid shipment status.")

if __name__ == "__main__":
    migrate()