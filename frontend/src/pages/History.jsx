import { useState, useEffect } from "react";
import { getCustomers, getShipments, getShipment } from "../api.js";
import ShipmentDetail from "../components/ShipmentDetail.jsx";
import { StatusTag, Spinner } from "../components/bits.jsx";

const STATUSES = ["", "processing", "approved", "flagged", "amendment_drafted", "reply_sent", "error"];

export default function History({ focusShipment, clearFocus }) {
  const [customers, setCustomers] = useState([]);
  const [customerId, setCustomerId] = useState("");
  const [status, setStatus] = useState("");
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getCustomers().then(setCustomers);
  }, []);

  useEffect(() => {
    load();
  }, [customerId, status]);

  // Deep-link from the Incoming page into a specific shipment.
  useEffect(() => {
    if (focusShipment) {
      open(focusShipment);
      clearFocus && clearFocus();
    }
  }, [focusShipment]);

  function load() {
    const params = {};
    if (customerId) params.customer_id = customerId;
    if (status) params.status = status;
    getShipments(params).then(setRows);
  }

  async function open(id) {
    setLoading(true);
    try {
      setSelected(await getShipment(id));
    } finally {
      setLoading(false);
    }
  }

  const counts = rows.reduce((acc, r) => {
    acc[r.status] = (acc[r.status] || 0) + 1;
    return acc;
  }, {});

  if (selected) {
    return (
      <div className="page">
        <div className="page-head">
          <div>
            <h1>Shipment {selected.shipment_id}</h1>
            <p className="muted">
              {selected.customer_name} · <StatusTag status={selected.status} /> ·{" "}
              {(selected.created_at || "").slice(0, 16).replace("T", " ")}
            </p>
          </div>
          <button className="btn btn-ghost" onClick={() => setSelected(null)}>← Back to list</button>
        </div>
        <ShipmentDetail detail={selected} onUpdated={() => { open(selected.shipment_id); load(); }} />
      </div>
    );
  }

  return (
    <div className="page">
      <h1>Shipment History</h1>
      <p className="muted">Every transaction the agent has processed, filterable by customer and status.</p>

      <div className="filters">
        <div>
          <label>Customer</label>
          <select value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
            <option value="">All customers</option>
            {customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <div>
          <label>Status</label>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s ? s.replace(/_/g, " ") : "All statuses"}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="metrics">
        {Object.entries(counts).map(([s, n]) => (
          <div className="metric" key={s}>
            <div className="metric-num">{n}</div>
            <div className="metric-label">{s.replace(/_/g, " ")}</div>
          </div>
        ))}
        {rows.length === 0 && <p className="muted">No shipments yet.</p>}
      </div>

      {loading && <Spinner label="Loading shipment…" />}

      {rows.length > 0 && (
        <div className="table-scroll">
          <table className="field-table">
            <thead>
              <tr><th>Shipment</th><th>Customer</th><th>Documents</th><th>Status</th><th>Created</th><th></th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td><code>{r.id}</code></td>
                  <td>{r.customer_name}</td>
                  <td className="muted small">{r.doc_filename || "—"}</td>
                  <td><StatusTag status={r.status} /></td>
                  <td className="muted small">{(r.created_at || "").slice(0, 16).replace("T", " ")}</td>
                  <td><button className="btn btn-ghost small" onClick={() => open(r.id)}>Open</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
