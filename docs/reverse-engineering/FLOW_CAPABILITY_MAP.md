# Flow Capability Map

Generated from power-automate/solutions/OrdersAutomations/flow-library/flow-index.json.

| Flow | State | Capability | Primary Behavior | Migration Target |
| --- | --- | --- | --- | --- |
| orders@ - Master - Email Processor | Activated | mailbox routing | Monitors or processes the shared orders mailbox and dispatches work to downstream processing modules. | Power Automate shell plus Azure /emails/ingest and data-driven routing rules. |
| orders@ - Master - Email Router | Activated | mailbox routing | Monitors or processes the shared orders mailbox and dispatches work to downstream processing modules. | Power Automate shell plus Azure /emails/ingest and data-driven routing rules. |
| Macro - Claim - Customer Identification - AI | Activated | customer ID | Identifies or updates customer metadata for an email/order and records the result for routing or processing. | Azure /customers/identify, deterministic rules, Azure OpenAI embeddings, and Cosmos vector search. |
| Macro - Completed - Customer Identification - AI | Activated | customer ID | Identifies or updates customer metadata for an email/order and records the result for routing or processing. | Azure /customers/identify, deterministic rules, Azure OpenAI embeddings, and Cosmos vector search. |
| Macro - Reroute Email - Customer Identification - AI | Activated | customer ID | Identifies or updates customer metadata for an email/order and records the result for routing or processing. | Azure /customers/identify, deterministic rules, Azure OpenAI embeddings, and Cosmos vector search. |
| orders@ - Module - Customer ID - Order Lines - Responses | Activated | customer ID | Identifies or updates customer metadata for an email/order and records the result for routing or processing. | Azure /customers/identify, deterministic rules, Azure OpenAI embeddings, and Cosmos vector search. |
| orders@ - Module - Customer ID - PDF - Responses | Activated | customer ID | Identifies or updates customer metadata for an email/order and records the result for routing or processing. | Azure /customers/identify, deterministic rules, Azure OpenAI embeddings, and Cosmos vector search. |
| orders@ - Module - Customer ID - Prompt Override - Responses | Activated | customer ID | Identifies or updates customer metadata for an email/order and records the result for routing or processing. | Azure /customers/identify, deterministic rules, Azure OpenAI embeddings, and Cosmos vector search. |
| orders@ - Module - Customer ID - Responses | Activated | customer ID | Identifies or updates customer metadata for an email/order and records the result for routing or processing. | Azure /customers/identify, deterministic rules, Azure OpenAI embeddings, and Cosmos vector search. |
| orders@ - Module - Customer Identification | Draft | customer ID | Identifies or updates customer metadata for an email/order and records the result for routing or processing. | Azure /customers/identify, deterministic rules, Azure OpenAI embeddings, and Cosmos vector search. |
| orders@ - Module - Customer Identification - Order Lines | Draft | customer ID | Identifies or updates customer metadata for an email/order and records the result for routing or processing. | Azure /customers/identify, deterministic rules, Azure OpenAI embeddings, and Cosmos vector search. |
| Backup - orderProcess - Google Document AI -  PDF | Draft | order processing | Transforms an email, attachment, PDF, workbook, CSV, or email body into order output and mailbox status updates. | Azure Durable Functions order-processing engine and per-source parser modules. |
| Manual - orderProcess - Google Document AI -  PDF | Draft | order processing | Transforms an email, attachment, PDF, workbook, CSV, or email body into order output and mailbox status updates. | Azure Durable Functions order-processing engine and per-source parser modules. |
| Manual Control - Starter - orderProcess | Activated | order processing | Transforms an email, attachment, PDF, workbook, CSV, or email body into order output and mailbox status updates. | Azure Durable Functions order-processing engine and per-source parser modules. |
| orderProcess - CSV Parse | Activated | order processing | Transforms an email, attachment, PDF, workbook, CSV, or email body into order output and mailbox status updates. | Azure Durable Functions order-processing engine and per-source parser modules. |
| orderProcess - Dorothy Lane XLSX | Activated | order processing | Transforms an email, attachment, PDF, workbook, CSV, or email body into order output and mailbox status updates. | Azure Durable Functions order-processing engine and per-source parser modules. |
| orderProcess - Email Body - Market Place Pet Supplies w Item Validation | Activated | order processing | Transforms an email, attachment, PDF, workbook, CSV, or email body into order output and mailbox status updates. | Azure Durable Functions order-processing engine and per-source parser modules. |
| orderProcess - Email Body - moduleID | Activated | order processing | Transforms an email, attachment, PDF, workbook, CSV, or email body into order output and mailbox status updates. | Azure Durable Functions order-processing engine and per-source parser modules. |
| orderProcess - Google Document AI -  PDF | Activated | order processing | Transforms an email, attachment, PDF, workbook, CSV, or email body into order output and mailbox status updates. | Azure Durable Functions order-processing engine and per-source parser modules. |
| orderProcess - XLS or XLT - AI Header - moduleID | Activated | order processing | Transforms an email, attachment, PDF, workbook, CSV, or email body into order output and mailbox status updates. | Azure Durable Functions order-processing engine and per-source parser modules. |
| orderProcess - XLSX - AI Header - moduleID | Activated | order processing | Transforms an email, attachment, PDF, workbook, CSV, or email body into order output and mailbox status updates. | Azure Durable Functions order-processing engine and per-source parser modules. |
| orderProcess - XLSX - AI Header ID - Petland | Activated | order processing | Transforms an email, attachment, PDF, workbook, CSV, or email body into order output and mailbox status updates. | Azure Durable Functions order-processing engine and per-source parser modules. |
| itemNumber Scheduler | Activated | data refresh | Refreshes item/customer reference data or maintenance state from SharePoint/SQL sources. | Azure /imports/customers or /imports/items writing canonical Cosmos records and Blob audit copies. |
| ItemNumber Sharepoint List | Activated | data refresh | Refreshes item/customer reference data or maintenance state from SharePoint/SQL sources. | Azure /imports/customers or /imports/items writing canonical Cosmos records and Blob audit copies. |
| ItemNumber Sharepoint List 1 | Activated | data refresh | Refreshes item/customer reference data or maintenance state from SharePoint/SQL sources. | Azure /imports/customers or /imports/items writing canonical Cosmos records and Blob audit copies. |
| ItemNumber Sharepoint List Delete Un-Updated | Activated | data refresh | Refreshes item/customer reference data or maintenance state from SharePoint/SQL sources. | Azure /imports/customers or /imports/items writing canonical Cosmos records and Blob audit copies. |
| Module - Item Number Validator | Activated | item validation | Validates provided item numbers/UPCs against SharePoint item reference data. | Azure /items/validate backed by Cosmos items. |
| Order Automate Request - Form Submission | Activated | support form | Supports request, problem-report, manual diagnostic, or temporary test workflows. | Console workflow, optional M365 adapter, or remove if temporary diagnostic only. |
| Problem Report - Form Submission | Activated | support form | Supports request, problem-report, manual diagnostic, or temporary test workflows. | Console workflow, optional M365 adapter, or remove if temporary diagnostic only. |
| orders@ - Customer List Assistant Creation and Deletion - 01:00 | Activated | data refresh | Refreshes item/customer reference data or maintenance state from SharePoint/SQL sources. | Azure /imports/customers or /imports/items writing canonical Cosmos records and Blob audit copies. |
| Temp - Manually Send Email HTTP Request | Activated | support form | Supports request, problem-report, manual diagnostic, or temporary test workflows. | Console workflow, optional M365 adapter, or remove if temporary diagnostic only. |
| Test - Send HTTP Request | Activated | support form | Supports request, problem-report, manual diagnostic, or temporary test workflows. | Console workflow, optional M365 adapter, or remove if temporary diagnostic only. |

## Capability Counts

- customer ID: 9
- data refresh: 5
- item validation: 1
- mailbox routing: 2
- order processing: 11
- support form: 4
