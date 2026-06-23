import { useState, useEffect } from "react";
import { getEvalReport, runEval } from "../api.js";
import { Spinner, titleCase } from "../components/bits.jsx";

export default function Eval() {
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getEvalReport().then((r) => setReport(Object.keys(r).length ? r : null)).catch(() => {});
  }, []);

  async function run() {
    setBusy(true);
    setError("");
    try {
      setReport(await runEval());
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const s = report?.summary || {};
  const fields = report?.field_accuracy || {};

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Evaluation</h1>
          <p className="muted">Offline accuracy eval against labeled ground-truth documents.</p>
        </div>
        <button className="btn btn-primary" onClick={run} disabled={busy}>
          {busy ? "Running…" : "▶ Run Eval Now"}
        </button>
      </div>

      {error && <div className="error-box">⚠ {error}</div>}
      {busy && <div className="card"><Spinner label="Running pipeline over the eval set…" /></div>}

      {!report && !busy && <p className="muted">No eval report yet — run one to populate metrics.</p>}

      {report && (
        <>
          <div className="metrics">
            <Metric num={s.total_docs ?? 0} label="Docs evaluated" />
            <Metric num={pct(s.avg_extraction_accuracy)} label="Extraction accuracy" />
            <Metric num={pct(s.decision_accuracy)} label="Decision accuracy" />
            <Metric num={`${s.avg_latency_seconds ?? 0}s`} label="Avg latency" />
          </div>

          <section className="card">
            <h3>Per-Field Accuracy</h3>
            <div className="table-scroll">
              <table className="field-table">
                <thead>
                  <tr><th>Field</th><th>Accuracy</th><th>Calibration issues</th></tr>
                </thead>
                <tbody>
                  {Object.entries(fields).map(([f, v]) => (
                    <tr key={f}>
                      <td className="field-name">{titleCase(f)}</td>
                      <td>{pct(v.accuracy)}</td>
                      <td>{v.calibration_issues > 0
                        ? <span className="flag flag-critical">⚠ {v.calibration_issues}</span>
                        : <span className="muted">0</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

const pct = (v) => `${Math.round((v || 0) * 100)}%`;

function Metric({ num, label }) {
  return (
    <div className="metric">
      <div className="metric-num">{num}</div>
      <div className="metric-label">{label}</div>
    </div>
  );
}
