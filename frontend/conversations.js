/* Own the signed-in conversation workspace, rendering, and chat actions. */
(function startPrivateConversations(window, document, api) {
  "use strict";

  const PAGE_SIZE = 50;
  const state = {
    user: null,
    conversations: [],
    activeId: null,
    messages: [],
    olderOffset: 0,
    hasOlder: false,
    requestVersion: 0,
    busy: false,
    assessment: null,
  };
  const elements = {
    root: document.getElementById("app-root"),
    email: document.getElementById("account-email"),
    list: document.getElementById("conversation-list"),
    listEmpty: document.getElementById("conversation-empty"),
    title: document.getElementById("conversation-title"),
    id: document.getElementById("conversation-id"),
    thread: document.getElementById("message-thread"),
    log: document.getElementById("message-log"),
    error: document.getElementById("app-error"),
    input: document.getElementById("message-input"),
    composer: document.getElementById("composer"),
    send: document.getElementById("send-message"),
    create: document.getElementById("new-conversation"),
    rename: document.getElementById("rename-conversation"),
    remove: document.getElementById("delete-conversation"),
  };

  /** Create a text-only element so account data is never interpreted as markup. */
  function element(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /** Display an operation error and return expired sessions to authentication. */
  function showError(error) {
    if (error.status === 401) {
      window.PrivateAuth.showSignIn();
      return;
    }
    elements.error.textContent = error.message || "The request failed. Try again.";
    elements.error.hidden = false;
  }

  /** Clear an old message before beginning another operation. */
  function clearError() {
    elements.error.textContent = "";
    elements.error.hidden = true;
    elements.error.classList.add("error");
  }

  /** Find the active conversation from the server-confirmed page. */
  function activeConversation() {
    return state.conversations.find((item) => item.conversation_id === state.activeId) || null;
  }

  /** Render the sidebar from the current user's server-owned conversation page. */
  function renderConversationList() {
    elements.list.replaceChildren();
    elements.listEmpty.hidden = state.conversations.length > 0;
    state.conversations.forEach((conversation) => {
      const item = element("li", "ticket-item-wrap");
      const button = element("button", "ticket-item");
      button.type = "button";
      button.setAttribute("aria-current", String(conversation.conversation_id === state.activeId));
      button.append(element("span", "ticket-title", conversation.title));
      button.append(element("span", "ticket-sub", formatDate(conversation.updated_at)));
      button.addEventListener("click", () => openConversation(conversation.conversation_id));
      item.append(button);
      elements.list.append(item);
    });
  }

  /** Format a backend timestamp for display while retaining an invalid value safely. */
  function formatDate(value) {
    const timestamp = new Date(String(value).replace(" ", "T") + "Z");
    return Number.isNaN(timestamp.getTime()) ? String(value) : timestamp.toLocaleString();
  }

  /** Build the diagnostic details and trace reference returned for the latest turn. */
  function renderAssessment(assessment) {
    if (!assessment) return;
    const card = element("section", "card entry");
    const head = element("div", "card-head");
    head.append(element("span", `chip ${categoryClass(assessment.category)}`, categoryLabel(assessment.category)));
    if (assessment.confidence !== undefined) {
      head.append(element("span", "meter", `Confidence ${Math.round(assessment.confidence * 100)}%`));
    }
    const body = element("div", "card-body");
    if (assessment.summary) body.append(element("p", "summary", assessment.summary));
    if (Array.isArray(assessment.findings) && assessment.findings.length) {
      const section = element("section", "");
      section.append(element("h2", "block-title", "Diagnostic findings"));
      const findings = element("ul", "findings");
      assessment.findings.forEach((finding) => {
        const row = element("li", `finding ${finding.status === "error" ? "error" : ""}`);
        row.append(element("span", "dot"));
        row.append(element("span", "tool", finding.tool));
        row.append(element("span", "detail", finding.details));
        findings.append(row);
      });
      section.append(findings);
      body.append(section);
    }
    if (Array.isArray(assessment.recommendations) && assessment.recommendations.length) {
      const section = element("section", "");
      section.append(element("h2", "block-title", "Recommended next steps"));
      const list = element("ol", "steps");
      assessment.recommendations.forEach((item) => list.append(element("li", "", item)));
      section.append(list);
      body.append(section);
    }
    card.append(head, body);
    elements.thread.append(card);
    if (assessment.pending_action) renderPendingAction(assessment.pending_action);
    if (assessment.trace_id) {
      const trace = element("p", "meta", `Trace ${assessment.trace_id}`);
      elements.thread.append(trace);
    }
  }

  /** Map known category identifiers to stable UI labels and styles. */
  function categoryClass(category) {
    return ({ network_issue: "network", software_issue: "software", hardware_issue: "hardware" })[category]
      || "unknown";
  }

  /** Return a readable label for a backend classification identifier. */
  function categoryLabel(category) {
    return ({
      network_issue: "Network issue",
      software_issue: "Software issue",
      hardware_issue: "Hardware issue",
      unknown: "Needs review",
    })[category] || "Needs review";
  }

  /** Show the existing server-issued pending action and require an explicit decision. */
  function renderPendingAction(action) {
    const card = element("section", "approval");
    card.append(element("h3", "", "Approval required"));
    card.append(element("p", "", action.description));
    const actions = element("div", "approval-actions");
    const approve = element("button", "btn btn-primary", "Approve action");
    const deny = element("button", "btn btn-secondary", "Deny");
    approve.type = deny.type = "button";
    const result = element("p", "approval-error");
    approve.addEventListener("click", () => decideAction(action, true, approve, deny, result));
    deny.addEventListener("click", () => decideAction(action, false, approve, deny, result));
    actions.append(approve, deny);
    card.append(actions, result);
    elements.thread.append(card);
  }

  /** Submit the user's explicit `approved` decision for a pending `action`. */
  async function decideAction(action, approved, approve, deny, result) {
    const conversationId = state.activeId;
    const version = state.requestVersion;
    approve.disabled = deny.disabled = true;
    try {
      const response = await api.confirmAction(conversationId, action.action_id, approved);
      if (conversationId !== state.activeId) return;
      result.textContent = response.message;
      result.className = "approval-error";
      approve.remove();
      deny.remove();
    } catch (error) {
      if (version === state.requestVersion) {
        result.textContent = error.message || "The action decision could not be saved.";
        approve.disabled = deny.disabled = false;
      }
    }
  }

  /** Add controls to an owned message; editing/deleting affects later replies by API contract. */
  function renderMessageActions(message, row) {
    const actions = element("div", "message-actions");
    const copy = element("button", "trace-btn", "Copy");
    copy.type = "button";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(message.content);
        copy.textContent = "Copied";
      } catch {
        copy.textContent = "Copy unavailable";
      }
    });
    actions.append(copy);
    if (message.role === "user") {
      const edit = element("button", "trace-btn", "Edit");
      edit.type = "button";
      edit.addEventListener("click", () => editMessage(message));
      const remove = element("button", "trace-btn", "Delete");
      remove.type = "button";
      remove.addEventListener("click", () => deleteMessage(message));
      actions.append(edit, remove);
    }
    row.append(actions);
  }

  /** Render every loaded message and any non-persisted diagnostics from the latest response. */
  function renderMessages() {
    elements.thread.replaceChildren();
    if (state.hasOlder) {
      const older = element("button", "btn btn-secondary", "Load older messages");
      older.type = "button";
      older.addEventListener("click", loadOlderMessages);
      elements.thread.append(older);
    }
    if (!state.messages.length) {
      const empty = element("section", "empty");
      empty.append(element("p", "empty-kicker", "SECURE IT SUPPORT"));
      empty.append(element("h2", "", "What can we help you solve?"));
      empty.append(element("p", "", "Describe the issue and Nexus will assess it, show findings, and suggest safe next steps."));
      elements.thread.append(empty);
    }
    state.messages.forEach((message, index) => {
      const row = element("article", `entry ${message.role === "user" ? "row-user" : "row-assistant"}`);
      const bubble = element("p", message.role === "user" ? "bubble-user" : "plain", message.content);
      row.append(bubble);
      row.append(element("time", "stamp", formatDate(message.created_at)));
      renderMessageActions(message, row);
      elements.thread.append(row);
      if (index === state.messages.length - 1 && message.role === "assistant") {
        renderAssessment(state.assessment);
      }
    });
    elements.log.scrollTop = elements.log.scrollHeight;
  }

  /** Load the next older page for the currently selected conversation. */
  async function loadOlderMessages() {
    const conversationId = state.activeId;
    const version = state.requestVersion;
    const button = elements.thread.querySelector("button");
    if (button) button.disabled = true;
    try {
      const older = await api.messages(conversationId, PAGE_SIZE, state.olderOffset);
      if (version !== state.requestVersion) return;
      state.messages = older.concat(state.messages);
      state.olderOffset += older.length;
      state.hasOlder = older.length === PAGE_SIZE;
      renderMessages();
    } catch (error) {
      if (version === state.requestVersion) showError(error);
    }
  }

  /** Edit one user message; the backend removes stale following replies before reload. */
  async function editMessage(message) {
    const conversationId = state.activeId;
    const version = state.requestVersion;
    const content = window.prompt("Edit your message. Later replies will be removed.", message.content);
    if (content === null || !content.trim() || content === message.content) return;
    try {
      await api.editMessage(conversationId, message.message_id, content.trim());
      if (version !== state.requestVersion) return;
      state.assessment = null;
      await reloadMessages();
      showSuccess("Message updated. Send it again to get a fresh answer.");
    } catch (error) {
      if (version === state.requestVersion) showError(error);
    }
  }

  /** Delete one user message and later replies after explicit confirmation. */
  async function deleteMessage(message) {
    if (!window.confirm("Delete this message and all later replies?")) return;
    const conversationId = state.activeId;
    const version = state.requestVersion;
    try {
      await api.deleteMessage(conversationId, message.message_id);
      if (version !== state.requestVersion) return;
      state.assessment = null;
      await reloadMessages();
      showSuccess("Message and later replies deleted.");
    } catch (error) {
      if (version === state.requestVersion) showError(error);
    }
  }

  /** Announce a successful change in the existing accessible status area. */
  function showSuccess(message) {
    elements.error.textContent = message;
    elements.error.classList.remove("error");
    elements.error.hidden = false;
  }

  /** Load the first bounded page of history for `conversationId`. */
  async function openConversation(conversationId) {
    state.activeId = conversationId;
    state.assessment = null;
    state.messages = [];
    state.olderOffset = 0;
    state.requestVersion += 1;
    clearError();
    renderConversationList();
    updateHeader();
    renderMessages();
    await reloadMessages();
  }

  /** Fetch initial history and ignore responses for a conversation no longer selected. */
  async function reloadMessages() {
    const conversationId = state.activeId;
    const version = state.requestVersion;
    if (!conversationId) return;
    try {
      const messages = await api.messages(conversationId, PAGE_SIZE, 0);
      if (version !== state.requestVersion) return;
      state.messages = messages;
      state.olderOffset = messages.length;
      state.hasOlder = messages.length === PAGE_SIZE;
      renderMessages();
    } catch (error) {
      if (version === state.requestVersion) showError(error);
    }
  }

  /** Synchronize title, identifier, and available controls with the selected conversation. */
  function updateHeader() {
    const conversation = activeConversation();
    elements.title.textContent = conversation ? conversation.title : "Choose a conversation";
    elements.id.textContent = conversation ? conversation.conversation_id.slice(0, 8) : "";
    elements.rename.disabled = !conversation;
    elements.remove.disabled = !conversation;
    elements.input.disabled = !conversation || state.busy;
    elements.send.disabled = !conversation || state.busy;
  }

  /** Fetch the current user's bounded conversation page and choose a safe initial view. */
  async function loadConversations(preferredId = null) {
    const version = state.requestVersion;
    const result = await api.conversations(PAGE_SIZE, 0);
    if (version !== state.requestVersion) return;
    state.conversations = result;
    const selectedId = preferredId || state.activeId;
    state.activeId = result.some((item) => item.conversation_id === selectedId)
      ? selectedId
      : (result[0] ? result[0].conversation_id : null);
    renderConversationList();
    updateHeader();
    if (state.activeId) await openConversation(state.activeId);
    else {
      state.messages = [];
      renderMessages();
    }
  }

  /** Create and open a private conversation owned by the signed-in account. */
  async function createConversation() {
    elements.create.disabled = true;
    clearError();
    try {
      const conversation = await api.createConversation("New conversation");
      state.conversations.unshift(conversation);
      await loadConversations(conversation.conversation_id);
    } catch (error) {
      showError(error);
    } finally {
      elements.create.disabled = false;
    }
  }

  /** Rename the active conversation after obtaining a non-empty title from the user. */
  async function renameConversation() {
    const conversation = activeConversation();
    if (!conversation) return;
    const conversationId = state.activeId;
    const title = window.prompt("Conversation name", conversation.title);
    if (title === null || !title.trim()) return;
    elements.rename.disabled = true;
    try {
      const updated = await api.renameConversation(conversationId, title.trim());
      if (conversationId !== state.activeId) return;
      state.conversations = state.conversations.map((item) =>
        item.conversation_id === conversationId ? updated : item);
      renderConversationList();
      updateHeader();
      showSuccess("Conversation renamed.");
    } catch (error) {
      showError(error);
    } finally {
      elements.rename.disabled = false;
    }
  }

  /** Delete the active conversation only after a clear confirmation. */
  async function deleteConversation() {
    const conversation = activeConversation();
    if (!conversation || !window.confirm(`Delete “${conversation.title}” and its messages?`)) return;
    const conversationId = state.activeId;
    try {
      await api.deleteConversation(conversationId);
      if (conversationId !== state.activeId) return;
      state.conversations = state.conversations.filter((item) => item.conversation_id !== conversationId);
      state.activeId = null;
      state.assessment = null;
      await loadConversations();
      showSuccess("Conversation deleted.");
    } catch (error) {
      showError(error);
    }
  }

  /** Send a message, store server-returned assessment details, and reload persisted history. */
  async function sendMessage(event) {
    event.preventDefault();
    const message = elements.input.value.trim();
    if (!message || state.busy) return;
    const conversationId = state.activeId;
    const version = state.requestVersion;
    state.busy = true;
    const originalValue = elements.input.value;
    elements.input.value = "";
    clearError();
    updateHeader();
    try {
      state.assessment = await api.chat(conversationId, message);
      if (version !== state.requestVersion) return;
      await reloadMessages();
      if (version !== state.requestVersion) return;
      state.conversations = await api.conversations(PAGE_SIZE, 0);
      if (version !== state.requestVersion) return;
      renderConversationList();
      updateHeader();
    } catch (error) {
      if (version === state.requestVersion) {
        elements.input.value = originalValue;
        showError(error);
      }
    } finally {
      if (version === state.requestVersion) {
        state.busy = false;
        updateHeader();
      }
    }
  }

  /** Attach workspace events once and expose the minimal lifecycle used by authentication. */
  function initialize() {
    elements.create.addEventListener("click", createConversation);
    elements.rename.addEventListener("click", renameConversation);
    elements.remove.addEventListener("click", deleteConversation);
    elements.composer.addEventListener("submit", sendMessage);
    window.PrivateConversations = Object.freeze({
      show: async (user) => {
        state.requestVersion += 1;
        state.user = user;
        state.activeId = null;
        state.conversations = [];
        state.messages = [];
        state.assessment = null;
        elements.root.hidden = false;
        elements.email.textContent = user.email;
        elements.list.replaceChildren();
        elements.thread.replaceChildren();
        updateHeader();
        clearError();
        try {
          await loadConversations();
        } catch (error) {
          showError(error);
        }
      },
      clear: () => {
        state.user = null;
        state.conversations = [];
        state.activeId = null;
        state.messages = [];
        state.assessment = null;
        state.busy = false;
        state.requestVersion += 1;
        elements.email.textContent = "";
        elements.list.replaceChildren();
        elements.thread.replaceChildren();
      },
    });
  }

  initialize();
})(window, document, window.SupportApi);
