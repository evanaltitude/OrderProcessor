param(
    [string]$EnvironmentId = "e51ca662-f633-e290-b476-202747054118",
    [string]$SolutionName = "OrdersAutomations",
    [string]$SolutionVersion = "1.0.0.0",
    [string]$SolutionRoot = ".\power-automate\solutions\OrdersAutomations"
)

$ErrorActionPreference = "Stop"

$solutionRootPath = Join-Path (Get-Location) $SolutionRoot
$exportsRoot = Join-Path $solutionRootPath "exports"
$unpackedRoot = Join-Path $solutionRootPath "unpacked"
$logsRoot = Join-Path $solutionRootPath "logs"
$zipPath = Join-Path $exportsRoot "$($SolutionName)_$($SolutionVersion)_unmanaged.zip"
$unpackLogPath = Join-Path $logsRoot "unpack.log"
$builderPath = Join-Path (Split-Path -Parent $PSCommandPath) "Build-FlowLibrary.ps1"

New-Item -ItemType Directory -Force -Path $exportsRoot, $unpackedRoot, $logsRoot | Out-Null

pac solution export `
    --environment $EnvironmentId `
    --name $SolutionName `
    --path $zipPath `
    --overwrite `
    --async `
    --max-async-wait-time 60

pac solution unpack `
    --zipfile $zipPath `
    --folder $unpackedRoot `
    --packagetype Unmanaged `
    --allowWrite `
    --clobber `
    --log $unpackLogPath

& $builderPath -SolutionRoot $SolutionRoot
