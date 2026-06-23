# Trade Document Pipeline

Multi-agent pipeline for trade document extraction, validation, and routing.

## Architecture

```
Upload (PDF/Image) → Extractor → Validator → Router → SQLite
                                                  ↓
                                         NL Query (Text-to-SQL + RAG)
                                                  ↓
                                           Streamlit UI
```

**Three LangGraph agents:**
- **Extractor**: vision LLM extracts 8 fields with per-field confidence. Text fallback for PDFs when vision fails.
- **Validator**: rule-based validation per customer (exact / contains / regex / not_null). Cross-document reconciliation for multi-doc shipments. Never silently approves uncertain fields (confidence < 0.6).
- **Router**: rule-based decision (auto_approve / flag_for_review / draft_amendment) + LLM-generated reasoning and draft amendment email.

## Setup

**Requires Python 3.10+** (3.11 or 3.13 recommended; the code uses 3.10+ syntax).

```bash
git clone <repo>
cd trade-doc-pipeline
python3.13 -m venv venv && source venv/bin/activate
pip install -r requirements.txt   # only what the code imports — resolves on 3.11–3.13
cp .env.example .env        # set LLM_PROVIDER and API key. Default: groq.
python -m db.database       # init DB + seed 5 demo customers (also runs on app start)
python documentation/generate_samples.py   # create sample test PDFs (optional)
```

> **Install note:** `requirements.txt` lists only what the pipeline actually imports
> (FAISS + scikit-learn for RAG, not torch/chromadb). The original full frozen
> environment is archived in `requirements-full.txt` for reference — you don't need
> it, and it pins heavy packages (`onnxruntime`, `torch`, `docling`) that may have no
> installable wheel on current Python.

Then run the app — **React + FastAPI** (current, see below) or the legacy
`streamlit run ui/app.py`.
## System Dependencies

### Tesseract-OCR (Required for scanned PDFs)
Used for PDF OCR fallback when the text layer is empty or poor quality. Install from:
- **Windows:** https://github.com/UB-Mannheim/tesseract/wiki (installer sets PATH automatically in many cases)
- **Mac:** `brew install tesseract`
- **Linux:** `sudo apt-get install tesseract-ocr`

**Verify:**
```bash 
tesseract --version
```

The pipeline auto-detects Tesseract at common install locations. If detection fails, set `TESSERACT_CMD` manually or reinstall.

## LLM Providers

| Provider | Cost | Vision | Setup |
|---|---|---|---|
| `groq` | Free | Via LLaMA 4 Scout | [console.groq.com](https://console.groq.com) no credit card |
| `gemini` | Free tier | Yes | [aistudio.google.com](https://aistudio.google.com) |
| `openai` | Paid | Yes | OpenAI API key |
| `ollama` | Free (local) | llava | `ollama pull llava && ollama pull llama3.2` |

Set in `.env`:
```
LLM_PROVIDER=groq
GROQ_API_KEY=your_key_here
```

## Usage

1. Open http://localhost:8501
2. **Run Pipeline** : select customer, upload one or more trade docs, click Run
3. **Shipment History** : browse past runs, see per-field issues
4. **Query Layer** : ask natural language questions ("how many flagged this week?")
5. **Manage Customers** : add customers and define validation rules
6. **Eval** : run offline accuracy evaluation

## Multi-Document Support

Upload multiple files (BOL + Commercial Invoice + Packing List) for one shipment. The validator cross-checks fields like consignee, HS code, and gross weight across all documents and flags inconsistencies.

## Project Structure

```
trade-doc-pipeline/
├── db/
│   ├── schema.sql          # SQLite schema
│   └── database.py         # DB helpers + 5 seeded demo customers
├── llm/
│   └── client.py           # Swappable LLM client
├── agents/
│   ├── extractor.py        # Vision LLM + text fallback + confidence scoring
│   ├── validator.py        # Rule engine + cross-doc reconciliation
│   └── router.py           # Decision + LLM draft email
├── pipeline/
│   └── graph.py            # LangGraph state graph + run_pipeline()
├── rag/
│   └── retriever.py        # FAISS + TF-IDF/SVD (no torch dependency)
├── query/
│   └── nl_query.py         # Text-to-SQL + RAG query routing
├── eval/
│   └── eval.py             # Offline evaluation script
├── ui/
│   └── app.py              # Streamlit UI
└── data/
    ├── sample_docs/        # Add test documents here
    └── ground_truth.json   # Labels for eval
```

## Demo Customers (pre-seeded)

| ID | Customer | Key Rules |
|---|---|---|
| CUST001 | Global Freight Corp | CIF, Shanghai, HS not null |
| CUST002 | Apex Logistics LLC | FOB or EXW, consignee match |
| CUST003 | Zenith Trading | FOB, Rotterdam discharge |
| CUST004 | Oceanic Ventures | DDP or DAP, invoice required |
| CUST005 | AutoParts Express | EXW, HS 8708x, consignee match |

## Design Decisions

**Why three agents?** Each has a distinct failure mode. Extractor fails on bad docs. Validator fails on wrong rules. Router fails on ambiguous logic. Separate agents = independent debuggability, replaceability, and eval.

**Why LangGraph?** Explicit state, crash recovery via checkpointer, conditional edges for error routing. State is visible at every step.

**Why FAISS over ChromaDB?** ChromaDB pulls `onnxruntime` which has DLL issues on Windows. FAISS + scikit-learn TF-IDF is lightweight, zero system dependencies, runs anywhere.

**Why Text-to-SQL + RAG?** Text-to-SQL answers structured questions about the database ("how many flagged"). RAG answers document-content questions ("what does the doc say about consignee"). Different query types, different tools.

**Confidence threshold:** `< 0.6` = uncertain. Uncertain fields are always surfaced and never silently approved.


----------------------------------------------------------------------------------------------------------------


# Part 2 — CG Verification Workflow

Extends Part 1 with: **email trigger** → **cross-doc validation** → **CG verification UI** → **draft reply**.

---

## What's new in Part 2

| Part 1 | Part 2 (added) |
|--------|---------------|
| Upload doc in browser | Email arrives → agent wakes up automatically |
| Per-doc validation | Cross-doc consistency check (BOL + Invoice + Packing List) |
| Streamlit pipeline UI | CG-facing verification UI with 4 distinct states |
| Draft email in a tab | Dedicated draft reply screen — editable, never auto-sent |

---

## Setup (Part 2)

Part 2 adds a **FastAPI** backend (Python) and a **React/Vite** frontend (needs
**Node 18+**). Everything else is reused from Part 1.

```bash
# 1. Python env (3.10+)
python3.13 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. Copy .env.example → .env, add your API key
cp .env.example .env
# Set at least one: GROQ_API_KEY or GEMINI_API_KEY or OPENAI_API_KEY
# Set LLM_PROVIDER=groq (or gemini / openai)

# 3. Init DB (also runs automatically on API startup, which self-heals old schemas)
python -c "from db.database import init_db, seed_demo_customers; init_db(); seed_demo_customers()"

# 4. Frontend deps
cd frontend && npm install && cd ..
```

---

## Run the CG Verification UI (Part 2) — React + FastAPI (current)

The Part 2 UI has been migrated off Streamlit to a **React/JSX single-page app**
backed by a **FastAPI** server that calls the existing `pipeline/`, `db/`, `rag/`,
`query/` and `eval/` code directly — nothing in the agent pipeline was rewritten.

```bash
# Terminal 1 — backend (serves the pipeline/DB/RAG/query/eval over JSON)
uvicorn api.main:app --reload --port 8000
#   API docs (Swagger): http://localhost:8000/docs

# Terminal 2 — frontend (React + Vite dev server, proxies /api → :8000)
cd frontend
npm install      # first run only
npm run dev
#   open http://localhost:5173
```

Pages: **Incoming** (upload / sample / Gmail feed → 4 CG states), **Shipment
History** (all transactions, filter by customer + status), **Query** (NL → SQL/RAG),
**Manage Customers** (add customers + rules), **Eval** (run + report).

The verification view shows every extracted field inline — Field · Value ·
Confidence bar · Status · Expected · Critical · Detail — with the **cross-document
consistency** section directly below the table and the **editable draft reply**
panel below that. The agent never sends; CG clicks **Mark as Sent**.

### Logging
Every stage (extractor → validator → router, RAG, query, inbox triggers, API)
logs through a central config (`core/logging_config.py`). Logs stream to the
console (coloured) and to `./logs/trade_pipeline.log` (rotating). Set
`LOG_LEVEL=DEBUG` in `.env` for full-step tracing.

### Legacy Streamlit UI (Part 1 / fallback)
The original Streamlit UIs still work: `streamlit run ui/app.py` (Part 1) or
`streamlit run ui/cg_app.py` (Part 2). The React + FastAPI stack above is the
current Part 2 interface.

---

## Run the Email Trigger (the "agent wakes on email" piece)

The trigger — not the model — is Part 2's missing piece. Three ways to fire it:

```bash
# A. Folder watcher (simulated inbox)
python -m inbox.trigger                                       # the watcher
cp inbox/sample_emails/email_multi_doc.json inbox/incoming/   # SU "sends" an email
#   → watcher runs Extract → Validate → Route, stores the result, and it appears
#     in the React UI's "📡 Inbox Watcher Feed" (Incoming page). Click View for the
#     full verification result + editable draft reply.

# A'. One-shot (no watcher loop)
python -m inbox.trigger inbox/sample_emails/email_mismatch.json

# B. In-UI "Simulate SU Email" — upload / sample buttons on the Incoming page (no watcher)

# C. Real Gmail inbox — see documentation/GMAIL_SETUP.md
python -m inbox.gmail_trigger
```

The assignment accepts a simulated inbox ("watch a folder or simulate an inbox").
Mode A demonstrates the trigger firing on arrival; Mode C wires the same logic to a
real Gmail account. Sample outcomes: `email_clean.json` → auto-approve,
`email_mismatch.json` → amendment, `email_multi_doc.json` → cross-doc check fires.

---

## The 4 CG States (UI walkthrough)

### State 1 — Incoming
Simulate an SU email arriving: fill in sender, subject, customer, and attach PDFs.
Or click **"Load Sample"** to use a pre-built email with existing docs.
The agent shows a live progress bar (Extracting → Validating → Routing).

### State 2 — Verification Result
Left panel shows every validated field as a clickable row:
- 🟢 Match | 🟡 Uncertain | 🔴 Mismatch / Missing
- Confidence bar per field
- Cross-document inconsistencies shown at the top if present

### State 3 — Discrepancy Detail
Click any field → right panel shows:
- What was found in the document (exact extracted value)
- What was expected (customer rule)
- Confidence score
- Rule type explanation
- Whether the field is critical

### State 4 — Draft Reply
Agent-drafted email to SU — editable text area.
CG reads, edits, clicks **"Mark as Sent"**.
**Agent never sends on its own. This is non-negotiable.**

---

## Email Trigger — How It Works

```
inbox/incoming/     ← drop .json email files here
inbox/processed/    ← moved here after successful run
inbox/failed/       ← moved here if pipeline errors
inbox/results/      ← {shipment_id}.json result stored here
```

Email JSON format:
```json
{
  "from": "supplier@example.com",
  "subject": "Shipment docs for Acme",
  "customer_id": "CUST001",
  "attachments": ["path/to/invoice.pdf", "path/to/packing_list.pdf"]
}
```

---

## Cross-Document Consistency

When a shipment has multiple documents (BOL + Invoice + Packing List), the validator checks that key fields — `consignee_name`, `hs_code`, `gross_weight`, `invoice_number` — are consistent across all files. Any conflict surfaces as a `cross_doc_discrepancy_<field>` mismatch result, shown at the top of the verification view with a red banner.

This logic already existed in the Part 1 validator (`run_validator` in `agents/validator.py`) — Part 2 exposes it in the CG UI with dedicated visual treatment.

---

## Sample Emails

Three pre-built emails in `inbox/sample_emails/`:

| File | Customer | Docs | Expected outcome |
|------|----------|------|-----------------|
| `email_clean.json` | Global Freight Corp | sample_clean.pdf | Auto-approve or minor flag |
| `email_mismatch.json` | Apex Logistics LLC | sample_mismatch.pdf | Amendment drafted |
| `email_multi_doc.json` | Zenith Trading | 3 × invoice PDFs | Cross-doc check fires |

---

## Architecture (what changed from Part 1)

```
SU Email (JSON)
    ↓
inbox/trigger.py          ← NEW: folder watcher, simulated inbox
    ↓
pipeline/graph.py         ← UNCHANGED: LangGraph pipeline
  ├── agents/extractor.py ← UNCHANGED
  ├── agents/validator.py ← UNCHANGED (cross-doc already implemented)
  └── agents/router.py    ← UNCHANGED
    ↓
db/database.py            ← UNCHANGED: SQLite storage
    ↓
ui/cg_app.py              ← NEW: CG verification UI (4 states)
```

Part 2 adds exactly two files. Everything else is reused from Part 1.

---

## North-Star Metric

**Median CG validation cycle time** — time from SU email received to CG clicking send on the reply.
Target: <15 min. Current manual baseline: 60–240 min.

---

## Gmail Integration (Real Inbox Trigger)

Part 2 includes a real Gmail inbox watcher — not just a folder simulation. Full setup guide: **[GMAIL_SETUP.md](./GMAIL_SETUP.md)**

Quick version:
```bash
# 1. Enable Gmail API in Google Cloud Console (free)
# 2. Download credentials.json → place in project root
# 3. Configure routing in .env:
#    GMAIL_CUSTOMER_MAP=acme-exports.com:CUST001,fastfreight.net:CUST002

# 4. Run the watcher (browser auth on first run only)
python -m inbox.gmail_trigger

# 5. Send a real email with a PDF attachment to your Gmail
#    → pipeline fires automatically within 10 seconds
#    → result appears in CG UI under 📡 Gmail Feed
```

The CG UI sidebar shows Gmail connection status (🟢/🟡/⚪) at all times.
If you prefer not to set up Gmail, the folder watcher and the in-UI upload form both work as fallbacks.

## What Changed Since Initial Part 2 Build

| Fix | What changed |
|-----|-------------|
| Gmail trigger | New `inbox/gmail_trigger.py` — real inbox polling with OAuth |
| Customer router | `resolve_customer()` in gmail_trigger — domain → customer_id mapping |
| Mark as Sent | Now persists `reply_sent` status to DB, reflected in queue |
| Unused import | `get_shipments_by_customer` removed from cg_app.py imports |
| Queue status | `reply_sent` added to filter options, metrics, and status icons |
| Sidebar | Gmail connection status indicator (🟢/🟡/⚪) |
| Gmail feed | Live feed panel on Incoming page shows watcher-processed results |
| gitignore | credentials.json, gmail_token.json, runtime dirs protected |

# If you ran Part 1 first and have an existing DB, run this once:
python -m db.migrate_add_reply_sent