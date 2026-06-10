"""
LangGraph Pipeline
- Defines the state graph: extractor -> validator -> router -> store
- Handles state persistence and crash recovery
- Each node wraps an agent with error handling
"""

import os
import uuid
from typing import TypedDict, Optional, Any
from dotenv import load_dotenv

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from agents.extractor import run_extractor
from agents.validator import run_validator
from agents.router import run_router
from db.database import (
    create_shipment,
    update_shipment_status,
    save_extraction_results,
    save_validation_results,
    save_decision,
    get_customer,
    init_db,
)

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./data/trade_docs.db")


# --- State ---

class PipelineState(TypedDict):
    shipment_id: str
    customer_id: str
    doc_path: str
    doc_filename: str
    customer_name: str
    extraction: dict                  # {field: {value, confidence, method}}
    validation: list                  # [{field, status, found, expected, ...}]
    decision: str                     # auto_approve | flag_for_review | draft_amendment
    reasoning: str
    draft_email: str
    validation_summary: dict
    error: str
    current_node: str


# --- Nodes ---

def node_extract(state: PipelineState) -> dict:
    """Extractor node: PDF/image -> structured fields with confidence."""
    print(f"[Extractor] Processing: {state['doc_filename']}")
    try:
        extraction = run_extractor(state["doc_path"])
        save_extraction_results(state["shipment_id"], extraction)
        return {
            "extraction": extraction,
            "current_node": "extractor",
            "error": "",
        }
    except Exception as e:
        error_msg = f"Extractor failed: {str(e)}"
        print(f"[Extractor ERROR] {error_msg}")
        update_shipment_status(state["shipment_id"], "error")
        return {
            "extraction": {},
            "current_node": "extractor",
            "error": error_msg,
        }


def node_validate(state: PipelineState) -> dict:
    """Validator node: extracted fields + rules -> field-by-field results."""
    if state.get("error"):
        return {"current_node": "validator"}

    print(f"[Validator] Validating against customer: {state['customer_id']}")
    try:
        validation = run_validator(state["extraction"], state["customer_id"])
        save_validation_results(state["shipment_id"], validation)
        return {
            "validation": validation,
            "current_node": "validator",
            "error": "",
        }
    except Exception as e:
        error_msg = f"Validator failed: {str(e)}"
        print(f"[Validator ERROR] {error_msg}")
        update_shipment_status(state["shipment_id"], "error")
        return {
            "validation": [],
            "current_node": "validator",
            "error": error_msg,
        }


def node_route(state: PipelineState) -> dict:
    """Router node: validation results -> decision + reasoning + draft email."""
    if state.get("error"):
        return {"current_node": "router"}

    print(f"[Router] Making decision for shipment: {state['shipment_id']}")
    try:
        result = run_router(
            state["validation"],
            shipment_id=state["shipment_id"],
            customer_name=state.get("customer_name", ""),
            extraction=state.get("extraction", {}),
        )
        save_decision(
            state["shipment_id"],
            result["decision"],
            result["reasoning"],
            result.get("draft_email"),
        )
        # Map decision to shipment status
        status_map = {
            "auto_approve": "approved",
            "flag_for_review": "flagged",
            "draft_amendment": "amendment_drafted",
        }
        update_shipment_status(state["shipment_id"], status_map.get(result["decision"], "flagged"))
        return {
            "decision": result["decision"],
            "reasoning": result["reasoning"],
            "draft_email": result.get("draft_email", ""),
            "validation_summary": result.get("summary", {}),
            "current_node": "router",
            "error": "",
        }
    except Exception as e:
        error_msg = f"Router failed: {str(e)}"
        print(f"[Router ERROR] {error_msg}")
        update_shipment_status(state["shipment_id"], "error")
        return {
            "decision": "flag_for_review",
            "reasoning": f"Router error — flagged for manual review. Error: {error_msg}",
            "draft_email": "",
            "current_node": "router",
            "error": error_msg,
        }


def should_continue_after_extract(state: PipelineState) -> str:
    """If extraction errored, skip to end."""
    if state.get("error"):
        return "end"
    return "validate"


def should_continue_after_validate(state: PipelineState) -> str:
    if state.get("error"):
        return "end"
    return "route"


# --- Build Graph ---

def build_pipeline(checkpointer=None) -> Any:
    """Build and compile the LangGraph pipeline."""
    graph = StateGraph(PipelineState)

    graph.add_node("extract", node_extract)
    graph.add_node("validate", node_validate)
    graph.add_node("route", node_route)

    graph.set_entry_point("extract")

    graph.add_conditional_edges(
        "extract",
        should_continue_after_extract,
        {"validate": "validate", "end": END}
    )
    graph.add_conditional_edges(
        "validate",
        should_continue_after_validate,
        {"route": "route", "end": END}
    )
    graph.add_edge("route", END)

    if checkpointer:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()


def get_checkpointer():
    """SQLite checkpointer for crash recovery and state persistence."""
    import sqlite3
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # Use a separate DB for checkpointing to avoid conflicts
    checkpoint_db = DB_PATH.replace(".db", "_checkpoints.db")
    conn = sqlite3.connect(checkpoint_db, check_same_thread=False)
    return SqliteSaver(conn)


def run_pipeline(doc_path: str, customer_id: str, doc_filename: str = None) -> PipelineState:
    """
    Run the full pipeline for a document.

    Args:
        doc_path: path to the trade document (PDF or image)
        customer_id: customer to validate against
        doc_filename: display filename

    Returns:
        Final pipeline state
    """
    init_db()

    if not doc_filename:
        doc_filename = os.path.basename(doc_path)

    customer = get_customer(customer_id)
    if not customer:
        raise ValueError(f"Customer not found: {customer_id}")

    shipment_id = create_shipment(customer_id, doc_path, doc_filename)
    print(f"Created shipment: {shipment_id}")

    checkpointer = get_checkpointer()
    pipeline = build_pipeline(checkpointer)

    initial_state: PipelineState = {
        "shipment_id": shipment_id,
        "customer_id": customer_id,
        "doc_path": doc_path,
        "doc_filename": doc_filename,
        "customer_name": customer["name"],
        "extraction": {},
        "validation": [],
        "decision": "",
        "reasoning": "",
        "draft_email": "",
        "validation_summary": {},
        "error": "",
        "current_node": "start",
    }

    config = {"configurable": {"thread_id": shipment_id}}

    final_state = pipeline.invoke(initial_state, config=config)
    print(f"Pipeline complete. Decision: {final_state.get('decision')}")

    return final_state


if __name__ == "__main__":
    import sys
    from db.database import seed_demo_customers

    init_db()
    seed_demo_customers()

    if len(sys.argv) >= 3:
        doc = sys.argv[1]
        cust = sys.argv[2]
        result = run_pipeline(doc, cust)
        print(f"\nDecision: {result['decision']}")
        print(f"Reasoning: {result['reasoning']}")
    else:
        print("Usage: python -m pipeline.graph <doc_path> <customer_id>")