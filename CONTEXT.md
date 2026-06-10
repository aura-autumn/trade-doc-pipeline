# Trade Document Pipeline — GoComet DAW Assignment
## Last updated: Session 2

---

## Goal
Build a multi-agent trade document validation pipeline for GoComet's Nova platform.
Part 1 of a 2-part assignment. 6-hour target.

---

## Current Status — What's Built and Working

```
[x] DB schema + database.py         (SQLite, seed data, all helpers)
[x] LLM client (swappable)          (groq | gemini | openai | ollama via LLM_PROVIDER)
[x] Extractor Agent                 (pdfplumber → Tesseract OCR → vision LLM fallback)
[x] Validator Agent                 (rule-based, match/mismatch/uncertain/missing/not_checked)
[x] Router Agent                    (rule-based decision, LLM reasoning + draft email)
[x] LangGraph graph wiring          (extract → validate → route, SQLite checkpointer)
[x] RAG layer                       (ChromaDB + SentenceTransformer, index + query)
[x] Text-to-SQL query layer         (nl_query.py, LLM generates SQL, RAG fallback)
[x] Streamlit UI                    (all pages working, stable rerenders)
[ ] Eval script                     (eval.py scaffolded, needs test docs + ground truth)
[ ] PRD
```

---

## HLD

```
[Input: PDF/Image + customer_id]
        |
        v
[LangGraph Pipeline]  ← SQLite checkpointer (crash recovery)
        |
   _____|______
  |            |
  v            v
[Extractor]  [Customer Rules DB] ← SQLite
  Layer 1: pdfplumber (text PDFs)
  Layer 2: Tesseract OCR (scanned/image PDFs)
  Layer 3: Vision LLM (last resort)
  Output: {field: {value, confidence, method}}
        |
        v
[Validator]
  Rule match per field: match | mismatch | uncertain | missing | not_checked
  confidence < 0.6 → always uncertain, never silently approved
  Fields with no customer rule → not_checked (surfaced, never dropped)
  Output: [{field, status, found_value, expected_value, is_critical, confidence, detail}]
        |
        v
[Router]
  Rule-based decision logic (no LLM for routing decision itself):
    0 mismatches + 0 missing + 0 critical_uncertain → auto_approve
    has_critical OR mismatches >= 2 OR missing >= 2   → draft_amendment
    otherwise                                          → flag_for_review
  LLM used only for: reasoning text + draft email/note
  extraction dict passed to router for email context (invoice no, consignee, ports)
        |
        v
[SQLite Store]
  Tables: customers, customer_rules, shipments, extraction_results,
          validation_results, decisions
        |
      __|___________
     |              |
     v              v
[Text-to-SQL]    [RAG Layer]
nl_query.py      retriever.py
NL → SQL →       ChromaDB (persistent, ./data/chroma)
structured       Embeddings: SentenceTransformer all-MiniLM-L6-v2
answer           (explicit ef passed to avoid ChromaDB's broken
                  onnxruntime default on Windows)
                 index_document() called after every pipeline run
                 answer_with_rag() for doc-specific questions
        |
        v
[Streamlit UI — ui/app.py]
Pages:
  ▶ Run Pipeline      — upload doc, select customer, run, see results in tabs
  📊 Shipment History — per-customer or all, with issues and decisions
  ❓ Query Layer       — NL query (Text-to-SQL + RAG), example buttons
  ⚙️ Manage Customers  — view rules, add new customer + rules
  📈 Eval             — run eval script, view per-field accuracy report

[Eval Script — eval/eval.py]
  Runs pipeline on labeled test docs in ./data/sample_docs/
  Compares extracted fields to ./data/ground_truth.json
  Reports: field accuracy, confidence calibration, flag rate, latency
```

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Orchestration | LangGraph | State persistence, crash recovery, conditional edges |
| Primary LLM | Groq llama-3.3-70b (default) | Free tier, fast, reliable text |
| Vision LLM | Groq llama-4-scout (vision) | Free tier vision, used as last resort |
| Alt providers | Gemini Flash / OpenAI / Ollama | Swappable via LLM_PROVIDER env var |
| PDF text extraction | pdfplumber | Fast, handles native text PDFs |
| OCR fallback | Tesseract (pytesseract) | Handles scanned docs, auto-detected on Windows |
| Storage | SQLite | Zero infra, queryable, LangGraph checkpointer |
| Vector store | ChromaDB (persistent) | Local, no infra |
| Embeddings | sentence-transformers all-MiniLM-L6-v2 | Free, local, explicit (bypasses ChromaDB onnxruntime bug) |
| UI | Streamlit | Fastest to ship |
| Language | Python 3.11+ | |

---

## LLM Client Design

- Swappable via `LLM_PROVIDER=groq|gemini|openai|ollama` in `.env`
- Default: `groq` (free tier, no credit card, ~30 req/min)
- `get_llm(vision=False)` → text model; `get_llm(vision=True)` → vision model
- `build_vision_message(file_path, prompt)` → multimodal HumanMessage
- Groq is text-only for structured extraction; vision only used when pdfplumber + OCR both fail

---

## DB Schema

```sql
CREATE TABLE customers (
    id TEXT PRIMARY KEY,
    name TEXT,
    created_at TIMESTAMP
);

CREATE TABLE customer_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT,
    field_name TEXT,         -- e.g. "incoterms", "consignee_name"
    expected_value TEXT,     -- exact match, pattern, or NULL for not_null rules
    rule_type TEXT,          -- exact | contains | regex | not_null
    is_critical BOOLEAN,     -- critical mismatches/uncertain → force draft_amendment
    description TEXT,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE shipments (
    id TEXT PRIMARY KEY,     -- UUID
    customer_id TEXT,
    doc_path TEXT,
    doc_filename TEXT,
    status TEXT,             -- processing | approved | flagged | amendment_drafted | error
    created_at TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE extraction_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT,
    field_name TEXT,
    field_value TEXT,
    confidence REAL,
    method TEXT,             -- pdfplumber | tesseract | llm
    FOREIGN KEY (shipment_id) REFERENCES shipments(id)
);

CREATE TABLE validation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT,
    field_name TEXT,
    status TEXT,             -- match | mismatch | uncertain | missing | not_checked
    found_value TEXT,
    expected_value TEXT,
    is_critical BOOLEAN,
    confidence REAL,
    detail TEXT,
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

## LangGraph State Shape (actual, in graph.py)

```python
class PipelineState(TypedDict):
    shipment_id: str
    customer_id: str
    doc_path: str
    doc_filename: str
    customer_name: str
    extraction: dict          # {field_name: {value, confidence, method}}
    validation: list          # [{field_name, status, found_value, expected_value,
                              #   rule_type, is_critical, confidence, detail}]
    decision: str             # auto_approve | flag_for_review | draft_amendment
    reasoning: str
    draft_email: str
    validation_summary: dict  # totals: matches, mismatches, missing, uncertain, not_checked
    error: str
    current_node: str
```

---

## Required Extracted Fields

- `consignee_name`
- `hs_code`
- `port_of_loading`
- `port_of_discharge`
- `incoterms`
- `description_of_goods`
- `gross_weight`
- `invoice_number`

---

## Demo Customers (seeded on every init_db call)

| ID | Name | Key Rules |
|---|---|---|
| CUST001 | Acme Imports Ltd | Incoterms=CIF, HS 8471x, POD=Nhava Sheva, consignee not_null |
| CUST002 | Global Tech Distributors | Incoterms=FOB, HS 8542x, POL=Shanghai |
| CUST003 | MediSupply Chain Co | Incoterms=DDP, HS 3004x, POD=Mumbai |
| CUST004 | FastFashion Retail | Incoterms=CFR, HS 6109x, POD=Chennai |
| CUST005 | AutoParts Express | Incoterms=EXW, HS 8708x |

---

## Project Structure

```
trade-doc-pipeline/
├── CONTEXT.md
├── README.md
├── requirements.txt
├── .env.example
├── db/
│   ├── schema.sql
│   └── database.py
├── llm/
│   └── client.py
├── agents/
│   ├── extractor.py
│   ├── validator.py
│   └── router.py
├── pipeline/
│   └── graph.py
├── rag/
│   └── retriever.py
├── query/
│   └── nl_query.py
├── eval/
│   └── eval.py
├── ui/
│   └── app.py
└── data/
    ├── chroma/               # ChromaDB persistent store
    ├── trade_docs.db         # SQLite main DB
    ├── trade_docs_checkpoints.db  # LangGraph SQLite checkpointer
    ├── sample_docs/          # add test docs here for eval
    └── ground_truth.json     # eval labels
```

---

## Key Constraints / Decisions

- Agent never sends email on its own. Human always reviews.
- Uncertain fields always surfaced, never silently approved.
- `confidence < 0.6` → uncertain regardless of value match.
- `not_checked` = field was extracted but no customer rule exists. Always shown to reviewer.
- Router decision is purely rule-based. LLM only writes reasoning and email text.
- `extraction` dict is passed through to router so email has real invoice/consignee context.
- ChromaDB uses explicit `SentenceTransformerEmbeddingFunction` — do NOT remove this or
  revert to the default, which has a broken onnxruntime lookup on Windows.
- `init_db()` + `seed_demo_customers()` wrapped in `@st.cache_resource` — runs once per
  server session, not on every Streamlit rerender.

---

## Known Issues / Next Steps

- Eval script needs real labeled test docs in `./data/sample_docs/` and ground truth JSON.
- pdfplumber only extracts 123 chars from `sample_messy.pdf` — this is expected for a low-quality
  scan; Tesseract kicks in as fallback. Add better test docs for higher confidence scores.
- RAG `answer_with_rag` requires the document to have been indexed first (happens automatically
  after pipeline run). Querying a shipment that was never run through the new app.py will return
  "No relevant content found."

---

## Notes / Decisions Log

- Session 1: HLD finalized, context file created, all agents + pipeline built.
- Session 2: Bug fixes — router missing `extraction` param in graph.py node_route call;
  ChromaDB onnxruntime fix (explicit SentenceTransformer ef); Streamlit rerender stability
  (cache_resource for init, cache_data for DB reads, RAG tab gated on button press);
  `use_container_width` → `width="stretch"` deprecation fix.