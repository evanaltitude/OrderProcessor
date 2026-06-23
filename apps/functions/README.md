# Order Processor Functions

Thin HTTP facade for the Azure backend endpoints listed in `docs/API_CONTRACTS.md`.

The Functions app delegates behavior to `src/order_processor`, so local unit tests can cover the same routing, customer identification, import, item validation, and output contracts before cloud wiring is added.

Cloud runtime notes:

- The app is provisioned as a Linux Python 3.11 Azure Functions app with Durable Functions enabled.
- Production storage uses identity-based `AzureWebJobsStorage__*` settings from `infra/main.bicep`; do not add storage account keys to app settings.
- Production data storage uses `ORDER_PROCESSOR_STORAGE_BACKEND=cosmos`; local development uses `memory`.
- The public ingress path is API Management. Function routes use an app-level shared key in `ORDER_PROCESSOR_FUNCTION_SHARED_KEY`; APIM injects it through `x-order-processor-function-key` from Key Vault.
- Do not send Azure's special `x-functions-key` header from APIM. The production Function host returned internal errors when the Azure host-key API was used, so backend gating is handled by the app-level shared key instead.
- Service calls should use managed identity/RBAC for Cosmos DB, Blob Storage, Key Vault, Azure OpenAI, Azure AI Services, and Azure Document Intelligence.
- Console routes under `/console/*` expect Microsoft identity headers forwarded from the console Web App's App Service Easy Auth layer. Use these routes for browser console calls instead of lower-level setup helpers such as `/mailboxes` or `/customers/{customerId}/users`.
- Microsoft Graph mailbox notifications post directly to `/graph/notifications` so Microsoft can perform its validation challenge without APIM subscription headers. The route only acknowledges and queues notifications; queued processing validates `clientState` against the stored mailbox subscription before fetching a message.
- `/mailboxes/subscriptions/sync` creates or renews Microsoft Graph webhook subscriptions and remains protected by `ORDER_PROCESSOR_FUNCTION_SHARED_KEY`.
- All routes preserve request headers before calling the backend so `traceparent`, APIM request ids, Power Automate flow-run ids, and Easy Auth principals can be written into Cosmos audit records.
- Azure Monitor OpenTelemetry is configured automatically when `APPLICATIONINSIGHTS_CONNECTION_STRING` is present in the Function app environment.

Local runtime notes:

- Azure Functions Core Tools is required to run the app locally with `func start`.
- Copy `local.settings.sample.json` to `local.settings.json` for local settings.
- The default local settings use the in-memory repository so tests and local experimentation do not require deployed Azure resources.
- Use `tools/Build-FunctionAppPackage.ps1` to build a Linux-friendly deployment zip with forward-slash paths, bundled Linux Python wheels, and generated HTTP `function.json` metadata.
