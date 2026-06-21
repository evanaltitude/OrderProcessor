# OrderProcessor Power Automate Solution

This folder contains the Phase 5 Power Automate shell solution for the rebuilt platform.

Target solution:

- Display name: `Order Processor`
- Unique name: `OrderProcessor`
- Environment: `Focus Automate`
- Environment ID: `abbd708f-4eaf-e875-a282-e1207f4e370c`
- Dataverse URL: `https://org2ccebd74.crm.dynamics.com/`
- Owner/account used by PAC: `evanb@altitudelogistics.com`
- Version: `1.0.0.0`
- Type: unmanaged

The existing exported reference solution remains under `power-automate/solutions/OrdersAutomations`.

## Status

Phase 5 is complete. PAC verified the online unmanaged `OrderProcessor` solution in the target environment, the local package was generated, and the unmanaged zip was imported asynchronously.

- Package: `exports/OrderProcessor_1.0.0.0_unmanaged.zip`
- Import id: `74e34f63-cb6c-f111-ab0d-7c1e5281c285`
- Import result: `Solution Imported successfully.`
- Import status record: `import-status.json`

The imported Office 365 Outlook connection reference still needs a concrete connection before dependent mailbox or M365 delivery flows can be started. The templates are intentionally disabled.

## Local Dataverse Project

PAC generated the local Dataverse project at `DataverseProject`. The generator keeps the workflow components, catalog, manifest, and connection reference metadata in sync.

Regenerate the shell templates and unmanaged package from the repo root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Build-OrderProcessorShellSolution.ps1
```

Import the generated package when a live refresh is intended:

```powershell
pac solution import --environment abbd708f-4eaf-e875-a282-e1207f4e370c --path .\power-automate\solutions\OrderProcessor\exports\OrderProcessor_1.0.0.0_unmanaged.zip --async --max-async-wait-time 10
```

## Shell Templates

| Flow | Workflow ID | Purpose |
| --- | --- | --- |
| `OrderProcessor - Mailbox Trigger Template` | `7f52d9d6-8eb1-4ad7-b2f6-89dd55dc4e01` | Shared mailbox trigger that calls APIM `POST /emails/ingest`. |
| `OrderProcessor - Customer Import Adapter Template` | `06b7f4fc-5f13-4a6f-8742-03f19c301902` | Customer-source adapter that calls APIM `POST /imports/customers`. |
| `OrderProcessor - Item Import Adapter Template` | `7aa4ab3f-d509-4866-98a0-fcce8dc79b03` | Item-source adapter that calls APIM `POST /imports/items`. |
| `OrderProcessor - Output Delivery Adapter Template` | `92df442b-2360-42dd-b787-ce339813d877` | Optional M365 delivery placeholder for customer-specific output delivery. |

## Boundary Rules

Keep the Power Automate layer limited to:

- Mailbox trigger flow calling Azure/APIM `/emails/ingest`.
- Customer import adapter calling Azure/APIM `/imports/customers`.
- Item import adapter calling Azure/APIM `/imports/items`.
- Optional M365-specific output delivery adapter.

Do not add parsing, customer identification, item validation, OpenAI, Google Document AI, Plumsail, SharePoint operational storage, direct Function URLs, or flow-to-flow webhook chains to the shell templates.
