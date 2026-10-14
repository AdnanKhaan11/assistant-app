const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const script = fs.readFileSync(path.join(__dirname, "..", "conversations.js"), "utf8");

/** Provide the small DOM surface used by the conversation module. */
function makeElement(tagName = "div") {
  return {
    tagName,
    hidden: false,
    disabled: false,
    value: "",
    textContent: "",
    className: "",
    children: [],
    listeners: {},
    attributes: {},
    classList: { add() {}, remove() {} },
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    setAttribute(name, value) { this.attributes[name] = value; },
    querySelector() { return this.children.find((child) => child.tagName === "button") || null; },
    remove() { this.removed = true; },
    scrollTop: 0,
    scrollHeight: 0,
  };
}

/** Load the browser module with fake DOM nodes and a controllable API client. */
function loadModule(api) {
  const ids = [
    "app-root", "account-email", "conversation-list", "conversation-empty", "conversation-title",
    "conversation-id", "message-thread", "message-log", "app-error", "message-input", "composer",
    "send-message", "new-conversation", "rename-conversation", "delete-conversation",
  ];
  const elements = Object.fromEntries(ids.map((id) => [id, makeElement()]));
  elements["app-root"].hidden = true;
  const document = {
    getElementById: (id) => elements[id],
    createElement: (tagName) => makeElement(tagName),
  };
  const window = {
    SupportApi: api,
    PrivateAuth: { showSignIn() {} },
    confirm: () => true,
    prompt: () => null,
  };
  vm.runInNewContext(script, { window, document, navigator: { clipboard: { writeText: async () => {} } }, Date });
  return { elements, module: window.PrivateConversations };
}

/** Collect visible text from a fake element and its child nodes. */
function visibleText(node) {
  return [node.textContent, ...node.children.map(visibleText)].join(" ");
}

test("loads private history and inserts untrusted message text as text", async () => {
  const hostile = "<img src=x onerror=alert(1)>";
  const api = {
    conversations: async () => [{ conversation_id: "c1", title: "My issue", updated_at: "2026-10-13 10:00:00" }],
    messages: async () => [{
      message_id: "m1", role: "user", content: hostile, created_at: "2026-10-13 10:00:00",
    }],
  };
  const { elements, module } = loadModule(api);

  await module.show({ email: "person@example.test" });

  assert.equal(elements["app-root"].hidden, false);
  assert.equal(elements["account-email"].textContent, "person@example.test");
  assert.equal(elements["conversation-title"].textContent, "My issue");
  assert.equal(elements["message-thread"].children[0].children[0].textContent, hostile);
});

test("sends a message and renders the server classification and trace", async () => {
  let storedMessages = [];
  const api = {
    conversations: async () => [{ conversation_id: "c1", title: "Network issue", updated_at: "2026-10-13 10:00:00" }],
    messages: async () => storedMessages,
    chat: async (conversationId, content) => {
      assert.equal(conversationId, "c1");
      storedMessages = [
        { message_id: "m1", role: "user", content, created_at: "2026-10-13 10:00:00" },
        { message_id: "m2", role: "assistant", content: "Try checking Wi-Fi.", created_at: "2026-10-13 10:00:01" },
      ];
      return {
        category: "network_issue",
        confidence: 0.9,
        summary: "Wi-Fi connection issue.",
        findings: [{ tool: "network_check", status: "ok", details: "Latency is stable." }],
        recommendations: ["Check the adapter."],
        trace_id: "trace-123",
      };
    },
  };
  const { elements, module } = loadModule(api);
  await module.show({ email: "person@example.test" });
  elements["message-input"].value = "Wi-Fi is not working";

  await elements.composer.listeners.submit({ preventDefault() {} });

  const text = elements["message-thread"].children.map(visibleText).join(" ");
  assert.match(text, /Try checking Wi-Fi/);
  assert.match(text, /Network issue/);
  assert.match(text, /Latency is stable/);
  assert.match(text, /trace-123/);
  assert.equal(elements["message-input"].disabled, false);
});
