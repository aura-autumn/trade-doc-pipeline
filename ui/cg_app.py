"""
Part 2 — Nova CG Verification Hub
Single unified app combining Part 1 pages + Part 2 CG workflow.

Pages:
  📥 Incoming / New Shipment  — CG email trigger + 4-state verification UI
  📊 Shipment Queue           — all shipments, filterable
  ❓ Query                    — NL query over stored data
  ⚙️ Manage Customers         — add/edit customers and rules
  📈 Eval                     — offline eval runner

Agent NEVER sends on its own. CG always reviews and clicks send.
"""

import os
import sys
import json
import time
import shutil
import tempfile
from pathlib import Path
from datetime import datetime

import streamlit as st

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from db.database import (
    init_db, seed_demo_customers,
    get_all_customers, get_customer_rules,
    get_decision, update_shipment_status,
    get_validation_results, get_extraction_results,
    get_shipments_by_customer, create_customer,
    upsert_customer_rules, get_connection,
)
from pipeline.graph import run_pipeline
from query.nl_query import run_nl_query

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Nova CG — Verification Hub",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Init ──────────────────────────────────────────────────────────────────────
@st.cache_resource
def _init():
    init_db()
    seed_demo_customers()

_init()

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #f8f9fb; }
.main .block-container { padding-top: 1.5rem; max-width: 1200px; }

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

[data-testid="stSidebar"] { background:#1e293b; }
[data-testid="stSidebar"] * { color:#cbd5e1 !important; }
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2 { color:#f1f5f9 !important; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pill(status):
    labels = {
        "match":       ("match",       "✓ Match"),
        "mismatch":    ("mismatch",    "✗ Mismatch"),
        "uncertain":   ("uncertain",   "? Uncertain"),
        "missing":     ("missing",     "⊘ Missing"),
        "not_checked": ("not_checked", "– No Rule"),
    }
    cls, txt = labels.get(status, ("not_checked", status))
    return f'<span class="pill pill-{cls}">{txt}</span>'


def _conf_bar(conf):
    pct = int(conf * 100)
    color = "#16a34a" if conf >= 0.85 else "#d97706" if conf >= 0.6 else "#dc2626"
    return (
        f'<div style="display:flex;align-items:center;gap:6px;">'
        f'<div style="flex:1;background:#e2e8f0;border-radius:4px;height:6px;">'
        f'<div style="width:{pct}%;background:{color};height:6px;border-radius:4px;"></div></div>'
        f'<span style="font-size:0.75rem;color:#64748b;min-width:32px;">{pct}%</span>'
        f'</div>'
    )


def _decision_banner(decision, reasoning):
    if decision == "auto_approve":
        cls, icon, label = "banner-approve", "✅", "Auto Approved"
    elif decision == "flag_for_review":
        cls, icon, label = "banner-flag", "⚠️", "Flagged for Review"
    else:
        cls, icon, label = "banner-amend", "📝", "Amendment Required"
    st.markdown(
        f'<div class="{cls}"><strong>{icon} {label}</strong><br>'
        f'<span style="font-size:0.85rem;">{reasoning}</span></div>',
        unsafe_allow_html=True,
    )


def _get_all_shipments():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT s.id, s.status, s.created_at, c.name as customer_name,
                   (SELECT GROUP_CONCAT(sd.doc_filename, ', ')
                    FROM shipment_documents sd WHERE sd.shipment_id = s.id) as docs
            FROM shipments s
            JOIN customers c ON s.customer_id = c.id
            ORDER BY s.created_at DESC LIMIT 200
        """).fetchall()
    return [dict(r) for r in rows]


@st.cache_data(ttl=5)
def _get_customers():
    return get_all_customers()


def _get_rules(customer_id):
    return get_customer_rules(customer_id)


def _confidence_color(conf):
    if conf >= 0.85: return "🟢"
    if conf >= 0.6:  return "🟡"
    return "🔴"


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📋 Nova CG")
    st.markdown("**Verification Hub**")
    st.divider()

    page = st.radio(
        "View",
        ["📥 Incoming / New Shipment", "📊 Shipment Queue",
         "❓ Query", "⚙️ Manage Customers", "📈 Eval"],
        label_visibility="collapsed",
    )

    st.divider()
    shipments_all = _get_all_shipments()
    pending  = sum(1 for s in shipments_all if s["status"] in ("processing", "flagged"))
    approved = sum(1 for s in shipments_all if s["status"] in ("approved", "reply_sent"))
    amend    = sum(1 for s in shipments_all if s["status"] == "amendment_drafted")

    st.markdown(f"🕐 **Pending review:** {pending}")
    st.markdown(f"✅ **Approved / Sent:** {approved}")
    st.markdown(f"📝 **Needs amendment:** {amend}")

    st.divider()
    token_exists = (ROOT / "gmail_token.json").exists()
    creds_exist  = (ROOT / "credentials.json").exists()
    if token_exists:
        st.markdown("📡 **Gmail:** 🟢 Connected")
    elif creds_exist:
        st.markdown("📡 **Gmail:** 🟡 Ready (run gmail_trigger.py)")
    else:
        st.markdown("📡 **Gmail:** ⚪ Not configured")

    st.divider()
    st.caption(f"LLM: `{os.getenv('LLM_PROVIDER', 'gemini').upper()}`")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — INCOMING / NEW SHIPMENT
# ══════════════════════════════════════════════════════════════════════════════
if page == "📥 Incoming / New Shipment":
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
                                f"{decision_icon} **{r.get('email_subject','(no subject)')}**  "
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

            # Index for RAG (non-fatal)
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

    # ── DONE: Full CG verification UI ─────────────────────────────────────────
    elif state == "done":
        result = st.session_state.get("current_result", {})
        shipment_id = result.get("shipment_id", "")

        col_h1, col_h2 = st.columns([4, 1])
        with col_h1:
            st.markdown(f"### {result.get('email_subject', 'Shipment')}")
            st.caption(
                f"From: **{result.get('email_from')}**  ·  "
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

        left, right = st.columns([2, 3])

        # ── Left: field list ──────────────────────────────────────────────────
        with left:
            st.markdown("#### Verification Result")
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

            field_map = {}
            for v in per_doc:
                fn = v["field_name"]
                if fn not in field_map or v.get("confidence", 0) > field_map[fn].get("confidence", 0):
                    field_map[fn] = v

            for fn, v in field_map.items():
                status   = v["status"]
                conf     = v.get("confidence", 0.0)
                is_crit  = v.get("is_critical", False)
                display  = fn.replace("_", " ").title()
                crit_mark = ' <span style="color:#dc2626;font-size:0.7rem;">CRITICAL</span>' if is_crit else ""

                if st.button(
                    f"{display}{' ⚠' if status in ('mismatch','uncertain','missing') else ''}",
                    key=f"field_{fn}",
                    use_container_width=True,
                    type="secondary",
                ):
                    st.session_state["selected_field"] = fn
                    st.rerun()

                st.markdown(
                    f'<div style="margin:-8px 0 6px 4px;font-size:0.78rem;display:flex;gap:8px;align-items:center;">'
                    f'{_pill(status)}{crit_mark}'
                    f'<span style="color:#94a3b8;">|</span>'
                    f'{_conf_bar(conf)}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # ── Right: detail tabs + draft + all fields ───────────────────────────
        with right:
            tab_detail, tab_draft, tab_all = st.tabs(
                ["🔍 Discrepancy Detail", "✉ Draft Reply", "📋 All Fields"]
            )

            with tab_detail:
                sel = st.session_state.get("selected_field")
                if not sel:
                    st.info("Click a field on the left to see details.")
                else:
                    field_results = [v for v in per_doc if v["field_name"] == sel]
                    st.markdown(f"#### {sel.replace('_',' ').title()}")
                    for v in field_results:
                        status = v["status"]
                        st.markdown(_pill(status), unsafe_allow_html=True)
                        col_a, col_b = st.columns(2)
                        with col_a:
                            st.markdown("**Found in document**")
                            st.code(v.get("found_value") or "—", language=None)
                        with col_b:
                            st.markdown("**Expected (customer rule)**")
                            st.code(v.get("expected_value") or "any / not null", language=None)
                        st.markdown(f"**Confidence:** {v.get('confidence', 0):.0%}")
                        if v.get("detail"):
                            st.caption(f"ℹ {v['detail']}")
                        rule_type = v.get("rule_type")
                        if rule_type:
                            rule_desc = {
                                "exact":    "Value must match exactly",
                                "contains": "Value must contain the expected text",
                                "regex":    "Value must satisfy the regex pattern",
                                "not_null": "Value must be present",
                            }.get(rule_type, rule_type)
                            st.caption(f"**Rule type:** {rule_type} — {rule_desc}")
                        if v.get("is_critical"):
                            st.warning("Critical field — mismatch triggers amendment.")

            with tab_draft:
                draft = result.get("draft_email", "")
                already_sent = result.get("reply_sent", False)

                if not draft:
                    st.info("No draft email — shipment was auto-approved.")
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
                        value=draft, height=300,
                        label_visibility="collapsed",
                    )

                    if already_sent:
                        st.success("✅ Reply already marked as sent.")
                    else:
                        col_s1, col_s2 = st.columns([2, 3])
                        with col_s1:
                            if st.button("✅ Mark as Sent", type="primary", key="mark_sent_btn"):
                                if shipment_id:
                                    update_shipment_status(shipment_id, "reply_sent")
                                    result["reply_sent"] = True
                                    st.session_state["current_result"] = result
                                st.rerun()
                        with col_s2:
                            st.caption("CG reviews → edits → sends. Never automatic.")

                    if st.button("📋 Copy to Clipboard", key="copy_draft"):
                        st.code(edited_draft)

            with tab_all:
                extraction = result.get("extraction", {})
                if not extraction:
                    st.info("No extraction data available.")
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
        st.caption(
            "Ask anything about document content, validation results, or why the decision was made. "
            "The assistant has full context of both the document and pipeline results."
        )

        if st.session_state.get("rag_index_error"):
            st.warning(f"RAG indexing skipped: {st.session_state['rag_index_error']}")

        _val = result.get("validation", [])
        _summary = result.get("validation_summary", {})
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
                "Answer using PIPELINE RESULTS and DOCUMENT SNIPPETS only. "
                "If the answer is not in either source, say so explicitly.\n\n"
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


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — SHIPMENT QUEUE
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📊 Shipment Queue":
    st.title("📊 Shipment Queue")
    st.caption("All shipments processed by the agent.")

    filter_status = st.multiselect(
        "Filter by status",
        ["processing", "approved", "flagged", "amendment_drafted", "reply_sent", "error"],
        default=["flagged", "amendment_drafted"],
    )

    shipments = _get_all_shipments()
    if filter_status:
        shipments = [s for s in shipments if s["status"] in filter_status]

    all_ships = _get_all_shipments()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total",           len(all_ships))
    col2.metric("Needs attention", sum(1 for s in all_ships if s["status"] in ("flagged", "amendment_drafted")))
    col3.metric("Approved / Sent", sum(1 for s in all_ships if s["status"] in ("approved", "reply_sent")))
    col4.metric("Showing",         len(shipments))

    st.divider()

    if not shipments:
        st.info("No shipments match the current filter.")
    else:
        for s in shipments:
            status_icon = {
                "approved":          "✅",
                "flagged":           "⚠️",
                "amendment_drafted": "📝",
                "processing":        "⚙️",
                "reply_sent":        "📨",
                "error":             "❌",
            }.get(s["status"], "❔")

            label = (
                f"{status_icon} {s.get('docs') or 'Unknown doc'} — "
                f"{s.get('customer_name','?')} — "
                f"{s['status'].replace('_',' ').upper()} — "
                f"{s['created_at'][:16].replace('T',' ')}"
            )
            with st.expander(label):
                decision = get_decision(s["id"])
                if decision:
                    _decision_banner(decision["decision"], decision["reasoning"])
                    st.markdown("")

                val_results = get_validation_results(s["id"])
                if val_results:
                    issues = [v for v in val_results if v["status"] not in ("match", "not_checked")]
                    if issues:
                        st.markdown(f"**Issues ({len(issues)}):**")
                        for v in issues:
                            crit = "🔴 " if v.get("is_critical") else ""
                            st.markdown(
                                f"- {crit}`{v['field_name']}` → "
                                f"{_pill(v['status'])} "
                                f"Found `{v.get('found_value') or '—'}` | "
                                f"Expected `{v.get('expected_value') or '—'}`",
                                unsafe_allow_html=True,
                            )

                if decision and decision.get("draft_email"):
                    with st.expander("📧 Draft Email"):
                        st.code(decision["draft_email"])

                # Load into verification view
                if st.button("🔍 Open in Verification View", key=f"open_{s['id']}"):
                    # Reconstruct minimal result dict from DB
                    dec = get_decision(s["id"]) or {}
                    val = get_validation_results(s["id"]) or []
                    st.session_state["current_result"] = {
                        "shipment_id":   s["id"],
                        "decision":      dec.get("decision", ""),
                        "reasoning":     dec.get("reasoning", ""),
                        "draft_email":   dec.get("draft_email", ""),
                        "validation":    val,
                        "extraction":    {},
                        "email_subject": f"Shipment {s['id']}",
                        "email_from":    s.get("customer_name", ""),
                        "received_at":   s["created_at"],
                        "elapsed":       0,
                        "reply_sent":    s["status"] == "reply_sent",
                    }
                    st.session_state["inbox_state"] = "done"
                    st.session_state.pop("selected_field", None)
                    st.switch_page("ui/cg_app.py") if hasattr(st, "switch_page") else st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — QUERY
# ══════════════════════════════════════════════════════════════════════════════
elif page == "❓ Query":
    st.title("❓ Query Shipments")
    st.caption("Ask natural-language questions over all processed shipments.")

    EXAMPLES = [
        "How many shipments were flagged this week?",
        "Show me all amendment requests for customer Global Freight Corp",
        "Which customer has the most mismatches?",
        "List all shipments pending review",
        "What were the last 5 approved shipments?",
    ]

    # Use a key that doesn't conflict with example button writes
    q = st.text_input("Ask anything…", key="nl_query_input", placeholder=EXAMPLES[0])

    col_ask, col_clear = st.columns([2, 1])
    with col_ask:
        ask_clicked = st.button("Ask", type="primary")
    with col_clear:
        if st.button("Clear"):
            st.session_state.pop("nl_result", None)
            st.session_state.pop("nl_last_q", None)
            st.rerun()

    # Example buttons — use session state flag instead of direct key write
    st.markdown("**Examples:**")
    cols = st.columns(len(EXAMPLES))
    for i, ex in enumerate(EXAMPLES):
        if cols[i].button(ex[:30] + "…" if len(ex) > 30 else ex, key=f"ex_{i}"):
            st.session_state["nl_prefill"] = ex
            st.rerun()

    # If prefill was set by example button, show it
    if "nl_prefill" in st.session_state:
        q = st.session_state.pop("nl_prefill")
        ask_clicked = True

    if ask_clicked and q:
        with st.spinner("Thinking…"):
            ans = run_nl_query(q)
        st.session_state["nl_result"] = ans
        st.session_state["nl_last_q"] = q

    if "nl_result" in st.session_state:
        res = st.session_state["nl_result"]
        st.markdown(f"**Q:** _{st.session_state.get('nl_last_q','')}_")
        st.markdown(f"**A:** {res['answer']}")
        if res.get("sql"):
            with st.expander("SQL"):
                st.code(res["sql"], language="sql")
        if res.get("results"):
            with st.expander(f"Raw data ({len(res['results'])} rows)"):
                st.json(res["results"][:20])


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — MANAGE CUSTOMERS
# ══════════════════════════════════════════════════════════════════════════════
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
                    rows = [{
                        "Field":       r["field_name"],
                        "Rule Type":   r["rule_type"],
                        "Expected":    r.get("expected_value") or "not null",
                        "Critical":    "Yes" if r["is_critical"] else "No",
                        "Description": r.get("description", ""),
                    } for r in rules]
                    st.dataframe(rows, use_container_width=True, hide_index=True)
                else:
                    st.info("No rules defined.")

    with tab2:
        st.subheader("Add New Customer")
        new_name = st.text_input("Customer Name")
        new_id   = st.text_input("Customer ID (optional, auto-generated if blank)")

        st.caption("Define rules (add at least one):")

        if "new_rules" not in st.session_state:
            st.session_state.new_rules = []

        with st.form("add_rule_form"):
            rc1, rc2, rc3, rc4 = st.columns([2, 2, 1, 1])
            field_name   = rc1.text_input("Field Name", placeholder="incoterms")
            expected_val = rc2.text_input("Expected Value", placeholder="CIF (leave blank for not_null)")
            rule_type    = rc3.selectbox("Rule Type", ["exact", "contains", "regex", "not_null"])
            is_critical  = rc4.checkbox("Critical")
            add_rule     = st.form_submit_button("Add Rule")

            if add_rule and field_name:
                st.session_state.new_rules.append({
                    "field_name":     field_name,
                    "expected_value": expected_val or None,
                    "rule_type":      rule_type,
                    "is_critical":    is_critical,
                    "description":    f"{rule_type} rule for {field_name}",
                })

        if st.session_state.new_rules:
            st.caption(f"Rules to add ({len(st.session_state.new_rules)}):")
            for r in st.session_state.new_rules:
                st.caption(
                    f"• `{r['field_name']}` — {r['rule_type']} "
                    f"`{r.get('expected_value') or 'not null'}` "
                    f"{'🔴' if r['is_critical'] else '🔵'}"
                )

        if st.button("💾 Create Customer", type="primary") and new_name:
            cid = create_customer(new_name, new_id or None)
            if st.session_state.new_rules:
                upsert_customer_rules(cid, st.session_state.new_rules)
            st.success(f"Customer '{new_name}' created with ID: `{cid}`")
            st.session_state.new_rules = []
            _get_customers.clear()
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 5 — EVAL
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📈 Eval":
    st.title("Evaluation")
    st.caption("Run offline eval on labeled test documents.")

    report_path = "./data/eval_report.json"

    if os.path.exists(report_path):
        with open(report_path) as f:
            report = json.load(f)

        summary = report.get("summary", {})
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Docs Evaluated",      summary.get("total_docs", 0))
        col2.metric("Extraction Accuracy", f"{summary.get('avg_extraction_accuracy', 0):.0%}")
        col3.metric("Decision Accuracy",   f"{summary.get('decision_accuracy', 0):.0%}")
        col4.metric("Avg Latency",         f"{summary.get('avg_latency_seconds', 0):.1f}s")

        st.subheader("Per-Field Accuracy")
        field_acc = report.get("field_accuracy", {})
        rows = [{
            "Field":              f.replace("_", " ").title(),
            "Accuracy":           f"{v['accuracy']:.0%}",
            "Calibration Issues": v.get("calibration_issues", 0),
        } for f, v in field_acc.items()]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No eval report found. Run eval below to generate one.")

    if st.button("▶ Run Eval Now", type="primary"):
        with st.spinner("Running evaluation..."):
            from eval.eval import run_eval
            report = run_eval()
        if report:
            st.success("Eval complete.")
            st.rerun()
        else:
            st.warning(
                "No test documents found. "
                "Add documents to ./data/sample_docs/ and ground truth to ./data/ground_truth.json"
            )