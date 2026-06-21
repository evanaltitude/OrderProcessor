# Phase 8 Customer and Item Data Refresh

Phase 8 is complete as of 2026-06-20. Customer and item refreshes now run through Azure backend import endpoints that parse source data, normalize records, write Cosmos canonical containers, archive original rows for audit/debug, and return refresh status for console visibility.

## Implemented Scope

- Parser registry for direct rows, CSV, JSON, and JSONL.
- Per-customer parser selection through `parserModule` or `importProfile.parserModule`.
- Field maps through request payloads or `importProfile.fieldMap`.
- Customer import endpoint with canonical `customers` writes.
- Customer alias generation into `customerAliases`.
- Item import endpoint with canonical `items` writes.
- Source-row preservation through a `SourceRowArchive` abstraction.
- Local in-memory archive for tests/offline work.
- Azure Blob archive implementation for deployed use.
- Customer refresh cadence default: every 1 day.
- Item refresh cadence default: every 7 days.
- Customer-specific refresh overrides.
- Optional import-time embeddings for customer and item vector search.
- Import audit events for customers and items.
- Import summaries with created/updated/skipped/error counts.

## Parser Modules

Supported parser module names:

- `rows`
- `csv`
- `json`
- `jsonl`
- `genericCustomerCsv`
- `genericItemCsv`

The backend infers the parser from `rows`, `contentType`, or `sourceName` when `parserModule` is not supplied. Parser selection can also be supplied in `importProfile.parserModule`.

CSV parsing uses Python's standard CSV parser and `csv.DictReader`. JSON imports accept an array or an object with a `rows` array. JSONL imports parse one object per line.

## Field Maps

Field maps translate source column names into canonical fields.

Customer fields:

- `customer_code`
- `name`
- `route_number`
- `csr_email`
- `csr_folder`
- `store_number`
- `sender_domains`
- `aliases`
- `known_subject_patterns`
- `alias_customer_codes`
- `alias_store_numbers`
- `alias_route_numbers`
- `alias_sender_domains`
- `alias_subject_patterns`

Item fields:

- `internal_item_number`
- `description`
- `upc`
- `customer_item_numbers`
- `aliases`

Customer rows require either `customer_code` or `name`. Item rows require at least one of `internal_item_number`, `upc`, or `customer_item_numbers`.

## Source Row Archive

Original rows are serialized as JSONL and archived before normalization. The archive result includes:

- `importRunId`
- `sourceRowsBlobUrl`
- `sourceRowsChecksum`
- `sourceRowCount`
- `archivedAt`

Local/offline default:

- `ORDER_PROCESSOR_SOURCE_ARCHIVE_BACKEND=memory`
- URLs use `memory://source-rows/...`

Deployed Blob Storage mode:

- `ORDER_PROCESSOR_SOURCE_ARCHIVE_BACKEND=blob`
- `SOURCE_ROWS_STORAGE_ACCOUNT_URL` or `STORAGE_ACCOUNT_NAME`
- `SOURCE_ROWS_CONTAINER_NAME`, default `source-rows`

The Azure Blob implementation uses managed identity through `DefaultAzureCredential`.

## Cosmos Writes

Customer imports write:

- `customers`
- `customerAliases`
- `auditEvents`

Item imports write:

- `items`
- `auditEvents`

`customers` and `items` include:

- `sourceName`
- `sourceRowsBlobUrl`
- `lastImportedAt`
- `rawSource`

`rawSource` includes original row data, source metadata, row index, import run id, checksum, archive timestamp, and source rows blob URL.

## Refresh Policy

Customer import default:

- `intervalDays: 1`
- `cadence: every 1 day`

Item import default:

- `intervalDays: 7`
- `cadence: every 7 days`

Overrides:

- `refreshIntervalDays`
- `importProfile.refreshIntervalDays`
- `customerConfig.customerRefreshIntervalDays`
- `customerConfig.itemRefreshIntervalDays`

Responses include `refreshPolicy` with `nextDueAt`, so the console can display data freshness and scheduling state.

## Embeddings

Import-time embeddings are disabled by default. Enable with:

- `ORDER_PROCESSOR_ENABLE_IMPORT_EMBEDDINGS=true`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`

The embedding client uses `AZURE_OPENAI_API_KEY` when present or managed identity otherwise. Customer embeddings include code/name/store/route/domain/alias text. Item embeddings include internal item number, description, UPC, customer item numbers, and aliases.

## Validation

Tests added or expanded:

- `tests/test_imports_output.py`

Coverage includes:

- Customer CSV parsing.
- JSON and JSONL parsing.
- Source row archiving.
- Customer alias generation.
- Daily customer refresh default.
- Weekly item refresh default.
- Customer-specific item refresh override.
- Incremental updates.
- Missing item identifier errors.
- Malformed JSON parse errors.
- Optional import embeddings.

## Handoff to Phase 9

Phase 9 can now rely on Cosmos `customers`, `customerAliases`, and `items` as the operational data source. New order processors should not read customer/item reference data from SharePoint local copies. If real customer source formats require specialized parsing, add parser modules to the Phase 8 registry and select them through `importProfile.parserModule`.
