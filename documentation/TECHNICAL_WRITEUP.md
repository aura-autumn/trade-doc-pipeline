# Technical Write-up: Multi-Agent Trade Document Pipeline

 All numbers below come from running the POC on the sample documents in `data/sample_docs/`.

---

## 1 | Architecture (where data flows, where state lives)

```
        SU EMAIL ARRIVES  (1+ PDFs per shipment)        TRIGGER [Part 2]
          ├─ inbox/trigger.py       folder watcher (simulated inbox)
          ├─ inbox/gmail_trigger.py real Gmail poller (OAuth)
          └─ React "Simulate SU Email" / file upload  →  POST /api/pipeline/run
                              │
                              ▼
        ┌─────────────────────────────────────────────────────────┐
        │  run_pipeline(docs, customer_id)        [pipeline/graph] │
        │                                                          │
        │  per-doc, BEFORE the graph:                              │
        │     run_extractor(path) ─► {field:{value,confidence,     │
        │                              method}}                    │
        │     │  Extractor [agents/extractor.py]                   │
        │     │  PDF text layer: pdfplumber→PyMuPDF→pdfminer       │
        │     │  scan/empty:     PyMuPDF render → Tesseract OCR    │
        │     │  last resort:    VISION LLM (Gemini/GPT-4o/LLaVA)  │
        │     │  → text LLM extracts 8 fields + confidence         │
        │     ▼                                                    │
        │  LangGraph StateGraph  (thread_id = shipment_id)         │
        │     [extractor passthrough] ─► [validator] ─► [router]   │
        │         │ Validator (NO LLM, deterministic)              │
        │         │   exact/contains/regex/not_null + conf gate    │
        │         │   + CROSS-DOC reconciliation (>1 doc)          │
        │         ▼                                                │
        │     Router: rule-based decision + LLM reasoning/draft    │
        │         auto_approve | flag_for_review | draft_amendment │
        └───────────────────────────┬──────────────────────────────┘
                                     ▼
   ┌──────────────────────── SQLite (data/trade_docs.db) ─────────────────────┐
   │ customers · customer_rules · shipments · shipment_documents ·            │
   │ extraction_results · validation_results · decisions   ← STATE LIVES HERE │
   └───────────────┬───────────────────────────────────┬──────────────────────┘
                   ▼                                   ▼
        Text-to-SQL  [query/nl_query]        FAISS RAG  [rag/retriever]
        NL → SELECT (SELECT-only, LIMIT)     TF-IDF(10k,bigram)→SVD(128)
                   │                          per-shipment .pkl store
                   └───────────────┬──────────────────┘
                                   ▼
                FastAPI  [api/main.py]  ── JSON ──►  React/Vite SPA  [frontend/]
        Incoming(4 states) · Shipment History · Query · Manage Customers · Eval
        (legacy Streamlit ui/app.py + ui/cg_app.py still runnable as a fallback)
```

**Where state lives:** authoritative state is **SQLite**, written through at every stage and keyed by `shipment_id` (the same value used as the LangGraph `thread_id`). The graph checkpointer holds in-process state; RAG indexes are per-shipment pickles. One `shipment_id` ties together documents → extractions → validations → decision → RAG store.

---

## 2 | The three nastiest failure modes (real, from testing)

**A. Partial / abbreviated fields that look valid.** `sample_messy.pdf` carries `HS: 8471` (a truncated 4-digit stub of the real `84713000`) and `Consignee: ACME IMPORTS` (missing "LTD"), with Incoterms and gross weight absent entirely. The danger is a `contains` rule passing on a partial string, or a missing field sliding through. **Handling:** every field gets a confidence; null/empty values are confidence-capped ≤0.3; the deterministic validator surfaces *found vs expected* per field; absent required fields become `missing`/`uncertain`, never `match`. **Observed:** the messy doc routes to flag/amendment (missing Incoterms + gross weight surfaced), not approval.

**B. OCR garbage / low-confidence scans.** When a PDF has no text layer we render at 300 DPI and OCR with Tesseract, which on poor scans yields noisy strings. The risk is confidently extracting nonsense. **Handling:** an 80-alphanumeric-char floor before we trust OCR text; a 0.6 confidence gate that turns anything unsure into `uncertain` (blocks auto-approve); and a bounded **vision-LLM fallback** when OCR is empty. **Observed:** low-confidence fields surface as ⚠ Uncertain in the UI and never reach `auto_approve`.

**C. Cross-document inconsistency.** A real shipment is BOL + Invoice + Packing List; consignee, HS code and gross weight must agree across all three. `batch_002/` reproduces this `invoice_good` (CIF, Port Said) vs `invoice_mismatch` (FOB, Singapore) share a consignee but disagree on Incoterms and discharge port. Per-doc validation alone would miss it. **Handling:** when a shipment has >1 doc, the validator builds a per-field cross-map and flags any field whose normalized value differs across docs as a **critical cross-doc discrepancy**. **Observed:** uploading both as one shipment produces `cross_doc_discrepancy_*` rows and forces an amendment.

---

## 3 | Observability (production for 50 customers)

**Trace one shipment, email → verified output:** everything is keyed by `shipment_id`. Given one ID you can replay the full chain `shipment_documents` (what arrived) → `extraction_results` (every field, confidence, extraction `method`) → `validation_results` (per-field status, found/expected, cross-doc rows) → `decisions` (decision, reasoning, draft email) → the per-shipment RAG store → any NL queries run against it. **Implemented now:** a central logging layer (`core/logging_config.py`) routes every stage — extractor, validator, router, RAG, query, the inbox triggers and the FastAPI request layer — to a coloured console stream and a rotating file at `logs/trade_pipeline.log`, with `LOG_LEVEL` switchable to `DEBUG` for full-step tracing. The API additionally logs the resolved DB path and seed counts at startup. For production I'd add **LangSmith tracing** for per-agent spans + token counts, attach `shipment_id` + `customer_id` to every log record, and persist `latency_ms`/`cost` per stage.

**Dashboard would show:** STP rate and false-auto-approve rate (the two headline numbers); flag/amendment rates; per-customer volume and pending-queue depth; p50/p95 latency per doc; cost per doc and the **vision-fallback share** (the cost driver); confidence calibration (confident-wrong rate); top mismatched fields; and LLM error/retry rate.

---

## 4 | Cost (back-of-envelope, per document)

| Stage | Work | Approx cost |
|---|---|---|
| Text extraction | pdfplumber / Tesseract OCR (local CPU) | ~$0 |
| Extractor LLM | ~2k input + ~0.3k output tokens, Flash-tier | ~$0.0005–0.002 |
| Router LLM | ~1.5k input + ~0.4k output tokens | ~$0.0005–0.001 |
| **Text-path total** | | **~$0.001–0.003 / doc** |
| Vision fallback | GPT-4o-class, 1–2k image tokens/page | **~$0.02–0.10+ / doc** |

**Where it blows up:** the **vision fallback**. A scanned, multi-page doc routed to a flagship vision model costs 10–50× the text path. NL queries add a small Text-to-SQL call only when used. **Control:** keep extraction text-first (OCR is local and free); reserve vision for genuine fallback; use a *Flash-tier* vision model rather than GPT-4o; downscale render DPI and cap pages; cache by document hash so re-runs are free; truncate inputs (already 8k chars).

---

## 5 | Latency (slowest hop)

Measured ~**3.7 s/doc** end-to-end on the clean samples. Breakdown: native-text extraction is <0.5 s, but **OCR on scanned pages (300 DPI, per page)** is 1–3 s/page, and the two **sequential LLM round-trips** (extractor, then router) are ~1–2 s each. So the slowest hops are (a) OCR rendering for scans and (b) the serial LLM calls.

**What I'd do to fix it:** the multi-doc loop extracts documents **sequentially**  parallelize it (asyncio/threads) so a 3-doc shipment isn't 3× the latency; only the extractor and router actually need the model, and they're already minimal; drop OCR DPI to ~200 with a quality check; and use a faster model tier where accuracy allows. The validator is free (pure Python), so it's never the bottleneck.

---

## 6 | What I'd do differently with a week instead of a day

- **Durable checkpointing:** swap `MemorySaver` for a SQLite/Postgres LangGraph checkpointer so recovery is graph-level, not just via re-readable SQLite rows.
- **Parallel multi-doc extraction** and a proper async pipeline (biggest latency win).
- **Real embeddings for RAG** (replace TF-IDF/SVD) so source-snippet retrieval for "what does the doc say about X" is genuinely strong it matters most for Part 2's discrepancy-detail view.
- **A labeled calibration set** to tune the 0.6 threshold per field and per customer, instead of one global cut-off.
- **Rule versioning + audit UI**, and a **human-review queue** that captures every override as a training signal.
- **The Part 2 email trigger** wired end-to-end, since the trigger and not the model is what makes this a real workflow.
- **Honest hardening:** retry/timeout budgets per provider, a dead-letter path for docs that fail all extraction methods, and unit tests on the deterministic validator (its correctness is the system's trust anchor).

---

## 7 | Part 2 — Wiring it into the real CG workflow

Part 1 ran on upload. Part 2's thesis is that **the trigger, not the model, is the
missing piece**, so the work was connecting the existing agents to a real arrival
event and a CG-usable interface — no agent code rewritten.

**Trigger (the missing piece).** Three interchangeable front-ends feed the *same*
`run_pipeline(docs, customer_id)`: a folder watcher (`inbox/trigger.py`, the
simulated inbox the brief asks for), a real Gmail poller (`inbox/gmail_trigger.py`,
OAuth, resolves customer from sender domain), and the React "Simulate SU Email" /
upload form. All persist a result to SQLite + `inbox/results/` and surface in the
CG UI's Inbox Watcher Feed. The agent **never sends** — every reply goes through
the editable Draft Reply panel and a manual *Mark as Sent*.

**Multi-doc + cross-validation.** A shipment is 1..N attachments. Extraction runs
per document before the graph; the deterministic validator reconciles
consignee/HS-code/gross-weight/invoice across all docs and emits
`cross_doc_discrepancy_*` rows, shown as a dedicated red banner above the field
table. `email_multi_doc.json` (3 docs) exercises this path.

**UI migration (Streamlit → React + FastAPI).** The CG UI moved to a React/Vite SPA
over a FastAPI JSON API (`api/main.py`) that calls the existing `pipeline/`, `db/`,
`rag/`, `query/`, `eval/` directly. The verification view is a single flat table —
Field · Value · Confidence · Status · Expected · Critical · Detail — chosen over
click-to-expand so a CG operator reads the whole shipment in one glance; the
cross-doc section and draft reply sit directly below, and a per-shipment RAG chat
grounds follow-up questions. The blocking pipeline call runs in FastAPI's threadpool
(sync route) so a multi-file run never freezes the server.

**Three Part-2 bugs worth recording (found by running it, not hypotheticals):**
- *RAG only indexed the first attachment.* `index_document` short-circuited if a
  shipment was already indexed, so docs 2..N of a multi-doc shipment were silently
  dropped from retrieval. Fixed with `index_documents(paths, shipment_id)` that
  builds one store over the union of all the shipment's chunks.
- *Empty DB depending on launch directory.* `DB_PATH`/`RAG_STORE` were relative to
  the process CWD, so launching uvicorn from a subdir opened a different, empty
  SQLite file — looking like "customers and history vanished." Anchored all data
  paths to the project root (`core/paths.py`).
- *`reply_sent` rejected by an old CHECK constraint.* Databases created before
  `reply_sent` existed couldn't accept *Mark as Sent*, and the original migration's
  self-check (`UPDATE … WHERE 1=0`) never triggered the constraint so it always
  false-passed. `init_db()` now detects the stale DDL in `sqlite_master` and rebuilds
  the table in place, preserving rows — self-healing on startup.
