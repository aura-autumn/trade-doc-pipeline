import { useState, useEffect } from "react";
import {
  getCustomers, getRules, getSamples, runSample, getGmailFeed, getShipment, api,
} from "../api.js";
import ShipmentDetail from "../components/ShipmentDetail.jsx";
import { Spinner, DecisionTag } from "../components/bits.jsx";

// State machine: idle → processing → done. Mirrors the brief's 4 CG states
// (Incoming email → agent processing → verification result → draft reply).
export default function Incoming() {
  const [state, setState] = useState("idle");
  const [customers, setCustomers] = useState([]);
  const [rules, setRules] = useState([]);
  const [samples, setSamples] = useState([]);
  const [feed, setFeed] = useState([]);

  const [customerId, setCustomerId] = useState("");
  const [sender, setSender] = useState("shipping@acme-exports.com");
  const [subject, setSubject] = useState("Shipment Documents");
  const [files, setFiles] = useState([]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getCustomers().then((cs) => {
      setCustomers(cs);
      if (cs[0]) setCustomerId(cs[0].id);
    });
    getSamples().then(setSamples);
    refreshFeed();
  }, []);

  useEffect(() => {
    if (customerId) getRules(customerId).then(setRules).catch(() => setRules([]));
  }, [customerId]);

  const refreshFeed = () => getGmailFeed().then(setFeed).catch(() => setFeed([]));

  async function runUpload() {
    setError("");
    setState("processing");
    try {
      const detail = await api.runPipeline({ customerId, sender, subject, files });
      setResult(detail);
      setState("done");
      refreshFeed();
    } catch (e) {
      setError(e.message);
      setState("idle");
    }
  }

  async function runSampleByName(name) {
    setError("");
    setState("processing");
    try {
      const detail = await runSample(name);
      setResult(detail);
      setState("done");
      refreshFeed();
    } catch (e) {
      setError(e.message);
      setState("idle");
    }
  }

  async function viewFeedItem(shipmentId) {
    setState("processing");
    try {
      const detail = await getShipment(shipmentId);
      setResult(detail);
      setState("done");
    } catch (e) {
      setError(e.message);
      setState("idle");
    }
  }

  function reset() {
    setResult(null);
    setFiles([]);
    setState("idle");
  }

  if (state === "processing") {
    return (
      <div className="page">
        <h1>Incoming Shipment</h1>
        <div className="card processing-card">
          <Spinner label="Agent processing — extracting → validating → routing…" />
        </div>
      </div>
    );
  }

  if (state === "done" && result) {
    return (
      <div className="page">
        <div className="page-head">
          <div>
            <h1>{result.email_subject || "Shipment"}</h1>
            <p className="muted">
              {result.email_from && <>From <strong>{result.email_from}</strong> · </>}
              Shipment <code>{result.shipment_id}</code> · {result.doc_count} doc(s)
              {result.elapsed != null && <> · processed in {result.elapsed}s</>}
            </p>
          </div>
          <button className="btn btn-ghost" onClick={reset}>📨 New Email</button>
        </div>
        <ShipmentDetail detail={result} onUpdated={() => getShipment(result.shipment_id).then(setResult)} />
      </div>
    );
  }

  // ── idle ───────────────────────────────────────────────────────────────────
  return (
    <div className="page">
      <h1>Incoming Shipment</h1>
      <p className="muted">
        Simulate an SU email arriving with trade-document attachments. The agent
        processes it immediately — extract, validate, decide, draft.
      </p>

      {error && <div className="error-box">⚠ {error}</div>}

      <div className="two-col">
        <section className="card">
          <h3>📨 Simulate SU Email</h3>
          <label>Customer</label>
          <select value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>

          <label>From (SU email)</label>
          <input value={sender} onChange={(e) => setSender(e.target.value)} />

          <label>Subject</label>
          <input value={subject} onChange={(e) => setSubject(e.target.value)} />

          <label>Attach trade documents (PDF / image)</label>
          <input
            type="file"
            multiple
            accept=".pdf,.jpg,.jpeg,.png,.webp"
            onChange={(e) => setFiles(Array.from(e.target.files))}
          />
          {files.length > 0 && (
            <p className="muted small">{files.map((f) => f.name).join(", ")}</p>
          )}

          <button
            className="btn btn-primary block"
            disabled={!files.length || !customerId}
            onClick={runUpload}
          >
            📨 Send Email to Nova
          </button>

          {samples.length > 0 && (
            <>
              <div className="divider">or use a pre-built sample</div>
              <div className="sample-list">
                {samples.map((s) => (
                  <button key={s.name} className="sample-btn" onClick={() => runSampleByName(s.name)}>
                    <strong>{s.name}</strong>
                    <span className="muted small">{s.subject} · {s.attachments.length} doc(s)</span>
                  </button>
                ))}
              </div>
            </>
          )}
        </section>

        <section className="card">
          <h3>Rules for this customer ({rules.length})</h3>
          {rules.length === 0 && <p className="muted">No rules defined.</p>}
          <ul className="rule-list">
            {rules.map((r) => (
              <li key={r.id || r.field_name}>
                <span className={r.is_critical ? "dot dot-crit" : "dot dot-opt"} />
                <code>{r.field_name}</code> — {r.rule_type}{" "}
                <strong>{r.expected_value || "not null"}</strong>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section className="card">
        <h3>📡 Inbox Watcher Feed — {feed.length} processed</h3>
        {feed.length === 0 ? (
          <p className="muted">
            No watcher-processed shipments yet. Start a watcher —{" "}
            <code>python -m inbox.trigger</code> (folder/simulated inbox) or{" "}
            <code>python -m inbox.gmail_trigger</code> (real Gmail) — then drop / send an email
            with PDF attachments. It appears here automatically.
          </p>
        ) : (
          <table className="field-table">
            <thead>
              <tr><th>Subject</th><th>From</th><th>Decision</th><th></th></tr>
            </thead>
            <tbody>
              {feed.map((f) => (
                <tr key={f.shipment_id}>
                  <td>{f.email_subject}</td>
                  <td className="muted small">{f.email_from}</td>
                  <td><DecisionTag decision={f.decision} /></td>
                  <td>
                    <button className="btn btn-ghost small" onClick={() => viewFeedItem(f.shipment_id)}>
                      View
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
