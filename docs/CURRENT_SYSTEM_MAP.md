# Current System Map

This document summarizes the exported `OrdersAutomations` solution so the new Azure implementation can preserve behavior while reducing Power Automate complexity.

## Exported Inventory

- Current solution: `OrdersAutomations`
- Source environment: `e51ca662-f633-e290-b476-202747054118`
- Export user: `automations@frontierdistributing.com`
- Flow count: 32
- Flow groups:
  - `01-master-email`: 2 flows
  - `02-customer-identification`: 9 flows
  - `03-order-process`: 11 flows
  - `04-item-number`: 5 flows
  - `05-form-submissions`: 2 flows
  - `06-maintenance`: 1 flow
  - `90-temp-test`: 2 flows

## Capability Map

| Capability | Existing flow group | New owner |
| --- | --- | --- |
| Mailbox routing | `01-master-email` | Power Automate trigger plus Azure `/emails/ingest` |
| Customer identification | `02-customer-identification` | Azure `/customers/identify` with deterministic rules and Cosmos vector fallback |
| Order processing | `03-order-process` | Azure Durable Functions orchestration and processor modules |
| Item validation | `04-item-number` | Azure `/items/validate` |
| Support forms | `05-form-submissions` | Console and optional Power Automate shell flows |
| Data refresh | `06-maintenance`, item SharePoint flows | Azure `/imports/customers`, `/imports/items` |
| Temporary/manual tests | `90-temp-test` | Removed or replaced by API tests |

CSV scope note: `orderProcess - CSV Parse` is the only active CSV processor flow in the reference library. The two removed/deprecated CSV variants were removed from generated indexes and should not be migrated.

## External Dependencies Found

| Dependency | Current use | Migration direction |
| --- | --- | --- |
| Office 365 Outlook | 213 actions across 24 flows | Keep only tenant mailbox triggers and optional delivery adapters |
| SharePoint | 126 actions across 24 flows | Replace operational lists with Cosmos DB and Blob Storage |
| OpenAI API | 99 endpoints across customer ID and order processing flows | Replace direct OpenAI calls with Azure OpenAI via backend service |
| Microsoft Graph | 171 endpoint references for mailbox folders/messages | Move Graph access behind backend where possible; expose mailbox config/status in the console |
| Google Document AI | PDF order extraction | Replace with Azure Document Intelligence |
| Plumsail Documents | 2 actions across 2 flows, including active CSV Parse CSV parsing | Replace with Azure Function code; generate XLSX only when required by output profile |
| Excel Online Business | Office Scripts and workbook operations | Keep only if pilot parity proves it is required |
| SQL Server connector | Item/customer data support flows | Replace with Cosmos import pipeline |
| Microsoft Forms and Approvals | Intake/problem report workflows | Replace with console workflow or keep as optional shell adapters |
| Direct Power Platform workflow webhooks | 54 endpoint references | Remove from new design; use API Management facade |

## SharePoint References

The flow analysis found 6 SharePoint list references on `https://pioneerpetfood.sharepoint.com/sites/Automations` and 97 list actions. The latest live list configuration/data fetch attempt on 2026-06-20 failed because Conditional Access invalidated the Graph refresh token. Static SharePoint action/list references are current.

Reference files:

- `power-automate/solutions/OrdersAutomations/sharepoint-reference/sharepoint-actions.json`
- `power-automate/solutions/OrdersAutomations/sharepoint-reference/sharepoint-list-references.json`
- `power-automate/solutions/OrdersAutomations/sharepoint-reference/retrieval-status.json`

## Flow Library

The organized reference library remains the source of truth for reverse engineering:

- `power-automate/solutions/OrdersAutomations/flow-library/flow-index.json`
- `power-automate/solutions/OrdersAutomations/flow-library/01-master-email`
- `power-automate/solutions/OrdersAutomations/flow-library/02-customer-identification`
- `power-automate/solutions/OrdersAutomations/flow-library/03-order-process`
- `power-automate/solutions/OrdersAutomations/flow-library/04-item-number`
- `power-automate/solutions/OrdersAutomations/flow-library/05-form-submissions`
- `power-automate/solutions/OrdersAutomations/flow-library/06-maintenance`

## Phase 2 Reverse Engineering

Phase 2 is complete. Detailed artifacts:

- `docs/reverse-engineering/PHASE_2_REVERSE_ENGINEERING.md`
- `docs/reverse-engineering/FLOW_CAPABILITY_MAP.md`
- `docs/reverse-engineering/flow-capability-map.json`
- `docs/reverse-engineering/ORDER_PROCESS_FLOW_ANALYSIS.md`
- `docs/reverse-engineering/order-process-flow-analysis.json`
- `samples/phase-2`

Completion notes:

- Every active flow was mapped to one migration capability.
- All 11 active order-processing flows were documented with trigger schema, parser assumptions, output side effects, customer assumptions, error path, and migration notes.
- `orderProcess - CSV Parse` is the only active CSV processor reference and its Plumsail CSV parsing dependency is documented as an Azure Function replacement target.
- Synthetic sample fixtures were created for CSV, XLSX, XLS/XLT, PDF, email body, and customer-specific cases. Real approved production samples still need to be added for pilot/shadow-run acceptance.

## Next Reverse-Engineering Follow-Ups

- Convert captured SharePoint list usage into Cosmos container mapping details during Phase 4.
- Add real pilot customer sample emails/files once approved source data is available.
- Use the Phase 2 analysis as the acceptance baseline for Phase 9 parser and output adapter parity.
