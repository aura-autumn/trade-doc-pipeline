import { useState, useEffect } from "react";
import { getConfig } from "./api.js";
import Incoming from "./pages/Incoming.jsx";
import History from "./pages/History.jsx";
import Query from "./pages/Query.jsx";
import Customers from "./pages/Customers.jsx";
import Eval from "./pages/Eval.jsx";

const PAGES = [
  { key: "incoming", label: "Incoming", icon: "📥", el: Incoming },
  { key: "history", label: "Shipment History", icon: "📊", el: History },
  { key: "query", label: "Query", icon: "❓", el: Query },
  { key: "customers", label: "Manage Customers", icon: "⚙️", el: Customers },
  { key: "eval", label: "Eval", icon: "📈", el: Eval },
];

const GMAIL_DOT = {
  connected: "🟢",
  needs_auth: "🟡",
  not_configured: "⚪",
};

export default function App() {
  const [page, setPage] = useState("incoming");
  const [config, setConfig] = useState(null);
  // Lets History link into a shipment, and Incoming hand a fresh shipment to History.
  const [focusShipment, setFocusShipment] = useState(null);

  useEffect(() => {
    getConfig().then(setConfig).catch(() => setConfig({ llm_provider: "?", gmail: {} }));
  }, []);

  const goHistory = (shipmentId) => {
    setFocusShipment(shipmentId || null);
    setPage("history");
  };

  const Active = PAGES.find((p) => p.key === page).el;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">🚢</span>
          <div>
            <div className="brand-name">Nova</div>
            <div className="brand-sub">Trade Doc Pipeline</div>
          </div>
        </div>
        <nav>
          {PAGES.map((p) => (
            <button
              key={p.key}
              className={`nav-item ${page === p.key ? "active" : ""}`}
              onClick={() => setPage(p.key)}
            >
              <span className="nav-icon">{p.icon}</span>
              {p.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          {config && (
            <>
              <div className="foot-row">
                <span className="muted small">LLM</span>
                <span className="chip">{(config.llm_provider || "?").toUpperCase()}</span>
              </div>
              <div className="foot-row">
                <span className="muted small">Gmail</span>
                <span title={config.gmail?.detail || ""}>
                  {GMAIL_DOT[config.gmail?.state] || "⚪"} {config.gmail?.label || "—"}
                </span>
              </div>
            </>
          )}
        </div>
      </aside>

      <main className="content">
        <Active goHistory={goHistory} focusShipment={focusShipment} clearFocus={() => setFocusShipment(null)} />
      </main>
    </div>
  );
}
