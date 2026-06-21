# OrderProcessor - Item Import Adapter Template

- Status: disabled template
- Trigger: HTTP request from customer-specific file/source adapter
- Azure/APIM target: POST /imports/items
- Workflow ID: 7aa4ab3f-d509-4866-98a0-fcce8dc79b03

## Notes

- Use only when item refresh input must originate in Power Automate.
- Flow forwards normalized rows/source metadata to APIM; Azure owns parsing, item normalization, embeddings, and Cosmos writes.

## Configuration

- OrderProcessorApiBaseUrl: APIM base URL, for example https://{apim-name}.azure-api.net/order-processor.
- OrderProcessorApimSubscriptionKey: APIM subscription key or future custom connector secret.
- OrderProcessorTenantId: platform tenant identifier.
- Mailbox templates additionally require OrderProcessorMailboxAddress, OrderProcessorMailboxAccountId, and OrderProcessorCustomerId.

Do not add parsing, customer identification, item validation, OpenAI, Google Document AI, Plumsail, SharePoint operational storage, or flow-to-flow webhook calls to this flow.