const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const routeScript = fs.readFileSync(path.join(__dirname, "..", "route.js"), "utf8");

function redirectedAddress(protocol, hostname, port) {
  let address = null;
  const context = {
    window: {
      location: {
        protocol,
        hostname,
        port,
        replace: (value) => {
          address = value;
        },
      },
    },
  };
  vm.runInNewContext(routeScript, context);
  return address;
}

test("routes localhost preview servers through FastAPI", () => {
  assert.equal(redirectedAddress("http:", "localhost", "5500"), "http://127.0.0.1:8000/");
});

test("routes file-opened UI through FastAPI", () => {
  assert.equal(redirectedAddress("file:", "", ""), "http://127.0.0.1:8000/");
});

test("keeps the FastAPI-served account page on its current origin", () => {
  assert.equal(redirectedAddress("http:", "127.0.0.1", "8000"), null);
});

test("loads the origin redirect before page styles and account API scripts", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  assert.match(html, /<script src="route\.js\?v=backend-origin-1"><\/script>\s*<link rel="stylesheet"/);
});

test("styles the account composer textarea instead of the legacy chat field", () => {
  const stylesheet = fs.readFileSync(path.join(__dirname, "..", "styles.css"), "utf8");
  assert.match(stylesheet, /#message-input\s*\{[^}]*flex:\s*1;[^}]*min-height:\s*41px;[^}]*resize:\s*none;/s);
  assert.match(stylesheet, /#message-input::placeholder/);
});
