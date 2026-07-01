from __future__ import annotations

import re
from email.utils import parseaddr
from typing import Any

from .data_model import GLOBAL_CUSTOMER_ID
from .models import EmailMessage, RoutingDecision, RoutingOutcome, RoutingRule


ORDER_ATTACHMENT_EXTENSIONS = {"csv", "xlsx", "xls", "xlt", "pdf", "txt"}
FILTER_FIELDS = {"sender", "recipient", "subject", "body"}
FILTER_OPERATORS = {"equals", "contains", "startsWith", "endsWith"}


def _sender_address(sender: str) -> str:
    _, address = parseaddr(sender or "")
    return (address or sender or "").lower().strip()


def _sender_domain(sender: str) -> str:
    address = _sender_address(sender)
    if "@" not in address:
        return ""
    return address.split("@", 1)[1]


def _safe_regex_match(pattern: str, value: str) -> bool:
    try:
        return re.search(pattern, value or "", re.IGNORECASE | re.MULTILINE) is not None
    except re.error:
        return False


def _has_regex_match(patterns: list[str], value: str) -> bool:
    return any(_safe_regex_match(pattern, value) for pattern in patterns)


def _attachment_extension(name: str) -> str:
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1].lower()


def _normalized_mailbox(value: str) -> str:
    return (value or "").strip().lower()


def _attachment_content_type(value: str) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _attachment_signals(email: EmailMessage) -> dict[str, Any]:
    return {
        "names": [attachment.name for attachment in email.attachments],
        "extensions": sorted({_attachment_extension(attachment.name) for attachment in email.attachments if attachment.name}),
        "contentTypes": sorted(
            {
                _attachment_content_type(attachment.content_type)
                for attachment in email.attachments
                if attachment.content_type
            }
        ),
        "count": len(email.attachments),
    }


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item or "") for item in value]
    return [str(value or "")]


def _recipient_value(email: EmailMessage) -> str:
    source = email.source or {}
    values: list[str] = []
    for key in ("toRecipients", "to_recipients", "recipients", "recipient", "to"):
        raw_value = source.get(key)
        if isinstance(raw_value, list):
            for item in raw_value:
                if isinstance(item, dict):
                    values.extend(
                        _as_text_list(
                            item.get("emailAddress")
                            or item.get("address")
                            or item.get("email")
                            or item.get("name")
                        )
                    )
                else:
                    values.extend(_as_text_list(item))
        elif isinstance(raw_value, dict):
            values.extend(
                _as_text_list(
                    raw_value.get("emailAddress")
                    or raw_value.get("address")
                    or raw_value.get("email")
                    or raw_value.get("name")
                )
            )
        else:
            values.extend(_as_text_list(raw_value))
    if email.mailbox:
        values.append(email.mailbox)
    return "\n".join(value for value in values if value)


def _filter_condition_value(email: EmailMessage, field: str, body_value: str) -> str:
    if field == "sender":
        return _sender_address(email.sender)
    if field == "recipient":
        return _recipient_value(email)
    if field == "subject":
        return email.subject or ""
    if field == "body":
        return body_value
    return ""


def _condition_matches(actual: str, operator: str, expected: str) -> bool:
    actual_text = str(actual or "").strip().lower()
    expected_text = str(expected or "").strip().lower()
    if not expected_text:
        return True
    if operator == "equals":
        return actual_text == expected_text or any(
            item.strip().lower() == expected_text
            for item in str(actual or "").splitlines()
        )
    if operator == "startsWith":
        return actual_text.startswith(expected_text)
    if operator == "endsWith":
        return actual_text.endswith(expected_text)
    return expected_text in actual_text


def _filter_condition_label(condition: dict[str, Any]) -> str:
    return " ".join(
        str(condition.get(key, "") or "").strip()
        for key in ("field", "operator", "value")
        if str(condition.get(key, "") or "").strip()
    )


def _filter_conditions_match(
    email: EmailMessage,
    rule: RoutingRule,
    body_value: str,
) -> tuple[bool, list[str]]:
    conditions = [
        condition
        for condition in rule.filter_conditions
        if isinstance(condition, dict)
        and str(condition.get("field", "") or "").strip() in FILTER_FIELDS
        and str(condition.get("operator", "") or "").strip() in FILTER_OPERATORS
        and str(condition.get("value", "") or "").strip()
    ]
    if not conditions:
        return True, []

    results = [
        _condition_matches(
            _filter_condition_value(email, str(condition.get("field")), body_value),
            str(condition.get("operator")),
            str(condition.get("value")),
        )
        for condition in conditions
    ]
    if rule.filter_logic == "any":
        if any(results):
            return True, ["one filter condition matched"]
        labels = ", ".join(_filter_condition_label(condition) for condition in conditions)
        return False, [f"no filter conditions matched: {labels}"]

    if all(results):
        return True, ["all filter conditions matched"]
    failed = [
        _filter_condition_label(condition)
        for condition, result in zip(conditions, results)
        if not result
    ]
    return False, [f"filter condition did not match: {', '.join(failed)}"]


def _rule_scope_matches(email: EmailMessage, rule: RoutingRule) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    if rule.customer_id and rule.customer_id != GLOBAL_CUSTOMER_ID and email.customer_id and rule.customer_id != email.customer_id:
        return False, [f"rule customer {rule.customer_id} did not match email customer {email.customer_id}"]

    if rule.mailbox_account_ids:
        allowed = {item.strip() for item in rule.mailbox_account_ids if item.strip()}
        if not email.mailbox_account_id or email.mailbox_account_id not in allowed:
            return False, ["mailbox account did not match configured mailbox account ids"]
        reasons.append("mailbox account matched")

    if rule.mailbox_addresses:
        allowed_addresses = {_normalized_mailbox(item) for item in rule.mailbox_addresses if item.strip()}
        if _normalized_mailbox(email.mailbox) not in allowed_addresses:
            return False, ["mailbox address did not match configured mailbox addresses"]
        reasons.append("mailbox address matched")

    return True, reasons


def rule_matches(email: EmailMessage, rule: RoutingRule) -> tuple[bool, list[str]]:
    if not rule.enabled:
        return False, ["rule disabled"]

    reasons: list[str] = []
    scope_matches, scope_reasons = _rule_scope_matches(email, rule)
    if not scope_matches:
        return False, scope_reasons
    reasons.extend(scope_reasons)

    sender = _sender_address(email.sender)
    domain = _sender_domain(email.sender)

    body_value = "\n".join([email.body_text or "", email.body_html or ""])
    filter_matches, filter_reasons = _filter_conditions_match(email, rule, body_value)
    if not filter_matches:
        return False, filter_reasons
    reasons.extend(filter_reasons)

    if rule.sender_equals:
        allowed = {item.lower().strip() for item in rule.sender_equals}
        if sender not in allowed:
            return False, [f"sender {sender} did not match configured senders"]
        reasons.append("sender matched")

    if rule.sender_domains:
        allowed_domains = {item.lower().strip().lstrip("@") for item in rule.sender_domains}
        if domain not in allowed_domains:
            return False, [f"sender domain {domain} did not match configured domains"]
        reasons.append("sender domain matched")

    if rule.subject_regex:
        if not _has_regex_match(rule.subject_regex, email.subject):
            return False, ["subject did not match configured patterns"]
        reasons.append("subject pattern matched")

    if rule.body_regex:
        if not _has_regex_match(rule.body_regex, body_value):
            return False, ["body did not match configured patterns"]
        reasons.append("body pattern matched")

    combined_message = "\n".join([email.subject or "", body_value])
    if rule.known_webstore_patterns:
        if not _has_regex_match(rule.known_webstore_patterns, combined_message):
            return False, ["known webstore pattern did not match"]
        reasons.append("known webstore pattern matched")

    if rule.prior_processed_subject_regex:
        if not _has_regex_match(rule.prior_processed_subject_regex, email.subject):
            return False, ["prior processed subject pattern did not match"]
        reasons.append("prior processed subject pattern matched")

    if rule.required_attachment and not email.attachments:
        return False, ["required attachment missing"]

    if rule.attachment_extensions:
        allowed_extensions = {item.lower().lstrip(".") for item in rule.attachment_extensions}
        actual_extensions = {_attachment_extension(attachment.name) for attachment in email.attachments}
        if not actual_extensions.intersection(allowed_extensions):
            return False, ["attachment extension did not match configured extensions"]
        reasons.append("attachment extension matched")

    if rule.attachment_content_types:
        allowed_content_types = {_attachment_content_type(item) for item in rule.attachment_content_types}
        actual_content_types = {
            _attachment_content_type(attachment.content_type)
            for attachment in email.attachments
            if attachment.content_type
        }
        if not actual_content_types.intersection(allowed_content_types):
            return False, ["attachment content type did not match configured content types"]
        reasons.append("attachment content type matched")

    if rule.attachment_name_regex:
        attachment_names = "\n".join(attachment.name for attachment in email.attachments)
        if not _has_regex_match(rule.attachment_name_regex, attachment_names):
            return False, ["attachment name did not match configured patterns"]
        reasons.append("attachment name pattern matched")

    if not reasons:
        reasons.append("catch-all rule matched")

    return True, reasons


def evaluate_routing(email: EmailMessage, rules: list[RoutingRule]) -> RoutingDecision:
    matched_signal_base = {
        "mailbox": _normalized_mailbox(email.mailbox),
        "mailboxAccountId": email.mailbox_account_id,
        "customerId": email.customer_id,
        "sender": _sender_address(email.sender),
        "senderDomain": _sender_domain(email.sender),
        "attachments": _attachment_signals(email),
    }

    for rule in sorted(rules, key=lambda item: item.priority):
        matches, reasons = rule_matches(email, rule)
        if matches:
            return RoutingDecision(
                outcome=rule.outcome,
                rule_id=rule.id,
                customer_id=email.customer_id if rule.customer_id == GLOBAL_CUSTOMER_ID else rule.customer_id or email.customer_id,
                processor_profile_id=rule.processor_profile_id,
                mailbox_account_id=email.mailbox_account_id,
                confidence=1.0,
                reasons=reasons,
                matched_signals={**matched_signal_base, "ruleName": rule.name, "tags": list(rule.tags)},
            )

    if email.customer_id:
        return RoutingDecision(
            outcome=RoutingOutcome.NEEDS_HUMAN_REVIEW,
            customer_id=email.customer_id,
            mailbox_account_id=email.mailbox_account_id,
            confidence=0.25,
            reasons=["no routing rule matched for known customer context"],
            matched_signals=matched_signal_base,
        )

    return RoutingDecision(
        outcome=RoutingOutcome.NEEDS_CUSTOMER_IDENTIFICATION,
        mailbox_account_id=email.mailbox_account_id,
        confidence=0.0,
        reasons=["no routing rule matched"],
        matched_signals=matched_signal_base,
    )


def default_order_signal(email: EmailMessage) -> bool:
    """Best-effort diagnostic only; routing outcomes still come from data rules."""

    subject_and_body = "\n".join([email.subject or "", email.body_text or "", email.body_html or ""])
    if _has_regex_match([r"\b(po|purchase order|order)\b", r"\border\s*#"], subject_and_body):
        return True
    return any(_attachment_extension(attachment.name) in ORDER_ATTACHMENT_EXTENSIONS for attachment in email.attachments)
