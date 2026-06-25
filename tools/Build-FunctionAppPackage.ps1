param(
    [string]$OutputPath = ".deploy/functions-posix.zip",
    [switch]$SkipDependencies
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$deployRoot = Join-Path $repoRoot ".deploy"
$stagingRoot = Join-Path $deployRoot "functions"
$outputFullPath = Join-Path $repoRoot $OutputPath

if (-not (Test-Path $deployRoot)) {
    New-Item -ItemType Directory -Path $deployRoot | Out-Null
}

if (Test-Path $stagingRoot) {
    $resolvedStaging = (Resolve-Path $stagingRoot).Path
    if (-not $resolvedStaging.StartsWith($deployRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean unexpected staging path: $resolvedStaging"
    }
    Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
}

New-Item -ItemType Directory -Path $stagingRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot "apps/functions/function_app.py") -Destination $stagingRoot -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "apps/functions/host.json") -Destination $stagingRoot -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "apps/functions/requirements.txt") -Destination $stagingRoot -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "src") -Destination (Join-Path $stagingRoot "src") -Recurse -Force

if (-not $SkipDependencies) {
    $sitePackages = Join-Path $stagingRoot ".python_packages/lib/site-packages"
    New-Item -ItemType Directory -Path $sitePackages | Out-Null
    python -m pip install `
        --requirement (Join-Path $repoRoot "apps/functions/requirements.txt") `
        --target $sitePackages `
        --platform manylinux2014_x86_64 `
        --implementation cp `
        --python-version 3.11 `
        --only-binary=:all: `
        --upgrade
}

$routes = @(
    @{ Name = "emails_ingest"; EntryPoint = "emails_ingest"; Route = "emails/ingest" },
    @{ Name = "orders_process"; EntryPoint = "orders_process"; Route = "orders/{orderRunId}/process" },
    @{ Name = "customers_identify"; EntryPoint = "customers_identify"; Route = "customers/identify" },
    @{ Name = "items_validate"; EntryPoint = "items_validate"; Route = "items/validate" },
    @{ Name = "imports_customers"; EntryPoint = "imports_customers"; Route = "imports/customers"; QueueOutput = "%ORDER_PROCESSOR_IMPORT_JOB_QUEUE%" },
    @{ Name = "imports_items"; EntryPoint = "imports_items"; Route = "imports/items"; QueueOutput = "%ORDER_PROCESSOR_IMPORT_JOB_QUEUE%" },
    @{ Name = "mailboxes_upsert"; EntryPoint = "mailboxes_upsert"; Route = "mailboxes" },
    @{ Name = "mailboxes_test_connection"; EntryPoint = "mailboxes_test_connection"; Route = "mailboxes/{id}/test-connection" },
    @{ Name = "mailboxes_poll"; EntryPoint = "mailboxes_poll"; Route = "mailboxes/poll" },
    @{ Name = "mailboxes_subscriptions_sync"; EntryPoint = "mailboxes_subscriptions_sync"; Route = "mailboxes/subscriptions/sync" },
    @{ Name = "graph_notifications"; EntryPoint = "graph_notifications"; Route = "graph/notifications" },
    @{ Name = "orders_timeline"; EntryPoint = "orders_timeline"; Route = "orders/{orderRunId}/timeline" },
    @{ Name = "console_session"; EntryPoint = "console_session"; Route = "console/session" },
    @{ Name = "console_dashboard"; EntryPoint = "console_dashboard"; Route = "console/dashboard" },
    @{ Name = "console_artifacts_download"; EntryPoint = "console_artifacts_download"; Route = "console/artifacts/download" },
    @{ Name = "console_routing_rules_upsert"; EntryPoint = "console_routing_rules_upsert"; Route = "console/routing-rules" },
    @{ Name = "console_customers_upsert"; EntryPoint = "console_customers_upsert"; Route = "console/customers" },
    @{ Name = "console_mailboxes_upsert"; EntryPoint = "console_mailboxes_upsert"; Route = "console/mailboxes" },
    @{ Name = "console_mailboxes_test_connection"; EntryPoint = "console_mailboxes_test_connection"; Route = "console/mailboxes/{id}/test-connection" },
    @{ Name = "console_microsoft_auth_start"; EntryPoint = "console_microsoft_auth_start"; Route = "console/microsoft-auth/start" },
    @{ Name = "console_microsoft_auth_callback"; EntryPoint = "console_microsoft_auth_callback"; Route = "console/microsoft-auth/callback" },
    @{ Name = "console_tenants_upsert"; EntryPoint = "console_tenants_upsert"; Route = "console/tenants" },
    @{ Name = "console_customer_identification_rules_upsert"; EntryPoint = "console_customer_identification_rules_upsert"; Route = "console/customer-identification-rules" },
    @{ Name = "console_processor_profiles_upsert"; EntryPoint = "console_processor_profiles_upsert"; Route = "console/processor-profiles" },
    @{ Name = "console_output_profiles_upsert"; EntryPoint = "console_output_profiles_upsert"; Route = "console/output-profiles" },
    @{ Name = "console_users_upsert"; EntryPoint = "console_users_upsert"; Route = "console/users" },
    @{ Name = "console_customer_users_assign"; EntryPoint = "console_customer_users_assign"; Route = "console/customers/{customerId}/users" },
    @{ Name = "console_exceptions_resolve"; EntryPoint = "console_exceptions_resolve"; Route = "console/exceptions/{id}/resolve" },
    @{ Name = "console_orders_reprocess"; EntryPoint = "console_orders_reprocess"; Route = "console/orders/{orderRunId}/reprocess" },
    @{ Name = "console_orders_timeline"; EntryPoint = "console_orders_timeline"; Route = "console/orders/{orderRunId}/timeline" },
    @{ Name = "customer_users_assign"; EntryPoint = "customer_users_assign"; Route = "customers/{customerId}/users" },
    @{ Name = "exceptions_resolve"; EntryPoint = "exceptions_resolve"; Route = "exceptions/{id}/resolve" },
    @{ Name = "orders_reprocess"; EntryPoint = "orders_reprocess"; Route = "orders/{orderRunId}/reprocess" }
)

foreach ($route in $routes) {
    $routeDir = Join-Path $stagingRoot $route.Name
    New-Item -ItemType Directory -Path $routeDir | Out-Null
    $bindings = @(
        @{
            authLevel = "anonymous"
            type = "httpTrigger"
            direction = "in"
            name = "req"
            methods = @("post")
            route = $route.Route
        }
    )
    if ($route.ContainsKey("QueueOutput")) {
        $bindings += @{
            type = "queue"
            direction = "out"
            name = "queued"
            queueName = $route.QueueOutput
            connection = "AzureWebJobsStorage"
        }
    }
    $bindings += @{
        type = "http"
        direction = "out"
        name = '$return'
    }
    $metadata = @{
        scriptFile = "../function_app.py"
        entryPoint = $route.EntryPoint
        bindings = $bindings
    } | ConvertTo-Json -Depth 10
    Set-Content -LiteralPath (Join-Path $routeDir "function.json") -Value $metadata -Encoding ASCII
}

$timers = @(
    @{ Name = "graph_subscription_renewal_timer"; EntryPoint = "graph_subscription_renewal_timer"; Schedule = "%ORDER_PROCESSOR_GRAPH_SUBSCRIPTION_RENEWAL_CRON%" }
)

foreach ($timer in $timers) {
    $timerDir = Join-Path $stagingRoot $timer.Name
    New-Item -ItemType Directory -Path $timerDir | Out-Null
    $metadata = @{
        scriptFile = "../function_app.py"
        entryPoint = $timer.EntryPoint
        bindings = @(
            @{
                type = "timerTrigger"
                direction = "in"
                name = "timer"
                schedule = $timer.Schedule
                runOnStartup = $false
                useMonitor = $true
            }
        )
    } | ConvertTo-Json -Depth 10
    Set-Content -LiteralPath (Join-Path $timerDir "function.json") -Value $metadata -Encoding ASCII
}

$queueTriggers = @(
    @{ Name = "graph_notifications_queue"; EntryPoint = "graph_notifications_queue"; QueueName = "%ORDER_PROCESSOR_GRAPH_NOTIFICATION_QUEUE%" },
    @{ Name = "import_jobs_queue"; EntryPoint = "import_jobs_queue"; QueueName = "%ORDER_PROCESSOR_IMPORT_JOB_QUEUE%" }
)

foreach ($queueTrigger in $queueTriggers) {
    $queueDir = Join-Path $stagingRoot $queueTrigger.Name
    New-Item -ItemType Directory -Path $queueDir | Out-Null
    $metadata = @{
        scriptFile = "../function_app.py"
        entryPoint = $queueTrigger.EntryPoint
        bindings = @(
            @{
                type = "queueTrigger"
                direction = "in"
                name = "msg"
                queueName = $queueTrigger.QueueName
                connection = "AzureWebJobsStorage"
            }
        )
    } | ConvertTo-Json -Depth 10
    Set-Content -LiteralPath (Join-Path $queueDir "function.json") -Value $metadata -Encoding ASCII
}

Get-ChildItem -Path $stagingRoot -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
if (Test-Path $outputFullPath) {
    Remove-Item -LiteralPath $outputFullPath -Force
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($outputFullPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    $stagingRootWithSeparator = $stagingRoot.TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
    Get-ChildItem -LiteralPath $stagingRoot -Recurse -File | ForEach-Object {
        $relativePath = $_.FullName.Substring($stagingRootWithSeparator.Length).Replace("\", "/")
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip,
            $_.FullName,
            $relativePath,
            [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
}
finally {
    $zip.Dispose()
}
Write-Host "Built Function package: $outputFullPath"
