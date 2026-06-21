param(
    [string]$SolutionRoot = ".\power-automate\solutions\OrdersAutomations",
    [string]$OutputRoot = ".\docs\reverse-engineering"
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

function ConvertTo-MarkdownList {
    param([string[]]$Values)

    $items = @($Values | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($items.Count -eq 0) {
        return "- None found in static analysis."
    }
    return (($items | ForEach-Object { "- $_" }) -join [Environment]::NewLine)
}

function Get-Capability {
    param([object]$Flow)

    switch ($Flow.group) {
        "01-master-email" { return "mailbox routing" }
        "02-customer-identification" { return "customer ID" }
        "03-order-process" { return "order processing" }
        "05-form-submissions" { return "support form" }
        "06-maintenance" { return "data refresh" }
        "90-temp-test" { return "support form" }
        "04-item-number" {
            if ($Flow.displayName -eq "Module - Item Number Validator") {
                return "item validation"
            }
            return "data refresh"
        }
        default { return "support form" }
    }
}

function Get-PrimaryBehavior {
    param([object]$Flow, [string]$Capability)

    switch ($Capability) {
        "mailbox routing" { return "Monitors or processes the shared orders mailbox and dispatches work to downstream processing modules." }
        "customer ID" { return "Identifies or updates customer metadata for an email/order and records the result for routing or processing." }
        "order processing" { return "Transforms an email, attachment, PDF, workbook, CSV, or email body into order output and mailbox status updates." }
        "item validation" { return "Validates provided item numbers/UPCs against SharePoint item reference data." }
        "data refresh" { return "Refreshes item/customer reference data or maintenance state from SharePoint/SQL sources." }
        "support form" { return "Supports request, problem-report, manual diagnostic, or temporary test workflows." }
        "output generation" { return "Generates downstream output artifacts." }
        default { return "Unclassified support behavior." }
    }
}

function Get-MigrationTarget {
    param([string]$Capability)

    switch ($Capability) {
        "mailbox routing" { return "Power Automate shell plus Azure /emails/ingest and data-driven routing rules." }
        "customer ID" { return "Azure /customers/identify, deterministic rules, Azure OpenAI embeddings, and Cosmos vector search." }
        "order processing" { return "Azure Durable Functions order-processing engine and per-source parser modules." }
        "item validation" { return "Azure /items/validate backed by Cosmos items." }
        "data refresh" { return "Azure /imports/customers or /imports/items writing canonical Cosmos records and Blob audit copies." }
        "support form" { return "Console workflow, optional M365 adapter, or remove if temporary diagnostic only." }
        "output generation" { return "Azure output adapters and Blob output artifacts." }
        default { return "Review during implementation." }
    }
}

function Get-SourceType {
    param([string]$FlowName)

    switch -Regex ($FlowName) {
        "Manual Control" { return "manual starter" }
        "CSV Parse" { return "CSV attachment" }
        "Dorothy Lane" { return "customer-specific XLSX" }
        "Market Place Pet Supplies" { return "customer-specific email body" }
        "Email Body" { return "email body" }
        "Google Document AI|PDF" { return "PDF attachment" }
        "XLS or XLT" { return "XLS/XLT attachment" }
        "XLSX" { return "XLSX attachment" }
        default { return "order-processing helper" }
    }
}

function Get-ConnectorName {
    param([string]$ApiId, [string]$ConnectionName)

    if (-not [string]::IsNullOrWhiteSpace($ApiId)) {
        return Split-Path $ApiId -Leaf
    }
    if (-not [string]::IsNullOrWhiteSpace($ConnectionName)) {
        return $ConnectionName
    }
    return ""
}

function Get-WorkflowNodes {
    param([object]$Definition)

    $nodes = New-Object System.Collections.Generic.List[object]

    function Visit-Nodes {
        param(
            [object]$NodeCollection,
            [string]$Kind,
            [string]$BasePath
        )

        if ($null -eq $NodeCollection) {
            return
        }

        foreach ($property in $NodeCollection.PSObject.Properties) {
            $nodeName = $property.Name
            $node = $property.Value
            $nodePath = if ([string]::IsNullOrWhiteSpace($BasePath)) { $nodeName } else { "$BasePath/$nodeName" }
            $apiId = ""
            $connectionName = ""
            $operationId = ""
            $method = ""
            $parameterKeys = @()

            if ($node.inputs -and $node.inputs.host) {
                $apiId = [string]$node.inputs.host.apiId
                $connectionName = [string]$node.inputs.host.connectionName
                $operationId = [string]$node.inputs.host.operationId
            }

            if ($node.inputs -and $node.inputs.method) {
                $method = [string]$node.inputs.method
            }
            elseif ($node.inputs -and $node.inputs.parameters -and $node.inputs.parameters.method) {
                $method = [string]$node.inputs.parameters.method
            }

            if ($node.inputs -and $node.inputs.parameters) {
                $parameterKeys = @($node.inputs.parameters.PSObject.Properties.Name | Sort-Object)
            }

            $text = $node | ConvertTo-Json -Depth 40 -Compress
            $nodes.Add([pscustomobject]@{
                name = $nodeName
                path = $nodePath
                kind = $Kind
                type = [string]$node.type
                connector = Get-ConnectorName -ApiId $apiId -ConnectionName $connectionName
                operationId = $operationId
                method = $method
                parameterKeys = $parameterKeys
                text = $text
            })

            if ($node.actions) {
                Visit-Nodes -NodeCollection $node.actions -Kind "action" -BasePath "$nodePath/actions"
            }
            if ($node.else -and $node.else.actions) {
                Visit-Nodes -NodeCollection $node.else.actions -Kind "action" -BasePath "$nodePath/else/actions"
            }
            if ($node.cases) {
                foreach ($case in $node.cases.PSObject.Properties) {
                    if ($case.Value.actions) {
                        Visit-Nodes -NodeCollection $case.Value.actions -Kind "action" -BasePath "$nodePath/cases/$($case.Name)/actions"
                    }
                }
            }
            if ($node.default -and $node.default.actions) {
                Visit-Nodes -NodeCollection $node.default.actions -Kind "action" -BasePath "$nodePath/default/actions"
            }
        }
    }

    Visit-Nodes -NodeCollection $Definition.properties.definition.triggers -Kind "trigger" -BasePath "triggers"
    Visit-Nodes -NodeCollection $Definition.properties.definition.actions -Kind "action" -BasePath "actions"
    return @($nodes.ToArray())
}

function Get-TriggerSchemaSummary {
    param([object]$Definition)

    $summaries = New-Object System.Collections.Generic.List[string]
    foreach ($triggerProperty in $Definition.properties.definition.triggers.PSObject.Properties) {
        $trigger = $triggerProperty.Value
        $schema = $trigger.inputs.schema
        $method = if ($trigger.inputs.method) { [string]$trigger.inputs.method } else { "" }
        $auth = if ($trigger.inputs.triggerAuthenticationType) { [string]$trigger.inputs.triggerAuthenticationType } else { "" }

        if ($null -eq $schema) {
            $summaries.Add("$($triggerProperty.Name): $($trigger.type) trigger with no static schema.")
            continue
        }

        $props = @()
        $required = @()
        if ($schema.type -eq "array" -and $schema.items) {
            if ($schema.items.properties) {
                $props = @($schema.items.properties.PSObject.Properties.Name)
            }
            if ($schema.items.required) {
                $required = @($schema.items.required)
            }
            $summaries.Add("$($triggerProperty.Name): $method array body; item properties: $($props -join ', '); required: $($required -join ', '); auth: $auth")
        }
        else {
            if ($schema.properties) {
                $props = @($schema.properties.PSObject.Properties.Name)
            }
            if ($schema.required) {
                $required = @($schema.required)
            }
            $summaries.Add("$($triggerProperty.Name): $method $($schema.type) body; properties: $($props -join ', '); required: $($required -join ', '); auth: $auth")
        }
    }

    return @($summaries.ToArray())
}

function Test-Text {
    param([string]$Text, [string]$Pattern)
    return $Text -match $Pattern
}

function Get-OrderParsingRules {
    param([object]$Flow, [object[]]$Nodes, [string]$AllText)

    $rules = New-Object System.Collections.Generic.List[string]
    $name = [string]$Flow.displayName

    if ($name -match "Manual Control") {
        $rules.Add("Manual starter/helper; no source parser is visible in the flow definition.")
    }
    if (Test-Text $AllText "FlowV1DocumentsJobsParseCsvPost") {
        $rules.Add("CSV attachments are decoded from email attachment `contentBytes`; the first line is split on commas to count columns; generic headers `Column 1..N` are generated; Plumsail `ParseCsv` converts the attachment into JSON.")
    }
    if (Test-Text $AllText "FlowV1DocumentsJobsXls2XlsxPost") {
        $rules.Add("Legacy XLS/XLT files are converted to XLSX through Plumsail before Excel processing.")
    }
    if (Test-Text $AllText "RunScriptProdV2") {
        $rules.Add("Excel Online Office Script runs before row extraction, likely to normalize workbook/table layout.")
    }
    if (Test-Text $AllText "List_rows_present_in_a_table") {
        $rules.Add("Excel table rows are read with `List rows present in a table`; downstream filters remove blank or invalid quantity rows.")
    }
    if (Test-Text $AllText "api.openai.com/v1/chat/completions") {
        $rules.Add("OpenAI chat completion calls identify headers, extract purchase orders, or parse email-body order lines into JSON.")
    }
    if (Test-Text $AllText "us-documentai.googleapis.com|Document AI|documentai") {
        $rules.Add("PDF parsing uses Google Document AI/OAuth endpoints and then maps extracted document/table content into order lines.")
    }
    if ($name -match "Email Body" -and $name -notmatch "Market Place Pet Supplies") {
        $rules.Add("Generic email-body parser extracts `purchaseOrder` and `orderLines` from sender, subject, and email body.")
    }
    if ($name -match "Market Place Pet Supplies") {
        $rules.Add("Customer-specific parser assumes Market Place Pet Supplies orders: PO appears after `PO#` in subject, and body lines follow `itemNumber-quantity`.")
    }
    if (Test-Text $AllText "quantity") {
        $rules.Add("Quantity is normalized to numeric/integer values; rows with missing or non-positive quantity are filtered or sent to review.")
    }
    if (Test-Text $AllText "validatedItemNumber|invalidItemNumber|Validation Needed") {
        $rules.Add("Item numbers/UPCs are validated and unresolved lines are counted for review/validation routing.")
    }
    if ($rules.Count -eq 0) {
        $rules.Add("No parser rule could be inferred from static action names; inspect definition.json manually before migration.")
    }

    return @($rules.ToArray())
}

function Get-OrderOutputs {
    param([object[]]$Nodes, [string]$AllText)

    $outputs = New-Object System.Collections.Generic.List[string]
    if (Test-Text $AllText "CreateFile") {
        $outputs.Add("Creates an output file through SharePoint/OneDrive connector actions.")
    }
    if (Test-Text $AllText "10,@\{formatDateTime") {
        $outputs.Add("Builds Frontier-style CSV line output containing record code/date/PO/sequence/customer/item/quantity fields.")
    }
    if (Test-Text $AllText "PostItem|PatchItem|DeleteItem") {
        $outputs.Add("Writes process state, run log, customer/item status, or cleanup data to SharePoint lists.")
    }
    if (Test-Text $AllText "Categories") {
        $outputs.Add("Updates Outlook categories such as Process, Review, Validate, Do Not Move, or failed states.")
    }
    if (Test-Text $AllText "/move") {
        $outputs.Add("Moves the source email into a CSR/customer folder through Microsoft Graph.")
    }
    if (Test-Text $AllText "subject.*Cust:|Cust:.*Rte:") {
        $outputs.Add("Updates or prefixes email subject with customer/route metadata.")
    }
    if (Test-Text $AllText "SendEmailV2") {
        $outputs.Add("Sends failure or support notification email on error paths.")
    }
    if ($outputs.Count -eq 0) {
        $outputs.Add("No output side effects inferred from static analysis.")
    }
    return @($outputs.ToArray())
}

function Get-CustomerAssumptions {
    param([object]$Flow, [string]$AllText)

    $assumptions = New-Object System.Collections.Generic.List[string]
    if (Test-Text $AllText "orders@frontierdistributing.com") {
        $assumptions.Add("Uses the shared `orders@frontierdistributing.com` mailbox directly.")
    }
    if (Test-Text $AllText "pioneerpetfood.sharepoint.com/sites/Automations") {
        $assumptions.Add("Uses the Pioneer/Frontier SharePoint Automations site for files, lists, or process state.")
    }
    if (Test-Text $AllText "cust_code|cust_csr|csr_email|email_folder|route") {
        $assumptions.Add("Depends on customer code, route, CSR, and/or email folder metadata resolved before or during processing.")
    }
    if ([string]$Flow.displayName -match "Petland" -or (Test-Text $AllText "PETLAND|Branch")) {
        $assumptions.Add("Contains Petland/branch-specific customer identification assumptions.")
    }
    if ([string]$Flow.displayName -match "Dorothy Lane") {
        $assumptions.Add("Contains Dorothy Lane-specific workbook/output assumptions.")
    }
    if ([string]$Flow.displayName -match "Market Place Pet Supplies" -or (Test-Text $AllText "Market Place Pet Supplies")) {
        $assumptions.Add("Hard-codes Market Place Pet Supplies parsing assumptions.")
    }
    if (Test-Text $AllText "OpenAIID|Assistant|model") {
        $assumptions.Add("Reads OpenAI model/key/assistant identifiers from SharePoint-stored configuration.")
    }
    if ($assumptions.Count -eq 0) {
        $assumptions.Add("No customer-specific assumption inferred from static analysis.")
    }
    return @($assumptions.ToArray())
}

function Get-ErrorPaths {
    param([object[]]$Nodes, [string]$AllText)

    $paths = New-Object System.Collections.Generic.List[string]
    $matches = @(
        $Nodes |
            Where-Object {
                $_.operationId -eq "SendEmailV2" -or
                $_.text -match "FAILED|Failed|TimedOut|Needs Attention|Run Script Error|Review|Validate"
            } |
            Select-Object -First 12
    )

    foreach ($node in $matches) {
        $detail = ""
        if ($node.operationId) {
            $detail = $node.operationId
        }
        elseif ($node.type) {
            $detail = $node.type
        }
        $paths.Add("$($node.name) at `$($node.path)` ($detail)")
    }

    if (Test-Text $AllText "Needs Attention - Failed To Process") {
        $paths.Add("Adds `Needs Attention - Failed To Process` category when processing fails.")
    }
    if (Test-Text $AllText "FAILED .*Order|FAILED Email Body|FAILED CSV Parse") {
        $paths.Add("Sends a failure email with original subject/body context.")
    }
    if ($paths.Count -eq 0) {
        $paths.Add("No explicit error path inferred from static analysis.")
    }
    return @($paths.ToArray() | Select-Object -Unique)
}

function Get-MigrationNotes {
    param([object]$Flow, [string]$SourceType, [string]$AllText)

    $notes = New-Object System.Collections.Generic.List[string]
    switch ($SourceType) {
        "CSV attachment" {
            $notes.Add("Replace Plumsail CSV parsing with Azure Function CSV parser; preserve generic-column fallback for headerless CSVs and add robust quoted-field handling.")
        }
        "XLS/XLT attachment" {
            $notes.Add("Replace Plumsail XLS/XLT conversion with backend workbook conversion or require upstream XLSX normalization; keep Office Scripts only if pilot parity proves they are required.")
        }
        "XLSX attachment" {
            $notes.Add("Move workbook/table normalization into Azure code where possible; encode customer-specific header rules in processor profiles.")
        }
        "customer-specific XLSX" {
            $notes.Add("Convert hard-coded customer workbook assumptions into a customer processor profile and golden-file tests.")
        }
        "PDF attachment" {
            $notes.Add("Replace Google Document AI with Azure Document Intelligence and compare extracted table/line accuracy in shadow mode.")
        }
        "email body" {
            $notes.Add("Replace prompt-only parsing with deterministic body parser first, then AI fallback when necessary.")
        }
        "customer-specific email body" {
            $notes.Add("Move customer-specific subject/body rules into customer processor configuration with tests.")
        }
        default {
            $notes.Add("Decide whether this helper remains a Power Automate shell action or is absorbed into Azure orchestration.")
        }
    }
    if (Test-Text $AllText "orders@frontierdistributing.com") {
        $notes.Add("Replace hard-coded shared mailbox with customer-specific `mailboxAccounts` configuration.")
    }
    if (Test-Text $AllText "pioneerpetfood.sharepoint.com") {
        $notes.Add("Replace SharePoint operational reads/writes with Cosmos containers and Blob artifacts.")
    }
    return @($notes.ToArray())
}

$solutionRootPath = Resolve-Path -LiteralPath $SolutionRoot
$flowIndexPath = Join-Path $solutionRootPath "flow-library\flow-index.json"
$outputRootPath = Join-Path (Resolve-Path ".").Path $OutputRoot

if (-not (Test-Path -LiteralPath $flowIndexPath)) {
    throw "Flow index not found: $flowIndexPath"
}

New-Item -ItemType Directory -Force -Path $outputRootPath | Out-Null

$flows = Get-Content -Raw -LiteralPath $flowIndexPath | ConvertFrom-Json
$capabilityRows = New-Object System.Collections.Generic.List[object]

foreach ($flow in ($flows | Sort-Object group, displayName)) {
    $capability = Get-Capability -Flow $flow
    $capabilityRows.Add([pscustomobject]@{
        displayName = $flow.displayName
        slug = $flow.slug
        state = $flow.state
        group = $flow.group
        capability = $capability
        primaryBehavior = Get-PrimaryBehavior -Flow $flow -Capability $capability
        migrationTarget = Get-MigrationTarget -Capability $capability
        triggers = @($flow.triggers | ForEach-Object { $_.Name })
        connectors = @($flow.connectors)
        operationIds = @($flow.operationIds)
        sourceDefinition = $flow.sourceDefinition
        libraryDefinition = $flow.libraryDefinition
    })
}

$orderAnalyses = New-Object System.Collections.Generic.List[object]
foreach ($flow in ($flows | Where-Object { $_.group -eq "03-order-process" } | Sort-Object displayName)) {
    $definitionPath = Join-Path $solutionRootPath $flow.libraryDefinition
    $definition = Get-Content -Raw -LiteralPath $definitionPath | ConvertFrom-Json
    $nodes = Get-WorkflowNodes -Definition $definition
    $allText = ($nodes | ForEach-Object { $_.text }) -join "`n"
    $sourceType = Get-SourceType -FlowName ([string]$flow.displayName)

    $notableActions = @(
        $nodes |
            Where-Object {
                $_.name -match "Parse|CSV|Script|Document|OpenAI|List_rows|CreateFile|Send|Category|Move|Purchase|Item|Validate|Customer|Folder|Response" -or
                $_.operationId -match "FlowV1|RunScript|GetFileContent|CreateFile|SendEmail|HttpRequest|PostItem|PatchItem"
            } |
            Select-Object -First 40 |
            ForEach-Object {
                [pscustomobject]@{
                    name = $_.name
                    path = $_.path
                    type = $_.type
                    connector = $_.connector
                    operationId = $_.operationId
                    method = $_.method
                    parameterKeys = $_.parameterKeys
                }
            }
    )

    $orderAnalyses.Add([pscustomobject]@{
        displayName = $flow.displayName
        slug = $flow.slug
        state = $flow.state
        sourceType = $sourceType
        actionCount = $flow.actionCount
        triggerSchema = Get-TriggerSchemaSummary -Definition $definition
        connectors = @($flow.connectors)
        operationIds = @($flow.operationIds)
        parsingRules = Get-OrderParsingRules -Flow $flow -Nodes $nodes -AllText $allText
        outputFormatAndSideEffects = Get-OrderOutputs -Nodes $nodes -AllText $allText
        customerSpecificAssumptions = Get-CustomerAssumptions -Flow $flow -AllText $allText
        errorPaths = Get-ErrorPaths -Nodes $nodes -AllText $allText
        migrationNotes = Get-MigrationNotes -Flow $flow -SourceType $sourceType -AllText $allText
        notableActions = $notableActions
        sourceDefinition = $flow.sourceDefinition
        libraryDefinition = $flow.libraryDefinition
    })
}

$capabilityRows | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $outputRootPath "flow-capability-map.json") -Encoding UTF8
$orderAnalyses | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $outputRootPath "order-process-flow-analysis.json") -Encoding UTF8

$capabilityLines = New-Object System.Collections.Generic.List[string]
$capabilityLines.Add("# Flow Capability Map")
$capabilityLines.Add("")
$capabilityLines.Add("Generated from `power-automate/solutions/OrdersAutomations/flow-library/flow-index.json`.")
$capabilityLines.Add("")
$capabilityLines.Add("| Flow | State | Capability | Primary Behavior | Migration Target |")
$capabilityLines.Add("| --- | --- | --- | --- | --- |")
foreach ($row in $capabilityRows) {
    $capabilityLines.Add("| $($row.displayName) | $($row.state) | $($row.capability) | $($row.primaryBehavior) | $($row.migrationTarget) |")
}
$capabilityLines.Add("")
$capabilityLines.Add("## Capability Counts")
$capabilityLines.Add("")
foreach ($group in ($capabilityRows | Group-Object capability | Sort-Object Name)) {
    $capabilityLines.Add("- $($group.Name): $($group.Count)")
}
$capabilityLines | Set-Content -LiteralPath (Join-Path $outputRootPath "FLOW_CAPABILITY_MAP.md") -Encoding UTF8

$orderLines = New-Object System.Collections.Generic.List[string]
$orderLines.Add("# Order Process Flow Analysis")
$orderLines.Add("")
$orderLines.Add("Generated from the active local flow library. This file documents the trigger contract, parser behavior, output format, assumptions, and error paths for every order-processing flow in group ``03-order-process``.")
$orderLines.Add("")
$orderLines.Add("## Summary")
$orderLines.Add("")
$orderLines.Add("| Flow | State | Source Type | Key Connectors |")
$orderLines.Add("| --- | --- | --- | --- |")
foreach ($analysis in $orderAnalyses) {
    $orderLines.Add("| $($analysis.displayName) | $($analysis.state) | $($analysis.sourceType) | $(@($analysis.connectors) -join ', ') |")
}
$orderLines.Add("")

foreach ($analysis in $orderAnalyses) {
    $orderLines.Add("## $($analysis.displayName)")
    $orderLines.Add("")
    $orderLines.Add("- State: $($analysis.state)")
    $orderLines.Add("- Source type: $($analysis.sourceType)")
    $orderLines.Add("- Action count: $($analysis.actionCount)")
    $orderLines.Add("- Definition: ``$($analysis.libraryDefinition)``")
    $orderLines.Add("")
    $orderLines.Add("### Input Trigger Schema")
    $orderLines.Add("")
    $orderLines.Add((ConvertTo-MarkdownList -Values $analysis.triggerSchema))
    $orderLines.Add("")
    $orderLines.Add("### Parsing Rules")
    $orderLines.Add("")
    $orderLines.Add((ConvertTo-MarkdownList -Values $analysis.parsingRules))
    $orderLines.Add("")
    $orderLines.Add("### Output Format And Side Effects")
    $orderLines.Add("")
    $orderLines.Add((ConvertTo-MarkdownList -Values $analysis.outputFormatAndSideEffects))
    $orderLines.Add("")
    $orderLines.Add("### Customer-Specific Assumptions")
    $orderLines.Add("")
    $orderLines.Add((ConvertTo-MarkdownList -Values $analysis.customerSpecificAssumptions))
    $orderLines.Add("")
    $orderLines.Add("### Error Paths And Review Behavior")
    $orderLines.Add("")
    $orderLines.Add((ConvertTo-MarkdownList -Values $analysis.errorPaths))
    $orderLines.Add("")
    $orderLines.Add("### Migration Notes")
    $orderLines.Add("")
    $orderLines.Add((ConvertTo-MarkdownList -Values $analysis.migrationNotes))
    $orderLines.Add("")
}

$orderLines | Set-Content -LiteralPath (Join-Path $outputRootPath "ORDER_PROCESS_FLOW_ANALYSIS.md") -Encoding UTF8

$phaseLines = New-Object System.Collections.Generic.List[string]
$phaseLines.Add("# Phase 2 Reverse Engineering")
$phaseLines.Add("")
$phaseLines.Add("Phase 2 captures the current Power Automate behavior so the Azure platform can reproduce it without carrying over unnecessary flow complexity.")
$phaseLines.Add("")
$phaseLines.Add("## Artifacts")
$phaseLines.Add("")
$phaseLines.Add("- FLOW_CAPABILITY_MAP.md: every active reference flow mapped to exactly one migration capability.")
$phaseLines.Add("- flow-capability-map.json: machine-readable flow capability map.")
$phaseLines.Add("- ORDER_PROCESS_FLOW_ANALYSIS.md: trigger schema, parser behavior, outputs, assumptions, error paths, and migration notes for order-processing flows.")
$phaseLines.Add("- order-process-flow-analysis.json: machine-readable order-process analysis.")
$phaseLines.Add("- samples/phase-2: representative synthetic sample emails/files and fixture manifest.")
$phaseLines.Add("")
$phaseLines.Add("## Current Findings")
$phaseLines.Add("")
$flowCount = $flows.Count
$orderAnalysisCount = $orderAnalyses.Count
$phaseLines.Add(("- Active reference flows: {0}." -f $flowCount))
$phaseLines.Add(("- Order-processing group flows: {0}." -f $orderAnalysisCount))
$phaseLines.Add("- Active CSV migration target: orderProcess - CSV Parse.")
$phaseLines.Add("- orderProcess - CSV Parse currently uses Plumsail FlowV1DocumentsJobsParseCsvPost; replacement should be Azure Function CSV parsing.")
$phaseLines.Add("- XLS/XLT flow currently uses Plumsail FlowV1DocumentsJobsXls2XlsxPost; replacement should be backend workbook conversion or upstream XLSX normalization.")
$phaseLines.Add("- PDF flows use Google Document AI and should be replaced by Azure Document Intelligence.")
$phaseLines.Add("- Generic and customer-specific email body flows use OpenAI extraction prompts and should gain deterministic parsers before AI fallback.")
$phaseLines.Add("")
$phaseLines.Add("## Completion Criteria")
$phaseLines.Add("")
$phaseLines.Add("- Every flow is mapped to one capability.")
$phaseLines.Add("- Every order-processing flow is documented with trigger contract, parser behavior, output side effects, customer assumptions, and error paths.")
$phaseLines.Add("- Representative sample fixture placeholders/files exist for CSV, XLSX, XLS/XLT, PDF, email body, and customer-specific cases.")
$phaseLines.Add("")
$phaseLines.Add("Generated: $(Get-Date -Format o)")
$phaseLines | Set-Content -LiteralPath (Join-Path $outputRootPath "PHASE_2_REVERSE_ENGINEERING.md") -Encoding UTF8

Write-Host "Wrote reverse-engineering artifacts to $outputRootPath"
Write-Host "Mapped $($capabilityRows.Count) flows and analyzed $($orderAnalyses.Count) order-processing flows."
