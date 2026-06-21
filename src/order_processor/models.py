from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def to_dict(value: Any) -> Any:
    if is_dataclass(value):
        return {key: to_dict(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    if isinstance(value, StrEnum):
        return value.value
    return value


class RoutingOutcome(StrEnum):
    KNOWN_ORDER = "knownOrder"
    KNOWN_CUSTOMER_NON_ORDER = "knownCustomerNonOrder"
    NEEDS_CUSTOMER_IDENTIFICATION = "needsCustomerIdentification"
    NEEDS_HUMAN_REVIEW = "needsHumanReview"
    IGNORED = "ignored"


class ProcessingStatus(StrEnum):
    RECEIVED = "received"
    ROUTED = "routed"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needsReview"
    IGNORED = "ignored"


class MatchStatus(StrEnum):
    MATCHED = "matched"
    POSSIBLE_MATCH = "possibleMatch"
    UNRESOLVED = "unresolved"


class ExceptionStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class AuthConnectionStatus(StrEnum):
    CONFIGURED = "configured"
    NEEDS_CONSENT = "needsConsent"
    ACTIVE = "active"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass(slots=True)
class Tenant:
    id: str
    tenant_id: str
    name: str
    environment: str = ""
    status: str = "active"
    settings: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class EmailAttachment:
    name: str
    content_type: str = ""
    size: int = 0
    blob_url: str = ""
    content_id: str = ""
    is_inline: bool = False
    source_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EmailMessage:
    id: str
    tenant_id: str
    mailbox: str
    message_id: str
    subject: str
    sender: str
    received_at: str
    body_text: str = ""
    body_html: str = ""
    categories: list[str] = field(default_factory=list)
    attachments: list[EmailAttachment] = field(default_factory=list)
    status: ProcessingStatus = ProcessingStatus.RECEIVED
    mailbox_account_id: str | None = None
    customer_id: str | None = None
    order_run_id: str | None = None
    correlation_id: str | None = None
    routing: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class RoutingRule:
    id: str
    tenant_id: str
    name: str
    outcome: RoutingOutcome
    priority: int = 100
    enabled: bool = True
    customer_id: str | None = None
    processor_profile_id: str | None = None
    mailbox_account_ids: list[str] = field(default_factory=list)
    mailbox_addresses: list[str] = field(default_factory=list)
    sender_equals: list[str] = field(default_factory=list)
    sender_domains: list[str] = field(default_factory=list)
    subject_regex: list[str] = field(default_factory=list)
    body_regex: list[str] = field(default_factory=list)
    known_webstore_patterns: list[str] = field(default_factory=list)
    prior_processed_subject_regex: list[str] = field(default_factory=list)
    attachment_extensions: list[str] = field(default_factory=list)
    attachment_content_types: list[str] = field(default_factory=list)
    attachment_name_regex: list[str] = field(default_factory=list)
    required_attachment: bool = False
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RoutingDecision:
    outcome: RoutingOutcome
    rule_id: str | None = None
    customer_id: str | None = None
    processor_profile_id: str | None = None
    mailbox_account_id: str | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    matched_signals: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CustomerProfile:
    id: str
    tenant_id: str
    customer_code: str
    name: str
    route_number: str = ""
    csr_email: str = ""
    csr_folder: str = ""
    store_number: str = ""
    sender_domains: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    known_subject_patterns: list[str] = field(default_factory=list)
    embedding: list[float] = field(default_factory=list)
    source_name: str = ""
    source_rows_blob_url: str = ""
    last_imported_at: str | None = None
    raw_source: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CustomerAlias:
    id: str
    tenant_id: str
    customer_id: str
    alias_type: str
    value: str
    normalized_value: str
    source: str = ""
    confidence: float = 1.0
    raw_source: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class CustomerIdentificationResult:
    status: MatchStatus
    customer_id: str | None = None
    customer_code: str | None = None
    route_number: str | None = None
    match_method: str = ""
    confidence: float = 0.0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    extracted_signals: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ItemRecord:
    id: str
    tenant_id: str
    customer_id: str
    internal_item_number: str
    description: str = ""
    upc: str = ""
    customer_item_numbers: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    embedding: list[float] = field(default_factory=list)
    source_name: str = ""
    source_rows_blob_url: str = ""
    last_imported_at: str | None = None
    raw_source: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ItemValidationResult:
    status: MatchStatus
    matched_item_id: str | None = None
    matched_internal_item_number: str | None = None
    match_method: str = ""
    confidence: float = 0.0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    unresolved_reason: str | None = None


@dataclass(slots=True)
class OrderLine:
    line_number: int
    quantity: float | None = None
    provided_item_number: str = ""
    provided_upc: str = ""
    description: str = ""
    unit: str = ""
    unit_price: float | None = None
    source_row_index: int | None = None
    matched_internal_item_number: str | None = None
    validation_status: MatchStatus = MatchStatus.UNRESOLVED
    validation_confidence: float = 0.0
    validation_method: str = ""
    validation_candidates: list[dict[str, Any]] = field(default_factory=list)
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OrderRun:
    id: str
    tenant_id: str
    email_message_id: str
    customer_id: str | None = None
    correlation_id: str | None = None
    processor_profile_id: str | None = None
    status: ProcessingStatus = ProcessingStatus.RECEIVED
    header: dict[str, Any] = field(default_factory=dict)
    po_number: str = ""
    order_number: str = ""
    source_type: str = ""
    source_file_name: str = ""
    source_metadata: dict[str, Any] = field(default_factory=dict)
    processor_type: str = ""
    processor_version: str = ""
    lines: list[OrderLine] = field(default_factory=list)
    output_artifacts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    parse_warnings: list[dict[str, Any]] = field(default_factory=list)
    processing_started_at: str | None = None
    processing_completed_at: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class ProcessorProfile:
    id: str
    tenant_id: str
    customer_id: str | None
    name: str
    processor_type: str
    output_profile_id: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OutputProfile:
    id: str
    tenant_id: str
    customer_id: str
    name: str
    output_type: str
    destination: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MailboxAccount:
    id: str
    tenant_id: str
    customer_id: str
    mailbox_address: str
    display_name: str = ""
    provider: str = "microsoft365"
    connection_id: str = ""
    enabled: bool = True
    ingest_status: str = "configured"
    permission_status: str = "unknown"
    required_permissions: list[str] = field(default_factory=list)
    graph_user_id: str = ""
    folder_ids: dict[str, str] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    last_tested_at: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class MicrosoftAuthConnection:
    id: str
    tenant_id: str
    provider: str
    display_name: str
    customer_id: str | None = None
    owner_email: str = ""
    connection_type: str = "delegated"
    status: AuthConnectionStatus = AuthConnectionStatus.CONFIGURED
    scopes: list[str] = field(default_factory=list)
    key_vault_secret_names: dict[str, str] = field(default_factory=dict)
    power_automate_connection_reference: str = ""
    tenant_authority: str = ""
    consented_by: str = ""
    consented_at: str | None = None
    expires_at: str | None = None
    last_tested_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class ConsoleUser:
    id: str
    tenant_id: str
    email: str
    display_name: str = ""
    roles: list[str] = field(default_factory=list)
    enabled: bool = True
    auth_provider: str = "microsoft"
    microsoft_user_id: str = ""
    last_login_at: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class CustomerUserAssignment:
    id: str
    tenant_id: str
    customer_id: str
    email: str
    roles: list[str] = field(default_factory=list)
    enabled: bool = True
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class ExceptionTask:
    id: str
    tenant_id: str
    type: str
    status: ExceptionStatus
    order_run_id: str | None = None
    email_message_id: str | None = None
    line_number: int | None = None
    assigned_to: str = ""
    prompt: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    resolution: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    resolved_at: str | None = None


@dataclass(slots=True)
class AuditEvent:
    id: str
    tenant_id: str
    event_type: str
    actor: str
    correlation_id: str
    subject_id: str
    customer_id: str | None = None
    order_run_id: str | None = None
    email_message_id: str | None = None
    operation_id: str = ""
    trace_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
