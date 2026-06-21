# Power Automate Shell Solution

The new Power Platform solution is `OrderProcessor` in environment `abbd708f-4eaf-e875-a282-e1207f4e370c`. Phase 5 completed the local shell implementation and imported the unmanaged package into the target Focus Automate environment on 2026-06-20.

Power Automate should stay intentionally small: it handles customer-specific mailbox/event triggers and M365-only adapters, while Azure owns processing state, mailbox configuration, Microsoft auth status, routing, parsing, matching, persistence, audit, and output generation.

## Implemented Standard Flows

| Flow | Workflow ID | Trigger | Azure call | Notes |
| --- | --- | --- | --- | --- |
| `OrderProcessor - Mailbox Trigger Template` | `7f52d9d6-8eb1-4ad7-b2f6-89dd55dc4e01` | New email in configured shared mailbox | `POST /emails/ingest` | Sends message metadata, mailbox identity, customer scope when known, and attachment references. No parsing branches in the flow. |
| `OrderProcessor - Customer Import Adapter Template` | `06b7f4fc-5f13-4a6f-8742-03f19c301902` | Customer-specific file/source adapter request | `POST /imports/customers` | Use only when the source must be received through M365 or another Power Automate-owned source. |
| `OrderProcessor - Item Import Adapter Template` | `7aa4ab3f-d509-4866-98a0-fcce8dc79b03` | Item source adapter request | `POST /imports/items` | Replaces SharePoint item list maintenance flows. |
| `OrderProcessor - Output Delivery Adapter Template` | `92df442b-2360-42dd-b787-ce339813d877` | Azure completion callback or scheduled adapter poll | Customer-specific M365 delivery | Optional; use only when output must be sent through M365/Power Automate. |

All four flows are disabled templates. Customer-specific instances should be turned on only after APIM deployment values, mailbox/backend configuration, and required connection references are configured.

## Implementation Artifacts

- Phase 5 handoff: `docs/PHASE_5_POWER_AUTOMATE_SHELL.md`
- Local solution folder: `power-automate/solutions/OrderProcessor`
- Generator: `tools/Build-OrderProcessorShellSolution.ps1`
- Flow catalog: `power-automate/solutions/OrderProcessor/FLOW_TEMPLATE_CATALOG.md`
- Configuration contract: `power-automate/solutions/OrderProcessor/CONFIGURATION_CONTRACT.md`
- Import status: `power-automate/solutions/OrderProcessor/import-status.json`

## Removed From Power Automate

- Direct OpenAI calls.
- Direct Google Document AI calls.
- Plumsail conversion/parsing.
- SharePoint as the operational customer/item store.
- Flow-to-flow direct workflow webhook chains.
- Item matching logic and customer identification branching.

## Console Configuration

- Initial full console admin: `connect@focuseautomate.com`.
- Customer mailbox address, owning customer, Microsoft connection status, and ingest behavior are managed in the backend console.
- Customer user management accepts a Microsoft email address, assigns customer/role access, and relies on Microsoft login for authentication.

## Imported Connection Reference

- Logical name: `alt_sharedoffice365_orderprocessor`
- Connector: `/providers/Microsoft.PowerApps/apis/shared_office365`
- Live connection reference id: `cbfe0a69-28e2-4b44-94eb-eeecba52a48f`
- Status: imported, but still requires a concrete Office 365 Outlook connection binding before dependent flows can be started.

## Security Expectations

- Power Automate calls API Management at the deployed `apiGatewayBaseUrl`, not raw Function URLs.
- API Management validates the caller through APIM subscription access and forwards to Azure Functions with the backend shared key stored in Key Vault.
- Adapter flows must not store backend shared keys, Cognitive Services keys, OpenAI keys, Document Intelligence keys, Cosmos keys, or direct Power Platform workflow webhook URLs.
- Console users authenticate with Entra ID/Microsoft login and are authorized from `consoleUsers` plus `customerUserAssignments`.
- Azure services use managed identity/RBAC where possible.
- Secrets are stored in Key Vault, not Power Automate variables.

## Reference Folder

The local shell reference is tracked at `power-automate/solutions/OrderProcessor/README.md`.
