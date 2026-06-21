param(
    [string]$SolutionRoot = ".\power-automate\solutions\OrdersAutomations"
)

$ErrorActionPreference = "Stop"

function ConvertTo-Slug {
    param([string]$Value)

    $slug = $Value.ToLowerInvariant()
    $slug = $slug -replace "[^a-z0-9]+", "-"
    $slug = $slug.Trim("-")

    if ([string]::IsNullOrWhiteSpace($slug)) {
        return "unnamed"
    }

    return $slug
}

function Get-WorkflowName {
    param([string]$JsonPath)

    $metadataPath = "$JsonPath.data.xml"
    if (-not (Test-Path -LiteralPath $metadataPath)) {
        return [System.IO.Path]::GetFileNameWithoutExtension($JsonPath)
    }

    [xml]$metadata = Get-Content -LiteralPath $metadataPath -Raw
    return [string]$metadata.Workflow.Name
}

function Get-ConnectorName {
    param(
        [string]$ApiId,
        [string]$ConnectionName
    )

    if (-not [string]::IsNullOrWhiteSpace($ApiId)) {
        return Split-Path $ApiId -Leaf
    }

    if (-not [string]::IsNullOrWhiteSpace($ConnectionName)) {
        return $ConnectionName
    }

    return ""
}

function Test-SensitiveName {
    param([string]$Name)

    return $Name -match "(?i)(authorization|password|secret|token|access[_-]?token|refresh[_-]?token|api[_-]?key|apikey|client[_-]?secret|sig|signature|connectionstring|connection[_-]?string)"
}

function ConvertTo-RedactedUrl {
    param([string]$Url)

    if ([string]::IsNullOrWhiteSpace($Url) -or $Url -notmatch "^https?://") {
        return $Url
    }

    $parts = $Url -split "\?", 2
    if ($parts.Count -lt 2) {
        return $Url
    }

    $queryParts = New-Object System.Collections.Generic.List[string]
    foreach ($pair in ($parts[1] -split "&")) {
        if ([string]::IsNullOrWhiteSpace($pair)) {
            continue
        }

        $keyValue = $pair -split "=", 2
        $key = $keyValue[0]
        $value = if ($keyValue.Count -gt 1) { $keyValue[1] } else { "" }

        if (Test-SensitiveName -Name $key) {
            $value = "[REDACTED]"
        }

        if ($keyValue.Count -gt 1) {
            $queryParts.Add("$key=$value")
        }
        else {
            $queryParts.Add($key)
        }
    }

    return "$($parts[0])?$($queryParts -join '&')"
}

function ConvertTo-SafeString {
    param(
        [string]$Value,
        [string]$Name
    )

    if (Test-SensitiveName -Name $Name) {
        return "[REDACTED]"
    }

    $urlPattern = "https?://[^\s'""<>]+"
    return [regex]::Replace($Value, $urlPattern, {
        param($match)
        ConvertTo-RedactedUrl -Url $match.Value
    })
}

function ConvertTo-SafeObject {
    param(
        [object]$Value,
        [string]$Name = ""
    )

    if ($null -eq $Value) {
        return $null
    }

    if ($Value -is [string]) {
        return ConvertTo-SafeString -Value $Value -Name $Name
    }

    if ($Value -is [bool] -or $Value -is [int] -or $Value -is [long] -or $Value -is [double] -or $Value -is [decimal]) {
        return $Value
    }

    if ($Value -is [System.Collections.IDictionary]) {
        $hash = [ordered]@{}
        foreach ($key in $Value.Keys) {
            $hash[[string]$key] = ConvertTo-SafeObject -Value $Value[$key] -Name ([string]$key)
        }

        return [pscustomobject]$hash
    }

    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
        $items = New-Object System.Collections.Generic.List[object]
        foreach ($item in $Value) {
            $items.Add((ConvertTo-SafeObject -Value $item -Name $Name))
        }

        return @($items.ToArray())
    }

    $objectHash = [ordered]@{}
    foreach ($property in $Value.PSObject.Properties) {
        $objectHash[$property.Name] = ConvertTo-SafeObject -Value $property.Value -Name $property.Name
    }

    return [pscustomobject]$objectHash
}

function Find-Urls {
    param(
        [object]$Value,
        [string]$Path = ""
    )

    $urls = New-Object System.Collections.Generic.List[object]

    function Visit {
        param(
            [object]$Node,
            [string]$NodePath
        )

        if ($null -eq $Node) {
            return
        }

        if ($Node -is [string]) {
            $urlPattern = "https?://[^\s'""<>]+"
            foreach ($match in [regex]::Matches($Node, $urlPattern)) {
                $rawUrl = $match.Value
                $hostName = ""
                $pathValue = ""
                $queryKeys = @()

                try {
                    $uri = [uri](ConvertTo-RedactedUrl -Url $rawUrl)
                    $hostName = $uri.Host
                    $pathValue = $uri.AbsolutePath
                    $queryKeys = @(
                        $uri.Query.TrimStart("?").Split("&") |
                            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                            ForEach-Object { ($_ -split "=", 2)[0] }
                    )
                }
                catch {
                    $hostName = ""
                    $pathValue = ""
                    $queryKeys = @()
                }

                $urls.Add([pscustomobject]@{
                    propertyPath = $NodePath
                    redactedUrl = ConvertTo-RedactedUrl -Url $rawUrl
                    host = $hostName
                    path = $pathValue
                    queryKeys = $queryKeys
                })
            }

            return
        }

        if ($Node -is [System.Collections.IDictionary]) {
            foreach ($key in $Node.Keys) {
                $childPath = if ([string]::IsNullOrWhiteSpace($NodePath)) { [string]$key } else { "$NodePath.$key" }
                Visit -Node $Node[$key] -NodePath $childPath
            }

            return
        }

        if ($Node -is [System.Collections.IEnumerable] -and $Node -isnot [string]) {
            $index = 0
            foreach ($item in $Node) {
                Visit -Node $item -NodePath "$($NodePath)[$index]"
                $index++
            }

            return
        }

        foreach ($property in $Node.PSObject.Properties) {
            $childPath = if ([string]::IsNullOrWhiteSpace($NodePath)) { $property.Name } else { "$NodePath.$($property.Name)" }
            Visit -Node $property.Value -NodePath $childPath
        }
    }

    Visit -Node $Value -NodePath $Path
    return @($urls.ToArray())
}

function Get-EndpointCategory {
    param(
        [string]$HostName,
        [string]$Path
    )

    switch -Regex ($HostName) {
        "^api\.openai\.com$" { return "OpenAI API" }
        "logic\.azure\.com$" { return "Azure Logic Apps / Power Automate webhook" }
        "environment\.api\.powerplatform\.com$" { return "Power Platform direct workflow webhook" }
        "^graph\.microsoft\.com$" { return "Microsoft Graph" }
        "sharepoint\.com$" { return "SharePoint / OneDrive" }
        "googleapis\.com$" { return "Google API" }
        default {
            if ($Path -match "powerautomate|workflows|triggers") {
                return "Workflow webhook"
            }

            return "Other HTTP endpoint"
        }
    }
}

function Get-NamedValues {
    param(
        [object]$Value,
        [string[]]$Names
    )

    $results = New-Object System.Collections.Generic.List[object]

    function Visit {
        param(
            [object]$Node,
            [string]$NodePath
        )

        if ($null -eq $Node) {
            return
        }

        if ($Node -is [string] -or $Node -is [bool] -or $Node -is [int] -or $Node -is [long] -or $Node -is [double] -or $Node -is [decimal]) {
            return
        }

        if ($Node -is [System.Collections.IDictionary]) {
            foreach ($key in $Node.Keys) {
                $childPath = if ([string]::IsNullOrWhiteSpace($NodePath)) { [string]$key } else { "$NodePath.$key" }
                if ($Names -contains [string]$key) {
                    $results.Add([pscustomobject]@{
                        path = $childPath
                        name = [string]$key
                        value = ConvertTo-SafeObject -Value $Node[$key] -Name ([string]$key)
                    })
                }
                Visit -Node $Node[$key] -NodePath $childPath
            }
            return
        }

        if ($Node -is [System.Collections.IEnumerable] -and $Node -isnot [string]) {
            $index = 0
            foreach ($item in $Node) {
                Visit -Node $item -NodePath "$($NodePath)[$index]"
                $index++
            }
            return
        }

        foreach ($property in $Node.PSObject.Properties) {
            $childPath = if ([string]::IsNullOrWhiteSpace($NodePath)) { $property.Name } else { "$NodePath.$($property.Name)" }
            if ($Names -contains $property.Name) {
                $results.Add([pscustomobject]@{
                    path = $childPath
                    name = $property.Name
                    value = ConvertTo-SafeObject -Value $property.Value -Name $property.Name
                })
            }
            Visit -Node $property.Value -NodePath $childPath
        }
    }

    Visit -Node $Value -NodePath ""
    return @($results.ToArray())
}

function Add-NodeInventory {
    param(
        [System.Collections.Generic.List[object]]$Actions,
        [System.Collections.Generic.List[object]]$Endpoints,
        [System.Collections.Generic.List[object]]$RequestTriggers,
        [string]$FlowName,
        [string]$FlowFile,
        [string]$NodeName,
        [string]$NodeKind,
        [string]$NodePath,
        [object]$Node
    )

    $apiId = ""
    $connectionName = ""
    $operationId = ""
    $connectorName = ""

    if ($Node.inputs -and $Node.inputs.host) {
        $apiId = [string]$Node.inputs.host.apiId
        $connectionName = [string]$Node.inputs.host.connectionName
        $operationId = [string]$Node.inputs.host.operationId
        $connectorName = Get-ConnectorName -ApiId $apiId -ConnectionName $connectionName
    }

    if (-not [string]::IsNullOrWhiteSpace($connectorName)) {
        $parameters = if ($Node.inputs) { $Node.inputs.parameters } else { $null }
        $Actions.Add([pscustomobject]@{
            flowName = $FlowName
            flowFile = $FlowFile
            nodeName = $NodeName
            nodeKind = $NodeKind
            nodePath = $NodePath
            nodeType = [string]$Node.type
            connector = $connectorName
            apiId = $apiId
            connectionName = $connectionName
            operationId = $operationId
            method = if ($Node.inputs -and $Node.inputs.method) { [string]$Node.inputs.method } elseif ($parameters -and $parameters.method) { [string]$parameters.method } else { "" }
            parameterKeys = if ($parameters) { @($parameters.PSObject.Properties.Name | Sort-Object) } else { @() }
            parameters = ConvertTo-SafeObject -Value $parameters
        })
    }

    if ($NodeKind -eq "trigger" -and [string]$Node.type -eq "Request") {
        $RequestTriggers.Add([pscustomobject]@{
            flowName = $FlowName
            flowFile = $FlowFile
            nodeName = $NodeName
            nodePath = $NodePath
            kind = [string]$Node.kind
            schema = ConvertTo-SafeObject -Value $Node.inputs.schema
            triggerAuthenticationType = if ($Node.inputs -and $Node.inputs.triggerAuthenticationType) { [string]$Node.inputs.triggerAuthenticationType } else { "" }
        })
    }

    $nodeForUrlScan = [pscustomobject]@{
        type = $Node.type
        kind = $Node.kind
        inputs = $Node.inputs
        metadata = $Node.metadata
        runtimeConfiguration = $Node.runtimeConfiguration
    }

    foreach ($url in (Find-Urls -Value $nodeForUrlScan -Path "")) {
        $Endpoints.Add([pscustomobject]@{
            flowName = $FlowName
            flowFile = $FlowFile
            nodeName = $NodeName
            nodeKind = $NodeKind
            nodePath = $NodePath
            nodeType = [string]$Node.type
            connector = $connectorName
            operationId = $operationId
            propertyPath = $url.propertyPath
            category = Get-EndpointCategory -HostName $url.host -Path $url.path
            host = $url.host
            path = $url.path
            queryKeys = $url.queryKeys
            redactedUrl = $url.redactedUrl
            method = if ($Node.inputs -and $Node.inputs.method) { [string]$Node.inputs.method } elseif ($Node.inputs -and $Node.inputs.parameters -and $Node.inputs.parameters.method) { [string]$Node.inputs.parameters.method } else { "" }
            notableFields = Get-NamedValues -Value $nodeForUrlScan -Names @("model", "assistant_id", "thread_id", "file_id", "vector_store_id", "vector_store_ids", "form_id", "formId")
        })
    }
}

function Visit-WorkflowNodes {
    param(
        [System.Collections.Generic.List[object]]$Actions,
        [System.Collections.Generic.List[object]]$Endpoints,
        [System.Collections.Generic.List[object]]$RequestTriggers,
        [string]$FlowName,
        [string]$FlowFile,
        [string]$NodeKind,
        [string]$Path,
        [object]$Nodes
    )

    if ($null -eq $Nodes) {
        return
    }

    foreach ($property in $Nodes.PSObject.Properties) {
        $nodeName = $property.Name
        $node = $property.Value
        $nodePath = if ([string]::IsNullOrWhiteSpace($Path)) { $nodeName } else { "$Path/$nodeName" }

        Add-NodeInventory -Actions $Actions -Endpoints $Endpoints -RequestTriggers $RequestTriggers -FlowName $FlowName -FlowFile $FlowFile -NodeName $nodeName -NodeKind $NodeKind -NodePath $nodePath -Node $node

        if ($node.actions) {
            Visit-WorkflowNodes -Actions $Actions -Endpoints $Endpoints -RequestTriggers $RequestTriggers -FlowName $FlowName -FlowFile $FlowFile -NodeKind "action" -Path "$nodePath/actions" -Nodes $node.actions
        }

        if ($node.else -and $node.else.actions) {
            Visit-WorkflowNodes -Actions $Actions -Endpoints $Endpoints -RequestTriggers $RequestTriggers -FlowName $FlowName -FlowFile $FlowFile -NodeKind "action" -Path "$nodePath/else/actions" -Nodes $node.else.actions
        }

        if ($node.cases) {
            foreach ($case in $node.cases.PSObject.Properties) {
                if ($case.Value.actions) {
                    Visit-WorkflowNodes -Actions $Actions -Endpoints $Endpoints -RequestTriggers $RequestTriggers -FlowName $FlowName -FlowFile $FlowFile -NodeKind "action" -Path "$nodePath/cases/$($case.Name)/actions" -Nodes $case.Value.actions
                }
            }
        }

        if ($node.default -and $node.default.actions) {
            Visit-WorkflowNodes -Actions $Actions -Endpoints $Endpoints -RequestTriggers $RequestTriggers -FlowName $FlowName -FlowFile $FlowFile -NodeKind "action" -Path "$nodePath/default/actions" -Nodes $node.default.actions
        }
    }
}

function Read-ConnectionReferences {
    param([string]$CustomizationsPath)

    $connections = New-Object System.Collections.Generic.List[object]

    if (-not (Test-Path -LiteralPath $CustomizationsPath)) {
        return @()
    }

    [xml]$customizations = Get-Content -LiteralPath $CustomizationsPath -Raw
    foreach ($connection in $customizations.ImportExportXml.connectionreferences.connectionreference) {
        $logicalName = [string]$connection.connectionreferencelogicalname
        if ([string]::IsNullOrWhiteSpace($logicalName) -and $connection.Attributes["connectionreferencelogicalname"]) {
            $logicalName = [string]$connection.Attributes["connectionreferencelogicalname"].Value
        }

        $connections.Add([pscustomobject]@{
            logicalName = $logicalName
            displayName = [string]$connection.connectionreferencedisplayname
            connectorId = [string]$connection.connectorid
            connector = Split-Path ([string]$connection.connectorid) -Leaf
            stateCode = [string]$connection.statecode
            statusCode = [string]$connection.statuscode
        })
    }

    return @($connections.ToArray())
}

function Write-JsonFile {
    param(
        [string]$Path,
        [object]$Value,
        [int]$Depth = 100
    )

    $Value | ConvertTo-Json -Depth $Depth | Set-Content -LiteralPath $Path -Encoding UTF8
}

function ConvertTo-BulletListText {
    param([object[]]$Values)

    if ($null -eq $Values -or @($Values).Count -eq 0) {
        return "- None"
    }

    return (@($Values) | ForEach-Object { "- $_" }) -join [Environment]::NewLine
}

$solutionRootPath = Resolve-Path -LiteralPath $SolutionRoot
$workflowRoot = Join-Path $solutionRootPath "unpacked\Workflows"
$customizationsPath = Join-Path $solutionRootPath "unpacked\Other\Customizations.xml"
$dependencyRoot = Join-Path $solutionRootPath "external-dependencies"
$byConnectorRoot = Join-Path $dependencyRoot "connectors"

if (-not (Test-Path -LiteralPath $workflowRoot)) {
    throw "Workflow folder not found: $workflowRoot"
}

New-Item -ItemType Directory -Force -Path $dependencyRoot, $byConnectorRoot | Out-Null

foreach ($generatedFile in @(
    "README.md",
    "all-connector-actions.json",
    "connection-references.json",
    "connector-summary.json",
    "endpoints.json",
    "endpoint-summary.json",
    "http-endpoints.json",
    "graph-references.json",
    "google-references.json",
    "openai-references.json",
    "power-automate-webhooks.json",
    "sql-references.json",
    "excel-references.json",
    "outlook-references.json",
    "forms-references.json",
    "approvals-references.json",
    "plumsail-references.json",
    "request-triggers.json"
)) {
    $path = Join-Path $dependencyRoot $generatedFile
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
    }
}

Get-ChildItem -LiteralPath $byConnectorRoot -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force

$actions = New-Object System.Collections.Generic.List[object]
$endpoints = New-Object System.Collections.Generic.List[object]
$requestTriggers = New-Object System.Collections.Generic.List[object]

foreach ($jsonPath in Get-ChildItem -LiteralPath $workflowRoot -Filter "*.json" | Sort-Object Name) {
    $flowName = Get-WorkflowName -JsonPath $jsonPath.FullName
    $definition = Get-Content -LiteralPath $jsonPath.FullName -Raw | ConvertFrom-Json

    Visit-WorkflowNodes -Actions $actions -Endpoints $endpoints -RequestTriggers $requestTriggers -FlowName $flowName -FlowFile $jsonPath.Name -NodeKind "trigger" -Path "triggers" -Nodes $definition.properties.definition.triggers
    Visit-WorkflowNodes -Actions $actions -Endpoints $endpoints -RequestTriggers $requestTriggers -FlowName $flowName -FlowFile $jsonPath.Name -NodeKind "action" -Path "actions" -Nodes $definition.properties.definition.actions
}

$connectionReferences = Read-ConnectionReferences -CustomizationsPath $customizationsPath
$actionArray = @($actions.ToArray() | Sort-Object connector, flowName, nodePath)
$endpointArray = @($endpoints.ToArray() | Sort-Object category, host, path, flowName, nodePath)
$requestTriggerArray = @($requestTriggers.ToArray() | Sort-Object flowName, nodePath)

$connectorSummary = @(
    foreach ($group in ($actionArray | Group-Object connector | Sort-Object Name)) {
        $connectorRefs = @($connectionReferences | Where-Object { $_.connector -eq $group.Name })
        [pscustomobject]@{
            connector = $group.Name
            actionCount = $group.Count
            flows = @($group.Group | ForEach-Object { $_.flowName } | Sort-Object -Unique)
            operations = @($group.Group | ForEach-Object { $_.operationId } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Sort-Object -Unique)
            connectionReferences = $connectorRefs
        }
    }
)

$endpointSummary = @(
    foreach ($group in ($endpointArray | Group-Object category | Sort-Object Name)) {
        [pscustomobject]@{
            category = $group.Name
            endpointCount = $group.Count
            hosts = @($group.Group | ForEach-Object { $_.host } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Sort-Object -Unique)
            paths = @($group.Group | ForEach-Object { $_.path } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Sort-Object -Unique)
            flows = @($group.Group | ForEach-Object { $_.flowName } | Sort-Object -Unique)
        }
    }
)

$sqlReferences = @($actionArray | Where-Object { $_.connector -eq "shared_sql" } | Select-Object flowName,nodeName,nodePath,operationId,method,parameters)
$excelReferences = @($actionArray | Where-Object { $_.connector -like "shared_excelonlinebusiness*" } | Select-Object flowName,nodeName,nodePath,connector,operationId,parameters)
$outlookReferences = @($actionArray | Where-Object { $_.connector -eq "shared_office365" } | Select-Object flowName,nodeName,nodePath,operationId,parameters)
$formsReferences = @($actionArray | Where-Object { $_.connector -eq "shared_microsoftforms" } | Select-Object flowName,nodeName,nodePath,operationId,parameters)
$approvalsReferences = @($actionArray | Where-Object { $_.connector -eq "shared_approvals" } | Select-Object flowName,nodeName,nodePath,operationId,parameters)
$plumsailReferences = @($actionArray | Where-Object { $_.connector -eq "shared_plumsail" } | Select-Object flowName,nodeName,nodePath,operationId,parameters)
$httpEndpoints = @($endpointArray | Where-Object { $_.category -ne "SharePoint / OneDrive" })
$graphEndpoints = @($endpointArray | Where-Object { $_.category -eq "Microsoft Graph" })
$googleEndpoints = @($endpointArray | Where-Object { $_.category -eq "Google API" })
$openAiEndpoints = @($endpointArray | Where-Object { $_.category -eq "OpenAI API" })
$powerAutomateWebhooks = @($endpointArray | Where-Object { $_.category -match "Power Automate|Logic Apps|Workflow webhook" })

$openAiReferences = @(
    foreach ($endpoint in $openAiEndpoints) {
        [pscustomobject]@{
            flowName = $endpoint.flowName
            flowFile = $endpoint.flowFile
            nodeName = $endpoint.nodeName
            nodePath = $endpoint.nodePath
            method = $endpoint.method
            path = $endpoint.path
            redactedUrl = $endpoint.redactedUrl
            connector = $endpoint.connector
            operationId = $endpoint.operationId
            notableFields = $endpoint.notableFields
        }
    }
)

Write-JsonFile -Path (Join-Path $dependencyRoot "connection-references.json") -Value $connectionReferences
Write-JsonFile -Path (Join-Path $dependencyRoot "all-connector-actions.json") -Value $actionArray
Write-JsonFile -Path (Join-Path $dependencyRoot "connector-summary.json") -Value $connectorSummary
Write-JsonFile -Path (Join-Path $dependencyRoot "endpoints.json") -Value $endpointArray
Write-JsonFile -Path (Join-Path $dependencyRoot "endpoint-summary.json") -Value $endpointSummary
Write-JsonFile -Path (Join-Path $dependencyRoot "http-endpoints.json") -Value $httpEndpoints
Write-JsonFile -Path (Join-Path $dependencyRoot "graph-references.json") -Value $graphEndpoints
Write-JsonFile -Path (Join-Path $dependencyRoot "google-references.json") -Value $googleEndpoints
Write-JsonFile -Path (Join-Path $dependencyRoot "openai-references.json") -Value $openAiReferences
Write-JsonFile -Path (Join-Path $dependencyRoot "power-automate-webhooks.json") -Value $powerAutomateWebhooks
Write-JsonFile -Path (Join-Path $dependencyRoot "sql-references.json") -Value $sqlReferences
Write-JsonFile -Path (Join-Path $dependencyRoot "excel-references.json") -Value $excelReferences
Write-JsonFile -Path (Join-Path $dependencyRoot "outlook-references.json") -Value $outlookReferences
Write-JsonFile -Path (Join-Path $dependencyRoot "forms-references.json") -Value $formsReferences
Write-JsonFile -Path (Join-Path $dependencyRoot "approvals-references.json") -Value $approvalsReferences
Write-JsonFile -Path (Join-Path $dependencyRoot "plumsail-references.json") -Value $plumsailReferences
Write-JsonFile -Path (Join-Path $dependencyRoot "request-triggers.json") -Value $requestTriggerArray

foreach ($connector in $connectorSummary) {
    $connectorFolder = Join-Path $byConnectorRoot (ConvertTo-Slug -Value $connector.connector)
    New-Item -ItemType Directory -Force -Path $connectorFolder | Out-Null
    $connectorActions = @($actionArray | Where-Object { $_.connector -eq $connector.connector })

    Write-JsonFile -Path (Join-Path $connectorFolder "actions.json") -Value $connectorActions
    Write-JsonFile -Path (Join-Path $connectorFolder "summary.json") -Value $connector

    $readme = @"
# $($connector.connector)

- Action count: $($connector.actionCount)
- Flow count: $(@($connector.flows).Count)
- Connection references: $(@($connector.connectionReferences).Count)

## Operations

$(ConvertTo-BulletListText -Values $connector.operations)

## Flows

$(ConvertTo-BulletListText -Values $connector.flows)

## Files

- actions.json: Connector actions with sanitized parameters.
- summary.json: Connector rollup.
"@

    $readme | Set-Content -LiteralPath (Join-Path $connectorFolder "README.md") -Encoding UTF8
}

$readmeLines = New-Object System.Collections.Generic.List[string]
$readmeLines.Add("# External Dependencies")
$readmeLines.Add("")
$readmeLines.Add("Static dependency inventory generated from the exported Power Automate flow definitions.")
$readmeLines.Add("")
$readmeLines.Add("Sensitive property names and sensitive URL query values are redacted in this report. The raw exported flow definitions may still contain environment-specific endpoints and trigger signatures.")
$readmeLines.Add("")
$readmeLines.Add("## Summary")
$readmeLines.Add("")
$readmeLines.Add("- Connector actions found: $(@($actionArray).Count)")
$readmeLines.Add("- Connection references found: $(@($connectionReferences).Count)")
$readmeLines.Add("- HTTP/URL references found: $(@($endpointArray).Count)")
$readmeLines.Add("- Inbound HTTP request triggers found: $(@($requestTriggerArray).Count)")
$readmeLines.Add("")
$readmeLines.Add("## Connectors")
$readmeLines.Add("")
$readmeLines.Add("| Connector | Actions | Flows | Operations |")
$readmeLines.Add("| --- | ---: | ---: | --- |")
foreach ($connector in $connectorSummary) {
    $operations = if (@($connector.operations).Count -gt 0) { (@($connector.operations) -join ", ") } else { "none" }
    $readmeLines.Add("| [$($connector.connector)](connectors/$(ConvertTo-Slug -Value $connector.connector)/README.md) | $($connector.actionCount) | $(@($connector.flows).Count) | $operations |")
}
$readmeLines.Add("")
$readmeLines.Add("## Endpoint Categories")
$readmeLines.Add("")
$readmeLines.Add("| Category | Endpoints | Hosts | Flows |")
$readmeLines.Add("| --- | ---: | --- | ---: |")
foreach ($summary in $endpointSummary) {
    $hosts = if (@($summary.hosts).Count -gt 0) { (@($summary.hosts) -join ", ") } else { "none" }
    $readmeLines.Add("| $($summary.category) | $($summary.endpointCount) | $hosts | $(@($summary.flows).Count) |")
}
$readmeLines.Add("")
$readmeLines.Add("## Key Files")
$readmeLines.Add("")
$readmeLines.Add('- `connector-summary.json`: connector rollup with flows, operations, and solution connection references.')
$readmeLines.Add('- `all-connector-actions.json`: every connector action with sanitized parameters.')
$readmeLines.Add('- `endpoints.json`: every URL found in flow definitions with redacted sensitive query values.')
$readmeLines.Add('- `graph-references.json`: Microsoft Graph endpoint references.')
$readmeLines.Add('- `google-references.json`: Google API endpoint references, including Document AI/OAuth URLs.')
$readmeLines.Add('- `openai-references.json`: OpenAI API endpoint references and notable model/assistant fields when statically visible.')
$readmeLines.Add('- `power-automate-webhooks.json`: direct workflow/webhook endpoint references.')
$readmeLines.Add('- `sql-references.json`: SQL Server connector references.')
$readmeLines.Add('- `excel-references.json`: Excel Online connector references.')
$readmeLines.Add('- `outlook-references.json`: Office 365 Outlook connector references.')
$readmeLines.Add('- `forms-references.json`: Microsoft Forms connector references.')
$readmeLines.Add('- `approvals-references.json`: Approvals connector references.')
$readmeLines.Add('- `plumsail-references.json`: Plumsail Documents connector references.')
$readmeLines.Add('- `request-triggers.json`: inbound HTTP request trigger schemas.')
$readmeLines.Add("")
$readmeLines.Add('SharePoint list-level dependencies are separately expanded in `../sharepoint-reference/`.')

$readmeLines | Set-Content -LiteralPath (Join-Path $dependencyRoot "README.md") -Encoding UTF8

Write-Host "Connector actions found: $(@($actionArray).Count)"
Write-Host "Connection references found: $(@($connectionReferences).Count)"
Write-Host "HTTP/URL references found: $(@($endpointArray).Count)"
Write-Host "Inbound request triggers found: $(@($requestTriggerArray).Count)"
Write-Host "External dependency reference written to $dependencyRoot"
