# OrderProcessor Power Automate Configuration Contract

## Required Deployment Values

| Value | Owner | Notes |
| --- | --- | --- |
| OrderProcessorApiBaseUrl | Azure/APIM deployment output | Example: https://{apim-name}.azure-api.net/order-processor. |
| OrderProcessorApimSubscriptionKey | APIM subscription/custom connector connection | Used by adapter flows until Entra/custom-connector auth replaces subscription keys. |
| OrderProcessorTenantId | Backend tenant config | Defaults to altitude for the first environment. |
| OrderProcessorMailboxAddress | Backend mailbox config | One value per monitored distributor mailbox trigger instance. |
| OrderProcessorMailboxAccountId | Cosmos mailboxAccounts.id | Lets Azure correlate the flow trigger to backend mailbox configuration. |

## Connection References

- alt_sharedoffice365_orderprocessor: Office 365 Outlook connection reference for mailbox trigger instances and any customer-specific M365 output delivery adapters.
- HTTP/APIM calls are represented as plain HTTP actions in the template. A custom connector can replace them later without changing the backend contract.

## Backend Configuration Boundary

Power Automate must not own customer routing, customer identification, or parser logic. Mailbox address, tenant ownership, connection metadata, Graph permission status, and ingest state are stored in Cosmos containers mailboxAccounts and microsoftAuthConnections, then exposed through the console. Downstream customer identification happens in Azure through deterministic rules, aliases, embeddings, and exception handling.

## Flow Activation Checklist

1. Confirm APIM apiGatewayBaseUrl from Azure deployment.
2. Create or select an APIM subscription for Power Automate adapters.
3. Create/update mailboxAccounts and microsoftAuthConnections through the backend/console.
4. Configure the flow template parameters for the specific mailbox or adapter.
5. Bind the Office 365 Outlook connection reference when the template uses M365.
6. Run a test invocation against APIM.
7. Turn on only the configured tenant mailbox flow instance.