# Phase 4 Core Data Model

Phase 4 is complete. The platform now has a concrete canonical data model, a Cosmos container manifest, repository normalization rules, customer-scoped partition strategy, mailbox/auth/console authorization models, and tests that protect the contract.

## Artifacts

- `src/order_processor/data_model.py`: canonical Cosmos container definitions, partition paths, vector metadata, customer-scope rules, document normalization, and partition-key helpers.
- `src/order_processor/models.py`: dataclasses for all canonical models listed in the phase plan.
- `src/order_processor/storage.py`: in-memory and Cosmos repository implementations with shared normalization/validation.
- `infra/main.bicep`: Cosmos container partition paths updated to match the data model, including hierarchical partition keys for stable customer-scoped containers.
- `docs/COSMOS_MODEL.md`: detailed model and operational contract.
- `tests/test_data_model.py`: unit tests for container coverage, partition behavior, document normalization, required fields, and customer queries.

## Container Contract

The model contains exactly 16 containers:

- `tenants`
- `customers`
- `customerAliases`
- `items`
- `routingRules`
- `processorProfiles`
- `outputProfiles`
- `mailboxAccounts`
- `microsoftAuthConnections`
- `consoleUsers`
- `customerUserAssignments`
- `emailMessages`
- `orderRuns`
- `orderLines`
- `exceptionTasks`
- `auditEvents`

Customer-scoped containers with stable customer ownership use hierarchical partition keys: `/tenantId`, then `/customerId`.

Tenant-wide or customer-mutable records stay partitioned by `/tenantId`, while still carrying `customerId` when known for query/filter use. This avoids partition-key rewrite problems for email, order, exception, and audit records whose customer may be unknown or corrected later.

## Canonical Models Added

Phase 4 added or formalized:

- `Tenant`
- `CustomerAlias`
- `MailboxAccount`
- `MicrosoftAuthConnection`
- `ConsoleUser`
- `CustomerUserAssignment`

Existing order, customer, item, routing, processor, output, exception, and audit models are now part of the same canonical container contract.

## Storage Rules

- Stored documents use camelCase keys for Cosmos compatibility.
- The local repository accepts snake_case dataclass output and normalizes it before storage.
- Required customer-scoped containers reject documents without `customerId`.
- Tenant-wide configuration in customer-scoped containers uses `customerId: "_global"`.
- Customer-unknown order line records use `customerId: "_unassigned"`.
- Cosmos repository access is selected with `ORDER_PROCESSOR_STORAGE_BACKEND=cosmos`.
- Local development remains in-memory with `ORDER_PROCESSOR_STORAGE_BACKEND=memory`.

## Mailbox and Auth Configuration

Mailbox configuration is now represented by `MailboxAccount` and stored in `mailboxAccounts` by `tenantId` plus `customerId`.

Microsoft authentication/connection metadata is represented by `MicrosoftAuthConnection` and stored in `microsoftAuthConnections`. Secret material is not stored in Cosmos; only Key Vault secret names and non-secret connection metadata are allowed.

## Console User Management

Console access is separate from customer profiles:

- `ConsoleUser` stores Microsoft-authenticated users and platform roles.
- `CustomerUserAssignment` stores customer-scoped access and roles.
- `connect@focuseautomate.com` is normalized as the bootstrap `platformAdmin` in the API scaffold.

## Validation

The phase was validated with:

- `python -m unittest discover -s tests`
- `python -m compileall -q src apps`
- `az bicep build --file .\infra\main.bicep`
- `az bicep build --file .\infra\subscription.bicep`

No live Azure deployment or Graph/SharePoint refresh was run during this phase.
