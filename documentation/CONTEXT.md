# Trade Document Pipeline
# Part 1 + Part 2 complete · UI migrated to React + FastAPI

Part 2 adds the email trigger, multi-doc cross-validation in a CG verification UI,
draft-reply handoff, and a React/Vite frontend over a FastAPI backend. The Part 1
agents (extractor / validator / router), DB, RAG and query layer are reused
unchanged — Part 2 only wires and exposes them.

---

## Architecture

```
[Upload: PDF/Image(s)] + [Customer ID]
        |
        v
[run_pipeline(docs, customer_id)]  ← accepts list of (path, filename) tuples
        |
        ├── for each doc: run_extractor() → extraction_map {doc_id: {field: {value, confidence}}}
        |
        v
[LangGraph: extractor node (passthrough) → validator node → router node]
        |
        ├── validator: run_validator(extraction_map, rules) cross-doc reconciliation included
        ├── router: rule-based decision + LLM reasoning + draft email
        |
        v
[SQLite: shipments, shipment_documents, extraction_results, validation_results, decisions]
        |
      __|___________
     |              |
     v              v
[Text-to-SQL]    [FAISS RAG]
NL → SQL →       TF-IDF + SVD + FAISS
structured answer  per-shipment pkl store
```

---

## File Map

| File | Purpose | Last changed |
|---|---|---|
| `db/schema.sql` | DB schema | Session 2 |
| `db/database.py` | All DB helpers + seed data | Session 2 |
| `llm/client.py` | Swappable LLM (groq/gemini/openai/ollama) | Session 2 |
| `agents/extractor.py` | Vision LLM + text fallback, confidence scores | Session 2 |
| `agents/validator.py` | Rule engine + cross-doc reconciliation | Session 2 |
| `agents/router.py` | Decision logic + LLM email draft | Session 2 |
| `pipeline/graph.py` | LangGraph graph + multi-doc run_pipeline | Session 3 (fixed) |
| `rag/retriever.py` | FAISS + TF-IDF/SVD, no torch dependency | Session 2 |
| `query/nl_query.py` | Text-to-SQL + RAG routing | Session 2 |
| `eval/eval.py` | Offline eval script | path-anchored |
| `core/logging_config.py` | Central coloured + rotating-file logging | Part 2 |
| `core/paths.py` | Project-root path anchoring (DB/RAG/eval) | Part 2 |
| `api/main.py` | FastAPI backend (pipeline/db/rag/query/eval over JSON) | Part 2 |
| `frontend/` | React + Vite SPA (5 pages, the CG verification UI) | Part 2 |
| `inbox/trigger.py` | Folder watcher (simulated SU inbox) | Part 2 |
| `inbox/gmail_trigger.py` | Real Gmail poller (OAuth) | Part 2 |
| `ui/app.py`, `ui/cg_app.py` | Legacy Streamlit UIs (fallback only) | Part 1/2 |

---

## Part 2 additions / migration

- **Trigger:** folder watcher + Gmail poller + in-UI simulate, all feeding the same `run_pipeline`.
- **UI:** Streamlit → React/Vite SPA over FastAPI (`api/main.py`). Flat verification table (no click-to-expand), cross-doc banner, editable draft reply, per-shipment RAG chat.
- **Logging:** every stage logs via `core/logging_config`; console + `logs/trade_pipeline.log`; `LOG_LEVEL` env.
- **RAG multi-doc fix:** `index_documents()` indexes ALL of a shipment's docs (was: only the first).
- **Path anchoring:** `core/paths.resolve_data_path` ties DB/RAG/eval to project root regardless of launch CWD.
- **Schema self-heal:** `init_db()` migrates the `shipments.status` CHECK to allow `reply_sent`.
- **Install:** `requirements.txt` is the only-what's-imported set (resolves on 3.11–3.13); `requirements-full.txt` is the heavy frozen env, archived for reference only.

---

## Key Decisions (locked)

- **LLM provider**: groq (default, free) | gemini | openai | ollama : swap via `LLM_PROVIDER` env var
- **Gemini model**: `gemini-2.0-flash`
- **Checkpointer**: `MemorySaver`
- **RAG**: FAISS + TF-IDF/SVD
- **Multi-doc**: extraction runs before graph, passed as pre-populated state. Validator does cross-doc reconciliation natively.
- **run_pipeline signature**: `run_pipeline(docs: list[tuple[str,str]], customer_id: str)`
- **Extraction return**: keyed by filename for UI, keyed by doc_id internally

---

## DB Schema (current)

```
customers(id, name, created_at)
customer_rules(id, customer_id, field_name, expected_value, rule_type, is_critical, description)
shipments(id, customer_id, status, created_at, updated_at)
shipment_documents(id, shipment_id, doc_path, doc_filename, doc_type, version_number, uploaded_at)
extraction_results(id, shipment_id, document_id, field_name, field_value, confidence, extraction_method)
validation_results(id, shipment_id, document_id, field_name, status, found_value, expected_value, rule_type, is_critical, detail)
decisions(id, shipment_id, decision, reasoning, draft_email, created_at)
```

---

## Bugs fixed (Session 3)

1. Gemini model `gemini-1.5-flash` → `gemini-2.0-flash`
2. `SqliteSaver.from_conn_string()` returns context manager → replaced with `MemorySaver`
3. `run_router` called with wrong positional args (summary passed as shipment_id)
4. `decision_packet["decision_status"]` key didn't exist → mapped via status_map
5. `doc_filename` KeyError in shipment history (removed from shipments table) → fetch from shipment_documents via subquery
6. Query layer button result lost on rerender → stored in session_state
7. Multi-doc upload: `file_uploader` now `accept_multiple_files=True`, `run_pipeline` accepts list of docs

---

## Demo Customers

| ID | Name | Key Rules |
|---|---|---|
| CUST001 | Global Freight Corp | Incoterms=CIF, POL=Shanghai, HS not null, weight not null |
| CUST002 | Apex Logistics LLC | Incoterms=FOB or EXW (regex), consignee contains APEX LOGISTICS |
| CUST003 | Zenith Trading | Incoterms=FOB, POD=Rotterdam, weight not null |
| CUST004 | Oceanic Ventures | Incoterms=DDP or DAP (regex), invoice not null |
| CUST005 | AutoParts Express | Incoterms=EXW, HS=^8708, consignee=AUTOPARTS EXPRESS |

---

## Run instructions

```bash
# Python 3.10+ (3.13 recommended)
python3.13 -m venv venv && source venv/bin/activate
pip install -r requirements.txt           # working set; requirements-full.txt = heavy archive
cp .env.example .env                       # set LLM_PROVIDER + API key

# Backend + frontend (Part 2 — current)
uvicorn api.main:app --reload --port 8000  # terminal 1
cd frontend && npm install && npm run dev  # terminal 2 → http://localhost:5173

# Legacy Streamlit (fallback): streamlit run ui/app.py
```

---