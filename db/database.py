import sqlite3
import os
import uuid
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./data/trade_docs.db")


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
    print(f"DB initialized at {DB_PATH}")


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


# --- Customer Rules ---

def upsert_customer_rules(customer_id: str, rules: list[dict]):
    """Replace all rules for a customer."""
    with get_connection() as conn:
        conn.execute("DELETE FROM customer_rules WHERE customer_id = ?", (customer_id,))
        for rule in rules:
            conn.execute(
                """INSERT INTO customer_rules 
                   (customer_id, field_name, expected_value, rule_type, is_critical, description)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    customer_id,
                    rule["field_name"],
                    rule.get("expected_value"),
                    rule["rule_type"],
                    1 if rule.get("is_critical") else 0,
                    rule.get("description", ""),
                )
            )


def get_customer_rules(customer_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM customer_rules WHERE customer_id = ?", (customer_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# --- Shipments ---

def create_shipment(customer_id: str, doc_path: str, doc_filename: str) -> str:
    sid = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO shipments (id, customer_id, doc_path, doc_filename, status)
               VALUES (?, ?, ?, ?, 'processing')""",
            (sid, customer_id, doc_path, doc_filename)
        )
    return sid


def update_shipment_status(shipment_id: str, status: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE shipments SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.now(), shipment_id)
        )


def get_shipment(shipment_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
    return dict(row) if row else None


def get_shipments_by_customer(customer_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM shipments WHERE customer_id = ? ORDER BY created_at DESC",
            (customer_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# --- Extraction Results ---

def save_extraction_results(shipment_id: str, extraction: dict):
    """extraction: {field_name: {value, confidence, method}}"""
    with get_connection() as conn:
        conn.execute("DELETE FROM extraction_results WHERE shipment_id = ?", (shipment_id,))
        for field_name, data in extraction.items():
            conn.execute(
                """INSERT INTO extraction_results 
                   (shipment_id, field_name, field_value, confidence, extraction_method)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    shipment_id,
                    field_name,
                    str(data.get("value", "")),
                    float(data.get("confidence", 0.0)),
                    data.get("method", "llm"),
                )
            )


def get_extraction_results(shipment_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM extraction_results WHERE shipment_id = ?", (shipment_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# --- Validation Results ---

def save_validation_results(shipment_id: str, validation: list[dict]):
    with get_connection() as conn:
        conn.execute("DELETE FROM validation_results WHERE shipment_id = ?", (shipment_id,))
        for v in validation:
            conn.execute(
                """INSERT INTO validation_results
                   (shipment_id, field_name, status, found_value, expected_value, rule_type, is_critical)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    shipment_id,
                    v["field_name"],
                    v["status"],
                    v.get("found_value", ""),
                    v.get("expected_value", ""),
                    v.get("rule_type", ""),
                    1 if v.get("is_critical") else 0,
                )
            )


def get_validation_results(shipment_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM validation_results WHERE shipment_id = ?", (shipment_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# --- Decisions ---

def save_decision(shipment_id: str, decision: str, reasoning: str, draft_email: Optional[str] = None):
    with get_connection() as conn:
        conn.execute("DELETE FROM decisions WHERE shipment_id = ?", (shipment_id,))
        conn.execute(
            """INSERT INTO decisions (shipment_id, decision, reasoning, draft_email)
               VALUES (?, ?, ?, ?)""",
            (shipment_id, decision, reasoning, draft_email)
        )


def get_decision(shipment_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM decisions WHERE shipment_id = ?", (shipment_id,)
        ).fetchone()
    return dict(row) if row else None


# --- Query helpers for NL layer ---

def run_raw_query(sql: str) -> list[dict]:
    """Run arbitrary read-only SQL. Used by the NL query layer."""
    if any(kw in sql.upper() for kw in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER"]):
        raise ValueError("Only SELECT queries are allowed")
    with get_connection() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def get_schema_description() -> str:
    """Return schema as string for LLM context in Text-to-SQL."""
    return """
Tables:
- customers(id, name, created_at)
- customer_rules(id, customer_id, field_name, expected_value, rule_type, is_critical, description)
- shipments(id, customer_id, doc_path, doc_filename, status, created_at, updated_at)
  status values: processing | approved | flagged | amendment_drafted | error
- extraction_results(id, shipment_id, field_name, field_value, confidence, extraction_method)
- validation_results(id, shipment_id, field_name, status, found_value, expected_value, rule_type, is_critical)
  status values: match | mismatch | uncertain | not_checked | missing
- decisions(id, shipment_id, decision, reasoning, draft_email, created_at)
  decision values: auto_approve | flag_for_review | draft_amendment

Relationships:
- shipments.customer_id -> customers.id
- extraction_results.shipment_id -> shipments.id
- validation_results.shipment_id -> shipments.id
- decisions.shipment_id -> shipments.id
- customer_rules.customer_id -> customers.id
"""


# --- Seed data ---

def seed_demo_customers():
    """Seed 5-6 demo customers with rules for testing."""
    customers = [
        {
            "id": "CUST001",
            "name": "Acme Imports Ltd",
            "rules": [
                {"field_name": "incoterms", "expected_value": "CIF", "rule_type": "exact", "is_critical": True, "description": "Incoterms must be CIF"},
                {"field_name": "consignee_name", "expected_value": "ACME IMPORTS LTD", "rule_type": "contains", "is_critical": True, "description": "Consignee must contain company name"},
                {"field_name": "port_of_discharge", "expected_value": "NHAVA SHEVA", "rule_type": "contains", "is_critical": False, "description": "Discharge port must be Nhava Sheva"},
                {"field_name": "hs_code", "expected_value": "^8471", "rule_type": "regex", "is_critical": True, "description": "HS code must start with 8471"},
                {"field_name": "invoice_number", "expected_value": None, "rule_type": "not_null", "is_critical": True, "description": "Invoice number must be present"},
                {"field_name": "gross_weight", "expected_value": None, "rule_type": "not_null", "is_critical": False, "description": "Gross weight must be present"},
            ]
        },
        {
            "id": "CUST002",
            "name": "Global Tech Distributors",
            "rules": [
                {"field_name": "incoterms", "expected_value": "FOB", "rule_type": "exact", "is_critical": True, "description": "Incoterms must be FOB"},
                {"field_name": "port_of_loading", "expected_value": "SHANGHAI", "rule_type": "contains", "is_critical": False, "description": "Loading port must be Shanghai"},
                {"field_name": "hs_code", "expected_value": "^8542", "rule_type": "regex", "is_critical": True, "description": "HS code must start with 8542 (semiconductors)"},
                {"field_name": "consignee_name", "expected_value": "GLOBAL TECH", "rule_type": "contains", "is_critical": True, "description": "Consignee must contain Global Tech"},
                {"field_name": "invoice_number", "expected_value": None, "rule_type": "not_null", "is_critical": True, "description": "Invoice number required"},
            ]
        },
        {
            "id": "CUST003",
            "name": "MediSupply Chain Co",
            "rules": [
                {"field_name": "incoterms", "expected_value": "DDP", "rule_type": "exact", "is_critical": True, "description": "Incoterms must be DDP for medical goods"},
                {"field_name": "hs_code", "expected_value": "^3004", "rule_type": "regex", "is_critical": True, "description": "HS code must start with 3004 (pharmaceuticals)"},
                {"field_name": "consignee_name", "expected_value": "MEDISUPPLY", "rule_type": "contains", "is_critical": True, "description": "Consignee must match"},
                {"field_name": "port_of_discharge", "expected_value": "MUMBAI", "rule_type": "contains", "is_critical": False, "description": "Discharge must be Mumbai"},
                {"field_name": "description_of_goods", "expected_value": None, "rule_type": "not_null", "is_critical": True, "description": "Goods description mandatory"},
                {"field_name": "invoice_number", "expected_value": None, "rule_type": "not_null", "is_critical": True, "description": "Invoice number required"},
            ]
        },
        {
            "id": "CUST004",
            "name": "FastFashion Retail",
            "rules": [
                {"field_name": "incoterms", "expected_value": "CFR", "rule_type": "exact", "is_critical": False, "description": "Incoterms should be CFR"},
                {"field_name": "hs_code", "expected_value": "^6109", "rule_type": "regex", "is_critical": True, "description": "HS code must start with 6109 (apparel)"},
                {"field_name": "port_of_discharge", "expected_value": "CHENNAI", "rule_type": "contains", "is_critical": False, "description": "Discharge port Chennai"},
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
        print(f"Seeded customer: {c['name']} ({c['id']}) with {len(c['rules'])} rules")


if __name__ == "__main__":
    init_db()
    seed_demo_customers()
