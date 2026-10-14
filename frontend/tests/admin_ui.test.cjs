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
    "admin-overview-tab", "admin-users-tab",
    "admin-audit-tab", "admin-overview", "admin-users", "admin-audit", "admin-health",
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
