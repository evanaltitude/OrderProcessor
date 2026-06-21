# Phase 3 Azure Foundation

Phase 3 converts the roadmap architecture into deployable Azure infrastructure-as-code. The foundation is ready for deployment, but no live Azure deployment was run during this pass to avoid creating billable resources without an explicit go-live instruction.

## Artifacts

- `infra/subscription.bicep`: subscription-scope entry point that creates the resource group and calls the foundation module.
- `infra/main.bicep`: resource-group-scope foundation for Functions, Durable storage, Cosmos DB, Blob Storage, Key Vault, Application Insights, API Management, Azure AI Services, Azure OpenAI, and Azure Document Intelligence.
- `infra/openapi/order-processor-api.yaml`: APIM-imported API contract for the public backend surface.
- `infra/parameters/dev.bicepparam`: resource-group-scope dev parameters.
- `infra/parameters/dev.subscription.bicepparam`: subscription-scope dev parameters.
- `apps/functions/host.json`: Durable Functions hub configuration.
- `apps/functions/requirements.txt`: Azure Functions, Durable Functions, identity, storage, Cosmos, Key Vault, OpenAI, and Document Intelligence client dependencies.
- `apps/functions/function_app.py` and `src/order_processor/api.py`: callable foundation routes for the full public APIM contract, including mailbox and console-user configuration scaffolds.

## Resource Coverage

| Requirement | Implementation |
| --- | --- |
| Resource group | `infra/subscription.bicep` provisions `rg-orderprocessor-dev` by default. |
| Azure Functions with Durable Functions | Linux Python 3.11 Function app, Durable hub app setting, Durable extension dependency, identity-based host storage. |
| Cosmos DB for NoSQL | Serverless account with local auth disabled, `orderProcessor` database, operational containers, and vector-enabled `customers`/`items`. |
| Blob Storage | Hardened StorageV2 account plus `email-attachments`, `order-artifacts`, `source-rows`, `imports`, and `dead-letter` containers. |
| Key Vault | RBAC-enabled vault for secrets, including the APIM-to-Function host key. |
| Application Insights | Workspace-based Application Insights connected to Log Analytics. |
| API Management | Consumption APIM, Power Automate adapter product, imported OpenAPI contract, API policy that forwards the Function host key from Key Vault. |
| Azure AI Foundry/Azure OpenAI | Azure AI Services account and Azure OpenAI account with local auth disabled. |
| Azure Document Intelligence | Form Recognizer/Document Intelligence account with local auth disabled. |

## APIM API Surface

The APIM contract exposes:

- `POST /emails/ingest`
- `POST /orders/{orderRunId}/process`
- `POST /customers/identify`
- `POST /items/validate`
- `POST /imports/customers`
- `POST /imports/items`
- `POST /mailboxes`
- `POST /mailboxes/{id}/test-connection`
- `POST /console/users`
- `POST /customers/{customerId}/users`
- `POST /exceptions/{id}/resolve`
- `POST /orders/{orderRunId}/reprocess`

Power Automate shell flows should use the APIM base URL output from deployment and an APIM subscription key. Raw Azure Function URLs and Power Platform direct workflow webhook chains are not part of the new design.

## Identity and RBAC

The Function app receives:

- Storage Blob Data Contributor on the storage account.
- Storage Queue Data Contributor on the storage account.
- Storage Table Data Contributor on the storage account.
- Key Vault Secrets User on Key Vault.
- Cosmos DB built-in data contributor through Cosmos SQL RBAC.
- Cognitive Services OpenAI User on Azure OpenAI.
- Cognitive Services User on Azure AI Services and Document Intelligence.

API Management receives:

- Key Vault Secrets User on Key Vault so it can resolve the Function host key named value.

Secrets stay in Key Vault or APIM secret references. Function app settings do not contain storage account keys, Cognitive Services keys, OpenAI keys, Document Intelligence keys, or Cosmos keys.

## Deployment Runbook

Validate templates:

```powershell
az bicep build --file .\infra\main.bicep
az bicep build --file .\infra\subscription.bicep
```

Deploy from subscription scope:

```powershell
az deployment sub create `
  --location eastus `
  --template-file .\infra\subscription.bicep `
  --parameters .\infra\parameters\dev.subscription.bicepparam
```

Deploy into an existing resource group:

```powershell
az deployment group create `
  --resource-group rg-orderprocessor-dev `
  --template-file .\infra\main.bicep `
  --parameters .\infra\parameters\dev.bicepparam
```

After deployment:

- Use the `apiGatewayBaseUrl` output in Power Automate adapter configuration.
- Create APIM subscriptions for adapter callers.
- Deploy Function app code through the eventual CI/CD pipeline.
- Deploy Azure OpenAI models only after capacity/model choices are confirmed.

## Known Boundaries

- No billable Azure resources were created in this pass.
- Azure OpenAI model deployments are intentionally deferred because model, version, SKU, region, and capacity should be selected when customer identification/item matching implementation begins.
- Private networking is not enabled yet. The current dev foundation uses public endpoints with Entra/RBAC and APIM facade controls; private endpoints/VNet integration can be added before production hardening.
- Phase 4 added the Cosmos repository and model contract. Local development still defaults to the in-memory repository, while deployed Functions use `ORDER_PROCESSOR_STORAGE_BACKEND=cosmos`.
- Mailbox and console-user endpoints are callable scaffolds. Live Microsoft Graph mailbox permission testing and full Entra console authorization are implemented in later mailbox, data-model, and console phases.
- The deployment principal must be allowed to create role assignments and write Key Vault secrets. In Azure RBAC terms, the deploying identity needs role-assignment permissions at the target scopes plus permission to deploy Key Vault secret resources.

## References

- Azure built-in roles: https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles
- Azure built-in Storage roles: https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/storage
- Azure built-in AI and machine learning roles: https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/ai-machine-learning
