# Customer Onboarding Checklist

Use this checklist for every customer package before moving from shadow mode to production cutover.

## 1. Customer Profile

- Create a stable customer id and customer code.
- Record customer name, store number, route number, CSR email, CSR folder, sender domains, aliases, and known subject patterns.
- Confirm the customer is not represented by a deprecated flow path.

## 2. Microsoft Access

- Create or select the Microsoft auth connection.
- Store secrets only in Key Vault.
- Confirm required mailbox scopes: `Mail.Read` and, if folder moves or categories are needed, `Mail.ReadWrite`.
- Add the monitored mailbox config and mark production actions disabled until cutover.
- Run live Graph mailbox validation before go-live.

## 3. Console Access

- Keep `connect@focuseautomate.com` as the bootstrap platform admin.
- Add customer Microsoft users by email.
- Assign each customer user to the correct customer id and role.
- Confirm unassigned users cannot access the customer dashboard.

## 4. Routing And Processing

- Add customer-scoped routing rules for mailbox, sender, subject/body patterns, attachment type, and filename patterns.
- Add processor profiles for each input type.
- For CSV customers, keep `usesPlumsail: false` and parse with backend code.
- Add output profiles in `shadowOnly` mode first.
- Record CSR folder and exception queue routing.

## 5. Data Refresh

- Define customer list source, owner, refresh cadence, parser, and field map.
- Define item list source, owner, refresh cadence, parser, and field map.
- Preserve original source rows in Blob Storage during real imports.
- Confirm duplicate/missing/malformed rows are covered by tests.

## 6. Fixtures

- Add approved sample emails and files for every input type used by the customer.
- Include expected output artifacts from the current production flow.
- Include negative fixtures for unresolved customer, unresolved item, parser failure, and non-order email paths where applicable.
- Keep sensitive real data out of source control unless explicitly approved and sanitized.

## 7. Validation

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Validate-OnboardingPackage.ps1 -PackagePath .\onboarding\reference\pilot-csv-parse\onboarding-package.json
python -m unittest tests.test_onboarding
```

The package must pass with fixtures enabled before shadow mode is considered complete.

## 8. Batch Migration

- Start with one pilot customer.
- Batch remaining customers by processor family, output adapter, mailbox/auth readiness, item data quality, and exception volume.
- Keep batches small enough that operations can review every shadow output.
- Do not cut over a batch until every customer in the batch has passing package validation and accepted real-sample shadow results.

## 9. Cutover Gate

- Package validator passes.
- Real shadow fixtures pass.
- Graph mailbox test succeeds.
- Operations approves output files, CSR routing, and exception handling.
- Rollback path is documented.
- Production delivery flags are deliberately enabled only at cutover.
