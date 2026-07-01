from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
import os
import re
import time
from typing import Any
from urllib import error as urlerror, parse, request as urlrequest

from .customer_identification import (
    DEFAULT_CUSTOMER_CONFIDENCE_THRESHOLD,
    CustomerAiIdentifier,
    CustomerVectorSearch,
    customer_ai_identifier_from_environment,
    customer_vector_search_from_environment,
    identify_customer as identify_customer_from_email,
    normalize_domain,
    normalize_identifier,
)
from .customer_vector_store import customer_vector_store_manager_from_environment
from .data_model import GLOBAL_CUSTOMER_ID, keys_to_camel
from .email_triage import build_email_action_plan, evaluate_email_triage, find_customer_by_code, normalize_triage_phase
from .imports import legacy_item_record_id, normalize_customer_row, normalize_item_row, scoped_item_record_id, stable_id
from .imports import (
    CUSTOMER_IMPORT_TYPE,
    ITEM_IMPORT_TYPE,
    SourceRowArchive,
    TextEmbeddingClient,
    apply_customer_embedding,
    apply_item_embedding,
    import_embedding_client_from_environment,
    import_profile_from_payload,
    normalize_customer_alias_rows,
    parse_import_rows,
    refresh_schedule_for_import,
    source_archive_from_environment,
    validate_customer_rows,
    validate_item_rows,
)
from .item_validation import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_POSSIBLE_MATCH_THRESHOLD,
    validate_item as validate_item_line,
)
from .models import (
    AuditEvent,
    AuthConnectionStatus,
    CustomerIdentificationResult,
    CustomerAlias,
    ConsoleUser,
    CustomerProfile,
    CustomerUserAssignment,
    EmailAttachment,
    EmailMessage,
    ExceptionStatus,
    ExceptionTask,
    ItemRecord,
    ItemValidationResult,
    MailboxAccount,
    MatchStatus,
    MicrosoftAuthConnection,
    OrderLine,
    OrderRun,
    OutputProfile,
    ProcessingStatus,
    ProcessorProfile,
    RoutingDecision,
    RoutingOutcome,
    RoutingRule,
    Tenant,
    to_dict,
    utc_now,
)
from .microsoft_graph import (
    InMemorySecretStore,
    MicrosoftGraphError,
    build_authorization_url,
    client_credentials_access_token,
    config_from_environment,
    exchange_authorization_code,
    graph_delete,
    graph_get,
    graph_patch,
    graph_post,
    refresh_access_token,
    secret_name,
    secret_store_from_environment,
    sign_state,
    state_secret_from_environment,
    test_shared_mailbox_access,
    token_expiry,
    verify_state,
)
from .observability import (
    correlation_context,
    dashboard_observability_metrics,
    duration_ms,
    merge_observability,
    order_timeline,
)
from .order_processing import process_order_payload, validate_order_lines
from .output_generation import (
    OutputArtifactStore,
    generate_order_output_artifacts,
    output_artifact_store_from_environment,
)
from .routing import default_order_signal, rule_matches
from .email_body_processing import extract_email_body_order as _extract_email_body_order
from .google_document_ai import (
    DocumentAiClient,
    GoogleDocumentAiClient,
    extract_order_from_google_document_ai_response as _extract_google_document_ai_order,
)
from .spreadsheet_processing import (
    analyze_spreadsheet_layout,
    extract_order_lines as _extract_spreadsheet_order_lines,
    normalize_spreadsheet as _normalize_spreadsheet_payload,
)
from .storage import InMemoryRepository, repository_from_environment


BOOTSTRAP_CONSOLE_ADMIN_EMAIL = "connect@focuseautomate.com"
SYSTEM_TENANT_ID = "__system__"
PROCESSING_CATEGORY = "Processing"
ORDER_PROCESSING_CATEGORY = "Order Processing - Do Not Move"
PROCESSING_EXCEPTION_CATEGORY = "Processing Exception"
OUTLOOK_CATEGORY_COLORS = {
    PROCESSING_CATEGORY: "preset9",
    ORDER_PROCESSING_CATEGORY: "preset9",
    PROCESSING_EXCEPTION_CATEGORY: "preset0",
    "csrAction": "preset3",
    "csrReview": "preset7",
    "csrValidate": "preset8",
}
WEBHOOK_PROCESSOR_TYPES = {"webhook", "customwebhook", "powerautomatewebhook", "powerautomate"}
MICROSOFT_AI_COST_PROVIDER = "microsoft"
GOOGLE_DOCUMENT_AI_COST_PROVIDER = "googleDocumentAi"
DEFAULT_COST_CURRENCY = "USD"
AI_COST_PROJECT_TAG_KEY = "project"
ACTIVE_RECONCILE_DEFAULT_MINUTES = 15
CONSOLE_CUSTOMER_FIELDS = [
    "id",
    "tenantId",
    "customerCode",
    "name",
    "routeNumber",
    "csrName",
    "csrEmail",
    "csrFolder",
    "storeNumber",
    "address1",
    "city",
    "state",
    "postalCode",
    "phone",
    "website",
    "customerEmail",
    "senderDomains",
    "aliases",
    "knownSubjectPatterns",
    "sourceName",
    "sourceRowsBlobUrl",
    "lastImportedAt",
    "customFields",
    "rawSource",
]
CONSOLE_ITEM_FIELDS = [
    "id",
    "tenantId",
    "customerId",
    "internalItemNumber",
    "description",
    "upc",
    "altPartsCombined",
    "customerItemNumbers",
    "aliases",
    "sourceName",
    "sourceRowsBlobUrl",
    "lastImportedAt",
    "rawSource",
]
CONSOLE_EMAIL_FIELDS = [
    "id",
    "tenantId",
    "mailbox",
    "messageId",
    "subject",
    "sender",
    "receivedAt",
    "categories",
    "status",
    "mailboxAccountId",
    "customerId",
    "orderRunId",
    "correlationId",
    "routing",
    "source",
    "customerIdentification",
    "createdAt",
    "updatedAt",
]
CONSOLE_MONITOR_RECORD_FIELDS = [
    "id",
    "tenantId",
    "section",
    "emailMessageId",
    "orderRunId",
    "exceptionId",
    "pathway",
    "status",
    "sender",
    "recipient",
    "subject",
    "receivedAt",
    "updatedAt",
    "categorizedAs",
    "customerId",
    "customerCode",
    "customerName",
    "csr",
    "csrEmail",
    "actionTaken",
    "movedTo",
    "emailUrl",
    "poNumber",
    "orderNumber",
    "lineCount",
    "artifactCount",
    "type",
    "exception",
    "prompt",
    "lineNumber",
    "context",
    "resolutionActions",
    "createdAt",
]
CONSOLE_CUSTOMER_SEARCH_FIELDS = [
    "id",
    "customerCode",
    "name",
    "routeNumber",
    "csrName",
    "csrEmail",
    "csrFolder",
    "storeNumber",
    "city",
    "state",
    "postalCode",
    "phone",
    "website",
    "customerEmail",
]
CONSOLE_ITEM_SEARCH_FIELDS = [
    "id",
    "customerId",
    "internalItemNumber",
    "description",
    "upc",
    "sourceName",
]


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


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _bool_flag(value: Any, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}
    return bool(value)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _round_money(value: Any) -> float:
    amount = _as_float(value)
    return round(float(amount or 0.0), 6)


def _month_start(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return datetime(current.year, current.month, 1, tzinfo=UTC)


def _parse_date(value: Any, default: datetime) -> datetime:
    if not value:
        return default
    text = str(value).strip()
    try:
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            parsed = datetime.fromisoformat(f"{text}T00:00:00+00:00")
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return default
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _period_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    now = datetime.now(UTC)
    period = str(_pick(payload, "period", default="currentMonth") or "currentMonth").strip().lower()
    if period in {"last30", "last30days", "rolling30"}:
        default_start = now - timedelta(days=30)
    elif period in {"lastmonth", "previousmonth"}:
        this_month = _month_start(now)
        previous_month_end = this_month - timedelta(days=1)
        default_start = _month_start(previous_month_end)
        now = this_month
    else:
        default_start = _month_start(now)

    start = _parse_date(_pick(payload, "startDate", "start", "from", default=None), default_start)
    end = _parse_date(_pick(payload, "endDate", "end", "to", default=None), now)
    if end < start:
        start, end = end, start
    return {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "label": period or "custom",
    }


def _in_period(value: Any, period: dict[str, str]) -> bool:
    timestamp = _parse_date(value, datetime.min.replace(tzinfo=UTC))
    start = _parse_date(period["startDate"], datetime.min.replace(tzinfo=UTC))
    end = _parse_date(period["endDate"], datetime.max.replace(tzinfo=UTC))
    return start <= timestamp <= end


def _cost_project_tag_value(tenant_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", str(tenant_id or "customer").lower()).strip("-")
    return normalized[:64] or "customer"


def _processor_cost_type(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    aliases = {
        "customerid": "customerIdentification",
        "customeridentification": "customerIdentification",
        "foundrycustomerconsensus": "customerIdentification",
        "foundrycustomerdecider": "customerIdentification",
        "documentintelligence": "pdf",
        "azuredocumentintelligence": "pdf",
        "googledocumentai": "pdf",
        "powerautomatewebhook": "powerAutomateWebhook",
    }
    return aliases.get(normalized, str(value or "unknown").strip() or "unknown")


def _normalize_regex_pattern(value: Any) -> str:
    pattern = str(value or "").strip()
    if not pattern:
        return ""
    return re.sub(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>", r"(?P<\1>", pattern)


def _normalize_regex_list(value: Any) -> list[str]:
    return [pattern for pattern in (_normalize_regex_pattern(item) for item in _as_list(value)) if pattern]


def _regex_validation_error(field_name: str, patterns: list[str]) -> dict[str, Any] | None:
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            return {
                "error": "invalidRegex",
                "field": field_name,
                "pattern": pattern,
                "message": f"{field_name} contains an invalid regular expression: {exc}",
            }
    return None


ROUTING_FILTER_FIELD_ALIASES = {
    "sender": "sender",
    "from": "sender",
    "senderemail": "sender",
    "senderaddress": "sender",
    "recipient": "recipient",
    "recipients": "recipient",
    "to": "recipient",
    "toemail": "recipient",
    "toaddress": "recipient",
    "subject": "subject",
    "body": "body",
    "bodytext": "body",
    "emailbody": "body",
}
ROUTING_FILTER_OPERATOR_ALIASES = {
    "equal": "equals",
    "equals": "equals",
    "is": "equals",
    "contains": "contains",
    "contain": "contains",
    "startswith": "startsWith",
    "starts": "startsWith",
    "endswith": "endsWith",
    "ends": "endsWith",
}


def _normalize_routing_filter_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _routing_filter_logic(value: Any) -> str:
    normalized = _normalize_routing_filter_key(value)
    return "any" if normalized in {"any", "or"} else "all"


def _normalize_routing_filter_conditions(
    value: Any,
    *,
    strict: bool = False,
) -> tuple[list[dict[str, str]], dict[str, Any] | None]:
    conditions: list[dict[str, str]] = []
    for index, item in enumerate(_as_list(value)):
        if not isinstance(item, dict):
            if strict:
                return [], {
                    "error": "invalidRoutingFilterCondition",
                    "field": f"filterConditions[{index}]",
                    "message": "Filter conditions must be objects.",
                }
            continue
        raw_value = str(_pick(item, "value", "text", "matchValue", default="") or "").strip()
        if not raw_value:
            continue
        field = ROUTING_FILTER_FIELD_ALIASES.get(
            _normalize_routing_filter_key(_pick(item, "field", "source", default=""))
        )
        if not field:
            if strict:
                return [], {
                    "error": "invalidRoutingFilterField",
                    "field": f"filterConditions[{index}].field",
                    "message": "Filter field must be Sender, Recipient, Subject, or Body.",
                    "allowedValues": ["sender", "recipient", "subject", "body"],
                }
            continue
        operator = ROUTING_FILTER_OPERATOR_ALIASES.get(
            _normalize_routing_filter_key(_pick(item, "operator", "match", "comparison", default="contains"))
        )
        if not operator:
            if strict:
                return [], {
                    "error": "invalidRoutingFilterOperator",
                    "field": f"filterConditions[{index}].operator",
                    "message": "Filter operator must be Equal, Contains, Starts With, or Ends With.",
                    "allowedValues": ["equals", "contains", "startsWith", "endsWith"],
                }
            continue
        conditions.append({"field": field, "operator": operator, "value": raw_value})
    return conditions, None


def _routing_outcome_from_value(value: Any) -> RoutingOutcome | None:
    if isinstance(value, RoutingOutcome):
        return value
    normalized = str(value or "").strip()
    if not normalized:
        return RoutingOutcome.NEEDS_HUMAN_REVIEW
    try:
        return RoutingOutcome(normalized)
    except ValueError:
        return None


def _routing_priority_from_value(value: Any) -> int | None:
    if value is None or value == "":
        return 100
    try:
        priority = int(value)
    except (TypeError, ValueError):
        return None
    return priority if priority >= 1 else None


def _api_value(value: Any) -> Any:
    return keys_to_camel(to_dict(value))


def _compact_import_audit_details(result: dict[str, Any], record_fields: list[str]) -> dict[str, Any]:
    details = {key: value for key, value in result.items() if key not in record_fields}
    suppressed: list[dict[str, Any]] = []
    for field in record_fields:
        records = _as_list(result.get(field))
        if not records:
            continue
        sample_ids = [
            str(_pick(record, "id", default=""))
            for record in records[:10]
            if isinstance(record, dict) and _pick(record, "id", default="")
        ]
        suppressed.append({"field": field, "count": len(records), "sampleIds": sample_ids})

    if suppressed:
        details["suppressedRecordDetails"] = suppressed

    errors = _as_list(details.get("errors"))
    if len(errors) > 25:
        details["errors"] = errors[:25]
        details["truncatedErrorCount"] = len(errors) - 25
    return details


def _without_heavy_console_fields(document: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key not in {"embedding", "Embedding"}}


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _graph_email_address(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    email_address = value.get("emailAddress")
    if isinstance(email_address, dict):
        return str(email_address.get("address", "") or "").strip().lower()
    return str(value.get("address", "") or "").strip().lower()


def _graph_recipient_addresses(values: Any) -> list[str]:
    recipients: list[str] = []
    for value in _as_list(values):
        address = _graph_email_address(value)
        if address:
            recipients.append(address)
    return recipients


def _email_from_payload(payload: dict[str, Any]) -> EmailMessage:
    tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
    message_id = _pick(payload, "messageId", "message_id", default="")
    subject = _pick(payload, "subject", default="")
    sender = _pick(payload, "sender", "from", default="")
    email_id = _pick(
        payload,
        "id",
        "emailMessageId",
        "email_message_id",
        default=stable_id(tenant_id, message_id, sender, subject),
    )

    attachments = [
        EmailAttachment(
            name=_pick(item, "name", "fileName", "file_name", default=""),
            content_type=_pick(item, "contentType", "content_type", default=""),
            size=int(_pick(item, "size", default=0) or 0),
            blob_url=_pick(item, "blobUrl", "blob_url", default=""),
            content_id=_pick(item, "contentId", "content_id", default=""),
            is_inline=bool(_pick(item, "isInline", "is_inline", default=False)),
            source_url=_pick(item, "sourceUrl", "source_url", "downloadUrl", "download_url", default=""),
            metadata=dict(_pick(item, "metadata", default={}) or {}),
        )
        for item in _as_list(_pick(payload, "attachments", default=[]))
        if isinstance(item, dict)
    ]

    status = _pick(payload, "status", default=ProcessingStatus.RECEIVED)
    if not isinstance(status, ProcessingStatus):
        status = ProcessingStatus(status)

    return EmailMessage(
        id=email_id,
        tenant_id=tenant_id,
        mailbox=_pick(payload, "mailbox", default=""),
        message_id=message_id or email_id,
        subject=subject,
        sender=sender,
        received_at=_pick(payload, "receivedAt", "received_at", default=utc_now()),
        body_text=_pick(payload, "bodyText", "body_text", default=""),
        body_html=_pick(payload, "bodyHtml", "body_html", default=""),
        categories=list(_as_list(_pick(payload, "categories", default=[]))),
        attachments=attachments,
        status=status,
        mailbox_account_id=_pick(payload, "mailboxAccountId", "mailbox_account_id", default=None),
        customer_id=_pick(payload, "customerId", "customer_id", default=None),
        order_run_id=_pick(payload, "orderRunId", "order_run_id", default=None),
        correlation_id=_pick(payload, "correlationId", "correlation_id", default=None),
        source=dict(_pick(payload, "source", default={}) or {}),
    )


def _routing_rule_from_doc(doc: dict[str, Any]) -> RoutingRule:
    outcome = _routing_outcome_from_value(
        _pick(doc, "outcome", default=RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION)
    ) or RoutingOutcome.NEEDS_HUMAN_REVIEW
    priority = _routing_priority_from_value(_pick(doc, "priority", default=100)) or 100
    filter_conditions, _ = _normalize_routing_filter_conditions(
        _pick(doc, "filterConditions", "filter_conditions", default=[])
    )

    return RoutingRule(
        id=str(_pick(doc, "id")),
        tenant_id=_pick(doc, "tenantId", "tenant_id", default="default"),
        name=_pick(doc, "name", default=""),
        outcome=outcome,
        phase=_pick(doc, "phase", "triagePhase", "triage_phase", default="general"),
        priority=priority,
        enabled=bool(_pick(doc, "enabled", default=True)),
        customer_id=_pick(doc, "customerId", "customer_id", default=None),
        processor_profile_id=_pick(doc, "processorProfileId", "processor_profile_id", default=None),
        mailbox_account_ids=list(_as_list(_pick(doc, "mailboxAccountIds", "mailbox_account_ids", default=[]))),
        mailbox_addresses=list(_as_list(_pick(doc, "mailboxAddresses", "mailbox_addresses", default=[]))),
        filter_conditions=filter_conditions,
        filter_logic=_routing_filter_logic(_pick(doc, "filterLogic", "filter_logic", default="all")),
        sender_equals=list(_as_list(_pick(doc, "senderEquals", "sender_equals", default=[]))),
        sender_domains=list(_as_list(_pick(doc, "senderDomains", "sender_domains", default=[]))),
        subject_regex=list(_as_list(_pick(doc, "subjectRegex", "subject_regex", default=[]))),
        body_regex=list(_as_list(_pick(doc, "bodyRegex", "body_regex", default=[]))),
        known_webstore_patterns=list(
            _as_list(_pick(doc, "knownWebstorePatterns", "known_webstore_patterns", default=[]))
        ),
        prior_processed_subject_regex=list(
            _as_list(
                _pick(doc, "priorProcessedSubjectRegex", "prior_processed_subject_regex", default=[])
            )
        ),
        attachment_extensions=list(
            _as_list(_pick(doc, "attachmentExtensions", "attachment_extensions", default=[]))
        ),
        attachment_content_types=list(
            _as_list(_pick(doc, "attachmentContentTypes", "attachment_content_types", default=[]))
        ),
        attachment_name_regex=list(
            _as_list(_pick(doc, "attachmentNameRegex", "attachment_name_regex", default=[]))
        ),
        required_attachment=bool(_pick(doc, "requiredAttachment", "required_attachment", default=False)),
        tags=list(_as_list(_pick(doc, "tags", default=[]))),
        customer_code_extraction=dict(
            _pick(doc, "customerCodeExtraction", "customer_code_extraction", default={}) or {}
        ),
        subject_update=dict(_pick(doc, "subjectUpdate", "subject_update", default={}) or {}),
        email_actions=dict(_pick(doc, "emailActions", "email_actions", default={}) or {}),
    )


def _customer_from_doc(doc: dict[str, Any]) -> CustomerProfile:
    return CustomerProfile(
        id=str(_pick(doc, "id")),
        tenant_id=_pick(doc, "tenantId", "tenant_id", default="default"),
        customer_code=_pick(doc, "customerCode", "customer_code", default=""),
        name=_pick(doc, "name", default=""),
        route_number=_pick(doc, "routeNumber", "route_number", default=""),
        csr_name=_pick(doc, "csrName", "csr_name", default=""),
        csr_email=_pick(doc, "csrEmail", "csr_email", default=""),
        csr_folder=_pick(doc, "csrFolder", "csr_folder", default=""),
        store_number=_pick(doc, "storeNumber", "store_number", default=""),
        address1=_pick(doc, "address1", "locationAddress1", "location_address1", default=""),
        city=_pick(doc, "city", "locationCity", "location_city", default=""),
        state=_pick(doc, "state", "locationState", "location_state", default=""),
        postal_code=_pick(doc, "postalCode", "postal_code", "locationZip", "location_zip", default=""),
        phone=_pick(doc, "phone", default=""),
        website=_pick(doc, "website", "customerWebsite", "customer_website", default=""),
        customer_email=_pick(doc, "customerEmail", "customer_email", default=""),
        sender_domains=list(_as_list(_pick(doc, "senderDomains", "sender_domains", default=[]))),
        aliases=list(_as_list(_pick(doc, "aliases", default=[]))),
        known_subject_patterns=list(
            _as_list(_pick(doc, "knownSubjectPatterns", "known_subject_patterns", default=[]))
        ),
        embedding=list(_as_list(_pick(doc, "embedding", default=[]))),
        source_name=_pick(doc, "sourceName", "source_name", default=""),
        source_rows_blob_url=_pick(doc, "sourceRowsBlobUrl", "source_rows_blob_url", default=""),
        last_imported_at=_pick(doc, "lastImportedAt", "last_imported_at", default=None),
        custom_fields=dict(_pick(doc, "customFields", "custom_fields", default={}) or {}),
        raw_source=dict(_pick(doc, "rawSource", "raw_source", default={}) or {}),
    )


def _customer_alias_from_doc(doc: dict[str, Any]) -> CustomerAlias:
    return CustomerAlias(
        id=str(_pick(doc, "id")),
        tenant_id=_pick(doc, "tenantId", "tenant_id", default="default"),
        customer_id=_pick(doc, "customerId", "customer_id", default=""),
        alias_type=_pick(doc, "aliasType", "alias_type", default=""),
        value=_pick(doc, "value", default=""),
        normalized_value=_pick(doc, "normalizedValue", "normalized_value", default=""),
        source=_pick(doc, "source", default=""),
        confidence=float(_pick(doc, "confidence", default=1.0) or 1.0),
        raw_source=dict(_pick(doc, "rawSource", "raw_source", default={}) or {}),
        created_at=_pick(doc, "createdAt", "created_at", default=utc_now()),
        updated_at=_pick(doc, "updatedAt", "updated_at", default=utc_now()),
    )


def _item_from_doc(doc: dict[str, Any]) -> ItemRecord:
    return ItemRecord(
        id=str(_pick(doc, "id")),
        tenant_id=_pick(doc, "tenantId", "tenant_id", default="default"),
        customer_id=_pick(doc, "customerId", "customer_id", default=""),
        internal_item_number=_pick(doc, "internalItemNumber", "internal_item_number", default=""),
        description=_pick(doc, "description", default=""),
        upc=_pick(doc, "upc", default=""),
        alt_parts_combined=list(_as_list(_pick(doc, "altPartsCombined", "alt_parts_combined", default=[]))),
        customer_item_numbers=list(
            _as_list(_pick(doc, "customerItemNumbers", "customer_item_numbers", default=[]))
        ),
        aliases=list(_as_list(_pick(doc, "aliases", default=[]))),
        embedding=list(_as_list(_pick(doc, "embedding", default=[]))),
        source_name=_pick(doc, "sourceName", "source_name", default=""),
        source_rows_blob_url=_pick(doc, "sourceRowsBlobUrl", "source_rows_blob_url", default=""),
        last_imported_at=_pick(doc, "lastImportedAt", "last_imported_at", default=None),
        raw_source=dict(_pick(doc, "rawSource", "raw_source", default={}) or {}),
    )


def _processor_profile_from_doc(doc: dict[str, Any]) -> ProcessorProfile:
    return ProcessorProfile(
        id=str(_pick(doc, "id")),
        tenant_id=_pick(doc, "tenantId", "tenant_id", default="default"),
        customer_id=_pick(doc, "customerId", "customer_id", default=None),
        name=_pick(doc, "name", default=""),
        processor_type=_pick(doc, "processorType", "processor_type", default="csv"),
        output_profile_id=_pick(doc, "outputProfileId", "output_profile_id", default=None),
        settings=dict(_pick(doc, "settings", default={}) or {}),
    )


def _output_profile_from_doc(doc: dict[str, Any]) -> OutputProfile:
    return OutputProfile(
        id=str(_pick(doc, "id")),
        tenant_id=_pick(doc, "tenantId", "tenant_id", default="default"),
        customer_id=_pick(doc, "customerId", "customer_id", default=GLOBAL_CUSTOMER_ID),
        name=_pick(doc, "name", default=""),
        output_type=_pick(doc, "outputType", "output_type", default="csv"),
        destination=dict(_pick(doc, "destination", default={}) or {}),
        settings=dict(_pick(doc, "settings", default={}) or {}),
    )


def _order_from_doc(doc: dict[str, Any]) -> OrderRun:
    lines = []
    for item in _as_list(_pick(doc, "lines", default=[])):
        if not isinstance(item, dict):
            continue
        validation_status = _pick(item, "validationStatus", "validation_status", default=MatchStatus.UNRESOLVED)
        if not isinstance(validation_status, MatchStatus):
            validation_status = MatchStatus(validation_status)
        lines.append(
            OrderLine(
                line_number=int(_pick(item, "lineNumber", "line_number", default=len(lines) + 1)),
                quantity=_as_float(_pick(item, "quantity", default=None)),
                provided_item_number=_pick(item, "providedItemNumber", "provided_item_number", default=""),
                provided_upc=_pick(item, "providedUpc", "provided_upc", default=""),
                description=_pick(item, "description", default=""),
                unit=_pick(item, "unit", default=""),
                unit_price=_as_float(_pick(item, "unitPrice", "unit_price", default=None)),
                source_row_index=(
                    int(_pick(item, "sourceRowIndex", "source_row_index", default=0) or 0) or None
                ),
                matched_internal_item_number=_pick(
                    item,
                    "matchedInternalItemNumber",
                    "matched_internal_item_number",
                    default=None,
                ),
                validation_status=validation_status,
                validation_confidence=float(
                    _pick(item, "validationConfidence", "validation_confidence", default=0.0) or 0.0
                ),
                validation_method=_pick(item, "validationMethod", "validation_method", default=""),
                validation_candidates=list(
                    _as_list(_pick(item, "validationCandidates", "validation_candidates", default=[]))
                ),
                validation_errors=list(
                    _as_list(_pick(item, "validationErrors", "validation_errors", default=[]))
                ),
                raw=dict(_pick(item, "raw", default={}) or {}),
            )
        )

    status = _pick(doc, "status", default=ProcessingStatus.RECEIVED)
    if not isinstance(status, ProcessingStatus):
        status = ProcessingStatus(status)

    return OrderRun(
        id=str(_pick(doc, "id")),
        tenant_id=_pick(doc, "tenantId", "tenant_id", default="default"),
        email_message_id=_pick(doc, "emailMessageId", "email_message_id", default=""),
        customer_id=_pick(doc, "customerId", "customer_id", default=None),
        correlation_id=_pick(doc, "correlationId", "correlation_id", default=None),
        processor_profile_id=_pick(doc, "processorProfileId", "processor_profile_id", default=None),
        status=status,
        header=dict(_pick(doc, "header", default={}) or {}),
        po_number=_pick(doc, "poNumber", "po_number", default=""),
        order_number=_pick(doc, "orderNumber", "order_number", default=""),
        source_type=_pick(doc, "sourceType", "source_type", default=""),
        source_file_name=_pick(doc, "sourceFileName", "source_file_name", default=""),
        source_metadata=dict(_pick(doc, "sourceMetadata", "source_metadata", default={}) or {}),
        processor_type=_pick(doc, "processorType", "processor_type", default=""),
        processor_version=_pick(doc, "processorVersion", "processor_version", default=""),
        lines=lines,
        output_artifacts=list(_as_list(_pick(doc, "outputArtifacts", "output_artifacts", default=[]))),
        errors=list(_as_list(_pick(doc, "errors", default=[]))),
        parse_warnings=list(_as_list(_pick(doc, "parseWarnings", "parse_warnings", default=[]))),
        processing_started_at=_pick(doc, "processingStartedAt", "processing_started_at", default=None),
        processing_completed_at=_pick(doc, "processingCompletedAt", "processing_completed_at", default=None),
        created_at=_pick(doc, "createdAt", "created_at", default=utc_now()),
        updated_at=_pick(doc, "updatedAt", "updated_at", default=utc_now()),
    )


def _normalized_mailbox_address(value: str) -> str:
    return str(value or "").strip().lower()


def _mailbox_enabled(mailbox: dict[str, Any]) -> bool:
    return bool(_pick(mailbox, "enabled", default=True))


def _candidate_routing_rules(email: EmailMessage, rules: list[RoutingRule]) -> list[RoutingRule]:
    candidates: list[RoutingRule] = []
    for rule in rules:
        if (
            email.customer_id
            and rule.customer_id
            and rule.customer_id != GLOBAL_CUSTOMER_ID
            and rule.customer_id != email.customer_id
        ):
            continue
        candidates.append(rule)
    return candidates


def _normalized_email(value: str) -> str:
    return str(value or "").strip().lower()


def _item_identity_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _document_customer_id(document: dict[str, Any]) -> str | None:
    customer_id = _pick(document, "customerId", "customer_id", default=None)
    if customer_id:
        return customer_id
    if "customerCode" in document or "customer_code" in document:
        return _pick(document, "id", default=None)
    return None


def _document_status(document: dict[str, Any]) -> str:
    return str(_pick(document, "status", default="") or "")


def _bounded_worker_count(
    count: int,
    *,
    configured: Any = None,
    env_var: str = "",
    default: int = 8,
    upper_bound: int = 32,
) -> int:
    raw = configured
    if raw in {None, ""} and env_var:
        raw = os.environ.get(env_var, "")
    try:
        value = int(str(raw).strip()) if raw not in {None, ""} else default
    except ValueError:
        value = default
    return max(1, min(max(1, count), max(1, upper_bound), max(1, value)))


def _parallel_ordered(items: list[Any], callback: Any, *, max_workers: int) -> list[Any]:
    if len(items) <= 1 or max_workers <= 1:
        return [callback(item) for item in items]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(callback, items))


def _action_key_for_routing_outcome(outcome: RoutingOutcome) -> str:
    if outcome == RoutingOutcome.KNOWN_ORDER:
        return "processedOrder"
    if outcome == RoutingOutcome.KNOWN_CUSTOMER_NON_ORDER:
        return "nonOrder"
    if outcome in {RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION, RoutingOutcome.NEEDS_HUMAN_REVIEW}:
        return "failedOrder"
    if outcome == RoutingOutcome.IGNORED:
        return "ignored"
    return "general"


def _normalized_customer_rule_value(alias_type: str, value: str) -> str:
    normalized_type = str(alias_type or "").replace("-", "_").lower()
    if normalized_type in {
        "customercode",
        "customer_code",
        "code",
        "accountnumber",
        "account_number",
        "storenumber",
        "store_number",
        "store",
        "location",
        "locationnumber",
        "location_number",
        "routenumber",
        "route_number",
        "route",
    }:
        return normalize_identifier(value)
    if normalized_type in {
        "senderdomain",
        "sender_domain",
        "domain",
        "emaildomain",
        "email_domain",
    }:
        return normalize_domain(value)
    if normalized_type in {"senderemail", "sender_email", "email", "emailaddress", "email_address"}:
        return str(value or "").strip().lower()
    return str(value or "").strip()


class OrderProcessorApi:
    def __init__(
        self,
        repository: InMemoryRepository | None = None,
        customer_vector_search: CustomerVectorSearch | None = None,
        customer_ai_identifier: CustomerAiIdentifier | None = None,
        source_archive: SourceRowArchive | None = None,
        import_embedding_client: TextEmbeddingClient | None = None,
        customer_vector_store_manager: Any | None = None,
        output_artifact_store: OutputArtifactStore | None = None,
        secret_store: Any | None = None,
        google_document_ai_client: DocumentAiClient | None = None,
    ) -> None:
        self.repository = repository or InMemoryRepository()
        self.customer_vector_search = (
            customer_vector_search
            if customer_vector_search is not None
            else customer_vector_search_from_environment(self.repository)
        )
        self.customer_ai_identifier = (
            customer_ai_identifier
            if customer_ai_identifier is not None
            else customer_ai_identifier_from_environment()
        )
        self.source_archive = source_archive or source_archive_from_environment()
        self.import_embedding_client = (
            import_embedding_client
            if import_embedding_client is not None
            else import_embedding_client_from_environment()
        )
        self.customer_vector_store_manager = (
            customer_vector_store_manager
            if customer_vector_store_manager is not None
            else customer_vector_store_manager_from_environment(self.repository)
        )
        self.output_artifact_store = output_artifact_store or output_artifact_store_from_environment()
        self.secret_store = secret_store if secret_store is not None else secret_store_from_environment()
        self.google_document_ai_client = google_document_ai_client or GoogleDocumentAiClient()
        self._console_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
        self._monitor_backfill_checked_tenants: set[str] = set()

    def ingest_email(self, payload: dict[str, Any]) -> dict[str, Any]:
        email = _email_from_payload(payload)
        observability = correlation_context(payload, email.correlation_id or email.id)
        email.correlation_id = observability["correlationId"]
        email.source["observability"] = merge_observability(
            dict(_pick(email.source, "observability", default={}) or {}),
            observability,
        )
        mailbox_account = self._resolve_mailbox_account(email)
        mailbox_account_requested = bool(email.mailbox_account_id)

        if mailbox_account:
            email.mailbox_account_id = str(_pick(mailbox_account, "id"))
            if not email.mailbox:
                email.mailbox = _pick(mailbox_account, "mailboxAddress", "mailbox_address", default="")

        email.status = ProcessingStatus.PROCESSING
        self._set_email_processing_state(email, stage="received", pathway="routing")
        email_doc = self.repository.upsert("emailMessages", to_dict(email))
        self._upsert_monitor_record_for_email(email_doc)

        rules: list[RoutingRule] = []
        customers: list[CustomerProfile] = []
        aliases: list[CustomerAlias] = []
        if mailbox_account_requested and mailbox_account is None:
            decision = RoutingDecision(
                outcome=RoutingOutcome.NEEDS_HUMAN_REVIEW,
                mailbox_account_id=email.mailbox_account_id,
                customer_id=email.customer_id,
                confidence=0.0,
                reasons=["mailbox account id was provided but no mailboxAccounts record was found"],
                matched_signals={
                    "mailbox": email.mailbox,
                    "mailboxAccountId": email.mailbox_account_id,
                    "customerId": email.customer_id,
                    "defaultOrderSignal": default_order_signal(email),
                },
            )
        elif mailbox_account is not None and not _mailbox_enabled(mailbox_account):
            decision = RoutingDecision(
                outcome=RoutingOutcome.IGNORED,
                mailbox_account_id=email.mailbox_account_id,
                customer_id=email.customer_id,
                confidence=1.0,
                reasons=["mailbox account is disabled"],
                matched_signals={
                    "mailbox": email.mailbox,
                    "mailboxAccountId": email.mailbox_account_id,
                    "customerId": email.customer_id,
                    "defaultOrderSignal": default_order_signal(email),
                },
            )
        else:
            rules = [
                _routing_rule_from_doc(doc)
                for doc in self.repository.query_by_tenant("routingRules", email.tenant_id)
            ]
            customers = [
                _customer_from_doc(doc)
                for doc in self.repository.query_by_tenant("customers", email.tenant_id)
            ]
            aliases = [
                _customer_alias_from_doc(doc)
                for doc in self.repository.query_by_tenant("customerAliases", email.tenant_id)
            ]
            decision = evaluate_email_triage(
                email,
                _candidate_routing_rules(email, rules),
                customers=customers,
                aliases=aliases,
            )
            decision = self._identify_customer_for_matched_routing(email, decision, rules, customers, aliases)

        email.customer_id = decision.customer_id or email.customer_id
        email.routing = to_dict(decision)
        email.updated_at = utc_now()
        email.status = self._status_for_routing_decision(decision)
        self._set_email_processing_state(
            email,
            stage=self._processing_stage_for_routing_decision(decision),
            pathway=self._pathway_for_routing_decision(decision),
        )
        email_doc = self.repository.upsert("emailMessages", to_dict(email))
        self._upsert_monitor_record_for_email(email_doc)

        order_run = None
        if decision.outcome == RoutingOutcome.KNOWN_ORDER:
            order_run = OrderRun(
                id=stable_id(email.tenant_id, email.id, decision.rule_id or "knownOrder"),
                tenant_id=email.tenant_id,
                email_message_id=email.id,
                customer_id=decision.customer_id,
                correlation_id=email.correlation_id,
                processor_profile_id=decision.processor_profile_id,
                status=ProcessingStatus.RECEIVED,
                source_metadata={
                    "emailMessageId": email.id,
                    "mailbox": email.mailbox,
                    "sender": email.sender,
                    "subject": email.subject,
                    "receivedAt": email.received_at,
                    "observability": observability,
                },
            )
            email.order_run_id = order_run.id
            email.updated_at = utc_now()
            order_doc = self.repository.upsert("orderRuns", to_dict(order_run))
            email_doc = self.repository.upsert("emailMessages", to_dict(email))
            self._upsert_monitor_record_for_email(email_doc, order=order_doc)

        exception_task = None
        if decision.outcome in {
            RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION,
            RoutingOutcome.NEEDS_HUMAN_REVIEW,
        }:
            exception_task = self._create_exception(
                tenant_id=email.tenant_id,
                task_type="routing",
                prompt="Resolve email routing or customer identification.",
                email_message_id=email.id,
                correlation_id=email.correlation_id,
                context={
                    "routingDecision": to_dict(decision),
                    "subject": email.subject,
                    "sender": email.sender,
                    "mailbox": email.mailbox,
                    "mailboxAccountId": email.mailbox_account_id,
                    "customerId": email.customer_id,
                    "defaultOrderSignal": default_order_signal(email),
                    "observability": observability,
                },
            )

        self._audit(
            email.tenant_id,
            "email.ingested",
            email.correlation_id or email.id,
            email.id,
            {
                "emailMessageId": email.id,
                "customerId": email.customer_id,
                "routingDecision": to_dict(decision),
                "mailboxAccount": mailbox_account,
                "defaultOrderSignal": default_order_signal(email),
                "observability": observability,
            },
            customer_id=email.customer_id,
            email_message_id=email.id,
        )

        return {
            "emailMessage": _api_value(email),
            "routingDecision": _api_value(decision),
            "orderRun": _api_value(order_run) if order_run else None,
            "exceptionTask": exception_task,
            "observability": observability,
        }

    def _identify_customer_for_matched_routing(
        self,
        email: EmailMessage,
        decision: RoutingDecision,
        rules: list[RoutingRule],
        customers: list[CustomerProfile],
        aliases: list[CustomerAlias],
    ) -> RoutingDecision:
        if decision.customer_id:
            return decision
        if decision.outcome == RoutingOutcome.KNOWN_ORDER:
            decision.matched_signals["customerIdentificationDeferred"] = "order customer identification runs after processor extraction"
            return decision
        if decision.outcome not in {
            RoutingOutcome.KNOWN_CUSTOMER_NON_ORDER,
            RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION,
        }:
            return decision

        email.status = ProcessingStatus.PROCESSING
        self._set_email_processing_state(
            email,
            stage="identifyingCustomer",
            pathway=self._pathway_for_routing_decision(decision),
            details={"routingDecision": to_dict(decision)},
        )
        email_doc = self.repository.upsert("emailMessages", to_dict(email))
        self._upsert_monitor_record_for_email(email_doc)

        result = identify_customer_from_email(
            email,
            customers,
            aliases=aliases,
            vector_search=self.customer_vector_search,
            ai_identifier=self.customer_ai_identifier,
            confidence_threshold=DEFAULT_CUSTOMER_CONFIDENCE_THRESHOLD,
        )
        decision.matched_signals["customerIdentification"] = _api_value(result)
        if result.status == MatchStatus.MATCHED and result.customer_id:
            decision.customer_id = result.customer_id
            decision.matched_signals["customerId"] = result.customer_id
            decision.matched_signals["identifiedCustomerCode"] = result.customer_code
            decision.reasons.append(f"customer identification matched customer {result.customer_id}")
            customer = next((item for item in customers if item.id == result.customer_id), None)
            rule = next((item for item in rules if item.id == decision.rule_id), None)
            if decision.outcome == RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION:
                rule = self._non_order_rule_for_identified_customer(email, result.customer_id, rules)
                if rule:
                    decision.outcome = RoutingOutcome.KNOWN_CUSTOMER_NON_ORDER
                    decision.rule_id = rule.id
                    decision.processor_profile_id = None
                    decision.confidence = max(decision.confidence, result.confidence)
                    decision.matched_signals.update(
                        {
                            "ruleName": rule.name,
                            "tags": list(rule.tags),
                            "triagePhase": normalize_triage_phase(rule.phase),
                            "promotedByCustomerIdentification": True,
                        }
                    )
                    decision.reasons.append(
                        f"customer identification promoted email to non-order rule {rule.name or rule.id}"
                    )
                else:
                    decision.outcome = RoutingOutcome.NEEDS_HUMAN_REVIEW
                    decision.processor_profile_id = None
                    decision.confidence = result.confidence
                    decision.reasons.append(
                        "customer identification matched a customer but no non-order routing rule matched"
                    )
            if rule and customer:
                action_plan = build_email_action_plan(
                    email,
                    customer,
                    rule,
                    _action_key_for_routing_outcome(decision.outcome),
                )
                if action_plan:
                    decision.matched_signals["emailActions"] = action_plan
            return decision

        original_outcome = decision.outcome
        decision.outcome = RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION
        decision.processor_profile_id = None
        decision.customer_id = None
        decision.confidence = result.confidence
        decision.reasons.append(
            f"{original_outcome.value} rule matched but customer identification did not produce a confident customer"
        )
        return decision

    @staticmethod
    def _non_order_rule_for_identified_customer(
        email: EmailMessage,
        customer_id: str,
        rules: list[RoutingRule],
    ) -> RoutingRule | None:
        original_customer_id = email.customer_id
        email.customer_id = customer_id
        try:
            for rule in sorted([item for item in rules if item.enabled], key=lambda item: item.priority):
                if rule.outcome != RoutingOutcome.KNOWN_CUSTOMER_NON_ORDER:
                    continue
                if normalize_triage_phase(rule.phase) not in {"nonOrder", "general"}:
                    continue
                matches, _ = rule_matches(email, rule)
                if matches:
                    return rule
        finally:
            email.customer_id = original_customer_id
        return None

    def poll_mailboxes(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(_pick(payload, "tenantId", "tenant_id", default="")).strip()
        limit = int(
            _pick(
                payload,
                "limit",
                default=os.environ.get("ORDER_PROCESSOR_MAILBOX_POLL_LIMIT", "25"),
            )
            or 25
        )
        limit = max(1, min(limit, 50))
        if tenant_id:
            mailboxes = self.repository.query_by_tenant("mailboxAccounts", tenant_id)
        else:
            mailboxes = self.repository.list("mailboxAccounts")

        mailbox_workers = _bounded_worker_count(
            len(mailboxes),
            configured=_pick(payload, "mailboxWorkers", "mailbox_workers", default=None),
            env_var="ORDER_PROCESSOR_MAILBOX_POLL_WORKERS",
            default=4,
        )
        results = _parallel_ordered(
            list(mailboxes),
            lambda mailbox: self._poll_mailbox(mailbox, limit=limit, payload=payload),
            max_workers=mailbox_workers,
        )
        return {
            "mailboxPoll": {
                "tenantId": tenant_id or "*",
                "mailboxCount": len(mailboxes),
                "processedCount": sum(int(result.get("processedCount", 0)) for result in results),
                "ingestedCount": sum(int(result.get("ingestedCount", 0)) for result in results),
                "skippedCount": sum(int(result.get("skippedCount", 0)) for result in results),
                "reconciledCount": sum(int(result.get("reconciledCount", 0)) for result in results),
                "failedCount": sum(1 for result in results if result.get("status") == "failed"),
                "checkedAt": utc_now(),
            },
            "results": results,
        }

    def sync_mailbox_subscriptions(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(_pick(payload, "tenantId", "tenant_id", default="")).strip()
        mailbox_id = str(_pick(payload, "mailboxAccountId", "mailbox_account_id", "mailboxId", default="")).strip()
        notification_url = str(
            _pick(payload, "notificationUrl", "notification_url", default=self._graph_notification_url())
        ).strip()
        lifecycle_url = str(
            _pick(payload, "lifecycleNotificationUrl", "lifecycle_notification_url", default=notification_url)
        ).strip()
        auth_mode = str(
            _pick(
                payload,
                "authMode",
                "auth_mode",
                default=os.environ.get("ORDER_PROCESSOR_GRAPH_SUBSCRIPTION_AUTH_MODE", "auto"),
            )
        ).strip().lower() or "auto"
        force = bool(_pick(payload, "force", default=False))
        if not notification_url:
            return {
                "error": "notificationUrlRequired",
                "message": "Set ORDER_PROCESSOR_GRAPH_NOTIFICATION_BASE_URL or pass notificationUrl.",
            }

        if mailbox_id:
            mailbox = self.repository.get("mailboxAccounts", mailbox_id)
            mailboxes = [mailbox] if mailbox else []
        elif tenant_id:
            mailboxes = self.repository.query_by_tenant("mailboxAccounts", tenant_id)
        else:
            mailboxes = self.repository.list("mailboxAccounts")

        results = [
            self._ensure_mailbox_subscription(
                mailbox,
                notification_url=notification_url,
                lifecycle_notification_url=lifecycle_url,
                auth_mode=auth_mode,
                force=force,
            )
            for mailbox in mailboxes
            if mailbox
        ]
        return {
            "mailboxSubscriptions": {
                "tenantId": tenant_id or "*",
                "mailboxCount": len(mailboxes),
                "activeCount": sum(1 for result in results if result.get("status") == "active"),
                "createdCount": sum(1 for result in results if result.get("action") == "created"),
                "recreatedCount": sum(1 for result in results if result.get("action") == "recreated"),
                "renewedCount": sum(1 for result in results if result.get("action") == "renewed"),
                "skippedCount": sum(1 for result in results if result.get("status") == "skipped"),
                "failedCount": sum(1 for result in results if result.get("status") == "failed"),
                "checkedAt": utc_now(),
            },
            "results": results,
        }

    def renew_mailbox_subscriptions(self, payload: dict[str, Any]) -> dict[str, Any]:
        renewal_window = int(
            _pick(
                payload,
                "renewalWindowMinutes",
                "renewal_window_minutes",
                default=os.environ.get("ORDER_PROCESSOR_GRAPH_SUBSCRIPTION_RENEWAL_WINDOW_MINUTES", "2880"),
            )
            or 2880
        )
        payload = {**payload, "renewalWindowMinutes": renewal_window}
        return self.sync_mailbox_subscriptions(payload)

    def process_graph_notifications(self, payload: dict[str, Any]) -> dict[str, Any]:
        notifications = list(_as_list(_pick(payload, "notifications", "value", default=[])))
        notification_items = [notification for notification in notifications if isinstance(notification, dict)]
        notification_workers = _bounded_worker_count(
            len(notification_items),
            configured=_pick(payload, "notificationWorkers", "notification_workers", default=None),
            env_var="ORDER_PROCESSOR_GRAPH_NOTIFICATION_WORKERS",
            default=16,
        )
        results = _parallel_ordered(
            notification_items,
            self._process_graph_notification,
            max_workers=notification_workers,
        )
        return {
            "graphNotifications": {
                "notificationCount": len(notifications),
                "processedCount": sum(1 for result in results if result.get("status") in {"ingested", "skipped"}),
                "ingestedCount": sum(1 for result in results if result.get("status") == "ingested"),
                "skippedCount": sum(1 for result in results if result.get("status") == "skipped"),
                "lifecycleCount": sum(1 for result in results if result.get("status") == "lifecycle"),
                "failedCount": sum(1 for result in results if result.get("status") == "failed"),
                "checkedAt": utc_now(),
            },
            "results": results,
        }

    def _ensure_mailbox_subscription(
        self,
        mailbox: dict[str, Any],
        *,
        notification_url: str,
        lifecycle_notification_url: str,
        auth_mode: str,
        force: bool = False,
    ) -> dict[str, Any]:
        mailbox_id = str(_pick(mailbox, "id", default=""))
        tenant_id = str(_pick(mailbox, "tenantId", "tenant_id", default=""))
        mailbox_address = str(_pick(mailbox, "mailboxAddress", "mailbox_address", default="")).strip().lower()
        result: dict[str, Any] = {
            "mailboxAccountId": mailbox_id,
            "tenantId": tenant_id,
            "mailboxAddress": mailbox_address,
            "status": "skipped",
        }
        if not _mailbox_enabled(mailbox):
            result["reason"] = "mailbox disabled"
            return result
        if not tenant_id or not mailbox_id or not mailbox_address:
            result["reason"] = "mailbox missing tenant, id, or address"
            return result

        settings = dict(_pick(mailbox, "settings", default={}) or {})
        existing = dict(settings.get("graphSubscription") or {})
        existing_id = str(existing.get("subscriptionId", ""))
        now = datetime.now(UTC)
        renewal_window = timedelta(
            minutes=int(os.environ.get("ORDER_PROCESSOR_GRAPH_SUBSCRIPTION_RENEWAL_WINDOW_MINUTES", "2880") or 2880)
        )
        existing_expiration = _parse_datetime(str(existing.get("expirationDateTime", "")))
        if (
            existing_id
            and existing_expiration
            and existing_expiration > now + renewal_window
            and existing.get("notificationUrl") == notification_url
            and not force
        ):
            result.update(
                {
                    "status": "active",
                    "action": "unchanged",
                    "subscriptionId": existing_id,
                    "expirationDateTime": existing.get("expirationDateTime"),
                    "authMethod": existing.get("authMethod", ""),
                }
            )
            return result

        try:
            if existing_id:
                try:
                    subscription, auth_method = self._renew_graph_subscription(mailbox, existing_id, auth_mode=auth_mode)
                    action = "renewed"
                except MicrosoftGraphError as exc:
                    if exc.status_code not in {404, 410}:
                        raise
                    subscription, auth_method = self._create_graph_subscription(
                        mailbox,
                        notification_url=notification_url,
                        lifecycle_notification_url=lifecycle_notification_url,
                        auth_mode=auth_mode,
                    )
                    action = "recreated"
            else:
                subscription, auth_method = self._create_graph_subscription(
                    mailbox,
                    notification_url=notification_url,
                    lifecycle_notification_url=lifecycle_notification_url,
                    auth_mode=auth_mode,
                )
                action = "created"
        except MicrosoftGraphError as exc:
            result.update(
                {
                    "status": "failed",
                    "reason": str(exc),
                    "statusCode": exc.status_code,
                    "details": exc.details,
                }
            )
            settings["graphSubscription"] = {
                **existing,
                "status": "failed",
                "lastError": str(exc),
                "lastErrorStatusCode": exc.status_code,
                "lastAttemptAt": utc_now(),
                "notificationUrl": notification_url,
            }
            mailbox.update({"settings": settings, "ingestStatus": "needsAttention", "updatedAt": utc_now()})
            self.repository.upsert("mailboxAccounts", mailbox)
            self._audit(
                tenant_id,
                "mailbox.graphSubscription.failed",
                mailbox_id,
                mailbox_id,
                {
                    "mailboxAccountId": mailbox_id,
                    "mailboxAddress": mailbox_address,
                    "reason": str(exc),
                    "statusCode": exc.status_code,
                    "details": exc.details,
                },
            )
            return result

        subscription_id = str(subscription.get("id", existing_id))
        client_state = str(subscription.get("clientState") or existing.get("clientState") or "")
        graph_subscription = {
            **existing,
            "status": "active",
            "subscriptionId": subscription_id,
            "resource": str(subscription.get("resource", self._graph_subscription_resource(mailbox_address))),
            "changeType": str(subscription.get("changeType", "created")),
            "clientState": client_state,
            "notificationUrl": notification_url,
            "lifecycleNotificationUrl": lifecycle_notification_url,
            "expirationDateTime": str(subscription.get("expirationDateTime", "")),
            "authMethod": auth_method,
            "lastSyncedAt": utc_now(),
            "lastError": "",
        }
        settings["graphSubscription"] = graph_subscription
        mailbox.update({"settings": settings, "ingestStatus": "active", "updatedAt": utc_now()})
        self.repository.upsert("mailboxAccounts", mailbox)
        self._audit(
            tenant_id,
            "mailbox.graphSubscription.synced",
            mailbox_id,
            mailbox_id,
            {
                "mailboxAccountId": mailbox_id,
                "mailboxAddress": mailbox_address,
                "subscriptionId": subscription_id,
                "action": action,
                "expirationDateTime": graph_subscription["expirationDateTime"],
                "authMethod": auth_method,
            },
        )
        result.update(
            {
                "status": "active",
                "action": action,
                "subscriptionId": subscription_id,
                "expirationDateTime": graph_subscription["expirationDateTime"],
                "authMethod": auth_method,
            }
        )
        return result

    def _create_graph_subscription(
        self,
        mailbox: dict[str, Any],
        *,
        notification_url: str,
        lifecycle_notification_url: str,
        auth_mode: str,
    ) -> tuple[dict[str, Any], str]:
        mailbox_id = str(_pick(mailbox, "id", default=""))
        tenant_id = str(_pick(mailbox, "tenantId", "tenant_id", default=""))
        mailbox_address = str(_pick(mailbox, "mailboxAddress", "mailbox_address", default="")).strip().lower()
        client_state = self._graph_client_state(tenant_id, mailbox_id, mailbox_address)
        subscription_payload = {
            "changeType": "created",
            "notificationUrl": notification_url,
            "lifecycleNotificationUrl": lifecycle_notification_url,
            "resource": self._graph_subscription_resource(mailbox_address),
            "expirationDateTime": self._graph_subscription_expiration(),
            "clientState": client_state,
            "latestSupportedTlsVersion": "v1_2",
        }
        return self._graph_subscription_request(mailbox, "POST", "https://graph.microsoft.com/v1.0/subscriptions", subscription_payload, auth_mode)

    def _renew_graph_subscription(
        self,
        mailbox: dict[str, Any],
        subscription_id: str,
        *,
        auth_mode: str,
    ) -> tuple[dict[str, Any], str]:
        encoded_subscription_id = parse.quote(subscription_id, safe="")
        payload = {"expirationDateTime": self._graph_subscription_expiration()}
        return self._graph_subscription_request(
            mailbox,
            "PATCH",
            f"https://graph.microsoft.com/v1.0/subscriptions/{encoded_subscription_id}",
            payload,
            auth_mode,
        )

    def _graph_subscription_request(
        self,
        mailbox: dict[str, Any],
        method: str,
        url: str,
        payload: dict[str, Any],
        auth_mode: str,
    ) -> tuple[dict[str, Any], str]:
        candidates = self._graph_access_token_candidates(mailbox, auth_mode=auth_mode)
        last_error: MicrosoftGraphError | None = None
        for candidate in candidates:
            try:
                if method == "POST":
                    return graph_post(candidate["accessToken"], url, payload), candidate["authMethod"]
                return graph_patch(candidate["accessToken"], url, payload), candidate["authMethod"]
            except MicrosoftGraphError as exc:
                last_error = exc
                if exc.status_code not in {401, 403}:
                    break
        if last_error:
            raise last_error
        raise MicrosoftGraphError("No Microsoft Graph access token is available for mailbox subscription.")

    def _process_graph_notification(self, notification: dict[str, Any]) -> dict[str, Any]:
        subscription_id = str(_pick(notification, "subscriptionId", "subscription_id", default=""))
        client_state = str(_pick(notification, "clientState", "client_state", default=""))
        lifecycle_event = str(_pick(notification, "lifecycleEvent", "lifecycle_event", default=""))
        mailbox = self._mailbox_for_graph_subscription(subscription_id, client_state)
        if not mailbox:
            return {
                "status": "failed",
                "reason": "unknown subscription or invalid clientState",
                "subscriptionId": subscription_id,
            }

        mailbox_id = str(_pick(mailbox, "id", default=""))
        tenant_id = str(_pick(mailbox, "tenantId", "tenant_id", default=""))
        mailbox_address = str(_pick(mailbox, "mailboxAddress", "mailbox_address", default="")).strip().lower()
        if lifecycle_event:
            settings = dict(_pick(mailbox, "settings", default={}) or {})
            graph_subscription = dict(settings.get("graphSubscription") or {})
            graph_subscription.update(
                {
                    "status": "lifecycle",
                    "lastLifecycleEvent": lifecycle_event,
                    "lastLifecycleAt": utc_now(),
                }
            )
            settings["graphSubscription"] = graph_subscription
            mailbox.update({"settings": settings, "updatedAt": utc_now()})
            self.repository.upsert("mailboxAccounts", mailbox)
            self._audit(
                tenant_id,
                "mailbox.graphSubscription.lifecycle",
                mailbox_id,
                mailbox_id,
                {
                    "mailboxAccountId": mailbox_id,
                    "mailboxAddress": mailbox_address,
                    "subscriptionId": subscription_id,
                    "lifecycleEvent": lifecycle_event,
                },
            )
            return {"status": "lifecycle", "subscriptionId": subscription_id, "lifecycleEvent": lifecycle_event}

        if str(_pick(notification, "changeType", "change_type", default="")).lower() not in {"", "created"}:
            return {"status": "skipped", "reason": "change type is not created", "subscriptionId": subscription_id}

        message_id = self._graph_message_id_from_notification(notification)
        if not message_id:
            return {"status": "failed", "reason": "notification did not include a message id", "subscriptionId": subscription_id}

        auth_mode = str(
            _pick(
                dict(_pick(mailbox, "settings", default={}) or {}).get("graphSubscription", {}) or {},
                "authMethod",
                default=os.environ.get("ORDER_PROCESSOR_GRAPH_SUBSCRIPTION_AUTH_MODE", "auto"),
            )
        ).lower() or "auto"
        candidates = self._graph_access_token_candidates(mailbox, auth_mode=auth_mode)
        last_error: MicrosoftGraphError | None = None
        for candidate in candidates:
            try:
                message = self._graph_message_by_id(candidate["accessToken"], mailbox_address, message_id)
                ingest_result = self._ingest_graph_message(candidate["accessToken"], mailbox, message)
                self._update_mailbox_after_graph_notification(mailbox, subscription_id, ingest_result)
                self._audit(
                    tenant_id,
                    "mailbox.graphNotification.processed",
                    mailbox_id,
                    mailbox_id,
                    {
                        "mailboxAccountId": mailbox_id,
                        "mailboxAddress": mailbox_address,
                        "subscriptionId": subscription_id,
                        "graphMessageId": message_id,
                        "result": ingest_result,
                        "authMethod": candidate["authMethod"],
                    },
                )
                return {**ingest_result, "subscriptionId": subscription_id, "authMethod": candidate["authMethod"]}
            except MicrosoftGraphError as exc:
                last_error = exc
                if exc.status_code not in {401, 403}:
                    break
        reason = str(last_error) if last_error else "No Microsoft Graph access token is available."
        self._audit(
            tenant_id,
            "mailbox.graphNotification.failed",
            mailbox_id,
            mailbox_id,
            {
                "mailboxAccountId": mailbox_id,
                "mailboxAddress": mailbox_address,
                "subscriptionId": subscription_id,
                "graphMessageId": message_id,
                "reason": reason,
                "statusCode": last_error.status_code if last_error else None,
                "details": last_error.details if last_error else None,
            },
        )
        return {
            "status": "failed",
            "reason": reason,
            "statusCode": last_error.status_code if last_error else None,
            "subscriptionId": subscription_id,
            "graphMessageId": message_id,
        }

    def _poll_mailbox(self, mailbox: dict[str, Any], *, limit: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        mailbox_id = str(_pick(mailbox, "id", default=""))
        tenant_id = str(_pick(mailbox, "tenantId", "tenant_id", default=""))
        mailbox_address = str(_pick(mailbox, "mailboxAddress", "mailbox_address", default="")).strip().lower()
        result: dict[str, Any] = {
            "mailboxAccountId": mailbox_id,
            "tenantId": tenant_id,
            "mailboxAddress": mailbox_address,
            "status": "skipped",
            "processedCount": 0,
            "ingestedCount": 0,
            "skippedCount": 0,
            "reconciledCount": 0,
            "errors": [],
        }
        if not _mailbox_enabled(mailbox):
            result["reason"] = "mailbox disabled"
            return result
        if not tenant_id or not mailbox_id or not mailbox_address:
            result["reason"] = "mailbox missing tenant, id, or address"
            return result

        connection_id = str(_pick(mailbox, "connectionId", "connection_id", default=""))
        connection = self.repository.get("microsoftAuthConnections", connection_id) if connection_id else None
        if not connection:
            result["reason"] = "missing Microsoft Graph connection"
            return result

        secret_names = dict(_pick(connection, "keyVaultSecretNames", "key_vault_secret_names", default={}) or {})
        refresh_token_value = self.secret_store.get_secret(str(secret_names.get("refreshToken", "")))
        if not refresh_token_value:
            result["reason"] = "missing Microsoft Graph refresh token"
            return result

        config = config_from_environment(str(_pick(connection, "metadata", default={}).get("redirectUri", "")))
        try:
            token = refresh_access_token(config, refresh_token_value, self._microsoft_client_secret(config))
            updated_secret_names = self._store_microsoft_tokens(connection_id, token)
            if updated_secret_names:
                connection["keyVaultSecretNames"] = {**secret_names, **updated_secret_names}
                connection["status"] = AuthConnectionStatus.ACTIVE.value
                connection["lastTestedAt"] = utc_now()
                self.repository.upsert("microsoftAuthConnections", connection)

            access_token = str(token.get("access_token", ""))
            messages = self._graph_recent_messages(access_token, mailbox_address, limit)
            result["status"] = "active"
            result["messageCount"] = len(messages)
            message_workers = _bounded_worker_count(
                len(messages),
                configured=_pick(payload, "messageWorkers", "message_workers", default=None),
                env_var="ORDER_PROCESSOR_MAILBOX_MESSAGE_WORKERS",
                default=8,
            )
            poll_items = _parallel_ordered(
                list(messages),
                lambda message: self._ingest_graph_message(access_token, mailbox, message),
                max_workers=message_workers,
            )
            for poll_item in poll_items:
                result["processedCount"] += int(poll_item.get("processed", False))
                if poll_item.get("status") == "ingested":
                    result["ingestedCount"] += 1
                elif poll_item.get("status") == "skipped":
                    result["skippedCount"] += 1
                if poll_item.get("error"):
                    result["errors"].append(poll_item)
            reconciliation = self._reconcile_active_mailbox_emails(
                mailbox,
                access_token=access_token,
                mailbox_address=mailbox_address,
                payload={**payload, "source": "mailboxPoll"},
            )
            result["reconciliation"] = reconciliation
            result["reconciledCount"] = int(reconciliation.get("clearedCount", 0) or 0)
        except Exception as exc:
            result["status"] = "failed"
            result["reason"] = str(exc)

        settings = dict(_pick(mailbox, "settings", default={}) or {})
        settings["lastPoll"] = {
            "status": result["status"],
            "checkedAt": utc_now(),
            "messageCount": result.get("messageCount", 0),
            "ingestedCount": result["ingestedCount"],
            "processedCount": result["processedCount"],
            "skippedCount": result["skippedCount"],
            "reconciledCount": result["reconciledCount"],
            "reason": result.get("reason", ""),
        }
        mailbox.update(
            {
                "ingestStatus": "active" if result["status"] == "active" else "needsAttention",
                "settings": settings,
                "updatedAt": utc_now(),
            }
        )
        self.repository.upsert("mailboxAccounts", mailbox)
        self._audit(
            tenant_id,
            "mailbox.polled",
            mailbox_id,
            mailbox_id,
            {
                "mailboxAccountId": mailbox_id,
                "mailboxAddress": mailbox_address,
                "status": result["status"],
                "messageCount": result.get("messageCount", 0),
                "ingestedCount": result["ingestedCount"],
                "processedCount": result["processedCount"],
                "skippedCount": result["skippedCount"],
                "reconciledCount": result["reconciledCount"],
                "reason": result.get("reason", ""),
            },
        )
        return result

    @staticmethod
    def _active_processing_statuses() -> set[str]:
        return {
            ProcessingStatus.RECEIVED.value,
            ProcessingStatus.ROUTED.value,
            ProcessingStatus.PROCESSING.value,
        }

    @staticmethod
    def _active_reconcile_min_age(payload: dict[str, Any]) -> timedelta:
        minutes = int(
            _pick(
                payload,
                "activeReconcileMinAgeMinutes",
                "active_reconcile_min_age_minutes",
                default=os.environ.get("ORDER_PROCESSOR_ACTIVE_RECONCILE_MINUTES", str(ACTIVE_RECONCILE_DEFAULT_MINUTES)),
            )
            or ACTIVE_RECONCILE_DEFAULT_MINUTES
        )
        return timedelta(minutes=max(1, minutes))

    def _active_email_reconcile_candidates(
        self,
        tenant_id: str,
        mailbox: dict[str, Any],
        *,
        min_age: timedelta,
    ) -> list[dict[str, Any]]:
        mailbox_id = str(_pick(mailbox, "id", default="") or "")
        mailbox_address = _normalized_email(str(_pick(mailbox, "mailboxAddress", "mailbox_address", default="") or ""))
        cutoff = datetime.now(UTC) - min_age
        candidates: list[dict[str, Any]] = []
        for email in self.repository.query_by_tenant("emailMessages", tenant_id):
            if _document_status(email) not in self._active_processing_statuses():
                continue
            email_mailbox_id = str(_pick(email, "mailboxAccountId", "mailbox_account_id", default="") or "")
            email_mailbox_address = _normalized_email(str(_pick(email, "mailbox", default="") or ""))
            if mailbox_id and email_mailbox_id and email_mailbox_id != mailbox_id:
                continue
            if mailbox_address and not email_mailbox_id and email_mailbox_address and email_mailbox_address != mailbox_address:
                continue
            if self._open_exception_for_monitor(tenant_id, str(_pick(email, "id", default="")), str(_pick(email, "orderRunId", "order_run_id", default="") or "")):
                continue
            timestamp = _parse_datetime(
                str(_pick(email, "updatedAt", "updated_at", "createdAt", "created_at", "receivedAt", "received_at", default="") or "")
            )
            if timestamp and timestamp > cutoff:
                continue
            if not self._graph_message_id_for_email(email):
                continue
            candidates.append(email)
        return candidates

    def _reconcile_active_mailbox_emails(
        self,
        mailbox: dict[str, Any],
        *,
        access_token: str,
        mailbox_address: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not _bool_flag(
            _pick(payload, "reconcileActiveEmails", "reconcile_active_emails", default=os.environ.get("ORDER_PROCESSOR_RECONCILE_ACTIVE_EMAILS", "true")),
            default=True,
        ):
            return {"status": "disabled", "checkedCount": 0, "clearedCount": 0, "errors": []}

        tenant_id = str(_pick(mailbox, "tenantId", "tenant_id", default="") or "")
        if not tenant_id or not access_token or not mailbox_address:
            return {"status": "skipped", "reason": "missing tenant, access token, or mailbox address", "checkedCount": 0, "clearedCount": 0, "errors": []}

        min_age = self._active_reconcile_min_age(payload)
        candidates = self._active_email_reconcile_candidates(tenant_id, mailbox, min_age=min_age)
        result: dict[str, Any] = {
            "status": "checked",
            "checkedCount": len(candidates),
            "presentCount": 0,
            "clearedCount": 0,
            "skippedCount": 0,
            "errors": [],
            "minAgeMinutes": int(min_age.total_seconds() // 60),
        }
        for email in candidates:
            email_id = str(_pick(email, "id", default="") or "")
            graph_message_id = self._graph_message_id_for_email(email)
            try:
                self._graph_inbox_message_by_id(access_token, mailbox_address, graph_message_id)
                result["presentCount"] += 1
            except MicrosoftGraphError as exc:
                if exc.status_code not in {404, 410}:
                    result["errors"].append(
                        {
                            "emailMessageId": email_id,
                            "graphMessageId": graph_message_id,
                            "statusCode": exc.status_code,
                            "reason": str(exc),
                            "details": exc.details,
                        }
                    )
                    continue
                clear_result = self.clear_active_processing_run(
                    email_id,
                    {
                        **payload,
                        "tenantId": tenant_id,
                        "notes": "Mailbox reconciliation did not find the message in Inbox by its Microsoft Graph id; assuming it was moved or handled outside automation.",
                        "clearReason": "activeRunGraphMessageMissing",
                        "actionTaken": "no longer present in inbox; manually cleared",
                    },
                )
                if clear_result.get("error"):
                    result["errors"].append({"emailMessageId": email_id, **clear_result})
                else:
                    result["clearedCount"] += 1
        if result["errors"]:
            result["status"] = "partial" if result["clearedCount"] else "failed"
        return result

    def clear_active_processing_run(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = str(run_id or "").strip()
        if not run_id:
            return {"error": "idRequired", "message": "Active processing row id is required."}

        email = self.repository.get("emailMessages", run_id)
        order = None
        if email:
            order_id = str(_pick(email, "orderRunId", "order_run_id", default="") or "")
            order = self.repository.get("orderRuns", order_id) if order_id else None
        else:
            order = self.repository.get("orderRuns", run_id)
            email_id = str(_pick(order or {}, "emailMessageId", "email_message_id", default="") or "")
            email = self.repository.get("emailMessages", email_id) if email_id else None
        if not email and not order:
            return {"error": "notFound", "message": f"Active processing row {run_id} was not found."}

        now = utc_now()
        tenant_id = str(_pick(email or order or {}, "tenantId", "tenant_id", default=_pick(payload, "tenantId", "tenant_id", default="default")) or "default")
        actor = self._actor_from_payload(payload)
        reason = str(_pick(payload, "clearReason", "reason", default="activeRunManualClear") or "activeRunManualClear")
        notes = str(_pick(payload, "notes", default="Manually cleared from active processing; email will be handled outside automation.") or "")
        action_taken = str(_pick(payload, "actionTaken", "action_taken", default="manually cleared from active processing") or "")
        manual_override = {
            "reason": reason,
            "actionTaken": action_taken,
            "notes": notes,
            "actor": actor,
            "at": now,
        }

        email_doc = None
        if email:
            source = dict(_pick(email, "source", default={}) or {})
            source["manualOverride"] = manual_override
            processing = dict(source.get("processing") or {})
            processing.update(
                {
                    "stage": "manualCleared",
                    "status": ProcessingStatus.COMPLETED.value,
                    "updatedAt": now,
                }
            )
            source["processing"] = processing
            email["source"] = source
            email["status"] = ProcessingStatus.COMPLETED.value
            email["updatedAt"] = now
            email_doc = self.repository.upsert("emailMessages", email)

        order_doc = None
        if order:
            metadata = dict(_pick(order, "sourceMetadata", "source_metadata", default={}) or {})
            metadata["manualOverride"] = manual_override
            order["sourceMetadata"] = metadata
            if _document_status(order) in self._active_processing_statuses():
                order["status"] = ProcessingStatus.COMPLETED.value
                order["processingCompletedAt"] = now
            order["updatedAt"] = now
            order_doc = self.repository.upsert("orderRuns", order)

        monitor_record = None
        if email_doc:
            monitor_record = self._upsert_monitor_record_for_email(email_doc, order=order_doc)
        elif order_doc:
            monitor_record = self._upsert_monitor_record_for_order(order_doc)

        self._clear_console_cache(tenant_id)
        self._audit(
            tenant_id,
            "email.activeProcessingCleared",
            str(_pick(email_doc or order_doc or {}, "correlationId", "correlation_id", default=run_id)),
            run_id,
            {
                "emailMessageId": str(_pick(email_doc or {}, "id", default="") or ""),
                "orderRunId": str(_pick(order_doc or {}, "id", default="") or ""),
                "reason": reason,
                "notes": notes,
                "actionTaken": action_taken,
                "source": str(_pick(payload, "source", default="console") or "console"),
            },
            customer_id=_document_customer_id(email_doc or order_doc or {}),
            order_run_id=str(_pick(order_doc or {}, "id", default="") or "") or None,
            email_message_id=str(_pick(email_doc or {}, "id", default="") or "") or None,
            actor=actor,
        )
        return {
            "status": "cleared",
            "emailMessage": email_doc,
            "orderRun": order_doc,
            "monitorRecord": monitor_record,
            "manualOverride": manual_override,
        }

    def _graph_access_token_candidates(self, mailbox: dict[str, Any], *, auth_mode: str) -> list[dict[str, str]]:
        normalized_mode = (auth_mode or "auto").lower()
        if normalized_mode in {"app", "app-only"}:
            normalized_mode = "application"
        if normalized_mode not in {"auto", "application", "delegated"}:
            normalized_mode = "auto"

        candidates: list[dict[str, str]] = []
        errors: list[MicrosoftGraphError] = []
        config = config_from_environment()
        client_secret = self._microsoft_client_secret(config)

        if normalized_mode in {"auto", "application"}:
            try:
                token = client_credentials_access_token(config, client_secret)
                access_token = str(token.get("access_token", ""))
                if access_token:
                    candidates.append({"accessToken": access_token, "authMethod": "application"})
            except MicrosoftGraphError as exc:
                errors.append(exc)
                if normalized_mode == "application":
                    raise

        if normalized_mode in {"auto", "delegated"}:
            try:
                delegated = self._delegated_graph_access_token_for_mailbox(mailbox)
                if delegated:
                    candidates.append(delegated)
            except MicrosoftGraphError as exc:
                errors.append(exc)
                if normalized_mode == "delegated":
                    raise

        if not candidates and errors:
            raise errors[-1]
        return candidates

    def _delegated_graph_access_token_for_mailbox(self, mailbox: dict[str, Any]) -> dict[str, str] | None:
        connection_id = str(_pick(mailbox, "connectionId", "connection_id", default=""))
        connection = self.repository.get("microsoftAuthConnections", connection_id) if connection_id else None
        if not connection:
            return None
        secret_names = dict(_pick(connection, "keyVaultSecretNames", "key_vault_secret_names", default={}) or {})
        refresh_token_value = self.secret_store.get_secret(str(secret_names.get("refreshToken", "")))
        if not refresh_token_value:
            return None
        config = config_from_environment(str(_pick(connection, "metadata", default={}).get("redirectUri", "")))
        token = refresh_access_token(config, refresh_token_value, self._microsoft_client_secret(config))
        updated_secret_names = self._store_microsoft_tokens(connection_id, token)
        if updated_secret_names:
            connection["keyVaultSecretNames"] = {**secret_names, **updated_secret_names}
            connection["status"] = AuthConnectionStatus.ACTIVE.value
            connection["lastTestedAt"] = utc_now()
            self.repository.upsert("microsoftAuthConnections", connection)
        access_token = str(token.get("access_token", ""))
        return {"accessToken": access_token, "authMethod": "delegated"} if access_token else None

    def _graph_message_by_id(self, access_token: str, mailbox_address: str, graph_message_id: str) -> dict[str, Any]:
        encoded_mailbox = parse.quote(mailbox_address, safe="")
        encoded_message = parse.quote(graph_message_id, safe="")
        query = parse.urlencode({"$select": self._graph_message_select_fields()}, safe=",")
        url = f"https://graph.microsoft.com/v1.0/users/{encoded_mailbox}/messages/{encoded_message}?{query}"
        return graph_get(access_token, url)

    def _graph_inbox_message_by_id(self, access_token: str, mailbox_address: str, graph_message_id: str) -> dict[str, Any]:
        encoded_mailbox = parse.quote(mailbox_address, safe="")
        encoded_message = parse.quote(graph_message_id, safe="")
        query = parse.urlencode({"$select": self._graph_message_select_fields()}, safe=",")
        url = f"https://graph.microsoft.com/v1.0/users/{encoded_mailbox}/mailFolders/inbox/messages/{encoded_message}?{query}"
        return graph_get(access_token, url)

    @staticmethod
    def _graph_message_select_fields() -> str:
        return ",".join(
            [
                "id",
                "internetMessageId",
                "subject",
                "from",
                "sender",
                "receivedDateTime",
                "body",
                "bodyPreview",
                "categories",
                "hasAttachments",
                "isRead",
                "toRecipients",
                "webLink",
            ]
        )

    def _mailbox_for_graph_subscription(self, subscription_id: str, client_state: str) -> dict[str, Any] | None:
        if not subscription_id or not client_state:
            return None
        for mailbox in self.repository.list("mailboxAccounts"):
            settings = dict(_pick(mailbox, "settings", default={}) or {})
            graph_subscription = dict(settings.get("graphSubscription") or {})
            if (
                str(graph_subscription.get("subscriptionId", "")) == subscription_id
                and str(graph_subscription.get("clientState", "")) == client_state
            ):
                return mailbox
        return None

    def _update_mailbox_after_graph_notification(
        self,
        mailbox: dict[str, Any],
        subscription_id: str,
        result: dict[str, Any],
    ) -> None:
        settings = dict(_pick(mailbox, "settings", default={}) or {})
        graph_subscription = dict(settings.get("graphSubscription") or {})
        graph_subscription.update(
            {
                "status": "active",
                "subscriptionId": subscription_id,
                "lastNotificationAt": utc_now(),
                "lastNotificationResult": {
                    "status": result.get("status", ""),
                    "emailMessageId": result.get("emailMessageId", ""),
                    "orderRunId": result.get("orderRunId", ""),
                    "processed": bool(result.get("processed")),
                },
            }
        )
        settings["graphSubscription"] = graph_subscription
        settings["lastWebhook"] = {
            "status": result.get("status", ""),
            "checkedAt": utc_now(),
            "emailMessageId": result.get("emailMessageId", ""),
            "orderRunId": result.get("orderRunId", ""),
            "processed": bool(result.get("processed")),
        }
        mailbox.update({"settings": settings, "ingestStatus": "active", "updatedAt": utc_now()})
        self.repository.upsert("mailboxAccounts", mailbox)

    @staticmethod
    def _graph_message_id_from_notification(notification: dict[str, Any]) -> str:
        resource_data = dict(_pick(notification, "resourceData", "resource_data", default={}) or {})
        message_id = str(_pick(resource_data, "id", default=""))
        if message_id:
            return message_id
        for value in (
            str(_pick(resource_data, "@odata.id", "odataId", default="")),
            str(_pick(notification, "resource", default="")),
        ):
            match = re.search(r"messages\('([^']+)'\)", value, flags=re.IGNORECASE)
            if match:
                return parse.unquote(match.group(1))
            match = re.search(r"/messages/([^/?]+)", value, flags=re.IGNORECASE)
            if match:
                return parse.unquote(match.group(1))
            match = re.search(r"/messages\('([^']+)'\)", value, flags=re.IGNORECASE)
            if match:
                return parse.unquote(match.group(1))
        return ""

    @staticmethod
    def _graph_subscription_resource(mailbox_address: str) -> str:
        encoded_mailbox = parse.quote(mailbox_address.strip().lower(), safe="")
        return f"users/{encoded_mailbox}/mailFolders('inbox')/messages"

    @staticmethod
    def _graph_subscription_expiration() -> str:
        minutes = int(os.environ.get("ORDER_PROCESSOR_GRAPH_SUBSCRIPTION_LIFETIME_MINUTES", "10020") or 10020)
        minutes = max(45, min(minutes, 10080))
        return (datetime.now(UTC) + timedelta(minutes=minutes)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _graph_notification_url() -> str:
        explicit = os.environ.get("ORDER_PROCESSOR_GRAPH_NOTIFICATION_URL", "").strip()
        if explicit:
            return explicit
        base = (
            os.environ.get("ORDER_PROCESSOR_GRAPH_NOTIFICATION_BASE_URL", "").strip()
            or os.environ.get("ORDER_PROCESSOR_FUNCTION_BASE_URL", "").strip()
        )
        if not base and os.environ.get("WEBSITE_HOSTNAME"):
            base = f"https://{os.environ['WEBSITE_HOSTNAME'].strip()}"
        return f"{base.rstrip('/')}/graph/notifications" if base else ""

    @staticmethod
    def _graph_client_state(tenant_id: str, mailbox_id: str, mailbox_address: str) -> str:
        secret = (
            os.environ.get("ORDER_PROCESSOR_GRAPH_WEBHOOK_CLIENT_STATE_SECRET")
            or os.environ.get("ORDER_PROCESSOR_FUNCTION_SHARED_KEY")
            or "local-development-graph-webhook"
        )
        return stable_id("graph-webhook", tenant_id, mailbox_id, mailbox_address, secret)

    def _graph_recent_messages(self, access_token: str, mailbox_address: str, limit: int) -> list[dict[str, Any]]:
        encoded_mailbox = parse.quote(mailbox_address, safe="")
        select_fields = self._graph_message_select_fields()
        query = parse.urlencode(
            {
                "$top": str(limit),
                "$orderby": "receivedDateTime desc",
                "$select": select_fields,
            },
            safe=",",
        )
        url = f"https://graph.microsoft.com/v1.0/users/{encoded_mailbox}/mailFolders/inbox/messages?{query}"
        response = graph_get(access_token, url)
        return [message for message in response.get("value", []) if isinstance(message, dict)]

    def _apply_graph_email_actions(
        self,
        access_token: str,
        mailbox_address: str,
        graph_message_id: str,
        ingest_result: dict[str, Any],
        existing_categories: list[Any],
        action_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        action_plan = action_plan if action_plan is not None else self._email_action_plan_from_ingest_result(ingest_result)
        email_message = _as_dict(_pick(ingest_result, "emailMessage", "email_message", default={}))
        tenant_id = str(_pick(email_message, "tenantId", "tenant_id", default=""))
        email_message_id = str(_pick(email_message, "id", default=""))
        correlation_id = str(_pick(email_message, "correlationId", "correlation_id", default=email_message_id))
        result: dict[str, Any] = {
            "status": "skipped",
            "emailMessageId": email_message_id,
            "graphMessageId": graph_message_id,
            "applied": [],
            "skipped": [],
            "errors": [],
        }
        if not action_plan:
            result["reason"] = "no email actions"
            return result
        if not _bool_flag(_pick(action_plan, "productionActionsEnabled", "production_actions_enabled", default=True), default=True):
            result["reason"] = "production email actions disabled"
            return result
        if not access_token or not mailbox_address or not graph_message_id:
            result["reason"] = "missing Microsoft Graph message context"
            return result

        encoded_mailbox = parse.quote(mailbox_address, safe="")
        encoded_message = parse.quote(graph_message_id, safe="")
        message_url = f"https://graph.microsoft.com/v1.0/users/{encoded_mailbox}/messages/{encoded_message}"
        patch_payload: dict[str, Any] = {}
        subject_plan = _as_dict(_pick(action_plan, "subject", default={}))
        subject_value = str(_pick(subject_plan, "value", default="")).strip()
        if subject_value:
            patch_payload["subject"] = subject_value

        categories = self._merged_graph_categories([], _as_list(_pick(action_plan, "categories", default=[])))
        if categories:
            patch_payload["categories"] = categories

        if patch_payload:
            try:
                graph_patch(access_token, message_url, patch_payload)
                result["patched"] = patch_payload
                result["applied"].append({"action": "patch", "fields": sorted(patch_payload)})
            except MicrosoftGraphError as exc:
                result["errors"].append(
                    {
                        "action": "patch",
                        "reason": str(exc),
                        "statusCode": exc.status_code,
                        "details": exc.details,
                    }
                )

        move_plan = _as_dict(_pick(action_plan, "move", default={}))
        folder_name = str(_pick(move_plan, "folderName", "folder_name", "folder", default="")).strip()
        if move_plan and _bool_flag(_pick(move_plan, "enabled", default=False), default=False) and folder_name:
            try:
                destination_id = self._graph_mail_folder_id(
                    access_token,
                    mailbox_address,
                    folder_name,
                    create_missing=True,
                )
                if destination_id:
                    move_response = graph_post(access_token, f"{message_url}/move", {"destinationId": destination_id})
                    result["applied"].append(
                        {
                            "action": "move",
                            "folderName": folder_name,
                            "destinationId": destination_id,
                            "movedGraphMessageId": str(_pick(move_response, "id", default="")),
                        }
                    )
                else:
                    result["skipped"].append(
                        {"action": "move", "reason": "destination folder not found", "folderName": folder_name}
                    )
            except MicrosoftGraphError as exc:
                result["errors"].append(
                    {
                        "action": "move",
                        "folderName": folder_name,
                        "reason": str(exc),
                        "statusCode": exc.status_code,
                        "details": exc.details,
                    }
                )
        elif move_plan:
            skipped_move = {
                "action": "move",
                "reason": "move disabled or destination folder was blank",
                "mode": str(_pick(move_plan, "mode", default="")),
                "customerField": str(_pick(move_plan, "customerField", "customer_field", default="")),
            }
            if (
                str(_pick(move_plan, "mode", default="")).lower() == "customerfield"
                and str(_pick(move_plan, "customerField", "customer_field", default="")).strip()
            ):
                skipped_move["reason"] = "customer field did not resolve to a destination folder"
                result["errors"].append(skipped_move)
            else:
                result["skipped"].append(skipped_move)

        if result["errors"]:
            result["status"] = "failed" if not result["applied"] else "partial"
        elif result["applied"]:
            result["status"] = "applied"
        else:
            result["reason"] = result.get("reason") or "no applicable email actions"

        if tenant_id and email_message_id:
            self._audit(
                tenant_id,
                "email.graphActions.applied" if result["status"] == "applied" else "email.graphActions.skipped",
                correlation_id or email_message_id,
                email_message_id,
                result,
                customer_id=_pick(email_message, "customerId", "customer_id", default=None),
                email_message_id=email_message_id,
            )
        return result

    def _update_email_after_graph_actions(
        self,
        email_message_id: str,
        ingest_result: dict[str, Any],
        action_result: dict[str, Any],
    ) -> None:
        email = self.repository.get("emailMessages", email_message_id)
        if email is None:
            return

        now = utc_now()
        source = dict(_pick(email, "source", default={}) or {})
        history = [item for item in _as_list(source.get("graphEmailActionHistory")) if isinstance(item, dict)]
        history.append(action_result)
        source["graphEmailActionHistory"] = history[-20:]
        source["graphEmailActions"] = action_result
        for item in reversed(_as_list(_pick(action_result, "applied", default=[]))):
            if not isinstance(item, dict) or _pick(item, "action", default="") != "move":
                continue
            moved_graph_message_id = str(_pick(item, "movedGraphMessageId", "moved_graph_message_id", default="") or "")
            if moved_graph_message_id:
                source["graphMessageId"] = moved_graph_message_id
                break
        patched = _as_dict(_pick(action_result, "patched", default={}))
        if "subject" in patched:
            email["subject"] = str(patched.get("subject") or "")
        if "categories" in patched:
            email["categories"] = [str(value) for value in _as_list(patched.get("categories")) if str(value).strip()]

        decision = _as_dict(_pick(ingest_result, "routingDecision", "routing_decision", default={}))
        outcome = str(_pick(decision, "outcome", default=""))
        order_run_id = str(_pick(email, "orderRunId", "order_run_id", default="") or "")
        action_status = str(_pick(action_result, "status", default="")).lower()
        pathway = self._pathway_from_email_doc(email)
        stage = "actioned"

        if action_status in {"failed", "partial"}:
            email["status"] = ProcessingStatus.NEEDS_REVIEW.value
            stage = "graphActionsFailed"
        elif outcome == RoutingOutcome.IGNORED.value:
            email["status"] = ProcessingStatus.IGNORED.value
            stage = "ignored"
        elif order_run_id:
            order = self.repository.get("orderRuns", order_run_id)
            order_status = _document_status(order or {})
            if order_status in {
                ProcessingStatus.COMPLETED.value,
                ProcessingStatus.FAILED.value,
                ProcessingStatus.NEEDS_REVIEW.value,
            }:
                email["status"] = order_status
                stage = "orderCompleted" if order_status == ProcessingStatus.COMPLETED.value else "orderNeedsReview"
            else:
                email["status"] = ProcessingStatus.PROCESSING.value
                stage = "orderProcessing"
        elif outcome == RoutingOutcome.KNOWN_CUSTOMER_NON_ORDER.value:
            email["status"] = ProcessingStatus.COMPLETED.value
            stage = "actioned" if action_status == "applied" else "routed"
        elif outcome in {
            RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION.value,
            RoutingOutcome.NEEDS_HUMAN_REVIEW.value,
        }:
            email["status"] = ProcessingStatus.NEEDS_REVIEW.value
            stage = "needsReview"
        else:
            email["status"] = ProcessingStatus.ROUTED.value
            stage = "routed"

        processing = dict(source.get("processing") or {})
        processing.update(
            {
                "stage": stage,
                "status": email["status"],
                "pathway": pathway,
                "updatedAt": now,
            }
        )
        source["processing"] = processing
        email["source"] = source
        email["updatedAt"] = now
        stored_email = self.repository.upsert("emailMessages", email)
        self._upsert_monitor_record_for_email(stored_email)

        if action_status not in {"failed", "partial"}:
            return

        tenant_id = str(_pick(email, "tenantId", "tenant_id", default=""))
        if not tenant_id:
            return
        self._create_exception(
            tenant_id=tenant_id,
            task_type="routing",
            prompt="Resolve Microsoft Graph email action failure.",
            order_run_id=order_run_id or None,
            email_message_id=email_message_id,
            customer_id=_document_customer_id(email),
            correlation_id=str(_pick(email, "correlationId", "correlation_id", default=email_message_id)),
            context={
                "routingDecision": decision,
                "graphEmailActions": action_result,
                "subject": _pick(email, "subject", default=""),
                "sender": _pick(email, "sender", default=""),
                "mailbox": _pick(email, "mailbox", default=""),
            },
            dedupe_key="graphEmailActions",
        )

    def _email_for_exception(self, task: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any] | None:
        email_message_id = str(
            _pick(task, "emailMessageId", "email_message_id", default="")
            or _pick(resolution, "emailMessageId", "email_message_id", default="")
            or ""
        )
        return self.repository.get("emailMessages", email_message_id) if email_message_id else None

    def _mailbox_for_email_document(self, email: dict[str, Any]) -> dict[str, Any] | None:
        mailbox_id = str(_pick(email, "mailboxAccountId", "mailbox_account_id", default="") or "")
        if mailbox_id:
            mailbox = self.repository.get("mailboxAccounts", mailbox_id)
            if mailbox:
                return mailbox
        tenant_id = str(_pick(email, "tenantId", "tenant_id", default=""))
        mailbox_address = _normalized_mailbox_address(str(_pick(email, "mailbox", default="") or ""))
        if not tenant_id or not mailbox_address:
            return None
        for mailbox in self.repository.query_by_tenant("mailboxAccounts", tenant_id):
            if _normalized_mailbox_address(str(_pick(mailbox, "mailboxAddress", "mailbox_address", default="") or "")) == mailbox_address:
                return mailbox
        return None

    @staticmethod
    def _graph_message_id_for_email(email: dict[str, Any]) -> str:
        source = _as_dict(_pick(email, "source", default={}))
        graph_actions = _as_dict(_pick(source, "graphEmailActions", "graph_email_actions", default={}))
        for item in reversed(_as_list(_pick(graph_actions, "applied", default=[]))):
            if isinstance(item, dict) and _pick(item, "action", default="") == "move":
                moved_id = str(_pick(item, "movedGraphMessageId", "moved_graph_message_id", default="") or "")
                if moved_id:
                    return moved_id
        return str(_pick(source, "graphMessageId", "graph_message_id", default="") or "")

    def _manual_graph_email_action(
        self,
        email: dict[str, Any],
        *,
        subject: str = "",
        categories: list[Any] | None = None,
        move_folder: str = "",
    ) -> dict[str, Any]:
        tenant_id = str(_pick(email, "tenantId", "tenant_id", default=""))
        email_id = str(_pick(email, "id", default=""))
        mailbox = self._mailbox_for_email_document(email)
        mailbox_address = str(
            _pick(mailbox or {}, "mailboxAddress", "mailbox_address", default=_pick(email, "mailbox", default="")) or ""
        ).strip().lower()
        graph_message_id = self._graph_message_id_for_email(email)
        result: dict[str, Any] = {
            "status": "skipped",
            "emailMessageId": email_id,
            "graphMessageId": graph_message_id,
            "applied": [],
            "errors": [],
        }
        if not mailbox or not mailbox_address or not graph_message_id:
            result.update({"status": "failed", "reason": "missing Microsoft Graph mailbox or message context"})
            return result

        auth_mode = str(
            _pick(
                dict(_pick(mailbox, "settings", default={}) or {}).get("graphSubscription", {}) or {},
                "authMethod",
                default=os.environ.get("ORDER_PROCESSOR_GRAPH_SUBSCRIPTION_AUTH_MODE", "auto"),
            )
        ).lower() or "auto"
        encoded_mailbox = parse.quote(mailbox_address, safe="")
        encoded_message = parse.quote(graph_message_id, safe="")
        message_url = f"https://graph.microsoft.com/v1.0/users/{encoded_mailbox}/messages/{encoded_message}"
        last_error: MicrosoftGraphError | None = None
        for candidate in self._graph_access_token_candidates(mailbox, auth_mode=auth_mode):
            candidate_result = {**result, "authMethod": candidate["authMethod"], "applied": [], "errors": []}
            current_message_url = message_url
            try:
                patch_payload: dict[str, Any] = {}
                if subject:
                    patch_payload["subject"] = subject
                if categories is not None:
                    patch_payload["categories"] = self._merged_graph_categories(
                        _as_list(_pick(email, "categories", default=[])),
                        categories,
                    )
                if patch_payload:
                    graph_patch(candidate["accessToken"], current_message_url, patch_payload)
                    candidate_result["patched"] = patch_payload
                    candidate_result["applied"].append({"action": "patch", "fields": sorted(patch_payload)})
                if move_folder:
                    destination_id = self._graph_mail_folder_id(
                        candidate["accessToken"],
                        mailbox_address,
                        move_folder,
                        create_missing=True,
                    )
                    if not destination_id:
                        candidate_result["errors"].append(
                            {"action": "move", "folderName": move_folder, "reason": "destination folder not found"}
                        )
                    else:
                        move_response = graph_post(
                            candidate["accessToken"],
                            f"{current_message_url}/move",
                            {"destinationId": destination_id},
                        )
                        moved_graph_message_id = str(_pick(move_response, "id", default="") or "")
                        candidate_result["applied"].append(
                            {
                                "action": "move",
                                "folderName": move_folder,
                                "destinationId": destination_id,
                                "movedGraphMessageId": moved_graph_message_id,
                            }
                        )
                        if moved_graph_message_id:
                            graph_message_id = moved_graph_message_id
                candidate_result["status"] = (
                    "failed" if candidate_result["errors"] and not candidate_result["applied"]
                    else "partial" if candidate_result["errors"]
                    else "applied" if candidate_result["applied"]
                    else "skipped"
                )
                result = candidate_result
                break
            except MicrosoftGraphError as exc:
                last_error = exc
                if exc.status_code not in {401, 403}:
                    result = {
                        **candidate_result,
                        "status": "failed",
                        "errors": [
                            {
                                "action": "manualGraphEmailAction",
                                "reason": str(exc),
                                "statusCode": exc.status_code,
                                "details": exc.details,
                            }
                        ],
                    }
                    break
        else:
            if last_error:
                result.update(
                    {
                        "status": "failed",
                        "reason": str(last_error),
                        "statusCode": last_error.status_code,
                        "details": last_error.details,
                    }
                )
            else:
                result.update({"status": "failed", "reason": "No Microsoft Graph access token is available."})

        source = dict(_pick(email, "source", default={}) or {})
        source["manualGraphEmailActions"] = result
        prior_actions = dict(_pick(source, "graphEmailActions", "graph_email_actions", default={}) or {})
        applied = list(_as_list(_pick(prior_actions, "applied", default=[])))
        applied.extend(_as_list(result.get("applied")))
        patched = dict(_pick(prior_actions, "patched", default={}) or {})
        patched.update(dict(result.get("patched") or {}))
        prior_actions.update({"status": result["status"], "applied": applied, "patched": patched})
        source["graphEmailActions"] = prior_actions
        if graph_message_id:
            source["graphMessageId"] = graph_message_id
        if subject and result["status"] in {"applied", "partial"}:
            email["subject"] = subject
        if categories is not None and result["status"] in {"applied", "partial"}:
            email["categories"] = [str(value) for value in _as_list(_pick(result, "patched", default={}).get("categories")) if str(value).strip()]
        email["source"] = source
        email["updatedAt"] = utc_now()
        stored = self.repository.upsert("emailMessages", email)
        self._upsert_monitor_record_for_email(stored)
        self._audit(
            tenant_id,
            "email.manualGraphAction",
            str(_pick(email, "correlationId", "correlation_id", default=email_id)),
            email_id,
            result,
            customer_id=_document_customer_id(email),
            email_message_id=email_id,
        )
        return result

    @staticmethod
    def _email_action_plan_from_ingest_result(ingest_result: dict[str, Any]) -> dict[str, Any]:
        decision = _as_dict(_pick(ingest_result, "routingDecision", "routing_decision", default={}))
        matched_signals = _as_dict(_pick(decision, "matchedSignals", "matched_signals", default={}))
        return _as_dict(_pick(matched_signals, "emailActions", "email_actions", default={}))

    def _email_action_plan_for_action_key(
        self,
        email: dict[str, Any],
        decision: dict[str, Any],
        action_key: str,
    ) -> dict[str, Any]:
        tenant_id = str(_pick(email, "tenantId", "tenant_id", default="") or "")
        rule_id = str(_pick(decision, "ruleId", "rule_id", default="") or "")
        rule_doc = self.repository.get("routingRules", rule_id) if rule_id else None
        if not tenant_id or not rule_doc:
            return {
                "actionKey": action_key,
                "productionActionsEnabled": True,
                "subject": {},
                "move": {"mode": "none", "enabled": False},
                "categories": [],
            }
        customer_id = str(
            _pick(email, "customerId", "customer_id", default="")
            or _pick(decision, "customerId", "customer_id", default="")
            or ""
        )
        customer_doc = self.repository.get("customers", customer_id) if customer_id else None
        return build_email_action_plan(
            _email_from_payload(email),
            _customer_from_doc(customer_doc) if customer_doc else None,
            _routing_rule_from_doc(rule_doc),
            action_key,
        ) or {
            "actionKey": action_key,
            "productionActionsEnabled": True,
            "subject": {},
            "move": {"mode": "none", "enabled": False},
            "categories": [],
        }

    def _lifecycle_email_action_plan(
        self,
        email: dict[str, Any],
        decision: dict[str, Any],
        action_key: str,
        categories: list[str],
    ) -> dict[str, Any]:
        plan = dict(self._email_action_plan_for_action_key(email, decision, action_key))
        plan["actionKey"] = action_key
        plan["categories"] = [category for category in categories if category]
        if action_key == "orderStart":
            plan["subject"] = {}
        return plan

    def _order_completion_action_plan(
        self,
        email: dict[str, Any],
        decision: dict[str, Any],
        order_result: dict[str, Any] | None,
        *,
        failed: bool = False,
    ) -> dict[str, Any]:
        order_run = _as_dict(_pick(order_result or {}, "orderRun", "order_run", default={}))
        status = str(_pick(order_run, "status", default="") or "")
        unresolved_count = int(_pick(order_result or {}, "unresolvedLineCount", "unresolved_line_count", default=0) or 0)
        if failed or status == ProcessingStatus.FAILED.value:
            return self._lifecycle_email_action_plan(email, decision, "failedOrder", [PROCESSING_EXCEPTION_CATEGORY])

        category = self._csr_order_completion_category(email, order_run, unresolved_count)
        return self._lifecycle_email_action_plan(email, decision, "processedOrder", [category])

    def _csr_order_completion_category(
        self,
        email: dict[str, Any],
        order_run: dict[str, Any],
        unresolved_count: int,
    ) -> str:
        customer_id = str(
            _pick(order_run, "customerId", "customer_id", default="")
            or _pick(email, "customerId", "customer_id", default="")
            or ""
        )
        customer = self.repository.get("customers", customer_id) if customer_id else None
        csr_name = self._customer_csr_category_name(customer or {})
        if not csr_name:
            return PROCESSING_EXCEPTION_CATEGORY
        suffix = "Validate" if unresolved_count > 0 or str(_pick(order_run, "status", default="")) == ProcessingStatus.NEEDS_REVIEW.value else "Review"
        return f"{csr_name} - {suffix}"

    def _non_order_action_category(self, email: dict[str, Any], decision: dict[str, Any]) -> str:
        customer_id = str(
            _pick(email, "customerId", "customer_id", default="")
            or _pick(decision, "customerId", "customer_id", default="")
            or ""
        )
        customer = self.repository.get("customers", customer_id) if customer_id else None
        csr_name = self._customer_csr_category_name(customer or {})
        return f"{csr_name} - Action" if csr_name else PROCESSING_EXCEPTION_CATEGORY

    @staticmethod
    def _customer_csr_category_name(customer: dict[str, Any]) -> str:
        for field_name in ("csrName", "csr_name", "csrFolder", "csr_folder", "csrEmail", "csr_email"):
            value = str(_pick(customer, field_name, default="") or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _merged_graph_categories(existing_categories: list[Any], configured_categories: list[Any]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in [*existing_categories, *configured_categories]:
            category = str(value or "").strip()
            if not category or category in seen:
                continue
            seen.add(category)
            result.append(category)
        return result

    def _graph_mail_folder_id(
        self,
        access_token: str,
        mailbox_address: str,
        folder_path: str,
        *,
        create_missing: bool = False,
    ) -> str:
        parts = [part.strip() for part in re.split(r"[\\/]+", folder_path or "") if part.strip()]
        if not parts:
            return ""
        if len(parts) == 1:
            root_match = self._graph_mail_folder_id_from_parent(
                access_token,
                mailbox_address,
                "",
                parts,
                create_missing=create_missing,
            )
            if root_match:
                return root_match
            return self._graph_mail_folder_id_from_parent(access_token, mailbox_address, "inbox", parts)

        inbox_match = self._graph_mail_folder_id_from_parent(access_token, mailbox_address, "inbox", parts)
        if inbox_match:
            return inbox_match
        return self._graph_mail_folder_id_from_parent(
            access_token,
            mailbox_address,
            "",
            parts,
            create_missing=create_missing,
        )

    def _graph_mail_folder_id_from_parent(
        self,
        access_token: str,
        mailbox_address: str,
        parent_id: str,
        parts: list[str],
        *,
        create_missing: bool = False,
    ) -> str:
        current_parent = parent_id
        current_id = ""
        for part in parts:
            current_id = self._graph_child_mail_folder_id(access_token, mailbox_address, current_parent, part)
            if not current_id:
                if not create_missing:
                    return ""
                current_id = self._graph_create_child_mail_folder(access_token, mailbox_address, current_parent, part)
            current_parent = current_id
        return current_id

    def _graph_child_mail_folder_id(
        self,
        access_token: str,
        mailbox_address: str,
        parent_id: str,
        display_name: str,
    ) -> str:
        encoded_mailbox = parse.quote(mailbox_address, safe="")
        escaped_name = display_name.replace("'", "''")
        query = parse.urlencode(
            {
                "$top": "10",
                "$select": "id,displayName",
                "$filter": f"displayName eq '{escaped_name}'",
            },
            safe="(),'",
        )
        if parent_id:
            encoded_parent = parent_id if parent_id.lower() == "inbox" else parse.quote(parent_id, safe="")
            url = f"https://graph.microsoft.com/v1.0/users/{encoded_mailbox}/mailFolders/{encoded_parent}/childFolders?{query}"
        else:
            url = f"https://graph.microsoft.com/v1.0/users/{encoded_mailbox}/mailFolders?{query}"
        response = graph_get(access_token, url)
        for item in _as_list(response.get("value")):
            if isinstance(item, dict) and str(_pick(item, "displayName", "display_name", default="")) == display_name:
                return str(_pick(item, "id", default=""))
        return ""

    def _graph_create_child_mail_folder(
        self,
        access_token: str,
        mailbox_address: str,
        parent_id: str,
        display_name: str,
    ) -> str:
        encoded_mailbox = parse.quote(mailbox_address, safe="")
        payload = {"displayName": display_name}
        if parent_id:
            encoded_parent = parent_id if parent_id.lower() == "inbox" else parse.quote(parent_id, safe="")
            url = f"https://graph.microsoft.com/v1.0/users/{encoded_mailbox}/mailFolders/{encoded_parent}/childFolders"
        else:
            url = f"https://graph.microsoft.com/v1.0/users/{encoded_mailbox}/mailFolders"
        response = graph_post(access_token, url, payload)
        return str(_pick(response, "id", default=""))

    def _mark_graph_message_processing(
        self,
        access_token: str,
        mailbox_address: str,
        graph_message_id: str,
        existing_categories: list[Any],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"status": "skipped", "category": PROCESSING_CATEGORY}
        if not access_token or not mailbox_address or not graph_message_id:
            result["reason"] = "missing Microsoft Graph message context"
            return result

        normalized_existing = [str(value or "").strip() for value in existing_categories if str(value or "").strip()]
        categories = self._merged_graph_categories(existing_categories, [PROCESSING_CATEGORY])
        if categories == normalized_existing:
            result["reason"] = "processing category already present"
            return result

        encoded_mailbox = parse.quote(mailbox_address, safe="")
        encoded_message = parse.quote(graph_message_id, safe="")
        message_url = f"https://graph.microsoft.com/v1.0/users/{encoded_mailbox}/messages/{encoded_message}"
        try:
            graph_patch(access_token, message_url, {"categories": categories})
            result.update({"status": "applied", "categories": categories})
        except MicrosoftGraphError as exc:
            result.update(
                {
                    "status": "failed",
                    "reason": str(exc),
                    "statusCode": exc.status_code,
                    "details": exc.details,
                }
            )
        return result

    def _ingest_graph_message(
        self,
        access_token: str,
        mailbox: dict[str, Any],
        message: dict[str, Any],
    ) -> dict[str, Any]:
        tenant_id = str(_pick(mailbox, "tenantId", "tenant_id", default=""))
        mailbox_id = str(_pick(mailbox, "id", default=""))
        mailbox_address = str(_pick(mailbox, "mailboxAddress", "mailbox_address", default="")).strip().lower()
        graph_message_id = str(_pick(message, "id", default=""))
        email_id = stable_id(tenant_id, mailbox_id, graph_message_id)
        if self.repository.get("emailMessages", email_id):
            return {"status": "skipped", "reason": "already ingested", "emailMessageId": email_id}

        original_categories = list(_as_list(_pick(message, "categories", default=[])))
        processing_category_result = self._mark_graph_message_processing(
            access_token,
            mailbox_address,
            graph_message_id,
            original_categories,
        )
        current_categories = self._merged_graph_categories(original_categories, [PROCESSING_CATEGORY])

        attachments, source_payload = self._graph_message_attachments(
            access_token,
            mailbox_address,
            graph_message_id,
            bool(message.get("hasAttachments")),
        )
        body = dict(message.get("body") or {})
        body_html = str(body.get("content", "") if str(body.get("contentType", "")).lower() == "html" else "")
        body_text = (
            str(body.get("content", "") if str(body.get("contentType", "")).lower() == "text" else "")
            or _html_to_text(body_html)
            or str(message.get("bodyPreview", "") or "")
        )
        sender = _graph_email_address(message.get("from")) or _graph_email_address(message.get("sender"))
        ingest_payload = {
            "tenantId": tenant_id,
            "id": email_id,
            "mailboxAccountId": mailbox_id,
            "mailbox": mailbox_address,
            "messageId": str(_pick(message, "internetMessageId", default="")) or graph_message_id,
            "subject": str(_pick(message, "subject", default="")),
            "sender": sender,
            "receivedAt": str(_pick(message, "receivedDateTime", default=utc_now())),
            "bodyText": body_text,
            "bodyHtml": body_html,
            "categories": current_categories,
            "attachments": attachments,
            "source": {
                "provider": "microsoftGraph",
                "graphMessageId": graph_message_id,
                "mailboxAccountId": mailbox_id,
                "isRead": bool(message.get("isRead")),
                "webLink": str(_pick(message, "webLink", "web_link", default="")),
                "toRecipients": _graph_recipient_addresses(_pick(message, "toRecipients", "to_recipients", default=[])),
            },
        }
        ingest_result = self.ingest_email(ingest_payload)
        processed = False
        order_run = ingest_result.get("orderRun")
        decision = _as_dict(_pick(ingest_result, "routingDecision", "routing_decision", default={}))
        stored_email = self.repository.get("emailMessages", email_id) or _as_dict(ingest_result.get("emailMessage"))
        email_action_result: dict[str, Any] = {"status": "skipped", "reason": "no lifecycle action"}
        order_start_action_result: dict[str, Any] | None = None
        order_completion_action_result: dict[str, Any] | None = None
        order_processing_result: dict[str, Any] | None = None
        active_graph_message_id = graph_message_id

        if order_run:
            start_plan = self._lifecycle_email_action_plan(
                stored_email,
                decision,
                "orderStart",
                [ORDER_PROCESSING_CATEGORY],
            )
            order_start_action_result = self._apply_graph_email_actions(
                access_token,
                mailbox_address,
                active_graph_message_id,
                ingest_result,
                current_categories,
                action_plan=start_plan,
            )
            self._update_email_after_graph_actions(email_id, ingest_result, order_start_action_result)
            stored_email = self.repository.get("emailMessages", email_id) or stored_email
            active_graph_message_id = self._graph_message_id_for_email(stored_email) or active_graph_message_id

            if self._should_process_polled_order(order_run, source_payload, body_text):
                process_payload = {
                    "tenantId": tenant_id,
                    "emailMessageId": email_id,
                    "customerId": order_run.get("customerId"),
                    "processorProfileId": order_run.get("processorProfileId"),
                    "mailbox": mailbox_address,
                    "sender": sender,
                    "subject": ingest_payload["subject"],
                    "receivedAt": ingest_payload["receivedAt"],
                    "bodyText": body_text,
                    "sourceMetadata": {
                        "provider": "microsoftGraph",
                        "graphMessageId": active_graph_message_id,
                        "mailboxAccountId": mailbox_id,
                    },
                    **source_payload,
                }
                try:
                    order_processing_result = self.process_order(order_run["id"], process_payload)
                    processed = True
                except Exception as exc:
                    stored_email = self.repository.get("emailMessages", email_id) or stored_email
                    failure_plan = self._order_completion_action_plan(
                        stored_email,
                        decision,
                        None,
                        failed=True,
                    )
                    order_completion_action_result = self._apply_graph_email_actions(
                        access_token,
                        mailbox_address,
                        active_graph_message_id,
                        ingest_result,
                        [],
                        action_plan=failure_plan,
                    )
                    self._update_email_after_graph_actions(email_id, ingest_result, order_completion_action_result)
                    return {
                        "status": "failed",
                        "error": True,
                        "reason": str(exc),
                        "emailMessageId": email_id,
                        "graphMessageId": active_graph_message_id,
                        "orderRunId": order_run.get("id") if order_run else "",
                        "processed": False,
                        "processingCategoryResult": processing_category_result,
                        "orderStartActionResult": order_start_action_result,
                        "emailActionResult": order_completion_action_result,
                    }

            stored_email = self.repository.get("emailMessages", email_id) or stored_email
            active_graph_message_id = self._graph_message_id_for_email(stored_email) or active_graph_message_id
            completion_plan = self._order_completion_action_plan(
                stored_email,
                decision,
                order_processing_result,
            )
            order_completion_action_result = self._apply_graph_email_actions(
                access_token,
                mailbox_address,
                active_graph_message_id,
                ingest_result,
                [],
                action_plan=completion_plan,
            )
            self._update_email_after_graph_actions(email_id, ingest_result, order_completion_action_result)
            email_action_result = order_completion_action_result
        else:
            outcome = str(_pick(decision, "outcome", default=""))
            if outcome == RoutingOutcome.KNOWN_CUSTOMER_NON_ORDER.value:
                action_category = self._non_order_action_category(stored_email, decision)
                non_order_plan = self._lifecycle_email_action_plan(
                    stored_email,
                    decision,
                    "nonOrder",
                    [action_category],
                )
                email_action_result = self._apply_graph_email_actions(
                    access_token,
                    mailbox_address,
                    active_graph_message_id,
                    ingest_result,
                    current_categories,
                    action_plan=non_order_plan,
                )
                self._update_email_after_graph_actions(email_id, ingest_result, email_action_result)
            else:
                email_action_result = self._apply_graph_email_actions(
                    access_token,
                    mailbox_address,
                    active_graph_message_id,
                    ingest_result,
                    current_categories,
                )
                self._update_email_after_graph_actions(email_id, ingest_result, email_action_result)
        return {
            "status": "ingested",
            "emailMessageId": email_id,
            "graphMessageId": active_graph_message_id,
            "orderRunId": order_run.get("id") if order_run else "",
            "processed": processed,
            "processingCategoryResult": processing_category_result,
            "orderStartActionResult": order_start_action_result,
            "orderCompletionActionResult": order_completion_action_result,
            "emailActionResult": email_action_result,
        }

    def _graph_message_attachments(
        self,
        access_token: str,
        mailbox_address: str,
        graph_message_id: str,
        has_attachments: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not has_attachments or not graph_message_id:
            return [], {}
        encoded_mailbox = parse.quote(mailbox_address, safe="")
        encoded_message = parse.quote(graph_message_id, safe="")
        url = f"https://graph.microsoft.com/v1.0/users/{encoded_mailbox}/messages/{encoded_message}/attachments"
        response = graph_get(access_token, url)
        attachments: list[dict[str, Any]] = []
        source_payload: dict[str, Any] = {}
        max_bytes = int(os.environ.get("ORDER_PROCESSOR_MAILBOX_POLL_MAX_ATTACHMENT_BYTES", "10000000") or 10000000)
        for item in response.get("value", []):
            if not isinstance(item, dict):
                continue
            metadata = {
                "graphAttachmentId": str(item.get("id", "")),
                "graphODataType": str(item.get("@odata.type", "")),
                "hasContentBytes": bool(item.get("contentBytes")),
            }
            attachments.append(
                {
                    "name": str(item.get("name", "")),
                    "contentType": str(item.get("contentType", "")),
                    "size": int(item.get("size", 0) or 0),
                    "contentId": str(item.get("id", "")),
                    "isInline": bool(item.get("isInline", False)),
                    "sourceUrl": url,
                    "metadata": metadata,
                }
            )
            content_bytes = str(item.get("contentBytes", "") or "")
            if source_payload or not content_bytes or bool(item.get("isInline", False)):
                continue
            if int(item.get("size", 0) or 0) > max_bytes:
                continue
            source_payload = {
                "sourceContentBase64": content_bytes,
                "sourceFileName": str(item.get("name", "")),
                "contentType": str(item.get("contentType", "")),
            }
        return attachments, source_payload

    def _should_process_polled_order(
        self,
        order_run: dict[str, Any],
        source_payload: dict[str, Any],
        body_text: str,
    ) -> bool:
        if source_payload:
            return True
        profile_id = str(_pick(order_run, "processorProfileId", "processor_profile_id", default=""))
        profile = self.repository.get("processorProfiles", profile_id) if profile_id else None
        processor_type = str(_pick(profile or {}, "processorType", "processor_type", default="")).lower()
        normalized = re.sub(r"[^a-z0-9]", "", processor_type)
        return normalized in WEBHOOK_PROCESSOR_TYPES or bool(
            body_text.strip() and normalized in {"emailbody", "customeroverride"}
        )

    @staticmethod
    def _set_email_processing_state(
        email: EmailMessage,
        *,
        stage: str,
        pathway: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        source = dict(email.source or {})
        processing = dict(source.get("processing") or {})
        processing.update(
            {
                "stage": stage,
                "status": email.status.value if isinstance(email.status, ProcessingStatus) else str(email.status),
                "updatedAt": utc_now(),
            }
        )
        if pathway:
            processing["pathway"] = pathway
        if details:
            processing["details"] = details
        source["processing"] = processing
        email.source = source

    @staticmethod
    def _pathway_for_routing_decision(decision: RoutingDecision) -> str:
        phase = str(_pick(decision.matched_signals, "triagePhase", "phase", default="")).strip()
        if phase:
            return phase
        if decision.outcome == RoutingOutcome.KNOWN_ORDER:
            return "orderProcessing"
        if decision.outcome == RoutingOutcome.KNOWN_CUSTOMER_NON_ORDER:
            return "nonOrder"
        if decision.outcome == RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION:
            return "customerIdentification"
        if decision.outcome == RoutingOutcome.NEEDS_HUMAN_REVIEW:
            return "humanReview"
        return str(decision.outcome.value if isinstance(decision.outcome, RoutingOutcome) else decision.outcome)

    @staticmethod
    def _processing_stage_for_routing_decision(decision: RoutingDecision) -> str:
        if decision.outcome == RoutingOutcome.IGNORED:
            return "ignored"
        if decision.outcome in {
            RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION,
            RoutingOutcome.NEEDS_HUMAN_REVIEW,
        }:
            return "needsReview"
        if decision.outcome == RoutingOutcome.KNOWN_ORDER:
            return "orderRouted"
        return "routed"

    def _resolve_mailbox_account(self, email: EmailMessage) -> dict[str, Any] | None:
        if email.mailbox_account_id:
            mailbox = self.repository.get("mailboxAccounts", email.mailbox_account_id)
            if mailbox is not None:
                return mailbox

        mailbox_address = _normalized_mailbox_address(email.mailbox)
        if not mailbox_address:
            return None

        for mailbox in self.repository.query_by_tenant("mailboxAccounts", email.tenant_id):
            if _normalized_mailbox_address(_pick(mailbox, "mailboxAddress", "mailbox_address", default="")) == mailbox_address:
                return mailbox
        return None

    @staticmethod
    def _status_for_routing_decision(decision: RoutingDecision) -> ProcessingStatus:
        if decision.outcome == RoutingOutcome.IGNORED:
            return ProcessingStatus.IGNORED
        if decision.outcome in {
            RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION,
            RoutingOutcome.NEEDS_HUMAN_REVIEW,
        }:
            return ProcessingStatus.NEEDS_REVIEW
        return ProcessingStatus.ROUTED

    def _resolve_processor_profile(self, order: OrderRun, payload: dict[str, Any]) -> ProcessorProfile | None:
        profile_id = _pick(payload, "processorProfileId", "processor_profile_id", default=order.processor_profile_id)
        if profile_id:
            doc = self.repository.get("processorProfiles", profile_id)
            return _processor_profile_from_doc(doc) if doc else None

        requested_type = _pick(payload, "processorType", "processor_type", default=None)
        candidate_docs: list[dict[str, Any]] = []
        if order.customer_id:
            candidate_docs.extend(self.repository.query_by_customer("processorProfiles", order.tenant_id, order.customer_id))
        candidate_docs.extend(self.repository.query_by_customer("processorProfiles", order.tenant_id, GLOBAL_CUSTOMER_ID))
        if not candidate_docs:
            return None

        if requested_type:
            normalized = str(requested_type).lower().replace("-", "").replace("_", "")
            for doc in candidate_docs:
                candidate_type = str(_pick(doc, "processorType", "processor_type", default="")).lower()
                if candidate_type.replace("-", "").replace("_", "") == normalized:
                    return _processor_profile_from_doc(doc)
        return _processor_profile_from_doc(candidate_docs[0])

    def _resolve_output_profiles(
        self,
        order: OrderRun,
        payload: dict[str, Any],
        processor_profile: ProcessorProfile | None,
    ) -> list[OutputProfile]:
        profiles: list[OutputProfile] = []
        explicit = False

        for profile_doc in _as_list(_pick(payload, "outputProfiles", "output_profiles", default=[])):
            if isinstance(profile_doc, dict):
                profiles.append(_output_profile_from_doc(profile_doc))
                explicit = True

        output_profile_ids = [
            str(value)
            for value in _as_list(_pick(payload, "outputProfileIds", "output_profile_ids", default=[]))
            if str(value).strip()
        ]
        single_output_profile_id = _pick(payload, "outputProfileId", "output_profile_id", default=None)
        if single_output_profile_id:
            output_profile_ids.insert(0, str(single_output_profile_id))
        if not output_profile_ids and processor_profile and processor_profile.output_profile_id:
            output_profile_ids.append(processor_profile.output_profile_id)

        for profile_id in output_profile_ids:
            doc = self.repository.get("outputProfiles", profile_id)
            if doc and bool(_pick(doc, "enabled", default=True)):
                profiles.append(_output_profile_from_doc(doc))
                explicit = True

        requested_output_types = [
            str(value)
            for value in _as_list(_pick(payload, "outputTypes", "requestedOutputTypes", default=[]))
            if str(value).strip()
        ]
        for output_type in requested_output_types:
            profiles.append(self._ad_hoc_output_profile(order, output_type, payload))
            explicit = True

        if not explicit:
            if order.customer_id:
                profiles.extend(
                    _output_profile_from_doc(doc)
                    for doc in self.repository.query_by_customer("outputProfiles", order.tenant_id, order.customer_id)
                    if bool(_pick(doc, "enabled", default=True))
                )
            profiles.extend(
                _output_profile_from_doc(doc)
                for doc in self.repository.query_by_customer("outputProfiles", order.tenant_id, GLOBAL_CUSTOMER_ID)
                if bool(_pick(doc, "enabled", default=True))
            )

        return self._dedupe_output_profiles(profiles)

    def _ad_hoc_output_profile(self, order: OrderRun, output_type: str, payload: dict[str, Any]) -> OutputProfile:
        output_settings = dict(_pick(payload, "outputSettings", "output_settings", default={}) or {})
        return OutputProfile(
            id=stable_id(order.tenant_id, order.customer_id or "", order.id, output_type),
            tenant_id=order.tenant_id,
            customer_id=order.customer_id or GLOBAL_CUSTOMER_ID,
            name=f"Requested {output_type}",
            output_type=output_type,
            destination=dict(_pick(payload, "outputDestination", "output_destination", default={}) or {}),
            settings=output_settings,
        )

    @staticmethod
    def _dedupe_output_profiles(profiles: list[OutputProfile]) -> list[OutputProfile]:
        seen: set[str] = set()
        deduped: list[OutputProfile] = []
        for profile in profiles:
            key = profile.id or stable_id(profile.tenant_id, profile.customer_id or "", profile.name, profile.output_type)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(profile)
        return deduped

    @staticmethod
    def _normalized_processor_type(value: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

    def _is_webhook_processor(self, processor_profile: ProcessorProfile | None, payload: dict[str, Any]) -> bool:
        processor_type = (
            _pick(payload, "processorType", "processor_type", default=None)
            or (processor_profile.processor_type if processor_profile else "")
        )
        settings = dict((processor_profile.settings if processor_profile else {}) or {})
        settings.update(dict(_pick(payload, "settings", default={}) or {}))
        return (
            self._normalized_processor_type(processor_type) in WEBHOOK_PROCESSOR_TYPES
            or bool(_pick(payload, "webhookUrl", "webhook_url", default=""))
            or bool(_pick(settings, "webhookUrl", "webhook_url", default=""))
        )

    def _process_webhook_order(
        self,
        order: OrderRun,
        payload: dict[str, Any],
        processor_profile: ProcessorProfile | None,
        observability: dict[str, Any],
    ) -> dict[str, Any]:
        settings = dict((processor_profile.settings if processor_profile else {}) or {})
        settings.update(dict(_pick(payload, "settings", default={}) or {}))
        webhook_url = str(
            _pick(payload, "webhookUrl", "webhook_url", default="")
            or _pick(settings, "webhookUrl", "webhook_url", "url", "endpoint", default="")
            or ""
        ).strip()
        try:
            timeout_seconds = int(_pick(settings, "timeoutSeconds", "timeout_seconds", default=30) or 30)
        except (TypeError, ValueError):
            timeout_seconds = 30
        timeout_seconds = max(5, min(timeout_seconds, 120))

        order.processor_profile_id = (
            _pick(payload, "processorProfileId", "processor_profile_id", default=order.processor_profile_id)
            or (processor_profile.id if processor_profile else None)
        )
        order.processor_type = "powerAutomateWebhook"
        order.processor_version = "phase9-webhook-v1"
        order.source_type = "powerAutomateWebhook"
        order.source_metadata.update(dict(_pick(payload, "sourceMetadata", "source_metadata", default={}) or {}))
        order.source_metadata["webhookProcessor"] = {
            "url": webhook_url,
            "processorProfileId": order.processor_profile_id,
            "processorProfileName": processor_profile.name if processor_profile else "",
            "requestedAt": utc_now(),
        }

        if not webhook_url:
            order.status = ProcessingStatus.FAILED
            order.errors.append({"code": "webhookUrlRequired", "message": "Webhook processor URL is required."})
            order.processing_completed_at = utc_now()
            order.updated_at = utc_now()
            order_doc = self.repository.upsert("orderRuns", to_dict(order))
            self._upsert_monitor_record_for_order(order_doc)
            self._create_exception(
                tenant_id=order.tenant_id,
                task_type="webhookProcessor",
                prompt="Configure the custom webhook processor URL.",
                order_run_id=order.id,
                email_message_id=order.email_message_id,
                customer_id=order.customer_id,
                correlation_id=order.correlation_id,
                context={"processorProfileId": order.processor_profile_id, "observability": observability},
                dedupe_key="webhookUrlRequired",
            )
            return order_doc

        webhook_body = self._webhook_order_payload(order, payload, processor_profile, webhook_url, observability)
        request_body = json.dumps(webhook_body, separators=(",", ":")).encode("utf-8")
        started = utc_now()
        try:
            request = urlrequest.Request(
                webhook_url,
                data=request_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlrequest.urlopen(request, timeout=timeout_seconds) as response:
                response_body = response.read(65536)
                status_code = int(getattr(response, "status", 200) or 200)
                content_type = str(response.headers.get("Content-Type", ""))
        except urlerror.HTTPError as exc:
            response_body = exc.read(65536)
            status_code = int(exc.code or 500)
            content_type = str(exc.headers.get("Content-Type", ""))
            error_message = str(exc)
        except (urlerror.URLError, TimeoutError, OSError) as exc:
            response_body = b""
            status_code = 0
            content_type = ""
            error_message = str(exc)
        else:
            error_message = ""

        response_payload = self._webhook_response_payload(response_body, content_type)
        succeeded = 200 <= status_code < 300
        handoff = {
            "url": webhook_url,
            "status": "accepted" if succeeded else "failed",
            "statusCode": status_code,
            "requestedAt": started,
            "completedAt": utc_now(),
            "request": {
                "orderRunId": order.id,
                "emailMessageId": order.email_message_id,
                "graphMessageId": webhook_body.get("graphMessageId", ""),
                "mailbox": webhook_body.get("mailbox", ""),
            },
            "response": response_payload,
        }
        if error_message:
            handoff["error"] = error_message
        order.source_metadata["webhookProcessor"] = {
            **dict(order.source_metadata.get("webhookProcessor") or {}),
            **handoff,
        }
        order.status = ProcessingStatus.COMPLETED if succeeded else ProcessingStatus.FAILED
        if not succeeded:
            order.errors.append(
                {
                    "code": "webhookProcessorFailed",
                    "message": error_message or f"Webhook processor returned HTTP {status_code}.",
                    "statusCode": status_code,
                }
            )
        order.processing_completed_at = utc_now()
        order.updated_at = utc_now()
        order_doc = self.repository.upsert("orderRuns", to_dict(order))
        self._upsert_monitor_record_for_order(order_doc)
        self._audit(
            order.tenant_id,
            "order.webhookProcessor.accepted" if succeeded else "order.webhookProcessor.failed",
            observability["correlationId"],
            order.id,
            {"handoff": handoff, "observability": observability},
            customer_id=order.customer_id,
            order_run_id=order.id,
            email_message_id=order.email_message_id,
        )
        if not succeeded:
            self._create_exception(
                tenant_id=order.tenant_id,
                task_type="webhookProcessor",
                prompt="Review custom webhook processor failure.",
                order_run_id=order.id,
                email_message_id=order.email_message_id,
                customer_id=order.customer_id,
                correlation_id=order.correlation_id,
                context={"handoff": handoff, "observability": observability},
                dedupe_key="webhookProcessorFailed",
            )
        return order_doc

    def _webhook_order_payload(
        self,
        order: OrderRun,
        payload: dict[str, Any],
        processor_profile: ProcessorProfile | None,
        webhook_url: str,
        observability: dict[str, Any],
    ) -> dict[str, Any]:
        email = self.repository.get("emailMessages", order.email_message_id) if order.email_message_id else None
        customer = self.repository.get("customers", order.customer_id or "") if order.customer_id else None
        source = dict(_pick(email or {}, "source", default={}) or {})
        routing = dict(_pick(email or {}, "routing", default={}) or {})
        return {
            "tenantId": order.tenant_id,
            "orderRunId": order.id,
            "emailMessageId": order.email_message_id,
            "processorProfileId": processor_profile.id if processor_profile else order.processor_profile_id,
            "processorProfileName": processor_profile.name if processor_profile else "",
            "webhookUrl": webhook_url,
            "correlationId": observability["correlationId"],
            "mailboxAccountId": _pick(email or {}, "mailboxAccountId", "mailbox_account_id", default=_pick(payload, "mailboxAccountId", "mailbox_account_id", default="")),
            "mailbox": _pick(email or {}, "mailbox", default=_pick(payload, "mailbox", default="")),
            "graphMessageId": _pick(source, "graphMessageId", "graph_message_id", default=_pick(payload, "graphMessageId", "graph_message_id", default="")),
            "internetMessageId": _pick(email or {}, "messageId", "message_id", default=_pick(payload, "messageId", "message_id", default="")),
            "sender": _pick(email or {}, "sender", default=_pick(payload, "sender", default="")),
            "recipient": "; ".join(str(item) for item in _as_list(_pick(source, "toRecipients", "to_recipients", default=[])) if str(item).strip()),
            "subject": _pick(email or {}, "subject", default=_pick(payload, "subject", default="")),
            "receivedAt": _pick(email or {}, "receivedAt", "received_at", default=_pick(payload, "receivedAt", "received_at", default="")),
            "emailUrl": _pick(source, "webLink", "web_link", default=""),
            "customerId": order.customer_id or "",
            "customerCode": _pick(customer or {}, "customerCode", "customer_code", default=""),
            "customerName": _pick(customer or {}, "name", default=""),
            "routeNumber": _pick(customer or {}, "routeNumber", "route_number", default=""),
            "csrName": _pick(customer or {}, "csrName", "csr_name", default=""),
            "csrFolder": _pick(customer or {}, "csrFolder", "csr_folder", default=""),
            "csrEmail": _pick(customer or {}, "csrEmail", "csr_email", default=""),
            "routing": routing,
            "attachments": [
                {
                    "name": _pick(attachment, "name", default=""),
                    "contentType": _pick(attachment, "contentType", "content_type", default=""),
                    "size": _pick(attachment, "size", default=0),
                }
                for attachment in _as_list(_pick(email or {}, "attachments", default=_pick(payload, "attachments", default=[])))
                if isinstance(attachment, dict)
            ],
            "audit": {
                "createdAt": utc_now(),
                "source": "orderProcessorWebhook",
                "processorVersion": "phase9-webhook-v1",
            },
        }

    @staticmethod
    def _webhook_response_payload(response_body: bytes, content_type: str) -> dict[str, Any]:
        text = response_body.decode("utf-8", errors="replace") if response_body else ""
        if "json" in content_type.lower() and text:
            try:
                return {"contentType": content_type, "json": json.loads(text)}
            except json.JSONDecodeError:
                pass
        return {"contentType": content_type, "text": text[:4000]}

    def process_order(self, order_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.repository.get("orderRuns", order_run_id)
        if existing:
            order = _order_from_doc(existing)
        else:
            order = OrderRun(
                id=order_run_id,
                tenant_id=_pick(payload, "tenantId", "tenant_id", default="default"),
                email_message_id=_pick(payload, "emailMessageId", "email_message_id", default=""),
                customer_id=_pick(payload, "customerId", "customer_id", default=None),
                processor_profile_id=_pick(payload, "processorProfileId", "processor_profile_id", default=None),
            )

        observability = correlation_context(payload, order.correlation_id or order.id)
        order.correlation_id = observability["correlationId"]
        order.source_metadata["observability"] = merge_observability(
            dict(_pick(order.source_metadata, "observability", default={}) or {}),
            observability,
        )
        order.customer_id = _pick(payload, "customerId", "customer_id", default=order.customer_id)
        order.po_number = _pick(payload, "poNumber", "po_number", default=order.po_number)
        order.order_number = _pick(payload, "orderNumber", "order_number", default=order.order_number)
        order.source_file_name = _pick(payload, "sourceFileName", "source_file_name", default=order.source_file_name)
        order.processing_started_at = order.processing_started_at or utc_now()
        self._audit(
            order.tenant_id,
            "order.processingStarted",
            observability["correlationId"],
            order.id,
            {
                "orderRunId": order.id,
                "customerId": order.customer_id,
                "processorProfileId": order.processor_profile_id,
                "sourceFileName": order.source_file_name,
                "observability": observability,
            },
            customer_id=order.customer_id,
            order_run_id=order.id,
        )
        processor_profile = self._resolve_processor_profile(order, payload)
        if self._is_webhook_processor(processor_profile, payload):
            order_doc = self._process_webhook_order(order, payload, processor_profile, observability)
            self._record_order_processor_cost_event(order_doc, payload, processor_profile, observability)
            webhook_handoff = dict(_pick(_as_dict(_pick(order_doc, "sourceMetadata", "source_metadata", default={})), "webhookProcessor", default={}) or {})
            return {
                "orderRun": order_doc,
                "unresolvedLineCount": 0,
                "webhookHandoff": webhook_handoff,
                "observability": observability,
            }

        payload = self._payload_with_google_document_ai_extraction(order, payload, processor_profile, observability)
        order = process_order_payload(order, payload, processor_profile)

        customer_identification_result = None
        if not order.customer_id and order.status != ProcessingStatus.FAILED:
            customer_identification_result = self._identify_customer_for_order(order, payload, observability)
            if customer_identification_result is not None:
                order.source_metadata["customerIdentification"] = _api_value(customer_identification_result)
                if (
                    customer_identification_result.status == MatchStatus.MATCHED
                    and customer_identification_result.customer_id
                ):
                    order.customer_id = customer_identification_result.customer_id
                    self._apply_order_customer_identification(order, customer_identification_result)
                else:
                    self._create_exception(
                        tenant_id=order.tenant_id,
                        task_type="customerIdentification",
                        prompt="Resolve customer match for extracted order.",
                        order_run_id=order.id,
                        email_message_id=order.email_message_id,
                        customer_id=customer_identification_result.customer_id,
                        correlation_id=order.correlation_id,
                        context={
                            "result": _api_value(customer_identification_result),
                            "sourceMetadata": order.source_metadata,
                            "subject": _pick(payload, "subject", default=_pick(order.source_metadata, "subject", default="")),
                            "sender": _pick(payload, "sender", default=_pick(order.source_metadata, "sender", default="")),
                            "orderCustomerIdentificationText": self._order_customer_identification_text(order, payload),
                            "observability": observability,
                        },
                    )

        items = [_item_from_doc(doc) for doc in self.repository.query_by_customer("items", order.tenant_id, GLOBAL_CUSTOMER_ID)]
        if items and order.lines:
            order = validate_order_lines(order, items)

        unresolved = [
            line
            for line in order.lines
            if line.validation_status in {MatchStatus.UNRESOLVED, MatchStatus.POSSIBLE_MATCH}
        ]
        for line in unresolved:
            self._create_exception(
                tenant_id=order.tenant_id,
                task_type="itemValidation",
                prompt="Resolve item match.",
                order_run_id=order.id,
                line_number=line.line_number,
                customer_id=order.customer_id,
                correlation_id=order.correlation_id,
                context={"line": to_dict(line)},
            )

        if order.status == ProcessingStatus.FAILED:
            self._create_exception(
                tenant_id=order.tenant_id,
                task_type="parserFailure",
                prompt="Review order parser failure.",
                order_run_id=order.id,
                customer_id=order.customer_id,
                correlation_id=order.correlation_id,
                context={"errors": order.errors, "sourceMetadata": order.source_metadata},
            )

        if order.status != ProcessingStatus.FAILED:
            order.status = ProcessingStatus.NEEDS_REVIEW if unresolved else ProcessingStatus.COMPLETED
        order.updated_at = utc_now()
        order.output_artifacts = []
        output_profiles = self._resolve_output_profiles(order, payload, processor_profile)
        try:
            order.output_artifacts = generate_order_output_artifacts(
                order,
                output_profiles,
                self.output_artifact_store,
            )
        except Exception as exc:  # pragma: no cover - defensive deployed storage boundary.
            order.errors.append({"code": "outputGenerationFailed", "message": str(exc)})
            order.status = ProcessingStatus.FAILED
            self._create_exception(
                tenant_id=order.tenant_id,
                task_type="outputGeneration",
                prompt="Review output generation failure.",
                order_run_id=order.id,
                customer_id=order.customer_id,
                correlation_id=order.correlation_id,
                context={"error": str(exc), "outputProfiles": [to_dict(profile) for profile in output_profiles]},
            )
        order.processing_completed_at = utc_now()
        order_doc = self.repository.upsert("orderRuns", to_dict(order))
        self._upsert_monitor_record_for_order(order_doc)
        self._audit(
            order.tenant_id,
            "order.processed",
            observability["correlationId"],
            order.id,
            {
                "orderRunId": order.id,
                "customerId": order.customer_id,
                "status": order.status,
                "processorType": order.processor_type,
                "sourceType": order.source_type,
                "lineCount": len(order.lines),
                "unresolvedLineCount": len(unresolved),
                "outputArtifactCount": len(order.output_artifacts),
                "outputArtifacts": order.output_artifacts,
                "outputProfileIds": [profile.id for profile in output_profiles],
                "errors": order.errors,
                "parseWarnings": order.parse_warnings,
                "processingLatencyMs": duration_ms(order.processing_started_at, order.processing_completed_at),
                "observability": observability,
            },
            customer_id=order.customer_id,
            order_run_id=order.id,
        )
        self._record_order_processor_cost_event(order_doc, payload, processor_profile, observability)
        return {"orderRun": _api_value(order), "unresolvedLineCount": len(unresolved), "observability": observability}

    def normalize_spreadsheet(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = dict(_pick(payload, "processorSettings", "processor_settings", "settings", default={}) or {})
        normalization = _normalize_spreadsheet_payload(payload, settings)
        return {"normalization": normalization}

    def extract_spreadsheet_order_lines(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = dict(_pick(payload, "processorSettings", "processor_settings", "settings", default={}) or {})
        normalization = _pick(payload, "normalization", "normalizedWorkbook", default=None)
        if isinstance(normalization, dict) and not isinstance(_pick(payload, "layout", "spreadsheetLayout", default=None), dict):
            payload = {
                **payload,
                "layout": analyze_spreadsheet_layout(normalization, payload, settings),
            }
        extraction = _extract_spreadsheet_order_lines(payload, settings)
        return {"extraction": extraction}

    def extract_email_body_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = dict(_pick(payload, "processorSettings", "processor_settings", "settings", default={}) or {})
        extraction = _extract_email_body_order(payload, settings)
        return {"extraction": extraction}

    def extract_google_document_ai_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = self._processor_settings_for_payload(payload, None)
        response = self.google_document_ai_client.process_pdf(
            payload,
            repository=self.repository,
            tenant_id=str(_pick(payload, "tenantId", "tenant_id", default="default") or "default"),
            settings=settings,
        )
        extraction = _extract_google_document_ai_order(response, payload, settings)
        return {"extraction": extraction}

    def _payload_with_google_document_ai_extraction(
        self,
        order: OrderRun,
        payload: dict[str, Any],
        processor_profile: ProcessorProfile | None,
        observability: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._should_run_google_document_ai(payload, processor_profile):
            return payload
        settings = self._processor_settings_for_payload(payload, processor_profile)
        try:
            response = self.google_document_ai_client.process_pdf(
                payload,
                repository=self.repository,
                tenant_id=order.tenant_id,
                settings=settings,
            )
            extraction = _extract_google_document_ai_order(response, payload, settings)
        except Exception as exc:
            extraction = {
                "schemaVersion": "google-document-ai-order-v1",
                "status": "failed",
                "purchaseOrder": "",
                "lineCount": 0,
                "lines": [],
                "customerIdentification": {},
                "headers": {},
                "rawDocument": {},
                "googleDocumentAi": {
                    "processor": "googleDocumentAi",
                    "error": type(exc).__name__,
                },
                "warnings": [],
                "errors": [{"code": "googleDocumentAiFailed", "message": str(exc)}],
                "requiresHumanReview": True,
                "observability": observability,
            }
        return {
            **payload,
            "googleDocumentAiExtraction": extraction,
        }

    def _should_run_google_document_ai(
        self,
        payload: dict[str, Any],
        processor_profile: ProcessorProfile | None,
    ) -> bool:
        if any(
            _pick(payload, key, default=None) is not None
            for key in (
                "googleDocumentAiExtraction",
                "google_document_ai_extraction",
                "googleDocumentAiResult",
                "google_document_ai_result",
                "googleDocumentAiResponse",
                "google_document_ai_response",
                "documentIntelligenceResult",
                "document_intelligence_result",
                "azureDocumentIntelligenceResult",
                "azure_document_intelligence_result",
                "extractedText",
                "extracted_text",
            )
        ):
            return False
        if not self._payload_has_pdf_source(payload):
            return False
        settings = self._processor_settings_for_payload(payload, processor_profile)
        processor_type = str(
            _pick(payload, "processorType", "processor_type", default=None)
            or (processor_profile.processor_type if processor_profile else None)
            or settings.get("processorType")
            or settings.get("processor_type")
            or ""
        )
        normalized = re.sub(r"[^a-z0-9]", "", processor_type.lower())
        return normalized in {"pdf", "googledocumentai", "googledocumentaipdf", "orderprocessgoogledocumentaipdf"}

    def _processor_settings_for_payload(
        self,
        payload: dict[str, Any],
        processor_profile: ProcessorProfile | None,
    ) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        if processor_profile:
            settings.update(processor_profile.settings)
            settings.setdefault("profileName", processor_profile.name)
        settings.update(dict(_pick(payload, "processorSettings", "processor_settings", "settings", default={}) or {}))
        return settings

    @staticmethod
    def _payload_has_pdf_source(payload: dict[str, Any]) -> bool:
        if any(
            key in payload
            for key in (
                "sourceContent",
                "source_content",
                "sourceContentBase64",
                "source_content_base64",
                "content",
                "contentBase64",
                "content_base64",
            )
        ):
            return True
        for item in _as_list(_pick(payload, "attachments", default=[])):
            if not isinstance(item, dict):
                continue
            name = str(_pick(item, "name", "fileName", "file_name", default="") or "").lower()
            content_type = str(_pick(item, "contentType", "content_type", "mimeType", default="") or "").lower()
            if name.endswith(".pdf") or content_type == "application/pdf":
                return True
        return False

    def _identify_customer_for_order(
        self,
        order: OrderRun,
        payload: dict[str, Any],
        observability: dict[str, Any],
    ) -> CustomerIdentificationResult:
        email = self._order_customer_identification_email(order, payload)
        customers = [_customer_from_doc(doc) for doc in self.repository.query_by_tenant("customers", order.tenant_id)]
        aliases = [
            _customer_alias_from_doc(doc)
            for doc in self.repository.query_by_tenant("customerAliases", order.tenant_id)
        ]
        result = identify_customer_from_email(
            email,
            customers,
            aliases=aliases,
            vector_search=self.customer_vector_search,
            ai_identifier=self.customer_ai_identifier,
            confidence_threshold=DEFAULT_CUSTOMER_CONFIDENCE_THRESHOLD,
        )
        self._record_customer_identification_cost_event(
            email,
            result,
            {
                **payload,
                "processorType": "customerIdentification",
                "operationType": "orderCustomerIdentification",
                "orderProcessorType": order.processor_type,
                "orderRunId": order.id,
            },
            observability,
        )
        self._audit(
            order.tenant_id,
            "order.customerIdentified",
            observability["correlationId"],
            order.id,
            {
                "orderRunId": order.id,
                "emailMessageId": order.email_message_id,
                "customerId": result.customer_id,
                "status": result.status,
                "matchMethod": result.match_method,
                "confidence": result.confidence,
                "result": _api_value(result),
                "observability": observability,
            },
            customer_id=result.customer_id,
            order_run_id=order.id,
            email_message_id=order.email_message_id,
        )
        return result

    def _apply_order_customer_identification(
        self,
        order: OrderRun,
        result: CustomerIdentificationResult,
    ) -> None:
        existing_email = self.repository.get("emailMessages", order.email_message_id)
        if existing_email is None:
            return
        existing_email["customerId"] = result.customer_id
        existing_email["customerIdentification"] = _api_value(result)
        routing = dict(_pick(existing_email, "routing", default={}) or {})
        matched_signals = dict(_pick(routing, "matchedSignals", "matched_signals", default={}) or {})
        matched_signals["orderCustomerIdentification"] = _api_value(result)
        routing["matchedSignals"] = matched_signals
        existing_email["routing"] = routing
        existing_email["updatedAt"] = utc_now()
        stored_email = self.repository.upsert("emailMessages", existing_email)
        self._upsert_monitor_record_for_email(stored_email, order=to_dict(order))

    def _order_customer_identification_email(self, order: OrderRun, payload: dict[str, Any]) -> EmailMessage:
        stored_email = self.repository.get("emailMessages", order.email_message_id) or {}
        email_payload = {
            "tenantId": order.tenant_id,
            "id": order.email_message_id or _pick(payload, "emailMessageId", "email_message_id", default=order.id),
            "messageId": _pick(stored_email, "messageId", "message_id", default=_pick(payload, "messageId", "message_id", default=order.email_message_id)),
            "mailbox": _pick(stored_email, "mailbox", default=_pick(payload, "mailbox", default=_pick(order.source_metadata, "mailbox", default=""))),
            "sender": _pick(stored_email, "sender", default=_pick(payload, "sender", default=_pick(order.source_metadata, "sender", default=""))),
            "subject": _pick(stored_email, "subject", default=_pick(payload, "subject", default=_pick(order.source_metadata, "subject", default=""))),
            "receivedAt": _pick(stored_email, "receivedAt", "received_at", default=_pick(payload, "receivedAt", "received_at", default=_pick(order.source_metadata, "receivedAt", default=utc_now()))),
            "bodyText": self._order_customer_identification_text(order, payload),
            "bodyHtml": _pick(stored_email, "bodyHtml", "body_html", default=_pick(payload, "bodyHtml", "body_html", default="")),
            "attachments": list(
                _as_list(_pick(stored_email, "attachments", default=_pick(payload, "attachments", default=[])))
            ),
            "status": ProcessingStatus.PROCESSING,
            "mailboxAccountId": _pick(stored_email, "mailboxAccountId", "mailbox_account_id", default=_pick(payload, "mailboxAccountId", "mailbox_account_id", default=None)),
            "customerId": order.customer_id,
            "orderRunId": order.id,
            "correlationId": order.correlation_id,
            "source": {
                **dict(_pick(stored_email, "source", default={}) or {}),
                "orderCustomerIdentification": {
                    "orderRunId": order.id,
                    "processorType": order.processor_type,
                    "sourceType": order.source_type,
                },
            },
        }
        return _email_from_payload(email_payload)

    def _order_customer_identification_text(self, order: OrderRun, payload: dict[str, Any]) -> str:
        parts: list[str] = []
        for label, value in (
            ("email subject", _pick(payload, "subject", default=_pick(order.source_metadata, "subject", default=""))),
            ("email sender", _pick(payload, "sender", default=_pick(order.source_metadata, "sender", default=""))),
            ("email body", _pick(payload, "bodyText", "body_text", default="")),
            ("email html", _html_to_text(str(_pick(payload, "bodyHtml", "body_html", default="") or ""))),
            ("source file", order.source_file_name),
            ("purchase order", order.po_number),
        ):
            if str(value or "").strip():
                parts.append(f"{label}: {value}")

        stored_email = self.repository.get("emailMessages", order.email_message_id) or {}
        for label, value in (
            ("stored email subject", _pick(stored_email, "subject", default="")),
            ("stored email sender", _pick(stored_email, "sender", default="")),
            ("stored email body", _pick(stored_email, "bodyText", "body_text", default="")),
        ):
            if str(value or "").strip():
                parts.append(f"{label}: {value}")

        customer_metadata = self._order_customer_metadata_fragments(order.source_metadata)
        parts.extend(customer_metadata)
        for line in order.lines[:5]:
            raw = dict(line.raw or {})
            values = raw.get("values") if isinstance(raw.get("values"), dict) else raw
            parts.append(
                "order row: "
                + json.dumps(
                    {
                        "lineNumber": line.line_number,
                        "description": line.description,
                        "item": line.provided_item_number,
                        "upc": line.provided_upc,
                        "quantity": line.quantity,
                        "raw": values,
                    },
                    ensure_ascii=True,
                    default=str,
                )
            )
        return "\n".join(dict.fromkeys(part for part in parts if part.strip()))[:16000]

    def _order_customer_metadata_fragments(self, metadata: dict[str, Any]) -> list[str]:
        fragments: list[str] = []
        spreadsheet = _as_dict(_pick(metadata, "spreadsheet", default={}))
        customer_identification = _as_dict(_pick(spreadsheet, "customerIdentification", default={}))
        search_text = str(_pick(customer_identification, "customerSearchText", "customer_search_text", default="") or "")
        if search_text.strip():
            fragments.append(f"extracted order customer search text:\n{search_text}")
        signals = _as_dict(_pick(customer_identification, "signals", default={}))
        if signals:
            fragments.append("extracted order customer signals: " + json.dumps(signals, ensure_ascii=True, default=str))
        for group in _as_list(_pick(spreadsheet, "orderGroups", "order_groups", default=[])):
            if not isinstance(group, dict):
                continue
            group_text = str(_pick(group, "customerSearchText", "customer_search_text", default="") or "")
            if group_text.strip():
                fragments.append(f"order group customer search text:\n{group_text}")

        email_body = _as_dict(_pick(metadata, "emailBody", "email_body", default={}))
        email_body_customer_identification = _as_dict(_pick(email_body, "customerIdentification", default={}))
        email_search_text = str(
            _pick(email_body_customer_identification, "customerSearchText", "customer_search_text", default="") or ""
        )
        if email_search_text.strip():
            fragments.append(f"email body customer search text:\n{email_search_text}")
        email_signals = _as_dict(_pick(email_body_customer_identification, "signals", default={}))
        if email_signals:
            fragments.append("email body customer signals: " + json.dumps(email_signals, ensure_ascii=True, default=str))

        google_document_ai = _as_dict(_pick(metadata, "googleDocumentAi", "google_document_ai", default={}))
        google_customer_identification = _as_dict(_pick(google_document_ai, "customerIdentification", default={}))
        google_search_text = str(
            _pick(google_customer_identification, "customerSearchText", "customer_search_text", default="") or ""
        )
        if google_search_text.strip():
            fragments.append(f"google document ai customer search text:\n{google_search_text}")
        google_signals = _as_dict(_pick(google_customer_identification, "signals", default={}))
        if google_signals:
            fragments.append("google document ai customer signals: " + json.dumps(google_signals, ensure_ascii=True, default=str))

        for key in ("customerIdentification", "documentCustomerIdentification", "document_intelligence", "documentIntelligence"):
            value = _pick(metadata, key, default=None)
            if isinstance(value, dict):
                text = str(_pick(value, "customerSearchText", "customer_search_text", "shipTo", "ship_to", default="") or "")
                if text.strip():
                    fragments.append(f"{key}: {text}")
        return fragments

    def identify_customer(self, payload: dict[str, Any]) -> dict[str, Any]:
        email_payload = _pick(payload, "emailMessage", "email", default=payload)
        email = _email_from_payload(email_payload)
        observability = correlation_context(payload, email.correlation_id or email.id)
        email.correlation_id = observability["correlationId"]
        customers_payload = _pick(payload, "customers", default=None)
        customers = (
            [_customer_from_doc(item) for item in customers_payload]
            if customers_payload is not None
            else [_customer_from_doc(doc) for doc in self.repository.query_by_tenant("customers", email.tenant_id)]
        )
        aliases_payload = _pick(payload, "customerAliases", "aliases", default=None)
        aliases = (
            [_customer_alias_from_doc(item) for item in aliases_payload]
            if aliases_payload is not None
            else [
                _customer_alias_from_doc(doc)
                for doc in self.repository.query_by_tenant("customerAliases", email.tenant_id)
            ]
        )
        confidence_threshold = float(
            _pick(
                payload,
                "confidenceThreshold",
                "confidence_threshold",
                default=DEFAULT_CUSTOMER_CONFIDENCE_THRESHOLD,
            )
            or DEFAULT_CUSTOMER_CONFIDENCE_THRESHOLD
        )
        result = identify_customer_from_email(
            email,
            customers,
            aliases=aliases,
            vector_search=self.customer_vector_search,
            ai_identifier=self.customer_ai_identifier,
            confidence_threshold=confidence_threshold,
        )
        self._record_customer_identification_cost_event(email, result, payload, observability)

        if result.status == MatchStatus.MATCHED and result.customer_id:
            self._apply_customer_identification(email, result)

        exception_task = None
        if result.status != MatchStatus.MATCHED:
            exception_task = self._create_exception(
                tenant_id=email.tenant_id,
                task_type="customerIdentification",
                prompt="Resolve customer match.",
                email_message_id=email.id,
                customer_id=result.customer_id,
                correlation_id=email.correlation_id,
                context={
                    "result": _api_value(result),
                    "subject": email.subject,
                    "sender": email.sender,
                    "mailbox": email.mailbox,
                    "confidenceThreshold": confidence_threshold,
                    "observability": observability,
                },
            )
        stored_email = self.repository.get("emailMessages", email.id)
        order_run_id = _pick(stored_email or {}, "orderRunId", "order_run_id", default=email.order_run_id)
        self._audit(
            email.tenant_id,
            "customer.identified",
            email.correlation_id or email.id,
            email.id,
            {
                "result": _api_value(result),
                "threshold": confidence_threshold,
                "exceptionTaskId": exception_task["id"] if exception_task else None,
                "emailMessageId": email.id,
                "orderRunId": order_run_id,
                "customerId": result.customer_id,
                "matchMethod": result.match_method,
                "confidence": result.confidence,
                "extractedSignals": result.extracted_signals,
                "observability": observability,
            },
            customer_id=result.customer_id,
            order_run_id=order_run_id,
            email_message_id=email.id,
        )
        return {"result": _api_value(result), "exceptionTask": exception_task, "observability": observability}

    def _apply_customer_identification(
        self,
        email: EmailMessage,
        result: CustomerIdentificationResult,
    ) -> None:
        existing_email = self.repository.get("emailMessages", email.id)
        if existing_email is not None:
            existing_email["customerId"] = result.customer_id
            existing_email["customerIdentification"] = _api_value(result)
            existing_email["updatedAt"] = utc_now()
            stored_email = self.repository.upsert("emailMessages", existing_email)
            self._upsert_monitor_record_for_email(stored_email)

            order_run_id = _pick(existing_email, "orderRunId", "order_run_id", default=email.order_run_id)
            if order_run_id:
                existing_order = self.repository.get("orderRuns", order_run_id)
                if existing_order is not None:
                    existing_order["customerId"] = result.customer_id
                    existing_order["updatedAt"] = utc_now()
                    order_doc = self.repository.upsert("orderRuns", existing_order)
                    self._upsert_monitor_record_for_order(order_doc)
        elif email.order_run_id:
            existing_order = self.repository.get("orderRuns", email.order_run_id)
            if existing_order is not None:
                existing_order["customerId"] = result.customer_id
                existing_order["updatedAt"] = utc_now()
                order_doc = self.repository.upsert("orderRuns", existing_order)
                self._upsert_monitor_record_for_order(order_doc)

    def validate_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        customer_id = _pick(payload, "customerId", "customer_id", default="")
        observability = correlation_context(
            payload,
            _pick(payload, "orderRunId", "order_run_id", default="") or stable_id(tenant_id, customer_id, "itemValidation"),
        )
        row_context = self._item_validation_row_context(payload)
        items_payload = _pick(payload, "items", default=None)
        items = (
            [_item_from_doc(item) for item in items_payload]
            if items_payload is not None
            else (
                [_item_from_doc(doc) for doc in self.repository.query_by_customer("items", tenant_id, GLOBAL_CUSTOMER_ID)]
            )
        )
        result = validate_item_line(
            tenant_id=tenant_id,
            customer_id=customer_id,
            provided_item_number=_pick(
                payload,
                "providedItemNumber",
                "provided_item_number",
                "itemNumber",
                "item_number",
                default="",
            ),
            provided_upc=_pick(payload, "providedUpc", "provided_upc", "upc", "barcode", default=""),
            description=_pick(payload, "description", "itemDescription", "item_description", default=""),
            items=items,
            confidence_threshold=float(
                _pick(payload, "confidenceThreshold", "confidence_threshold", default=DEFAULT_CONFIDENCE_THRESHOLD)
                or DEFAULT_CONFIDENCE_THRESHOLD
            ),
            row_context=row_context,
            candidate_limit=int(
                _pick(payload, "candidateLimit", "candidate_limit", default=DEFAULT_CANDIDATE_LIMIT)
                or DEFAULT_CANDIDATE_LIMIT
            ),
            possible_match_threshold=float(
                _pick(
                    payload,
                    "possibleMatchThreshold",
                    "possible_match_threshold",
                    default=DEFAULT_POSSIBLE_MATCH_THRESHOLD,
                )
                or DEFAULT_POSSIBLE_MATCH_THRESHOLD
            ),
        )
        updated_line = self._apply_item_validation_to_order_line(payload, result)
        exception_task = None
        if result.status != MatchStatus.MATCHED:
            exception_task = self._create_exception(
                tenant_id=tenant_id,
                task_type="itemValidation",
                prompt="Resolve item match.",
                order_run_id=_pick(payload, "orderRunId", "order_run_id", default=None),
                line_number=int(_pick(payload, "lineNumber", "line_number", default=0) or 0) or None,
                customer_id=customer_id,
                correlation_id=observability["correlationId"],
                context={
                    "result": to_dict(result),
                    "request": self._item_validation_request_context(payload),
                    "rowContext": row_context,
                    "observability": observability,
                },
                dedupe_key=self._item_validation_dedupe_key(payload, result),
            )
        self._audit(
            tenant_id,
            "item.validated",
            observability["correlationId"],
            result.matched_internal_item_number or _pick(payload, "orderRunId", "order_run_id", default=""),
            {
                "orderRunId": _pick(payload, "orderRunId", "order_run_id", default=""),
                "lineNumber": int(_pick(payload, "lineNumber", "line_number", default=0) or 0) or None,
                "customerId": customer_id,
                "status": result.status,
                "matchedInternalItemNumber": result.matched_internal_item_number,
                "matchedItemId": result.matched_item_id,
                "matchMethod": result.match_method,
                "confidence": result.confidence,
                "candidateCount": len(result.candidates),
                "candidates": result.candidates,
                "exceptionTaskId": exception_task["id"] if exception_task else None,
                "observability": observability,
            },
            customer_id=customer_id,
            order_run_id=_pick(payload, "orderRunId", "order_run_id", default=None),
        )
        response = {"result": _api_value(result), "exceptionTask": exception_task, "observability": observability}
        if updated_line is not None:
            response["updatedOrderLine"] = _api_value(updated_line)
        return response

    def _item_validation_row_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        row_context = dict(_pick(payload, "rowContext", "row_context", default={}) or {})
        line_payload = _pick(payload, "line", "orderLine", "order_line", default=None)
        if isinstance(line_payload, dict):
            row_context.update(dict(_pick(line_payload, "raw", default=line_payload) or {}))
            for key in (
                "providedItemNumber",
                "provided_item_number",
                "providedUpc",
                "provided_upc",
                "description",
                "quantity",
            ):
                value = _pick(line_payload, key, default=None)
                if value is not None and key not in row_context:
                    row_context[key] = value
        return row_context

    def _apply_item_validation_to_order_line(
        self,
        payload: dict[str, Any],
        result: ItemValidationResult,
    ) -> OrderLine | None:
        order_run_id = _pick(payload, "orderRunId", "order_run_id", default=None)
        line_number = int(_pick(payload, "lineNumber", "line_number", default=0) or 0)
        if not order_run_id or not line_number:
            return None

        existing_order = self.repository.get("orderRuns", order_run_id)
        if existing_order is None:
            return None

        order = _order_from_doc(existing_order)
        updated_line = None
        for line in order.lines:
            if line.line_number != line_number:
                continue
            line.validation_status = result.status
            line.validation_confidence = result.confidence
            line.validation_method = result.match_method
            line.validation_candidates = result.candidates
            line.matched_internal_item_number = result.matched_internal_item_number
            if result.status == MatchStatus.MATCHED:
                line.validation_errors = []
            else:
                line.validation_errors = [
                    {
                        "code": "unresolvedItem",
                        "message": result.unresolved_reason or "Item validation requires review.",
                    }
                ]
            updated_line = line
            break

        if updated_line is None:
            return None

        unresolved = [
            line
            for line in order.lines
            if line.validation_status in {MatchStatus.UNRESOLVED, MatchStatus.POSSIBLE_MATCH}
        ]
        if order.status != ProcessingStatus.FAILED:
            order.status = ProcessingStatus.NEEDS_REVIEW if unresolved else ProcessingStatus.COMPLETED
        order.updated_at = utc_now()
        order_doc = self.repository.upsert("orderRuns", to_dict(order))
        self._upsert_monitor_record_for_order(order_doc)
        return updated_line

    def _item_validation_request_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        safe_keys = [
            "tenantId",
            "tenant_id",
            "customerId",
            "customer_id",
            "providedItemNumber",
            "provided_item_number",
            "itemNumber",
            "item_number",
            "providedUpc",
            "provided_upc",
            "upc",
            "barcode",
            "description",
            "orderRunId",
            "order_run_id",
            "lineNumber",
            "line_number",
            "confidenceThreshold",
            "confidence_threshold",
            "candidateLimit",
            "candidate_limit",
        ]
        return {key: payload[key] for key in safe_keys if key in payload}

    def _item_validation_dedupe_key(
        self,
        payload: dict[str, Any],
        result: ItemValidationResult,
    ) -> str:
        order_run_id = _pick(payload, "orderRunId", "order_run_id", default="")
        line_number = _pick(payload, "lineNumber", "line_number", default="")
        if order_run_id or line_number:
            return ""
        return stable_id(
            _pick(payload, "tenantId", "tenant_id", default="default"),
            _pick(payload, "customerId", "customer_id", default=""),
            _pick(payload, "providedItemNumber", "provided_item_number", "itemNumber", default=""),
            _pick(payload, "providedUpc", "provided_upc", "upc", default=""),
            _pick(payload, "description", default=""),
            result.unresolved_reason or result.match_method,
        )

    def import_customers(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        profile = import_profile_from_payload(payload, CUSTOMER_IMPORT_TYPE, "rows")
        parsed = parse_import_rows(payload, profile)
        validation = validate_customer_rows(parsed.rows, profile.field_map)
        imported_at = utc_now()
        source_metadata = self._import_source_metadata(payload, profile, imported_at)
        archive = self.source_archive.archive_rows(
            tenant_id=tenant_id,
            import_type=CUSTOMER_IMPORT_TYPE,
            rows=parsed.rows,
            metadata=source_metadata,
        )
        source_metadata.update(
            {
                "sourceRowsBlobUrl": archive.blob_url,
                "sourceRowsChecksum": archive.checksum,
                "importRunId": archive.import_run_id,
                "archivedAt": archive.archived_at,
            }
        )

        imported: list[CustomerProfile] = []
        aliases: list[CustomerAlias] = []
        created_count = 0
        updated_count = 0
        for row_index, row in validation.valid_rows:
            row_metadata = {**source_metadata, "rowIndex": row_index}
            customer = normalize_customer_row(tenant_id, row, profile.field_map, row_metadata)
            customer = apply_customer_embedding(customer, self.import_embedding_client)
            if self.repository.get("customers", customer.id):
                updated_count += 1
            else:
                created_count += 1
            self.repository.upsert("customers", to_dict(customer))
            imported.append(customer)

            for alias in normalize_customer_alias_rows(tenant_id, customer, row, profile.field_map, row_metadata):
                self.repository.upsert("customerAliases", to_dict(alias))
                aliases.append(alias)

        schedule = refresh_schedule_for_import(CUSTOMER_IMPORT_TYPE, profile, payload, imported_at)
        errors = [*parsed.errors, *validation.errors]
        result = {
            "importType": CUSTOMER_IMPORT_TYPE,
            "importRunId": archive.import_run_id,
            "sourceRowsBlobUrl": archive.blob_url,
            "sourceRowsChecksum": archive.checksum,
            "sourceRowCount": archive.row_count,
            "parserModule": parsed.parser_module,
            "importedCount": len(imported),
            "createdCount": created_count,
            "updatedCount": updated_count,
            "skippedCount": len(validation.errors),
            "errorCount": len(errors),
            "errors": errors,
            "refreshPolicy": _api_value(schedule),
            "customers": [_api_value(item) for item in imported],
            "customerAliases": [_api_value(item) for item in aliases],
        }
        result["csrDirectory"] = self._refresh_tenant_csr_directory(tenant_id)
        result["mailboxCategorySync"] = self._sync_mailbox_categories_after_customer_import(
            tenant_id,
            result["csrDirectory"],
            payload,
        )
        observability = correlation_context(payload, archive.import_run_id)
        result["observability"] = observability
        if self.customer_vector_store_manager and len(imported) > 0:
            vector_store_result = self.customer_vector_store_manager.rotate_after_customer_import(
                tenant_id,
                result,
                payload,
            )
            result["customerVectorStore"] = _api_value(vector_store_result)
            self._audit(
                tenant_id,
                "customers.vectorStoreRotated",
                observability["correlationId"],
                archive.import_run_id,
                {"observability": observability, **vector_store_result},
            )
        self._audit(
            tenant_id,
            "customers.imported",
            observability["correlationId"],
            archive.import_run_id,
            _compact_import_audit_details(result, ["customers", "customerAliases"]),
        )
        self._clear_console_cache(tenant_id)
        return result

    def import_items(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        customer_id = GLOBAL_CUSTOMER_ID
        profile = import_profile_from_payload(payload, ITEM_IMPORT_TYPE, "rows")
        parsed = parse_import_rows(payload, profile)
        validation = validate_item_rows(parsed.rows, profile.field_map)
        imported_at = utc_now()
        source_metadata = self._import_source_metadata(payload, profile, imported_at)
        archive = self.source_archive.archive_rows(
            tenant_id=tenant_id,
            import_type=ITEM_IMPORT_TYPE,
            rows=parsed.rows,
            metadata={**source_metadata, "customerId": customer_id},
        )
        source_metadata.update(
            {
                "sourceRowsBlobUrl": archive.blob_url,
                "sourceRowsChecksum": archive.checksum,
                "importRunId": archive.import_run_id,
                "archivedAt": archive.archived_at,
            }
        )

        imported: list[ItemRecord] = []
        created_count = 0
        updated_count = 0
        legacy_rekeyed_count = 0
        existing_items_by_number = self._existing_item_documents_by_number(tenant_id)
        for row_index, row in validation.valid_rows:
            row_metadata = {**source_metadata, "rowIndex": row_index, "customerId": customer_id}
            item = normalize_item_row(tenant_id, customer_id, row, profile.field_map, row_metadata)
            item = apply_item_embedding(item, self.import_embedding_client)
            existing_item = self.repository.get("items", item.id)
            legacy_matches = [
                document
                for document in existing_items_by_number.get(_item_identity_key(item.internal_item_number), [])
                if str(_pick(document, "id", default="")) != item.id
            ]
            if existing_item or legacy_matches:
                updated_count += 1
            else:
                created_count += 1
            self.repository.upsert("items", to_dict(item))
            legacy_ids = {
                scoped_item_record_id(tenant_id, customer_id, item.internal_item_number),
                legacy_item_record_id(tenant_id, customer_id, item.internal_item_number, item.upc),
                *[str(_pick(document, "id", default="")) for document in legacy_matches],
            }
            delete = getattr(self.repository, "delete", None)
            if callable(delete):
                for legacy_id in legacy_ids:
                    if legacy_id and legacy_id != item.id and self.repository.get("items", legacy_id) and delete("items", legacy_id):
                        legacy_rekeyed_count += 1
            item_key = _item_identity_key(item.internal_item_number)
            if item_key:
                existing_items_by_number[item_key] = [to_dict(item)]
            imported.append(item)

        schedule = refresh_schedule_for_import(ITEM_IMPORT_TYPE, profile, payload, imported_at)
        errors = [*parsed.errors, *validation.errors]
        result = {
            "importType": ITEM_IMPORT_TYPE,
            "importRunId": archive.import_run_id,
            "customerId": customer_id,
            "customerCode": "",
            "sourceRowsBlobUrl": archive.blob_url,
            "sourceRowsChecksum": archive.checksum,
            "sourceRowCount": archive.row_count,
            "parserModule": parsed.parser_module,
            "importedCount": len(imported),
            "createdCount": created_count,
            "updatedCount": updated_count,
            "legacyRekeyedCount": legacy_rekeyed_count,
            "skippedCount": len(validation.errors),
            "errorCount": len(errors),
            "errors": errors,
            "refreshPolicy": _api_value(schedule),
            "items": [_api_value(item) for item in imported],
        }
        observability = correlation_context(payload, archive.import_run_id)
        result["observability"] = observability
        self._audit(
            tenant_id,
            "items.imported",
            observability["correlationId"],
            archive.import_run_id,
            _compact_import_audit_details(result, ["items"]),
            customer_id=customer_id,
        )
        self._clear_console_cache(tenant_id)
        return result

    def _existing_item_documents_by_number(self, tenant_id: str) -> dict[str, list[dict[str, Any]]]:
        items_by_number: dict[str, list[dict[str, Any]]] = {}
        for document in self.repository.query_by_tenant("items", tenant_id):
            key = _item_identity_key(_pick(document, "internalItemNumber", "internal_item_number", default=""))
            if key:
                items_by_number.setdefault(key, []).append(document)
        return items_by_number

    def _resolve_item_import_customer_id(self, tenant_id: str, customer_id: Any, customer_code: Any) -> str:
        requested_customer_id = str(customer_id or "").strip()
        requested_customer_code = str(customer_code or "").strip()
        if requested_customer_id and self.repository.get("customers", requested_customer_id):
            return requested_customer_id

        normalized_requested_id = normalize_identifier(requested_customer_id)
        normalized_requested_code = normalize_identifier(requested_customer_code)
        for customer in self.repository.query_by_tenant("customers", tenant_id):
            stored_id = str(_pick(customer, "id", default=""))
            stored_code = str(_pick(customer, "customerCode", "customer_code", default=""))
            normalized_stored_code = normalize_identifier(stored_code)
            if requested_customer_id and stored_id == requested_customer_id:
                return stored_id
            if normalized_requested_code and normalized_stored_code == normalized_requested_code:
                return stored_id
            if normalized_requested_id and normalized_stored_code == normalized_requested_id:
                return stored_id

        if requested_customer_code:
            return stable_id(tenant_id, requested_customer_code)
        return requested_customer_id or GLOBAL_CUSTOMER_ID

    @staticmethod
    def _import_source_metadata(
        payload: dict[str, Any],
        profile: Any,
        imported_at: str,
    ) -> dict[str, Any]:
        return {
            "sourceName": profile.source_name
            or _pick(payload, "sourceName", "source_name", default=""),
            "sourceMetadata": dict(_pick(payload, "sourceMetadata", "source_metadata", default={}) or {}),
            "parserModule": profile.parser_module,
            "importedAt": imported_at,
        }

    def console_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        principal = self._console_principal_from_payload(payload)
        self._ensure_bootstrap_console_admin(tenant_id)

        email = principal["email"]
        if not email:
            return {
                "authorized": False,
                "reason": "missingMicrosoftPrincipal",
                "requiredAuthProvider": "microsoft",
                "bootstrapAdminEmail": BOOTSTRAP_CONSOLE_ADMIN_EMAIL,
            }

        user = self._console_user_by_email(tenant_id, email)
        if user is None:
            user = self._platform_admin_user_by_email(email)
            if user is not None:
                user = {
                    **dict(user),
                    "id": stable_id(tenant_id, email),
                    "tenantId": tenant_id,
                    "updatedAt": utc_now(),
                }
                roles = list(_as_list(_pick(user, "roles", default=[])))
                if "platformAdmin" not in roles:
                    roles.append("platformAdmin")
                user["roles"] = roles
                user = self.repository.upsert("consoleUsers", user)
        if user is None:
            return {
                "authorized": False,
                "reason": "consoleUserNotAssigned",
                "email": email,
                "bootstrapAdminEmail": BOOTSTRAP_CONSOLE_ADMIN_EMAIL,
            }
        if not bool(_pick(user, "enabled", default=True)):
            return {"authorized": False, "reason": "consoleUserDisabled", "email": email}

        user["lastLoginAt"] = utc_now()
        user["updatedAt"] = utc_now()
        if principal.get("microsoftUserId") and not _pick(user, "microsoftUserId", "microsoft_user_id", default=""):
            user["microsoftUserId"] = principal["microsoftUserId"]
        stored_user = self.repository.upsert("consoleUsers", user)

        assignments = [
            assignment
            for assignment in self.repository.query_by_tenant("customerUserAssignments", tenant_id)
            if _normalized_email(_pick(assignment, "email", default="")) == email
            and bool(_pick(assignment, "enabled", default=True))
        ]
        roles = set(_as_list(_pick(stored_user, "roles", default=[])))
        is_platform_admin = "platformAdmin" in roles
        allowed_customer_ids = sorted(
            {
                str(_pick(assignment, "customerId", "customer_id", default=""))
                for assignment in assignments
                if _pick(assignment, "customerId", "customer_id", default="")
            }
        )

        session = {
            "authorized": True,
            "tenantId": tenant_id,
            "principal": principal,
            "consoleUser": stored_user,
            "assignments": assignments,
            "isPlatformAdmin": is_platform_admin,
            "allowedCustomerIds": allowed_customer_ids,
            "permissions": self._console_permissions(is_platform_admin, roles, assignments),
        }
        observability = correlation_context(payload, email)
        self._audit(
            tenant_id,
            "console.session",
            observability["correlationId"],
            email,
            {"isPlatformAdmin": is_platform_admin, "observability": observability},
            actor=email,
        )
        return session

    def _query_console_records(self, container: str, tenant_id: str, fields: list[str]) -> list[dict[str, Any]]:
        query_fields = getattr(self.repository, "query_by_tenant_fields", None)
        if callable(query_fields):
            return [_without_heavy_console_fields(item) for item in query_fields(container, tenant_id, fields)]
        return [
            _without_heavy_console_fields(item)
            for item in self.repository.query_by_tenant(container, tenant_id)
        ]

    def _refresh_tenant_csr_directory(self, tenant_id: str) -> list[dict[str, Any]]:
        directory = self._csr_directory_from_customers(self.repository.query_by_tenant("customers", tenant_id))
        tenant = self.repository.get("tenants", tenant_id) or {
            "id": tenant_id,
            "tenantId": tenant_id,
            "name": tenant_id,
            "environment": "",
            "status": "active",
            "createdAt": utc_now(),
        }
        settings = dict(_pick(tenant, "settings", default={}) or {})
        settings["csrDirectory"] = directory
        settings["csrDirectoryUpdatedAt"] = utc_now()
        tenant["settings"] = settings
        tenant["updatedAt"] = utc_now()
        self.repository.upsert("tenants", tenant)
        return directory

    def _sync_mailbox_categories_after_customer_import(
        self,
        tenant_id: str,
        csr_directory: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not _bool_flag(
            _pick(
                payload,
                "syncMailboxCategories",
                "sync_mailbox_categories",
                default=os.environ.get("ORDER_PROCESSOR_SYNC_MAILBOX_CATEGORIES", "true"),
            ),
            default=True,
        ):
            return {"status": "skipped", "reason": "mailbox category sync disabled"}

        mailboxes = [
            mailbox
            for mailbox in self.repository.query_by_tenant("mailboxAccounts", tenant_id)
            if mailbox and _mailbox_enabled(mailbox)
        ]
        if not mailboxes:
            self._store_managed_mailbox_category_state(tenant_id, csr_directory, {})
            return {"status": "skipped", "reason": "no active mailbox accounts", "mailboxCount": 0}

        desired_categories = self._desired_outlook_categories(csr_directory)
        previous_managed = self._previous_managed_csr_categories(tenant_id)
        results = [
            self._sync_mailbox_master_categories(mailbox, desired_categories, previous_managed)
            for mailbox in mailboxes
        ]
        self._store_managed_mailbox_category_state(tenant_id, csr_directory, desired_categories)
        failed_count = sum(1 for result in results if result.get("status") == "failed")
        applied_count = sum(1 for result in results if result.get("status") in {"applied", "partial"})
        return {
            "status": "failed" if failed_count == len(results) else "partial" if failed_count else "applied" if applied_count else "skipped",
            "mailboxCount": len(results),
            "appliedCount": applied_count,
            "failedCount": failed_count,
            "desiredCategoryCount": len(desired_categories),
            "results": results,
        }

    def _desired_outlook_categories(self, csr_directory: list[dict[str, Any]]) -> dict[str, str]:
        categories: dict[str, str] = {
            PROCESSING_CATEGORY: OUTLOOK_CATEGORY_COLORS[PROCESSING_CATEGORY],
            ORDER_PROCESSING_CATEGORY: OUTLOOK_CATEGORY_COLORS[ORDER_PROCESSING_CATEGORY],
            PROCESSING_EXCEPTION_CATEGORY: OUTLOOK_CATEGORY_COLORS[PROCESSING_EXCEPTION_CATEGORY],
        }
        for csr in csr_directory:
            csr_name = str(_pick(csr, "name", "folder", "email", default="") or "").strip()
            if not csr_name:
                continue
            categories[f"{csr_name} - Action"] = OUTLOOK_CATEGORY_COLORS["csrAction"]
            categories[f"{csr_name} - Review"] = OUTLOOK_CATEGORY_COLORS["csrReview"]
            categories[f"{csr_name} - Validate"] = OUTLOOK_CATEGORY_COLORS["csrValidate"]
        return categories

    def _previous_managed_csr_categories(self, tenant_id: str) -> set[str]:
        tenant = self.repository.get("tenants", tenant_id) or {}
        settings = dict(_pick(tenant, "settings", default={}) or {})
        state = dict(settings.get("managedMailboxCategories") or {})
        return {
            str(value).strip()
            for value in _as_list(state.get("csrCategories"))
            if str(value).strip()
        }

    def _store_managed_mailbox_category_state(
        self,
        tenant_id: str,
        csr_directory: list[dict[str, Any]],
        desired_categories: dict[str, str],
    ) -> None:
        tenant = self.repository.get("tenants", tenant_id) or {
            "id": tenant_id,
            "tenantId": tenant_id,
            "name": tenant_id,
            "environment": "",
            "status": "active",
            "createdAt": utc_now(),
        }
        settings = dict(_pick(tenant, "settings", default={}) or {})
        csr_categories = sorted(
            category
            for category in desired_categories
            if category not in {PROCESSING_CATEGORY, ORDER_PROCESSING_CATEGORY, PROCESSING_EXCEPTION_CATEGORY}
        )
        settings["managedMailboxCategories"] = {
            "fixedCategories": [
                PROCESSING_CATEGORY,
                ORDER_PROCESSING_CATEGORY,
                PROCESSING_EXCEPTION_CATEGORY,
            ],
            "csrCategories": csr_categories,
            "csrCount": len(csr_directory),
            "updatedAt": utc_now(),
        }
        tenant["settings"] = settings
        tenant["updatedAt"] = utc_now()
        self.repository.upsert("tenants", tenant)

    def _sync_mailbox_master_categories(
        self,
        mailbox: dict[str, Any],
        desired_categories: dict[str, str],
        previous_managed_csr_categories: set[str],
    ) -> dict[str, Any]:
        mailbox_address = str(_pick(mailbox, "mailboxAddress", "mailbox_address", default="") or "").strip().lower()
        result: dict[str, Any] = {
            "mailboxAccountId": str(_pick(mailbox, "id", default="") or ""),
            "mailboxAddress": mailbox_address,
            "status": "skipped",
            "created": [],
            "updated": [],
            "deleted": [],
            "errors": [],
        }
        if not mailbox_address:
            result["reason"] = "mailbox address missing"
            return result

        try:
            candidates = self._graph_access_token_candidates(mailbox, auth_mode="auto")
        except MicrosoftGraphError as exc:
            result.update({"status": "failed", "reason": str(exc), "statusCode": exc.status_code, "details": exc.details})
            return result
        if not candidates:
            result["reason"] = "no Microsoft Graph access token"
            return result

        last_error: MicrosoftGraphError | None = None
        for candidate in candidates:
            try:
                sync_result = self._sync_mailbox_master_categories_with_token(
                    candidate["accessToken"],
                    mailbox_address,
                    desired_categories,
                    previous_managed_csr_categories,
                )
                result.update(sync_result)
                result["authMethod"] = candidate["authMethod"]
                return result
            except MicrosoftGraphError as exc:
                last_error = exc
                if exc.status_code not in {401, 403}:
                    break
        result.update(
            {
                "status": "failed",
                "reason": str(last_error) if last_error else "Microsoft Graph category sync failed",
                "statusCode": last_error.status_code if last_error else None,
                "details": last_error.details if last_error else None,
            }
        )
        return result

    def _sync_mailbox_master_categories_with_token(
        self,
        access_token: str,
        mailbox_address: str,
        desired_categories: dict[str, str],
        previous_managed_csr_categories: set[str],
    ) -> dict[str, Any]:
        encoded_mailbox = parse.quote(mailbox_address, safe="")
        base_url = f"https://graph.microsoft.com/v1.0/users/{encoded_mailbox}/outlook/masterCategories"
        existing_response = graph_get(access_token, base_url)
        existing_by_name = {
            str(_pick(item, "displayName", "display_name", default="")): dict(item)
            for item in _as_list(existing_response.get("value"))
            if isinstance(item, dict) and str(_pick(item, "displayName", "display_name", default="")).strip()
        }
        created: list[str] = []
        updated: list[str] = []
        deleted: list[str] = []
        errors: list[dict[str, Any]] = []

        for display_name, color in desired_categories.items():
            existing = existing_by_name.get(display_name)
            if not existing:
                graph_post(access_token, base_url, {"displayName": display_name, "color": color})
                created.append(display_name)
                continue
            existing_color = str(_pick(existing, "color", default="") or "")
            category_id = str(_pick(existing, "id", default="") or "")
            if category_id and existing_color != color:
                graph_patch(access_token, f"{base_url}/{parse.quote(category_id, safe='')}", {"color": color})
                updated.append(display_name)

        desired_names = set(desired_categories)
        for display_name in sorted(previous_managed_csr_categories - desired_names):
            existing = existing_by_name.get(display_name)
            category_id = str(_pick(existing or {}, "id", default="") or "")
            if not category_id:
                continue
            try:
                graph_delete(access_token, f"{base_url}/{parse.quote(category_id, safe='')}")
                deleted.append(display_name)
            except MicrosoftGraphError as exc:
                errors.append(
                    {
                        "displayName": display_name,
                        "reason": str(exc),
                        "statusCode": exc.status_code,
                        "details": exc.details,
                    }
                )

        status = "partial" if errors and (created or updated or deleted) else "failed" if errors else "applied" if created or updated or deleted else "skipped"
        return {
            "status": status,
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "errors": errors,
        }

    def _ensure_ai_cost_source_for_tenant(self, tenant: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(_pick(tenant, "tenantId", "tenant_id", "id", default="default") or "default")
        source_id = stable_id(tenant_id, MICROSOFT_AI_COST_PROVIDER, "ai-cost-source")
        existing = self.repository.get("aiCostSources", source_id) or {}
        settings = dict(_pick(tenant, "settings", default={}) or {})
        configured = dict(_pick(settings, "aiCostSource", "ai_cost_source", default={}) or {})
        project_tag_value = str(
            _pick(configured, "projectTagValue", "project_tag_value", default="")
            or _cost_project_tag_value(tenant_id)
        )
        project_name = str(
            _pick(configured, "projectName", "project_name", default="")
            or f"fa-{project_tag_value}-ai-costs"
        )
        microsoft_project_id = str(
            _pick(
                configured,
                "microsoftProjectId",
                "microsoft_project_id",
                "foundryProjectId",
                "foundry_project_id",
                default=_pick(existing, "microsoftProjectId", "foundryProjectId", default=""),
            )
            or ""
        )
        resource_id = str(
            _pick(
                configured,
                "resourceId",
                "resource_id",
                default=_pick(existing, "resourceId", default=os.environ.get("AZURE_AI_FOUNDRY_RESOURCE_ID", "")),
            )
            or ""
        )
        source = {
            **existing,
            "id": source_id,
            "tenantId": tenant_id,
            "provider": MICROSOFT_AI_COST_PROVIDER,
            "displayName": f"{_pick(tenant, 'name', default=tenant_id)} Microsoft AI costs",
            "projectName": project_name,
            "projectTagKey": str(_pick(configured, "projectTagKey", "project_tag_key", default=AI_COST_PROJECT_TAG_KEY)),
            "projectTagValue": project_tag_value,
            "microsoftProjectId": microsoft_project_id,
            "resourceId": resource_id,
            "resourceGroup": str(
                _pick(configured, "resourceGroup", "resource_group", default=os.environ.get("AZURE_RESOURCE_GROUP", ""))
                or ""
            ),
            "subscriptionId": str(
                _pick(configured, "subscriptionId", "subscription_id", default=os.environ.get("AZURE_SUBSCRIPTION_ID", ""))
                or ""
            ),
            "status": "configured" if microsoft_project_id or resource_id else "pendingMicrosoftProject",
            "costProvider": "azureCostManagement",
            "updatedAt": utc_now(),
            "createdAt": _pick(existing, "createdAt", "created_at", default=utc_now()),
        }
        source["notes"] = (
            "Microsoft Foundry costs are attributed with the project tag. "
            "The app cost ledger records per-run usage immediately; Azure Cost Management remains the reconciliation source."
        )
        stored = self.repository.upsert("aiCostSources", source)
        settings["aiCostSource"] = {
            "provider": MICROSOFT_AI_COST_PROVIDER,
            "sourceId": source_id,
            "projectName": project_name,
            "projectTagKey": source["projectTagKey"],
            "projectTagValue": project_tag_value,
            "status": source["status"],
        }
        tenant["settings"] = settings
        tenant["updatedAt"] = utc_now()
        self.repository.upsert("tenants", tenant)
        return stored

    def record_ai_cost_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event = self._ai_cost_event_from_payload(payload)
        stored = self.repository.upsert("aiCostEvents", event)
        self._audit(
            event["tenantId"],
            "aiCost.recorded",
            str(_pick(event, "correlationId", default=event["id"])),
            event["id"],
            {
                "provider": event["provider"],
                "processorType": event["processorType"],
                "operationType": event["operationType"],
                "customerId": event.get("customerId", ""),
                "costUsd": event["costUsd"],
            },
            customer_id=event.get("customerId") or None,
            order_run_id=event.get("orderRunId") or None,
            email_message_id=event.get("emailMessageId") or None,
            actor=str(_pick(payload, "actor", default="system")),
        )
        return {"aiCostEvent": stored}

    def _ai_cost_event_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(_pick(payload, "tenantId", "tenant_id", default="default") or "default")
        event_time = str(_pick(payload, "eventTime", "event_time", "createdAt", "created_at", default=utc_now()))
        usage = dict(_pick(payload, "usage", "aiUsage", "ai_usage", default={}) or {})
        cost = dict(_pick(payload, "cost", "costDetails", "cost_details", default={}) or {})
        provider = str(_pick(payload, "provider", "serviceProvider", "service_provider", default=MICROSOFT_AI_COST_PROVIDER) or MICROSOFT_AI_COST_PROVIDER)
        processor_type = _processor_cost_type(
            _pick(payload, "processorType", "processor_type", "orderProcessorType", "order_processor_type", default="unknown")
        )
        operation_type = str(_pick(payload, "operationType", "operation_type", "operation", default=processor_type) or processor_type)
        input_tokens = _as_int(_pick(usage, "inputTokens", "promptTokens", "prompt_tokens", "input_tokens", default=0))
        output_tokens = _as_int(_pick(usage, "outputTokens", "completionTokens", "completion_tokens", "output_tokens", default=0))
        embedding_tokens = _as_int(_pick(usage, "embeddingTokens", "embedding_tokens", default=0))
        document_pages = _as_int(_pick(usage, "documentPages", "document_pages", "pages", default=0))
        run_count = max(1, _as_int(_pick(payload, "runCount", "run_count", default=1), 1))
        explicit_cost = _pick(payload, "costUsd", "cost_usd", default=_pick(cost, "usd", "costUsd", "cost_usd", default=None))
        cost_usd = _round_money(explicit_cost)
        event_id = str(
            _pick(payload, "id", "eventId", "event_id", default="")
            or stable_id(
                tenant_id,
                provider,
                processor_type,
                operation_type,
                _pick(payload, "orderRunId", "order_run_id", default=""),
                _pick(payload, "emailMessageId", "email_message_id", default=""),
                event_time,
            )
        )
        return {
            "id": event_id,
            "tenantId": tenant_id,
            "customerId": str(_pick(payload, "customerId", "customer_id", default="") or ""),
            "provider": provider,
            "processorType": processor_type,
            "operationType": operation_type,
            "orderProcessorType": _processor_cost_type(_pick(payload, "orderProcessorType", "order_processor_type", default=processor_type)),
            "modelDeployment": str(_pick(payload, "modelDeployment", "model_deployment", "deployment", default="") or ""),
            "modelName": str(_pick(payload, "modelName", "model_name", "model", default="") or ""),
            "meterName": str(_pick(payload, "meterName", "meter_name", default="") or ""),
            "orderRunId": str(_pick(payload, "orderRunId", "order_run_id", default="") or ""),
            "emailMessageId": str(_pick(payload, "emailMessageId", "email_message_id", default="") or ""),
            "correlationId": str(_pick(payload, "correlationId", "correlation_id", default=event_id) or event_id),
            "runCount": run_count,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "embeddingTokens": embedding_tokens,
            "documentPages": document_pages,
            "costUsd": cost_usd,
            "currency": str(_pick(payload, "currency", default=DEFAULT_COST_CURRENCY) or DEFAULT_COST_CURRENCY),
            "estimated": bool(_pick(payload, "estimated", default=explicit_cost is None)),
            "source": str(_pick(payload, "source", default="appCostLedger") or "appCostLedger"),
            "metadata": dict(_pick(payload, "metadata", default={}) or {}),
            "createdAt": event_time,
            "updatedAt": utc_now(),
        }

    @staticmethod
    def _ai_cost_input(payload: dict[str, Any]) -> dict[str, Any]:
        explicit = _as_dict(_pick(payload, "aiCost", "ai_cost", default={}))
        source_metadata = _as_dict(_pick(payload, "sourceMetadata", "source_metadata", default={}))
        source_cost = _as_dict(_pick(source_metadata, "aiCost", "ai_cost", default={}))
        usage = (
            _as_dict(_pick(payload, "usage", "aiUsage", "ai_usage", default={}))
            or _as_dict(_pick(explicit, "usage", "aiUsage", "ai_usage", default={}))
            or _as_dict(_pick(source_cost, "usage", "aiUsage", "ai_usage", default={}))
        )
        cost = (
            _as_dict(_pick(payload, "cost", "costDetails", "cost_details", default={}))
            or _as_dict(_pick(explicit, "cost", "costDetails", "cost_details", default={}))
            or _as_dict(_pick(source_cost, "cost", "costDetails", "cost_details", default={}))
        )
        cost_usd = _pick(
            payload,
            "costUsd",
            "cost_usd",
            default=_pick(explicit, "costUsd", "cost_usd", default=_pick(source_cost, "costUsd", "cost_usd", default=None)),
        )
        provider = str(
            _pick(payload, "provider", default=_pick(explicit, "provider", default=_pick(source_cost, "provider", default="")))
            or ""
        )
        return {"explicit": explicit, "sourceCost": source_cost, "usage": usage, "cost": cost, "costUsd": cost_usd, "provider": provider}

    @staticmethod
    def _has_ai_cost_signal(cost_input: dict[str, Any]) -> bool:
        return bool(
            cost_input.get("explicit")
            or cost_input.get("sourceCost")
            or cost_input.get("usage")
            or cost_input.get("cost")
            or cost_input.get("costUsd") is not None
        )

    def _record_customer_identification_cost_event(
        self,
        email: EmailMessage,
        result: CustomerIdentificationResult,
        payload: dict[str, Any],
        observability: dict[str, Any],
    ) -> dict[str, Any] | None:
        cost_input = self._ai_cost_input(payload)
        match_method = str(result.match_method or "")
        ai_method = any(marker in match_method.lower() for marker in ("ai", "foundry", "openai", "llm", "model"))
        if not self._has_ai_cost_signal(cost_input) and not ai_method:
            return None
        event_payload = {
            **cost_input["explicit"],
            "tenantId": email.tenant_id,
            "customerId": result.customer_id or "",
            "provider": cost_input["provider"] or MICROSOFT_AI_COST_PROVIDER,
            "processorType": "customerIdentification",
            "operationType": match_method or "customerIdentification",
            "emailMessageId": email.id,
            "correlationId": observability["correlationId"],
            "usage": cost_input["usage"],
            "cost": cost_input["cost"],
            "costUsd": cost_input["costUsd"],
            "metadata": {
                **_as_dict(_pick(cost_input["explicit"], "metadata", default={})),
                "matchStatus": str(result.status),
                "matchMethod": match_method,
                "confidence": result.confidence,
            },
            "source": "customerIdentification",
        }
        return self._record_ai_cost_event_safely(event_payload)

    def _record_order_processor_cost_event(
        self,
        order_doc: dict[str, Any],
        payload: dict[str, Any],
        processor_profile: ProcessorProfile | None,
        observability: dict[str, Any],
    ) -> dict[str, Any] | None:
        cost_input = self._ai_cost_input(payload)
        source_metadata = _as_dict(_pick(order_doc, "sourceMetadata", "source_metadata", default={}))
        profile_processor_type = processor_profile.processor_type if processor_profile is not None else "orderProcessor"
        processor_type = _processor_cost_type(
            _pick(order_doc, "processorType", "processor_type", default=profile_processor_type)
        )
        document_result = _pick(
            payload,
            "documentIntelligenceResult",
            "document_intelligence_result",
            "azureDocumentIntelligenceResult",
            "azure_document_intelligence_result",
            default=None,
        )
        document_model_id = str(_pick(source_metadata, "documentIntelligenceModelId", default="") or "")
        google_document_ai = _as_dict(_pick(source_metadata, "googleDocumentAi", "google_document_ai", default={}))
        is_azure_document_ai = processor_type == "pdf" and (document_result is not None or document_model_id)
        is_google_document_ai = processor_type == "pdf" and bool(google_document_ai)
        if not self._has_ai_cost_signal(cost_input) and not (is_azure_document_ai or is_google_document_ai):
            return None
        provider = cost_input["provider"] or (GOOGLE_DOCUMENT_AI_COST_PROVIDER if is_google_document_ai else MICROSOFT_AI_COST_PROVIDER)
        operation_type = str(
            _pick(cost_input["explicit"], "operationType", "operation_type", default="")
            or (
                "googleDocumentAi"
                if is_google_document_ai
                else "azureDocumentIntelligence"
                if is_azure_document_ai
                else "orderProcessor"
            )
        )
        usage = dict(cost_input["usage"])
        if (is_azure_document_ai or is_google_document_ai) and not _pick(usage, "documentPages", "document_pages", "pages", default=None):
            raw_document = _as_dict(_pick(google_document_ai, "rawDocument", "raw_document", default={}))
            usage["documentPages"] = _pick(
                raw_document,
                "pageCount",
                "page_count",
                default=_pick(payload, "documentPages", "document_pages", "pageCount", "page_count", default=0),
            )
        event_payload = {
            **cost_input["explicit"],
            "tenantId": str(_pick(order_doc, "tenantId", "tenant_id", default="default") or "default"),
            "customerId": str(_document_customer_id(order_doc) or ""),
            "provider": provider,
            "processorType": processor_type,
            "operationType": operation_type,
            "orderProcessorType": processor_type,
            "modelDeployment": str(_pick(cost_input["explicit"], "modelDeployment", "model_deployment", default=document_model_id) or ""),
            "orderRunId": str(_pick(order_doc, "id", default="") or ""),
            "emailMessageId": str(_pick(order_doc, "emailMessageId", "email_message_id", default="") or ""),
            "correlationId": observability["correlationId"],
            "usage": usage,
            "cost": cost_input["cost"],
            "costUsd": cost_input["costUsd"],
            "metadata": {
                **_as_dict(_pick(cost_input["explicit"], "metadata", default={})),
                "orderStatus": _document_status(order_doc),
                "processorProfileId": str(_pick(order_doc, "processorProfileId", "processor_profile_id", default="") or ""),
            },
            "source": "orderProcessor",
        }
        return self._record_ai_cost_event_safely(event_payload)

    def _record_ai_cost_event_safely(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return self.record_ai_cost_event(payload)["aiCostEvent"]
        except Exception as exc:  # pragma: no cover - defensive telemetry path.
            tenant_id = str(_pick(payload, "tenantId", "tenant_id", default="default") or "default")
            correlation_id = str(_pick(payload, "correlationId", "correlation_id", default=stable_id(tenant_id, "aiCostRecordFailed")))
            self._audit(
                tenant_id,
                "aiCost.recordFailed",
                correlation_id,
                str(_pick(payload, "orderRunId", "emailMessageId", default=correlation_id) or correlation_id),
                {
                    "error": str(exc),
                    "processorType": _pick(payload, "processorType", "processor_type", default=""),
                    "operationType": _pick(payload, "operationType", "operation_type", default=""),
                },
                customer_id=str(_pick(payload, "customerId", "customer_id", default="") or "") or None,
                order_run_id=str(_pick(payload, "orderRunId", "order_run_id", default="") or "") or None,
                email_message_id=str(_pick(payload, "emailMessageId", "email_message_id", default="") or "") or None,
            )
            return None

    def cost_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(_pick(payload, "tenantId", "tenant_id", default="default") or "default")
        period = _period_from_payload(payload)
        provider_filter = str(_pick(payload, "provider", "serviceProvider", "service_provider", default="") or "").strip()
        customer_filter = str(_pick(payload, "customerId", "customer_id", default="") or "").strip()
        customer_ids_filter = {
            str(value)
            for value in _as_list(_pick(payload, "customerIds", "customer_ids", default=[]))
            if str(value).strip()
        }
        processor_filter = str(_pick(payload, "processorType", "processor_type", default="") or "").strip()
        events = [
            event
            for event in self.repository.query_by_tenant("aiCostEvents", tenant_id)
            if _in_period(_pick(event, "createdAt", "created_at", default=""), period)
            and (not provider_filter or str(_pick(event, "provider", default="")) == provider_filter)
            and (not customer_filter or str(_pick(event, "customerId", "customer_id", default="")) == customer_filter)
            and (not customer_ids_filter or str(_pick(event, "customerId", "customer_id", default="")) in customer_ids_filter)
            and (not processor_filter or str(_pick(event, "processorType", "processor_type", default="")) == processor_filter)
        ]
        rows = self._ai_cost_summary_rows(events)
        sources = self.repository.query_by_tenant("aiCostSources", tenant_id)
        return {
            "tenantId": tenant_id,
            "period": period,
            "currency": DEFAULT_COST_CURRENCY,
            "totalRunCount": sum(int(_pick(row, "runCount", default=0) or 0) for row in rows),
            "totalCostUsd": _round_money(sum(float(_pick(row, "costUsd", default=0) or 0) for row in rows)),
            "rows": rows,
            "costSources": sources,
            "providerNotes": {
                MICROSOFT_AI_COST_PROVIDER: "Microsoft Foundry/Azure OpenAI costs reconcile through Azure Cost Management using the project tag.",
                GOOGLE_DOCUMENT_AI_COST_PROVIDER: "Google Document AI PDF processor costs are tracked separately when that provider integration is configured.",
            },
        }

    @staticmethod
    def _ai_cost_summary_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for event in events:
            key = (
                str(_pick(event, "customerId", "customer_id", default="") or ""),
                str(_pick(event, "provider", default="") or ""),
                str(_pick(event, "processorType", "processor_type", default="unknown") or "unknown"),
                str(_pick(event, "operationType", "operation_type", default="") or ""),
            )
            row = grouped.setdefault(
                key,
                {
                    "customerId": key[0],
                    "provider": key[1],
                    "processorType": key[2],
                    "operationType": key[3],
                    "runCount": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "embeddingTokens": 0,
                    "documentPages": 0,
                    "costUsd": 0.0,
                    "estimated": False,
                },
            )
            row["runCount"] += max(1, int(_pick(event, "runCount", "run_count", default=1) or 1))
            row["inputTokens"] += int(_pick(event, "inputTokens", "input_tokens", default=0) or 0)
            row["outputTokens"] += int(_pick(event, "outputTokens", "output_tokens", default=0) or 0)
            row["embeddingTokens"] += int(_pick(event, "embeddingTokens", "embedding_tokens", default=0) or 0)
            row["documentPages"] += int(_pick(event, "documentPages", "document_pages", default=0) or 0)
            row["costUsd"] = _round_money(float(row["costUsd"]) + float(_pick(event, "costUsd", "cost_usd", default=0) or 0))
            row["estimated"] = bool(row["estimated"] or _pick(event, "estimated", default=False))
        return sorted(grouped.values(), key=lambda item: (str(item["customerId"]), str(item["processorType"]), str(item["operationType"])))

    def _csr_directory_for_console(self, tenant_id: str, tenant: dict[str, Any]) -> list[dict[str, Any]]:
        settings = dict(_pick(tenant, "settings", default={}) or {})
        configured = [item for item in _as_list(settings.get("csrDirectory")) if isinstance(item, dict)]
        if configured:
            return sorted(configured, key=lambda item: str(_pick(item, "label", "name", "folder", default="")).lower())
        return self._cached_console_value(
            ("csrDirectory", tenant_id),
            lambda: self._csr_directory_from_customers(self.repository.query_by_tenant("customers", tenant_id)),
        )

    @staticmethod
    def _csr_directory_from_customers(customers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_key: dict[str, dict[str, Any]] = {}
        for customer in customers:
            name = str(_pick(customer, "csrName", "csr_name", default="") or "").strip()
            folder = str(_pick(customer, "csrFolder", "csr_folder", default="") or "").strip()
            email = str(_pick(customer, "csrEmail", "csr_email", default="") or "").strip().lower()
            if not name and not folder and not email:
                continue
            display_name = name or folder or email
            folder_name = folder or name
            key = stable_id("csr", display_name.lower(), folder_name.lower(), email)
            existing = by_key.get(key, {})
            customer_codes = set(_as_list(existing.get("customerCodes")))
            customer_code = str(_pick(customer, "customerCode", "customer_code", default="") or "").strip()
            if customer_code:
                customer_codes.add(customer_code)
            by_key[key] = {
                "id": key,
                "name": display_name,
                "folder": folder_name,
                "email": email,
                "label": display_name if not email else f"{display_name} ({email})",
                "customerCodes": sorted(customer_codes),
            }
        return sorted(by_key.values(), key=lambda item: str(item.get("label") or item.get("name") or "").lower())

    def _cached_console_value(self, key: tuple[Any, ...], factory: Any, ttl_seconds: int = 45) -> Any:
        now = time.monotonic()
        cached = self._console_cache.get(key)
        if cached and now - cached[0] < ttl_seconds:
            return cached[1]
        value = factory()
        self._console_cache[key] = (now, value)
        return value

    def _clear_console_cache(self, tenant_id: str | None = None) -> None:
        if tenant_id is None:
            self._console_cache.clear()
            return
        prefix = str(tenant_id)
        self._console_cache = {
            key: value
            for key, value in self._console_cache.items()
            if len(key) < 2 or str(key[1]) != prefix
        }

    @staticmethod
    def _console_page_request(payload: dict[str, Any]) -> dict[str, Any]:
        limit = int(_pick(payload, "limit", "pageSize", "page_size", default=100) or 100)
        offset = int(_pick(payload, "offset", default=0) or 0)
        page = int(_pick(payload, "page", default=0) or 0)
        limit = max(1, min(limit, 500))
        if page > 0 and not offset:
            offset = (page - 1) * limit
        return {
            "limit": limit,
            "offset": max(0, offset),
            "search": str(_pick(payload, "search", "q", default="") or "").strip(),
        }

    def _query_console_page(
        self,
        container: str,
        tenant_id: str,
        *,
        fields: list[str],
        search_fields: list[str],
        order_by: str,
        descending: bool,
        payload: dict[str, Any],
        customer_filter: set[str] | None | str = None,
        include_global: bool = False,
    ) -> dict[str, Any]:
        page = self._console_page_request(payload)
        query_page = getattr(self.repository, "query_by_tenant_page", None)
        customer_ids = None if customer_filter is None else sorted(customer_filter) if isinstance(customer_filter, set) else []
        if callable(query_page):
            result = query_page(
                container,
                tenant_id,
                fields=fields,
                limit=page["limit"],
                offset=page["offset"],
                search=page["search"],
                search_fields=search_fields,
                order_by=order_by,
                descending=descending,
                customer_ids=customer_ids,
                include_global=include_global,
            )
        else:
            documents = self._filter_customer_documents(
                self._query_console_records(container, tenant_id, fields),
                customer_filter,
                {"isPlatformAdmin": True, "permissions": ["viewAllCustomers"], "allowedCustomerIds": []},
                include_global=include_global,
            )
            result = {
                "items": documents[page["offset"] : page["offset"] + page["limit"]],
                "total": len(documents),
                "limit": page["limit"],
                "offset": page["offset"],
            }
        result["items"] = [_without_heavy_console_fields(item) for item in _as_list(result.get("items")) if isinstance(item, dict)]
        result["hasNext"] = int(result.get("offset", 0)) + len(result["items"]) < int(result.get("total", 0))
        result["hasPrevious"] = int(result.get("offset", 0)) > 0
        result["search"] = page["search"]
        return result

    def _query_console_stats(self, container: str, tenant_id: str) -> dict[str, Any]:
        stats = getattr(self.repository, "query_by_tenant_stats", None)
        if callable(stats):
            return dict(stats(container, tenant_id, "lastImportedAt"))
        documents = self.repository.query_by_tenant(container, tenant_id)
        latest = sorted(
            [
                str(_pick(item, "lastImportedAt", "last_imported_at", "updatedAt", "updated_at", default=""))
                for item in documents
                if _pick(item, "lastImportedAt", "last_imported_at", "updatedAt", "updated_at", default="")
            ]
        )
        return {"count": len(documents), "latest": latest[-1] if latest else ""}

    @staticmethod
    def _pathway_from_email_doc(email: dict[str, Any], order: dict[str, Any] | None = None) -> str:
        source = _as_dict(_pick(email, "source", default={}))
        processing = _as_dict(_pick(source, "processing", default={}))
        pathway = str(_pick(processing, "pathway", default="")).strip()
        if pathway:
            return pathway
        routing = _as_dict(_pick(email, "routing", default={}))
        matched_signals = _as_dict(_pick(routing, "matchedSignals", "matched_signals", default={}))
        phase = str(_pick(matched_signals, "triagePhase", "triage_phase", default="")).strip()
        if phase:
            return phase
        if order or _pick(email, "orderRunId", "order_run_id", default=""):
            return "orderProcessing"
        outcome = str(_pick(routing, "outcome", default=""))
        if outcome == RoutingOutcome.KNOWN_CUSTOMER_NON_ORDER.value:
            return "nonOrder"
        if outcome == RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION.value:
            return "customerIdentification"
        if outcome == RoutingOutcome.NEEDS_HUMAN_REVIEW.value:
            return "humanReview"
        return outcome or "email"

    def _upsert_monitor_record_for_email(
        self,
        email: dict[str, Any],
        order: dict[str, Any] | None = None,
        exception: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        tenant_id = str(_pick(email, "tenantId", "tenant_id", default=""))
        email_id = str(_pick(email, "id", default=""))
        if not tenant_id or not email_id:
            return None
        order = order or (
            self.repository.get("orderRuns", str(_pick(email, "orderRunId", "order_run_id", default="")))
            if _pick(email, "orderRunId", "order_run_id", default="")
            else None
        )
        exception = exception or self._open_exception_for_monitor(tenant_id, email_id, str(_pick(order or {}, "id", default="")))
        customer = self.repository.get("customers", str(_document_customer_id(email) or _document_customer_id(order or {}) or ""))
        customer_by_id = {str(_pick(customer or {}, "id", default="")): customer} if customer else {}
        entry = (
            self._monitor_entry_from_exception(
                exception,
                customer_by_id,
                {email_id: email},
                {str(_pick(order or {}, "id", default="")): order} if order else {},
            )
            if exception
            else self._monitor_entry_from_order(order, customer_by_id, email) if order else self._monitor_entry_from_email(email, customer_by_id, None)
        )
        record = self._monitor_record_from_entry(entry, tenant_id)
        return self.repository.upsert("monitorRecords", record)

    def _upsert_monitor_record_for_order(self, order: dict[str, Any]) -> dict[str, Any] | None:
        tenant_id = str(_pick(order, "tenantId", "tenant_id", default=""))
        if not tenant_id:
            return None
        email_id = str(_pick(order, "emailMessageId", "email_message_id", default=""))
        email = self.repository.get("emailMessages", email_id) if email_id else None
        if email:
            return self._upsert_monitor_record_for_email(email, order=order)
        customer = self.repository.get("customers", str(_document_customer_id(order) or ""))
        customer_by_id = {str(_pick(customer or {}, "id", default="")): customer} if customer else {}
        exception = self._open_exception_for_monitor(tenant_id, "", str(_pick(order, "id", default="")))
        entry = (
            self._monitor_entry_from_exception(exception, customer_by_id, {}, {str(_pick(order, "id", default="")): order})
            if exception
            else self._monitor_entry_from_order(order, customer_by_id, None)
        )
        return self.repository.upsert("monitorRecords", self._monitor_record_from_entry(entry, tenant_id))

    def _upsert_monitor_record_for_exception(self, task: dict[str, Any]) -> dict[str, Any] | None:
        tenant_id = str(_pick(task, "tenantId", "tenant_id", default=""))
        if not tenant_id:
            return None
        email_id = str(_pick(task, "emailMessageId", "email_message_id", default=""))
        order_id = str(_pick(task, "orderRunId", "order_run_id", default=""))
        email = self.repository.get("emailMessages", email_id) if email_id else None
        order = self.repository.get("orderRuns", order_id) if order_id else None
        if email:
            return self._upsert_monitor_record_for_email(
                email,
                order=order,
                exception=task if _document_status(task) == ExceptionStatus.OPEN.value else None,
            )
        if order:
            return self._upsert_monitor_record_for_order(order)
        customer = self.repository.get("customers", str(self._exception_customer_id(task) or ""))
        customer_by_id = {str(_pick(customer or {}, "id", default="")): customer} if customer else {}
        entry = self._monitor_entry_from_exception(task, customer_by_id, {}, {})
        return self.repository.upsert("monitorRecords", self._monitor_record_from_entry(entry, tenant_id))

    def _open_exception_for_monitor(
        self,
        tenant_id: str,
        email_message_id: str,
        order_run_id: str,
    ) -> dict[str, Any] | None:
        if not email_message_id and not order_run_id:
            return None
        for task in self.repository.query_by_tenant("exceptionTasks", tenant_id):
            if _document_status(task) != ExceptionStatus.OPEN.value:
                continue
            if email_message_id and _pick(task, "emailMessageId", "email_message_id", default="") == email_message_id:
                return task
            if order_run_id and _pick(task, "orderRunId", "order_run_id", default="") == order_run_id:
                return task
        return None

    @staticmethod
    def _monitor_record_from_entry(entry: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        active_statuses = {
            ProcessingStatus.RECEIVED.value,
            ProcessingStatus.ROUTED.value,
            ProcessingStatus.PROCESSING.value,
        }
        status = str(_pick(entry, "status", default=""))
        section = "nonOrderEmails"
        if _pick(entry, "exceptionId", default="") and status == ExceptionStatus.OPEN.value:
            section = "exceptions"
        elif status in active_statuses:
            section = "active"
        elif _pick(entry, "orderRunId", default="") and entry.get("pathway") == "orderProcessing":
            section = "processedOrders"
        elif entry.get("pathway") == "webstoreOrder":
            section = "webstoreOrders"
        record_id = str(_pick(entry, "exceptionId", default="") or "") if section == "exceptions" else ""
        for key in ("emailMessageId", "orderRunId", "exceptionId", "id"):
            if record_id:
                break
            value = str(_pick(entry, key, default="") or "").strip()
            if value:
                record_id = value
                break
        record = {
            field: entry[field]
            for field in CONSOLE_MONITOR_RECORD_FIELDS
            if field in entry and entry[field] is not None
        }
        record.update(
            {
                "id": record_id or stable_id(tenant_id, utc_now()),
                "tenantId": tenant_id,
                "section": section,
                "updatedAt": str(_pick(entry, "updatedAt", "createdAt", default=utc_now())),
            }
        )
        return record

    def _monitor_sections_from_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        sections = {
            "active": [],
            "exceptions": [],
            "processedOrders": [],
            "webstoreOrders": [],
            "nonOrderEmails": [],
        }
        for record in records:
            section = str(_pick(record, "section", default="nonOrderEmails"))
            sections.setdefault(section, []).append(record)
        return {key: self._sort_recent(value) for key, value in sections.items()}

    def _backfill_monitor_records_for_console(
        self,
        tenant_id: str,
        customer_filter: set[str] | None | str,
        session: dict[str, Any],
    ) -> dict[str, Any]:
        customers = self._filter_customer_documents(
            self._query_console_records("customers", tenant_id, CONSOLE_CUSTOMER_FIELDS),
            customer_filter,
            session,
        )
        order_runs = self._filter_customer_documents(
            self.repository.query_by_tenant("orderRuns", tenant_id),
            customer_filter,
            session,
        )
        email_messages = self._filter_customer_documents(
            self._query_console_records("emailMessages", tenant_id, CONSOLE_EMAIL_FIELDS),
            customer_filter,
            session,
        )
        exceptions = self._filter_exceptions_for_console(
            self.repository.query_by_tenant("exceptionTasks", tenant_id),
            order_runs,
            customer_filter,
            session,
        )
        monitor = self._monitor_sections(email_messages, order_runs, exceptions, customers)
        for entries in monitor.values():
            for entry in _as_list(entries):
                if isinstance(entry, dict):
                    self.repository.upsert("monitorRecords", self._monitor_record_from_entry(entry, tenant_id))
        return monitor

    @staticmethod
    def _console_summary_from_monitor(monitor: dict[str, Any], item_stats: dict[str, Any] | None = None) -> dict[str, Any]:
        processed_orders = _as_list(monitor.get("processedOrders"))
        exceptions = _as_list(monitor.get("exceptions"))
        completed = [item for item in processed_orders if _document_status(item) == ProcessingStatus.COMPLETED.value]
        failed = [item for item in processed_orders if _document_status(item) == ProcessingStatus.FAILED.value]
        total_finished = len(completed) + len(failed)
        return {
            "activeRunCount": len(_as_list(monitor.get("active"))),
            "processedOrderCount": len(processed_orders),
            "successRate": round(len(completed) / total_finished, 4) if total_finished else 0.0,
            "openExceptionCount": len(exceptions),
            "unresolvedLineCount": 0,
            "itemRecordCount": int((item_stats or {}).get("count") or 0),
            "customerIdentificationFailureCount": len(
                [item for item in exceptions if str(_pick(item, "type", default="")) == "customerIdentification"]
            ),
            "processorFailureCount": len(
                [item for item in exceptions if str(_pick(item, "type", default="")) == "parserFailure"]
            ),
            "outputGenerationFailureCount": len(
                [item for item in exceptions if str(_pick(item, "type", default="")) == "outputGeneration"]
            ),
            "averageProcessingLatencyMs": None,
            "p95ProcessingLatencyMs": None,
        }

    def _monitor_sections(
        self,
        email_messages: list[dict[str, Any]],
        order_runs: list[dict[str, Any]],
        exceptions: list[dict[str, Any]],
        customers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        customer_by_id = {str(_pick(customer, "id", default="")): customer for customer in customers}
        email_by_id = {str(_pick(email, "id", default="")): email for email in email_messages}
        order_by_id = {str(_pick(order, "id", default="")): order for order in order_runs}
        order_by_email = {
            str(_pick(order, "emailMessageId", "email_message_id", default="")): order
            for order in order_runs
            if _pick(order, "emailMessageId", "email_message_id", default="")
        }
        open_exceptions = [
            task for task in exceptions if _document_status(task) == ExceptionStatus.OPEN.value
        ]
        exception_email_ids = {
            str(_pick(task, "emailMessageId", "email_message_id", default=""))
            for task in open_exceptions
            if _pick(task, "emailMessageId", "email_message_id", default="")
        }
        exception_order_ids = {
            str(_pick(task, "orderRunId", "order_run_id", default=""))
            for task in open_exceptions
            if _pick(task, "orderRunId", "order_run_id", default="")
        }
        active_statuses = {
            ProcessingStatus.RECEIVED.value,
            ProcessingStatus.ROUTED.value,
            ProcessingStatus.PROCESSING.value,
        }

        active: list[dict[str, Any]] = []
        for email in email_messages:
            email_id = str(_pick(email, "id", default=""))
            order = order_by_email.get(email_id)
            order_id = str(_pick(order or {}, "id", default=""))
            if email_id in exception_email_ids or order_id in exception_order_ids:
                continue
            if _document_status(email) in active_statuses:
                active.append(self._monitor_entry_from_email(email, customer_by_id, order))

        for order in order_runs:
            email_id = str(_pick(order, "emailMessageId", "email_message_id", default=""))
            if email_id in email_by_id:
                continue
            order_id = str(_pick(order, "id", default=""))
            if order_id in exception_order_ids:
                continue
            if _document_status(order) in active_statuses:
                active.append(self._monitor_entry_from_order(order, customer_by_id, None))

        processed_orders = [
            self._monitor_entry_from_order(order, customer_by_id, email_by_id.get(str(_pick(order, "emailMessageId", "email_message_id", default=""))))
            for order in order_runs
            if _document_status(order) not in active_statuses
        ]

        webstore_orders: list[dict[str, Any]] = []
        non_order_emails: list[dict[str, Any]] = []
        for email in email_messages:
            email_id = str(_pick(email, "id", default=""))
            if email_id in exception_email_ids or email_id in order_by_email:
                continue
            status = _document_status(email)
            if status in active_statuses or status in {ProcessingStatus.NEEDS_REVIEW.value, ProcessingStatus.IGNORED.value}:
                continue
            entry = self._monitor_entry_from_email(email, customer_by_id, None)
            if entry["pathway"] == "webstoreOrder":
                webstore_orders.append(entry)
            else:
                non_order_emails.append(entry)

        exception_entries = [
            self._monitor_entry_from_exception(task, customer_by_id, email_by_id, order_by_id)
            for task in open_exceptions
        ]

        return {
            "active": self._sort_recent(active),
            "exceptions": self._sort_recent(exception_entries),
            "processedOrders": self._sort_recent(processed_orders),
            "webstoreOrders": self._sort_recent(webstore_orders),
            "nonOrderEmails": self._sort_recent(non_order_emails),
        }

    def _monitor_entry_from_email(
        self,
        email: dict[str, Any],
        customer_by_id: dict[str, dict[str, Any]],
        order: dict[str, Any] | None,
    ) -> dict[str, Any]:
        customer = customer_by_id.get(str(_document_customer_id(email) or ""))
        source = _as_dict(_pick(email, "source", default={}))
        graph_actions = _as_dict(_pick(source, "graphEmailActions", "graph_email_actions", default={}))
        return {
            "id": str(_pick(email, "id", default="")),
            "emailMessageId": str(_pick(email, "id", default="")),
            "orderRunId": str(_pick(order or {}, "id", default=_pick(email, "orderRunId", "order_run_id", default="")) or ""),
            "pathway": self._pathway_from_email_doc(email, order),
            "status": _document_status(order or {}) or _document_status(email),
            "sender": str(_pick(email, "sender", default="")),
            "recipient": self._monitor_recipient(email),
            "subject": str(_pick(email, "subject", default="")),
            "receivedAt": str(_pick(email, "receivedAt", "received_at", default="")),
            "updatedAt": str(_pick(email, "updatedAt", "updated_at", "createdAt", "created_at", default="")),
            "categorizedAs": self._monitor_category(email),
            "customerId": str(_document_customer_id(email) or _document_customer_id(order or {}) or ""),
            "customerCode": str(_pick(customer or {}, "customerCode", "customer_code", default="")),
            "customerName": str(_pick(customer or {}, "name", default="")),
            "csr": str(_pick(customer or {}, "csrName", "csr_name", "csrFolder", "csr_folder", default="")),
            "csrEmail": str(_pick(customer or {}, "csrEmail", "csr_email", default="")),
            "actionTaken": self._monitor_action_summary(email, order, graph_actions),
            "movedTo": self._monitor_moved_to(graph_actions),
            "emailUrl": self._monitor_email_url(email, graph_actions),
            "createdAt": str(_pick(email, "createdAt", "created_at", default="")),
        }

    def _monitor_entry_from_order(
        self,
        order: dict[str, Any],
        customer_by_id: dict[str, dict[str, Any]],
        email: dict[str, Any] | None,
    ) -> dict[str, Any]:
        customer = customer_by_id.get(str(_document_customer_id(order) or _document_customer_id(email or {}) or ""))
        source_metadata = _as_dict(_pick(order, "sourceMetadata", "source_metadata", default={}))
        entry = self._monitor_entry_from_email(email, customer_by_id, order) if email else {
            "id": str(_pick(order, "id", default="")),
            "emailMessageId": str(_pick(order, "emailMessageId", "email_message_id", default="")),
            "sender": str(source_metadata.get("sender", "")),
            "recipient": str(source_metadata.get("mailbox", "")),
            "subject": "",
            "receivedAt": str(source_metadata.get("receivedAt", "")),
            "categorizedAs": "order",
            "emailUrl": "",
            "createdAt": str(_pick(order, "createdAt", "created_at", default="")),
        }
        entry.update(
            {
                "id": str(_pick(order, "id", default=entry.get("id", ""))),
                "orderRunId": str(_pick(order, "id", default="")),
                "pathway": "orderProcessing",
                "status": _document_status(order),
                "updatedAt": str(_pick(order, "updatedAt", "updated_at", "createdAt", "created_at", default="")),
                "customerId": str(_document_customer_id(order) or entry.get("customerId", "")),
                "customerCode": str(_pick(customer or {}, "customerCode", "customer_code", default=entry.get("customerCode", ""))),
                "customerName": str(_pick(customer or {}, "name", default=entry.get("customerName", ""))),
                "csr": str(_pick(customer or {}, "csrName", "csr_name", "csrFolder", "csr_folder", default=entry.get("csr", ""))),
                "csrEmail": str(_pick(customer or {}, "csrEmail", "csr_email", default=entry.get("csrEmail", ""))),
                "poNumber": str(_pick(order, "poNumber", "po_number", default="")),
                "orderNumber": str(_pick(order, "orderNumber", "order_number", default="")),
                "lineCount": len(_as_list(_pick(order, "lines", default=[]))),
                "artifactCount": len(_as_list(_pick(order, "outputArtifacts", "output_artifacts", default=[]))),
                "actionTaken": self._monitor_action_summary(email or {}, order, _as_dict(_pick(_as_dict(_pick(email or {}, "source", default={})), "graphEmailActions", default={}))),
            }
        )
        return entry

    def _monitor_entry_from_exception(
        self,
        task: dict[str, Any],
        customer_by_id: dict[str, dict[str, Any]],
        email_by_id: dict[str, dict[str, Any]],
        order_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        order = order_by_id.get(str(_pick(task, "orderRunId", "order_run_id", default="")))
        email = email_by_id.get(str(_pick(task, "emailMessageId", "email_message_id", default="")))
        base = (
            self._monitor_entry_from_email(email, customer_by_id, order)
            if email
            else self._monitor_entry_from_order(order, customer_by_id, None) if order else {}
        )
        customer = customer_by_id.get(str(_document_customer_id(task) or base.get("customerId", "")))
        return {
            **task,
            **base,
            "id": str(_pick(task, "id", default=base.get("id", ""))),
            "exceptionId": str(_pick(task, "id", default="")),
            "type": str(_pick(task, "type", default="")),
            "status": _document_status(task),
            "exception": str(_pick(task, "prompt", default="")),
            "prompt": str(_pick(task, "prompt", default="")),
            "customerId": str(_document_customer_id(task) or base.get("customerId", "")),
            "customerCode": str(_pick(customer or {}, "customerCode", "customer_code", default=base.get("customerCode", ""))),
            "customerName": str(_pick(customer or {}, "name", default=base.get("customerName", ""))),
            "resolutionActions": self._exception_resolution_actions(task, base, customer),
            "updatedAt": str(_pick(task, "updatedAt", "updated_at", "createdAt", "created_at", default=base.get("updatedAt", ""))),
        }

    @staticmethod
    def _exception_resolution_actions(
        task: dict[str, Any],
        base: dict[str, Any],
        customer: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        task_type = str(_pick(task, "type", default=""))
        email_id = str(_pick(task, "emailMessageId", "email_message_id", default=base.get("emailMessageId", "")) or "")
        order_id = str(_pick(task, "orderRunId", "order_run_id", default=base.get("orderRunId", "")) or "")
        customer_id = str(_document_customer_id(task) or base.get("customerId", "") or "")
        actions: list[dict[str, Any]] = []
        actions.append({"key": "disregard", "label": "Disregard / manual handling", "requires": ["notes"], "completesException": True})
        if task_type in {"customerIdentification", "routing"} or email_id or order_id:
            actions.append({"key": "customer", "label": "Set customer code", "requires": ["customerCode"], "keepsExceptionOpen": True})
        if email_id:
            if customer_id:
                actions.append(
                    {
                        "key": "csr",
                        "label": "Assign CSR",
                        "requires": ["csr"],
                        "keepsExceptionOpen": True,
                        "customerHasCsr": bool(
                            _pick(customer or {}, "csrName", "csr_name", "csrFolder", "csr_folder", default="")
                        ),
                    }
                )
            actions.append({"key": "emailSubject", "label": "Update email subject", "requires": ["subject"], "keepsExceptionOpen": True})
            actions.append({"key": "manualCategory", "label": "Apply category", "requires": ["category"], "keepsExceptionOpen": True})
            actions.append({"key": "emailReprocess", "label": "Reprocess email from start", "requires": [], "reactivatesEmail": True})
            actions.append({"key": "forceOrder", "label": "Force to order processor", "requires": ["processorProfileId"], "reactivatesEmail": True})
            actions.append({"key": "moveEmail", "label": "Move email and complete", "requires": ["folder"], "completesException": True})
        if task_type == "itemValidation":
            actions.append({"key": "item", "label": "Set ERP item", "requires": ["matchedInternalItemNumber"]})
        if task_type in {"parserFailure", "outputGeneration"} and order_id:
            actions.append({"key": "orderReprocess", "label": "Reprocess order", "requires": []})
        if not actions:
            actions.append({"key": "notes", "label": "Resolve with notes", "requires": ["notes"]})
        return actions

    @staticmethod
    def _monitor_recipient(email: dict[str, Any]) -> str:
        source = _as_dict(_pick(email, "source", default={}))
        recipients = [str(value) for value in _as_list(_pick(source, "toRecipients", "to_recipients", default=[])) if str(value).strip()]
        return "; ".join(recipients) or str(_pick(email, "mailbox", default=""))

    @staticmethod
    def _monitor_category(email: dict[str, Any]) -> str:
        categories = [str(value) for value in _as_list(_pick(email, "categories", default=[])) if str(value).strip()]
        if categories:
            return ", ".join(categories)
        routing = _as_dict(_pick(email, "routing", default={}))
        return str(_pick(routing, "outcome", default=""))

    @staticmethod
    def _monitor_moved_to(graph_actions: dict[str, Any]) -> str:
        for item in _as_list(_pick(graph_actions, "applied", default=[])):
            if not isinstance(item, dict) or _pick(item, "action", default="") != "move":
                continue
            return str(_pick(item, "folderName", "folder_name", "destinationId", default=""))
        return ""

    @staticmethod
    def _monitor_email_url(email: dict[str, Any], graph_actions: dict[str, Any]) -> str:
        source = _as_dict(_pick(email, "source", default={}))
        web_link = str(_pick(source, "webLink", "web_link", default=""))
        if web_link:
            return web_link
        for item in _as_list(_pick(graph_actions, "applied", default=[])):
            if isinstance(item, dict) and _pick(item, "action", default="") == "move":
                moved_id = str(_pick(item, "movedGraphMessageId", "moved_graph_message_id", default=""))
                if moved_id:
                    return ""
        return ""

    @staticmethod
    def _monitor_action_summary(
        email: dict[str, Any],
        order: dict[str, Any] | None,
        graph_actions: dict[str, Any],
    ) -> str:
        parts: list[str] = []
        source = _as_dict(_pick(email, "source", default={}))
        manual_override = _as_dict(_pick(source, "manualOverride", "manual_override", default={}))
        if manual_override:
            action_taken = str(_pick(manual_override, "actionTaken", "action_taken", default="") or "").strip()
            reason = str(_pick(manual_override, "reason", default="") or "").strip()
            if action_taken:
                parts.append(action_taken)
            elif reason == "exceptionDisregarded":
                parts.append("manual override")
            elif reason == "exceptionManualMove":
                parts.append("manual move")
        if order:
            status = _document_status(order)
            parts.append(f"Order {status}" if status else "Order created")
        patched = _as_dict(_pick(graph_actions, "patched", default={}))
        if "subject" in patched:
            parts.append("subject updated")
        if "categories" in patched:
            parts.append("category applied")
        moved_to = OrderProcessorApi._monitor_moved_to(graph_actions)
        if moved_to:
            parts.append(f"moved to {moved_to}")
        if not parts:
            status = str(_pick(graph_actions, "status", default="")).strip()
            if status:
                parts.append(f"Graph actions {status}")
        if not parts:
            routing = _as_dict(_pick(email, "routing", default={}))
            outcome = str(_pick(routing, "outcome", default="")).strip()
            if outcome:
                parts.append(outcome)
        return "; ".join(parts)

    def console_dashboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.console_session(payload)
        if not session.get("authorized"):
            return {"session": session}

        tenant_id = session["tenantId"]
        dashboard_view = str(_pick(payload, "view", "dashboardView", "dashboard_view", default="full") or "full").lower()
        compact_monitor = dashboard_view == "monitor"
        config_view = dashboard_view == "config"
        requested_customer_id = _pick(payload, "customerId", "customer_id", default=None)
        customer_filter = self._authorized_customer_filter(session, requested_customer_id)
        if customer_filter == "__denied__":
            return {"session": session, "error": "forbidden", "message": "Customer is outside this user's assignments."}

        tenant = self.repository.get("tenants", tenant_id) or {
            "id": tenant_id,
            "tenantId": tenant_id,
            "name": tenant_id,
            "environment": "",
            "status": "active",
            "settings": {},
        }
        system_tenant = self.repository.get("tenants", SYSTEM_TENANT_ID) or {
            "id": SYSTEM_TENANT_ID,
            "tenantId": SYSTEM_TENANT_ID,
            "name": "System Settings",
            "environment": "system",
            "status": "active",
            "settings": {},
        }
        distributor_customers = self._cached_console_value(
            ("distributorCustomers", tenant_id, tuple(_as_list(session.get("permissions", [])))),
            lambda: self._distributor_customers_for_console(session, tenant),
        )
        item_stats = self._query_console_stats("items", tenant_id)
        customer_stats = self._query_console_stats("customers", tenant_id)
        csr_directory = self._csr_directory_for_console(tenant_id, tenant)
        lightweight_processor_profiles = self._cached_console_value(
            ("processorProfiles", tenant_id, str(customer_filter)),
            lambda: self._filter_customer_documents(
                self.repository.query_by_tenant("processorProfiles", tenant_id),
                customer_filter,
                session,
                include_global=True,
            ),
        )

        if compact_monitor:
            monitor_page = self._query_console_page(
                "monitorRecords",
                tenant_id,
                fields=CONSOLE_MONITOR_RECORD_FIELDS,
                search_fields=["sender", "recipient", "subject", "customerCode", "customerName", "csr", "status", "pathway"],
                order_by="updatedAt",
                descending=True,
                payload={**payload, "limit": _pick(payload, "limit", default=250)},
                customer_filter=customer_filter,
            )
            monitor = self._monitor_sections_from_records(monitor_page["items"])
            if tenant_id not in self._monitor_backfill_checked_tenants and int(monitor_page.get("total", 0) or 0) < 250:
                monitor = self._backfill_monitor_records_for_console(tenant_id, customer_filter, session)
                self._monitor_backfill_checked_tenants.add(tenant_id)
            return {
                "session": session,
                "dashboardView": "monitor",
                "tenant": tenant,
                "systemSettings": dict(_pick(system_tenant, "settings", default={}) or {}),
                "distributorCustomers": distributor_customers,
                "summary": self._console_summary_from_monitor(monitor, item_stats),
                "observabilityMetrics": {},
                "recentAuditEvents": [],
                "monitor": monitor,
                "monitorPage": monitor_page,
                "activeRuns": monitor["active"],
                "processedOrders": monitor["processedOrders"],
                "exceptionQueue": monitor["exceptions"],
                "mailboxes": [],
                "customerIdentificationRules": [],
                "routingRules": [],
                "customers": [],
                "items": [],
                "customerListStats": customer_stats,
                "itemListStats": item_stats,
                "customerDataStatus": [],
                "itemDataStatus": [],
                "importTargets": {},
                "csrDirectory": csr_directory,
                "processorProfiles": lightweight_processor_profiles,
                "outputProfiles": [],
                "microsoftAuthConnections": [],
                "outputArtifacts": [],
            }

        customers = [] if config_view else self._filter_customer_documents(
            self._query_console_records("customers", tenant_id, CONSOLE_CUSTOMER_FIELDS),
            customer_filter,
            session,
        )
        order_runs = [] if config_view else self._filter_customer_documents(
            self.repository.query_by_tenant("orderRuns", tenant_id),
            customer_filter,
            session,
        )
        email_messages = [] if config_view else self._filter_customer_documents(
            self._query_console_records("emailMessages", tenant_id, CONSOLE_EMAIL_FIELDS),
            customer_filter,
            session,
        )
        exceptions = [] if config_view else self._filter_exceptions_for_console(
            self.repository.query_by_tenant("exceptionTasks", tenant_id),
            order_runs,
            customer_filter,
            session,
        )
        if compact_monitor or config_view:
            mailboxes: list[dict[str, Any]] = []
            customer_identification_rules: list[dict[str, Any]] = []
            routing_rules: list[dict[str, Any]] = []
            items: list[dict[str, Any]] = []
            audit_events: list[dict[str, Any]] = []
            processor_profiles: list[dict[str, Any]] = []
            output_profiles: list[dict[str, Any]] = []
            microsoft_auth_connections: list[dict[str, Any]] = []
            if config_view:
                mailboxes = self._cached_console_value(
                    ("mailboxes", tenant_id, str(customer_filter)),
                    lambda: self._filter_customer_documents(
                        self.repository.query_by_tenant("mailboxAccounts", tenant_id),
                        customer_filter,
                        session,
                        include_global=True,
                    ),
                )
                customer_identification_rules = self._cached_console_value(
                    ("customerAliases", tenant_id, str(customer_filter)),
                    lambda: self._filter_customer_documents(
                        self.repository.query_by_tenant("customerAliases", tenant_id),
                        customer_filter,
                        session,
                    ),
                )
                routing_rules = self._cached_console_value(
                    ("routingRules", tenant_id, str(customer_filter)),
                    lambda: self._filter_customer_documents(
                        self.repository.query_by_tenant("routingRules", tenant_id),
                        customer_filter,
                        session,
                        include_global=True,
                    ),
                )
                processor_profiles = self._cached_console_value(
                    ("processorProfiles", tenant_id, str(customer_filter)),
                    lambda: self._filter_customer_documents(
                        self.repository.query_by_tenant("processorProfiles", tenant_id),
                        customer_filter,
                        session,
                        include_global=True,
                    ),
                )
                output_profiles = self._cached_console_value(
                    ("outputProfiles", tenant_id, str(customer_filter)),
                    lambda: self._filter_customer_documents(
                        self.repository.query_by_tenant("outputProfiles", tenant_id),
                        customer_filter,
                        session,
                        include_global=True,
                    ),
                )
                microsoft_auth_connections = self._cached_console_value(
                    ("microsoftAuthConnections", tenant_id, str(customer_filter)),
                    lambda: self._filter_customer_documents(
                        self.repository.query_by_tenant("microsoftAuthConnections", tenant_id),
                        customer_filter,
                        session,
                        include_global=True,
                    ),
                )
                monitor_page = self._query_console_page(
                    "monitorRecords",
                    tenant_id,
                    fields=CONSOLE_MONITOR_RECORD_FIELDS,
                    search_fields=[],
                    order_by="updatedAt",
                    descending=True,
                    payload={**payload, "limit": 250},
                    customer_filter=customer_filter,
                )
                monitor = self._monitor_sections_from_records(monitor_page["items"])
                if tenant_id not in self._monitor_backfill_checked_tenants and int(monitor_page.get("total", 0) or 0) < 250:
                    monitor = self._backfill_monitor_records_for_console(tenant_id, customer_filter, session)
                    self._monitor_backfill_checked_tenants.add(tenant_id)
                return {
                    "session": session,
                    "dashboardView": "config",
                    "tenant": tenant,
                    "systemSettings": dict(_pick(system_tenant, "settings", default={}) or {}),
                    "distributorCustomers": distributor_customers,
                    "summary": self._console_summary_from_monitor(monitor, item_stats),
                    "observabilityMetrics": {},
                    "recentAuditEvents": [],
                    "monitor": monitor,
                    "monitorPage": monitor_page,
                    "activeRuns": monitor["active"],
                    "processedOrders": monitor["processedOrders"],
                    "exceptionQueue": monitor["exceptions"],
                    "mailboxes": self._sort_recent(mailboxes),
                    "customerIdentificationRules": self._sort_recent(customer_identification_rules),
                    "routingRules": sorted(
                        routing_rules,
                        key=lambda rule: int(_pick(rule, "priority", default=100) or 100),
                    ),
                    "customers": [],
                    "items": [],
                    "customerListStats": customer_stats,
                    "itemListStats": item_stats,
                    "customerDataStatus": [],
                    "itemDataStatus": [],
                    "importTargets": self._console_import_targets(tenant_id),
                    "csrDirectory": csr_directory,
                    "processorProfiles": processor_profiles,
                    "outputProfiles": output_profiles,
                    "microsoftAuthConnections": microsoft_auth_connections,
                    "outputArtifacts": [],
                }
        else:
            mailboxes = self._filter_customer_documents(
                self.repository.query_by_tenant("mailboxAccounts", tenant_id),
                customer_filter,
                session,
                include_global=True,
            )
            customer_identification_rules = self._filter_customer_documents(
                self.repository.query_by_tenant("customerAliases", tenant_id),
                customer_filter,
                session,
            )
            routing_rules = self._filter_customer_documents(
                self.repository.query_by_tenant("routingRules", tenant_id),
                customer_filter,
                session,
                include_global=True,
            )
            items = self._filter_customer_documents(
                self._query_console_records("items", tenant_id, CONSOLE_ITEM_FIELDS),
                customer_filter,
                session,
                include_global=True,
            )
            audit_events = self._filter_audit_events_for_console(
                self.repository.query_by_tenant("auditEvents", tenant_id),
                order_runs,
                exceptions,
                customer_filter,
                session,
            )
            processor_profiles = self._filter_customer_documents(
                self.repository.query_by_tenant("processorProfiles", tenant_id),
                customer_filter,
                session,
                include_global=True,
            )
            output_profiles = self._filter_customer_documents(
                self.repository.query_by_tenant("outputProfiles", tenant_id),
                customer_filter,
                session,
                include_global=True,
            )
            microsoft_auth_connections = self._filter_customer_documents(
                self.repository.query_by_tenant("microsoftAuthConnections", tenant_id),
                customer_filter,
                session,
                include_global=True,
            )

        active_statuses = {
            ProcessingStatus.RECEIVED.value,
            ProcessingStatus.ROUTED.value,
            ProcessingStatus.PROCESSING.value,
            ProcessingStatus.NEEDS_REVIEW.value,
        }
        active_runs = [order for order in order_runs if _document_status(order) in active_statuses]
        processed_orders = [order for order in order_runs if _document_status(order) not in active_statuses]
        output_artifacts = [
            {
                "orderRunId": order["id"],
                "customerId": _document_customer_id(order),
                **artifact,
            }
            for order in order_runs
            for artifact in _as_list(_pick(order, "outputArtifacts", "output_artifacts", default=[]))
            if isinstance(artifact, dict)
        ]

        monitor = self._monitor_sections(email_messages, order_runs, exceptions, customers)
        summary = (
            self._console_summary_from_monitor(monitor, item_stats)
            if config_view
            else self._console_summary(order_runs, exceptions, items, email_messages)
        )
        observability_metrics = dashboard_observability_metrics(order_runs, exceptions, audit_events)
        summary.update(
            {
                "customerIdentificationFailureCount": observability_metrics["customerIdentificationFailureCount"],
                "processorFailureCount": observability_metrics["processorFailureCount"],
                "outputGenerationFailureCount": observability_metrics["outputGenerationFailureCount"],
                "averageProcessingLatencyMs": observability_metrics["processingLatency"]["averageMs"],
                "p95ProcessingLatencyMs": observability_metrics["processingLatency"]["p95Ms"],
            }
        )
        return {
            "session": session,
            "dashboardView": "config" if config_view else "full",
            "tenant": tenant,
            "systemSettings": dict(_pick(system_tenant, "settings", default={}) or {}),
            "distributorCustomers": distributor_customers,
            "summary": summary,
            "observabilityMetrics": observability_metrics,
            "recentAuditEvents": self._sort_recent(audit_events)[:50],
            "monitor": monitor,
            "activeRuns": self._sort_recent(active_runs),
            "processedOrders": self._sort_recent(processed_orders),
            "exceptionQueue": self._sort_recent(
                [item for item in exceptions if _document_status(item) == ExceptionStatus.OPEN.value]
            ),
            "mailboxes": self._sort_recent(mailboxes),
            "customerIdentificationRules": self._sort_recent(customer_identification_rules),
            "routingRules": sorted(routing_rules, key=lambda rule: int(_pick(rule, "priority", default=100) or 100)),
            "customers": sorted(customers, key=lambda customer: str(_pick(customer, "name", default="")).lower()),
            "items": sorted(items, key=lambda item: str(_pick(item, "internalItemNumber", "id", default="")).lower()),
            "customerListStats": customer_stats,
            "itemListStats": item_stats,
            "customerDataStatus": self._customer_data_status(customers),
            "itemDataStatus": self._item_data_status(items),
            "importTargets": self._console_import_targets(tenant_id),
            "csrDirectory": csr_directory,
            "processorProfiles": processor_profiles,
            "outputProfiles": output_profiles,
            "microsoftAuthConnections": microsoft_auth_connections,
            "outputArtifacts": output_artifacts,
        }

    def console_data(self, section: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.console_session(payload)
        if not session.get("authorized"):
            return {"session": session}

        tenant_id = session["tenantId"]
        requested_customer_id = _pick(payload, "customerId", "customer_id", default=None)
        customer_filter = self._authorized_customer_filter(session, requested_customer_id)
        if customer_filter == "__denied__":
            return {"session": session, "error": "forbidden", "message": "Customer is outside this user's assignments."}

        section_key = str(section or "").strip().lower()
        if section_key in {"customers", "customer-list", "customerlist"}:
            if isinstance(customer_filter, set) and customer_filter:
                records = []
                for customer_id in sorted(customer_filter):
                    document = self.repository.get("customers", customer_id)
                    if document and str(_pick(document, "tenantId", "tenant_id", default="")) == tenant_id:
                        records.append({field: document[field] for field in CONSOLE_CUSTOMER_FIELDS if field in document})
                search = str(_pick(payload, "search", "q", default="") or "").strip().lower()
                if search:
                    records = [
                        record for record in records
                        if any(search in str(_pick(record, field, default="")).lower() for field in CONSOLE_CUSTOMER_SEARCH_FIELDS)
                    ]
                page = self._console_page_request(payload)
                total = len(records)
                items = records[page["offset"] : page["offset"] + page["limit"]]
                result = {"items": items, "total": total, "limit": page["limit"], "offset": page["offset"], "search": page["search"]}
                result["hasNext"] = page["offset"] + len(items) < total
                result["hasPrevious"] = page["offset"] > 0
            else:
                result = self._query_console_page(
                    "customers",
                    tenant_id,
                    fields=CONSOLE_CUSTOMER_FIELDS,
                    search_fields=CONSOLE_CUSTOMER_SEARCH_FIELDS,
                    order_by="name",
                    descending=False,
                    payload=payload,
                )
            return {"session": session, "section": "customers", "customers": result["items"], "page": result}

        if section_key in {"items", "item-list", "itemlist"}:
            result = self._query_console_page(
                "items",
                tenant_id,
                fields=CONSOLE_ITEM_FIELDS,
                search_fields=CONSOLE_ITEM_SEARCH_FIELDS,
                order_by="internalItemNumber",
                descending=False,
                payload=payload,
                customer_filter={GLOBAL_CUSTOMER_ID},
                include_global=True,
            )
            return {"session": session, "section": "items", "items": result["items"], "page": result}

        if section_key in {"outputs", "artifacts"}:
            page = self._console_page_request(payload)
            order_page = self._query_console_page(
                "orderRuns",
                tenant_id,
                fields=[],
                search_fields=["id", "poNumber", "orderNumber", "customerId"],
                order_by="updatedAt",
                descending=True,
                payload=payload,
                customer_filter=customer_filter,
            )
            artifacts = [
                {
                    "orderRunId": order["id"],
                    "customerId": _document_customer_id(order),
                    **artifact,
                }
                for order in order_page["items"]
                for artifact in _as_list(_pick(order, "outputArtifacts", "output_artifacts", default=[]))
                if isinstance(artifact, dict)
            ]
            return {
                "session": session,
                "section": "outputs",
                "outputArtifacts": artifacts,
                "page": {
                    **page,
                    "total": order_page["total"],
                    "hasNext": order_page["hasNext"],
                    "hasPrevious": order_page["hasPrevious"],
                },
            }

        if section_key == "monitor":
            monitor_page = self._query_console_page(
                "monitorRecords",
                tenant_id,
                fields=CONSOLE_MONITOR_RECORD_FIELDS,
                search_fields=["sender", "recipient", "subject", "customerCode", "customerName", "csr", "status", "pathway"],
                order_by="updatedAt",
                descending=True,
                payload={**payload, "limit": _pick(payload, "limit", default=250)},
                customer_filter=customer_filter,
            )
            monitor = self._monitor_sections_from_records(monitor_page["items"])
            return {
                "session": session,
                "section": "monitor",
                "monitor": monitor,
                "page": monitor_page,
                "summary": self._console_summary_from_monitor(monitor, self._query_console_stats("items", tenant_id)),
                "csrDirectory": self._csr_directory_for_console(tenant_id, self.repository.get("tenants", tenant_id) or {}),
                "processorProfiles": self._filter_customer_documents(
                    self.repository.query_by_tenant("processorProfiles", tenant_id),
                    customer_filter,
                    session,
                    include_global=True,
                ),
            }

        if section_key in {"costs", "billing"}:
            cost_payload = dict(payload)
            if isinstance(customer_filter, set):
                cost_payload["customerIds"] = sorted(customer_filter)
            summary = self.cost_summary(cost_payload)
            return {
                "session": session,
                "section": "costs",
                "costs": summary,
                "costSources": summary["costSources"],
                "period": summary["period"],
                "rows": summary["rows"],
            }

        return {"session": session, "error": "unknownSection", "message": f"Unknown console data section {section}."}

    def order_observability_timeline(self, order_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        order = self.repository.get("orderRuns", order_run_id)
        if order is None:
            return {"error": "notFound", "message": f"Order run {order_run_id} was not found."}

        tenant_id = _pick(payload, "tenantId", "tenant_id", default=_pick(order, "tenantId", "tenant_id", default="default"))
        email_message_id = _pick(order, "emailMessageId", "email_message_id", default="")
        email = self.repository.get("emailMessages", email_message_id) if email_message_id else None
        exceptions = [
            task
            for task in self.repository.query_by_tenant("exceptionTasks", tenant_id)
            if _pick(task, "orderRunId", "order_run_id", default="") == order_run_id
            or _pick(task, "emailMessageId", "email_message_id", default="") == email_message_id
        ]
        audit_events = self.repository.query_by_tenant("auditEvents", tenant_id)
        return {
            "orderRun": order,
            "emailMessage": email,
            "timeline": order_timeline(order, email, exceptions, audit_events),
        }

    def console_order_timeline(self, order_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.console_session(payload)
        if not session.get("authorized"):
            return {"session": session}

        order = self.repository.get("orderRuns", order_run_id)
        if order is None:
            return {"session": session, "error": "notFound", "message": f"Order run {order_run_id} was not found."}
        if not self._session_can_access_customer(session, _document_customer_id(order)):
            return {"session": session, "error": "forbidden", "message": "Order is outside this user's assignments."}

        result = self.order_observability_timeline(order_run_id, payload)
        result["session"] = session
        return result

    def console_output_artifact(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.console_session(payload)
        if not session.get("authorized"):
            return {"session": session}

        tenant_id = session["tenantId"]
        order_run_id = _pick(payload, "orderRunId", "order_run_id", default="")
        artifact_id = _pick(payload, "artifactId", "artifact_id", default="")
        blob_url = _pick(payload, "blobUrl", "blob_url", default="")
        order = self.repository.get("orderRuns", order_run_id) if order_run_id else None
        if order is None and blob_url:
            order = self._order_for_artifact_url(tenant_id, blob_url)
        if order is None:
            return {"session": session, "error": "notFound", "message": "Output artifact order was not found."}
        if not self._session_can_access_customer(session, _document_customer_id(order)):
            return {"session": session, "error": "forbidden", "message": "Artifact is outside this user's assignments."}

        artifact = None
        for candidate in _as_list(_pick(order, "outputArtifacts", "output_artifacts", default=[])):
            if not isinstance(candidate, dict):
                continue
            if artifact_id and _pick(candidate, "id", default="") == artifact_id:
                artifact = candidate
                break
            if blob_url and _pick(candidate, "blobUrl", "blob_url", default="") == blob_url:
                artifact = candidate
                break
        if artifact is None:
            return {"session": session, "error": "notFound", "message": "Output artifact was not found."}

        response = {"session": session, "artifact": artifact}
        object_store = getattr(self.output_artifact_store, "objects", None)
        artifact_url = _pick(artifact, "blobUrl", "blob_url", default="")
        if isinstance(object_store, dict) and artifact_url in object_store:
            content = object_store[artifact_url]
            content_type = str(_pick(artifact, "contentType", "content_type", default=""))
            if content_type.startswith("text/") or content_type == "application/json":
                response["content"] = content.decode("utf-8")
            else:
                response["contentBase64"] = base64.b64encode(content).decode("ascii")
        observability = correlation_context(payload, artifact_id or artifact_url or _pick(order, "id", default=""))
        self._audit(
            tenant_id,
            "output.artifactAccessed",
            observability["correlationId"],
            _pick(artifact, "id", default=artifact_url),
            {
                "orderRunId": _pick(order, "id", default=""),
                "customerId": _document_customer_id(order),
                "artifactId": _pick(artifact, "id", default=""),
                "artifactType": _pick(artifact, "type", default=""),
                "fileName": _pick(artifact, "fileName", "file_name", default=""),
                "userIntervention": True,
                "observability": observability,
            },
            customer_id=_document_customer_id(order),
            order_run_id=_pick(order, "id", default=""),
            actor=self._actor_from_payload(payload),
        )
        return response

    def console_upsert_mailbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        error = self._console_permission_error(payload, "manageMailboxes")
        if error:
            return error
        result = self.upsert_mailbox(payload)
        result["session"] = self.console_session(payload)
        return result

    def console_start_microsoft_auth(self, payload: dict[str, Any]) -> dict[str, Any]:
        error = self._console_permission_error(payload, "manageMailboxes")
        if error:
            return error

        tenant_id = _pick(payload, "targetTenantId", "target_tenant_id", "tenantId", "tenant_id", default="default")
        mailbox_id = _pick(payload, "mailboxAccountId", "mailbox_account_id", default="")
        mailbox = self.repository.get("mailboxAccounts", mailbox_id) if mailbox_id else None
        mailbox_address = _pick(payload, "mailboxAddress", "mailbox_address", default=None)
        if mailbox is not None:
            mailbox_address = _pick(mailbox, "mailboxAddress", "mailbox_address", default=mailbox_address)
        if not mailbox_address:
            return {"error": "mailboxRequired", "message": "Save the shared mailbox before authorizing Microsoft access."}
        connection_id = _pick(
            payload,
            "connectionId",
            "connection_id",
            default=_pick(mailbox or {}, "connectionId", "connection_id", default=""),
        )
        if not connection_id:
            connection_id = stable_id(tenant_id, "microsoft365", mailbox_address or "shared-mailbox")
        redirect_uri = _pick(payload, "redirectUri", "redirect_uri", default="")
        config = config_from_environment(redirect_uri)
        if not config.client_id:
            return {
                "error": "microsoftAuthNotConfigured",
                "message": "ORDER_PROCESSOR_MICROSOFT_AUTH_CLIENT_ID is required before mailbox authorization can start.",
            }
        if not config.redirect_uri:
            return {
                "error": "microsoftAuthRedirectNotConfigured",
                "message": "Configure ORDER_PROCESSOR_MICROSOFT_AUTH_REDIRECT_URI or pass redirectUri from the console.",
            }

        session = self.console_session(payload)
        actor_email = _pick(session.get("principal", {}), "email", default="").lower()
        authorized_user_email = _normalized_email(
            _pick(payload, "authorizedUserEmail", "authorized_user_email", "loginHint", "login_hint", default="")
        )
        state = sign_state(
            {
                "tenantId": tenant_id,
                "connectionId": connection_id,
                "mailboxAccountId": mailbox_id,
                "mailboxAddress": mailbox_address or "",
                "requestedBy": authorized_user_email or actor_email,
                "initiatedBy": actor_email,
                "authorizedUserEmail": authorized_user_email,
                "redirectUri": config.redirect_uri,
                "returnTo": _pick(payload, "returnTo", "return_to", default="/"),
            },
            state_secret_from_environment(),
        )
        authorization_url = build_authorization_url(config, state, prompt="select_account", login_hint=authorized_user_email)
        connection = MicrosoftAuthConnection(
            id=connection_id,
            tenant_id=tenant_id,
            customer_id=GLOBAL_CUSTOMER_ID,
            provider="microsoft365",
            display_name=_pick(payload, "displayName", "display_name", default=mailbox_address or "Microsoft 365"),
            owner_email=authorized_user_email or actor_email,
            connection_type="delegated",
            status=AuthConnectionStatus.NEEDS_CONSENT,
            scopes=config.scopes,
            power_automate_connection_reference=_pick(payload, "powerAutomateConnectionReference", default=""),
            tenant_authority=config.tenant_authority,
            metadata={
                "mailboxAccountId": mailbox_id,
                "mailboxAddress": mailbox_address or "",
                "redirectUri": config.redirect_uri,
                "authMethod": "delegatedAuthorizationCode",
                "authorizedUserEmail": authorized_user_email,
                "initiatedBy": actor_email,
            },
        )
        stored = self.repository.upsert("microsoftAuthConnections", to_dict(connection))
        return {
            "authorizationUrl": authorization_url,
            "microsoftAuthConnection": stored,
            "stateExpiresInSeconds": 900,
            "session": session,
        }

    def console_complete_microsoft_auth(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = _pick(payload, "state", default="")
        code = _pick(payload, "code", default="")
        provider_error = _pick(payload, "error", default="")
        try:
            state_payload = verify_state(
                state,
                state_secret_from_environment(_pick(payload, "clientId", "client_id", default="")),
            )
        except MicrosoftGraphError as exc:
            return {"error": "invalidMicrosoftAuthState", "message": str(exc)}

        tenant_id = _pick(state_payload, "tenantId", default=_pick(payload, "tenantId", "tenant_id", default="default"))
        connection_id = _pick(state_payload, "connectionId", default="")
        mailbox_id = _pick(state_payload, "mailboxAccountId", default="")
        mailbox_address = _pick(state_payload, "mailboxAddress", default="")
        redirect_uri = _pick(state_payload, "redirectUri", default=_pick(payload, "redirectUri", "redirect_uri", default=""))
        config = config_from_environment(redirect_uri)
        existing = self.repository.get("microsoftAuthConnections", connection_id) or {}

        if provider_error:
            failed = dict(existing)
            failed.update({"status": AuthConnectionStatus.FAILED.value, "updatedAt": utc_now()})
            failed.setdefault("id", connection_id)
            failed.setdefault("tenantId", tenant_id)
            failed.setdefault("customerId", GLOBAL_CUSTOMER_ID)
            failed.setdefault("provider", "microsoft365")
            failed["metadata"] = {
                **dict(_pick(failed, "metadata", default={}) or {}),
                "error": provider_error,
                "errorDescription": _pick(payload, "errorDescription", "error_description", default=""),
            }
            stored = self.repository.upsert("microsoftAuthConnections", failed)
            return {"error": provider_error, "microsoftAuthConnection": stored}
        if not code:
            return {"error": "missingCode", "message": "Microsoft callback did not include an authorization code."}

        try:
            client_secret = self._microsoft_client_secret(config)
            token = exchange_authorization_code(config, code, client_secret)
        except MicrosoftGraphError as exc:
            return {
                "error": "microsoftTokenExchangeFailed",
                "message": str(exc),
                "statusCode": exc.status_code,
                "details": exc.details,
            }

        secrets = self._store_microsoft_tokens(connection_id, token)
        access_token = str(token.get("access_token", ""))
        authorized_user: dict[str, Any] = {}
        authorized_email = _normalized_email(_pick(state_payload, "authorizedUserEmail", "requestedBy", default=""))
        if access_token:
            try:
                authorized_user = graph_get(
                    access_token,
                    "https://graph.microsoft.com/v1.0/me?$select=id,displayName,userPrincipalName,mail",
                )
                authorized_email = _normalized_email(
                    _pick(authorized_user, "mail", "userPrincipalName", default=authorized_email)
                )
            except MicrosoftGraphError:
                authorized_user = {}
        mailbox_test = (
            test_shared_mailbox_access(access_token, mailbox_address)
            if access_token and mailbox_address
            else {"status": "notTested", "canAccess": False, "message": "Mailbox address was not supplied."}
        )
        connection = MicrosoftAuthConnection(
            id=connection_id,
            tenant_id=tenant_id,
            customer_id=GLOBAL_CUSTOMER_ID,
            provider="microsoft365",
            display_name=_pick(existing, "displayName", "display_name", default=mailbox_address or "Microsoft 365"),
            owner_email=authorized_email or _pick(existing, "ownerEmail", "owner_email", default=""),
            connection_type="delegated",
            status=AuthConnectionStatus.ACTIVE if mailbox_test.get("canAccess") else AuthConnectionStatus.CONFIGURED,
            scopes=list(_as_list(token.get("scope", " ".join(config.scopes)).split())),
            key_vault_secret_names=secrets,
            tenant_authority=config.tenant_authority,
            consented_by=authorized_email,
            consented_at=utc_now(),
            expires_at=token_expiry(token.get("expires_in")),
            metadata={
                **dict(_pick(existing, "metadata", default={}) or {}),
                "mailboxAccountId": mailbox_id,
                "mailboxAddress": mailbox_address,
                "authMethod": "delegatedAuthorizationCode",
                "mailboxAccess": mailbox_test,
                "authorizedUser": authorized_user,
                "authorizedUserEmail": authorized_email,
                "initiatedBy": _pick(state_payload, "initiatedBy", default=""),
            },
        )
        stored_connection = self.repository.upsert("microsoftAuthConnections", to_dict(connection))
        stored_mailbox = None
        if mailbox_id:
            stored_mailbox = self._update_mailbox_after_graph_auth(
                mailbox_id,
                connection_id,
                mailbox_test,
                authorized_by=authorized_email,
            )
        self._audit(
            tenant_id,
            "microsoftAuthConnection.authorized",
            connection_id,
            connection_id,
            {"connectionId": connection_id, "mailboxAccountId": mailbox_id, "mailboxAccess": mailbox_test},
        )
        return {
            "microsoftAuthConnection": stored_connection,
            "mailboxAccount": stored_mailbox,
            "mailboxAccess": mailbox_test,
            "returnTo": _pick(state_payload, "returnTo", default="/"),
        }

    def console_test_mailbox_connection(self, mailbox_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        error = self._console_permission_error(payload, "manageMailboxes")
        if error:
            return error
        result = self.test_mailbox_connection(mailbox_id, payload)
        result["session"] = self.console_session(payload)
        return result

    def console_upsert_tenant_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        error = self._console_permission_error(payload, "manageCustomers")
        if error:
            return error
        result = self.upsert_tenant_config(payload)
        result["session"] = self.console_session(payload)
        return result

    def console_upsert_routing_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        error = self._console_permission_error(
            payload,
            "manageRouting",
            _pick(payload, "customerId", "customer_id", default=None),
        )
        if error:
            return error
        result = self.upsert_routing_rule(payload)
        result["session"] = self.console_session(payload)
        return result

    def console_upsert_customer_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        error = self._console_permission_error(
            payload,
            "manageCustomers",
            _pick(payload, "id", "customerId", "customer_id", default=None),
        )
        if error:
            return error
        result = self.upsert_customer_config(payload)
        result["session"] = self.console_session(payload)
        return result

    def console_upsert_customer_identification_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        error = self._console_permission_error(
            payload,
            "manageCustomers",
            _pick(payload, "customerId", "customer_id", default=None),
        )
        if error:
            return error
        result = self.upsert_customer_identification_rule(payload)
        result["session"] = self.console_session(payload)
        return result

    def console_upsert_processor_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        error = self._console_permission_error(
            payload,
            "manageCustomers",
            _pick(payload, "customerId", "customer_id", default=None),
        )
        if error:
            return error
        result = self.upsert_processor_profile(payload)
        result["session"] = self.console_session(payload)
        return result

    def console_upsert_output_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        error = self._console_permission_error(
            payload,
            "manageCustomers",
            _pick(payload, "customerId", "customer_id", default=None),
        )
        if error:
            return error
        result = self.upsert_output_profile(payload)
        result["session"] = self.console_session(payload)
        return result

    def console_upsert_console_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        error = self._console_permission_error(payload, "manageUsers")
        if error:
            return error
        result = self.upsert_console_user(payload)
        result["session"] = self.console_session(payload)
        return result

    def console_assign_customer_user(self, customer_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        error = self._console_permission_error(payload, "manageUsers", customer_id)
        if error:
            return error
        result = self.assign_customer_user(customer_id, payload)
        result["session"] = self.console_session(payload)
        return result

    def console_resolve_exception(self, exception_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.console_session(payload)
        if not session.get("authorized"):
            return {"session": session}
        if "resolveExceptions" not in set(_as_list(session.get("permissions", []))):
            return {"session": session, "error": "forbidden", "message": "User cannot resolve exceptions."}

        task = self.repository.get("exceptionTasks", exception_id)
        if task is None:
            return {"session": session, "error": "notFound", "message": f"Exception task {exception_id} was not found."}
        if not self._session_can_access_customer(session, self._exception_customer_id(task)):
            return {"session": session, "error": "forbidden", "message": "Exception is outside this user's assignments."}
        result = self.resolve_exception(exception_id, payload)
        result["session"] = session
        return result

    def console_clear_active_processing_run(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.console_session(payload)
        if not session.get("authorized"):
            return {"session": session}
        if "resolveExceptions" not in set(_as_list(session.get("permissions", []))):
            return {"session": session, "error": "forbidden", "message": "User cannot clear active processing rows."}

        email = self.repository.get("emailMessages", run_id)
        order = None
        if email:
            order_id = str(_pick(email, "orderRunId", "order_run_id", default="") or "")
            order = self.repository.get("orderRuns", order_id) if order_id else None
        else:
            order = self.repository.get("orderRuns", run_id)
            email_id = str(_pick(order or {}, "emailMessageId", "email_message_id", default="") or "")
            email = self.repository.get("emailMessages", email_id) if email_id else None
        if not email and not order:
            return {"session": session, "error": "notFound", "message": f"Active processing row {run_id} was not found."}
        customer_id = _document_customer_id(email or {}) or _document_customer_id(order or {})
        if not self._session_can_access_customer(session, customer_id):
            return {"session": session, "error": "forbidden", "message": "Active processing row is outside this user's assignments."}

        result = self.clear_active_processing_run(run_id, {**payload, "source": "console"})
        result["session"] = session
        return result

    def console_reprocess_order(self, order_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.console_session(payload)
        if not session.get("authorized"):
            return {"session": session}
        if "reprocessOrders" not in set(_as_list(session.get("permissions", []))):
            return {"session": session, "error": "forbidden", "message": "User cannot reprocess orders."}

        order = self.repository.get("orderRuns", order_run_id)
        if order is None:
            return {"session": session, "error": "notFound", "message": f"Order run {order_run_id} was not found."}
        if not self._session_can_access_customer(session, _document_customer_id(order)):
            return {"session": session, "error": "forbidden", "message": "Order is outside this user's assignments."}
        result = self.reprocess_order(order_run_id, payload)
        result["session"] = session
        return result

    def upsert_routing_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(_pick(payload, "tenantId", "tenant_id", default="default") or "default").strip() or "default"
        customer_id = str(
            _pick(payload, "customerId", "customer_id", default=GLOBAL_CUSTOMER_ID) or GLOBAL_CUSTOMER_ID
        ).strip() or GLOBAL_CUSTOMER_ID
        name = str(_pick(payload, "name", default="")).strip()
        rule_id = str(_pick(payload, "id", default="") or "").strip()
        outcome = _routing_outcome_from_value(_pick(payload, "outcome", default=RoutingOutcome.NEEDS_HUMAN_REVIEW))
        if outcome is None:
            return {
                "error": "invalidRoutingOutcome",
                "message": "Routing action is not valid.",
                "field": "outcome",
                "allowedValues": [item.value for item in RoutingOutcome],
            }
        priority = _routing_priority_from_value(_pick(payload, "priority", default=100))
        if priority is None:
            return {
                "error": "invalidRoutingPriority",
                "message": "Priority must be a whole number greater than zero.",
                "field": "priority",
            }
        filter_conditions, filter_error = _normalize_routing_filter_conditions(
            _pick(payload, "filterConditions", "filter_conditions", default=[]),
            strict=True,
        )
        if filter_error:
            return filter_error
        filter_logic = _routing_filter_logic(_pick(payload, "filterLogic", "filter_logic", default="all"))
        subject_regex = _normalize_regex_list(_pick(payload, "subjectRegex", "subject_regex", default=[]))
        body_regex = _normalize_regex_list(_pick(payload, "bodyRegex", "body_regex", default=[]))
        known_webstore_patterns = _normalize_regex_list(
            _pick(payload, "knownWebstorePatterns", "known_webstore_patterns", default=[])
        )
        prior_processed_subject_regex = _normalize_regex_list(
            _pick(payload, "priorProcessedSubjectRegex", "prior_processed_subject_regex", default=[])
        )
        attachment_name_regex = _normalize_regex_list(
            _pick(payload, "attachmentNameRegex", "attachment_name_regex", default=[])
        )
        regex_fields = {
            "subjectRegex": subject_regex,
            "bodyRegex": body_regex,
            "knownWebstorePatterns": known_webstore_patterns,
            "priorProcessedSubjectRegex": prior_processed_subject_regex,
            "attachmentNameRegex": attachment_name_regex,
        }
        for field_name, patterns in regex_fields.items():
            error = _regex_validation_error(field_name, patterns)
            if error:
                return error
        customer_code_extraction = self._routing_customer_code_extraction_from_payload(payload)
        extraction_regex = _normalize_regex_pattern(
            _pick(
                customer_code_extraction,
                "regex",
                "pattern",
                "customerCodeRegex",
                default="",
            )
        )
        if extraction_regex:
            customer_code_extraction["regex"] = extraction_regex
            error = _regex_validation_error("customerCodeRegex", [extraction_regex])
            if error:
                return error
        subject_update = self._routing_subject_update_from_payload(payload)
        for field_name, subject_update_key in {
            "processedSubjectDetectRegex": "detectRegex",
            "processedSubjectCustomerCodeRegex": "customerCodeRegex",
            "processedSubjectRouteRegex": "routeRegex",
        }.items():
            pattern = _normalize_regex_pattern(subject_update.get(subject_update_key))
            if not pattern:
                continue
            subject_update[subject_update_key] = pattern
            error = _regex_validation_error(field_name, [pattern])
            if error:
                return error
        rule_doc = {
            "id": rule_id or stable_id(tenant_id, customer_id, name or "routing-rule"),
            "tenantId": tenant_id,
            "customerId": customer_id,
            "name": name,
            "outcome": outcome,
            "phase": _pick(payload, "phase", "triagePhase", "triage_phase", default="general"),
            "priority": priority,
            "enabled": bool(_pick(payload, "enabled", default=True)),
            "processorProfileId": _pick(payload, "processorProfileId", "processor_profile_id", default=None),
            "mailboxAccountIds": list(_as_list(_pick(payload, "mailboxAccountIds", "mailbox_account_ids", default=[]))),
            "mailboxAddresses": list(_as_list(_pick(payload, "mailboxAddresses", "mailbox_addresses", default=[]))),
            "filterConditions": filter_conditions,
            "filterLogic": filter_logic,
            "senderEquals": list(_as_list(_pick(payload, "senderEquals", "sender_equals", default=[]))),
            "senderDomains": list(_as_list(_pick(payload, "senderDomains", "sender_domains", default=[]))),
            "subjectRegex": subject_regex,
            "bodyRegex": body_regex,
            "knownWebstorePatterns": known_webstore_patterns,
            "priorProcessedSubjectRegex": prior_processed_subject_regex,
            "attachmentExtensions": list(_as_list(_pick(payload, "attachmentExtensions", "attachment_extensions", default=[]))),
            "attachmentContentTypes": list(_as_list(_pick(payload, "attachmentContentTypes", "attachment_content_types", default=[]))),
            "attachmentNameRegex": attachment_name_regex,
            "requiredAttachment": bool(_pick(payload, "requiredAttachment", "required_attachment", default=False)),
            "tags": list(_as_list(_pick(payload, "tags", default=[]))),
            "customerCodeExtraction": customer_code_extraction,
            "subjectUpdate": subject_update,
            "emailActions": self._routing_email_actions_from_payload(payload),
        }
        rule = _routing_rule_from_doc(rule_doc)
        stored = self.repository.upsert("routingRules", to_dict(rule))
        self._clear_console_cache(tenant_id)
        self._audit(tenant_id, "routingRule.upserted", stored["id"], stored["id"], {"customerId": customer_id})
        return {"routingRule": stored}

    @staticmethod
    def _routing_customer_code_extraction_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
        extraction = dict(_pick(payload, "customerCodeExtraction", "customer_code_extraction", default={}) or {})
        for key in ("regex", "pattern", "customerCodeRegex"):
            if extraction.get(key):
                extraction[key] = _normalize_regex_pattern(extraction[key])
        regex = _pick(payload, "customerCodeRegex", "customer_code_regex", default=None)
        if regex:
            extraction.setdefault("regex", _normalize_regex_pattern(regex))
            extraction.setdefault("source", _pick(payload, "customerCodeSource", "customer_code_source", default="combined"))
            extraction.setdefault("group", _pick(payload, "customerCodeGroup", "customer_code_group", default="customerCode"))
            extraction.setdefault("required", bool(_pick(payload, "customerCodeRequired", "customer_code_required", default=True)))
        return extraction

    @staticmethod
    def _routing_subject_update_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
        subject_update = dict(_pick(payload, "subjectUpdate", "subject_update", default={}) or {})
        template = _pick(payload, "subjectTemplate", "subject_template", default=None)
        if template:
            subject_update.setdefault("template", template)
        detect_regex = _pick(payload, "processedSubjectDetectRegex", "processed_subject_detect_regex", default=None)
        if detect_regex:
            subject_update.setdefault("detectRegex", _normalize_regex_pattern(detect_regex))
        customer_code_regex = _pick(payload, "processedSubjectCustomerCodeRegex", "processed_subject_customer_code_regex", default=None)
        if customer_code_regex:
            subject_update.setdefault("customerCodeRegex", _normalize_regex_pattern(customer_code_regex))
        route_regex = _pick(payload, "processedSubjectRouteRegex", "processed_subject_route_regex", default=None)
        if route_regex:
            subject_update.setdefault("routeRegex", _normalize_regex_pattern(route_regex))
        return subject_update

    @staticmethod
    def _routing_email_actions_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
        email_actions = dict(_pick(payload, "emailActions", "email_actions", default={}) or {})
        category_templates = list(_as_list(_pick(payload, "categoryTemplates", "category_templates", default=[])))
        if category_templates:
            email_actions.setdefault("categoryTemplates", category_templates)
        csr_field = _pick(payload, "categoryCsrField", "category_csr_field", default=None)
        if csr_field:
            email_actions.setdefault("csrField", csr_field)
        moves = dict(email_actions.get("moves") or {})
        for action_key, prefix in {
            "orderStart": "orderStart",
            "processedOrder": "processed",
            "failedOrder": "failed",
            "nonOrder": "nonOrder",
        }.items():
            mode = _pick(payload, f"{prefix}MoveMode", f"{prefix}_move_mode", default=None)
            folder = _pick(payload, f"{prefix}MoveFolder", f"{prefix}_move_folder", default=None)
            field = _pick(payload, f"{prefix}MoveCustomerField", f"{prefix}_move_customer_field", default=None)
            if action_key == "orderStart":
                mode = mode or _pick(payload, "orderProcessingMoveMode", "order_processing_move_mode", default=None)
                folder = folder or _pick(payload, "orderProcessingMoveFolder", "order_processing_move_folder", default=None)
                field = field or _pick(payload, "orderProcessingMoveCustomerField", "order_processing_move_customer_field", default=None)
            if mode or folder or field:
                moves[action_key] = {
                    "mode": mode or ("customerField" if field else "staticFolder"),
                    "folder": folder or "",
                    "field": field or "",
                }
        if moves:
            email_actions["moves"] = moves
        return email_actions

    def upsert_tenant_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(
            _pick(payload, "targetTenantId", "target_tenant_id", "id", "tenantId", "tenant_id", default="")
        ).strip()
        if not tenant_id:
            return {"error": "tenantIdRequired", "message": "Customer id is required."}
        existing = self.repository.get("tenants", tenant_id) or {}
        settings = {
            **dict(_pick(existing, "settings", default={}) or {}),
            **dict(_pick(payload, "settings", default={}) or {}),
        }
        tenant = Tenant(
            id=tenant_id,
            tenant_id=tenant_id,
            name=_pick(payload, "name", default=_pick(existing, "name", default=tenant_id)),
            environment=_pick(payload, "environment", default=_pick(existing, "environment", default="")),
            status=_pick(payload, "status", default=_pick(existing, "status", default="active")),
            settings=settings,
            created_at=_pick(existing, "createdAt", "created_at", default=utc_now()),
        )
        stored = self.repository.upsert("tenants", to_dict(tenant))
        ai_cost_source = self._ensure_ai_cost_source_for_tenant(stored)
        self._clear_console_cache(tenant_id)
        self._audit(
            tenant_id,
            "tenantConfig.upserted",
            stored["id"],
            stored["id"],
            {"name": stored["name"], "aiCostSourceId": ai_cost_source["id"]},
        )
        return {"tenant": self.repository.get("tenants", tenant_id) or stored, "aiCostSource": ai_cost_source}

    def upsert_customer_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        customer_code = _pick(payload, "customerCode", "customer_code", default="")
        name = _pick(payload, "name", default=customer_code)
        customer = CustomerProfile(
            id=_pick(payload, "id", "customerId", "customer_id", default=stable_id(tenant_id, customer_code or name)),
            tenant_id=tenant_id,
            customer_code=customer_code,
            name=name,
            route_number=_pick(payload, "routeNumber", "route_number", default=""),
            csr_name=_pick(payload, "csrName", "csr_name", default=""),
            csr_email=_pick(payload, "csrEmail", "csr_email", default=""),
            csr_folder=_pick(payload, "csrFolder", "csr_folder", default=""),
            store_number=_pick(payload, "storeNumber", "store_number", default=""),
            address1=_pick(payload, "address1", "locationAddress1", "location_address1", default=""),
            city=_pick(payload, "city", "locationCity", "location_city", default=""),
            state=_pick(payload, "state", "locationState", "location_state", default=""),
            postal_code=_pick(payload, "postalCode", "postal_code", "locationZip", "location_zip", default=""),
            phone=_pick(payload, "phone", default=""),
            website=_pick(payload, "website", "customerWebsite", "customer_website", default=""),
            customer_email=_pick(payload, "customerEmail", "customer_email", default=""),
            sender_domains=list(_as_list(_pick(payload, "senderDomains", "sender_domains", default=[]))),
            aliases=list(_as_list(_pick(payload, "aliases", default=[]))),
            known_subject_patterns=list(_as_list(_pick(payload, "knownSubjectPatterns", "known_subject_patterns", default=[]))),
            source_name=_pick(payload, "sourceName", "source_name", default="console"),
            source_rows_blob_url=_pick(payload, "sourceRowsBlobUrl", "source_rows_blob_url", default=""),
            last_imported_at=_pick(payload, "lastImportedAt", "last_imported_at", default=None),
            custom_fields=dict(_pick(payload, "customFields", "custom_fields", default={}) or {}),
            raw_source=dict(_pick(payload, "rawSource", "raw_source", default={}) or {}),
        )
        stored = self.repository.upsert("customers", to_dict(customer))
        self._refresh_tenant_csr_directory(tenant_id)
        self._clear_console_cache(tenant_id)
        self._audit(tenant_id, "customerConfig.upserted", stored["id"], stored["id"], {"customerCode": customer_code})
        return {"customer": stored}

    def upsert_customer_identification_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        customer_id = _pick(payload, "customerId", "customer_id", default="")
        alias_type = _pick(payload, "aliasType", "alias_type", "ruleType", "rule_type", default="")
        value = str(_pick(payload, "value", "pattern", default="")).strip()
        normalized_value = _pick(payload, "normalizedValue", "normalized_value", default=None)
        if normalized_value is None:
            normalized_value = _normalized_customer_rule_value(alias_type, value)
        rule = CustomerAlias(
            id=_pick(payload, "id", "ruleId", "rule_id", default=stable_id(tenant_id, customer_id, alias_type, value)),
            tenant_id=tenant_id,
            customer_id=customer_id,
            alias_type=alias_type,
            value=value,
            normalized_value=normalized_value,
            source=_pick(payload, "source", default="console"),
            confidence=float(_pick(payload, "confidence", default=1.0) or 1.0),
            raw_source=dict(_pick(payload, "rawSource", "raw_source", default={}) or {}),
        )
        stored = self.repository.upsert("customerAliases", to_dict(rule))
        self._clear_console_cache(tenant_id)
        self._audit(
            tenant_id,
            "customerIdentificationRule.upserted",
            stored["id"],
            stored["id"],
            {"customerId": customer_id, "aliasType": alias_type, "value": value},
            customer_id=customer_id,
        )
        return {"customerIdentificationRule": stored}

    def upsert_processor_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(_pick(payload, "tenantId", "tenant_id", default="default") or "default").strip() or "default"
        customer_id = str(
            _pick(payload, "customerId", "customer_id", default=GLOBAL_CUSTOMER_ID) or GLOBAL_CUSTOMER_ID
        ).strip() or GLOBAL_CUSTOMER_ID
        name = str(_pick(payload, "name", default="") or "").strip()
        profile_id = str(_pick(payload, "id", "processorProfileId", "processor_profile_id", default="") or "").strip()
        profile = ProcessorProfile(
            id=profile_id or stable_id(tenant_id, customer_id, name or "processor"),
            tenant_id=tenant_id,
            customer_id=customer_id,
            name=name,
            processor_type=_pick(payload, "processorType", "processor_type", default="csv"),
            output_profile_id=_pick(payload, "outputProfileId", "output_profile_id", default=None),
            settings=dict(_pick(payload, "settings", default={}) or {}),
        )
        stored = self.repository.upsert("processorProfiles", to_dict(profile))
        self._clear_console_cache(tenant_id)
        self._audit(tenant_id, "processorProfile.upserted", stored["id"], stored["id"], {"customerId": customer_id})
        return {"processorProfile": stored}

    def upsert_output_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = str(_pick(payload, "tenantId", "tenant_id", default="default") or "default").strip() or "default"
        customer_id = str(
            _pick(payload, "customerId", "customer_id", default=GLOBAL_CUSTOMER_ID) or GLOBAL_CUSTOMER_ID
        ).strip() or GLOBAL_CUSTOMER_ID
        name = str(_pick(payload, "name", default="") or "").strip()
        profile_id = str(_pick(payload, "id", "outputProfileId", "output_profile_id", default="") or "").strip()
        profile = OutputProfile(
            id=profile_id or stable_id(tenant_id, customer_id, name or "output"),
            tenant_id=tenant_id,
            customer_id=customer_id,
            name=name,
            output_type=_pick(payload, "outputType", "output_type", default="csv"),
            destination=dict(_pick(payload, "destination", default={}) or {}),
            settings=dict(_pick(payload, "settings", default={}) or {}),
        )
        stored = self.repository.upsert("outputProfiles", to_dict(profile))
        self._clear_console_cache(tenant_id)
        self._audit(tenant_id, "outputProfile.upserted", stored["id"], stored["id"], {"customerId": customer_id})
        return {"outputProfile": stored}

    def upsert_mailbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        deprecated_customer_id = _pick(payload, "customerId", "customer_id", default="")
        customer_id = GLOBAL_CUSTOMER_ID
        mailbox_address = str(_pick(payload, "mailboxAddress", "mailbox_address", default="")).strip().lower()
        if not mailbox_address:
            return {"error": "mailboxAddressRequired", "message": "Mailbox address is required."}
        mailbox_id = _pick(
            payload,
            "id",
            "mailboxAccountId",
            "mailbox_account_id",
            default=stable_id(tenant_id, mailbox_address),
        )
        settings = dict(_pick(payload, "settings", default={}) or {})
        if deprecated_customer_id and deprecated_customer_id != GLOBAL_CUSTOMER_ID:
            settings["deprecatedMailboxCustomerId"] = deprecated_customer_id
        connection_id = _pick(payload, "connectionId", "connection_id", default="") or stable_id(
            tenant_id,
            "microsoft365",
            mailbox_address,
        )
        mailbox = MailboxAccount(
            id=mailbox_id,
            tenant_id=tenant_id,
            mailbox_address=mailbox_address,
            customer_id=customer_id,
            display_name=_pick(payload, "displayName", "display_name", default=mailbox_address),
            provider=_pick(payload, "provider", default="microsoft365"),
            connection_id=connection_id,
            enabled=bool(_pick(payload, "enabled", default=True)),
            ingest_status=_pick(payload, "ingestStatus", "ingest_status", default="configured"),
            permission_status=_pick(payload, "permissionStatus", "permission_status", default="unknown"),
            required_permissions=list(
                _as_list(
                    _pick(
                        payload,
                        "requiredPermissions",
                        "required_permissions",
                        default=["Mail.Read", "Mail.ReadWrite"],
                    )
                )
            ),
            graph_user_id=_pick(payload, "graphUserId", "graph_user_id", default=""),
            folder_ids=dict(_pick(payload, "folderIds", "folder_ids", default={}) or {}),
            settings=settings,
        )
        stored = self.repository.upsert("mailboxAccounts", to_dict(mailbox))
        self._clear_console_cache(tenant_id)
        self._audit(
            tenant_id,
            "mailbox.upserted",
            stored["id"],
            stored["id"],
            {
                "mailboxAddress": mailbox_address,
                "mailboxScope": "tenant",
                "deprecatedCustomerIdIgnored": deprecated_customer_id or None,
            },
        )
        return {"mailboxAccount": stored}

    def test_mailbox_connection(self, mailbox_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        mailbox = self.repository.get("mailboxAccounts", mailbox_id)
        if mailbox is None:
            return {"error": "notFound", "message": f"Mailbox account {mailbox_id} was not found."}

        connection_id = str(_pick(mailbox, "connectionId", "connection_id", default=""))
        connection = self.repository.get("microsoftAuthConnections", connection_id) if connection_id else None
        if not connection:
            status = {
                "mailboxAccountId": mailbox_id,
                "provider": mailbox.get("provider", "microsoft365"),
                "connectionId": connection_id,
                "canAccess": False,
                "status": "needsConsent",
                "message": "Authorize Microsoft access with a delegated user before testing this mailbox.",
                "checkedAt": utc_now(),
            }
            return {"mailboxAccount": mailbox, "connectionStatus": status}

        secret_names = dict(_pick(connection, "keyVaultSecretNames", "key_vault_secret_names", default={}) or {})
        refresh_secret_name = str(secret_names.get("refreshToken", ""))
        refresh_token_value = self.secret_store.get_secret(refresh_secret_name)
        if not refresh_token_value:
            status = {
                "mailboxAccountId": mailbox_id,
                "provider": mailbox.get("provider", "microsoft365"),
                "connectionId": connection_id,
                "canAccess": False,
                "status": "needsConsent",
                "message": "No Microsoft Graph refresh token is stored for this connection.",
                "checkedAt": utc_now(),
            }
            return {"mailboxAccount": mailbox, "microsoftAuthConnection": connection, "connectionStatus": status}

        config = config_from_environment(_pick(connection, "metadata", default={}).get("redirectUri", ""))
        try:
            token = refresh_access_token(config, refresh_token_value, self._microsoft_client_secret(config))
            self._store_microsoft_tokens(connection_id, token)
            status = test_shared_mailbox_access(
                str(token.get("access_token", "")),
                str(_pick(mailbox, "mailboxAddress", "mailbox_address", default="")),
            )
        except MicrosoftGraphError as exc:
            status = {
                "mailboxAccountId": mailbox_id,
                "provider": mailbox.get("provider", "microsoft365"),
                "connectionId": connection_id,
                "canAccess": False,
                "status": "failed",
                "statusCode": exc.status_code,
                "message": str(exc),
                "details": exc.details,
                "checkedAt": utc_now(),
            }

        stored_mailbox = self._update_mailbox_after_graph_auth(mailbox_id, connection_id, status)
        connection["status"] = AuthConnectionStatus.ACTIVE.value if status.get("canAccess") else AuthConnectionStatus.FAILED.value
        connection["lastTestedAt"] = status.get("checkedAt", utc_now())
        connection["metadata"] = {
            **dict(_pick(connection, "metadata", default={}) or {}),
            "mailboxAccess": status,
        }
        stored_connection = self.repository.upsert("microsoftAuthConnections", connection)
        self._clear_console_cache(str(_pick(stored_mailbox, "tenantId", "tenant_id", default="")))
        return {"mailboxAccount": stored_mailbox, "microsoftAuthConnection": stored_connection, "connectionStatus": status}

    def upsert_console_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        email = str(_pick(payload, "email", default="")).strip().lower()
        roles = list(_as_list(_pick(payload, "roles", default=[])))
        if email == "connect@focuseautomate.com" and "platformAdmin" not in roles:
            roles.append("platformAdmin")

        user = ConsoleUser(
            id=_pick(payload, "id", default=stable_id(tenant_id, email)),
            tenant_id=tenant_id,
            email=email,
            display_name=_pick(payload, "displayName", "display_name", default=email),
            roles=roles,
            enabled=bool(_pick(payload, "enabled", default=True)),
            auth_provider="microsoft",
            microsoft_user_id=_pick(payload, "microsoftUserId", "microsoft_user_id", default=""),
        )
        stored = self.repository.upsert("consoleUsers", to_dict(user))
        self._clear_console_cache(tenant_id)
        self._audit(tenant_id, "consoleUser.upserted", stored["id"], stored["id"], {"email": email})
        return {"consoleUser": stored}

    def assign_customer_user(self, customer_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        email = str(_pick(payload, "email", default="")).strip().lower()
        assignment = CustomerUserAssignment(
            id=_pick(payload, "id", default=stable_id(tenant_id, customer_id, email)),
            tenant_id=tenant_id,
            customer_id=customer_id,
            email=email,
            roles=list(_as_list(_pick(payload, "roles", default=[]))),
            enabled=bool(_pick(payload, "enabled", default=True)),
        )
        stored = self.repository.upsert("customerUserAssignments", to_dict(assignment))
        self._clear_console_cache(tenant_id)
        self._audit(
            tenant_id,
            "customerUser.assigned",
            stored["id"],
            customer_id,
            {"email": email, "roles": stored["roles"]},
        )
        return {"customerUserAssignment": stored}

    def upsert_microsoft_auth_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        customer_id = _pick(payload, "customerId", "customer_id", default=None)
        provider = _pick(payload, "provider", default="microsoft365")
        owner_email = str(_pick(payload, "ownerEmail", "owner_email", default="")).strip().lower()
        connection = MicrosoftAuthConnection(
            id=_pick(payload, "id", "connectionId", "connection_id", default=stable_id(tenant_id, provider, owner_email)),
            tenant_id=tenant_id,
            customer_id=customer_id,
            provider=provider,
            display_name=_pick(payload, "displayName", "display_name", default=provider),
            owner_email=owner_email,
            connection_type=_pick(payload, "connectionType", "connection_type", default="delegated"),
            status=_pick(payload, "status", default="configured"),
            scopes=list(_as_list(_pick(payload, "scopes", default=[]))),
            key_vault_secret_names=dict(
                _pick(payload, "keyVaultSecretNames", "key_vault_secret_names", default={}) or {}
            ),
            power_automate_connection_reference=_pick(
                payload,
                "powerAutomateConnectionReference",
                "power_automate_connection_reference",
                default="",
            ),
            tenant_authority=_pick(payload, "tenantAuthority", "tenant_authority", default=""),
            consented_by=_pick(payload, "consentedBy", "consented_by", default=""),
            metadata=dict(_pick(payload, "metadata", default={}) or {}),
        )
        stored = self.repository.upsert("microsoftAuthConnections", to_dict(connection))
        self._clear_console_cache(tenant_id)
        self._audit(tenant_id, "microsoftAuthConnection.upserted", stored["id"], stored["id"], {"provider": provider})
        return {"microsoftAuthConnection": stored}

    def resolve_exception(self, exception_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.repository.get("exceptionTasks", exception_id)
        if existing is None:
            return {"error": "notFound", "message": f"Exception task {exception_id} was not found."}

        observability = correlation_context(payload, exception_id)
        resolution = dict(_pick(payload, "resolution", default=payload) or {})
        resolution.setdefault("actor", self._actor_from_payload(payload))
        resolution_result = self._apply_exception_resolution(existing, resolution)
        resolution_status = str(_pick(resolution_result, "status", default="resolved") or "resolved")
        existing["status"] = (
            ExceptionStatus.OPEN.value if resolution_status in {"notFound", "invalid", "failed", "updated"} else ExceptionStatus.RESOLVED.value
        )
        existing["resolution"] = resolution
        existing["resolvedBy"] = self._actor_from_payload(payload)
        if existing["status"] == ExceptionStatus.RESOLVED.value:
            existing["resolved_at"] = utc_now()
        stored = self.repository.upsert("exceptionTasks", existing)
        self._upsert_monitor_record_for_exception(stored)
        event_type = (
            "exception.resolved"
            if existing["status"] == ExceptionStatus.RESOLVED.value
            else "exception.updated"
            if resolution_status == "updated"
            else "exception.resolutionFailed"
        )
        self._audit(
            _pick(existing, "tenantId", "tenant_id", default="default"),
            event_type,
            observability["correlationId"],
            exception_id,
            {
                "exceptionTaskId": exception_id,
                "type": _pick(existing, "type", default=""),
                "orderRunId": _pick(existing, "orderRunId", "order_run_id", default=None),
                "emailMessageId": _pick(existing, "emailMessageId", "email_message_id", default=None),
                "customerId": self._exception_customer_id(existing),
                "resolution": existing["resolution"],
                "result": resolution_result,
                "userIntervention": True,
                "observability": observability,
            },
            customer_id=self._exception_customer_id(existing),
            order_run_id=_pick(existing, "orderRunId", "order_run_id", default=None),
            email_message_id=_pick(existing, "emailMessageId", "email_message_id", default=None),
            actor=self._actor_from_payload(payload),
        )
        return {"exceptionTask": stored, "resolutionResult": resolution_result}

    def _apply_exception_resolution(
        self,
        task: dict[str, Any],
        resolution: dict[str, Any],
    ) -> dict[str, Any]:
        action = str(_pick(resolution, "action", "resolutionAction", "resolution_action", default="") or "").strip()
        if action in {"disregard", "clear", "manualOverride", "manualHandled", "manualHandling"}:
            return self._apply_disregard_resolution(task, resolution)
        if action in {"csr", "setCsr", "moveToCsr"}:
            return self._apply_csr_resolution(task, resolution)
        if action in {"emailSubject", "updateSubject"}:
            return self._apply_email_subject_resolution(task, resolution)
        if action in {"manualCategory", "category", "addCategory", "applyCategory"}:
            return self._apply_manual_category_resolution(task, resolution)
        if action in {"moveEmail", "manualMove", "move"}:
            return self._apply_manual_move_resolution(task, resolution)
        if action in {"emailReprocess", "reprocessEmail"}:
            return self._apply_email_reprocess_resolution(task, resolution)
        if action in {"forceOrder", "forceOrderProcessor"}:
            return self._apply_force_order_resolution(task, resolution)
        if action in {"orderReprocess", "reprocessOrder"}:
            order_run_id = _pick(task, "orderRunId", "order_run_id", default=None) or _pick(
                resolution, "orderRunId", "order_run_id", default=None
            )
            if order_run_id:
                return {"status": "reprocessed", "reprocess": self.reprocess_order(order_run_id, {"source": "exceptionResolution"})}
            return {"status": "invalid", "message": "No order run is attached to this exception."}

        task_type = _pick(task, "type", default="")
        if task_type in {"customerIdentification", "routing"}:
            return self._apply_customer_resolution(task, resolution)
        if task_type == "itemValidation":
            return self._apply_item_resolution(task, resolution)
        if task_type in {"parserFailure", "outputGeneration"}:
            if bool(_pick(resolution, "reprocess", "reprocessOrder", "rerun", default=False)):
                order_run_id = _pick(task, "orderRunId", "order_run_id", default=None)
                if order_run_id:
                    return {"reprocess": self.reprocess_order(order_run_id, {"source": "exceptionResolution"})}
            return {"status": "triaged"}
        return {"status": "recorded"}

    def _apply_disregard_resolution(self, task: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        actor = str(_pick(resolution, "actor", "resolvedBy", "resolved_by", default="") or "console")
        notes = str(_pick(resolution, "notes", "reason", default="Manual override; email will be handled outside automation.") or "")
        result: dict[str, Any] = {
            "status": "manualOverride",
            "manualOverride": True,
            "notes": notes,
            "completedAt": now,
        }
        email_message_id = str(_pick(task, "emailMessageId", "email_message_id", default="") or "")
        if email_message_id:
            email = self.repository.get("emailMessages", email_message_id)
            if email:
                source = dict(_pick(email, "source", default={}) or {})
                source["manualOverride"] = {
                    "reason": "exceptionDisregarded",
                    "notes": notes,
                    "actor": actor,
                    "at": now,
                }
                email["source"] = source
                email["status"] = ProcessingStatus.COMPLETED.value
                email["updatedAt"] = now
                email_doc = self.repository.upsert("emailMessages", email)
                result["emailMessageId"] = email_message_id
                self._upsert_monitor_record_for_email(email_doc)

        order_run_id = str(_pick(task, "orderRunId", "order_run_id", default="") or "")
        if order_run_id:
            order = self.repository.get("orderRuns", order_run_id)
            if order:
                metadata = dict(_pick(order, "sourceMetadata", "source_metadata", default={}) or {})
                metadata["manualOverride"] = {
                    "reason": "exceptionDisregarded",
                    "notes": notes,
                    "actor": actor,
                    "at": now,
                }
                order["sourceMetadata"] = metadata
                order["status"] = ProcessingStatus.COMPLETED.value
                order["processingCompletedAt"] = now
                order["updatedAt"] = now
                order_doc = self.repository.upsert("orderRuns", order)
                result["orderRunId"] = order_run_id
                self._upsert_monitor_record_for_order(order_doc)
        return result

    def _customer_for_exception(self, task: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any] | None:
        customer_id = str(
            _pick(resolution, "selectedCustomerId", "customerId", "customer_id", default="")
            or self._exception_customer_id(task)
            or ""
        )
        if customer_id:
            customer = self.repository.get("customers", customer_id)
            if customer:
                return customer
        email = self._email_for_exception(task, resolution)
        if email:
            customer_id = str(_document_customer_id(email) or "")
            if customer_id:
                return self.repository.get("customers", customer_id)
        order_run_id = str(
            _pick(task, "orderRunId", "order_run_id", default="")
            or _pick(resolution, "orderRunId", "order_run_id", default="")
            or ""
        )
        order = self.repository.get("orderRuns", order_run_id) if order_run_id else None
        customer_id = str(_document_customer_id(order or {}) or "")
        return self.repository.get("customers", customer_id) if customer_id else None

    def _apply_csr_resolution(self, task: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
        customer = self._customer_for_exception(task, resolution)
        if not customer:
            return {"status": "invalid", "message": "Resolve the customer before assigning a CSR."}

        csr_name = str(_pick(resolution, "csrName", "csr_name", default="") or "").strip()
        csr_folder = str(_pick(resolution, "csrFolder", "csr_folder", default="") or "").strip()
        csr_email = str(_pick(resolution, "csrEmail", "csr_email", default="") or "").strip().lower()
        if not csr_name and not csr_folder and not csr_email:
            return {"status": "invalid", "message": "Choose or enter a CSR before submitting."}

        customer["csrName"] = csr_name or str(_pick(customer, "csrName", "csr_name", default="") or csr_folder)
        customer["csrFolder"] = csr_folder or str(_pick(customer, "csrFolder", "csr_folder", default="") or csr_name)
        if csr_email:
            customer["csrEmail"] = csr_email
        customer["updatedAt"] = utc_now()
        stored_customer = self.repository.upsert("customers", customer)
        tenant_id = str(_pick(stored_customer, "tenantId", "tenant_id", default="default"))
        csr_directory = self._refresh_tenant_csr_directory(tenant_id)
        self._clear_console_cache(tenant_id)

        email = self._email_for_exception(task, resolution)
        graph_result = None
        if email is not None:
            email["customerId"] = str(_pick(stored_customer, "id", default=""))
            email["updatedAt"] = utc_now()
            stored_email = self.repository.upsert("emailMessages", email)
            self._upsert_monitor_record_for_email(stored_email)

        order_run_id = str(_pick(task, "orderRunId", "order_run_id", default="") or "")
        order = self.repository.get("orderRuns", order_run_id) if order_run_id else None
        if order:
            order["customerId"] = str(_pick(stored_customer, "id", default=""))
            order["updatedAt"] = utc_now()
            self._upsert_monitor_record_for_order(self.repository.upsert("orderRuns", order))

        return {
            "status": "updated",
            "customer": stored_customer,
            "csrDirectory": csr_directory,
            "graphEmailAction": graph_result,
        }

    def _apply_email_subject_resolution(self, task: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
        subject = str(_pick(resolution, "subject", "updatedSubject", "updated_subject", default="") or "").strip()
        if not subject:
            return {"status": "invalid", "message": "Enter the subject to apply."}
        email = self._email_for_exception(task, resolution)
        if email is None:
            return {"status": "notFound", "message": "No email is attached to this exception."}
        graph_result = self._manual_graph_email_action(email, subject=subject)
        if str(_pick(graph_result, "status", default="")) in {"failed", "partial"}:
            return {"status": "failed", "graphEmailAction": graph_result}
        return {"status": "updated", "graphEmailAction": graph_result}

    def _apply_manual_category_resolution(self, task: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
        category = str(_pick(resolution, "category", "categoryName", "category_name", default="") or "").strip()
        if not category:
            return {"status": "invalid", "message": "Choose a category to apply."}
        email = self._email_for_exception(task, resolution)
        if email is None:
            return {"status": "notFound", "message": "No email is attached to this exception."}
        graph_result = self._manual_graph_email_action(email, categories=[category])
        if str(_pick(graph_result, "status", default="")) in {"failed", "partial"}:
            return {"status": "failed", "graphEmailAction": graph_result}
        return {"status": "updated", "category": category, "graphEmailAction": graph_result}

    def _apply_manual_move_resolution(self, task: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
        folder = str(_pick(resolution, "folder", "folderName", "folder_name", "moveFolder", "move_folder", default="") or "").strip()
        if not folder:
            return {"status": "invalid", "message": "Choose a destination folder."}
        email = self._email_for_exception(task, resolution)
        if email is None:
            return {"status": "notFound", "message": "No email is attached to this exception."}
        graph_result = self._manual_graph_email_action(email, move_folder=folder)
        if str(_pick(graph_result, "status", default="")) in {"failed", "partial"}:
            return {"status": "failed", "graphEmailAction": graph_result}
        now = utc_now()
        actor = str(_pick(resolution, "actor", "resolvedBy", "resolved_by", default="") or "console")
        notes = str(_pick(resolution, "notes", "reason", default=f"Moved to {folder} from exception queue.") or "")
        email = self.repository.get("emailMessages", str(_pick(email, "id", default=""))) or email
        source = dict(_pick(email, "source", default={}) or {})
        source["manualOverride"] = {
            "reason": "exceptionManualMove",
            "actionTaken": f"manual move to {folder}",
            "notes": notes,
            "actor": actor,
            "at": now,
        }
        email["source"] = source
        email["status"] = ProcessingStatus.COMPLETED.value
        email["updatedAt"] = now
        email_doc = self.repository.upsert("emailMessages", email)
        self._upsert_monitor_record_for_email(email_doc)

        order_run_id = str(_pick(task, "orderRunId", "order_run_id", default=_pick(email, "orderRunId", "order_run_id", default="")) or "")
        if order_run_id:
            order = self.repository.get("orderRuns", order_run_id)
            if order:
                metadata = dict(_pick(order, "sourceMetadata", "source_metadata", default={}) or {})
                metadata["manualOverride"] = {
                    "reason": "exceptionManualMove",
                    "actionTaken": f"manual move to {folder}",
                    "notes": notes,
                    "actor": actor,
                    "at": now,
                }
                order["sourceMetadata"] = metadata
                order["status"] = ProcessingStatus.COMPLETED.value
                order["processingCompletedAt"] = now
                order["updatedAt"] = now
                self._upsert_monitor_record_for_order(self.repository.upsert("orderRuns", order))
        return {
            "status": "manualMoved",
            "folder": folder,
            "notes": notes,
            "emailMessageId": str(_pick(email, "id", default="")),
            "orderRunId": order_run_id,
            "graphEmailAction": graph_result,
        }

    def _apply_email_reprocess_resolution(self, task: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
        email = self._email_for_exception(task, resolution)
        if email is None:
            return {"status": "notFound", "message": "No email is attached to this exception."}
        ingest_payload = {
            "tenantId": _pick(email, "tenantId", "tenant_id", default="default"),
            "id": _pick(email, "id", default=""),
            "mailboxAccountId": _pick(email, "mailboxAccountId", "mailbox_account_id", default=None),
            "mailbox": _pick(email, "mailbox", default=""),
            "messageId": _pick(email, "messageId", "message_id", default=""),
            "subject": _pick(email, "subject", default=""),
            "sender": _pick(email, "sender", default=""),
            "receivedAt": _pick(email, "receivedAt", "received_at", default=utc_now()),
            "bodyText": _pick(email, "bodyText", "body_text", default=""),
            "bodyHtml": _pick(email, "bodyHtml", "body_html", default=""),
            "categories": list(_as_list(_pick(email, "categories", default=[]))),
            "attachments": list(_as_list(_pick(email, "attachments", default=[]))),
            "source": dict(_pick(email, "source", default={}) or {}),
        }
        ingest_result = self.ingest_email(ingest_payload)
        email_message = _as_dict(_pick(ingest_result, "emailMessage", "email_message", default={}))
        order_run = _as_dict(_pick(ingest_result, "orderRun", "order_run", default={}))
        processed = None
        if order_run:
            processed = self.process_order(
                str(_pick(order_run, "id", default="")),
                {
                    "tenantId": _pick(email_message, "tenantId", "tenant_id", default=_pick(email, "tenantId", "tenant_id", default="default")),
                    "emailMessageId": _pick(email_message, "id", default=_pick(email, "id", default="")),
                    "customerId": _pick(order_run, "customerId", "customer_id", default=_document_customer_id(email_message)),
                    "processorProfileId": _pick(order_run, "processorProfileId", "processor_profile_id", default=None),
                    "mailbox": _pick(email_message, "mailbox", default=_pick(email, "mailbox", default="")),
                    "sender": _pick(email_message, "sender", default=_pick(email, "sender", default="")),
                    "subject": _pick(email_message, "subject", default=_pick(email, "subject", default="")),
                    "receivedAt": _pick(email_message, "receivedAt", "received_at", default=_pick(email, "receivedAt", "received_at", default=utc_now())),
                    "bodyText": _pick(email, "bodyText", "body_text", default=""),
                    "bodyHtml": _pick(email, "bodyHtml", "body_html", default=""),
                    "attachments": list(_as_list(_pick(email, "attachments", default=[]))),
                    "sourceMetadata": {
                        "provider": "exceptionResolution",
                        "emailMessageId": _pick(email, "id", default=""),
                    },
                },
            )
        graph_result = None
        latest_email = self.repository.get("emailMessages", str(_pick(email, "id", default="")))
        if latest_email:
            graph_message_id = self._graph_message_id_for_email(latest_email)
            mailbox = self._mailbox_for_email_document(latest_email)
            if graph_message_id and mailbox:
                action_plan = self._email_action_plan_from_ingest_result(ingest_result)
                if action_plan:
                    try:
                        candidates = self._graph_access_token_candidates(mailbox, auth_mode="auto")
                    except MicrosoftGraphError as exc:
                        candidates = []
                        graph_result = {
                            "status": "failed",
                            "reason": str(exc),
                            "statusCode": exc.status_code,
                            "details": exc.details,
                        }
                    if candidates:
                        graph_result = self._apply_graph_email_actions(
                            candidates[0]["accessToken"],
                            str(_pick(mailbox, "mailboxAddress", "mailbox_address", default=_pick(latest_email, "mailbox", default=""))),
                            graph_message_id,
                            ingest_result,
                            list(_as_list(_pick(latest_email, "categories", default=[]))),
                        )
                        self._update_email_after_graph_actions(str(_pick(latest_email, "id", default="")), ingest_result, graph_result)
        return {
            "status": "reprocessed",
            "ingestResult": ingest_result,
            "processResult": processed,
            "graphEmailAction": graph_result,
        }

    def _apply_force_order_resolution(self, task: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
        email = self._email_for_exception(task, resolution)
        if email is None:
            return {"status": "notFound", "message": "No email is attached to this exception."}
        processor_profile_id = str(_pick(resolution, "processorProfileId", "processor_profile_id", default="") or "").strip()
        if not processor_profile_id:
            return {"status": "invalid", "message": "Choose an order processor."}
        processor_profile = self.repository.get("processorProfiles", processor_profile_id)
        if processor_profile is None:
            return {"status": "notFound", "message": f"Processor profile {processor_profile_id} was not found."}

        tenant_id = str(_pick(email, "tenantId", "tenant_id", default="default"))
        if str(_pick(processor_profile, "tenantId", "tenant_id", default="")) != tenant_id:
            return {"status": "invalid", "message": "Processor profile belongs to a different distributor."}
        customer_id = str(
            _pick(resolution, "customerId", "customer_id", default="")
            or _document_customer_id(email)
            or self._exception_customer_id(task)
            or ""
        )
        order_run_id = str(
            _pick(task, "orderRunId", "order_run_id", default="")
            or stable_id(tenant_id, _pick(email, "id", default=""), "forcedOrder", processor_profile_id)
        )
        order = OrderRun(
            id=order_run_id,
            tenant_id=tenant_id,
            email_message_id=str(_pick(email, "id", default="")),
            customer_id=customer_id or None,
            processor_profile_id=processor_profile_id,
            correlation_id=str(_pick(email, "correlationId", "correlation_id", default=order_run_id)),
            status=ProcessingStatus.RECEIVED,
            source_metadata={
                "emailMessageId": _pick(email, "id", default=""),
                "mailbox": _pick(email, "mailbox", default=""),
                "sender": _pick(email, "sender", default=""),
                "subject": _pick(email, "subject", default=""),
                "receivedAt": _pick(email, "receivedAt", "received_at", default=""),
                "manualOverride": True,
            },
        )
        order_doc = self.repository.upsert("orderRuns", to_dict(order))
        email["orderRunId"] = order_run_id
        if customer_id:
            email["customerId"] = customer_id
        email["routing"] = {
            **dict(_pick(email, "routing", default={}) or {}),
            "outcome": RoutingOutcome.KNOWN_ORDER.value,
            "processorProfileId": processor_profile_id,
            "matchedSignals": {
                **dict(_pick(_as_dict(_pick(email, "routing", default={})), "matchedSignals", "matched_signals", default={}) or {}),
                "manualOverride": True,
            },
        }
        email["status"] = ProcessingStatus.PROCESSING.value
        email["updatedAt"] = utc_now()
        email_doc = self.repository.upsert("emailMessages", email)
        self._upsert_monitor_record_for_email(email_doc, order=order_doc)
        processed = self.process_order(
            order_run_id,
            {
                "tenantId": tenant_id,
                "emailMessageId": _pick(email, "id", default=""),
                "customerId": customer_id or None,
                "processorProfileId": processor_profile_id,
                "mailbox": _pick(email, "mailbox", default=""),
                "sender": _pick(email, "sender", default=""),
                "subject": _pick(email, "subject", default=""),
                "receivedAt": _pick(email, "receivedAt", "received_at", default=utc_now()),
                "bodyText": _pick(email, "bodyText", "body_text", default=""),
                "bodyHtml": _pick(email, "bodyHtml", "body_html", default=""),
                "attachments": list(_as_list(_pick(email, "attachments", default=[]))),
                "sourceMetadata": {
                    "provider": "exceptionResolution",
                    "manualOverride": True,
                    "emailMessageId": _pick(email, "id", default=""),
                },
            },
        )
        return {"status": "forcedOrder", "orderRun": self.repository.get("orderRuns", order_run_id), "processResult": processed}

    def _apply_customer_resolution(
        self,
        task: dict[str, Any],
        resolution: dict[str, Any],
    ) -> dict[str, Any]:
        tenant_id = str(_pick(task, "tenantId", "tenant_id", default="default"))
        customer_id = _pick(resolution, "selectedCustomerId", "customerId", "customer_id", default=None)
        customer_code = str(
            _pick(
                resolution,
                "customerCode",
                "customer_code",
                "accountNumber",
                "account_number",
                "customerReference",
                "customer_reference",
                default="",
            )
            or ""
        ).strip()
        if not customer_id and customer_code:
            customer = self._customer_by_code_or_id(tenant_id, customer_code)
            if customer is None:
                return {
                    "status": "notFound",
                    "message": f"No customer record matched customer code or id {customer_code}.",
                    "customerCode": customer_code,
                }
            customer_id = str(_pick(customer, "id", default=""))
        if not customer_id:
            return {"status": "recorded", "message": "No customer id supplied."}

        updated: dict[str, Any] = {"status": "updated", "customerId": customer_id}
        if customer_code:
            updated["customerCode"] = customer_code
        order_run_id = _pick(task, "orderRunId", "order_run_id", default=None) or _pick(
            resolution, "orderRunId", "order_run_id", default=None
        )
        if order_run_id:
            order = self.repository.get("orderRuns", order_run_id)
            if order is not None:
                order["customerId"] = customer_id
                order["updatedAt"] = utc_now()
                order_doc = self.repository.upsert("orderRuns", order)
                self._upsert_monitor_record_for_order(order_doc)
                updated["orderRunId"] = order_run_id

        email_message_id = _pick(task, "emailMessageId", "email_message_id", default=None) or _pick(
            resolution, "emailMessageId", "email_message_id", default=None
        )
        if email_message_id:
            email = self.repository.get("emailMessages", email_message_id)
            if email is not None:
                email["customerId"] = customer_id
                email["updatedAt"] = utc_now()
                email_doc = self.repository.upsert("emailMessages", email)
                self._upsert_monitor_record_for_email(email_doc)
                updated["emailMessageId"] = email_message_id
        return updated

    def _customer_by_code_or_id(self, tenant_id: str, customer_reference: str) -> dict[str, Any] | None:
        reference = str(customer_reference or "").strip()
        if not reference:
            return None
        exact = self.repository.get("customers", reference)
        if exact is not None and str(_pick(exact, "tenantId", "tenant_id", default="")) == tenant_id:
            return exact
        customer_docs = self.repository.query_by_tenant("customers", tenant_id)
        customers = [_customer_from_doc(doc) for doc in customer_docs]
        aliases = [
            _customer_alias_from_doc(doc)
            for doc in self.repository.query_by_tenant("customerAliases", tenant_id)
        ]
        matched = find_customer_by_code(reference, customers, aliases)
        if matched is None:
            return None
        return next((doc for doc in customer_docs if str(_pick(doc, "id", default="")) == matched.id), None)

    def _apply_item_resolution(
        self,
        task: dict[str, Any],
        resolution: dict[str, Any],
    ) -> dict[str, Any]:
        order_run_id = _pick(task, "orderRunId", "order_run_id", default=None) or _pick(
            resolution, "orderRunId", "order_run_id", default=None
        )
        line_number = int(
            _pick(task, "lineNumber", "line_number", default=0)
            or _pick(resolution, "lineNumber", "line_number", default=0)
            or 0
        )
        matched_item_number = _pick(
            resolution,
            "matchedInternalItemNumber",
            "internalItemNumber",
            "internal_item_number",
            default=None,
        )
        selected_candidate = _pick(resolution, "selectedCandidate", "candidate", default=None)
        if isinstance(selected_candidate, dict):
            matched_item_number = matched_item_number or _pick(
                selected_candidate,
                "internalItemNumber",
                "internal_item_number",
                default=None,
            )
        if not order_run_id or not line_number or not matched_item_number:
            return {"status": "recorded", "message": "Order run, line number, or matched item was not supplied."}

        existing_order = self.repository.get("orderRuns", order_run_id)
        if existing_order is None:
            return {"status": "notFound", "message": f"Order run {order_run_id} was not found."}

        order = _order_from_doc(existing_order)
        updated = False
        for line in order.lines:
            if line.line_number != line_number:
                continue
            line.matched_internal_item_number = str(matched_item_number)
            line.validation_status = MatchStatus.MATCHED
            line.validation_confidence = float(_pick(resolution, "confidence", default=1.0) or 1.0)
            line.validation_method = _pick(resolution, "matchMethod", "match_method", default="manualConsoleResolution")
            line.validation_errors = []
            updated = True
            break
        if not updated:
            return {"status": "notFound", "message": f"Line {line_number} was not found."}

        unresolved = [
            line
            for line in order.lines
            if line.validation_status in {MatchStatus.UNRESOLVED, MatchStatus.POSSIBLE_MATCH}
        ]
        if order.status != ProcessingStatus.FAILED:
            order.status = ProcessingStatus.NEEDS_REVIEW if unresolved else ProcessingStatus.COMPLETED
        order.updated_at = utc_now()
        order_doc = self.repository.upsert("orderRuns", to_dict(order))
        self._upsert_monitor_record_for_order(order_doc)
        return {"orderRunId": order_run_id, "lineNumber": line_number, "matchedInternalItemNumber": matched_item_number}

    def reprocess_order(self, order_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.repository.get("orderRuns", order_run_id)
        if existing is None:
            return {"error": "notFound", "message": f"Order run {order_run_id} was not found."}
        order = _order_from_doc(existing)
        observability = correlation_context(payload, order.correlation_id or order_run_id)
        order.status = ProcessingStatus.RECEIVED
        order.errors = []
        order.parse_warnings = []
        order.output_artifacts = []
        order.correlation_id = observability["correlationId"]
        order.source_metadata["observability"] = merge_observability(
            dict(_pick(order.source_metadata, "observability", default={}) or {}),
            observability,
        )
        order.processing_started_at = None
        order.processing_completed_at = None
        order.updated_at = utc_now()
        order_doc = self.repository.upsert("orderRuns", to_dict(order))
        self._upsert_monitor_record_for_order(order_doc)
        self._audit(
            order.tenant_id,
            "order.reprocessRequested",
            observability["correlationId"],
            order_run_id,
            {
                **payload,
                "orderRunId": order_run_id,
                "customerId": order.customer_id,
                "userIntervention": True,
                "observability": observability,
            },
            customer_id=order.customer_id,
            order_run_id=order_run_id,
            actor=self._actor_from_payload(payload),
        )
        return {"orderRun": _api_value(order), "observability": observability}

    def _console_principal_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        principal = dict(_pick(payload, "principal", "user", default={}) or {})
        headers = {
            str(key).lower(): value
            for key, value in dict(_pick(payload, "headers", default={}) or {}).items()
        }
        easy_auth = self._decode_easy_auth_principal(headers.get("x-ms-client-principal", ""))
        principal.update({key: value for key, value in easy_auth.items() if value})

        header_email = _pick(
            principal,
            "email",
            "preferred_username",
            "upn",
            "userPrincipalName",
            "name",
            default=headers.get("x-ms-client-principal-name", ""),
        )
        payload_email = _pick(
            payload,
            "principalEmail",
            "principal_email",
            "currentUserEmail",
            "current_user_email",
            "userEmail",
            "user_email",
            "email",
            default="",
        )
        email = _normalized_email(header_email or payload_email)
        display_name = _pick(payload, "displayName", "display_name", default=_pick(principal, "displayName", "name", default=email))
        return {
            "email": email,
            "displayName": display_name,
            "authProvider": _pick(principal, "authProvider", "auth_typ", default="microsoft"),
            "microsoftUserId": _pick(principal, "microsoftUserId", "oid", "objectidentifier", default=""),
        }

    def _actor_from_payload(self, payload: dict[str, Any]) -> str:
        actor = _pick(payload, "actor", "resolvedBy", "requestedBy", default="")
        if actor:
            return str(actor)
        principal = self._console_principal_from_payload(payload)
        return principal["email"] or "system"

    def _microsoft_client_secret(self, config: Any) -> str:
        if config.client_secret:
            return config.client_secret
        if config.client_secret_name:
            return self.secret_store.get_secret(config.client_secret_name)
        return ""

    def _store_microsoft_tokens(self, connection_id: str, token: dict[str, Any]) -> dict[str, str]:
        names: dict[str, str] = {}
        refresh_token_value = str(token.get("refresh_token", ""))
        access_token_value = str(token.get("access_token", ""))
        if refresh_token_value:
            name = secret_name("msgraph", connection_id, "refresh-token")
            self.secret_store.set_secret(name, refresh_token_value)
            names["refreshToken"] = name
        if access_token_value:
            name = secret_name("msgraph", connection_id, "access-token")
            self.secret_store.set_secret(name, access_token_value)
            names["accessToken"] = name
        return names

    def _update_mailbox_after_graph_auth(
        self,
        mailbox_id: str,
        connection_id: str,
        status: dict[str, Any],
        authorized_by: str = "",
    ) -> dict[str, Any] | None:
        mailbox = self.repository.get("mailboxAccounts", mailbox_id)
        if mailbox is None:
            return None
        settings = dict(_pick(mailbox, "settings", default={}) or {})
        if authorized_by:
            settings["authorizedBy"] = authorized_by
        settings["graphAccess"] = status
        mailbox.update(
            {
                "connectionId": connection_id,
                "permissionStatus": "active" if status.get("canAccess") else "failed",
                "ingestStatus": "configured" if status.get("canAccess") else "needsAttention",
                "lastTestedAt": status.get("checkedAt", utc_now()),
                "settings": settings,
            }
        )
        return self.repository.upsert("mailboxAccounts", mailbox)

    def _distributor_customers_for_console(self, session: dict[str, Any], current_tenant: dict[str, Any]) -> list[dict[str, Any]]:
        can_view_all = session.get("isPlatformAdmin") or "viewAllCustomers" in set(
            _as_list(session.get("permissions", []))
        )
        tenants = self.repository.list("tenants") if can_view_all else [current_tenant]
        by_id: dict[str, dict[str, Any]] = {}
        for tenant in tenants + [current_tenant]:
            tenant_id = str(_pick(tenant, "tenantId", "tenant_id", "id", default=""))
            if not tenant_id or tenant_id == SYSTEM_TENANT_ID:
                continue
            by_id[tenant_id] = {
                "id": str(_pick(tenant, "id", default=tenant_id)),
                "tenantId": tenant_id,
                "name": _pick(tenant, "name", default=tenant_id),
                "environment": _pick(tenant, "environment", default=""),
                "status": _pick(tenant, "status", default="active"),
                "settings": dict(_pick(tenant, "settings", default={}) or {}),
                "updatedAt": _pick(tenant, "updatedAt", "updated_at", default=""),
                "createdAt": _pick(tenant, "createdAt", "created_at", default=""),
            }
        return sorted(by_id.values(), key=lambda item: str(item.get("name") or item.get("tenantId")).lower())

    @staticmethod
    def _decode_easy_auth_principal(header_value: str) -> dict[str, Any]:
        if not header_value:
            return {}
        try:
            decoded = base64.b64decode(header_value).decode("utf-8")
            principal = json.loads(decoded)
        except (ValueError, json.JSONDecodeError):
            return {}
        claims = {
            str(claim.get("typ", "")).split("/")[-1].lower(): claim.get("val", "")
            for claim in _as_list(principal.get("claims", []))
            if isinstance(claim, dict)
        }
        return {
            "auth_typ": principal.get("auth_typ", ""),
            "name": principal.get("name", ""),
            "email": claims.get("email") or claims.get("preferred_username") or claims.get("upn"),
            "preferred_username": claims.get("preferred_username"),
            "oid": claims.get("objectidentifier") or claims.get("oid"),
        }

    def _ensure_bootstrap_console_admin(self, tenant_id: str) -> None:
        if self._console_user_by_email(tenant_id, BOOTSTRAP_CONSOLE_ADMIN_EMAIL):
            return
        self.upsert_console_user(
            {
                "tenantId": tenant_id,
                "email": BOOTSTRAP_CONSOLE_ADMIN_EMAIL,
                "displayName": "Focus Automate Admin",
                "roles": ["platformAdmin"],
                "enabled": True,
            }
        )

    def _console_user_by_email(self, tenant_id: str, email: str) -> dict[str, Any] | None:
        normalized = _normalized_email(email)
        for user in self.repository.query_by_tenant("consoleUsers", tenant_id):
            if _normalized_email(_pick(user, "email", default="")) == normalized:
                return user
        return None

    def _platform_admin_user_by_email(self, email: str) -> dict[str, Any] | None:
        normalized = _normalized_email(email)
        for user in self.repository.list("consoleUsers"):
            if _normalized_email(_pick(user, "email", default="")) != normalized:
                continue
            if not bool(_pick(user, "enabled", default=True)):
                continue
            if "platformAdmin" in set(_as_list(_pick(user, "roles", default=[]))):
                return user
        return None

    @staticmethod
    def _console_permissions(
        is_platform_admin: bool,
        user_roles: set[str],
        assignments: list[dict[str, Any]],
    ) -> list[str]:
        if is_platform_admin:
            return [
                "platformAdmin",
                "viewAllCustomers",
                "manageUsers",
                "manageCustomers",
                "manageRouting",
                "manageMailboxes",
                "resolveExceptions",
                "reprocessOrders",
                "downloadOutputs",
            ]
        roles = {role for assignment in assignments for role in _as_list(_pick(assignment, "roles", default=[]))}
        roles.update(user_roles)
        if "tenantAdmin" in roles:
            return [
                "viewAllCustomers",
                "manageUsers",
                "manageCustomers",
                "manageRouting",
                "manageMailboxes",
                "resolveExceptions",
                "reprocessOrders",
                "downloadOutputs",
            ]
        permissions = {"viewAssignedCustomers"}
        if "tenantUser" in roles:
            permissions.add("viewAllCustomers")
        if "exceptionResolver" in roles:
            permissions.add("resolveExceptions")
        if "orderManager" in roles:
            permissions.update({"reprocessOrders", "downloadOutputs"})
        if "orderViewer" in roles:
            permissions.add("downloadOutputs")
        return sorted(permissions)

    def _console_permission_error(
        self,
        payload: dict[str, Any],
        permission: str,
        customer_id: str | None = None,
    ) -> dict[str, Any] | None:
        session = self.console_session(payload)
        if not session.get("authorized"):
            return {"session": session}
        if permission not in set(_as_list(session.get("permissions", []))):
            return {"session": session, "error": "forbidden", "message": f"User does not have {permission}."}
        if customer_id and not self._session_can_access_customer(session, customer_id):
            return {"session": session, "error": "forbidden", "message": "Customer is outside this user's assignments."}
        return None

    def _authorized_customer_filter(self, session: dict[str, Any], requested_customer_id: str | None) -> set[str] | None | str:
        if session.get("isPlatformAdmin") or "viewAllCustomers" in set(_as_list(session.get("permissions", []))):
            return {requested_customer_id} if requested_customer_id else None
        allowed = set(_as_list(session.get("allowedCustomerIds", [])))
        if requested_customer_id:
            return {requested_customer_id} if requested_customer_id in allowed else "__denied__"
        return allowed

    def _session_can_access_customer(self, session: dict[str, Any], customer_id: str | None) -> bool:
        if session.get("isPlatformAdmin") or "viewAllCustomers" in set(_as_list(session.get("permissions", []))):
            return True
        return bool(customer_id and customer_id in set(_as_list(session.get("allowedCustomerIds", []))))

    def _filter_customer_documents(
        self,
        documents: list[dict[str, Any]],
        customer_filter: set[str] | None | str,
        session: dict[str, Any],
        include_global: bool = False,
    ) -> list[dict[str, Any]]:
        if customer_filter == "__denied__":
            return []
        can_view_all = session.get("isPlatformAdmin") or "viewAllCustomers" in set(_as_list(session.get("permissions", [])))
        if can_view_all and customer_filter is None:
            return documents
        allowed = set(customer_filter or _as_list(session.get("allowedCustomerIds", [])))
        filtered = []
        for document in documents:
            customer_id = _document_customer_id(document)
            if include_global and customer_id == GLOBAL_CUSTOMER_ID:
                filtered.append(document)
            elif customer_id in allowed:
                filtered.append(document)
            elif customer_id is None and can_view_all:
                filtered.append(document)
        return filtered

    def _filter_exceptions_for_console(
        self,
        exceptions: list[dict[str, Any]],
        visible_orders: list[dict[str, Any]],
        customer_filter: set[str] | None | str,
        session: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if customer_filter == "__denied__":
            return []
        orders_by_id = {order["id"]: order for order in visible_orders if "id" in order}
        visible = []
        for task in exceptions:
            order_run_id = _pick(task, "orderRunId", "order_run_id", default="")
            if order_run_id and order_run_id in orders_by_id:
                visible.append(task)
                continue
            customer_id = self._exception_customer_id(task)
            if self._session_can_access_customer(session, customer_id):
                visible.append(task)
            elif "viewAllCustomers" in set(_as_list(session.get("permissions", []))):
                visible.append(task)
        return visible

    def _filter_audit_events_for_console(
        self,
        audit_events: list[dict[str, Any]],
        visible_orders: list[dict[str, Any]],
        visible_exceptions: list[dict[str, Any]],
        customer_filter: set[str] | None | str,
        session: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if customer_filter == "__denied__":
            return []
        if (
            (session.get("isPlatformAdmin") or "viewAllCustomers" in set(_as_list(session.get("permissions", []))))
            and customer_filter is None
        ):
            return audit_events

        allowed = set(customer_filter or _as_list(session.get("allowedCustomerIds", [])))
        order_ids = {_pick(order, "id", default="") for order in visible_orders}
        email_ids = {
            _pick(order, "emailMessageId", "email_message_id", default="")
            for order in visible_orders
            if _pick(order, "emailMessageId", "email_message_id", default="")
        }
        exception_ids = {_pick(task, "id", default="") for task in visible_exceptions}
        visible = []
        for event in audit_events:
            details = dict(_pick(event, "details", default={}) or {})
            customer_id = _pick(event, "customerId", "customer_id", default=None) or _pick(
                details,
                "customerId",
                "customer_id",
                default=None,
            )
            subject_id = _pick(event, "subjectId", "subject_id", default="")
            order_run_id = _pick(event, "orderRunId", "order_run_id", default="") or _pick(
                details,
                "orderRunId",
                "order_run_id",
                default="",
            )
            email_message_id = _pick(event, "emailMessageId", "email_message_id", default="") or _pick(
                details,
                "emailMessageId",
                "email_message_id",
                default="",
            )
            if customer_id in allowed:
                visible.append(event)
            elif order_run_id in order_ids or subject_id in order_ids:
                visible.append(event)
            elif email_message_id in email_ids or subject_id in email_ids:
                visible.append(event)
            elif subject_id in exception_ids:
                visible.append(event)
        return visible

    def _exception_customer_id(self, task: dict[str, Any]) -> str | None:
        order_run_id = _pick(task, "orderRunId", "order_run_id", default="")
        if order_run_id:
            order = self.repository.get("orderRuns", order_run_id)
            if order is not None:
                customer_id = _document_customer_id(order)
                if customer_id:
                    return customer_id

        email_message_id = _pick(task, "emailMessageId", "email_message_id", default="")
        if email_message_id:
            email = self.repository.get("emailMessages", email_message_id)
            if email is not None:
                customer_id = _document_customer_id(email)
                if customer_id:
                    return customer_id

        context = dict(_pick(task, "context", default={}) or {})
        return _pick(context, "customerId", "customer_id", "mailboxCustomerId", default=None)

    @staticmethod
    def _sort_recent(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            documents,
            key=lambda document: str(
                _pick(document, "updatedAt", "updated_at", "createdAt", "created_at", default="")
            ),
            reverse=True,
        )

    @staticmethod
    def _console_summary(
        order_runs: list[dict[str, Any]],
        exceptions: list[dict[str, Any]],
        items: list[dict[str, Any]],
        email_messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        completed = [order for order in order_runs if _document_status(order) == ProcessingStatus.COMPLETED.value]
        failed = [order for order in order_runs if _document_status(order) == ProcessingStatus.FAILED.value]
        open_exceptions = [task for task in exceptions if _document_status(task) == ExceptionStatus.OPEN.value]
        active_statuses = {
            ProcessingStatus.RECEIVED.value,
            ProcessingStatus.ROUTED.value,
            ProcessingStatus.PROCESSING.value,
            ProcessingStatus.NEEDS_REVIEW.value,
        }
        active_order_email_ids = {
            str(_pick(order, "emailMessageId", "email_message_id", default=""))
            for order in order_runs
            if _document_status(order) in active_statuses
        }
        active_email_count = len(
            [
                email
                for email in email_messages or []
                if _document_status(email) in active_statuses
                and str(_pick(email, "id", default="")) not in active_order_email_ids
            ]
        )
        unresolved_lines = 0
        for order in order_runs:
            for line in _as_list(_pick(order, "lines", default=[])):
                if isinstance(line, dict) and _pick(line, "validationStatus", "validation_status", default="") in {
                    MatchStatus.UNRESOLVED.value,
                    MatchStatus.POSSIBLE_MATCH.value,
                }:
                    unresolved_lines += 1
        total_finished = len(completed) + len(failed)
        success_rate = round(len(completed) / total_finished, 4) if total_finished else 0.0
        return {
            "activeRunCount": len([order for order in order_runs if _document_status(order) in active_statuses]) + active_email_count,
            "processedOrderCount": len(order_runs),
            "successRate": success_rate,
            "openExceptionCount": len(open_exceptions),
            "unresolvedLineCount": unresolved_lines,
            "itemRecordCount": len(items),
        }

    @staticmethod
    def _console_import_targets(tenant_id: str) -> dict[str, Any]:
        endpoint = os.environ.get("COSMOS_ACCOUNT_ENDPOINT", "").rstrip("/")
        database_name = os.environ.get("COSMOS_DATABASE_NAME", "orderProcessor")
        api_base_url = (
            os.environ.get("APIM_API_BASE_URL")
            or os.environ.get("ORDER_PROCESSOR_API_BASE_URL")
            or os.environ.get("ORDER_PROCESSOR_FUNCTION_BASE_URL")
            or ""
        ).rstrip("/")
        account_name = ""
        if endpoint:
            parsed = parse.urlparse(endpoint)
            account_name = parsed.hostname.split(".")[0] if parsed.hostname else ""

        def api_url(path: str) -> str:
            return f"{api_base_url}{path}" if api_base_url else path

        return {
            "tenantId": tenant_id,
            "recommendedWriter": "importApi",
            "authentication": {
                "type": "apimSubscriptionKey",
                "header": "Ocp-Apim-Subscription-Key",
                "note": "Use an APIM subscription key for automation flows; do not grant customer flows direct Cosmos writes.",
            },
            "cosmos": {
                "accountName": account_name,
                "endpoint": endpoint,
                "databaseName": database_name,
            },
            "customerList": {
                "displayName": "Downstream Customer List",
                "cadence": "daily",
                "containerName": "customers",
                "partitionKeyPath": "/tenantId",
                "partitionKeyValue": tenant_id,
                "apiPath": "/imports/customers",
                "apiUrl": api_url("/imports/customers"),
                "minimumBody": {
                    "tenantId": tenant_id,
                    "sourceName": "customers.json",
                    "rows": [
                        {
                            "internal_route_code": "999",
                            "route_code": "900",
                            "stop_no": 999,
                            "cust_code": "100022",
                            "bus_name": "CHOW HOUND STORES - MASTER A/R",
                            "address1": "734 28TH STREET SE",
                            "city": "GRAND RAPIDS",
                            "state": "MI",
                            "zip": "49548",
                            "phone": "616-452-7877",
                            "cust_csr": "rrussell",
                            "csr_email": "richele.russell@frontierdistributing.com",
                            "process_day": "900",
                            "email_addresses": None,
                            "store_website": "WWW.CHOWHOUNDPET.COM",
                            "store_email": None,
                            "mailblast_addr": None,
                            "rank": 1,
                        }
                    ],
                },
            },
            "itemList": {
                "displayName": "Item List",
                "cadence": "weekly",
                "containerName": "items",
                "partitionKeyPath": [
                    "/tenantId",
                    "/customerId",
                ],
                "partitionKeyValue": [
                    tenant_id,
                    GLOBAL_CUSTOMER_ID,
                ],
                "apiPath": "/imports/items",
                "apiUrl": api_url("/imports/items"),
                "minimumBody": {
                    "tenantId": tenant_id,
                    "sourceName": "itemNumbers.json",
                    "rows": [
                        {
                            "part_code": "100510100",
                            "upc_code": "031865BRN4R",
                            "alt_parts_combined": [
                                {"alt_part": "031865BRN4R"},
                                {"alt_part": "10004120"},
                            ],
                            "part_desc": "Bed-r Nest Kraft Irradiated 4 gram 1600 per case",
                        }
                    ],
                },
            },
        }

    @staticmethod
    def _customer_data_status(customers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "customerId": customer["id"],
                "customerCode": _pick(customer, "customerCode", "customer_code", default=""),
                "name": _pick(customer, "name", default=""),
                "sourceName": _pick(customer, "sourceName", "source_name", default=""),
                "sourceRowsBlobUrl": _pick(customer, "sourceRowsBlobUrl", "source_rows_blob_url", default=""),
                "lastImportedAt": _pick(customer, "lastImportedAt", "last_imported_at", default=None),
            }
            for customer in customers
            if "id" in customer
        ]

    @staticmethod
    def _item_data_status(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_customer: dict[str, dict[str, Any]] = {}
        for item in items:
            customer_id = _pick(item, "customerId", "customer_id", default="_unassigned")
            status = by_customer.setdefault(
                customer_id,
                {"customerId": customer_id, "itemCount": 0, "lastImportedAt": None, "sourceRowsBlobUrls": set()},
            )
            status["itemCount"] += 1
            imported_at = _pick(item, "lastImportedAt", "last_imported_at", default=None)
            if imported_at and (status["lastImportedAt"] is None or str(imported_at) > str(status["lastImportedAt"])):
                status["lastImportedAt"] = imported_at
            source_url = _pick(item, "sourceRowsBlobUrl", "source_rows_blob_url", default="")
            if source_url:
                status["sourceRowsBlobUrls"].add(source_url)
        return [
            {
                **status,
                "sourceRowsBlobUrls": sorted(status["sourceRowsBlobUrls"]),
            }
            for status in by_customer.values()
        ]

    def _order_for_artifact_url(self, tenant_id: str, blob_url: str) -> dict[str, Any] | None:
        for order in self.repository.query_by_tenant("orderRuns", tenant_id):
            for artifact in _as_list(_pick(order, "outputArtifacts", "output_artifacts", default=[])):
                if isinstance(artifact, dict) and _pick(artifact, "blobUrl", "blob_url", default="") == blob_url:
                    return order
        return None

    def _create_exception(
        self,
        tenant_id: str,
        task_type: str,
        prompt: str,
        order_run_id: str | None = None,
        email_message_id: str | None = None,
        line_number: int | None = None,
        customer_id: str | None = None,
        correlation_id: str | None = None,
        context: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        identity_parts = [tenant_id, task_type, order_run_id or "", email_message_id or "", str(line_number or "")]
        if dedupe_key:
            identity_parts.append(dedupe_key)
        task = ExceptionTask(
            id=stable_id(*identity_parts),
            tenant_id=tenant_id,
            type=task_type,
            status=ExceptionStatus.OPEN,
            order_run_id=order_run_id,
            email_message_id=email_message_id,
            line_number=line_number,
            prompt=prompt,
            context=context or {},
        )
        doc = to_dict(task)
        stored = self.repository.upsert("exceptionTasks", doc)
        self._upsert_monitor_record_for_exception(stored)
        self._audit(
            tenant_id,
            "exception.created",
            correlation_id or stable_id(*identity_parts),
            stored["id"],
            {
                "exceptionTaskId": stored["id"],
                "type": task_type,
                "status": stored["status"],
                "orderRunId": order_run_id,
                "emailMessageId": email_message_id,
                "lineNumber": line_number,
                "customerId": customer_id,
                "prompt": prompt,
                "context": context or {},
            },
            customer_id=customer_id,
            order_run_id=order_run_id,
            email_message_id=email_message_id,
        )
        return stored

    def _audit(
        self,
        tenant_id: str,
        event_type: str,
        correlation_id: str,
        subject_id: str,
        details: dict[str, Any],
        customer_id: str | None = None,
        order_run_id: str | None = None,
        email_message_id: str | None = None,
        actor: str = "system",
    ) -> None:
        observability = dict(_pick(details, "observability", default={}) or {})
        customer_id = customer_id or _pick(details, "customerId", "customer_id", default=None)
        order_run_id = order_run_id or _pick(details, "orderRunId", "order_run_id", default=None)
        email_message_id = email_message_id or _pick(details, "emailMessageId", "email_message_id", default=None)
        event = AuditEvent(
            id=stable_id(tenant_id, event_type, correlation_id, utc_now()),
            tenant_id=tenant_id,
            event_type=event_type,
            actor=actor,
            correlation_id=correlation_id,
            subject_id=subject_id,
            customer_id=customer_id,
            order_run_id=order_run_id,
            email_message_id=email_message_id,
            operation_id=str(_pick(observability, "operationId", "operation_id", default="")),
            trace_id=str(_pick(observability, "traceId", "trace_id", default="")),
            details=details,
        )
        self.repository.upsert("auditEvents", to_dict(event))


default_api = OrderProcessorApi(repository_from_environment())


def ingest_email(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.ingest_email(payload)


def process_order(order_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.process_order(order_run_id, payload)


def normalize_spreadsheet(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.normalize_spreadsheet(payload)


def extract_spreadsheet_order_lines(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.extract_spreadsheet_order_lines(payload)


def extract_email_body_order(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.extract_email_body_order(payload)


def extract_google_document_ai_order(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.extract_google_document_ai_order(payload)


def identify_customer(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.identify_customer(payload)


def validate_item(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.validate_item(payload)


def import_customers(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.import_customers(payload)


def import_items(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.import_items(payload)


def upsert_mailbox(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.upsert_mailbox(payload)


def test_mailbox_connection(mailbox_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.test_mailbox_connection(mailbox_id, payload)


def poll_mailboxes(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.poll_mailboxes(payload)


def sync_mailbox_subscriptions(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.sync_mailbox_subscriptions(payload)


def renew_mailbox_subscriptions(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.renew_mailbox_subscriptions(payload)


def process_graph_notifications(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.process_graph_notifications(payload)


def console_session(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_session(payload)


def console_dashboard(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_dashboard(payload)


def console_data(section: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_data(section, payload)


def record_ai_cost_event(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.record_ai_cost_event(payload)


def cost_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.cost_summary(payload)


def order_observability_timeline(order_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.order_observability_timeline(order_run_id, payload)


def console_order_timeline(order_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_order_timeline(order_run_id, payload)


def console_output_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_output_artifact(payload)


def console_upsert_mailbox(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_upsert_mailbox(payload)


def console_test_mailbox_connection(mailbox_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_test_mailbox_connection(mailbox_id, payload)


def console_start_microsoft_auth(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_start_microsoft_auth(payload)


def console_complete_microsoft_auth(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_complete_microsoft_auth(payload)


def console_upsert_tenant_config(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_upsert_tenant_config(payload)


def console_upsert_routing_rule(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_upsert_routing_rule(payload)


def console_upsert_customer_config(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_upsert_customer_config(payload)


def console_upsert_customer_identification_rule(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_upsert_customer_identification_rule(payload)


def console_upsert_processor_profile(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_upsert_processor_profile(payload)


def console_upsert_output_profile(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_upsert_output_profile(payload)


def console_upsert_console_user(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_upsert_console_user(payload)


def console_assign_customer_user(customer_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_assign_customer_user(customer_id, payload)


def console_resolve_exception(exception_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_resolve_exception(exception_id, payload)


def console_clear_active_processing_run(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_clear_active_processing_run(run_id, payload)


def console_reprocess_order(order_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_reprocess_order(order_run_id, payload)


def upsert_routing_rule(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.upsert_routing_rule(payload)


def upsert_tenant_config(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.upsert_tenant_config(payload)


def upsert_customer_config(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.upsert_customer_config(payload)


def upsert_customer_identification_rule(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.upsert_customer_identification_rule(payload)


def upsert_processor_profile(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.upsert_processor_profile(payload)


def upsert_output_profile(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.upsert_output_profile(payload)


def upsert_console_user(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.upsert_console_user(payload)


def assign_customer_user(customer_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.assign_customer_user(customer_id, payload)


def upsert_microsoft_auth_connection(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.upsert_microsoft_auth_connection(payload)


def resolve_exception(exception_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.resolve_exception(exception_id, payload)


def reprocess_order(order_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.reprocess_order(order_run_id, payload)
