# Phase 1 Baseline

Phase 1 was completed on 2026-06-20. This document records the planning artifact, refreshed references, current inventory counts, and known external blockers for the next implementation pass.

## Scope Completed

- Created and maintained the project roadmap in `docs/ORDER_PROCESSOR_PHASE_PLAN.md`.
- Linked the current reference set:
  - `power-automate/solutions/OrdersAutomations/flow-library`
  - `power-automate/solutions/OrdersAutomations/sharepoint-reference`
  - `power-automate/solutions/OrdersAutomations/external-dependencies`
- Regenerated the flow library from the remaining local workflow definitions.
- Regenerated the external dependency inventory from the remaining local workflow definitions.
- Regenerated the static SharePoint action/list reference inventory from the remaining local workflow definitions.
- Attempted live SharePoint list schema/item export through Microsoft Graph.

## Flow Baseline

- Total flows in the local reference library: 32
- Flow groups:
  - `01-master-email`: 2
  - `02-customer-identification`: 9
  - `03-order-process`: 11
  - `04-item-number`: 5
  - `05-form-submissions`: 2
  - `06-maintenance`: 1
  - `90-temp-test`: 2
- Active CSV processor scope: `orderProcess - CSV Parse` only.
- Removed/deprecated CSV variants are no longer present in generated reference indexes.

## Dependency Baseline

| Connector | Actions | Flows | Operations |
| --- | ---: | ---: | --- |
| `shared_approvals` | 3 | 2 | `StartAndWaitForAnApproval` |
| `shared_excelonlinebusiness` | 13 | 4 | `GetItems`, `RunScriptProdV2` |
| `shared_microsoftforms` | 4 | 2 | `CreateFormWebhook`, `GetFormResponseById` |
| `shared_office365` | 213 | 24 | `GetAttachment_V2`, `GetEmailsV3`, `GetEmailV2`, `HttpRequest`, `MoveV2`, `SendEmailV2`, `SharedMailboxOnNewEmailV2` |
| `shared_plumsail` | 2 | 2 | `FlowV1DocumentsJobsParseCsvPost`, `FlowV1DocumentsJobsXls2XlsxPost` |
| `shared_sharepointonline` | 126 | 24 | `CreateFile`, `DeleteFile`, `DeleteItem`, `GetFileContent`, `GetItem`, `GetItems`, `PatchItem`, `PostItem` |
| `shared_sql` | 4 | 4 | `GetItems_V2` |

Endpoint baseline:

- Azure Logic Apps / Power Automate webhook: 4 endpoints
- Google API: 7 endpoints
- Microsoft Graph: 171 endpoints
- OpenAI API: 99 endpoints
- Power Platform direct workflow webhook: 54 endpoints
- SharePoint / OneDrive: 126 endpoints

## SharePoint Baseline

- Referenced site count: 1
- Referenced list count: 6
- SharePoint connector actions: 126
- SharePoint list actions: 97
- Referenced site: `https://pioneerpetfood.sharepoint.com/sites/Automations`

Live Graph export status:

- Attempted: yes
- Succeeded: no
- Status file: `power-automate/solutions/OrdersAutomations/sharepoint-reference/retrieval-status.json`
- Current blocker: Frontier tenant Conditional Access invalidates the Azure CLI refresh token for `automations@frontierdistributing.com`, returning `AADSTS530036`.

Because Graph retrieval did not succeed, list schemas, columns, content types, and live list items were not exported. The static action/list usage inventory is current and can still be used for reverse engineering.

## Known Baseline Decisions

- Customer-specific monitored mailbox configuration is first-class platform data and must be exposed in the backend console.
- Initial full console administrator is `connect@focuseautomate.com`.
- Customer users authenticate with Microsoft login and receive customer access through console-managed assignments.
- Power Automate remains a shell for mailbox/event triggers and optional M365 adapters.
- The active CSV migration target is `orderProcess - CSV Parse`.
- Plumsail CSV parsing/conversion must be replaced by Azure Function code and standard libraries. XLSX should be generated only when required by an output profile.

## Reproduction Commands

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Build-FlowLibrary.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Export-ExternalDependencies.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Export-SharePointReference.ps1
```

The SharePoint command records the live Graph auth result in `retrieval-status.json`. If Graph auth is fixed, rerun it without `-SkipFetch` to export list configs and items.

## Handoff To Phase 2

- Use `flow-index.json` as the canonical active-flow list.
- Use the static SharePoint references for current list usage until Graph auth is fixed.
- Treat raw exported workflow definitions and dependency reports as sensitive because they may include tenant-specific endpoints, connection references, and trigger URLs.
- Do not reintroduce removed/deprecated CSV variants into migration scope.
