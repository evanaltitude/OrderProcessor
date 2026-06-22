const params = new URLSearchParams(window.location.search);
const apiBase = params.get("apiBase")
  || window.localStorage.getItem("orderProcessorApiBase")
  || document.querySelector('meta[name="order-api-base"]')?.content
  || "/api";
const tenantId = params.get("tenantId") || "default";
const state = { dashboard: null };

const el = (id) => document.getElementById(id);
const value = (form, name) => new FormData(form).get(name)?.toString().trim() || "";
const split = (text) => text.split(",").map((part) => part.trim()).filter(Boolean);

async function post(path, body = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ tenantId, ...body })
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function showDetails(payload) {
  const pane = el("detailsPane");
  pane.innerHTML = `<pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>`;
  pane.classList.add("open");
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function statusPill(status) {
  const tone = status === "completed" || status === "matched" ? "good" : status === "failed" ? "bad" : "warn";
  return `<span class="pill ${tone}">${escapeHtml(status || "")}</span>`;
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

  el("routingRulesBody").innerHTML = (dashboard.routingRules || []).map((rule) => `
    <tr>
      <td>${escapeHtml(rule.priority ?? "")}</td>
      <td>${escapeHtml(rule.name || rule.id)}<br><span class="muted">${escapeHtml(rule.phase || "general")}</span></td>
      <td>${escapeHtml(rule.customerId || "")}</td>
      <td>${escapeHtml(rule.outcome || "")}</td>
      <td>${rule.enabled ? "Yes" : "No"}</td>
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

function renderDataStatus() {
  const customers = state.dashboard?.customerDataStatus || [];
  const items = new Map((state.dashboard?.itemDataStatus || []).map((entry) => [entry.customerId, entry]));
  const customerFilter = el("customerFilter");
  customerFilter.innerHTML = '<option value="">All end customers</option>' + (state.dashboard?.customers || [])
    .map((customer) => `<option value="${escapeHtml(customer.id)}">${escapeHtml(customer.name || customer.id)}</option>`)
    .join("");
  el("dataStatusBody").innerHTML = customers.map((customer) => {
    const itemStatus = items.get(customer.customerId) || {};
    return `
      <tr>
        <td>${escapeHtml(customer.name || customer.customerId)}</td>
        <td>${escapeHtml(customer.customerCode || "")}</td>
        <td>${escapeHtml(customer.lastImportedAt || "")}</td>
        <td>${itemStatus.itemCount || 0}</td>
        <td>${escapeHtml(itemStatus.lastImportedAt || "")}</td>
      </tr>
    `;
  }).join("");
}

function renderCustomerRules() {
  const rules = state.dashboard?.customerIdentificationRules || [];
  el("customerRulesBody").innerHTML = rules.map((rule) => `
    <tr>
      <td>${escapeHtml(rule.customerId || "")}</td>
      <td>${escapeHtml(rule.aliasType || "")}</td>
      <td>${escapeHtml(rule.value || "")}</td>
      <td>${escapeHtml(rule.confidence ?? "")}</td>
    </tr>
  `).join("");
}

function renderSession() {
  const session = state.dashboard?.session || {};
  el("sessionLine").textContent = session.authorized
    ? `${session.consoleUser.email} - ${session.isPlatformAdmin ? "platformAdmin" : "customerUser"}`
    : `${session.reason || "unauthorized"}`;
}

async function refresh() {
  const customerId = el("customerFilter").value;
  state.dashboard = await post("/console/dashboard", customerId ? { customerId } : {});
  renderSession();
  renderMetrics(state.dashboard.summary);
  renderRows();
  renderExceptions();
  renderArtifacts();
  renderDataStatus();
  renderCustomerRules();
}

function activeView(id) {
  document.querySelectorAll(".tab").forEach((button) => button.classList.toggle("active", button.dataset.view === id));
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === id));
}

async function resolveTask(id, type) {
  const resolution = { notes: "Resolved from console" };
  if (type === "itemValidation") {
    resolution.matchedInternalItemNumber = window.prompt("Internal item number") || "";
  } else if (type === "customerIdentification" || type === "routing") {
    resolution.selectedCustomerId = window.prompt("Customer id") || "";
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
    showDetails(await post("/console/tenants", {
      id: value(form, "id") || tenantId,
      name: value(form, "name"),
      environment: value(form, "environment")
    }));
    await refresh();
  });

  el("customerForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    showDetails(await post("/console/customers", {
      id: value(form, "id"),
      customerCode: value(form, "customerCode"),
      name: value(form, "name"),
      storeNumber: value(form, "storeNumber"),
      routeNumber: value(form, "routeNumber"),
      address1: value(form, "address1"),
      city: value(form, "city"),
      state: value(form, "state"),
      postalCode: value(form, "postalCode"),
      phone: value(form, "phone"),
      website: value(form, "website"),
      customerEmail: value(form, "customerEmail"),
      aliases: split(value(form, "aliases")),
      knownSubjectPatterns: split(value(form, "knownSubjectPatterns")),
      csrEmail: value(form, "csrEmail"),
      csrFolder: value(form, "csrFolder")
    }));
    await refresh();
  });

  el("mailboxForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    showDetails(await post("/console/mailboxes", {
      mailboxAddress: value(form, "mailboxAddress"),
      displayName: value(form, "displayName"),
      connectionId: value(form, "connectionId"),
      enabled: form.enabled.checked
    }));
    await refresh();
  });

  el("customerRuleForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    showDetails(await post("/console/customer-identification-rules", {
      customerId: value(form, "customerId"),
      aliasType: value(form, "aliasType"),
      value: value(form, "value"),
      confidence: value(form, "confidence") ? Number(value(form, "confidence")) : 1
    }));
    await refresh();
  });

  el("routingForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    showDetails(await post("/console/routing-rules", {
      id: value(form, "id"),
      customerId: value(form, "customerId"),
      name: value(form, "name"),
      phase: value(form, "phase"),
      outcome: value(form, "outcome"),
      priority: value(form, "priority") ? Number(value(form, "priority")) : 100,
      processorProfileId: value(form, "processorProfileId"),
      mailboxAccountIds: split(value(form, "mailboxAccountIds")),
      mailboxAddresses: split(value(form, "mailboxAddresses")),
      senderEquals: split(value(form, "senderEquals")),
      senderDomains: split(value(form, "senderDomains")),
      subjectRegex: split(value(form, "subjectRegex")),
      bodyRegex: split(value(form, "bodyRegex")),
      knownWebstorePatterns: split(value(form, "knownWebstorePatterns")),
      priorProcessedSubjectRegex: split(value(form, "priorProcessedSubjectRegex")),
      attachmentExtensions: split(value(form, "attachmentExtensions")),
      attachmentNameRegex: split(value(form, "attachmentNameRegex")),
      tags: split(value(form, "tags")),
      customerCodeSource: value(form, "customerCodeSource"),
      customerCodeRegex: value(form, "customerCodeRegex"),
      customerCodeGroup: value(form, "customerCodeGroup") || "customerCode",
      subjectTemplate: value(form, "subjectTemplate"),
      categoryCsrField: value(form, "categoryCsrField"),
      categoryTemplates: split(value(form, "categoryTemplates")),
      processedMoveMode: value(form, "processedMoveMode"),
      processedMoveCustomerField: value(form, "processedMoveCustomerField"),
      processedMoveFolder: value(form, "processedMoveFolder"),
      failedMoveMode: value(form, "failedMoveMode"),
      failedMoveCustomerField: value(form, "failedMoveCustomerField"),
      failedMoveFolder: value(form, "failedMoveFolder"),
      nonOrderMoveMode: value(form, "nonOrderMoveMode"),
      nonOrderMoveCustomerField: value(form, "nonOrderMoveCustomerField"),
      nonOrderMoveFolder: value(form, "nonOrderMoveFolder")
    }));
    await refresh();
  });

  el("profileForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const settings = value(form, "settings") ? JSON.parse(value(form, "settings")) : {};
    const body = { customerId: value(form, "customerId"), name: value(form, "name"), settings };
    const path = value(form, "kind") === "processor" ? "/console/processor-profiles" : "/console/output-profiles";
    if (value(form, "kind") === "processor") body.processorType = value(form, "type");
    else body.outputType = value(form, "type");
    showDetails(await post(path, body));
    await refresh();
  });

  el("userForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    showDetails(await post("/console/users", {
      email: value(form, "email"),
      displayName: value(form, "displayName"),
      roles: split(value(form, "roles")),
      enabled: form.enabled.checked
    }));
    await refresh();
  });

  el("assignmentForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    showDetails(await post(`/console/customers/${value(form, "customerId")}/users`, {
      email: value(form, "email"),
      roles: split(value(form, "roles")),
      enabled: form.enabled.checked
    }));
    await refresh();
  });
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  if (target.classList.contains("tab")) activeView(target.dataset.view);
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
refresh().catch(showDetails);
