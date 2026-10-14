/* Keep login, registration, and logout separate from chat and conversation rendering. */
(function startAuthentication(window, document, api) {
  "use strict";

  const authRoot = document.getElementById("auth-root");
  const appRoot = document.getElementById("app-root");
  const form = document.getElementById("auth-form");
  const error = document.getElementById("auth-error");
  let accountRevision = 0;
  let activeTheme = "light";
  let themeSelect;
  let themeStatus;
  let isBusy = false;

  /** Display a friendly message without inserting dynamic text as HTML. */
  function showMessage(message, isSuccess = false) {
    error.textContent = message;
    error.hidden = false;
    error.classList.toggle("error", !isSuccess);
  }

  /** Clear a previous login or registration message. */
  function clearMessage() {
    error.textContent = "";
    error.hidden = true;
    error.classList.add("error");
  }

  /** Set the authentication screen to either `login` or `register` mode. */
  function setMode(mode) {
    const isRegistering = mode === "register";
    document.getElementById("auth-title").textContent = isRegistering ? "Create your account" : "Welcome back";
    document.getElementById("auth-description").textContent = isRegistering
      ? "Create a secure account for your private support workspace."
      : "Sign in to open your private support workspace.";
    document.getElementById("auth-submit").textContent = isRegistering ? "Create account" : "Sign in";
    document.getElementById("auth-password").autocomplete = isRegistering ? "new-password" : "current-password";
    document.getElementById("auth-mode").textContent = isRegistering
      ? "I already have an account"
      : "Create an account";
    form.dataset.mode = mode;
    clearMessage();
  }

  /** Show only the sign-in screen and clear the prior account's visible details. */
  function showSignIn() {
    accountRevision += 1;
    applyTheme("light");
    window.PrivateConversations.clear();
    appRoot.hidden = true;
    authRoot.hidden = false;
    document.getElementById("account-email").textContent = "";
    setMode("login");
  }

  /** Show the private conversation workspace after a server-confirmed session. */
  function showAccount(user) {
    const revision = ++accountRevision;
    authRoot.hidden = true;
    window.PrivateConversations.show(user);
    loadTheme(revision);
  }

  /** Apply only a known theme to the document root. */
  function applyTheme(theme) {
    activeTheme = theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = activeTheme;
  }

  /** Load the server preference for the current session without blocking chat access. */
  async function loadTheme(revision) {
    try {
      const settings = await api.userSettings();
      if (revision !== accountRevision) return;
      applyTheme(settings.theme);
      themeSelect.value = activeTheme;
      themeStatus.textContent = `Using your saved ${activeTheme} theme.`;
    } catch (requestError) {
      if (revision !== accountRevision) return;
      themeSelect.value = activeTheme;
      themeStatus.textContent = requestError.message || "Theme preference could not be loaded.";
    }
  }

  /** Save a user-selected theme and keep the last server-confirmed theme on failure. */
  async function saveTheme() {
    const requestedTheme = themeSelect.value;
    const revision = accountRevision;
    themeSelect.disabled = true;
    themeStatus.textContent = "Saving preference...";
    try {
      const settings = await api.saveUserSettings({ theme: requestedTheme });
      if (revision !== accountRevision) return;
      applyTheme(settings.theme);
      themeSelect.value = activeTheme;
      themeStatus.textContent = `Saved ${activeTheme} theme.`;
    } catch (requestError) {
      if (revision !== accountRevision) return;
      themeSelect.value = activeTheme;
      themeStatus.textContent = requestError.message || "Theme preference could not be saved.";
    } finally {
      themeSelect.disabled = false;
    }
  }

  /** Create the accessible theme control in the signed-in sidebar. */
  function addThemeControl() {
    const signOutButton = document.getElementById("sign-out");
    const preferences = document.createElement("div");
    preferences.className = "user-preferences";
    const label = document.createElement("label");
    label.htmlFor = "theme-select";
    label.textContent = "Appearance";
    themeSelect = document.createElement("select");
    themeSelect.id = "theme-select";
    ["light", "dark"].forEach((theme) => {
      const option = document.createElement("option");
      option.value = theme;
      option.textContent = theme === "light" ? "Light theme" : "Dark theme";
      themeSelect.append(option);
    });
    themeSelect.value = activeTheme;
    themeStatus = document.createElement("span");
    themeStatus.className = "user-preferences-status";
    themeStatus.setAttribute("role", "status");
    themeStatus.setAttribute("aria-live", "polite");
    themeSelect.addEventListener("change", saveTheme);
    preferences.append(label, themeSelect, themeStatus);
    signOutButton.parentElement.append(preferences);
  }

  /** Submit sign-in or account creation without storing passwords in browser storage. */
  async function submit(event) {
    event.preventDefault();
    if (isBusy) return;
    isBusy = true;
    const button = document.getElementById("auth-submit");
    button.disabled = true;
    clearMessage();
    const email = document.getElementById("auth-email").value.trim();
    const password = document.getElementById("auth-password").value;
    try {
      if (form.dataset.mode === "register") {
        await api.register(email, password);
        setMode("login");
        document.getElementById("auth-email").value = email;
        showMessage("Account created. Sign in to continue.", true);
      } else {
        const result = await api.login(email, password);
        showAccount(result.user);
      }
      document.getElementById("auth-password").value = "";
    } catch (requestError) {
      showMessage(requestError.message || "The request failed. Try again.");
    } finally {
      isBusy = false;
      button.disabled = false;
    }
  }

  /** Revoke the server session before returning the browser to sign-in. */
  async function signOut() {
    const button = document.getElementById("sign-out");
    button.disabled = true;
    try {
      await api.logout();
      showSignIn();
    } catch (requestError) {
      showMessage(requestError.message || "Sign out failed. Try again.");
      authRoot.hidden = false;
      appRoot.hidden = true;
    } finally {
      button.disabled = false;
    }
  }

  /** Restore an existing cookie session or leave the user at the login form. */
  async function restoreSession() {
    try {
      showAccount(await api.currentUser());
    } catch (requestError) {
      showSignIn();
      if (requestError.status !== 401) showMessage(requestError.message);
    }
  }

  /** Attach form controls and restore the server-managed session on page load. */
  function initialize() {
    addThemeControl();
    form.addEventListener("submit", submit);
    document.getElementById("auth-mode").addEventListener("click", () => {
      setMode(form.dataset.mode === "register" ? "login" : "register");
    });
    document.getElementById("sign-out").addEventListener("click", signOut);
    setMode("login");
    restoreSession();
  }

  initialize();
  window.PrivateAuth = Object.freeze({ showSignIn });
})(window, document, window.SupportApi);
