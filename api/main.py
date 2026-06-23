"""
FastAPI backend for the Nova Trade-Doc Pipeline (Part 2).

This is the migration target from the Streamlit UIs (ui/app.py, ui/cg_app.py).
It exposes the EXISTING Part 1/Part 2 logic — pipeline/, db/, rag/, query/, eval/
— over a clean JSON API that the React frontend (frontend/) consumes. Nothing
in the agent pipeline is rewritten; this layer only orchestrates and serialises.

Run:
    uvicorn api.main:app --reload --port 8000

Endpoints (all under /api):
    GET  /health
    GET  /config
    GET  /customers                      list customers (+ rule counts)
    GET  /customers/{id}/rules           rules for one customer
    POST /customers                      create customer + rules
    GET  /samples                        list pre-built sample emails
    POST /pipeline/run                   multipart upload → run full pipeline
    POST /pipeline/run-sample            run a pre-built sample email
    GET  /shipments                      history (filter by customer/status)
    GET  /shipments/{id}                 full verification detail
    POST /shipments/{id}/mark-sent       mark draft reply as sent
    POST /shipments/{id}/ask             RAG + pipeline-context Q&A
    POST /query                          NL → SQL / RAG query
    GET  /gmail/status                   Gmail watcher connection status
    GET  /gmail/feed                     shipments processed by the watcher
    GET  /eval/report                    last eval report
    POST /eval/run                       run offline eval
"""

from __future__ import annotations

import os
import json
import shutil
import tempfile
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Make repo-root imports work no matter where uvicorn is launched from.
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

load_dotenv()

from core.logging_config import get_logger, setup_logging
from db.database import (
    init_db,
    seed_demo_customers,
    get_all_customers,
    get_customer,
    get_customer_rules,
    create_customer,
    upsert_customer_rules,
    get_shipment,
    get_all_shipments,
    get_shipments_by_customer,
    get_shipment_documents,
    get_extraction_results,
    get_validation_results,
    get_decision,
    update_shipment_status,
)
from pipeline.graph import run_pipeline
from agents.validator import get_validation_summary
from query.nl_query import run_nl_query, EXAMPLE_QUERIES
from rag.retriever import index_documents, query_document

setup_logging()
log = get_logger("api")

SAMPLE_DIR = ROOT / "inbox" / "sample_emails"
RESULTS_DIR = ROOT / "inbox" / "results"
GMAIL_TOKEN = ROOT / os.getenv("GMAIL_TOKEN_PATH", "gmail_token.json")
GMAIL_CREDS = ROOT / os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
EVAL_REPORT = ROOT / "data" / "eval_report.json"

app = FastAPI(title="Nova Trade-Doc Pipeline API", version="2.0.0")

# The React dev server runs on a different origin (Vite default :5173).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    from db.database import DB_PATH
    init_db()
    seed_demo_customers()
    n_customers = len(get_all_customers())
    n_shipments = len(get_all_shipments())
    log.info("API ready. LLM=%s | DB=%s | %d customer(s), %d shipment(s)",
             os.getenv("LLM_PROVIDER", "groq"), DB_PATH, n_customers, n_shipments)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _assemble_detail(shipment_id: str) -> Optional[dict]:
    """
    Rebuild the full verification view for a shipment straight from SQLite, in the
    SAME shape the live pipeline returns — so the frontend has one renderer for
    both freshly-run and historical shipments.

    Validation rows don't persist confidence, so we join it back from the
    extraction rows by (document_id, field_name).
    """
    ship = get_shipment(shipment_id)
    if not ship:
        return None

    docs = get_shipment_documents(shipment_id)
    doc_id_to_name = {d["id"]: d["doc_filename"] for d in docs}

    extraction: dict[str, dict] = {}
    conf_map: dict[tuple, float] = {}
    for r in get_extraction_results(shipment_id):
        fname = doc_id_to_name.get(r["document_id"], r["document_id"] or "document")
        extraction.setdefault(fname, {})[r["field_name"]] = {
            "value": r["field_value"] or None,
            "confidence": r["confidence"],
            "method": r["extraction_method"],
        }
        conf_map[(r["document_id"], r["field_name"])] = r["confidence"]

    validation = []
    for v in get_validation_results(shipment_id):
        conf = conf_map.get((v["document_id"], v["field_name"]))
        if conf is None:
            conf = 1.0 if v["status"] == "match" else 0.0
        validation.append({
            "document_id": v["document_id"],
            "field_name": v["field_name"],
            "status": v["status"],
            "found_value": v["found_value"] or None,
            "expected_value": v["expected_value"] or None,
            "rule_type": v["rule_type"],
            "is_critical": bool(v["is_critical"]),
            "detail": v["detail"],
            "confidence": conf,
        })

    decision = get_decision(shipment_id) or {}

    return {
        "shipment_id": shipment_id,
        "customer_id": ship["customer_id"],
        "customer_name": ship["customer_name"],
        "status": ship["status"],
        "reply_sent": ship["status"] == "reply_sent",
        "created_at": ship["created_at"],
        "updated_at": ship["updated_at"],
        "doc_count": len(docs),
        "doc_names": [d["doc_filename"] for d in docs],
        "decision": decision.get("decision", ""),
        "reasoning": decision.get("reasoning", ""),
        "draft_email": decision.get("draft_email", ""),
        "extraction": extraction,
        "validation": validation,
        "validation_summary": get_validation_summary(validation),
    }


def _save_uploads_to_temp(files: list[UploadFile]) -> list[tuple[str, str]]:
    """Persist uploaded files to a temp dir; return [(tmp_path, original_name)]."""
    tmp_dir = tempfile.mkdtemp(prefix="nova_upload_")
    docs: list[tuple[str, str]] = []
    for uf in files:
        suffix = Path(uf.filename).suffix
        dest = os.path.join(tmp_dir, uf.filename or f"doc{suffix}")
        with open(dest, "wb") as out:
            shutil.copyfileobj(uf.file, out)
        docs.append((dest, uf.filename or os.path.basename(dest)))
    return docs


def _run_and_index(docs: list[tuple[str, str]], customer_id: str,
                   email_meta: dict | None = None) -> dict:
    """Run the pipeline, index RAG for all docs, persist a result JSON, return detail."""
    start = time.time()
    result = run_pipeline(docs, customer_id)
    elapsed = time.time() - start

    try:
        index_documents([p for p, _ in docs], result["shipment_id"])
    except Exception as exc:
        log.warning("RAG indexing skipped for %s: %s", result.get("shipment_id"), exc)

    detail = _assemble_detail(result["shipment_id"]) or {}
    detail["elapsed"] = round(elapsed, 2)
    if email_meta:
        detail.update(email_meta)

    # Persist a result JSON (unifies uploads with the watcher feed + audit trail).
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(RESULTS_DIR / f"{result['shipment_id']}.json", "w") as f:
            json.dump(detail, f, indent=2, default=str)
    except Exception as exc:
        log.warning("Could not persist result JSON for %s: %s", result["shipment_id"], exc)

    return detail


# ──────────────────────────────────────────────────────────────────────────────
# Meta
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict:
    return {
        "llm_provider": os.getenv("LLM_PROVIDER", "groq"),
        "gmail": _gmail_status(),
        "example_queries": EXAMPLE_QUERIES,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Customers
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/customers")
def list_customers() -> list[dict]:
    customers = get_all_customers()
    for c in customers:
        c["rule_count"] = len(get_customer_rules(c["id"]))
    return customers


@app.get("/api/customers/{customer_id}/rules")
def customer_rules(customer_id: str) -> list[dict]:
    if not get_customer(customer_id):
        raise HTTPException(404, f"Customer not found: {customer_id}")
    return get_customer_rules(customer_id)


class RuleIn(BaseModel):
    field_name: str
    expected_value: Optional[str] = None
    rule_type: str
    is_critical: bool = False
    description: str = ""


class CustomerIn(BaseModel):
    name: str
    id: Optional[str] = None
    rules: list[RuleIn] = []


@app.post("/api/customers")
def create_customer_endpoint(payload: CustomerIn) -> dict:
    if not payload.name.strip():
        raise HTTPException(400, "Customer name is required.")
    cid = create_customer(payload.name, payload.id or None)
    if payload.rules:
        upsert_customer_rules(cid, [r.model_dump() for r in payload.rules])
    log.info("Created/updated customer %s (%s) with %d rule(s)", payload.name, cid, len(payload.rules))
    return {"id": cid, "name": payload.name, "rule_count": len(payload.rules)}


# ──────────────────────────────────────────────────────────────────────────────
# Samples
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/samples")
def list_samples() -> list[dict]:
    out = []
    if SAMPLE_DIR.exists():
        for f in sorted(SAMPLE_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                out.append({
                    "name": f.stem,
                    "from": data.get("from", ""),
                    "subject": data.get("subject", ""),
                    "customer_id": data.get("customer_id", ""),
                    "attachments": data.get("attachments", []),
                })
            except Exception as exc:
                log.warning("Bad sample email %s: %s", f.name, exc)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────────────────────────────────────

# NOTE: declared as a plain `def` (not `async def`) on purpose. run_pipeline is a
# long, blocking call (LLM + OCR per document). A sync def route is run by FastAPI
# in a worker threadpool, so a multi-file run never freezes the event loop / the
# rest of the UI. An async def here would block every other request until it finished.
@app.post("/api/pipeline/run")
def pipeline_run(
    customer_id: str = Form(...),
    sender: str = Form("supplier@example.com"),
    subject: str = Form("Shipment Documents"),
    files: list[UploadFile] = File(...),
) -> dict:
    if not get_customer(customer_id):
        raise HTTPException(404, f"Customer not found: {customer_id}")
    if not files:
        raise HTTPException(400, "At least one document is required.")

    docs = _save_uploads_to_temp(files)
    log.info("Upload run: %d doc(s) for %s from %s", len(docs), customer_id, sender)
    email_meta = {
        "email_from": sender,
        "email_subject": subject,
        "received_at": datetime.utcnow().isoformat(),
    }
    try:
        return _run_and_index(docs, customer_id, email_meta)
    except Exception as exc:
        log.error("Pipeline run failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Pipeline failed: {exc}")
    finally:
        for p, _ in docs:
            try:
                shutil.rmtree(os.path.dirname(p), ignore_errors=True)
                break
            except Exception:
                pass


class SampleRun(BaseModel):
    name: str


@app.post("/api/pipeline/run-sample")
def pipeline_run_sample(payload: SampleRun) -> dict:
    sample_path = SAMPLE_DIR / f"{payload.name}.json"
    if not sample_path.exists():
        raise HTTPException(404, f"Sample not found: {payload.name}")

    email = json.loads(sample_path.read_text())
    base = sample_path.parent

    tmp_dir = tempfile.mkdtemp(prefix="nova_sample_")
    docs: list[tuple[str, str]] = []
    for att in email.get("attachments", []):
        att_p = Path(att) if Path(att).is_absolute() else base / att
        if att_p.exists():
            dest = os.path.join(tmp_dir, att_p.name)
            shutil.copyfile(str(att_p), dest)
            docs.append((dest, att_p.name))
    if not docs:
        raise HTTPException(400, "Sample has no resolvable attachments.")

    customer_id = email.get("customer_id", "CUST001")
    email_meta = {
        "email_from": email.get("from", "supplier@example.com"),
        "email_subject": email.get("subject", "(no subject)"),
        "received_at": datetime.utcnow().isoformat(),
    }
    try:
        return _run_and_index(docs, customer_id, email_meta)
    except Exception as exc:
        log.error("Sample run failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Pipeline failed: {exc}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────────
# Shipments
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/shipments")
def shipments(customer_id: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    rows = get_shipments_by_customer(customer_id) if customer_id else get_all_shipments()
    # get_shipments_by_customer doesn't include customer_name; backfill it.
    for r in rows:
        if "customer_name" not in r:
            cust = get_customer(r["customer_id"])
            r["customer_name"] = cust["name"] if cust else r["customer_id"]
    if status:
        rows = [r for r in rows if r["status"] == status]
    return rows


@app.get("/api/shipments/{shipment_id}")
def shipment_detail(shipment_id: str) -> dict:
    detail = _assemble_detail(shipment_id)
    if not detail:
        raise HTTPException(404, f"Shipment not found: {shipment_id}")
    return detail


@app.post("/api/shipments/{shipment_id}/mark-sent")
def mark_sent(shipment_id: str) -> dict:
    if not get_shipment(shipment_id):
        raise HTTPException(404, f"Shipment not found: {shipment_id}")
    update_shipment_status(shipment_id, "reply_sent")
    log.info("Shipment %s marked reply_sent (CG clicked send)", shipment_id)
    return {"shipment_id": shipment_id, "status": "reply_sent"}


class SnippetIn(BaseModel):
    query: str
    top_k: int = 2


@app.post("/api/shipments/{shipment_id}/snippets")
def shipment_snippets(shipment_id: str, payload: SnippetIn) -> dict:
    """
    Raw RAG retrieval for one field (no LLM) — backs the per-field "source snippet"
    expander in the verification table. Returns the document chunks the value was
    grounded in, so CG can see where a flagged field came from.
    """
    if not get_shipment(shipment_id):
        raise HTTPException(404, f"Shipment not found: {shipment_id}")
    try:
        snippets = query_document(payload.query, shipment_id, n_results=payload.top_k)
    except Exception as exc:
        log.warning("Snippet retrieval failed for %s: %s", shipment_id, exc)
        snippets = []
    return {"snippets": snippets}


class AskIn(BaseModel):
    question: str


@app.post("/api/shipments/{shipment_id}/ask")
def shipment_ask(shipment_id: str, payload: AskIn) -> dict:
    """RAG over the shipment's documents, grounded with the pipeline results."""
    detail = _assemble_detail(shipment_id)
    if not detail:
        raise HTTPException(404, f"Shipment not found: {shipment_id}")

    try:
        snippets = query_document(payload.question, shipment_id, n_results=3)
        doc_context = "\n\n---\n\n".join(s["text"] for s in snippets) if snippets else "No document snippets available."
    except Exception as exc:
        log.warning("RAG retrieval failed for %s: %s", shipment_id, exc)
        doc_context = "RAG retrieval unavailable."

    val_lines = [
        f"  {v['field_name'].replace('_', ' ').title()}: {v['status'].upper()}"
        f" | found='{v.get('found_value') or '—'}' | expected='{v.get('expected_value') or '—'}'"
        f" | critical={'YES' if v.get('is_critical') else 'no'} | confidence={v.get('confidence', 0):.0%}"
        for v in detail["validation"]
    ]
    s = detail["validation_summary"]
    pipeline_context = (
        "PIPELINE RESULTS:\n"
        f"Decision: {detail.get('decision', '').upper()}\n"
        f"Reasoning: {detail.get('reasoning', '')}\n"
        f"Summary: {s.get('matches', 0)} match, {s.get('mismatches', 0)} mismatch, "
        f"{s.get('missing', 0)} missing, {s.get('uncertain', 0)} uncertain\n"
        "\nFIELD-BY-FIELD:\n" + "\n".join(val_lines)
    )

    from llm.client import get_llm
    prompt = (
        "You are a trade document validation assistant. Answer using the PIPELINE "
        "RESULTS and DOCUMENT SNIPPETS only.\n\n"
        + pipeline_context
        + "\n\nDOCUMENT SNIPPETS:\n" + doc_context
        + "\n\nQuestion: " + payload.question
    )
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            resp = get_llm(vision=False).invoke(prompt)
        answer = resp.content if hasattr(resp, "content") else str(resp)
    except Exception as exc:
        log.error("RAG answer failed for %s: %s", shipment_id, exc)
        answer = f"Error answering question: {exc}"

    return {"question": payload.question, "answer": answer, "snippets": doc_context}


# ──────────────────────────────────────────────────────────────────────────────
# NL Query
# ──────────────────────────────────────────────────────────────────────────────

class QueryIn(BaseModel):
    question: str
    shipment_id: Optional[str] = None


@app.post("/api/query")
def nl_query(payload: QueryIn) -> dict:
    if not payload.question.strip():
        raise HTTPException(400, "Question is required.")
    return run_nl_query(payload.question, shipment_id=payload.shipment_id or None)


# ──────────────────────────────────────────────────────────────────────────────
# Gmail
# ──────────────────────────────────────────────────────────────────────────────

def _gmail_status() -> dict:
    """⚪ no creds · 🟡 creds but no token (needs auth) · 🟢 authenticated."""
    if not GMAIL_CREDS.exists():
        return {"state": "not_configured", "label": "Not configured",
                "detail": "credentials.json not found in project root."}
    if not GMAIL_TOKEN.exists():
        return {"state": "needs_auth", "label": "Needs authentication",
                "detail": "Run `python -m inbox.gmail_trigger` once to authenticate."}
    return {"state": "connected", "label": "Connected",
            "detail": "Gmail watcher authenticated."}


@app.get("/api/gmail/status")
def gmail_status() -> dict:
    return _gmail_status()


@app.get("/api/gmail/feed")
def gmail_feed(limit: int = 10) -> list[dict]:
    if not RESULTS_DIR.exists():
        return []
    files = sorted(RESULTS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    out = []
    for rf in files[:limit]:
        try:
            r = json.loads(rf.read_text())
            out.append({
                "shipment_id": r.get("shipment_id", rf.stem),
                "email_subject": r.get("email_subject", "(no subject)"),
                "email_from": r.get("email_from", ""),
                "received_at": r.get("received_at", ""),
                "decision": r.get("decision", ""),
                "customer_name": r.get("customer_name", ""),
            })
        except Exception:
            pass
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Eval
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/eval/report")
def eval_report() -> dict:
    if EVAL_REPORT.exists():
        return json.loads(EVAL_REPORT.read_text())
    return {}


@app.post("/api/eval/run")
def eval_run() -> dict:
    from eval.eval import run_eval
    log.info("Running offline eval...")
    report = run_eval()
    if not report:
        raise HTTPException(400, "No test documents found for eval.")
    return report
