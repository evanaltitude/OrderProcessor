param(
    [switch]$SkipPack
)

$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$solutionRoot = Join-Path $repoRoot 'power-automate\solutions\OrderProcessor'
$templateRoot = Join-Path $solutionRoot 'flow-templates'
$dataverseRoot = Join-Path $solutionRoot 'DataverseProject'
$dataverseSrc = Join-Path $dataverseRoot 'src'
$workflowRoot = Join-Path $dataverseSrc 'Workflows'
$exportRoot = Join-Path $solutionRoot 'exports'
$logRoot = Join-Path $solutionRoot 'logs'

New-Item -ItemType Directory -Force -Path $templateRoot, $workflowRoot, $exportRoot, $logRoot | Out-Null
Get-ChildItem -Path $workflowRoot -File -Filter '*.json*' -ErrorAction SilentlyContinue | Remove-Item -Force

function ConvertTo-PrettyJson {
    param([Parameter(Mandatory = $true)] [object] $Value)
    $Value | ConvertTo-Json -Depth 100
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)] [string] $Path,
        [Parameter(Mandatory = $true)] [string] $Content
    )
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

function New-CommonParameters {
    param([bool] $IncludeMailbox = $false)

    $parameters = [ordered]@{
        '$connections' = [ordered]@{
            defaultValue = [ordered]@{}
            type = 'Object'
        }
        '$authentication' = [ordered]@{
            defaultValue = [ordered]@{}
            type = 'SecureObject'
        }
        OrderProcessorApiBaseUrl = [ordered]@{
            defaultValue = 'https://replace-with-apim-name.azure-api.net/order-processor'
            type = 'String'
        }
        OrderProcessorApimSubscriptionKey = [ordered]@{
            defaultValue = ''
            type = 'SecureString'
        }
        OrderProcessorTenantId = [ordered]@{
            defaultValue = 'altitude'
            type = 'String'
        }
    }

    if ($IncludeMailbox) {
        $parameters.OrderProcessorMailboxAddress = [ordered]@{
            defaultValue = 'customer-orders@example.com'
            type = 'String'
        }
        $parameters.OrderProcessorMailboxAccountId = [ordered]@{
            defaultValue = ''
            type = 'String'
        }
    }

    return $parameters
}

function New-HttpHeaders {
    [ordered]@{
        'Content-Type' = 'application/json'
        'Ocp-Apim-Subscription-Key' = "@parameters('OrderProcessorApimSubscriptionKey')"
    }
}

function New-MailboxTriggerDefinition {
    [ordered]@{
        properties = [ordered]@{
            connectionReferences = [ordered]@{
                shared_office365 = [ordered]@{
                    runtimeSource = 'embedded'
                    connection = [ordered]@{
                        connectionReferenceLogicalName = 'alt_sharedoffice365_orderprocessor'
                    }
                    api = [ordered]@{
                        name = 'shared_office365'
                    }
                }
            }
            definition = [ordered]@{
                '$schema' = 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
                contentVersion = '1.0.0.0'
                parameters = New-CommonParameters -IncludeMailbox $true
                triggers = [ordered]@{
                    When_a_new_email_arrives_in_configured_shared_mailbox = [ordered]@{
                        recurrence = [ordered]@{
                            frequency = 'Minute'
                            interval = 1
                        }
                        metadata = [ordered]@{
                            operationMetadataId = '10000000-0000-4000-8000-000000000001'
                        }
                        type = 'OpenApiConnection'
                        inputs = [ordered]@{
                            host = [ordered]@{
                                apiId = '/providers/Microsoft.PowerApps/apis/shared_office365'
                                connectionName = 'shared_office365'
                                operationId = 'SharedMailboxOnNewEmailV2'
                            }
                            parameters = [ordered]@{
                                mailboxAddress = "@parameters('OrderProcessorMailboxAddress')"
                                folderId = 'Inbox'
                                includeAttachments = $true
                                importance = 'Any'
                                fetchOnlyWithAttachment = $false
                            }
                            authentication = "@parameters('$authentication')"
                        }
                    }
                }
                actions = [ordered]@{
                    Post_Email_Metadata_To_Order_Processor = [ordered]@{
                        runAfter = [ordered]@{}
                        type = 'Http'
                        inputs = [ordered]@{
                            method = 'POST'
                            uri = "@{concat(parameters('OrderProcessorApiBaseUrl'), '/emails/ingest')}"
                            headers = New-HttpHeaders
                            body = [ordered]@{
                                tenantId = "@{parameters('OrderProcessorTenantId')}"
                                mailbox = "@{parameters('OrderProcessorMailboxAddress')}"
                                mailboxAccountId = "@{parameters('OrderProcessorMailboxAccountId')}"
                                messageId = "@{triggerOutputs()?['body/id']}"
                                sender = "@{coalesce(triggerOutputs()?['body/from/emailAddress/address'], triggerOutputs()?['body/from'])}"
                                subject = "@{triggerOutputs()?['body/subject']}"
                                receivedAt = "@{triggerOutputs()?['body/receivedDateTime']}"
                                bodyText = "@{triggerOutputs()?['body/bodyPreview']}"
                                bodyHtml = "@{triggerOutputs()?['body/body']}"
                                attachments = "@{triggerOutputs()?['body/attachments']}"
                            }
                        }
                    }
                    Stop_On_Ingest_Failure = [ordered]@{
                        runAfter = [ordered]@{
                            Post_Email_Metadata_To_Order_Processor = @('Failed', 'TimedOut')
                        }
                        type = 'Terminate'
                        inputs = [ordered]@{
                            runStatus = 'Failed'
                            runError = [ordered]@{
                                code = 'orderProcessorIngestFailed'
                                message = 'APIM /emails/ingest call failed. Check APIM, Function, and mailbox configuration.'
                            }
                        }
                    }
                }
                outputs = [ordered]@{}
                templateName = $null
            }
        }
        schemaVersion = '1.0.0.0'
    }
}

function New-RequestAdapterDefinition {
    param(
        [Parameter(Mandatory = $true)] [string] $Endpoint,
        [Parameter(Mandatory = $true)] [string] $OperationName,
        [Parameter(Mandatory = $true)] [hashtable] $Schema,
        [Parameter(Mandatory = $true)] [string] $BodyExpression
    )

    [ordered]@{
        properties = [ordered]@{
            connectionReferences = [ordered]@{}
            definition = [ordered]@{
                '$schema' = 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
                contentVersion = '1.0.0.0'
                parameters = New-CommonParameters
                triggers = [ordered]@{
                    manual = [ordered]@{
                        type = 'Request'
                        kind = 'Http'
                        inputs = [ordered]@{
                            schema = $Schema
                            triggerAuthenticationType = 'All'
                        }
                    }
                }
                actions = [ordered]@{
                    "Post_To_Order_Processor_$OperationName" = [ordered]@{
                        runAfter = [ordered]@{}
                        type = 'Http'
                        inputs = [ordered]@{
                            method = 'POST'
                            uri = "@{concat(parameters('OrderProcessorApiBaseUrl'), '$Endpoint')}"
                            headers = New-HttpHeaders
                            body = $BodyExpression
                        }
                    }
                    Respond_Accepted = [ordered]@{
                        runAfter = [ordered]@{
                            "Post_To_Order_Processor_$OperationName" = @('Succeeded')
                        }
                        type = 'Response'
                        kind = 'Http'
                        inputs = [ordered]@{
                            statusCode = 202
                            body = [ordered]@{
                                accepted = $true
                                endpoint = $Endpoint
                                backendResponse = "@{body('Post_To_Order_Processor_$OperationName')}"
                            }
                        }
                    }
                    Stop_On_Backend_Failure = [ordered]@{
                        runAfter = [ordered]@{
                            "Post_To_Order_Processor_$OperationName" = @('Failed', 'TimedOut')
                        }
                        type = 'Terminate'
                        inputs = [ordered]@{
                            runStatus = 'Failed'
                            runError = [ordered]@{
                                code = 'orderProcessorAdapterFailed'
                                message = "APIM $Endpoint call failed."
                            }
                        }
                    }
                }
                outputs = [ordered]@{}
                templateName = $null
            }
        }
        schemaVersion = '1.0.0.0'
    }
}

function New-OutputAdapterDefinition {
    $schema = [ordered]@{
        type = 'object'
        required = @('tenantId', 'customerId', 'orderRunId')
        properties = [ordered]@{
            tenantId = [ordered]@{ type = 'string' }
            customerId = [ordered]@{ type = 'string' }
            orderRunId = [ordered]@{ type = 'string' }
            deliveryMode = [ordered]@{ type = 'string' }
            destination = [ordered]@{ type = 'object' }
            outputArtifacts = [ordered]@{
                type = 'array'
                items = [ordered]@{ type = 'object' }
            }
        }
    }

    [ordered]@{
        properties = [ordered]@{
            connectionReferences = [ordered]@{}
            definition = [ordered]@{
                '$schema' = 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
                contentVersion = '1.0.0.0'
                parameters = New-CommonParameters
                triggers = [ordered]@{
                    manual = [ordered]@{
                        type = 'Request'
                        kind = 'Http'
                        inputs = [ordered]@{
                            schema = $schema
                            triggerAuthenticationType = 'All'
                        }
                    }
                }
                actions = [ordered]@{
                    Customer_Specific_M365_Delivery_Placeholder = [ordered]@{
                        runAfter = [ordered]@{}
                        type = 'Compose'
                        inputs = [ordered]@{
                            note = 'Add customer-specific M365 delivery actions here only when delivery must stay in Power Automate.'
                            tenantId = "@{triggerBody()?['tenantId']}"
                            customerId = "@{triggerBody()?['customerId']}"
                            orderRunId = "@{triggerBody()?['orderRunId']}"
                            deliveryMode = "@{triggerBody()?['deliveryMode']}"
                            destination = "@{triggerBody()?['destination']}"
                            outputArtifacts = "@{triggerBody()?['outputArtifacts']}"
                        }
                    }
                    Respond_Delivery_Template_Accepted = [ordered]@{
                        runAfter = [ordered]@{
                            Customer_Specific_M365_Delivery_Placeholder = @('Succeeded')
                        }
                        type = 'Response'
                        kind = 'Http'
                        inputs = [ordered]@{
                            statusCode = 202
                            body = [ordered]@{
                                accepted = $true
                                orderRunId = "@{triggerBody()?['orderRunId']}"
                                note = 'Template invoked. Configure actual M365 delivery actions per customer output profile.'
                            }
                        }
                    }
                }
                outputs = [ordered]@{}
                templateName = $null
            }
        }
        schemaVersion = '1.0.0.0'
    }
}

$customerImportSchema = [ordered]@{
    type = 'object'
    required = @('tenantId', 'rows')
    properties = [ordered]@{
        tenantId = [ordered]@{ type = 'string' }
        sourceName = [ordered]@{ type = 'string' }
        fieldMap = [ordered]@{ type = 'object' }
        rows = [ordered]@{
            type = 'array'
            items = [ordered]@{ type = 'object' }
        }
        sourceMetadata = [ordered]@{ type = 'object' }
    }
}

$itemImportSchema = [ordered]@{
    type = 'object'
    required = @('tenantId', 'rows')
    properties = [ordered]@{
        tenantId = [ordered]@{ type = 'string' }
        customerId = [ordered]@{ type = 'string' }
        customerCode = [ordered]@{ type = 'string' }
        sourceName = [ordered]@{ type = 'string' }
        fieldMap = [ordered]@{ type = 'object' }
        rows = [ordered]@{
            type = 'array'
            items = [ordered]@{ type = 'object' }
        }
        sourceMetadata = [ordered]@{ type = 'object' }
    }
}

$flows = @(
    [ordered]@{
        name = 'OrderProcessor - Mailbox Trigger Template'
        slug = 'orderprocessor-mailbox-trigger-template'
        workflowId = '7f52d9d6-8eb1-4ad7-b2f6-89dd55dc4e01'
        fileStem = 'OP-MailboxTrigger-7F52D9D6-8EB1-4AD7-B2F6-89DD55DC4E01'
        trigger = 'Office 365 Outlook shared mailbox new-email trigger'
        endpoint = 'POST /emails/ingest'
        status = 'disabled template'
        connectionReferences = @('shared_office365')
        definition = New-MailboxTriggerDefinition
        notes = @(
            'Instantiate one copy per monitored distributor mailbox when Power Automate must own the mailbox trigger.',
            'Mailbox address and mailboxAccountId are tenant configuration values, not downstream customer branches.',
            'Flow sends metadata and attachment references to APIM only; Azure performs routing and processing.'
        )
    }
    [ordered]@{
        name = 'OrderProcessor - Customer Import Adapter Template'
        slug = 'orderprocessor-customer-import-adapter-template'
        workflowId = '06b7f4fc-5f13-4a6f-8742-03f19c301902'
        fileStem = 'OP-CustomerImport-06B7F4FC-5F13-4A6F-8742-03F19C301902'
        trigger = 'HTTP request from customer-specific file/source adapter'
        endpoint = 'POST /imports/customers'
        status = 'disabled template'
        connectionReferences = @()
        definition = New-RequestAdapterDefinition -Endpoint '/imports/customers' -OperationName 'Customer_Import' -Schema $customerImportSchema -BodyExpression "@triggerBody()"
        notes = @(
            'Use only when a customer source file must be received through M365 or another Power Automate-owned source.',
            'Flow forwards normalized rows/source metadata to APIM; Azure owns parsing, validation, and Cosmos writes.'
        )
    }
    [ordered]@{
        name = 'OrderProcessor - Item Import Adapter Template'
        slug = 'orderprocessor-item-import-adapter-template'
        workflowId = '7aa4ab3f-d509-4866-98a0-fcce8dc79b03'
        fileStem = 'OP-ItemImport-7AA4AB3F-D509-4866-98A0-FCCE8DC79B03'
        trigger = 'HTTP request from distributor item file/source adapter'
        endpoint = 'POST /imports/items'
        status = 'disabled template'
        connectionReferences = @()
        definition = New-RequestAdapterDefinition -Endpoint '/imports/items' -OperationName 'Item_Import' -Schema $itemImportSchema -BodyExpression "@triggerBody()"
        notes = @(
            'Use only when item refresh input must originate in Power Automate.',
            'Omit customerId/customerCode for the distributor master item catalog; provide customerCode only for a customer-specific override list.',
            'Flow forwards source rows/metadata to APIM; Azure owns parsing, item normalization, embeddings, and Cosmos writes.'
        )
    }
    [ordered]@{
        name = 'OrderProcessor - Output Delivery Adapter Template'
        slug = 'orderprocessor-output-delivery-adapter-template'
        workflowId = '92df442b-2360-42dd-b787-ce339813d877'
        fileStem = 'OP-OutputDelivery-92DF442B-2360-42DD-B787-CE339813D877'
        trigger = 'HTTP request from Azure completion callback or scheduled adapter poll'
        endpoint = 'customer-specific M365 delivery'
        status = 'disabled optional template'
        connectionReferences = @()
        definition = New-OutputAdapterDefinition
        notes = @(
            'Use only when customer output delivery must remain in M365 or Power Automate.',
            'Default platform output generation belongs in Azure. This template is a placeholder for customer-specific delivery actions.'
        )
    }
)

foreach ($flow in $flows) {
    $flowDir = Join-Path $templateRoot $flow.slug
    New-Item -ItemType Directory -Force -Path $flowDir | Out-Null

    $definitionJson = ConvertTo-PrettyJson $flow.definition
    Write-Utf8NoBom -Path (Join-Path $flowDir 'definition.json') -Content ($definitionJson + [Environment]::NewLine)
    Write-Utf8NoBom -Path (Join-Path $workflowRoot "$($flow.fileStem).json") -Content ($definitionJson + [Environment]::NewLine)

    $metadata = [ordered]@{
        name = $flow.name
        slug = $flow.slug
        workflowId = $flow.workflowId
        trigger = $flow.trigger
        endpoint = $flow.endpoint
        status = $flow.status
        connectionReferences = $flow.connectionReferences
        notes = $flow.notes
    }
    Write-Utf8NoBom -Path (Join-Path $flowDir 'metadata.json') -Content ((ConvertTo-PrettyJson $metadata) + [Environment]::NewLine)

    $readme = @"
# $($flow.name)

- Status: $($flow.status)
- Trigger: $($flow.trigger)
- Azure/APIM target: $($flow.endpoint)
- Workflow ID: $($flow.workflowId)

## Notes

$($flow.notes | ForEach-Object { "- $_" } | Out-String)
## Configuration

- OrderProcessorApiBaseUrl: APIM base URL, for example https://{apim-name}.azure-api.net/order-processor.
- OrderProcessorApimSubscriptionKey: APIM subscription key or future custom connector secret.
- OrderProcessorTenantId: platform tenant identifier.
- Mailbox templates additionally require OrderProcessorMailboxAddress and OrderProcessorMailboxAccountId.

Do not add parsing, customer identification, item validation, OpenAI, Google Document AI, Plumsail, SharePoint operational storage, or flow-to-flow webhook calls to this flow.
"@
    Write-Utf8NoBom -Path (Join-Path $flowDir 'README.md') -Content $readme

    $workflowXml = @"
<?xml version="1.0" encoding="utf-8"?>
<Workflow WorkflowId="{$($flow.workflowId)}" Name="$($flow.name)" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <JsonFileName>/Workflows/$($flow.fileStem).json</JsonFileName>
  <Type>1</Type>
  <Subprocess>0</Subprocess>
  <Category>5</Category>
  <Mode>0</Mode>
  <Scope>4</Scope>
  <OnDemand>0</OnDemand>
  <TriggerOnCreate>0</TriggerOnCreate>
  <TriggerOnDelete>0</TriggerOnDelete>
  <AsyncAutodelete>0</AsyncAutodelete>
  <SyncWorkflowLogOnFailure>0</SyncWorkflowLogOnFailure>
  <StateCode>1</StateCode>
  <StatusCode>2</StatusCode>
  <RunAs>1</RunAs>
  <IsTransacted>1</IsTransacted>
  <IntroducedVersion>1.0.0.0</IntroducedVersion>
  <IsCustomizable>1</IsCustomizable>
  <BusinessProcessType>0</BusinessProcessType>
  <IsCustomProcessingStepAllowedForOtherPublishers>1</IsCustomProcessingStepAllowedForOtherPublishers>
  <ModernFlowType>0</ModernFlowType>
  <PrimaryEntity>none</PrimaryEntity>
  <LocalizedNames>
    <LocalizedName languagecode="1033" description="$($flow.name)" />
  </LocalizedNames>
</Workflow>
"@
    Write-Utf8NoBom -Path (Join-Path $workflowRoot "$($flow.fileStem).json.data.xml") -Content $workflowXml
}

$manifest = [ordered]@{
    solution = [ordered]@{
        uniqueName = 'OrderProcessor'
        displayName = 'Order Processor'
        targetEnvironmentId = 'abbd708f-4eaf-e875-a282-e1207f4e370c'
        targetEnvironmentName = 'Focus Automate'
        owner = 'evanb@altitudelogistics.com'
        version = '1.0.0.0'
    }
    principles = @(
        'Power Automate is a thin adapter layer.',
        'All backend calls go through API Management.',
        'Complex parsing, customer identification, item validation, output generation, and persistence belong in Azure.',
        'Mailbox/tenant configuration and downstream customer identification rules are backend/console data, not flow branching.'
    )
    flows = $flows | ForEach-Object {
        [ordered]@{
            name = $_.name
            slug = $_.slug
            workflowId = $_.workflowId
            trigger = $_.trigger
            endpoint = $_.endpoint
            status = $_.status
            connectionReferences = $_.connectionReferences
        }
    }
    generatedAt = (Get-Date).ToString('o')
}
Write-Utf8NoBom -Path (Join-Path $solutionRoot 'shell-solution-manifest.json') -Content ((ConvertTo-PrettyJson $manifest) + [Environment]::NewLine)

$catalogRows = $flows | ForEach-Object {
    "| $($_.name) | $($_.status) | $($_.trigger) | $($_.endpoint) | $((($_.connectionReferences) -join ', ')) |"
}
$catalog = @"
# OrderProcessor Shell Flow Catalog

The OrderProcessor Power Automate solution intentionally contains only mailbox/customer adapter flows. These flows are templates and should stay disabled until APIM, mailbox configuration, and connection references are configured for a distributor tenant.

| Flow | Status | Trigger | Target | Connection references |
| --- | --- | --- | --- | --- |
$($catalogRows -join [Environment]::NewLine)

## Guardrails

- Call APIM through OrderProcessorApiBaseUrl; never call raw Azure Function URLs.
- Do not add OpenAI, Google Document AI, Plumsail, SharePoint operational storage, item validation, customer identification, parser, or output-generation logic to these flows.
- Instantiate mailbox trigger templates per monitored mailbox only when Power Automate must own the event trigger.
- Keep mailbox addresses, Microsoft connection metadata, and customer/user assignments in backend configuration and the console.
- Keep APIM subscription material in secure configuration or a future custom connector connection, not hard-coded in flow actions.
"@
Write-Utf8NoBom -Path (Join-Path $solutionRoot 'FLOW_TEMPLATE_CATALOG.md') -Content $catalog

$configurationContract = @"
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
"@
Write-Utf8NoBom -Path (Join-Path $solutionRoot 'CONFIGURATION_CONTRACT.md') -Content $configurationContract

$solutionXmlPath = Join-Path $dataverseSrc 'Other\Solution.xml'
[xml]$solutionXml = Get-Content -Path $solutionXmlPath
$manifestNode = $solutionXml.SelectSingleNode('/ImportExportXml/SolutionManifest')
$rootComponentsNode = $solutionXml.SelectSingleNode('/ImportExportXml/SolutionManifest/RootComponents')
if ($null -eq $rootComponentsNode) {
    $rootComponentsNode = $solutionXml.CreateElement('RootComponents')
    [void]$manifestNode.AppendChild($rootComponentsNode)
}

$existingIds = @($flows | ForEach-Object { $_.workflowId.ToLowerInvariant() })
@($rootComponentsNode.RootComponent) | Where-Object { $null -ne $_ } | ForEach-Object {
    $id = $_.GetAttribute('id').Trim('{}').ToLowerInvariant()
    if ($existingIds -contains $id) {
        [void]$rootComponentsNode.RemoveChild($_)
    }
}

foreach ($flow in $flows) {
    $rootComponent = $solutionXml.CreateElement('RootComponent')
    $rootComponent.SetAttribute('type', '29')
    $rootComponent.SetAttribute('id', "{$($flow.workflowId)}")
    $rootComponent.SetAttribute('behavior', '0')
    [void]$rootComponentsNode.AppendChild($rootComponent)
}
$solutionXml.Save($solutionXmlPath)

$customizationsXmlPath = Join-Path $dataverseSrc 'Other\Customizations.xml'
[xml]$customizationsXml = Get-Content -Path $customizationsXmlPath
$importExportNode = $customizationsXml.SelectSingleNode('/ImportExportXml')
$connectionReferencesNode = $customizationsXml.SelectSingleNode('/ImportExportXml/connectionreferences')
if ($null -eq $connectionReferencesNode) {
    $connectionReferencesNode = $customizationsXml.CreateElement('connectionreferences')
    $languagesNode = $customizationsXml.SelectSingleNode('/ImportExportXml/Languages')
    if ($null -ne $languagesNode) {
        [void]$importExportNode.InsertBefore($connectionReferencesNode, $languagesNode)
    } else {
        [void]$importExportNode.AppendChild($connectionReferencesNode)
    }
}

$outlookConnectionReferenceName = 'alt_sharedoffice365_orderprocessor'
@($connectionReferencesNode.connectionreference) |
    Where-Object { $null -ne $_ -and $_.GetAttribute('connectionreferencelogicalname') -eq $outlookConnectionReferenceName } |
    ForEach-Object { [void]$connectionReferencesNode.RemoveChild($_) }

$outlookConnectionReference = $customizationsXml.CreateElement('connectionreference')
$outlookConnectionReference.SetAttribute('connectionreferencelogicalname', $outlookConnectionReferenceName)
foreach ($item in @(
    @{ name = 'connectionreferencedisplayname'; value = 'Office 365 Outlook OrderProcessor' },
    @{ name = 'connectorid'; value = '/providers/Microsoft.PowerApps/apis/shared_office365' },
    @{ name = 'iscustomizable'; value = '1' },
    @{ name = 'promptingbehavior'; value = '0' },
    @{ name = 'statecode'; value = '0' },
    @{ name = 'statuscode'; value = '1' }
)) {
    $node = $customizationsXml.CreateElement($item.name)
    $node.InnerText = $item.value
    [void]$outlookConnectionReference.AppendChild($node)
}
[void]$connectionReferencesNode.AppendChild($outlookConnectionReference)
$customizationsXml.Save($customizationsXmlPath)

if (-not $SkipPack) {
    $zipPath = Join-Path $exportRoot 'OrderProcessor_1.0.0.0_unmanaged.zip'
    $logPath = Join-Path $logRoot 'pack.log'
    if (Test-Path $logPath) {
        Remove-Item -LiteralPath $logPath -Force
    }
    pac solution pack --zipfile $zipPath --folder $dataverseSrc --packagetype Unmanaged --log $logPath --allowWrite --clobber | Out-Host
}

Write-Host "Generated $($flows.Count) OrderProcessor shell flow templates."
