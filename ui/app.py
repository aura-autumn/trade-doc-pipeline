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
import shutil
import tempfile
import streamlit as st
from pathlib import Path
from datetime import datetime
import pandas as pd

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
    update_shipment_status,
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

# --- Init (runs once per server session, not on every rerender) ---
@st.cache_resource
def _init_app():
    init_db()
    seed_demo_customers()

_init_app()

# --- Cached DB reads (invalidated only when needed) ---
@st.cache_data(ttl=30)
def _get_customers():
    return get_all_customers()

@st.cache_data(ttl=30)
def _get_rules(customer_id):
    return get_customer_rules(customer_id)


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
    ["📥 Incoming", "▶ Run Pipeline", "📊 Shipment History", "❓ Query Layer", "⚙️ Manage Customers", "📈 Eval"],
)

# CSS for UI layouts and data tables
st.markdown("""
<style>
.pill { display:inline-block; padding:3px 12px; border-radius:20px; font-size:0.78rem; font-weight:600; }
.pill-match      { background:#dcfce7; color:#166534; }
.pill-mismatch   { background:#fee2e2; color:#991b1b; }
.pill-uncertain  { background:#fef3c7; color:#92400e; }
.pill-missing    { background:#f3e8ff; color:#6b21a8; }
.pill-not_checked{ background:#f1f5f9; color:#475569; }
.banner-approve { background:#dcfce7; border-left:4px solid #16a34a; padding:12px 18px; border-radius:6px; }
.banner-flag    { background:#fef3c7; border-left:4px solid #d97706; padding:12px 18px; border-radius:6px; }
.banner-amend   { background:#fee2e2; border-left:4px solid #dc2626; padding:12px 18px; border-radius:6px; }
.email-notice { background:#fff7ed; border:1px solid #fed7aa; border-radius:6px;
                padding:10px 14px; font-size:0.82rem; color:#9a3412; margin-bottom:12px; }
.incoming-card { background:white; border:1px solid #e2e8f0; border-radius:10px;
                 padding:18px 22px; margin-bottom:12px; }
.incoming-badge { display:inline-block; padding:2px 10px; background:#dbeafe;
                  color:#1e40af; border-radius:20px; font-size:0.72rem; font-weight:700; }
.processing-badge { display:inline-block; padding:2px 10px; background:#fef9c3;
                    color:#854d0e; border-radius:20px; font-size:0.72rem; font-weight:700; }
div[data-testid="stDataFrame"] { background-color: #0F172A; border-radius: 0.5rem; }
</style>
""", unsafe_allow_html=True)

st.sidebar.divider()
st.sidebar.caption(f"LLM: `{os.getenv('LLM_PROVIDER', 'gemini').upper()}`")


# =====================
# HELPERS: INCOMING PAGE
# =====================
def _pill(status):
    label = {"match": "✅ Match", "mismatch": "✗ Mismatch", "uncertain": "⚠ Uncertain",
             "missing": "🚫 Missing", "not_checked": "➖ No Rule"}.get(status, status)
    css   = {"match": "match", "mismatch": "mismatch", "uncertain": "uncertain",
             "missing": "missing", "not_checked": "not_checked"}.get(status, "not_checked")
    return f'<span class="pill pill-{css}">{label}</span>'


def _conf_bar(conf):
    color = "#16a34a" if conf >= 0.85 else "#d97706" if conf >= 0.6 else "#dc2626"
    filled = int(conf * 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f'<span style="font-family:monospace;color:{color};font-size:0.75rem;">{bar}</span> <span style="font-size:0.75rem;">{conf:.0%}</span>'


def _decision_banner(decision, reasoning):
    cls  = {"auto_approve": "approve", "flag_for_review": "flag", "draft_amendment": "amend"}.get(decision, "flag")
    icon = {"auto_approve": "✅", "flag_for_review": "⚠️", "draft_amendment": "📝"}.get(decision, "❔")
    label = {"auto_approve": "Auto Approved", "flag_for_review": "Flagged for Review",
             "draft_amendment": "Amendment Drafted"}.get(decision, decision)
    st.markdown(
        f'<div class="banner-{cls}"><strong>{icon} {label}</strong><br>'
        f'<span style="font-size:0.85rem;">{reasoning}</span></div>',
        unsafe_allow_html=True,
    )


def _confidence_color(conf):
    return "🟢" if conf >= 0.85 else "🟡" if conf >= 0.6 else "🔴"


# =====================
# PAGE: INCOMING
# =====================
if page == "📥 Incoming":
    ROOT = Path(__file__).parent.parent
    st.title("📥 Incoming Shipments")
    st.caption("Simulate an SU email arriving with trade document attachments. Agent processes immediately.")

    # Gmail live feed
    results_dir = ROOT / "inbox" / "results"
    if results_dir.exists():
        result_files = sorted(results_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if result_files:
            with st.expander(f"📡 Gmail Feed — {len(result_files)} shipment(s) processed by watcher", expanded=False):
                for rf in result_files[:5]:
                    try:
                        r = json.loads(rf.read_text())
                        decision_icon = {"auto_approve": "✅", "flag_for_review": "⚠️", "draft_amendment": "📝"}.get(r.get("decision"), "❔")
                        col_a, col_b = st.columns([4, 1])
                        with col_a:
                            st.markdown(
                                f"{decision_icon} **{r.get('email_subject','(no subject)')}** "
                                f"— From: {r.get('email_from','')} · {r.get('received_at','')[:16].replace('T',' ')} UTC"
                            )
                        with col_b:
                            if st.button("View", key=f"gmail_view_{rf.stem}"):
                                st.session_state["current_result"] = r
                                st.session_state["inbox_state"]    = "done"
                                st.rerun()
                    except Exception:
                        pass

    state = st.session_state.get("inbox_state", "idle")

    # ── IDLE ─────────────────────────────────────────────────────────────────
    if state == "idle":
        with st.container():
            st.markdown("#### Simulate SU Email Arrival")
            col1, col2 = st.columns([3, 2])

            with col1:
                customers = _get_customers()
                cust_map  = {c["name"]: c["id"] for c in customers}
                sel_name  = st.selectbox("Customer", list(cust_map.keys()))
                sel_cid   = cust_map[sel_name]

                sender   = st.text_input("From (SU email)", "shipping@acme-exports.com")
                subject  = st.text_input("Subject", f"Shipment Documents for {sel_name}")
                uploaded = st.file_uploader(
                    "Attach Trade Documents (PDF/Image)",
                    type=["pdf", "jpg", "jpeg", "png"],
                    accept_multiple_files=True,
                )
                if uploaded:
                    st.caption(", ".join(f"**{f.name}**" for f in uploaded))

                go = st.button("📨 Send Email to Nova", type="primary", disabled=not uploaded)

                st.divider()
                st.caption("**Or use a pre-built sample:**")
                sample_dir   = ROOT / "inbox" / "sample_emails"
                sample_files = sorted(sample_dir.glob("*.json")) if sample_dir.exists() else []
                if sample_files:
                    sample_choice = st.selectbox(
                        "Sample email",
                        [""] + [f.stem for f in sample_files],
                        format_func=lambda x: "Select a sample..." if x == "" else x,
                    )
                    load_sample = st.button("Load Sample", disabled=not sample_choice)
                else:
                    sample_choice = None
                    load_sample   = False

            with col2:
                if sel_cid:
                    rules = _get_rules(sel_cid)
                    st.markdown(f"**Rules for {sel_name}** ({len(rules)})")
                    for r in rules:
                        badge = "🔴" if r["is_critical"] else "🔵"
                        st.caption(
                            f"{badge} `{r['field_name']}` — "
                            f"{r['rule_type']} `{r.get('expected_value') or 'not null'}`"
                        )

        if go and uploaded:
            tmp_docs = []
            for uf in uploaded:
                with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uf.name).suffix) as t:
                    t.write(uf.read())
                    tmp_docs.append((t.name, uf.name))
            st.session_state["inbox_email"] = {
                "from": sender, "subject": subject,
                "customer_id": sel_cid, "customer_name": sel_name,
                "docs": tmp_docs, "received_at": datetime.utcnow().isoformat(),
            }
            st.session_state["inbox_state"] = "incoming"
            st.rerun()

        if load_sample and sample_choice:
            sample_path = sample_dir / f"{sample_choice}.json"
            with open(sample_path) as f:
                email_data = json.load(f)
            base     = sample_path.parent
            tmp_docs = []
            for att in email_data.get("attachments", []):
                att_p = Path(att) if Path(att).is_absolute() else base / att
                if att_p.exists():
                    with tempfile.NamedTemporaryFile(delete=False, suffix=att_p.suffix) as t:
                        shutil.copyfile(str(att_p), t.name)
                        tmp_docs.append((t.name, att_p.name))
            cust_id   = email_data.get("customer_id", "CUST001")
            cust_name = next((c["name"] for c in customers if c["id"] == cust_id), cust_id)
            st.session_state["inbox_email"] = {
                "from": email_data.get("from", "supplier@example.com"),
                "subject": email_data.get("subject", "(no subject)"),
                "customer_id": cust_id, "customer_name": cust_name,
                "docs": tmp_docs, "received_at": datetime.utcnow().isoformat(),
            }
            st.session_state["inbox_state"] = "incoming"
            st.rerun()

    # ── INCOMING: processing ──────────────────────────────────────────────────
    elif state == "incoming":
        email = st.session_state["inbox_email"]
        st.markdown(
            f'<div class="incoming-card">'
            f'<span class="incoming-badge">NEW EMAIL</span><br>'
            f'<strong style="font-size:1.05rem;">{email["subject"]}</strong><br>'
            f'<span style="color:#64748b;font-size:0.85rem;">From: {email["from"]} &nbsp;·&nbsp; '
            f'{email["received_at"][:16].replace("T"," ")} UTC</span><br><br>'
            f'<strong>Customer:</strong> {email["customer_name"]}<br>'
            f'<strong>Attachments:</strong> {", ".join(n for _, n in email["docs"])}'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<span class="processing-badge">⚙ AGENT PROCESSING</span>', unsafe_allow_html=True)
        progress   = st.progress(0, text="Initialising pipeline...")
        status_txt = st.empty()

        try:
            n = len(email["docs"])
            for i, (_, fname) in enumerate(email["docs"]):
                pct = 10 + int((i / n) * 45)
                progress.progress(pct, text=f"📄 Extracting: {fname} ({i+1}/{n})")
                status_txt.caption(f"Extracting fields from {fname}…")

            start   = time.time()
            result  = run_pipeline(email["docs"], email["customer_id"])
            elapsed = time.time() - start

            progress.progress(85, text="🔍 Validating + cross-doc consistency…")
            status_txt.caption("Running validator agent…")
            time.sleep(0.3)
            progress.progress(100, text="✅ Pipeline complete")
            status_txt.empty()

            result["email_from"]    = email["from"]
            result["email_subject"] = email["subject"]
            result["received_at"]   = email["received_at"]
            result["elapsed"]       = elapsed

            try:
                from rag.retriever import index_document
                for doc_path, doc_name in email["docs"]:
                    index_document(doc_path, result.get("shipment_id", ""))
            except Exception as rag_err:
                st.session_state["rag_index_error"] = str(rag_err)

            st.session_state["current_result"]   = result
            st.session_state["rag_chat_history"] = []
            st.session_state["inbox_state"]      = "done"
            st.rerun()

        except Exception as e:
            import traceback as tb
            progress.empty()
            st.error(f"Pipeline failed: {e}")
            st.code(tb.format_exc())
            if st.button("↩ Back"):
                st.session_state["inbox_state"] = "idle"
                st.rerun()

    # ── DONE: Full Tabular CG verification UI ─────────────────────────────────
    elif state == "done":
        result      = st.session_state.get("current_result", {})
        shipment_id = result.get("shipment_id", "")

        col_h1, col_h2 = st.columns([4, 1])
        with col_h1:
            st.markdown(f"### {result.get('email_subject', 'Shipment')}")
            st.caption(
                f"From: **{result.get('email_from')}** ·  "
                f"Received: {result.get('received_at', '')[:16].replace('T',' ')} UTC  ·  "
                f"Shipment: `{shipment_id}`  ·  "
                f"Docs: {result.get('doc_count', 1)}  ·  "
                f"Processed in {result.get('elapsed', 0):.1f}s"
            )
        with col_h2:
            if st.button("📨 New Email", type="secondary"):
                st.session_state["inbox_state"] = "idle"
                st.session_state.pop("current_result", None)
                st.session_state.pop("selected_field", None)
                st.session_state.pop("rag_chat_history", None)
                st.rerun()

        _decision_banner(result.get("decision", ""), result.get("reasoning", ""))
        st.markdown("")

        # 1-Table Presentation Matrix replacing split vertical lists/click-to-view items
        st.markdown("#### 📋 Validation Results Matrix")
        validation = result.get("validation", [])
        cross_doc  = [v for v in validation if v.get("field_name", "").startswith("cross_doc_")]
        per_doc    = [v for v in validation if not v.get("field_name", "").startswith("cross_doc_")]

        if cross_doc:
            st.markdown("**⚠ Cross-Document Inconsistencies**")
            for v in cross_doc:
                field_clean = v["field_name"].replace("cross_doc_discrepancy_", "").replace("_", " ").title()
                st.markdown(
                    f'<div style="background:#fee2e2;border-left:4px solid #dc2626;'
                    f'padding:8px 12px;border-radius:6px;margin-bottom:6px;">'
                    f'<strong>{field_clean}</strong> '
                    f'<span class="pill pill-mismatch">✗ Cross-doc conflict</span><br>'
                    f'<span style="font-size:0.8rem;color:#64748b;">{v.get("found_value","")}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # Rebuilding dictionary references to preserve the single flat dataframe rows look
        field_map = {}
        for v in per_doc:
            fn = v["field_name"]
            if fn not in field_map or v.get("confidence", 0) > field_map[fn].get("confidence", 0):
                field_map[fn] = v

        ui_table_rows = []
        for fn, v in field_map.items():
            conf_val = v.get("confidence", 0.0)
            ui_table_rows.append({
                "Document Field": fn.replace("_", " ").title(),
                "Validation Status": status_badge(v["status"]),
                "Extracted Value": v.get("found_value") or "—",
                "Expected Condition": v.get("expected_value") or "Any / Not Null",
                "Confidence Score": f"{confidence_color(conf_val)} {conf_val:.0%}",
                "Critical Check": "🔴 Critical" if v.get("is_critical") else "🔵 Optional",
                "Agent Reason / Details": v.get("detail") or "Verified against active templates."
            })

        if ui_table_rows:
            st.dataframe(pd.DataFrame(ui_table_rows), use_container_width=True, hide_index=True)

        st.markdown("")
        tab_draft, tab_all = st.tabs(["✉ Draft Reply Buffer", "📋 Raw Document Field Mappings"])

        with tab_draft:
            draft        = result.get("draft_email", "")
            already_sent = result.get("reply_sent", False)

            if not draft:
                st.info("No draft email required — shipment successfully approved.")
            else:
                st.markdown(
                    '<div class="email-notice">'
                    '⚠ <strong>Agent-drafted reply. Review every line before sending. '
                    'Agent never sends on its own.</strong>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                edited_draft = st.text_area(
                    "Edit before sending",
                    value=draft, height=250,
                    label_visibility="collapsed",
                )
                if already_sent:
                    st.success("✅ Reply already marked as sent.")
                else:
                    col_s1, col_s2 = st.columns([2, 3])
                    with col_s1:
                        if st.button("✅ Mark as Sent", type="primary", key="inc_mark_sent"):
                            if shipment_id:
                                update_shipment_status(shipment_id, "reply_sent")
                                result["reply_sent"] = True
                                st.session_state["current_result"] = result
                            st.rerun()
                    with col_s2:
                        st.caption("CG reviews → edits → sends. Never automatic.")
                if st.button("📋 Copy to Clipboard", key="inc_copy_draft"):
                    st.code(edited_draft)

        with tab_all:
            extraction = result.get("extraction", {})
            if not extraction:
                st.info("No explicit extraction values available.")
            else:
                for doc_name, fields in extraction.items():
                    st.markdown(f"**📄 {doc_name}**")
                    rows = []
                    for field, data in fields.items():
                        conf = data.get("confidence", 0.0)
                        rows.append({
                            "Field":      field.replace("_", " ").title(),
                            "Value":      data.get("value") or "—",
                            "Confidence": f"{_confidence_color(conf)} {conf:.0%}",
                            "Method":     data.get("method", "llm").upper(),
                        })
                    st.dataframe(rows, use_container_width=True, hide_index=True)

        # ── RAG Chatbot ───────────────────────────────────────────────────────
        st.divider()
        st.markdown("#### 🔎 Ask About This Shipment")
        st.caption("Ask anything about document content, validation results, or why the decision was made.")

        if st.session_state.get("rag_index_error"):
            st.warning(f"RAG indexing skipped: {st.session_state['rag_index_error']}")

        _val      = result.get("validation", [])
        _summary  = result.get("validation_summary", {})
        _val_lines = [
            f"  {v['field_name'].replace('_',' ').title()}: {v['status'].upper()}"
            f" | found='{v.get('found_value') or '—'}'"
            f" | expected='{v.get('expected_value') or '—'}'"
            f" | critical={'YES' if v.get('is_critical') else 'no'}"
            f" | confidence={v.get('confidence', 0):.0%}"
            for v in _val
        ]
        _pipeline_context = (
            "PIPELINE RESULTS:\n"
            f"Decision: {result.get('decision','').upper()}\n"
            f"Reasoning: {result.get('reasoning','')}\n"
            f"Summary: {_summary.get('matches',0)} match, {_summary.get('mismatches',0)} mismatch, "
            f"{_summary.get('missing',0)} missing, {_summary.get('uncertain',0)} uncertain\n"
            "\nFIELD-BY-FIELD:\n" + "\n".join(_val_lines)
        )

        if "rag_chat_history" not in st.session_state:
            st.session_state["rag_chat_history"] = []

        for msg in st.session_state["rag_chat_history"]:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        rag_q = st.chat_input("Ask about this shipment...", key="rag_chat_input")
        if rag_q:
            st.session_state["rag_chat_history"].append({"role": "user", "content": rag_q})
            try:
                from rag.retriever import query_document
                snippets    = query_document(rag_q, shipment_id, n_results=3)
                doc_context = "\n\n---\n\n".join(s["text"] for s in snippets) if snippets else "No document snippets available."
            except Exception:
                doc_context = "RAG retrieval unavailable."

            from llm.client import get_llm
            llm = get_llm(vision=False)
            full_prompt = (
                "You are a trade document validation assistant. "
                "Answer using PIPELINE RESULTS and DOCUMENT SNIPPETS only.\n\n"
                + _pipeline_context
                + "\n\nDOCUMENT SNIPPETS:\n" + doc_context
                + "\n\nQuestion: " + rag_q
            )
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    response = llm.invoke(full_prompt)
                answer = response.content if hasattr(response, "content") else str(response)
            except Exception as e:
                answer = f"Error: {e}"

            st.session_state["rag_chat_history"].append({"role": "assistant", "content": answer})
            st.rerun()

        if st.session_state.get("rag_chat_history"):
            if st.button("🗑️ Clear chat", key="clear_rag_chat"):
                st.session_state["rag_chat_history"] = []
                st.rerun()

# =====================
# PAGE: RUN PIPELINE
# =====================
elif page == "▶ Run Pipeline":
    st.title("Run Pipeline")
    st.caption("Upload one or more trade documents (BOL, Invoice, Packing List). Select a customer. Pipeline extracts, validates, and decides across all docs.")

    col1, col2 = st.columns([1, 1])

    with col1:
        customers = _get_customers()
        if not customers:
            st.warning("No customers found. Go to Manage Customers to add one.")
            st.stop()

        customer_options = {c["name"]: c["id"] for c in customers}
        selected_name = st.selectbox("Customer", list(customer_options.keys()))
        selected_customer_id = customer_options[selected_name]

        uploaded_files = st.file_uploader(
            "Trade Documents (PDF or Image) — upload multiple for cross-doc validation",
            type=["pdf", "jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
        )

        if uploaded_files:
            st.caption(f"{len(uploaded_files)} file(s) selected: " + ", ".join(f"**{f.name}**" for f in uploaded_files))

        run_btn = st.button("▶ Run Pipeline", type="primary", disabled=not uploaded_files)

    with col2:
        if selected_customer_id:
            rules = _get_rules(selected_customer_id)
            if rules:
                st.caption(f"**Rules for {selected_name}** ({len(rules)} rules)")
                for r in rules:
                    critical = "🔴 Critical" if r["is_critical"] else "🔵 Optional"
                    st.caption(f"• `{r['field_name']}` — {r['rule_type'].upper()} `{r.get('expected_value') or 'not null'}` {critical}")

    st.divider()

    if run_btn and uploaded_files:
        tmp_docs = []
        for uf in uploaded_files:
            suffix = Path(uf.name).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uf.read())
                tmp_docs.append((tmp.name, uf.name))

        doc_names = ", ".join(f"**{f}**" for _, f in tmp_docs)
        st.info(f"Processing {len(tmp_docs)} document(s): {doc_names}")
        progress = st.progress(0, text="Starting pipeline...")

        with st.spinner("Running pipeline..."):
            try:
                n = len(tmp_docs)
                for i, (_, fname) in enumerate(tmp_docs):
                    pct = int(10 + (i / n) * 40)
                    progress.progress(pct, text=f"📄 Extracting: {fname} ({i+1}/{n})...")

                start = time.time()
                final_state = run_pipeline(tmp_docs, selected_customer_id)
                elapsed = time.time() - start

                progress.progress(80, text="🔍 Validating + cross-doc check...")
                time.sleep(0.3)
                progress.progress(100, text="✅ Pipeline complete!")

                try:
                    index_document(tmp_docs[0][0], final_state["shipment_id"])
                except Exception as rag_err:
                    st.session_state["rag_index_error"] = str(rag_err)

                st.session_state["last_state"] = final_state
                st.session_state["last_shipment_id"] = final_state["shipment_id"]
                st.session_state.pop("rag_index_error", None)
                st.session_state["rag_chat_history"] = []

            except Exception as e:
                import traceback
                st.error(f"Pipeline failed: {e}")
                st.code(traceback.format_exc())
                st.stop()

        st.success(f"Pipeline completed in {elapsed:.1f}s — {len(tmp_docs)} doc(s) processed")

    if "last_state" in st.session_state:
        state = st.session_state["last_state"]
        shipment_id = state["shipment_id"]

        st.subheader(f"Results — Shipment `{shipment_id[:8]}...`")

        decision = state.get("decision", "")
        if decision:
            label = decision_label(decision)
            st.markdown(f"### {label}")
            st.info(f"**Reasoning:** {state.get('reasoning', '')}")

        tab1, tab2, tab3 = st.tabs(["📋 Extraction Matrix", "🔍 Field-Level Validation", "📝 Draft Email Output"])

        with tab1:
            extraction = state.get("extraction", {})
            if extraction:
                doc_names = list(extraction.keys())
                if len(doc_names) == 1:
                    rows = []
                    for field, data in extraction[doc_names[0]].items():
                        conf = data.get("confidence", 0.0)
                        rows.append({
                            "Field": field.replace("_", " ").title(),
                            "Value": data.get("value") or "—",
                            "Confidence": f"{confidence_color(conf)} {conf:.0%}",
                            "Method": data.get("method", "llm").upper(),
                        })
                    st.dataframe(rows, use_container_width=True, hide_index=True)
                else:
                    doc_tabs = st.tabs([f"📄 {n}" for n in doc_names])
                    for dt, dname in zip(doc_tabs, doc_names):
                        with dt:
                            rows = []
                            for field, data in extraction[dname].items():
                                conf = data.get("confidence", 0.0)
                                rows.append({
                                    "Field": field.replace("_", " ").title(),
                                    "Value": data.get("value") or "—",
                                    "Confidence": f"{confidence_color(conf)} {conf:.0%}",
                                    "Method": data.get("method", "llm").upper(),
                                })
                            st.dataframe(rows, use_container_width=True, hide_index=True)

        with tab2:
            validation = state.get("validation", [])
            if validation:
                cross_doc = [v for v in validation if v.get("field_name", "").startswith("cross_doc_")]
                per_doc   = [v for v in validation if not v.get("field_name", "").startswith("cross_doc_")]

                rows = []
                for v in per_doc:
                    rows.append({
                        "Field": v["field_name"].replace("_", " ").title(),
                        "Status": status_badge(v["status"]),
                        "Found": v.get("found_value") or "—",
                        "Expected Rule": v.get("expected_value") or "—",
                        "Critical Check": "🔴 Critical" if v.get("is_critical") else "🔵 Optional",
                        "Confidence Score": f"{v.get('confidence', 0):.0%}",
                        "Reasoning Detail": v.get("detail", ""),
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)

                if cross_doc:
                    st.warning(f"⚠️ {len(cross_doc)} cross-document discrepancy(s) found:")
                    for v in cross_doc:
                        field_clean = v["field_name"].replace("cross_doc_discrepancy_", "").replace("_", " ").title()
                        st.error(f"**{field_clean}** — values differ across documents: {v.get('found_value')}")

        with tab3:
            draft_email = state.get("draft_email", "")
            if draft_email:
                st.caption("⚠️ This email is a draft. Review before sending. Agent never sends automatically.")
                edited = st.text_area("Draft Email (editable)", value=draft_email, height=300)
                if st.button("📋 Copy to Clipboard"):
                    st.code(edited)
            else:
                st.info("No draft email required.")

        # ── RAG Chat ──
        st.divider()
        st.markdown("#### 🔎 Ask About This Shipment")

        _val = state.get("validation", [])
        _summary = state.get("validation_summary", {})
        _val_lines = []
        for v in _val:
            _val_lines.append(
                "  " + v["field_name"].replace("_", " ").title()
                + ": " + v["status"].upper()
                + " | found='" + str(v.get("found_value") or "—") + "'"
                + " | expected='" + str(v.get("expected_value") or "—") + "'"
            )
        _val_context = "\n".join(_val_lines) if _val_lines else "No validation data."
        _pipeline_context = (
            "PIPELINE RESULTS FOR THIS SHIPMENT:\n"
            + "Decision: " + state.get("decision", "").upper() + "\n"
            + "\nFIELD-BY-FIELD VALIDATION:\n" + _val_context
        )

        if "rag_chat_history" not in st.session_state:
            st.session_state["rag_chat_history"] = []

        for msg in st.session_state["rag_chat_history"]:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        rag_q = st.chat_input("Ask about this shipment...", key="rag_chat_input")
        if rag_q:
            st.session_state["rag_chat_history"].append({"role": "user", "content": rag_q})
            from rag.retriever import query_document
            snippets = query_document(rag_q, shipment_id, n_results=3)
            doc_context = "\n\n---\n\n".join(s["text"] for s in snippets) if snippets else "No document snippets available."

            from llm.client import get_llm
            llm = get_llm(vision=False)

            full_prompt = (
                "You are a trade document validation assistant."
                "\n\n" + _pipeline_context + "\n\nDOCUMENT SNIPPETS:\n" + doc_context + "\n\nQuestion: " + rag_q
            )
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    response = llm.invoke(full_prompt)
                answer = response.content if hasattr(response, "content") else str(response)
            except Exception as e:
                answer = "Error: " + str(e)

            st.session_state["rag_chat_history"].append({"role": "assistant", "content": answer})
            st.rerun()

        if st.session_state.get("rag_chat_history"):
            if st.button("🗑️ Clear chat", key="clear_rag_chat"):
                st.session_state["rag_chat_history"] = []
                st.rerun()

# =====================
# PAGE: SHIPMENT HISTORY
# =====================
elif page == "📊 Shipment History":
    st.title("Shipment History")

    customers = _get_customers()
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
            rows = conn.execute("""
                SELECT s.*, c.name as customer_name,
                    (SELECT GROUP_CONCAT(sd.doc_filename, ', ')
                     FROM shipment_documents sd WHERE sd.shipment_id = s.id) as doc_filename
                FROM shipments s
                JOIN customers c ON s.customer_id = c.id
                ORDER BY s.created_at DESC LIMIT 100
            """).fetchall()
        shipments = [dict(r) for r in rows]

    if not shipments:
        st.info("No shipments yet. Run the pipeline first.")
        st.stop()

    status_counts = {}
    for s in shipments:
        status_counts[s["status"]] = status_counts.get(s["status"], 0) + 1

    cols = st.columns(len(status_counts) or 1)
    for i, (status, count) in enumerate(status_counts.items()):
        cols[i].metric(status.replace("_", " ").title(), count)

    st.divider()

    for s in shipments:
        doc_label = s.get("doc_filename") or "Unknown document"
        with st.expander(f"📄 {doc_label} — {s.get('customer_name', s['customer_id'])} — {s['status'].upper()} — {s['created_at'][:16]}"):
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
    st.caption("Ask questions about shipments in plain English.")

    col1, col2 = st.columns([2, 1])

    with col1:
        query = st.text_input(
            "Ask a question...",
            key="nl_query_input",
            placeholder="How many shipments were flagged this week?",
        )
        shipment_id_opt = st.text_input("(Optional) Shipment ID for document-specific questions", "")

        if st.button("Ask", type="primary") and query:
            with st.spinner("Thinking..."):
                result = run_nl_query(query, shipment_id=shipment_id_opt or None)
            st.session_state["nl_result"] = result
            st.session_state["nl_question"] = query

        if "nl_result" in st.session_state:
            result = st.session_state["nl_result"]
            st.caption(f"Q: *{st.session_state.get('nl_question', '')}*")
            st.write(f"**Answer:** {result['answer']}")

            if result.get("sql"):
                with st.expander("SQL Query"):
                    st.code(result["sql"], language="sql")

            if result.get("results"):
                with st.expander(f"Raw Results ({len(result['results'])} rows)"):
                    st.json(result["results"][:20])

    with col2:
        st.caption("**Example queries:**")
        def _use_example(example: str):
            st.session_state["nl_query_input"] = example

        for eq in EXAMPLE_QUERIES:
            st.button(eq, key=f"ex_{eq}", on_click=_use_example, args=(eq,))

# =====================
# PAGE: MANAGE CUSTOMERS
# =====================
elif page == "⚙️ Manage Customers":
    st.title("Manage Customers")
    tab1, tab2 = st.tabs(["Existing Customers", "Add New Customer"])

    with tab1:
        customers = _get_customers()
        if not customers:
            st.info("No customers yet.")
        for c in customers:
            with st.expander(f"**{c['name']}** (`{c['id']}`)"):
                rules = _get_rules(c["id"])
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
            _get_customers.clear()
            _get_rules.clear()
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
            st.warning("No test documents found.")