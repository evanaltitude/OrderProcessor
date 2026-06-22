const params = new URLSearchParams(window.location.search);
const apiBase = params.get("apiBase")
  || window.localStorage.getItem("orderProcessorApiBase")
  || document.querySelector('meta[name="order-api-base"]')?.content
  || "/api";
const initialTenantId = params.get("tenantId") || "default";
const state = {
  tenantId: initialTenantId,
  dashboard: null,
  selectedDistributorId: initialTenantId,
  customerPage: "list"
};

const el = (id) => document.getElementById(id);
const value = (form, name) => new FormData(form).get(name)?.toString().trim() || "";
const split = (text) => text.split(",").map((part) => part.trim()).filter(Boolean);

async function post(path, body = {}, options = {}) {
  const tenantId = options.tenantId || state.tenantId;
  const response = await fetch(`${apiBase}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ tenantId, ...body })
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function actionFailed(result) {
  return Boolean(result?.error || result?.session?.authorized === false);
}

function showActionResult(result) {
  showDetails(result);
  return !actionFailed(result);
}

function activeConsoleView() {
  return document.querySelector(".tab.active")?.dataset.view || "monitor";
}

function showDetails(payload) {
  const pane = el("detailsPane");
  pane.innerHTML = `<pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>`;
  pane.classList.add("open");
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function statusPill(status) {
  const tone = status === "completed" || status === "matched" || status === "active" ? "good" : status === "failed" ? "bad" : "warn";
  return `<span class="pill ${tone}">${escapeHtml(status || "")}</span>`;
}

function parseJsonField(form, name) {
  const text = value(form, name);
  return text ? JSON.parse(text) : {};
}

function selectedDistributor() {
  if (!state.selectedDistributorId) {
    return { id: "", tenantId: "", name: "", environment: "", status: "active", settings: {} };
  }
  const distributors = state.dashboard?.distributorCustomers || [];
  return distributors.find((item) => item.tenantId === state.selectedDistributorId)
    || state.dashboard?.tenant
    || { tenantId: state.tenantId, id: state.tenantId, name: state.tenantId, settings: {} };
}

function primaryMailbox() {
  return (state.dashboard?.mailboxes || [])[0] || null;
}

function primaryConnection() {
  const mailbox = primaryMailbox();
  const connections = state.dashboard?.microsoftAuthConnections || [];
  if (mailbox?.connectionId) {
    return connections.find((connection) => connection.id === mailbox.connectionId) || null;
  }
  return connections[0] || null;
}

function latestDate(values) {
  return values
    .map((value) => value?.lastImportedAt || value?.updatedAt || value?.createdAt || "")
    .filter(Boolean)
    .sort()
    .at(-1) || "";
}

function renderMetrics(summary = {}) {
  const metrics = [
    ["Active", summary.activeRunCount ?? 0],
    ["Processed", summary.processedOrderCount ?? 0],
    ["Success", `${Math.round((summary.successRate || 0) * 100)}%`],
    ["Exceptions", summary.openExceptionCount ?? 0],
    ["Unresolved", summary.unresolvedLineCount ?? 0],
    ["Customer ID", summary.customerIdentificationFailureCount ?? 0],
    ["Processor Fail", summary.processorFailureCount ?? 0],
    ["Avg Latency", summary.averageProcessingLatencyMs == null ? "" : `${Math.round(summary.averageProcessingLatencyMs)}ms`]
  ];
  el("metrics").innerHTML = metrics
    .map(([label, number]) => `<div class="metric"><span>${label}</span><strong>${number}</strong></div>`)
    .join("");
}

function renderRows() {
  const dashboard = state.dashboard || {};
  el("activeRunsBody").innerHTML = (dashboard.activeRuns || []).map((run) => `
    <tr>
      <td>${escapeHtml(run.id)}</td>
      <td>${escapeHtml(run.customerId || "")}</td>
      <td>${statusPill(run.status)}</td>
      <td>${escapeHtml(run.poNumber || run.orderNumber || "")}</td>
      <td>${(run.lines || []).length}</td>
      <td>${escapeHtml(run.updatedAt || run.createdAt || "")}</td>
      <td><button class="secondary" data-action="timeline" data-run="${escapeHtml(run.id)}" type="button">Timeline</button></td>
    </tr>
  `).join("");

  el("processedOrdersBody").innerHTML = (dashboard.processedOrders || []).map((run) => `
    <tr>
      <td>${escapeHtml(run.id)}</td>
      <td>${escapeHtml(run.customerId || "")}</td>
      <td>${statusPill(run.status)}</td>
      <td>${(run.outputArtifacts || []).length}</td>
      <td>${escapeHtml(run.updatedAt || run.createdAt || "")}</td>
      <td>
        <button class="secondary" data-action="timeline" data-run="${escapeHtml(run.id)}" type="button">Timeline</button>
        <button class="secondary" data-action="reprocess" data-run="${escapeHtml(run.id)}" type="button">Reprocess</button>
      </td>
    </tr>
  `).join("");
}

function renderExceptions() {
  const tasks = state.dashboard?.exceptionQueue || [];
  el("exceptionList").innerHTML = tasks.map((task) => `
    <article class="task">
      <header>
        <strong>${escapeHtml(task.type)}</strong>
        ${statusPill(task.status)}
      </header>
      <div>${escapeHtml(task.prompt || task.id)}</div>
      <div class="pill">Run ${escapeHtml(task.orderRunId || "")}</div>
      <div class="header-actions">
        <button data-action="resolve" data-id="${escapeHtml(task.id)}" data-type="${escapeHtml(task.type)}" type="button">Resolve</button>
        <button class="secondary" data-action="inspect" data-payload="${escapeHtml(JSON.stringify(task))}" type="button">Inspect</button>
      </div>
    </article>
  `).join("");
}

function renderArtifacts() {
  const artifacts = state.dashboard?.outputArtifacts || [];
  el("artifactList").innerHTML = artifacts.map((artifact) => `
    <article class="artifact">
      <header>
        <strong>${escapeHtml(artifact.fileName || artifact.type)}</strong>
        <span class="pill">${escapeHtml(artifact.type)}</span>
      </header>
      <div>${escapeHtml(artifact.customerId || "")} ${escapeHtml(artifact.orderRunId || "")}</div>
      <div>${escapeHtml(artifact.contentType || "")} ${artifact.sizeBytes || 0} bytes</div>
      <button data-action="download" data-run="${escapeHtml(artifact.orderRunId)}" data-artifact="${escapeHtml(artifact.id)}" type="button">Open</button>
    </article>
  `).join("");
}

function renderCustomerFilter() {
  const customerFilter = el("customerFilter");
  const current = customerFilter.value;
  customerFilter.innerHTML = '<option value="">All downstream customers</option>' + (state.dashboard?.customers || [])
    .map((customer) => `<option value="${escapeHtml(customer.id)}">${escapeHtml(customer.name || customer.id)}</option>`)
    .join("");
  customerFilter.value = current;
}

function renderDistributors() {
  const distributors = state.dashboard?.distributorCustomers || [];
  el("distributorBody").innerHTML = distributors.map((distributor) => `
    <tr class="click-row" data-action="open-distributor" data-tenant="${escapeHtml(distributor.tenantId)}">
      <td>${escapeHtml(distributor.name || distributor.tenantId)}</td>
      <td>${escapeHtml(distributor.tenantId)}</td>
      <td>${escapeHtml(distributor.environment || "")}</td>
      <td>${statusPill(distributor.status || "active")}</td>
      <td>${escapeHtml(distributor.updatedAt || distributor.createdAt || "")}</td>
    </tr>
  `).join("");
}

function renderCustomerPage() {
  ["distributorListPage", "distributorDetailPage", "distributorEditPage"].forEach((id) => {
    el(id).classList.add("hidden");
  });
  if (state.customerPage === "edit") {
    renderDistributorEdit();
    el("distributorEditPage").classList.remove("hidden");
    return;
  }
  if (state.customerPage === "detail") {
    renderDistributorDetail();
    el("distributorDetailPage").classList.remove("hidden");
    return;
  }
  renderDistributors();
  el("distributorListPage").classList.remove("hidden");
}

function renderDistributorEdit() {
  const distributor = selectedDistributor();
  const form = el("tenantForm");
  el("distributorEditTitle").textContent = distributor?.tenantId ? "Edit Customer" : "Create Customer";
  form.elements.id.value = distributor?.tenantId || "";
  form.elements.name.value = distributor?.name || "";
  form.elements.environment.value = distributor?.environment || "";
  form.elements.status.value = distributor?.status || "active";
}

function renderDistributorDetail() {
  const distributor = selectedDistributor();
  const mailbox = primaryMailbox();
  const connection = primaryConnection();
  const settings = distributor.settings || {};
  el("distributorDetailTitle").textContent = distributor.name || distributor.tenantId;
  el("distributorDetailMeta").textContent = `${distributor.tenantId} ${distributor.environment || ""}`.trim();

  const mailboxForm = el("mailboxForm");
  mailboxForm.elements.mailboxAddress.value = mailbox?.mailboxAddress || "";
  mailboxForm.elements.displayName.value = mailbox?.displayName || "";
  mailboxForm.elements.connectionId.value = mailbox?.connectionId || connection?.id || "";
  mailboxForm.elements.enabled.checked = mailbox?.enabled !== false;

  el("authSummary").innerHTML = `
    <div><strong>Connection</strong> ${escapeHtml(connection?.id || mailbox?.connectionId || "")}</div>
    <div><strong>Status</strong> ${statusPill(connection?.status || mailbox?.permissionStatus || "needsConsent")}</div>
    <div><strong>Owner</strong> ${escapeHtml(connection?.ownerEmail || mailbox?.settings?.authorizedBy || "")}</div>
    <div><strong>Mailbox</strong> ${escapeHtml(mailbox?.mailboxAddress || "")}</div>
    <div><strong>Last tested</strong> ${escapeHtml(mailbox?.lastTestedAt || connection?.lastTestedAt || "")}</div>
  `;

  const automationForm = el("automationSettingsForm");
  automationForm.elements.routingSettings.value = JSON.stringify(settings.routingSettings || {}, null, 2);
  automationForm.elements.orderConditions.value = JSON.stringify(settings.orderConditions || {}, null, 2);
  automationForm.elements.webstoreEmailConditions.value = JSON.stringify(settings.webstoreEmailConditions || {}, null, 2);
  automationForm.elements.extractionRules.value = JSON.stringify(settings.extractionRules || {}, null, 2);
  automationForm.elements.emailSubjectSettings.value = JSON.stringify(settings.emailSubjectSettings || {}, null, 2);

  renderRoutingRules();
  renderReadOnlyLists();
}

function renderRoutingRules() {
  el("routingRulesBody").innerHTML = (state.dashboard?.routingRules || []).map((rule) => `
    <tr>
      <td>${escapeHtml(rule.priority ?? "")}</td>
      <td>${escapeHtml(rule.name || rule.id)}</td>
      <td>${escapeHtml(rule.phase || "general")}</td>
      <td>${escapeHtml(rule.outcome || "")}</td>
      <td>${rule.enabled ? "Yes" : "No"}</td>
    </tr>
  `).join("");
}

function renderReadOnlyLists() {
  const customers = state.dashboard?.customers || [];
  const items = state.dashboard?.items || [];
  el("customerListUpdated").textContent = `Last update ${latestDate(customers) || "not yet imported"}`;
  el("itemListUpdated").textContent = `Last update ${latestDate(items) || "not yet imported"}`;
  el("downstreamCustomersBody").innerHTML = customers.map((customer) => `
    <tr>
      <td>${escapeHtml(customer.customerCode || "")}</td>
      <td>${escapeHtml(customer.name || customer.id)}</td>
      <td>${escapeHtml(customer.storeNumber || "")}</td>
      <td>${escapeHtml(customer.routeNumber || "")}</td>
      <td>${escapeHtml(customer.csrFolder || customer.csrEmail || "")}</td>
      <td>${escapeHtml(customer.lastImportedAt || "")}</td>
    </tr>
  `).join("");
  el("itemListBody").innerHTML = items.map((item) => `
    <tr>
      <td>${escapeHtml(item.customerId || "")}</td>
      <td>${escapeHtml(item.internalItemNumber || "")}</td>
      <td>${escapeHtml(item.description || "")}</td>
      <td>${escapeHtml(item.upc || "")}</td>
      <td>${escapeHtml([...(item.altPartsCombined || []), ...(item.customerItemNumbers || [])].join(", "))}</td>
      <td>${escapeHtml(item.lastImportedAt || "")}</td>
    </tr>
  `).join("");
}

function renderSession() {
  const session = state.dashboard?.session || {};
  el("sessionLine").textContent = session.authorized
    ? `${session.consoleUser.email} - ${session.isPlatformAdmin ? "platformAdmin" : "customerUser"}`
    : `${session.reason || "unauthorized"}`;
}

async function refresh(options = {}) {
  const includeCustomerFilter = options.includeCustomerFilter ?? activeConsoleView() !== "customers";
  const customerId = includeCustomerFilter ? el("customerFilter").value : "";
  state.dashboard = await post("/console/dashboard", customerId ? { customerId } : {});
  state.selectedDistributorId = state.dashboard?.tenant?.tenantId || state.tenantId;
  renderSession();
  renderMetrics(state.dashboard.summary);
  renderRows();
  renderExceptions();
  renderArtifacts();
  renderCustomerFilter();
  renderCustomerPage();
}

function activeView(id) {
  document.querySelectorAll(".tab").forEach((button) => button.classList.toggle("active", button.dataset.view === id));
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === id));
}

async function openDistributor(tenantId) {
  state.tenantId = tenantId;
  state.selectedDistributorId = tenantId;
  state.customerPage = "detail";
  await refresh({ includeCustomerFilter: false });
}

async function resolveTask(id, type) {
  const resolution = { notes: "Resolved from console" };
  if (type === "itemValidation") {
    resolution.matchedInternalItemNumber = window.prompt("Internal item number") || "";
  } else if (type === "customerIdentification" || type === "routing") {
    resolution.selectedCustomerId = window.prompt("Downstream customer id") || "";
  } else {
    resolution.reprocess = window.confirm("Request reprocess?");
  }
  const result = await post(`/console/exceptions/${id}/resolve`, { resolution });
  showDetails(result);
  await refresh();
}

function wireForms() {
  el("tenantForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const targetTenantId = value(form, "id") || state.tenantId;
    const result = await post("/console/tenants", {
      targetTenantId,
      id: targetTenantId,
      name: value(form, "name"),
      environment: value(form, "environment"),
      status: value(form, "status") || "active"
    });
    if (!showActionResult(result)) return;
    state.tenantId = targetTenantId;
    state.selectedDistributorId = targetTenantId;
    state.customerPage = "detail";
    await refresh({ includeCustomerFilter: false });
  });

  el("mailboxForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const mailbox = primaryMailbox();
    const result = await post("/console/mailboxes", {
      id: mailbox?.id,
      mailboxAddress: value(form, "mailboxAddress"),
      displayName: value(form, "displayName"),
      connectionId: value(form, "connectionId"),
      enabled: form.enabled.checked
    });
    if (!showActionResult(result)) return;
    await refresh({ includeCustomerFilter: false });
  });

  el("automationSettingsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = await post("/console/tenants", {
      targetTenantId: state.tenantId,
      id: state.tenantId,
      name: selectedDistributor().name || state.tenantId,
      environment: selectedDistributor().environment || "",
      status: selectedDistributor().status || "active",
      settings: {
        routingSettings: parseJsonField(form, "routingSettings"),
        orderConditions: parseJsonField(form, "orderConditions"),
        webstoreEmailConditions: parseJsonField(form, "webstoreEmailConditions"),
        extractionRules: parseJsonField(form, "extractionRules"),
        emailSubjectSettings: parseJsonField(form, "emailSubjectSettings")
      }
    });
    if (!showActionResult(result)) return;
    await refresh({ includeCustomerFilter: false });
  });

  el("routingForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const mailbox = primaryMailbox();
    const result = await post("/console/routing-rules", {
      id: value(form, "id"),
      customerId: "_global",
      name: value(form, "name"),
      phase: value(form, "phase"),
      outcome: value(form, "outcome"),
      priority: value(form, "priority") ? Number(value(form, "priority")) : 100,
      processorProfileId: value(form, "processorProfileId"),
      mailboxAccountIds: mailbox?.id ? [mailbox.id] : [],
      mailboxAddresses: mailbox?.mailboxAddress ? [mailbox.mailboxAddress] : [],
      senderEquals: split(value(form, "senderEquals")),
      senderDomains: split(value(form, "senderDomains")),
      subjectRegex: split(value(form, "subjectRegex")),
      bodyRegex: split(value(form, "bodyRegex")),
      knownWebstorePatterns: split(value(form, "knownWebstorePatterns")),
      priorProcessedSubjectRegex: split(value(form, "priorProcessedSubjectRegex")),
      attachmentExtensions: split(value(form, "attachmentExtensions")),
      attachmentNameRegex: split(value(form, "attachmentNameRegex")),
      customerCodeSource: "combined",
      customerCodeRegex: value(form, "customerCodeRegex"),
      customerCodeGroup: "customerCode",
      subjectTemplate: value(form, "subjectTemplate"),
      categoryCsrField: "csrFolder",
      categoryTemplates: split(value(form, "categoryTemplates"))
    });
    if (!showActionResult(result)) return;
    await refresh({ includeCustomerFilter: false });
  });

  el("profileForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const settings = value(form, "settings") ? JSON.parse(value(form, "settings")) : {};
    const body = { customerId: "_global", name: value(form, "name"), settings };
    const path = value(form, "kind") === "processor" ? "/console/processor-profiles" : "/console/output-profiles";
    if (value(form, "kind") === "processor") body.processorType = value(form, "type");
    else body.outputType = value(form, "type");
    const result = await post(path, body);
    if (!showActionResult(result)) return;
    await refresh({ includeCustomerFilter: false });
  });

  el("userForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = await post("/console/users", {
      email: value(form, "email"),
      displayName: value(form, "displayName"),
      roles: split(value(form, "roles")),
      enabled: form.enabled.checked
    });
    if (!showActionResult(result)) return;
    await refresh();
  });

  el("assignmentForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = await post(`/console/customers/${value(form, "customerId")}/users`, {
      email: value(form, "email"),
      roles: split(value(form, "roles")),
      enabled: form.enabled.checked
    });
    if (!showActionResult(result)) return;
    await refresh();
  });
}

async function authorizeMicrosoft() {
  const mailbox = primaryMailbox();
  if (!mailbox?.id) {
    showDetails({ error: "mailboxRequired", message: "Save the shared mailbox before authorizing Microsoft access." });
    return;
  }
  const result = await post("/console/microsoft-auth/start", {
    mailboxAccountId: mailbox.id,
    mailboxAddress: mailbox.mailboxAddress,
    connectionId: mailbox.connectionId || `m365-${state.tenantId}`,
    displayName: mailbox.displayName || mailbox.mailboxAddress,
    redirectUri: `${window.location.origin}/auth/microsoft/callback`,
    returnTo: "/"
  });
  if (actionFailed(result)) {
    showDetails(result);
    return;
  }
  if (result.authorizationUrl) {
    window.location.href = result.authorizationUrl;
  } else {
    showDetails(result);
  }
}

async function testMailbox() {
  const mailbox = primaryMailbox();
  if (!mailbox?.id) {
    showDetails({ error: "mailboxRequired", message: "Save the shared mailbox before testing Microsoft access." });
    return;
  }
  showDetails(await post(`/console/mailboxes/${mailbox.id}/test-connection`, {}));
  await refresh();
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button, tr");
  if (!target) return;
  if (target.classList.contains("tab")) {
    activeView(target.dataset.view);
    if (target.dataset.view === "customers") await refresh({ includeCustomerFilter: false });
  }
  if (target.dataset.action === "open-distributor") await openDistributor(target.dataset.tenant);
  if (target.id === "addDistributorButton") {
    state.customerPage = "edit";
    state.selectedDistributorId = "";
    renderCustomerPage();
  }
  if (target.id === "backToDistributorsButton") {
    state.customerPage = "list";
    renderCustomerPage();
  }
  if (target.id === "editDistributorButton") {
    state.customerPage = "edit";
    renderCustomerPage();
  }
  if (target.id === "cancelDistributorEditButton") {
    state.customerPage = state.selectedDistributorId ? "detail" : "list";
    renderCustomerPage();
  }
  if (target.id === "authorizeMicrosoftButton") await authorizeMicrosoft();
  if (target.id === "testMailboxButton") await testMailbox();
  if (target.dataset.action === "inspect") showDetails(JSON.parse(target.dataset.payload));
  if (target.dataset.action === "resolve") await resolveTask(target.dataset.id, target.dataset.type);
  if (target.dataset.action === "reprocess") {
    showDetails(await post(`/console/orders/${target.dataset.run}/reprocess`, { source: "console" }));
    await refresh();
  }
  if (target.dataset.action === "timeline") {
    showDetails(await post(`/console/orders/${target.dataset.run}/timeline`, { source: "console" }));
  }
  if (target.dataset.action === "download") {
    showDetails(await post("/console/artifacts/download", {
      orderRunId: target.dataset.run,
      artifactId: target.dataset.artifact
    }));
  }
});

el("refreshButton").addEventListener("click", refresh);
el("customerFilter").addEventListener("change", refresh);
wireForms();
if (params.get("authStatus")) {
  state.customerPage = "detail";
  showDetails({ microsoftAuthStatus: params.get("authStatus"), connectionId: params.get("connectionId") });
}
refresh().catch(showDetails);
