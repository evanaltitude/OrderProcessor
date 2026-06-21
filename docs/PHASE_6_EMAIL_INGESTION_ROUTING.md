# Phase 6 Email Ingestion and Routing

Phase 6 is complete as of 2026-06-20. The backend now treats `/emails/ingest` as the durable handoff point from Power Automate mailbox adapters into Azure-owned processing.

## Implemented Scope

- Ingested email metadata is normalized into `EmailMessage`.
- Attachments are stored as metadata/reference records, not binary content.
- Mailbox account context is resolved from `mailboxAccountId` or mailbox address.
- Customer-specific mailbox accounts scope routing to the owning customer.
- Data-driven `routingRules` determine whether the message is an order, non-order, needs identification, needs human review, or should be ignored.
- Routing decisions are stored on the email record and emitted in the API response.
- `knownOrder` creates an `orderRun`.
- `needsCustomerIdentification` and `needsHumanReview` create `exceptionTasks`.
- `knownCustomerNonOrder` and `ignored` do not create order runs.
- Every ingest records an `auditEvents` entry with routing details and mailbox context.

## Stored Email Record

`emailMessages` now carries:

- `id`
- `tenantId`
- `mailbox`
- `mailboxAccountId`
- `messageId`
- `sender`
- `subject`
- `receivedAt`
- `bodyText`
- `bodyHtml`
- `categories`
- `attachments`
- `status`
- `customerId`
- `orderRunId`
- `correlationId`
- `routing`
- `source`
- `createdAt`
- `updatedAt`

Attachment records carry `name`, `contentType`, `size`, `blobUrl`, `sourceUrl`, `contentId`, `isInline`, and `metadata`. Actual attachment content should be stored in Blob Storage by the adapter/backend integration and referenced from `blobUrl`.

## Mailbox Resolution

The ingest API resolves mailbox configuration in this order:

1. Use `mailboxAccountId` when provided.
2. Fall back to exact normalized mailbox address lookup in `mailboxAccounts`.
3. Continue without mailbox configuration when only a raw mailbox address is available.

Resolved mailbox accounts set `email.mailboxAccountId` and, when the mailbox owns a customer, scope `email.customerId`.

Special cases:

- Disabled mailbox accounts route to `ignored`.
- A provided but unknown `mailboxAccountId` routes to `needsHumanReview`.
- A payload `customerId` that conflicts with the resolved mailbox customer routes to `needsHumanReview` and records the mailbox customer as the effective customer.

## Routing Rule Signals

Rules are evaluated by priority and remain data, not flow branches. Supported signals:

- `mailboxAccountIds`
- `mailboxAddresses`
- `senderEquals`
- `senderDomains`
- `subjectRegex`
- `bodyRegex`
- `knownWebstorePatterns`
- `priorProcessedSubjectRegex`
- `attachmentExtensions`
- `attachmentContentTypes`
- `attachmentNameRegex`
- `requiredAttachment`
- `tags`

Tenant-wide routing rules use `customerId: "_global"`. When a customer-specific mailbox scopes the email to a customer, the router considers global rules and that customer's rules, preventing another customer's rule from claiming the message.

## Rule Examples

Known customer CSV order:

```json
{
  "id": "pilot-csv-orders",
  "tenantId": "altitude",
  "customerId": "pilot-customer",
  "name": "Pilot CSV orders",
  "outcome": "knownOrder",
  "priority": 10,
  "mailboxAccountIds": ["pilot-mailbox"],
  "senderDomains": ["pilot.example"],
  "subjectRegex": ["\\bPO\\b|\\border\\b"],
  "attachmentExtensions": ["csv"],
  "requiredAttachment": true,
  "processorProfileId": "pilot-csv-profile"
}
```

Already-processed customer reply:

```json
{
  "id": "pilot-processed-replies",
  "tenantId": "altitude",
  "customerId": "pilot-customer",
  "name": "Pilot processed replies",
  "outcome": "knownCustomerNonOrder",
  "priority": 20,
  "priorProcessedSubjectRegex": ["\\bprocessed\\b", "\\bcompleted\\b"]
}
```

Tenant-wide webstore notification ignore rule:

```json
{
  "id": "webstore-shipment-notices",
  "tenantId": "altitude",
  "customerId": "_global",
  "name": "Webstore shipment notices",
  "outcome": "ignored",
  "priority": 30,
  "senderDomains": ["webstore.example"],
  "knownWebstorePatterns": ["shipment confirmation", "tracking number"]
}
```

## Outcomes

| Outcome | Email status | Follow-up |
| --- | --- | --- |
| `knownOrder` | `routed` | Creates `orderRun` with customer and processor profile from the matched rule/context. |
| `knownCustomerNonOrder` | `routed` | Stores the email and decision only. |
| `needsCustomerIdentification` | `needsReview` | Creates routing/customer-identification exception task. |
| `needsHumanReview` | `needsReview` | Creates routing exception task. |
| `ignored` | `ignored` | Stores the email and decision only. |

If no rule matches and the email has no customer context, the outcome is `needsCustomerIdentification`. If no rule matches but a customer-specific mailbox already scopes the email, the outcome is `needsHumanReview`; this avoids silently processing known-customer mail without an explicit data rule.

## Validation

Tests added or expanded:

- `tests/test_routing.py`
- `tests/test_api.py`

Coverage includes:

- Known CSV/XLSX order routing.
- Customer-specific mailbox scoping.
- Filtering out other customers' routing rules.
- Disabled mailbox ignored behavior.
- Unknown mailbox account id review behavior.
- Payload customer/mailbox customer conflict handling.
- Prior processed subject patterns.
- Known webstore patterns.
- Attachment extension and content-type matching.
- Invalid regex safety.
- Persisted email routing decision and attachment metadata.

## Handoff to Phase 7

Phase 6 deliberately stops before customer identification logic beyond routing disposition. Phase 7 should build on the `needsCustomerIdentification` path by resolving customers through deterministic extraction first and Azure OpenAI/Cosmos vector fallback second. When a customer is resolved, the console/backend reprocess path should be able to re-run routing or continue order processing using the corrected customer.
