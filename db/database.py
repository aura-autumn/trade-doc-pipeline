import sqlite3
import os
import uuid
from datetime import datetime
from typing import Optional, Any
from dotenv import load_dotenv

from core.logging_config import get_logger
from core.paths import resolve_data_path

load_dotenv()

log = get_logger(__name__)

# Anchor to the project root so the SAME db is used no matter where the process
# (uvicorn / streamlit / inbox triggers) is launched from.
DB_PATH = resolve_data_path(os.getenv("DB_PATH", "./data/trade_docs.db"))


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        schema = f.read()
    with get_connection() as conn:
        conn.executescript(schema)
        _migrate_status_constraint(conn)
    log.debug("DB initialized at %s", DB_PATH)


def _migrate_status_constraint(conn) -> None:
    """
    Self-healing migration: an older DB created the `shipments` table before
    'reply_sent' was a valid status, and SQLite can't ALTER a CHECK constraint.
    `CREATE TABLE IF NOT EXISTS` never touches the existing table, so we detect
    the stale definition from sqlite_master and rebuild it, preserving rows.

    Reliable detection (the standalone migration's `UPDATE ... WHERE 1=0` check is
    broken: a 0-row UPDATE never evaluates the CHECK, so it always false-passes).
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='shipments'"
    ).fetchone()
    if not row or not row["sql"]:
        return
    if "reply_sent" in row["sql"]:
        return  # already up to date

    log.info("Migrating shipments.status CHECK constraint to include 'reply_sent'...")
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        CREATE TABLE shipments_new (
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
        CREATE INDEX IF NOT EXISTS idx_shipments_customer ON shipments(customer_id);
        CREATE INDEX IF NOT EXISTS idx_shipments_status ON shipments(status);
        PRAGMA foreign_keys = ON;
        """
    )
    log.info("Migration complete: 'reply_sent' is now a valid shipment status.")


# --- Customers ---

def create_customer(name: str, customer_id: Optional[str] = None) -> str:
    cid = customer_id or str(uuid.uuid4())[:8]
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO customers (id, name) VALUES (?, ?)",
            (cid, name)
        )
    return cid


def get_all_customers() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM customers ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_customer(customer_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return dict(row) if row else None


def get_customer_rules(customer_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM customer_rules WHERE customer_id = ?", (customer_id,)).fetchall()
    return [dict(r) for r in rows]


def upsert_customer_rules(customer_id: str, rules: list[dict]) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM customer_rules WHERE customer_id = ?", (customer_id,))
        for r in rules:
            conn.execute(
                """
                INSERT INTO customer_rules (customer_id, field_name, expected_value, rule_type, is_critical, description)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_id,
                    r["field_name"],
                    r.get("expected_value"),
                    r["rule_type"],
                    1 if r.get("is_critical") else 0,
                    r.get("description", "")
                )
            )


# --- Shipments & Transaction Documents ---

def create_shipment(customer_id: str) -> str:
    """Creates a top-level transaction folder frame."""
    sid = str(uuid.uuid4())[:8]
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO shipments (id, customer_id, status) VALUES (?, ?, 'processing')",
            (sid, customer_id)
        )
    return sid


def add_document_to_shipment(shipment_id: str, doc_path: str, doc_filename: str, doc_type: str = "unknown") -> str:
    """Adds an isolated tracked document reference mapping to a transaction."""
    did = str(uuid.uuid4())[:8]
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) as cnt FROM shipment_documents 
            WHERE shipment_id = ? AND doc_filename = ?
            """,
            (shipment_id, doc_filename)
        )
        version = row.fetchone()["cnt"] + 1

        conn.execute(
            """
            INSERT INTO shipment_documents (id, shipment_id, doc_path, doc_filename, doc_type, version_number)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (did, shipment_id, doc_path, doc_filename, doc_type, version)
        )
    return did


def get_shipment_documents(shipment_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM shipment_documents WHERE shipment_id = ?", (shipment_id,)).fetchall()
    return [dict(r) for r in rows]


def update_shipment_status(shipment_id: str, status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE shipments SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, shipment_id)
        )


def get_shipment(shipment_id: str) -> Optional[dict]:
    """Fetch one shipment joined with its customer name and concatenated doc filenames."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT s.*, c.name AS customer_name,
                   (SELECT GROUP_CONCAT(sd.doc_filename, ', ')
                    FROM shipment_documents sd WHERE sd.shipment_id = s.id) AS doc_filename
            FROM shipments s
            JOIN customers c ON s.customer_id = c.id
            WHERE s.id = ?
            """,
            (shipment_id,),
        ).fetchone()
    return dict(row) if row else None


def get_all_shipments(limit: int = 200) -> list[dict]:
    """All shipments across customers, newest first, with customer name + doc filenames."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.*, c.name AS customer_name,
                   (SELECT GROUP_CONCAT(sd.doc_filename, ', ')
                    FROM shipment_documents sd WHERE sd.shipment_id = s.id) AS doc_filename
            FROM shipments s
            JOIN customers c ON s.customer_id = c.id
            ORDER BY s.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_shipments_by_customer(customer_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.*, 
                   (SELECT doc_filename FROM shipment_documents WHERE shipment_id = s.id ORDER BY uploaded_at ASC LIMIT 1) as doc_filename
            FROM shipments s 
            WHERE s.customer_id = ? 
            ORDER BY s.created_at DESC
            """, 
            (customer_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# --- Operations Writes & Reads ---

def save_extraction_results(shipment_id: str, document_id: Optional[str], extraction: dict) -> None:
    with get_connection() as conn:
        for field, data in extraction.items():
            conn.execute(
                """
                INSERT INTO extraction_results (shipment_id, document_id, field_name, field_value, confidence, extraction_method)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    shipment_id,
                    document_id,
                    field,
                    str(data.get("value") or ""),
                    float(data.get("confidence", 1.0)),
                    data.get("method", "llm")
                )
            )


def save_validation_results(shipment_id: str, document_id: Optional[str], validation: list[dict]) -> None:
    with get_connection() as conn:
        for v in validation:
            conn.execute(
                """
                INSERT INTO validation_results (shipment_id, document_id, field_name, status, found_value, expected_value, rule_type, is_critical, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    shipment_id,
                    document_id,
                    v["field_name"],
                    v["status"],
                    str(v.get("found_value") or ""),
                    str(v.get("expected_value") or ""),
                    v.get("rule_type"),
                    1 if v.get("is_critical") else 0,
                    v.get("detail", "")
                )
            )


def save_decision(shipment_id: str, decision: str, reasoning: str, draft_email: Optional[str] = None) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM decisions WHERE shipment_id = ?", (shipment_id,))
        conn.execute(
            """
            INSERT INTO decisions (shipment_id, decision, reasoning, draft_email)
            VALUES (?, ?, ?, ?)
            """,
            (shipment_id, decision, reasoning, draft_email)
        )


def get_extraction_results(shipment_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM extraction_results WHERE shipment_id = ?", (shipment_id,)).fetchall()
    return [dict(r) for r in rows]


def get_validation_results(shipment_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM validation_results WHERE shipment_id = ?", (shipment_id,)).fetchall()
    return [dict(r) for r in rows]


def get_decision(shipment_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM decisions WHERE shipment_id = ?", (shipment_id,)).fetchone()
    return dict(row) if row else None


def run_raw_query(sql: str, params: tuple = ()) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_schema_description() -> str:
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    if os.path.exists(schema_path):
        with open(schema_path) as f:
            return f.read()
    return ""


def seed_demo_customers():
    customers = [
        {
            "id": "CUST001",
            "name": "Global Freight Corp",
            "rules": [
                {"field_name": "incoterms", "expected_value": "CIF", "rule_type": "exact", "is_critical": True, "description": "Must be exactly CIF"},
                {"field_name": "port_of_loading", "expected_value": "SHANGHAI", "rule_type": "contains", "is_critical": True, "description": "Must ship from Shanghai port"},
                {"field_name": "hs_code", "expected_value": None, "rule_type": "not_null", "is_critical": False, "description": "HS Code required for customs clearance"},
                {"field_name": "gross_weight", "expected_value": None, "rule_type": "not_null", "is_critical": True, "description": "Weight mandatory parameter"},
            ]
        },
        {
            "id": "CUST002",
            "name": "Apex Logistics LLC",
            "rules": [
                {"field_name": "incoterms", "expected_value": "FOB|EXW", "rule_type": "regex", "is_critical": True, "description": "FOB or EXW shipping standard rules"},
                {"field_name": "consignee_name", "expected_value": "APEX LOGISTICS", "rule_type": "contains", "is_critical": True, "description": "Consignee identifier check"},
                {"field_name": "invoice_number", "expected_value": None, "rule_type": "not_null", "is_critical": False, "description": "Invoice mapping metadata track"},
            ]
        },
        {
            "id": "CUST003",
            "name": "Zenith Trading",
            "rules": [
                {"field_name": "incoterms", "expected_value": "FOB", "rule_type": "exact", "is_critical": True, "description": "Incoterms must be FOB"},
                {"field_name": "port_of_discharge", "expected_value": "ROTTERDAM", "rule_type": "contains", "is_critical": True, "description": "Discharge port must be Rotterdam"},
                {"field_name": "gross_weight", "expected_value": None, "rule_type": "not_null", "is_critical": True, "description": "Gross weight required"},
            ]
        },
        {
            "id": "CUST004",
            "name": "Oceanic Ventures",
            "rules": [
                {"field_name": "incoterms", "expected_value": "DDP|DAP", "rule_type": "regex", "is_critical": True, "description": "DDP or DAP required"},
                {"field_name": "gross_weight", "expected_value": None, "rule_type": "not_null", "is_critical": False, "description": "Weight must be stated"},
                {"field_name": "invoice_number", "expected_value": None, "rule_type": "not_null", "is_critical": True, "description": "Invoice number required"},
            ]
        },
        {
            "id": "CUST005",
            "name": "AutoParts Express",
            "rules": [
                {"field_name": "incoterms", "expected_value": "EXW", "rule_type": "exact", "is_critical": True, "description": "Incoterms must be EXW"},
                {"field_name": "hs_code", "expected_value": "^8708", "rule_type": "regex", "is_critical": True, "description": "HS code 8708 for auto parts"},
                {"field_name": "consignee_name", "expected_value": "AUTOPARTS EXPRESS", "rule_type": "contains", "is_critical": True, "description": "Consignee must match"},
                {"field_name": "invoice_number", "expected_value": None, "rule_type": "not_null", "is_critical": True, "description": "Invoice required"},
                {"field_name": "gross_weight", "expected_value": None, "rule_type": "not_null", "is_critical": False, "description": "Weight required"},
            ]
        },
    ]

    for c in customers:
        create_customer(c["name"], c["id"])
        upsert_customer_rules(c["id"], c["rules"])
    log.debug("Demo customers seeded successfully.")