using '../subscription.bicep'

param location = 'westus2'
param resourceGroupName = 'rg-orderprocessor-prod'
param environmentName = 'prod'
param projectName = 'orderprocessor'
param apiPublisherEmail = 'evanb@altitudelogistics.com'
param apiPublisherName = 'Altitude Logistics'
param deployAzureOpenAI = false
param consoleLocation = 'centralus'
param consoleEntraClientId = '699e9bbf-e17f-4577-8283-63122234ea29'
param microsoftGraphAuthClientId = 'f56d0201-9bd0-4d8a-9b1f-89eaab1ae785'
param microsoftGraphAuthTenantId = '209270b8-d2c8-4e4c-aeff-1236dbfed6ca'
param functionPackageUrl = 'https://opprodvc5upbm44rc4w.blob.core.windows.net/app-packages/functions-prod-22c1db7.zip'
param tags = {
  workload: 'order-processor'
  environment: 'prod'
}
