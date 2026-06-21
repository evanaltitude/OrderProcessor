# Phase 15 Repeatable Onboarding

Completed: 2026-06-21

## Scope

Phase 15 packages onboarding as a repeatable customer migration unit. Each package captures the customer profile, monitored mailbox, Microsoft auth setup, console access, routing rules, parser profiles, output adapters, customer/item source refreshes, CSR routing, fixtures, and batch migration plan.

The pilot CSV Parse customer is now the first reference implementation.

## Added Artifacts

- `onboarding/templates/customer-onboarding-template.json`: copy-start template for a new customer onboarding package.
- `onboarding/reference/pilot-csv-parse/onboarding-package.json`: pilot customer reference package that points at the Phase 14 pilot config and fixture files.
- `onboarding/README.md`: quick-start instructions for the package format.
- `docs/ONBOARDING_CHECKLIST.md`: operational checklist for customer setup, Microsoft access, console access, data refresh, fixtures, validation, batching, and cutover.
- `src/order_processor/onboarding.py`: automated package validator and CLI.
- `tools/Validate-OnboardingPackage.ps1`: PowerShell wrapper for local validation.
- `tests/test_onboarding.py`: automated validation coverage for the reference package and failure cases.

## Validation Coverage

The validator checks:

- package id and tenant id
- customer profile completeness and CSR folder
- Microsoft auth connection mail scopes and no inline secret values
- monitored mailbox customer scope, email format, and auth connection linkage
- bootstrap admin plus customer console user assignments
- customer-scoped processor profiles with supported processor types and no Plumsail usage
- routing rules with known mailbox/profile references and concrete matchers
- customer-scoped output profiles with shadow-safe delivery before cutover
- customer and item import sources with owners, cadence, and field maps
- CSR routing matching the customer profile
- at least one fixture, with executable fixture validation when enabled
- pilot-first migration batches and remaining-customer strategy

Run from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Validate-OnboardingPackage.ps1
```

Or:

```powershell
$env:PYTHONPATH = ".\src"
python -m order_processor.onboarding .\onboarding\reference\pilot-csv-parse\onboarding-package.json
```

## Batch Migration Strategy

Batch 1 is the pilot CSV Parse customer. Later batches should be grouped by:

- processor family: CSV, workbook, PDF, email body, customer override
- output adapter shape: CSV/XLSX/text/API/M365 delivery
- mailbox/auth readiness
- quality of customer and item source data
- expected exception volume
- CSR routing complexity

Every customer package must pass validation with fixtures enabled before moving from shadow mode to production cutover.

## Remaining External Gates

The package and validator are complete for local onboarding readiness. Production onboarding still requires live Microsoft Graph mailbox validation, approved real sample emails/files, production Azure deployment, and operations sign-off.
