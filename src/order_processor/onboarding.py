from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .pilot_shadow import run_pilot_shadow_case


SUPPORTED_PROCESSOR_TYPES = {"csv", "xlsx", "xls", "xlt", "legacyworkbook", "pdf", "emailbody", "customeroverride"}
SUPPORTED_OUTPUT_TYPES = {"csv", "linecsv", "xlsx", "linexlsx", "text", "txt", "api", "apipayload", "json", "universalorderjson", "multi"}
MAIL_PERMISSIONS = {"mail.read", "mail.readwrite"}


def load_onboarding_package(package_path: str | Path) -> tuple[dict[str, Any], Path]:
    path = Path(package_path).resolve()
    return json.loads(path.read_text(encoding="utf-8")), path.parent


def validate_onboarding_package(
    package_path: str | Path,
    run_fixtures: bool = True,
) -> dict[str, Any]:
    package, base_path = load_onboarding_package(package_path)
    return validate_onboarding_package_data(package, base_path, run_fixtures=run_fixtures)


def validate_onboarding_package_data(
    package: dict[str, Any],
    base_path: str | Path,
    run_fixtures: bool = True,
) -> dict[str, Any]:
    base = Path(base_path).resolve()
    package_id = str(_pick(package, "onboardingPackageId", "packageId", default=""))
    tenant_id = str(_pick(package, "tenantId", default=""))
    customer_section = dict(_pick(package, "customerProfile", "customer", default={}) or {})
    selected_customer_id = str(_pick(customer_section, "id", "customerId", "customer_id", default=""))

    checks: list[dict[str, Any]] = []
    fixture_results: list[dict[str, Any]] = []

    _add_check(checks, "packageMetadata", bool(package_id and tenant_id), "Package id and tenant id are required.")

    customers = _section_documents(customer_section, base)
    customer = _find_document(customers, selected_customer_id) if selected_customer_id else (customers[0] if customers else {})
    customer_id = _customer_id(customer) or selected_customer_id
    _add_check(
        checks,
        "customerProfile",
        bool(
            customer
            and customer_id
            and _same_tenant(customer, tenant_id)
            and _text(customer, "customerCode", "customer_code")
            and _text(customer, "name")
            and _text(customer, "csrFolder", "csr_folder")
        ),
        "Customer profile must include id, tenantId, customer code, name, and CSR folder.",
        {"customerId": customer_id},
    )

    auth_connections = _section_documents(_pick(package, "microsoftAuthConnections", default=[]), base)
    auth_ids = {_text(connection, "id") for connection in auth_connections if _text(connection, "id")}
    _add_check(
        checks,
        "microsoftAuthConnections",
        bool(auth_connections)
        and all(_same_tenant(item, tenant_id) for item in auth_connections)
        and all(_has_mail_permission(item) for item in auth_connections)
        and all(not _contains_plain_secret(item) for item in auth_connections),
        "At least one Microsoft auth connection must be defined with mail scopes and without inline secret values.",
        {"connectionIds": sorted(auth_ids)},
    )

    mailboxes = _section_documents(_pick(package, "monitoredMailboxes", "mailboxes", default=[]), base)
    mailbox_ids = {_text(mailbox, "id") for mailbox in mailboxes if _text(mailbox, "id")}
    mailbox_addresses = {_normalized_email(_text(mailbox, "mailboxAddress", "mailbox_address")) for mailbox in mailboxes}
    _add_check(
        checks,
        "monitoredMailboxes",
        bool(mailboxes)
        and all(_same_tenant(item, tenant_id) for item in mailboxes)
        and all(_customer_id(item) == customer_id for item in mailboxes)
        and all(_valid_email(_text(item, "mailboxAddress", "mailbox_address")) for item in mailboxes)
        and all(_mailbox_connection_is_known(item, auth_ids) for item in mailboxes),
        "Each monitored mailbox must be customer-scoped, syntactically valid, and tied to a known Microsoft connection.",
        {"mailboxIds": sorted(mailbox_ids)},
    )

    console_users = _section_documents(_pick(package, "consoleUsers", default=[]), base)
    user_emails = {_normalized_email(_text(user, "email")) for user in console_users if _text(user, "email")}
    assignments = _section_documents(_pick(package, "customerUserAssignments", default=[]), base)
    _add_check(
        checks,
        "consoleUserAssignments",
        bool(console_users)
        and bool(assignments)
        and "connect@focuseautomate.com" in user_emails
        and all(_valid_email(_text(user, "email")) for user in console_users)
        and all(_customer_id(assignment) == customer_id for assignment in assignments)
        and all(_normalized_email(_text(assignment, "email")) in user_emails for assignment in assignments),
        "Console onboarding must include bootstrap admin and customer user assignments for known Microsoft users.",
        {"consoleUsers": sorted(user_emails)},
    )

    processor_profiles = _section_documents(_pick(package, "processorProfiles", default=[]), base)
    processor_ids = {_text(profile, "id") for profile in processor_profiles if _text(profile, "id")}
    _add_check(
        checks,
        "processorProfiles",
        bool(processor_profiles)
        and all(_same_tenant(profile, tenant_id) for profile in processor_profiles)
        and all(_customer_id(profile) == customer_id for profile in processor_profiles)
        and all(_normalize_type(_text(profile, "processorType", "processor_type")) in SUPPORTED_PROCESSOR_TYPES for profile in processor_profiles)
        and all(not bool((_as_dict(profile.get("settings")).get("usesPlumsail"))) for profile in processor_profiles),
        "Processor profiles must be customer-scoped, supported, and free of Plumsail dependencies.",
        {"processorProfileIds": sorted(processor_ids)},
    )

    routing_rules = _section_documents(_pick(package, "routingRules", default=[]), base)
    _add_check(
        checks,
        "routingRules",
        bool(routing_rules)
        and all(_same_tenant(rule, tenant_id) for rule in routing_rules)
        and all(_customer_id(rule) == customer_id for rule in routing_rules)
        and all(_text(rule, "processorProfileId", "processor_profile_id") in processor_ids for rule in routing_rules)
        and all(_routing_rule_has_matcher(rule) for rule in routing_rules)
        and all(_routing_rule_mailbox_known(rule, mailbox_ids, mailbox_addresses) for rule in routing_rules),
        "Routing rules must point to known processor profiles and customer mailboxes and include concrete matching signals.",
        {"routingRuleIds": [_text(rule, "id") for rule in routing_rules]},
    )

    output_profiles = _section_documents(_pick(package, "outputProfiles", default=[]), base)
    current_stage = _normalize_type(_text(_as_dict(package.get("migrationPlan")), "currentStage", default="shadow"))
    _add_check(
        checks,
        "outputProfiles",
        bool(output_profiles)
        and all(_same_tenant(profile, tenant_id) for profile in output_profiles)
        and all(_customer_id(profile) == customer_id for profile in output_profiles)
        and all(_normalize_type(_text(profile, "outputType", "output_type")) in SUPPORTED_OUTPUT_TYPES for profile in output_profiles)
        and all(_output_delivery_is_safe(profile, current_stage) for profile in output_profiles),
        "Output profiles must be customer-scoped, supported, and shadow-safe before cutover.",
        {"outputProfileIds": [_text(profile, "id") for profile in output_profiles]},
    )

    import_sources = _as_dict(package.get("importSources"))
    _add_check(
        checks,
        "importSources",
        _import_source_valid(import_sources.get("customerList")) and _import_source_valid(import_sources.get("itemList")),
        "Customer and item list sources must both define source type, owner, cadence, and field mapping.",
        {"sourceTypes": {key: _text(_as_dict(value), "sourceType", "source_type") for key, value in import_sources.items()}},
    )

    csr_routing = _as_dict(package.get("csrRouting"))
    _add_check(
        checks,
        "csrRouting",
        bool(csr_routing)
        and _text(csr_routing, "customerId", "customer_id", default=customer_id) == customer_id
        and _text(csr_routing, "csrFolder", "csr_folder") == _text(customer, "csrFolder", "csr_folder")
        and _text(csr_routing, "routingMode", "routing_mode"),
        "CSR routing must be explicit and match the customer CSR folder.",
        {"csrFolder": _text(csr_routing, "csrFolder", "csr_folder")},
    )

    test_fixtures = list(_as_list(package.get("testFixtures")))
    fixture_check_passed = bool(test_fixtures)
    for fixture in test_fixtures:
        fixture_result = _validate_fixture(_as_dict(fixture), base, run_fixtures=run_fixtures)
        fixture_results.append(fixture_result)
        fixture_check_passed = fixture_check_passed and fixture_result["passed"]
    _add_check(
        checks,
        "testFixtures",
        fixture_check_passed,
        "At least one test fixture must exist and pass when fixture execution is enabled.",
        {"fixtureCount": len(test_fixtures)},
    )

    migration_plan = _as_dict(package.get("migrationPlan"))
    _add_check(
        checks,
        "batchMigrationPlan",
        _batch_plan_valid(migration_plan, customer_id),
        "Migration plan must define pilot-first batches and remaining-customer strategy.",
        {"currentStage": _text(migration_plan, "currentStage", default="")},
    )

    return {
        "packageId": package_id,
        "tenantId": tenant_id,
        "customerId": customer_id,
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "fixtureResults": fixture_results,
        "summary": {
            "customerCount": len(customers),
            "mailboxCount": len(mailboxes),
            "routingRuleCount": len(routing_rules),
            "processorProfileCount": len(processor_profiles),
            "outputProfileCount": len(output_profiles),
            "fixtureCount": len(test_fixtures),
        },
    }


def _validate_fixture(fixture: dict[str, Any], base: Path, run_fixtures: bool) -> dict[str, Any]:
    fixture_type = _normalize_type(_text(fixture, "type", default=""))
    fixture_path = _resolve_path(base, _text(fixture, "path", default=""))
    exists = fixture_path.exists()
    result: dict[str, Any] = {
        "type": fixture_type,
        "path": str(fixture_path),
        "exists": exists,
        "passed": exists,
    }
    if not exists or not run_fixtures:
        return result

    if fixture_type == "pilotshadowmanifest":
        shadow_result = run_pilot_shadow_case(fixture_path)
        result["passed"] = bool(shadow_result.get("passed"))
        result["pilotId"] = shadow_result.get("pilotId")
        result["checkCount"] = len(shadow_result.get("checks") or [])
        result["failedChecks"] = [
            check.get("name")
            for check in shadow_result.get("checks") or []
            if not check.get("passed")
        ]
        return result

    result["passed"] = False
    result["error"] = f"Unsupported fixture type: {fixture_type}"
    return result


def _section_documents(section: Any, base: Path) -> list[dict[str, Any]]:
    if section is None:
        return []
    if isinstance(section, str):
        return _json_documents(_resolve_path(base, section))
    if isinstance(section, list):
        return [dict(item) for item in section if isinstance(item, dict)]
    if isinstance(section, dict):
        if section.get("path"):
            docs = _json_documents(_resolve_path(base, str(section["path"])))
            selected_id = _text(section, "id", "customerId", default="")
            return [_find_document(docs, selected_id)] if selected_id and _find_document(docs, selected_id) else docs
        if "items" in section:
            return _section_documents(section.get("items"), base)
        if "document" in section:
            document = section.get("document")
            return [dict(document)] if isinstance(document, dict) else []
        if _text(section, "id"):
            return [dict(section)]
    return []


def _json_documents(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [dict(data)]
    return []


def _find_document(documents: list[dict[str, Any]], document_id: str) -> dict[str, Any]:
    for document in documents:
        if _text(document, "id") == document_id or _customer_id(document) == document_id:
            return document
    return {}


def _add_check(
    checks: list[dict[str, Any]],
    name: str,
    passed: bool,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    checks.append({"name": name, "passed": bool(passed), "message": message, "details": details or {}})


def _pick(document: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in document and document[name] is not None:
            return document[name]
    return default


def _text(document: dict[str, Any], *names: str, default: str = "") -> str:
    return str(_pick(document, *names, default=default) or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _resolve_path(base: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _same_tenant(document: dict[str, Any], tenant_id: str) -> bool:
    return _text(document, "tenantId", "tenant_id") == tenant_id


def _customer_id(document: dict[str, Any]) -> str:
    return _text(document, "customerId", "customer_id", default=_text(document, "id"))


def _normalized_email(value: str) -> str:
    return str(value or "").strip().lower()


def _valid_email(value: str) -> bool:
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value or "") is not None


def _normalize_type(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _has_mail_permission(connection: dict[str, Any]) -> bool:
    scopes = {_normalize_scope(scope) for scope in _as_list(connection.get("scopes"))}
    return bool(scopes.intersection(MAIL_PERMISSIONS))


def _normalize_scope(value: Any) -> str:
    return str(value or "").strip().lower()


def _contains_plain_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = _normalize_type(str(key))
            if normalized_key == "keyvaultsecretnames":
                continue
            if normalized_key in {"clientsecret", "secret", "password", "accesstoken", "refreshtoken"} and item:
                return True
            if _contains_plain_secret(item):
                return True
    if isinstance(value, list):
        return any(_contains_plain_secret(item) for item in value)
    return False


def _mailbox_connection_is_known(mailbox: dict[str, Any], auth_ids: set[str]) -> bool:
    connection_id = _text(mailbox, "connectionId", "connection_id")
    return bool(connection_id and (not auth_ids or connection_id in auth_ids))


def _routing_rule_has_matcher(rule: dict[str, Any]) -> bool:
    matcher_fields = [
        "mailboxAccountIds",
        "mailboxAddresses",
        "senderEquals",
        "senderDomains",
        "subjectRegex",
        "bodyRegex",
        "knownWebstorePatterns",
        "priorProcessedSubjectRegex",
        "attachmentExtensions",
        "attachmentContentTypes",
        "attachmentNameRegex",
    ]
    for field in matcher_fields:
        if _as_list(rule.get(field)):
            return True
    return bool(rule.get("requiredAttachment") or rule.get("required_attachment"))


def _routing_rule_mailbox_known(rule: dict[str, Any], mailbox_ids: set[str], mailbox_addresses: set[str]) -> bool:
    rule_mailbox_ids = {_text({"value": value}, "value") for value in _as_list(rule.get("mailboxAccountIds") or rule.get("mailbox_account_ids"))}
    rule_addresses = {
        _normalized_email(str(value))
        for value in _as_list(rule.get("mailboxAddresses") or rule.get("mailbox_addresses"))
    }
    if rule_mailbox_ids and not rule_mailbox_ids.issubset(mailbox_ids):
        return False
    if rule_addresses and not rule_addresses.issubset(mailbox_addresses):
        return False
    return bool(rule_mailbox_ids or rule_addresses or not mailbox_ids)


def _output_delivery_is_safe(profile: dict[str, Any], current_stage: str) -> bool:
    if current_stage in {"live", "production"}:
        return True
    destination = _as_dict(profile.get("destination"))
    return destination.get("productionDeliveryEnabled") is False or _normalize_type(str(destination.get("deliveryMode", ""))) == "shadowonly"


def _import_source_valid(source: Any) -> bool:
    source_doc = _as_dict(source)
    return bool(
        _text(source_doc, "sourceType", "source_type")
        and _text(source_doc, "owner")
        and _text(source_doc, "refreshCadence", "refresh_cadence")
        and _as_dict(source_doc.get("fieldMap") or source_doc.get("field_map"))
    )


def _batch_plan_valid(plan: dict[str, Any], customer_id: str) -> bool:
    batches = [item for item in _as_list(plan.get("migrationBatches")) if isinstance(item, dict)]
    if not batches or not _text(plan, "remainingCustomersStrategy", "remaining_customers_strategy"):
        return False
    first_batch = sorted(batches, key=lambda item: int(item.get("sequence", 999)))[0]
    return customer_id in [str(value) for value in _as_list(first_batch.get("customerIds"))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an Order Processor onboarding package.")
    parser.add_argument("package", type=Path, help="Path to onboarding-package.json")
    parser.add_argument("--skip-fixtures", action="store_true", help="Validate package shape without running fixture cases.")
    args = parser.parse_args(argv)
    result = validate_onboarding_package(args.package, run_fixtures=not args.skip_fixtures)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
