// Thin fetch wrapper around the FastAPI backend. All paths are relative; the
// Vite dev server proxies /api → http://localhost:8000.

async function handle(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

const json = (method) => (path, body) =>
  fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(handle);

export const api = {
  get: (path) => fetch(path).then(handle),
  post: json("POST"),

  // Pipeline run with file uploads (multipart).
  runPipeline: ({ customerId, sender, subject, files }) => {
    const fd = new FormData();
    fd.append("customer_id", customerId);
    fd.append("sender", sender || "supplier@example.com");
    fd.append("subject", subject || "Shipment Documents");
    for (const f of files) fd.append("files", f);
    return fetch("/api/pipeline/run", { method: "POST", body: fd }).then(handle);
  },
};

// ── Convenience endpoints ─────────────────────────────────────────────────────
export const getConfig = () => api.get("/api/config");
export const getCustomers = () => api.get("/api/customers");
export const getRules = (id) => api.get(`/api/customers/${id}/rules`);
export const createCustomer = (payload) => api.post("/api/customers", payload);
export const getSamples = () => api.get("/api/samples");
export const runSample = (name) => api.post("/api/pipeline/run-sample", { name });
export const getShipments = (params = {}) => {
  const qs = new URLSearchParams(params).toString();
  return api.get(`/api/shipments${qs ? `?${qs}` : ""}`);
};
export const getShipment = (id) => api.get(`/api/shipments/${id}`);
export const markSent = (id) => api.post(`/api/shipments/${id}/mark-sent`);
export const askShipment = (id, question) =>
  api.post(`/api/shipments/${id}/ask`, { question });
export const fetchSnippets = (id, query, top_k = 2) =>
  api.post(`/api/shipments/${id}/snippets`, { query, top_k });
export const runQuery = (question, shipmentId) =>
  api.post("/api/query", { question, shipment_id: shipmentId || null });
export const getGmailStatus = () => api.get("/api/gmail/status");
export const getGmailFeed = () => api.get("/api/gmail/feed");
export const getEvalReport = () => api.get("/api/eval/report");
export const runEval = () => api.post("/api/eval/run");
