# Order Processor

Reference workspace for rebuilding the current Power Automate order-processing flow set into a repeatable multi-customer software solution.

## New Platform Scaffold

The Azure-first rebuild is now tracked in these locations:

- [Phase plan](docs/ORDER_PROCESSOR_PHASE_PLAN.md)
- [Current system map](docs/CURRENT_SYSTEM_MAP.md)
- [Phase 1 baseline](docs/PHASE_1_BASELINE.md)
- [Phase 2 reverse engineering](docs/reverse-engineering/PHASE_2_REVERSE_ENGINEERING.md)
- [Phase 3 Azure foundation](docs/PHASE_3_AZURE_FOUNDATION.md)
- [Phase 4 core data model](docs/PHASE_4_CORE_DATA_MODEL.md)
- [Phase 5 Power Automate shell](docs/PHASE_5_POWER_AUTOMATE_SHELL.md)
- [Phase 6 email ingestion and routing](docs/PHASE_6_EMAIL_INGESTION_ROUTING.md)
- [Phase 7 customer identification](docs/PHASE_7_CUSTOMER_IDENTIFICATION.md)
- [Phase 8 customer and item data refresh](docs/PHASE_8_CUSTOMER_ITEM_REFRESH.md)
- [Phase 9 order processing engine](docs/PHASE_9_ORDER_PROCESSING_ENGINE.md)
- [Phase 10 item validation service](docs/PHASE_10_ITEM_VALIDATION_SERVICE.md)
- [Phase 11 output generation](docs/PHASE_11_OUTPUT_GENERATION.md)
- [Phase 12 console backend and frontend](docs/PHASE_12_CONSOLE_BACKEND_FRONTEND.md)
- [Phase 13 observability and audit](docs/PHASE_13_OBSERVABILITY_AUDIT.md)
- [Phase 14 pilot migration](docs/PHASE_14_PILOT_MIGRATION.md)
- [Phase 15 repeatable onboarding](docs/PHASE_15_REPEATABLE_ONBOARDING.md)
- [Customer onboarding checklist](docs/ONBOARDING_CHECKLIST.md)
- [Flow capability map](docs/reverse-engineering/FLOW_CAPABILITY_MAP.md)
- [Order-process behavior analysis](docs/reverse-engineering/ORDER_PROCESS_FLOW_ANALYSIS.md)
- [API contracts](docs/API_CONTRACTS.md)
- [Cosmos model](docs/COSMOS_MODEL.md)
- [Power Automate shell design](docs/POWER_AUTOMATE_SHELL.md)
- [Core Python package](src/order_processor)
- [Azure Functions facade](apps/functions)
- [Console Web App](apps/console)
- [Azure infrastructure](infra)
- [Customer onboarding packages](onboarding)
- [Pilot fixtures](samples/pilot)
- [Phase 2 synthetic fixtures](samples/phase-2)
- [OrderProcessor Power Platform shell](power-automate/solutions/OrderProcessor)

Local verification:

```powershell
python -m unittest discover -s tests
python -m compileall src apps
az bicep build --file .\infra\main.bicep
az bicep build --file .\infra\subscription.bicep
```

Pilot shadow-run verification:

```powershell
$env:PYTHONPATH = ".\src"
python -m order_processor.pilot_shadow .\samples\pilot\pilot-shadow-manifest.json
```

Customer onboarding package verification:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Validate-OnboardingPackage.ps1
```

`azd` and Azure Functions Core Tools are required for full local cloud workflow/deployment but are not installed on this machine yet.

## Current Library

The initial reference library was exported from:

- Power Platform environment: Orders Automations (`e51ca662-f633-e290-b476-202747054118`)
- Solution display name: Orders Automations
- Solution unique name: `OrdersAutomations`
- Export user: `automations@frontierdistributing.com`

Start here:

- [Flow library](power-automate/solutions/OrdersAutomations/flow-library/README.md)
- [SharePoint reference](power-automate/solutions/OrdersAutomations/sharepoint-reference/README.md)
- [External dependency reference](power-automate/solutions/OrdersAutomations/external-dependencies/README.md)
- [Raw solution export](power-automate/solutions/OrdersAutomations/exports/OrdersAutomations_1.0.0.0_unmanaged.zip)
- [Unpacked solution components](power-automate/solutions/OrdersAutomations/unpacked)

## Refresh

From this folder, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Export-OrdersAutomations.ps1
```

That script exports the unmanaged solution, unpacks it, and rebuilds the organized flow library.

To rebuild the SharePoint dependency inventory and fetch list config/items after Graph auth is available:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Export-SharePointReference.ps1
```

To rebuild the broader external dependency inventory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Export-ExternalDependencies.ps1
```

To rebuild the Phase 2 reverse-engineering reports and synthetic fixture set:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Build-ReverseEngineering.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\New-Phase2SampleFixtures.ps1
```

To rebuild the new `OrderProcessor` Power Automate shell templates and unmanaged solution package:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Build-OrderProcessorShellSolution.ps1
```

Treat exported definitions as sensitive because Power Automate exports can include environment-specific endpoints, connection references, and direct trigger URLs.

The legacy `OrdersAutomations` export/reference folder remains local-only and is ignored from GitHub because the exported flow definitions can contain direct workflow callback URLs and API key material. Regenerate it locally with the refresh scripts when needed.
