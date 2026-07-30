from __future__ import annotations

import base64
import csv
from dataclasses import dataclass
from html.parser import HTMLParser
import hashlib
import io
import json
import os
import re
import zipfile
from typing import Any
import xml.etree.ElementTree as ET

from .models import OrderLine


NORMALIZATION_SCHEMA_VERSION = "spreadsheet-normalization-v1"
LAYOUT_SCHEMA_VERSION = "spreadsheet-layout-v1"
EXTRACTION_SCHEMA_VERSION = "spreadsheet-extraction-v1"
MAX_PREVIEW_ROWS = 40

SPREADSHEET_LAYOUT_SYSTEM_PROMPT = """You are identifying the layout of a distributor order spreadsheet.
Return only JSON matching the requested schema. Do not extract every line item. Identify the order table,
the header row, data row range, the item identifier columns, UPC column, quantity columns, description
column, purchase order source, and any customer/store/ship-to signals. Prefer the specific ship-to store
or delivery location over bill-to/master account details. Preserve leading zeroes in item and UPC fields.
If quantity is expressed in packs and a pack-size column exists, report quantityBasis as packs; otherwise
report units. Mark needsReview when the table or required columns are ambiguous."""

SPREADSHEET_LAYOUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schemaVersion": {"type": "string"},
        "status": {"type": "string"},
        "confidence": {"type": "number"},
        "orderTables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tableId": {"type": "string"},
                    "sheetName": {"type": "string"},
                    "headerRows": {"type": "array", "items": {"type": "integer"}},
                    "dataStartRow": {"type": "integer"},
                    "dataEndRow": {"type": "integer"},
                    "quantityBasis": {"type": "string"},
                    "confidence": {"type": "number"},
                    "columns": {
                        "type": "object",
                        "properties": {
                            role: {
                                "type": ["object", "null"],
                                "properties": {
                                    "index": {"type": "integer"},
                                    "header": {"type": "string"},
                                    "confidence": {"type": "number"},
                                },
                                "required": ["index", "header", "confidence"],
                                "additionalProperties": False,
                            }
                            for role in (
                                "purchaseOrder",
                                "itemNumber",
                                "upc",
                                "quantity",
                                "packQuantity",
                                "packSize",
                                "description",
                                "storeNumber",
                                "storeName",
                                "shipToAddress",
                                "shipToCity",
                                "shipToState",
                                "shipToZip",
                            )
                        },
                        "required": [
                            "purchaseOrder",
                            "itemNumber",
                            "upc",
                            "quantity",
                            "packQuantity",
                            "packSize",
                            "description",
                            "storeNumber",
                            "storeName",
                            "shipToAddress",
                            "shipToCity",
                            "shipToState",
                            "shipToZip",
                        ],
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "tableId",
                    "sheetName",
                    "headerRows",
                    "dataStartRow",
                    "dataEndRow",
                    "quantityBasis",
                    "confidence",
                    "columns",
                ],
                "additionalProperties": False,
            },
        },
        "purchaseOrder": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "source": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["value", "source", "confidence"],
            "additionalProperties": False,
        },
        "customerIdentification": {
            "type": "object",
            "properties": {
                "customerSearchText": {"type": "string"},
                "signals": {"type": "object"},
                "instructions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["customerSearchText", "signals", "instructions"],
            "additionalProperties": True,
        },
        "warnings": {"type": "array", "items": {"type": "object"}},
        "errors": {"type": "array", "items": {"type": "object"}},
    },
    "required": [
        "schemaVersion",
        "status",
        "confidence",
        "orderTables",
        "purchaseOrder",
        "customerIdentification",
        "warnings",
        "errors",
    ],
    "additionalProperties": False,
}


ROLE_ALIASES: dict[str, list[str]] = {
    "itemNumber": [
        "supplier code",
        "suppliercode",
        "supplier item",
        "supplier item number",
        "vendor item",
        "vendors item",
        "vendor item number",
        "vendor sku",
        "our item no",
        "our item number",
        "item #",
        "item no",
        "item number",
        "item code",
        "product code",
        "sku",
    ],
    "upc": [
        "upc",
        "upc #",
        "upc code",
        "product upc",
        "product upc code",
        "barcode",
        "bar code",
        "gtin",
        "ean",
        "product barcode",
    ],
    "quantity": [
        "qty ordered",
        "qty",
        "quantity",
        "order qty",
        "order quantity",
        "units ordered",
        "each qty",
        "ea qty",
    ],
    "packQuantity": ["qty packs ordered", "qty (packs) ordered", "packs ordered", "case qty", "case quantity"],
    "packSize": ["pack size", "pack", "case pack", "case size", "units per case"],
    "description": ["description", "item description", "product description", "product", "item name", "name"],
    "purchaseOrder": ["purchase order", "purchaseorder", "po", "po #", "po number", "po no", "order number"],
    "storeNumber": [
        "store #",
        "store number",
        "store no",
        "store id",
        "ship to store",
        "ship-to store",
        "location number",
        "location id",
    ],
    "storeName": ["store name", "location", "location name", "ship to", "ship-to", "delivery location"],
    "shipToAddress": ["ship to address", "ship-to address", "delivery address", "address"],
    "shipToCity": ["ship to city", "city", "delivery city"],
    "shipToState": ["ship to state", "state", "delivery state"],
    "shipToZip": ["ship to zip", "ship to postal", "zip", "postal code"],
}

NEGATIVE_ROLE_WORDS: dict[str, list[str]] = {
    "quantity": ["stock", "inventory", "weight", "cost", "price", "total", "retail", "received"],
    "packQuantity": ["stock", "inventory", "weight", "cost", "price", "total", "retail", "received"],
    "itemNumber": ["asin", "barcode", "upc", "total", "cost", "price"],
    "upc": ["asin", "supplier code", "suppliercode", "cost", "price", "qty"],
    "storeNumber": ["date", "stock", "cost", "price", "qty", "quantity", "weight"],
    "storeName": ["date", "stock", "cost", "price", "qty", "quantity", "weight"],
}


@dataclass(frozen=True, slots=True)
class SpreadsheetSource:
    file_name: str
    content_type: str
    content: bytes
    source_text: str = ""


def normalize_spreadsheet(payload: dict[str, Any] | bytes | str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    source = _spreadsheet_source(payload, settings)
    file_type = _file_type(source)
    warnings: list[dict[str, Any]] = []

    if file_type in {"csv", "tsv", "txt"}:
        matrix, delimiter, csv_warnings = _csv_matrix(source, settings, file_type)
        warnings.extend(csv_warnings)
        sheets = [_sheet_profile("Sheet1", matrix, delimiter=delimiter)]
    elif file_type in {"xlsx", "xlsm", "xltx"}:
        sheets = _xlsx_sheet_profiles(source.content)
    elif file_type in {"xls", "xlt"}:
        text = source.source_text or _decode_bytes(source.content)
        if "<table" in text.lower():
            sheets = [_sheet_profile("Sheet1", _html_table_matrix(text), delimiter="htmlTable")]
        else:
            try:
                sheets = _xlrd_sheet_profiles(source.content)
            except ModuleNotFoundError as exc:
                return {
                    "schemaVersion": NORMALIZATION_SCHEMA_VERSION,
                    "normalizationId": _normalization_id(source),
                    "status": "failed",
                    "sourceFileName": source.file_name,
                    "fileType": file_type,
                    "contentType": source.content_type,
                    "sheets": [],
                    "warnings": [],
                    "errors": [
                        {
                            "code": "legacyWorkbookDependencyMissing",
                            "message": "Binary XLS/XLT parsing requires the xlrd package in the Function runtime.",
                            "dependency": "xlrd",
                        }
                    ],
                }
            except Exception as exc:
                return {
                    "schemaVersion": NORMALIZATION_SCHEMA_VERSION,
                    "normalizationId": _normalization_id(source),
                    "status": "failed",
                    "sourceFileName": source.file_name,
                    "fileType": file_type,
                    "contentType": source.content_type,
                    "sheets": [],
                    "warnings": [],
                    "errors": [
                        {
                            "code": "legacyWorkbookParseFailed",
                            "message": str(exc),
                        }
                    ],
                }
    else:
        return {
            "schemaVersion": NORMALIZATION_SCHEMA_VERSION,
            "normalizationId": _normalization_id(source),
            "status": "failed",
            "sourceFileName": source.file_name,
            "fileType": file_type,
            "contentType": source.content_type,
            "sheets": [],
            "warnings": [],
            "errors": [{"code": "unsupportedSpreadsheetType", "message": f"Unsupported spreadsheet type: {file_type}"}],
        }

    profile = {
        "schemaVersion": NORMALIZATION_SCHEMA_VERSION,
        "normalizationId": _normalization_id(source),
        "status": "ready",
        "sourceFileName": source.file_name,
        "fileType": file_type,
        "contentType": source.content_type,
        "sheetCount": len(sheets),
        "sheets": sheets,
        "warnings": warnings,
        "errors": [],
    }
    return profile


def analyze_spreadsheet_layout(
    normalization: dict[str, Any],
    payload: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or {}
    deterministic = _deterministic_spreadsheet_layout(normalization, payload, settings)
    if not _truthy(settings.get("enableAiLayout") or settings.get("useAiLayout") or os.environ.get("ORDER_PROCESSOR_ENABLE_SPREADSHEET_LAYOUT_AI")):
        return deterministic
    try:
        ai_layout = _foundry_spreadsheet_layout(normalization, payload or {}, settings)
        return _merge_ai_layout(ai_layout, deterministic)
    except Exception as exc:  # pragma: no cover - deployed model boundary.
        deterministic.setdefault("warnings", []).append(
            {"code": "spreadsheetLayoutAiFailed", "message": str(exc), "fallback": "deterministicLayout"}
        )
        deterministic["aiLayout"] = {
            **dict(deterministic.get("aiLayout") or {}),
            "status": "deterministicFallbackAfterAiFailure",
        }
        return deterministic


def _deterministic_spreadsheet_layout(
    normalization: dict[str, Any],
    payload: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or {}
    warnings: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    for sheet in _as_list(normalization.get("sheets")):
        if not isinstance(sheet, dict):
            continue
        rows = _matrix_from_sheet(sheet)
        for candidate in _candidate_tables(rows):
            if best is None or float(candidate["score"]) > float(best["score"]):
                best = {**candidate, "sheetName": sheet.get("name") or "Sheet1"}

    if best is None:
        return {
            "schemaVersion": LAYOUT_SCHEMA_VERSION,
            "status": "needsReview",
            "confidence": 0.0,
            "orderTables": [],
            "purchaseOrder": {"value": "", "source": "", "confidence": 0.0},
            "customerIdentification": _customer_identification_plan(normalization, None, payload),
            "warnings": [{"code": "noOrderTable", "message": "No likely order table was found."}],
            "errors": [],
            "aiLayout": _ai_layout_metadata(settings),
        }

    sheet = _sheet_by_name(normalization, str(best["sheetName"]))
    rows = _matrix_from_sheet(sheet)
    header_rows = best["headerRows"]
    headers = _headers_for_rows(rows, header_rows)
    data_start = int(best["dataStartRow"])
    data_end = _infer_data_end(rows, data_start)
    sample_rows = rows[data_start - 1 : min(len(rows), data_start + 8)]
    columns = _map_columns(headers, sample_rows)
    confidence = min(0.99, max(0.0, float(best["score"]) / 30.0))

    required_missing = [
        role
        for role in ("itemNumber", "upc", "quantity", "packQuantity")
        if role in {"itemNumber", "upc"} and role not in columns
    ]
    has_identifier = "itemNumber" in columns or "upc" in columns
    has_quantity = "quantity" in columns or "packQuantity" in columns
    if not has_identifier:
        warnings.append({"code": "missingItemIdentifierColumn", "message": "No confident item number or UPC column was found."})
    if not has_quantity:
        warnings.append({"code": "missingQuantityColumn", "message": "No confident quantity column was found."})
    if required_missing and not has_identifier:
        confidence = min(confidence, 0.35)

    purchase_order = _purchase_order_plan(normalization, payload, rows, data_start, data_end, columns)
    quantity_basis = "packs" if "quantity" not in columns and "packQuantity" in columns else "units"
    table = {
        "tableId": "order-table-1",
        "sheetName": best["sheetName"],
        "headerRows": header_rows,
        "dataStartRow": data_start,
        "dataEndRow": data_end,
        "columns": columns,
        "quantityBasis": quantity_basis,
        "confidence": round(confidence, 3),
        "evidence": best["evidence"],
    }
    status = "ready" if has_identifier and has_quantity and confidence >= float(settings.get("minimumLayoutConfidence", 0.55)) else "needsReview"

    return {
        "schemaVersion": LAYOUT_SCHEMA_VERSION,
        "status": status,
        "confidence": round(confidence, 3),
        "orderTables": [table],
        "purchaseOrder": purchase_order,
        "customerIdentification": _customer_identification_plan(normalization, table, payload),
        "warnings": warnings,
        "errors": [],
        "aiLayout": _ai_layout_metadata(settings),
    }


def extract_order_lines(
    payload: dict[str, Any] | bytes | str,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or {}
    if isinstance(payload, dict):
        normalization = _pick(payload, "normalization", "normalizedWorkbook", default=None)
        if not isinstance(normalization, dict):
            normalization = normalize_spreadsheet(payload, settings)
        layout = _pick(payload, "layout", "spreadsheetLayout", default=None)
        if not isinstance(layout, dict):
            layout = analyze_spreadsheet_layout(normalization, payload, settings)
    else:
        normalization = normalize_spreadsheet(payload, settings)
        layout = analyze_spreadsheet_layout(normalization, {}, settings)

    if normalization.get("status") == "failed":
        return {
            "schemaVersion": EXTRACTION_SCHEMA_VERSION,
            "status": "failed",
            "normalization": normalization,
            "layout": layout if isinstance(layout, dict) else {},
            "purchaseOrder": "",
            "lineCount": 0,
            "lines": [],
            "orderGroups": [],
            "warnings": normalization.get("warnings", []),
            "errors": normalization.get("errors", []),
            "requiresHumanReview": True,
        }

    table = (_as_list(layout.get("orderTables")) or [{}])[0]
    if not isinstance(table, dict) or not table:
        return {
            "schemaVersion": EXTRACTION_SCHEMA_VERSION,
            "status": "needsReview",
            "normalization": _compact_normalization(normalization),
            "layout": layout,
            "purchaseOrder": "",
            "lineCount": 0,
            "lines": [],
            "orderGroups": [],
            "warnings": layout.get("warnings", []),
            "errors": [{"code": "noOrderTable", "message": "No order table layout is available."}],
            "requiresHumanReview": True,
        }

    sheet = _sheet_by_name(normalization, str(table.get("sheetName") or "Sheet1"))
    rows = _matrix_from_sheet(sheet)
    columns = dict(table.get("columns") or {})
    data_start = int(table.get("dataStartRow") or 1)
    data_end = int(table.get("dataEndRow") or len(rows))
    quantity_basis = str(table.get("quantityBasis") or "units")
    purchase_order = str(_pick(layout.get("purchaseOrder", {}) if isinstance(layout.get("purchaseOrder"), dict) else {}, "value", default="") or "")

    lines: list[dict[str, Any]] = []
    warnings = [dict(item) for item in _as_list(layout.get("warnings")) if isinstance(item, dict)]
    for row_number in range(data_start, min(data_end, len(rows)) + 1):
        row = rows[row_number - 1]
        if _row_is_blank(row) or _row_looks_like_total(row):
            continue
        raw_values = _row_dict(row, columns)
        item_number = _clean_identifier(_column_value(row, columns, "itemNumber"))
        upc = _clean_identifier(_column_value(row, columns, "upc"))
        description = _clean_text(_column_value(row, columns, "description"))
        quantity, quantity_source = _line_quantity(row, columns, quantity_basis)
        if quantity is None or quantity <= 0:
            if item_number or upc or description:
                warnings.append(
                    {
                        "code": "skippedInvalidQuantity",
                        "message": "Skipped row with missing or non-positive quantity.",
                        "sourceRowIndex": row_number,
                    }
                )
            continue
        if not item_number and not upc and not description:
            continue
        line = {
            "lineNumber": len(lines) + 1,
            "sourceRowIndex": row_number,
            "providedItemNumber": item_number,
            "providedUpc": upc,
            "description": description,
            "quantity": quantity,
            "unit": "EA",
            "unitPrice": _parse_decimal(_column_value(row, columns, "unitPrice")),
            "raw": {
                "spreadsheetRow": row_number,
                "quantitySource": quantity_source,
                "quantityBasis": quantity_basis,
                "values": raw_values,
            },
        }
        lines.append(line)
        if not purchase_order:
            purchase_order = _clean_identifier(_column_value(row, columns, "purchaseOrder"))

    if not purchase_order:
        purchase_order = _fallback_purchase_order(payload if isinstance(payload, dict) else {}, rows)

    groups = _group_lines_for_customer(lines, normalization, layout, payload if isinstance(payload, dict) else {})
    requires_review = bool(layout.get("status") != "ready" or not lines)
    return {
        "schemaVersion": EXTRACTION_SCHEMA_VERSION,
        "status": "needsReview" if requires_review else "ready",
        "normalization": _compact_normalization(normalization),
        "layout": layout,
        "purchaseOrder": purchase_order,
        "lineCount": len(lines),
        "lines": lines,
        "orderGroups": groups,
        "warnings": warnings,
        "errors": [] if lines else [{"code": "noOrderLines", "message": "No valid order lines were extracted."}],
        "requiresHumanReview": requires_review,
    }


def order_lines_from_extraction(extraction: dict[str, Any]) -> list[OrderLine]:
    lines: list[OrderLine] = []
    for item in _as_list(extraction.get("lines")):
        if not isinstance(item, dict):
            continue
        line = OrderLine(
            line_number=int(item.get("lineNumber") or len(lines) + 1),
            quantity=_parse_decimal(item.get("quantity")),
            provided_item_number=str(item.get("providedItemNumber") or ""),
            provided_upc=str(item.get("providedUpc") or ""),
            description=str(item.get("description") or ""),
            unit=str(item.get("unit") or ""),
            unit_price=_parse_decimal(item.get("unitPrice")),
            source_row_index=_parse_int(item.get("sourceRowIndex")),
            raw=dict(item.get("raw") or {}),
        )
        lines.append(line)
    return lines


def _spreadsheet_source(payload: dict[str, Any] | bytes | str, settings: dict[str, Any]) -> SpreadsheetSource:
    if isinstance(payload, bytes):
        return SpreadsheetSource(file_name=str(settings.get("sourceFileName") or "source.csv"), content_type="", content=payload)
    if not isinstance(payload, dict):
        return SpreadsheetSource(
            file_name=str(settings.get("sourceFileName") or "source.csv"),
            content_type="text/csv",
            content=str(payload or "").encode("utf-8"),
            source_text=str(payload or ""),
        )

    attachment = _selected_attachment(payload)
    file_name = str(
        _pick(payload, "sourceFileName", "source_file_name", "fileName", "file_name", default=None)
        or _pick(attachment, "name", "fileName", "file_name", default="")
        or settings.get("sourceFileName")
        or "source.csv"
    )
    content_type = str(
        _pick(payload, "contentType", "content_type", default=None)
        or _pick(attachment, "contentType", "content_type", default="")
        or ""
    )
    value = _pick(payload, "sourceContentBase64", "source_content_base64", "contentBase64", default=None)
    if value is None:
        value = _pick(attachment, "contentBytes", "contentBase64", "content_base64", default=None)
    if value is not None:
        return SpreadsheetSource(file_name=file_name, content_type=content_type, content=base64.b64decode(str(value)))

    value = _pick(payload, "sourceContent", "source_content", "content", default=None)
    if value is None:
        value = _pick(attachment, "content", default=b"")
    if isinstance(value, bytes):
        return SpreadsheetSource(file_name=file_name, content_type=content_type, content=value)

    encoding = str(_pick(payload, "sourceEncoding", "source_encoding", default=settings.get("sourceEncoding", "")) or "")
    if encoding.lower() == "base64":
        content = base64.b64decode(str(value or ""))
        return SpreadsheetSource(file_name=file_name, content_type=content_type, content=content)
    text = str(value or "")
    return SpreadsheetSource(file_name=file_name, content_type=content_type, content=text.encode("utf-8"), source_text=text)


def _selected_attachment(payload: dict[str, Any]) -> dict[str, Any]:
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


def _file_type(source: SpreadsheetSource) -> str:
    extension = source.file_name.rsplit(".", 1)[-1].lower() if "." in source.file_name else ""
    if extension:
        return extension
    content_type = source.content_type.lower()
    if "csv" in content_type:
        return "csv"
    if "tab-separated" in content_type:
        return "tsv"
    if "spreadsheetml" in content_type or "xlsx" in content_type:
        return "xlsx"
    if "excel" in content_type:
        return "xls"
    return "csv"


def _normalization_id(source: SpreadsheetSource) -> str:
    digest = hashlib.sha256(source.content).hexdigest()[:24]
    return f"spreadsheet-{digest}"


def _csv_matrix(source: SpreadsheetSource, settings: dict[str, Any], file_type: str) -> tuple[list[list[str]], str, list[dict[str, Any]]]:
    text = source.source_text or _decode_bytes(source.content)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    delimiter = str(settings.get("delimiter") or "")
    warnings: list[dict[str, Any]] = []
    if not delimiter:
        if file_type == "tsv":
            delimiter = "\t"
        else:
            sample = text[:8192]
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
            except csv.Error:
                delimiter = "\t" if "\t" in sample and sample.count("\t") > sample.count(",") else ","
                warnings.append({"code": "csvDelimiterFallback", "message": "Spreadsheet delimiter sniffing used a fallback."})
    reader = csv.reader(io.StringIO(_strip_bom(text)), delimiter=delimiter)
    matrix = [[_clean_text(value) for value in row] for row in reader]
    return _pad_matrix(matrix), delimiter, warnings


def _xlsx_sheet_profiles(source: bytes) -> list[dict[str, Any]]:
    if not source:
        return []
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        shared_strings = _shared_strings(archive)
        sheets = _workbook_sheets(archive)
        profiles: list[dict[str, Any]] = []
        for sheet_name, sheet_path in sheets:
            if sheet_path not in archive.namelist():
                continue
            matrix = _xlsx_sheet_matrix(archive.read(sheet_path), shared_strings)
            profiles.append(_sheet_profile(sheet_name, matrix, delimiter="xlsx"))
        return profiles


def _xlrd_sheet_profiles(source: bytes) -> list[dict[str, Any]]:
    import xlrd

    book = xlrd.open_workbook(file_contents=source)
    profiles: list[dict[str, Any]] = []
    for sheet in book.sheets():
        matrix: list[list[str]] = []
        for row_index in range(sheet.nrows):
            values: list[str] = []
            for column_index in range(sheet.ncols):
                cell = sheet.cell(row_index, column_index)
                values.append(_xlrd_cell_value(cell, book.datemode))
            matrix.append(values)
        profiles.append(_sheet_profile(sheet.name or f"Sheet{len(profiles) + 1}", matrix, delimiter="xls"))
    return profiles


def _xlrd_cell_value(cell: Any, datemode: int) -> str:
    import xlrd

    if cell.ctype == xlrd.XL_CELL_EMPTY or cell.value is None:
        return ""
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate.xldate_as_datetime(cell.value, datemode).date().isoformat()
        except Exception:
            return str(cell.value)
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        value = float(cell.value)
        return str(int(value)) if value.is_integer() else str(value)
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return "TRUE" if bool(cell.value) else "FALSE"
    return _clean_text(cell.value)


def _workbook_sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    names = archive.namelist()
    if "xl/workbook.xml" not in names:
        return [("Sheet1", name) for name in sorted(n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml"))]
    ns = {
        "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rel_map: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in names:
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        for rel in rels.findall("rel:Relationship", ns):
            rel_id = rel.attrib.get("Id", "")
            target = rel.attrib.get("Target", "")
            if target.startswith("/"):
                path = target.lstrip("/")
            else:
                path = f"xl/{target}"
            rel_map[rel_id] = path.replace("\\", "/")
    sheets: list[tuple[str, str]] = []
    for index, sheet in enumerate(workbook.findall(".//x:sheet", ns), start=1):
        name = sheet.attrib.get("name", f"Sheet{index}")
        rel_id = sheet.attrib.get(f"{{{ns['r']}}}id", "")
        path = rel_map.get(rel_id, f"xl/worksheets/sheet{index}.xml")
        sheets.append((name, path))
    return sheets


def _xlsx_sheet_matrix(xml_bytes: bytes, shared_strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(xml_bytes)
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows_by_index: dict[int, dict[int, str]] = {}
    max_row = 0
    max_col = 0
    for row in root.findall(".//x:sheetData/x:row", ns):
        row_index = int(row.attrib.get("r", len(rows_by_index) + 1))
        max_row = max(max_row, row_index)
        values_by_column: dict[int, str] = {}
        for cell in row.findall("x:c", ns):
            ref = cell.attrib.get("r", "")
            column_index = _xlsx_column_index(ref)
            max_col = max(max_col, column_index)
            values_by_column[column_index] = _xlsx_cell_value(cell, shared_strings, ns)
        rows_by_index[row_index] = values_by_column
    matrix: list[list[str]] = []
    for row_index in range(1, max_row + 1):
        values = rows_by_index.get(row_index, {})
        matrix.append([_clean_text(values.get(index, "")) for index in range(1, max_col + 1)])
    return _pad_matrix(matrix)


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("x:si", ns):
        values.append("".join(text.text or "" for text in item.findall(".//x:t", ns)))
    return values


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//x:t", ns))
    value = cell.find("x:v", ns)
    text = value.text if value is not None else ""
    if cell_type == "s":
        try:
            index = int(text or "0")
        except ValueError:
            return ""
        return shared_strings[index] if 0 <= index < len(shared_strings) else ""
    return text or ""


def _xlsx_column_index(reference: str) -> int:
    letters = re.sub(r"[^A-Z]", "", reference.upper())
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return index or 1


def _sheet_profile(name: str, matrix: list[list[str]], delimiter: str) -> dict[str, Any]:
    matrix = _pad_matrix(matrix)
    candidate_tables = _candidate_tables(matrix)
    return {
        "name": name,
        "rowCount": len(matrix),
        "columnCount": max((len(row) for row in matrix), default=0),
        "delimiter": delimiter,
        "previewRows": matrix[:MAX_PREVIEW_ROWS],
        "rows": matrix,
        "candidateTables": [
            {
                "headerRows": candidate["headerRows"],
                "dataStartRow": candidate["dataStartRow"],
                "score": round(float(candidate["score"]), 3),
                "evidence": candidate["evidence"],
            }
            for candidate in candidate_tables[:5]
        ],
    }


def _candidate_tables(matrix: list[list[str]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row_index in range(len(matrix)):
        if _row_is_blank(matrix[row_index]):
            continue
        for header_height in (1, 2):
            if row_index + header_height > len(matrix):
                continue
            header_rows = list(range(row_index + 1, row_index + header_height + 1))
            headers = _headers_for_rows(matrix, header_rows)
            if not any(headers):
                continue
            sample_start = row_index + header_height
            sample_rows = matrix[sample_start : min(len(matrix), sample_start + 8)]
            header_score, header_evidence = _header_score(headers)
            sample_score, sample_evidence = _sample_score(headers, sample_rows)
            density_score = min(4.0, sum(1 for row in sample_rows if not _row_is_blank(row)) / 2.0)
            score = header_score + sample_score + density_score - (1.5 if header_height == 2 else 0)
            if score >= 8:
                candidates.append(
                    {
                        "headerRows": header_rows,
                        "dataStartRow": sample_start + 1,
                        "score": score,
                        "evidence": {
                            "header": header_evidence,
                            "sample": sample_evidence,
                            "densityScore": round(density_score, 3),
                        },
                    }
                )
    return sorted(candidates, key=lambda item: float(item["score"]), reverse=True)


def _header_score(headers: list[str]) -> tuple[float, dict[str, Any]]:
    role_hits: dict[str, list[str]] = {}
    score = 0.0
    for role in ("itemNumber", "upc", "quantity", "description", "purchaseOrder", "storeNumber", "storeName"):
        best = _best_role_column(headers, [], role)
        if best:
            role_hits[role] = [headers[int(best["index"]) - 1]]
            score += float(best["score"])
    if "itemNumber" in role_hits or "upc" in role_hits:
        score += 4
    if "quantity" in role_hits:
        score += 5
    return min(score, 24.0), {"roleHits": role_hits}


def _sample_score(headers: list[str], rows: list[list[str]]) -> tuple[float, dict[str, Any]]:
    if not rows:
        return 0.0, {"dataRows": 0}
    columns = _map_columns(headers, rows)
    score = 0.0
    if "itemNumber" in columns or "upc" in columns:
        score += 3.0
    if "quantity" in columns or "packQuantity" in columns:
        score += 3.0
    nonempty = sum(1 for row in rows if not _row_is_blank(row))
    score += min(3.0, nonempty / 2.0)
    return score, {"mappedColumns": sorted(columns.keys()), "dataRows": nonempty}


def _headers_for_rows(matrix: list[list[str]], header_rows: list[int]) -> list[str]:
    if not header_rows:
        return []
    width = max((len(matrix[index - 1]) for index in header_rows if 0 <= index - 1 < len(matrix)), default=0)
    headers: list[str] = []
    for column in range(width):
        parts: list[str] = []
        for row_number in header_rows:
            row_index = row_number - 1
            if row_index < 0 or row_index >= len(matrix):
                continue
            value = _clean_text(matrix[row_index][column] if column < len(matrix[row_index]) else "")
            if value and value not in parts:
                parts.append(value)
        headers.append(" ".join(parts).strip() or f"Column {column + 1}")
    return headers


def _map_columns(headers: list[str], sample_rows: list[list[str]]) -> dict[str, dict[str, Any]]:
    columns: dict[str, dict[str, Any]] = {}
    for role in (
        "purchaseOrder",
        "itemNumber",
        "upc",
        "quantity",
        "packQuantity",
        "packSize",
        "description",
        "storeNumber",
        "storeName",
        "shipToAddress",
        "shipToCity",
        "shipToState",
        "shipToZip",
    ):
        best = _best_role_column(headers, sample_rows, role)
        if best and (
            role not in {"storeNumber", "storeName", "shipToAddress", "shipToCity", "shipToState", "shipToZip"}
            or best["score"] >= 4
        ):
            columns[role] = best
    if "quantity" in columns and "packQuantity" in columns and columns["quantity"]["index"] == columns["packQuantity"]["index"]:
        if columns["packQuantity"]["score"] > columns["quantity"]["score"]:
            columns.pop("quantity", None)
        else:
            columns.pop("packQuantity", None)
    if "itemNumber" in columns and "upc" in columns and columns["itemNumber"]["index"] == columns["upc"]["index"]:
        if columns["upc"]["score"] > columns["itemNumber"]["score"]:
            columns.pop("itemNumber", None)
        else:
            columns.pop("upc", None)
    return columns


def _best_role_column(headers: list[str], sample_rows: list[list[str]], role: str) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for index, header in enumerate(headers, start=1):
        values = [row[index - 1] for row in sample_rows if index - 1 < len(row)]
        score = _role_score(header, values, role)
        if score <= 0:
            continue
        candidate = {
            "index": index,
            "header": header,
            "confidence": round(min(score / 12.0, 0.99), 3),
            "score": round(score, 3),
        }
        if best is None or score > float(best["score"]):
            best = candidate
    return best


def _role_score(header: str, values: list[str], role: str) -> float:
    normalized = _normalized_header(header)
    human = _human_header(header)
    score = 0.0
    for alias in ROLE_ALIASES.get(role, []):
        alias_norm = _normalized_header(alias)
        if normalized == alias_norm:
            score += 10.0
        elif alias_norm and len(alias_norm) >= 6 and alias_norm in normalized:
            score += 6.0
    for negative in NEGATIVE_ROLE_WORDS.get(role, []):
        if _normalized_header(negative) in normalized:
            score -= 7.0

    nonempty = [_clean_text(value) for value in values if _clean_text(value)]
    if not nonempty:
        return score
    if role == "upc":
        score += 4.0 * _value_ratio(nonempty, lambda value: bool(re.fullmatch(r"\d{8,14}", _clean_identifier(value))))
    elif role == "itemNumber":
        score += 3.0 * _value_ratio(nonempty, lambda value: bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,20}", _clean_identifier(value))))
        if "supplier" in human:
            score += 3.0
    elif role in {"quantity", "packQuantity", "packSize"}:
        score += 4.0 * _value_ratio(nonempty, lambda value: _parse_decimal(value) is not None)
        if role == "quantity" and "pack" in human:
            score -= 5.0
        if role == "packQuantity" and "pack" in human:
            score += 4.0
    elif role == "purchaseOrder":
        score += 3.0 * _value_ratio(nonempty, lambda value: bool(_clean_identifier(value)))
    elif role == "description":
        score += 2.0 * _value_ratio(nonempty, lambda value: len(value) > 8 and not bool(re.fullmatch(r"\d+(\.\d+)?", value)))
    else:
        score += 1.0 * _value_ratio(nonempty, lambda value: bool(value))
    return score


def _purchase_order_plan(
    normalization: dict[str, Any],
    payload: dict[str, Any] | None,
    rows: list[list[str]],
    data_start: int,
    data_end: int,
    columns: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    payload = payload or {}
    text_sources = [
        str(_pick(payload, "subject", default="") or ""),
        str(_pick(payload, "bodyText", "body_text", "sourceText", "source_text", default="") or ""),
    ]
    email = _pick(payload, "email", "emailMessage", default={})
    if isinstance(email, dict):
        text_sources.insert(0, str(_pick(email, "subject", default="") or ""))
        text_sources.append(str(_pick(email, "body", "bodyText", "body_text", default="") or ""))
    for source_name, text in (("emailSubjectBody", "\n".join(text_sources)),):
        value = _po_from_text(text)
        if value:
            return {"value": value, "source": source_name, "confidence": 0.85}
    metadata_rows = _metadata_text(normalization, before_row=data_start)
    value = _po_from_text(metadata_rows)
    if value:
        return {"value": value, "source": "spreadsheetMetadata", "confidence": 0.75}
    if "purchaseOrder" in columns:
        values = [
            _clean_identifier(_column_value(rows[row_index - 1], columns, "purchaseOrder"))
            for row_index in range(data_start, min(data_end, len(rows)) + 1)
            if not _row_is_blank(rows[row_index - 1])
        ]
        repeated = _dominant_value(values)
        if repeated:
            return {"value": repeated, "source": "spreadsheetColumn", "confidence": 0.95}
    return {"value": "", "source": "", "confidence": 0.0}


def _customer_identification_plan(
    normalization: dict[str, Any],
    table: dict[str, Any] | None,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = payload or {}
    signals: dict[str, Any] = {}
    if table:
        sheet = _sheet_by_name(normalization, str(table.get("sheetName") or "Sheet1"))
        rows = _matrix_from_sheet(sheet)
        columns = dict(table.get("columns") or {})
        data_start = int(table.get("dataStartRow") or 1)
        data_end = int(table.get("dataEndRow") or min(len(rows), data_start + 20))
        store_roles = ["storeNumber", "storeName", "shipToAddress", "shipToCity", "shipToState", "shipToZip"]
        for role in store_roles:
            if role in columns:
                values = [
                    _column_value(rows[row_index - 1], columns, role)
                    for row_index in range(data_start, min(data_end, len(rows)) + 1)
                ]
                dominant = _dominant_value(values)
                if dominant:
                    signals[role] = dominant
        signals["repeatingSpreadsheetValues"] = _repeating_values(rows, data_start, min(data_end, len(rows)), columns)
        signals["firstOrderRows"] = [
            _row_dict(rows[row_index - 1], columns)
            for row_index in range(data_start, min(data_end, data_start + 4, len(rows)) + 1)
            if not _row_is_blank(rows[row_index - 1])
        ]
        signals["metadataRows"] = _metadata_rows(rows, before_row=data_start)

    email = _pick(payload, "email", "emailMessage", default={})
    if isinstance(email, dict):
        signals["sender"] = _pick(email, "sender", "from", default="")
        signals["subject"] = _pick(email, "subject", default="")
        body = str(_pick(email, "body", "bodyText", "body_text", default="") or "")
        signals["emailBodyPreview"] = _clean_text(body)[:1500]
    else:
        signals["sender"] = _pick(payload, "sender", default="")
        signals["subject"] = _pick(payload, "subject", default="")
    signals["sourceFileName"] = normalization.get("sourceFileName", "")
    text = _customer_search_text(signals)
    return {
        "customerSearchText": text,
        "signals": signals,
        "instructions": [
            "Identify the specific ship-to/store/location customer code, not just the bill-to or master account.",
            "Use sender/domain, email body, spreadsheet metadata above the table, repeated store/location columns, and the first order rows.",
        ],
    }


def _ai_layout_metadata(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "layoutOnly",
        "status": "deterministicFallbackUsed",
        "recommendedDeployment": settings.get("layoutModelDeployment", settings.get("foundryDeployment", "")),
        "systemPrompt": SPREADSHEET_LAYOUT_SYSTEM_PROMPT,
    }


def _foundry_spreadsheet_layout(
    normalization: dict[str, Any],
    payload: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    from .customer_identification import FoundryCustomerAiJsonClient

    deployment = (
        settings.get("layoutModelDeployment")
        or settings.get("spreadsheetLayoutDeployment")
        or os.environ.get("AZURE_AI_FOUNDRY_SPREADSHEET_LAYOUT_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_SPREADSHEET_LAYOUT_DEPLOYMENT")
        or None
    )
    client = FoundryCustomerAiJsonClient(deployment=str(deployment) if deployment else None)
    email = _pick(payload, "email", "emailMessage", default={})
    if not isinstance(email, dict):
        email = {}
    response = client.complete_json(
        system_prompt=SPREADSHEET_LAYOUT_SYSTEM_PROMPT,
        user_payload={
            "normalizedWorkbook": _compact_normalization(normalization),
            "emailContext": {
                "sender": _pick(email, "sender", "from", default=_pick(payload, "sender", default="")),
                "subject": _pick(email, "subject", default=_pick(payload, "subject", default="")),
                "bodyPreview": str(_pick(email, "body", "bodyText", "body_text", default=_pick(payload, "bodyText", "body_text", default="")) or "")[:2500],
            },
            "requiredOutput": "Return a layout plan only. Do not return all order lines.",
        },
        schema=SPREADSHEET_LAYOUT_SCHEMA,
        schema_name="spreadsheet_order_layout",
        temperature=0.0,
    )
    if not isinstance(response, dict):
        raise ValueError("Spreadsheet layout model returned a non-object response.")
    return response


def _merge_ai_layout(ai_layout: dict[str, Any], deterministic: dict[str, Any]) -> dict[str, Any]:
    tables = [table for table in _as_list(ai_layout.get("orderTables")) if isinstance(table, dict)]
    if not tables:
        raise ValueError("Spreadsheet layout model did not return any orderTables.")
    table = tables[0]
    columns = {
        role: value
        for role, value in dict(table.get("columns") or {}).items()
        if isinstance(value, dict) and int(value.get("index") or 0) > 0
    }
    table["columns"] = columns
    ai_layout = {
        **ai_layout,
        "schemaVersion": LAYOUT_SCHEMA_VERSION,
        "orderTables": [table],
        "warnings": [dict(item) for item in _as_list(ai_layout.get("warnings")) if isinstance(item, dict)],
        "errors": [dict(item) for item in _as_list(ai_layout.get("errors")) if isinstance(item, dict)],
        "aiLayout": {
            "mode": "layoutOnly",
            "status": "foundryLayoutUsed",
            "fallbackConfidence": deterministic.get("confidence", 0),
        },
    }
    if not isinstance(ai_layout.get("purchaseOrder"), dict):
        ai_layout["purchaseOrder"] = deterministic.get("purchaseOrder", {"value": "", "source": "", "confidence": 0.0})
    if not isinstance(ai_layout.get("customerIdentification"), dict):
        ai_layout["customerIdentification"] = deterministic.get("customerIdentification", {})
    elif not ai_layout["customerIdentification"].get("customerSearchText"):
        ai_layout["customerIdentification"] = deterministic.get("customerIdentification", {})
    return ai_layout


def _group_lines_for_customer(
    lines: list[dict[str, Any]],
    normalization: dict[str, Any],
    layout: dict[str, Any],
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    customer_plan = dict(layout.get("customerIdentification") or {})
    return [
        {
            "groupId": "default",
            "lineCount": len(lines),
            "customerSearchText": customer_plan.get("customerSearchText", ""),
            "customerSignals": customer_plan.get("signals", {}),
            "lines": lines,
        }
    ]


def _line_quantity(row: list[str], columns: dict[str, dict[str, Any]], quantity_basis: str) -> tuple[float | None, dict[str, Any]]:
    units = _parse_decimal(_column_value(row, columns, "quantity"))
    packs = _parse_decimal(_column_value(row, columns, "packQuantity"))
    pack_size = _parse_decimal(_column_value(row, columns, "packSize"))
    if units is not None and quantity_basis != "packs":
        return units, {"role": "quantity", "rawQuantity": units}
    if packs is not None and pack_size is not None:
        return packs * pack_size, {"role": "packQuantity", "rawQuantity": packs, "packSize": pack_size}
    if units is not None:
        return units, {"role": "quantity", "rawQuantity": units}
    if packs is not None:
        return packs, {"role": "packQuantity", "rawQuantity": packs}
    return None, {}


def _column_value(row: list[str], columns: dict[str, dict[str, Any]], role: str) -> str:
    column = columns.get(role)
    if not column:
        return ""
    index = int(column.get("index") or 0) - 1
    if index < 0 or index >= len(row):
        return ""
    return _clean_text(row[index])


def _row_dict(row: list[str], columns: dict[str, dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for role, column in columns.items():
        value = _column_value(row, columns, role)
        if value:
            result[role] = value
            result[str(column.get("header") or role)] = value
    return result


def _infer_data_end(rows: list[list[str]], data_start: int) -> int:
    blank_streak = 0
    end = len(rows)
    for row_number in range(data_start, len(rows) + 1):
        row = rows[row_number - 1]
        if _row_is_blank(row):
            blank_streak += 1
            if blank_streak >= 2:
                return max(data_start, row_number - blank_streak)
            continue
        blank_streak = 0
        if _row_looks_like_total(row):
            return max(data_start, row_number - 1)
        end = row_number
    return end


def _metadata_rows(rows: list[list[str]], before_row: int) -> list[str]:
    result: list[str] = []
    for row in rows[: max(0, before_row - 1)]:
        values = [_clean_text(value) for value in row if _clean_text(value)]
        if values:
            result.append(" | ".join(values))
    return result[-12:]


def _metadata_text(normalization: dict[str, Any], before_row: int) -> str:
    sheet = (_as_list(normalization.get("sheets")) or [{}])[0]
    rows = _matrix_from_sheet(sheet if isinstance(sheet, dict) else {})
    return "\n".join(_metadata_rows(rows, before_row))


def _repeating_values(
    rows: list[list[str]],
    data_start: int,
    data_end: int,
    mapped_columns: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    mapped_indexes = {int(column.get("index") or 0) for column in mapped_columns.values()}
    result: list[dict[str, Any]] = []
    width = max((len(row) for row in rows), default=0)
    data_rows = [row for row in rows[data_start - 1 : data_end] if not _row_is_blank(row)]
    if len(data_rows) < 2:
        return result
    for column_index in range(1, width + 1):
        if column_index in mapped_indexes:
            continue
        values = [_clean_text(row[column_index - 1] if column_index - 1 < len(row) else "") for row in data_rows]
        dominant = _dominant_value(values)
        if dominant and len(dominant) >= 2:
            result.append({"columnIndex": column_index, "value": dominant})
        if len(result) >= 8:
            break
    return result


def _customer_search_text(signals: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("sender", "subject", "sourceFileName", "storeNumber", "storeName", "shipToAddress", "shipToCity", "shipToState", "shipToZip"):
        value = signals.get(key)
        if value:
            parts.append(f"{key}: {value}")
    for row in _as_list(signals.get("metadataRows")):
        parts.append(f"metadata: {row}")
    for item in _as_list(signals.get("repeatingSpreadsheetValues")):
        if isinstance(item, dict) and item.get("value"):
            parts.append(f"repeating value: {item.get('value')}")
    for row in _as_list(signals.get("firstOrderRows"))[:5]:
        if isinstance(row, dict):
            compact = ", ".join(f"{key}={value}" for key, value in row.items() if value)
            if compact:
                parts.append(f"order row: {compact}")
    body = signals.get("emailBodyPreview")
    if body:
        parts.append(f"email body: {body}")
    return "\n".join(dict.fromkeys(parts))[:8000]


def _compact_normalization(normalization: dict[str, Any]) -> dict[str, Any]:
    compact = dict(normalization)
    sheets = []
    for sheet in _as_list(normalization.get("sheets")):
        if not isinstance(sheet, dict):
            continue
        sheet_copy = {key: value for key, value in sheet.items() if key != "rows"}
        sheets.append(sheet_copy)
    compact["sheets"] = sheets
    return compact


def _sheet_by_name(normalization: dict[str, Any], name: str) -> dict[str, Any]:
    sheets = [sheet for sheet in _as_list(normalization.get("sheets")) if isinstance(sheet, dict)]
    for sheet in sheets:
        if str(sheet.get("name") or "") == name:
            return sheet
    return sheets[0] if sheets else {}


def _matrix_from_sheet(sheet: dict[str, Any]) -> list[list[str]]:
    rows = _pick(sheet, "rows", "previewRows", default=[])
    matrix: list[list[str]] = []
    for row in _as_list(rows):
        if isinstance(row, list):
            matrix.append([_clean_text(value) for value in row])
    return _pad_matrix(matrix)


def _pad_matrix(matrix: list[list[str]]) -> list[list[str]]:
    width = max((len(row) for row in matrix), default=0)
    return [row + [""] * (width - len(row)) for row in matrix]


def _row_is_blank(row: list[str]) -> bool:
    return not any(_clean_text(value) for value in row)


def _row_looks_like_total(row: list[str]) -> bool:
    text = " ".join(_clean_text(value).lower() for value in row if _clean_text(value))
    return bool(re.search(r"\b(grand\s+total|order\s+total|subtotal|total)\b", text))


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\ufeff", "")).strip()


def _clean_identifier(value: Any) -> str:
    text = _clean_text(value)
    if text.endswith(".0") and re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    text = text.strip("'")
    text = re.sub(r"[\s-]+", "", text)
    return text


def _human_header(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _normalized_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _value_ratio(values: list[str], predicate: Any) -> float:
    if not values:
        return 0.0
    matches = sum(1 for value in values if predicate(value))
    return matches / len(values)


def _parse_decimal(value: Any) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    text = text.replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dominant_value(values: list[Any]) -> str:
    cleaned = [_clean_text(value) for value in values if _clean_text(value)]
    if not cleaned:
        return ""
    counts: dict[str, int] = {}
    for value in cleaned:
        counts[value] = counts.get(value, 0) + 1
    value, count = max(counts.items(), key=lambda item: item[1])
    if count >= max(1, len(cleaned) // 2):
        return value
    return ""


def _po_from_text(text: str) -> str:
    if not text:
        return ""
    patterns = [
        r"\b(?:purchase\s+order|po|p\.o\.|order)\s*(?:number|no\.?|#)?\s*(?:[:|\-]\s*)?([A-Z0-9][A-Z0-9_.-]{2,})",
        r"\bPO([A-Z0-9][A-Z0-9_.-]{2,})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _clean_identifier(match.group(1))
    return ""


def _fallback_purchase_order(payload: dict[str, Any], rows: list[list[str]]) -> str:
    for key in ("poNumber", "po_number", "orderNumber", "order_number"):
        value = _clean_identifier(_pick(payload, key, default=""))
        if value:
            return value
    for row in rows[:15]:
        value = _po_from_text(" ".join(row))
        if value:
            return value
    return ""


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _pick(payload: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return default


def _strip_bom(value: str) -> str:
    return value.lstrip("\ufeff")


def _decode_bytes(value: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _html_table_matrix(text: str) -> list[list[str]]:
    parser = _HtmlTableParser()
    parser.feed(text)
    return parser.first_table


class _HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.first_table: list[list[str]] = []
        self._in_table = False
        self._in_cell = False
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table" and not self.first_table:
            self._in_table = True
        elif self._in_table and tag == "tr":
            self._current_row = []
        elif self._in_table and tag in {"td", "th"}:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._in_table and tag in {"td", "th"}:
            self._current_row.append(_clean_text("".join(self._current_cell)))
            self._in_cell = False
        elif self._in_table and tag == "tr":
            if self._current_row:
                self.first_table.append(self._current_row)
            self._current_row = []
        elif self._in_table and tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)
