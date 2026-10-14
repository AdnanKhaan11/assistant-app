const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const authScript = fs.readFileSync(path.join(__dirname, "..", "auth.js"), "utf8");

function makeElement() {
  return {
    hidden: false,
    disabled: false,
    value: "",
    textContent: "",
    className: "",
    dataset: {},
    children: [],
    listeners: {},
    attributes: {},
    classList: {
      add() {},
      toggle() {},
    },
    append(...children) { this.children.push(...children); },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, callback) {
      this.listeners[name] = callback;
    },
  };
}

function loadAuth(api) {
  const ids = [
    "auth-root",
    "app-root",
    "auth-form",
    "auth-error",
    "auth-title",
    "auth-description",
    "auth-submit",
    "auth-password",
    "auth-mode",
    "auth-email",
    "account-email",
    "sign-out",
  ];
  const elements = Object.fromEntries(ids.map((id) => [id, makeElement()]));
  const railFoot = makeElement();
  elements["sign-out"].parentElement = railFoot;
  const document = {
    getElementById: (id) => elements[id],
    createElement: () => makeElement(),
    documentElement: { dataset: {} },
  };
  const window = {
    SupportApi: api,
    PrivateConversations: {
      show(user) {
        elements["app-root"].hidden = false;
        elements["account-email"].textContent = user.email;
      },
      clear() { elements["app-root"].hidden = true; },
    },
  };
  vm.runInNewContext(authScript, { window, document });
  return { elements, railFoot, document };
}

test("restores a cookie session without asking for credentials again", async () => {
  const { elements, railFoot } = loadAuth({
    currentUser: async () => ({ email: "person@example.test" }),
    userSettings: async () => ({ theme: "dark" }),
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(elements["auth-root"].hidden, true);
  assert.equal(elements["app-root"].hidden, false);
  assert.equal(elements["account-email"].textContent, "person@example.test");
  assert.equal(railFoot.children[0].className, "user-preferences");
  assert.equal(railFoot.children[0].children[1].value, "dark");
});

test("registration returns to login, and sign-in opens the account view", async () => {
  const calls = [];
  const { elements } = loadAuth({
    currentUser: async () => { throw Object.assign(new Error("expired"), { status: 401 }); },
    register: async (...args) => calls.push(["register", ...args]),
    login: async (...args) => {
      calls.push(["login", ...args]);
      return { user: { email: args[0] } };
    },
    logout: async () => calls.push(["logout"]),
  });
  await new Promise((resolve) => setImmediate(resolve));
  elements["auth-form"].dataset.mode = "register";
  elements["auth-email"].value = "person@example.test";
  elements["auth-password"].value = "a secure test password";
  await elements["auth-form"].listeners.submit({ preventDefault() {} });

  assert.equal(elements["auth-form"].dataset.mode, "login");
  assert.equal(elements["auth-error"].textContent, "Account created. Sign in to continue.");
  assert.equal(elements["auth-password"].value, "");
  elements["auth-password"].value = "a secure test password";
  await elements["auth-form"].listeners.submit({ preventDefault() {} });

  assert.deepEqual(calls.map((call) => call[0]), ["register", "login"]);
  assert.equal(elements["auth-root"].hidden, true);
  assert.equal(elements["app-root"].hidden, false);
});

test("sign-out waits for server revocation before showing login", async () => {
  const { elements } = loadAuth({
    currentUser: async () => ({ email: "person@example.test" }),
    logout: async () => {},
  });
  await new Promise((resolve) => setImmediate(resolve));

  await elements["sign-out"].listeners.click();

  assert.equal(elements["app-root"].hidden, true);
  assert.equal(elements["auth-root"].hidden, false);
  assert.equal(elements["auth-form"].dataset.mode, "login");
});

test("theme changes apply only after the server confirms the saved preference", async () => {
  const calls = [];
  const { railFoot, document } = loadAuth({
    currentUser: async () => ({ email: "person@example.test" }),
    userSettings: async () => ({ theme: "dark" }),
    saveUserSettings: async (settings) => {
      calls.push(settings);
      return settings;
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  const control = railFoot.children[0].children[1];
  control.value = "light";
  await control.listeners.change();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].theme, "light");
  assert.equal(document.documentElement.dataset.theme, "light");
  assert.equal(railFoot.children[0].children[2].textContent, "Saved light theme.");
});

test("theme save failure restores the last server-confirmed appearance", async () => {
  const { railFoot, document } = loadAuth({
    currentUser: async () => ({ email: "person@example.test" }),
    userSettings: async () => ({ theme: "dark" }),
    saveUserSettings: async () => { throw new Error("Preference could not be saved."); },
  });
  await new Promise((resolve) => setImmediate(resolve));
  const control = railFoot.children[0].children[1];
  control.value = "light";
  await control.listeners.change();

  assert.equal(control.value, "dark");
  assert.equal(document.documentElement.dataset.theme, "dark");
  assert.equal(railFoot.children[0].children[2].textContent, "Preference could not be saved.");
});
