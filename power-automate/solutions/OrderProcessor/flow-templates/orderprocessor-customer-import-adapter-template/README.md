# OrderProcessor - Customer Import Adapter Template

- Status: disabled template
- Trigger: HTTP request from customer-specific file/source adapter
- Azure/APIM target: POST /imports/customers
- Workflow ID: 06b7f4fc-5f13-4a6f-8742-03f19c301902

## Notes

- Use only when a customer source file must be received through M365 or another Power Automate-owned source.
- Flow forwards normalized rows/source metadata to APIM; Azure owns parsing, validation, and Cosmos writes.

## Configuration

- OrderProcessorApiBaseUrl: APIM base URL, for example https://{apim-name}.azure-api.net/order-processor.
- OrderProcessorApimSubscriptionKey: APIM subscription key or future custom connector secret.
- OrderProcessorTenantId: platform tenant identifier.
- Mailbox templates additionally require OrderProcessorMailboxAddress, OrderProcessorMailboxAccountId, and OrderProcessorCustomerId.

Do not add parsing, customer identification, item validation, OpenAI, Google Document AI, Plumsail, SharePoint operational storage, or flow-to-flow webhook calls to this flow.