# OrderProcessor - Output Delivery Adapter Template

- Status: disabled optional template
- Trigger: HTTP request from Azure completion callback or scheduled adapter poll
- Azure/APIM target: customer-specific M365 delivery
- Workflow ID: 92df442b-2360-42dd-b787-ce339813d877

## Notes

- Use only when customer output delivery must remain in M365 or Power Automate.
- Default platform output generation belongs in Azure. This template is a placeholder for customer-specific delivery actions.

## Configuration

- OrderProcessorApiBaseUrl: APIM base URL, for example https://{apim-name}.azure-api.net/order-processor.
- OrderProcessorApimSubscriptionKey: APIM subscription key or future custom connector secret.
- OrderProcessorTenantId: platform tenant identifier.
- Mailbox templates additionally require OrderProcessorMailboxAddress and OrderProcessorMailboxAccountId.

Do not add parsing, customer identification, item validation, OpenAI, Google Document AI, Plumsail, SharePoint operational storage, or flow-to-flow webhook calls to this flow.