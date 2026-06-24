# Phase 7 Customer Identification

Phase 7 is complete as of 2026-06-20. Customer identification now runs as an Azure backend concern through `POST /customers/identify`, with deterministic matching first and Azure OpenAI/Cosmos vector fallback second.

## Implemented Scope

- Deterministic extraction from email context.
- Matching against canonical `customers` records.
- Matching against `customerAliases` records.
- Known subject pattern matching.
- Ambiguity detection for duplicated deterministic signals.
- Confidence threshold handling.
- Optional vector fallback behind a configurable Azure OpenAI/Cosmos boundary.
- Exception task creation for unresolved, ambiguous, or low-confidence results.
- Stored email/order update when a confident match is found.
- Audit event creation for every identification attempt.

## Deterministic Signals

The backend extracts:

- `customerCode`
- `storeNumber`
- `routeNumber`
- `senderDomain`
- `subject`

The engine then compares those signals to:

- `CustomerProfile.customerCode`
- `CustomerProfile.storeNumber`
- `CustomerProfile.routeNumber`
- `CustomerProfile.senderDomains`
- `CustomerProfile.knownSubjectPatterns`
- `CustomerProfile.aliases`
- `CustomerAlias` records with alias types for customer code, store number, route number, sender domain, and known subject pattern

Default deterministic confidence values:

| Match method | Confidence |
| --- | --- |
| `customerContext` | `1.0` |
| `customerCode` | `1.0` |
| `storeNumber` | `0.96` |
| `routeNumber` | `0.93` |
| `knownSubjectPattern` | `0.92` |
| `senderDomain` | `0.9` |

The default match threshold is `0.85`. A deterministic result must be above threshold and unique to short-circuit. If deterministic signals are ambiguous or below threshold and vector fallback is enabled, vector search is allowed to try to resolve the match. If vector fallback is unavailable or still below threshold, the result becomes `possibleMatch` and creates a console exception task.

## Vector Fallback

Vector fallback is intentionally disabled by default for local/offline work. Enable it only when the Azure OpenAI embedding deployment and Cosmos customer embeddings are ready.

Environment variables:

- `ORDER_PROCESSOR_ENABLE_CUSTOMER_VECTOR_SEARCH=true`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`
- `AZURE_OPENAI_API_VERSION` optional, defaults to `2024-02-15-preview`
- `AZURE_OPENAI_API_KEY` optional; when omitted, managed identity is used through `DefaultAzureCredential`

Implementation pieces:

- `AzureOpenAIEmbeddingClient` creates query embeddings.
- `CosmosCustomerVectorSearch` calls repository-native `vector_search_customers` when available.
- `CosmosRepository.vector_search_customers` performs a native Cosmos `VectorDistance` query against `customers.embedding`.
- Local/in-memory fallback uses cosine similarity over stored customer embeddings for tests and offline development.

Vector candidates below the confidence threshold become `possibleMatch` and create an exception task. Confident vector matches return `matched`.

## Foundry AI Consensus

Customer identification can also run the live Power Automate pattern against a Foundry/Azure OpenAI chat deployment:

- Run two independent customer-record prompts over the daily customer vector candidates.
- Accept the customer only when both prompts return the same `cust_code`.
- Retry disagreement up to `ORDER_PROCESSOR_CUSTOMER_AI_MAX_ATTEMPTS`.
- If the two prompts still disagree, run a decider prompt over the original criteria and both returned records.
- If the decider cannot return a clear `cust_code`, return no customer code so the email lands in the exceptions queue.

Environment variables:

- `ORDER_PROCESSOR_ENABLE_CUSTOMER_AI_CONSENSUS=true`
- `ORDER_PROCESSOR_CUSTOMER_AI_MAX_ATTEMPTS` optional, defaults to `3`
- `ORDER_PROCESSOR_CUSTOMER_AI_CANDIDATE_LIMIT` optional, defaults to `8`
- `AZURE_AI_FOUNDRY_OPENAI_ENDPOINT` or `AZURE_OPENAI_ENDPOINT`
- `AZURE_AI_FOUNDRY_CUSTOMER_ID_DEPLOYMENT` or `AZURE_OPENAI_CUSTOMER_ID_DEPLOYMENT`
- `AZURE_AI_FOUNDRY_OPENAI_API_VERSION` or `AZURE_OPENAI_API_VERSION` optional
- `AZURE_AI_FOUNDRY_API_KEY` or `AZURE_OPENAI_API_KEY` optional; when omitted, managed identity is used through `DefaultAzureCredential`

When AI consensus is enabled and vector candidates exist, the vector score supplies the tight candidate set but does not auto-accept a customer by itself. The two-prompt consensus or decider must return a valid customer code from the candidate records.

## API Behavior

`POST /customers/identify` accepts an `emailMessage`, optional `confidenceThreshold`, and optional caller-supplied `customers` or `customerAliases` for controlled tests. Normal backend operation loads customers and aliases from Cosmos.

Response:

- `result`
- `exceptionTask`

`result` includes:

- `status`
- `customerId`
- `customerCode`
- `routeNumber`
- `matchMethod`
- `confidence`
- `candidates`
- `reasons`
- `extractedSignals`

When `result.status` is `matched`, the backend updates existing `emailMessages` and associated `orderRuns` records with the matched `customerId` when those records exist. The email also receives `customerIdentification` details.

When `result.status` is `possibleMatch` or `unresolved`, the backend creates a `customerIdentification` exception task. The task context includes the result, extracted signals, subject, sender, mailbox, and confidence threshold.

Every call writes a `customer.identified` audit event.

## Validation

Tests added or expanded:

- `tests/test_customer_identification.py`
- `tests/test_api.py`
- `tests/test_imports_output.py`

Coverage includes:

- Customer code matching.
- Sender domain matching.
- Store number matching.
- Route number matching.
- Known subject pattern matching.
- Alias-backed deterministic matching.
- Deterministic match preventing vector fallback.
- Ambiguous deterministic signals requiring review.
- Confident vector fallback matching.
- Low-confidence vector fallback creating review tasks.
- Stored email/order customer updates after a confident match.
- Customer import normalization for known subject patterns.

## Handoff to Phase 8

Phase 8 should focus on customer and item data refresh. Customer import work should populate deterministic identification fields and aliases reliably, including sender domains, store numbers, route numbers, subject patterns, and embeddings. Embeddings should be generated with the same Azure OpenAI deployment used by Phase 7 fallback so Cosmos vector search has meaningful customer records.
