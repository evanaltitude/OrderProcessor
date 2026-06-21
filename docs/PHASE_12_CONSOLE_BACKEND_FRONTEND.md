# Phase 12 Console Backend and Frontend

Completed: 2026-06-21

## Scope

Phase 12 implements the first usable backend/admin console surface for the new Order Processor platform. The console is designed as an Azure Web App protected by Microsoft Entra ID through App Service Easy Auth, with APIM as the only backend API ingress.

Initial admin access is bootstrapped to `connect@focuseautomate.com`. All other users must be added by a platform admin and assigned to one or more customers.

## Backend Implementation

`src/order_processor/api.py` now includes console session, dashboard, authorization, artifact, configuration, exception, and reprocess methods.

Implemented console endpoints in `apps/functions/function_app.py`:

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

The lower-level service endpoints remain available for backend/service setup, but browser console mutations use the console-prefixed routes so Easy Auth headers are evaluated.

## Authorization Rules

The console prefers Easy Auth identity headers over form payload fields. This prevents a user-management form field such as `email` from being mistaken for the signed-in principal.

Roles and permissions:

- `platformAdmin`: full access. The bootstrap platform admin is `connect@focuseautomate.com`.
- `orderViewer`: assigned-customer dashboard and output download access.
- `exceptionResolver`: assigned-customer exception resolution.
- `orderManager`: assigned-customer exception resolution, output downloads, and reprocess controls.

Admin-only operations:

- User management.
- Customer configuration.
- Mailbox configuration.
- Routing rule edits.
- Processor/output profile edits.

Customer-scoped operations:

- Dashboard reads are filtered to assigned customers.
- Output artifact access is filtered to assigned customers.
- Exception resolution and reprocess requests verify the related order/customer assignment.

## Console UI

The first console UI is in `apps/console`.

Files:

- `index.html`: operational console layout with tabs for monitor, exceptions, customers, routing, outputs, and users.
- `styles.css`: compact work-focused UI styling.
- `app.js`: browser controller that calls the console API routes with `credentials: "include"`.
- `server.js`: dependency-free Node host for Azure Web App. It serves static files and proxies `/api/*` calls to APIM while forwarding Easy Auth identity headers.
- `package.json`: Web App start command.

Supported UI functions:

- Active run monitor.
- Processed order history.
- Output artifact open/download metadata.
- Exception queue with customer, item, parser/output triage resolution paths.
- Reprocess controls.
- Customer config editing.
- Customer-specific mailbox config editing.
- Routing rule editing.
- Processor/output profile editing.
- Customer/item data status.
- Console user creation and customer assignment.

The frontend supports `?tenantId=...` and optional `?apiBase=...`. In deployed Web App mode, the default `/api` path is proxied by `server.js`.

## Azure Infrastructure

`infra/main.bicep` now provisions:

- Linux Azure Web App plan for the console.
- Linux Node Web App for `apps/console`.
- Optional App Service Easy Auth configuration when `consoleEntraClientId` is supplied.
- APIM subscription for the console Web App proxy.
- Console app settings for APIM base URL, APIM subscription key, Application Insights, and bootstrap admin email.
- Outputs for `consoleWebAppName`, `consoleWebAppDefaultHostName`, and `consoleWebAppUrl`.

`infra/subscription.bicep` passes through console SKU, Easy Auth client id, and bootstrap admin parameters, and outputs the console Web App URL.

Deployment note: create/register the Entra app for the console before enabling Easy Auth, then pass its client id as `consoleEntraClientId`.

## Contracts and Tests

Updated:

- `docs/API_CONTRACTS.md`
- `docs/COSMOS_MODEL.md`
- `infra/openapi/order-processor-api.yaml`
- `tests/test_console_backend.py`
- `tests/test_console_frontend.py`
- `tests/test_infra_contract.py`

Validated during completion:

- `python -m unittest tests.test_console_backend tests.test_console_frontend`
- `python -m compileall -q src apps`
- `node --check .\apps\console\server.js`
- `node --check .\apps\console\app.js`
- `az bicep build --file .\infra\main.bicep`
- `az bicep build --file .\infra\subscription.bicep`

## Known Follow-Ups

- Live Entra app registration and Easy Auth deployment have not been run from this workspace.
- Live Microsoft Graph mailbox validation remains a later wiring task; `/mailboxes/{id}/test-connection` still returns `notTested` locally.
- Console UI is intentionally first-pass and operational. It should be expanded during pilot work with richer tables, audit timeline views, and better resolution forms once real pilot data is available.
- Phase 13 should add deeper Application Insights correlation, dashboard telemetry, and audit timeline views.
