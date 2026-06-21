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
        return "unnamed-flow"
    }

    return $slug
}

function Get-FlowGroup {
    param([string]$DisplayName)

    switch -Regex ($DisplayName) {
        "^orders@ - Master" { return @{ Folder = "01-master-email"; Label = "Master email orchestration" } }
        "^orders@ - Module|^Macro -" { return @{ Folder = "02-customer-identification"; Label = "Customer identification modules" } }
        "^orderProcess|^Manual - orderProcess|^Manual Control - Starter - orderProcess|^Backup - orderProcess" { return @{ Folder = "03-order-process"; Label = "Order processing modules" } }
        "^itemNumber|^ItemNumber|Item Number" { return @{ Folder = "04-item-number"; Label = "Item number maintenance and validation" } }
        "Form Submission" { return @{ Folder = "05-form-submissions"; Label = "Form submissions" } }
        "Customer List Assistant" { return @{ Folder = "06-maintenance"; Label = "Maintenance and scheduled support" } }
        "Temp|Test" { return @{ Folder = "90-temp-test"; Label = "Temporary and test flows" } }
        default { return @{ Folder = "99-other"; Label = "Other flows" } }
    }
}

function Get-StateLabel {
    param(
        [string]$StateCode,
        [string]$StatusCode
    )

    if ($StateCode -eq "1" -and $StatusCode -eq "2") {
        return "Activated"
    }

    if ($StateCode -eq "0" -and $StatusCode -eq "1") {
        return "Draft"
    }

    return "StateCode $StateCode / StatusCode $StatusCode"
}

function Get-ActionStats {
    param([object]$Actions)

    $result = [ordered]@{
        Count = 0
        Types = New-Object System.Collections.Generic.HashSet[string]
        Operations = New-Object System.Collections.Generic.HashSet[string]
        Connectors = New-Object System.Collections.Generic.HashSet[string]
    }

    function Visit-Actions {
        param([object]$Node)

        if ($null -eq $Node) {
            return
        }

        foreach ($property in $Node.PSObject.Properties) {
            $action = $property.Value
            $result.Count++

            if ($action.type) {
                [void]$result.Types.Add([string]$action.type)
            }

            if ($action.inputs -and $action.inputs.host) {
                if ($action.inputs.host.operationId) {
                    [void]$result.Operations.Add([string]$action.inputs.host.operationId)
                }

                if ($action.inputs.host.connectionName) {
                    [void]$result.Connectors.Add([string]$action.inputs.host.connectionName)
                }
            }

            if ($action.inputs -and $action.inputs.apiId) {
                $apiName = [string]$action.inputs.apiId
                $apiName = Split-Path $apiName -Leaf
                if ($apiName) {
                    [void]$result.Connectors.Add($apiName)
                }
            }

            if ($action.actions) {
                Visit-Actions -Node $action.actions
            }

            if ($action.else -and $action.else.actions) {
                Visit-Actions -Node $action.else.actions
            }

            if ($action.cases) {
                foreach ($case in $action.cases.PSObject.Properties) {
                    if ($case.Value.actions) {
                        Visit-Actions -Node $case.Value.actions
                    }
                }
            }
        }
    }

    Visit-Actions -Node $Actions

    return [pscustomobject]@{
        Count = $result.Count
        Types = @($result.Types | Sort-Object)
        Operations = @($result.Operations | Sort-Object)
        Connectors = @($result.Connectors | Sort-Object)
    }
}

function Get-TriggerSummary {
    param([object]$Triggers)

    if ($null -eq $Triggers) {
        return @()
    }

    $summaries = foreach ($property in $Triggers.PSObject.Properties) {
        $trigger = $property.Value
        [pscustomobject]@{
            Name = $property.Name
            Type = if ($trigger.type) { [string]$trigger.type } else { "" }
            Kind = if ($trigger.kind) { [string]$trigger.kind } else { "" }
            OperationId = if ($trigger.inputs -and $trigger.inputs.host -and $trigger.inputs.host.operationId) { [string]$trigger.inputs.host.operationId } else { "" }
        }
    }

    return @($summaries)
}

function Read-ConnectionReferences {
    param([string]$CustomizationsPath)

    $connections = @{}

    if (-not (Test-Path -LiteralPath $CustomizationsPath)) {
        return $connections
    }

    [xml]$customizations = Get-Content -LiteralPath $CustomizationsPath -Raw
    foreach ($connection in $customizations.ImportExportXml.connectionreferences.connectionreference) {
        $logicalName = [string]$connection.connectionreferencelogicalname
        if ([string]::IsNullOrWhiteSpace($logicalName)) {
            $logicalName = [string]$connection.Attributes["connectionreferencelogicalname"].Value
        }

        if (-not [string]::IsNullOrWhiteSpace($logicalName)) {
            $connections[$logicalName] = [pscustomobject]@{
                logicalName = $logicalName
                displayName = [string]$connection.connectionreferencedisplayname
                connectorId = [string]$connection.connectorid
            }
        }
    }

    return $connections
}

$solutionRootPath = Resolve-Path -LiteralPath $SolutionRoot
$workflowRoot = Join-Path $solutionRootPath "unpacked\Workflows"
$libraryRoot = Join-Path $solutionRootPath "flow-library"
$customizationsPath = Join-Path $solutionRootPath "unpacked\Other\Customizations.xml"

if (-not (Test-Path -LiteralPath $workflowRoot)) {
    throw "Workflow folder not found: $workflowRoot"
}

New-Item -ItemType Directory -Force -Path $libraryRoot | Out-Null

$generatedGroups = @(
    "01-master-email",
    "02-customer-identification",
    "03-order-process",
    "04-item-number",
    "05-form-submissions",
    "06-maintenance",
    "90-temp-test",
    "99-other"
)

$resolvedLibraryRoot = Resolve-Path -LiteralPath $libraryRoot
if (-not $resolvedLibraryRoot.Path.StartsWith($solutionRootPath.Path, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean a flow library outside the solution root: $($resolvedLibraryRoot.Path)"
}

foreach ($groupFolder in $generatedGroups) {
    $groupPath = Join-Path $resolvedLibraryRoot.Path $groupFolder
    if (Test-Path -LiteralPath $groupPath) {
        $resolvedGroupPath = Resolve-Path -LiteralPath $groupPath
        if (-not $resolvedGroupPath.Path.StartsWith($resolvedLibraryRoot.Path, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a generated group outside the flow library: $($resolvedGroupPath.Path)"
        }

        Remove-Item -LiteralPath $resolvedGroupPath.Path -Recurse -Force
    }
}

foreach ($generatedFile in @("README.md", "flow-index.json", "connection-references.json")) {
    $generatedFilePath = Join-Path $resolvedLibraryRoot.Path $generatedFile
    if (Test-Path -LiteralPath $generatedFilePath) {
        Remove-Item -LiteralPath $generatedFilePath -Force
    }
}

$connectionReferenceMap = Read-ConnectionReferences -CustomizationsPath $customizationsPath
$flows = New-Object System.Collections.Generic.List[object]

foreach ($jsonPath in Get-ChildItem -LiteralPath $workflowRoot -Filter "*.json" | Sort-Object Name) {
    $metadataPath = "$($jsonPath.FullName).data.xml"

    if (-not (Test-Path -LiteralPath $metadataPath)) {
        Write-Warning "Skipping $($jsonPath.Name); matching metadata XML was not found."
        continue
    }

    [xml]$metadata = Get-Content -LiteralPath $metadataPath -Raw
    $definition = Get-Content -LiteralPath $jsonPath.FullName -Raw | ConvertFrom-Json

    $workflow = $metadata.Workflow
    $displayName = [string]$workflow.Name
    $group = Get-FlowGroup -DisplayName $displayName
    $slug = ConvertTo-Slug -Value $displayName
    $flowDir = Join-Path (Join-Path $libraryRoot $group.Folder) $slug

    New-Item -ItemType Directory -Force -Path $flowDir | Out-Null

    $definitionCopy = Join-Path $flowDir "definition.json"
    $metadataCopy = Join-Path $flowDir "metadata.xml"
    Copy-Item -LiteralPath $jsonPath.FullName -Destination $definitionCopy -Force
    Copy-Item -LiteralPath $metadataPath -Destination $metadataCopy -Force

    $connectionReferences = @()
    if ($definition.properties.connectionReferences) {
        foreach ($property in $definition.properties.connectionReferences.PSObject.Properties) {
            $logicalName = ""
            if ($property.Value.connection -and $property.Value.connection.connectionReferenceLogicalName) {
                $logicalName = [string]$property.Value.connection.connectionReferenceLogicalName
            }

            $details = if ($connectionReferenceMap.ContainsKey($logicalName)) { $connectionReferenceMap[$logicalName] } else { $null }
            $connectionReferences += [pscustomobject]@{
                api = [string]$property.Value.api.name
                logicalName = $logicalName
                displayName = if ($details) { $details.displayName } else { "" }
                connectorId = if ($details) { $details.connectorId } else { "" }
            }
        }
    }

    $actionStats = Get-ActionStats -Actions $definition.properties.definition.actions
    $triggerSummary = Get-TriggerSummary -Triggers $definition.properties.definition.triggers
    $stateLabel = Get-StateLabel -StateCode ([string]$workflow.StateCode) -StatusCode ([string]$workflow.StatusCode)

    $flowSummary = [pscustomobject]@{
        displayName = $displayName
        slug = $slug
        group = $group.Folder
        groupLabel = $group.Label
        workflowId = ([string]$workflow.WorkflowId).Trim("{}")
        state = $stateLabel
        stateCode = [string]$workflow.StateCode
        statusCode = [string]$workflow.StatusCode
        introducedVersion = [string]$workflow.IntroducedVersion
        triggers = @($triggerSummary)
        actionCount = $actionStats.Count
        actionTypes = $actionStats.Types
        operationIds = $actionStats.Operations
        connectors = @(@($connectionReferences | ForEach-Object { $_.api }) + @($actionStats.Connectors) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Sort-Object -Unique)
        connectionReferences = $connectionReferences
        sourceDefinition = "unpacked/Workflows/$($jsonPath.Name)"
        sourceMetadata = "unpacked/Workflows/$($jsonPath.Name).data.xml"
        libraryDefinition = "flow-library/$($group.Folder)/$slug/definition.json"
        libraryMetadata = "flow-library/$($group.Folder)/$slug/metadata.xml"
    }

    $flowSummary | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $flowDir "summary.json") -Encoding UTF8

    $triggerText = if (@($triggerSummary).Count -gt 0) {
        (@($triggerSummary) | ForEach-Object {
            $parts = @($_.Name, $_.Type, $_.Kind, $_.OperationId) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
            "- " + ($parts -join " | ")
        }) -join [Environment]::NewLine
    }
    else {
        "- None detected"
    }

    $connectorText = if (@($flowSummary.connectors).Count -gt 0) {
        (@($flowSummary.connectors) | ForEach-Object { "- $_" }) -join [Environment]::NewLine
    }
    else {
        "- None detected"
    }

    $readme = @"
# $displayName

- Group: $($group.Label)
- Workflow ID: $($flowSummary.workflowId)
- State: $stateLabel
- Version: $($flowSummary.introducedVersion)
- Action count: $($flowSummary.actionCount)

## Triggers

$triggerText

## Connectors

$connectorText

## Files

- definition.json: Exported Power Automate flow definition.
- metadata.xml: Dataverse workflow metadata from the solution export.
- summary.json: Parsed metadata used by the flow index.

## Source

- $($flowSummary.sourceDefinition)
- $($flowSummary.sourceMetadata)
"@

    $readme | Set-Content -LiteralPath (Join-Path $flowDir "README.md") -Encoding UTF8
    $flows.Add($flowSummary)
}

$flowArray = @($flows | Sort-Object group, displayName)
$flowArray | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $libraryRoot "flow-index.json") -Encoding UTF8

$connectionReferenceMap.Values |
    Sort-Object logicalName |
    ConvertTo-Json -Depth 10 |
    Set-Content -LiteralPath (Join-Path $libraryRoot "connection-references.json") -Encoding UTF8

$groups = $flowArray | Group-Object group | Sort-Object Name
$indexLines = New-Object System.Collections.Generic.List[string]
$indexLines.Add("# Orders Automations Flow Library")
$indexLines.Add("")
$indexLines.Add('Generated from the unmanaged solution export in `../exports/OrdersAutomations_1.0.0.0_unmanaged.zip`.')
$indexLines.Add("")
$indexLines.Add("Treat exported flow definitions as sensitive. Power Automate exports can contain environment-specific endpoints, connector references, and direct trigger URLs.")
$indexLines.Add("")
$indexLines.Add("## Summary")
$indexLines.Add("")
$indexLines.Add('- Solution: Orders Automations (`OrdersAutomations`)')
$indexLines.Add('- Environment: Orders Automations (`e51ca662-f633-e290-b476-202747054118`)')
$indexLines.Add("- Export type: Unmanaged")
$indexLines.Add("- Flow count: $($flowArray.Count)")
$indexLines.Add("")
$indexLines.Add("## Groups")
$indexLines.Add("")

foreach ($group in $groups) {
    $label = ($group.Group | Select-Object -First 1).groupLabel
    $indexLines.Add("### $($group.Name) - $label")
    $indexLines.Add("")

    foreach ($flow in ($group.Group | Sort-Object displayName)) {
        $triggerNames = if (@($flow.triggers).Count -gt 0) { ((@($flow.triggers) | ForEach-Object { $_.Name }) -join ", ") } else { "none" }
        $connectors = if (@($flow.connectors).Count -gt 0) { (@($flow.connectors) -join ", ") } else { "none" }
        $indexLines.Add("- [$($flow.displayName)]($($group.Name)/$($flow.slug)/README.md) - $($flow.state); triggers: $triggerNames; connectors: $connectors")
    }

    $indexLines.Add("")
}

$indexLines.Add("## Files")
$indexLines.Add("")
$indexLines.Add('- `flow-index.json`: Parsed catalog for automation, search, or migration planning.')
$indexLines.Add('- `connection-references.json`: Solution-level connector references.')
$indexLines.Add('- `<group>/<flow>/definition.json`: Exported flow definition.')
$indexLines.Add('- `<group>/<flow>/metadata.xml`: Exported Dataverse workflow metadata.')

$indexLines | Set-Content -LiteralPath (Join-Path $libraryRoot "README.md") -Encoding UTF8

Write-Host "Built flow library with $($flowArray.Count) flows at $libraryRoot"
