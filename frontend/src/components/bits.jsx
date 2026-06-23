// Small shared presentational pieces: status pills, confidence bars, decision
// banners, critical flags. Kept dependency-free.

export function Pill({ status }) {
  const map = {
    match: ["✓ Match", "pill-match"],
    mismatch: ["✗ Mismatch", "pill-mismatch"],
    uncertain: ["⚠ Uncertain", "pill-uncertain"],
    missing: ["🚫 Missing", "pill-missing"],
    not_checked: ["➖ No Rule", "pill-neutral"],
  };
  const [label, cls] = map[status] || [status, "pill-neutral"];
  return <span className={`pill ${cls}`}>{label}</span>;
}

export function ConfBar({ value = 0 }) {
  const pct = Math.round(value * 100);
  const color = value >= 0.85 ? "#16a34a" : value >= 0.6 ? "#d97706" : "#dc2626";
  return (
    <div className="confbar">
      <div className="confbar-track">
        <div className="confbar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="confbar-pct" style={{ color }}>{pct}%</span>
    </div>
  );
}

export function CriticalFlag({ critical }) {
  return critical ? (
    <span className="flag flag-critical">● Critical</span>
  ) : (
    <span className="flag flag-optional">○ Optional</span>
  );
}

const DECISION = {
  auto_approve: ["✅ Auto Approved", "banner-approve"],
  flag_for_review: ["⚠️ Flagged for Review", "banner-flag"],
  draft_amendment: ["📝 Amendment Drafted", "banner-amend"],
};

export function DecisionBanner({ decision, reasoning }) {
  const [label, cls] = DECISION[decision] || [decision || "—", "banner-flag"];
  return (
    <div className={`banner ${cls}`}>
      <strong>{label}</strong>
      {reasoning && <div className="banner-reason">{reasoning}</div>}
    </div>
  );
}

export function DecisionTag({ decision }) {
  const [label, cls] = DECISION[decision] || [decision || "—", "banner-flag"];
  return <span className={`tag ${cls}`}>{label}</span>;
}

export function StatusTag({ status }) {
  return <span className={`tag tag-${status}`}>{(status || "").replace(/_/g, " ")}</span>;
}

export function Spinner({ label }) {
  return (
    <div className="spinner-wrap">
      <div className="spinner" />
      {label && <span>{label}</span>}
    </div>
  );
}

export const titleCase = (s = "") =>
  s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
