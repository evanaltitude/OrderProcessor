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
  customerPage: "list",
  pendingRequests: 0,
  lists: {
    customers: { items: [], page: { total: 0, limit: 100, offset: 0, hasNext: false, hasPrevious: false }, loaded: false },
    items: { items: [], page: { total: 0, limit: 100, offset: 0, hasNext: false, hasPrevious: false }, loaded: false }
  },
  outputs: { outputArtifacts: [], page: { total: 0, limit: 100, offset: 0, hasNext: false, hasPrevious: false }, loaded: false },
  listTimers: {}
};

const el = (id) => document.getElementById(id);
const value = (form, name) => new FormData(form).get(name)?.toString().trim() || "";
const split = (text) => text.split(",").map((part) => part.trim()).filter(Boolean);
const checked = (form, name) => Boolean(form.elements[name]?.checked);

const DEFAULT_ROUTING_PHASES = ["webstoreOrder", "previouslyProcessed", "orderCandidate", "nonOrder", "general"];
const ROUTING_PATHS = {
  webstoreOrder: {
    label: "Webstore order receipt",
    summary: "Fixed receipt format. Extract customer code, update subject, add CSR tags, and move the email without creating an order file.",
    phase: "webstoreOrder",
    outcome: "knownCustomerNonOrder",
    customerCodeSource: "subject",
    requiredAttachment: false,
    processedMoveMode: "none",
    processedMoveTarget: "",
    nonOrderMoveMode: "customerField",
    nonOrderMoveTarget: "csrFolder"
  },
  previouslyIdentified: {
    label: "Previously identified email chain",
    summary: "Subject already has the customer code format. Extract the code from the subject, refresh subject/tags, and move to the CSR folder.",
    phase: "previouslyProcessed",
    outcome: "knownCustomerNonOrder",
    customerCodeSource: "subject",
    priorProcessedSubjectRegex: "Cust:\\s*(?P<customerCode>\\d+)",
    requiredAttachment: false,
    processedMoveMode: "none",
    processedMoveTarget: "",
    nonOrderMoveMode: "customerField",
    nonOrderMoveTarget: "csrFolder"
  },
  generalNonOrder: {
    label: "General non-order email",
    summary: "General message. Identify the customer from sender/content, update subject, add CSR tags, and move to the CSR folder.",
    phase: "nonOrder",
    outcome: "knownCustomerNonOrder",
    customerCodeSource: "combined",
    requiredAttachment: false,
    processedMoveMode: "none",
    processedMoveTarget: "",
    nonOrderMoveMode: "customerField",
    nonOrderMoveTarget: "csrFolder"
  },
  orderEmail: {
    label: "Order email",
    summary: "Order to process. Identify the customer, create the order run and universal output, then update/tag/move the source email.",
    phase: "orderCandidate",
    outcome: "knownOrder",
    customerCodeSource: "combined",
    requiredAttachment: true,
    processedMoveMode: "customerField",
    processedMoveTarget: "csrFolder",
    nonOrderMoveMode: "none",
    nonOrderMoveTarget: ""
  }
};
const SYSTEM_TENANT_ID = "__system__";
const SUPPORTED_FILE_TYPE_OPTIONS = [
  { value: "csv", label: "CSV" },
  { value: "xlsx", label: "Excel XLSX" },
  { value: "xls", label: "Excel XLS" },
  { value: "xlt", label: "Excel XLT" },
  { value: "pdf", label: "PDF" },
  { value: "txt", label: "Text" },
  { value: "emailBody", label: "Email Body" }
];
const DEFAULT_SUPPORTED_FILE_TYPES = SUPPORTED_FILE_TYPE_OPTIONS.map((option) => option.value);
const DEFAULT_OUTPUT_FIELDS = [
  "po_number",
  "order_number",
  "line_number",
  "quantity",
  "provided_item_number",
  "provided_upc",
  "description",
  "matched_internal_item_number",
  "validation_status"
];
const OUTPUT_FIELD_LABELS = {
  po_number: "PO",
  order_number: "Order #",
  line_number: "Line",
  quantity: "Quantity",
  provided_item_number: "Customer item",
  provided_upc: "UPC",
  description: "Description",
  matched_internal_item_number: "ERP item",
  validation_status: "Status",
  validation_confidence: "Confidence",
  customer_id: "Customer ID",
  order_run_id: "Run ID"
};
const CUSTOMER_LIST_BASE_COLUMNS = [
  { key: "customerCode", label: "Account" },
  { key: "name", label: "Name" },
  { key: "storeNumber", label: "Store" },
  { key: "routeNumber", label: "Route" },
  { key: "csrEmail", label: "CSR Email" },
  { key: "csrFolder", label: "CSR Folder" },
  { key: "address1", label: "Address" },
  { key: "city", label: "City" },
  { key: "state", label: "State" },
  { key: "postalCode", label: "Zip" },
  { key: "phone", label: "Phone" },
  { key: "website", label: "Website" },
  { key: "customerEmail", label: "Customer Email" },
  { key: "lastImportedAt", label: "Last Update" },
  { key: "sourceName", label: "Source" },
  { key: "id", label: "Record ID" }
];
const ITEM_LIST_BASE_COLUMNS = [
  { key: "customerId", label: "Customer" },
  { key: "internalItemNumber", label: "Item" },
  { key: "description", label: "Description" },
  { key: "upc", label: "UPC" },
  { key: "alternateIds", label: "Alternate IDs" },
  { key: "customerItemNumbers", label: "Customer Item Numbers" },
  { key: "altPartsCombined", label: "Alt Parts Combined" },
  { key: "aliases", label: "Aliases" },
  { key: "lastImportedAt", label: "Last Update" },
  { key: "sourceName", label: "Source" },
  { key: "id", label: "Record ID" }
];

function slugifyId(text) {
  return String(text || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

function connectionIdFor(tenantId, mailboxAddress) {
  const mailboxKey = slugifyId(String(mailboxAddress || "").split("@")[0] || "mailbox") || "mailbox";
  return `m365-${slugifyId(tenantId) || "customer"}-${mailboxKey}`;
}

function setValue(form, name, nextValue) {
  if (!form.elements[name]) return;
  form.elements[name].value = nextValue ?? "";
}

function setChecked(form, name, nextValue) {
  if (!form.elements[name]) return;
  form.elements[name].checked = Boolean(nextValue);
}

function listText(values) {
  return (values || []).filter(Boolean).join(", ");
}

function moveTarget(move = {}) {
  return move.mode === "customerField" ? move.field || "" : move.folder || "";
}

function moveFromForm(form, prefix) {
  const mode = value(form, `${prefix}MoveMode`) || "none";
  const target = value(form, `${prefix}MoveTarget`);
  return {
    mode,
    folder: mode === "staticFolder" ? target : "",
    field: mode === "customerField" ? target : ""
  };
}

function compactObject(object) {
  return Object.fromEntries(
    Object.entries(object).filter(([, nextValue]) => {
      if (Array.isArray(nextValue)) return nextValue.length > 0;
      if (nextValue && typeof nextValue === "object") return Object.keys(nextValue).length > 0;
      return nextValue !== "" && nextValue !== null && nextValue !== undefined;
    })
  );
}

function fillSelect(select, options, selectedValue = "") {
  select.innerHTML = options
    .map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`)
    .join("");
  select.value = selectedValue || "";
}

function outputProfileOptions(selectedValue = "") {
  return [
    { value: "", label: "None" },
    ...(state.dashboard?.outputProfiles || []).map((profile) => ({
      value: profile.id,
      label: profile.name || profile.id
    }))
  ].map((option) => ({ ...option, selected: option.value === selectedValue }));
}

function processorProfileOptions(selectedValue = "") {
  return [
    { value: "", label: "None" },
    ...(state.dashboard?.processorProfiles || []).map((profile) => ({
      value: profile.id,
      label: profile.name || profile.id
    }))
  ].map((option) => ({ ...option, selected: option.value === selectedValue }));
}

function csrDirectoryOptions(selectedValue = "") {
  return [
    { value: "", label: "Choose CSR" },
    ...(state.dashboard?.csrDirectory || []).map((csr) => ({
      value: csr.id || `${csr.name || ""}|${csr.folder || ""}|${csr.email || ""}`,
      label: csr.label || csr.name || csr.folder || csr.email || "CSR"
    }))
  ].map((option) => ({ ...option, selected: option.value === selectedValue }));
}

function selectedCsr(directoryKey = "") {
  return (state.dashboard?.csrDirectory || []).find((csr) => (
    (csr.id || `${csr.name || ""}|${csr.folder || ""}|${csr.email || ""}`) === directoryKey
  )) || null;
}

function selectOptions(options) {
  return options
    .map((option) => `<option value="${escapeHtml(option.value)}"${option.selected ? " selected" : ""}>${escapeHtml(option.label)}</option>`)
    .join("");
}

function beginBusy(label = "Working") {
  state.pendingRequests += 1;
  el("busyText").textContent = label;
  el("busyOverlay").classList.remove("hidden");
  document.body.classList.add("is-busy");
}

function endBusy() {
  state.pendingRequests = Math.max(0, state.pendingRequests - 1);
  if (state.pendingRequests === 0) {
    el("busyOverlay").classList.add("hidden");
    document.body.classList.remove("is-busy");
  }
}

async function post(path, body = {}, options = {}) {
  const tenantId = options.tenantId || state.tenantId;
  const showBusy = options.quiet !== true;
  if (showBusy) beginBusy(options.busyText || "Working");
  try {
    const response = await fetch(`${apiBase}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ tenantId, ...body })
    });
    const payload = await responsePayload(response);
    if (payload?.error === "invalidJsonResponse") {
      const error = new Error(payload.message);
      error.payload = payload;
      throw error;
    }
    if (redirectToMicrosoftSignIn(payload, response)) {
      return new Promise(() => {});
    }
    if (!response.ok) {
      const error = new Error(payload?.message || `${response.status} ${response.statusText}`);
      error.payload = payload;
      throw error;
    }
    return payload || {};
  } finally {
    if (showBusy) endBusy();
  }
}

async function responsePayload(response) {
  const contentType = response.headers.get("content-type") || "";
  const text = await response.text();
  if (!text.trim()) return null;
  if (contentType.includes("application/json")) {
    try {
      return JSON.parse(text);
    } catch {
      return {
        error: "invalidJsonResponse",
        message: "The server returned a malformed JSON response.",
        raw: text.slice(0, 1000)
      };
    }
  }
  return { message: text.slice(0, 1000), raw: text.slice(0, 1000) };
}

function actionFailed(result) {
  return Boolean(result?.error || result?.session?.authorized === false);
}

function isMissingMicrosoftPrincipal(payload) {
  const session = payload?.session || payload;
  return session?.authorized === false
    && session?.reason === "missingMicrosoftPrincipal"
    && session?.requiredAuthProvider === "microsoft";
}

function consoleLoginUrl() {
  const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}` || "/";
  return `/.auth/login/aad?post_login_redirect_uri=${encodeURIComponent(returnTo)}`;
}

function redirectToMicrosoftSignIn(payload, response) {
  if (isMissingMicrosoftPrincipal(payload) || (response?.status === 401 && !payload)) {
    window.location.replace(consoleLoginUrl());
    return true;
  }
  return false;
}

function showActionResult(result) {
  showDetails(result);
  return !actionFailed(result);
}

function activeConsoleView() {
  return document.querySelector(".tab.active")?.dataset.view || "monitor";
}

let detailsTimer = null;

function clearDetails() {
  const pane = el("detailsPane");
  pane.classList.remove("open");
  pane.innerHTML = "";
  if (detailsTimer) {
    window.clearTimeout(detailsTimer);
    detailsTimer = null;
  }
}

function payloadForError(error) {
  if (error?.payload) return error.payload;
  return { error: "actionFailed", message: error?.message || String(error || "Unknown error") };
}

function showDetails(payload, options = {}) {
  const pane = el("detailsPane");
  const title = options.title || (actionFailed(payload) ? "Needs Attention" : "Done");
  pane.innerHTML = `
    <div class="details-header">
      <strong>${escapeHtml(title)}</strong>
      <button id="closeDetailsButton" class="icon-button" type="button" aria-label="Close details">x</button>
    </div>
    <pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>
  `;
  pane.classList.add("open");
  if (detailsTimer) window.clearTimeout(detailsTimer);
  const autoCloseMs = options.autoCloseMs ?? (actionFailed(payload) ? 14000 : 7000);
  detailsTimer = window.setTimeout(clearDetails, autoCloseMs);
}

function setFormStatus(id, message = "", tone = "") {
  const target = el(id);
  if (!target) return;
  target.textContent = message;
  target.className = `form-status ${tone || ""}`.trim();
  target.classList.toggle("hidden", !message);
}

function errorMessage(errorOrPayload) {
  const payload = errorOrPayload?.payload || errorOrPayload || {};
  return payload.message || payload.error || errorOrPayload?.message || "Something went wrong.";
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

function populateProfileSelects() {
  fillSelect(el("routingForm").elements.processorProfileId, processorProfileOptions(), "");
  fillSelect(el("processorProfileForm").elements.outputProfileId, outputProfileOptions(), "");
}

function populateAutomationSettings(settings = {}) {
  const form = el("automationSettingsForm");
  const orderConditions = settings.orderConditions || {};
  const webstore = settings.webstoreEmailConditions || {};
  const extraction = settings.extractionRules || {};
  const subject = settings.emailSubjectSettings || {};
  const moves = subject.moves || {};

  setValue(form, "webstoreSenderDomains", listText(webstore.senderDomains || []));
  setValue(form, "webstoreSubjectPatterns", listText(webstore.subjectPatterns || []));
  setValue(form, "customerCodeSource", extraction.customerCodeSource || extraction.source || "combined");
  setValue(form, "customerCodeRegex", extraction.customerCodeRegex || extraction.regex || "");
  setValue(form, "subjectTemplate", subject.template || "");
  setValue(form, "categoryTemplates", listText(subject.categoryTemplates || []));
  setChecked(form, "requiredAttachments", Boolean(orderConditions.requiredAttachments || orderConditions.requiredAttachment));

  for (const [prefix, key] of [["processed", "processedOrder"], ["failed", "failedOrder"], ["nonOrder", "nonOrder"]]) {
    const move = moves[key] || {};
    setValue(form, `${prefix}MoveMode`, move.mode || "none");
    setValue(form, `${prefix}MoveTarget`, moveTarget(move));
  }
}

function automationSettingsFromForm(form) {
  const moves = {
    processedOrder: moveFromForm(form, "processed"),
    failedOrder: moveFromForm(form, "failed"),
    nonOrder: moveFromForm(form, "nonOrder")
  };
  return {
    routingSettings: compactObject({
      routingPhases: DEFAULT_ROUTING_PHASES
    }),
    orderConditions: compactObject({
      requiredAttachments: checked(form, "requiredAttachments")
    }),
    webstoreEmailConditions: compactObject({
      senderDomains: split(value(form, "webstoreSenderDomains")),
      subjectPatterns: split(value(form, "webstoreSubjectPatterns"))
    }),
    extractionRules: compactObject({
      customerCodeSource: value(form, "customerCodeSource"),
      customerCodeRegex: value(form, "customerCodeRegex"),
      customerCodeGroup: "customerCode"
    }),
    emailSubjectSettings: compactObject({
      template: value(form, "subjectTemplate"),
      categoryTemplates: split(value(form, "categoryTemplates")),
      moves
    })
  };
}

function systemSettings() {
  return state.dashboard?.systemSettings || {};
}

function supportedFileTypes() {
  const settings = systemSettings().supportedFileTypes || {};
  const values = settings.orderInputExtensions || settings.extensions || DEFAULT_SUPPORTED_FILE_TYPES;
  return [...new Set(values.map((item) => String(item || "").trim()).filter(Boolean))];
}

function customSupportedFileTypes(selected = supportedFileTypes()) {
  const builtIns = new Set(SUPPORTED_FILE_TYPE_OPTIONS.map((option) => option.value.toLowerCase()));
  return selected.filter((item) => !builtIns.has(item.toLowerCase()));
}

function renderSystemSettings() {
  const selected = new Set(supportedFileTypes());
  el("supportedFileTypeChoices").innerHTML = SUPPORTED_FILE_TYPE_OPTIONS.map((option) => `
    <label class="checkline">
      <input name="supportedFileTypes" type="checkbox" value="${escapeHtml(option.value)}" ${selected.has(option.value) ? "checked" : ""}>
      ${escapeHtml(option.label)}
    </label>
  `).join("");
  setValue(el("systemSettingsForm"), "customFileTypes", listText(customSupportedFileTypes([...selected])));
}

function systemSettingsFromForm(form) {
  const selected = [...form.querySelectorAll('input[name="supportedFileTypes"]:checked')].map((input) => input.value);
  const custom = split(value(form, "customFileTypes")).map((item) => item.replace(/^\./, ""));
  return {
    ...systemSettings(),
    supportedFileTypes: {
      ...(systemSettings().supportedFileTypes || {}),
      orderInputExtensions: [...new Set([...selected, ...custom].map((item) => item.trim()).filter(Boolean))]
    }
  };
}

function outputFieldValues(settings = {}) {
  return settings.fields || settings.columns || DEFAULT_OUTPUT_FIELDS;
}

function renderOutputFieldChoices(selectedFields = DEFAULT_OUTPUT_FIELDS) {
  const selected = new Set(selectedFields);
  const fields = [...new Set([...DEFAULT_OUTPUT_FIELDS, "validation_confidence", "customer_id", "order_run_id"])];
  el("outputFieldChoices").innerHTML = fields
    .map((field) => `
      <label class="checkline">
        <input name="outputFields" type="checkbox" value="${escapeHtml(field)}" ${selected.has(field) ? "checked" : ""}>
        ${escapeHtml(OUTPUT_FIELD_LABELS[field] || field)}
      </label>
    `)
    .join("");
}

function selectedOutputFields() {
  return [...document.querySelectorAll('input[name="outputFields"]:checked')].map((input) => input.value);
}

function routingPathForRule(rule = {}) {
  if (rule.phase === "webstoreOrder") return "webstoreOrder";
  if (rule.phase === "previouslyProcessed") return "previouslyIdentified";
  if (rule.outcome === "knownOrder" || rule.phase === "orderCandidate") return "orderEmail";
  if (rule.outcome === "knownCustomerNonOrder") return "generalNonOrder";
  return "generalNonOrder";
}

function routingPathForForm(form) {
  return routingPathForRule({
    phase: value(form, "phase"),
    outcome: value(form, "outcome")
  });
}

function renderRoutingPathSummary() {
  const form = el("routingForm");
  const path = ROUTING_PATHS[value(form, "routingPath")] || ROUTING_PATHS.generalNonOrder;
  el("routingPathSummary").innerHTML = `
    <strong>${escapeHtml(path.label)}</strong>
    <span>${escapeHtml(path.summary)}</span>
  `;
}

function applyRoutingPathDefaults() {
  const form = el("routingForm");
  const path = ROUTING_PATHS[value(form, "routingPath")] || ROUTING_PATHS.generalNonOrder;
  setValue(form, "phase", path.phase);
  setValue(form, "outcome", path.outcome);
  setValue(form, "customerCodeSource", path.customerCodeSource);
  setChecked(form, "requiredAttachment", path.requiredAttachment);
  setValue(form, "processedMoveMode", path.processedMoveMode);
  setValue(form, "processedMoveTarget", path.processedMoveTarget);
  setValue(form, "nonOrderMoveMode", path.nonOrderMoveMode);
  setValue(form, "nonOrderMoveTarget", path.nonOrderMoveTarget);
  if (path.priorProcessedSubjectRegex && !value(form, "priorProcessedSubjectRegex")) {
    setValue(form, "priorProcessedSubjectRegex", path.priorProcessedSubjectRegex);
  }
  if (path.outcome !== "knownOrder") {
    setValue(form, "processorProfileId", "");
  }
  renderRoutingPathSummary();
}

function clearRoutingForm() {
  const form = el("routingForm");
  form.reset();
  setValue(form, "routingPath", "webstoreOrder");
  setValue(form, "priority", "100");
  setChecked(form, "enabled", true);
  fillSelect(form.elements.processorProfileId, processorProfileOptions(), "");
  applyRoutingPathDefaults();
}

function loadRoutingRule(ruleId) {
  const rule = (state.dashboard?.routingRules || []).find((item) => item.id === ruleId);
  if (!rule) return;
  const form = el("routingForm");
  setValue(form, "routingPath", routingPathForRule(rule));
  setValue(form, "id", rule.id);
  setValue(form, "name", rule.name || "");
  setValue(form, "phase", rule.phase || "general");
  setValue(form, "outcome", rule.outcome || "needsHumanReview");
  setValue(form, "priority", rule.priority ?? 100);
  fillSelect(form.elements.processorProfileId, processorProfileOptions(), rule.processorProfileId || "");
  setValue(form, "senderEquals", listText(rule.senderEquals || []));
  setValue(form, "senderDomains", listText(rule.senderDomains || []));
  setValue(form, "subjectRegex", listText(rule.subjectRegex || []));
  setValue(form, "bodyRegex", listText(rule.bodyRegex || []));
  setValue(form, "attachmentExtensions", listText(rule.attachmentExtensions || []));
  setValue(form, "attachmentNameRegex", listText(rule.attachmentNameRegex || []));
  setValue(form, "priorProcessedSubjectRegex", listText(rule.priorProcessedSubjectRegex || []));
  setValue(form, "customerCodeSource", rule.customerCodeExtraction?.source || "combined");
  setValue(form, "customerCodeRegex", rule.customerCodeExtraction?.regex || "");
  setValue(form, "subjectTemplate", rule.subjectUpdate?.template || "");
  setValue(form, "categoryTemplates", listText(rule.emailActions?.categoryTemplates || []));
  const processedMove = rule.emailActions?.moves?.processedOrder || {};
  setValue(form, "processedMoveMode", processedMove.mode || "none");
  setValue(form, "processedMoveTarget", moveTarget(processedMove));
  const nonOrderMove = rule.emailActions?.moves?.nonOrder || {};
  setValue(form, "nonOrderMoveMode", nonOrderMove.mode || "none");
  setValue(form, "nonOrderMoveTarget", moveTarget(nonOrderMove));
  setChecked(form, "requiredAttachment", rule.requiredAttachment);
  setChecked(form, "enabled", rule.enabled !== false);
  renderRoutingPathSummary();
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

function syncRoutingDefaultsForPhase() {
  const form = el("routingForm");
  if (value(form, "phase") === "webstoreOrder" && value(form, "outcome") === "knownOrder") {
    setValue(form, "outcome", "knownCustomerNonOrder");
  }
  setValue(form, "routingPath", routingPathForForm(form));
  renderRoutingPathSummary();
}

function clearProcessorProfileForm() {
  const form = el("processorProfileForm");
  form.reset();
  setValue(form, "processorType", "csv");
  setValue(form, "delimiter", "");
  setValue(form, "webhookUrl", "");
  setValue(form, "webhookTimeoutSeconds", "");
  setChecked(form, "hasHeader", true);
  fillSelect(form.elements.outputProfileId, outputProfileOptions(), "");
}

function loadProcessorProfile(profileId) {
  const profile = (state.dashboard?.processorProfiles || []).find((item) => item.id === profileId);
  if (!profile) return;
  const form = el("processorProfileForm");
  const settings = profile.settings || {};
  const fieldMap = settings.fieldMap || {};
  setValue(form, "id", profile.id);
  setValue(form, "name", profile.name || "");
  setValue(form, "processorType", profile.processorType || "csv");
  fillSelect(form.elements.outputProfileId, outputProfileOptions(), profile.outputProfileId || "");
  setValue(form, "webhookUrl", settings.webhookUrl || "");
  setValue(form, "webhookTimeoutSeconds", settings.timeoutSeconds || "");
  setValue(form, "delimiter", settings.delimiter || "");
  setValue(form, "headerlessColumns", listText(settings.headerlessColumns || settings.columns || []));
  setValue(form, "itemNumberField", fieldMap.provided_item_number || "");
  setValue(form, "upcField", fieldMap.provided_upc || "");
  setValue(form, "quantityField", fieldMap.quantity || "");
  setValue(form, "descriptionField", fieldMap.description || "");
  setValue(form, "poNumberField", fieldMap.po_number || "");
  setValue(form, "orderNumberField", fieldMap.order_number || "");
  setValue(form, "linePattern", settings.linePattern || "");
  setValue(form, "baseProcessorType", settings.baseProcessorType || "");
  setValue(form, "documentIntelligenceModelId", settings.documentIntelligenceModelId || "");
  setChecked(form, "hasHeader", settings.hasHeader !== false);
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

function clearOutputProfileForm() {
  const form = el("outputProfileForm");
  form.reset();
  setValue(form, "outputType", "csv");
  setValue(form, "delimiter", ",");
  setValue(form, "encoding", "utf-8");
  setValue(form, "destinationAdapter", "blob");
  setChecked(form, "includeHeader", true);
  renderOutputFieldChoices(DEFAULT_OUTPUT_FIELDS);
}

function loadOutputProfile(profileId) {
  const profile = (state.dashboard?.outputProfiles || []).find((item) => item.id === profileId);
  if (!profile) return;
  const form = el("outputProfileForm");
  const settings = profile.settings || {};
  const destination = profile.destination || {};
  setValue(form, "id", profile.id);
  setValue(form, "name", profile.name || "");
  setValue(form, "outputType", profile.outputType || "csv");
  setValue(form, "fileNameTemplate", settings.fileNameTemplate || "");
  setValue(form, "delimiter", settings.delimiter || ",");
  setValue(form, "encoding", settings.encoding || "utf-8");
  setValue(form, "textTemplate", settings.template || "");
  setValue(form, "formats", listText(settings.formats || settings.outputTypes || []));
  setValue(form, "destinationAdapter", destination.adapter || "blob");
  setValue(form, "destinationFolder", destination.folder || "");
  setValue(form, "destinationUrl", destination.url || settings.url || "");
  setChecked(form, "includeHeader", settings.includeHeader !== false);
  setChecked(form, "productionDeliveryEnabled", destination.productionDeliveryEnabled === true);
  renderOutputFieldChoices(outputFieldValues(settings));
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

function latestDate(values) {
  return values
    .map((value) => value?.lastImportedAt || value?.updatedAt || value?.createdAt || "")
    .filter(Boolean)
    .sort()
    .at(-1) || "";
}

function compactJson(value) {
  return JSON.stringify(value || {});
}

function partitionText(target = {}) {
  const paths = Array.isArray(target.partitionKeyPath) ? target.partitionKeyPath : [target.partitionKeyPath];
  const values = Array.isArray(target.partitionKeyValue) ? target.partitionKeyValue : [target.partitionKeyValue];
  return paths.map((path, index) => `${path}: ${values[index] ?? ""}`).join(" + ");
}

function importTargetHtml(target = {}, targets = {}) {
  const cosmos = targets.cosmos || {};
  const auth = targets.authentication || {};
  const cosmosLocation = [
    cosmos.accountName || cosmos.endpoint || "Cosmos account",
    cosmos.databaseName || "database",
    target.containerName || "container"
  ].filter(Boolean).join(" / ");
  return `
    <dl>
      <div>
        <dt>Power Automate POST</dt>
        <dd>${escapeHtml(target.apiUrl || target.apiPath || "")}</dd>
      </div>
      <div>
        <dt>Cosmos location</dt>
        <dd>${escapeHtml(cosmosLocation)}</dd>
      </div>
      <div>
        <dt>Partition</dt>
        <dd>${escapeHtml(partitionText(target))}</dd>
      </div>
      <div>
        <dt>Cadence</dt>
        <dd>${escapeHtml(target.cadence || "")}</dd>
      </div>
      <div>
        <dt>Auth header</dt>
        <dd>${escapeHtml(auth.header || "")}</dd>
      </div>
      <div>
        <dt>Minimum body</dt>
        <dd>${escapeHtml(compactJson(target.minimumBody))}</dd>
      </div>
    </dl>
  `;
}

function renderImportTargets() {
  const targets = state.dashboard?.importTargets || {};
  el("customerListTarget").innerHTML = importTargetHtml(targets.customerList, targets);
  el("itemListTarget").innerHTML = importTargetHtml(targets.itemList, targets);
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

function pathLabel(pathway = "") {
  const labels = {
    webstoreOrder: "Webstore order",
    previouslyProcessed: "Known chain",
    orderCandidate: "Order processing",
    orderProcessing: "Order processing",
    nonOrder: "Inquiry",
    general: "Inquiry",
    customerIdentification: "Customer ID",
    humanReview: "Review"
  };
  return labels[pathway] || pathway || "";
}

function monitorCustomer(entry = {}) {
  const code = entry.customerCode || entry.customerId || "";
  const name = entry.customerName || "";
  return [code, name].filter(Boolean).join(" - ");
}

function monitorEmailLink(entry = {}) {
  if (!entry.emailUrl) return "";
  return `<a class="table-link" href="${escapeHtml(entry.emailUrl)}" target="_blank" rel="noopener">Open</a>`;
}

function monitorOrderCell(entry = {}) {
  const orderLabel = entry.poNumber || entry.orderNumber || entry.orderRunId || "";
  const details = [
    entry.lineCount == null ? "" : `${entry.lineCount} lines`,
    entry.artifactCount == null ? "" : `${entry.artifactCount} files`
  ].filter(Boolean).join(" / ");
  const actions = entry.orderRunId ? `
    <div class="row-actions">
      <button class="secondary" data-action="timeline" data-run="${escapeHtml(entry.orderRunId)}" type="button">Timeline</button>
      <button class="secondary" data-action="reprocess" data-run="${escapeHtml(entry.orderRunId)}" type="button">Reprocess</button>
    </div>
  ` : "";
  return `
    <div>${escapeHtml(orderLabel)}</div>
    <div class="muted">${escapeHtml(details)}</div>
    ${actions}
  `;
}

function monitorActionCell(entry = {}) {
  const action = entry.actionTaken || "";
  const movedText = entry.movedTo ? `Moved to ${entry.movedTo}` : "";
  const alreadyIncludesMove = action.toLowerCase().includes(movedText.toLowerCase());
  return [action, alreadyIncludesMove ? "" : movedText].filter(Boolean).map(escapeHtml).join("<br>");
}

function emptyTableRow(colspan, message) {
  return `<tr><td colspan="${colspan}" class="muted">${escapeHtml(message)}</td></tr>`;
}

function activeMonitorRow(entry = {}) {
  return `
    <tr>
      <td>${escapeHtml(entry.receivedAt || entry.updatedAt || "")}</td>
      <td>${escapeHtml(pathLabel(entry.pathway))}</td>
      <td>${statusPill(entry.status)}</td>
      <td class="wrap-cell">${escapeHtml(entry.sender || "")}</td>
      <td class="wrap-cell">${escapeHtml(entry.recipient || "")}</td>
      <td class="wrap-cell">${escapeHtml(entry.subject || "")}</td>
      <td class="wrap-cell">${escapeHtml(monitorCustomer(entry))}</td>
      <td class="wrap-cell">${escapeHtml(entry.csr || entry.csrEmail || "")}</td>
      <td class="wrap-cell">${monitorActionCell(entry)}</td>
      <td>${monitorEmailLink(entry)}</td>
    </tr>
  `;
}

function processedOrderRow(entry = {}) {
  return `
    <tr>
      <td>${escapeHtml(entry.receivedAt || entry.updatedAt || "")}</td>
      <td>${statusPill(entry.status)}</td>
      <td class="wrap-cell">${escapeHtml(entry.sender || "")}</td>
      <td class="wrap-cell">${escapeHtml(entry.subject || "")}</td>
      <td class="wrap-cell">${escapeHtml(monitorCustomer(entry))}</td>
      <td class="wrap-cell">${escapeHtml(entry.csr || entry.csrEmail || "")}</td>
      <td class="wrap-cell">${monitorOrderCell(entry)}</td>
      <td class="wrap-cell">${monitorActionCell(entry)}</td>
      <td>${monitorEmailLink(entry)}</td>
    </tr>
  `;
}

function completedEmailRow(entry = {}) {
  return `
    <tr>
      <td>${escapeHtml(entry.receivedAt || entry.updatedAt || "")}</td>
      <td>${statusPill(entry.status)}</td>
      <td class="wrap-cell">${escapeHtml(entry.sender || "")}</td>
      <td class="wrap-cell">${escapeHtml(entry.subject || "")}</td>
      <td class="wrap-cell">${escapeHtml(monitorCustomer(entry))}</td>
      <td class="wrap-cell">${escapeHtml(entry.csr || entry.csrEmail || "")}</td>
      <td class="wrap-cell">${escapeHtml(entry.categorizedAs || "")}</td>
      <td class="wrap-cell">${monitorActionCell(entry)}</td>
      <td>${monitorEmailLink(entry)}</td>
    </tr>
  `;
}

function renderRows() {
  const dashboard = state.dashboard || {};
  const monitor = dashboard.monitor || {};
  const active = monitor.active || [];
  const processedOrders = monitor.processedOrders || [];
  const webstoreOrders = monitor.webstoreOrders || [];
  const nonOrderEmails = monitor.nonOrderEmails || [];
  el("activeRunsBody").innerHTML = active.length
    ? active.map(activeMonitorRow).join("")
    : emptyTableRow(10, "No active email processing.");
  el("processedOrdersBody").innerHTML = processedOrders.length
    ? processedOrders.map(processedOrderRow).join("")
    : emptyTableRow(9, "No processed orders yet.");
  el("webstoreOrdersBody").innerHTML = webstoreOrders.length
    ? webstoreOrders.map(completedEmailRow).join("")
    : emptyTableRow(9, "No webstore order emails completed yet.");
  el("nonOrderEmailsBody").innerHTML = nonOrderEmails.length
    ? nonOrderEmails.map(completedEmailRow).join("")
    : emptyTableRow(9, "No non-order emails completed yet.");
}

function exceptionCard(task) {
  return `
    <article class="task">
      <header>
        <strong>${escapeHtml(task.type)}</strong>
        ${statusPill(task.status)}
      </header>
      <div>${escapeHtml(task.exception || task.prompt || task.id)}</div>
      <div class="task-meta">
        <span>${escapeHtml(task.sender || "")}</span>
        <span>${escapeHtml(task.subject || "")}</span>
        <span>${escapeHtml(monitorCustomer(task))}</span>
        <span>${escapeHtml(task.actionTaken || "")}</span>
        ${task.emailUrl ? monitorEmailLink(task) : ""}
      </div>
      <div class="pill">${escapeHtml(task.orderRunId ? `Run ${task.orderRunId}` : `Email ${task.emailMessageId || ""}`)}</div>
      ${exceptionResolutionControls(task)}
      <div class="header-actions">
        <button class="secondary" data-action="inspect" data-payload="${escapeHtml(JSON.stringify(task))}" type="button">Inspect</button>
      </div>
    </article>
  `;
}

function renderExceptions() {
  const tasks = state.dashboard?.monitor?.exceptions || state.dashboard?.exceptionQueue || [];
  const html = tasks.length
    ? tasks.map(exceptionCard).join("")
    : '<p class="muted">No open exceptions.</p>';
  if (el("monitorExceptionList")) el("monitorExceptionList").innerHTML = html;
  el("exceptionList").innerHTML = html;
}

function exceptionResolutionControls(task) {
  const id = escapeHtml(task.exceptionId || task.id || "");
  const type = escapeHtml(task.type || "");
  const csrOptions = selectOptions(csrDirectoryOptions());
  const processorOptions = selectOptions(processorProfileOptions());
  const hasEmail = Boolean(task.emailMessageId);
  const customerControls = (task.type === "customerIdentification" || task.type === "routing") ? `
    <form class="exception-resolution-form" data-exception-id="${id}" data-exception-type="${type}" data-resolution-action="customer">
      <label>Customer code
        <input name="customerCode" value="${escapeHtml(exceptionCustomerReference(task))}" placeholder="102598" required>
      </label>
      <button type="submit">Set Customer</button>
    </form>
  ` : "";
  const csrControls = hasEmail ? `
    <form class="exception-resolution-form multi" data-exception-id="${id}" data-exception-type="${type}" data-resolution-action="csr">
      <label>CSR
        <select name="csrDirectoryKey" required>${csrOptions}</select>
      </label>
      <label>Subject
        <input name="subject" value="${escapeHtml(task.subject || "")}" placeholder="Updated subject">
      </label>
      <label class="checkbox-label">
        <input name="forceMove" type="checkbox" checked>
        <span>Move email</span>
      </label>
      <button type="submit">Apply CSR</button>
    </form>
  ` : "";
  const subjectControls = hasEmail ? `
    <form class="exception-resolution-form" data-exception-id="${id}" data-exception-type="${type}" data-resolution-action="emailSubject">
      <label>Subject
        <input name="subject" value="${escapeHtml(task.subject || "")}" placeholder="Updated subject" required>
      </label>
      <button type="submit">Update Subject</button>
    </form>
  ` : "";
  const emailReprocessControls = hasEmail ? `
    <form class="exception-resolution-form compact" data-exception-id="${id}" data-exception-type="${type}" data-resolution-action="emailReprocess">
      <button type="submit">Reprocess Email</button>
    </form>
  ` : "";
  const forceOrderControls = hasEmail ? `
    <form class="exception-resolution-form" data-exception-id="${id}" data-exception-type="${type}" data-resolution-action="forceOrder">
      <label>Order processor
        <select name="processorProfileId" required>${processorOptions}</select>
      </label>
      <button type="submit">Force Order</button>
    </form>
  ` : "";
  if (task.type === "customerIdentification" || task.type === "routing") {
    return `<div class="exception-actions">${customerControls}${csrControls}${subjectControls}${emailReprocessControls}${forceOrderControls}</div>`;
  }
  if (task.type === "itemValidation") {
    return `
      <form class="exception-resolution-form" data-exception-id="${id}" data-exception-type="${type}" data-resolution-action="item">
        <label>ERP item
          <input name="matchedInternalItemNumber" value="${escapeHtml(task.context?.line?.matchedInternalItemNumber || "")}" placeholder="10001" required>
        </label>
        <button type="submit">Resolve</button>
      </form>
    `;
  }
  if (task.type === "parserFailure" || task.type === "outputGeneration") {
    return `
      <form class="exception-resolution-form compact" data-exception-id="${id}" data-exception-type="${type}" data-resolution-action="orderReprocess">
        <button type="submit">Reprocess</button>
      </form>
    `;
  }
  return `
    <div class="exception-actions">
    ${csrControls}${subjectControls}${emailReprocessControls}${forceOrderControls}
    <form class="exception-resolution-form" data-exception-id="${id}" data-exception-type="${type}" data-resolution-action="notes">
      <label>Notes
        <input name="notes" placeholder="Resolved">
      </label>
      <button type="submit">Resolve</button>
    </form>
    </div>
  `;
}

function exceptionCustomerReference(task) {
  const signals = task.context?.routingDecision?.matchedSignals || {};
  const extracted = signals.extractedCustomerCode || signals.customerCodeExtraction?.value || "";
  const identification = task.context?.result?.extractedSignals || {};
  return extracted || identification.customerCode || identification.accountNumber || "";
}

function renderArtifacts() {
  const artifacts = state.outputs.outputArtifacts || state.dashboard?.outputArtifacts || [];
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

function customerLabel(distributor) {
  if (!distributor) return "System overview";
  const name = distributor.name || distributor.tenantId;
  return distributor.tenantId === "default" ? `${name} (system)` : name;
}

function renderCustomerContext() {
  const tenant = state.dashboard?.tenant || {};
  const selected = selectedDistributor();
  const name = selected?.name || tenant.name || state.tenantId;
  const tenantId = selected?.tenantId || tenant.tenantId || state.tenantId;
  el("customerContextLine").textContent = tenantId === "default"
    ? "System overview"
    : `Viewing customer profile: ${name} (${tenantId})`;
}

function renderDistributorSelector() {
  const selector = el("distributorSelector");
  const distributors = state.dashboard?.distributorCustomers || [];
  selector.innerHTML = '<option value="">Select customer profile</option>' + distributors
    .map((distributor) => `<option value="${escapeHtml(distributor.tenantId)}">${escapeHtml(customerLabel(distributor))}</option>`)
    .join("");
  selector.value = distributors.some((distributor) => distributor.tenantId === state.tenantId) ? state.tenantId : "";
}

function renderDistributors() {
  const distributors = state.dashboard?.distributorCustomers || [];
  el("distributorBody").innerHTML = distributors.map((distributor) => `
    <tr class="click-row ${distributor.tenantId === state.tenantId ? "selected-row" : ""}" data-action="open-distributor" data-tenant="${escapeHtml(distributor.tenantId)}">
      <td>${escapeHtml(distributor.name || distributor.tenantId)}</td>
      <td>${escapeHtml(distributor.tenantId)}</td>
      <td>${escapeHtml(distributor.environment || "")}</td>
      <td>${statusPill(distributor.status || "active")}</td>
      <td>${escapeHtml(distributor.updatedAt || distributor.createdAt || "")}</td>
    </tr>
  `).join("");
}

function renderCustomerPage() {
  [
    "distributorListPage",
    "distributorDetailPage",
    "downstreamCustomerListPage",
    "itemListPage",
    "distributorEditPage"
  ].forEach((id) => {
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
  if (state.customerPage === "customer-list") {
    renderCustomerDataList("customers");
    el("downstreamCustomerListPage").classList.remove("hidden");
    return;
  }
  if (state.customerPage === "item-list") {
    renderCustomerDataList("items");
    el("itemListPage").classList.remove("hidden");
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
  el("distributorDetailTitle").textContent = `Customer Profile: ${distributor.name || distributor.tenantId}`;
  el("distributorDetailMeta").textContent = `Customer ID ${distributor.tenantId}${distributor.environment ? ` - ${distributor.environment}` : ""}`;

  const mailboxForm = el("mailboxForm");
  mailboxForm.elements.mailboxAddress.value = mailbox?.mailboxAddress || "";
  mailboxForm.elements.displayName.value = mailbox?.displayName || "";
  mailboxForm.elements.authorizedUserEmail.value = mailbox?.settings?.authorizedUserEmail || connection?.metadata?.authorizedUserEmail || connection?.ownerEmail || "";
  mailboxForm.elements.connectionId.value = mailbox?.connectionId || connection?.id || connectionIdFor(distributor.tenantId, mailbox?.mailboxAddress || "mailbox");
  mailboxForm.elements.enabled.value = mailbox?.enabled === false ? "false" : "true";

  el("authSummary").innerHTML = `
    <div><strong>Connection</strong> ${escapeHtml(connection?.id || mailbox?.connectionId || "")}</div>
    <div><strong>Status</strong> ${statusPill(connection?.status || mailbox?.permissionStatus || "needsConsent")}</div>
    <div><strong>Owner</strong> ${escapeHtml(connection?.ownerEmail || mailbox?.settings?.authorizedBy || "")}</div>
    <div><strong>Mailbox</strong> ${escapeHtml(mailbox?.mailboxAddress || "")}</div>
    <div><strong>Last tested</strong> ${escapeHtml(mailbox?.lastTestedAt || connection?.lastTestedAt || "")}</div>
  `;

  populateProfileSelects();
  populateAutomationSettings(settings);
  if (!el("routingForm").elements.id.value) clearRoutingForm();
  if (!el("processorProfileForm").elements.id.value) clearProcessorProfileForm();
  if (!el("outputProfileForm").elements.id.value) clearOutputProfileForm();

  renderRoutingRules();
  renderProfiles();
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
      <td><button class="secondary" data-action="edit-routing-rule" data-rule="${escapeHtml(rule.id)}" type="button">Edit</button></td>
    </tr>
  `).join("");
}

function renderProfiles() {
  el("processorProfilesBody").innerHTML = (state.dashboard?.processorProfiles || []).map((profile) => `
    <tr>
      <td>${escapeHtml(profile.name || profile.id)}</td>
      <td>${escapeHtml(profile.processorType || "")}</td>
      <td>${escapeHtml(profile.outputProfileId || "")}</td>
      <td><button class="secondary" data-action="edit-processor-profile" data-profile="${escapeHtml(profile.id)}" type="button">Edit</button></td>
    </tr>
  `).join("");
  el("outputProfilesBody").innerHTML = (state.dashboard?.outputProfiles || []).map((profile) => `
    <tr>
      <td>${escapeHtml(profile.name || profile.id)}</td>
      <td>${escapeHtml(profile.outputType || "")}</td>
      <td>${escapeHtml(profile.destination?.adapter || "")}</td>
      <td><button class="secondary" data-action="edit-output-profile" data-profile="${escapeHtml(profile.id)}" type="button">Edit</button></td>
    </tr>
  `).join("");
}

function rawSourceRow(record = {}) {
  const source = record.rawSource || record.raw_source || {};
  if (source?.row && typeof source.row === "object" && !Array.isArray(source.row)) return source.row;
  if (source && typeof source === "object" && !Array.isArray(source)) return source;
  return {};
}

function preferredObjectValue(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const keys = ["alt_part", "altPart", "value", "code", "id", "name", "email", "domain"];
  for (const key of keys) {
    if (value[key] !== undefined && value[key] !== null && value[key] !== "") return value[key];
  }
  return null;
}

function flattenCellValue(value, delimiter = " | ") {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.map((item) => flattenCellValue(item, delimiter)).filter(Boolean).join(delimiter);
  const preferred = preferredObjectValue(value);
  if (preferred !== null) return flattenCellValue(preferred, delimiter);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function uniqueValues(values) {
  const seen = new Set();
  return values
    .map((value) => flattenCellValue(value).trim())
    .filter((value) => {
      const key = value.toLowerCase();
      if (!value || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function asList(value) {
  if (value === null || value === undefined || value === "") return [];
  return Array.isArray(value) ? value : [value];
}

function itemAlternateIds(item = {}) {
  return uniqueValues([
    ...asList(item.altPartsCombined),
    ...asList(item.customerItemNumbers),
    ...asList(item.aliases)
  ]).join(" | ");
}

async function loadConsoleData(section, body = {}, options = {}) {
  const result = await post(
    `/console/data/${section}`,
    body,
    { quiet: options.quiet, busyText: options.busyText || "Loading" }
  );
  if (result.session) state.dashboard = { ...(state.dashboard || {}), session: result.session };
  return result;
}

function listSearchPayload(kind, offset = null) {
  const prefix = listPrefix(kind);
  const page = state.lists[kind]?.page || {};
  const search = el(`${prefix}Search`)?.value.trim() || "";
  const filterValue = el(`${prefix}FilterValue`)?.value.trim() || "";
  return {
    limit: page.limit || 100,
    offset: offset === null ? page.offset || 0 : Math.max(0, offset),
    search: [search, filterValue].filter(Boolean).join(" ")
  };
}

async function loadCustomerDataList(kind, options = {}) {
  const result = await loadConsoleData(
    kind === "items" ? "items" : "customers",
    listSearchPayload(kind, options.offset ?? null),
    { quiet: options.quiet, busyText: kind === "items" ? "Loading items" : "Loading customers" }
  );
  state.lists[kind] = {
    items: result[kind] || [],
    page: result.page || { total: 0, limit: 100, offset: 0, hasNext: false, hasPrevious: false },
    loaded: true
  };
  renderCustomerDataList(kind);
}

function scheduleCustomerDataListLoad(kind) {
  window.clearTimeout(state.listTimers[kind]);
  state.listTimers[kind] = window.setTimeout(() => {
    loadCustomerDataList(kind, { offset: 0, quiet: true }).catch((error) => showDetails(payloadForError(error)));
  }, 250);
}

async function loadOutputs(options = {}) {
  const page = state.outputs.page || {};
  const result = await loadConsoleData(
    "outputs",
    { limit: page.limit || 100, offset: options.offset ?? page.offset ?? 0 },
    { quiet: options.quiet, busyText: "Loading outputs" }
  );
  state.outputs = {
    outputArtifacts: result.outputArtifacts || [],
    page: result.page || { total: 0, limit: 100, offset: 0, hasNext: false, hasPrevious: false },
    loaded: true
  };
  renderArtifacts();
}

function listRecords(kind) {
  return state.lists[kind]?.items || [];
}

function listBaseColumns(kind) {
  return kind === "items" ? ITEM_LIST_BASE_COLUMNS : CUSTOMER_LIST_BASE_COLUMNS;
}

function listPrefix(kind) {
  return kind === "items" ? "itemList" : "downstreamCustomer";
}

function rawColumnKeys(records) {
  const keys = new Set();
  records.forEach((record) => {
    Object.keys(rawSourceRow(record)).forEach((key) => keys.add(key));
  });
  return [...keys].sort((left, right) => left.localeCompare(right));
}

function listColumns(kind, records) {
  const base = listBaseColumns(kind);
  const existing = new Set(base.map((column) => column.key.toLowerCase()));
  const raw = rawColumnKeys(records)
    .filter((key) => !existing.has(key.toLowerCase()))
    .map((key) => ({ key: `raw:${key}`, label: key }));
  return [...base, ...raw];
}

function listCellValue(record, column, kind) {
  if (column.key.startsWith("raw:")) {
    return flattenCellValue(rawSourceRow(record)[column.key.slice(4)]);
  }
  if (kind === "items" && column.key === "alternateIds") return itemAlternateIds(record);
  return flattenCellValue(record[column.key]);
}

function updateFilterFieldOptions(select, columns) {
  const selected = select.value;
  select.innerHTML = [
    '<option value="">Any field</option>',
    ...columns.map((column) => `<option value="${escapeHtml(column.key)}">${escapeHtml(column.label)}</option>`)
  ].join("");
  select.value = columns.some((column) => column.key === selected) ? selected : "";
}

function filteredListRows(kind, records, columns) {
  const prefix = listPrefix(kind);
  const search = el(`${prefix}Search`).value.trim().toLowerCase();
  const filterField = el(`${prefix}FilterField`).value;
  const filterValue = el(`${prefix}FilterValue`).value.trim().toLowerCase();
  return records.filter((record) => {
    const values = columns.map((column) => listCellValue(record, column, kind));
    const haystack = values.join(" ").toLowerCase();
    if (search && !haystack.includes(search)) return false;
    if (filterField && filterValue) {
      const column = columns.find((item) => item.key === filterField);
      if (!column || !listCellValue(record, column, kind).toLowerCase().includes(filterValue)) return false;
    }
    return true;
  });
}

function renderListTable(kind, records, columns) {
  const ids = kind === "items"
    ? { head: "itemListHead", body: "itemListBody" }
    : { head: "downstreamCustomerListHead", body: "downstreamCustomerListBody" };
  el(ids.head).innerHTML = `
    <tr>${columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("")}</tr>
  `;
  el(ids.body).innerHTML = records.length ? records.map((record) => `
    <tr>${columns.map((column) => `<td>${escapeHtml(listCellValue(record, column, kind))}</td>`).join("")}</tr>
  `).join("") : `<tr><td colspan="${columns.length}">No records match the current filters.</td></tr>`;
}

function renderCustomerDataList(kind) {
  const records = listRecords(kind);
  const columns = listColumns(kind, records);
  const prefix = listPrefix(kind);
  updateFilterFieldOptions(el(`${prefix}FilterField`), columns);
  const filtered = filteredListRows(kind, records, columns);
  const page = state.lists[kind]?.page || {};
  const latest = latestDate(records);
  const metaId = kind === "items" ? "fullItemListMeta" : "downstreamCustomerListMeta";
  const start = page.total ? (page.offset || 0) + 1 : 0;
  const end = Math.min((page.offset || 0) + records.length, page.total || records.length);
  el(metaId).textContent = `${start}-${end} of ${page.total ?? records.length} records${latest ? ` - page latest ${latest}` : ""}`;
  const prevButton = el(kind === "items" ? "itemListPreviousButton" : "downstreamCustomerPreviousButton");
  const nextButton = el(kind === "items" ? "itemListNextButton" : "downstreamCustomerNextButton");
  if (prevButton) prevButton.disabled = !page.hasPrevious;
  if (nextButton) nextButton.disabled = !page.hasNext;
  renderListTable(kind, filtered, columns);
}

function csvEscape(value) {
  const text = flattenCellValue(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function exportCustomerDataList(kind) {
  const records = listRecords(kind);
  const columns = listColumns(kind, records);
  const filtered = filteredListRows(kind, records, columns);
  const rows = [
    columns.map((column) => csvEscape(column.label)).join(","),
    ...filtered.map((record) => columns.map((column) => csvEscape(listCellValue(record, column, kind))).join(","))
  ].join("\n");
  const blob = new Blob([rows], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${state.tenantId}-${kind === "items" ? "items" : "downstream-customers"}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function renderReadOnlyLists() {
  const customerStats = state.dashboard?.customerListStats || {};
  const itemStats = state.dashboard?.itemListStats || {};
  el("customerListUpdated").textContent = `Last update ${customerStats.latest || "not yet imported"}`;
  el("itemListUpdated").textContent = `Last update ${itemStats.latest || "not yet imported"}`;
  renderImportTargets();
  el("downstreamCustomerPreview").innerHTML = `
    <div><strong>Records</strong><span>${customerStats.count ?? 0}</span></div>
    <div><strong>Fields</strong><span>${CUSTOMER_LIST_BASE_COLUMNS.length}</span></div>
    <div><strong>Last Update</strong><span>${escapeHtml(customerStats.latest || "not yet imported")}</span></div>
  `;
  el("itemListPreview").innerHTML = `
    <div><strong>Records</strong><span>${itemStats.count ?? 0}</span></div>
    <div><strong>Fields</strong><span>${ITEM_LIST_BASE_COLUMNS.length}</span></div>
    <div><strong>Last Update</strong><span>${escapeHtml(itemStats.latest || "not yet imported")}</span></div>
  `;
}

function renderSession() {
  const session = state.dashboard?.session || {};
  el("sessionLine").textContent = session.authorized
    ? `${session.consoleUser.email} - ${session.isPlatformAdmin ? "platformAdmin" : "customerUser"}`
    : `${session.reason || "unauthorized"}`;
}

function resetSectionData() {
  state.lists.customers = { items: [], page: { total: 0, limit: 100, offset: 0, hasNext: false, hasPrevious: false }, loaded: false };
  state.lists.items = { items: [], page: { total: 0, limit: 100, offset: 0, hasNext: false, hasPrevious: false }, loaded: false };
  state.outputs = { outputArtifacts: [], page: { total: 0, limit: 100, offset: 0, hasNext: false, hasPrevious: false }, loaded: false };
}

async function refresh(options = {}) {
  if (options.quiet && state.pendingRequests > 0) return;
  const dashboardView = options.full ? "full" : activeConsoleView() === "monitor" ? "monitor" : "config";
  const selectedBeforeRefresh = state.selectedDistributorId;
  state.dashboard = await post(
    "/console/dashboard",
    { view: dashboardView },
    { quiet: options.quiet, busyText: dashboardView === "monitor" ? "Refreshing monitor" : "Working" }
  );
  const distributors = state.dashboard?.distributorCustomers || [];
  state.selectedDistributorId = distributors.some((distributor) => distributor.tenantId === selectedBeforeRefresh)
    ? selectedBeforeRefresh
    : state.dashboard?.tenant?.tenantId || state.tenantId;
  renderSession();
  renderCustomerContext();
  renderMetrics(state.dashboard.summary);
  renderRows();
  renderExceptions();
  renderArtifacts();
  renderSystemSettings();
  renderDistributorSelector();
  renderCustomerPage();
  if (activeConsoleView() === "outputs") await loadOutputs({ quiet: options.quiet });
  if (activeConsoleView() === "customers" && state.customerPage === "customer-list") {
    await loadCustomerDataList("customers", { quiet: options.quiet });
  }
  if (activeConsoleView() === "customers" && state.customerPage === "item-list") {
    await loadCustomerDataList("items", { quiet: options.quiet });
  }
}

function activeView(id) {
  document.querySelectorAll(".tab").forEach((button) => button.classList.toggle("active", button.dataset.view === id));
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === id));
}

async function openDistributor(tenantId) {
  state.tenantId = tenantId;
  state.selectedDistributorId = tenantId;
  state.customerPage = "detail";
  resetSectionData();
  await refresh();
}

async function switchDistributor(tenantId) {
  if (!tenantId || tenantId === state.tenantId) return;
  state.tenantId = tenantId;
  state.selectedDistributorId = tenantId;
  resetSectionData();
  if (activeConsoleView() === "customers") {
    state.customerPage = "detail";
  }
  await refresh();
}

async function resolveTask(id, type) {
  const resolution = { notes: "Resolved from console" };
  if (type === "itemValidation") {
    resolution.matchedInternalItemNumber = window.prompt("Internal item number") || "";
  } else if (type === "customerIdentification" || type === "routing") {
    resolution.customerCode = window.prompt("Customer code or record id") || "";
  } else {
    resolution.reprocess = window.confirm("Request reprocess?");
  }
  const result = await post(`/console/exceptions/${id}/resolve`, { resolution });
  showDetails(result);
  await refresh();
}

async function resolveExceptionForm(form) {
  const id = form.dataset.exceptionId;
  const type = form.dataset.exceptionType;
  const action = form.dataset.resolutionAction || "";
  const resolution = {
    action,
    notes: value(form, "notes") || "Resolved from console"
  };
  if (action === "customer") {
    resolution.customerCode = value(form, "customerCode");
  } else if (action === "csr") {
    const csr = selectedCsr(value(form, "csrDirectoryKey")) || {};
    resolution.csrName = csr.name || "";
    resolution.csrFolder = csr.folder || csr.name || "";
    resolution.csrEmail = csr.email || "";
    resolution.subject = value(form, "subject");
    resolution.forceMoveToCsr = checked(form, "forceMove");
  } else if (action === "emailSubject") {
    resolution.subject = value(form, "subject");
  } else if (action === "emailReprocess") {
    resolution.reprocess = true;
  } else if (action === "forceOrder") {
    resolution.processorProfileId = value(form, "processorProfileId");
  } else if (action === "orderReprocess") {
    resolution.reprocessOrder = true;
  } else if (type === "itemValidation" || action === "item") {
    resolution.matchedInternalItemNumber = value(form, "matchedInternalItemNumber");
  } else if (type === "customerIdentification" || type === "routing") {
    resolution.customerCode = value(form, "customerCode");
  } else {
    resolution.reprocess = value(form, "reprocess") === "true";
  }
  const result = await post(`/console/exceptions/${id}/resolve`, { resolution });
  showDetails(result);
  await refresh();
}

function wireForms() {
  el("tenantForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const targetTenantId = value(form, "id") || slugifyId(value(form, "name"));
    if (!targetTenantId) {
      showDetails({ error: "customerIdRequired", message: "Enter a customer name or customer id before saving." });
      return;
    }
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
    await refresh();
  });

  el("mailboxForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const mailbox = primaryMailbox();
    const mailboxAddress = value(form, "mailboxAddress");
    if (!mailboxAddress) {
      showDetails({ error: "mailboxAddressRequired", message: "Enter the shared mailbox address before saving." });
      return;
    }
    const connectionId = mailbox?.connectionId || connectionIdFor(state.tenantId, mailboxAddress);
    const authorizedUserEmail = value(form, "authorizedUserEmail");
    const result = await post("/console/mailboxes", {
      id: mailbox?.id,
      mailboxAddress,
      displayName: value(form, "displayName"),
      connectionId,
      enabled: value(form, "enabled") !== "false",
      settings: {
        ...(mailbox?.settings || {}),
        authorizedUserEmail
      }
    });
    if (!showActionResult(result)) return;
    await refresh();
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
      settings: automationSettingsFromForm(form)
    });
    if (!showActionResult(result)) return;
    await refresh();
  });

  el("systemSettingsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = await post("/console/tenants", {
      targetTenantId: SYSTEM_TENANT_ID,
      id: SYSTEM_TENANT_ID,
      name: "System Settings",
      environment: "system",
      status: "active",
      settings: systemSettingsFromForm(form)
    });
    if (!showActionResult(result)) return;
    await refresh();
  });

  el("routingForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const mailbox = primaryMailbox();
    const submittedRuleId = value(form, "id");
    const submittedRuleName = value(form, "name") || "routing rule";
    setFormStatus("routingFormStatus", "Saving routing rule...", "warn");
    try {
      const result = await post("/console/routing-rules", {
        id: submittedRuleId,
        customerId: "_global",
        name: submittedRuleName,
        phase: value(form, "phase"),
        outcome: value(form, "outcome"),
        priority: value(form, "priority") ? Number(value(form, "priority")) : 100,
        processorProfileId: value(form, "outcome") === "knownOrder" ? value(form, "processorProfileId") : "",
        mailboxAccountIds: mailbox?.id ? [mailbox.id] : [],
        mailboxAddresses: mailbox?.mailboxAddress ? [mailbox.mailboxAddress] : [],
        senderEquals: split(value(form, "senderEquals")),
        senderDomains: split(value(form, "senderDomains")),
        subjectRegex: split(value(form, "subjectRegex")),
        bodyRegex: split(value(form, "bodyRegex")),
        priorProcessedSubjectRegex: split(value(form, "priorProcessedSubjectRegex")),
        attachmentExtensions: split(value(form, "attachmentExtensions")),
        attachmentNameRegex: split(value(form, "attachmentNameRegex")),
        requiredAttachment: checked(form, "requiredAttachment"),
        enabled: checked(form, "enabled"),
        customerCodeSource: value(form, "customerCodeSource"),
        customerCodeRegex: value(form, "customerCodeRegex"),
        customerCodeGroup: "customerCode",
        subjectTemplate: value(form, "subjectTemplate"),
        categoryCsrField: "csrFolder",
        categoryTemplates: split(value(form, "categoryTemplates")),
        processedMoveMode: value(form, "processedMoveMode"),
        processedMoveFolder: value(form, "processedMoveMode") === "staticFolder" ? value(form, "processedMoveTarget") : "",
        processedMoveCustomerField: value(form, "processedMoveMode") === "customerField" ? value(form, "processedMoveTarget") : "",
        nonOrderMoveMode: value(form, "nonOrderMoveMode"),
        nonOrderMoveFolder: value(form, "nonOrderMoveMode") === "staticFolder" ? value(form, "nonOrderMoveTarget") : "",
        nonOrderMoveCustomerField: value(form, "nonOrderMoveMode") === "customerField" ? value(form, "nonOrderMoveTarget") : ""
      });
      if (actionFailed(result)) {
        showDetails(result);
        setFormStatus("routingFormStatus", `Rule was not saved: ${errorMessage(result)}`, "bad");
        return;
      }
      if (Object.keys(result || {}).length) showDetails(result);
      const savedRuleId = result.routingRule?.id || submittedRuleId;
      const savedRuleName = result.routingRule?.name || submittedRuleName;
      await refresh();
      const savedRule = (state.dashboard?.routingRules || []).find((rule) => (
        savedRuleId ? rule.id === savedRuleId : rule.name === submittedRuleName
      ));
      if (!savedRule) {
        setFormStatus(
          "routingFormStatus",
          `The API returned success for ${savedRuleName}, but it was not found after refresh. Check customer selection and permissions.`,
          "bad"
        );
        showDetails({
          error: "routingRuleNotVisibleAfterSave",
          message: "The routing rule save returned success but the refreshed dashboard did not include it.",
          tenantId: state.tenantId,
          savedRuleId,
          savedRuleName
        });
        return;
      }
      clearRoutingForm();
      setFormStatus("routingFormStatus", `Saved ${savedRuleName} for ${selectedDistributor().name || state.tenantId}.`, "good");
    } catch (error) {
      const payload = payloadForError(error);
      showDetails(payload);
      setFormStatus("routingFormStatus", `Rule was not saved: ${errorMessage(payload)}`, "bad");
    }
  });

  el("processorProfileForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const fieldMap = compactObject({
      provided_item_number: value(form, "itemNumberField"),
      provided_upc: value(form, "upcField"),
      quantity: value(form, "quantityField"),
      description: value(form, "descriptionField"),
      po_number: value(form, "poNumberField"),
      order_number: value(form, "orderNumberField")
    });
    const settings = compactObject({
      hasHeader: checked(form, "hasHeader"),
      delimiter: value(form, "delimiter"),
      headerlessColumns: split(value(form, "headerlessColumns")),
      fieldMap,
      linePattern: value(form, "linePattern"),
      baseProcessorType: value(form, "baseProcessorType"),
      documentIntelligenceModelId: value(form, "documentIntelligenceModelId"),
      webhookUrl: value(form, "webhookUrl"),
      timeoutSeconds: value(form, "webhookTimeoutSeconds")
    });
    const result = await post("/console/processor-profiles", {
      id: value(form, "id"),
      customerId: "_global",
      name: value(form, "name"),
      processorType: value(form, "processorType"),
      outputProfileId: value(form, "outputProfileId"),
      settings
    });
    if (!showActionResult(result)) return;
    clearProcessorProfileForm();
    await refresh();
  });

  el("outputProfileForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const settings = compactObject({
      fileNameTemplate: value(form, "fileNameTemplate"),
      delimiter: value(form, "delimiter"),
      includeHeader: checked(form, "includeHeader"),
      encoding: value(form, "encoding"),
      template: value(form, "textTemplate"),
      formats: split(value(form, "formats")),
      fields: selectedOutputFields()
    });
    const destination = compactObject({
      adapter: value(form, "destinationAdapter"),
      folder: value(form, "destinationFolder"),
      url: value(form, "destinationUrl"),
      productionDeliveryEnabled: checked(form, "productionDeliveryEnabled")
    });
    const result = await post("/console/output-profiles", {
      id: value(form, "id"),
      customerId: "_global",
      name: value(form, "name"),
      outputType: value(form, "outputType"),
      destination,
      settings
    });
    if (!showActionResult(result)) return;
    clearOutputProfileForm();
    await refresh();
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
    authorizedUserEmail: mailbox.settings?.authorizedUserEmail || "",
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

document.addEventListener("submit", async (event) => {
  const form = event.target.closest(".exception-resolution-form");
  if (!form) return;
  event.preventDefault();
  try {
    await resolveExceptionForm(form);
  } catch (error) {
    showDetails(payloadForError(error));
  }
});

document.addEventListener("click", async (event) => {
  try {
  const target = event.target.closest("button, tr");
  if (!target) return;
  if (target.id === "closeDetailsButton") {
    clearDetails();
    return;
  }
  if (target.classList.contains("tab")) {
    activeView(target.dataset.view);
    if (target.dataset.view === "customers") {
      state.customerPage = "list";
    }
    if (["customers", "outputs", "users", "settings"].includes(target.dataset.view)) {
      await refresh();
    }
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
  if (target.id === "openDownstreamCustomerListButton") {
    state.customerPage = "customer-list";
    renderCustomerPage();
    await loadCustomerDataList("customers", { offset: 0 });
  }
  if (target.id === "openItemListButton") {
    state.customerPage = "item-list";
    renderCustomerPage();
    await loadCustomerDataList("items", { offset: 0 });
  }
  if (target.id === "backFromDownstreamCustomerListButton" || target.id === "backFromItemListButton") {
    state.customerPage = "detail";
    renderCustomerPage();
  }
  if (target.id === "downstreamCustomerPreviousButton") {
    const page = state.lists.customers.page || {};
    await loadCustomerDataList("customers", { offset: Math.max(0, (page.offset || 0) - (page.limit || 100)) });
  }
  if (target.id === "downstreamCustomerNextButton") {
    const page = state.lists.customers.page || {};
    await loadCustomerDataList("customers", { offset: (page.offset || 0) + (page.limit || 100) });
  }
  if (target.id === "itemListPreviousButton") {
    const page = state.lists.items.page || {};
    await loadCustomerDataList("items", { offset: Math.max(0, (page.offset || 0) - (page.limit || 100)) });
  }
  if (target.id === "itemListNextButton") {
    const page = state.lists.items.page || {};
    await loadCustomerDataList("items", { offset: (page.offset || 0) + (page.limit || 100) });
  }
  if (target.id === "exportDownstreamCustomersButton") exportCustomerDataList("customers");
  if (target.id === "exportItemsButton") exportCustomerDataList("items");
  if (target.id === "cancelDistributorEditButton") {
    state.customerPage = state.selectedDistributorId ? "detail" : "list";
    renderCustomerPage();
  }
  if (target.id === "authorizeMicrosoftButton") await authorizeMicrosoft();
  if (target.id === "testMailboxButton") await testMailbox();
  if (target.id === "clearRoutingFormButton") clearRoutingForm();
  if (target.dataset.action === "edit-routing-rule") loadRoutingRule(target.dataset.rule);
  if (target.dataset.action === "edit-processor-profile") loadProcessorProfile(target.dataset.profile);
  if (target.dataset.action === "edit-output-profile") loadOutputProfile(target.dataset.profile);
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
  } catch (error) {
    showDetails(payloadForError(error));
  }
});

el("refreshButton").addEventListener("click", refresh);
el("distributorSelector").addEventListener("change", async (event) => {
  await switchDistributor(event.target.value);
});
el("routingForm").elements.routingPath.addEventListener("change", applyRoutingPathDefaults);
el("routingForm").elements.phase.addEventListener("change", syncRoutingDefaultsForPhase);
el("routingForm").elements.outcome.addEventListener("change", syncRoutingDefaultsForPhase);
[
  "downstreamCustomerSearch",
  "downstreamCustomerFilterField",
  "downstreamCustomerFilterValue"
].forEach((id) => {
  el(id).addEventListener(id.endsWith("Field") ? "change" : "input", () => scheduleCustomerDataListLoad("customers"));
});
[
  "itemListSearch",
  "itemListFilterField",
  "itemListFilterValue"
].forEach((id) => {
  el(id).addEventListener(id.endsWith("Field") ? "change" : "input", () => scheduleCustomerDataListLoad("items"));
});
wireForms();
window.addEventListener("unhandledrejection", (event) => {
  event.preventDefault();
  showDetails(payloadForError(event.reason));
});
window.addEventListener("error", (event) => {
  showDetails({ error: "consoleError", message: event.message });
});
if (params.get("authStatus")) {
  state.customerPage = "detail";
  showDetails({ microsoftAuthStatus: params.get("authStatus"), connectionId: params.get("connectionId") });
}
refresh().catch(showDetails);
window.setInterval(() => {
  if (activeConsoleView() !== "monitor") return;
  refresh({ quiet: true }).catch((error) => showDetails(payloadForError(error)));
}, 12000);
