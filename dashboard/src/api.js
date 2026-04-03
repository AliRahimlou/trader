const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

export function getApiBase() {
  return API_BASE;
}

export async function fetchJson(path, options = {}) {
  const headers = {
    ...(options.headers || {}),
  };
  if (options.body !== undefined && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(`${API_BASE}${path}`, {
    headers,
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    try {
      const parsed = JSON.parse(body);
      throw new Error(parsed.detail || body || `Request failed with status ${response.status}`);
    } catch {
      throw new Error(body || `Request failed with status ${response.status}`);
    }
  }
  return response.json();
}

export async function postControl(commandType, payload = {}, confirm = false, actor = "local-operator") {
  return fetchJson(`/api/controls/${commandType}`, {
    method: "POST",
    body: JSON.stringify({
      actor,
      confirm,
      payload,
    }),
  });
}
