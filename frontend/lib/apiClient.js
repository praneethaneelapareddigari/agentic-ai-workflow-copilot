const BASE = "/api/backend";

export async function createWorkflow(message, conversationId) {
  const res = await fetch(`${BASE}/workflows`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId || null }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed with status ${res.status}`);
  }
  return res.json();
}

export async function getWorkflow(workflowId) {
  const res = await fetch(`${BASE}/workflows/${workflowId}`);
  if (!res.ok) throw new Error("Failed to fetch workflow");
  return res.json();
}

export async function listWorkflows(conversationId) {
  const qs = conversationId ? `?conversation_id=${conversationId}` : "";
  const res = await fetch(`${BASE}/workflows${qs}`);
  if (!res.ok) throw new Error("Failed to list workflows");
  return res.json();
}

export async function getMetrics() {
  const res = await fetch(`${BASE}/workflows/_meta/metrics`);
  if (!res.ok) throw new Error("Failed to fetch metrics");
  return res.json();
}
