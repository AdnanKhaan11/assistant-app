/* Add the server-authorized administrator dashboard to the signed-in workspace. */
(function startAdminDashboard(window, document, api) {
  "use strict";

  const PAGE_SIZE = 20;
  const appRoot = document.getElementById("app-root");
  const navButton = document.getElementById("admin-nav");
  const adminRoot = document.getElementById("admin-root");
  const normalViewIds = ["conversation-topbar"];
  const normalView = normalViewIds.map((id) => document.getElementById(id));
  const messages = document.getElementById("message-log");
  const appError = document.getElementById("app-error");
  const composer = document.getElementById("composer");
  const overviewTab = document.getElementById("admin-overview-tab");
  const usersTab = document.getElementById("admin-users-tab");
  const auditTab = document.getElementById("admin-audit-tab");
  const overviewPanel = document.getElementById("admin-overview");
  const usersPanel = document.getElementById("admin-users");
  const auditPanel = document.getElementById("admin-audit");
  const healthStatus = document.getElementById("admin-health");
  const usersStatus = document.getElementById("admin-users-status");
  const userList = document.getElementById("admin-user-list");
  const previousPage = document.getElementById("admin-users-previous");
  const nextPage = document.getElementById("admin-users-next");
  const pageLabel = document.getElementById("admin-users-page");
  const auditStatus = document.getElementById("admin-audit-status");
  const auditList = document.getElementById("admin-audit-list");
  const auditPrevious = document.getElementById("admin-audit-previous");
  const auditNext = document.getElementById("admin-audit-next");
  const auditPageLabel = document.getElementById("admin-audit-page");
  const auditRefresh = document.getElementById("admin-audit-refresh");
  const state = {
    isAdmin: false,
    userId: null,
    offset: 0,
    auditOffset: 0,
    generation: 0,
    userRequest: 0,
    auditRequest: 0,
    priorHidden: new Map(),
  };
  const visibleViews = [...normalView, messages, appError, composer];

  /** Create a node and assign dynamic text without parsing HTML. */
  function makeElement(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /** Display API errors in the dashboard without exposing server internals. */
  function showError(error, status = usersStatus) {
    status.textContent = error.message || "The administrator request failed.";
    if (error.status === 401) {
      window.PrivateAuth.showSignIn();
    }
  }

  /** Select either the overview or the user-management section. */
  function selectSection(section) {
    const isUsers = section === "users";
    const isAudit = section === "audit";
    overviewPanel.hidden = isUsers || isAudit;
    usersPanel.hidden = !isUsers;
    auditPanel.hidden = !isAudit;
    overviewTab.setAttribute("aria-current", String(!isUsers && !isAudit));
    usersTab.setAttribute("aria-current", String(isUsers));
    auditTab.setAttribute("aria-current", String(isAudit));
    if (isUsers) loadUsers();
    if (isAudit) loadAudit();
  }

  /** Render one audit record using only text nodes and the backend's safe fields. */
  function renderAuditEvent(event) {
    const row = makeElement("li", "card");
    const body = makeElement("div", "card-body");
    body.append(makeElement("strong", "", event.action));
    body.append(makeElement("p", "summary", `By ${event.actor_email || "Deleted account"} · For ${event.target_email || "Deleted account"}`));
    body.append(makeElement("time", "stamp", event.created_at));
    const details = Object.entries(event.details || {})
      .map(([key, value]) => `${key}: ${String(value)}`)
      .join(" · ");
    if (details) body.append(makeElement("p", "", details));
    row.append(body);
    return row;
  }

  /** Load a bounded audit page and ignore responses from a superseded request or session. */
  async function loadAudit() {
    const generation = state.generation;
    const requestId = ++state.auditRequest;
    auditStatus.textContent = "Loading audit events...";
    auditList.replaceChildren();
    auditPrevious.disabled = state.auditOffset === 0;
    auditNext.disabled = true;
    try {
      const events = await api.adminAudit(PAGE_SIZE, state.auditOffset);
      if (generation !== state.generation || requestId !== state.auditRequest || !state.isAdmin) return;
      events.forEach((event) => auditList.append(renderAuditEvent(event)));
      auditStatus.textContent = events.length ? `${events.length} events on this page.` : "No audit events on this page.";
      auditPageLabel.textContent = `Page ${Math.floor(state.auditOffset / PAGE_SIZE) + 1}`;
      auditPrevious.disabled = state.auditOffset === 0;
      auditNext.disabled = events.length < PAGE_SIZE;
    } catch (error) {
      if (generation === state.generation && requestId === state.auditRequest) showError(error, auditStatus);
    }
  }

  /** Refresh the liveness status using the existing public health endpoint. */
  async function loadOverview() {
    healthStatus.textContent = "Checking service status...";
    try {
      const result = await api.health();
      healthStatus.textContent = result.status === "ok" ? "Backend service is available." : "Backend status is unknown.";
    } catch (error) {
      healthStatus.textContent = error.message || "Backend status could not be checked.";
    }
  }

  /** Create safe controls for one server-returned account. */
  function renderUser(user) {
    const row = makeElement("li", "card");
    const body = makeElement("div", "card-body");
    const email = makeElement("strong", "", user.email);
    const metadata = makeElement("p", "summary", `${user.role} · ${user.is_active ? "Active" : "Disabled"} · Created ${user.created_at}`);
    const roleLabel = makeElement("label", "", "Role");
    const role = makeElement("select", "btn-quiet");
    role.id = `admin-role-${user.user_id}`;
    roleLabel.setAttribute("for", role.id);
    ["user", "admin"].forEach((value) => {
      const option = makeElement("option", "", value === "admin" ? "Administrator" : "User");
      option.value = value;
      option.selected = value === user.role;
      role.append(option);
    });
    const activeLabel = makeElement("label", "", "Active account");
    const active = makeElement("input", "");
    active.id = `admin-active-${user.user_id}`;
    active.type = "checkbox";
    active.checked = user.is_active;
    activeLabel.setAttribute("for", active.id);
    const save = makeElement("button", "btn btn-secondary", "Save account");
    const feedback = makeElement("p", "approval-error");
    const isCurrentUser = user.user_id === state.userId;
    role.disabled = active.disabled = save.disabled = isCurrentUser;
    if (isCurrentUser) feedback.textContent = "Your own account cannot be changed here.";
    save.addEventListener("click", () => saveUser(user.user_id, role.value, active.checked, save, feedback));
    body.append(email, metadata, roleLabel, role, activeLabel, active, save, feedback);
    row.append(body);
    return row;
  }

  /** Load a bounded user page from the administrator-only API. */
  async function loadUsers() {
    const generation = state.generation;
    const requestId = ++state.userRequest;
    usersStatus.textContent = "Loading accounts...";
    userList.replaceChildren();
    previousPage.disabled = state.offset === 0;
    try {
      const users = await api.adminUsers(PAGE_SIZE, state.offset);
      if (generation !== state.generation || requestId !== state.userRequest || !state.isAdmin) return;
      users.forEach((user) => userList.append(renderUser(user)));
      usersStatus.textContent = users.length ? `${users.length} accounts on this page.` : "No accounts on this page.";
      pageLabel.textContent = `Page ${Math.floor(state.offset / PAGE_SIZE) + 1}`;
      previousPage.disabled = state.offset === 0;
      nextPage.disabled = users.length < PAGE_SIZE;
    } catch (error) {
      if (generation === state.generation && requestId === state.userRequest) showError(error);
    }
  }

  /** Save a role and active-state change only after the backend confirms it. */
  async function saveUser(userId, role, isActive, button, feedback) {
    button.disabled = true;
    feedback.textContent = "Saving...";
    try {
      await api.updateAdminUser(userId, role, isActive);
      feedback.textContent = "Account updated.";
      await loadUsers();
    } catch (error) {
      feedback.textContent = error.message || "The account could not be updated.";
      if (error.status === 401) showError(error);
    } finally {
      button.disabled = userId === state.userId;
    }
  }

  /** Open the dashboard and hide conversation controls while preserving chat state. */
  function openDashboard() {
    if (!state.isAdmin) return;
    state.priorHidden = new Map(visibleViews.map((node) => [node, node.hidden]));
    adminRoot.hidden = false;
    visibleViews.forEach((node) => { node.hidden = true; });
    loadOverview();
    selectSection("overview");
  }

  /** Return to conversations and restore their existing controls. */
  function closeDashboard() {
    adminRoot.hidden = true;
    visibleViews.forEach((node) => {
      node.hidden = state.priorHidden.has(node) ? state.priorHidden.get(node) : false;
    });
    state.priorHidden.clear();
  }

  /** Check the server-confirmed role whenever authentication changes the protected root. */
  async function refreshRole() {
    const generation = ++state.generation;
    if (appRoot.hidden) {
      state.isAdmin = false;
      state.userId = null;
      navButton.hidden = true;
      closeDashboard();
      return;
    }
    try {
      const user = await api.currentUser();
      if (generation !== state.generation) return;
      state.isAdmin = user.role === "admin";
      state.userId = state.isAdmin ? user.user_id : null;
      navButton.hidden = !state.isAdmin;
      if (!state.isAdmin) closeDashboard();
    } catch {
      if (generation === state.generation) {
        state.isAdmin = false;
        state.userId = null;
        navButton.hidden = true;
        closeDashboard();
      }
    }
  }

  /** Attach admin navigation, account paging, and authentication-role observation. */
  function initialize() {
    navButton.addEventListener("click", openDashboard);
    document.getElementById("admin-back").addEventListener("click", closeDashboard);
    overviewTab.addEventListener("click", () => selectSection("overview"));
    usersTab.addEventListener("click", () => selectSection("users"));
    auditTab.addEventListener("click", () => selectSection("audit"));
    auditRefresh.addEventListener("click", loadAudit);
    previousPage.addEventListener("click", () => {
      state.offset = Math.max(0, state.offset - PAGE_SIZE);
      loadUsers();
    });
    nextPage.addEventListener("click", () => {
      state.offset += PAGE_SIZE;
      loadUsers();
    });
    auditPrevious.addEventListener("click", () => {
      state.auditOffset = Math.max(0, state.auditOffset - PAGE_SIZE);
      loadAudit();
    });
    auditNext.addEventListener("click", () => {
      state.auditOffset += PAGE_SIZE;
      loadAudit();
    });
    new window.MutationObserver(refreshRole).observe(appRoot, {
      attributes: true,
      attributeFilter: ["hidden"],
    });
    if (!appRoot.hidden) refreshRole();
  }

  initialize();
})(window, document, window.SupportApi);
