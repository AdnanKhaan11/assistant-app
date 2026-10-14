/* Helpdesk Assistant - plain JavaScript client (no build step, no dependencies).
 *
 * API contract (see backend/schemas.py)
 *   POST   /api/chat                         {ticket_id, message}  -> AssessmentResponse
 *   POST   /api/actions/{id}/confirm         {approved}            -> ActionResultResponse
 *   GET    /api/tickets/{ticket_id}/history  -> [{role, content, ...}]   (optional, restores a ticket after reload)
 *   DELETE /api/tickets/{ticket_id}
 *   GET    /health
 *
 * Security: every piece of text from the user or the server is inserted with textContent,
 * never innerHTML. innerHTML is only used for the constant SVG icons below.
 */
(() => {
  "use strict";

  const localHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
  const isFilePage = window.location.protocol === "file:";
  const isLocalPreview =
    localHosts.has(window.location.hostname) && window.location.port !== "8000";
  if (isFilePage || isLocalPreview) {
    window.location.replace("http://127.0.0.1:8000/");
    return;
  }

  const API = {
    chat: "/api/chat",
    confirm: (id) => `/api/actions/${encodeURIComponent(id)}/confirm`,
    history: (t) => `/api/tickets/${encodeURIComponent(t)}/history`,
    ticket: (t) => `/api/tickets/${encodeURIComponent(t)}`,
    health: "/health",
  };

  const STORE_KEY = "helpdesk.tickets.v1";
  const SAMPLE_TICKETS = [
    { id: "T-1001", title: "Wi-Fi drops during calls" },
    { id: "T-1002", title: "Low disk space warning" },
    { id: "T-1003", title: "Docking station not detected" },
  ];
  const SUGGESTIONS = [
    "My Wi-Fi keeps dropping during video calls",
    "My laptop says the disk is almost full",
    "My external monitors stay black after the update",
  ];
  const CATEGORY = {
    network_issue: { label: "Network", cls: "network" },
    software_issue: { label: "Software", cls: "software" },
    hardware_issue: { label: "Hardware", cls: "hardware" },
    unknown: { label: "Needs more detail", cls: "unknown" },
  };

  const ICONS = {
    check:
      '<svg viewBox="0 0 20 20" aria-hidden="true">' +
      '<circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
      '<path d="m6.5 10.2 2.4 2.4 4.6-5" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    alert:
      '<svg viewBox="0 0 20 20" aria-hidden="true">' +
      '<circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
      '<path d="M10 6v4.6M10 13.4v.1" stroke="currentColor" ' +
      'stroke-width="1.9" stroke-linecap="round"/></svg>',
    arrow:
      '<svg viewBox="0 0 20 20" aria-hidden="true">' +
      '<path d="M4 10h11M11 5.5 15.5 10 11 14.5" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  // ---------- state ----------
  const state = {
    tickets: loadTickets(),
    activeId: null,
    transcripts: new Map(), // ticketId -> entries[]
    historyLoaded: new Set(),
    busy: new Set(), // ticket ids with a request in flight
    nextEntryId: 1,
  };

  // ---------- dom ----------
  const $ = (id) => document.getElementById(id);
  const shell = $("shell"),
    thread = $("thread"),
    logEl = $("log"),
    form = $("composer");
  const input = $("message"),
    sendBtn = $("send-btn"),
    counter = $("char-count");

  function h(tag, props = {}, ...kids) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on"))
        node.addEventListener(key.slice(2).toLowerCase(), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const kid of kids.flat())
      if (kid != null && kid !== false) node.append(kid);
    return node;
  }
  function icon(name) {
    const holder = document.createElement("span");
    holder.innerHTML = ICONS[name]; // constant, trusted markup only
    return holder.firstElementChild;
  }

  // ---------- tickets ----------
  function loadTickets() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
      if (Array.isArray(saved) && saved.length) return saved;
    } catch {
      /* storage unavailable or corrupted: fall back to samples */
    }
    return SAMPLE_TICKETS.map((t) => ({ ...t, custom: false }));
  }
  function saveTickets() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(state.tickets));
    } catch {
      /* ignore */
    }
  }
  function newTicketId() {
    return "T-" + Date.now().toString(36).toUpperCase().slice(-5);
  }
  function activeTicket() {
    return state.tickets.find((t) => t.id === state.activeId);
  }
  function transcript(id) {
    if (!state.transcripts.has(id)) state.transcripts.set(id, []);
    return state.transcripts.get(id);
  }
  function createTicket() {
    const ticket = { id: newTicketId(), title: "New ticket", custom: true };
    state.tickets.unshift(ticket);
    saveTickets();
    selectTicket(ticket.id);
    closeRail();
    input.focus();
  }
  function selectTicket(id) {
    state.activeId = id;
    renderRail();
    renderHeader();
    renderThread();
    syncComposer();
    loadHistory(id);
  }

  async function loadHistory(id) {
    if (state.historyLoaded.has(id) || transcript(id).length) return;
    state.historyLoaded.add(id);
    try {
      const res = await fetch(API.history(id));
      if (!res.ok) return;
      const rows = await res.json();
      const turns = Array.isArray(rows) ? rows : rows.history || [];
      if (!turns.length || transcript(id).length) return;
      for (const row of turns) {
        const role = row.role === "user" ? "user" : "assistant_text";
        transcript(id).push({
          id: state.nextEntryId++,
          type: role,
          text: String(row.content ?? ""),
          at: row.created_at ? new Date(row.created_at + "Z") : null,
        });
      }
      if (state.activeId === id) renderThread();
    } catch {
      /* history is optional */
    }
  }

  // ---------- rendering ----------
  function renderRail() {
    const list = $("ticket-list");
    list.replaceChildren(
      ...state.tickets.map((t) =>
        h(
          "li",
          {},
          h(
            "button",
            {
              class: "ticket-item",
              type: "button",
              "aria-current": t.id === state.activeId ? "true" : "false",
              onClick: () => {
                selectTicket(t.id);
                closeRail();
              },
            },
            h("span", { class: "ticket-title", text: t.title }),
            h("span", { class: "ticket-sub", text: t.id }),
          ),
        ),
      ),
    );
  }
  function renderHeader() {
    const t = activeTicket();
    $("ticket-heading").textContent = t ? t.title : "New ticket";
    $("ticket-id").textContent = t ? t.id : "";
    document.title = t
      ? `${t.title} - Nexus Support`
      : "Nexus | IT Incident Response";
  }

  // mode: "end" = jump to newest (default) | "lastTop" = show the start of the newest reply | "keep" = don't move
  function renderThread(mode = "end") {
    const previous = logEl.scrollTop;
    const entries = transcript(state.activeId);
    const nodes = entries.map(renderEntry);
    if (!entries.length && !state.busy.has(state.activeId))
      nodes.push(renderEmpty());
    if (state.busy.has(state.activeId)) nodes.push(renderWorking());
    thread.replaceChildren(...nodes);
    requestAnimationFrame(() => {
      if (mode === "keep") logEl.scrollTop = previous;
      else if (mode === "lastTop" && thread.lastElementChild)
        logEl.scrollTop +=
          thread.lastElementChild.getBoundingClientRect().top -
          logEl.getBoundingClientRect().top -
          16;
      else logEl.scrollTop = logEl.scrollHeight;
    });
  }

  function renderEmpty() {
    return h(
      "div",
      { class: "empty" },
      h("span", { class: "empty-kicker", text: "NEXUS · IT RESPONSE" }),
      h("h2", { text: "Let's get your tech back on track." }),
      h("p", {
        text:
          "Tell us what happened. Nexus will assess the issue, run safe checks where useful, " +
          "and give you practical next steps. Changes to your system always wait for your approval.",
      }),
      h(
        "div",
        { class: "suggestions" },
        SUGGESTIONS.map((text) =>
          h(
            "button",
            {
              class: "suggestion",
              type: "button",
              onClick: () => {
                input.value = text;
                autosize();
                syncComposer();
                submit();
              },
            },
            h("span", { text }),
            icon("arrow"),
          ),
        ),
      ),
    );
  }

  function renderWorking() {
    return h(
      "div",
      { class: "row-assistant entry", "aria-label": "Working on your issue" },
      h(
        "div",
        { class: "working" },
        h("span", { class: "dots" }, h("i"), h("i"), h("i")),
        "Analysing your issue",
      ),
    );
  }

  function renderEntry(entry) {
    switch (entry.type) {
      case "user":
        return renderUser(entry);
      case "assistant_text":
        return h(
          "div",
          { class: "row-assistant" },
          h("div", { class: "plain", text: entry.text }),
        );
      case "assessment":
        return renderAssessment(entry);
      case "notice":
        return renderNotice(entry);
      default:
        return h("div");
    }
  }

  function renderUser(entry) {
    return h(
      "div",
      { class: "row-user entry" },
      h("div", { class: "bubble-user", text: entry.text }),
      entry.at
        ? h("span", { class: "stamp", text: formatTime(entry.at) })
        : null,
    );
  }

  function renderAssessment(entry) {
    const d = entry.data;
    const cat = CATEGORY[d.category] || CATEGORY.unknown;
    const pct = Math.round(
      Math.max(0, Math.min(1, Number(d.confidence) || 0)) * 100,
    );
    const filled = Math.round(pct / 10);
    const unknown = d.category === "unknown";

    const head = h(
      "div",
      { class: "card-head" },
      h("span", { class: `chip ${cat.cls}`, text: cat.label }),
      h(
        "div",
        {
          class: "meter",
          title: "How sure the assistant is about this classification",
        },
        h("span", { text: "Confidence" }),
        h(
          "span",
          { class: "meter-bar", "aria-hidden": "true" },
          Array.from({ length: 10 }, (_, i) =>
            h("i", { class: i < filled ? "on" : "" }),
          ),
        ),
        h("strong", { text: unknown ? "Low" : `${pct}%` }),
      ),
    );

    const body = h(
      "div",
      { class: "card-body" },
      h("p", { class: "answer", text: d.answer }),
      d.summary ? h("p", { class: "summary", text: d.summary }) : null,
      d.recommendations && d.recommendations.length
        ? h(
            "div",
            {},
            h("h3", { class: "block-title", text: "Suggested next steps" }),
            h(
              "ul",
              { class: "steps" },
              d.recommendations.map((r) =>
                h("li", {}, icon("check"), h("span", { text: r })),
              ),
            ),
          )
        : null,
      d.findings && d.findings.length
        ? h(
            "div",
            {},
            h("h3", { class: "block-title", text: "What I checked" }),
            h(
              "div",
              { class: "findings" },
              d.findings.map((f) =>
                h(
                  "div",
                  { class: `finding ${f.status === "error" ? "error" : ""}` },
                  h("span", { class: "dot" }),
                  h("span", { class: "tool", text: f.tool }),
                  h("span", { class: "detail", text: f.details }),
                ),
              ),
            ),
          )
        : null,
      d.pending_action ? renderApproval(entry) : null,
    );

    return h(
      "div",
      { class: "row-assistant entry" },
      h("article", { class: "card" }, head, body),
      h(
        "div",
        { class: "meta" },
        entry.at ? h("span", { text: formatTime(entry.at) }) : null,
        entry.traceId ? traceButton(entry.traceId) : null,
      ),
    );
  }

  function renderApproval(entry) {
    const action = entry.data.pending_action;
    const p = entry.pending || { status: "open" };

    if (p.status === "executed" || p.status === "failed") {
      return h(
        "div",
        { class: `outcome ${p.status}` },
        icon(p.status === "executed" ? "check" : "alert"),
        h("span", { text: p.message }),
      );
    }
    if (p.status === "denied") {
      return h(
        "div",
        { class: "outcome" },
        icon("check"),
        h("span", { text: "Not run. Nothing was changed." }),
      );
    }
    const working = p.status === "working";
    return h(
      "div",
      { class: "approval", role: "group", "aria-label": "Approval needed" },
      h("h3", { text: "Approval needed" }),
      h("p", { text: action.description }),
      h("p", { text: "Nothing runs until you approve." }),
      p.error
        ? h("p", { class: "approval-error", role: "alert", text: p.error })
        : null,
      h(
        "div",
        { class: "approval-actions" },
        h(
          "button",
          {
            class: "btn btn-primary",
            type: "button",
            disabled: working,
            onClick: () => decide(entry, true),
          },
          working ? "Running…" : "Approve and run",
        ),
        h(
          "button",
          {
            class: "btn btn-secondary",
            type: "button",
            disabled: working,
            onClick: () => decide(entry, false),
          },
          "Don't run",
        ),
      ),
    );
  }

  function renderNotice(entry) {
    return h(
      "div",
      { class: "row-assistant entry" },
      h(
        "div",
        {
          class: `notice ${entry.tone || ""}`,
          role: entry.tone === "error" ? "alert" : null,
        },
        icon(entry.tone === "error" ? "alert" : "check"),
        h("p", { text: entry.text }),
        entry.retryText
          ? h(
              "button",
              {
                class: "btn btn-secondary",
                type: "button",
                onClick: () => retry(entry),
              },
              "Try again",
            )
          : null,
      ),
    );
  }

  function traceButton(traceId) {
    const short = traceId.slice(0, 8);
    const btn = h(
      "button",
      {
        class: "trace-btn",
        type: "button",
        title: "Copy trace id to look this request up in Jaeger",
        "aria-label": `Copy trace id ${traceId}`,
      },
      `trace ${short}`,
    );
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(traceId);
        btn.textContent = "copied";
      } catch {
        btn.textContent = traceId;
      }
      setTimeout(() => (btn.textContent = `trace ${short}`), 1600);
    });
    return btn;
  }

  const formatTime = (date) =>
    date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  // ---------- network ----------
  async function postJSON(url, body) {
    let res;
    try {
      res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch {
      throw friendlyError(0);
    }
    const traceId = res.headers.get("X-Trace-Id");
    if (!res.ok) {
      let detail = "";
      try {
        const payload = await res.json();
        detail = typeof payload.detail === "string" ? payload.detail : "";
      } catch {
        detail = "";
      }
      throw friendlyError(res.status, traceId, detail);
    }
    let data;
    try {
      data = await res.json();
    } catch {
      throw friendlyError(502, traceId, "The API returned an unreadable response.");
    }
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      throw friendlyError(502, traceId, "The API response had an unexpected format.");
    }
    return { data, traceId: traceId || data.trace_id || null };
  }
  function friendlyError(status, traceId, detail = "") {
    const messages = {
      0: "Can't reach the assistant API. Open http://127.0.0.1:8000/ and keep the backend running.",
      404: "That request is no longer available. Start again from a new message.",
      422: "That message couldn't be processed. Shorten it or remove unusual characters.",
      502: "The AI provider isn't responding right now. Try again in a moment.",
    };
    const safeDetail = status < 500 && detail ? ` ${detail.slice(0, 240)}` : "";
    const fallback = status >= 500
      ? `The assistant API returned HTTP ${status}. Check the backend log and try again.`
      : `The request failed with HTTP ${status}.`;
    const message = messages[status] || fallback;
    const err = new Error(`${message}${safeDetail}${traceId ? ` Request ID: ${traceId}` : ""}`);
    err.traceId = traceId || null;
    return err;
  }

  // ---------- actions ----------
  async function submit() {
    const text = input.value.trim();
    const ticket = activeTicket();
    if (!text || !ticket || state.busy.has(ticket.id)) return;
    input.value = "";
    autosize();
    syncComposer();
    await sendMessage(ticket, text);
  }

  async function sendMessage(ticket, text) {
    const entries = transcript(ticket.id);
    entries.push({
      id: state.nextEntryId++,
      type: "user",
      text,
      at: new Date(),
    });
    if (ticket.title === "New ticket") {
      ticket.title =
        text.length > 42 ? text.slice(0, 40).trimEnd() + "…" : text;
      saveTickets();
      renderRail();
      renderHeader();
    }
    state.busy.add(ticket.id);
    if (state.activeId === ticket.id) renderThread();
    syncComposer();

    try {
      const { data, traceId } = await postJSON(API.chat, {
        ticket_id: ticket.id,
        message: text,
      });
      entries.push({
        id: state.nextEntryId++,
        type: "assessment",
        data,
        traceId,
        at: new Date(),
        pending: { status: "open" },
      });
    } catch (err) {
      entries.push({
        id: state.nextEntryId++,
        type: "notice",
        tone: "error",
        text: err.message,
        traceId: err.traceId || null,
        retryText: text,
      });
    } finally {
      state.busy.delete(ticket.id);
      if (state.activeId === ticket.id) renderThread("lastTop");
      syncComposer();
    }
  }

  async function retry(entry) {
    const ticket = activeTicket();
    const entries = transcript(ticket.id);
    entries.splice(entries.indexOf(entry), 1);
    // drop the failed user message too, sendMessage adds it again
    const last = entries[entries.length - 1];
    if (last && last.type === "user" && last.text === entry.retryText)
      entries.pop();
    await sendMessage(ticket, entry.retryText);
  }

  async function decide(entry, approved) {
    const ticketId = state.activeId;
    entry.pending = { status: "working" };
    renderThread("keep");
    try {
      const { data } = await postJSON(
        API.confirm(entry.data.pending_action.action_id),
        { approved },
      );
      entry.pending = { status: data.status, message: data.message };
    } catch (err) {
      entry.pending = { status: "open", error: err.message };
    }
    if (state.activeId === ticketId) renderThread("keep");
  }

  async function clearConversation() {
    const ticket = activeTicket();
    if (!ticket) return;
    if (
      !window.confirm(
        "Clear this conversation? The assistant will also forget this ticket.",
      )
    )
      return;
    try {
      const res = await fetch(API.ticket(ticket.id), { method: "DELETE" });
      if (!res.ok) throw new Error();
      state.transcripts.set(ticket.id, []);
    } catch {
      transcript(ticket.id).push({
        id: state.nextEntryId++,
        type: "notice",
        tone: "error",
        text: "The conversation couldn't be cleared on the server. Try again.",
      });
    }
    renderThread();
  }

  // ---------- composer ----------
  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
  }
  function syncComposer() {
    const busy = state.busy.has(state.activeId);
    sendBtn.disabled = busy || !input.value.trim();
    const remaining = input.maxLength - input.value.length;
    counter.hidden = remaining > 500;
    counter.textContent = remaining <= 500 ? `${remaining} left` : "";
  }

  input.addEventListener("input", () => {
    autosize();
    syncComposer();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submit();
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit();
  });
  $("new-ticket").addEventListener("click", createTicket);
  $("clear-btn").addEventListener("click", clearConversation);

  // ---------- mobile rail ----------
  function openRail() {
    shell.classList.add("rail-open");
    $("scrim").hidden = false;
    $("menu-btn").setAttribute("aria-expanded", "true");
  }
  function closeRail() {
    shell.classList.remove("rail-open");
    $("scrim").hidden = true;
    $("menu-btn").setAttribute("aria-expanded", "false");
  }
  $("menu-btn").addEventListener("click", openRail);
  $("scrim").addEventListener("click", closeRail);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeRail();
  });

  // ---------- connection indicator ----------
  async function checkHealth() {
    const dot = $("health-dot"),
      text = $("health-text");
    try {
      const res = await fetch(API.health, { cache: "no-store" });
      if (!res.ok) throw new Error();
      dot.dataset.state = "up";
      text.textContent = "Connected";
    } catch {
      dot.dataset.state = "down";
      text.textContent = "Server unreachable";
    }
  }

  // ---------- start ----------
  selectTicket(state.tickets[0].id);
  checkHealth();
  setInterval(checkHealth, 30000);
})();
