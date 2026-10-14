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
  const aiTab = document.getElementById("admin-ai-tab");
  const embeddingsTab = document.getElementById("admin-embeddings-tab");
  const memoryTab = document.getElementById("admin-memory-tab");
  const settingsTab = document.getElementById("admin-settings-tab");
  const auditTab = document.getElementById("admin-audit-tab");
  const overviewPanel = document.getElementById("admin-overview");
  const usersPanel = document.getElementById("admin-users");
  const aiPanel = document.getElementById("admin-ai");
  const embeddingsPanel = document.getElementById("admin-embeddings");
  const memoryPanel = document.getElementById("admin-memory");
  const settingsPanel = document.getElementById("admin-settings");
  const auditPanel = document.getElementById("admin-audit");
  const healthStatus = document.getElementById("admin-health");
  const systemStatus = document.getElementById("admin-system-status");
  const systemDetails = document.getElementById("admin-system-details");
  const systemRefresh = document.getElementById("admin-system-refresh");
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
  const aiActive = document.getElementById("admin-ai-active");
  const aiSaved = document.getElementById("admin-ai-saved");
  const aiProvider = document.getElementById("admin-ai-provider");
  const aiModel = document.getElementById("admin-ai-model");
  const aiValidate = document.getElementById("admin-ai-validate");
  const aiSave = document.getElementById("admin-ai-save");
  const aiStatus = document.getElementById("admin-ai-status");
  const embeddingsStatus = document.getElementById("admin-embeddings-status");
  const embeddingsDetails = document.getElementById("admin-embeddings-details");
  const embeddingsNote = document.getElementById("admin-embeddings-note");
  const memoryStatus = document.getElementById("admin-memory-status");
  const memoryDetails = document.getElementById("admin-memory-details");
  const settingsStatus = document.getElementById("admin-settings-status");
  const settingsDetails = document.getElementById("admin-settings-details");
  const state = {
    isAdmin: false,
    userId: null,
    offset: 0,
    auditOffset: 0,
    generation: 0,
    userRequest: 0,
    auditRequest: 0,
    aiRequest: 0,
    embeddingsRequest: 0,
    memoryRequest: 0,
    settingsRequest: 0,
    systemRequest: 0,
    aiChoiceRevision: 0,
    aiConfig: null,
    validatedAI: null,
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

  /** Show one administrator section and load its server-backed data when needed. */
  function selectSection(section) {
    const isUsers = section === "users";
    const isAudit = section === "audit";
    const isAI = section === "ai";
    const isEmbeddings = section === "embeddings";
    const isMemory = section === "memory";
    const isSettings = section === "settings";
    const isOverview = !isUsers && !isAudit && !isAI && !isEmbeddings && !isMemory && !isSettings;
    overviewPanel.hidden = !isOverview;
    usersPanel.hidden = !isUsers;
    aiPanel.hidden = !isAI;
    embeddingsPanel.hidden = !isEmbeddings;
    memoryPanel.hidden = !isMemory;
    settingsPanel.hidden = !isSettings;
    auditPanel.hidden = !isAudit;
    overviewTab.setAttribute("aria-current", String(isOverview));
    usersTab.setAttribute("aria-current", String(isUsers));
    aiTab.setAttribute("aria-current", String(isAI));
    embeddingsTab.setAttribute("aria-current", String(isEmbeddings));
    memoryTab.setAttribute("aria-current", String(isMemory));
    settingsTab.setAttribute("aria-current", String(isSettings));
    auditTab.setAttribute("aria-current", String(isAudit));
    if (isUsers) loadUsers();
    if (isOverview) loadSystemStatus();
    if (isAI) loadAIConfig();
    if (isEmbeddings) loadEmbeddingStatus();
    if (isMemory) loadMemoryStatus();
    if (isSettings) loadSettingsStatus();
    if (isAudit) loadAudit();
  }

  /** Render labeled status values as text-only rows in an admin panel. */
  function renderStatusRows(container, values) {
    container.replaceChildren();
    values.forEach(([label, value]) => {
      const row = makeElement("li", "finding");
      row.append(makeElement("span", "dot"), makeElement("strong", "tool", label));
      row.append(makeElement("span", "detail", String(value)));
      container.append(row);
    });
  }

  /** Load memory and retrieval status without claiming vector-store connectivity. */
  async function loadMemoryStatus() {
    const generation = state.generation;
    const requestId = ++state.memoryRequest;
    memoryStatus.textContent = "Loading memory and retrieval status...";
    memoryDetails.replaceChildren();
    try {
      const [system, embeddings] = await Promise.all([
        api.adminSystemStatus(),
        api.adminEmbeddingStatus(),
      ]);
      if (generation !== state.generation || requestId !== state.memoryRequest || !state.isAdmin) return;
      renderStatusRows(memoryDetails, [
        ["Short-term memory window", `${system.memory_window_turns} turns`],
        ["Semantic retrieval", system.semantic_retrieval_configured ? "Configured" : "Incomplete configuration"],
        ["Embedding provider and model", `${embeddings.provider} / ${embeddings.model}`],
        ["Vector store", `${embeddings.vector_store} · ${embeddings.is_vector_store_configured ? "Configured" : "Not configured"}`],
        ["Indexed vector dimension", embeddings.vector_dimension ?? "Not tracked"],
        ["Troubleshooting source manual", embeddings.is_manual_available ? "Available" : "Not found"],
      ]);
      memoryStatus.textContent = "Configuration loaded. Vector connectivity and index health are not checked.";
    } catch (error) {
      if (generation === state.generation && requestId === state.memoryRequest) showError(error, memoryStatus);
    }
  }

  /** Load supported application limits and features as read-only administrator settings. */
  async function loadSettingsStatus() {
    const generation = state.generation;
    const requestId = ++state.settingsRequest;
    settingsStatus.textContent = "Loading application settings...";
    settingsDetails.replaceChildren();
    try {
      const result = await api.adminSystemStatus();
      if (generation !== state.generation || requestId !== state.settingsRequest || !state.isAdmin) return;
      renderStatusRows(settingsDetails, [
        ["Maximum upload size", `${result.max_upload_bytes} bytes`],
        ["Attachments per message", result.max_attachments_per_message],
        ["Maximum stream duration", `${result.stream_max_seconds} seconds`],
        ["Vision support", result.vision_enabled ? "Enabled" : "Disabled"],
      ]);
      settingsStatus.textContent = "These supported application settings are currently read-only.";
    } catch (error) {
      if (generation === state.generation && requestId === state.settingsRequest) showError(error, settingsStatus);
    }
  }

  /** Load and safely display configured embedding components for an administrator. */
  async function loadEmbeddingStatus() {
    const generation = state.generation;
    const requestId = ++state.embeddingsRequest;
    embeddingsStatus.textContent = "Loading embedding configuration...";
    embeddingsDetails.replaceChildren();
    try {
      const result = await api.adminEmbeddingStatus();
      if (generation !== state.generation || requestId !== state.embeddingsRequest || !state.isAdmin) return;
      const values = [
        ["Embedding provider", `${result.provider} · ${result.is_provider_configured ? "Configured" : "Not configured"}`],
        ["Embedding model", result.model],
        ["Vector store", `${result.vector_store} · ${result.is_vector_store_configured ? "Configured" : "Not configured"}`],
        ["Troubleshooting manual", result.is_manual_available ? "Available" : "Not found"],
        ["Indexed vector dimension", result.vector_dimension ?? "Not tracked"],
      ];
      values.forEach(([label, value]) => {
        const row = makeElement("li", "finding");
        row.append(makeElement("span", "dot"), makeElement("strong", "tool", label));
        row.append(makeElement("span", "detail", String(value)));
        embeddingsDetails.append(row);
      });
      embeddingsNote.textContent = result.note;
      embeddingsStatus.textContent = "Configuration status loaded. Connectivity and index health are not checked here.";
    } catch (error) {
      if (generation === state.generation && requestId === state.embeddingsRequest) {
        showError(error, embeddingsStatus);
      }
    }
  }

  /** Load safe system readiness fields and ignore stale dashboard responses. */
  async function loadSystemStatus() {
    const generation = state.generation;
    const requestId = ++state.systemRequest;
    systemStatus.textContent = "Loading runtime system status...";
    systemDetails.replaceChildren();
    systemRefresh.disabled = true;
    try {
      const result = await api.adminSystemStatus();
      if (generation !== state.generation || requestId !== state.systemRequest || !state.isAdmin) return;
      const values = [
        ["Database", result.database_status === "available" ? "Available" : "Unavailable"],
        ["Active chat model", `${result.active_chat_provider} / ${result.active_chat_model}`],
        ["Chat provider configuration", result.is_chat_provider_configured ? "Configured" : "Missing credentials"],
        ["Upload limit", `${result.max_upload_bytes} bytes · ${result.max_attachments_per_message} attachments per message`],
        ["Stream time limit", `${result.stream_max_seconds} seconds`],
        ["Memory window", `${result.memory_window_turns} turns`],
        ["Vision support", result.vision_enabled ? "Enabled" : "Disabled"],
        ["Semantic retrieval configuration", result.semantic_retrieval_configured ? "Configured" : "Incomplete"],
      ];
      values.forEach(([label, value]) => {
        const row = makeElement("li", "finding");
        row.append(makeElement("span", "dot"), makeElement("strong", "tool", label));
        row.append(makeElement("span", "detail", String(value)));
        systemDetails.append(row);
      });
      systemStatus.textContent = result.note;
    } catch (error) {
      if (generation === state.generation && requestId === state.systemRequest) showError(error, systemStatus);
    } finally {
      if (generation === state.generation && requestId === state.systemRequest) {
        systemRefresh.disabled = !state.isAdmin;
      }
    }
  }

  /** Load supported model choices and show the active and saved values separately. */
  async function loadAIConfig() {
    const generation = state.generation;
    const requestId = ++state.aiRequest;
    aiStatus.textContent = "Loading model configuration...";
    aiValidate.disabled = true;
    aiSave.disabled = true;
    aiProvider.disabled = true;
    aiModel.disabled = true;
    try {
      const config = await api.adminAIConfig();
      if (generation !== state.generation || requestId !== state.aiRequest || !state.isAdmin) return;
      state.aiConfig = config;
      state.validatedAI = null;
      aiActive.textContent = `Active: ${config.active_provider} / ${config.active_model}`;
      aiSaved.textContent = config.saved
        ? `Saved: ${config.saved.provider} / ${config.saved.model} (${config.saved.updated_at})`
        : "No saved model selection.";
      renderAIChoices(config);
      aiStatus.textContent = "Validate a supported selection before saving.";
      aiProvider.disabled = false;
      aiModel.disabled = false;
      aiValidate.disabled = !aiProvider.value || !aiModel.value;
    } catch (error) {
      if (generation === state.generation && requestId === state.aiRequest) {
        showError(error, aiStatus);
        aiProvider.disabled = false;
        aiModel.disabled = false;
      }
    }
  }

  /** Populate provider/model selects from the backend-owned supported choice list. */
  function renderAIChoices(config) {
    aiProvider.replaceChildren();
    config.providers.forEach((provider) => {
      const option = makeElement("option", "", provider.provider);
      option.value = provider.provider;
      aiProvider.append(option);
    });
    const preferred = config.saved?.provider || config.active_provider;
    aiProvider.value = config.providers.some((item) => item.provider === preferred)
      ? preferred
      : config.providers[0]?.provider || "";
    renderAIModels(config);
  }

  /** Clear prior validation whenever an administrator edits either model field. */
  function invalidateAISelection() {
    state.aiChoiceRevision += 1;
    state.validatedAI = null;
    aiSave.disabled = true;
    aiValidate.disabled = !aiProvider.value || !aiModel.value;
    aiStatus.textContent = "Validate the new selection before saving.";
  }

  /** Refresh the model list after a supported provider selection changes. */
  function handleAIProviderChange() {
    invalidateAISelection();
    if (state.aiConfig) renderAIModels(state.aiConfig);
  }

  /** Fill the model select for the currently selected supported provider. */
  function renderAIModels(config) {
    const provider = config.providers.find((item) => item.provider === aiProvider.value);
    aiModel.replaceChildren();
    (provider?.models || []).forEach((model) => {
      const option = makeElement("option", "", model);
      option.value = model;
      aiModel.append(option);
    });
    const saved = config.saved?.provider === aiProvider.value ? config.saved.model : "";
    const active = config.active_provider === aiProvider.value ? config.active_model : "";
    aiModel.value = provider?.models.includes(saved)
      ? saved
      : provider?.models.includes(active)
        ? active
        : provider?.models[0] || "";
  }

  /** Validate the selected pair and enable saving only for that exact unchanged pair. */
  async function validateAISelection() {
    const selection = { provider: aiProvider.value, model: aiModel.value };
    if (!selection.provider || !selection.model) {
      aiStatus.textContent = "Choose a supported provider and model.";
      return;
    }
    const generation = state.generation;
    const choiceRevision = state.aiChoiceRevision;
    state.validatedAI = null;
    aiSave.disabled = true;
    aiValidate.disabled = true;
    aiStatus.textContent = "Validating selection...";
    try {
      const result = await api.validateAdminAI(selection.provider, selection.model);
      if (
        !state.isAdmin
        || generation !== state.generation
        || choiceRevision !== state.aiChoiceRevision
        || selection.provider !== aiProvider.value
        || selection.model !== aiModel.value
      ) return;
      aiStatus.textContent = result.message;
      if (result.is_valid) {
        state.validatedAI = selection;
        aiSave.disabled = false;
      }
    } catch (error) {
      if (generation === state.generation && choiceRevision === state.aiChoiceRevision) showError(error, aiStatus);
    } finally {
      if (generation === state.generation) aiValidate.disabled = !state.isAdmin;
    }
  }

  /** Save the current previously validated selection after server confirmation. */
  async function saveAISelection() {
    const selection = state.validatedAI;
    if (!selection || selection.provider !== aiProvider.value || selection.model !== aiModel.value) return;
    const generation = state.generation;
    aiSave.disabled = true;
    aiValidate.disabled = true;
    aiProvider.disabled = true;
    aiModel.disabled = true;
    aiStatus.textContent = "Saving model selection...";
    try {
      const saved = await api.saveAdminAI(selection.provider, selection.model);
      if (!state.isAdmin || generation !== state.generation) return;
      aiSaved.textContent = `Saved: ${saved.provider} / ${saved.model} (${saved.updated_at})`;
      aiStatus.textContent = "Saved for review only. This selection does not activate on restart; update backend .env to change the active model.";
      state.validatedAI = selection;
    } catch (error) {
      if (generation === state.generation) showError(error, aiStatus);
      aiSave.disabled = true;
    } finally {
      if (generation === state.generation) {
        aiProvider.disabled = !state.isAdmin;
        aiModel.disabled = !state.isAdmin;
        aiValidate.disabled = !state.isAdmin;
      }
    }
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
    aiTab.addEventListener("click", () => selectSection("ai"));
    embeddingsTab.addEventListener("click", () => selectSection("embeddings"));
    memoryTab.addEventListener("click", () => selectSection("memory"));
    settingsTab.addEventListener("click", () => selectSection("settings"));
    aiProvider.addEventListener("change", handleAIProviderChange);
    aiModel.addEventListener("change", invalidateAISelection);
    aiValidate.addEventListener("click", validateAISelection);
    aiSave.addEventListener("click", saveAISelection);
    auditRefresh.addEventListener("click", loadAudit);
    systemRefresh.addEventListener("click", loadSystemStatus);
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
