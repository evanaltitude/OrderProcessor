from __future__ import annotations

import base64
import csv
from dataclasses import dataclass
from email import message_from_string
from email.message import Message
from html.parser import HTMLParser
import io
import os
import re
from typing import Any

from .models import OrderLine


EXTRACTION_SCHEMA_VERSION = "email-body-order-v1"

EMAIL_BODY_ORDER_SYSTEM_PROMPT = """You extract purchase orders from email bodies for Frontier Distributing.
Return JSON only. Use the email subject, sender, body text, and HTML text provided by the user payload.
Extract order lines only when the email clearly contains an item, UPC, description, and/or quantity.
Do not infer quantities or identifiers that are not present. Preserve leading zeroes in UPCs and item numbers.
Also extract customer identification context from the email, especially slam dunk customer codes, ship-to or delivery
locations, store numbers, address lines, and named customer locations. The customer search text should be useful for
matching the exact ship-to location, not just a bill-to or master account."""

EMAIL_BODY_ORDER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "purchaseOrder": {"type": "string"},
        "customerIdentification": {
            "type": "object",
            "properties": {
                "customerSearchText": {"type": "string"},
                "signals": {
                    "type": "object",
                    "properties": {
                        "customerCode": {"type": "string"},
                        "storeNumber": {"type": "string"},
                        "shipToName": {"type": "string"},
                        "shipToAddress": {"type": "string"},
                        "shipToCity": {"type": "string"},
                        "shipToState": {"type": "string"},
                        "shipToPostalCode": {"type": "string"},
                        "deliveryLocation": {"type": "string"},
                        "sender": {"type": "string"},
                        "subject": {"type": "string"},
                        "emailBodyPreview": {"type": "string"},
                    },
                    "required": [
                        "customerCode",
                        "storeNumber",
                        "shipToName",
                        "shipToAddress",
                        "shipToCity",
                        "shipToState",
                        "shipToPostalCode",
                        "deliveryLocation",
                        "sender",
                        "subject",
                        "emailBodyPreview",
                    ],
                    "additionalProperties": False,
                },
            },
            "required": ["customerSearchText", "signals"],
            "additionalProperties": False,
        },
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "providedItemNumber": {"type": "string"},
                    "providedUpc": {"type": "string"},
                    "description": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string"},
                    "unitPrice": {"type": ["number", "null"]},
                    "sourceText": {"type": "string"},
                },
                "required": [
                    "providedItemNumber",
                    "providedUpc",
                    "description",
                    "quantity",
                    "unit",
                    "unitPrice",
                    "sourceText",
                ],
                "additionalProperties": False,
            },
        },
        "warnings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["code", "message"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["purchaseOrder", "customerIdentification", "lines", "warnings"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class EmailBodySource:
    subject: str = ""
    sender: str = ""
    recipients: str = ""
    body_text: str = ""
    body_html: str = ""
    source_text: str = ""


def extract_email_body_order(
    payload: dict[str, Any] | bytes | str,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or {}
    source = _email_body_source(payload)
    rows = _rows_from_payload(payload) if isinstance(payload, dict) else None
    line_warnings: list[dict[str, Any]] = []
    if rows is not None:
        lines = _canonical_lines_from_rows(rows, line_warnings)
    else:
        lines = _deterministic_lines(source, settings, line_warnings)

    deterministic = {
        "schemaVersion": EXTRACTION_SCHEMA_VERSION,
        "status": "ready" if lines else "needsReview",
        "purchaseOrder": _purchase_order(source),
        "lineCount": len(lines),
        "lines": lines,
        "customerIdentification": _customer_identification_plan(source, settings),
        "warnings": line_warnings,
        "errors": [] if lines else [{"code": "noOrderLines", "message": "No valid order lines were extracted from the email body."}],
        "requiresHumanReview": not bool(lines),
        "aiExtraction": _ai_extraction_metadata(settings, "deterministicFallbackUsed"),
        "source": {
            "subject": source.subject,
            "sender": source.sender,
            "recipients": source.recipients,
            "bodyTextPreview": _clean_text(source.body_text)[:1500],
            "bodyHtmlPresent": bool(source.body_html.strip()),
        },
    }
    if not _ai_enabled(settings):
        return deterministic

    try:
        ai = _foundry_email_body_extraction(source, settings)
        return _merge_ai_extraction(ai, deterministic, source, settings)
    except Exception as exc:  # pragma: no cover - external service boundary.
        deterministic["warnings"].append(
            {
                "code": "foundryEmailBodyExtractionFailed",
                "message": str(exc),
            }
        )
        deterministic["aiExtraction"] = _ai_extraction_metadata(settings, "fallbackAfterFoundryFailure")
        return deterministic


def order_lines_from_extraction(extraction: dict[str, Any]) -> list[OrderLine]:
    lines: list[OrderLine] = []
    for item in _as_list(extraction.get("lines")):
        if not isinstance(item, dict):
            continue
        line = OrderLine(
            line_number=_parse_int(item.get("lineNumber"), len(lines) + 1),
            quantity=_parse_decimal(item.get("quantity")),
            provided_item_number=_clean_identifier(item.get("providedItemNumber")),
            provided_upc=_clean_identifier(item.get("providedUpc")),
            description=_clean_text(str(item.get("description") or "")),
            unit=str(item.get("unit") or ""),
            unit_price=_parse_decimal(item.get("unitPrice")),
            source_row_index=_parse_int(item.get("sourceRowIndex"), None),
            raw=dict(item.get("raw") or {}),
        )
        lines.append(line)
    return lines


def _email_body_source(payload: dict[str, Any] | bytes | str) -> EmailBodySource:
    if isinstance(payload, bytes):
        return _source_from_text(_decode_bytes(payload), {}, "")
    if not isinstance(payload, dict):
        return _source_from_text(str(payload or ""), {}, "")

    nested = _pick(payload, "emailMessage", "email", default={})
    if not isinstance(nested, dict):
        nested = {}
    body_text = str(
        _pick(
            payload,
            "bodyText",
            "body_text",
            "sourceText",
            "source_text",
            "text",
            default=_pick(nested, "bodyText", "body_text", "text", default=""),
        )
        or ""
    )
    body_html = str(
        _pick(
            payload,
            "bodyHtml",
            "body_html",
            "html",
            default=_pick(nested, "bodyHtml", "body_html", "html", default=""),
        )
        or ""
    )
    source_content = _pick(
        payload,
        "sourceContent",
        "source_content",
        "content",
        default=_pick(nested, "sourceContent", "source_content", "content", default=""),
    )
    source_content_base64 = _pick(
        payload,
        "sourceContentBase64",
        "source_content_base64",
        "contentBase64",
        "content_base64",
        default=None,
    )
    if source_content_base64 is not None and not body_text:
        body_text = _decode_bytes(base64.b64decode(str(source_content_base64)))
    elif source_content is not None and not body_text:
        body_text = _decode_bytes(source_content) if isinstance(source_content, bytes) else str(source_content or "")

    headers = {
        "subject": str(_pick(payload, "subject", default=_pick(nested, "subject", default="")) or ""),
        "sender": str(_pick(payload, "sender", "from", default=_pick(nested, "sender", "from", default="")) or ""),
        "recipients": str(
            _pick(
                payload,
                "recipients",
                "to",
                default=_pick(nested, "recipients", "to", default=""),
            )
            or ""
        ),
    }
    return _source_from_text(body_text, headers, body_html)


def _source_from_text(text: str, headers: dict[str, str], body_html: str) -> EmailBodySource:
    parsed = _parse_rfc_email(text)
    subject = headers.get("subject") or parsed.get("subject", "")
    sender = headers.get("sender") or parsed.get("sender", "")
    recipients = headers.get("recipients") or parsed.get("recipients", "")
    parsed_text = parsed.get("bodyText", "")
    parsed_html = parsed.get("bodyHtml", "")
    body_text = parsed_text or text
    html = body_html or parsed_html
    html_text = _html_to_text(html)
    if html_text and html_text not in body_text:
        body_text = "\n".join(part for part in [body_text, html_text] if part.strip())
    return EmailBodySource(
        subject=_clean_text(subject),
        sender=_clean_text(sender),
        recipients=_clean_text(recipients),
        body_text=_strip_bom(body_text),
        body_html=html,
        source_text=text,
    )


def _parse_rfc_email(text: str) -> dict[str, str]:
    if not _looks_like_eml(text):
        return {}
    message = message_from_string(text)
    return {
        "subject": str(message.get("Subject", "") or ""),
        "sender": str(message.get("From", "") or ""),
        "recipients": str(message.get("To", "") or ""),
        "bodyText": _message_body(message, "plain"),
        "bodyHtml": _message_body(message, "html"),
    }


def _looks_like_eml(text: str) -> bool:
    sample = text[:4000]
    if "\n\n" not in sample.replace("\r\n", "\n"):
        return False
    return bool(re.search(r"(?im)^(From|To|Subject|Date|Message-ID):\s+.+$", sample))


def _message_body(message: Message, subtype: str) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_subtype().lower() != subtype:
                continue
            payload = part.get_payload(decode=True)
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace") if payload else ""
        return ""
    if message.get_content_subtype().lower() != subtype:
        return ""
    payload = message.get_payload(decode=True)
    charset = message.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace") if payload else str(message.get_payload() or "")


def _deterministic_lines(
    source: EmailBodySource,
    settings: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    text = source.body_text
    line_pattern = settings.get("linePattern") or settings.get("line_pattern")
    if line_pattern:
        rows = _rows_from_line_pattern(text, str(line_pattern))
        lines = _canonical_lines_from_rows(rows, warnings)
        if lines:
            return lines

    html_rows = _rows_from_html_tables(source.body_html)
    lines = _canonical_lines_from_rows(html_rows, warnings)
    if lines:
        return lines

    rows = _rows_from_pipe_table(text)
    lines = _canonical_lines_from_rows(rows, warnings)
    if lines:
        return lines

    rows = _rows_from_delimited_tables(text)
    lines = _canonical_lines_from_rows(rows, warnings)
    if lines:
        return lines

    return _lines_from_simple_text(text, warnings)


def _rows_from_html_tables(html: str) -> list[dict[str, Any]]:
    if not html.strip() or "<table" not in html.lower():
        return []
    parser = _HtmlTableParser()
    parser.feed(html)
    for table in parser.tables:
        rows = _matrix_to_rows(table)
        if rows:
            return rows
    return []


def _rows_from_pipe_table(text: str) -> list[dict[str, Any]]:
    pipe_lines = [line.strip() for line in text.splitlines() if "|" in line and line.strip()]
    if len(pipe_lines) < 2:
        return []
    matrix = [_split_pipe_line(line) for line in pipe_lines if not _is_markdown_separator(line)]
    matrix = [row for row in matrix if any(cell.strip() for cell in row)]
    return _matrix_to_rows(matrix)


def _split_pipe_line(line: str) -> list[str]:
    cells = [part.strip() for part in line.strip().split("|")]
    if cells and not cells[0]:
        cells = cells[1:]
    if cells and not cells[-1]:
        cells = cells[:-1]
    return cells


def _is_markdown_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?[\s:\-|]+\|?\s*", line)) and "---" in line


def _rows_from_delimited_tables(text: str) -> list[dict[str, Any]]:
    lines = [line.strip() for line in text.splitlines()]
    for delimiter in [",", "\t", ";"]:
        block: list[str] = []
        for line in [*lines, ""]:
            if line and delimiter in line:
                block.append(line)
                continue
            rows = _rows_from_delimited_block(block, delimiter)
            if rows:
                return rows
            block = []
    return []


def _rows_from_delimited_block(block: list[str], delimiter: str) -> list[dict[str, Any]]:
    if len(block) < 2 or not _line_has_order_header(block[0]):
        return []
    reader = csv.reader(io.StringIO("\n".join(block)), delimiter=delimiter)
    matrix = [[cell.strip() for cell in row] for row in reader]
    return _matrix_to_rows(matrix)


def _matrix_to_rows(matrix: list[list[str]]) -> list[dict[str, Any]]:
    matrix = [row for row in matrix if any(str(value).strip() for value in row)]
    if not matrix:
        return []
    if _row_has_order_header(matrix[0]):
        headers = [str(value).strip() or f"Column {index + 1}" for index, value in enumerate(matrix[0])]
        data_rows = matrix[1:]
        start_index = 2
    else:
        headers = ["provided_item_number", "provided_upc", "quantity", "description"]
        data_rows = matrix
        start_index = 1

    rows: list[dict[str, Any]] = []
    for row_index, values in enumerate(data_rows, start=start_index):
        if not any(str(value).strip() for value in values):
            continue
        row = {
            headers[column] if column < len(headers) else f"Column {column + 1}": value
            for column, value in enumerate(values)
        }
        row["_sourceRowIndex"] = row_index
        rows.append(row)
    return rows


def _lines_from_simple_text(text: str, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    patterns = [
        re.compile(
            r"^\s*(?P<provided_item_number>\d{5,12})\s*(?:,|\t|\s+-\s+|\s{2,})\s*"
            r"(?P<provided_upc>\d{8,14})\s*(?:,|\t|\s+-\s+|\s{2,})\s*"
            r"(?P<quantity>\d+(?:\.\d+)?)\s*(?P<description>.*?)\s*$"
        ),
        re.compile(r"^\s*(?P<provided_upc>\d{8,14})\s*[-xX]\s*(?P<quantity>\d+(?:\.\d+)?)\s*$"),
        re.compile(
            r"\b(?:item|sku|supplier\s*code)\s*[:#-]?\s*(?P<provided_item_number>[A-Za-z0-9_.-]{3,})"
            r".{0,80}?\b(?:upc|barcode|gtin)\s*[:#-]?\s*(?P<provided_upc>\d{8,14})"
            r".{0,80}?\b(?:qty|quantity)\s*[:#-]?\s*(?P<quantity>\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
    ]
    for index, line in enumerate(text.splitlines(), start=1):
        for pattern in patterns:
            match = pattern.search(line)
            if not match:
                continue
            row = dict(match.groupdict())
            row["_sourceRowIndex"] = index
            row["_sourceText"] = line.strip()
            rows.append(row)
            break
    return _canonical_lines_from_rows(rows, warnings)


def _canonical_lines_from_rows(rows: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float | None, int | None]] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or not any(str(value).strip() for value in row.values()):
            continue
        quantity = _parse_decimal(_row_value(row, "quantity"))
        item_number = _clean_identifier(_row_value(row, "providedItemNumber"))
        upc = _clean_identifier(_row_value(row, "providedUpc"))
        description = _clean_text(_row_value(row, "description"))
        source_row_index = _parse_int(row.get("_sourceRowIndex") or row.get("_source_row_index"), None)
        if quantity is None or quantity <= 0:
            if item_number or upc or description:
                warnings.append(
                    {
                        "code": "skippedInvalidQuantity",
                        "message": "Skipped email body row with missing or non-positive quantity.",
                        "sourceRowIndex": source_row_index,
                    }
                )
            continue
        if not item_number and not upc and not description:
            continue
        identity = (item_number, upc, quantity, source_row_index)
        if identity in seen:
            continue
        seen.add(identity)
        line = {
            "lineNumber": len(lines) + 1,
            "sourceRowIndex": source_row_index,
            "providedItemNumber": item_number,
            "providedUpc": upc,
            "description": description,
            "quantity": quantity,
            "unit": _clean_text(_row_value(row, "unit")) or "EA",
            "unitPrice": _parse_decimal(_row_value(row, "unitPrice")),
            "raw": {
                "emailBodyRow": source_row_index or index,
                "sourceText": str(row.get("_sourceText") or ""),
                "values": dict(row),
            },
        }
        lines.append(line)
    return lines


def _row_value(row: dict[str, Any], canonical: str) -> str:
    aliases = {
        "providedItemNumber": [
            "providedItemNumber",
            "provided_item_number",
            "item_number",
            "item number",
            "item",
            "item no",
            "item #",
            "sku",
            "supplier code",
            "suppliercode",
            "vendor item",
            "product code",
            "Column 1",
        ],
        "providedUpc": [
            "providedUpc",
            "provided_upc",
            "upc",
            "upc #",
            "barcode",
            "bar code",
            "gtin",
            "Column 2",
        ],
        "quantity": ["quantity", "qty", "order qty", "order quantity", "qty ordered", "qtyordered", "Column 3"],
        "description": ["description", "item description", "product", "product description", "desc", "Column 4", "Column 5"],
        "unit": ["unit", "uom", "unit of measure"],
        "unitPrice": ["unitPrice", "unit_price", "price", "unit price"],
    }
    lookup = {_normalized_header(key): value for key, value in row.items()}
    for alias in aliases.get(canonical, [canonical]):
        value = lookup.get(_normalized_header(alias))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _customer_identification_plan(source: EmailBodySource, settings: dict[str, Any]) -> dict[str, Any]:
    body = _clean_text(source.body_text)
    signals = _customer_signals(source, settings)
    search_parts = [
        f"email sender: {source.sender}" if source.sender else "",
        f"email subject: {source.subject}" if source.subject else "",
    ]
    if signals.get("customerCode"):
        search_parts.append(f"customer code: {signals['customerCode']}")
    for label in ("shipToName", "deliveryLocation", "shipToAddress", "shipToCity", "shipToState", "shipToPostalCode", "storeNumber"):
        value = signals.get(label)
        if value:
            search_parts.append(f"{label}: {value}")
    location_snippets = signals.get("locationSnippets")
    if isinstance(location_snippets, list) and location_snippets:
        search_parts.append("location snippets:\n" + "\n".join(str(item) for item in location_snippets[:8]))
    max_body_chars = _parse_int(settings.get("customerSearchBodyMaxChars"), 8000) or 8000
    if body:
        search_parts.append("email body:\n" + body[:max_body_chars])
    return {
        "customerSearchText": "\n".join(dict.fromkeys(part for part in search_parts if part.strip()))[:12000],
        "signals": signals,
        "instructions": [
            "Customer code is the highest-priority signal when explicitly labeled.",
            "Ship-to and delivery location names or addresses are next-highest priority.",
            "Use the rest of the email only after those specific signals.",
        ],
    }


def _customer_signals(source: EmailBodySource, settings: dict[str, Any]) -> dict[str, Any]:
    text = "\n".join(part for part in [source.subject, source.sender, source.body_text] if part.strip())
    snippets = _location_snippets(text)
    city_state_zip = _city_state_zip(text)
    signals: dict[str, Any] = {
        "sender": source.sender,
        "subject": source.subject,
        "customerCode": _customer_code(text),
        "storeNumber": _store_number(text),
        "shipToName": _labeled_value(text, ["ship to", "ship-to", "shipto", "delivery to", "deliver to"]),
        "deliveryLocation": _labeled_value(text, ["delivery location", "location", "store location"]),
        "shipToAddress": _address_line(text),
        "shipToCity": city_state_zip.get("city", ""),
        "shipToState": city_state_zip.get("state", ""),
        "shipToPostalCode": city_state_zip.get("postalCode", ""),
        "emailBodyPreview": _clean_text(source.body_text)[:1500],
        "locationSnippets": snippets,
    }
    return {key: value for key, value in signals.items() if _has_value(value)}


def _location_snippets(text: str) -> list[str]:
    lines = [_clean_text(line) for line in text.splitlines()]
    snippets: list[str] = []
    for index, line in enumerate(lines):
        if not line:
            continue
        if re.search(r"\b(ship\s*to|ship-to|delivery|deliver\s*to|store|location|customer)\b", line, re.IGNORECASE):
            chunk = [value for value in lines[index : index + 5] if value]
            snippets.append(" | ".join(chunk))
        elif _ADDRESS_RE.search(line):
            chunk = [value for value in lines[max(0, index - 1) : index + 3] if value]
            snippets.append(" | ".join(chunk))
    return list(dict.fromkeys(snippets))[:10]


def _purchase_order(source: EmailBodySource) -> str:
    for text in (source.subject, source.body_text):
        value = _po_from_text(text)
        if value:
            return value
    return ""


def _po_from_text(text: str) -> str:
    patterns = [
        r"\bP\.?\s*O\.?\s*(?:#|Number|No\.?)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9_.-]{1,40})",
        r"\bPurchase\s+Order\s*(?:#|Number|No\.?)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9_.-]{1,40})",
        r"\bOrder\s*(?:#|Number|No\.?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9_.-]{1,40})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return match.group(1).strip(" .,:;")
    return ""


def _customer_code(text: str) -> str:
    match = re.search(
        r"\b(?:cust|cst|customer)\s*(?:code|id|#|number|no\.?)?\s*[:#-]\s*([A-Z0-9][A-Z0-9_.-]{2,30})",
        text or "",
        re.IGNORECASE,
    )
    return match.group(1).strip(" .,:;") if match else ""


def _store_number(text: str) -> str:
    match = re.search(r"\b(?:store|location)\s*(?:#|number|no\.?)\s*[:#-]?\s*([A-Z0-9_.-]{1,20})", text or "", re.IGNORECASE)
    return match.group(1).strip(" .,:;") if match else ""


def _labeled_value(text: str, labels: list[str]) -> str:
    for label in labels:
        pattern = rf"(?im)^\s*{re.escape(label)}\s*[:#-]\s*(.+?)\s*$"
        match = re.search(pattern, text or "")
        if match:
            return _clean_text(match.group(1))
    return ""


_ADDRESS_RE = re.compile(
    r"\b\d{2,6}\s+[A-Za-z0-9 .'-]+(?:ST|STREET|AVE|AVENUE|BLVD|BOULEVARD|RD|ROAD|DR|DRIVE|LN|LANE|WAY|HWY|PKWY|COURT|CT|CIR|CIRCLE)\b",
    re.IGNORECASE,
)


def _address_line(text: str) -> str:
    for line in text.splitlines():
        match = _ADDRESS_RE.search(line)
        if match:
            return _clean_text(match.group(0))
    return ""


def _city_state_zip(text: str) -> dict[str, str]:
    patterns = [
        r"\b(?P<city>[A-Za-z .'-]{2,40}),?\s+(?P<state>[A-Z]{2})\s+(?P<postalCode>\d{5}(?:-\d{4})?)\b",
        r"\bCity\s*[:#-]\s*(?P<city>[A-Za-z .'-]{2,40}).{0,40}?\bState\s*[:#-]\s*(?P<state>[A-Z]{2}).{0,40}?\b(?:Zip|Postal\s*Code)\s*[:#-]\s*(?P<postalCode>\d{5}(?:-\d{4})?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return {
                "city": _clean_text(match.group("city")),
                "state": str(match.group("state") or "").upper(),
                "postalCode": str(match.group("postalCode") or ""),
            }
    return {}


def _foundry_email_body_extraction(source: EmailBodySource, settings: dict[str, Any]) -> dict[str, Any]:
    from .customer_identification import FoundryCustomerAiJsonClient

    deployment = (
        settings.get("emailBodyOrderDeployment")
        or settings.get("emailBodyExtractionDeployment")
        or settings.get("foundryDeployment")
        or os.environ.get("AZURE_AI_FOUNDRY_EMAIL_BODY_ORDER_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_EMAIL_BODY_ORDER_DEPLOYMENT")
        or None
    )
    client = FoundryCustomerAiJsonClient(deployment=str(deployment) if deployment else None)
    response = client.complete_json(
        system_prompt=EMAIL_BODY_ORDER_SYSTEM_PROMPT,
        user_payload={
            "email": {
                "sender": source.sender,
                "recipients": source.recipients,
                "subject": source.subject,
                "bodyText": source.body_text[:12000],
                "bodyHtmlText": _html_to_text(source.body_html)[:4000],
            },
            "requiredOutput": "Extract order lines, purchase order, and customer identification context from this email body.",
        },
        schema=EMAIL_BODY_ORDER_SCHEMA,
        schema_name="email_body_order_extraction",
        temperature=0.0,
    )
    if not isinstance(response, dict):
        raise ValueError("Email body extraction model returned a non-object response.")
    return response


def _merge_ai_extraction(
    ai: dict[str, Any],
    deterministic: dict[str, Any],
    source: EmailBodySource,
    settings: dict[str, Any],
) -> dict[str, Any]:
    warnings = [dict(item) for item in _as_list(deterministic.get("warnings")) if isinstance(item, dict)]
    warnings.extend(dict(item) for item in _as_list(ai.get("warnings")) if isinstance(item, dict))
    ai_lines = _canonical_lines_from_ai(ai, warnings)
    customer_identification = _merge_customer_identification(
        _as_dict(ai.get("customerIdentification")),
        _as_dict(deterministic.get("customerIdentification")),
    )
    lines = ai_lines or list(deterministic.get("lines") or [])
    purchase_order = str(ai.get("purchaseOrder") or deterministic.get("purchaseOrder") or "")
    result = {
        **deterministic,
        "status": "ready" if lines else "needsReview",
        "purchaseOrder": purchase_order,
        "lineCount": len(lines),
        "lines": lines,
        "customerIdentification": customer_identification,
        "warnings": warnings,
        "errors": [] if lines else [{"code": "noOrderLines", "message": "No valid order lines were extracted from the email body."}],
        "requiresHumanReview": not bool(lines),
        "aiExtraction": _ai_extraction_metadata(settings, "foundryExtractionUsed" if ai_lines else "foundryCustomerContextUsed"),
    }
    return result


def _canonical_lines_from_ai(ai: dict[str, Any], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(_as_list(ai.get("lines")), start=1):
        if not isinstance(line, dict):
            continue
        rows.append(
            {
                "providedItemNumber": line.get("providedItemNumber", ""),
                "providedUpc": line.get("providedUpc", ""),
                "description": line.get("description", ""),
                "quantity": line.get("quantity", ""),
                "unit": line.get("unit", ""),
                "unitPrice": line.get("unitPrice", None),
                "_sourceRowIndex": index,
                "_sourceText": line.get("sourceText", ""),
            }
        )
    return _canonical_lines_from_rows(rows, warnings)


def _merge_customer_identification(ai: dict[str, Any], deterministic: dict[str, Any]) -> dict[str, Any]:
    ai_text = str(_pick(ai, "customerSearchText", "customer_search_text", default="") or "").strip()
    deterministic_text = str(_pick(deterministic, "customerSearchText", "customer_search_text", default="") or "").strip()
    signals = {
        **_as_dict(deterministic.get("signals")),
        **_as_dict(ai.get("signals")),
    }
    text = "\n".join(dict.fromkeys(part for part in [ai_text, deterministic_text] if part))
    return {
        **deterministic,
        **ai,
        "customerSearchText": text[:12000],
        "signals": {key: value for key, value in signals.items() if _has_value(value)},
    }


def _ai_enabled(settings: dict[str, Any]) -> bool:
    value = (
        settings.get("enableAiExtraction")
        if "enableAiExtraction" in settings
        else settings.get("useAiExtraction")
        if "useAiExtraction" in settings
        else settings.get("enableFoundryExtraction")
        if "enableFoundryExtraction" in settings
        else os.environ.get("ORDER_PROCESSOR_ENABLE_EMAIL_BODY_ORDER_AI", "")
    )
    return _bool_flag(value, default=False)


def _ai_extraction_metadata(settings: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "mode": "emailBodyOrderExtraction",
        "status": status,
        "recommendedDeployment": (
            settings.get("emailBodyOrderDeployment")
            or settings.get("emailBodyExtractionDeployment")
            or settings.get("foundryDeployment")
            or os.environ.get("AZURE_AI_FOUNDRY_EMAIL_BODY_ORDER_DEPLOYMENT", "")
            or os.environ.get("AZURE_OPENAI_EMAIL_BODY_ORDER_DEPLOYMENT", "")
        ),
        "systemPrompt": EMAIL_BODY_ORDER_SYSTEM_PROMPT,
    }


def _rows_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    rows = _pick(payload, "sourceRows", "source_rows", "rows", default=None)
    if rows is None:
        return None
    if isinstance(rows, list):
        return [dict(row) for row in rows if isinstance(row, dict)]
    return None


def _rows_from_line_pattern(text: str, line_pattern: str) -> list[dict[str, Any]]:
    regex = re.compile(line_pattern)
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        match = regex.search(line)
        if not match:
            continue
        row = dict(match.groupdict())
        row["_sourceRowIndex"] = index
        row["_sourceText"] = line.strip()
        rows.append(row)
    return rows


def _row_has_order_header(row: list[str]) -> bool:
    return _line_has_order_header(" ".join(str(value) for value in row))


def _line_has_order_header(line: str) -> bool:
    normalized = _normalized_header(line)
    has_quantity = any(value in normalized for value in ["quantity", "qty", "orderqty", "qtyordered"])
    has_item = any(value in normalized for value in ["item", "sku", "suppliercode", "vendoritem", "upc", "barcode", "gtin"])
    return has_quantity and has_item


class _HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []
        self._in_table = False
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "table":
            self._in_table = True
            self._current_table = []
        elif self._in_table and lowered == "tr":
            self._current_row = []
        elif self._in_table and lowered in {"td", "th"}:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if self._in_table and lowered in {"td", "th"}:
            self._current_row.append(_clean_text("".join(self._current_cell)))
            self._current_cell = []
            self._in_cell = False
        elif self._in_table and lowered == "tr":
            if self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = []
        elif self._in_table and lowered == "table":
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = []
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|table)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return _clean_text(text.replace("\xa0", " "))


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\ufeff", " ")).strip()


def _clean_identifier(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\s-]+", "", text)
    return text.strip(" ,;:")


def _decode_bytes(value: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _strip_bom(value: str) -> str:
    return value.lstrip("\ufeff")


def _normalized_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _parse_decimal(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else None


def _parse_int(value: Any, default: int | None = 0) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _pick(payload: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return default


def _bool_flag(value: Any, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}
    return bool(value)
