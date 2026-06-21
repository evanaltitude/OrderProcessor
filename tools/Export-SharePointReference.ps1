param(
    [string]$SolutionRoot = ".\power-automate\solutions\OrdersAutomations",
    [string]$TenantId = "9d7ae37d-248f-4727-922c-7b5d3ae57a70",
    [string]$AzureSubscription = "FDI CSP",
    [switch]$SkipFetch
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

function ConvertTo-PlainObject {
    param([object]$Value)

    if ($null -eq $Value) {
        return $null
    }

    return $Value | ConvertTo-Json -Depth 100 | ConvertFrom-Json
}

function Get-AccessType {
    param([string]$OperationId)

    switch -Regex ($OperationId) {
        "^(Get|List)" { return "Read" }
        "^(Create|Post|Patch|Update)" { return "Write" }
        "^Delete" { return "Delete" }
        default { return "Other" }
    }
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

function Add-SharePointNode {
    param(
        [System.Collections.Generic.List[object]]$Actions,
        [string]$FlowName,
        [string]$FlowFile,
        [string]$NodeName,
        [string]$NodeKind,
        [string]$NodePath,
        [object]$Node
    )

    if (-not $Node.inputs -or -not $Node.inputs.host) {
        return
    }

    $hostInfo = $Node.inputs.host
    $apiId = [string]$hostInfo.apiId
    $connectionName = [string]$hostInfo.connectionName

    if ($apiId -notmatch "shared_sharepointonline" -and $connectionName -ne "shared_sharepointonline") {
        return
    }

    $operationId = [string]$hostInfo.operationId
    $parameters = $Node.inputs.parameters
    $dataset = if ($parameters -and $parameters.dataset) { [string]$parameters.dataset } else { "" }
    $table = if ($parameters -and $parameters.table) { [string]$parameters.table } else { "" }
    $accessType = Get-AccessType -OperationId $operationId

    $fieldMappings = [ordered]@{}
    if ($parameters) {
        foreach ($property in $parameters.PSObject.Properties) {
            if ($property.Name -like "item/*") {
                $fieldName = $property.Name.Substring(5)
                $fieldMappings[$fieldName] = $property.Value
            }
        }
    }

    $Actions.Add([pscustomobject]@{
        flowName = $FlowName
        flowFile = $FlowFile
        nodeName = $NodeName
        nodeKind = $NodeKind
        nodePath = $NodePath
        operationId = $operationId
        accessType = $accessType
        isListOperation = -not [string]::IsNullOrWhiteSpace($table)
        siteUrl = $dataset
        listId = $table
        fieldMappings = $fieldMappings
        parameters = ConvertTo-PlainObject -Value $parameters
    })
}

function Visit-WorkflowNodes {
    param(
        [System.Collections.Generic.List[object]]$Actions,
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

        Add-SharePointNode -Actions $Actions -FlowName $FlowName -FlowFile $FlowFile -NodeName $nodeName -NodeKind $NodeKind -NodePath $nodePath -Node $node

        if ($node.actions) {
            Visit-WorkflowNodes -Actions $Actions -FlowName $FlowName -FlowFile $FlowFile -NodeKind "action" -Path "$nodePath/actions" -Nodes $node.actions
        }

        if ($node.else -and $node.else.actions) {
            Visit-WorkflowNodes -Actions $Actions -FlowName $FlowName -FlowFile $FlowFile -NodeKind "action" -Path "$nodePath/else/actions" -Nodes $node.else.actions
        }

        if ($node.cases) {
            foreach ($case in $node.cases.PSObject.Properties) {
                if ($case.Value.actions) {
                    Visit-WorkflowNodes -Actions $Actions -FlowName $FlowName -FlowFile $FlowFile -NodeKind "action" -Path "$nodePath/cases/$($case.Name)/actions" -Nodes $case.Value.actions
                }
            }
        }

        if ($node.default -and $node.default.actions) {
            Visit-WorkflowNodes -Actions $Actions -FlowName $FlowName -FlowFile $FlowFile -NodeKind "action" -Path "$nodePath/default/actions" -Nodes $node.default.actions
        }
    }
}

function Get-GraphToken {
    param(
        [string]$TenantId,
        [string]$Subscription
    )

    $arguments = @(
        "account",
        "get-access-token",
        "--resource",
        "https://graph.microsoft.com/",
        "--tenant",
        $TenantId,
        "--output",
        "json"
    )

    if ([string]::IsNullOrWhiteSpace($TenantId) -and -not [string]::IsNullOrWhiteSpace($Subscription)) {
        $arguments += @("--subscription", $Subscription)
    }

    $tokenOutput = & az @arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ($tokenOutput -join [Environment]::NewLine)
    }

    $token = $tokenOutput | Out-String | ConvertFrom-Json
    return [string]$token.accessToken
}

function Invoke-GraphGet {
    param(
        [string]$Url,
        [string]$AccessToken
    )

    Invoke-RestMethod -Method Get -Uri $Url -Headers @{ Authorization = "Bearer $AccessToken" }
}

function Invoke-GraphPagedGet {
    param(
        [string]$Url,
        [string]$AccessToken
    )

    $items = New-Object System.Collections.Generic.List[object]
    $nextUrl = $Url

    while (-not [string]::IsNullOrWhiteSpace($nextUrl)) {
        $response = Invoke-GraphGet -Url $nextUrl -AccessToken $AccessToken
        if ($response.value) {
            foreach ($item in $response.value) {
                $items.Add($item)
            }
        }

        $nextUrl = $response.'@odata.nextLink'
    }

    return @($items)
}

function Get-SiteGraphPath {
    param([string]$SiteUrl)

    $uri = [uri]$SiteUrl
    $path = $uri.AbsolutePath.Trim("/")
    return "https://graph.microsoft.com/v1.0/sites/$($uri.Host):/$path"
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
$referenceRoot = Join-Path $solutionRootPath "sharepoint-reference"
$listsRoot = Join-Path $referenceRoot "lists"
$logsRoot = Join-Path $referenceRoot "logs"

if (-not (Test-Path -LiteralPath $workflowRoot)) {
    throw "Workflow folder not found: $workflowRoot"
}

New-Item -ItemType Directory -Force -Path $referenceRoot, $listsRoot, $logsRoot | Out-Null

$actions = New-Object System.Collections.Generic.List[object]

foreach ($jsonPath in Get-ChildItem -LiteralPath $workflowRoot -Filter "*.json" | Sort-Object Name) {
    $flowName = Get-WorkflowName -JsonPath $jsonPath.FullName
    $definition = Get-Content -LiteralPath $jsonPath.FullName -Raw | ConvertFrom-Json

    Visit-WorkflowNodes -Actions $actions -FlowName $flowName -FlowFile $jsonPath.Name -NodeKind "trigger" -Path "triggers" -Nodes $definition.properties.definition.triggers
    Visit-WorkflowNodes -Actions $actions -FlowName $flowName -FlowFile $jsonPath.Name -NodeKind "action" -Path "actions" -Nodes $definition.properties.definition.actions
}

$sharePointActions = @($actions | Sort-Object siteUrl, listId, flowName, nodePath)
$listActions = @($sharePointActions | Where-Object { $_.isListOperation -and -not [string]::IsNullOrWhiteSpace($_.siteUrl) -and -not [string]::IsNullOrWhiteSpace($_.listId) })

$listReferences = New-Object System.Collections.Generic.List[object]
foreach ($group in ($listActions | Group-Object { "$($_.siteUrl)|$($_.listId)" } | Sort-Object Name)) {
    $first = $group.Group | Select-Object -First 1
    $fieldNames = @(
        $group.Group |
            ForEach-Object { $_.fieldMappings.Keys } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            Sort-Object -Unique
    )

    $listReferences.Add([pscustomobject]@{
        siteUrl = $first.siteUrl
        listId = $first.listId
        slug = ConvertTo-Slug -Value "$($first.siteUrl)-$($first.listId)"
        actionCount = $group.Count
        flows = @($group.Group | ForEach-Object { $_.flowName } | Sort-Object -Unique)
        operations = @($group.Group | ForEach-Object { $_.operationId } | Sort-Object -Unique)
        accessTypes = @($group.Group | ForEach-Object { $_.accessType } | Sort-Object -Unique)
        writeFieldNames = $fieldNames
    })
}

$listReferenceArray = @($listReferences.ToArray())

Write-JsonFile -Path (Join-Path $referenceRoot "sharepoint-actions.json") -Value $sharePointActions
Write-JsonFile -Path (Join-Path $referenceRoot "sharepoint-list-references.json") -Value $listReferenceArray

foreach ($reference in $listReferenceArray) {
    $listFolder = Join-Path $listsRoot $reference.slug
    New-Item -ItemType Directory -Force -Path $listFolder | Out-Null

    $referenceActions = @(
        $listActions |
            Where-Object { $_.siteUrl -eq $reference.siteUrl -and $_.listId -eq $reference.listId } |
            Sort-Object flowName, nodePath
    )

    Write-JsonFile -Path (Join-Path $listFolder "usage.json") -Value ([pscustomobject]@{
        reference = $reference
        actions = $referenceActions
    })

    $usageReadme = @"
# SharePoint List Reference

- Site: $($reference.siteUrl)
- List ID: $($reference.listId)
- Action count: $($reference.actionCount)

## Flow References

$(ConvertTo-BulletListText -Values $reference.flows)

## Operations

$(ConvertTo-BulletListText -Values $reference.operations)

## Write Field Names

$(ConvertTo-BulletListText -Values $reference.writeFieldNames)

## Files

- usage.json: Flow references, operations, and raw SharePoint action records for this list.
- config/: Created when Graph retrieval succeeds.
- items/: Created when Graph retrieval succeeds.
"@

    $usageReadme | Set-Content -LiteralPath (Join-Path $listFolder "README.md") -Encoding UTF8
}

$retrievalStatus = [ordered]@{
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    tenantId = $TenantId
    azureSubscription = $AzureSubscription
    fetchAttempted = -not $SkipFetch.IsPresent
    fetchSucceeded = $false
    siteCount = @($listReferenceArray | ForEach-Object { $_.siteUrl } | Sort-Object -Unique).Count
    listReferenceCount = @($listReferenceArray).Count
    actionCount = @($sharePointActions).Count
    listActionCount = @($listActions).Count
    error = $null
}

if (-not $SkipFetch.IsPresent -and @($listReferenceArray).Count -gt 0) {
    try {
        $accessToken = Get-GraphToken -TenantId $TenantId -Subscription $AzureSubscription
        $siteCache = @{}

        foreach ($reference in $listReferenceArray) {
            $siteUrl = [string]$reference.siteUrl
            if (-not $siteCache.ContainsKey($siteUrl)) {
                $site = Invoke-GraphGet -Url (Get-SiteGraphPath -SiteUrl $siteUrl) -AccessToken $accessToken
                $siteCache[$siteUrl] = $site
            }

            $siteInfo = $siteCache[$siteUrl]
            $listId = [string]$reference.listId
            $listFolderName = $reference.slug
            $listFolder = Join-Path $listsRoot $listFolderName
            $configFolder = Join-Path $listFolder "config"
            $itemsFolder = Join-Path $listFolder "items"

            New-Item -ItemType Directory -Force -Path $configFolder, $itemsFolder | Out-Null

            $listBaseUrl = "https://graph.microsoft.com/v1.0/sites/$($siteInfo.id)/lists/$listId"
            $list = Invoke-GraphGet -Url $listBaseUrl -AccessToken $accessToken
            $columns = Invoke-GraphPagedGet -Url "$listBaseUrl/columns?`$top=999" -AccessToken $accessToken
            $contentTypes = Invoke-GraphPagedGet -Url "$listBaseUrl/contentTypes?`$top=999" -AccessToken $accessToken
            $items = Invoke-GraphPagedGet -Url "$listBaseUrl/items?expand=fields&`$top=999" -AccessToken $accessToken

            Write-JsonFile -Path (Join-Path $configFolder "site.json") -Value $siteInfo
            Write-JsonFile -Path (Join-Path $configFolder "list.json") -Value $list
            Write-JsonFile -Path (Join-Path $configFolder "columns.json") -Value $columns
            Write-JsonFile -Path (Join-Path $configFolder "content-types.json") -Value $contentTypes
            Write-JsonFile -Path (Join-Path $itemsFolder "items.raw.json") -Value $items
            Write-JsonFile -Path (Join-Path $itemsFolder "items.fields.json") -Value @($items | ForEach-Object { $_.fields })

            $listSummary = [pscustomobject]@{
                displayName = $list.displayName
                listId = $list.id
                siteUrl = $siteUrl
                siteId = $siteInfo.id
                webUrl = $list.webUrl
                columnCount = @($columns).Count
                contentTypeCount = @($contentTypes).Count
                itemCount = @($items).Count
                flowReferences = $reference.flows
                operations = $reference.operations
                accessTypes = $reference.accessTypes
                writeFieldNames = $reference.writeFieldNames
            }

            Write-JsonFile -Path (Join-Path $listFolder "summary.json") -Value $listSummary

            $readme = @"
# $($list.displayName)

- Site: $siteUrl
- List ID: $($list.id)
- Web URL: $($list.webUrl)
- Columns: $(@($columns).Count)
- Content types: $(@($contentTypes).Count)
- Items exported: $(@($items).Count)

## Flow References

$(@($reference.flows) | ForEach-Object { "- $_" } | Out-String)
## Operations

$(@($reference.operations) | ForEach-Object { "- $_" } | Out-String)
## Files

- config/list.json
- config/columns.json
- config/content-types.json
- items/items.raw.json
- items/items.fields.json
- summary.json
"@
            $readme | Set-Content -LiteralPath (Join-Path $listFolder "README.md") -Encoding UTF8
        }

        $retrievalStatus.fetchSucceeded = $true
    }
    catch {
        $retrievalStatus.error = $_.Exception.Message
        $retrievalStatus.fetchSucceeded = $false
    }
}

Write-JsonFile -Path (Join-Path $referenceRoot "retrieval-status.json") -Value $retrievalStatus

$readmeLines = New-Object System.Collections.Generic.List[string]
$readmeLines.Add("# SharePoint Reference")
$readmeLines.Add("")
$readmeLines.Add("This folder records SharePoint dependencies discovered from the exported Power Automate flows.")
$readmeLines.Add("")
$readmeLines.Add("## Inventory")
$readmeLines.Add("")
$readmeLines.Add("- SharePoint connector actions discovered: $(@($sharePointActions).Count)")
$readmeLines.Add("- List actions discovered: $(@($listActions).Count)")
$readmeLines.Add("- Unique SharePoint lists referenced: $(@($listReferenceArray).Count)")
$readmeLines.Add("- Unique sites referenced: $($retrievalStatus.siteCount)")
$readmeLines.Add("")
$readmeLines.Add("## Files")
$readmeLines.Add("")
$readmeLines.Add('- `sharepoint-actions.json`: every SharePoint connector node found in the flow definitions.')
$readmeLines.Add('- `sharepoint-list-references.json`: unique list references with flows, operations, and write-field names.')
$readmeLines.Add('- `retrieval-status.json`: Graph retrieval status and any auth/access error.')
$readmeLines.Add('- `lists/`: per-list configuration and item exports when Graph retrieval succeeds.')
$readmeLines.Add("")
$readmeLines.Add("## List References")
$readmeLines.Add("")
$readmeLines.Add("| Site | List ID | Actions | Operations |")
$readmeLines.Add("| --- | --- | ---: | --- |")
foreach ($reference in $listReferenceArray) {
    $operations = if (@($reference.operations).Count -gt 0) { @($reference.operations) -join ", " } else { "none" }
    $readmeLines.Add("| $($reference.siteUrl) | [$($reference.listId)](lists/$($reference.slug)/README.md) | $($reference.actionCount) | $operations |")
}
$readmeLines.Add("")
$readmeLines.Add("## Retrieval Status")
$readmeLines.Add("")
if ($retrievalStatus.fetchSucceeded) {
    $readmeLines.Add('Graph retrieval succeeded. Per-list configurations and items are available under `lists/`.')
}
elseif ($SkipFetch.IsPresent) {
    $readmeLines.Add("Graph retrieval was skipped.")
}
else {
    $readmeLines.Add('Graph retrieval did not complete. See `retrieval-status.json` for the exact error.')
}
$readmeLines.Add("")
$readmeLines.Add("Treat exported SharePoint data as sensitive customer data.")

$readmeLines | Set-Content -LiteralPath (Join-Path $referenceRoot "README.md") -Encoding UTF8

Write-Host "SharePoint actions discovered: $(@($sharePointActions).Count)"
Write-Host "List actions discovered: $(@($listActions).Count)"
Write-Host "Unique lists referenced: $(@($listReferenceArray).Count)"
Write-Host "Reference written to $referenceRoot"
if (-not $retrievalStatus.fetchSucceeded -and -not $SkipFetch.IsPresent) {
    Write-Warning "Graph retrieval did not complete. See retrieval-status.json."
}
