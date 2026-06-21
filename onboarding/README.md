# Order Processor Onboarding

This folder contains the repeatable customer onboarding package format introduced in Phase 15.

Start with:

- `templates/customer-onboarding-template.json`
- `reference/pilot-csv-parse/onboarding-package.json`

Validate a package from the repository root:

```powershell
$env:PYTHONPATH = ".\src"
python -m order_processor.onboarding .\onboarding\reference\pilot-csv-parse\onboarding-package.json
```

The validator checks customer profile, Microsoft mailbox/auth setup, console user assignments, routing rules, processor profiles, output profiles, import sources, CSR routing, migration batches, and test fixtures. For the pilot reference package it also runs the Phase 14 shadow-run fixture.
