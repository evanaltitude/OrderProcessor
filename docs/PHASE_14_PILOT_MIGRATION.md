# Phase 14 Pilot Migration

Completed: 2026-06-21

## Scope

Phase 14 turns the pilot migration step into an executable local shadow-run package. The selected pilot is the active `orderProcess CSV Parse` path, because it is the only active CSV reference flow and it exercises the highest-value replacement decision: parsing CSV orders without the Plumsail CSV-to-XLSX connector.

This phase does not cut over production traffic. It creates the repeatable pilot structure needed to compare the new Azure-backed platform against approved legacy outputs while current Power Automate flows remain untouched.

## Pilot Selection

Selected pilot customer:

- Customer id: `pilot-customer`
- Customer code: `PILOT`
- Reference flow: `orderProcess CSV Parse`
- Input type: CSV attachment from a customer-specific monitored Microsoft mailbox
- Output type: line-level CSV artifact plus universal order JSON
- CSR folder expectation: `CSR/Pilot CSV Parse`

The pilot fixture is synthetic and safe to keep in source control. Real production samples still need business approval before they can be added to the same manifest structure for final parity decisions.

## Implemented Artifacts

Pilot data lives in `samples/pilot`:

- `pilot-shadow-manifest.json`: the shadow-run contract, selected customer, seed files, email fixture, source file, expected outputs, and acceptance expectations.
- `customers.json`: customer config, sender domain, subject patterns, and CSR folder.
- `mailboxes.json`: customer-specific Microsoft mailbox config in shadow mode.
- `routing-rules.json`: mailbox/sender/subject/attachment routing rule with routing tags.
- `processor-profiles.json`: CSV Parse processor profile with `usesPlumsail: false`.
- `output-profiles.json`: shadow-only CSV output adapter.
- `items.json`: canonical item records used for validation.
- `sample-order.csv`: synthetic order attachment.
- `expected-line-output.csv`: legacy-shadow expected line output.

The executable harness is `src/order_processor/pilot_shadow.py`. It seeds the same repository containers used by the backend, ingests the pilot email, processes the CSV order, stores output artifacts in the in-memory artifact store, and compares the Phase 14 gates.

Run locally:

```powershell
$env:PYTHONPATH = ".\src"
python -m order_processor.pilot_shadow .\samples\pilot\pilot-shadow-manifest.json
```

## Acceptance Gates Covered

The harness currently checks:

- Routing outcome is `knownOrder`.
- Customer identification resolves to the pilot customer through mailbox/routing configuration.
- The processed order status is `completed`.
- Routing tags match the pilot rule.
- CSR folder config matches the expected CSR destination.
- Item validation resolves every line to the expected internal item numbers.
- Unresolved item count is zero.
- Exception task count is zero.
- Output artifact types are universal order JSON and line CSV.
- Line CSV output matches `expected-line-output.csv`.
- Output delivery remains `shadowOnly` with production delivery disabled.
- The active CSV profile declares `usesPlumsail: false`.
- Order errors are empty.

## Shadow Mode Boundary

The pilot output profile writes to a `shadowBlob` destination with `productionDeliveryEnabled: false`. This records intended delivery metadata without moving mailbox folders, writing customer production outputs, or invoking external adapters.

When live samples are approved, add them as additional manifests or expand the current manifest with multiple cases. The current harness should remain the parity runner for those samples.

## Go-Live Gate

Production cutover remains blocked until all of the following are true:

- Approved real pilot emails and attachments are added to the pilot manifest structure.
- The new platform matches or improves current outputs for customer identification, item validation, output files, routing tags, CSR folder moves, and error handling.
- Microsoft Graph mailbox access is validated against the real pilot mailbox.
- Shadow output artifacts are reviewed by operations.
- Any customer-specific output or CSR routing behavior is either implemented in Azure or explicitly retained as a Power Automate adapter.

## Verification

Added tests:

- `tests/test_pilot_shadow.py`

The full local suite verifies the pilot shadow package and guards the comparator against false positives by asserting that an expected-output mismatch fails the run.
