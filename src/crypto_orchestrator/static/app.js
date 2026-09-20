(function () {
  "use strict";

  const SESSION_TOKEN_KEY = "crypto-orchestrator.access-token";
  const state = {
    token: sessionStorage.getItem(SESSION_TOKEN_KEY),
    account: null,
    credentials: [],
    workflows: [],
    plans: [],
    operations: [],
    strategyCycles: [],
    lessons: [],
    health: null,
    selectedOperation: null,
    currentView: "overview",
  };

  const $ = (selector, root) => (root || document).querySelector(selector);
  const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));
  const elements = {
    alert: $("#global-alert"),
    authPanel: $("#auth-panel"),
    workspace: $("#workspace"),
    registerForm: $("#register-form"),
    signinForm: $("#signin-form"),
    registrationToken: $("#registration-token"),
    createdToken: $("#created-token"),
    copyToken: $("#copy-token"),
    railAccount: $("#rail-account"),
    railAvatar: $("#rail-avatar"),
    railAccountName: $("#rail-account-name"),
    railAccountId: $("#rail-account-id"),
    accountAvatar: $("#account-avatar"),
    accountName: $("#account-name"),
    accountId: $("#account-id"),
    accountCreated: $("#account-created"),
    signOut: $("#sign-out"),
    connectionPill: $("#connection-pill"),
    connectionLabel: $("#connection-label"),
    lastSync: $("#last-sync"),
    currentViewLabel: $("#current-view-label"),
    mobileNavToggle: $("#mobile-nav-toggle"),
    healthSummary: $("#health-summary"),
    healthErrors: $("#health-errors"),
    kpiOperations: $("#kpi-operations"),
    kpiOperationsNote: $("#kpi-operations-note"),
    kpiOpen: $("#kpi-open"),
    kpiOpenNote: $("#kpi-open-note"),
    kpiPlans: $("#kpi-plans"),
    kpiPlansNote: $("#kpi-plans-note"),
    kpiLessons: $("#kpi-lessons"),
    kpiLessonsNote: $("#kpi-lessons-note"),
    kpiHealth: $("#kpi-health"),
    kpiHealthNote: $("#kpi-health-note"),
    testTelegram: $("#test-telegram"),
    overviewOperations: $("#overview-operations"),
    overviewMarketResult: $("#overview-market-result"),
    planList: $("#plan-list"),
    planCount: $("#plan-count"),
    planForm: $("#plan-form"),
    planId: $("#plan-id"),
    planSymbolOptions: $("#plan-symbol-options"),
    planFormTitle: $("#plan-form-title"),
    planFormMode: $("#plan-form-mode"),
    planSteps: $("#plan-steps"),
    savePlan: $("#save-plan"),
    runPlanName: $("#run-plan-name"),
    strategyPlanName: $("#strategy-plan-name"),
    strategyRunId: $("#strategy-run-id"),
    strategyOutput: $("#strategy-output"),
    strategyCycleHistory: $("#strategy-cycle-history"),
    cycleCount: $("#cycle-count"),
    planRunOutput: $("#plan-run-output"),
    workflowList: $("#workflow-list"),
    workflowCount: $("#workflow-count"),
    workflowForm: $("#workflow-form"),
    workflowId: $("#workflow-id"),
    workflowName: $("#workflow-name"),
    workflowDescription: $("#workflow-description"),
    workflowEnabled: $("#workflow-enabled"),
    workflowFormTitle: $("#workflow-form-title"),
    workflowFormMode: $("#workflow-form-mode"),
    saveWorkflow: $("#save-workflow"),
    beforeSteps: $("#before-steps"),
    afterSteps: $("#after-steps"),
    operationList: $("#operation-list"),
    operationCount: $("#operation-count"),
    operationDetail: $("#operation-detail"),
    operationDetailTitle: $("#operation-detail-title"),
    operationDetailStatus: $("#operation-detail-status"),
    proposalEditor: $("#proposal-editor"),
    proposalForm: $("#proposal-form"),
    riskOutput: $("#risk-output"),
    patternRows: $("#pattern-rows"),
    outcomeEditor: $("#outcome-editor"),
    outcomeForm: $("#outcome-form"),
    postmortemEditor: $("#postmortem-editor"),
    postmortemForm: $("#postmortem-form"),
  };

  function escapeHtml(value) {
    return String(value === undefined || value === null ? "" : value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[character]);
  }

  function safeExternalUrl(value) {
    try {
      const url = new URL(String(value || ""), window.location.origin);
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
    } catch (error) {
      return "";
    }
  }

  function setAlert(message, kind) {
    if (!elements.alert) return;
    elements.alert.hidden = !message;
    elements.alert.textContent = message || "";
    if (message) elements.alert.dataset.kind = kind || "info";
    else delete elements.alert.dataset.kind;
  }

  function setConnection(connected, label) {
    elements.connectionPill.classList.toggle("is-online", Boolean(connected));
    elements.connectionLabel.textContent = label || (connected ? "connected" : "offline");
  }

  function setBusy(button, busy, label) {
    if (!button) return;
    if (busy) {
      if (!button.dataset.originalLabel) button.dataset.originalLabel = button.textContent;
      button.disabled = true;
      button.textContent = label || "Working…";
    } else {
      button.disabled = false;
      if (button.dataset.originalLabel) {
        button.textContent = button.dataset.originalLabel;
        delete button.dataset.originalLabel;
      }
    }
  }

  function setSessionToken(token) {
    state.token = token;
    sessionStorage.setItem(SESSION_TOKEN_KEY, token);
  }

  function clearSession() {
    state.token = null;
    state.account = null;
    state.credentials = [];
    state.workflows = [];
    state.plans = [];
    state.operations = [];
    state.strategyCycles = [];
    state.lessons = [];
    state.health = null;
    state.selectedOperation = null;
    sessionStorage.removeItem(SESSION_TOKEN_KEY);
    elements.authPanel.hidden = false;
    elements.workspace.hidden = true;
    elements.railAccount.hidden = true;
    elements.signinForm.reset();
    closeEditor("proposal-editor");
    closeEditor("outcome-editor");
    closeEditor("postmortem-editor");
    resetWorkflowEditor();
    resetPlanEditor();
    setConnection(false, "offline");
    elements.lastSync.textContent = "not connected";
  }

  function formatError(detail, status) {
    if (typeof detail === "string" && detail) return detail;
    if (detail && typeof detail === "object") {
      if (typeof detail.message === "string") return detail.message;
      if (Array.isArray(detail)) {
        return detail.map((item) => item.msg || item.message || JSON.stringify(item)).join("; ");
      }
      if (Array.isArray(detail.errors)) return formatError(detail.errors, status);
      return JSON.stringify(detail);
    }
    return "Request failed (" + status + ").";
  }

  async function api(path, options, authenticated) {
    const requestOptions = options || {};
    const headers = new Headers(requestOptions.headers || {});
    if (requestOptions.body && !headers.has("content-type")) headers.set("content-type", "application/json");
    if (authenticated !== false && state.token) headers.set("authorization", "Bearer " + state.token);
    const response = await fetch(path, { ...requestOptions, headers });
    const contentType = response.headers.get("content-type") || "";
    const payload = response.status === 204 ? null : contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      if (response.status === 401 && authenticated !== false) clearSession();
      throw new Error(formatError(payload && typeof payload === "object" ? payload.detail : payload, response.status));
    }
    return payload;
  }

  function formatDate(value, includeTime) {
    if (!value) return "—";
    try {
      return new Intl.DateTimeFormat(undefined, includeTime === false ? { dateStyle: "medium" } : { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
    } catch (error) {
      return String(value);
    }
  }

  function formatNumber(value, digits) {
    if (value === null || value === undefined || value === "") return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits === undefined ? 4 : digits }).format(number);
  }

  function formatPercent(value) {
    if (value === null || value === undefined || value === "") return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    return formatNumber(number, 2) + "%";
  }

  function canonicalSymbol(value) {
    return String(value || "").replace(/[\s/]/g, "").toUpperCase();
  }

  function lines(value) {
    return String(value || "").split("\n").map((item) => item.trim()).filter(Boolean);
  }

  function commaList(value) {
    return String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
  }

  function keyValueMap(value) {
    const result = {};
    commaList(value).forEach((pair) => {
      const separator = pair.indexOf("=");
      if (separator > 0) result[pair.slice(0, separator).trim()] = pair.slice(separator + 1).trim();
    });
    return result;
  }

  function optionalNumber(value) {
    return value === undefined || value === null || String(value).trim() === "" ? undefined : Number(value);
  }

  function formValue(form, name) {
    const field = form.elements[name];
    return field ? String(field.value || "").trim() : "";
  }

  function providerMetadata(provider) {
    return state.credentials.find((item) => item.provider === provider);
  }

  function accountInitial(name) {
    return (String(name || "A").trim().charAt(0) || "A").toUpperCase();
  }

  function renderAccount() {
    if (!state.account) return;
    const initial = accountInitial(state.account.name);
    elements.accountName.textContent = state.account.name;
    elements.accountId.textContent = state.account.account_id;
    elements.accountCreated.textContent = "Created " + formatDate(state.account.created_at, false);
    elements.accountAvatar.textContent = initial;
    elements.railAvatar.textContent = initial;
    elements.railAccountName.textContent = state.account.name;
    elements.railAccountId.textContent = state.account.account_id;
    elements.railAccount.hidden = false;
  }

  function showView(view, updateHash) {
    const requested = document.querySelector('[data-view-panel="' + view + '"]') ? view : "overview";
    state.currentView = requested;
    $$('[data-view-panel]').forEach((panel) => {
      const active = panel.dataset.viewPanel === requested;
      panel.hidden = !active;
      panel.classList.toggle("is-visible", active);
    });
    $$('[data-view]').forEach((link) => link.classList.toggle("is-active", link.dataset.view === requested));
    elements.currentViewLabel.textContent = requested;
    if (updateHash !== false && window.location.hash !== "#" + requested) history.replaceState(null, "", "#" + requested);
    if (elements.mobileNavToggle) {
      elements.mobileNavToggle.setAttribute("aria-expanded", "false");
      document.body.classList.remove("nav-open");
    }
  }

  function updateSync() {
    elements.lastSync.textContent = "synced " + new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(new Date());
  }

  function healthRows(value, prefix) {
    if (!value || typeof value !== "object") return "";
    return Object.entries(value).map(([key, item]) => {
      const label = prefix ? prefix + " / " + key : key;
      if (item && typeof item === "object" && !Array.isArray(item)) return '<div class="health-group"><span>' + escapeHtml(label) + '</span><div>' + healthRows(item, "") + "</div></div>";
      const normalized = String(item);
      const good = ["ok", "configured", "enabled", "sent", "available"].includes(normalized);
      return '<div class="health-row"><span>' + escapeHtml(label.replaceAll("_", " ")) + '</span><strong class="health-value ' + (good ? "good" : "muted") + '"><i></i>' + escapeHtml(normalized) + "</strong></div>";
    }).join("");
  }

  function renderHealth(health) {
    state.health = health;
    if (!health) return;
    const rows = healthRows({ status: health.status, environment: health.environment, paper_trading_only: health.paper_trading_only, connectors: health.connectors });
    elements.healthSummary.innerHTML = rows || '<div class="empty-state compact"><strong>No health data</strong></div>';
    const connectorErrors = [];
    const walk = (value, path) => {
      if (!value || typeof value !== "object") return;
      Object.entries(value).forEach(([key, item]) => {
        const next = path ? path + "." + key : key;
        if (item && typeof item === "object") walk(item, next);
        else if (["unavailable", "not_configured", "disabled", "error"].some((word) => String(item).includes(word))) connectorErrors.push(next + " = " + item);
      });
    };
    walk(health.connectors, "connectors");
    elements.healthErrors.hidden = !connectorErrors.length;
    elements.healthErrors.textContent = connectorErrors.length ? connectorErrors.join(" · ") : "";
    elements.kpiHealth.textContent = health.status === "ok" ? "OK" : "WARN";
    elements.kpiHealthNote.textContent = health.paper_trading_only ? "paper boundary enforced" : "check execution mode";
  }

  async function refreshHealth() {
    try {
      const health = await api("/health", {}, false);
      renderHealth(health);
      setConnection(true, "connected");
    } catch (error) {
      setConnection(false, "unavailable");
      elements.kpiHealth.textContent = "—";
      elements.kpiHealthNote.textContent = error.message;
    }
  }

  function renderCredentialStatus() {
    ["binance", "telegram"].forEach((provider) => {
      const status = $('[data-status-for="' + provider + '"]');
      const remove = $('[data-delete-provider="' + provider + '"]');
      const metadata = providerMetadata(provider);
      if (metadata) {
        status.dataset.state = "configured";
        status.textContent = "configured · " + formatDate(metadata.updated_at);
        remove.hidden = false;
      } else {
        delete status.dataset.state;
        status.textContent = "not configured";
        remove.hidden = true;
      }
    });
    const telegramConfigured = Boolean(providerMetadata("telegram"));
    const telegramToken = $('[data-provider-form="telegram"] [data-credential="bot_token"]');
    if (telegramToken) {
      telegramToken.required = !telegramConfigured;
      telegramToken.placeholder = telegramConfigured ? "Leave blank to keep the saved bot token" : "123456:ABC••••";
    }
    elements.testTelegram.disabled = !telegramConfigured;
  }

  function renderKpis() {
    const operations = state.operations;
    const open = operations.filter((operation) => operation.status === "paper_open").length;
    const activePlans = state.plans.filter((plan) => plan.status === "active").length;
    elements.kpiOperations.textContent = formatNumber(operations.length, 0);
    elements.kpiOperationsNote.textContent = operations.length ? "account ledger" : "no proposals yet";
    elements.kpiOpen.textContent = formatNumber(open, 0);
    elements.kpiOpenNote.textContent = open ? "positions need monitoring" : "no active positions";
    elements.kpiPlans.textContent = formatNumber(activePlans, 0);
    elements.kpiPlansNote.textContent = state.plans.length + " total plan" + (state.plans.length === 1 ? "" : "s");
    elements.kpiLessons.textContent = formatNumber(state.lessons.length, 0);
    elements.kpiLessonsNote.textContent = state.lessons.length ? "candidate observations" : "memory is empty";
  }

  function renderOverviewOperations() {
    const recent = state.operations.slice(0, 5);
    if (!recent.length) {
      elements.overviewOperations.innerHTML = '<div class="empty-state compact"><span class="empty-glyph">◌</span><strong>No operations yet</strong><p>Accepted paper proposals will appear here.</p></div>';
      return;
    }
    elements.overviewOperations.innerHTML = '<table class="data-table compact-table"><thead><tr><th>Symbol</th><th>Side</th><th>Status</th><th>Created</th></tr></thead><tbody>' + recent.map((operation) => {
      const proposal = operation.proposal || {};
      return '<tr data-operation-id="' + escapeHtml(operation.operation_id) + '"><td><strong>' + escapeHtml(proposal.context && proposal.context.symbol) + '</strong><small>' + escapeHtml(operation.operation_id) + '</small></td><td class="side-' + escapeHtml(proposal.context && proposal.context.side) + '">' + escapeHtml(proposal.context && proposal.context.side) + '</td><td><span class="status-badge status-' + escapeHtml(operation.status) + '">' + escapeHtml(operation.status.replaceAll("_", " ")) + '</span></td><td>' + escapeHtml(formatDate(operation.created_at)) + '</td></tr>';
    }).join("") + "</tbody></table>";
  }

  async function loadWorkspace() {
    if (!state.token) return;
    setAlert("Loading the desk…", "info");
    try {
      const [account, credentials, workflows, plans, operations, strategyCycles, lessons] = await Promise.all([
        api("/api/v1/account"),
        api("/api/v1/account/credentials"),
        api("/api/v1/workflows"),
        api("/api/v1/execution-plans"),
        api("/api/v1/operations"),
        api("/api/v1/strategy/cycles?limit=100"),
        api("/api/v1/lessons?limit=500"),
      ]);
      state.account = account;
      state.credentials = credentials;
      state.workflows = workflows;
      state.plans = plans;
      state.operations = operations;
      state.strategyCycles = strategyCycles;
      state.lessons = lessons;
      renderAccount();
      renderCredentialStatus();
      renderWorkflows();
      renderPlans();
      renderOperations();
      renderStrategyCycles();
      renderKpis();
      renderOverviewOperations();
      populateResourceSelects();
      elements.authPanel.hidden = true;
      elements.workspace.hidden = false;
      showView(window.location.hash.slice(1) || "overview", false);
      updateSync();
      setAlert("");
      await refreshHealth();
    } catch (error) {
      clearSession();
      setAlert(error.message || "Could not open the account workspace.", "error");
    }
  }

  async function refreshWorkspace() {
    if (!state.token) return;
    try {
      const [workflows, plans, operations, strategyCycles, lessons] = await Promise.all([
        api("/api/v1/workflows"),
        api("/api/v1/execution-plans"),
        api("/api/v1/operations"),
        api("/api/v1/strategy/cycles?limit=100"),
        api("/api/v1/lessons?limit=500"),
      ]);
      state.workflows = workflows;
      state.plans = plans;
      state.operations = operations;
      state.strategyCycles = strategyCycles;
      state.lessons = lessons;
      renderWorkflows();
      renderPlans();
      renderOperations();
      renderStrategyCycles();
      renderKpis();
      renderOverviewOperations();
      populateResourceSelects();
      updateSync();
      await refreshHealth();
      setAlert("Desk refreshed.", "success");
    } catch (error) {
      setAlert(error.message || "Could not refresh the desk.", "error");
    }
  }

  // Workflow editor -------------------------------------------------------
  const workflowStepLabels = { market_snapshot: "Market snapshot", crypto_news: "Crypto news", x_posts: "X posts (disabled)", pattern_context: "Pattern context", lessons: "Candidate lessons", agent_instruction: "Agent instruction" };
  const workflowSymbolChoices = [["$context.symbol", "Use operation symbol ($context.symbol)"], ["BTC/USDT", "BTC/USDT"], ["ETH/USDT", "ETH/USDT"], ["ZEC/USDT", "ZEC/USDT"], ["LTC/USDT", "LTC/USDT"], ["LINK/USDT", "LINK/USDT"], ["AVAX/USDT", "AVAX/USDT"], ["DOT/USDT", "DOT/USDT"], ["UNI/USDT", "UNI/USDT"], ["AAVE/USDT", "AAVE/USDT"], ["TRX/USDT", "TRX/USDT"], ["SOL/USDT", "SOL/USDT"], ["BNB/USDT", "BNB/USDT"], ["XRP/USDT", "XRP/USDT"], ["ADA/USDT", "ADA/USDT"], ["DOGE/USDT", "DOGE/USDT"]];
  const planSymbolChoices = workflowSymbolChoices.slice(1).map(([symbol]) => symbol);

  function workflowSymbolOptions(savedValue) {
    const known = new Set(workflowSymbolChoices.map((choice) => choice[0]));
    const options = workflowSymbolChoices.map((choice) => '<option value="' + escapeHtml(choice[0]) + '">' + escapeHtml(choice[1]) + "</option>");
    if (savedValue && !known.has(String(savedValue))) options.splice(1, 0, '<option value="' + escapeHtml(savedValue) + '">Saved symbol: ' + escapeHtml(savedValue) + "</option>");
    return options.join("");
  }

  function stepSelect(label, name, options) {
    return '<label class="field"><span>' + label + '</span><select data-step-param="' + name + '">' + options + "</select></label>";
  }

  function stepField(label, name, placeholder, wide) {
    return '<label class="field' + (wide ? " wide" : "") + '"><span>' + label + '</span><input data-step-param="' + name + '" type="text" placeholder="' + escapeHtml(placeholder) + '" autocomplete="off" spellcheck="false" /></label>';
  }

  function stepTypeOptions(selected) {
    const options = ['<option value="market_snapshot">Market snapshot</option>', '<option value="crypto_news">Crypto news</option>', '<option value="x_posts">X posts (disabled)</option>', '<option value="pattern_context">Pattern context</option>', '<option value="lessons">Candidate lessons</option>', '<option value="agent_instruction">Agent instruction</option>'];
    return options.join("");
  }

  function stepFields(type, parameters) {
    const values = parameters || {};
    if (type === "market_snapshot") {
      return [
        stepSelect(
          "Symbol or context variable",
          "symbol",
          workflowSymbolOptions(values.symbol)
        ),
        stepSelect("Market type", "market_type", '<option value="spot">Spot</option><option value="perpetual">Perpetual</option>'),
        stepField("Timeframe", "timeframe", "15m"),
        stepField("Candle limit", "limit", "20"),
      ].join("");
    }
    if (type === "crypto_news" || type === "x_posts") return [stepField("Symbol <em>optional</em>", "symbol", "$context.symbol or BTC/USDT"), stepField("Lookback minutes", "lookback_minutes", "1440"), stepField("Result limit", "limit", "20")].join("");
    if (type === "pattern_context") return [stepField("Pattern or context variable", "pattern_id", "$context.pattern_id"), stepField("Symbol <em>optional</em>", "symbol", "$context.symbol"), stepSelect("Market type <em>optional</em>", "market_type", '<option value="">Any market</option><option value="spot">Spot</option><option value="perpetual">Perpetual</option>'), stepSelect("Side <em>optional</em>", "side", '<option value="">Any side</option><option value="long">Long</option><option value="short">Short</option>'), stepField("Case limit", "limit", "50")].join("");
    if (type === "lessons") return [stepField("Pattern <em>optional</em>", "pattern_id", "$context.pattern_id"), stepField("Symbol <em>optional</em>", "symbol", "$context.symbol"), stepSelect("Market type <em>optional</em>", "market_type", '<option value="">Any market</option><option value="spot">Spot</option><option value="perpetual">Perpetual</option>'), stepSelect("Side <em>optional</em>", "side", '<option value="">Any side</option><option value="long">Long</option><option value="short">Short</option>'), stepField("Lesson limit", "limit", "100")].join("");
    return '<label class="field wide"><span>Instruction for the agent</span><textarea data-step-param="instruction" rows="3" placeholder="Explain how the agent should interpret the outputs."></textarea></label>';
  }

  function renderWorkflowStepFields(row, parameters) {
    const fields = row.querySelector("[data-step-fields]");
    fields.innerHTML = stepFields(row.querySelector("[data-step-type]").value, parameters);
    Object.entries(parameters || {}).forEach(([name, value]) => {
      const input = fields.querySelector('[data-step-param="' + name + '"]');
      if (input) input.value = String(value);
    });
  }

  function renumberWorkflowSteps(phase) {
    const container = phase === "before_operation" ? elements.beforeSteps : elements.afterSteps;
    $$("[data-step-row]", container).forEach((row, index) => { row.querySelector(".step-order").textContent = String(index + 1).padStart(2, "0"); });
  }

  function addWorkflowStep(phase, step) {
    const container = phase === "before_operation" ? elements.beforeSteps : elements.afterSteps;
    const row = document.createElement("div");
    row.className = "workflow-step";
    row.dataset.stepRow = "";
    if (step && step.step_id) row.dataset.stepId = step.step_id;
    row.innerHTML = '<div class="step-row-header"><span class="step-order">01</span><label class="field"><span>Step name</span><input data-step-name type="text" maxlength="120" required /></label><select class="step-type" data-step-type aria-label="Step type">' + stepTypeOptions(step && step.type) + '</select><button class="remove-step" type="button">Remove</button></div><div class="step-fields" data-step-fields></div>';
    const type = (step && step.type) || "market_snapshot";
    row.querySelector("[data-step-type]").value = type;
    row.querySelector("[data-step-name]").value = (step && step.name) || workflowStepLabels[type];
    renderWorkflowStepFields(row, step && step.parameters);
    row.querySelector("[data-step-type]").addEventListener("change", () => {
      const selected = row.querySelector("[data-step-type]").value;
      row.querySelector("[data-step-name]").value = workflowStepLabels[selected];
      renderWorkflowStepFields(row);
    });
    row.querySelector(".remove-step").addEventListener("click", () => { row.remove(); renumberWorkflowSteps(phase); });
    container.appendChild(row);
    renumberWorkflowSteps(phase);
  }

  function collectWorkflowSteps(container) {
    return $$('[data-step-row]', container).map((row) => {
      const type = row.querySelector("[data-step-type]").value;
      const parameters = {};
      $$('[data-step-param]', row).forEach((input) => { if (input.value.trim()) parameters[input.dataset.stepParam] = input.value.trim(); });
      const step = { name: row.querySelector("[data-step-name]").value.trim() || workflowStepLabels[type], type, parameters };
      if (row.dataset.stepId) step.step_id = row.dataset.stepId;
      return step;
    });
  }

  function resetWorkflowEditor() {
    if (!elements.workflowForm) return;
    elements.workflowForm.reset();
    elements.workflowId.value = "";
    elements.workflowFormTitle.textContent = "Create workflow";
    elements.workflowFormMode.textContent = "new";
    elements.saveWorkflow.textContent = "Save workflow";
    elements.beforeSteps.replaceChildren();
    elements.afterSteps.replaceChildren();
    addWorkflowStep("before_operation");
    addWorkflowStep("after_operation");
  }

  function renderWorkflows() {
    elements.workflowCount.textContent = state.workflows.length;
    if (!state.workflows.length) {
      elements.workflowList.innerHTML = '<div class="empty-state"><span class="empty-glyph">□</span><strong>No workflows yet</strong><p>Create a playbook so agents can select it by name.</p></div>';
      return;
    }
    elements.workflowList.innerHTML = state.workflows.map((workflow) => '<article class="resource-item" data-workflow-id="' + escapeHtml(workflow.workflow_id) + '"><div class="resource-item-top"><div><strong>' + escapeHtml(workflow.name) + '</strong><small>' + escapeHtml(workflow.workflow_id) + " · v" + escapeHtml(workflow.version) + '</small></div><span class="status-badge ' + (workflow.enabled ? "status-good" : "status-muted") + '">' + (workflow.enabled ? "enabled" : "disabled") + '</span></div><p>' + escapeHtml(workflow.description || "No description provided.") + '</p><div class="resource-meta"><span>' + workflow.before_steps.length + " pre · " + workflow.after_steps.length + ' post steps</span><span>' + escapeHtml(formatDate(workflow.updated_at)) + '</span></div><div class="resource-actions"><button class="text-link" type="button" data-edit-workflow="' + escapeHtml(workflow.workflow_id) + '">Edit</button><button class="text-link danger-link" type="button" data-delete-workflow="' + escapeHtml(workflow.workflow_id) + '">Delete</button></div></article>').join("");
  }

  async function loadWorkflows() {
    state.workflows = await api("/api/v1/workflows");
    renderWorkflows();
    populateResourceSelects();
  }

  async function saveWorkflow(event) {
    event.preventDefault();
    const workflowId = elements.workflowId.value.trim();
    const beforeSteps = collectWorkflowSteps(elements.beforeSteps);
    const afterSteps = collectWorkflowSteps(elements.afterSteps);
    if (!beforeSteps.length && !afterSteps.length) { setAlert("Add at least one workflow step.", "error"); return; }
    const payload = { name: elements.workflowName.value.trim(), description: elements.workflowDescription.value.trim(), before_steps: beforeSteps, after_steps: afterSteps, enabled: elements.workflowEnabled.checked };
    setBusy(elements.saveWorkflow, true, "Saving…");
    try {
      await api(workflowId ? "/api/v1/workflows/" + encodeURIComponent(workflowId) : "/api/v1/workflows", { method: workflowId ? "PUT" : "POST", body: JSON.stringify(payload) });
      await loadWorkflows();
      resetWorkflowEditor();
      setAlert(workflowId ? "Workflow updated." : "Workflow created.", "success");
    } catch (error) { setAlert(error.message, "error"); }
    finally { setBusy(elements.saveWorkflow, false); }
  }

  function editWorkflow(id) {
    const workflow = state.workflows.find((item) => item.workflow_id === id);
    if (!workflow) return;
    elements.workflowId.value = workflow.workflow_id;
    elements.workflowName.value = workflow.name;
    elements.workflowDescription.value = workflow.description;
    elements.workflowEnabled.checked = workflow.enabled;
    elements.workflowFormTitle.textContent = "Edit workflow";
    elements.workflowFormMode.textContent = "editing";
    elements.saveWorkflow.textContent = "Update workflow";
    elements.beforeSteps.replaceChildren();
    elements.afterSteps.replaceChildren();
    workflow.before_steps.forEach((step) => addWorkflowStep("before_operation", step));
    workflow.after_steps.forEach((step) => addWorkflowStep("after_operation", step));
    showView("automations");
    elements.workflowName.focus();
  }

  async function deleteWorkflow(id) {
    const workflow = state.workflows.find((item) => item.workflow_id === id);
    if (!workflow || !window.confirm("Delete the workflow " + workflow.name + "?")) return;
    try {
      await api("/api/v1/workflows/" + encodeURIComponent(id), { method: "DELETE" });
      if (elements.workflowId.value === id) resetWorkflowEditor();
      await loadWorkflows();
      setAlert("Workflow deleted.", "success");
    } catch (error) { setAlert(error.message, "error"); }
  }

  async function runWorkflow(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button[type=submit]");
    const payload = { workflow_name: formValue(form, "workflow_name"), phase: formValue(form, "phase") };
    ["operation_id", "symbol", "pattern_id", "execution_plan_run_id"].forEach((name) => { const value = formValue(form, name); if (value) payload[name] = value; });
    setBusy(button, true, "Running…");
    try { const result = await api("/api/v1/workflows/run", { method: "POST", body: JSON.stringify(payload) }); renderWorkflowRun(result); setAlert("Workflow phase completed.", "success"); }
    catch (error) { renderResultError($("#workflow-run-output"), error); setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  function renderWorkflowRun(result) {
    const steps = (result.steps || []).map((step) => '<li><span class="status-dot-small ' + escapeHtml(step.status) + '"></span><div><strong>' + escapeHtml(step.name) + '</strong><small>' + escapeHtml(step.status) + (step.error ? " · " + escapeHtml(step.error) : "") + '</small></div></li>').join("");
    $("#workflow-run-output").className = "result-block";
    $("#workflow-run-output").innerHTML = '<div class="result-summary"><strong>' + escapeHtml(result.status) + '</strong><span>' + escapeHtml(result.workflow_name) + " · " + escapeHtml(result.phase) + '</span></div><ul class="result-list">' + steps + '</ul><details><summary>Run metadata</summary><pre>' + escapeHtml(JSON.stringify(result, null, 2)) + "</pre></details>";
  }

  // Execution plans -------------------------------------------------------
  const planActions = { read_market_snapshot: "Read market snapshot", read_crypto_news: "Read crypto news", read_derivatives_positioning: "Read derivatives positioning", read_pattern_context: "Read pattern context", read_lessons: "Read lessons", run_before_workflow: "Run before workflow", agent_decision: "Agent decision", propose_paper_trade: "Propose paper trade", execute_paper_trade: "Execute paper trade", monitor_paper_trade: "Monitor paper trade", record_operation_outcome: "Record operation outcome", record_agent_postmortem: "Record agent postmortem", run_after_workflow: "Run after workflow", review_metrics: "Review metrics" };

  function planActionOptions(selected) {
    return Object.entries(planActions).map(([value, label]) => '<option value="' + value + '"' + (value === selected ? " selected" : "") + ">" + label + "</option>").join("");
  }

  function addPlanStep(step) {
    const row = document.createElement("div");
    row.className = "plan-step-row";
    row.dataset.planStep = "";
    if (step && step.step_id) row.dataset.stepId = step.step_id;
    row.innerHTML = '<span class="step-order">01</span><label class="field"><span>Name</span><input data-plan-step-name maxlength="120" required /></label><label class="field"><span>Action</span><select data-plan-step-action>' + planActionOptions(step && step.action) + '</select></label><label class="field plan-instruction"><span>Instructions</span><input data-plan-step-instructions minlength="10" maxlength="2000" required /></label><label class="step-required"><input data-plan-step-required type="checkbox" checked /> Required</label><button class="remove-step" type="button">Remove</button>';
    const action = (step && step.action) || "agent_decision";
    row.querySelector("[data-plan-step-action]").value = action;
    row.querySelector("[data-plan-step-name]").value = (step && step.name) || planActions[action];
    row.querySelector("[data-plan-step-instructions]").value = (step && step.instructions) || "Explain the evidence and the safe next action.";
    if (step && step.required === false) row.querySelector("[data-plan-step-required]").checked = false;
    row.querySelector("[data-plan-step-action]").addEventListener("change", () => { const value = row.querySelector("[data-plan-step-action]").value; row.querySelector("[data-plan-step-name]").value = planActions[value]; });
    row.querySelector(".remove-step").addEventListener("click", () => { row.remove(); renumberPlanSteps(); });
    elements.planSteps.appendChild(row);
    renumberPlanSteps();
  }

  function renumberPlanSteps() { $$('[data-plan-step]', elements.planSteps).forEach((row, index) => { row.querySelector(".step-order").textContent = String(index + 1).padStart(2, "0"); }); }

  function collectPlanSteps() {
    return $$('[data-plan-step]', elements.planSteps).map((row) => {
      const step = { name: row.querySelector("[data-plan-step-name]").value.trim(), action: row.querySelector("[data-plan-step-action]").value, instructions: row.querySelector("[data-plan-step-instructions]").value.trim(), required: row.querySelector("[data-plan-step-required]").checked };
      if (row.dataset.stepId) step.step_id = row.dataset.stepId;
      return step;
    });
  }

  function renderPlanSymbolOptions() {
    if (!elements.planSymbolOptions || elements.planSymbolOptions.dataset.ready) return;
    elements.planSymbolOptions.innerHTML = '<legend>Symbols the AI may use</legend>' + planSymbolChoices.map((symbol) => {
      const id = "plan-symbol-" + canonicalSymbol(symbol).toLowerCase();
      return '<label for="' + id + '"><input id="' + id + '" type="checkbox" name="plan_symbols" value="' + escapeHtml(symbol) + '" data-plan-symbol /> <span>' + escapeHtml(symbol) + '</span></label>';
    }).join("");
    $$('[data-plan-symbol]', elements.planSymbolOptions).forEach((input) => input.addEventListener("change", () => {
      const custom = commaList(formValue(elements.planForm, "symbols"));
      const known = new Set(planSymbolChoices.map(canonicalSymbol));
      selectValue(elements.planForm, "symbols", custom.filter((symbol) => !known.has(canonicalSymbol(symbol))).join(", "));
    }));
    elements.planSymbolOptions.dataset.ready = "true";
  }

  function setPlanSymbols(symbols) {
    renderPlanSymbolOptions();
    const selected = new Set((symbols || []).map(canonicalSymbol));
    const known = new Set(planSymbolChoices.map(canonicalSymbol));
    $$('[data-plan-symbol]', elements.planSymbolOptions).forEach((input) => { input.checked = selected.has(canonicalSymbol(input.value)); });
    selectValue(elements.planForm, "symbols", (symbols || []).filter((symbol) => !known.has(canonicalSymbol(symbol))).join(", "));
  }

  function selectedPlanSymbols() {
    const selected = $$('[data-plan-symbol]:checked', elements.planSymbolOptions).map((input) => input.value);
    return Array.from(new Map([...selected, ...commaList(formValue(elements.planForm, "symbols"))].map((symbol) => [canonicalSymbol(symbol), symbol])).values());
  }

  function syncPlanSymbolChoicesFromInput() {
    const values = commaList(formValue(elements.planForm, "symbols"));
    const known = new Set(planSymbolChoices.map(canonicalSymbol));
    const selected = new Set(values.map(canonicalSymbol));
    $$('[data-plan-symbol]', elements.planSymbolOptions).forEach((input) => { input.checked = selected.has(canonicalSymbol(input.value)); });
    selectValue(elements.planForm, "symbols", values.filter((symbol) => !known.has(canonicalSymbol(symbol))).join(", "));
  }

  function resetPlanEditor() {
    if (!elements.planForm) return;
    renderPlanSymbolOptions();
    elements.planForm.reset();
    elements.planId.value = "";
    elements.planFormTitle.textContent = "Create execution plan";
    elements.planFormMode.textContent = "new";
    elements.savePlan.textContent = "Save plan";
    setPlanSymbols(["BTC/USDT", "ETH/USDT"]);
    elements.planSteps.replaceChildren();
    addPlanStep({ action: "read_market_snapshot", name: "Read market", instructions: "Read the allowed market snapshot before deciding." });
    addPlanStep({ action: "read_crypto_news", name: "Read RSS", instructions: "Read configured RSS news as context, never as an order trigger." });
    addPlanStep({ action: "agent_decision", name: "Agent decision", instructions: "Explain the evidence before proposing a paper trade." });
  }

  function renderPlans() {
    elements.planCount.textContent = state.plans.length;
    if (!state.plans.length) {
      elements.planList.innerHTML = '<div class="empty-state"><span class="empty-glyph">□</span><strong>No execution plans</strong><p>Build the first paper perimeter below.</p></div>';
      return;
    }
    elements.planList.innerHTML = state.plans.map((plan) => '<article class="resource-item" data-plan-id="' + escapeHtml(plan.plan_id) + '"><div class="resource-item-top"><div><strong>' + escapeHtml(plan.name) + '</strong><small>' + escapeHtml(plan.plan_id) + " · v" + escapeHtml(plan.version) + '</small></div><span class="status-badge status-' + escapeHtml(plan.status) + '">' + escapeHtml(plan.status) + '</span></div><p>' + escapeHtml(plan.objective) + '</p><div class="resource-meta"><span>' + escapeHtml(plan.symbols.join(", ")) + '</span><span>' + escapeHtml(plan.market_type) + " · " + escapeHtml(plan.timeframe) + '</span></div><div class="resource-actions"><button class="text-link" type="button" data-run-plan="' + escapeHtml(plan.name) + '">Preflight</button><button class="text-link" type="button" data-edit-plan="' + escapeHtml(plan.plan_id) + '">Edit</button><button class="text-link danger-link" type="button" data-delete-plan="' + escapeHtml(plan.plan_id) + '">Delete</button></div></article>').join("");
  }

  function selectValue(form, name, value) {
    const field = form.elements[name];
    if (field && value !== undefined && value !== null) field.value = value;
  }

  function checkedValues(name, root) { return $$('input[name="' + name + '"]:checked', root).map((input) => input.value); }

  function planPayloadFromForm() {
    const form = elements.planForm;
    const payload = { name: formValue(form, "name"), description: formValue(form, "description"), objective: formValue(form, "objective"), strategy_version: formValue(form, "strategy_version"), pattern_id: formValue(form, "pattern_id"), mode: "paper", capital_quote: Number(formValue(form, "capital_quote")), max_trade_notional_quote: Number(formValue(form, "max_trade_notional_quote")), risk_per_trade_quote: Number(formValue(form, "risk_per_trade_quote")), max_daily_loss_quote: Number(formValue(form, "max_daily_loss_quote")), minimum_net_reward_risk_ratio: Number(formValue(form, "minimum_net_reward_risk_ratio")), exploratory_trades_enabled: form.elements.exploratory_trades_enabled.checked, exploratory_minimum_signal_score: Number(formValue(form, "exploratory_minimum_signal_score")), exploratory_risk_fraction: Number(formValue(form, "exploratory_risk_fraction")), max_open_operations: Number(formValue(form, "max_open_operations")), max_duration_minutes: Number(formValue(form, "max_duration_minutes")), target_operations: Number(formValue(form, "target_operations")), symbols: selectedPlanSymbols(), market_type: formValue(form, "market_type"), timeframe: formValue(form, "timeframe"), allowed_sides: checkedValues("allowed_sides", form), data_sources: checkedValues("data_sources", form), before_workflow_name: formValue(form, "before_workflow_name") || null, after_workflow_name: formValue(form, "after_workflow_name") || null, entry_rules: lines(formValue(form, "entry_rules")), exit_rules: lines(formValue(form, "exit_rules")), risk_rules: lines(formValue(form, "risk_rules")), evaluation_metrics: commaList(formValue(form, "evaluation_metrics")), steps: collectPlanSteps(), status: formValue(form, "status") };
    const score = formValue(form, "minimum_signal_score");
    if (score) payload.minimum_signal_score = Number(score);
    return payload;
  }

  function editPlan(id) {
    const plan = state.plans.find((item) => item.plan_id === id);
    if (!plan) return;
    renderPlanSymbolOptions();
    elements.planForm.reset();
    elements.planId.value = plan.plan_id;
    Object.entries(plan).forEach(([name, value]) => { if (elements.planForm.elements[name] && !["plan_id", "steps", "account_id", "plan_id", "version", "created_at", "updated_at"].includes(name) && typeof value !== "object") selectValue(elements.planForm, name, value); });
    setPlanSymbols(plan.symbols);
    selectValue(elements.planForm, "before_workflow_name", plan.before_workflow_name || "");
    selectValue(elements.planForm, "after_workflow_name", plan.after_workflow_name || "");
    selectValue(elements.planForm, "evaluation_metrics", plan.evaluation_metrics.join(", "));
    selectValue(elements.planForm, "entry_rules", plan.entry_rules.join("\n"));
    selectValue(elements.planForm, "exit_rules", plan.exit_rules.join("\n"));
    selectValue(elements.planForm, "risk_rules", plan.risk_rules.join("\n"));
    if (elements.planForm.elements.exploratory_trades_enabled) elements.planForm.elements.exploratory_trades_enabled.checked = plan.exploratory_trades_enabled;
    $$('input[name="allowed_sides"]', elements.planForm).forEach((input) => { input.checked = plan.allowed_sides.includes(input.value); });
    $$('input[name="data_sources"]', elements.planForm).forEach((input) => { input.checked = plan.data_sources.includes(input.value); });
    elements.planFormTitle.textContent = "Edit execution plan";
    elements.planFormMode.textContent = "editing";
    elements.savePlan.textContent = "Update plan";
    elements.planSteps.replaceChildren();
    plan.steps.forEach((step) => addPlanStep(step));
    showView("strategy");
    $("#plan-editor").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function savePlan(event) {
    event.preventDefault();
    const planId = elements.planId.value.trim();
    const payload = planPayloadFromForm();
    if (!payload.symbols.length) { setAlert("Select at least one symbol for the AI to use.", "error"); return; }
    if (!payload.allowed_sides.length || !payload.data_sources.length || !payload.steps.length) { setAlert("A plan needs sides, a data source and at least one step.", "error"); return; }
    setBusy(elements.savePlan, true, "Saving…");
    try {
      await api(planId ? "/api/v1/execution-plans/" + encodeURIComponent(planId) : "/api/v1/execution-plans", { method: planId ? "PUT" : "POST", body: JSON.stringify(payload) });
      await loadPlans();
      resetPlanEditor();
      setAlert(planId ? "Execution plan updated." : "Execution plan created.", "success");
    } catch (error) { setAlert(error.message, "error"); }
    finally { setBusy(elements.savePlan, false); }
  }

  async function loadPlans() { state.plans = await api("/api/v1/execution-plans"); renderPlans(); populateResourceSelects(); renderKpis(); }

  async function deletePlan(id) {
    const plan = state.plans.find((item) => item.plan_id === id);
    if (!plan || !window.confirm("Delete the execution plan " + plan.name + "?")) return;
    try { await api("/api/v1/execution-plans/" + encodeURIComponent(id), { method: "DELETE" }); await loadPlans(); setAlert("Execution plan deleted.", "success"); }
    catch (error) { setAlert(error.message, "error"); }
  }

  function renderPlanRun(result) {
    const blockers = (result.blockers || []).map((item) => "<li>" + escapeHtml(item) + "</li>").join("");
    const preflight = (result.preflight || []).map((item) => "<li>" + escapeHtml(item) + "</li>").join("");
    const next = (result.next_steps || []).map((item) => "<li><strong>" + escapeHtml(item.name) + "</strong><small>" + escapeHtml(item.action) + "</small></li>").join("");
    elements.planRunOutput.className = "result-block";
    elements.planRunOutput.innerHTML = '<div class="result-summary"><strong class="status-' + escapeHtml(result.status) + '">' + escapeHtml(result.status) + '</strong><span>' + escapeHtml(result.run_id) + " · " + escapeHtml(result.selected_symbol) + '</span></div>' + (blockers ? '<div class="result-callout error"><strong>Blockers</strong><ul>' + blockers + '</ul></div>' : '<div class="result-callout success"><strong>Preflight passed</strong><ul>' + preflight + '</ul></div>') + (next ? '<div class="next-steps"><strong>Next steps</strong><ol>' + next + '</ol></div>' : "") + '<div class="result-actions"><button class="text-link" type="button" data-load-receipt="' + escapeHtml(result.run_id) + '">Load durable receipt</button><button class="text-link" type="button" data-use-run="' + escapeHtml(result.run_id) + '" data-use-plan="' + escapeHtml(result.plan.name) + '">Use for evaluation</button></div><details><summary>Run metadata</summary><pre>' + escapeHtml(JSON.stringify(result, null, 2)) + "</pre></details>";
  }

  function renderReceipt(receipt) {
    elements.planRunOutput.className = "result-block";
    elements.planRunOutput.innerHTML = '<div class="result-summary"><strong class="status-' + escapeHtml(receipt.status) + '">' + escapeHtml(receipt.status) + '</strong><span>' + escapeHtml(receipt.run_id) + " · " + escapeHtml(receipt.plan_name) + '</span></div><div class="receipt-meta"><span>' + receipt.receipts.length + " / " + receipt.required_reads.length + ' reads receipted</span><span>expires ' + escapeHtml(formatDate(receipt.expires_at)) + '</span></div><ul class="receipt-list">' + (receipt.receipts.length ? receipt.receipts.map((item) => '<li><span class="status-dot-small completed"></span><div><strong>' + escapeHtml(item.read_type.replaceAll("_", " ")) + '</strong><small>' + escapeHtml(item.symbol) + " · " + escapeHtml(formatDate(item.recorded_at)) + '</small></div></li>').join("") : '<li class="muted-line">No successful reads recorded yet.</li>') + "</ul><details><summary>Receipt JSON</summary><pre>" + escapeHtml(JSON.stringify(receipt, null, 2)) + "</pre></details>";
  }

  async function runPlan(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button[type=submit]");
    const payload = { plan_name: formValue(form, "plan_name") };
    ["symbol", "duration_minutes", "target_operations"].forEach((name) => { const value = formValue(form, name); if (value) payload[name] = name === "symbol" ? value : Number(value); });
    setBusy(button, true, "Checking…");
    try { const result = await api("/api/v1/execution-plans/run", { method: "POST", body: JSON.stringify(payload) }); renderPlanRun(result); setAlert("Plan preflight completed.", result.status === "ready" ? "success" : "error"); }
    catch (error) { renderResultError(elements.planRunOutput, error); setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  async function loadReceipt(runId) { try { renderReceipt(await api("/api/v1/execution-plan-runs/" + encodeURIComponent(runId))); } catch (error) { setAlert(error.message, "error"); } }

  function useRunForEvaluation(runId, planName) { elements.strategyRunId.value = runId; elements.strategyPlanName.value = planName; showView("strategy"); $("#strategy-form").scrollIntoView({ behavior: "smooth", block: "center" }); setAlert("Run receipt attached to the evaluator.", "success"); }

  const strategyReasonLabels = {
    no_eligible_candidate: "No eligible setup met the strategy rules.",
    operation_opened: "The best eligible setup passed the paper risk checks.",
    risk_rejected: "A candidate existed, but the server risk engine rejected it.",
    execution_plan_run_id_required: "The cycle needs a valid execution-plan run receipt.",
    insufficient_closed_candles: "There were not enough closed candles to evaluate the indicators.",
    indicators_unavailable: "The required technical indicators could not be calculated.",
    derivatives_data_missing: "Derivatives data was unavailable or neutral.",
    news_data_missing_or_neutral: "News data was unavailable or neutral.",
    fundamental_proxy_not_available: "No external event context was available.",
    long_technical_setup_not_eligible: "The long technical setup was not eligible.",
    short_technical_setup_not_eligible: "The short technical setup was not eligible.",
    long_score_below_exploratory_threshold: "The long score was below the exploratory threshold.",
    short_score_below_exploratory_threshold: "The short score was below the exploratory threshold.",
    exploratory_trades_disabled: "Exploratory trades are disabled for this plan.",
    spread_above_limit: "The market spread was above the configured limit.",
    execution_plan_run_not_active: "The execution-plan run is no longer active.",
    execution_plan_run_expired: "The execution-plan run expired before the cycle completed.",
    execution_plan_run_not_found: "The execution-plan run could not be found.",
    strategy_market_data_unavailable: "No usable market snapshot was available.",
  };

  function strategyReasonLabel(value) {
    const key = String(value || "");
    if (strategyReasonLabels[key]) return strategyReasonLabels[key];
    const readable = key.replaceAll("_", " ").replace(/^./, (character) => character.toUpperCase());
    return readable || "The strategy did not open a paper operation.";
  }

  function cycleDecisionReasons(record) {
    const reasons = [];
    if (record.reason) reasons.push(strategyReasonLabel(record.reason));
    const evaluation = record.evaluation || {};
    if (evaluation.market_regime) reasons.push("Market regime: " + strategyReasonLabel(evaluation.market_regime));
    (evaluation.rejection_reasons || []).forEach((reason) => reasons.push(strategyReasonLabel(reason)));
    (evaluation.data_quality || []).forEach((reason) => reasons.push("Data quality: " + strategyReasonLabel(reason)));
    if (record.risk_check && (record.risk_check.reasons || []).length) {
      record.risk_check.reasons.forEach((reason) => reasons.push("Risk: " + strategyReasonLabel(reason)));
    }
    if (!reasons.length) reasons.push("The cycle completed without opening a paper operation.");
    return Array.from(new Set(reasons));
  }

  function renderStrategyCycles() {
    const cycles = state.strategyCycles || [];
    elements.cycleCount.textContent = cycles.length;
    if (!cycles.length) {
      elements.strategyCycleHistory.innerHTML = '<div class="empty-state compact"><span class="empty-glyph">◌</span><strong>No cycles recorded</strong><p>Run a strategy cycle to keep its decision evidence.</p></div>';
      return;
    }
    elements.strategyCycleHistory.innerHTML = cycles.map((cycle) => {
      const opened = Boolean(cycle.executed);
      const reasons = opened ? [] : cycleDecisionReasons(cycle);
      const reasonMarkup = reasons.length ? '<ul class="cycle-reasons">' + reasons.map((reason) => "<li>" + escapeHtml(reason) + "</li>").join("") + "</ul>" : "";
      const operation = cycle.operation_id ? '<span class="cycle-operation">' + escapeHtml(cycle.operation_id) + "</span>" : "";
      return '<article class="cycle-record ' + (opened ? "is-opened" : "is-skipped") + '"><div class="cycle-record-top"><div><strong>' + escapeHtml(opened ? "Paper operation opened" : "No operation opened") + '</strong><small>' + escapeHtml(cycle.plan_name) + " · " + escapeHtml(cycle.symbol) + " · " + escapeHtml(formatDate(cycle.created_at)) + '</small></div><span class="status-badge ' + (opened ? "status-good" : "status-muted") + '">' + escapeHtml(opened ? "opened" : "skipped") + '</span></div>' + reasonMarkup + (operation ? '<div class="cycle-record-meta">' + operation + '</div>' : "") + '<details><summary>Cycle evidence</summary><pre>' + escapeHtml(JSON.stringify(cycle, null, 2)) + "</pre></details></article>";
    }).join("");
  }

  async function refreshStrategyCycles() {
    state.strategyCycles = await api("/api/v1/strategy/cycles?limit=100");
    renderStrategyCycles();
  }

  function renderStrategyResult(result) {
    if (result && Object.prototype.hasOwnProperty.call(result, "executed")) {
      const evaluation = result.evaluation;
      elements.strategyOutput.className = "strategy-output";
      const decision = result.executed ? "Paper cycle executed" : "No paper operation opened";
      const evidence = result.executed ? "" : '<div class="cycle-decision no-trade"><strong>Why this cycle stayed flat</strong><ul class="cycle-reasons">' + cycleDecisionReasons(result).map((reason) => "<li>" + escapeHtml(reason) + "</li>").join("") + "</ul><p>This decision is saved in cycle history.</p></div>";
      elements.strategyOutput.innerHTML = '<div class="result-summary"><strong class="' + (result.executed ? "text-good" : "text-amber") + '">' + decision + '</strong><span>' + escapeHtml(result.reason || (result.operation && result.operation.operation_id) || "") + '</span></div>' + evidence + (evaluation ? renderEvaluationMarkup(evaluation) : "") + '<details><summary>Cycle response</summary><pre>' + escapeHtml(JSON.stringify(result, null, 2)) + "</pre></details>";
      return;
    }
    elements.strategyOutput.className = "strategy-output";
    elements.strategyOutput.innerHTML = renderEvaluationMarkup(result);
  }

  function scoreClass(value) {
    const score = Number(value);
    return score >= 0.7 ? "high" : score >= 0.5 ? "medium" : "low";
  }

  function renderEvaluationMarkup(evaluation) {
    const candidates = (evaluation.candidates || []).map((candidate) => '<article class="candidate-card"><div class="candidate-top"><strong>' + escapeHtml(candidate.side) + " · " + escapeHtml(candidate.signal_tier) + '</strong><span>' + escapeHtml(formatPercent(candidate.signal_score * 100)) + '</span></div><div class="candidate-price"><strong>' + escapeHtml(formatNumber(candidate.entry_price, 4)) + '</strong><span>entry</span><strong>' + escapeHtml(formatNumber(candidate.take_profit_price, 4)) + '</strong><span>target</span></div><div class="score-bars"><span class="score-bar score-' + scoreClass(candidate.technical_score) + '">technical ' + escapeHtml(formatPercent(candidate.technical_score * 100)) + '</span><span class="score-bar score-' + scoreClass(candidate.derivatives_score) + '">derivatives ' + escapeHtml(formatPercent(candidate.derivatives_score * 100)) + '</span><span class="score-bar score-' + scoreClass(candidate.sentiment_score) + '">sentiment ' + escapeHtml(formatPercent(candidate.sentiment_score * 100)) + '</span></div><ul class="reason-list">' + (candidate.reasons || []).map((reason) => "<li>" + escapeHtml(reason) + "</li>").join("") + '</ul></article>').join("");
    return '<div class="evaluation-head"><div><strong>' + escapeHtml(evaluation.symbol) + '</strong><span>' + escapeHtml(evaluation.market_regime) + " · " + escapeHtml(evaluation.timeframe) + '</span></div><strong class="evaluation-price">' + escapeHtml(formatNumber(evaluation.price, 4)) + '</strong></div>' + (candidates ? '<div class="candidate-grid">' + candidates + '</div>' : '<p class="muted-line">No eligible candidates.</p>') + '<div class="metric-grid">' + Object.entries(evaluation.metrics || {}).map(([key, value]) => '<div><span>' + escapeHtml(key.replaceAll("_", " ")) + '</span><strong>' + escapeHtml(value) + '</strong></div>').join("") + '</div>' + ((evaluation.rejection_reasons || []).length ? '<div class="result-callout error"><strong>Rejected conditions</strong><ul>' + evaluation.rejection_reasons.map((item) => "<li>" + escapeHtml(item) + "</li>").join("") + "</ul></div>" : "") + ((evaluation.data_quality || []).length ? '<div class="data-quality"><strong>Data quality</strong><span>' + escapeHtml(evaluation.data_quality.join(" · ")) + "</span></div>" : "") + '<details><summary>Evaluation JSON</summary><pre>' + escapeHtml(JSON.stringify(evaluation, null, 2)) + "</pre></details>";
  }

  async function evaluateStrategy(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = event.submitter || form.querySelector("button[type=submit]");
    const params = new URLSearchParams({ plan_name: formValue(form, "plan_name"), symbol: formValue(form, "symbol"), execution_plan_run_id: formValue(form, "execution_plan_run_id"), lookback_minutes: formValue(form, "lookback_minutes"), candle_limit: formValue(form, "candle_limit") });
    setBusy(button, true, "Evaluating…");
    try { renderStrategyResult(await api("/api/v1/strategy/evaluate?" + params.toString())); setAlert("Strategy evaluation completed.", "success"); }
    catch (error) { renderResultError(elements.strategyOutput, error); setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  async function runStrategyCycle() {
    const form = $("#strategy-form");
    const button = $("#run-cycle");
    const params = new URLSearchParams({ plan_name: formValue(form, "plan_name"), symbol: formValue(form, "symbol"), execution_plan_run_id: formValue(form, "execution_plan_run_id") });
    setBusy(button, true, "Cycling…");
    try {
      const result = await api("/api/v1/strategy/cycle?" + params.toString(), { method: "POST" });
      renderStrategyResult(result);
      await refreshStrategyCycles();
      setAlert("Strategy cycle completed.", "success");
    }
    catch (error) { renderResultError(elements.strategyOutput, error); setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  // Operations ------------------------------------------------------------
  function newIdempotencyKey() { return "ui-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10); }

  function addPatternRow(pattern) {
    const row = document.createElement("div");
    row.className = "pattern-row";
    row.dataset.patternRow = "";
    row.innerHTML = '<label class="field"><span>Pattern ID</span><input data-pattern-id required pattern="[a-z0-9]+(?:_[a-z0-9]+)*" /></label><label class="field"><span>Role</span><select data-pattern-role><option value="primary">Primary</option><option value="secondary">Secondary</option></select></label><label class="field"><span>Version</span><input data-pattern-version type="number" min="1" value="1" required /></label><label class="field"><span>Confidence</span><input data-pattern-confidence type="number" min="0" max="1" step="0.01" value="0.7" required /></label><label class="field pattern-evidence"><span>Evidence refs <small>comma separated</small></span><input data-pattern-evidence /></label><button class="remove-step" type="button">Remove</button>';
    row.querySelector("[data-pattern-id]").value = (pattern && pattern.pattern_id) || "momentum_breakout";
    row.querySelector("[data-pattern-role]").value = (pattern && pattern.role) || "primary";
    row.querySelector("[data-pattern-version]").value = (pattern && pattern.pattern_version) || 1;
    row.querySelector("[data-pattern-confidence]").value = (pattern && pattern.confidence) || 0.7;
    row.querySelector("[data-pattern-evidence]").value = pattern && pattern.evidence_refs ? pattern.evidence_refs.join(", ") : "operator-observation";
    row.querySelector(".remove-step").addEventListener("click", () => row.remove());
    elements.patternRows.appendChild(row);
  }

  function ensureProposalPlanRunField() {
    if (elements.proposalForm.elements.execution_plan_run_id) return;
    const planField = $("#proposal-plan").closest(".field");
    const label = document.createElement("label");
    label.className = "field";
    label.innerHTML = '<span>Plan run ID <small>required with a plan</small></span><input name="execution_plan_run_id" placeholder="epr_…" />';
    planField.after(label);
  }

  function resetProposalForm() {
    ensureProposalPlanRunField();
    elements.proposalForm.reset();
    elements.proposalForm.elements.idempotency_key.value = newIdempotencyKey();
    elements.patternRows.replaceChildren();
    addPatternRow();
    elements.riskOutput.hidden = true;
    elements.riskOutput.textContent = "";
  }

  function proposalPayloadFromForm() {
    const form = elements.proposalForm;
    const patterns = $$('[data-pattern-row]', elements.patternRows).map((row) => ({ pattern_id: row.querySelector("[data-pattern-id]").value.trim(), pattern_version: Number(row.querySelector("[data-pattern-version]").value), role: row.querySelector("[data-pattern-role]").value, confidence: Number(row.querySelector("[data-pattern-confidence]").value), evidence_refs: commaList(row.querySelector("[data-pattern-evidence]").value) }));
    if (!patterns.length || !patterns.some((pattern) => pattern.role === "primary")) throw new Error("Add one primary pattern hypothesis.");
    const planName = formValue(form, "execution_plan_name");
    const selectedPlan = state.plans.find((plan) => plan.name === planName);
    const planRunId = formValue(form, "execution_plan_run_id");
    const payload = { idempotency_key: formValue(form, "idempotency_key"), mode: "paper", agent_id: formValue(form, "agent_id"), model_version: formValue(form, "model_version"), strategy_version: formValue(form, "strategy_version"), signal_tier: formValue(form, "signal_tier"), signal_score: Number(formValue(form, "signal_score")), context: { venue: formValue(form, "venue"), symbol: formValue(form, "symbol"), market_type: formValue(form, "market_type"), timeframe: formValue(form, "timeframe"), side: formValue(form, "side"), market_regime: formValue(form, "market_regime") }, thesis: { summary: formValue(form, "thesis_summary"), evidence: [{ source: formValue(form, "evidence_source"), reference: formValue(form, "evidence_reference"), feature: formValue(form, "evidence_feature"), value: formValue(form, "evidence_value"), weight: Number(formValue(form, "evidence_weight")) }], scope: keyValueMap(formValue(form, "scope")), assumptions: lines(formValue(form, "assumptions")), invalidation_conditions: lines(formValue(form, "invalidation_conditions")), expected_horizon_minutes: Number(formValue(form, "expected_horizon_minutes")), confidence: Number(formValue(form, "confidence")) }, patterns, quantity: Number(formValue(form, "quantity")), entry_price: Number(formValue(form, "entry_price")), stop_loss_price: Number(formValue(form, "stop_loss_price")), leverage: Number(formValue(form, "leverage")), max_loss_quote: Number(formValue(form, "max_loss_quote")) };
    if (formValue(form, "take_profit_price")) payload.take_profit_price = Number(formValue(form, "take_profit_price"));
    if (formValue(form, "workflow_name")) payload.workflow_name = formValue(form, "workflow_name");
    if (planName) {
      payload.execution_plan_name = planName;
      payload.execution_plan_version = selectedPlan ? selectedPlan.version : undefined;
      if (planRunId) payload.execution_plan_run_id = planRunId;
    }
    return payload;
  }

  function renderRisk(check) {
    elements.riskOutput.hidden = false;
    elements.riskOutput.className = "inline-result " + (check.allowed ? "is-good" : "is-bad");
    elements.riskOutput.innerHTML = '<strong>' + (check.allowed ? "risk accepted" : "risk blocked") + '</strong><span>notional ' + escapeHtml(formatNumber(check.notional_quote, 2)) + " · stop risk " + escapeHtml(formatNumber(check.estimated_stop_loss_quote, 2)) + " · net R:R " + escapeHtml(formatNumber(check.estimated_reward_risk_ratio, 2)) + '</span>' + (check.reasons && check.reasons.length ? '<ul>' + check.reasons.map((reason) => "<li>" + escapeHtml(reason) + "</li>").join("") + "</ul>" : "");
  }

  async function checkRisk() {
    const button = $("#check-risk");
    setBusy(button, true, "Checking…");
    try { renderRisk(await api("/api/v1/risk/check", { method: "POST", body: JSON.stringify(proposalPayloadFromForm()) })); }
    catch (error) { renderResultError(elements.riskOutput, error); elements.riskOutput.hidden = false; setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  function renderOperations() {
    elements.operationCount.textContent = state.operations.length;
    if (!state.operations.length) { elements.operationList.innerHTML = '<div class="empty-state"><span class="empty-glyph">◌</span><strong>No proposals yet</strong><p>Build a complete thesis to create the first one.</p></div>'; return; }
    elements.operationList.innerHTML = '<table class="data-table operation-table"><thead><tr><th>Operation</th><th>Context</th><th>Status</th><th>Created</th></tr></thead><tbody>' + state.operations.map((operation) => { const proposal = operation.proposal || {}; const context = proposal.context || {}; const outcome = operation.outcome; return '<tr data-operation-id="' + escapeHtml(operation.operation_id) + '" class="' + (state.selectedOperation && state.selectedOperation.operation_id === operation.operation_id ? "is-selected" : "") + '"><td><strong>' + escapeHtml(operation.operation_id) + '</strong><small>' + escapeHtml(proposal.agent_id) + '</small></td><td><strong>' + escapeHtml(context.symbol) + '</strong><small class="side-' + escapeHtml(context.side) + '">' + escapeHtml(context.side) + " · " + escapeHtml(context.market_type) + (outcome ? " · PnL " + escapeHtml(formatNumber(outcome.pnl_net, 2)) : "") + '</small></td><td><span class="status-badge status-' + escapeHtml(operation.status) + '">' + escapeHtml(operation.status.replaceAll("_", " ")) + '</span></td><td>' + escapeHtml(formatDate(operation.created_at)) + '</td></tr>'; }).join("") + "</tbody></table>";
  }

  function detailRow(label, value) { return '<div class="detail-row"><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(value === undefined || value === null ? "—" : value) + "</strong></div>"; }

  function renderOperationDetail(operation) {
    if (!operation) return;
    const proposal = operation.proposal || {};
    const context = proposal.context || {};
    elements.operationDetailTitle.textContent = operation.operation_id;
    elements.operationDetailStatus.textContent = operation.status.replaceAll("_", " ");
    elements.operationDetailStatus.className = "status-badge status-" + operation.status;
    const actions = [];
    if (operation.status === "proposed") actions.push('<button class="button button-primary" type="button" data-execute-operation="' + escapeHtml(operation.operation_id) + '">Execute paper</button>');
    if (operation.status === "paper_open") actions.push('<button class="button button-secondary" type="button" data-outcome-operation="' + escapeHtml(operation.operation_id) + '">Record outcome</button>');
    if (operation.status === "closed" && !operation.postmortem) actions.push('<button class="button button-secondary" type="button" data-postmortem-operation="' + escapeHtml(operation.operation_id) + '">Write postmortem</button>');
    const outcome = operation.outcome ? '<div class="detail-section"><span class="section-kicker">Outcome</span><div class="detail-grid">' + detailRow("Status", operation.outcome.status) + detailRow("Exit price", formatNumber(operation.outcome.exit_price, 4)) + detailRow("Net PnL", formatNumber(operation.outcome.pnl_net, 2)) + detailRow("Reason", operation.outcome.exit_reason) + '</div></div>' : "";
    const postmortem = operation.postmortem ? '<div class="detail-section"><span class="section-kicker">Postmortem</span><p>' + escapeHtml(operation.postmortem.proposed_lesson) + '</p><div class="detail-grid">' + detailRow("Failure category", operation.postmortem.failure_category) + detailRow("Confidence", formatPercent(Number(operation.postmortem.confidence) * 100)) + '</div></div>' : "";
    elements.operationDetail.innerHTML = '<div class="detail-actions">' + (actions.length ? actions.join("") : '<span class="muted-line">No action pending for this record.</span>') + '</div><div class="detail-section"><span class="section-kicker">Market context</span><div class="detail-grid">' + detailRow("Symbol", context.symbol) + detailRow("Side", context.side) + detailRow("Market", context.market_type + " · " + context.timeframe) + detailRow("Regime", context.market_regime) + detailRow("Entry", formatNumber(proposal.entry_price, 4)) + detailRow("Stop", formatNumber(proposal.stop_loss_price, 4)) + detailRow("Target", formatNumber(proposal.take_profit_price, 4)) + detailRow("Max loss", formatNumber(proposal.max_loss_quote, 2)) + '</div></div><div class="detail-section"><span class="section-kicker">Thesis</span><p>' + escapeHtml(proposal.thesis && proposal.thesis.summary) + '</p><div class="detail-tags">' + ((proposal.thesis && proposal.thesis.invalidation_conditions) || []).map((item) => '<span>' + escapeHtml(item) + '</span>').join("") + '</div></div>' + outcome + postmortem + '<details class="detail-json"><summary>Full operation JSON</summary><pre>' + escapeHtml(JSON.stringify(operation, null, 2)) + "</pre></details>";
  }

  async function selectOperation(id) {
    try {
      state.selectedOperation = await api("/api/v1/operations/" + encodeURIComponent(id));
      renderOperations();
      renderOperationDetail(state.selectedOperation);
    } catch (error) { setAlert(error.message, "error"); }
  }

  async function createProposal(event) {
    event.preventDefault();
    const button = $("#create-proposal");
    setBusy(button, true, "Submitting…");
    try {
      const operation = await api("/api/v1/operations", { method: "POST", body: JSON.stringify(proposalPayloadFromForm()) });
      closeEditor("proposal-editor");
      await refreshOperations();
      await selectOperation(operation.operation_id);
      setAlert("Paper proposal accepted by the risk engine.", "success");
    } catch (error) { setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  async function refreshOperations() {
    state.operations = await api("/api/v1/operations");
    state.lessons = await api("/api/v1/lessons?limit=500");
    renderOperations();
    renderKpis();
    renderOverviewOperations();
  }

  async function executeOperation(id) {
    if (!window.confirm("Execute this proposal in the paper environment?")) return;
    try { const operation = await api("/api/v1/operations/" + encodeURIComponent(id) + "/execute-paper", { method: "POST" }); await refreshOperations(); state.selectedOperation = operation; renderOperations(); renderOperationDetail(operation); setAlert("Paper operation opened.", "success"); }
    catch (error) { setAlert(error.message, "error"); }
  }

  function showOutcomeEditor(operation) {
    elements.outcomeForm.elements.operation_id.value = operation.operation_id;
    elements.outcomeForm.elements.exit_price.value = operation.proposal.entry_price;
    elements.outcomeEditor.hidden = false;
    elements.outcomeEditor.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function showPostmortemEditor(operation) {
    elements.postmortemForm.elements.operation_id.value = operation.operation_id;
    elements.postmortemEditor.hidden = false;
    elements.postmortemEditor.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function saveOutcome(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button[type=submit]");
    const payload = { exit_price: Number(formValue(form, "exit_price")), fees_quote: Number(formValue(form, "fees_quote") || 0), slippage_quote: Number(formValue(form, "slippage_quote") || 0), funding_quote: Number(formValue(form, "funding_quote") || 0), exit_reason: formValue(form, "exit_reason") };
    if (formValue(form, "forced_status")) payload.forced_status = formValue(form, "forced_status");
    setBusy(button, true, "Closing…");
    try { const operation = await api("/api/v1/operations/" + encodeURIComponent(formValue(form, "operation_id")) + "/outcome", { method: "POST", body: JSON.stringify(payload) }); closeEditor("outcome-editor"); await refreshOperations(); state.selectedOperation = operation; renderOperations(); renderOperationDetail(operation); setAlert("Outcome recorded.", "success"); }
    catch (error) { setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  async function savePostmortem(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const operation = state.selectedOperation;
    const pattern = operation && operation.proposal && operation.proposal.patterns ? operation.proposal.patterns[0] : null;
    const button = form.querySelector("button[type=submit]");
    const payload = { what_worked: lines(formValue(form, "what_worked")), what_failed: lines(formValue(form, "what_failed")), failure_category: formValue(form, "failure_category"), failure_explanation: formValue(form, "failure_explanation"), counterfactual: formValue(form, "counterfactual"), proposed_lesson: formValue(form, "proposed_lesson"), confidence: Number(formValue(form, "confidence")), pattern_verdicts: pattern ? [{ pattern_id: pattern.pattern_id, status: formValue(form, "pattern_status"), explanation: formValue(form, "pattern_explanation") }] : [] };
    setBusy(button, true, "Saving…");
    try { const updated = await api("/api/v1/operations/" + encodeURIComponent(formValue(form, "operation_id")) + "/postmortem", { method: "POST", body: JSON.stringify(payload) }); closeEditor("postmortem-editor"); await refreshOperations(); state.selectedOperation = updated; renderOperations(); renderOperationDetail(updated); setAlert("Postmortem saved and lesson candidate recorded.", "success"); }
    catch (error) { setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  // Intelligence ----------------------------------------------------------
  function formParams(form, names) {
    const params = new URLSearchParams();
    names.forEach((name) => { const value = formValue(form, name); if (value) params.set(name, value); });
    return params;
  }

  function renderResultError(element, error) { element.className = "result-block result-error"; element.innerHTML = '<strong>Request failed</strong><p>' + escapeHtml(error.message) + '</p>'; }

  function renderMarketResult(response, target) {
    const snapshots = response.snapshots || [];
    const errors = response.errors || [];
    if (!snapshots.length) { target.className = "result-block result-error"; target.innerHTML = '<strong>No snapshot returned</strong>' + (errors.length ? '<p>' + escapeHtml(errors.map((item) => item.source + ": " + item.message).join(" · ")) + "</p>" : ""); return; }
    const snapshot = snapshots[0];
    const change = snapshot.change_24h_percent;
    const candles = (snapshot.candles || []).slice(-8).reverse().map((candle) => '<tr><td>' + escapeHtml(formatDate(candle.close_time)) + '</td><td>' + escapeHtml(formatNumber(candle.open_price, 4)) + '</td><td>' + escapeHtml(formatNumber(candle.high_price, 4)) + '</td><td>' + escapeHtml(formatNumber(candle.low_price, 4)) + '</td><td>' + escapeHtml(formatNumber(candle.close_price, 4)) + '</td></tr>').join("");
    target.className = "result-block";
    target.innerHTML = '<div class="quote-head"><div><strong>' + escapeHtml(snapshot.symbol) + '</strong><span>' + escapeHtml(snapshot.venue) + " · " + escapeHtml(snapshot.market_type) + " · " + escapeHtml(snapshot.timeframe) + '</span></div><strong class="quote-price">' + escapeHtml(formatNumber(snapshot.price, 4)) + '</strong></div><div class="quote-meta"><span class="' + (Number(change) >= 0 ? "text-good" : "text-bad") + '">' + escapeHtml(formatPercent(change)) + " 24h</span><span>bid " + escapeHtml(formatNumber(snapshot.bid_price, 4)) + " / ask " + escapeHtml(formatNumber(snapshot.ask_price, 4)) + '</span><span>' + escapeHtml(snapshot.latency_ms) + ' ms</span></div>' + (candles ? '<div class="mini-table"><table class="data-table"><thead><tr><th>Close</th><th>Open</th><th>High</th><th>Low</th><th>Close</th></tr></thead><tbody>' + candles + "</tbody></table></div>" : "") + (errors.length ? '<div class="result-callout error"><strong>Source errors</strong><span>' + escapeHtml(errors.map((item) => item.source + ": " + item.message).join(" · ")) + "</span></div>" : "") + '<details><summary>Snapshot JSON</summary><pre>' + escapeHtml(JSON.stringify(response, null, 2)) + "</pre></details>";
  }

  function renderPositioning(response) {
    const target = $("#positioning-output");
    const snapshot = response.snapshots && response.snapshots[0];
    if (!snapshot) { renderResultError(target, new Error((response.errors || []).map((item) => item.message).join(" · ") || "No positioning returned.")); return; }
    const point = snapshot.current || {};
    target.className = "result-block";
    target.innerHTML = '<div class="positioning-head"><strong>' + escapeHtml(response.symbol) + '</strong><span>' + escapeHtml(snapshot.contract_type) + " · " + escapeHtml(snapshot.period) + '</span></div><div class="metric-grid four"><div><span>Open interest</span><strong>' + escapeHtml(formatNumber(point.open_interest_value_quote || point.open_interest_contracts, 2)) + '</strong></div><div><span>Funding</span><strong>' + escapeHtml(formatNumber(point.funding_rate, 6)) + '</strong></div><div><span>Global L/S</span><strong>' + escapeHtml(formatNumber(point.global_long_short_account_ratio, 3)) + '</strong></div><div><span>Taker buy/sell</span><strong>' + escapeHtml(formatNumber(point.taker_buy_sell_volume_ratio, 3)) + '</strong></div></div><p class="disclaimer">' + escapeHtml(response.disclaimer) + '</p><details><summary>Positioning JSON</summary><pre>' + escapeHtml(JSON.stringify(response, null, 2)) + "</pre></details>";
  }

  function renderSignalResponse(response, target, emptyText) {
    const signals = response.signals || [];
    const errors = response.errors || [];
    target.className = "result-block";
    target.innerHTML = (signals.length ? '<ul class="signal-list">' + signals.slice(0, 12).map((signal) => '<li><span class="signal-type">' + escapeHtml(signal.signal_type) + '</span><div><strong>' + (signal.url ? '<a href="' + escapeHtml(safeExternalUrl(signal.url)) + '" target="_blank" rel="noreferrer">' + escapeHtml(signal.title) + ' ↗</a>' : escapeHtml(signal.title)) + '</strong><small>' + escapeHtml(signal.source) + " · " + escapeHtml(formatDate(signal.published_at || signal.observed_at)) + '</small><p>' + escapeHtml(signal.text) + '</p></div></li>').join("") + "</ul>" : '<div class="empty-state compact"><span class="empty-glyph">≋</span><strong>No signals returned</strong><p>' + escapeHtml(emptyText) + "</p></div>") + (errors.length ? '<div class="result-callout error"><strong>Source errors</strong><span>' + escapeHtml(errors.map((item) => item.source + ": " + item.message).join(" · ")) + "</span></div>" : "") + '<details><summary>Response JSON</summary><pre>' + escapeHtml(JSON.stringify(response, null, 2)) + "</pre></details>";
  }

  async function submitIntelligence(form, path, target, renderer) {
    const button = form.querySelector("button[type=submit]");
    setBusy(button, true, "Reading…");
    try { const response = await api(path + "?" + formParams(form, Array.from(form.elements).map((field) => field.name).filter(Boolean)).toString()); renderer(response, target); setAlert("Context refreshed.", "success"); }
    catch (error) { renderResultError(target, error); setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  // Memory ---------------------------------------------------------------
  async function queryPattern(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const target = $("#pattern-output");
    const button = form.querySelector("button[type=submit]");
    const patternId = formValue(form, "pattern_id");
    const params = formParams(form, ["symbol", "market_type", "side", "limit"]);
    setBusy(button, true, "Reading…");
    try {
      const context = await api("/api/v1/patterns/" + encodeURIComponent(patternId) + "/context?" + params.toString());
      target.className = "result-block";
      target.innerHTML = [
        '<div class="memory-head"><strong>', escapeHtml(context.pattern_id), '</strong><span>',
        context.total_cases, " cases · ", context.wins, " wins · ", context.losses,
        '</span></div><div class="metric-grid three"><div><span>Win rate</span><strong>',
        escapeHtml(context.total_cases ? formatPercent(context.wins / context.total_cases * 100) : "—"),
        '</strong></div><div><span>Net PnL</span><strong>',
        escapeHtml(formatNumber(context.net_pnl_quote, 2)),
        '</strong></div><div><span>Lessons</span><strong>', context.lessons.length,
        '</strong></div></div><p class="disclaimer">', escapeHtml(context.disclaimer),
        '</p>', renderLessonList(context.lessons),
        '<details><summary>Context JSON</summary><pre>',
        escapeHtml(JSON.stringify(context, null, 2)), "</pre></details>",
      ].join("");
      setAlert("Pattern context loaded.", "success");
    }
    catch (error) { renderResultError(target, error); setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  function renderLessonList(lessons) { return lessons && lessons.length ? '<ul class="lesson-list">' + lessons.map((lesson) => '<li><div><strong>' + escapeHtml(lesson.pattern_id) + " · " + escapeHtml(lesson.outcome_status) + '</strong><small>' + escapeHtml(lesson.validation_status) + " · " + escapeHtml(formatDate(lesson.created_at)) + '</small></div><p>' + escapeHtml(lesson.lesson) + '</p></li>').join("") + "</ul>" : '<div class="empty-state compact"><strong>No candidate lessons</strong><p>Close an operation and write a postmortem to grow this memory.</p></div>'; }

  async function queryLessons(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const target = $("#lessons-output");
    const button = form.querySelector("button[type=submit]");
    setBusy(button, true, "Searching…");
    try { const lessons = await api("/api/v1/lessons?" + formParams(form, ["pattern_id", "symbol", "market_type", "side", "limit"]).toString()); target.className = "result-block"; target.innerHTML = renderLessonList(lessons) + '<details><summary>Lessons JSON</summary><pre>' + escapeHtml(JSON.stringify(lessons, null, 2)) + "</pre></details>"; setAlert(lessons.length + " lesson" + (lessons.length === 1 ? "" : "s") + " found.", "success"); }
    catch (error) { renderResultError(target, error); setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  // Settings and shared select lists -------------------------------------
  function populateSelect(select, values, placeholder, selected) {
    const current = selected === undefined ? select.value : selected;
    select.replaceChildren();
    if (placeholder !== null) { const option = document.createElement("option"); option.value = ""; option.textContent = placeholder; select.appendChild(option); }
    values.forEach((value) => { const option = document.createElement("option"); option.value = value; option.textContent = value; select.appendChild(option); });
    if (values.includes(current)) select.value = current;
  }

  function populateResourceSelects() {
    const workflowNames = state.workflows.map((workflow) => workflow.name);
    const planNames = state.plans.map((plan) => plan.name);
    populateSelect($("#run-workflow-name"), workflowNames, "Select a workflow");
    populateSelect($("#proposal-workflow"), workflowNames, "None");
    populateSelect($("#plan-before-workflow"), workflowNames, "None");
    populateSelect($("#plan-after-workflow"), workflowNames, "None");
    populateSelect(elements.runPlanName, planNames, "Create or select a plan");
    populateSelect(elements.strategyPlanName, planNames, "Select a plan");
    populateSelect($("#proposal-plan"), planNames, "Global risk only");
  }

  async function saveCredential(card) {
    const provider = card.dataset.providerForm;
    const button = card.querySelector("button[type=submit]");
    const values = {};
    $$('[data-credential]', card).forEach((input) => { if (input.value.trim()) values[input.dataset.credential] = input.value.trim(); });
    const missing = $$('[data-credential][required]', card).some((input) => !values[input.dataset.credential]);
    if (missing) { setAlert("Complete the required credential fields before saving.", "error"); return; }
    setBusy(button, true, "Saving…");
    try { await api("/api/v1/account/credentials/" + encodeURIComponent(provider), { method: "PUT", body: JSON.stringify({ values }) }); $$('input[type=password]', card).forEach((input) => { input.value = ""; }); state.credentials = await api("/api/v1/account/credentials"); renderCredentialStatus(); setAlert(providerLabel(provider) + " credentials saved securely.", "success"); }
    catch (error) { setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  function providerLabel(provider) { return { binance: "Binance", telegram: "Telegram" }[provider] || provider; }

  async function deleteCredential(provider) {
    if (!window.confirm("Remove the " + providerLabel(provider) + " credentials from this account?")) return;
    try { await api("/api/v1/account/credentials/" + encodeURIComponent(provider), { method: "DELETE" }); const card = $('[data-provider-form="' + provider + '"]'); card.reset ? card.reset() : $$('input', card).forEach((input) => { input.value = ""; }); state.credentials = await api("/api/v1/account/credentials"); renderCredentialStatus(); setAlert(providerLabel(provider) + " credentials removed.", "success"); }
    catch (error) { setAlert(error.message, "error"); }
  }

  async function testTelegram() {
    const button = elements.testTelegram;
    setBusy(button, true, "Sending…");
    try { const result = await api("/api/v1/account/notifications/telegram/test", { method: "POST" }); if (result.status === "sent") setAlert("Telegram test delivered to " + result.delivered_chats + " chat(s).", "success"); else setAlert(result.error || "Telegram could not deliver the test.", "error"); }
    catch (error) { setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  function closeEditor(id) { const element = typeof id === "string" ? $("#" + id) : id; if (element) element.hidden = true; }

  async function register(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button[type=submit]");
    const headers = {};
    if (formValue(form, "bootstrap_token")) headers["X-Bootstrap-Token"] = formValue(form, "bootstrap_token");
    setBusy(button, true, "Creating…");
    try { const created = await api("/api/v1/accounts", { method: "POST", headers, body: JSON.stringify({ name: formValue(form, "account_name") }) }, false); setSessionToken(created.access_token); elements.createdToken.textContent = created.access_token; elements.registrationToken.hidden = false; form.reset(); await loadWorkspace(); setAlert("Account created. Save the access token before leaving this tab.", "success"); }
    catch (error) { setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  async function signIn(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button[type=submit]");
    setSessionToken(formValue(form, "access_token"));
    setBusy(button, true, "Opening…");
    try { await loadWorkspace(); if (elements.workspace.hidden) throw new Error("Could not authenticate the account."); form.reset(); }
    catch (error) { setAlert(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  async function copyToken() {
    try { await navigator.clipboard.writeText(elements.createdToken.textContent); setAlert("Access token copied to the clipboard.", "success"); }
    catch (error) { setAlert("Copy was blocked. Select the token manually.", "error"); }
  }

  // Events ---------------------------------------------------------------
  elements.registerForm.addEventListener("submit", register);
  elements.signinForm.addEventListener("submit", signIn);
  elements.copyToken.addEventListener("click", copyToken);
  elements.signOut.addEventListener("click", () => { clearSession(); elements.registrationToken.hidden = true; setAlert("Signed out. The account token was removed from this tab.", "success"); });
  $$('[data-view]').forEach((link) => link.addEventListener("click", (event) => { event.preventDefault(); showView(link.dataset.view); }));
  $$('[data-go-view]').forEach((button) => button.addEventListener("click", () => showView(button.dataset.goView)));
  $$('[data-refresh-workspace]').forEach((button) => button.addEventListener("click", refreshWorkspace));
  $$('[data-refresh-health]').forEach((button) => button.addEventListener("click", refreshHealth));
  $$('[data-refresh-operations]').forEach((button) => button.addEventListener("click", () => refreshOperations().then(() => setAlert("Operations refreshed.", "success")).catch((error) => setAlert(error.message, "error"))));
  $$('[data-refresh-cycles]').forEach((button) => button.addEventListener("click", () => refreshStrategyCycles().then(() => setAlert("Cycle history refreshed.", "success")).catch((error) => setAlert(error.message, "error"))));
  elements.mobileNavToggle.addEventListener("click", () => { const open = document.body.classList.toggle("nav-open"); elements.mobileNavToggle.setAttribute("aria-expanded", String(open)); });
  window.addEventListener("hashchange", () => { if (state.token) showView(window.location.hash.slice(1) || "overview", false); });

  $("#overview-market-form").addEventListener("submit", async (event) => { event.preventDefault(); const form = event.currentTarget; const button = form.querySelector("button[type=submit]"); setBusy(button, true, "Reading…"); try { const response = await api("/api/v1/market/snapshot?" + formParams(form, ["symbol", "market_type", "timeframe"]).toString()); renderMarketResult(response, elements.overviewMarketResult); } catch (error) { renderResultError(elements.overviewMarketResult, error); setAlert(error.message, "error"); } finally { setBusy(button, false); } });
  $("#market-form").addEventListener("submit", (event) => { event.preventDefault(); submitIntelligence(event.currentTarget, "/api/v1/market/snapshot", $("#market-output"), (response, target) => renderMarketResult(response, target)); });
  $("#positioning-form").addEventListener("submit", (event) => { event.preventDefault(); submitIntelligence(event.currentTarget, "/api/v1/market/positioning", $("#positioning-output"), (response) => renderPositioning(response)); });
  $("#news-form").addEventListener("submit", (event) => { event.preventDefault(); submitIntelligence(event.currentTarget, "/api/v1/news", $("#news-output"), (response, target) => renderSignalResponse(response, target, "No RSS stories matched this context.")); });
  $("#x-form").addEventListener("submit", (event) => { event.preventDefault(); submitIntelligence(event.currentTarget, "/api/v1/signals/x", $("#x-output"), (response, target) => renderSignalResponse(response, target, "X ingestion is disabled by default.")); });

  elements.planForm.addEventListener("submit", savePlan);
  elements.planForm.elements.symbols.addEventListener("input", syncPlanSymbolChoicesFromInput);
  $("#new-plan").addEventListener("click", () => { resetPlanEditor(); showView("strategy"); $("#plan-editor").scrollIntoView({ behavior: "smooth", block: "start" }); });
  $("#reset-plan").addEventListener("click", resetPlanEditor);
  $("#add-plan-step").addEventListener("click", () => addPlanStep());
  $("#plan-run-form").addEventListener("submit", runPlan);
  $("#strategy-form").addEventListener("submit", evaluateStrategy);
  $("#run-cycle").addEventListener("click", runStrategyCycle);
  elements.planList.addEventListener("click", (event) => { const edit = event.target.closest("[data-edit-plan]"); if (edit) return editPlan(edit.dataset.editPlan); const remove = event.target.closest("[data-delete-plan]"); if (remove) return deletePlan(remove.dataset.deletePlan); const run = event.target.closest("[data-run-plan]"); if (run) { elements.runPlanName.value = run.dataset.runPlan; showView("strategy"); $("#plan-run-form").scrollIntoView({ behavior: "smooth", block: "center" }); } });
  elements.planRunOutput.addEventListener("click", (event) => { const receipt = event.target.closest("[data-load-receipt]"); if (receipt) loadReceipt(receipt.dataset.loadReceipt); const use = event.target.closest("[data-use-run]"); if (use) useRunForEvaluation(use.dataset.useRun, use.dataset.usePlan); });

  elements.workflowForm.addEventListener("submit", saveWorkflow);
  $("#new-workflow").addEventListener("click", () => { resetWorkflowEditor(); showView("automations"); $("#workflow-form").scrollIntoView({ behavior: "smooth", block: "start" }); });
  $("#reset-workflow").addEventListener("click", resetWorkflowEditor);
  $$('[data-add-step]').forEach((button) => button.addEventListener("click", () => addWorkflowStep(button.dataset.addStep)));
  elements.workflowList.addEventListener("click", (event) => { const edit = event.target.closest("[data-edit-workflow]"); if (edit) return editWorkflow(edit.dataset.editWorkflow); const remove = event.target.closest("[data-delete-workflow]"); if (remove) deleteWorkflow(remove.dataset.deleteWorkflow); });
  $("#workflow-run-form").addEventListener("submit", runWorkflow);

  $("#new-operation").addEventListener("click", () => { resetProposalForm(); elements.proposalEditor.hidden = false; showView("operations"); elements.proposalEditor.scrollIntoView({ behavior: "smooth", block: "start" }); });
  $("#cancel-proposal").addEventListener("click", () => closeEditor("proposal-editor"));
  $("#add-pattern").addEventListener("click", () => addPatternRow({ role: "secondary" }));
  $("#check-risk").addEventListener("click", checkRisk);
  elements.proposalForm.addEventListener("submit", createProposal);
  elements.operationList.addEventListener("click", (event) => { const row = event.target.closest("[data-operation-id]"); if (row) selectOperation(row.dataset.operationId); });
  elements.overviewOperations.addEventListener("click", (event) => { const row = event.target.closest("[data-operation-id]"); if (row) { showView("operations"); selectOperation(row.dataset.operationId); } });
  elements.operationDetail.addEventListener("click", (event) => { const execute = event.target.closest("[data-execute-operation]"); if (execute) return executeOperation(execute.dataset.executeOperation); const outcome = event.target.closest("[data-outcome-operation]"); if (outcome) return showOutcomeEditor(state.selectedOperation); const postmortem = event.target.closest("[data-postmortem-operation]"); if (postmortem) showPostmortemEditor(state.selectedOperation); });
  elements.outcomeForm.addEventListener("submit", saveOutcome);
  elements.postmortemForm.addEventListener("submit", savePostmortem);
  $$('[data-close-editor]').forEach((button) => button.addEventListener("click", () => closeEditor(button.dataset.closeEditor)));

  $("#pattern-form").addEventListener("submit", queryPattern);
  $("#lessons-form").addEventListener("submit", queryLessons);
  $$('[data-provider-form]').forEach((card) => {
    card.querySelector('button[type="submit"]').addEventListener("click", () => saveCredential(card));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.target.matches("input")) {
        event.preventDefault();
        saveCredential(card);
      }
    });
  });
  $$('[data-delete-provider]').forEach((button) => button.addEventListener("click", () => deleteCredential(button.dataset.deleteProvider)));
  elements.testTelegram.addEventListener("click", testTelegram);
  $("#proposal-plan").addEventListener("change", (event) => { const plan = state.plans.find((item) => item.name === event.target.value); const input = elements.proposalForm.elements.execution_plan_run_id; if (input) input.placeholder = plan ? "Run preflight first: epr_… (v" + plan.version + ")" : "epr_…"; });

  resetWorkflowEditor();
  resetPlanEditor();
  resetProposalForm();
  showView(window.location.hash.slice(1) || "overview", false);
  if (state.token) loadWorkspace();
})();
