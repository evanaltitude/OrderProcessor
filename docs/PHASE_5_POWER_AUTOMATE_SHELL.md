# Phase 5 Power Automate Shell Solution

Phase 5 is complete as of 2026-06-20. The new Power Platform solution remains intentionally small: Power Automate owns mailbox/event triggers and optional M365 delivery adapters, while Azure owns routing, parsing, customer identification, item validation, persistence, audit, and output generation.

## Target Environment

- Environment name: `Focus Automate`
- Environment ID: `abbd708f-4eaf-e875-a282-e1207f4e370c`
- Dataverse URL: `https://org2ccebd74.crm.dynamics.com/`
- Connected PAC user: `evanb@altitudelogistics.com`
- Solution unique name: `OrderProcessor`
- Solution display name: `Order Processor`
- Solution version: `1.0.0.0`
- Solution type: unmanaged

PAC verification found the online unmanaged solution in the target environment before import. The generated unmanaged package was then imported asynchronously into the same environment. PAC reported import id `74e34f63-cb6c-f111-ab0d-7c1e5281c285` with `Solution Imported successfully`.

## Local Artifacts

- Shell folder: `power-automate/solutions/OrderProcessor`
- Generator: `tools/Build-OrderProcessorShellSolution.ps1`
- Flow templates: `power-automate/solutions/OrderProcessor/flow-templates`
- Dataverse project: `power-automate/solutions/OrderProcessor/DataverseProject`
- Unmanaged package: `power-automate/solutions/OrderProcessor/exports/OrderProcessor_1.0.0.0_unmanaged.zip`
- Shell manifest: `power-automate/solutions/OrderProcessor/shell-solution-manifest.json`
- Flow catalog: `power-automate/solutions/OrderProcessor/FLOW_TEMPLATE_CATALOG.md`
- Configuration contract: `power-automate/solutions/OrderProcessor/CONFIGURATION_CONTRACT.md`
- Import status: `power-automate/solutions/OrderProcessor/import-status.json`

Regenerate the local templates and package with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Build-OrderProcessorShellSolution.ps1
```

## Shell Flows

| Flow | Workflow ID | Status | Trigger | Backend boundary |
| --- | --- | --- | --- | --- |
| `OrderProcessor - Mailbox Trigger Template` | `7f52d9d6-8eb1-4ad7-b2f6-89dd55dc4e01` | Disabled template | Office 365 Outlook shared mailbox new-email trigger | Calls APIM `POST /emails/ingest`. |
| `OrderProcessor - Customer Import Adapter Template` | `06b7f4fc-5f13-4a6f-8742-03f19c301902` | Disabled template | HTTP request from a customer-specific file/source adapter | Calls APIM `POST /imports/customers`. |
| `OrderProcessor - Item Import Adapter Template` | `7aa4ab3f-d509-4866-98a0-fcce8dc79b03` | Disabled template | HTTP request from a customer-specific file/source adapter | Calls APIM `POST /imports/items`. |
| `OrderProcessor - Output Delivery Adapter Template` | `92df442b-2360-42dd-b787-ce339813d877` | Disabled optional template | HTTP request from Azure completion callback or scheduled adapter poll | Placeholder for M365 delivery only when required. |

The mailbox trigger sends mailbox address, mailbox account id, optional customer id, message id, sender, subject, received date, body preview/body content, and attachment references. It does not parse orders or branch by customer.

## Connection Reference

The solution defines one connection reference:

- Logical name: `alt_sharedoffice365_orderprocessor`
- Display name: `Office 365 Outlook OrderProcessor`
- Connector: `/providers/Microsoft.PowerApps/apis/shared_office365`
- Live connection reference id found after import: `cbfe0a69-28e2-4b44-94eb-eeecba52a48f`

The import completed, but PAC reported that connection references need concrete connections before dependent flows can be started. This is expected. Do not turn on mailbox or M365 delivery instances until the customer mailbox connection is created and bound.

## Guardrails

- Shell flows call API Management through `OrderProcessorApiBaseUrl`; they do not call raw Azure Function URLs.
- APIM subscription material is parameterized as `OrderProcessorApimSubscriptionKey` until a custom connector or Entra-based adapter auth replaces it.
- Mailbox addresses, mailbox ownership, Microsoft connection metadata, Graph permission state, customer/user assignments, and ingest status live in backend configuration and the console.
- Routing rules, customer identification, parser selection, Plumsail replacement logic, item validation, output generation, Cosmos writes, and audit timeline generation remain in Azure.
- The shell templates must not reintroduce SharePoint as operational customer/item storage, Plumsail, Google Document AI, direct OpenAI calls, direct workflow webhook chains, or CSV/XLS conversion connector actions.

## Activation Checklist

1. Deploy the Azure foundation so APIM returns a real `apiGatewayBaseUrl`.
2. Create or select an APIM subscription for Power Automate adapters.
3. Create backend `mailboxAccounts` and `microsoftAuthConnections` records for the customer mailbox.
4. Bind `alt_sharedoffice365_orderprocessor` to a concrete Office 365 Outlook connection with access to the monitored mailbox.
5. Configure a tenant mailbox copy/instance of the mailbox trigger with `OrderProcessorMailboxAddress`, `OrderProcessorMailboxAccountId`, `OrderProcessorTenantId`, `OrderProcessorApiBaseUrl`, and secure APIM subscription material.
6. Run a test email or manual trigger against APIM and confirm an `emailMessages` record is created.
7. Turn on only the configured customer-specific instance.

## Validation

Local tests added in `tests/test_power_automate_shell.py` verify:

- The shell manifest contains the four required flows and target environment id.
- Flow definitions use APIM parameters and avoid legacy dependencies.
- The mailbox template uses only the Office 365 Outlook shared-mailbox trigger plus a single HTTP call to APIM.
- The packaged unmanaged solution contains all four workflow JSON files.
- `Solution.xml` includes four workflow root components.
- `Customizations.xml` includes the Office 365 Outlook connection reference.

Live verification after import found the four workflow components and the Outlook connection reference in the `OrderProcessor` solution.
