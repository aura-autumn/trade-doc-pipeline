"""
LangGraph Pipeline — multi-document aware
extract (pre-populated) -> validate -> route
"""

import os
from typing import TypedDict, Optional
from dotenv import load_dotenv

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from agents.validator import run_validator, get_validation_summary
from agents.router import run_router
from db.database import (
    create_shipment,
    add_document_to_shipment,
    update_shipment_status,
    save_extraction_results,
    save_validation_results,
    save_decision,
    get_customer,
    get_customer_rules,
    init_db,
)

load_dotenv()


# ── State ────────────────────────────────────────────────────────────────────

class PipelineState(TypedDict):
    shipment_id: str
    customer_id: str
    customer_name: str
    extraction: dict        # {doc_id: {field: {value, confidence, method}}}
    validation: list
    decision: str
    reasoning: str
    draft_email: str
    validation_summary: dict
    error: str
    current_node: str
    active_document_id: str


# ── Nodes ────────────────────────────────────────────────────────────────────

def node_extract(state: PipelineState) -> dict:
    """
    Extraction is pre-populated before the graph runs (multi-doc loop in run_pipeline).
    This node is a no-op passthrough — it exists so the graph structure is visible
    and so single-doc callers can still trigger extraction here if needed.
    """
    if state.get("extraction"):
        print(f"[Extractor] Pre-populated extraction for {len(state['extraction'])} doc(s) — skipping re-extract.")
        return {"current_node": "extract"}

    # Fallback: single doc extraction (backward compat)
    from agents.extractor import run_extractor
    from db.database import get_shipment_documents
    docs = get_shipment_documents(state["shipment_id"])
    doc_id = state.get("active_document_id")
    target = next((d for d in docs if d["id"] == doc_id), docs[0] if docs else None)
    if not target:
        return {"error": "No documents found for shipment.", "current_node": "extract"}

    fields = run_extractor(target["doc_path"])
    save_extraction_results(state["shipment_id"], target["id"], fields)
    return {"extraction": {target["id"]: fields}, "current_node": "extract"}


def node_validate(state: PipelineState) -> dict:
    """Validate all extracted fields against customer rules + cross-doc reconciliation."""
    print(f"[Validator] Validating {len(state['extraction'])} doc(s) for shipment {state['shipment_id']}")
    rules = get_customer_rules(state["customer_id"])
    validation_results = run_validator(state["extraction"], rules)
    summary = get_validation_summary(validation_results)

    # Persist per-doc results
    for doc_id in state["extraction"]:
        doc_results = [v for v in validation_results if v.get("document_id") == doc_id]
        save_validation_results(state["shipment_id"], doc_id, doc_results)
    # Persist cross-doc results
    cross_results = [v for v in validation_results if v.get("document_id") is None]
    if cross_results:
        save_validation_results(state["shipment_id"], None, cross_results)

    return {"validation": validation_results, "validation_summary": summary, "current_node": "validate"}


def node_route(state: PipelineState) -> dict:
    """Decide: auto_approve | flag_for_review | draft_amendment."""
    print(f"[Router] Deciding for shipment {state['shipment_id']}")
    # Flatten extraction for router context (highest confidence per field wins)
    flat = {}
    for fields in state["extraction"].values():
        for field, data in fields.items():
            if field not in flat or data.get("confidence", 0) > flat[field].get("confidence", 0):
                flat[field] = data

    packet = run_router(
        state["validation"],
        shipment_id=state["shipment_id"],
        customer_name=state["customer_name"],
        extraction=flat,
    )
    save_decision(state["shipment_id"], packet["decision"], packet["reasoning"], packet.get("draft_email"))
    status_map = {"auto_approve": "approved", "flag_for_review": "flagged", "draft_amendment": "amendment_drafted"}
    update_shipment_status(state["shipment_id"], status_map.get(packet["decision"], "flagged"))

    return {
        "decision": packet["decision"],
        "reasoning": packet["reasoning"],
        "draft_email": packet.get("draft_email", ""),
        "validation_summary": packet.get("summary", state.get("validation_summary", {})),
        "current_node": "route",
    }


# ── Graph ────────────────────────────────────────────────────────────────────

def build_pipeline(checkpointer=None):
    wf = StateGraph(PipelineState)
    wf.add_node("extractor", node_extract)
    wf.add_node("validator", node_validate)
    wf.add_node("router", node_route)
    wf.set_entry_point("extractor")
    wf.add_edge("extractor", "validator")
    wf.add_edge("validator", "router")
    wf.add_edge("router", END)
    return wf.compile(checkpointer=checkpointer or MemorySaver())


# ── Public API ────────────────────────────────────────────────────────────────

def run_pipeline(
    docs: list[tuple[str, str]],  # [(tmp_path, original_filename), ...]
    customer_id: str,
) -> dict:
    """
    Run the full pipeline for one or more documents.
    All docs belong to one shipment and are validated together.
    """
    init_db()
    customer = get_customer(customer_id)
    if not customer:
        raise ValueError(f"Customer not found: {customer_id}")

    shipment_id = create_shipment(customer_id)
    print(f"[Pipeline] Shipment {shipment_id} | {len(docs)} doc(s)")

    # Register all docs, run extractor on each, build extraction map
    from agents.extractor import run_extractor
    extraction_map = {}   # doc_id -> fields
    doc_id_to_name = {}   # doc_id -> filename

    for doc_path, doc_filename in docs:
        doc_id = add_document_to_shipment(shipment_id, doc_path, doc_filename)
        doc_id_to_name[doc_id] = doc_filename
        print(f"[Pipeline] Extracting: {doc_filename}")
        fields = run_extractor(doc_path)
        extraction_map[doc_id] = fields
        save_extraction_results(shipment_id, doc_id, fields)

    initial_state: PipelineState = {
        "shipment_id": shipment_id,
        "customer_id": customer_id,
        "customer_name": customer["name"],
        "extraction": extraction_map,
        "validation": [],
        "decision": "",
        "reasoning": "",
        "draft_email": "",
        "validation_summary": {},
        "error": "",
        "current_node": "start",
        "active_document_id": list(extraction_map.keys())[0],
    }

    config = {"configurable": {"thread_id": shipment_id}}
    final_state = build_pipeline().invoke(initial_state, config=config)

    # Build per-filename extraction for UI (keyed by filename, not doc_id)
    extraction_by_filename = {
        doc_id_to_name[did]: fields
        for did, fields in final_state.get("extraction", {}).items()
    }

    return {
        "shipment_id": shipment_id,
        "customer_name": customer["name"],
        "decision": final_state.get("decision"),
        "reasoning": final_state.get("reasoning"),
        "extraction": extraction_by_filename,       # {filename: {field: {value,confidence}}}
        "validation": final_state.get("validation"),
        "validation_summary": final_state.get("validation_summary", {}),
        "draft_email": final_state.get("draft_email"),
        "doc_count": len(docs),
        "doc_names": [f for _, f in docs],
    }


if __name__ == "__main__":
    import sys
    init_db()
    if len(sys.argv) >= 3:
        run_pipeline([(sys.argv[1], os.path.basename(sys.argv[1]))], sys.argv[2])