# Phase 2 Reverse Engineering

Phase 2 captures the current Power Automate behavior so the Azure platform can reproduce it without carrying over unnecessary flow complexity.

## Artifacts

- FLOW_CAPABILITY_MAP.md: every active reference flow mapped to exactly one migration capability.
- flow-capability-map.json: machine-readable flow capability map.
- ORDER_PROCESS_FLOW_ANALYSIS.md: trigger schema, parser behavior, outputs, assumptions, error paths, and migration notes for order-processing flows.
- order-process-flow-analysis.json: machine-readable order-process analysis.
- samples/phase-2: representative synthetic sample emails/files and fixture manifest.

## Current Findings

- Active reference flows: 32.
- Order-processing group flows: 11.
- Active CSV migration target: orderProcess - CSV Parse.
- orderProcess - CSV Parse currently uses Plumsail FlowV1DocumentsJobsParseCsvPost; replacement should be Azure Function CSV parsing.
- XLS/XLT flow currently uses Plumsail FlowV1DocumentsJobsXls2XlsxPost; replacement should be backend workbook conversion or upstream XLSX normalization.
- PDF flows use Google Document AI and should be replaced by Azure Document Intelligence.
- Generic and customer-specific email body flows use OpenAI extraction prompts and should gain deterministic parsers before AI fallback.

## Completion Criteria

- Every flow is mapped to one capability.
- Every order-processing flow is documented with trigger contract, parser behavior, output side effects, customer assumptions, and error paths.
- Representative sample fixture placeholders/files exist for CSV, XLSX, XLS/XLT, PDF, email body, and customer-specific cases.

Generated: 2026-06-20T09:49:33.6104621-04:00
