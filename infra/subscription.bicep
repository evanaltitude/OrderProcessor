targetScope = 'subscription'

@description('Azure region for the resource group and regional resources.')
param location string = 'eastus'

@description('Resource group that will contain the Order Processor foundation.')
param resourceGroupName string = 'rg-orderprocessor-dev'

@description('Short environment name, such as dev, test, or prod.')
param environmentName string = 'dev'

@description('Base application name used for resource naming.')
param projectName string = 'orderprocessor'

@description('Publisher email required by API Management.')
param apiPublisherEmail string

@description('Publisher name required by API Management.')
param apiPublisherName string = 'Altitude Logistics'

@description('Embedding dimensions stored in Cosmos DB vector fields.')
param embeddingDimensions int = 1536

@description('Tags applied to the resource group and child resources.')
param tags object = {
  workload: 'order-processor'
}

@description('SKU name for the console Azure Web App plan.')
param consoleAppServiceSkuName string = 'B1'

@description('SKU tier for the console Azure Web App plan.')
param consoleAppServiceSkuTier string = 'Basic'

@description('Azure region for the console Azure Web App plan and site. Defaults to the main deployment location.')
param consoleLocation string = location

@description('Microsoft Entra application client id for App Service Easy Auth. Leave blank to deploy the console app without auth settings.')
param consoleEntraClientId string = ''

@description('App setting name that contains the App Service Easy Auth client credential. Leave blank only when the configured auth app does not use a credential.')
param consoleEntraClientCredentialSettingName string = 'ORDER_PROCESSOR_CONSOLE_AUTH_CLIENT_SECRET'

@description('Initial Microsoft account allowed as the full console administrator.')
param consoleBootstrapAdminEmail string = 'connect@focuseautomate.com'

@description('Microsoft Entra application client id used for delegated Microsoft Graph mailbox authorization.')
param microsoftGraphAuthClientId string = ''

@description('Key Vault secret name containing the delegated Microsoft Graph OAuth client secret.')
param microsoftGraphAuthClientSecretName string = 'microsoft-graph-oauth-client-secret'

@description('Microsoft Entra tenant id or authority segment used for delegated Microsoft Graph OAuth.')
param microsoftGraphAuthTenantId string = subscription().tenantId

@description('Space-delimited delegated Microsoft Graph OAuth scopes requested for shared mailbox automation.')
param microsoftGraphAuthScopes string = 'openid profile offline_access User.Read Mail.ReadWrite.Shared Mail.Send.Shared MailboxSettings.ReadWrite'

@description('Deploy the Azure OpenAI account. Set false when the subscription is not yet enabled for OpenAI S0 quota/features.')
param deployAzureOpenAI bool = true

@description('Optional Blob URL for the Linux Consumption Function run-from-package deployment package.')
param functionPackageUrl string = ''

@secure()
@description('Shared backend key APIM sends to the Function app in x-order-processor-function-key. Defaults to a new value per deployment.')
param functionSharedKey string = newGuid()

resource foundationResourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module foundation 'main.bicep' = {
  name: 'orderProcessorFoundation-${environmentName}'
  scope: foundationResourceGroup
  params: {
    location: location
    environmentName: environmentName
    projectName: projectName
    apiPublisherEmail: apiPublisherEmail
    apiPublisherName: apiPublisherName
    embeddingDimensions: embeddingDimensions
    consoleAppServiceSkuName: consoleAppServiceSkuName
    consoleAppServiceSkuTier: consoleAppServiceSkuTier
    consoleLocation: consoleLocation
    consoleEntraClientId: consoleEntraClientId
    consoleEntraClientCredentialSettingName: consoleEntraClientCredentialSettingName
    consoleBootstrapAdminEmail: consoleBootstrapAdminEmail
    microsoftGraphAuthClientId: microsoftGraphAuthClientId
    microsoftGraphAuthClientSecretName: microsoftGraphAuthClientSecretName
    microsoftGraphAuthTenantId: microsoftGraphAuthTenantId
    microsoftGraphAuthScopes: microsoftGraphAuthScopes
    deployAzureOpenAI: deployAzureOpenAI
    functionPackageUrl: functionPackageUrl
    functionSharedKey: functionSharedKey
    tags: tags
  }
}

output resourceGroupName string = foundationResourceGroup.name
output functionAppName string = foundation.outputs.functionAppName
output consoleWebAppName string = foundation.outputs.consoleWebAppName
output consoleWebAppUrl string = foundation.outputs.consoleWebAppUrl
output apiGatewayBaseUrl string = foundation.outputs.apiGatewayBaseUrl
output cosmosAccountName string = foundation.outputs.cosmosAccountName
output keyVaultName string = foundation.outputs.keyVaultName
output azureOpenAiDeployed bool = foundation.outputs.azureOpenAiDeployed
