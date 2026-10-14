const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const script = fs.readFileSync(path.join(__dirname, "..", "admin.js"), "utf8");

/** Create a minimal node that supports the dashboard's DOM operations. */
function makeElement(tagName = "div") {
  return {
    tagName,
    hidden: false,
    disabled: false,
    checked: false,
    selected: false,
    value: "",
    textContent: "",
    className: "",
    children: [],
    listeners: {},
    attributes: {},
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    setAttribute(name, value) { this.attributes[name] = value; },
  };
}

/** Start the module with a controllable API, workspace, and visibility observer. */
function loadAdmin(api) {
  const ids = [
    "app-root", "admin-nav", "admin-root", "conversation-topbar", "message-log", "app-error", "composer",
    "admin-overview-tab", "admin-users-tab", "admin-ai-tab", "admin-embeddings-tab",
    "admin-memory-tab", "admin-settings-tab", "admin-audit-tab", "admin-overview", "admin-users",
    "admin-memory", "admin-settings", "admin-audit", "admin-health",
    "admin-system-status", "admin-system-details", "admin-system-refresh",
    "admin-memory-status", "admin-memory-details", "admin-settings-status", "admin-settings-details",
    "admin-embeddings", "admin-embeddings-status", "admin-embeddings-details", "admin-embeddings-note",
    "admin-ai", "admin-ai-active", "admin-ai-saved", "admin-ai-provider", "admin-ai-model",
    "admin-ai-validate", "admin-ai-save", "admin-ai-status",
    "admin-users-status", "admin-user-list", "admin-audit-status", "admin-audit-list",
    "admin-users-previous", "admin-users-next", "admin-users-page", "admin-back",
    "admin-audit-previous", "admin-audit-next", "admin-audit-page", "admin-audit-refresh",
  ];
  const elements = Object.fromEntries(ids.map((id) => [id, makeElement()]));
  elements["app-root"].hidden = true;
  let onVisibilityChange;
  class FakeMutationObserver {
    /** Save the visibility callback for deterministic test control. */
    constructor(callback) { onVisibilityChange = callback; }
    /** Observe a node without scheduling browser work in the test. */
    observe() {}
  }
  const document = {
    getElementById: (id) => elements[id],
    createElement: (tagName) => makeElement(tagName),
  };
  const window = { SupportApi: api, MutationObserver: FakeMutationObserver };
  vm.runInNewContext(script, { window, document });
  return { elements, checkSession: onVisibilityChange };
}

test("only a server-confirmed administrator receives admin navigation", async () => {
  let role = "user";
  const { elements, checkSession } = loadAdmin({ currentUser: async () => ({ role, user_id: "u1" }) });
  elements["app-root"].hidden = false;
  await checkSession();
  assert.equal(elements["admin-nav"].hidden, true);

  role = "admin";
  await checkSession();
  assert.equal(elements["admin-nav"].hidden, false);
});

test("administrator overview loads health and user actions use the admin API", async () => {
  const calls = [];
  const { elements, checkSession } = loadAdmin({
    currentUser: async () => ({ role: "admin", user_id: "admin-id" }),
    health: async () => ({ status: "ok" }),
    adminSystemStatus: async () => ({
      database_status: "available",
      active_chat_provider: "groq",
      active_chat_model: "active-model",
      is_chat_provider_configured: true,
      max_upload_bytes: 2048,
      max_attachments_per_message: 2,
      stream_max_seconds: 90,
      memory_window_turns: 8,
      vision_enabled: false,
      semantic_retrieval_configured: true,
      note: "External provider connectivity is not checked.",
    }),
    adminUsers: async (limit, offset) => {
      calls.push(["list", limit, offset]);
      return [{
        user_id: "user-id",
        email: "person@example.test",
        role: "user",
        is_active: true,
        created_at: "2026-10-13",
      }];
    },
    updateAdminUser: async (...args) => calls.push(["update", ...args]),
  });
  elements["app-root"].hidden = false;
  await checkSession();
  elements["admin-nav"].listeners.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(elements["admin-health"].textContent, /available/);
  assert.match(elements["admin-system-status"].textContent, /connectivity is not checked/);
  const systemRows = elements["admin-system-details"].children
    .map((row) => row.children.map((child) => child.textContent).join(" "));
  assert.match(systemRows.join(" "), /Database Available/);
  assert.match(systemRows.join(" "), /groq \/ active-model/);
  assert.match(systemRows.join(" "), /2048 bytes/);
  assert.equal(elements["admin-system-refresh"].disabled, false);

  elements["admin-users-tab"].listeners.click();
  await new Promise((resolve) => setImmediate(resolve));
  const body = elements["admin-user-list"].children[0].children[0];
  const role = body.children[3];
  const active = body.children[5];
  const save = body.children[6];
  role.value = "admin";
  active.checked = false;
  await save.listeners.click();

  assert.deepEqual(calls[0], ["list", 20, 0]);
  assert.deepEqual(calls[1], ["update", "user-id", "admin", false]);
  assert.equal(elements["admin-users-page"].textContent, "Page 1");
});

test("AI Models loads supported choices, validates before saving, and shows saved versus active", async () => {
  const calls = [];
  const { elements, checkSession } = loadAdmin({
    currentUser: async () => ({ role: "admin", user_id: "admin-id" }),
    health: async () => ({ status: "ok" }),
    adminAIConfig: async () => ({
      active_provider: "groq",
      active_model: "active-groq",
      saved: null,
      providers: [
        { provider: "groq", models: ["active-groq", "default-groq"] },
        { provider: "openai", models: ["default-openai"] },
      ],
    }),
    validateAdminAI: async (provider, model) => {
      calls.push(["validate", provider, model]);
      return { is_valid: true, message: "Provider and model are configured." };
    },
    saveAdminAI: async (provider, model) => {
      calls.push(["save", provider, model]);
      return { provider, model, updated_at: "2026-10-14 12:00:00" };
    },
  });
  elements["app-root"].hidden = false;
  await checkSession();
  elements["admin-nav"].listeners.click();
  elements["admin-ai-tab"].listeners.click();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(elements["admin-ai-active"].textContent, "Active: groq / active-groq");
  assert.equal(elements["admin-ai-provider"].children.length, 2);
  assert.equal(elements["admin-ai-model"].value, "active-groq");
  assert.equal(elements["admin-ai-save"].disabled, true);
  await elements["admin-ai-validate"].listeners.click();
  assert.equal(elements["admin-ai-save"].disabled, false);
  await elements["admin-ai-save"].listeners.click();

  assert.deepEqual(calls, [
    ["validate", "groq", "active-groq"],
    ["save", "groq", "active-groq"],
  ]);
  assert.match(elements["admin-ai-saved"].textContent, /Saved: groq \/ active-groq/);
  assert.match(elements["admin-ai-status"].textContent, /does not activate on restart/);
  assert.equal(elements["admin-ai-active"].textContent, "Active: groq / active-groq");
});

test("audit tab loads bounded pages and renders account details as plain text", async () => {
  const calls = [];
  const { elements, checkSession } = loadAdmin({
    currentUser: async () => ({ role: "admin", user_id: "admin-id" }),
    health: async () => ({ status: "ok" }),
    adminAudit: async (limit, offset) => {
      calls.push([limit, offset]);
      return offset === 0 ? [{
        event_id: 3,
        action: "user.account_updated",
        actor_email: "admin@example.test",
        target_email: "<script>target@example.test</script>",
        details: { to_role: "admin", to_active: false },
        created_at: "2026-10-13 10:00:00",
      }] : [];
    },
  });
  elements["app-root"].hidden = false;
  await checkSession();
  elements["admin-nav"].listeners.click();
  elements["admin-audit-tab"].listeners.click();
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls[0], [20, 0]);
  const content = elements["admin-audit-list"].children[0].children[0].children;
  assert.equal(content[0].textContent, "user.account_updated");
  assert.match(content[1].textContent, /<script>target@example.test<\/script>/);
  assert.equal(elements["admin-audit-page"].textContent, "Page 1");

  elements["admin-audit-next"].listeners.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls[1], [20, 20]);
  assert.equal(elements["admin-audit-page"].textContent, "Page 2");
});

test("embedding panel shows safe configuration status and explains untracked health", async () => {
  const { elements, checkSession } = loadAdmin({
    currentUser: async () => ({ role: "admin", user_id: "admin-id" }),
    health: async () => ({ status: "ok" }),
    adminEmbeddingStatus: async () => ({
      provider: "huggingface",
      model: "thenlper/gte-large",
      is_provider_configured: true,
      vector_store: "qdrant",
      is_vector_store_configured: true,
      is_manual_available: true,
      vector_dimension: null,
      note: "Connectivity and indexed vector dimensions are not tracked.",
    }),
  });
  elements["app-root"].hidden = false;
  await checkSession();
  elements["admin-nav"].listeners.click();
  elements["admin-embeddings-tab"].listeners.click();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(elements["admin-embeddings"].hidden, false);
  assert.equal(elements["admin-ai"].hidden, true);
  assert.equal(elements["admin-embeddings-details"].children.length, 5);
  const rows = elements["admin-embeddings-details"].children.map((row) => row.children.map((child) => child.textContent).join(" "));
  assert.match(rows.join(" "), /huggingface/);
  assert.match(rows.join(" "), /Configured/);
  assert.match(rows.join(" "), /thenlper\/gte-large/);
  assert.match(rows.join(" "), /Not tracked/);
  assert.match(elements["admin-embeddings-status"].textContent, /not checked/);
});

test("embedding panel reports safe API failures", async () => {
  const { elements, checkSession } = loadAdmin({
    currentUser: async () => ({ role: "admin", user_id: "admin-id" }),
    health: async () => ({ status: "ok" }),
    adminEmbeddingStatus: async () => { throw new Error("Embedding status unavailable"); },
  });
  elements["app-root"].hidden = false;
  await checkSession();
  elements["admin-nav"].listeners.click();
  elements["admin-embeddings-tab"].listeners.click();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(elements["admin-embeddings-status"].textContent, "Embedding status unavailable");
  assert.equal(elements["admin-embeddings-details"].children.length, 0);
});

test("system status reports admin API failures without rendering stale details", async () => {
  const { elements, checkSession } = loadAdmin({
    currentUser: async () => ({ role: "admin", user_id: "admin-id" }),
    health: async () => ({ status: "ok" }),
    adminSystemStatus: async () => { throw new Error("System status unavailable"); },
  });
  elements["app-root"].hidden = false;
  await checkSession();
  elements["admin-nav"].listeners.click();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(elements["admin-system-status"].textContent, "System status unavailable");
  assert.equal(elements["admin-system-details"].children.length, 0);
  assert.equal(elements["admin-system-refresh"].disabled, false);
});

test("memory panel shows bounded memory and retrieval configuration without private history", async () => {
  const { elements, checkSession } = loadAdmin({
    currentUser: async () => ({ role: "admin", user_id: "admin-id" }),
    health: async () => ({ status: "ok" }),
    adminSystemStatus: async () => ({ memory_window_turns: 8, semantic_retrieval_configured: true }),
    adminEmbeddingStatus: async () => ({
      provider: "huggingface",
      model: "sample/embed-model",
      vector_store: "qdrant",
      is_vector_store_configured: true,
      vector_dimension: null,
      is_manual_available: true,
    }),
  });
  elements["app-root"].hidden = false;
  await checkSession();
  elements["admin-nav"].listeners.click();
  elements["admin-memory-tab"].listeners.click();
  await new Promise((resolve) => setImmediate(resolve));

  const rows = elements["admin-memory-details"].children
    .map((row) => row.children.map((child) => child.textContent).join(" "));
  assert.equal(elements["admin-memory"].hidden, false);
  assert.match(rows.join(" "), /8 turns/);
  assert.match(rows.join(" "), /huggingface \/ sample\/embed-model/);
  assert.match(rows.join(" "), /qdrant.*Configured/);
  assert.match(rows.join(" "), /Not tracked/);
  assert.match(elements["admin-memory-status"].textContent, /not checked/);
});

test("application settings panel lists supported limits as read-only values", async () => {
  const { elements, checkSession } = loadAdmin({
    currentUser: async () => ({ role: "admin", user_id: "admin-id" }),
    health: async () => ({ status: "ok" }),
    adminSystemStatus: async () => ({
      max_upload_bytes: 4096,
      max_attachments_per_message: 2,
      stream_max_seconds: 75,
      vision_enabled: true,
    }),
  });
  elements["app-root"].hidden = false;
  await checkSession();
  elements["admin-nav"].listeners.click();
  elements["admin-settings-tab"].listeners.click();
  await new Promise((resolve) => setImmediate(resolve));

  const rows = elements["admin-settings-details"].children
    .map((row) => row.children.map((child) => child.textContent).join(" "));
  assert.equal(elements["admin-settings"].hidden, false);
  assert.match(rows.join(" "), /4096 bytes/);
  assert.match(rows.join(" "), /Attachments per message 2/);
  assert.match(rows.join(" "), /75 seconds/);
  assert.match(rows.join(" "), /Vision support Enabled/);
  assert.match(elements["admin-settings-status"].textContent, /read-only/);
});
