from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .api import OrderProcessorApi
from .output_generation import InMemoryOutputArtifactStore
from .storage import InMemoryRepository


@dataclass(slots=True)
class PilotShadowCase:
    manifest_path: Path
    manifest: dict[str, Any]

    @property
    def base_path(self) -> Path:
        return self.manifest_path.parent


def load_pilot_shadow_case(manifest_path: str | Path) -> PilotShadowCase:
    path = Path(manifest_path).resolve()
    return PilotShadowCase(path, json.loads(path.read_text(encoding="utf-8")))


def run_pilot_shadow_case(
    case_or_path: PilotShadowCase | str | Path,
    api: OrderProcessorApi | None = None,
) -> dict[str, Any]:
    case = case_or_path if isinstance(case_or_path, PilotShadowCase) else load_pilot_shadow_case(case_or_path)
    repository = InMemoryRepository()
    artifact_store = InMemoryOutputArtifactStore()
    api = api or OrderProcessorApi(repository, output_artifact_store=artifact_store)
    seed_summary = _seed_case(api, case)

    manifest = case.manifest
    expected = dict(manifest.get("expected") or {})
    tenant_id = str(manifest.get("tenantId") or "default")
    source_file = case.base_path / str(manifest.get("sourceFile") or "")
    source_content = source_file.read_text(encoding="utf-8")
    headers = dict(manifest.get("headers") or {})

    email_payload = _email_payload(manifest, tenant_id, source_file, source_content, headers)
    ingest_result = api.ingest_email(email_payload)
    order_run_id = (ingest_result.get("orderRun") or {}).get("id")

    processed_result: dict[str, Any] = {}
    if order_run_id:
        process_payload = _process_payload(manifest, ingest_result, tenant_id, source_file, source_content, headers)
        processed_result = api.process_order(order_run_id, process_payload)

    order_run = processed_result.get("orderRun") or {}
    routing_decision = ingest_result.get("routingDecision") or {}
    email_message = ingest_result.get("emailMessage") or {}
    customer = api.repository.get("customers", str(expected.get("customerId") or ""))
    exceptions = api.repository.query_by_tenant("exceptionTasks", tenant_id)
    audit_events = api.repository.query_by_tenant("auditEvents", tenant_id)
    checks = _shadow_checks(
        api=api,
        case=case,
        expected=expected,
        routing_decision=routing_decision,
        email_message=email_message,
        order_run=order_run,
        customer=customer or {},
        exceptions=exceptions,
    )

    return {
        "pilotId": manifest.get("pilotId"),
        "phase": manifest.get("phase"),
        "mode": manifest.get("mode", "shadow"),
        "passed": all(check["passed"] for check in checks),
        "selectedCustomer": manifest.get("selectedCustomer", {}),
        "seedSummary": seed_summary,
        "ingest": {
            "emailMessageId": email_message.get("id"),
            "orderRunId": order_run_id,
            "routingOutcome": routing_decision.get("outcome"),
            "routingRuleId": routing_decision.get("ruleId"),
            "customerId": email_message.get("customerId") or routing_decision.get("customerId"),
        },
        "orderRun": _order_summary(order_run),
        "checks": checks,
        "outputArtifacts": _artifact_summary(order_run),
        "exceptionTasks": [
            {
                "id": item.get("id"),
                "type": item.get("type"),
                "status": item.get("status"),
                "lineNumber": item.get("lineNumber"),
            }
            for item in exceptions
        ],
        "auditEventTypes": [event.get("eventType") for event in audit_events],
    }


def _seed_case(api: OrderProcessorApi, case: PilotShadowCase) -> dict[str, int]:
    counts: dict[str, int] = {}
    for seed in case.manifest.get("seedFiles", []):
        container = str(seed["container"])
        docs = _read_json(case.base_path / str(seed["path"]))
        if isinstance(docs, dict):
            docs = [docs]
        counts[container] = counts.get(container, 0) + len(docs)
        for doc in docs:
            _seed_document(api, container, dict(doc))
    return counts


def _seed_document(api: OrderProcessorApi, container: str, document: dict[str, Any]) -> None:
    if container == "customers":
        api.upsert_customer_config(document)
    elif container == "mailboxAccounts":
        api.upsert_mailbox(document)
    elif container == "routingRules":
        api.upsert_routing_rule(document)
    elif container == "processorProfiles":
        api.upsert_processor_profile(document)
    elif container == "outputProfiles":
        api.upsert_output_profile(document)
    elif container == "consoleUsers":
        api.upsert_console_user(document)
    elif container == "microsoftAuthConnections":
        api.upsert_microsoft_auth_connection(document)
    else:
        api.repository.upsert(container, document)


def _email_payload(
    manifest: dict[str, Any],
    tenant_id: str,
    source_file: Path,
    source_content: str,
    headers: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(manifest.get("email") or {})
    payload["tenantId"] = tenant_id
    payload["headers"] = {**headers, **dict(payload.get("headers") or {})}
    attachments = [dict(item) for item in payload.get("attachments", [])]
    if not attachments:
        attachments = [{"name": source_file.name, "contentType": "text/csv"}]
    for attachment in attachments:
        attachment.setdefault("name", source_file.name)
        attachment.setdefault("contentType", "text/csv")
        attachment.setdefault("size", len(source_content.encode("utf-8")))
        attachment.setdefault("blobUrl", f"shadow://{source_file.as_posix()}")
    payload["attachments"] = attachments
    return payload


def _process_payload(
    manifest: dict[str, Any],
    ingest_result: dict[str, Any],
    tenant_id: str,
    source_file: Path,
    source_content: str,
    headers: dict[str, Any],
) -> dict[str, Any]:
    routing_decision = ingest_result.get("routingDecision") or {}
    payload = dict(manifest.get("processPayload") or {})
    payload["tenantId"] = tenant_id
    payload.setdefault("customerId", routing_decision.get("customerId"))
    payload.setdefault("sourceFileName", source_file.name)
    payload["sourceContent"] = source_content
    payload["headers"] = {**headers, **dict(payload.get("headers") or {})}
    return payload


def _shadow_checks(
    api: OrderProcessorApi,
    case: PilotShadowCase,
    expected: dict[str, Any],
    routing_decision: dict[str, Any],
    email_message: dict[str, Any],
    order_run: dict[str, Any],
    customer: dict[str, Any],
    exceptions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    lines = list(order_run.get("lines") or [])
    line_csv = _artifact_text(api, _first_artifact(order_run, "lineCsv"))
    expected_line_csv = str(expected.get("lineOutputText") or "")
    if not expected_line_csv and expected.get("lineOutputFile"):
        expected_line_csv = (case.base_path / str(expected["lineOutputFile"])).read_text(encoding="utf-8")
    output_artifact_types = [artifact.get("type") for artifact in order_run.get("outputArtifacts", [])]
    line_artifact = _first_artifact(order_run, "lineCsv")
    processor_profile = api.repository.get("processorProfiles", str(order_run.get("processorProfileId") or ""))

    return [
        _check("routingOutcome", expected.get("routingOutcome"), routing_decision.get("outcome")),
        _check(
            "customerIdentification",
            expected.get("customerId"),
            email_message.get("customerId") or routing_decision.get("customerId") or order_run.get("customerId"),
            "Customer was identified through the configured mailbox and routing rule.",
        ),
        _check("orderStatus", expected.get("orderStatus"), order_run.get("status")),
        _check("routingTags", sorted(expected.get("routingTags") or []), sorted((routing_decision.get("matchedSignals") or {}).get("tags") or [])),
        _check("csrFolder", expected.get("csrFolder"), customer.get("csrFolder")),
        _check(
            "itemValidation",
            expected.get("matchedInternalItemNumbers") or [],
            [line.get("matchedInternalItemNumber") for line in lines],
        ),
        _check("unresolvedLineCount", expected.get("unresolvedLineCount"), _unresolved_line_count(lines)),
        _check("exceptionCount", expected.get("exceptionCount"), len(exceptions)),
        _check("outputArtifactTypes", sorted(expected.get("outputArtifactTypes") or []), sorted(output_artifact_types)),
        _check(
            "outputFiles",
            _normalize_text(expected_line_csv),
            _normalize_text(line_csv),
            "Line CSV output matches the checked-in legacy-shadow expectation.",
        ),
        _check(
            "shadowIsolation",
            {
                "deliveryMode": expected.get("shadowDeliveryMode"),
                "productionDeliveryEnabled": expected.get("productionDeliveryEnabled"),
            },
            {
                "deliveryMode": (line_artifact.get("destination") or {}).get("deliveryMode"),
                "productionDeliveryEnabled": (line_artifact.get("destination") or {}).get("productionDeliveryEnabled"),
            },
            "Output remains in shadow-only delivery mode.",
        ),
        _check(
            "plumsailReplacement",
            expected.get("plumsailUsed"),
            bool((processor_profile or {}).get("settings", {}).get("usesPlumsail", False)),
            "The active CSV Parse pilot uses backend CSV code rather than the Plumsail connector.",
        ),
        _check("errorHandling", [], order_run.get("errors") or []),
    ]


def _check(name: str, expected: Any, actual: Any, note: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "passed": expected == actual,
        "expected": expected,
        "actual": actual,
        "note": note,
    }


def _unresolved_line_count(lines: list[dict[str, Any]]) -> int:
    return sum(1 for line in lines if line.get("validationStatus") in {"unresolved", "possibleMatch"})


def _first_artifact(order_run: dict[str, Any], artifact_type: str) -> dict[str, Any]:
    for artifact in order_run.get("outputArtifacts", []) or []:
        if artifact.get("type") == artifact_type:
            return artifact
    return {}


def _artifact_text(api: OrderProcessorApi, artifact: dict[str, Any]) -> str:
    blob_url = artifact.get("blobUrl") or ""
    objects = getattr(api.output_artifact_store, "objects", {})
    content = objects.get(blob_url, b"") if isinstance(objects, dict) else b""
    if isinstance(content, bytes):
        return content.decode("utf-8")
    return str(content or "")


def _artifact_summary(order_run: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": artifact.get("id"),
            "type": artifact.get("type"),
            "fileName": artifact.get("fileName"),
            "contentType": artifact.get("contentType"),
            "sizeBytes": artifact.get("sizeBytes"),
            "destination": artifact.get("destination", {}),
        }
        for artifact in order_run.get("outputArtifacts", []) or []
    ]


def _order_summary(order_run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": order_run.get("id"),
        "status": order_run.get("status"),
        "customerId": order_run.get("customerId"),
        "processorProfileId": order_run.get("processorProfileId"),
        "processorType": order_run.get("processorType"),
        "sourceType": order_run.get("sourceType"),
        "sourceFileName": order_run.get("sourceFileName"),
        "poNumber": order_run.get("poNumber"),
        "lineCount": len(order_run.get("lines") or []),
        "outputArtifactCount": len(order_run.get("outputArtifacts") or []),
        "errorCount": len(order_run.get("errors") or []),
    }


def _normalize_text(value: str) -> str:
    if not value:
        return ""
    return "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n")) + "\n"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a local pilot shadow comparison.")
    parser.add_argument("manifest", type=Path, help="Path to samples/pilot/pilot-shadow-manifest.json")
    args = parser.parse_args(argv)
    result = run_pilot_shadow_case(args.manifest)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
