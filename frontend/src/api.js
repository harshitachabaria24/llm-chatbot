// Base URL is injected at build time (VITE_API_BASE_URL); no trailing slash.
const BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export function newSessionId() {
  const id =
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID().slice(0, 8)
      : Math.random().toString(36).slice(2, 10);
  return `s-${id}`;
}

// Turn any failure into a short, readable sentence for the chat.
function describeError(status, detail) {
  if (status === 503) {
    return "All AI providers are unavailable right now. Wait a moment and send your message again.";
  }
  if (status === 422) {
    if (Array.isArray(detail)) {
      const first = detail[0] || {};
      const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : "message";
      if (field === "message") {
        return "Your message must be between 1 and 4000 characters. Edit it and send again.";
      }
      return `The request was rejected: check the ${field} field and try again.`;
    }
    return "The request was rejected. Check your message and try again.";
  }
  if (typeof detail === "string" && detail) return detail;
  return `Something went wrong (status ${status}). Try again.`;
}

export async function sendChat({ sessionId, message, systemPrompt, simulateFailure }) {
  const headers = { "Content-Type": "application/json" };
  if (simulateFailure) headers["X-Simulate-Failure"] = simulateFailure;

  const body = { session_id: sessionId, message };
  if (systemPrompt && systemPrompt.trim()) body.system_prompt = systemPrompt.trim();

  let res;
  try {
    res = await fetch(`${BASE}/chat`, { method: "POST", headers, body: JSON.stringify(body) });
  } catch {
    throw new Error("Can't reach the server. Check your connection and try again.");
  }

  let data = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) throw new Error(describeError(res.status, data && data.detail));
  return data;
}
