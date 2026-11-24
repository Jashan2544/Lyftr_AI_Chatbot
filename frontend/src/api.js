const runtime = (typeof window !== "undefined" && window.__ENV__) || {};
export const API_URL = (runtime.API_URL || import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");

export function newSessionId() {
  const rand =
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID().replace(/-/g, "").slice(0, 12)
      : Math.random().toString(36).slice(2, 14);
  return `s-${rand}`;
}

function describeError(status, body) {
  const detail = body && body.detail;
  if (status === 422) {
    if (Array.isArray(detail)) {
      const msgs = detail.map((d) => d.msg?.replace(/^Value error, /, "")).filter(Boolean);
      return `Invalid request: ${msgs.join("; ") || "validation failed"}`;
    }
    return `Invalid request: ${detail || "validation failed"}`;
  }
  if (status === 503) return "All AI providers are unavailable right now. Please try again in a moment.";
  if (typeof detail === "string") return `Error ${status}: ${detail}`;
  return `Unexpected error (HTTP ${status}).`;
}

export async function sendChat({ sessionId, message, systemPrompt, simulateFailure }) {
  const headers = { "Content-Type": "application/json" };
  if (simulateFailure) headers["X-Simulate-Failure"] = simulateFailure;
  const payload = { session_id: sessionId, message };
  if (systemPrompt && systemPrompt.trim()) payload.system_prompt = systemPrompt.trim();

  let res;
  try {
    res = await fetch(`${API_URL}/chat`, { method: "POST", headers, body: JSON.stringify(payload) });
  } catch {
    return { ok: false, error: `Could not reach the backend at ${API_URL}.` };
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) return { ok: false, status: res.status, error: describeError(res.status, body) };
  return { ok: true, data: body };
}
