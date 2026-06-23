# OrderProcessor Shell Flow Catalog

The OrderProcessor Power Automate solution intentionally contains only mailbox/customer adapter flows. These flows are templates and should stay disabled until APIM, mailbox configuration, and connection references are configured for a distributor tenant.

| Flow | Status | Trigger | Target | Connection references |
| --- | --- | --- | --- | --- |
| OrderProcessor - Mailbox Trigger Template | disabled template | Office 365 Outlook shared mailbox new-email trigger | POST /emails/ingest | shared_office365 |
| OrderProcessor - Customer Import Adapter Template | disabled template | HTTP request from customer-specific file/source adapter | POST /imports/customers |  |
| OrderProcessor - Item Import Adapter Template | disabled template | HTTP request from distributor item file/source adapter | POST /imports/items |  |
| OrderProcessor - Output Delivery Adapter Template | disabled optional template | HTTP request from Azure completion callback or scheduled adapter poll | customer-specific M365 delivery |  |

## Guardrails

- Call APIM through OrderProcessorApiBaseUrl; never call raw Azure Function URLs.
- Do not add OpenAI, Google Document AI, Plumsail, SharePoint operational storage, item validation, customer identification, parser, or output-generation logic to these flows.
- Instantiate mailbox trigger templates per monitored mailbox only when Power Automate must own the event trigger.
- Keep mailbox addresses, Microsoft connection metadata, and customer/user assignments in backend configuration and the console.
- Keep APIM subscription material in secure configuration or a future custom connector connection, not hard-coded in flow actions.