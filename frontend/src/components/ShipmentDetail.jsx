import { useState, useEffect } from "react";
import { Pill, ConfBar, CriticalFlag, DecisionBanner, Spinner, titleCase } from "./bits.jsx";
import { markSent, askShipment, fetchSnippets } from "../api.js";

// The single shared renderer for a shipment's verification result — used by both
// the Incoming page (fresh run) and History (reconstructed from DB). Matches the
// agreed spec: flat field table (no clicks), cross-doc section below it, editable
// draft reply panel below that, then a RAG Q&A box.

function buildFieldRows(validation = []) {
  // Per-doc rows only; dedupe by field, keeping the highest-confidence instance.
  const perDoc = validation.filter((v) => !v.field_name.startsWith("cross_doc_"));
  const byField = {};
  for (const v of perDoc) {
    const cur = byField[v.field_name];
    if (!cur || (v.confidence || 0) > (cur.confidence || 0)) byField[v.field_name] = v;
  }
  return Object.values(byField);
}

export default function ShipmentDetail({ detail, onUpdated }) {
  const [draft, setDraft] = useState(detail.draft_email || "");
  const [sent, setSent] = useState(detail.reply_sent);
  const [marking, setMarking] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setDraft(detail.draft_email || "");
    setSent(detail.reply_sent);
  }, [detail.shipment_id, detail.draft_email, detail.reply_sent]);

  const fieldRows = buildFieldRows(detail.validation);
  const crossDoc = (detail.validation || []).filter((v) => v.field_name.startsWith("cross_doc_"));

  async function handleMarkSent() {
    setMarking(true);
    try {
      await markSent(detail.shipment_id);
      setSent(true);
      onUpdated && onUpdated();
    } catch (e) {
      alert("Failed to mark as sent: " + e.message);
    } finally {
      setMarking(false);
    }
  }

  return (
    <div className="detail">
      <DecisionBanner decision={detail.decision} reasoning={detail.reasoning} />

      {/* Cross-document consistency — surfaced, never hidden */}
      {crossDoc.length > 0 && (
        <section className="card cross-doc">
          <h3>⚠ Cross-Document Inconsistencies ({crossDoc.length})</h3>
          {crossDoc.map((v) => (
            <div className="cross-doc-row" key={v.field_name}>
              <strong>{titleCase(v.field_name.replace("cross_doc_discrepancy_", ""))}</strong>
              <Pill status="mismatch" />
              <div className="cross-doc-detail">{v.detail || v.found_value}</div>
            </div>
          ))}
        </section>
      )}

      {/* Flat per-field validation table — every field inline, no clicks.
          Optional "Source" expander grounds each field in the doc snippet it came from. */}
      <section className="card">
        <h3>📋 Validation Results — {detail.customer_name}</h3>
        <div className="table-scroll">
          <table className="field-table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Extracted Value</th>
                <th>Confidence</th>
                <th>Status</th>
                <th>Expected</th>
                <th>Critical</th>
                <th>Detail</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {fieldRows.length === 0 && (
                <tr><td colSpan={8} className="muted">No validated fields.</td></tr>
              )}
              {fieldRows.map((v) => (
                <FieldRow key={v.field_name} v={v} shipmentId={detail.shipment_id} />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Draft reply — editable panel, agent NEVER sends on its own */}
      <section className="card">
        <h3>✉ Draft Reply to Supplier</h3>
        {!draft ? (
          <p className="muted">No draft email required — shipment was approved.</p>
        ) : (
          <>
            <div className="notice">
              ⚠ Agent-drafted reply. Review every line before sending. The agent never sends on its own.
            </div>
            <textarea
              className="draft-area"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={12}
              disabled={sent}
            />
            <div className="draft-actions">
              {sent ? (
                <span className="sent-badge">✅ Reply marked as sent</span>
              ) : (
                <button className="btn btn-primary" onClick={handleMarkSent} disabled={marking}>
                  {marking ? "Marking…" : "✅ Mark as Sent"}
                </button>
              )}
              <button
                className="btn btn-ghost"
                onClick={() => {
                  navigator.clipboard?.writeText(draft);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                }}
              >
                {copied ? "Copied!" : "📋 Copy"}
              </button>
              <span className="muted small">CG reviews → edits → sends. Never automatic.</span>
            </div>
          </>
        )}
      </section>

      <RawExtraction extraction={detail.extraction} />
      <RagChat shipmentId={detail.shipment_id} />
    </div>
  );
}

function FieldRow({ v, shipmentId }) {
  const [open, setOpen] = useState(false);
  const [snippets, setSnippets] = useState(null); // null = not loaded, [] = none found
  const [loading, setLoading] = useState(false);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && snippets === null) {
      setLoading(true);
      try {
        // Ground the field by querying for its name + extracted value.
        const q = `${v.field_name.replace(/_/g, " ")} ${v.found_value || ""}`.trim();
        const res = await fetchSnippets(shipmentId, q, 2);
        setSnippets(res.snippets || []);
      } catch {
        setSnippets([]);
      } finally {
        setLoading(false);
      }
    }
  }

  return (
    <>
      <tr>
        <td className="field-name">{titleCase(v.field_name)}</td>
        <td>{v.found_value || <span className="muted">—</span>}</td>
        <td><ConfBar value={v.confidence || 0} /></td>
        <td><Pill status={v.status} /></td>
        <td>{v.expected_value || <span className="muted">Any / Not null</span>}</td>
        <td><CriticalFlag critical={v.is_critical} /></td>
        <td className="detail-cell">{v.detail || ""}</td>
        <td>
          <button className="btn btn-ghost small" onClick={toggle} title="Show the document text this field came from">
            {open ? "Hide" : "🔍 Source"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="source-row">
          <td colSpan={8}>
            {loading && <Spinner label="Retrieving source snippet…" />}
            {!loading && snippets && snippets.length === 0 && (
              <span className="muted small">
                No source snippet found (document may not be RAG-indexed for this shipment).
              </span>
            )}
            {!loading && snippets && snippets.map((s, i) => (
              <div className="snippet" key={i}>
                <span className="snippet-score">match {Math.round((s.score || 0) * 100)}%</span>
                <div className="snippet-text">{s.text}</div>
              </div>
            ))}
          </td>
        </tr>
      )}
    </>
  );
}

function RawExtraction({ extraction = {} }) {
  const docs = Object.entries(extraction);
  if (docs.length === 0) return null;
  return (
    <section className="card">
      <h3>📄 Raw Extraction by Document</h3>
      {docs.map(([docName, fields]) => (
        <div key={docName} className="raw-doc">
          <div className="raw-doc-name">{docName}</div>
          <div className="table-scroll">
            <table className="field-table compact">
              <thead>
                <tr><th>Field</th><th>Value</th><th>Confidence</th><th>Method</th></tr>
              </thead>
              <tbody>
                {Object.entries(fields).map(([f, d]) => (
                  <tr key={f}>
                    <td className="field-name">{titleCase(f)}</td>
                    <td>{d.value || <span className="muted">—</span>}</td>
                    <td><ConfBar value={d.confidence || 0} /></td>
                    <td className="muted small">{(d.method || "llm").toUpperCase()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </section>
  );
}

function RagChat({ shipmentId }) {
  const [history, setHistory] = useState([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  async function ask(e) {
    e.preventDefault();
    if (!q.trim() || busy) return;
    const question = q.trim();
    setHistory((h) => [...h, { role: "user", content: question }]);
    setQ("");
    setBusy(true);
    try {
      const res = await askShipment(shipmentId, question);
      setHistory((h) => [...h, { role: "assistant", content: res.answer }]);
    } catch (err) {
      setHistory((h) => [...h, { role: "assistant", content: "Error: " + err.message }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h3>🔎 Ask About This Shipment</h3>
      <p className="muted small">
        Ask about document content, validation results, or why the decision was made.
      </p>
      <div className="chat">
        {history.map((m, i) => (
          <div key={i} className={`chat-msg chat-${m.role}`}>
            <span className="chat-role">{m.role === "user" ? "You" : "Nova"}</span>
            <div>{m.content}</div>
          </div>
        ))}
        {busy && <Spinner label="Thinking…" />}
      </div>
      <form className="chat-input" onSubmit={ask}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="e.g. What HS code is on the invoice?"
        />
        <button className="btn btn-primary" disabled={busy}>Ask</button>
      </form>
    </section>
  );
}
