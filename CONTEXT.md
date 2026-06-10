# Trade Document Pipeline — GoComet DAW Assignment
## Last updated: Session 1

---

## Goal
Build a multi-agent trade document validation pipeline for GoComet's Nova platform.
Part 1 of a 2-part assignment. 6-hour target.

---

## HLD

```
[Input: PDF/Image + customer_id]
        |
        v
[LangGraph Pipeline]
        |
   _____|______
  |            |
  v            v
[Extractor]  [Customer Rules] <-- SQLite (rules per customer)
Vision LLM (primary: Gemini Flash free tier)
Docling (fallback for low-confidence/bad quality docs)
Output: JSON + per-field confidence score
        |
        v
[Validator]
Rule match per field: match | mismatch | uncertain
Never silent approve. Uncertain fields always surfaced.
Output: field-level validation result
        |
        v
[Router]
Decision: auto-approve | flag-for-review | draft-amendment
Produces reasoning + draft amendment email if needed
        |
        v
[SQLite Store]
Tables: shipments, validation_results, customer_rules, documents
        |
      __|___________
     |              |
     v              v
[Text-to-SQL]    [RAG Layer]
NL → SQL →       Embed original docs (ChromaDB)
structured answer  Answer "where in doc" questions
                   Source snippet retrieval
        |
        v
[Streamlit UI]
- Customer selector / creator
- Rule set editor per customer
- Upload doc (PDF or image)
- Live LangGraph pipeline state
- Field table: value + confidence
- Validation result per field
- Decision + reasoning
- Draft amendment email (editable, never auto-send)
- NL query box

[Eval Script]
- Runs pipeline on labeled test docs
- Compares extracted fields to ground truth
- Reports: field accuracy, confidence calibration, flag rate
```

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Orchestration | LangGraph | State persistence, crash recovery, showcases skills |
| Primary LLM | Gemini Flash (free tier) | No cost, vision capable |
| Fallback LLM | Ollama + LLaVA (local) | No cost, works offline |
| PDF extraction fallback | Docling | Better structured extraction for bad quality docs |
| Storage | SQLite | Zero infra, queryable |
| Vector store | ChromaDB | Local, no infra |
| Embeddings | sentence-transformers | Free, local |
| UI | Streamlit | Fastest to ship |
| Language | Python 3.11+ | |

---

## LLM Client Design
- Swappable via env var: `LLM_PROVIDER=gemini|openai|ollama`
- All agents use the same client interface
- Vision calls routed to vision-capable model automatically

---

## DB Schema

```sql
-- Customers and their rule sets
CREATE TABLE customers (
    id TEXT PRIMARY KEY,
    name TEXT,
    created_at TIMESTAMP
);

CREATE TABLE customer_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT,
    field_name TEXT,         -- e.g. "incoterms", "consignee_name"
    expected_value TEXT,     -- exact match or pattern
    rule_type TEXT,          -- exact | contains | regex | not_null
    is_critical BOOLEAN,     -- critical mismatches force amendment
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

-- Document runs
CREATE TABLE shipments (
    id TEXT PRIMARY KEY,
    customer_id TEXT,
    doc_path TEXT,
    status TEXT,             -- processing | approved | flagged | amendment_sent
    created_at TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE extraction_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT,
    field_name TEXT,
    field_value TEXT,
    confidence REAL,         -- 0.0 to 1.0
    FOREIGN KEY (shipment_id) REFERENCES shipments(id)
);

CREATE TABLE validation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT,
    field_name TEXT,
    status TEXT,             -- match | mismatch | uncertain | not_checked
    found_value TEXT,
    expected_value TEXT,
    FOREIGN KEY (shipment_id) REFERENCES shipments(id)
);

CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT,
    decision TEXT,           -- auto_approve | flag_for_review | draft_amendment
    reasoning TEXT,
    draft_email TEXT,
    created_at TIMESTAMP,
    FOREIGN KEY (shipment_id) REFERENCES shipments(id)
);
```

---

## Required Extracted Fields (minimum bar)
- consignee_name
- hs_code
- port_of_loading
- port_of_discharge
- incoterms
- description_of_goods
- gross_weight
- invoice_number

---

## LangGraph State Shape

```python
class PipelineState(TypedDict):
    shipment_id: str
    customer_id: str
    doc_path: str
    doc_text: str                    # raw extracted text
    extraction: dict                 # field -> {value, confidence}
    validation: list                 # [{field, status, found, expected}]
    decision: str                    # auto_approve | flag_for_review | draft_amendment
    reasoning: str
    draft_email: str
    error: str                       # non-empty if pipeline errored
    current_node: str                # for crash recovery visibility
```

---

## Project Structure

```
trade-doc-pipeline/
├── CONTEXT.md                  # this file
├── README.md
├── requirements.txt
├── .env.example
├── db/
│   ├── schema.sql
│   └── database.py             # DB init + helpers
├── llm/
│   └── client.py               # swappable LLM client
├── agents/
│   ├── extractor.py
│   ├── validator.py
│   └── router.py
├── pipeline/
│   └── graph.py                # LangGraph definition
├── rag/
│   └── retriever.py            # ChromaDB + embeddings
├── query/
│   └── nl_query.py             # Text-to-SQL + RAG query
├── eval/
│   └── eval.py                 # offline eval script
├── ui/
│   └── app.py                  # Streamlit app
├── data/
│   ├── sample_docs/            # test documents
│   └── ground_truth.json       # for eval
└── tests/
```

---

## Build Order
- [x] CONTEXT.md
- [ ] Project structure + requirements.txt + .env.example
- [ ] DB schema + database.py
- [ ] LLM client (swappable)
- [ ] Extractor Agent
- [ ] Validator Agent
- [ ] Router Agent
- [ ] LangGraph graph wiring
- [ ] RAG layer
- [ ] Text-to-SQL query layer
- [ ] Streamlit UI
- [ ] Eval script
- [ ] README
- [ ] PRD (last)

---

## Key Constraints / Decisions
- Agent never sends email on its own. CG always reviews.
- Uncertain fields always surfaced, never silently approved.
- Low confidence (< 0.6) = uncertain, not approved.
- Customer rules stored in DB, not hardcoded.
- LLM provider swappable via env var.
- Docling used as fallback when extraction confidence is low overall.

---

## Notes / Decisions Log
- Session 1: HLD finalized, context file created, starting code.
