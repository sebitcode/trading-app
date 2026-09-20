(function () {
  "use strict";

  const SESSION_TOKEN_KEY = "crypto-orchestrator.access-token";
  const state = {
    token: sessionStorage.getItem(SESSION_TOKEN_KEY),
    account: null,
    credentials: [],
    workflows: [],
  };

  const elements = {
    alert: document.querySelector("#global-alert"),
    authPanel: document.querySelector("#auth-panel"),
    workspace: document.querySelector("#workspace"),
    registerForm: document.querySelector("#register-form"),
    signinForm: document.querySelector("#signin-form"),
    registrationToken: document.querySelector("#registration-token"),
    createdToken: document.querySelector("#created-token"),
    copyToken: document.querySelector("#copy-token"),
    accountName: document.querySelector("#account-name"),
    accountId: document.querySelector("#account-id"),
    accountAvatar: document.querySelector("#account-avatar"),
    signOut: document.querySelector("#sign-out"),
    testTelegram: document.querySelector("#test-telegram"),
    workflowForm: document.querySelector("#workflow-form"),
    workflowId: document.querySelector("#workflow-id"),
    workflowName: document.querySelector("#workflow-name"),
    workflowDescription: document.querySelector("#workflow-description"),
    workflowEnabled: document.querySelector("#workflow-enabled"),
    workflowFormTitle: document.querySelector("#workflow-form-title"),
    workflowFormMode: document.querySelector("#workflow-form-mode"),
    saveWorkflow: document.querySelector("#save-workflow"),
    resetWorkflow: document.querySelector("#reset-workflow"),
    beforeSteps: document.querySelector("#before-steps"),
    afterSteps: document.querySelector("#after-steps"),
    workflowList: document.querySelector("#workflow-list"),
    workflowCount: document.querySelector("#workflow-count"),
  };

  function setAlert(message, kind) {
    if (!message) {
      elements.alert.hidden = true;
      elements.alert.textContent = "";
      delete elements.alert.dataset.kind;
      return;
    }
    elements.alert.hidden = false;
    elements.alert.dataset.kind = kind || "info";
    elements.alert.textContent = message;
  }

  function setButtonBusy(button, busy, busyLabel) {
    if (!button) {
      return;
    }
    if (busy) {
      button.dataset.defaultLabel = button.textContent;
      button.disabled = true;
      button.textContent = busyLabel || "Working…";
      return;
    }
    button.disabled = false;
    if (button.dataset.defaultLabel) {
      button.textContent = button.dataset.defaultLabel;
      delete button.dataset.defaultLabel;
    }
  }

  function setSessionToken(token) {
    state.token = token;
    sessionStorage.setItem(SESSION_TOKEN_KEY, token);
  }

  function renderSessionToken() {
    const token = state.token || "";
    elements.createdToken.textContent = token;
    elements.registrationToken.hidden = !token;
  }

  function clearSession() {
    state.token = null;
    state.account = null;
    state.credentials = [];
    state.workflows = [];
    sessionStorage.removeItem(SESSION_TOKEN_KEY);
    renderSessionToken();
    elements.authPanel.hidden = false;
    elements.workspace.hidden = true;
    elements.signinForm.reset();
    document.querySelectorAll("[data-provider-form]").forEach(function (form) {
      form.reset();
    });
    resetWorkflowEditor();
  }

  async function api(path, options, authenticated) {
    const requestOptions = options || {};
    const headers = new Headers(requestOptions.headers || {});
    if (requestOptions.body && !headers.has("content-type")) {
      headers.set("content-type", "application/json");
    }
    if (authenticated !== false && state.token) {
      headers.set("authorization", "Bearer " + state.token);
    }

    const response = await fetch(path, {
      ...requestOptions,
      headers: headers,
    });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : await response.text();

    if (!response.ok) {
      const detail = payload && typeof payload === "object" ? payload.detail : payload;
      if (response.status === 401 && authenticated !== false) {
        clearSession();
      }
      throw new Error(formatError(detail, response.status));
    }
    return payload;
  }

  function formatError(detail, status) {
    if (typeof detail === "string" && detail) {
      return detail;
    }
    if (detail && typeof detail === "object") {
      if (typeof detail.message === "string") {
        return detail.message;
      }
      return JSON.stringify(detail);
    }
    return "Request failed (" + status + ").";
  }

  function accountInitial(name) {
    const normalized = (name || "A").trim();
    return normalized.charAt(0).toUpperCase() || "A";
  }

  function formatDate(value) {
    if (!value) {
      return "";
    }
    try {
      return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value));
    } catch (error) {
      return value;
    }
  }

  function providerMetadata(provider) {
    return state.credentials.find(function (item) {
      return item.provider === provider;
    });
  }

  function renderAccount() {
    elements.accountName.textContent = state.account.name;
    elements.accountId.textContent = state.account.account_id;
    elements.accountAvatar.textContent = accountInitial(state.account.name);
  }

  function renderCredentialStatus() {
    ["binance", "telegram"].forEach(function (provider) {
      const status = document.querySelector('[data-status-for="' + provider + '"]');
      const removeButton = document.querySelector('[data-delete-provider="' + provider + '"]');
      const metadata = providerMetadata(provider);
      if (metadata) {
        status.dataset.state = "configured";
        status.textContent = "Configured · " + formatDate(metadata.updated_at);
        removeButton.hidden = false;
      } else {
        delete status.dataset.state;
        status.textContent = "Not configured";
        removeButton.hidden = true;
      }
    });

    const telegramConfigured = Boolean(providerMetadata("telegram"));
    const telegramForm = document.querySelector('[data-provider-form="telegram"]');
    const telegramToken = telegramForm
      ? telegramForm.querySelector('[data-credential="bot_token"]')
      : null;
    if (telegramToken) {
      telegramToken.required = !telegramConfigured;
      telegramToken.placeholder = telegramConfigured
        ? "Leave blank to keep the saved bot token"
        : "123456:ABC••••";
    }
    elements.testTelegram.disabled = !telegramConfigured;
  }

  async function loadWorkspace() {
    if (!state.token) {
      return;
    }
    setAlert("Loading account workspace…", "info");
    try {
      const responses = await Promise.all([
        api("/api/v1/account"),
        api("/api/v1/account/credentials"),
        api("/api/v1/workflows"),
      ]);
      state.account = responses[0];
      state.credentials = responses[1];
      state.workflows = responses[2];
      renderSessionToken();
      renderAccount();
      renderCredentialStatus();
      renderWorkflows();
      elements.authPanel.hidden = true;
      elements.workspace.hidden = false;
      setAlert("");
    } catch (error) {
      clearSession();
      setAlert(error.message || "Could not open the account workspace.", "error");
    }
  }

  const workflowStepLabels = {
    market_snapshot: "Market snapshot",
    crypto_news: "Crypto news",
    x_posts: "X posts (disabled)",
    pattern_context: "Pattern context",
    lessons: "Candidate lessons",
    agent_instruction: "Agent instruction",
  };

  function stepTypeOptions(selectedType) {
    const options = [
      '<option value="market_snapshot">Market snapshot</option>',
      '<option value="crypto_news">Crypto news</option>',
      '<option value="pattern_context">Pattern context</option>',
      '<option value="lessons">Candidate lessons</option>',
      '<option value="agent_instruction">Agent instruction</option>',
    ];
    if (selectedType === "x_posts") {
      options.splice(2, 0, '<option value="x_posts">X posts (disabled)</option>');
    }
    return options.join("");
  }

  function stepField(label, name, placeholder, wide) {
    return (
      '<label class="field' +
      (wide ? " wide" : "") +
      '">' +
      "<span>" +
      label +
      "</span>" +
      '<input data-step-param="' +
      name +
      '" type="text" placeholder="' +
      placeholder +
      '" autocomplete="off" spellcheck="false" />' +
      "</label>"
    );
  }

  function stepSelect(label, name, options) {
    return (
      '<label class="field">' +
      "<span>" +
      label +
      "</span>" +
      '<select data-step-param="' +
      name +
      '">' +
      options +
      "</select>" +
      "</label>"
    );
  }

  const workflowSymbolChoices = [
    ["$context.symbol", "Use operation symbol ($context.symbol)"],
    ["BTC/USDT", "BTC/USDT"],
    ["ETH/USDT", "ETH/USDT"],
    ["SOL/USDT", "SOL/USDT"],
    ["BNB/USDT", "BNB/USDT"],
    ["XRP/USDT", "XRP/USDT"],
    ["ADA/USDT", "ADA/USDT"],
    ["DOGE/USDT", "DOGE/USDT"],
  ];

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (character) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[character];
    });
  }

  function workflowSymbolOptions(savedValue) {
    const knownValues = new Set(workflowSymbolChoices.map(function (choice) {
      return choice[0];
    }));
    const options = workflowSymbolChoices.map(function (choice) {
      return (
        '<option value="' +
        escapeHtml(choice[0]) +
        '">' +
        escapeHtml(choice[1]) +
        "</option>"
      );
    });
    if (savedValue && !knownValues.has(String(savedValue))) {
      const safeValue = escapeHtml(savedValue);
      options.splice(
        1,
        0,
        '<option value="' + safeValue + '">Saved symbol: ' + safeValue + "</option>"
      );
    }
    return options.join("");
  }

  function stepFields(type, parameters) {
    if (type === "market_snapshot") {
      return [
        stepSelect(
          "Symbol or context variable",
          "symbol",
          workflowSymbolOptions(parameters && parameters.symbol)
        ),
        stepSelect(
          "Market type",
          "market_type",
          '<option value="spot">Spot</option><option value="perpetual">Perpetual</option>'
        ),
        stepField("Timeframe", "timeframe", "15m"),
        stepField("Candle limit", "limit", "20"),
      ].join("");
    }
    if (type === "crypto_news" || type === "x_posts") {
      return [
        stepField("Symbol <em>optional</em>", "symbol", "$context.symbol or BTC/USDT"),
        stepField("Lookback minutes", "lookback_minutes", "1440"),
        stepField("Result limit", "limit", "20"),
      ].join("");
    }
    if (type === "pattern_context") {
      return [
        stepField("Pattern or context variable", "pattern_id", "$context.pattern_id"),
        stepField("Symbol <em>optional</em>", "symbol", "$context.symbol"),
        stepSelect(
          "Market type <em>optional</em>",
          "market_type",
          '<option value="">Any market</option><option value="spot">Spot</option><option value="perpetual">Perpetual</option>'
        ),
        stepSelect(
          "Side <em>optional</em>",
          "side",
          '<option value="">Any side</option><option value="long">Long</option><option value="short">Short</option>'
        ),
        stepField("Case limit", "limit", "50"),
      ].join("");
    }
    if (type === "lessons") {
      return [
        stepField("Pattern <em>optional</em>", "pattern_id", "$context.pattern_id"),
        stepField("Symbol <em>optional</em>", "symbol", "$context.symbol"),
        stepSelect(
          "Market type <em>optional</em>",
          "market_type",
          '<option value="">Any market</option><option value="spot">Spot</option><option value="perpetual">Perpetual</option>'
        ),
        stepSelect(
          "Side <em>optional</em>",
          "side",
          '<option value="">Any side</option><option value="long">Long</option><option value="short">Short</option>'
        ),
        stepField("Lesson limit", "limit", "100"),
      ].join("");
    }
    return (
      '<label class="field wide">' +
      "<span>Instruction for the agent</span>" +
      '<textarea data-step-param="instruction" rows="3" placeholder="Explain how the agent should interpret the outputs."></textarea>' +
      "</label>"
    );
  }

  function renderStepFields(row, parameters) {
    const type = row.querySelector("[data-step-type]").value;
    const fields = row.querySelector("[data-step-fields]");
    fields.innerHTML = stepFields(type, parameters);
    Object.keys(parameters || {}).forEach(function (name) {
      const input = fields.querySelector('[data-step-param="' + name + '"]');
      if (input) {
        input.value = String(parameters[name]);
      }
    });
  }

  function renumberSteps(phase) {
    const container = phase === "before_operation" ? elements.beforeSteps : elements.afterSteps;
    container.querySelectorAll("[data-step-row]").forEach(function (row, index) {
      row.querySelector(".step-order").textContent = String(index + 1).padStart(2, "0");
    });
  }

  function addStep(phase, step) {
    const container = phase === "before_operation" ? elements.beforeSteps : elements.afterSteps;
    const row = document.createElement("div");
    row.className = "workflow-step";
    row.dataset.stepRow = "";
    if (step && step.step_id) {
      row.dataset.stepId = step.step_id;
    }
    row.innerHTML =
      '<div class="step-row-header">' +
      '<span class="step-order">01</span>' +
      '<label class="field">' +
      "<span>Step name</span>" +
      '<input data-step-name type="text" maxlength="120" autocomplete="off" required />' +
      "</label>" +
      '<select class="step-type" data-step-type aria-label="Step type">' +
      stepTypeOptions(step && step.type) +
      "</select>" +
      '<button class="remove-step" type="button" aria-label="Remove workflow step">Remove</button>' +
      "</div>" +
      '<div class="step-fields" data-step-fields></div>';
    const type = (step && step.type) || "market_snapshot";
    row.querySelector("[data-step-type]").value = type;
    row.querySelector("[data-step-name]").value =
      (step && step.name) || workflowStepLabels[type];
    renderStepFields(row, step && step.parameters);
    row.querySelector("[data-step-type]").addEventListener("change", function () {
      const selectedType = row.querySelector("[data-step-type]").value;
      row.querySelector("[data-step-name]").value = workflowStepLabels[selectedType];
      renderStepFields(row);
    });
    row.querySelector(".remove-step").addEventListener("click", function () {
      row.remove();
      renumberSteps(phase);
    });
    container.appendChild(row);
    renumberSteps(phase);
  }

  function collectSteps(container) {
    return Array.from(container.querySelectorAll("[data-step-row]")).map(function (row) {
      const type = row.querySelector("[data-step-type]").value;
      const name = row.querySelector("[data-step-name]").value.trim() || workflowStepLabels[type];
      const parameters = {};
      row.querySelectorAll("[data-step-param]").forEach(function (input) {
        const value = input.value.trim();
        if (value) {
          parameters[input.dataset.stepParam] = value;
        }
      });
      const step = {
        name: name,
        type: type,
        parameters: parameters,
      };
      if (row.dataset.stepId) {
        step.step_id = row.dataset.stepId;
      }
      return step;
    });
  }

  function resetWorkflowEditor() {
    if (!elements.workflowForm) {
      return;
    }
    elements.workflowForm.reset();
    elements.workflowId.value = "";
    elements.workflowFormTitle.textContent = "Create workflow";
    elements.workflowFormMode.textContent = "New";
    elements.saveWorkflow.textContent = "Save workflow";
    elements.beforeSteps.replaceChildren();
    elements.afterSteps.replaceChildren();
    addStep("before_operation");
    addStep("after_operation");
  }

  function renderWorkflows() {
    elements.workflowCount.textContent = String(state.workflows.length);
    elements.workflowList.replaceChildren();
    if (!state.workflows.length) {
      const empty = document.createElement("div");
      empty.className = "empty-workflows";
      empty.innerHTML =
        '<span class="empty-mark" aria-hidden="true">+</span>' +
        "<strong>No workflows yet</strong>" +
        "<p>Create a named playbook so agents can select it by name.</p>";
      elements.workflowList.appendChild(empty);
      return;
    }

    state.workflows.forEach(function (workflow) {
      const item = document.createElement("article");
      item.className = "workflow-list-item";
      item.dataset.workflowId = workflow.workflow_id;
      item.dataset.enabled = String(workflow.enabled);

      const top = document.createElement("div");
      top.className = "workflow-list-item-top";
      const name = document.createElement("strong");
      name.textContent = workflow.name;
      const stateBadge = document.createElement("span");
      stateBadge.className = "workflow-state";
      stateBadge.dataset.enabled = String(workflow.enabled);
      stateBadge.textContent = workflow.enabled ? "Enabled" : "Disabled";
      top.append(name, stateBadge);

      const description = document.createElement("p");
      description.textContent = workflow.description || "No description provided.";

      const meta = document.createElement("div");
      meta.className = "workflow-list-item-meta";
      const phases = document.createElement("span");
      phases.textContent =
        workflow.before_steps.length +
        " pre · " +
        workflow.after_steps.length +
        " post steps";
      const version = document.createElement("span");
      version.textContent = "v" + workflow.version;
      meta.append(phases, version);

      const actions = document.createElement("div");
      actions.className = "workflow-list-item-actions";
      const edit = document.createElement("button");
      edit.className = "text-button";
      edit.type = "button";
      edit.dataset.editWorkflow = workflow.workflow_id;
      edit.textContent = "Edit";
      const remove = document.createElement("button");
      remove.className = "text-button danger-button";
      remove.type = "button";
      remove.dataset.deleteWorkflow = workflow.workflow_id;
      remove.textContent = "Delete";
      actions.append(edit, remove);

      item.append(top, description, meta, actions);
      elements.workflowList.appendChild(item);
    });
  }

  async function loadWorkflows() {
    state.workflows = await api("/api/v1/workflows");
    renderWorkflows();
  }

  async function handleSaveWorkflow(event) {
    event.preventDefault();
    const workflowId = elements.workflowId.value.trim();
    const beforeSteps = collectSteps(elements.beforeSteps);
    const afterSteps = collectSteps(elements.afterSteps);
    if (!beforeSteps.length && !afterSteps.length) {
      setAlert("Add at least one workflow step.", "error");
      return;
    }
    const payload = {
      name: elements.workflowName.value.trim(),
      description: elements.workflowDescription.value.trim(),
      before_steps: beforeSteps,
      after_steps: afterSteps,
      enabled: elements.workflowEnabled.checked,
    };
    setButtonBusy(elements.saveWorkflow, true, "Saving…");
    setAlert("");
    try {
      await api(
        workflowId ? "/api/v1/workflows/" + encodeURIComponent(workflowId) : "/api/v1/workflows",
        {
          method: workflowId ? "PUT" : "POST",
          body: JSON.stringify(payload),
        }
      );
      await loadWorkflows();
      resetWorkflowEditor();
      setAlert(workflowId ? "Workflow updated." : "Workflow created.", "success");
    } catch (error) {
      setAlert(error.message || "Could not save the workflow.", "error");
    } finally {
      setButtonBusy(elements.saveWorkflow, false);
    }
  }

  function editWorkflow(workflowId) {
    const workflow = state.workflows.find(function (item) {
      return item.workflow_id === workflowId;
    });
    if (!workflow) {
      return;
    }
    elements.workflowId.value = workflow.workflow_id;
    elements.workflowName.value = workflow.name;
    elements.workflowDescription.value = workflow.description;
    elements.workflowEnabled.checked = workflow.enabled;
    elements.workflowFormTitle.textContent = "Edit workflow";
    elements.workflowFormMode.textContent = "Editing";
    elements.saveWorkflow.textContent = "Update workflow";
    elements.beforeSteps.replaceChildren();
    elements.afterSteps.replaceChildren();
    workflow.before_steps.forEach(function (step) {
      addStep("before_operation", step);
    });
    workflow.after_steps.forEach(function (step) {
      addStep("after_operation", step);
    });
    elements.workflowName.focus();
  }

  async function deleteWorkflow(workflowId) {
    const workflow = state.workflows.find(function (item) {
      return item.workflow_id === workflowId;
    });
    if (!workflow || !window.confirm("Delete the workflow " + workflow.name + "?")) {
      return;
    }
    setAlert("");
    try {
      await api("/api/v1/workflows/" + encodeURIComponent(workflowId), {
        method: "DELETE",
      });
      if (elements.workflowId.value === workflowId) {
        resetWorkflowEditor();
      }
      await loadWorkflows();
      setAlert("Workflow deleted.", "success");
    } catch (error) {
      setAlert(error.message || "Could not delete the workflow.", "error");
    }
  }

  function handleWorkflowListClick(event) {
    const editButton = event.target.closest("[data-edit-workflow]");
    if (editButton) {
      editWorkflow(editButton.dataset.editWorkflow);
      return;
    }
    const deleteButton = event.target.closest("[data-delete-workflow]");
    if (deleteButton) {
      deleteWorkflow(deleteButton.dataset.deleteWorkflow);
    }
  }

  async function handleRegister(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const submitButton = form.querySelector("button[type=submit]");
    const name = form.elements.account_name.value.trim();
    const bootstrapToken = form.elements.bootstrap_token.value.trim();
    const headers = {};

    if (bootstrapToken) {
      headers["X-Bootstrap-Token"] = bootstrapToken;
    }

    setButtonBusy(submitButton, true, "Creating…");
    setAlert("");
    try {
      const created = await api(
        "/api/v1/accounts",
        {
          method: "POST",
          headers: headers,
          body: JSON.stringify({ name: name }),
        },
        false
      );
      setSessionToken(created.access_token);
      form.elements.bootstrap_token.value = "";
      await loadWorkspace();
      setAlert("Account created. The access token remains visible until you sign out.", "success");
    } catch (error) {
      setAlert(error.message || "Could not create the account.", "error");
    } finally {
      setButtonBusy(submitButton, false);
    }
  }

  async function handleSignIn(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const submitButton = form.querySelector("button[type=submit]");
    const token = form.elements.access_token.value.trim();
    setSessionToken(token);
    setButtonBusy(submitButton, true, "Opening…");
    setAlert("");
    try {
      await loadWorkspace();
      if (elements.workspace.hidden) {
        throw new Error("Could not authenticate the account.");
      }
      form.elements.access_token.value = "";
    } catch (error) {
      setAlert(error.message || "Could not open the account.", "error");
    } finally {
      setButtonBusy(submitButton, false);
    }
  }

  function credentialValues(form) {
    const values = {};
    form.querySelectorAll("[data-credential]").forEach(function (input) {
      const value = input.value.trim();
      if (value) {
        values[input.dataset.credential] = value;
      }
    });
    return values;
  }

  async function handleSaveCredential(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const provider = form.dataset.providerForm;
    const submitButton = form.querySelector("button[type=submit]");
    setButtonBusy(submitButton, true, "Saving…");
    setAlert("");

    try {
      const values = credentialValues(form);
      const requiredInputs = form.querySelectorAll("[data-credential][required]");
      const missing = Array.from(requiredInputs).some(function (input) {
        return !values[input.dataset.credential];
      });
      if (missing) {
        throw new Error("Complete the required fields before saving.");
      }

      await api("/api/v1/account/credentials/" + encodeURIComponent(provider), {
        method: "PUT",
        body: JSON.stringify({ values: values }),
      });
      form.querySelectorAll("input[type=password]").forEach(function (input) {
        input.value = "";
      });
      state.credentials = await api("/api/v1/account/credentials");
      renderCredentialStatus();
      setAlert(providerLabel(provider) + " credentials saved securely.", "success");
    } catch (error) {
      setAlert(error.message || "Could not save the credentials.", "error");
    } finally {
      setButtonBusy(submitButton, false);
    }
  }

  async function handleDelete(provider) {
    if (!window.confirm("Remove the " + providerLabel(provider) + " credentials from this account?")) {
      return;
    }
    setAlert("");
    try {
      await api("/api/v1/account/credentials/" + encodeURIComponent(provider), {
        method: "DELETE",
      });
      const form = document.querySelector('[data-provider-form="' + provider + '"]');
      form.reset();
      state.credentials = await api("/api/v1/account/credentials");
      renderCredentialStatus();
      setAlert(providerLabel(provider) + " credentials removed.", "success");
    } catch (error) {
      setAlert(error.message || "Could not remove the credentials.", "error");
    }
  }

  function providerLabel(provider) {
    return {
      x: "X",
      binance: "Binance",
      telegram: "Telegram",
    }[provider] || provider;
  }

  async function handleTelegramTest() {
    setButtonBusy(elements.testTelegram, true, "Sending…");
    setAlert("");
    try {
      const result = await api("/api/v1/account/notifications/telegram/test", {
        method: "POST",
      });
      if (result.status === "sent") {
        setAlert("Telegram test delivered to " + result.delivered_chats + " chat(s).", "success");
      } else if (result.status === "not_configured") {
        setAlert("Configure Telegram before sending a test.", "error");
      } else {
        setAlert(result.error || "Telegram could not deliver the test message.", "error");
      }
    } catch (error) {
      setAlert(error.message || "Could not test Telegram.", "error");
    } finally {
      setButtonBusy(elements.testTelegram, false);
      renderCredentialStatus();
    }
  }

  async function copyCreatedToken() {
    const token = elements.createdToken.textContent;
    try {
      await navigator.clipboard.writeText(token);
      setAlert("Access token copied to the clipboard.", "success");
    } catch (error) {
      setAlert("Copy was blocked by the browser. Select the token manually.", "error");
    }
  }

  elements.registerForm.addEventListener("submit", handleRegister);
  elements.signinForm.addEventListener("submit", handleSignIn);
  elements.copyToken.addEventListener("click", copyCreatedToken);
  elements.signOut.addEventListener("click", function () {
    clearSession();
    setAlert("Signed out. The account token was removed from this tab.", "success");
  });
  elements.testTelegram.addEventListener("click", handleTelegramTest);
  elements.workflowForm.addEventListener("submit", handleSaveWorkflow);
  elements.resetWorkflow.addEventListener("click", resetWorkflowEditor);
  elements.workflowList.addEventListener("click", handleWorkflowListClick);

  document.querySelectorAll("[data-provider-form]").forEach(function (form) {
    form.addEventListener("submit", handleSaveCredential);
  });

  document.querySelectorAll("[data-delete-provider]").forEach(function (button) {
    button.addEventListener("click", function () {
      handleDelete(button.dataset.deleteProvider);
    });
  });

  document.querySelectorAll("[data-add-step]").forEach(function (button) {
    button.addEventListener("click", function () {
      addStep(button.dataset.addStep);
    });
  });

  resetWorkflowEditor();
  loadWorkspace();
})();
