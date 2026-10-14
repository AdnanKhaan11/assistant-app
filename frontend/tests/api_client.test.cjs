const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const apiScript = fs.readFileSync(path.join(__dirname, "..", "api.js"), "utf8");
const page = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");

function loadApi(fetch, cookie = "") {
  const window = {};
  vm.runInNewContext(apiScript, {
    window,
    document: { cookie },
    fetch,
    Headers,
    encodeURIComponent,
    Error,
  });
  return window.SupportApi;
}

test("login uses same-origin cookies without saving credentials", async () => {
  let request;
  const api = loadApi(async (url, options) => {
    request = { url, options };
    return Response.json({ user: { email: "user@example.test" } });
  });

  await api.login("user@example.test", "private password");

  assert.equal(request.url, "/api/auth/login");
  assert.equal(request.options.credentials, "same-origin");
  assert.equal(request.options.headers.get("Content-Type"), "application/json");
  assert.deepEqual(JSON.parse(request.options.body), {
    email: "user@example.test",
    password: "private password",
  });
  assert.equal(request.options.headers.has("X-CSRF-Token"), false);
});

test("private writes include the CSRF cookie and encode conversation IDs", async () => {
  let request;
  const api = loadApi(async (url, options) => {
    request = { url, options };
    return Response.json({ conversation_id: "created" });
  }, "assistant_csrf=nonce%2Fvalue");

  await api.chat("id/with spaces", "hello");

  assert.equal(request.url, "/api/conversations/id%2Fwith%20spaces/chat");
  assert.equal(request.options.headers.get("X-CSRF-Token"), "nonce/value");
  assert.equal(request.options.credentials, "same-origin");
});

test("401 responses become a friendly session-expired API error", async () => {
  const api = loadApi(async () => Response.json({ detail: "internal session detail" }, { status: 401 }));

  await assert.rejects(api.currentUser(), (error) => {
    assert.equal(error.name, "ApiError");
    assert.equal(error.status, 401);
    assert.equal(error.message, "Your session expired. Sign in again.");
    return true;
  });
});

test("login failures show the credential error instead of expired-session guidance", async () => {
  const api = loadApi(async () => Response.json(
    { detail: "Email or password is incorrect." },
    { status: 401 },
  ));

  await assert.rejects(api.login("person@example.test", "wrong password"), (error) => {
    assert.equal(error.message, "Email or password is incorrect.");
    return true;
  });
});

test("registration validation responses explain email and password requirements", async () => {
  const api = loadApi(async () => Response.json({
    detail: [{ loc: ["body", "password"], type: "string_too_short" }],
  }, { status: 422 }));

  await assert.rejects(api.register("person@example.test", "short"), (error) => {
    assert.equal(error.status, 422);
    assert.equal(error.message, "Password must be at least 12 characters.");
    return true;
  });
});

test("route and server failures give useful guidance without showing internals", async () => {
  const missingRoute = loadApi(async () => Response.json({ detail: "Not Found" }, { status: 404 }));
  await assert.rejects(missingRoute.login("person@example.test", "password"), (error) => {
    assert.match(error.message, /http:\/\/127\.0\.0\.1:8000\//);
    return true;
  });

  const serverFailure = loadApi(async () => Response.json({ detail: "database password=secret" }, { status: 500 }));
  await assert.rejects(serverFailure.login("person@example.test", "password"), (error) => {
    assert.match(error.message, /backend terminal/);
    assert.doesNotMatch(error.message, /secret|database/);
    return true;
  });
});

test("administrator APIs use bounded paths, CSRF proof, and safe update fields", async () => {
  const requests = [];
  const api = loadApi(async (url, options) => {
    requests.push({ url, options });
    return Response.json({ status: "ok" });
  }, "assistant_csrf=admin-nonce");

  await api.adminUsers(20, 40);
  await api.adminAudit(20, 60);
  await api.updateAdminUser("user/one", "admin", false);
  await api.health();

  assert.equal(requests[0].url, "/api/auth/admin/users?limit=20&offset=40");
  assert.equal(requests[1].url, "/api/auth/admin/audit?limit=20&offset=60");
  assert.equal(requests[2].url, "/api/auth/admin/users/user%2Fone");
  assert.equal(requests[2].options.headers.get("X-CSRF-Token"), "admin-nonce");
  assert.deepEqual(JSON.parse(requests[2].options.body), { role: "admin", is_active: false });
  assert.equal(requests[3].url, "/health");
});

test("frontend entry keeps legacy ticket code out of the private account page", () => {
  assert.match(page, /id="auth-root"/);
  assert.match(page, /id="app-root"[^>]*hidden/);
  assert.match(page, /<script src="api\.js\?v=auth-flow-2"><\/script>\s*<script src="conversations\.js"><\/script>\s*<script src="auth\.js"><\/script>\s*<script src="admin\.js"><\/script>/);
  assert.match(page, /id="admin-root"[^>]*hidden/);
  assert.match(page, /id="admin-audit-tab"/);
  assert.match(page, /id="admin-audit"[^>]*hidden/);
  assert.doesNotMatch(page, /<script[^>]+src="app\.js"/);
});
