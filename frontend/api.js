/* Shared same-origin API calls for account, conversation, and administrator workflows. */
(function attachSupportApi(window, document) {
  "use strict";

  /** Preserve HTTP status for UI decisions without exposing provider or server internals. */
  class ApiError extends Error {
    /**
     * Store a friendly API `message` and HTTP `status` for UI handling.
     * @param {string} message Safe text to display to the user.
     * @param {number} status HTTP status code, or zero for a transport error.
     */
    constructor(message, status = 0) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

  /** Add CSRF proof to cookie-authenticated writes without persisting credentials in the browser. */
  function csrfToken() {
    const cookie = document.cookie.split(";").map((item) => item.trim());
    const match = cookie.find((item) => item.startsWith("assistant_csrf="));
    return match ? decodeURIComponent(match.slice("assistant_csrf=".length)) : "";
  }

  /**
   * Turn FastAPI validation entries into safe account-form guidance.
   * @param {unknown} errors Validation details returned by FastAPI.
   */
  function validationMessage(errors) {
    if (!Array.isArray(errors)) return "Check the email and password, then try again.";
    const issue = errors.find((item) => item && typeof item === "object");
    if (!issue) return "Check the email and password, then try again.";
    const field = Array.isArray(issue.loc) ? issue.loc[issue.loc.length - 1] : "";
    if (field === "email") return "Enter a valid email address.";
    if (field === "password" && issue.type === "string_too_short") {
      return "Password must be at least 12 characters.";
    }
    if (field === "password" && issue.type === "string_too_long") {
      return "Password must be no more than 128 characters.";
    }
    return "Check the email and password, then try again.";
  }

  /**
   * Choose a clear, non-sensitive message for an HTTP failure response.
   * @param {number} status HTTP status code returned by the server.
   * @param {unknown} detail Optional FastAPI error detail.
   * @param {string} path Endpoint that returned the failure.
   */
  function failureMessage(status, detail, path) {
    if (status === 401 && path === "/api/auth/login") {
      return typeof detail === "string" ? detail : "Email or password is incorrect.";
    }
    if (status === 401) return "Your session expired. Sign in again.";
    if (status === 404) return "Sign-in service not found. Open the app at http://127.0.0.1:8000/.";
    if (status === 422) {
      return typeof detail === "string" ? detail : validationMessage(detail);
    }
    if (status >= 500) return "The server encountered an error. Check the backend terminal and try again.";
    if (typeof detail === "string" && detail) return detail;
    return `Request rejected (HTTP ${status}). Check the fields and try again.`;
  }

  /**
   * Send one same-origin JSON request and return its payload or a friendly API error.
   * @param {string} path Backend endpoint path.
   * @param {{method?: string, body?: unknown, csrf?: boolean, headers?: HeadersInit}} options Request settings.
   */
  async function request(path, options = {}) {
    const method = options.method || "GET";
    const headers = new Headers(options.headers || {});
    const init = { method, credentials: "same-origin", headers, cache: "no-store" };
    if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(options.body);
    }
    if (options.csrf) {
      const token = csrfToken();
      if (!token) throw new ApiError("Your session expired. Sign in again.", 401);
      headers.set("X-CSRF-Token", token);
    }
    let response;
    try {
      response = await fetch(path, init);
    } catch {
      throw new ApiError("The server could not be reached. Try again.");
    }
    if (!response.ok) {
      let detail;
      try {
        const payload = await response.json();
        detail = payload.detail;
      } catch {
        // Use the status-specific safe message when the server did not return JSON.
      }
      throw new ApiError(failureMessage(response.status, detail, path), response.status);
    }
    if (response.status === 204) return null;
    return response.json();
  }

  /**
   * Encode one resource path component.
   * @param {string} base Endpoint path before the identifier.
   * @param {string} id Server-issued resource identifier.
   */
  function resourcePath(base, id) {
    return `${base}/${encodeURIComponent(id)}`;
  }

  const api = Object.freeze({
    ApiError,
    /** Register an account with `email` and `password`. */
    register: (email, password) => request("/api/auth/register", {
      method: "POST",
      body: { email, password },
    }),
    /** Start a cookie session using `email` and `password`. */
    login: (email, password) => request("/api/auth/login", {
      method: "POST",
      body: { email, password },
    }),
    /** Return the current authenticated account. */
    currentUser: () => request("/api/auth/me"),
    /** Read the authenticated account's saved interface preferences. */
    userSettings: () => request("/api/settings"),
    /** Save the authenticated account's supported interface preferences. */
    saveUserSettings: (settings) => request("/api/settings", {
      method: "PUT",
      body: settings,
      csrf: true,
    }),
    /** Return a bounded administrator-only page of safe account metadata. */
    adminUsers: (limit = 20, offset = 0) =>
      request(`/api/auth/admin/users?limit=${limit}&offset=${offset}`),
    /** Return a bounded administrator-only page of safe account audit events. */
    adminAudit: (limit = 20, offset = 0) =>
      request(`/api/auth/admin/audit?limit=${limit}&offset=${offset}`),
    /** Read active and saved chat-model configuration and supported choices for an administrator. */
    adminAIConfig: () => request("/api/auth/admin/ai-config"),
    /** Read configured embedding components without exposing credentials or local paths. */
    adminEmbeddingStatus: () => request("/api/auth/admin/ai-config/embeddings"),
    /** Read safe runtime readiness details through the administrator-only API. */
    adminSystemStatus: () => request("/api/auth/admin/ai-config/system-status"),
    /** Validate a provider/model pair without saving it. */
    validateAdminAI: (provider, model) => request("/api/auth/admin/ai-config/validate", {
      method: "POST",
      body: { provider, model },
      csrf: true,
    }),
    /** Save a validated non-secret provider/model pair for a later backend restart. */
    saveAdminAI: (provider, model) => request("/api/auth/admin/ai-config", {
      method: "PUT",
      body: { provider, model },
      csrf: true,
    }),
    /** Update one account's role and active state through the administrator-only endpoint. */
    updateAdminUser: (userId, role, isActive) => request(
      `/api/auth/admin/users/${encodeURIComponent(userId)}`,
      { method: "PATCH", body: { role, is_active: isActive }, csrf: true },
    ),
    /** Read the minimal public backend liveness status. */
    health: () => request("/health"),
    /** Revoke the active session and clear its cookies. */
    logout: () => request("/api/auth/logout", { method: "POST", csrf: true }),
    /** Return a bounded page using `limit` and `offset` for the current user. */
    conversations: (limit = 50, offset = 0) =>
      request(`/api/conversations?limit=${limit}&offset=${offset}`),
    /** Create an owned conversation with `title`. */
    createConversation: (title) => request("/api/conversations", {
      method: "POST",
      body: { title },
      csrf: true,
    }),
    /** Read a bounded message page for `conversationId`, using `limit` and `offset`. */
    messages: (conversationId, limit = 50, offset = 0) =>
      request(`${resourcePath("/api/conversations", conversationId)}/messages?limit=${limit}&offset=${offset}`),
    /** Send `message` to the assistant in owned `conversationId`. */
    chat: (conversationId, message) => request(
      `${resourcePath("/api/conversations", conversationId)}/chat`,
      { method: "POST", body: { message }, csrf: true },
    ),
    /** Submit `approved` for `actionId` in owned `conversationId`. */
    confirmAction: (conversationId, actionId, approved) => request(
      `${resourcePath("/api/conversations", conversationId)}/actions/${encodeURIComponent(actionId)}/confirm`,
      { method: "POST", body: { approved }, csrf: true },
    ),
    /** Rename owned `conversationId` to `title`. */
    renameConversation: (conversationId, title) => request(
      resourcePath("/api/conversations", conversationId),
      { method: "PATCH", body: { title }, csrf: true },
    ),
    /** Delete owned `conversationId` and its messages. */
    deleteConversation: (conversationId) => request(
      resourcePath("/api/conversations", conversationId),
      { method: "DELETE", csrf: true },
    ),
    /** Replace `messageId` text in `conversationId`; the backend removes stale later replies. */
    editMessage: (conversationId, messageId, content) => request(
      `${resourcePath("/api/conversations", conversationId)}/messages/${encodeURIComponent(messageId)}`,
      { method: "PATCH", body: { content }, csrf: true },
    ),
    /** Delete `messageId` and later replies from `conversationId`. */
    deleteMessage: (conversationId, messageId) => request(
      `${resourcePath("/api/conversations", conversationId)}/messages/${encodeURIComponent(messageId)}`,
      { method: "DELETE", csrf: true },
    ),
  });

  window.SupportApi = api;
})(window, document);
