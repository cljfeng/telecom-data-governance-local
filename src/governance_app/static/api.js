let inMemoryAccessToken = "";

export async function fetchJson(url, options = {}) {
  let response;
  const method = String(options.method || "GET").toUpperCase();
  const csrfToken = window.sessionStorage.getItem("governance.csrf") || "";
  const securityHeaders =
    method === "GET" || method === "HEAD" || !csrfToken
      ? {}
      : { "X-CSRF-Token": csrfToken };
  const authorizationHeaders = inMemoryAccessToken
    ? { Authorization: `Bearer ${inMemoryAccessToken}` }
    : {};
  try {
    response = await fetch(url, {
      ...options,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        ...authorizationHeaders,
        ...securityHeaders,
        ...(options.headers || {}),
      },
    });
  } catch (error) {
    throw new Error(`无法连接本地服务：${error.message}`);
  }
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent("governance:auth-required"));
  }
  if (response.status === 202 && data.task) {
    window.dispatchEvent(
      new CustomEvent("governance:task-queued", { detail: data.task }),
    );
  }
  if (!response.ok) {
    const error = new Error(data.error || `HTTP ${response.status}`);
    error.data = data;
    throw error;
  }
  return data;
}

export function setCsrfToken(token) {
  if (token) window.sessionStorage.setItem("governance.csrf", token);
  else window.sessionStorage.removeItem("governance.csrf");
}

export function setAccessToken(token) {
  inMemoryAccessToken = token || "";
}

export async function postJson(url, payload) {
  return fetchJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function postFormData(url, formData) {
  return fetchJson(url, {
    method: "POST",
    body: formData,
  });
}
