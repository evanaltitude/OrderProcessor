from __future__ import annotations

from email.utils import parseaddr
import re
from typing import Any

from .customer_identification import normalize_domain, normalize_identifier
from .data_model import GLOBAL_CUSTOMER_ID
from .models import CustomerAlias, CustomerProfile, EmailMessage, RoutingDecision, RoutingOutcome, RoutingRule
from .routing import rule_matches


TRIAGE_PHASE_ORDER = ("webstoreOrder", "previouslyProcessed", "orderCandidate", "nonOrder", "general")


def normalize_triage_phase(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", str(value or "general").lower())
    aliases = {
        "webstore": "webstoreOrder",
        "webstoreorder": "webstoreOrder",
        "knownwebstore": "webstoreOrder",
        "previouslyprocessed": "previouslyProcessed",
        "priorprocessed": "previouslyProcessed",
        "processedreply": "previouslyProcessed",
        "order": "orderCandidate",
        "ordercandidate": "orderCandidate",
        "orderprocess": "orderCandidate",
        "nonorder": "nonOrder",
        "nonorderroute": "nonOrder",
        "general": "general",
    }
    return aliases.get(normalized, "general")


def evaluate_email_triage(
    email: EmailMessage,
    rules: list[RoutingRule],
    customers: list[CustomerProfile] | None = None,
    aliases: list[CustomerAlias] | None = None,
) -> RoutingDecision:
    customers = customers or []
    aliases = aliases or []
    sorted_rules = sorted([rule for rule in rules if rule.enabled], key=lambda item: item.priority)

    for phase in TRIAGE_PHASE_ORDER:
        for rule in sorted_rules:
            if normalize_triage_phase(rule.phase) != phase:
                continue
            matches, reasons = rule_matches(email, rule)
            if not matches:
                continue
            return _decision_for_rule(email, rule, phase, reasons, customers, aliases)

    return _fallback_decision(email)


def build_email_action_plan(
    email: EmailMessage,
    customer: CustomerProfile | None,
    rule: RoutingRule,
    action_key: str,
) -> dict[str, Any]:
    policy = dict(rule.email_actions or {})
    if not policy and not rule.subject_update:
        return {}

    csr_field = str(
        policy.get("csrNameField")
        or policy.get("csrField")
        or _as_dict(policy.get("categories")).get("csrField")
        or "csrFolder"
    )
    context = _render_context(email, customer, rule, action_key, csr_field)

    subject_policy = {**_as_dict(policy.get("subject")), **dict(rule.subject_update or {})}
    subject_template = str(subject_policy.get("template") or policy.get("subjectTemplate") or "")
    subject = {}
    if subject_template:
        subject = {
            "mode": "replace",
            "template": subject_template,
            "value": _render_template(subject_template, context),
        }

    category_templates = _as_list(policy.get("categoryTemplates"))
    category_policy = _as_dict(policy.get("categories"))
    category_templates.extend(_as_list(category_policy.get("templates")))
    categories = [_render_template(template, context) for template in category_templates]
    categories = [value for value in _unique(categories) if value]

    return {
        "actionKey": action_key,
        "productionActionsEnabled": _bool_policy(policy.get("productionActionsEnabled"), default=True),
        "subject": subject,
        "move": _resolve_move(policy, action_key, customer),
        "categories": categories,
        "categoryCsrField": csr_field,
    }


def extract_customer_code(email: EmailMessage, extraction: dict[str, Any]) -> dict[str, Any]:
    pattern = str(
        extraction.get("regex")
        or extraction.get("pattern")
        or extraction.get("customerCodeRegex")
        or ""
    )
    if not pattern:
        return {"value": "", "matched": False, "reason": "no extraction regex configured"}

    source = str(extraction.get("source") or extraction.get("customerCodeSource") or "combined")
    value = _source_text(email, source)
    try:
        match = re.search(pattern, value, re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        return {"value": "", "matched": False, "reason": f"invalid extraction regex: {exc}"}
    if not match:
        return {"value": "", "matched": False, "reason": "extraction regex did not match", "source": source}

    group = extraction.get("group", extraction.get("customerCodeGroup", "customerCode"))
    extracted = _match_group(match, group)
    return {
        "value": extracted.strip(),
        "matched": bool(extracted.strip()),
        "source": source,
        "regex": pattern,
        "group": group,
    }


def _decision_for_rule(
    email: EmailMessage,
    rule: RoutingRule,
    phase: str,
    reasons: list[str],
    customers: list[CustomerProfile],
    aliases: list[CustomerAlias],
) -> RoutingDecision:
    customer_id = email.customer_id if rule.customer_id == GLOBAL_CUSTOMER_ID else rule.customer_id or email.customer_id
    matched_signals = {
        "mailbox": _normalized_mailbox(email.mailbox),
        "mailboxAccountId": email.mailbox_account_id,
        "customerId": customer_id,
        "sender": _sender_address(email.sender),
        "senderDomain": _sender_domain(email.sender),
        "ruleName": rule.name,
        "tags": list(rule.tags),
        "triagePhase": phase,
    }
    outcome = rule.outcome
    processor_profile_id = rule.processor_profile_id
    extraction = _extraction_config(rule, phase)
    customer = _customer_by_id(customer_id, customers)

    if extraction:
        extracted = extract_customer_code(email, extraction)
        matched_signals["customerCodeExtraction"] = extracted
        if extracted.get("value"):
            customer = find_customer_by_code(str(extracted["value"]), customers, aliases)
            if customer:
                customer_id = customer.id
                matched_signals["customerId"] = customer_id
                matched_signals["extractedCustomerCode"] = extracted["value"]
                reasons.append(f"extracted customer code {extracted['value']} matched customer {customer.id}")
            else:
                reasons.append(f"extracted customer code {extracted['value']} did not match a customer record")
        elif extracted.get("reason"):
            reasons.append(str(extracted["reason"]))

        if _extraction_required(extraction, phase) and not customer:
            outcome = RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION
            processor_profile_id = None

    if customer is None:
        customer = _customer_by_id(customer_id, customers)

    action_key = _action_key_for_outcome(outcome)
    action_plan = build_email_action_plan(email, customer, rule, action_key)
    if action_plan:
        matched_signals["emailActions"] = action_plan

    return RoutingDecision(
        outcome=outcome,
        rule_id=rule.id,
        customer_id=customer_id,
        processor_profile_id=processor_profile_id,
        mailbox_account_id=email.mailbox_account_id,
        confidence=1.0 if outcome != RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION else 0.35,
        reasons=reasons,
        matched_signals=matched_signals,
    )


def find_customer_by_code(
    customer_code: str,
    customers: list[CustomerProfile],
    aliases: list[CustomerAlias] | None = None,
) -> CustomerProfile | None:
    normalized = normalize_identifier(customer_code)
    if not normalized:
        return None
    alias_customer_ids = {
        alias.customer_id
        for alias in aliases or []
        if _alias_is_customer_code(alias.alias_type)
        and normalize_identifier(alias.normalized_value or alias.value) == normalized
    }
    for customer in customers:
        values = [
            customer.customer_code,
            str(customer.raw_source.get("customerCode", "")),
            str(customer.raw_source.get("customer_code", "")),
            str(customer.raw_source.get("cust_code", "")),
        ]
        row = customer.raw_source.get("row")
        if isinstance(row, dict):
            values.extend([str(row.get("customerCode", "")), str(row.get("customer_code", "")), str(row.get("cust_code", ""))])
        if customer.id in alias_customer_ids:
            return customer
        if any(normalize_identifier(value) == normalized for value in values if value):
            return customer
    return None


def _extraction_config(rule: RoutingRule, phase: str) -> dict[str, Any]:
    config = dict(rule.customer_code_extraction or {})
    subject_update = dict(rule.subject_update or {})
    if not config and subject_update.get("customerCodeRegex"):
        config = {
            "source": subject_update.get("customerCodeSource", "subject"),
            "regex": subject_update.get("customerCodeRegex"),
            "group": subject_update.get("customerCodeGroup", "customerCode"),
            "required": subject_update.get("customerCodeRequired", phase in {"webstoreOrder", "previouslyProcessed"}),
        }
    return config


def _extraction_required(extraction: dict[str, Any], phase: str) -> bool:
    if "required" in extraction:
        return bool(extraction["required"])
    return phase in {"webstoreOrder", "previouslyProcessed"}


def _source_text(email: EmailMessage, source: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", source.lower())
    if normalized == "subject":
        return email.subject or ""
    if normalized in {"body", "bodytext"}:
        return email.body_text or ""
    if normalized == "bodyhtml":
        return email.body_html or ""
    if normalized == "sender":
        return email.sender or ""
    if normalized in {"attachment", "attachmentname", "attachmentnames", "filename", "filenames"}:
        return "\n".join(attachment.name for attachment in email.attachments if attachment.name)
    return "\n".join(
        [
            email.subject or "",
            email.sender or "",
            email.body_text or "",
            email.body_html or "",
            "\n".join(attachment.name for attachment in email.attachments if attachment.name),
        ]
    )


def _match_group(match: re.Match[str], group: Any) -> str:
    if isinstance(group, str) and group:
        if group in match.groupdict():
            return str(match.group(group) or "")
        if group.isdigit():
            index = int(group)
            return str(match.group(index) or "") if index <= len(match.groups()) else ""
    if isinstance(group, int) and group <= len(match.groups()):
        return str(match.group(group) or "")
    if match.groups():
        return str(match.group(1) or "")
    return str(match.group(0) or "")


def _fallback_decision(email: EmailMessage) -> RoutingDecision:
    matched_signals = {
        "mailbox": _normalized_mailbox(email.mailbox),
        "mailboxAccountId": email.mailbox_account_id,
        "customerId": email.customer_id,
        "sender": _sender_address(email.sender),
        "senderDomain": _sender_domain(email.sender),
    }
    if email.customer_id:
        return RoutingDecision(
            outcome=RoutingOutcome.NEEDS_HUMAN_REVIEW,
            customer_id=email.customer_id,
            mailbox_account_id=email.mailbox_account_id,
            confidence=0.25,
            reasons=["no email triage rule matched for known customer context"],
            matched_signals=matched_signals,
        )
    return RoutingDecision(
        outcome=RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION,
        mailbox_account_id=email.mailbox_account_id,
        confidence=0.0,
        reasons=["no email triage rule matched"],
        matched_signals=matched_signals,
    )


def _render_context(
    email: EmailMessage,
    customer: CustomerProfile | None,
    rule: RoutingRule,
    action_key: str,
    csr_field: str,
) -> dict[str, Any]:
    csr_name = _customer_field(customer, csr_field) or (customer.csr_name if customer else "")
    return {
        "actionKey": action_key,
        "status": action_key,
        "ruleName": rule.name,
        "originalSubject": email.subject or "",
        "mailbox": email.mailbox or "",
        "sender": email.sender or "",
        "customerId": customer.id if customer else "",
        "customerCode": customer.customer_code if customer else "",
        "customerName": customer.name if customer else "",
        "storeNumber": customer.store_number if customer else "",
        "routeNumber": customer.route_number if customer else "",
        "csrEmail": customer.csr_email if customer else "",
        "csrFolder": customer.csr_folder if customer else "",
        "csrName": csr_name,
    }


def _resolve_move(policy: dict[str, Any], action_key: str, customer: CustomerProfile | None) -> dict[str, Any]:
    moves = _as_dict(policy.get("moves"))
    move_policy = _as_dict(moves.get(action_key) or moves.get("*") or policy.get("move"))
    mode = str(move_policy.get("mode") or move_policy.get("type") or "none")
    normalized_mode = re.sub(r"[^a-z0-9]", "", mode.lower())
    if normalized_mode in {"", "none", "off", "disabled"}:
        return {"mode": "none", "enabled": False}
    if normalized_mode in {"static", "staticfolder", "folder"}:
        folder = str(move_policy.get("folder") or move_policy.get("folderName") or "").strip()
        return {"mode": "staticFolder", "enabled": bool(folder), "folderName": folder}
    if normalized_mode in {"customerfield", "field", "customerrecordfield"}:
        field_name = str(move_policy.get("field") or move_policy.get("customerField") or "csrFolder")
        folder = _customer_field(customer, field_name)
        return {"mode": "customerField", "enabled": bool(folder), "customerField": field_name, "folderName": folder}
    return {"mode": mode, "enabled": False}


def _customer_field(customer: CustomerProfile | None, field_name: str) -> str:
    if customer is None:
        return ""
    candidates = [field_name, _camel_to_snake(field_name)]
    for candidate in candidates:
        if hasattr(customer, candidate):
            value = getattr(customer, candidate)
            if value not in {None, ""}:
                return str(value)
        if candidate in customer.custom_fields and customer.custom_fields[candidate] not in {None, ""}:
            return str(customer.custom_fields[candidate])
        if candidate in customer.raw_source and customer.raw_source[candidate] not in {None, ""}:
            return str(customer.raw_source[candidate])
    row = customer.raw_source.get("row")
    if isinstance(row, dict):
        for candidate in candidates:
            if candidate in row and row[candidate] not in {None, ""}:
                return str(row[candidate])
    return ""


def _render_template(template: str, context: dict[str, Any]) -> str:
    class Missing(dict[str, Any]):
        def __missing__(self, key: str) -> str:
            return ""

    return str(template).format_map(Missing(context)).strip()


def _action_key_for_outcome(outcome: RoutingOutcome) -> str:
    if outcome == RoutingOutcome.KNOWN_ORDER:
        return "processedOrder"
    if outcome == RoutingOutcome.KNOWN_CUSTOMER_NON_ORDER:
        return "nonOrder"
    if outcome in {RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION, RoutingOutcome.NEEDS_HUMAN_REVIEW}:
        return "failedOrder"
    if outcome == RoutingOutcome.IGNORED:
        return "ignored"
    return "general"


def _alias_is_customer_code(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    return normalized in {"customercode", "code", "accountnumber", "account"}


def _sender_address(sender: str) -> str:
    _, address = parseaddr(sender or "")
    return (address or sender or "").lower().strip()


def _sender_domain(sender: str) -> str:
    address = _sender_address(sender)
    if "@" not in address:
        return ""
    return normalize_domain(address.rsplit("@", 1)[1])


def _normalized_mailbox(value: str) -> str:
    return (value or "").strip().lower()


def _customer_by_id(customer_id: str | None, customers: list[CustomerProfile]) -> CustomerProfile | None:
    if not customer_id:
        return None
    return next((customer for customer in customers if customer.id == customer_id), None)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _bool_policy(value: Any, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}
    return bool(value)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _camel_to_snake(value: str) -> str:
    result = ""
    for character in str(value or ""):
        if character.isupper():
            result += f"_{character.lower()}"
        else:
            result += character
    return result.lstrip("_")
