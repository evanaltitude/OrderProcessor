targetScope = 'resourceGroup'

@description('Azure region for all regional resources.')
param location string = resourceGroup().location

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

@description('Tags applied to all tagged resources.')
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
param microsoftGraphAuthScopes string = 'openid profile offline_access User.Read Mail.ReadWrite.Shared Mail.Send.Shared'

@description('CRON schedule for renewing Microsoft Graph mailbox webhook subscriptions. Default is every 6 hours.')
param graphSubscriptionRenewalCron string = '0 0 */6 * * *'

@allowed([
  'auto'
  'app'
  'delegated'
])
@description('Microsoft Graph authorization mode used when creating mailbox webhook subscriptions.')
param graphSubscriptionAuthMode string = 'auto'

@description('Azure Storage queue used to hand off Microsoft Graph webhook notifications for mailbox processing.')
param graphNotificationQueueName string = 'graph-mailbox-notifications'

@description('Azure Storage queue used to hand off customer/item import jobs for background processing.')
param importJobQueueName string = 'import-jobs'

@description('CRON schedule for manual fallback mailbox polling. The recurring poll timer is no longer deployed by default.')
param mailboxPollCron string = '0 */5 * * * *'

@description('Deploy the Azure OpenAI account. Set false when the subscription is not yet enabled for OpenAI S0 quota/features.')
param deployAzureOpenAI bool = true

@description('Optional Blob URL for the Linux Consumption Function run-from-package deployment package.')
param functionPackageUrl string = ''

@secure()
@description('Shared backend key APIM sends to the Function app in x-order-processor-function-key. Defaults to a new value per deployment.')
param functionSharedKey string = newGuid()

var normalizedEnvironment = toLower(replace(environmentName, '-', ''))
var suffix = toLower(uniqueString(resourceGroup().id, projectName, environmentName))
var storageAccountName = take('op${normalizedEnvironment}${suffix}', 24)
var logAnalyticsName = take('${projectName}-${environmentName}-log-${suffix}', 63)
var appInsightsName = take('${projectName}-${environmentName}-appi-${suffix}', 63)
var functionPlanName = take('${projectName}-${environmentName}-plan-${suffix}', 63)
var functionAppName = take('${projectName}-${environmentName}-func-${suffix}', 60)
var consolePlanName = take('${projectName}-${environmentName}-console-plan-${suffix}', 63)
var consoleWebAppName = take('${projectName}-${environmentName}-console-${suffix}', 60)
var cosmosAccountName = take('${projectName}-${environmentName}-cosmos-${suffix}', 44)
var keyVaultName = take('kv-${projectName}-${environmentName}-${suffix}', 24)
var apiManagementName = take('${projectName}-${environmentName}-apim-${suffix}', 50)
var aiServicesName = take('${projectName}-${environmentName}-aisvc-${suffix}', 64)
var openAiName = take('${projectName}-${environmentName}-aoai-${suffix}', 64)
var documentIntelligenceName = take('${projectName}-${environmentName}-docintel-${suffix}', 64)
var durableHubName = take('OrderProcessor${normalizedEnvironment}', 45)
var apiPath = 'order-processor'
var openAiEndpoint = deployAzureOpenAI ? 'https://${openAiName}.openai.azure.com/' : ''
var documentIntelligenceEndpoint = 'https://${documentIntelligenceName}.cognitiveservices.azure.com/'
var aiServicesEndpoint = 'https://${aiServicesName}.cognitiveservices.azure.com/'
var consoleOpenIdIssuer = '${environment().authentication.loginEndpoint}${subscription().tenantId}/v2.0'
var microsoftGraphAuthRedirectUri = 'https://${consoleWebAppName}.azurewebsites.net/auth/microsoft/callback'
var consoleAadRegistration = union({
  clientId: consoleEntraClientId
  openIdIssuer: consoleOpenIdIssuer
}, empty(consoleEntraClientCredentialSettingName) ? {} : {
  clientSecretSettingName: consoleEntraClientCredentialSettingName
})

var storageBlobDataContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var storageBlobDataOwnerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
var storageQueueDataContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
var storageTableDataContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
var storageAccountContributorRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '17d1049b-9a84-46fb-8f53-869881c3d3ab')
var keyVaultSecretsUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var keyVaultSecretsOfficerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
var cognitiveServicesUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
var cognitiveServicesOpenAiUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')

var blobContainers = [
  'email-attachments'
  'order-artifacts'
  'source-rows'
  'imports'
  'dead-letter'
]

var operationalContainers = [
  {
    name: 'tenants'
    partitionKeyPaths: [
      '/tenantId'
    ]
  }
  {
    name: 'customerAliases'
    partitionKeyPaths: [
      '/tenantId'
      '/customerId'
    ]
  }
  {
    name: 'routingRules'
    partitionKeyPaths: [
      '/tenantId'
      '/customerId'
    ]
  }
  {
    name: 'processorProfiles'
    partitionKeyPaths: [
      '/tenantId'
      '/customerId'
    ]
  }
  {
    name: 'outputProfiles'
    partitionKeyPaths: [
      '/tenantId'
      '/customerId'
    ]
  }
  {
    name: 'mailboxAccounts'
    partitionKeyPaths: [
      '/tenantId'
      '/customerId'
    ]
  }
  {
    name: 'microsoftAuthConnections'
    partitionKeyPaths: [
      '/tenantId'
      '/customerId'
    ]
  }
  {
    name: 'consoleUsers'
    partitionKeyPaths: [
      '/tenantId'
    ]
  }
  {
    name: 'customerUserAssignments'
    partitionKeyPaths: [
      '/tenantId'
      '/customerId'
    ]
  }
  {
    name: 'emailMessages'
    partitionKeyPaths: [
      '/tenantId'
    ]
  }
  {
    name: 'orderRuns'
    partitionKeyPaths: [
      '/tenantId'
    ]
  }
  {
    name: 'orderLines'
    partitionKeyPaths: [
      '/tenantId'
      '/customerId'
    ]
  }
  {
    name: 'exceptionTasks'
    partitionKeyPaths: [
      '/tenantId'
    ]
  }
  {
    name: 'auditEvents'
    partitionKeyPaths: [
      '/tenantId'
    ]
  }
]

var apiPolicyXml = '''
<policies>
  <inbound>
    <base />
    <set-header name="x-order-processor-function-key" exists-action="override">
      <value>{{function-host-key}}</value>
    </set-header>
    <set-header name="x-order-processor-ingress" exists-action="override">
      <value>api-management</value>
    </set-header>
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>
'''

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowCrossTenantReplication: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 14
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 14
    }
  }
}

resource blobContainerResources 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for containerName in blobContainers: {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}]

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource functionPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: functionPlanName
  location: location
  tags: tags
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  kind: 'functionapp'
  properties: {
    reserved: true
  }
}

resource consolePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: consolePlanName
  location: consoleLocation
  tags: tags
  sku: {
    name: consoleAppServiceSkuName
    tier: consoleAppServiceSkuTier
  }
  kind: 'linux'
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: functionPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      alwaysOn: false
      appSettings: concat([
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storageAccount.name
        }
        {
          name: 'AzureWebJobsStorage__blobServiceUri'
          value: storageAccount.properties.primaryEndpoints.blob
        }
        {
          name: 'AzureWebJobsStorage__queueServiceUri'
          value: storageAccount.properties.primaryEndpoints.queue
        }
        {
          name: 'AzureWebJobsStorage__tableServiceUri'
          value: storageAccount.properties.primaryEndpoints.table
        }
        {
          name: 'AzureWebJobsStorage__credential'
          value: 'managedidentity'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: empty(functionPackageUrl) ? 'true' : 'false'
        }
        {
          name: 'ENABLE_ORYX_BUILD'
          value: empty(functionPackageUrl) ? 'true' : 'false'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'AzureFunctionsJobHost__extensions__durableTask__hubName'
          value: durableHubName
        }
        {
          name: 'ORDER_PROCESSOR_ENV'
          value: environmentName
        }
        {
          name: 'ORDER_PROCESSOR_STORAGE_BACKEND'
          value: 'cosmos'
        }
        {
          name: 'ORDER_PROCESSOR_SOURCE_ARCHIVE_BACKEND'
          value: 'blob'
        }
        {
          name: 'COSMOS_DATABASE_NAME'
          value: cosmosDatabase.name
        }
        {
          name: 'COSMOS_ACCOUNT_ENDPOINT'
          value: cosmosAccount.properties.documentEndpoint
        }
        {
          name: 'BLOB_SERVICE_ENDPOINT'
          value: storageAccount.properties.primaryEndpoints.blob
        }
        {
          name: 'SOURCE_ROWS_STORAGE_ACCOUNT_URL'
          value: storageAccount.properties.primaryEndpoints.blob
        }
        {
          name: 'EMAIL_ATTACHMENTS_CONTAINER_NAME'
          value: 'email-attachments'
        }
        {
          name: 'ORDER_ARTIFACTS_CONTAINER_NAME'
          value: 'order-artifacts'
        }
        {
          name: 'SOURCE_ROWS_CONTAINER_NAME'
          value: 'source-rows'
        }
        {
          name: 'IMPORTS_CONTAINER_NAME'
          value: 'imports'
        }
        {
          name: 'DEAD_LETTER_CONTAINER_NAME'
          value: 'dead-letter'
        }
        {
          name: 'KEY_VAULT_URI'
          value: keyVault.properties.vaultUri
        }
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: openAiEndpoint
        }
        {
          name: 'AZURE_AI_SERVICES_ENDPOINT'
          value: aiServicesEndpoint
        }
        {
          name: 'DOCUMENT_INTELLIGENCE_ENDPOINT'
          value: documentIntelligenceEndpoint
        }
        {
          name: 'APIM_API_BASE_URL'
          value: 'https://${apiManagementName}.azure-api.net/${apiPath}'
        }
        {
          name: 'ORDER_PROCESSOR_FUNCTION_SHARED_KEY'
          value: functionSharedKey
        }
        {
          name: 'ORDER_PROCESSOR_MICROSOFT_AUTH_CLIENT_ID'
          value: microsoftGraphAuthClientId
        }
        {
          name: 'ORDER_PROCESSOR_MICROSOFT_AUTH_CLIENT_SECRET_NAME'
          value: microsoftGraphAuthClientSecretName
        }
        {
          name: 'ORDER_PROCESSOR_MICROSOFT_AUTH_TENANT_ID'
          value: microsoftGraphAuthTenantId
        }
        {
          name: 'ORDER_PROCESSOR_MICROSOFT_AUTH_SCOPES'
          value: microsoftGraphAuthScopes
        }
        {
          name: 'ORDER_PROCESSOR_MICROSOFT_AUTH_REDIRECT_URI'
          value: microsoftGraphAuthRedirectUri
        }
        {
          name: 'ORDER_PROCESSOR_MAILBOX_POLL_CRON'
          value: mailboxPollCron
        }
        {
          name: 'ORDER_PROCESSOR_GRAPH_SUBSCRIPTION_RENEWAL_CRON'
          value: graphSubscriptionRenewalCron
        }
        {
          name: 'ORDER_PROCESSOR_GRAPH_SUBSCRIPTION_AUTH_MODE'
          value: graphSubscriptionAuthMode
        }
        {
          name: 'ORDER_PROCESSOR_GRAPH_NOTIFICATION_QUEUE'
          value: graphNotificationQueueName
        }
        {
          name: 'ORDER_PROCESSOR_IMPORT_JOB_QUEUE'
          value: importJobQueueName
        }
        {
          name: 'ORDER_PROCESSOR_DEBUG_ERRORS'
          value: 'false'
        }
      ], empty(functionPackageUrl) ? [] : [
        {
          name: 'WEBSITE_RUN_FROM_PACKAGE'
          value: functionPackageUrl
        }
        {
          name: 'WEBSITE_RUN_FROM_PACKAGE_BLOB_MI_RESOURCE_ID'
          value: 'SystemAssigned'
        }
      ])
    }
  }
}

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosAccountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
    capabilities: [
      {
        name: 'EnableServerless'
      }
      {
        name: 'EnableNoSQLVectorSearch'
      }
    ]
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
  }
}

resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosAccount
  name: 'orderProcessor'
  properties: {
    resource: {
      id: 'orderProcessor'
    }
  }
}

resource operationalContainerResources 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = [for container in operationalContainers: {
  parent: cosmosDatabase
  name: container.name
  properties: {
    resource: {
      id: container.name
      partitionKey: {
        paths: container.partitionKeyPaths
        kind: length(container.partitionKeyPaths) > 1 ? 'MultiHash' : 'Hash'
        version: length(container.partitionKeyPaths) > 1 ? 2 : 1
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          {
            path: '/*'
          }
        ]
        excludedPaths: [
          {
            path: '/"_etag"/?'
          }
        ]
      }
    }
  }
}]

resource customersContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDatabase
  name: 'customers'
  properties: any({
    resource: {
      id: 'customers'
      partitionKey: {
        paths: [
          '/tenantId'
        ]
        kind: 'Hash'
      }
      vectorEmbeddingPolicy: {
        vectorEmbeddings: [
          {
            path: '/embedding'
            dataType: 'float32'
            distanceFunction: 'cosine'
            dimensions: embeddingDimensions
          }
        ]
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          {
            path: '/*'
          }
        ]
        excludedPaths: [
          {
            path: '/"_etag"/?'
          }
          {
            path: '/embedding/*'
          }
        ]
        vectorIndexes: [
          {
            path: '/embedding'
            type: 'quantizedFlat'
          }
        ]
      }
    }
  })
}

resource itemsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDatabase
  name: 'items'
  properties: any({
    resource: {
      id: 'items'
      partitionKey: {
        paths: [
          '/tenantId'
          '/customerId'
        ]
        kind: 'MultiHash'
        version: 2
      }
      vectorEmbeddingPolicy: {
        vectorEmbeddings: [
          {
            path: '/embedding'
            dataType: 'float32'
            distanceFunction: 'cosine'
            dimensions: embeddingDimensions
          }
        ]
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          {
            path: '/*'
          }
        ]
        excludedPaths: [
          {
            path: '/"_etag"/?'
          }
          {
            path: '/embedding/*'
          }
        ]
        vectorIndexes: [
          {
            path: '/embedding'
            type: 'quantizedFlat'
          }
        ]
      }
    }
  })
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    accessPolicies: []
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

resource apiManagement 'Microsoft.ApiManagement/service@2023-09-01-preview' = {
  name: apiManagementName
  location: location
  tags: tags
  sku: {
    name: 'Consumption'
    capacity: 0
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherEmail: apiPublisherEmail
    publisherName: apiPublisherName
  }
}

resource apiProduct 'Microsoft.ApiManagement/service/products@2023-09-01-preview' = {
  parent: apiManagement
  name: 'power-automate-adapters'
  properties: {
    displayName: 'Power Automate Adapters'
    description: 'Subscription-protected API surface for mailbox and customer adapter flows.'
    state: 'published'
    subscriptionRequired: true
    approvalRequired: false
  }
}

resource orderProcessorApi 'Microsoft.ApiManagement/service/apis@2023-09-01-preview' = {
  parent: apiManagement
  name: 'order-processor'
  properties: {
    displayName: 'Order Processor API'
    path: apiPath
    protocols: [
      'https'
    ]
    serviceUrl: 'https://${functionApp.properties.defaultHostName}'
    subscriptionRequired: true
    format: 'openapi+yaml'
    value: loadTextContent('openapi/order-processor-api.yaml')
  }
}

resource productApiLink 'Microsoft.ApiManagement/service/products/apis@2023-09-01-preview' = {
  parent: apiProduct
  name: orderProcessorApi.name
}

resource consoleApiSubscription 'Microsoft.ApiManagement/service/subscriptions@2023-09-01-preview' = {
  parent: apiManagement
  name: 'console-web-app'
  properties: {
    displayName: 'Order Processor Console Web App'
    scope: apiProduct.id
    state: 'active'
    allowTracing: false
  }
  dependsOn: [
    productApiLink
  ]
}

resource consoleWebApp 'Microsoft.Web/sites@2023-12-01' = {
  name: consoleWebAppName
  location: consoleLocation
  tags: tags
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: consolePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'NODE|20-lts'
      appCommandLine: 'node server.js'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      alwaysOn: true
      appSettings: [
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'ORDER_PROCESSOR_API_BASE_URL'
          value: 'https://${apiManagementName}.azure-api.net/${apiPath}'
        }
        {
          name: 'ORDER_PROCESSOR_APIM_SUBSCRIPTION_KEY'
          value: consoleApiSubscription.listSecrets().primaryKey
        }
        {
          name: 'ORDER_PROCESSOR_BOOTSTRAP_ADMIN_EMAIL'
          value: consoleBootstrapAdminEmail
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'false'
        }
      ]
    }
  }
}

resource consoleAuthSettings 'Microsoft.Web/sites/config@2023-12-01' = if (!empty(consoleEntraClientId)) {
  parent: consoleWebApp
  name: 'authsettingsV2'
  properties: any({
    platform: {
      enabled: true
      runtimeVersion: '~1'
    }
    globalValidation: {
      requireAuthentication: false
      unauthenticatedClientAction: 'AllowAnonymous'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: consoleAadRegistration
        validation: {
          allowedAudiences: [
            consoleEntraClientId
            'api://${consoleEntraClientId}'
          ]
        }
      }
    }
    login: {
      tokenStore: {
        enabled: true
      }
    }
    httpSettings: {
      requireHttps: true
    }
  })
}

resource apimFunctionKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'function-host-key-apim'
  properties: {
    value: functionSharedKey
  }
}

resource functionHostKeyNamedValue 'Microsoft.ApiManagement/service/namedValues@2023-09-01-preview' = {
  parent: apiManagement
  name: 'function-host-key'
  properties: {
    displayName: 'function-host-key'
    keyVault: {
      secretIdentifier: apimFunctionKeySecret.properties.secretUriWithVersion
    }
    secret: true
  }
  dependsOn: [
    apimKeyVaultSecretsUser
  ]
}

resource orderProcessorApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-09-01-preview' = {
  parent: orderProcessorApi
  name: 'policy'
  properties: {
    format: 'xml'
    value: apiPolicyXml
  }
  dependsOn: [
    functionHostKeyNamedValue
  ]
}

resource aiServices 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: aiServicesName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: aiServicesName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' = if (deployAzureOpenAI) {
  name: openAiName
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: openAiName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource documentIntelligence 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: documentIntelligenceName
  location: location
  tags: tags
  kind: 'FormRecognizer'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: documentIntelligenceName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource functionStorageBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.name, 'blob-data-contributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: storageBlobDataContributorRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionStorageBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.name, 'blob-data-owner')
  scope: storageAccount
  properties: {
    roleDefinitionId: storageBlobDataOwnerRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionStorageQueueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.name, 'queue-data-contributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: storageQueueDataContributorRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionStorageTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.name, 'table-data-contributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: storageTableDataContributorRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionStorageAccountContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.name, 'storage-account-contributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: storageAccountContributorRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionKeyVaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, functionApp.name, 'key-vault-secrets-user')
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionKeyVaultSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, functionApp.name, 'key-vault-secrets-officer')
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsOfficerRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource apimKeyVaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, apiManagement.name, 'key-vault-secrets-user')
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleId
    principalId: apiManagement.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAzureOpenAI) {
  name: guid(openAi.id, functionApp.name, 'openai-user')
  scope: openAi
  properties: {
    roleDefinitionId: cognitiveServicesOpenAiUserRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionDocumentIntelligenceUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(documentIntelligence.id, functionApp.name, 'cognitive-services-user')
  scope: documentIntelligence
  properties: {
    roleDefinitionId: cognitiveServicesUserRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionAiServicesUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, functionApp.name, 'cognitive-services-user')
  scope: aiServices
  properties: {
    roleDefinitionId: cognitiveServicesUserRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionCosmosDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, functionApp.name, 'cosmos-data-contributor')
  properties: {
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: functionApp.identity.principalId
    scope: cosmosAccount.id
  }
}

output functionAppName string = functionApp.name
output functionAppDefaultHostName string = functionApp.properties.defaultHostName
output consoleWebAppName string = consoleWebApp.name
output consoleWebAppDefaultHostName string = consoleWebApp.properties.defaultHostName
output consoleWebAppUrl string = 'https://${consoleWebApp.properties.defaultHostName}'
output cosmosAccountName string = cosmosAccount.name
output cosmosDatabaseName string = cosmosDatabase.name
output storageAccountName string = storageAccount.name
output apiManagementName string = apiManagement.name
output apiGatewayBaseUrl string = 'https://${apiManagement.name}.azure-api.net/${apiPath}'
output keyVaultName string = keyVault.name
output aiServicesName string = aiServices.name
output openAiName string = deployAzureOpenAI ? openAi.name : ''
output openAiEndpoint string = openAiEndpoint
output azureOpenAiDeployed bool = deployAzureOpenAI
output documentIntelligenceName string = documentIntelligence.name
output documentIntelligenceEndpoint string = documentIntelligenceEndpoint
