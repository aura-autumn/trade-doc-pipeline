-- Trade Document Pipeline Schema

CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS customer_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    expected_value TEXT,
    rule_type TEXT NOT NULL CHECK(rule_type IN ('exact', 'contains', 'regex', 'not_null')),
    is_critical INTEGER DEFAULT 0,  -- 1 = critical, forces amendment
    description TEXT,               -- human readable rule description
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS shipments (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    doc_path TEXT NOT NULL,
    doc_filename TEXT,
    status TEXT DEFAULT 'processing' CHECK(status IN ('processing', 'approved', 'flagged', 'amendment_drafted', 'error')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS extraction_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_value TEXT,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    extraction_method TEXT DEFAULT 'llm',  -- llm | docling
    FOREIGN KEY (shipment_id) REFERENCES shipments(id)
);

CREATE TABLE IF NOT EXISTS validation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('match', 'mismatch', 'uncertain', 'not_checked', 'missing')),
    found_value TEXT,
    expected_value TEXT,
    rule_type TEXT,
    is_critical INTEGER DEFAULT 0,
    FOREIGN KEY (shipment_id) REFERENCES shipments(id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('auto_approve', 'flag_for_review', 'draft_amendment')),
    reasoning TEXT NOT NULL,
    draft_email TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (shipment_id) REFERENCES shipments(id)
);

-- Indexes for query performance
CREATE INDEX IF NOT EXISTS idx_shipments_customer ON shipments(customer_id);
CREATE INDEX IF NOT EXISTS idx_shipments_status ON shipments(status);
CREATE INDEX IF NOT EXISTS idx_extraction_shipment ON extraction_results(shipment_id);
CREATE INDEX IF NOT EXISTS idx_validation_shipment ON validation_results(shipment_id);
CREATE INDEX IF NOT EXISTS idx_rules_customer ON customer_rules(customer_id);
