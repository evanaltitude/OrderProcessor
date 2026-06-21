# OrderProcessor - Mailbox Trigger Template

- Status: disabled template
- Trigger: Office 365 Outlook shared mailbox new-email trigger
- Azure/APIM target: POST /emails/ingest
- Workflow ID: 7f52d9d6-8eb1-4ad7-b2f6-89dd55dc4e01

## Notes

- Instantiate one copy per monitored customer mailbox when Power Automate must own the mailbox trigger.
- Mailbox address, mailboxAccountId, and customerId are parameters/configuration values, not branches.
- Flow sends metadata and attachment references to APIM only; Azure performs routing and processing.

## Configuration

- OrderProcessorApiBaseUrl: APIM base URL, for example https://{apim-name}.azure-api.net/order-processor.
- OrderProcessorApimSubscriptionKey: APIM subscription key or future custom connector secret.
- OrderProcessorTenantId: platform tenant identifier.
- Mailbox templates additionally require OrderProcessorMailboxAddress, OrderProcessorMailboxAccountId, and OrderProcessorCustomerId.

Do not add parsing, customer identification, item validation, OpenAI, Google Document AI, Plumsail, SharePoint operational storage, or flow-to-flow webhook calls to this flow.