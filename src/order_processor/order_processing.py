from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import dataclass, field
from html.parser import HTMLParser
import io
import json
import os
import re
import zipfile
from typing import Any, Protocol
import xml.etree.ElementTree as ET

from .data_model import GLOBAL_CUSTOMER_ID
from .item_validation import (
    ITEM_NUMBER_FIELDS,
    UPC_FIELDS,
    normalize_item_token,
    normalize_upc,
    unequal_length_identifier_match,
    validate_item,
)
from .email_body_processing import extract_email_body_order
from .email_body_processing import order_lines_from_extraction as email_body_order_lines_from_extraction
from .google_document_ai import extract_order_from_google_document_ai_response
from .google_document_ai import order_lines_from_google_extraction
from .models import (
    ItemRecord,
    MatchStatus,
    OrderLine,
    OrderRun,
    ProcessingStatus,
    ProcessorProfile,
)
from .spreadsheet_processing import extract_order_lines as extract_spreadsheet_order_lines
from .spreadsheet_processing import order_lines_from_extraction


PROCESSOR_VERSION = "phase9-universal-v1"
DEFAULT_HEADERLESS_COLUMNS = ["item_number", "upc", "quantity", "description"]


class OrderProcessor(Protocol):
    processor_type: str

    def parse(
        self,
        order: OrderRun,
        payload: dict[str, Any] | bytes | str,
        context: "OrderProcessorContext | None" = None,
    ) -> OrderRun:
        """Parse source payload into the universal order model."""


@dataclass(slots=True)
class OrderProcessorContext:
    tenant_id: str
    customer_id: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    source_metadata: dict[str, Any] = field(default_factory=dict)


def process_order_payload(
    order: OrderRun,
    payload: dict[str, Any],
    processor_profile: ProcessorProfile | None = None,
) -> OrderRun:
    settings = _merged_settings(payload, processor_profile)
    processor_type = _processor_type_from_payload(payload, processor_profile, settings)
    context = OrderProcessorContext(
        tenant_id=order.tenant_id,
        customer_id=order.customer_id,
        settings=settings,
        source_metadata=_source_metadata_from_payload(payload),
    )

    order.processor_profile_id = (
        _pick(payload, "processorProfileId", "processor_profile_id", default=order.processor_profile_id)
        or (processor_profile.id if processor_profile else None)
    )
    order.processor_type = processor_type
    order.processor_version = PROCESSOR_VERSION
    order.header.update(dict(_pick(payload, "header", default={}) or {}))
    order.po_number = str(_pick(payload, "poNumber", "po_number", default=order.po_number) or "")
    order.order_number = str(_pick(payload, "orderNumber", "order_number", default=order.order_number) or "")
    order.source_file_name = str(
        _pick(payload, "sourceFileName", "source_file_name", "fileName", "file_name", default=order.source_file_name)
        or ""
    )
    order.source_metadata.update(context.source_metadata)

    if "lines" in payload and not _has_source_payload(payload):
        order.lines = [_line_from_payload(index, line) for index, line in enumerate(_as_list(payload["lines"]), 1)]
        order.source_type = "canonicalLines"
        order.status = ProcessingStatus.PROCESSING if order.lines else ProcessingStatus.FAILED
        if not order.lines:
            _append_error(order, "noOrderLines", "No canonical order lines were supplied.")
        return order

    processor = create_order_processor(processor_type, settings)
    try:
        order = processor.parse(order, payload, context)
    except Exception as exc:  # pragma: no cover - defensive boundary for deployed adapters.
        _append_error(
            order,
            "processorFailed",
            f"{processor_type} processor failed: {exc}",
            processorType=processor_type,
        )
        order.status = ProcessingStatus.FAILED

    if not order.lines and order.status != ProcessingStatus.FAILED:
        _append_error(
            order,
            "noOrderLines",
            "Processor completed without producing order lines.",
            processorType=processor_type,
        )
        order.status = ProcessingStatus.FAILED
    return order


@dataclass(slots=True)
class CsvOrderProcessor:
    processor_type: str = "csv"

    def parse(
        self,
        order: OrderRun,
        payload: dict[str, Any] | bytes | str,
        context: OrderProcessorContext | None = None,
    ) -> OrderRun:
        settings = _settings(context)
        if isinstance(payload, dict) and _rows_from_payload(payload) is not None:
            rows = _rows_from_payload(payload) or []
            _apply_rows_to_order(order, rows, settings, "csv")
            return order

        text = _source_text(payload, settings)
        if not text.strip():
            _append_error(order, "emptySourceContent", "CSV source content is empty.")
            order.status = ProcessingStatus.FAILED
            order.source_type = "csv"
            return order

        rows, warnings = parse_csv_rows(text, settings)
        order.parse_warnings.extend(warnings)
        _apply_rows_to_order(order, rows, settings, "csv")
        return order


@dataclass(slots=True)
class XlsxOrderProcessor:
    processor_type: str = "xlsx"

    def parse(
        self,
        order: OrderRun,
        payload: dict[str, Any] | bytes | str,
        context: OrderProcessorContext | None = None,
    ) -> OrderRun:
        settings = _settings(context)
        rows = _rows_from_payload(payload) if isinstance(payload, dict) else None
        actual_type = "xlsx"
        if rows is None:
            actual_type = _spreadsheet_file_type_from_payload(payload, settings)
            if actual_type not in {"xlsx", "xlsm", "xltx"}:
                return SpreadsheetOrderProcessor().parse(order, payload, context)
            source = _source_bytes(payload, settings)
            rows = parse_xlsx_rows(source, settings)
        original_po_number = order.po_number
        original_order_number = order.order_number
        original_source_type = order.source_type
        original_status = order.status
        original_lines = list(order.lines)
        _apply_rows_to_order(order, rows, settings, "xlsx")
        if not _has_extractable_order_lines(order):
            order.po_number = original_po_number
            order.order_number = original_order_number
            order.source_type = original_source_type
            order.status = original_status
            order.lines = original_lines
            order.parse_warnings.append(
                {
                    "code": "xlsxSimpleParserFallback",
                    "message": "Simple XLSX parsing did not find order lines; retrying with spreadsheet layout extraction.",
                }
            )
            return SpreadsheetOrderProcessor().parse(order, _spreadsheet_fallback_payload(payload, actual_type), context)
        return order


@dataclass(slots=True)
class LegacyWorkbookOrderProcessor:
    processor_type: str = "legacyWorkbook"

    def parse(
        self,
        order: OrderRun,
        payload: dict[str, Any] | bytes | str,
        context: OrderProcessorContext | None = None,
    ) -> OrderRun:
        settings = _settings(context)
        rows = _rows_from_payload(payload) if isinstance(payload, dict) else None
        if rows is not None:
            _apply_rows_to_order(order, rows, settings, "legacyWorkbook")
            return order

        source = _source_bytes(payload, settings)
        text = _decode_bytes(source)
        if "<table" not in text.lower():
            _append_error(
                order,
                "legacyWorkbookConversionRequired",
                "Binary XLS/XLT files must be normalized by backend workbook conversion before parsing.",
                replacement="Azure Function code path; no Plumsail connector",
            )
            order.source_type = "legacyWorkbook"
            order.status = ProcessingStatus.FAILED
            return order

        rows = parse_html_table_rows(text, settings)
        _apply_rows_to_order(order, rows, settings, "legacyWorkbook")
        return order


@dataclass(slots=True)
class SpreadsheetOrderProcessor:
    processor_type: str = "spreadsheet"

    def parse(
        self,
        order: OrderRun,
        payload: dict[str, Any] | bytes | str,
        context: OrderProcessorContext | None = None,
    ) -> OrderRun:
        settings = _settings(context)
        extraction = extract_spreadsheet_order_lines(payload, settings)
        order.source_type = "spreadsheet"
        if isinstance(payload, dict):
            order.source_file_name = str(
                _pick(payload, "sourceFileName", "source_file_name", "fileName", "file_name", default=order.source_file_name)
                or order.source_file_name
            )
        order.source_metadata["spreadsheet"] = {
            "normalization": extraction.get("normalization", {}),
            "layout": extraction.get("layout", {}),
            "customerIdentification": dict(extraction.get("layout", {}).get("customerIdentification", {}) or {}),
            "orderGroups": [
                {key: value for key, value in group.items() if key != "lines"}
                for group in extraction.get("orderGroups", [])
                if isinstance(group, dict)
            ],
            "requiresHumanReview": bool(extraction.get("requiresHumanReview")),
        }
        order.parse_warnings.extend(
            [dict(item) for item in extraction.get("warnings", []) if isinstance(item, dict)]
        )
        if extraction.get("purchaseOrder") and not order.po_number:
            order.po_number = str(extraction.get("purchaseOrder") or "")
        order.lines = order_lines_from_extraction(extraction)
        if extraction.get("status") == "failed" or not order.lines:
            for error in extraction.get("errors", []):
                if isinstance(error, dict):
                    order.errors.append(dict(error))
            order.status = ProcessingStatus.FAILED
        else:
            order.status = ProcessingStatus.PROCESSING
        return order


@dataclass(slots=True)
class PdfOrderProcessor:
    processor_type: str = "pdf"

    def parse(
        self,
        order: OrderRun,
        payload: dict[str, Any] | bytes | str,
        context: OrderProcessorContext | None = None,
    ) -> OrderRun:
        settings = _settings(context)
        if isinstance(payload, dict):
            rows = _rows_from_payload(payload)
            if rows is not None:
                _apply_rows_to_order(order, rows, settings, "pdf")
                return order

            google_extraction = _pick(
                payload,
                "googleDocumentAiExtraction",
                "google_document_ai_extraction",
                default=None,
            )
            google_result = _pick(
                payload,
                "googleDocumentAiResult",
                "google_document_ai_result",
                "googleDocumentAiResponse",
                "google_document_ai_response",
                default=None,
            )
            if not isinstance(google_extraction, dict) and isinstance(google_result, dict):
                google_extraction = extract_order_from_google_document_ai_response(google_result, payload, settings)
            if isinstance(google_extraction, dict):
                order.source_type = "pdf"
                order.source_metadata["googleDocumentAi"] = {
                    "customerIdentification": dict(google_extraction.get("customerIdentification", {}) or {}),
                    "headers": dict(google_extraction.get("headers", {}) or {}),
                    "rawDocument": dict(google_extraction.get("rawDocument", {}) or {}),
                    "processor": dict(google_extraction.get("googleDocumentAi", {}) or {}),
                    "requiresHumanReview": bool(google_extraction.get("requiresHumanReview")),
                }
                order.parse_warnings.extend(
                    [dict(item) for item in google_extraction.get("warnings", []) if isinstance(item, dict)]
                )
                if google_extraction.get("purchaseOrder") and not order.po_number:
                    order.po_number = str(google_extraction.get("purchaseOrder") or "")
                order.lines = order_lines_from_google_extraction(google_extraction)
                if google_extraction.get("status") == "failed" or not order.lines:
                    for error in google_extraction.get("errors", []):
                        if isinstance(error, dict):
                            order.errors.append(dict(error))
                    order.status = ProcessingStatus.FAILED
                else:
                    order.status = ProcessingStatus.PROCESSING
                return order

            extracted = _pick(
                payload,
                "documentIntelligenceResult",
                "document_intelligence_result",
                "azureDocumentIntelligenceResult",
                "azure_document_intelligence_result",
                default=None,
            )
            if isinstance(extracted, dict):
                rows = rows_from_document_intelligence(extracted, settings)
                _apply_rows_to_order(order, rows, settings, "pdf")
                order.source_metadata["documentIntelligenceModelId"] = settings.get(
                    "documentIntelligenceModelId", "prebuilt-layout"
                )
                return order

            text = _pick(payload, "extractedText", "extracted_text", "sourceText", "source_text", default="")
            if text:
                rows = rows_from_text_table(str(text), settings)
                _apply_rows_to_order(order, rows, settings, "pdf")
                return order

        _append_error(
            order,
            "documentExtractionRequired",
            "PDF processing requires Google Document AI extraction, Azure Document Intelligence extracted tables, or text before line parsing.",
            integration="googleDocumentAi",
            modelId=settings.get("googleDocumentAiProcessorId", "d3e3bcfffcbad47c"),
        )
        order.source_type = "pdf"
        order.status = ProcessingStatus.FAILED
        return order


@dataclass(slots=True)
class EmailBodyOrderProcessor:
    processor_type: str = "emailBody"

    def parse(
        self,
        order: OrderRun,
        payload: dict[str, Any] | bytes | str,
        context: OrderProcessorContext | None = None,
    ) -> OrderRun:
        settings = _settings(context)
        extraction = extract_email_body_order(payload, settings)
        order.source_type = "emailBody"
        order.source_metadata["emailBody"] = {
            "customerIdentification": dict(extraction.get("customerIdentification", {}) or {}),
            "aiExtraction": dict(extraction.get("aiExtraction", {}) or {}),
            "source": dict(extraction.get("source", {}) or {}),
            "requiresHumanReview": bool(extraction.get("requiresHumanReview")),
        }
        order.parse_warnings.extend(
            [dict(item) for item in extraction.get("warnings", []) if isinstance(item, dict)]
        )
        if extraction.get("purchaseOrder") and not order.po_number:
            order.po_number = str(extraction.get("purchaseOrder") or "")
        order.lines = email_body_order_lines_from_extraction(extraction)
        if extraction.get("status") == "failed" or not order.lines:
            for error in extraction.get("errors", []):
                if isinstance(error, dict):
                    order.errors.append(dict(error))
            order.status = ProcessingStatus.FAILED
        else:
            order.status = ProcessingStatus.PROCESSING
        return order


@dataclass(slots=True)
class CustomerOverrideOrderProcessor:
    processor_type: str = "customerOverride"

    def parse(
        self,
        order: OrderRun,
        payload: dict[str, Any] | bytes | str,
        context: OrderProcessorContext | None = None,
    ) -> OrderRun:
        settings = _settings(context)
        base_type = str(
            settings.get("baseProcessorType")
            or settings.get("base_processor_type")
            or settings.get("sourceProcessorType")
            or settings.get("source_processor_type")
            or "csv"
        )
        if _normalize_processor_type(base_type) == "customerOverride":
            _append_error(
                order,
                "invalidCustomerOverride",
                "Customer override must delegate to a concrete base processor.",
            )
            order.status = ProcessingStatus.FAILED
            return order
        order.source_metadata["customerOverride"] = {
            "baseProcessorType": base_type,
            "profileName": settings.get("profileName", ""),
        }
        return create_order_processor(base_type, settings).parse(order, payload, context)


def create_order_processor(processor_type: str, settings: dict[str, Any] | None = None) -> OrderProcessor:
    normalized = _normalize_processor_type(processor_type)
    if normalized == "spreadsheet":
        return SpreadsheetOrderProcessor()
    if normalized == "csv":
        return CsvOrderProcessor()
    if normalized == "xlsx":
        return XlsxOrderProcessor()
    if normalized in {"xls", "xlt", "legacyWorkbook"}:
        return LegacyWorkbookOrderProcessor()
    if normalized == "pdf":
        return PdfOrderProcessor()
    if normalized == "emailBody":
        return EmailBodyOrderProcessor()
    if normalized == "customerOverride":
        return CustomerOverrideOrderProcessor()
    raise ValueError(f"Unsupported order processor type: {processor_type}")


def parse_csv_rows(text: str, settings: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    settings = settings or {}
    warnings: list[dict[str, Any]] = []
    delimiter = str(settings.get("delimiter") or "")
    sample = text[:4096]
    if not delimiter:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=str(settings.get("delimiters") or ",\t;|")).delimiter
        except csv.Error:
            delimiter = ","
            warnings.append({"code": "csvDelimiterFallback", "message": "CSV delimiter sniffing fell back to comma."})

    has_header = settings.get("hasHeader", settings.get("has_header", None))
    if has_header is None:
        try:
            has_header = csv.Sniffer().has_header(sample)
        except csv.Error:
            has_header = True
    has_header = bool(has_header)

    buffer = io.StringIO(text)
    rows: list[dict[str, Any]] = []
    if has_header:
        reader = csv.DictReader(buffer, delimiter=delimiter)
        for index, row in enumerate(reader, start=1):
            rows.append({str(key or f"Column {column + 1}"): value for column, (key, value) in enumerate(row.items())})
    else:
        columns = list(settings.get("headerlessColumns") or settings.get("columns") or DEFAULT_HEADERLESS_COLUMNS)
        reader = csv.reader(buffer, delimiter=delimiter)
        for index, values in enumerate(reader, start=1):
            if not any(str(value).strip() for value in values):
                continue
            row = {columns[column] if column < len(columns) else f"Column {column + 1}": value for column, value in enumerate(values)}
            row["_sourceRowIndex"] = index
            rows.append(row)
    return rows, warnings


def parse_xlsx_rows(source: bytes, settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    settings = settings or {}
    if not source:
        return []

    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        sheet_path = _first_sheet_path(archive, settings)
        shared_strings = _shared_strings(archive)
        xml_bytes = archive.read(sheet_path)

    root = ET.fromstring(xml_bytes)
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    matrix: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", ns):
        values_by_column: dict[int, str] = {}
        for cell in row.findall("x:c", ns):
            ref = cell.attrib.get("r", "")
            column_index = _xlsx_column_index(ref)
            values_by_column[column_index] = _xlsx_cell_value(cell, shared_strings, ns)
        if values_by_column:
            width = max(values_by_column)
            matrix.append([values_by_column.get(index, "") for index in range(1, width + 1)])

    return _matrix_to_rows(matrix, settings)


def parse_html_table_rows(text: str, settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    parser = _HtmlTableParser()
    parser.feed(text)
    return _matrix_to_rows(parser.first_table, settings or {})


def rows_from_document_intelligence(result: dict[str, Any], settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    settings = settings or {}
    tables = result.get("tables") or []
    for table in tables:
        cells = table.get("cells") or []
        matrix: list[list[str]] = []
        for cell in cells:
            row_index = int(cell.get("rowIndex", 0))
            column_index = int(cell.get("columnIndex", 0))
            while len(matrix) <= row_index:
                matrix.append([])
            while len(matrix[row_index]) <= column_index:
                matrix[row_index].append("")
            matrix[row_index][column_index] = str(cell.get("content", "") or "")
        rows = _matrix_to_rows(matrix, settings)
        if rows:
            return rows

    content = result.get("content") or result.get("text") or ""
    return rows_from_text_table(str(content), settings) if content else []


def rows_from_text_table(text: str, settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    settings = settings or {}
    line_pattern = settings.get("linePattern") or settings.get("line_pattern")
    if line_pattern:
        rows = _rows_from_line_pattern(text, str(line_pattern))
        if rows:
            return rows

    pipe_lines = [line.strip() for line in text.splitlines() if "|" in line and line.strip()]
    if pipe_lines:
        matrix = [[part.strip() for part in line.split("|")] for line in pipe_lines]
        return _matrix_to_rows(matrix, settings)

    rows: list[dict[str, Any]] = []
    line_regex = re.compile(
        r"^\s*(?P<item>[A-Za-z0-9][A-Za-z0-9_.-]{2,})\s*[-,\t ]+\s*"
        r"(?:(?P<upc>\d{8,14})\s*[-,\t ]+\s*)?(?P<quantity>\d+(?:\.\d+)?)\s*$"
    )
    for index, line in enumerate(text.splitlines(), start=1):
        match = line_regex.match(line)
        if not match:
            continue
        rows.append(
            {
                "item_number": match.group("item") or "",
                "upc": match.group("upc") or "",
                "quantity": match.group("quantity") or "",
                "_sourceRowIndex": index,
            }
        )
    return rows


def validate_order_lines(order: OrderRun, items: list[ItemRecord], max_workers: int | None = None) -> OrderRun:
    if not order.customer_id:
        order.errors.append({"code": "missingCustomer", "message": "Cannot validate lines without a customer."})
        for line in order.lines:
            line.validation_errors.append({"code": "missingCustomer"})
        return order

    scoped_items = [
        item
        for item in items
        if item.tenant_id == order.tenant_id and item.customer_id in {order.customer_id, GLOBAL_CUSTOMER_ID}
    ]
    item_number_index: dict[str, list[ItemRecord]] = {}
    upc_index: dict[str, list[ItemRecord]] = {}
    for item in scoped_items:
        for value in [
            item.internal_item_number,
            *item.alt_parts_combined,
            *item.customer_item_numbers,
            *item.aliases,
        ]:
            key = normalize_item_token(str(value or ""))
            if key:
                item_number_index.setdefault(key, []).append(item)
        upc_key = normalize_upc(item.upc)
        if upc_key:
            upc_index.setdefault(upc_key, []).append(item)

    def line_identifier_values(line: OrderLine) -> tuple[str, str]:
        item_value = line.provided_item_number or _first_context_alias_value(line.raw, ITEM_NUMBER_FIELDS)
        upc_value = line.provided_upc or _first_context_alias_value(line.raw, UPC_FIELDS)
        return str(item_value or ""), str(upc_value or "")

    def exact_candidate_items(line: OrderLine) -> list[ItemRecord]:
        candidates: dict[str, ItemRecord] = {}
        item_value, upc_value = line_identifier_values(line)
        item_key = normalize_item_token(item_value)
        upc_key = normalize_upc(upc_value)
        for item in item_number_index.get(item_key, []) if item_key else []:
            candidates[item.id] = item
        for item in upc_index.get(upc_key, []) if upc_key else []:
            candidates[item.id] = item
        if item_key or upc_key:
            for item in scoped_items:
                if item.id in candidates:
                    continue
                searchable_numbers = [
                    normalize_item_token(str(value or ""))
                    for value in [
                        item.internal_item_number,
                        *item.alt_parts_combined,
                        *item.customer_item_numbers,
                        *item.aliases,
                    ]
                ]
                if item_key and any(unequal_length_identifier_match(item_key, value) for value in searchable_numbers):
                    candidates[item.id] = item
                    continue
                item_upc = normalize_upc(item.upc)
                if upc_key and unequal_length_identifier_match(upc_key, item_upc):
                    candidates[item.id] = item
        return list(candidates.values())

    def validate_line(line: OrderLine):
        candidate_items = exact_candidate_items(line)
        item_value, upc_value = line_identifier_values(line)
        provided_identifier = bool(normalize_item_token(item_value) or normalize_upc(upc_value))
        return validate_item(
            tenant_id=order.tenant_id,
            customer_id=order.customer_id,
            provided_item_number=line.provided_item_number,
            provided_upc=line.provided_upc,
            description=line.description,
            items=candidate_items if provided_identifier else scoped_items,
            row_context=line.raw,
        )

    if len(order.lines) > 1:
        worker_count = max_workers or _item_validation_worker_count(len(order.lines))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            results = list(executor.map(validate_line, order.lines))
    else:
        results = [validate_line(line) for line in order.lines]

    for line, result in zip(order.lines, results, strict=True):
        line.validation_status = result.status
        line.validation_confidence = result.confidence
        line.validation_method = result.match_method
        line.validation_candidates = result.candidates
        line.matched_internal_item_number = result.matched_internal_item_number
        if result.unresolved_reason:
            line.validation_errors.append({"code": "unresolvedItem", "message": result.unresolved_reason})
    return order


def _item_validation_worker_count(line_count: int) -> int:
    configured = os.environ.get("ORDER_ITEM_VALIDATION_MAX_WORKERS")
    if configured:
        try:
            return max(1, min(int(configured), line_count))
        except ValueError:
            pass
    return max(1, min(8, line_count))


class AzureDocumentIntelligenceExtractor:
    """Thin Azure boundary used by deployed PDF processors before table parsing."""

    def __init__(
        self,
        endpoint: str | None = None,
        model_id: str = "prebuilt-layout",
        credential: Any | None = None,
    ) -> None:
        self.endpoint = endpoint or os.environ.get("DOCUMENT_INTELLIGENCE_ENDPOINT", "")
        self.model_id = model_id
        self.credential = credential

    def analyze(self, source: bytes, content_type: str = "application/pdf") -> dict[str, Any]:
        if not self.endpoint:
            raise ValueError("DOCUMENT_INTELLIGENCE_ENDPOINT is required for PDF extraction.")
        try:
            from azure.ai.documentintelligence import DocumentIntelligenceClient
            from azure.identity import DefaultAzureCredential
        except ModuleNotFoundError as exc:  # pragma: no cover - deployed dependency boundary.
            raise RuntimeError("Azure Document Intelligence dependencies are not installed.") from exc

        credential = self.credential or DefaultAzureCredential()
        client = DocumentIntelligenceClient(self.endpoint, credential)
        poller = client.begin_analyze_document(self.model_id, io.BytesIO(source), content_type=content_type)
        result = poller.result()
        if hasattr(result, "as_dict"):
            return result.as_dict()
        if isinstance(result, dict):
            return result
        return json.loads(json.dumps(result, default=lambda value: getattr(value, "__dict__", str(value))))


def _apply_rows_to_order(
    order: OrderRun,
    rows: list[dict[str, Any]],
    settings: dict[str, Any],
    source_type: str,
) -> None:
    field_map = dict(settings.get("fieldMap") or settings.get("field_map") or {})
    order.source_type = source_type
    order.status = ProcessingStatus.PROCESSING
    lines: list[OrderLine] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        if _row_is_empty(row):
            continue
        if not order.po_number:
            order.po_number = _row_value(row, "po_number", field_map)
        if not order.order_number:
            order.order_number = _row_value(row, "order_number", field_map)
        line = OrderLine(
            line_number=index,
            quantity=_as_float(_row_value(row, "quantity", field_map)),
            provided_item_number=_row_value(row, "provided_item_number", field_map),
            provided_upc=_row_value(row, "provided_upc", field_map),
            description=_row_value(row, "description", field_map),
            unit=_row_value(row, "unit", field_map),
            unit_price=_as_float(_row_value(row, "unit_price", field_map)),
            source_row_index=_as_int(row.get("_sourceRowIndex") or row.get("_source_row_index")),
            raw=dict(row),
        )
        if line.provided_item_number or line.provided_upc or line.description or line.quantity is not None:
            lines.append(line)
    order.lines = lines


def _has_extractable_order_lines(order: OrderRun) -> bool:
    return any(
        line.quantity is not None
        and (line.provided_item_number or line.provided_upc or line.description)
        for line in order.lines
    )


def _spreadsheet_fallback_payload(payload: dict[str, Any] | bytes | str, file_type: str) -> dict[str, Any] | bytes | str:
    file_type = file_type if file_type in {"xlsx", "xlsm", "xltx"} else "xlsx"
    if isinstance(payload, bytes):
        return {"sourceContent": payload, "sourceFileName": f"source.{file_type}"}
    if not isinstance(payload, dict):
        return payload
    if any(
        _pick(payload, key, default=None)
        for key in ("sourceFileName", "source_file_name", "fileName", "file_name", "contentType", "content_type")
    ):
        return payload
    if _selected_spreadsheet_attachment(payload):
        return payload
    return {**payload, "sourceFileName": f"source.{file_type}"}


def _matrix_to_rows(matrix: list[list[str]], settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not matrix:
        return []
    has_header = bool(settings.get("hasHeader", settings.get("has_header", True)))
    if has_header:
        headers = [str(value).strip() or f"Column {index + 1}" for index, value in enumerate(matrix[0])]
        data_rows = matrix[1:]
    else:
        headers = list(settings.get("headerlessColumns") or settings.get("columns") or DEFAULT_HEADERLESS_COLUMNS)
        data_rows = matrix

    rows: list[dict[str, Any]] = []
    for row_index, values in enumerate(data_rows, start=2 if has_header else 1):
        if not any(str(value).strip() for value in values):
            continue
        row = {
            headers[column] if column < len(headers) else f"Column {column + 1}": value
            for column, value in enumerate(values)
        }
        row["_sourceRowIndex"] = row_index
        rows.append(row)
    return rows


def _row_value(row: dict[str, Any], canonical: str, field_map: dict[str, Any]) -> str:
    candidates = []
    for key, value in field_map.items():
        if _normalized_header(key) in {_normalized_header(canonical), *_field_aliases(canonical)}:
            candidates.append(str(value))
        if _normalized_header(str(value)) in {_normalized_header(canonical), *_field_aliases(canonical)}:
            candidates.append(str(key))
    candidates.extend(_default_fields(canonical))

    lookup = {_normalized_header(key): value for key, value in row.items()}
    for candidate in candidates:
        value = lookup.get(_normalized_header(candidate))
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def _default_fields(canonical: str) -> list[str]:
    fields = {
        "provided_item_number": [
            "provided_item_number",
            "providedItemNumber",
            "item_number",
            "item number",
            "item",
            "item no",
            "our item no.",
            "vendor item",
            "suppliercode",
            "supplier code",
            "sku",
            "product code",
            "Column 1",
        ],
        "provided_upc": [
            "provided_upc",
            "providedUpc",
            "upc",
            "upc #",
            "upc code",
            "product upc",
            "product upc code",
            "barcode",
            "bar code",
            "product barcode",
            "gtin",
            "Column 2",
        ],
        "quantity": ["quantity", "qty", "order qty", "order quantity", "qtyordered", "qty ordered", "Column 3"],
        "description": ["description", "item description", "product", "product description", "Column 4", "Column 5"],
        "unit": ["unit", "uom", "unit of measure"],
        "unit_price": ["unit_price", "unitPrice", "price", "unit price"],
        "po_number": ["po_number", "poNumber", "po", "po number", "purchase order", "purchaseorder"],
        "order_number": ["order_number", "orderNumber", "order number", "order"],
    }
    return fields.get(canonical, [canonical])


def _field_aliases(canonical: str) -> set[str]:
    return {_normalized_header(value) for value in _default_fields(canonical)}


def _rows_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    rows = _pick(payload, "sourceRows", "source_rows", "rows", default=None)
    if rows is None:
        return None
    if isinstance(rows, list):
        return [dict(row) for row in rows if isinstance(row, dict)]
    return None


def _spreadsheet_file_type_from_payload(payload: dict[str, Any] | bytes | str, settings: dict[str, Any]) -> str:
    file_name = str(settings.get("sourceFileName") or "")
    content_type = ""
    if isinstance(payload, dict):
        attachment = _selected_spreadsheet_attachment(payload)
        file_name = str(
            _pick(payload, "sourceFileName", "source_file_name", "fileName", "file_name", default=None)
            or _pick(attachment, "name", "fileName", "file_name", default="")
            or file_name
        )
        content_type = str(
            _pick(payload, "contentType", "content_type", default=None)
            or _pick(attachment, "contentType", "content_type", default="")
            or ""
        )
    extension = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if extension in {"csv", "tsv", "txt", "xlsx", "xlsm", "xltx", "xls", "xlt"}:
        return extension
    lowered_content_type = content_type.lower()
    if "csv" in lowered_content_type:
        return "csv"
    if "tab-separated" in lowered_content_type:
        return "tsv"
    if "spreadsheetml" in lowered_content_type or "xlsx" in lowered_content_type:
        return "xlsx"
    if "excel" in lowered_content_type:
        return "xls"
    return "xlsx"


def _selected_spreadsheet_attachment(payload: dict[str, Any]) -> dict[str, Any]:
    attachments = _as_list(_pick(payload, "attachments", default=[]))
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        name = str(_pick(attachment, "name", "fileName", "file_name", default="")).lower()
        content_type = str(_pick(attachment, "contentType", "content_type", default="")).lower()
        if any(name.endswith(f".{ext}") for ext in ("csv", "tsv", "txt", "xlsx", "xlsm", "xls", "xlt", "xltx")):
            return attachment
        if "spreadsheet" in content_type or "csv" in content_type or "excel" in content_type:
            return attachment
    return attachments[0] if attachments and isinstance(attachments[0], dict) else {}


def _source_text(payload: dict[str, Any] | bytes | str, settings: dict[str, Any]) -> str:
    if isinstance(payload, dict):
        value = _pick(payload, "sourceContent", "source_content", "content", default="")
        if isinstance(value, bytes):
            return _decode_bytes(value)
        if value:
            return _strip_bom(str(value or ""))
        encoded = _pick(payload, "sourceContentBase64", "source_content_base64", default=None)
        if encoded:
            return _decode_bytes(base64.b64decode(str(encoded)))
        return ""
    if isinstance(payload, bytes):
        return _decode_bytes(payload)
    return _strip_bom(str(payload or ""))


def _source_bytes(payload: dict[str, Any] | bytes | str, settings: dict[str, Any]) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, dict):
        value = _pick(payload, "sourceContentBase64", "source_content_base64", default=None)
        if value is not None:
            return base64.b64decode(str(value))
        value = _pick(payload, "sourceContent", "source_content", "content", default=b"")
        encoding = str(_pick(payload, "sourceEncoding", "source_encoding", default=settings.get("sourceEncoding", "")))
    else:
        value = payload
        encoding = str(settings.get("sourceEncoding", ""))
    if isinstance(value, bytes):
        return value
    if encoding.lower() == "base64":
        return base64.b64decode(str(value))
    return str(value or "").encode("utf-8")


def _email_body_text(payload: dict[str, Any] | bytes | str) -> str:
    if isinstance(payload, dict):
        value = _pick(payload, "bodyText", "body_text", "sourceText", "source_text", "sourceContent", default="")
        return str(value or "")
    if isinstance(payload, bytes):
        return _decode_bytes(payload)
    return str(payload or "")


def _rows_from_line_pattern(text: str, line_pattern: str) -> list[dict[str, Any]]:
    regex = re.compile(line_pattern)
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        match = regex.search(line)
        if not match:
            continue
        row = dict(match.groupdict())
        row["_sourceRowIndex"] = index
        rows.append(row)
    return rows


def _apply_po_from_text(order: OrderRun, text: str) -> None:
    if order.po_number:
        return
    match = re.search(r"\bPO\s*(?:#|Number|No\.?)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9_.-]*)", text, re.IGNORECASE)
    if match:
        order.po_number = match.group(1)


def _first_sheet_path(archive: zipfile.ZipFile, settings: dict[str, Any]) -> str:
    requested = str(settings.get("sheetName") or settings.get("sheetPath") or "")
    names = archive.namelist()
    if requested and requested in names:
        return requested
    sheet_paths = sorted(name for name in names if name.startswith("xl/worksheets/") and name.endswith(".xml"))
    if not sheet_paths:
        raise ValueError("XLSX workbook does not contain worksheet XML.")
    return sheet_paths[0]


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("x:si", ns):
        parts = [text.text or "" for text in item.findall(".//x:t", ns)]
        values.append("".join(parts))
    return values


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//x:t", ns))
    value = cell.find("x:v", ns)
    if value is None:
        return ""
    text = value.text or ""
    if cell_type == "s":
        index = int(text or "0")
        return shared_strings[index] if 0 <= index < len(shared_strings) else ""
    return text


def _xlsx_column_index(reference: str) -> int:
    letters = re.sub(r"[^A-Z]", "", reference.upper())
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return index or 1


class _HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.first_table: list[list[str]] = []
        self._in_table = False
        self._in_cell = False
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "table" and not self.first_table:
            self._in_table = True
        elif self._in_table and tag.lower() == "tr":
            self._current_row = []
        elif self._in_table and tag.lower() in {"td", "th"}:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if self._in_table and tag.lower() in {"td", "th"}:
            self._current_row.append("".join(self._current_cell).strip())
            self._in_cell = False
        elif self._in_table and tag.lower() == "tr":
            if self._current_row:
                self.first_table.append(self._current_row)
            self._current_row = []
        elif self._in_table and tag.lower() == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)


def _merged_settings(payload: dict[str, Any], processor_profile: ProcessorProfile | None) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    if processor_profile:
        settings.update(processor_profile.settings)
        settings.setdefault("profileName", processor_profile.name)
    settings.update(dict(_pick(payload, "processorSettings", "processor_settings", default={}) or {}))
    field_map = dict(settings.get("fieldMap") or settings.get("field_map") or {})
    field_map.update(dict(_pick(payload, "fieldMap", "field_map", default={}) or {}))
    if field_map:
        settings["fieldMap"] = field_map
    return settings


def _processor_type_from_payload(
    payload: dict[str, Any],
    processor_profile: ProcessorProfile | None,
    settings: dict[str, Any],
) -> str:
    return _normalize_processor_type(
        str(
            _pick(payload, "processorType", "processor_type", default=None)
            or (processor_profile.processor_type if processor_profile else None)
            or settings.get("processorType")
            or settings.get("processor_type")
            or "csv"
        )
    )


def _normalize_processor_type(processor_type: str) -> str:
    value = re.sub(r"[^a-z0-9]", "", str(processor_type or "csv").lower())
    aliases = {
        "csv": "csv",
        "csvparse": "csv",
        "orderprocesscsvparse": "csv",
        "spreadsheet": "spreadsheet",
        "spreadsheetailayout": "spreadsheet",
        "orderprocessspreadsheetailayout": "spreadsheet",
        "orderprocessspreadsheet": "spreadsheet",
        "spreadsheetorder": "spreadsheet",
        "xlsx": "xlsx",
        "excelxlsx": "xlsx",
        "xls": "xls",
        "xlt": "xlt",
        "legacyworkbook": "legacyWorkbook",
        "pdf": "pdf",
        "documentintelligence": "pdf",
        "emailbody": "emailBody",
        "body": "emailBody",
        "customeroverride": "customerOverride",
        "customerspecific": "customerOverride",
    }
    return aliases.get(value, str(processor_type or "csv"))


def _source_metadata_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(_pick(payload, "sourceMetadata", "source_metadata", default={}) or {})
    for source_key, target_key in {
        "emailMessageId": "emailMessageId",
        "mailbox": "mailbox",
        "sender": "sender",
        "subject": "subject",
        "receivedAt": "receivedAt",
        "sourceFileName": "sourceFileName",
        "contentType": "contentType",
    }.items():
        value = _pick(payload, source_key, default=None)
        if value is not None:
            metadata[target_key] = value
    return metadata


def _has_source_payload(payload: dict[str, Any]) -> bool:
    return any(
        key in payload
        for key in (
            "sourceContent",
            "source_content",
            "sourceContentBase64",
            "source_content_base64",
            "sourceRows",
            "source_rows",
            "rows",
            "content",
            "contentBase64",
            "attachments",
            "bodyText",
            "body_text",
            "sourceText",
            "source_text",
            "extractedText",
            "documentIntelligenceResult",
        )
    )


def _settings(context: OrderProcessorContext | None) -> dict[str, Any]:
    return dict(context.settings if context else {})


def _pick(payload: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _line_from_payload(index: int, payload: Any) -> OrderLine:
    if not isinstance(payload, dict):
        return OrderLine(line_number=index, raw={"value": payload})
    status = payload.get("validationStatus", payload.get("validation_status", MatchStatus.UNRESOLVED))
    if not isinstance(status, MatchStatus):
        status = MatchStatus(status)
    raw = dict(payload.get("raw", payload) or {})
    provided_item_number = (
        payload.get("providedItemNumber", payload.get("provided_item_number", ""))
        or _first_context_alias_value(raw, ITEM_NUMBER_FIELDS)
    )
    provided_upc = (
        payload.get("providedUpc", payload.get("provided_upc", ""))
        or _first_context_alias_value(raw, UPC_FIELDS)
    )
    return OrderLine(
        line_number=int(payload.get("lineNumber", payload.get("line_number", index)) or index),
        quantity=_as_float(payload.get("quantity")),
        provided_item_number=str(provided_item_number or ""),
        provided_upc=str(provided_upc or ""),
        description=str(payload.get("description", "") or ""),
        unit=str(payload.get("unit", "") or ""),
        unit_price=_as_float(payload.get("unitPrice", payload.get("unit_price"))),
        source_row_index=_as_int(payload.get("sourceRowIndex", payload.get("source_row_index"))),
        matched_internal_item_number=payload.get(
            "matchedInternalItemNumber", payload.get("matched_internal_item_number", None)
        ),
        validation_status=status,
        validation_confidence=float(payload.get("validationConfidence", payload.get("validation_confidence", 0.0)) or 0.0),
        validation_method=str(payload.get("validationMethod", payload.get("validation_method", "")) or ""),
        validation_candidates=list(payload.get("validationCandidates", payload.get("validation_candidates", [])) or []),
        validation_errors=list(payload.get("validationErrors", payload.get("validation_errors", [])) or []),
        raw=raw,
    )


def _first_context_alias_value(row_context: dict[str, Any], fields: list[str]) -> str:
    normalized = {re.sub(r"[^a-z0-9]", "", str(key or "").lower()): value for key, value in row_context.items()}
    for field in fields:
        value = normalized.get(re.sub(r"[^a-z0-9]", "", str(field or "").lower()))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _append_error(order: OrderRun, code: str, message: str, **details: Any) -> None:
    error = {"code": code, "message": message}
    error.update(details)
    order.errors.append(error)


def _row_is_empty(row: dict[str, Any]) -> bool:
    return not any(str(value).strip() for key, value in row.items() if not str(key).startswith("_"))


def _normalized_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decode_bytes(source: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return _strip_bom(source.decode(encoding))
        except UnicodeDecodeError:
            continue
    return _strip_bom(source.decode("utf-8", errors="replace"))


def _strip_bom(value: str) -> str:
    return value.lstrip("\ufeff\xef\xbb\xbf")
