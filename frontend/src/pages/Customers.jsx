import { useState, useEffect } from "react";
import { getCustomers, getRules, createCustomer } from "../api.js";

const RULE_TYPES = ["exact", "contains", "regex", "not_null"];

export default function Customers() {
  const [customers, setCustomers] = useState([]);
  const [rulesById, setRulesById] = useState({});
  const [expanded, setExpanded] = useState(null);

  const [name, setName] = useState("");
  const [id, setId] = useState("");
  const [draftRules, setDraftRules] = useState([]);
  const [rule, setRule] = useState({ field_name: "", expected_value: "", rule_type: "exact", is_critical: false });
  const [msg, setMsg] = useState("");

  useEffect(() => { refresh(); }, []);

  function refresh() {
    getCustomers().then(setCustomers);
  }

  async function toggle(cid) {
    if (expanded === cid) { setExpanded(null); return; }
    setExpanded(cid);
    if (!rulesById[cid]) {
      const r = await getRules(cid);
      setRulesById((m) => ({ ...m, [cid]: r }));
    }
  }

  function addRule() {
    if (!rule.field_name.trim()) return;
    setDraftRules((rs) => [...rs, { ...rule, expected_value: rule.expected_value || null }]);
    setRule({ field_name: "", expected_value: "", rule_type: "exact", is_critical: false });
  }

  async function submit() {
    if (!name.trim()) { setMsg("Customer name is required."); return; }
    try {
      const res = await createCustomer({ name, id: id || null, rules: draftRules });
      setMsg(`✅ Created ${res.name} (${res.id}) with ${res.rule_count} rule(s).`);
      setName(""); setId(""); setDraftRules([]);
      refresh();
    } catch (e) {
      setMsg("⚠ " + e.message);
    }
  }

  return (
    <div className="page">
      <h1>Manage Customers</h1>
      <p className="muted">Each customer carries its own validation rule set — this is the tacit knowledge the FDE encodes.</p>

      <div className="two-col">
        <section className="card">
          <h3>Existing Customers ({customers.length})</h3>
          {customers.map((c) => (
            <div key={c.id} className="customer-row">
              <button className="customer-head" onClick={() => toggle(c.id)}>
                <strong>{c.name}</strong>
                <span className="muted small">{c.id} · {c.rule_count} rules</span>
                <span>{expanded === c.id ? "▲" : "▼"}</span>
              </button>
              {expanded === c.id && (
                <div className="table-scroll">
                  <table className="field-table compact">
                    <thead>
                      <tr><th>Field</th><th>Type</th><th>Expected</th><th>Critical</th></tr>
                    </thead>
                    <tbody>
                      {(rulesById[c.id] || []).map((r) => (
                        <tr key={r.id || r.field_name}>
                          <td className="field-name">{r.field_name}</td>
                          <td>{r.rule_type}</td>
                          <td>{r.expected_value || <span className="muted">not null</span>}</td>
                          <td>{r.is_critical ? "● Yes" : "○ No"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
        </section>

        <section className="card">
          <h3>Add New Customer</h3>
          <label>Customer Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Acme Imports Ltd" />
          <label>Customer ID (optional)</label>
          <input value={id} onChange={(e) => setId(e.target.value)} placeholder="auto-generated if blank" />

          <div className="divider">Validation rules</div>
          <div className="rule-builder">
            <input
              placeholder="field_name (e.g. incoterms)"
              value={rule.field_name}
              onChange={(e) => setRule({ ...rule, field_name: e.target.value })}
            />
            <input
              placeholder="expected (blank = not null)"
              value={rule.expected_value}
              onChange={(e) => setRule({ ...rule, expected_value: e.target.value })}
            />
            <select value={rule.rule_type} onChange={(e) => setRule({ ...rule, rule_type: e.target.value })}>
              {RULE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={rule.is_critical}
                onChange={(e) => setRule({ ...rule, is_critical: e.target.checked })}
              />
              Critical
            </label>
            <button className="btn btn-ghost" onClick={addRule}>+ Add rule</button>
          </div>

          {draftRules.length > 0 && (
            <ul className="rule-list">
              {draftRules.map((r, i) => (
                <li key={i}>
                  <span className={r.is_critical ? "dot dot-crit" : "dot dot-opt"} />
                  <code>{r.field_name}</code> — {r.rule_type} <strong>{r.expected_value || "not null"}</strong>
                </li>
              ))}
            </ul>
          )}

          <button className="btn btn-primary block" onClick={submit}>💾 Create Customer</button>
          {msg && <p className="msg">{msg}</p>}
        </section>
      </div>
    </div>
  );
}
