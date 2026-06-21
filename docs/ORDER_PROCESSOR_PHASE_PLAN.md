# Order Processor Platform Phase Plan

## Summary

Build the new `OrderProcessor` solution in Microsoft environment `abbd708f-4eaf-e875-a282-e1207f4e370c` under `evanb@altitudelogistics.com`, using Azure as the system of record and Power Automate as a thin mailbox/customer-adapter layer. Default choices: Azure core architecture, Cosmos DB including vector search for customer/item records, customer-specific monitored mailboxes, Microsoft/Entra ID login for console access, and one pilot customer migrated end-to-end first.

## Current References

- Flow library: `power-automate/solutions/OrdersAutomations/flow-library`
- SharePoint reference: `power-automate/solutions/OrdersAutomations/sharepoint-reference`
- External dependency reference: `power-automate/solutions/OrdersAutomations/external-dependencies`
- Current system map: `docs/CURRENT_SYSTEM_MAP.md`
- API contracts: `docs/API_CONTRACTS.md`
- Cosmos model: `docs/COSMOS_MODEL.md`
- Power Automate shell design: `docs/POWER_AUTOMATE_SHELL.md`
- Phase 1 baseline: `docs/PHASE_1_BASELINE.md`
- Phase 2 reverse engineering: `docs/reverse-engineering/PHASE_2_REVERSE_ENGINEERING.md`
- Phase 3 Azure foundation: `docs/PHASE_3_AZURE_FOUNDATION.md`
- Phase 4 core data model: `docs/PHASE_4_CORE_DATA_MODEL.md`
- Phase 5 Power Automate shell: `docs/PHASE_5_POWER_AUTOMATE_SHELL.md`
- Phase 6 email ingestion and routing: `docs/PHASE_6_EMAIL_INGESTION_ROUTING.md`
- Phase 7 customer identification: `docs/PHASE_7_CUSTOMER_IDENTIFICATION.md`
- Phase 8 customer and item data refresh: `docs/PHASE_8_CUSTOMER_ITEM_REFRESH.md`
- Phase 9 order processing engine: `docs/PHASE_9_ORDER_PROCESSING_ENGINE.md`
- Phase 10 item validation service: `docs/PHASE_10_ITEM_VALIDATION_SERVICE.md`
- Phase 11 output generation: `docs/PHASE_11_OUTPUT_GENERATION.md`
- Phase 12 console backend and frontend: `docs/PHASE_12_CONSOLE_BACKEND_FRONTEND.md`
- Phase 13 observability and audit: `docs/PHASE_13_OBSERVABILITY_AUDIT.md`
- Flow capability map: `docs/reverse-engineering/FLOW_CAPABILITY_MAP.md`
- Order-process behavior analysis: `docs/reverse-engineering/ORDER_PROCESS_FLOW_ANALYSIS.md`
- Phase 2 sample fixtures: `samples/phase-2`
- OrderProcessor shell implementation: `power-automate/solutions/OrderProcessor`
- OrderProcessor shell manifest: `power-automate/solutions/OrderProcessor/shell-solution-manifest.json`
- OrderProcessor shell flow catalog: `power-automate/solutions/OrderProcessor/FLOW_TEMPLATE_CATALOG.md`
- OrderProcessor shell configuration contract: `power-automate/solutions/OrderProcessor/CONFIGURATION_CONTRACT.md`

SharePoint list schemas/items still need a successful live refresh after Graph authentication is available. The latest live attempt on 2026-06-20 was blocked by Conditional Access token policy and is recorded in `power-automate/solutions/OrdersAutomations/sharepoint-reference/retrieval-status.json`; the extracted static flow/list references remain current for review.

Current CSV clarification: the only active CSV processor reference flow is `orderProcess - CSV Parse`. The two removed/deprecated CSV variants should not be included in future migration scope.

## Numbered Phases

1. **Planning Artifact and Baseline** - Completed 2026-06-20
   - Create this plan as `docs/ORDER_PROCESSOR_PHASE_PLAN.md`.
   - Keep current references linked: flow library, SharePoint reference, external dependency reference.
   - Refresh SharePoint list schema/items once Graph auth is available.
   - Completion notes:
     - Planning artifact exists and links all current baseline references.
     - Phase 1 baseline was recorded in `docs/PHASE_1_BASELINE.md`.
     - Flow library was regenerated from current local workflow definitions: 32 active reference flows.
     - External dependency inventory was regenerated: 365 connector actions and 461 URL references.
     - Static SharePoint reference was regenerated: 6 lists, 126 SharePoint actions, and 97 list actions.
     - Live SharePoint schema/item export was attempted through Graph on 2026-06-20 and failed due Frontier tenant Conditional Access error `AADSTS530036`. This is an external authentication blocker, not a missing local implementation step.
     - The baseline confirms `orderProcess - CSV Parse` is the only active CSV processor reference.
     - Raw workflow exports and dependency inventories remain sensitive because they may contain tenant-specific endpoints, connection references, and trigger URLs.

2. **Reverse-Engineer Current Behavior** - Completed 2026-06-20
   - Map every existing flow to one capability: mailbox routing, customer ID, order processing, item validation, data refresh, output generation, or support form.
   - For each active `orderProcess*` flow, document input trigger schema, parsing rules, output format, customer-specific assumptions, and error paths.
   - Treat `orderProcess - CSV Parse` as the only active CSV flow; ignore the two removed/deprecated CSV variants.
   - For `orderProcess - CSV Parse`, document the Plumsail CSV parsing/conversion usage and define the Azure Function/code replacement path.
   - Gather representative sample emails/files for CSV, XLSX, XLS/XLT, PDF, email body, and customer-specific cases.
   - Completion notes:
     - Phase 2 summary was recorded in `docs/reverse-engineering/PHASE_2_REVERSE_ENGINEERING.md`.
     - Every active reference flow was mapped to exactly one migration capability in `docs/reverse-engineering/FLOW_CAPABILITY_MAP.md` and `docs/reverse-engineering/flow-capability-map.json`.
     - Active reference flow count remains 32: 2 mailbox routing, 9 customer ID, 11 order processing, 1 item validation, 5 data refresh, and 4 support form flows.
     - No standalone output-generation-only flows were found; current output generation behavior is embedded inside order-processing flows and should move into the Phase 11 output adapter model.
     - All 11 order-processing flows were documented in `docs/reverse-engineering/ORDER_PROCESS_FLOW_ANALYSIS.md` and `docs/reverse-engineering/order-process-flow-analysis.json`.
     - The order-process analysis captures trigger contracts, parsing rules, output side effects, customer-specific assumptions, error paths, migration notes, and notable connector/API dependencies for each flow.
     - `orderProcess - CSV Parse` is confirmed as the only active CSV processor reference. Its Plumsail `FlowV1DocumentsJobsParseCsvPost` dependency is documented as a Phase 9 replacement target for Azure Function code using a robust CSV parser and optional code-generated XLSX output only when a customer output profile requires it.
     - The XLS/XLT AI-header flow's Plumsail `FlowV1DocumentsJobsXls2XlsxPost` dependency is documented separately as a workbook-conversion replacement target.
     - PDF flows' Google Document AI dependency, OpenAI prompt-based extraction, Office Scripts/Excel operations, hard-coded Frontier mailbox usage, SharePoint Automations site usage, CSR routing, and customer-specific parser assumptions were captured for migration planning.
     - Representative synthetic fixtures were generated under `samples/phase-2` with `samples/phase-2/manifest.json`. The fixture set covers CSV, CSV-with-header, XLSX, legacy XLS, legacy XLT, PDF, generic email body, Marketplace Pet Supplies email body, and Petland customer-specific XLSX cases.
     - No real production sample emails/files were available in the local workspace during this phase. The Phase 2 fixtures are safe synthetic baselines for parser design and tests; pilot/shadow-run work must add real approved samples before production parity decisions.

3. **Azure Foundation** - Completed 2026-06-20
   - Create IaC for resource group, Azure Functions with Durable Functions, Cosmos DB for NoSQL, Blob Storage, Key Vault, Application Insights, API Management, Azure AI Foundry/Azure OpenAI, and Azure Document Intelligence.
   - Expose all callable backend APIs through API Management; remove direct unauthenticated workflow-webhook style calls from the new design.
   - Use managed identities/RBAC for Azure service-to-service access; keep secrets only in Key Vault.
   - Completion notes:
     - Phase 3 summary and deployment runbook were recorded in `docs/PHASE_3_AZURE_FOUNDATION.md`.
     - Subscription-scope IaC was added in `infra/subscription.bicep` to create the resource group and invoke the foundation template.
     - Resource-group-scope IaC in `infra/main.bicep` now provisions the Azure foundation: Linux Python Azure Functions, Durable Functions storage settings, Cosmos DB for NoSQL, Blob Storage, Key Vault, Log Analytics, Application Insights, API Management, Azure AI Services, Azure OpenAI, and Azure Document Intelligence.
     - Blob containers were expanded to `email-attachments`, `order-artifacts`, `source-rows`, `imports`, and `dead-letter` so email ingestion, imports, output generation, audit/debug, and failure isolation have defined storage locations.
     - Cosmos local auth is disabled and the `customers` and `items` containers retain vector embedding policy/indexing for future Azure OpenAI embedding search.
     - Storage shared key access is disabled and Function host storage uses identity-based `AzureWebJobsStorage__*` settings instead of account keys.
     - Key Vault uses RBAC and stores the APIM-to-Function host key; API Management reads that secret through managed identity.
     - Azure OpenAI, Azure AI Services, and Document Intelligence accounts have local key auth disabled; Function access is intended through Entra/RBAC.
     - Function app and API Management both use system-assigned managed identities.
     - RBAC assignments were added for Function access to Storage Blob/Queue/Table data, Key Vault secrets, Cosmos DB SQL data, Azure OpenAI, Azure AI Services, and Document Intelligence. API Management receives Key Vault Secrets User.
     - API Management imports `infra/openapi/order-processor-api.yaml`, publishes the `order-processor` API under a subscription-required Power Automate adapter product, and injects the Function host key through an API policy. New Power Automate shell flows should call APIM only, not raw Function URLs or direct workflow webhooks.
     - Functions runtime files were updated for Durable Functions readiness and Azure client dependencies.
     - Function facade scaffolds now include callable routes for the full public APIM contract, including mailbox configuration, mailbox connection-test placeholder, console user upsert, and customer user assignment. Live Graph permission testing and full console authorization remain in later phases.
     - The foundation was validated locally with Bicep builds and tests. No live Azure deployment was run during this phase to avoid creating billable resources without an explicit deployment instruction.
     - Azure OpenAI model deployments are intentionally deferred until model/version/SKU/capacity choices are made during customer identification and item matching implementation.

4. **Core Data Model** - Completed 2026-06-20
   - Create Cosmos containers: `tenants`, `customers`, `customerAliases`, `items`, `routingRules`, `processorProfiles`, `outputProfiles`, `mailboxAccounts`, `microsoftAuthConnections`, `consoleUsers`, `customerUserAssignments`, `emailMessages`, `orderRuns`, `orderLines`, `exceptionTasks`, and `auditEvents`.
   - Partition all operational data by `tenantId`, then `customerId` where appropriate.
   - Store customer and item embeddings in Cosmos alongside canonical records for vector/keyword matching.
   - Store mailbox configuration by customer, including mailbox address, owning customer, Graph/Power Automate connection metadata, ingest status, and permission requirements.
   - Store console access assignments separately from customer records so a Microsoft user can be granted access to one or more customers.
   - Completion notes:
     - Phase 4 summary and handoff were recorded in `docs/PHASE_4_CORE_DATA_MODEL.md`.
     - The canonical Cosmos container contract was implemented in `src/order_processor/data_model.py` with all 16 required containers, partition key paths, customer-scope rules, vector metadata, document normalization, and partition-key helpers.
     - `docs/COSMOS_MODEL.md` was expanded with the complete container table, canonical key rules, model list, vector field rules, mailbox/auth metadata rules, console authorization separation, and repository behavior.
     - `src/order_processor/models.py` now includes the missing canonical records: `Tenant`, `CustomerAlias`, `MailboxAccount`, `MicrosoftAuthConnection`, `ConsoleUser`, and `CustomerUserAssignment`.
     - `src/order_processor/storage.py` now provides `InMemoryRepository`, `CosmosRepository`, and `repository_from_environment()`. Local development defaults to memory; deployed Functions use Cosmos through `ORDER_PROCESSOR_STORAGE_BACKEND=cosmos`.
     - Repository storage normalizes snake_case dataclass output into Cosmos-compatible camelCase keys, including `tenantId` and `customerId`.
     - `infra/main.bicep` was updated so stable customer-scoped containers use hierarchical partition keys `/tenantId`, then `/customerId`. Tenant-wide or customer-mutable records such as `emailMessages`, `orderRuns`, `exceptionTasks`, and `auditEvents` remain tenant-partitioned while carrying `customerId` when known.
     - `customers` and `items` retain vector embedding policies; `items` now partitions by tenant plus customer so item matching can be scoped per customer.
     - Mailbox configuration is represented by `MailboxAccount` in `mailboxAccounts`, including mailbox address, customer owner, connection id, ingest status, permission status, required permissions, Graph user id, folder ids, settings, and test timestamp.
     - Microsoft auth/connection metadata is represented by `MicrosoftAuthConnection` in `microsoftAuthConnections`; secrets remain in Key Vault and Cosmos stores only secret names/metadata.
     - Console authorization is represented separately with `ConsoleUser` and `CustomerUserAssignment`, preserving the bootstrap admin `connect@focuseautomate.com` as `platformAdmin` in the API scaffold.
     - Data model tests were added in `tests/test_data_model.py`; infra tests now also guard hierarchical partition key configuration.
     - No live Cosmos deployment or Graph/SharePoint refresh was run during this phase.

5. **Power Automate Shell Solution** - Completed 2026-06-20
   - Create Power Platform solution `OrderProcessor`.
   - Add minimal standard flows:
     - Customer-specific mailbox trigger flow/template: detects new order mailbox email for a configured customer mailbox and calls Azure `/emails/ingest`.
     - Customer data import flow template: receives customer-specific source files and calls Azure `/imports/customers`.
     - Item data import flow template: receives item source files and calls Azure `/imports/items`.
     - Optional output adapter flow template: handles customer-specific delivery when delivery must stay in M365/Power Automate.
   - Mailbox addresses and Microsoft authentication/connection requirements must be visible and editable through backend configuration, not hidden inside flow branches.
   - All complex processing moves to Azure Functions unless a customer adapter must remain user-editable.
   - Completion notes:
     - Phase 5 summary and activation handoff were recorded in `docs/PHASE_5_POWER_AUTOMATE_SHELL.md`.
     - PAC was used against the target Focus Automate environment `abbd708f-4eaf-e875-a282-e1207f4e370c` as `evanb@altitudelogistics.com`.
     - The online unmanaged solution exists as unique name `OrderProcessor`, display name `Order Processor`, version `1.0.0.0`.
     - `tools/Build-OrderProcessorShellSolution.ps1` now generates the local shell templates, Dataverse workflow components, connection reference metadata, catalog, configuration contract, manifest, and unmanaged solution package.
     - Four disabled template flows were generated under `power-automate/solutions/OrderProcessor/flow-templates` and packaged under `power-automate/solutions/OrderProcessor/exports/OrderProcessor_1.0.0.0_unmanaged.zip`.
     - The packaged shell workflows are `OrderProcessor - Mailbox Trigger Template` (`7f52d9d6-8eb1-4ad7-b2f6-89dd55dc4e01`), `OrderProcessor - Customer Import Adapter Template` (`06b7f4fc-5f13-4a6f-8742-03f19c301902`), `OrderProcessor - Item Import Adapter Template` (`7aa4ab3f-d509-4866-98a0-fcce8dc79b03`), and `OrderProcessor - Output Delivery Adapter Template` (`92df442b-2360-42dd-b787-ce339813d877`).
     - The mailbox trigger template uses the Office 365 Outlook shared-mailbox new-email trigger and calls APIM `POST /emails/ingest` with mailbox address, mailbox account id, optional customer id, message metadata, body preview/body content, and attachment references.
     - Customer and item import templates are HTTP adapter templates that forward source rows/metadata to APIM `POST /imports/customers` and `POST /imports/items`.
     - The optional output delivery template is a disabled placeholder for customer-specific M365 delivery only when output delivery must stay in Power Automate.
     - The solution defines Office 365 Outlook connection reference `alt_sharedoffice365_orderprocessor` for mailbox trigger instances and future M365-specific delivery adapters.
     - The unmanaged solution package was imported asynchronously into the target Focus Automate environment on 2026-06-20. PAC reported import id `74e34f63-cb6c-f111-ab0d-7c1e5281c285` with `Solution Imported successfully`.
     - Live Dataverse verification found the four workflow components and connection reference `cbfe0a69-28e2-4b44-94eb-eeecba52a48f` in the `OrderProcessor` solution.
     - Shell templates intentionally keep all APIM values parameterized through `OrderProcessorApiBaseUrl` and `OrderProcessorApimSubscriptionKey`; no raw Azure Function URL or direct Power Platform webhook dependency is embedded.
     - Guardrail tests verify the shell templates do not contain SharePoint operational storage, Plumsail, Google Document AI, direct OpenAI calls, old Power Platform workflow webhook chains, or CSV/XLS conversion actions.
     - Mailbox addresses, mailbox ownership, Microsoft connection metadata, Graph permission state, customer/user assignments, routing decisions, parser selection, item validation, and output generation remain backend/console responsibilities.
     - The imported Outlook connection reference still requires a concrete connection binding before any dependent mailbox trigger or M365 delivery flow can be turned on. The templates are intentionally disabled until APIM deployment values, mailbox configuration, and connection references are configured for a customer.

6. **Email Ingestion and Routing** - Completed 2026-06-20
   - Azure stores each email, attachments, message IDs, sender, subject, received date, mailbox, and processing status.
   - Mailbox identity is part of routing. A message received in a customer-specific mailbox can directly scope customer identification, routing rules, and available processor profiles.
   - Implement routing rules as data, not flow branches: sender, subject/body regex, attachment type, file name, known webstore patterns, prior processed subject patterns.
   - The router returns one of: `knownOrder`, `knownCustomerNonOrder`, `needsCustomerIdentification`, `needsHumanReview`, or `ignored`.
   - Completion notes:
     - Phase 6 summary and handoff were recorded in `docs/PHASE_6_EMAIL_INGESTION_ROUTING.md`.
     - `EmailMessage` now persists mailbox account id, customer id when known, message id, sender, subject, received date, body text/body HTML, categories, attachment metadata, processing status, routing decision, source metadata, correlation id, and timestamps.
     - `EmailAttachment` now records `name`, `contentType`, `size`, `blobUrl`, `sourceUrl`, `contentId`, `isInline`, and metadata. Binary attachment content remains a Blob Storage responsibility; Cosmos stores references and metadata.
     - `/emails/ingest` resolves mailbox configuration by `mailboxAccountId` first and mailbox address second. Resolved customer-specific mailbox accounts scope the email to the owning customer.
     - Disabled mailbox accounts route to `ignored`; unknown mailbox account ids route to `needsHumanReview`; payload customer/mailbox customer conflicts route to `needsHumanReview` and preserve the mailbox customer as the effective customer.
     - Routing rules remain data-driven in `routingRules` and now support mailbox account ids, mailbox addresses, sender exact match, sender domains, subject regex, body regex, known webstore patterns, prior processed subject patterns, attachment extensions, attachment content types, attachment filename regex, required attachments, tags, priority, customer scope, and processor profile selection.
     - Tenant-wide routing rules continue to use `customerId: "_global"`. When a mailbox scopes the email to a customer, the router considers tenant-wide rules plus that customer's rules, preventing another customer's rule from claiming the message.
     - `knownOrder` creates an `orderRun` with customer and processor profile context. `knownCustomerNonOrder` and `ignored` store the email/routing decision without creating an order run. `needsCustomerIdentification` and `needsHumanReview` create routing exception tasks.
     - The fallback outcome is `needsCustomerIdentification` when no rule matches and no customer is known. If no rule matches but a customer-specific mailbox already identifies the customer, the fallback is `needsHumanReview` so a CSR/admin can add an explicit routing rule instead of silently processing mail.
     - Every ingest writes an `auditEvents` record with routing decision, mailbox context, and a diagnostic default-order signal.
     - API responses for dataclass-backed records now normalize to camelCase so Power Automate, console callers, OpenAPI examples, and stored Cosmos documents share the same field shape.
     - `infra/openapi/order-processor-api.yaml`, `docs/API_CONTRACTS.md`, and `docs/COSMOS_MODEL.md` were updated with the Phase 6 ingestion/routing contract.
     - Tests were expanded in `tests/test_routing.py` and `tests/test_api.py` for mailbox scoping, rule filtering, disabled/unknown/conflicting mailbox behavior, content-type matching, prior processed subject patterns, known webstore patterns, invalid regex safety, persisted routing decisions, and attachment metadata.

7. **Customer Identification** - Completed 2026-06-20
   - Replace OpenAI vector store usage with Azure OpenAI embedding calls plus Cosmos vector search.
   - Apply deterministic extraction first: customer code, store number, route number, known subject pattern, sender domain.
   - Use AI/vector fallback only when deterministic rules do not produce a confident match.
   - If confidence is below threshold, create an `exceptionTask` for console resolution instead of failing the run.
   - Completion notes:
     - Phase 7 summary and handoff were recorded in `docs/PHASE_7_CUSTOMER_IDENTIFICATION.md`.
     - `src/order_processor/customer_identification.py` now implements deterministic signal extraction for customer code, store number, route number, sender domain, and subject text.
     - Deterministic matching now checks canonical `customers` fields, `customers.knownSubjectPatterns`, `customers.aliases`, and `customerAliases` records for customer code, store number, route number, sender domain, and known subject pattern aliases.
     - Deterministic confidence values are explicit and auditable: customer context and customer code `1.0`, store number `0.96`, route number `0.93`, known subject pattern `0.92`, and sender domain `0.9`.
     - The default confidence threshold is `0.85`, and callers can override it with `confidenceThreshold`.
     - Deterministic matching must be above threshold and unique to short-circuit. Ambiguous or below-threshold deterministic matches can fall through to vector search when enabled; otherwise they become `possibleMatch` and create a `customerIdentification` exception task instead of failing processing.
     - Vector fallback is implemented behind `CustomerVectorSearch`. It is disabled by default locally and can be enabled with `ORDER_PROCESSOR_ENABLE_CUSTOMER_VECTOR_SEARCH=true`.
     - `AzureOpenAIEmbeddingClient` creates embeddings from `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, using `AZURE_OPENAI_API_KEY` when present or managed identity/`DefaultAzureCredential` when no key is configured.
     - `CosmosCustomerVectorSearch` uses repository-native customer vector search when available, and local/in-memory cosine similarity for tests and offline development.
     - `CosmosRepository.vector_search_customers()` now provides a native Cosmos `VectorDistance` query over `customers.embedding`.
     - `/customers/identify` now loads `customers` and `customerAliases` from Cosmos/repository by tenant unless controlled caller/test payloads supply them directly.
     - A confident match updates existing `emailMessages.customerId`, stores `emailMessages.customerIdentification`, and updates associated `orderRuns.customerId` when those records exist.
     - Unresolved, ambiguous, and low-confidence vector results create `customerIdentification` exception tasks with extracted signals, candidate details, subject, sender, mailbox, and threshold context for console resolution.
     - Every identification attempt writes a `customer.identified` audit event.
     - API responses include `extractedSignals`, candidates, confidence, reasons, and match method in camelCase.
     - `CustomerProfile` now carries `knownSubjectPatterns`, and customer import normalization supports semicolon-delimited `known_subject_patterns`.
     - `infra/openapi/order-processor-api.yaml`, `docs/API_CONTRACTS.md`, and `docs/COSMOS_MODEL.md` were updated with the Phase 7 contract and storage expectations.
     - Tests were expanded in `tests/test_customer_identification.py`, `tests/test_api.py`, and `tests/test_imports_output.py` for deterministic matching, alias matching, ambiguity, vector fallback, low-confidence exception creation, stored email/order updates, and known subject pattern import.

8. **Customer and Item Data Refresh** - Completed 2026-06-20
   - Implement per-customer parser modules for customer lists and item lists.
   - Customer list refresh runs daily; item list refresh runs weekly unless customer config overrides it.
   - Imports write normalized canonical records to Cosmos and preserve original source rows in Blob Storage for audit/debug.
   - Replace current SharePoint item/customer local copies with Cosmos containers.
   - Completion notes:
     - Phase 8 summary and handoff were recorded in `docs/PHASE_8_CUSTOMER_ITEM_REFRESH.md`.
     - `src/order_processor/imports.py` now provides an import parser registry for `rows`, `csv`, `json`, `jsonl`, `genericCustomerCsv`, and `genericItemCsv`.
     - Parser modules can be selected with `parserModule` or `importProfile.parserModule`; field maps can be supplied directly or through `importProfile.fieldMap`.
     - Customer imports validate that each row has `customer_code` or `name`; item imports validate that each row has at least one of `internal_item_number`, `upc`, or `customer_item_numbers`.
     - Customer import normalization writes canonical `customers` records with customer code, name, route number, CSR metadata, store number, sender domains, aliases, known subject patterns, source name, source-row archive URL, import timestamp, and raw source metadata.
     - Customer imports now generate `customerAliases` records for customer code, store number, route number, sender domain, and known subject pattern so Phase 7 deterministic matching has normalized lookup records.
     - Item import normalization writes canonical `items` records with internal item number, description, UPC, customer item numbers, aliases, source name, source-row archive URL, import timestamp, and raw source metadata.
     - Original source rows are serialized as JSONL before normalization and archived through the `SourceRowArchive` abstraction. Local/offline work uses `InMemorySourceRowArchive`; deployed mode can use Azure Blob Storage with `ORDER_PROCESSOR_SOURCE_ARCHIVE_BACKEND=blob`.
     - Azure Blob source-row archiving uses `SOURCE_ROWS_STORAGE_ACCOUNT_URL` or `STORAGE_ACCOUNT_NAME`, `SOURCE_ROWS_CONTAINER_NAME`, and managed identity through `DefaultAzureCredential`.
     - Import responses include `importRunId`, `sourceRowsBlobUrl`, checksum, parser module, source row count, imported/created/updated/skipped/error counts, row errors, normalized records, and `refreshPolicy`.
     - Customer imports default to a daily refresh cadence. Item imports default to a weekly refresh cadence.
     - Refresh overrides are supported through `refreshIntervalDays`, `importProfile.refreshIntervalDays`, `customerConfig.customerRefreshIntervalDays`, and `customerConfig.itemRefreshIntervalDays`.
     - Optional import-time embeddings are available with `ORDER_PROCESSOR_ENABLE_IMPORT_EMBEDDINGS=true`, using the Azure OpenAI endpoint/deployment configuration. Customer and item embeddings remain disabled by default for local/offline work.
     - `/imports/customers` and `/imports/items` now write normalized records to Cosmos/repository containers and record `customers.imported` or `items.imported` audit events with import summary details.
     - The customer/item import endpoints now replace operational SharePoint local-copy behavior with Cosmos `customers`, `customerAliases`, and `items` containers. SharePoint may remain an upstream file/source adapter, but not the canonical operational store.
     - `infra/openapi/order-processor-api.yaml`, `docs/API_CONTRACTS.md`, `docs/COSMOS_MODEL.md`, and `apps/functions/local.settings.sample.json` were updated with the Phase 8 import/archive/embedding contract.
     - Tests were expanded in `tests/test_imports_output.py` for customer CSV parsing, JSON/JSONL parsing, source row archiving, customer alias generation, daily/weekly/default/override refresh policies, incremental updates, missing identifier errors, malformed JSON errors, and optional import embeddings.

9. **Order Processing Engine**
   - Define a universal order model: header, customer, PO/order identifiers, source email/file metadata, line items, validation results, output artifacts, and errors.
   - Implement processors for CSV, XLSX, XLS/XLT, PDF, email body, and customer-specific overrides.
   - First CSV migration target is `orderProcess - CSV Parse`; no scope should be assigned to removed/deprecated CSV variants.
   - Replace `orderProcess - CSV Parse` Plumsail usage with backend code. Preferred implementation: parse CSV directly in Azure Functions using a robust CSV parser and generate XLSX only when a downstream/customer output profile requires it.
   - If XLSX output is required, generate it with code in Azure Functions instead of Plumsail; do not introduce a new third-party document connector.
   - Replace Google Document AI with Azure Document Intelligence for PDF extraction.
   - Replace Plumsail/Excel conversion dependencies with Azure code where possible; keep Office Scripts only if a pilot customer proves they are required.
   - Completion notes:
     - Phase 9 was completed on 2026-06-20 and summarized in `docs/PHASE_9_ORDER_PROCESSING_ENGINE.md`.
     - `OrderRun` now acts as the universal order envelope with `header`, PO/order identifiers, source type, source file name, source metadata, processor profile/type/version, canonical lines, output artifacts, errors, and parse warnings.
     - `OrderLine` now carries source row index, provided item number, UPC, description, quantity, unit, unit price, matched internal item number, validation status, validation confidence, validation method, validation candidates, validation errors, and raw source row.
     - `src/order_processor/order_processing.py` now provides a processor registry for `csv`, `xlsx`, `xls`, `xlt`, `pdf`, `emailBody`, and `customerOverride`.
     - The active `orderProcess - CSV Parse` migration path is implemented in backend code with Python's standard CSV parser, delimiter sniffing, header/headerless support, configurable headerless columns, field maps, and BOM handling. Plumsail CSV parsing is not used.
     - `XlsxOrderProcessor` reads simple XLSX workbooks with standard-library ZIP/XML parsing. `LegacyWorkbookOrderProcessor` supports caller-supplied rows and HTML-backed XLS/XLT exports; binary BIFF XLS/XLT files are explicitly marked for backend conversion rather than sent to Plumsail.
     - `PdfOrderProcessor` accepts Azure Document Intelligence extracted table/text results through `documentIntelligenceResult`, `extractedText`, or `sourceRows`. Raw PDF bytes are rejected with `documentIntelligenceExtractionRequired` and a parser-failure exception task until extraction is configured.
     - `EmailBodyOrderProcessor` supports pipe-delimited body tables, simple line formats, and PO extraction. `CustomerOverrideOrderProcessor` delegates to configured base processors with profile-driven settings such as `linePattern` and `fieldMap`.
     - `/orders/{orderRunId}/process` now resolves processor profiles by explicit ID, explicit processor type, customer-scoped profile, tenant-global profile, or default CSV.
     - Parser failures now create `exceptionTasks` with `taskType: parserFailure`; unresolved or possible item matches continue to create `itemValidation` exception tasks.
     - `output_generation.py` now generates XLSX workbooks in backend code when an output profile or request asks for XLSX. This replaces the Plumsail CSV-to-XLSX dependency path.
     - `infra/openapi/order-processor-api.yaml`, `docs/API_CONTRACTS.md`, and `docs/COSMOS_MODEL.md` were updated with the Phase 9 processor, source payload, universal order, and output artifact contracts.
     - Tests were expanded in `tests/test_order_processing_engine.py` for active headerless CSV parity without Plumsail, XLSX parsing, HTML-backed XLS parsing, email-body parsing, customer override regex delegation, Azure Document Intelligence result parsing, raw-PDF extraction-required failure, API profile dispatch, and code-generated XLSX output.
     - Validation completed: `python -m unittest discover -s tests` passed with 67 tests.
     - Known follow-up: binary BIFF XLS/XLT conversion still needs an Azure backend conversion module if pilot samples require it; live PDF extraction still needs deployed Azure Document Intelligence endpoint configuration and real sample PDFs.

10. **Item Validation Service**
    - Rebuild `Module - Item Number Validator` as an Azure endpoint.
    - Inputs: customer ID, provided item number, UPC, description, and optional row context.
    - Output: matched internal item number, match method, confidence, candidate list, and validation status.
    - If no confident match, mark line unresolved and create a console task.
    - Completion notes:
      - Phase 10 was completed on 2026-06-20 and summarized in `docs/PHASE_10_ITEM_VALIDATION_SERVICE.md`.
      - `src/order_processor/item_validation.py` now implements a service-grade matcher with exact item number/internal number/customer number/alias matching, exact UPC matching, fuzzy item-number matching, fuzzy description matching, confidence thresholds, possible-match thresholds, candidate limits, and ambiguity detection.
      - `ItemValidationResult` now includes `matchedItemId`, matched internal item number, match method, confidence, candidate list, validation status, and unresolved reason.
      - `/items/validate` now accepts customer ID, provided item number, UPC, description, optional row context, optional order run id, optional line number, confidence threshold, possible-match threshold, candidate limit, and optional caller-supplied items for tests/controlled callers.
      - Row context is supported for raw parser/source rows. When direct fields are blank, the service can infer item number, UPC, or description from common keys such as `Vendor Item`, `Item Number`, `SupplierCode`, `UPC`, `Barcode`, `Description`, `Column 1`, and `Column 2`.
      - The endpoint now loads customer-scoped item records from Cosmos/repository by `tenantId` and `customerId` instead of scanning unrelated customer items.
      - When `orderRunId` and `lineNumber` are supplied, `/items/validate` updates the matching `orderRuns.lines[]` entry with validation status, confidence, method, candidates, matched internal item number, and validation errors.
      - If a line is unresolved or only a possible match, the endpoint marks the order run `needsReview` and creates an `exceptionTasks` record with `type: itemValidation`, compact request context, row context, and full validation result for console resolution.
      - If all lines are confidently matched and the order is not failed, the endpoint can move the order run to `completed`.
      - Every item validation call now records an `auditEvents` entry with `eventType: item.validated`, customer id, status, matched internal item number, match method, confidence, candidate count, and exception task id when applicable.
      - `docs/API_CONTRACTS.md`, `docs/COSMOS_MODEL.md`, and `infra/openapi/order-processor-api.yaml` were updated with the Phase 10 item-validation request/response and persistence contract.
      - Tests were expanded in `tests/test_item_validation.py` and `tests/test_item_validation_service.py` for exact matching, row-context fallback, possible matches, ambiguous matches, unresolved customer scopes, endpoint matching, persisted line updates, exception creation, candidate limits, and audit events.
      - Validation completed: `python -m unittest discover -s tests` passed with 74 tests.

11. **Output Generation**
    - Generate a universal order JSON document for every processed order.
    - Add per-customer output adapters that transform the universal order into customer-required CSV/XLSX/text/API formats and delivery destinations.
    - Store completed outputs in Blob Storage and reference them from `orderRuns`.
    - Completion notes:
      - Phase 11 was completed on 2026-06-20 and summarized in `docs/PHASE_11_OUTPUT_GENERATION.md`.
      - `src/order_processor/output_generation.py` now provides a profile-driven output adapter layer plus `OutputArtifactStore`, `InMemoryOutputArtifactStore`, `AzureBlobOutputArtifactStore`, and `output_artifact_store_from_environment()`.
      - `/orders/{orderRunId}/process` now generates output artifacts after final order status is known, stores artifact bytes through the configured output store, and writes artifact references to `orderRuns.outputArtifacts`.
      - A stored `universalOrderJson` artifact is generated for every processed order.
      - If no output profile is configured, the backend also generates the default `lineCsv` artifact for continuity with the earlier scaffold.
      - Supported output profile types are `csv`, `xlsx`, `text`, `api`, `json`, and `multi`.
      - Output profile resolution now supports inline request profiles, `outputProfileId`, `outputProfileIds`, `processorProfiles.outputProfileId`, request-time `outputTypes` or `requestedOutputTypes`, customer-scoped repository profiles, tenant-global profiles, and default line CSV fallback.
      - CSV output supports configurable fields, delimiter, header inclusion, encoding, destination metadata, and filename templates.
      - XLSX output is generated by backend code as an Open XML workbook and stored as an artifact; no Plumsail connector is used.
      - Text output supports customer templates such as `{line_number}|{matched_internal_item_number}|{quantity}`.
      - API output profiles generate stored `apiPayload` artifacts with method, URL, headers, and body plus `deliveryStatus: pendingExternalDelivery`; direct external API delivery is deferred to a later worker or optional customer-specific adapter.
      - Output artifact references now include id, type, file name, content type, Blob/memory URL, size, checksum, generated timestamp, output profile id/name, destination, and metadata. Inline output content is no longer stored in `orderRuns`.
      - Deployed artifact storage can use Blob Storage with `ORDER_PROCESSOR_OUTPUT_ARCHIVE_BACKEND=blob`, `ORDER_ARTIFACTS_STORAGE_ACCOUNT_URL` or `BLOB_SERVICE_ENDPOINT` or `STORAGE_ACCOUNT_NAME`, and `ORDER_ARTIFACTS_CONTAINER_NAME`.
      - Output generation failures now mark the order failed, record `outputGenerationFailed`, and create an `exceptionTasks` record with `type: outputGeneration`.
      - `order.processed` audit events now include output artifact count and output profile ids.
      - `docs/API_CONTRACTS.md`, `docs/COSMOS_MODEL.md`, `infra/openapi/order-processor-api.yaml`, and `apps/functions/local.settings.sample.json` were updated with the Phase 11 output profile and artifact-storage contract.
      - Tests were added in `tests/test_output_generation_service.py` for default universal JSON/line CSV artifacts, memory artifact storage, customer CSV/text/API profiles, destination metadata, request-time XLSX output, and valid stored Open XML workbook payloads.
      - Validation completed: `python -m unittest discover -s tests` passed with 77 tests.

12. **Console Backend and Frontend**
    - Build Azure Web App console with Entra ID auth.
    - Initial console access is restricted to `connect@focuseautomate.com` as the full backend console administrator.
    - Add customer user management: an admin can enter a Microsoft email address, assign that user to one or more customers/roles, and the user can sign in with Microsoft authentication.
    - Features: active run monitor, processed order history, download outputs, routing rule editor, customer/item data status, customer config, customer mailbox config, Microsoft auth/connection status, customer user management, exception queue, and reprocess controls.
    - Human resolution flows must support customer match correction, item match correction, parser failure triage, and manual re-run.
    - Completion notes:
      - Phase 12 was completed on 2026-06-21 and summarized in `docs/PHASE_12_CONSOLE_BACKEND_FRONTEND.md`.
      - `src/order_processor/api.py` now implements `console_session`, `console_dashboard`, console artifact access, guarded console configuration upserts, guarded customer-user assignment, guarded exception resolution, and guarded order reprocess controls.
      - `connect@focuseautomate.com` is bootstrapped as the only initial `platformAdmin`; unassigned Microsoft users receive `consoleUserNotAssigned`.
      - Console identity now prefers App Service Easy Auth headers such as `x-ms-client-principal` over request-body form fields, preventing user-management form fields from being mistaken for the signed-in principal.
      - Console roles map to permissions: `platformAdmin` has full access, `orderViewer` can view/download assigned-customer data, `exceptionResolver` can resolve assigned-customer tasks, and `orderManager` can resolve, download, and reprocess assigned-customer orders.
      - The dashboard composes scoped views from `orderRuns`, `exceptionTasks`, `mailboxAccounts`, `routingRules`, `customers`, `items`, `processorProfiles`, `outputProfiles`, `microsoftAuthConnections`, and `orderRuns.outputArtifacts`.
      - Human resolution now applies customer match corrections to related email/order records, applies manual item matches to `orderRuns.lines[]`, supports parser/output triage notes, and can trigger manual reprocess requests.
      - Function routes now expose console-prefixed guarded endpoints for session, dashboard, artifact access, mailbox/customer/routing/profile/user edits, customer-user assignment, exception resolution, and order reprocessing.
      - The static console app was added under `apps/console` with monitor, exceptions, customers, routing, outputs, and users views.
      - `apps/console/server.js` provides a dependency-free Node Web App host that serves static files and proxies `/api/*` to APIM while forwarding Easy Auth identity headers.
      - `infra/main.bicep` now provisions a Linux console Web App plan/app, optional App Service Easy Auth when `consoleEntraClientId` is supplied, a console APIM subscription, console app settings, and console Web App outputs.
      - `infra/subscription.bicep` now passes console SKU, Easy Auth client id, and bootstrap admin parameters and returns the console URL.
      - `docs/API_CONTRACTS.md`, `docs/COSMOS_MODEL.md`, `infra/openapi/order-processor-api.yaml`, `apps/functions/README.md`, `apps/console/README.md`, and `README.md` were updated for the Phase 12 console contract.
      - Tests were added or expanded in `tests/test_console_backend.py`, `tests/test_console_frontend.py`, and `tests/test_infra_contract.py` for bootstrap admin access, denied unassigned access, Easy Auth principal precedence, customer-scoped dashboard filtering, guarded mutations, artifact access, exception resolution, reprocess controls, frontend route safety, and console IaC/API contract coverage.
      - Live Entra app registration, deployed Easy Auth validation, and live Graph mailbox connection testing were not run in this phase. `/mailboxes/{id}/test-connection` remains a local `notTested` scaffold until Microsoft Graph wiring is implemented.

13. **Observability and Audit**
    - Use Application Insights correlation IDs across mailbox trigger, Azure APIs, Durable orchestrations, Cosmos records, and Power Automate calls.
    - Every order run records timeline events, decisions, extracted values, match confidence, output artifacts, and user interventions.
    - Add dashboard views for success rate, unresolved item count, customer-ID failures, processor failures, and processing latency.
    - Completion notes:
      - Phase 13 was completed on 2026-06-21 and summarized in `docs/PHASE_13_OBSERVABILITY_AUDIT.md`.
      - `src/order_processor/observability.py` now centralizes correlation context extraction, W3C traceparent parsing, latency calculations, dashboard observability metrics, and order timeline construction.
      - All Function routes now preserve request headers before calling the backend, so APIM request ids, Power Automate flow-run ids, W3C trace headers, Durable instance ids, and Easy Auth principals can be captured.
      - `apps/functions/function_app.py` configures Azure Monitor OpenTelemetry when `APPLICATIONINSIGHTS_CONNECTION_STRING` is present in the deployed Function app environment.
      - `emailMessages` and `orderRuns` now persist `correlationId`; `orderRuns` also persists `processingStartedAt`, `processingCompletedAt`, and `sourceMetadata.observability`.
      - `auditEvents` now carry top-level `customerId`, `orderRunId`, `emailMessageId`, `operationId`, and `traceId` fields for easier Cosmos querying and Application Insights correlation.
      - Ingestion, customer identification, item validation, order processing, exception creation, exception resolution, reprocess requests, output artifact access, imports, console sessions, and configuration changes now write richer audit details.
      - Order processing now records `order.processingStarted` and enhanced `order.processed` audit events with status, processor/source type, line counts, unresolved line counts, output artifacts, errors, parse warnings, and processing latency.
      - Customer identification audit details now include extracted signals, match method, confidence, candidates/result details, threshold, email id, order id when known, and exception task id when created.
      - Item validation audit details now include order id, line number, status, matched item id/internal item number, match method, confidence, candidates, and exception task id when created.
      - Exception resolution, output artifact access, and reprocess requests now mark `userIntervention: true` and record the Microsoft console actor when available.
      - New timeline routes were added: `POST /orders/{orderRunId}/timeline` and customer-scoped `POST /console/orders/{orderRunId}/timeline`.
      - `/console/dashboard` now returns `observabilityMetrics` and `recentAuditEvents`; summary cards include customer-ID failure count, processor failure count, output-generation failure count, average processing latency, and p95 processing latency.
      - The console UI now displays customer-ID failures, processor failures, average latency, and timeline buttons for active and processed orders.
      - `docs/API_CONTRACTS.md`, `docs/COSMOS_MODEL.md`, `infra/openapi/order-processor-api.yaml`, `apps/functions/README.md`, `apps/console/README.md`, and `README.md` were updated for the Phase 13 observability contract.
      - Tests were added or expanded in `tests/test_observability.py`, `tests/test_console_frontend.py`, and `tests/test_infra_contract.py` for header preservation, correlation propagation, audit fields, timeline content, dashboard metrics, frontend timeline references, and OpenAPI path coverage.
      - No live Azure deployment or Application Insights query validation was run in this phase. Durable orchestration correlation is represented by `durableInstanceId` propagation until the later Durable Functions orchestration implementation is built.

14. **Pilot Migration**
    - Select one pilot customer and migrate end-to-end.
    - Run shadow mode: current flows and new platform process the same sample/order stream without changing production behavior.
    - Compare customer identification, item validation, output files, routing tags, CSR folder moves, and error handling.
    - Go live only after parity plus improved exception handling is demonstrated.
    - Completion notes:
      - Phase 14 was completed on 2026-06-21 and summarized in `docs/PHASE_14_PILOT_MIGRATION.md`.
      - The selected pilot path is the active `orderProcess CSV Parse` reference flow. The deprecated CSV flows remain excluded from the pilot and plan.
      - `samples/pilot/pilot-shadow-manifest.json` now defines the pilot customer, customer-specific monitored Microsoft mailbox, routing rule, CSV processor profile, shadow output profile, item records, source CSV attachment, expected output, and acceptance gates.
      - `src/order_processor/pilot_shadow.py` now provides an executable local shadow-run harness that seeds backend containers, ingests the pilot email, processes the CSV order through the backend service, stores artifacts in the memory artifact store, and compares the Phase 14 parity gates.
      - The pilot checks customer identification through mailbox/routing config, item validation, line CSV output parity, routing tags, CSR folder config, exception count, output artifact types, shadow-only delivery, empty order errors, and `usesPlumsail: false` on the active CSV processor profile.
      - `tests/test_pilot_shadow.py` verifies that the pilot shadow run passes and that output mismatches fail the comparator.
      - The CSV Parse pilot replaces the Plumsail CSV-to-XLSX dependency with backend CSV parsing and code-generated output artifacts. XLSX generation remains available only when an output profile requests XLSX.
      - This phase does not cut over production traffic. The fixture is synthetic; final go-live still requires approved real pilot emails/files, live Microsoft Graph mailbox validation, operations review of shadow outputs, and explicit sign-off that parity plus improved exception handling has been demonstrated.

15. **Repeatable Onboarding**
    - Package onboarding as config plus templates: customer profile, monitored mailbox, Microsoft auth/connection setup, console user assignments, routing rules, input parsers, output adapter, customer list source, item list source, CSR routing, and test fixtures.
    - Add an onboarding checklist and automated validation suite.
    - Use the pilot customer as the first reference implementation, then migrate the remaining customers in batches.
    - Completion notes:
      - Phase 15 was completed on 2026-06-21 and summarized in `docs/PHASE_15_REPEATABLE_ONBOARDING.md`.
      - `onboarding/templates/customer-onboarding-template.json` now provides a copy-start customer package template covering customer profile, monitored mailbox, Microsoft auth connection, console users, customer assignments, routing rules, processor profile, output profile, import sources, CSR routing, fixtures, batches, and cutover gates.
      - `onboarding/reference/pilot-csv-parse/onboarding-package.json` uses the Phase 14 pilot as the first reference onboarding package.
      - `src/order_processor/onboarding.py` now validates onboarding packages and can run executable fixtures, including the pilot shadow-run manifest.
      - `tools/Validate-OnboardingPackage.ps1` wraps the validator for local CLI use.
      - `docs/ONBOARDING_CHECKLIST.md` records the operational checklist for customer profile setup, Microsoft access, console access, routing/processing, data refresh, fixtures, validation, batch migration, and cutover.
      - The validator checks package metadata, customer profile completeness, Microsoft mail scopes with no inline secrets, mailbox/auth linkage, bootstrap admin and customer assignments, routing/profile references, Plumsail-free processor profiles, shadow-safe output profiles, customer/item import sources, CSR routing, runnable fixtures, and pilot-first batch migration strategy.
      - `tests/test_onboarding.py` verifies the pilot reference package, fixture execution, template section coverage, and failure detection for missing item imports and inline secret values.
      - The current phase ledger is complete. Remaining work is now an execution track: add approved real customer samples, validate live Microsoft Graph mailbox access, deploy to Azure, and run customer batches through the onboarding package process.

## Public Interfaces and Contracts

- Azure API surface:
  - `POST /emails/ingest`
  - `POST /orders/{orderRunId}/process`
  - `POST /orders/{orderRunId}/timeline`
  - `POST /customers/identify`
  - `POST /items/validate`
  - `POST /imports/customers`
  - `POST /imports/items`
  - `POST /mailboxes`
  - `POST /mailboxes/{id}/test-connection`
  - `POST /console/session`
  - `POST /console/dashboard`
  - `POST /console/artifacts/download`
  - `POST /console/mailboxes`
  - `POST /console/routing-rules`
  - `POST /console/customers`
  - `POST /console/processor-profiles`
  - `POST /console/output-profiles`
  - `POST /console/users`
  - `POST /console/customers/{customerId}/users`
  - `POST /console/exceptions/{id}/resolve`
  - `POST /console/orders/{orderRunId}/reprocess`
  - `POST /console/orders/{orderRunId}/timeline`
  - `POST /customers/{customerId}/users`
  - `POST /exceptions/{id}/resolve`
  - `POST /orders/{orderRunId}/reprocess`
- Canonical models:
  - `EmailMessage`, `OrderRun`, `OrderLine`, `CustomerProfile`, `ItemRecord`, `RoutingRule`, `ProcessorProfile`, `OutputProfile`, `MailboxAccount`, `MicrosoftAuthConnection`, `ConsoleUser`, `CustomerUserAssignment`, `ExceptionTask`, `AuditEvent`.
- Power Automate remains responsible only for mailbox/event triggers and optional customer-specific M365 delivery adapters.

## Test Plan

- Unit tests for routing rule evaluation, customer identification, item normalization, item matching, parser modules, and output adapters.
- Golden-file tests for CSV, XLSX, XLS/XLT, PDF, email body, and edge-case customer processors.
- Import tests for customer and item list refreshes, including duplicate rows, missing fields, malformed files, and incremental updates.
- End-to-end tests from sample email to completed output file and CSR routing decision.
- Console tests for exception resolution, reprocessing, audit history, and download links.
- Console auth tests for `connect@focuseautomate.com` bootstrap admin access, Microsoft login, customer-scoped authorization, and denied access for unassigned users.
- Mailbox config tests for customer-specific mailbox routing, Microsoft connection status, and ingest scoping.
- CSV migration test for `orderProcess - CSV Parse` parity without Plumsail.
- Shadow-run acceptance: new platform output must match or improve current flow output for pilot samples before production cutover.

## Assumptions and Defaults

- Azure core, Cosmos vector retrieval, and one pilot customer first.
- `connect@focuseautomate.com` is the initial backend console administrator.
- Customer users authenticate with Microsoft accounts and receive customer access through console-managed assignments.
- Customer-specific mailbox configuration is first-class platform data and must be visible in the console.
- The only active CSV reference flow is `orderProcess - CSV Parse`.
- Plumsail CSV parsing/conversion should be replaced with Azure Function code and standard libraries; XLSX generation should happen only when required by an output profile.
- Use Cosmos DB as canonical operational storage and vector store.
- Use Durable Functions for long-running order orchestration and async status tracking in the next backend iteration.
- Use API Management plus managed identity/RBAC as the secure facade for backend APIs.
- References used:
  - https://learn.microsoft.com/en-us/azure/cosmos-db/vector-search
  - https://learn.microsoft.com/en-us/azure/durable-task/durable-functions/durable-functions-http-features
  - https://learn.microsoft.com/en-us/azure/azure-functions/security-concepts
  - https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-use-managed-service-identity

## Implementation Status

- Phase 1 completed on 2026-06-20: planning artifact, baseline reference links, regenerated flow library, regenerated external dependency inventory, regenerated static SharePoint reference, and live Graph export attempt are complete.
- Phase 2 completed on 2026-06-20: every active reference flow was mapped to a migration capability, all active order-processing flows were documented, Plumsail/Google/OpenAI/Office/SharePoint/mailbox dependencies were captured, and synthetic representative fixtures were created under `samples/phase-2`.
- Phase 3 completed on 2026-06-20: deployable Azure foundation IaC, subscription/resource-group deployment paths, APIM OpenAPI facade, Key Vault secret path, managed identities, RBAC assignments, Durable Functions readiness, storage hardening, and deployment runbook are complete.
- Phase 4 completed on 2026-06-20: canonical Cosmos data model, all required container definitions, customer-scoped partition strategy, vector metadata, mailbox/auth/console dataclasses, repository normalization, Cosmos repository selector, and data-model tests are complete.
- Phase 5 completed on 2026-06-20: target `OrderProcessor` Power Platform shell solution verified in Focus Automate, four disabled adapter-flow templates generated, unmanaged package built and imported, Office 365 Outlook connection reference added, APIM-only shell boundaries documented, and shell guardrail tests added.
- Phase 6 completed on 2026-06-20: mailbox-aware `/emails/ingest`, persisted email/attachment/routing data, customer-scoped routing rule evaluation, disabled/unknown/conflicting mailbox handling, routing exception creation, order-run creation for known orders, audit events, API/OpenAPI/Cosmos docs, and Phase 6 tests are complete.
- Phase 7 completed on 2026-06-20: deterministic customer identification, customer alias support, known subject patterns, confidence thresholding, ambiguity handling, Azure OpenAI embedding client boundary, Cosmos customer vector-search adapter, low-confidence exception creation, stored email/order customer updates, audit events, API/OpenAPI/Cosmos docs, and Phase 7 tests are complete.
- Phase 8 completed on 2026-06-20: parser registry for customer/item imports, refresh cadence policy, source-row archive abstraction with Blob-ready implementation, canonical Cosmos customer/item writes, customer alias generation, import metadata, optional import embeddings, import audit events, API/OpenAPI/Cosmos/local settings docs, and Phase 8 tests are complete.
- Phase 9 completed on 2026-06-20: universal order model, processor registry, active CSV Parse replacement without Plumsail, standard-library XLSX parsing, HTML-backed XLS/XLT parsing, Azure Document Intelligence PDF result boundary, email-body parsing, customer override delegation, parser-failure exceptions, profile-dispatched `/orders/{orderRunId}/process`, code-generated XLSX output, API/OpenAPI/Cosmos docs, Phase 9 handoff doc, and Phase 9 tests are complete.
- Phase 10 completed on 2026-06-20: service-grade `/items/validate`, row-context-aware item matching, exact/fuzzy/ambiguous candidate scoring, matched item id in results, customer-scoped item loading, persisted order-line validation updates, item-validation exception tasks, item validation audit events, API/OpenAPI/Cosmos docs, Phase 10 handoff doc, and Phase 10 tests are complete.
- Phase 11 completed on 2026-06-20: universal output artifact generation, profile-driven CSV/XLSX/text/API/json/multi output adapters, Blob-ready output artifact storage, memory artifact storage for local tests, artifact references in `orderRuns`, default line CSV fallback, output profile resolution, output-generation exception handling, output audit details, API/OpenAPI/Cosmos/local settings docs, Phase 11 handoff doc, and Phase 11 tests are complete.
- Phase 12 completed on 2026-06-21: Azure Web App console scaffold, Node static/proxy host, optional Entra Easy Auth IaC, bootstrap admin access for `connect@focuseautomate.com`, Microsoft user/customer assignment model, guarded console API routes, customer-scoped dashboard, mailbox/customer/routing/profile/user editors, output artifact access, exception resolution, reprocess controls, API/OpenAPI/Cosmos/README docs, Phase 12 handoff doc, and Phase 12 tests are complete.
- Phase 13 completed on 2026-06-21: correlation context extraction, Function header preservation, optional Azure Monitor OpenTelemetry setup, first-class order/email correlation ids, order processing timestamps, enriched auditEvents with operation/trace/customer/order/email fields, order processing/customer identification/item validation/user-intervention audit details, order timeline routes, console observability metrics, recent audit events, console timeline buttons, API/OpenAPI/Cosmos/README docs, Phase 13 handoff doc, and Phase 13 tests are complete.
- Phase 14 completed on 2026-06-21: active `orderProcess CSV Parse` pilot selected, synthetic customer-specific mailbox/customer/item/routing/processor/output fixture package created, executable local shadow-run comparator added, Plumsail-free CSV Parse replacement asserted, customer identification/item validation/output/routing tag/CSR folder/error handling parity gates checked, Phase 14 handoff doc added, README updated, and Phase 14 tests are complete.
- Phase 15 completed on 2026-06-21: repeatable onboarding package template added, pilot CSV Parse reference package added, onboarding CLI validator implemented, PowerShell validation wrapper added, customer onboarding checklist and Phase 15 handoff doc added, pilot-first batch migration strategy documented, package validation covers Microsoft auth/mailbox/user/routing/parser/output/import/CSR/fixture/cutover concerns, and Phase 15 tests are complete.
- Completed beyond the current phase ledger: local backend package scaffold, Azure Functions HTTP facade, Power Automate shell design docs, and unit tests for first-pass services.
- Next uncompleted phase: none in the current 15-phase ledger. Next execution track: approved real pilot samples, live Microsoft Graph mailbox validation, Azure deployment, and customer batch onboarding.
- External blocker: live SharePoint list schema/items export still requires valid Graph authentication for the Frontier tenant; current Azure CLI token fails Conditional Access with `AADSTS530036`.
