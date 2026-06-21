# Phase 13 Observability and Audit

Completed: 2026-06-21

## Scope

Phase 13 adds end-to-end correlation, richer audit events, order timelines, and operational metrics for the console. The implementation keeps `auditEvents` as the durable timeline store and derives dashboard views from existing operational records.

## Correlation Context

All Function routes now preserve request headers before delegating to the backend. The backend extracts observability context from request bodies, source/sourceMetadata, and headers.

Supported context inputs:

- `correlationId`
- `operationId`
- `traceparent`
- `flowRunId`
- `flowName`
- `durableInstanceId`
- `x-correlation-id`
- `x-ms-correlation-id`
- `x-ms-client-request-id`
- `x-ms-request-id`
- `x-ms-workflow-run-id`
- `x-ms-workflow-name`
- `x-order-processor-ingress`

Stored correlation fields:

- `emailMessages.correlationId`
- `orderRuns.correlationId`
- `orderRuns.sourceMetadata.observability`
- `auditEvents.correlationId`
- `auditEvents.operationId`
- `auditEvents.traceId`

`apps/functions/function_app.py` also configures Azure Monitor OpenTelemetry when `APPLICATIONINSIGHTS_CONNECTION_STRING` is available in the deployed Function app environment.

## Audit Events

`auditEvents` now include top-level query fields:

- `customerId`
- `orderRunId`
- `emailMessageId`
- `operationId`
- `traceId`
- `actor`

Audit coverage now includes:

- email ingestion and routing decisions
- processing start
- processing completion
- customer identification results, extracted signals, confidence, and candidates
- item validation status, confidence, candidates, and line context
- exception creation
- exception resolution
- output artifact generation through order processing details
- output artifact access
- reprocess requests
- imports
- console sessions
- configuration changes

User-driven events such as exception resolution, output artifact access, and reprocess requests include `userIntervention: true` in audit details and use the Microsoft console principal as the actor when available.

## Order Timeline

New API routes:

- `POST /orders/{orderRunId}/timeline`
- `POST /console/orders/{orderRunId}/timeline`

The console route validates customer assignment before returning the timeline.

Timeline output is composed from:

- the `orderRuns` record
- the source `emailMessages` record
- related `exceptionTasks`
- matching `auditEvents`
- `orderRuns.outputArtifacts`

The response includes ordered events and `processingLatencyMs`.

## Dashboard Metrics

`/console/dashboard` now returns `observabilityMetrics` and `recentAuditEvents`. The existing `summary` object also exposes the most important metrics for the console cards.

Metrics include:

- success rate
- unresolved item count
- customer identification failure count
- processor failure count
- output-generation failure count
- audit event count
- processing latency average, p50, p95, and max milliseconds

The console UI now shows customer-ID failure count, processor failure count, average processing latency, and timeline buttons for active and processed runs.

## Files Updated

- `src/order_processor/models.py`
- `src/order_processor/observability.py`
- `src/order_processor/api.py`
- `apps/functions/function_app.py`
- `apps/console/app.js`
- `apps/console/index.html`
- `apps/console/styles.css`
- `infra/openapi/order-processor-api.yaml`
- `docs/API_CONTRACTS.md`
- `docs/COSMOS_MODEL.md`
- `tests/test_observability.py`
- `tests/test_console_frontend.py`
- `tests/test_infra_contract.py`

## Validation

Validation completed during this phase:

- `python -m unittest tests.test_observability tests.test_console_backend tests.test_console_frontend tests.test_infra_contract`
- `python -m compileall -q src apps`
- `node --check .\apps\console\app.js`
- `node --check .\apps\console\server.js`
- `python -m unittest discover -s tests`
- `az bicep build --file .\infra\main.bicep`
- `az bicep build --file .\infra\subscription.bicep`

## Known Follow-Ups

- No live Azure deployment or Application Insights query validation was run from this workspace.
- Durable Functions orchestration is still represented by correlation fields and `durableInstanceId` propagation; the actual Durable orchestration implementation remains a later backend iteration.
- Phase 14 pilot work should confirm real Power Automate headers and flow-run ids from deployed mailbox adapters and adjust header mappings if Microsoft emits additional useful values.
