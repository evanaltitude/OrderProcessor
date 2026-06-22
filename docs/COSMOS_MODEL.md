# Cosmos Model

Cosmos DB for NoSQL is the canonical operational store for the new platform. The container contract is implemented in `src/order_processor/data_model.py`, mirrored in `infra/main.bicep`, and validated by `tests/test_data_model.py`.

The database name is `orderProcessor`.

## Containers

| Container | Partition key paths | Customer scoped | Purpose |
| --- | --- | --- | --- |
| `tenants` | `/tenantId` | No | Tenant-level settings and environment flags |
| `customers` | `/tenantId` | No | Canonical customer profiles, aliases, routing metadata, embeddings |
| `customerAliases` | `/tenantId`, `/customerId` | Yes, required | Alternate names, account numbers, optional sender rules, regex rules, and deterministic customer matching keys |
| `items` | `/tenantId`, `/customerId` | Yes, required | Canonical item records, customer item numbers, UPCs, aliases, embeddings |
| `routingRules` | `/tenantId`, `/customerId` | Yes, default `_global` | Data-driven email routing rules |
| `processorProfiles` | `/tenantId`, `/customerId` | Yes, default `_global` | Parser configuration by customer/source type |
| `outputProfiles` | `/tenantId`, `/customerId` | Yes, default `_global` | Output format and delivery adapter configuration |
| `mailboxAccounts` | `/tenantId`, `/customerId` | Yes, default `_global` | Tenant-scoped monitored mailbox configuration and ingest status |
| `microsoftAuthConnections` | `/tenantId`, `/customerId` | Yes, default `_global` | Microsoft/Graph/Power Automate connection metadata and consent status; secrets stay in Key Vault |
| `consoleUsers` | `/tenantId` | No | Microsoft login users allowed into the console, including bootstrap admin `connect@focuseautomate.com` |
| `customerUserAssignments` | `/tenantId`, `/customerId` | Yes, required | Customer-scoped user/role assignments for console authorization |
| `emailMessages` | `/tenantId` | No | Ingested email metadata, attachments, status, routing result |
| `orderRuns` | `/tenantId` | No | Order lifecycle, status, output artifacts, errors |
| `orderLines` | `/tenantId`, `/customerId` | Yes, default `_unassigned` | Searchable line-level detail when split from order run documents |
| `exceptionTasks` | `/tenantId` | No | Human review queue for routing, customer ID, item validation, parser failures |
| `auditEvents` | `/tenantId` | No | Timeline events, decisions, confidence scores, user interventions |

Customer-scoped containers use Cosmos hierarchical partition keys where the second partition path is stable. `emailMessages`, `orderRuns`, `exceptionTasks`, and `auditEvents` stay tenant-partitioned because their customer may be unknown or corrected later; those documents still carry `customerId` when known for query/filter use.

## Canonical Key Rules

- Stored Cosmos documents use camelCase keys, especially `tenantId` and `customerId`.
- The local repository accepts existing snake_case dataclass output and normalizes it to camelCase before storage.
- Every document must have `id` and `tenantId`.
- Required customer-scoped containers reject documents without `customerId`.
- Tenant-wide customer-scoped config uses `customerId: "_global"`.
- `mailboxAccounts.customerId` is `_global` for tenant mailbox partitioning and does not identify the downstream end customer.
- Customer-unknown line-level records use `customerId: "_unassigned"` until resolved.

## Canonical Models

Implemented dataclasses in `src/order_processor/models.py`:

- `Tenant`
- `CustomerProfile`
- `CustomerAlias`
- `ItemRecord`
- `RoutingRule`
- `ProcessorProfile`
- `OutputProfile`
- `MailboxAccount`
- `MicrosoftAuthConnection`
- `ConsoleUser`
- `CustomerUserAssignment`
- `EmailMessage`
- `OrderRun`
- `OrderLine`
- `ExceptionTask`
- `AuditEvent`

The model layer also keeps result/helper models such as `RoutingDecision`, `CustomerIdentificationResult`, and `ItemValidationResult`.

## Universal Order Data

`orderRuns` is the durable universal order envelope used by Phase 9 processors. It now carries:

- `header`
- `correlationId`
- `poNumber`
- `orderNumber`
- `sourceType`
- `sourceFileName`
- `sourceMetadata`
- `processorProfileId`
- `processorType`
- `processorVersion`
- `lines`
- `outputArtifacts`
- `errors`
- `parseWarnings`
- `processingStartedAt`
- `processingCompletedAt`

`sourceMetadata` stores mailbox/email/file context such as mailbox, sender, subject, received date, content type, original filename, and `sourceMetadata.observability`. Binary documents and generated files should remain in Blob Storage; Cosmos stores references and audit metadata.

`orderRuns.lines` stores canonical line items with source and validation detail:

- `lineNumber`
- `quantity`
- `providedItemNumber`
- `providedUpc`
- `description`
- `unit`
- `unitPrice`
- `sourceRowIndex`
- `matchedInternalItemNumber`
- `validationStatus`
- `validationConfidence`
- `validationMethod`
- `validationCandidates`
- `validationErrors`
- `raw`

`processorProfiles` select the parser module and customer-specific settings. Supported Phase 9 `processorType` values are `csv`, `xlsx`, `xls`, `xlt`, `pdf`, `emailBody`, and `customerOverride`. Profile settings can include `fieldMap`, `hasHeader`, `headerlessColumns`, `delimiter`, `linePattern`, `baseProcessorType`, `documentIntelligenceModelId`, and source-specific options.

`outputProfiles` may request code-generated XLSX output through `outputType: "xlsx"`, `settings.generateXlsx: true`, or `settings.formats` containing `xlsx`. This replaces Plumsail CSV/XLSX conversion behavior with Azure Function code.

Phase 11 stores generated output files in Blob Storage or local memory, then writes references to `orderRuns.outputArtifacts`. Artifact references include:

- `id`
- `type`
- `fileName`
- `contentType`
- `blobUrl`
- `sizeBytes`
- `checksum`
- `generatedAt`
- `outputProfileId`
- `outputProfileName`
- `destination`
- `metadata`

Supported output profile types are:

- `csv`
- `xlsx`
- `text`
- `api`
- `json`
- `multi`

`api` output profiles generate a stored payload with destination metadata and `pendingExternalDelivery` status. Direct external delivery is deferred to customer-specific delivery adapters or a later backend worker.

## Console Authorization and Configuration Data

Phase 12 stores console identity and authorization separately from customer configuration:

- `consoleUsers` stores Microsoft-authenticated users allowed into the console. The bootstrap admin is `connect@focuseautomate.com` with `roles: ["platformAdmin"]`.
- `customerUserAssignments` maps a Microsoft email to a downstream customer and role list when customer-specific access is needed. Tenant-level roles on `consoleUsers`, such as `tenantAdmin`, can see/manage all downstream customers inside the tenant without thousands of assignments.
- `mailboxAccounts` stores tenant-scoped monitored mailboxes, provider, connection id, enabled/ingest status, permission status, Graph user id, folder ids, and settings. Legacy mailbox `customerId` values are ignored for matching and may be preserved only as deprecated metadata.
- `microsoftAuthConnections` stores Microsoft/Graph/Power Automate connection metadata and consent status. Secrets stay in Key Vault; Cosmos stores only secret names and metadata.

Console roles map to permissions in the backend:

- `platformAdmin`: full console access across customers, users, routing, mailboxes, profiles, exceptions, reprocess, and outputs.
- `tenantAdmin`: full console access within one distributor tenant without cross-tenant/platform access.
- `tenantUser`: all-customer dashboard access within one distributor tenant.
- `orderViewer`: assigned-customer dashboard/output access.
- `exceptionResolver`: assigned-customer exception resolution.
- `orderManager`: assigned-customer exception resolution, output download, and reprocess controls.

The console dashboard is a composed read model over `orderRuns`, `exceptionTasks`, `mailboxAccounts`, `routingRules`, `customers`, `items`, `processorProfiles`, `outputProfiles`, `microsoftAuthConnections`, and `orderRuns.outputArtifacts`. No separate dashboard container is introduced in this phase.

## Observability and Audit Data

Phase 13 uses `auditEvents` as the durable timeline store. The backend extracts correlation context from request bodies and headers, including `correlationId`, W3C `traceparent`, APIM request ids, Power Automate flow run ids, and Durable instance ids.

`auditEvents` now carry these top-level fields for queryability:

- `correlationId`
- `operationId`
- `traceId`
- `subjectId`
- `customerId`
- `orderRunId`
- `emailMessageId`
- `actor`
- `eventType`
- `createdAt`
- `details`

`details.observability` preserves the full extracted context, including Power Automate and Durable identifiers when supplied. User actions such as exception resolution, output artifact access, and reprocess requests include `userIntervention: true` and record the console actor.

Order timeline views are generated from `orderRuns`, the source `emailMessages` record, related `exceptionTasks`, `auditEvents`, and `orderRuns.outputArtifacts`. No separate timeline container is introduced in this phase.

Dashboard observability metrics are derived from the same operational records:

- success rate
- unresolved item count
- customer identification failure count
- processor failure count
- output-generation failure count
- audit event count
- processing latency average/p50/p95/max

## Item Validation Data

`items` remains the canonical lookup source for `/items/validate`. The endpoint loads customer-scoped records by `tenantId` and `customerId`, then evaluates:

- `items.internalItemNumber`
- `items.customerItemNumbers`
- `items.aliases`
- `items.upc`
- `items.description`

Validation results are written back to `orderRuns.lines[]` when the caller supplies `orderRunId` and `lineNumber`:

- `matchedInternalItemNumber`
- `validationStatus`
- `validationConfidence`
- `validationMethod`
- `validationCandidates`
- `validationErrors`

When validation is not confidently matched, an `exceptionTasks` record is created with `type: "itemValidation"` and context containing the validation result, compact request, and source row context. `auditEvents` records `item.validated` with status, confidence, match method, candidate count, and exception task id when one is created.

## Vector Fields

`customers` and `items` include `/embedding` as a vector field. These vectors support customer and item fallback matching after deterministic rules fail.

Default embedding dimensions in the Bicep parameter file are `1536`; update `embeddingDimensions` if the selected Azure OpenAI embedding model uses a different size.

Customer identification uses `customers.embedding` for Azure OpenAI/Cosmos vector fallback. Deterministic signals should still be kept on canonical records because they are more auditable and cheaper than vector matching:

- `customers.customerCode`
- `customers.storeNumber`
- `customers.routeNumber`
- `customers.knownSubjectPatterns`
- `customers.aliases`
- `customerAliases` records for account number/customer code, store number, route number, sender email, sender domain, known subject pattern, body pattern, and file-name pattern

Sender domains are intentionally no longer exposed as a primary console field on customer profiles. Use `customerAliases` customer-identification rules for sender email/domain only when that signal is deterministic for a downstream end customer.

Phase 8 import endpoints can populate `customers.embedding` and `items.embedding` when `ORDER_PROCESSOR_ENABLE_IMPORT_EMBEDDINGS=true`. Local/offline imports leave embeddings empty unless an embedding client is injected by tests.

## Customer and Item Import Data

Customer and item refreshes write normalized records to Cosmos and preserve original rows in Blob Storage.

`customers` and `items` now carry import status metadata:

- `sourceName`
- `sourceRowsBlobUrl`
- `lastImportedAt`
- `rawSource`

`rawSource` contains the original row, source metadata, row index, import run id, checksum, archive timestamp, and source rows blob URL. Binary/source files remain in Blob Storage; Cosmos stores canonical fields and audit references.

Customer imports also generate `customerAliases` records for:

- `customerCode`
- `storeNumber`
- `routeNumber`
- `senderEmail`
- `senderDomain`
- `knownSubjectPattern`

Import runs are not stored in a separate container in this phase. Import summaries are written to `auditEvents` as `customers.imported` and `items.imported`, including parser module, source rows blob URL, checksum, imported/created/updated/skipped/error counts, and refresh policy.

Refresh cadence:

- Customer imports default to every 1 day.
- Item imports default to every 7 days.
- Overrides can come from `refreshIntervalDays`, `importProfile.refreshIntervalDays`, `customerConfig.customerRefreshIntervalDays`, or `customerConfig.itemRefreshIntervalDays`.

## Email Ingestion and Routing Data

`emailMessages` stores the Phase 6 ingestion record:

- `mailbox`
- `mailboxAccountId`
- `messageId`
- `sender`
- `subject`
- `receivedAt`
- `bodyText`
- `bodyHtml`
- `categories`
- `attachments`
- `status`
- `customerId`
- `orderRunId`
- `correlationId`
- `routing`
- `customerIdentification`
- `source`
- `createdAt`
- `updatedAt`

Attachment records store `name`, `contentType`, `size`, `blobUrl`, `sourceUrl`, `contentId`, `isInline`, and `metadata`. Binary content belongs in Blob Storage; Cosmos stores metadata and blob/source references only.

Universal customer imports can use distributor-customer fields such as `cust_code`, `customer_name`, `customer_store_number`, `location_address1`, `location_city`, `location_state`, `location_zip`, `phone`, `customer_website`, and `customer_email` without a field map. These normalize into `CustomerProfile` fields while preserving the original source row in `rawSource`.

Universal item imports can use `part_code`, `upc_code`, `alt_parts_combined`, and `part_desc` without a field map. `alt_parts_combined` is stored as an array on `items.altPartsCombined` and also feeds normalized searchable item numbers for `items.customerItemNumbers`, so the item validator can match UPCs and alternate item identifiers.

`routingRules` are data-driven and support tenant-wide distributor rules through `customerId: "_global"` plus customer-specific rules through the customer partition. Routing rules include an ordered `phase`:

- `webstoreOrder`
- `previouslyProcessed`
- `orderCandidate`
- `nonOrder`
- `general`

Rules are evaluated in that order. This mirrors the current Power Automate flow shape: known webstore messages can extract a downstream customer code first, already-processed subject formats can recover the customer code next, then distributor-specific order-candidate rules decide whether to create an order run, followed by non-order/general handling.

Rule signals include:

- `mailboxAccountIds`
- `mailboxAddresses`
- `senderEquals`
- `senderDomains`
- `subjectRegex`
- `bodyRegex`
- `knownWebstorePatterns`
- `priorProcessedSubjectRegex`
- `attachmentExtensions`
- `attachmentContentTypes`
- `attachmentNameRegex`
- `requiredAttachment`
- `tags`
- `customerCodeExtraction`
- `subjectUpdate`
- `emailActions`

`customerCodeExtraction` stores a regex extraction policy with `source`, `regex`, `group`, and `required`. The extracted value is matched against `customers.customerCode` and `customerAliases` account/customer-code aliases.

`subjectUpdate` stores distributor-specific subject policy, including templates such as `Cust: {customerCode} Rte: {routeNumber} - {originalSubject}` and extraction regexes for already-processed subjects.

`emailActions` stores planned Graph/Power Automate adapter actions. Category templates can use customer fields such as `{csrName}`, and move policies can be configured separately for `processedOrder`, `failedOrder`, `nonOrder`, and `ignored` with `none`, `staticFolder`, or `customerField` modes.

Routing decisions are stored on the email under `routing`, including `outcome`, `ruleId`, `customerId`, `processorProfileId`, `mailboxAccountId`, `confidence`, `reasons`, and `matchedSignals`.

## Mailbox and Microsoft Auth Data

`mailboxAccounts` stores distributor/tenant mailbox configuration:

- `mailboxAddress`
- `customerId` as `_global` partition metadata only
- `provider`
- `connectionId`
- `enabled`
- `ingestStatus`
- `permissionStatus`
- `requiredPermissions`
- `graphUserId`
- `folderIds`
- `settings`
- `lastTestedAt`

`microsoftAuthConnections` stores non-secret connection metadata:

- `provider`
- `customerId` or `_global`
- `ownerEmail`
- `connectionType`
- `status`
- `scopes`
- `keyVaultSecretNames`
- `powerAutomateConnectionReference`
- `tenantAuthority`
- consent/test metadata

Secrets, refresh tokens, client secrets, and certificates stay in Key Vault and are referenced by name only.

## Console Authorization Data

Console authorization is separate from customers:

- `consoleUsers` stores Microsoft-authenticated users and platform roles.
- `customerUserAssignments` stores customer-scoped roles.
- The bootstrap admin is `connect@focuseautomate.com` with `platformAdmin`.

This allows one Microsoft user to be assigned to one or more customers without duplicating identity records.

## Repository Behavior

`src/order_processor/storage.py` provides:

- `InMemoryRepository` for local tests and local Functions work.
- `CosmosRepository` for deployed Azure usage with Entra identity/RBAC.
- `repository_from_environment()` which selects Cosmos only when `ORDER_PROCESSOR_STORAGE_BACKEND=cosmos`.

Local settings use `ORDER_PROCESSOR_STORAGE_BACKEND=memory`. The deployed Function app uses `ORDER_PROCESSOR_STORAGE_BACKEND=cosmos` from Bicep.

## Operational Rules

- Keep `tenantId` on every document.
- Keep `customerId` on customer-scoped operational/configuration records.
- Keep mailbox configuration tenant-scoped and queryable from console configuration. Do not use mailbox configuration to assign downstream `customerId`.
- Keep console authorization data separate from customer profiles.
- Store source files and original source rows in Blob Storage, then reference blob URLs from Cosmos documents.
- Store secrets in Key Vault only; Cosmos documents contain identifiers and metadata, not credentials.
- Use `auditEvents` for every automated decision that a CSR may later need to understand.
