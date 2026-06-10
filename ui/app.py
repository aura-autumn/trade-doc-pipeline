"""
Streamlit UI
- Upload document
- Select / create customer
- Run pipeline with live progress
- Show extraction, validation, decision, draft email
- NL query interface
"""

import os
import sys
import json
import time
import tempfile
import streamlit as st
from pathlib import Path

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import (
    init_db,
    seed_demo_customers,
    get_all_customers,
    get_customer_rules,
    create_customer,
    upsert_customer_rules,
    get_shipments_by_customer,
    get_extraction_results,
    get_validation_results,
    get_decision,
)
from pipeline.graph import run_pipeline
from query.nl_query import run_nl_query, EXAMPLE_QUERIES
from rag.retriever import index_document

# --- Page config ---
st.set_page_config(
    page_title="Nova — Trade Doc Pipeline",
    page_icon="🚢",
    layout="wide",
)

# --- Init ---
init_db()
seed_demo_customers()


# --- Helpers ---

def confidence_color(conf: float) -> str:
    if conf >= 0.85:
        return "🟢"
    elif conf >= 0.6:
        return "🟡"
    else:
        return "🔴"


def status_badge(status: str) -> str:
    badges = {
        "match": "✅ Match",
        "mismatch": "❌ Mismatch",
        "uncertain": "⚠️ Uncertain",
        "missing": "🚫 Missing",
        "not_checked": "➖ No Rule",
    }
    return badges.get(status, status)


def decision_color(decision: str) -> str:
    colors = {
        "auto_approve": "green",
        "flag_for_review": "orange",
        "draft_amendment": "red",
    }
    return colors.get(decision, "gray")


def decision_label(decision: str) -> str:
    labels = {
        "auto_approve": "✅ Auto Approved",
        "flag_for_review": "⚠️ Flagged for Review",
        "draft_amendment": "📝 Amendment Drafted",
    }
    return labels.get(decision, decision)


# --- Sidebar ---
st.sidebar.title("🚢 Nova")
st.sidebar.caption("Trade Document Pipeline")

page = st.sidebar.radio(
    "Navigate",
    ["▶ Run Pipeline", "📊 Shipment History", "❓ Query Layer", "⚙️ Manage Customers", "📈 Eval"],
)

st.sidebar.divider()
st.sidebar.caption(f"LLM: `{os.getenv('LLM_PROVIDER', 'gemini').upper()}`")


# =====================
# PAGE: RUN PIPELINE
# =====================
if page == "▶ Run Pipeline":
    st.title("Run Pipeline")
    st.caption("Upload a trade document. Select a customer. Pipeline runs extract → validate → decide.")

    col1, col2 = st.columns([1, 1])

    with col1:
        customers = get_all_customers()
        if not customers:
            st.warning("No customers found. Go to Manage Customers to add one.")
            st.stop()

        customer_options = {c["name"]: c["id"] for c in customers}
        selected_name = st.selectbox("Customer", list(customer_options.keys()))
        selected_customer_id = customer_options[selected_name]

        uploaded_file = st.file_uploader(
            "Trade Document (PDF or Image)",
            type=["pdf", "jpg", "jpeg", "png", "webp"],
        )

        run_btn = st.button("▶ Run Pipeline", type="primary", disabled=not uploaded_file)

    with col2:
        if selected_customer_id:
            rules = get_customer_rules(selected_customer_id)
            if rules:
                st.caption(f"**Rules for {selected_name}** ({len(rules)} rules)")
                for r in rules:
                    critical = "🔴 Critical" if r["is_critical"] else "🔵 Optional"
                    st.caption(f"• `{r['field_name']}` — {r['rule_type'].upper()} `{r.get('expected_value') or 'not null'}` {critical}")

    st.divider()

    if run_btn and uploaded_file:
        # Save uploaded file to temp
        suffix = Path(uploaded_file.name).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded_file.read())
            tmp_path = tmp.name

        st.info(f"Processing: **{uploaded_file.name}**")

        # Progress indicators
        progress = st.progress(0, text="Starting pipeline...")
        status_placeholder = st.empty()

        with st.spinner("Running pipeline..."):
            try:
                progress.progress(10, text="📄 Extracting fields from document...")
                status_placeholder.caption("Extractor Agent: Analyzing document with vision LLM...")

                # We run the full pipeline (LangGraph handles sequencing)
                start = time.time()
                final_state = run_pipeline(tmp_path, selected_customer_id, uploaded_file.name)
                elapsed = time.time() - start

                progress.progress(60, text="✅ Extraction complete. Validating...")
                time.sleep(0.3)
                progress.progress(80, text="🔍 Validation complete. Routing...")
                time.sleep(0.3)
                progress.progress(100, text="✅ Pipeline complete!")

                # Index for RAG
                try:
                    index_document(tmp_path, final_state["shipment_id"])
                except Exception:
                    pass

                st.session_state["last_state"] = final_state
                st.session_state["last_shipment_id"] = final_state["shipment_id"]

            except Exception as e:
                st.error(f"Pipeline failed: {e}")
                st.stop()

        st.success(f"Pipeline completed in {elapsed:.1f}s")

    # Show results
    if "last_state" in st.session_state:
        state = st.session_state["last_state"]
        shipment_id = state["shipment_id"]

        st.subheader(f"Results — Shipment `{shipment_id[:8]}...`")

        # Decision banner
        decision = state.get("decision", "")
        if decision:
            color = decision_color(decision)
            label = decision_label(decision)
            st.markdown(f"### {label}")
            st.info(f"**Reasoning:** {state.get('reasoning', '')}")

        tab1, tab2, tab3, tab4 = st.tabs(["📋 Extraction", "🔍 Validation", "📝 Draft Email", "🔎 RAG Lookup"])

        with tab1:
            extraction = state.get("extraction", {})
            if extraction:
                rows = []
                for field, data in extraction.items():
                    conf = data.get("confidence", 0.0)
                    rows.append({
                        "Field": field.replace("_", " ").title(),
                        "Value": data.get("value") or "—",
                        "Confidence": f"{confidence_color(conf)} {conf:.0%}",
                        "Method": data.get("method", "llm").upper(),
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.warning("No extraction data.")

        with tab2:
            validation = state.get("validation", [])
            if validation:
                rows = []
                for v in validation:
                    rows.append({
                        "Field": v["field_name"].replace("_", " ").title(),
                        "Status": status_badge(v["status"]),
                        "Found": v.get("found_value") or "—",
                        "Expected": v.get("expected_value") or "—",
                        "Critical": "🔴" if v.get("is_critical") else "🔵",
                        "Confidence": f"{v.get('confidence', 0):.0%}",
                        "Detail": v.get("detail", ""),
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)

                # Summary
                summary = state.get("validation_summary", {})
                if summary:
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Matches", summary.get("matches", 0))
                    c2.metric("Mismatches", summary.get("mismatches", 0))
                    c3.metric("Uncertain", summary.get("uncertain", 0))
                    c4.metric("Missing", summary.get("missing", 0))
            else:
                st.warning("No validation data.")

        with tab3:
            draft_email = state.get("draft_email", "")
            if draft_email:
                st.caption("⚠️ This email is a draft. Review before sending. Agent never sends automatically.")
                edited = st.text_area("Draft Email (editable)", value=draft_email, height=300)
                if st.button("📋 Copy to Clipboard"):
                    st.code(edited)
            else:
                st.info("No draft email (document was approved or flagged without amendment).")

        with tab4:
            st.caption("Ask a question about the specific document content (RAG-powered).")
            rag_q = st.text_input("Ask about this document...", placeholder="What is the consignee address in the document?")
            if rag_q:
                from rag.retriever import answer_with_rag
                with st.spinner("Searching document..."):
                    answer = answer_with_rag(rag_q, shipment_id)
                st.write(answer)


# =====================
# PAGE: SHIPMENT HISTORY
# =====================
elif page == "📊 Shipment History":
    st.title("Shipment History")

    customers = get_all_customers()
    if not customers:
        st.info("No customers yet.")
        st.stop()

    customer_options = {"All Customers": None} | {c["name"]: c["id"] for c in customers}
    selected = st.selectbox("Filter by customer", list(customer_options.keys()))
    selected_id = customer_options[selected]

    if selected_id:
        shipments = get_shipments_by_customer(selected_id)
    else:
        from db.database import get_connection
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT s.*, c.name as customer_name FROM shipments s JOIN customers c ON s.customer_id = c.id ORDER BY s.created_at DESC LIMIT 100"
            ).fetchall()
        shipments = [dict(r) for r in rows]

    if not shipments:
        st.info("No shipments yet. Run the pipeline first.")
        st.stop()

    # Summary metrics
    status_counts = {}
    for s in shipments:
        status_counts[s["status"]] = status_counts.get(s["status"], 0) + 1

    cols = st.columns(len(status_counts) or 1)
    for i, (status, count) in enumerate(status_counts.items()):
        cols[i].metric(status.replace("_", " ").title(), count)

    st.divider()

    for s in shipments:
        with st.expander(f"📄 {s['doc_filename']} — {s.get('customer_name', s['customer_id'])} — {s['status'].upper()} — {s['created_at'][:16]}"):
            decision = get_decision(s["id"])
            if decision:
                st.write(f"**Decision:** {decision_label(decision['decision'])}")
                st.write(f"**Reasoning:** {decision['reasoning']}")

            val_results = get_validation_results(s["id"])
            if val_results:
                issues = [v for v in val_results if v["status"] not in ("match", "not_checked")]
                if issues:
                    st.write(f"**Issues ({len(issues)}):**")
                    for v in issues:
                        st.write(f"• `{v['field_name']}`: {status_badge(v['status'])} — Found `{v.get('found_value')}` | Expected `{v.get('expected_value')}`")


# =====================
# PAGE: QUERY LAYER
# =====================
elif page == "❓ Query Layer":
    st.title("Natural Language Query")
    st.caption("Ask questions about shipments in plain English. Text-to-SQL for database queries, RAG for document-specific questions.")

    col1, col2 = st.columns([2, 1])

    with col1:
        query = st.text_input("Ask a question...", placeholder="How many shipments were flagged this week?")

        shipment_id_opt = st.text_input("(Optional) Shipment ID for document-specific questions", "")

        if st.button("Ask", type="primary") and query:
            with st.spinner("Thinking..."):
                result = run_nl_query(query, shipment_id=shipment_id_opt or None)

            st.write(f"**Answer:** {result['answer']}")

            if result.get("sql"):
                with st.expander("SQL Query"):
                    st.code(result["sql"], language="sql")

            if result.get("results"):
                with st.expander(f"Raw Results ({len(result['results'])} rows)"):
                    st.json(result["results"][:20])

    with col2:
        st.caption("**Example queries:**")
        for eq in EXAMPLE_QUERIES:
            if st.button(eq, key=eq):
                st.session_state["prefill_query"] = eq
                st.rerun()


# =====================
# PAGE: MANAGE CUSTOMERS
# =====================
elif page == "⚙️ Manage Customers":
    st.title("Manage Customers")

    tab1, tab2 = st.tabs(["Existing Customers", "Add New Customer"])

    with tab1:
        customers = get_all_customers()
        if not customers:
            st.info("No customers yet.")
        for c in customers:
            with st.expander(f"**{c['name']}** (`{c['id']}`)"):
                rules = get_customer_rules(c["id"])
                if rules:
                    rows = []
                    for r in rules:
                        rows.append({
                            "Field": r["field_name"],
                            "Rule Type": r["rule_type"],
                            "Expected": r.get("expected_value") or "not null",
                            "Critical": "Yes" if r["is_critical"] else "No",
                            "Description": r.get("description", ""),
                        })
                    st.dataframe(rows, use_container_width=True, hide_index=True)
                else:
                    st.info("No rules defined.")

    with tab2:
        st.subheader("Add New Customer")
        new_name = st.text_input("Customer Name")
        new_id = st.text_input("Customer ID (optional, auto-generated if blank)")

        st.caption("Define rules (add at least one):")

        if "new_rules" not in st.session_state:
            st.session_state.new_rules = []

        with st.form("add_rule_form"):
            rc1, rc2, rc3, rc4 = st.columns([2, 2, 1, 1])
            field_name = rc1.text_input("Field Name", placeholder="incoterms")
            expected_val = rc2.text_input("Expected Value", placeholder="CIF (leave blank for not_null)")
            rule_type = rc3.selectbox("Rule Type", ["exact", "contains", "regex", "not_null"])
            is_critical = rc4.checkbox("Critical")
            add_rule = st.form_submit_button("Add Rule")

            if add_rule and field_name:
                st.session_state.new_rules.append({
                    "field_name": field_name,
                    "expected_value": expected_val or None,
                    "rule_type": rule_type,
                    "is_critical": is_critical,
                    "description": f"{rule_type} rule for {field_name}",
                })

        if st.session_state.new_rules:
            st.caption(f"Rules to add ({len(st.session_state.new_rules)}):")
            for r in st.session_state.new_rules:
                st.caption(f"• `{r['field_name']}` — {r['rule_type']} `{r.get('expected_value') or 'not null'}` {'🔴' if r['is_critical'] else '🔵'}")

        if st.button("💾 Create Customer", type="primary") and new_name:
            cid = create_customer(new_name, new_id or None)
            if st.session_state.new_rules:
                upsert_customer_rules(cid, st.session_state.new_rules)
            st.success(f"Customer '{new_name}' created with ID: `{cid}`")
            st.session_state.new_rules = []
            st.rerun()


# =====================
# PAGE: EVAL
# =====================
elif page == "📈 Eval":
    st.title("Evaluation")
    st.caption("Run offline eval on labeled test documents.")

    report_path = "./data/eval_report.json"

    if os.path.exists(report_path):
        with open(report_path) as f:
            report = json.load(f)

        summary = report.get("summary", {})
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Docs Evaluated", summary.get("total_docs", 0))
        col2.metric("Extraction Accuracy", f"{summary.get('avg_extraction_accuracy', 0):.0%}")
        col3.metric("Decision Accuracy", f"{summary.get('decision_accuracy', 0):.0%}")
        col4.metric("Avg Latency", f"{summary.get('avg_latency_seconds', 0):.1f}s")

        st.subheader("Per-Field Accuracy")
        field_acc = report.get("field_accuracy", {})
        rows = [
            {
                "Field": f.replace("_", " ").title(),
                "Accuracy": f"{v['accuracy']:.0%}",
                "Calibration Issues": v.get("calibration_issues", 0),
            }
            for f, v in field_acc.items()
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

    if st.button("▶ Run Eval Now", type="primary"):
        with st.spinner("Running evaluation..."):
            from eval.eval import run_eval
            report = run_eval()
        if report:
            st.success("Eval complete. Refresh to see results.")
            st.rerun()
        else:
            st.warning("No test documents found. Add documents to ./data/sample_docs/ and ground truth to ./data/ground_truth.json")
