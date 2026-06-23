import { useState, useEffect } from "react";
import { runQuery, getConfig } from "../api.js";
import { Spinner } from "../components/bits.jsx";

export default function Query() {
  const [question, setQuestion] = useState("");
  const [shipmentId, setShipmentId] = useState("");
  const [examples, setExamples] = useState([]);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getConfig().then((c) => setExamples(c.example_queries || [])).catch(() => {});
  }, []);

  async function ask(qOverride) {
    const q = qOverride ?? question;
    if (!q.trim()) return;
    setQuestion(q);
    setBusy(true);
    setResult(null);
    try {
      setResult(await runQuery(q, shipmentId));
    } catch (e) {
      setResult({ answer: "Error: " + e.message, error: e.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <h1>Natural Language Query</h1>
      <p className="muted">
        Ask about shipments in plain English. Structured questions route to Text-to-SQL;
        document-content questions (with a shipment ID) route to RAG.
      </p>

      <div className="two-col">
        <section className="card">
          <label>Question</label>
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="How many shipments were flagged this week?"
            onKeyDown={(e) => e.key === "Enter" && ask()}
          />
          <label>Shipment ID (optional — for document-content questions)</label>
          <input value={shipmentId} onChange={(e) => setShipmentId(e.target.value)} placeholder="e.g. a1b2c3d4" />
          <button className="btn btn-primary block" onClick={() => ask()} disabled={busy}>
            Ask
          </button>

          {busy && <Spinner label="Thinking…" />}

          {result && (
            <div className="query-result">
              <h4>Answer</h4>
              <p className="answer">{result.answer}</p>
              {result.sql && (
                <details open>
                  <summary>SQL</summary>
                  <pre className="sql">{result.sql}</pre>
                </details>
              )}
              {result.results && result.results.length > 0 && (
                <details>
                  <summary>Raw results ({result.results.length} rows)</summary>
                  <pre className="sql">{JSON.stringify(result.results.slice(0, 20), null, 2)}</pre>
                </details>
              )}
            </div>
          )}
        </section>

        <section className="card">
          <h3>Example queries</h3>
          <div className="example-list">
            {examples.map((eq) => (
              <button key={eq} className="example-btn" onClick={() => ask(eq)}>{eq}</button>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
