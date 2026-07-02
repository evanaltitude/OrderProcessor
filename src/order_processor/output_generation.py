from __future__ import annotations

import csv
from dataclasses import dataclass, field
import hashlib
import io
import json
import os
import re
from string import Formatter
from typing import Any, Protocol
from uuid import uuid4
import zipfile
from xml.sax.saxutils import escape

from .imports import stable_id
from .models import MatchStatus, OrderRun, OutputProfile, to_dict, utc_now


DEFAULT_LINE_FIELDS = [
    "po_number",
    "order_number",
    "line_number",
    "quantity",
    "provided_item_number",
    "provided_upc",
    "description",
    "matched_internal_item_number",
    "validation_status",
    "validation_confidence",
]


@dataclass(frozen=True, slots=True)
class OutputArtifactContent:
    artifact_type: str
    file_name: str
    content_type: str
    content: bytes
    output_profile_id: str | None = None
    output_profile_name: str = ""
    destination: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class OutputArtifactStore(Protocol):
    def store_artifact(
        self,
        tenant_id: str,
        customer_id: str,
        order_run_id: str,
        artifact: OutputArtifactContent,
    ) -> dict[str, Any]:
        """Persist an output artifact and return the orderRun reference document."""


class InMemoryOutputArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def store_artifact(
        self,
        tenant_id: str,
        customer_id: str,
        order_run_id: str,
        artifact: OutputArtifactContent,
    ) -> dict[str, Any]:
        checksum = hashlib.sha256(artifact.content).hexdigest()
        artifact_id = stable_id(tenant_id, customer_id, order_run_id, artifact.artifact_type, artifact.file_name, checksum)
        blob_url = f"memory://order-artifacts/{tenant_id}/{customer_id or '_unassigned'}/{order_run_id}/{artifact_id}/{artifact.file_name}"
        self.objects[blob_url] = artifact.content
        return _artifact_reference(artifact_id, artifact, blob_url, checksum)


class AzureBlobOutputArtifactStore:
    def __init__(
        self,
        account_url: str | None = None,
        container_name: str | None = None,
    ) -> None:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient, ContentSettings
        except ModuleNotFoundError as exc:  # pragma: no cover - deployed dependency boundary.
            raise RuntimeError("azure-storage-blob and azure-identity are required for output artifact storage.") from exc

        account_url = (
            account_url
            or os.environ.get("ORDER_ARTIFACTS_STORAGE_ACCOUNT_URL", "")
            or os.environ.get("BLOB_SERVICE_ENDPOINT", "")
        )
        if not account_url:
            account_name = os.environ.get("STORAGE_ACCOUNT_NAME", "")
            if account_name:
                account_url = f"https://{account_name}.blob.core.windows.net"
        if not account_url:
            raise ValueError("ORDER_ARTIFACTS_STORAGE_ACCOUNT_URL, BLOB_SERVICE_ENDPOINT, or STORAGE_ACCOUNT_NAME is required.")

        self.container_name = container_name or os.environ.get("ORDER_ARTIFACTS_CONTAINER_NAME", "order-artifacts")
        self.blob_service_client = BlobServiceClient(account_url, credential=DefaultAzureCredential())

    def store_artifact(
        self,
        tenant_id: str,
        customer_id: str,
        order_run_id: str,
        artifact: OutputArtifactContent,
    ) -> dict[str, Any]:
        checksum = hashlib.sha256(artifact.content).hexdigest()
        artifact_id = stable_id(tenant_id, customer_id, order_run_id, artifact.artifact_type, artifact.file_name, checksum)
        blob_name = f"{tenant_id}/{customer_id or '_unassigned'}/{order_run_id}/{artifact_id}/{artifact.file_name}"
        container = self.blob_service_client.get_container_client(self.container_name)
        blob_client = container.get_blob_client(blob_name)
        try:
            from azure.storage.blob import ContentSettings
        except ModuleNotFoundError as exc:  # pragma: no cover - deployed dependency boundary.
            raise RuntimeError("azure-storage-blob is required for output artifact storage.") from exc
        blob_client.upload_blob(
            artifact.content,
            overwrite=True,
            content_settings=ContentSettings(content_type=artifact.content_type),
        )
        return _artifact_reference(artifact_id, artifact, blob_client.url, checksum)


def output_artifact_store_from_environment() -> OutputArtifactStore:
    backend = (
        os.environ.get("ORDER_PROCESSOR_OUTPUT_ARTIFACT_BACKEND", "")
        or os.environ.get("ORDER_PROCESSOR_OUTPUT_ARCHIVE_BACKEND", "")
        or "memory"
    ).strip().lower()
    if backend == "blob":
        return AzureBlobOutputArtifactStore()
    return InMemoryOutputArtifactStore()


def generate_order_output_artifacts(
    order: OrderRun,
    output_profiles: list[OutputProfile],
    artifact_store: OutputArtifactStore,
) -> list[dict[str, Any]]:
    artifacts = [_universal_order_artifact(order)]
    if output_profiles:
        for profile in output_profiles:
            artifacts.extend(_artifacts_for_profile(order, profile))
    else:
        artifacts.append(_line_csv_artifact(order, None))

    stored: list[dict[str, Any]] = []
    for artifact in artifacts:
        stored.append(
            artifact_store.store_artifact(
                tenant_id=order.tenant_id,
                customer_id=order.customer_id or "_unassigned",
                order_run_id=order.id,
                artifact=artifact,
            )
        )
    return stored


def order_to_json(order: OrderRun) -> str:
    return json.dumps(to_dict(order), indent=2, sort_keys=True)


def order_to_line_csv(order: OrderRun, settings: dict[str, Any] | None = None) -> str:
    settings = settings or {}
    fields = list(settings.get("fields") or settings.get("columns") or DEFAULT_LINE_FIELDS)
    delimiter = str(settings.get("delimiter") or ",")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, delimiter=delimiter, lineterminator="\n")
    if bool(settings.get("includeHeader", settings.get("include_header", True))):
        writer.writeheader()
    for line in order.lines:
        row = _line_output_row(order, line)
        writer.writerow({field: row.get(field, "") for field in fields})
    return buffer.getvalue()


def order_to_text(order: OrderRun, settings: dict[str, Any] | None = None) -> str:
    settings = settings or {}
    template = str(settings.get("template") or "")
    if template:
        lines = [_render_template(template, order, line) for line in order.lines]
        header = str(settings.get("header", ""))
        footer = str(settings.get("footer", ""))
        parts = [part for part in [header, *lines, footer] if part]
        return "\n".join(parts) + ("\n" if parts else "")

    output = io.StringIO()
    output.write(f"PO: {order.po_number or order.order_number or order.id}\n")
    for line in order.lines:
        output.write(
            f"{line.line_number}: {line.quantity or ''} "
            f"{line.matched_internal_item_number or line.provided_item_number or line.provided_upc} "
            f"{line.description}\n"
        )
    return output.getvalue()


def order_to_api_payload(order: OrderRun, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    body_mode = str(settings.get("bodyMode") or settings.get("body_mode") or "universalOrder").lower()
    if body_mode == "lines":
        body: Any = [_line_output_row(order, line) for line in order.lines]
    elif body_mode == "summary":
        body = {
            "orderRunId": order.id,
            "tenantId": order.tenant_id,
            "customerId": order.customer_id,
            "poNumber": order.po_number,
            "orderNumber": order.order_number,
            "lineCount": len(order.lines),
            "status": order.status,
        }
    else:
        body = to_dict(order)

    return {
        "method": str(settings.get("method") or "POST").upper(),
        "url": settings.get("url", ""),
        "headers": dict(settings.get("headers") or {}),
        "body": body,
    }


def order_to_xlsx_bytes(order: OrderRun, settings: dict[str, Any] | None = None) -> bytes:
    rows = list(csv.DictReader(io.StringIO(order_to_line_csv(order, settings))))
    headers = rows[0].keys() if rows else list(settings.get("fields", DEFAULT_LINE_FIELDS) if settings else DEFAULT_LINE_FIELDS)

    sheet_rows = [_xlsx_row(1, list(headers))]
    for index, row in enumerate(rows, start=2):
        sheet_rows.append(_xlsx_row(index, [row.get(header, "") for header in headers]))

    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        + "".join(sheet_rows)
        + "</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Order Lines" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return output.getvalue()


def _artifacts_for_profile(order: OrderRun, profile: OutputProfile) -> list[OutputArtifactContent]:
    output_type = _normalize_output_type(profile.output_type)
    settings = dict(profile.settings or {})
    if output_type in {"csv", "linecsv"}:
        return [_line_csv_artifact(order, profile)]
    if output_type in {"xlsx", "linexlsx"}:
        return [_line_xlsx_artifact(order, profile)]
    if output_type in {"text", "txt"}:
        return [_text_artifact(order, profile)]
    if output_type in {"api", "apipayload"}:
        return [_api_payload_artifact(order, profile)]
    if output_type in {"json", "universalorderjson"}:
        return [_universal_order_artifact(order, profile)]
    if output_type == "multi":
        artifacts: list[OutputArtifactContent] = []
        for nested_type in settings.get("formats", settings.get("outputTypes", [])):
            nested = OutputProfile(
                id=f"{profile.id}-{nested_type}",
                tenant_id=profile.tenant_id,
                customer_id=profile.customer_id,
                name=f"{profile.name} {nested_type}",
                output_type=str(nested_type),
                destination=profile.destination,
                settings=settings,
            )
            artifacts.extend(_artifacts_for_profile(order, nested))
        return artifacts
    raise ValueError(f"Unsupported output profile type: {profile.output_type}")


def _universal_order_artifact(order: OrderRun, profile: OutputProfile | None = None) -> OutputArtifactContent:
    settings = dict(profile.settings if profile else {})
    return OutputArtifactContent(
        artifact_type="universalOrderJson",
        file_name=_file_name(order, settings, "universal-order.json"),
        content_type="application/json",
        content=order_to_json(order).encode("utf-8"),
        output_profile_id=profile.id if profile else None,
        output_profile_name=profile.name if profile else "Universal order JSON",
        destination=dict(profile.destination if profile else {}),
        metadata={"format": "json", "adapter": "universalOrder"},
    )


def _line_csv_artifact(order: OrderRun, profile: OutputProfile | None = None) -> OutputArtifactContent:
    settings = dict(profile.settings if profile else {})
    return OutputArtifactContent(
        artifact_type="lineCsv",
        file_name=_file_name(order, settings, "order-lines.csv"),
        content_type="text/csv",
        content=order_to_line_csv(order, settings).encode(str(settings.get("encoding") or "utf-8")),
        output_profile_id=profile.id if profile else None,
        output_profile_name=profile.name if profile else "Default line CSV",
        destination=dict(profile.destination if profile else {}),
        metadata={"format": "csv", "adapter": "lineCsv"},
    )


def _line_xlsx_artifact(order: OrderRun, profile: OutputProfile) -> OutputArtifactContent:
    return OutputArtifactContent(
        artifact_type="lineXlsx",
        file_name=_file_name(order, profile.settings, "order-lines.xlsx"),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        content=order_to_xlsx_bytes(order, profile.settings),
        output_profile_id=profile.id,
        output_profile_name=profile.name,
        destination=dict(profile.destination),
        metadata={"format": "xlsx", "adapter": "lineXlsx", "generator": "azureFunctionCode"},
    )


def _text_artifact(order: OrderRun, profile: OutputProfile) -> OutputArtifactContent:
    encoding = str(profile.settings.get("encoding") or "utf-8")
    return OutputArtifactContent(
        artifact_type="text",
        file_name=_file_name(order, profile.settings, "order.txt"),
        content_type="text/plain",
        content=order_to_text(order, profile.settings).encode(encoding),
        output_profile_id=profile.id,
        output_profile_name=profile.name,
        destination=dict(profile.destination),
        metadata={"format": "text", "adapter": "text"},
    )


def _api_payload_artifact(order: OrderRun, profile: OutputProfile) -> OutputArtifactContent:
    payload = order_to_api_payload(order, {**profile.settings, **{"url": profile.destination.get("url", profile.settings.get("url", ""))}})
    return OutputArtifactContent(
        artifact_type="apiPayload",
        file_name=_file_name(order, profile.settings, "api-payload.json"),
        content_type="application/json",
        content=json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8"),
        output_profile_id=profile.id,
        output_profile_name=profile.name,
        destination=dict(profile.destination),
        metadata={"format": "json", "adapter": "apiPayload", "deliveryStatus": "pendingExternalDelivery"},
    )


def _artifact_reference(
    artifact_id: str,
    artifact: OutputArtifactContent,
    blob_url: str,
    checksum: str,
) -> dict[str, Any]:
    return {
        "id": artifact_id,
        "type": artifact.artifact_type,
        "fileName": artifact.file_name,
        "contentType": artifact.content_type,
        "blobUrl": blob_url,
        "sizeBytes": len(artifact.content),
        "checksum": checksum,
        "generatedAt": utc_now(),
        "outputProfileId": artifact.output_profile_id,
        "outputProfileName": artifact.output_profile_name,
        "destination": artifact.destination,
        "metadata": artifact.metadata,
    }


def _line_output_row(order: OrderRun, line: Any) -> dict[str, Any]:
    status = line.validation_status.value if isinstance(line.validation_status, MatchStatus) else line.validation_status
    return {
        "po_number": order.po_number,
        "poNumber": order.po_number,
        "order_number": order.order_number,
        "orderNumber": order.order_number,
        "order_run_id": order.id,
        "orderRunId": order.id,
        "customer_id": order.customer_id or "",
        "customerId": order.customer_id or "",
        "line_number": line.line_number,
        "lineNumber": line.line_number,
        "quantity": line.quantity if line.quantity is not None else "",
        "provided_item_number": line.provided_item_number,
        "providedItemNumber": line.provided_item_number,
        "provided_upc": line.provided_upc,
        "providedUpc": line.provided_upc,
        "description": line.description,
        "matched_internal_item_number": line.matched_internal_item_number or "",
        "matchedInternalItemNumber": line.matched_internal_item_number or "",
        "validation_status": status,
        "validationStatus": status,
        "validation_confidence": line.validation_confidence,
        "validationConfidence": line.validation_confidence,
    }


def _file_name(order: OrderRun, settings: dict[str, Any], default_name: str) -> str:
    template = str(settings.get("fileNameTemplate") or settings.get("file_name_template") or "")
    if template:
        name = _render_order_template(template, order)
    else:
        prefix = _safe_file_part(order.customer_id or "customer")
        po = _safe_file_part(order.po_number or order.order_number or order.id)
        name = f"{prefix}-{po}-{default_name}"
    return _safe_file_name(name)


def _render_template(template: str, order: OrderRun, line: Any) -> str:
    row = _line_output_row(order, line)
    values = {**row, "po": order.po_number, "order": order.order_number}
    return _safe_format(template, values)


def _render_order_template(template: str, order: OrderRun) -> str:
    values = {
        "orderRunId": order.id,
        "customerId": order.customer_id or "",
        "poNumber": order.po_number,
        "orderNumber": order.order_number,
        "sourceType": order.source_type,
        "uuid": uuid4().hex[:8],
    }
    return _safe_format(template, values)


def _safe_format(template: str, values: dict[str, Any]) -> str:
    allowed = {field_name for _, field_name, _, _ in Formatter().parse(template) if field_name}
    return template.format(**{field: values.get(field, "") for field in allowed})


def _normalize_output_type(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "csv").lower())


def _safe_file_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-") or "value"


def _safe_file_name(value: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "-", value).strip().strip(".")
    return name or "order-output"


def _xlsx_row(row_index: int, values: list[str]) -> str:
    cells = []
    for index, value in enumerate(values, start=1):
        cells.append(
            f'<c r="{_xlsx_column(index)}{row_index}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
        )
    return f'<row r="{row_index}">{"".join(cells)}</row>'


def _xlsx_column(index: int) -> str:
    column = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        column = chr(ord("A") + remainder) + column
    return column
