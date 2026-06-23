# API Contracts

The Azure backend exposes these endpoints through API Management. The APIM import source is `infra/openapi/order-processor-api.yaml`. The current local implementation lives in `src/order_processor/api.py`, with a thin Functions wrapper in `apps/functions/function_app.py`.

Deployment output `apiGatewayBaseUrl` is the only intended external base URL for Power Automate adapters and future console calls.

## Correlation and Audit

Phase 13 adds a shared observability contract across Power Automate, APIM, Azure Functions, Cosmos, and the console.

Every Function route preserves request headers and the backend extracts observability context from:

- body fields `correlationId`, `operationId`, `traceparent`, `flowRunId`, `flowName`, and `durableInstanceId`
- source/sourceMetadata fields with the same names
- headers `traceparent`, `x-correlation-id`, `x-ms-correlation-id`, `x-ms-client-request-id`, `x-ms-request-id`, `x-ms-workflow-run-id`, `x-ms-workflow-name`, and `x-order-processor-ingress`

Stored records use this context as follows:

- `emailMessages.correlationId`
- `orderRuns.correlationId`
- `orderRuns.processingStartedAt`
- `orderRuns.processingCompletedAt`
- `orderRuns.sourceMetadata.observability`
- `auditEvents.correlationId`
- `auditEvents.operationId`
- `auditEvents.traceId`
- `auditEvents.customerId`
- `auditEvents.orderRunId`
- `auditEvents.emailMessageId`

Audit events are written for ingestion, routing exceptions, customer identification, item validation, processing start/completion, generated artifacts, exception creation/resolution, reprocess requests, output artifact access, imports, console sessions, and configuration changes. User-driven actions set `userIntervention: true` in audit details and record the console actor when available.

## `POST /emails/ingest`

Stores an email message and evaluates routing rules.

Request shape:

```json
{
  "tenantId": "altitude",
  "mailbox": "customer-orders@example.com",
  "mailboxAccountId": "mailbox-1",
  "customerId": "customer-1",
  "messageId": "<graph-message-id>",
  "sender": "orders@example.com",
  "subject": "PO 12345",
  "receivedAt": "2026-06-19T12:00:00Z",
  "bodyText": "Customer: ABC",
  "bodyHtml": "<p>Customer: ABC</p>",
  "categories": ["Inbox"],
  "correlationId": "flow-run-or-trace-id",
  "source": {
    "provider": "powerAutomate",
    "flowName": "OrderProcessor - Mailbox Trigger Template"
  },
  "attachments": [
    {
      "name": "order.csv",
      "contentType": "text/csv",
      "size": 1200,
      "blobUrl": "https://...",
      "sourceUrl": "https://graph.microsoft.com/...",
      "contentId": "",
      "isInline": false,
      "metadata": {
        "graphAttachmentId": "attachment-1"
      }
    }
  ]
}
```

Response includes `emailMessage`, `routingDecision`, optional `orderRun`, optional `exceptionTask`, and `observability`. Response documents use camelCase keys.

Mailbox identity is first-class tenant context. A monitored mailbox belongs to the distributor/tenant whose orders inbox is being watched; it does not identify the downstream end customer. The ingest request should include `mailboxAccountId` when available. `customerId` should be supplied only by a trusted caller that has already identified the downstream customer/account through customer-ID logic or manual resolution. The backend also resolves the mailbox by mailbox address when `mailboxAccountId` is omitted.

Phase 6 routing behavior:

- The backend persists email metadata, attachment references, message id, sender, subject, received date, mailbox, mailbox account id, customer id when known, processing status, source metadata, and the routing decision in `emailMessages`.
- Mailbox configuration is read from `mailboxAccounts`. Disabled mailboxes route to `ignored`; unknown mailbox account ids route to `needsHumanReview`. Mailbox configuration no longer overrides or assigns downstream `customerId`.
- Candidate routing rules come from `routingRules`. Tenant-wide distributor rules use `customerId: "_global"`; downstream customer-specific rules can still identify a customer when hard conditions truly identify one end customer.
- Routing rules are data, not flow branches. Supported rule signals include mailbox account id, mailbox address, sender, sender domain, subject regex, body regex, known webstore patterns, prior processed subject patterns, attachment extension, attachment content type, attachment filename regex, and required-attachment checks.
- Routing rules now carry an ordered `phase`: `webstoreOrder`, `previouslyProcessed`, `orderCandidate`, `nonOrder`, or `general`. The backend evaluates phases in that order so webstore customer-code extraction and already-identified subject extraction happen before generic order/non-order decisions.
- `customerCodeExtraction` lets a distributor-specific rule extract the downstream customer code from subject, body, sender, attachment names, or combined email text, then match it to `customers.customerCode` or `customerAliases`.
- `subjectUpdate` stores subject detection/update policy, such as detecting subjects containing `Cust:` and `Rte:` and rendering `Cust: {customerCode} Rte: {routeNumber} - {originalSubject}`.
- `emailActions` stores planned Microsoft mail actions for the Graph/Power Automate adapter: category templates, the customer record field used for CSR name, and per-status move settings for `processedOrder`, `failedOrder`, `nonOrder`, and `ignored`. Move mode can be `none`, `staticFolder`, or `customerField`.
- Router outcomes are `knownOrder`, `knownCustomerNonOrder`, `needsCustomerIdentification`, `needsHumanReview`, and `ignored`.
- `knownOrder` creates an `orderRun`. `needsCustomerIdentification` and `needsHumanReview` create an `exceptionTask`. `knownCustomerNonOrder` and `ignored` do not create order runs.

## `POST /orders/{orderRunId}/process`

Processes an order payload into the universal order model. Phase 9 supports CSV, XLSX, XLS/XLT or HTML-backed legacy workbook exports, PDF extraction results from Azure Document Intelligence, email body orders, customer-specific override profiles, and direct canonical lines.

The active CSV migration target is `orderProcess - CSV Parse`. CSV parsing now happens in backend code with Python's CSV parser and configurable header/headerless field maps. Plumsail is not used. XLSX line-output can be generated in backend code only when `outputTypes`, `requestedOutputTypes`, or the selected output profile asks for XLSX.

Processor selection order:

- Explicit `processorProfileId`.
- Explicit `processorType`.
- Customer-scoped `processorProfiles`.
- Tenant-global `processorProfiles`.
- Default `csv`.

Supported processor types:

- `csv`
- `xlsx`
- `xls`
- `xlt`
- `pdf`
- `emailBody`
- `customerOverride`

Request shape:

```json
{
  "tenantId": "altitude",
  "customerId": "customer-1",
  "processorType": "csv",
  "poNumber": "PO-12345",
  "sourceFileName": "order.csv",
  "sourceContent": "item_number,quantity,description\nABC,2,Dog food\n",
  "sourceMetadata": {
    "mailbox": "orders@example.com",
    "sender": "buyer@example.com",
    "subject": "PO-12345"
  }
}
```

Workbook sources can send `sourceContentBase64` or pre-normalized `sourceRows`. PDF sources should send `documentIntelligenceResult`, `extractedText`, or `sourceRows`; raw PDF bytes without extraction are rejected with `documentIntelligenceExtractionRequired` and a parser-failure exception task.

Customer-specific processors use a processor profile such as:

```json
{
  "processorProfileId": "market-place-email-body",
  "processorType": "customerOverride",
  "processorSettings": {
    "baseProcessorType": "emailBody",
    "linePattern": "(?P<provided_upc>\\d{12})-(?P<quantity>\\d+)"
  },
  "bodyText": "019962896026-1"
}
```

Response includes `orderRun`, `unresolvedLineCount`, and `observability`. `orderRun` carries universal header/source metadata, parser details, line-level validation results, output artifacts, errors, parse warnings, correlation id, and processing timestamps.

Phase 11 output generation:

- A `universalOrderJson` artifact is generated for every processed order.
- When no output profile is configured, the backend also generates the default `lineCsv` artifact.
- Customer/global `outputProfiles` can generate `csv`, `xlsx`, `text`, `api`, `json`, or `multi` outputs.
- Request-time `outputProfileId`, `outputProfileIds`, inline `outputProfiles`, or `outputTypes` can override repository profile discovery.
- `api` output profiles generate a stored API payload and mark delivery as `pendingExternalDelivery`; they do not call the external customer API directly in this phase.
- Artifacts are stored in Blob Storage when `ORDER_PROCESSOR_OUTPUT_ARCHIVE_BACKEND=blob`, or in an in-memory store for local tests.

Output artifact reference shape:

```json
{
  "id": "artifact-id",
  "type": "lineCsv",
  "fileName": "customer-PO-12345-order-lines.csv",
  "contentType": "text/csv",
  "blobUrl": "https://storage.example/order-artifacts/.../order-lines.csv",
  "sizeBytes": 128,
  "checksum": "sha256",
  "generatedAt": "2026-06-20T00:00:00Z",
  "outputProfileId": "customer-csv",
  "destination": {
    "adapter": "blob",
    "folder": "customer/outbound"
  },
  "metadata": {
    "adapter": "lineCsv",
    "format": "csv"
  }
}
```

## `POST /orders/{orderRunId}/timeline`

Returns the order run, source email when available, and an ordered audit timeline for the run.

Response shape:

```json
{
  "orderRun": {},
  "emailMessage": {},
  "timeline": {
    "orderRunId": "order-run-1",
    "correlationId": "trace-or-flow-run-id",
    "processingLatencyMs": 3000,
    "eventCount": 5,
    "events": [
      {
        "timestamp": "2026-06-21T10:00:00+00:00",
        "eventType": "order.processed",
        "title": "order.processed",
        "actor": "system",
        "correlationId": "trace-or-flow-run-id",
        "details": {}
      }
    ]
  }
}
```

The console uses `POST /console/orders/{orderRunId}/timeline`, which verifies the signed-in user's customer assignment before returning the same timeline payload.

## `POST /customers/identify`

Runs customer identification from email context. Phase 7 applies deterministic rules first, then uses Azure OpenAI embeddings plus Cosmos customer vector search only when deterministic rules do not produce a confident match.

Request shape:

```json
{
  "emailMessage": {
    "id": "email-message-id",
    "tenantId": "altitude",
    "mailbox": "orders@example.com",
    "mailboxAccountId": "mailbox-1",
    "messageId": "<graph-message-id>",
    "sender": "buyer@example.com",
    "subject": "Customer: ABC PO 12345",
    "bodyText": "Store #101",
    "bodyHtml": "",
    "orderRunId": "order-run-1",
    "correlationId": "trace-id"
  },
  "confidenceThreshold": 0.85
}
```

Optional request fields `customers` and `customerAliases` can be supplied for tests or controlled callers. Normal backend operation loads `customers` and `customerAliases` from Cosmos.

Response includes `result` and optional `exceptionTask`. `result` includes status, matched customer id/code when confident, route number when known, match method, confidence, candidate list, reasons, and extracted signals.

Deterministic match order/signals:

- Existing email customer scope.
- Customer code extracted from subject/body/sender text.
- Store number.
- Route number.
- Sender email.
- Sender domain.
- Known subject pattern.
- Known body pattern.
- Attachment/file-name pattern.
- Customer alias records in `customerAliases` for account number/customer code, store number, route number, sender email, sender domain, known subject pattern, body pattern, and file-name pattern.

Vector fallback:

- Disabled by default for local/offline work.
- Enable with `ORDER_PROCESSOR_ENABLE_CUSTOMER_VECTOR_SEARCH=true`.
- Requires `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`.
- Uses `AZURE_OPENAI_API_KEY` when provided; otherwise uses managed identity through `DefaultAzureCredential`.
- Uses Cosmos `customers.embedding` and native customer vector search when running against Cosmos.

If the result is below `confidenceThreshold` or ambiguous, the API creates a `customerIdentification` exception task for console resolution. If a customer is confidently matched, the stored `emailMessages` record and associated `orderRuns` record are updated with the customer id and identification result when those records exist.

## `POST /items/validate`

Replaces the current `Module - Item Number Validator` as an Azure endpoint backed by Cosmos `items`.

The service evaluates deterministic item signals in this order:

- customer item number, internal item number, or alias exact match
- UPC exact match
- item number fuzzy match
- description fuzzy match

The endpoint accepts direct fields plus optional `rowContext`. If direct fields are blank, `rowContext` can supply customer-specific source columns such as `Vendor Item`, `UPC`, `Barcode`, `Description`, or `Column 1`.

Request shape:

```json
{
  "tenantId": "altitude",
  "customerId": "customer-1",
  "providedItemNumber": "ABC-123",
  "providedUpc": "012345678905",
  "description": "Dog food 25 lb",
  "rowContext": {
    "Vendor Item": "ABC-123",
    "Qty": "2"
  },
  "lineNumber": 1,
  "orderRunId": "order-run-1",
  "confidenceThreshold": 0.9,
  "candidateLimit": 5
}
```

Response includes:

- `result.status`: `matched`, `possibleMatch`, or `unresolved`
- `result.matchedInternalItemNumber`
- `result.matchedItemId`
- `result.matchMethod`
- `result.confidence`
- `result.candidates`
- `result.unresolvedReason`
- optional `exceptionTask`
- optional `updatedOrderLine` when `orderRunId` and `lineNumber` are supplied

If the best candidate is below `confidenceThreshold`, ambiguous, missing, or unresolved, the endpoint creates an `itemValidation` console task. When `orderRunId` and `lineNumber` are supplied, the matching `orderRuns.lines[]` entry is updated with validation status, confidence, method, candidates, matched item number, and validation errors. The order run moves to `needsReview` when unresolved lines remain and to `completed` when all lines are confidently matched.

## `POST /imports/customers`

Queues normalized customer profile imports into Cosmos and archives original source rows for audit/debug. The HTTP endpoint accepts direct `rows` or `sourceContent`, writes the incoming import payload to Blob Storage, queues a background import job, and returns `202 Accepted` quickly. Use `responseMode=inline` only for small debugging calls when the caller needs to wait for the full import result.

Request shape:

```json
{
  "tenantId": "altitude",
  "sourceName": "pilot-customers.csv",
  "contentType": "text/csv",
  "parserModule": "genericCustomerCsv",
  "fieldMap": {
    "customer_code": "Customer Code",
    "name": "Customer Name",
    "sender_domains": "Domains",
    "store_number": "Store",
    "route_number": "Route",
    "known_subject_patterns": "Subject Patterns"
  },
  "sourceContent": "Customer Code,Customer Name,Domains\nABC,ABC Stores,example.com\n",
  "sourceMetadata": {
    "sourceSystem": "sharepoint-export"
  }
}
```

Supported parsers: `rows`, `csv`, `json`, `jsonl`, `genericCustomerCsv`, and `genericItemCsv`. Parser selection can be supplied directly as `parserModule` or through `importProfile.parserModule`. Field maps can be supplied directly or through `importProfile.fieldMap`.

The immediate response includes `accepted`, `queued`, `status`, `importType`, `tenantId`, `jobId`, and `receivedAt`. The queued worker performs the actual import and writes the normal import audit event with import type, import run id, source rows blob URL/checksum, parser module, imported/created/updated/skipped/error counts, row errors, refresh policy, normalized customers, and generated customer aliases.

Customer imports default to a daily refresh cadence. Override with `refreshIntervalDays`, `importProfile.refreshIntervalDays`, or `customerConfig.customerRefreshIntervalDays`.

The universal customer row shape is accepted without a field map when upstream adapters can already emit it:

```json
{
  "customer_name": "CHOW HOUND #4",
  "customer_store_number": "504",
  "location_address1": "734 28TH ST SE",
  "location_city": "GRAND RAPIDS",
  "location_state": "MI",
  "location_zip": "49548",
  "phone": "616-452-7877",
  "customer_website": "WWW.CHOWHOUNDPET.COM",
  "customer_email": "GREGC@CHOWHOUNDPET.COM",
  "cust_code": "100029"
}
```

Customer imports write:

- `customers`
- `customerAliases`
- `auditEvents`
- source rows archive in Blob Storage, or `memory://` archive in local tests

Customer alias records are generated for customer code, sender email, sender domains, store number, route number, and known subject patterns. These feed deterministic customer identification.

## `POST /imports/items`

Queues normalized item imports into Cosmos and archives original source rows for audit/debug. The HTTP endpoint writes the incoming import payload to Blob Storage, queues a background import job, and returns `202 Accepted` quickly. Use `responseMode=inline` only for small debugging calls. When `customerId` and `customerCode` are omitted, rows are stored as the distributor master item catalog under `customerId: "_global"` and are available for all downstream customers in that distributor tenant. Provide `customerCode` only for customer-specific override lists.

Request shape:

```json
{
  "tenantId": "altitude",
  "sourceName": "itemNumbers.json",
  "contentType": "application/json",
  "fieldMap": {
    "internal_item_number": "Item",
    "description": "Description",
    "upc": "UPC",
    "customer_item_numbers": "Customer Item"
  },
  "customerConfig": {
    "itemRefreshIntervalDays": 3
  },
  "sourceContent": "[{\"Item\":\"10001\",\"Description\":\"Dog food\",\"UPC\":\"012345678905\",\"Customer Item\":\"ABC-123\"}]",
  "rows": []
}
```

Item imports default to a weekly refresh cadence. A customer can override with `customerConfig.itemRefreshIntervalDays`, `refreshIntervalDays`, or `importProfile.refreshIntervalDays`.

Item rows require at least one identifier: internal item number, UPC, customer item number, or alternate item id. Missing-identifier rows are skipped and returned in `errors`.

The universal item row shape is accepted without a field map. `alt_parts_combined` can arrive as an array of `{ "alt_part": "..." }` objects, an array of strings, or a pipe/comma/semicolon/newline-delimited string. It is normalized into an array while also feeding item validation aliases:

```json
{
  "part_code": "100510100",
  "upc_code": "031865BRN4R",
  "alt_parts_combined": [
    { "alt_part": "031865BRN4R" },
    { "alt_part": "10004120" }
  ],
  "part_desc": "Bed-r Nest Kraft Irradiated 4 gram 1600 per case"
}
```

Item imports write:

- `items`
- `auditEvents`
- source rows archive in Blob Storage, or `memory://` archive in local tests

Both customer and item imports support optional embedding generation with `ORDER_PROCESSOR_ENABLE_IMPORT_EMBEDDINGS=true`. Embeddings use the Azure OpenAI endpoint/deployment configuration documented under customer identification and are stored on `customers.embedding` and `items.embedding`.

Example direct-row payload:

```json
{
  "tenantId": "altitude",
  "customerId": "customer-1",
  "fieldMap": {
    "internal_item_number": "Item",
    "description": "Description"
  },
  "rows": [
    {
      "Item": "10001",
      "Description": "Dog food",
      "upc": "012345678905",
      "customer_item_numbers": "ABC-123"
    }
  ]
}
```

## `POST /mailboxes`

Creates or updates a tenant-scoped monitored mailbox configuration. The mailbox represents the distributor/company inbox being monitored; it does not represent one downstream end customer.

Request shape:

```json
{
  "tenantId": "altitude",
  "mailboxAddress": "orders@example.com",
  "displayName": "Orders Mailbox",
  "provider": "microsoft365",
  "connectionId": "m365-connection-1",
  "enabled": true
}
```

Legacy callers may still send `customerId`; the backend stores mailbox records in the `_global` partition and preserves the legacy value only as deprecated metadata under `settings.deprecatedMailboxCustomerId`.

## `POST /mailboxes/{id}/test-connection`

Checks whether the configured delegated Microsoft Graph connection can access the shared mailbox and returns connection/permission status. The backend refreshes the stored Graph token, probes the shared inbox through Graph, updates `mailboxAccounts.permissionStatus`, and updates the matching `microsoftAuthConnections` status. If no delegated refresh token is stored yet, the route returns `needsConsent`.

## Console Auth and Dashboard

The console is hosted as an Azure Web App and should be protected by App Service Easy Auth with Microsoft Entra ID. The Web App proxies browser calls from `/api/*` to APIM, forwarding Easy Auth headers such as `x-ms-client-principal`.

Only `connect@focuseautomate.com` is bootstrapped as the initial `platformAdmin`. Other Microsoft users must be created in `consoleUsers`. Tenant-level roles such as `tenantAdmin` can manage the distributor tenant without assigning the user to every downstream customer; downstream customer-specific access can still be represented through `customerUserAssignments`.

Mailbox automation uses a separate delegated Microsoft Graph OAuth flow started from the customer detail page. The authorized user must already have access to the shared mailbox in Microsoft 365. The app requests `offline_access`, `User.Read`, `Mail.ReadWrite.Shared`, and `Mail.Send.Shared`; refresh/access tokens are stored in Key Vault and Cosmos stores only secret names plus consent/test metadata. The console callback path is `/auth/microsoft/callback`.

Console user permissions:

- `platformAdmin`: full customer, mailbox, routing, profile, user, exception, reprocess, and output access.
- `orderViewer`: assigned-customer dashboard and output downloads.
- `exceptionResolver`: assigned-customer exception resolution.
- `orderManager`: assigned-customer exception resolution, output download, and reprocess controls.

## `POST /console/session`

Returns the signed-in Microsoft user's console session, assignments, customer scope, and permissions. In deployed mode, identity should come from Easy Auth headers. Local/test callers can supply `principalEmail`.

Response includes `authorized`, `consoleUser`, `assignments`, `isPlatformAdmin`, `allowedCustomerIds`, and `permissions`.

## `POST /console/dashboard`

Returns the console dashboard for the signed-in user. Platform admins can see all distributor/customer tenants in `distributorCustomers` and can filter downstream lists by `customerId`. Customer users only see assigned downstream customers inside their tenant.

Response includes:

- `tenant`
- `distributorCustomers`
- `summary`
- `activeRuns`
- `processedOrders`
- `exceptionQueue`
- `mailboxes`
- `routingRules`
- `customers`
- `items`
- `customerDataStatus`
- `itemDataStatus`
- `processorProfiles`
- `outputProfiles`
- `microsoftAuthConnections`
- `outputArtifacts`
- `observabilityMetrics`
- `recentAuditEvents`

`summary` and `observabilityMetrics` include success rate, unresolved item count, customer identification failure count, processor failure count, output-generation failure count, audit event count, and processing latency summary with average, p50, p95, and max milliseconds.

## `POST /console/artifacts/download`

Authorizes access to an output artifact by `orderRunId` plus `artifactId`, or by `blobUrl`. The response always includes the stored artifact reference. Local memory-backed artifacts also return inline `content` or `contentBase64`; deployed Blob artifacts are referenced by URL/metadata.

## Console Admin Routes

The console-prefixed mutation routes require the signed-in user to have the relevant console permission. These routes should be used by the Web App instead of raw service helpers.

- `POST /console/mailboxes`: admin tenant mailbox configuration.
- `POST /console/mailboxes/{id}/test-connection`: admin delegated Graph mailbox access test.
- `POST /console/microsoft-auth/start`: admin delegated Graph OAuth authorization start for the selected mailbox.
- `POST /console/microsoft-auth/callback`: console host callback completion route that exchanges the auth code and stores token secrets.
- `POST /console/tenants`: admin distribution company/tenant configuration.
- `POST /console/customers`: admin downstream end-customer/account profile edits.
- `POST /console/customer-identification-rules`: admin deterministic customer-ID hard-rule edits backed by `customerAliases`.
- `POST /console/routing-rules`: admin routing rule edits.
- `POST /console/processor-profiles`: admin parser profile edits.
- `POST /console/output-profiles`: admin output profile edits.
- `POST /console/users`: admin Microsoft user creation/update.
- `POST /console/customers/{customerId}/users`: admin customer assignment for a Microsoft user.

## `POST /console/users`

Creates or updates a console user by Microsoft email address. Bootstrap admin access starts with `connect@focuseautomate.com`.

Request shape:

```json
{
  "tenantId": "altitude",
  "email": "user@example.com",
  "displayName": "Customer User",
  "roles": ["customerUser"],
  "enabled": true
}
```

In the browser console, this route requires `manageUsers`. The lower-level helper remains available in the backend for service/test setup.

## `POST /customers/{customerId}/users`

Assigns a Microsoft-authenticated console user to a customer.

Request shape:

```json
{
  "tenantId": "altitude",
  "email": "user@example.com",
  "roles": ["orderViewer", "exceptionResolver"]
}
```

The browser console uses `POST /console/customers/{customerId}/users`, which requires `manageUsers`. The non-console route is retained for service-level setup through APIM.

## `POST /exceptions/{id}/resolve`

Records a human resolution. The console uses `POST /console/exceptions/{id}/resolve`, which requires `resolveExceptions` and verifies customer assignment.

Request shape:

```json
{
  "resolution": {
    "selectedCustomerId": "customer-1",
    "notes": "Matched by CSR"
  }
}
```

Resolution behavior:

- `customerIdentification` or routing tasks can apply `selectedCustomerId`/`customerId` to the related `emailMessages` and `orderRuns` records.
- `itemValidation` tasks can apply `matchedInternalItemNumber` to the target `orderRuns.lines[]`, clear validation errors, and move the order toward `completed` when all lines are resolved.
- `parserFailure` and `outputGeneration` tasks can record triage notes or request reprocessing with `reprocess: true`.

## `POST /orders/{orderRunId}/reprocess`

Resets an order run for reprocessing after configuration or exception resolution. The console uses `POST /console/orders/{orderRunId}/reprocess`, which requires `reprocessOrders` and verifies customer assignment.

Request shape:

```json
{
  "reason": "item mapping corrected"
}
```
