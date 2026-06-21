from __future__ import annotations

import base64
import json
from typing import Any

from .customer_identification import (
    DEFAULT_CUSTOMER_CONFIDENCE_THRESHOLD,
    CustomerVectorSearch,
    customer_vector_search_from_environment,
    identify_customer as identify_customer_from_email,
)
from .data_model import GLOBAL_CUSTOMER_ID, keys_to_camel
from .imports import normalize_customer_row, normalize_item_row, stable_id
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
    to_dict,
    utc_now,
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
from .routing import default_order_signal, evaluate_routing
from .storage import InMemoryRepository, repository_from_environment


BOOTSTRAP_CONSOLE_ADMIN_EMAIL = "connect@focuseautomate.com"


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


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _api_value(value: Any) -> Any:
    return keys_to_camel(to_dict(value))


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
    outcome = _pick(doc, "outcome", default=RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION)
    if not isinstance(outcome, RoutingOutcome):
        outcome = RoutingOutcome(outcome)

    return RoutingRule(
        id=str(_pick(doc, "id")),
        tenant_id=_pick(doc, "tenantId", "tenant_id", default="default"),
        name=_pick(doc, "name", default=""),
        outcome=outcome,
        priority=int(_pick(doc, "priority", default=100)),
        enabled=bool(_pick(doc, "enabled", default=True)),
        customer_id=_pick(doc, "customerId", "customer_id", default=None),
        processor_profile_id=_pick(doc, "processorProfileId", "processor_profile_id", default=None),
        mailbox_account_ids=list(_as_list(_pick(doc, "mailboxAccountIds", "mailbox_account_ids", default=[]))),
        mailbox_addresses=list(_as_list(_pick(doc, "mailboxAddresses", "mailbox_addresses", default=[]))),
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
    )


def _customer_from_doc(doc: dict[str, Any]) -> CustomerProfile:
    return CustomerProfile(
        id=str(_pick(doc, "id")),
        tenant_id=_pick(doc, "tenantId", "tenant_id", default="default"),
        customer_code=_pick(doc, "customerCode", "customer_code", default=""),
        name=_pick(doc, "name", default=""),
        route_number=_pick(doc, "routeNumber", "route_number", default=""),
        csr_email=_pick(doc, "csrEmail", "csr_email", default=""),
        csr_folder=_pick(doc, "csrFolder", "csr_folder", default=""),
        store_number=_pick(doc, "storeNumber", "store_number", default=""),
        sender_domains=list(_as_list(_pick(doc, "senderDomains", "sender_domains", default=[]))),
        aliases=list(_as_list(_pick(doc, "aliases", default=[]))),
        known_subject_patterns=list(
            _as_list(_pick(doc, "knownSubjectPatterns", "known_subject_patterns", default=[]))
        ),
        embedding=list(_as_list(_pick(doc, "embedding", default=[]))),
        source_name=_pick(doc, "sourceName", "source_name", default=""),
        source_rows_blob_url=_pick(doc, "sourceRowsBlobUrl", "source_rows_blob_url", default=""),
        last_imported_at=_pick(doc, "lastImportedAt", "last_imported_at", default=None),
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


def _mailbox_customer_id(mailbox: dict[str, Any] | None) -> str | None:
    if not mailbox:
        return None
    return _pick(mailbox, "customerId", "customer_id", default=None)


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


def _document_customer_id(document: dict[str, Any]) -> str | None:
    customer_id = _pick(document, "customerId", "customer_id", default=None)
    if customer_id:
        return customer_id
    if "customerCode" in document or "customer_code" in document:
        return _pick(document, "id", default=None)
    return None


def _document_status(document: dict[str, Any]) -> str:
    return str(_pick(document, "status", default="") or "")


class OrderProcessorApi:
    def __init__(
        self,
        repository: InMemoryRepository | None = None,
        customer_vector_search: CustomerVectorSearch | None = None,
        source_archive: SourceRowArchive | None = None,
        import_embedding_client: TextEmbeddingClient | None = None,
        output_artifact_store: OutputArtifactStore | None = None,
    ) -> None:
        self.repository = repository or InMemoryRepository()
        self.customer_vector_search = (
            customer_vector_search
            if customer_vector_search is not None
            else customer_vector_search_from_environment(self.repository)
        )
        self.source_archive = source_archive or source_archive_from_environment()
        self.import_embedding_client = (
            import_embedding_client
            if import_embedding_client is not None
            else import_embedding_client_from_environment()
        )
        self.output_artifact_store = output_artifact_store or output_artifact_store_from_environment()

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
            account_customer_id = _mailbox_customer_id(mailbox_account)
        else:
            account_customer_id = None

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
            email.customer_id = email.customer_id or account_customer_id
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
        elif email.customer_id and account_customer_id and email.customer_id != account_customer_id:
            decision = RoutingDecision(
                outcome=RoutingOutcome.NEEDS_HUMAN_REVIEW,
                mailbox_account_id=email.mailbox_account_id,
                customer_id=account_customer_id,
                confidence=0.0,
                reasons=[
                    f"payload customer {email.customer_id} conflicts with mailbox account customer {account_customer_id}"
                ],
                matched_signals={
                    "mailbox": email.mailbox,
                    "mailboxAccountId": email.mailbox_account_id,
                    "payloadCustomerId": email.customer_id,
                    "mailboxCustomerId": account_customer_id,
                    "defaultOrderSignal": default_order_signal(email),
                },
            )
            email.customer_id = account_customer_id
        else:
            email.customer_id = email.customer_id or account_customer_id
            rules = [
                _routing_rule_from_doc(doc)
                for doc in self.repository.query_by_tenant("routingRules", email.tenant_id)
            ]
            decision = evaluate_routing(email, _candidate_routing_rules(email, rules))

        email.customer_id = decision.customer_id or email.customer_id
        email.routing = to_dict(decision)
        email.updated_at = utc_now()
        email.status = self._status_for_routing_decision(decision)
        self.repository.upsert("emailMessages", to_dict(email))

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
            self.repository.upsert("orderRuns", to_dict(order_run))
            self.repository.upsert("emailMessages", to_dict(email))

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
        order = process_order_payload(order, payload, processor_profile)

        items = [_item_from_doc(doc) for doc in self.repository.query_by_tenant("items", order.tenant_id)]
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
        self.repository.upsert("orderRuns", to_dict(order))
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
        return {"orderRun": _api_value(order), "unresolvedLineCount": len(unresolved), "observability": observability}

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
            confidence_threshold=confidence_threshold,
        )

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
            self.repository.upsert("emailMessages", existing_email)

            order_run_id = _pick(existing_email, "orderRunId", "order_run_id", default=email.order_run_id)
            if order_run_id:
                existing_order = self.repository.get("orderRuns", order_run_id)
                if existing_order is not None:
                    existing_order["customerId"] = result.customer_id
                    existing_order["updatedAt"] = utc_now()
                    self.repository.upsert("orderRuns", existing_order)
        elif email.order_run_id:
            existing_order = self.repository.get("orderRuns", email.order_run_id)
            if existing_order is not None:
                existing_order["customerId"] = result.customer_id
                existing_order["updatedAt"] = utc_now()
                self.repository.upsert("orderRuns", existing_order)

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
                [_item_from_doc(doc) for doc in self.repository.query_by_customer("items", tenant_id, customer_id)]
                if customer_id
                else [_item_from_doc(doc) for doc in self.repository.query_by_tenant("items", tenant_id)]
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
        self.repository.upsert("orderRuns", to_dict(order))
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
        observability = correlation_context(payload, archive.import_run_id)
        result["observability"] = observability
        self._audit(tenant_id, "customers.imported", observability["correlationId"], archive.import_run_id, result)
        return result

    def import_items(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        customer_id = _pick(payload, "customerId", "customer_id", default="")
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
        for row_index, row in validation.valid_rows:
            row_metadata = {**source_metadata, "rowIndex": row_index, "customerId": customer_id}
            item = normalize_item_row(tenant_id, customer_id, row, profile.field_map, row_metadata)
            item = apply_item_embedding(item, self.import_embedding_client)
            if self.repository.get("items", item.id):
                updated_count += 1
            else:
                created_count += 1
            self.repository.upsert("items", to_dict(item))
            imported.append(item)

        schedule = refresh_schedule_for_import(ITEM_IMPORT_TYPE, profile, payload, imported_at)
        errors = [*parsed.errors, *validation.errors]
        result = {
            "importType": ITEM_IMPORT_TYPE,
            "importRunId": archive.import_run_id,
            "customerId": customer_id,
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
            "items": [_api_value(item) for item in imported],
        }
        observability = correlation_context(payload, archive.import_run_id)
        result["observability"] = observability
        self._audit(
            tenant_id,
            "items.imported",
            observability["correlationId"],
            archive.import_run_id,
            result,
            customer_id=customer_id,
        )
        return result

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
            "permissions": self._console_permissions(is_platform_admin, assignments),
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

    def console_dashboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.console_session(payload)
        if not session.get("authorized"):
            return {"session": session}

        tenant_id = session["tenantId"]
        requested_customer_id = _pick(payload, "customerId", "customer_id", default=None)
        customer_filter = self._authorized_customer_filter(session, requested_customer_id)
        if customer_filter == "__denied__":
            return {"session": session, "error": "forbidden", "message": "Customer is outside this user's assignments."}

        customers = self._filter_customer_documents(
            self.repository.query_by_tenant("customers", tenant_id),
            customer_filter,
            session,
        )
        order_runs = self._filter_customer_documents(
            self.repository.query_by_tenant("orderRuns", tenant_id),
            customer_filter,
            session,
        )
        exceptions = self._filter_exceptions_for_console(
            self.repository.query_by_tenant("exceptionTasks", tenant_id),
            order_runs,
            customer_filter,
            session,
        )
        mailboxes = self._filter_customer_documents(
            self.repository.query_by_tenant("mailboxAccounts", tenant_id),
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
            self.repository.query_by_tenant("items", tenant_id),
            customer_filter,
            session,
        )
        audit_events = self._filter_audit_events_for_console(
            self.repository.query_by_tenant("auditEvents", tenant_id),
            order_runs,
            exceptions,
            customer_filter,
            session,
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

        summary = self._console_summary(order_runs, exceptions, items)
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
            "summary": summary,
            "observabilityMetrics": observability_metrics,
            "recentAuditEvents": self._sort_recent(audit_events)[:50],
            "activeRuns": self._sort_recent(active_runs),
            "processedOrders": self._sort_recent(processed_orders),
            "exceptionQueue": self._sort_recent(
                [item for item in exceptions if _document_status(item) == ExceptionStatus.OPEN.value]
            ),
            "mailboxes": self._sort_recent(mailboxes),
            "routingRules": sorted(routing_rules, key=lambda rule: int(_pick(rule, "priority", default=100) or 100)),
            "customers": sorted(customers, key=lambda customer: str(_pick(customer, "name", default="")).lower()),
            "customerDataStatus": self._customer_data_status(customers),
            "itemDataStatus": self._item_data_status(items),
            "processorProfiles": self._filter_customer_documents(
                self.repository.query_by_tenant("processorProfiles", tenant_id),
                customer_filter,
                session,
                include_global=True,
            ),
            "outputProfiles": self._filter_customer_documents(
                self.repository.query_by_tenant("outputProfiles", tenant_id),
                customer_filter,
                session,
                include_global=True,
            ),
            "microsoftAuthConnections": self._filter_customer_documents(
                self.repository.query_by_tenant("microsoftAuthConnections", tenant_id),
                customer_filter,
                session,
                include_global=True,
            ),
            "outputArtifacts": output_artifacts,
        }

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
        error = self._console_permission_error(
            payload,
            "manageMailboxes",
            _pick(payload, "customerId", "customer_id", default=None),
        )
        if error:
            return error
        result = self.upsert_mailbox(payload)
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
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        customer_id = _pick(payload, "customerId", "customer_id", default=GLOBAL_CUSTOMER_ID)
        rule_doc = {
            "id": _pick(payload, "id", default=stable_id(tenant_id, customer_id, _pick(payload, "name", default="routing-rule"))),
            "tenantId": tenant_id,
            "customerId": customer_id,
            "name": _pick(payload, "name", default=""),
            "outcome": _pick(payload, "outcome", default=RoutingOutcome.NEEDS_HUMAN_REVIEW),
            "priority": int(_pick(payload, "priority", default=100) or 100),
            "enabled": bool(_pick(payload, "enabled", default=True)),
            "processorProfileId": _pick(payload, "processorProfileId", "processor_profile_id", default=None),
            "mailboxAccountIds": list(_as_list(_pick(payload, "mailboxAccountIds", "mailbox_account_ids", default=[]))),
            "mailboxAddresses": list(_as_list(_pick(payload, "mailboxAddresses", "mailbox_addresses", default=[]))),
            "senderEquals": list(_as_list(_pick(payload, "senderEquals", "sender_equals", default=[]))),
            "senderDomains": list(_as_list(_pick(payload, "senderDomains", "sender_domains", default=[]))),
            "subjectRegex": list(_as_list(_pick(payload, "subjectRegex", "subject_regex", default=[]))),
            "bodyRegex": list(_as_list(_pick(payload, "bodyRegex", "body_regex", default=[]))),
            "knownWebstorePatterns": list(_as_list(_pick(payload, "knownWebstorePatterns", "known_webstore_patterns", default=[]))),
            "priorProcessedSubjectRegex": list(_as_list(_pick(payload, "priorProcessedSubjectRegex", "prior_processed_subject_regex", default=[]))),
            "attachmentExtensions": list(_as_list(_pick(payload, "attachmentExtensions", "attachment_extensions", default=[]))),
            "attachmentContentTypes": list(_as_list(_pick(payload, "attachmentContentTypes", "attachment_content_types", default=[]))),
            "attachmentNameRegex": list(_as_list(_pick(payload, "attachmentNameRegex", "attachment_name_regex", default=[]))),
            "requiredAttachment": bool(_pick(payload, "requiredAttachment", "required_attachment", default=False)),
            "tags": list(_as_list(_pick(payload, "tags", default=[]))),
        }
        rule = _routing_rule_from_doc(rule_doc)
        stored = self.repository.upsert("routingRules", to_dict(rule))
        self._audit(tenant_id, "routingRule.upserted", stored["id"], stored["id"], {"customerId": customer_id})
        return {"routingRule": stored}

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
            csr_email=_pick(payload, "csrEmail", "csr_email", default=""),
            csr_folder=_pick(payload, "csrFolder", "csr_folder", default=""),
            store_number=_pick(payload, "storeNumber", "store_number", default=""),
            sender_domains=list(_as_list(_pick(payload, "senderDomains", "sender_domains", default=[]))),
            aliases=list(_as_list(_pick(payload, "aliases", default=[]))),
            known_subject_patterns=list(_as_list(_pick(payload, "knownSubjectPatterns", "known_subject_patterns", default=[]))),
            source_name=_pick(payload, "sourceName", "source_name", default="console"),
            source_rows_blob_url=_pick(payload, "sourceRowsBlobUrl", "source_rows_blob_url", default=""),
            last_imported_at=_pick(payload, "lastImportedAt", "last_imported_at", default=None),
            raw_source=dict(_pick(payload, "rawSource", "raw_source", default={}) or {}),
        )
        stored = self.repository.upsert("customers", to_dict(customer))
        self._audit(tenant_id, "customerConfig.upserted", stored["id"], stored["id"], {"customerCode": customer_code})
        return {"customer": stored}

    def upsert_processor_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        customer_id = _pick(payload, "customerId", "customer_id", default=GLOBAL_CUSTOMER_ID)
        profile = ProcessorProfile(
            id=_pick(payload, "id", "processorProfileId", "processor_profile_id", default=stable_id(tenant_id, customer_id, _pick(payload, "name", default="processor"))),
            tenant_id=tenant_id,
            customer_id=customer_id,
            name=_pick(payload, "name", default=""),
            processor_type=_pick(payload, "processorType", "processor_type", default="csv"),
            output_profile_id=_pick(payload, "outputProfileId", "output_profile_id", default=None),
            settings=dict(_pick(payload, "settings", default={}) or {}),
        )
        stored = self.repository.upsert("processorProfiles", to_dict(profile))
        self._audit(tenant_id, "processorProfile.upserted", stored["id"], stored["id"], {"customerId": customer_id})
        return {"processorProfile": stored}

    def upsert_output_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        customer_id = _pick(payload, "customerId", "customer_id", default=GLOBAL_CUSTOMER_ID)
        profile = OutputProfile(
            id=_pick(payload, "id", "outputProfileId", "output_profile_id", default=stable_id(tenant_id, customer_id, _pick(payload, "name", default="output"))),
            tenant_id=tenant_id,
            customer_id=customer_id,
            name=_pick(payload, "name", default=""),
            output_type=_pick(payload, "outputType", "output_type", default="csv"),
            destination=dict(_pick(payload, "destination", default={}) or {}),
            settings=dict(_pick(payload, "settings", default={}) or {}),
        )
        stored = self.repository.upsert("outputProfiles", to_dict(profile))
        self._audit(tenant_id, "outputProfile.upserted", stored["id"], stored["id"], {"customerId": customer_id})
        return {"outputProfile": stored}

    def upsert_mailbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _pick(payload, "tenantId", "tenant_id", default="default")
        customer_id = _pick(payload, "customerId", "customer_id", default="")
        mailbox_address = str(_pick(payload, "mailboxAddress", "mailbox_address", default="")).strip().lower()
        mailbox_id = _pick(
            payload,
            "id",
            "mailboxAccountId",
            "mailbox_account_id",
            default=stable_id(tenant_id, customer_id, mailbox_address),
        )
        mailbox = MailboxAccount(
            id=mailbox_id,
            tenant_id=tenant_id,
            customer_id=customer_id,
            mailbox_address=mailbox_address,
            display_name=_pick(payload, "displayName", "display_name", default=mailbox_address),
            provider=_pick(payload, "provider", default="microsoft365"),
            connection_id=_pick(payload, "connectionId", "connection_id", default=""),
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
            settings=dict(_pick(payload, "settings", default={}) or {}),
        )
        stored = self.repository.upsert("mailboxAccounts", to_dict(mailbox))
        self._audit(tenant_id, "mailbox.upserted", stored["id"], stored["id"], {"customerId": customer_id})
        return {"mailboxAccount": stored}

    def test_mailbox_connection(self, mailbox_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        mailbox = self.repository.get("mailboxAccounts", mailbox_id)
        if mailbox is None:
            return {"error": "notFound", "message": f"Mailbox account {mailbox_id} was not found."}

        status = {
            "mailboxAccountId": mailbox_id,
            "provider": mailbox.get("provider", "microsoft365"),
            "connectionId": mailbox.get("connectionId", ""),
            "canAccess": False,
            "status": "notTested",
            "message": "Live Microsoft Graph mailbox validation is implemented when Graph connection wiring is added.",
            "checkedAt": utc_now(),
            "request": payload,
        }
        return {"mailboxAccount": mailbox, "connectionStatus": status}

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
        self._audit(tenant_id, "microsoftAuthConnection.upserted", stored["id"], stored["id"], {"provider": provider})
        return {"microsoftAuthConnection": stored}

    def resolve_exception(self, exception_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.repository.get("exceptionTasks", exception_id)
        if existing is None:
            return {"error": "notFound", "message": f"Exception task {exception_id} was not found."}

        observability = correlation_context(payload, exception_id)
        resolution = _pick(payload, "resolution", default=payload)
        resolution_result = self._apply_exception_resolution(existing, resolution)
        existing["status"] = ExceptionStatus.RESOLVED.value
        existing["resolution"] = resolution
        existing["resolvedBy"] = self._actor_from_payload(payload)
        existing["resolved_at"] = utc_now()
        stored = self.repository.upsert("exceptionTasks", existing)
        self._audit(
            _pick(existing, "tenantId", "tenant_id", default="default"),
            "exception.resolved",
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

    def _apply_customer_resolution(
        self,
        task: dict[str, Any],
        resolution: dict[str, Any],
    ) -> dict[str, Any]:
        customer_id = _pick(resolution, "selectedCustomerId", "customerId", "customer_id", default=None)
        if not customer_id:
            return {"status": "recorded", "message": "No customer id supplied."}

        updated: dict[str, Any] = {"customerId": customer_id}
        order_run_id = _pick(task, "orderRunId", "order_run_id", default=None) or _pick(
            resolution, "orderRunId", "order_run_id", default=None
        )
        if order_run_id:
            order = self.repository.get("orderRuns", order_run_id)
            if order is not None:
                order["customerId"] = customer_id
                order["updatedAt"] = utc_now()
                self.repository.upsert("orderRuns", order)
                updated["orderRunId"] = order_run_id

        email_message_id = _pick(task, "emailMessageId", "email_message_id", default=None) or _pick(
            resolution, "emailMessageId", "email_message_id", default=None
        )
        if email_message_id:
            email = self.repository.get("emailMessages", email_message_id)
            if email is not None:
                email["customerId"] = customer_id
                email["updatedAt"] = utc_now()
                self.repository.upsert("emailMessages", email)
                updated["emailMessageId"] = email_message_id
        return updated

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
        self.repository.upsert("orderRuns", to_dict(order))
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
        self.repository.upsert("orderRuns", to_dict(order))
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

    @staticmethod
    def _console_permissions(is_platform_admin: bool, assignments: list[dict[str, Any]]) -> list[str]:
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
        permissions = {"viewAssignedCustomers"}
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
        if session.get("isPlatformAdmin"):
            return {requested_customer_id} if requested_customer_id else None
        allowed = set(_as_list(session.get("allowedCustomerIds", [])))
        if requested_customer_id:
            return {requested_customer_id} if requested_customer_id in allowed else "__denied__"
        return allowed

    def _session_can_access_customer(self, session: dict[str, Any], customer_id: str | None) -> bool:
        if session.get("isPlatformAdmin"):
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
        if session.get("isPlatformAdmin") and customer_filter is None:
            return documents
        allowed = set(customer_filter or _as_list(session.get("allowedCustomerIds", [])))
        filtered = []
        for document in documents:
            customer_id = _document_customer_id(document)
            if include_global and customer_id == GLOBAL_CUSTOMER_ID:
                filtered.append(document)
            elif customer_id in allowed:
                filtered.append(document)
            elif customer_id is None and session.get("isPlatformAdmin"):
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
            context = dict(_pick(task, "context", default={}) or {})
            customer_id = _pick(context, "customerId", "customer_id", "mailboxCustomerId", default=None)
            if self._session_can_access_customer(session, customer_id):
                visible.append(task)
            elif session.get("isPlatformAdmin"):
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
        if session.get("isPlatformAdmin") and customer_filter is None:
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
    ) -> dict[str, Any]:
        completed = [order for order in order_runs if _document_status(order) == ProcessingStatus.COMPLETED.value]
        failed = [order for order in order_runs if _document_status(order) == ProcessingStatus.FAILED.value]
        open_exceptions = [task for task in exceptions if _document_status(task) == ExceptionStatus.OPEN.value]
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
            "activeRunCount": len([order for order in order_runs if _document_status(order) in {
                ProcessingStatus.RECEIVED.value,
                ProcessingStatus.ROUTED.value,
                ProcessingStatus.PROCESSING.value,
                ProcessingStatus.NEEDS_REVIEW.value,
            }]),
            "processedOrderCount": len(order_runs),
            "successRate": success_rate,
            "openExceptionCount": len(open_exceptions),
            "unresolvedLineCount": unresolved_lines,
            "itemRecordCount": len(items),
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


def console_session(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_session(payload)


def console_dashboard(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_dashboard(payload)


def order_observability_timeline(order_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.order_observability_timeline(order_run_id, payload)


def console_order_timeline(order_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_order_timeline(order_run_id, payload)


def console_output_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_output_artifact(payload)


def console_upsert_mailbox(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_upsert_mailbox(payload)


def console_upsert_routing_rule(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_upsert_routing_rule(payload)


def console_upsert_customer_config(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_upsert_customer_config(payload)


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


def console_reprocess_order(order_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.console_reprocess_order(order_run_id, payload)


def upsert_routing_rule(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.upsert_routing_rule(payload)


def upsert_customer_config(payload: dict[str, Any]) -> dict[str, Any]:
    return default_api.upsert_customer_config(payload)


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
