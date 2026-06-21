from __future__ import annotations

import csv
from dataclasses import dataclass, field
import hashlib
import io
import json
import os
from typing import Any, Protocol

from .customer_identification import AzureOpenAIEmbeddingClient, TextEmbeddingClient, normalize_domain, normalize_identifier
from .item_validation import normalize_item_token
from .models import CustomerAlias, CustomerProfile, ItemRecord, utc_now


CUSTOMER_IMPORT_TYPE = "customers"
ITEM_IMPORT_TYPE = "items"
DEFAULT_CUSTOMER_REFRESH_INTERVAL_DAYS = 1
DEFAULT_ITEM_REFRESH_INTERVAL_DAYS = 7


def stable_id(*parts: str) -> str:
    joined = "|".join(part.strip().lower() for part in parts if part is not None)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def split_multi(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    normalized = str(value).replace("\r", "\n")
    parts: list[str] = []
    for chunk in normalized.split("\n"):
        for semicolon_part in chunk.split(";"):
            for comma_part in semicolon_part.split(","):
                item = comma_part.strip()
                if item:
                    parts.append(item)
    return parts


def _pick_field(row: dict[str, Any], field_map: dict[str, str], name: str, default: str = "") -> str:
    source = field_map.get(name, name)
    value = row.get(source, default)
    return "" if value is None else str(value).strip()


@dataclass(frozen=True, slots=True)
class SourceArchiveResult:
    import_run_id: str
    blob_url: str
    row_count: int
    checksum: str
    archived_at: str


class SourceRowArchive(Protocol):
    def archive_rows(
        self,
        tenant_id: str,
        import_type: str,
        rows: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> SourceArchiveResult:
        """Persist original import rows and return an audit reference."""


class InMemorySourceRowArchive:
    def __init__(self) -> None:
        self.objects: dict[str, str] = {}

    def archive_rows(
        self,
        tenant_id: str,
        import_type: str,
        rows: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> SourceArchiveResult:
        content = rows_to_jsonl(rows)
        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
        import_run_id = stable_id(tenant_id, import_type, checksum, metadata.get("sourceName", ""), utc_now())
        blob_url = f"memory://source-rows/{tenant_id}/{import_type}/{import_run_id}.jsonl"
        self.objects[blob_url] = content
        return SourceArchiveResult(
            import_run_id=import_run_id,
            blob_url=blob_url,
            row_count=len(rows),
            checksum=checksum,
            archived_at=utc_now(),
        )


class AzureBlobSourceRowArchive:
    def __init__(
        self,
        account_url: str | None = None,
        container_name: str | None = None,
    ) -> None:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient, ContentSettings
        except ModuleNotFoundError as exc:  # pragma: no cover - deployed dependency.
            raise RuntimeError("azure-storage-blob and azure-identity are required for blob source archiving.") from exc

        account_url = account_url or os.environ.get("SOURCE_ROWS_STORAGE_ACCOUNT_URL", "")
        if not account_url:
            account_name = os.environ.get("STORAGE_ACCOUNT_NAME", "")
            if account_name:
                account_url = f"https://{account_name}.blob.core.windows.net"
        if not account_url:
            raise ValueError("SOURCE_ROWS_STORAGE_ACCOUNT_URL or STORAGE_ACCOUNT_NAME is required.")

        self.container_name = container_name or os.environ.get("SOURCE_ROWS_CONTAINER_NAME", "source-rows")
        self.blob_service_client = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        self.content_settings = ContentSettings(content_type="application/x-ndjson")

    def archive_rows(
        self,
        tenant_id: str,
        import_type: str,
        rows: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> SourceArchiveResult:
        content = rows_to_jsonl(rows)
        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
        import_run_id = stable_id(tenant_id, import_type, checksum, metadata.get("sourceName", ""), utc_now())
        blob_name = f"{tenant_id}/{import_type}/{import_run_id}/source-rows.jsonl"
        container = self.blob_service_client.get_container_client(self.container_name)
        blob_client = container.get_blob_client(blob_name)
        blob_client.upload_blob(content, overwrite=True, content_settings=self.content_settings)
        return SourceArchiveResult(
            import_run_id=import_run_id,
            blob_url=blob_client.url,
            row_count=len(rows),
            checksum=checksum,
            archived_at=utc_now(),
        )


def source_archive_from_environment() -> SourceRowArchive:
    backend = os.environ.get("ORDER_PROCESSOR_SOURCE_ARCHIVE_BACKEND", "memory").strip().lower()
    if backend == "blob":
        return AzureBlobSourceRowArchive()
    return InMemorySourceRowArchive()


def rows_to_jsonl(rows: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows) + ("\n" if rows else "")


@dataclass(frozen=True, slots=True)
class ImportProfile:
    import_type: str
    parser_module: str
    field_map: dict[str, str] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    source_name: str = ""
    refresh_interval_days: int | None = None


@dataclass(frozen=True, slots=True)
class ImportSchedule:
    import_type: str
    interval_days: int
    cadence: str
    next_due_at: str
    source: str


@dataclass(frozen=True, slots=True)
class ParsedImportRows:
    rows: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    parser_module: str


@dataclass(frozen=True, slots=True)
class RowValidationResult:
    valid_rows: list[tuple[int, dict[str, Any]]]
    errors: list[dict[str, Any]]


def import_profile_from_payload(
    payload: dict[str, Any],
    import_type: str,
    default_parser: str,
) -> ImportProfile:
    profile_payload = payload.get("importProfile") or payload.get("processorProfile") or {}
    settings = dict(profile_payload.get("settings") or payload.get("settings") or {})
    field_map = dict(profile_payload.get("fieldMap") or payload.get("fieldMap") or payload.get("field_map") or {})
    parser_module = (
        payload.get("parserModule")
        or payload.get("parser_module")
        or profile_payload.get("parserModule")
        or profile_payload.get("parser_module")
        or infer_parser_module(payload, default_parser)
    )
    refresh_interval_days = (
        payload.get("refreshIntervalDays")
        or payload.get("refresh_interval_days")
        or profile_payload.get("refreshIntervalDays")
        or settings.get("refreshIntervalDays")
    )
    return ImportProfile(
        import_type=import_type,
        parser_module=str(parser_module),
        field_map=field_map,
        settings=settings,
        source_name=str(payload.get("sourceName") or payload.get("source_name") or profile_payload.get("sourceName") or ""),
        refresh_interval_days=int(refresh_interval_days) if refresh_interval_days not in {None, ""} else None,
    )


def infer_parser_module(payload: dict[str, Any], default_parser: str) -> str:
    if "rows" in payload:
        return "rows"
    content_type = str(payload.get("contentType") or payload.get("content_type") or "").lower()
    source_name = str(payload.get("sourceName") or payload.get("source_name") or "").lower()
    if "jsonl" in content_type or source_name.endswith(".jsonl"):
        return "jsonl"
    if "json" in content_type or source_name.endswith(".json"):
        return "json"
    if "csv" in content_type or source_name.endswith(".csv"):
        return "csv"
    return default_parser


def parse_import_rows(payload: dict[str, Any], profile: ImportProfile) -> ParsedImportRows:
    parser_name = profile.parser_module.strip()
    normalized_name = parser_name.lower()
    if normalized_name in {"rows", "passthrough"}:
        rows = payload.get("rows", [])
        if not isinstance(rows, list):
            return ParsedImportRows([], [{"code": "invalidRows", "message": "rows must be an array"}], parser_name)
        valid_rows = [row for row in rows if isinstance(row, dict)]
        errors = [
            {"code": "invalidRow", "message": "row must be an object", "rowIndex": index}
            for index, row in enumerate(rows)
            if not isinstance(row, dict)
        ]
        return ParsedImportRows(valid_rows, errors, parser_name)

    if normalized_name in {"csv", "genericcustomercsv", "genericitemcsv"}:
        return _parse_csv(payload, profile)

    if normalized_name in {"json", "genericcustomerjson", "genericitemjson"}:
        return _parse_json(payload, parser_name)

    if normalized_name == "jsonl":
        return _parse_jsonl(payload, parser_name)

    return ParsedImportRows([], [{"code": "unknownParser", "message": f"Unknown parser module: {parser_name}"}], parser_name)


def _source_content(payload: dict[str, Any]) -> str:
    content = payload.get("sourceContent", payload.get("source_content", ""))
    return "" if content is None else str(content)


def _parse_csv(payload: dict[str, Any], profile: ImportProfile) -> ParsedImportRows:
    content = _source_content(payload)
    if not content.strip():
        return ParsedImportRows([], [{"code": "emptySourceContent", "message": "CSV sourceContent is empty"}], profile.parser_module)

    delimiter = str(profile.settings.get("delimiter") or payload.get("delimiter") or ",")
    try:
        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        if not reader.fieldnames:
            return ParsedImportRows([], [{"code": "missingHeader", "message": "CSV source is missing a header row"}], profile.parser_module)
        rows = [dict(row) for row in reader]
    except csv.Error as exc:
        return ParsedImportRows([], [{"code": "csvParseError", "message": str(exc)}], profile.parser_module)
    return ParsedImportRows(rows, [], profile.parser_module)


def _parse_json(payload: dict[str, Any], parser_module: str) -> ParsedImportRows:
    content = _source_content(payload)
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        return ParsedImportRows([], [{"code": "jsonParseError", "message": str(exc)}], parser_module)
    rows = value.get("rows") if isinstance(value, dict) else value
    if not isinstance(rows, list):
        return ParsedImportRows([], [{"code": "invalidJsonRows", "message": "JSON source must be an array or object with rows array"}], parser_module)
    return ParsedImportRows(
        [row for row in rows if isinstance(row, dict)],
        [
            {"code": "invalidRow", "message": "JSON row must be an object", "rowIndex": index}
            for index, row in enumerate(rows)
            if not isinstance(row, dict)
        ],
        parser_module,
    )


def _parse_jsonl(payload: dict[str, Any], parser_module: str) -> ParsedImportRows:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, line in enumerate(_source_content(payload).splitlines()):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append({"code": "jsonlParseError", "message": str(exc), "rowIndex": index})
            continue
        if not isinstance(value, dict):
            errors.append({"code": "invalidRow", "message": "JSONL row must be an object", "rowIndex": index})
            continue
        rows.append(value)
    return ParsedImportRows(rows, errors, parser_module)


def refresh_schedule_for_import(
    import_type: str,
    profile: ImportProfile,
    payload: dict[str, Any],
    imported_at: str,
) -> ImportSchedule:
    customer_config = dict(payload.get("customerConfig") or payload.get("customer_config") or {})
    if profile.refresh_interval_days:
        interval_days = profile.refresh_interval_days
        source = "importProfile"
    elif import_type == ITEM_IMPORT_TYPE and customer_config.get("itemRefreshIntervalDays"):
        interval_days = int(customer_config["itemRefreshIntervalDays"])
        source = "customerConfig"
    elif import_type == CUSTOMER_IMPORT_TYPE and customer_config.get("customerRefreshIntervalDays"):
        interval_days = int(customer_config["customerRefreshIntervalDays"])
        source = "customerConfig"
    elif import_type == CUSTOMER_IMPORT_TYPE:
        interval_days = DEFAULT_CUSTOMER_REFRESH_INTERVAL_DAYS
        source = "default"
    else:
        interval_days = DEFAULT_ITEM_REFRESH_INTERVAL_DAYS
        source = "default"
    return ImportSchedule(
        import_type=import_type,
        interval_days=interval_days,
        cadence=f"every {interval_days} day" + ("" if interval_days == 1 else "s"),
        next_due_at=_add_days_iso(imported_at, interval_days),
        source=source,
    )


def _add_days_iso(value: str, days: int) -> str:
    from datetime import datetime, timedelta

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.fromisoformat(utc_now())
    return (parsed + timedelta(days=days)).isoformat()


def validate_customer_rows(rows: list[dict[str, Any]], field_map: dict[str, str]) -> RowValidationResult:
    valid: list[tuple[int, dict[str, Any]]] = []
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        customer_code = _pick_field(row, field_map, "customer_code")
        name = _pick_field(row, field_map, "name")
        if not customer_code and not name:
            errors.append(
                {
                    "code": "missingCustomerIdentifier",
                    "message": "Customer row requires customer_code or name.",
                    "rowIndex": index,
                    "row": row,
                }
            )
            continue
        valid.append((index, row))
    return RowValidationResult(valid, errors)


def validate_item_rows(rows: list[dict[str, Any]], field_map: dict[str, str]) -> RowValidationResult:
    valid: list[tuple[int, dict[str, Any]]] = []
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        internal_item_number = _pick_field(row, field_map, "internal_item_number")
        upc = _pick_field(row, field_map, "upc")
        customer_item_numbers = _pick_field(row, field_map, "customer_item_numbers")
        if not internal_item_number and not upc and not customer_item_numbers:
            errors.append(
                {
                    "code": "missingItemIdentifier",
                    "message": "Item row requires internal_item_number, upc, or customer_item_numbers.",
                    "rowIndex": index,
                    "row": row,
                }
            )
            continue
        valid.append((index, row))
    return RowValidationResult(valid, errors)


def normalize_customer_row(
    tenant_id: str,
    row: dict[str, Any],
    field_map: dict[str, str],
    import_metadata: dict[str, Any] | None = None,
) -> CustomerProfile:
    customer_code = _pick_field(row, field_map, "customer_code")
    name = _pick_field(row, field_map, "name")
    customer_id = stable_id(tenant_id, customer_code or name)
    sender_domains = [normalize_domain(value) for value in split_multi(_pick_field(row, field_map, "sender_domains"))]
    aliases = split_multi(_pick_field(row, field_map, "aliases"))
    known_subject_patterns = split_multi(_pick_field(row, field_map, "known_subject_patterns"))
    metadata = dict(import_metadata or {})

    return CustomerProfile(
        id=customer_id,
        tenant_id=tenant_id,
        customer_code=customer_code,
        name=name,
        route_number=_pick_field(row, field_map, "route_number"),
        csr_email=_pick_field(row, field_map, "csr_email"),
        csr_folder=_pick_field(row, field_map, "csr_folder"),
        store_number=_pick_field(row, field_map, "store_number"),
        sender_domains=[domain for domain in sender_domains if domain],
        aliases=aliases,
        known_subject_patterns=known_subject_patterns,
        source_name=str(metadata.get("sourceName", "")),
        source_rows_blob_url=str(metadata.get("sourceRowsBlobUrl", "")),
        last_imported_at=metadata.get("importedAt"),
        raw_source={"row": row, **metadata} if metadata else row,
    )


def normalize_customer_alias_rows(
    tenant_id: str,
    customer: CustomerProfile,
    row: dict[str, Any],
    field_map: dict[str, str],
    import_metadata: dict[str, Any] | None = None,
) -> list[CustomerAlias]:
    aliases: list[CustomerAlias] = []
    alias_specs = [
        ("customerCode", [customer.customer_code, *split_multi(_pick_field(row, field_map, "alias_customer_codes"))]),
        ("storeNumber", [customer.store_number, *split_multi(_pick_field(row, field_map, "alias_store_numbers"))]),
        ("routeNumber", [customer.route_number, *split_multi(_pick_field(row, field_map, "alias_route_numbers"))]),
        (
            "senderDomain",
            [
                *customer.sender_domains,
                *[normalize_domain(value) for value in split_multi(_pick_field(row, field_map, "alias_sender_domains"))],
            ],
        ),
        (
            "knownSubjectPattern",
            [
                *customer.known_subject_patterns,
                *split_multi(_pick_field(row, field_map, "alias_subject_patterns")),
            ],
        ),
    ]
    for alias_type, values in alias_specs:
        for value in values:
            normalized = _normalize_alias_value(alias_type, value)
            if not normalized:
                continue
            aliases.append(
                CustomerAlias(
                    id=stable_id(tenant_id, customer.id, alias_type, normalized),
                    tenant_id=tenant_id,
                    customer_id=customer.id,
                    alias_type=alias_type,
                    value=str(value).strip(),
                    normalized_value=normalized,
                    source=str((import_metadata or {}).get("sourceName", "")),
                    raw_source={"row": row, **(import_metadata or {})},
                )
            )
    return aliases


def _normalize_alias_value(alias_type: str, value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if alias_type == "senderDomain":
        return normalize_domain(text)
    if alias_type == "knownSubjectPattern":
        return text
    return normalize_identifier(text)


def normalize_item_row(
    tenant_id: str,
    customer_id: str,
    row: dict[str, Any],
    field_map: dict[str, str],
    import_metadata: dict[str, Any] | None = None,
) -> ItemRecord:
    internal_item_number = _pick_field(row, field_map, "internal_item_number")
    upc = _pick_field(row, field_map, "upc")
    fallback_item_number = normalize_item_token(split_multi(_pick_field(row, field_map, "customer_item_numbers"))[0]) if _pick_field(row, field_map, "customer_item_numbers") else ""
    canonical_item_number = internal_item_number or upc or fallback_item_number
    customer_item_numbers = [
        normalize_item_token(value)
        for value in split_multi(_pick_field(row, field_map, "customer_item_numbers"))
        if value.strip()
    ]
    aliases = split_multi(_pick_field(row, field_map, "aliases"))
    metadata = dict(import_metadata or {})

    return ItemRecord(
        id=stable_id(tenant_id, customer_id, canonical_item_number, upc),
        tenant_id=tenant_id,
        customer_id=customer_id,
        internal_item_number=canonical_item_number,
        description=_pick_field(row, field_map, "description"),
        upc=upc,
        customer_item_numbers=customer_item_numbers,
        aliases=aliases,
        source_name=str(metadata.get("sourceName", "")),
        source_rows_blob_url=str(metadata.get("sourceRowsBlobUrl", "")),
        last_imported_at=metadata.get("importedAt"),
        raw_source={"row": row, **metadata} if metadata else row,
    )


def import_embedding_client_from_environment() -> TextEmbeddingClient | None:
    enabled = os.environ.get("ORDER_PROCESSOR_ENABLE_IMPORT_EMBEDDINGS", "").strip().lower()
    if enabled not in {"1", "true", "yes"}:
        return None
    return AzureOpenAIEmbeddingClient()


def apply_customer_embedding(customer: CustomerProfile, embedding_client: TextEmbeddingClient | None) -> CustomerProfile:
    if embedding_client is None:
        return customer
    text = " ".join(
        [
            customer.customer_code,
            customer.name,
            customer.store_number,
            customer.route_number,
            " ".join(customer.sender_domains),
            " ".join(customer.aliases),
        ]
    )
    customer.embedding[:] = embedding_client.embed(text)
    return customer


def apply_item_embedding(item: ItemRecord, embedding_client: TextEmbeddingClient | None) -> ItemRecord:
    if embedding_client is None:
        return item
    text = " ".join(
        [
            item.internal_item_number,
            item.description,
            item.upc,
            " ".join(item.customer_item_numbers),
            " ".join(item.aliases),
        ]
    )
    item.embedding[:] = embedding_client.embed(text)
    return item
