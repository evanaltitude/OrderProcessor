param(
    [string]$PackagePath = ".\onboarding\reference\pilot-csv-parse\onboarding-package.json",
    [switch]$SkipFixtures
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $Root "src"

$Arguments = @("-m", "order_processor.onboarding", $PackagePath)
if ($SkipFixtures) {
    $Arguments += "--skip-fixtures"
}

python @Arguments
