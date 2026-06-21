# Phase 10 Item Validation Service

Completed on 2026-06-20.

## Scope Completed

Phase 10 rebuilt `Module - Item Number Validator` as the Azure backend `/items/validate` endpoint.

Implemented files:

- `src/order_processor/item_validation.py`
- `src/order_processor/api.py`
- `src/order_processor/models.py`
- `tests/test_item_validation.py`
- `tests/test_item_validation_service.py`
- `docs/API_CONTRACTS.md`
- `docs/COSMOS_MODEL.md`
- `infra/openapi/order-processor-api.yaml`

## Service Contract

`POST /items/validate` accepts:

- `tenantId`
- `customerId`
- `providedItemNumber`
- `providedUpc`
- `description`
- optional `rowContext`
- optional `orderRunId`
- optional `lineNumber`
- optional `confidenceThreshold`
- optional `possibleMatchThreshold`
- optional `candidateLimit`

The endpoint normally loads customer-scoped item records from Cosmos. Tests and controlled callers may pass `items` directly.

## Matching Behavior

The matcher evaluates:

- exact internal/customer/alias item number matches
- exact UPC matches
- fuzzy item number matches
- fuzzy description matches

The result includes:

- validation status: `matched`, `possibleMatch`, or `unresolved`
- matched item id
- matched internal item number
- match method
- confidence
- candidate list
- unresolved reason when review is required

Ambiguous high-confidence matches are intentionally returned as `possibleMatch` so a console user can choose the correct item instead of silently picking one.

## Row Context

`rowContext` is supported for customer-specific source rows and Phase 9 parser output. If direct request fields are blank, the service can infer values from common source keys such as:

- `Vendor Item`
- `Item Number`
- `SupplierCode`
- `UPC`
- `Barcode`
- `Description`
- `Column 1`
- `Column 2`

This gives parser modules a stable way to pass raw rows without adding custom endpoint branches for every customer.

## Order Line Updates

When `orderRunId` and `lineNumber` are supplied, the endpoint updates the matching `orderRuns.lines[]` entry with:

- `matchedInternalItemNumber`
- `validationStatus`
- `validationConfidence`
- `validationMethod`
- `validationCandidates`
- `validationErrors`

If unresolved or possible matches remain, the order run is marked `needsReview`. If all lines are confidently matched and the order is not failed, it can move to `completed`.

## Console Tasks and Audit

When the result is not `matched`, the endpoint creates an `exceptionTasks` record with:

- `type: "itemValidation"`
- `orderRunId`
- `lineNumber`
- compact request context
- row context
- full validation result

Every call records an `auditEvents` entry with `eventType: "item.validated"`, status, confidence, match method, candidate count, matched item number, and exception task id when created.

## Test Coverage

Added and expanded tests for:

- token normalization
- exact customer item number matching
- row-context fallback
- possible match below threshold
- ambiguous exact matches
- unresolved customer scope
- endpoint match response
- persisted order-line validation update
- endpoint row-context handling
- candidate limit behavior
- unresolved-line exception creation
- possible-match exception creation
- validation audit events

Full local suite status after implementation:

```powershell
python -m unittest discover -s tests
# Ran 74 tests - OK
```

## Known Follow-Ups

- Phase 12 console work should expose item-validation tasks with candidate selection and line revalidation controls.
- Future vector/semantic item matching can extend the candidate scorer using `items.embedding`; the current Phase 10 implementation stays deterministic and auditable.
