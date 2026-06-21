# Azure Foundation

This folder contains the Phase 3 Bicep foundation for the Order Processor platform.

Provisioned resource types:

- Resource group entry point at subscription scope.
- Azure Functions on Linux with Durable Functions enabled and Application Insights wired in.
- Cosmos DB for NoSQL with the operational containers required by the roadmap.
- Vector-enabled `customers` and `items` containers for embedding-backed matching.
- Hierarchical Cosmos partition keys for stable customer-scoped containers such as items, aliases, routing/profile config, mailbox config, Microsoft auth metadata, customer user assignments, and order lines.
- Blob Storage containers for email attachments, source-row archives, imports, order artifacts, and dead-letter payloads.
- Key Vault with RBAC enabled.
- API Management as the public facade for backend APIs, imported from `openapi/order-processor-api.yaml`.
- Azure AI Services and Azure Document Intelligence accounts with local key auth disabled.
- Optional Azure OpenAI deployment with local key auth disabled when `deployAzureOpenAI` is enabled.

The template is infrastructure-only. Power Automate remains a shell for mailbox/event triggers and optional M365 delivery adapters.

Expected deployment context:

- Azure owner/account: `evanb@altitudelogistics.com`
- Power Platform environment: `abbd708f-4eaf-e875-a282-e1207f4e370c`

## Deployment Options

Subscription-scope deployment creates the resource group and foundation resources:

```powershell
az deployment sub create `
  --location eastus `
  --template-file .\infra\subscription.bicep `
  --parameters .\infra\parameters\dev.subscription.bicepparam
```

Resource-group deployment is available when the resource group already exists:

```powershell
az deployment group create `
  --resource-group rg-orderprocessor-dev `
  --template-file .\infra\main.bicep `
  --parameters .\infra\parameters\dev.bicepparam
```

Local validation:

```powershell
az bicep build --file .\infra\main.bicep
az bicep build --file .\infra\subscription.bicep
```

Production parameters currently set `deployAzureOpenAI = false` because the target subscription rejected the OpenAI `S0` account with a quota/feature gate during deployment. Enable the Azure OpenAI feature/quota in the Azure Portal, then flip that parameter to `true` to deploy the account and populate `AZURE_OPENAI_ENDPOINT`.

Production parameters also set `consoleLocation = 'centralus'` while the core foundation remains in `westus2`. This avoids the App Service capacity conflict Azure returned for the console plan in `westus2` while keeping Cosmos, Functions, Storage, APIM, Key Vault, and AI services together.

Production parameters set `functionPackageUrl` to the private Blob package uploaded for the current commit. Linux Consumption Functions require `WEBSITE_RUN_FROM_PACKAGE` to point at a URL; the Function app uses its system-assigned managed identity to fetch that private package.

## Security Model

- The Function app uses a system-assigned managed identity.
- Function storage uses identity-based `AzureWebJobsStorage__*` settings; storage account keys are not placed in app settings.
- Cosmos DB local auth is disabled. The Function identity receives Cosmos DB built-in data contributor access through a Cosmos SQL role assignment.
- Storage shared key access is disabled. The Function identity receives Blob, Queue, and Table data contributor roles for host storage, Durable Functions state, attachments, imports, and artifacts.
- Key Vault uses RBAC. Function and API Management identities receive `Key Vault Secrets User`.
- Azure OpenAI, Azure AI Services, and Azure Document Intelligence local key auth is disabled when deployed. The Function identity receives RBAC access for AI calls.
- API Management imports the public API contract and injects the Function host key from Key Vault. Power Automate should call APIM, not raw Function URLs.

## API Management

External callers use:

```text
https://{apim-name}.azure-api.net/order-processor
```

The imported API includes every public contract endpoint currently listed in `docs/API_CONTRACTS.md`. Subscription keys belong at the APIM layer. Function host keys stay behind APIM and are stored in Key Vault.
