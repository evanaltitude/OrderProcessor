using '../main.bicep'

param environmentName = 'prod'
param projectName = 'orderprocessor'
param apiPublisherEmail = 'evanb@altitudelogistics.com'
param apiPublisherName = 'Altitude Logistics'
param deployAzureOpenAI = false
param consoleLocation = 'centralus'
param functionPackageUrl = 'https://opprodvc5upbm44rc4w.blob.core.windows.net/app-packages/functions-prod.zip'
param tags = {
  workload: 'order-processor'
  environment: 'prod'
}
