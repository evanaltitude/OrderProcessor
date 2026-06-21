from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4


def _pick(document: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in document and document[name] is not None:
            return document[name]
    return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    return {
        str(key).lower(): str(value)
        for key, value in dict(_pick(payload, "headers", default={}) or {}).items()
        if value is not None
    }


def _source(payload: dict[str, Any]) -> dict[str, Any]:
    value = _pick(payload, "source", "sourceMetadata", "source_metadata", default={}) or {}
    if isinstance(value, dict):
        return dict(value)
    return {"source": value}


def _trace_parts(traceparent: str) -> tuple[str, str]:
    parts = str(traceparent or "").split("-")
    if len(parts) >= 4 and len(parts[1]) == 32 and len(parts[2]) == 16:
        return parts[1], parts[2]
    return "", ""


def correlation_context(payload: dict[str, Any], fallback_correlation_id: str = "") -> dict[str, Any]:
    headers = _headers(payload)
    source = _source(payload)
    traceparent = _pick(
        payload,
        "traceparent",
        "traceParent",
        default=_pick(source, "traceparent", "traceParent", default=headers.get("traceparent", "")),
    )
    trace_id, parent_span_id = _trace_parts(str(traceparent or ""))
    correlation_id = str(
        _pick(
            payload,
            "correlationId",
            "correlation_id",
            default=_pick(
                source,
                "correlationId",
                "correlation_id",
                default=headers.get("x-correlation-id")
                or headers.get("x-ms-correlation-id")
                or headers.get("x-ms-client-request-id")
                or headers.get("x-ms-request-id")
                or trace_id
                or fallback_correlation_id
                or str(uuid4()),
            ),
        )
        or ""
    )
    operation_id = str(
        _pick(
            payload,
            "operationId",
            "operation_id",
            default=_pick(
                source,
                "operationId",
                "operation_id",
                default=headers.get("x-ms-request-id") or trace_id or correlation_id,
            ),
        )
        or correlation_id
    )

    context = {
        "correlationId": correlation_id,
        "operationId": operation_id,
        "traceParent": str(traceparent or ""),
        "traceId": trace_id,
        "parentSpanId": parent_span_id,
        "clientRequestId": headers.get("x-ms-client-request-id", ""),
        "apimRequestId": headers.get("x-ms-request-id", ""),
        "powerAutomateFlowRunId": _pick(
            payload,
            "flowRunId",
            "powerAutomateFlowRunId",
            default=_pick(source, "flowRunId", "powerAutomateFlowRunId", default=headers.get("x-ms-workflow-run-id", "")),
        ),
        "powerAutomateFlowName": _pick(
            payload,
            "flowName",
            "powerAutomateFlowName",
            default=_pick(source, "flowName", "powerAutomateFlowName", default=headers.get("x-ms-workflow-name", "")),
        ),
        "durableInstanceId": _pick(
            payload,
            "durableInstanceId",
            "durable_instance_id",
            default=_pick(source, "durableInstanceId", "durable_instance_id", default=""),
        ),
        "ingress": headers.get("x-order-processor-ingress", ""),
        "sourceSystem": _pick(payload, "sourceSystem", default=_pick(source, "provider", "sourceSystem", default="")),
    }
    return {key: value for key, value in context.items() if value not in ("", None)}


def merge_observability(existing: dict[str, Any] | None, new_context: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing or {})
    for key, value in new_context.items():
        if value not in ("", None):
            merged[key] = value
    return merged


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def duration_ms(start: Any, end: Any) -> int | None:
    start_dt = parse_timestamp(start)
    end_dt = parse_timestamp(end)
    if start_dt is None or end_dt is None:
        return None
    return max(0, int((end_dt - start_dt).total_seconds() * 1000))


def processing_latency_ms(order: dict[str, Any]) -> int | None:
    return duration_ms(
        _pick(order, "processingStartedAt", "processing_started_at", "createdAt", "created_at", default=None),
        _pick(order, "processingCompletedAt", "processing_completed_at", "updatedAt", "updated_at", default=None),
    )


def _percentile(values: list[int], percent: float) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percent)))
    return ordered[index]


def latency_summary(order_runs: list[dict[str, Any]]) -> dict[str, Any]:
    finished_statuses = {"completed", "failed", "ignored"}
    latencies = []
    for order in order_runs:
        value = processing_latency_ms(order)
        if value is not None and str(_pick(order, "status", default="")) in finished_statuses:
            latencies.append(value)
    return {
        "count": len(latencies),
        "averageMs": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "p50Ms": _percentile(latencies, 0.5),
        "p95Ms": _percentile(latencies, 0.95),
        "maxMs": max(latencies) if latencies else None,
    }


def dashboard_observability_metrics(
    order_runs: list[dict[str, Any]],
    exceptions: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
) -> dict[str, Any]:
    customer_failure_events = [
        event
        for event in audit_events
        if _pick(event, "eventType", "event_type", default="") == "customer.identified"
        and _pick(dict(_pick(event, "details", default={}) or {}).get("result", {}) or {}, "status", default="") != "matched"
    ]
    customer_failure_tasks = [
        task
        for task in exceptions
        if _pick(task, "type", default="") in {"customerIdentification", "routing"}
    ]
    parser_failure_tasks = [task for task in exceptions if _pick(task, "type", default="") == "parserFailure"]
    output_failure_tasks = [task for task in exceptions if _pick(task, "type", default="") == "outputGeneration"]
    failed_orders = [order for order in order_runs if _pick(order, "status", default="") == "failed"]
    processor_failure_ids = {_pick(order, "id", default="") for order in failed_orders}
    for task in parser_failure_tasks:
        processor_failure_ids.add(_pick(task, "orderRunId", "order_run_id", default="") or _pick(task, "id", default=""))

    return {
        "customerIdentificationFailureCount": len({*[_pick(task, "id", default="") for task in customer_failure_tasks], *[_pick(event, "id", default="") for event in customer_failure_events]}),
        "processorFailureCount": len({item for item in processor_failure_ids if item}),
        "outputGenerationFailureCount": len(output_failure_tasks),
        "processingLatency": latency_summary(order_runs),
        "auditEventCount": len(audit_events),
    }


def _event_matches_order(event: dict[str, Any], order: dict[str, Any], email: dict[str, Any] | None) -> bool:
    order_id = _pick(order, "id", default="")
    email_id = _pick(order, "emailMessageId", "email_message_id", default="") or _pick(email or {}, "id", default="")
    order_correlation = _pick(order, "correlationId", "correlation_id", default="")
    email_correlation = _pick(email or {}, "correlationId", "correlation_id", default="")
    details = dict(_pick(event, "details", default={}) or {})
    return bool(
        _pick(event, "subjectId", "subject_id", default="") in {order_id, email_id}
        or _pick(event, "orderRunId", "order_run_id", default="") == order_id
        or _pick(event, "emailMessageId", "email_message_id", default="") == email_id
        or _pick(details, "orderRunId", "order_run_id", default="") == order_id
        or _pick(details, "emailMessageId", "email_message_id", default="") == email_id
        or (
            order_correlation
            and _pick(event, "correlationId", "correlation_id", default="") == order_correlation
        )
        or (
            email_correlation
            and _pick(event, "correlationId", "correlation_id", default="") == email_correlation
        )
    )


def _timeline_entry(
    timestamp: Any,
    event_type: str,
    title: str,
    details: dict[str, Any],
    correlation_id: str = "",
    actor: str = "system",
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "eventType": event_type,
        "title": title,
        "actor": actor,
        "correlationId": correlation_id,
        "details": details,
    }


def order_timeline(
    order: dict[str, Any],
    email: dict[str, Any] | None,
    exceptions: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
) -> dict[str, Any]:
    order_id = _pick(order, "id", default="")
    email_id = _pick(order, "emailMessageId", "email_message_id", default="")
    selected_events = [event for event in audit_events if _event_matches_order(event, order, email)]

    entries = [
        _timeline_entry(
            _pick(order, "createdAt", "created_at", default=""),
            "order.created",
            "Order run created",
            {
                "orderRunId": order_id,
                "emailMessageId": email_id,
                "customerId": _pick(order, "customerId", "customer_id", default=None),
                "status": _pick(order, "status", default=""),
            },
            _pick(order, "correlationId", "correlation_id", default=""),
        )
    ]
    if email:
        entries.append(
            _timeline_entry(
                _pick(email, "receivedAt", "received_at", "createdAt", "created_at", default=""),
                "email.received",
                "Email received",
                {
                    "emailMessageId": _pick(email, "id", default=""),
                    "mailbox": _pick(email, "mailbox", default=""),
                    "sender": _pick(email, "sender", default=""),
                    "subject": _pick(email, "subject", default=""),
                    "routing": _pick(email, "routing", default={}),
                },
                _pick(email, "correlationId", "correlation_id", default=""),
            )
        )

    for event in selected_events:
        entries.append(
            _timeline_entry(
                _pick(event, "createdAt", "created_at", default=""),
                _pick(event, "eventType", "event_type", default=""),
                _pick(event, "eventType", "event_type", default=""),
                dict(_pick(event, "details", default={}) or {}),
                _pick(event, "correlationId", "correlation_id", default=""),
                _pick(event, "actor", default="system"),
            )
        )

    for task in exceptions:
        if _pick(task, "orderRunId", "order_run_id", default="") != order_id:
            continue
        event_type = "exception.resolved" if _pick(task, "status", default="") == "resolved" else "exception.opened"
        entries.append(
            _timeline_entry(
                _pick(task, "resolvedAt", "resolved_at", "createdAt", "created_at", default=""),
                event_type,
                f"{_pick(task, 'type', default='exception')} {event_type.split('.')[-1]}",
                {
                    "exceptionTaskId": _pick(task, "id", default=""),
                    "type": _pick(task, "type", default=""),
                    "lineNumber": _pick(task, "lineNumber", "line_number", default=None),
                    "prompt": _pick(task, "prompt", default=""),
                    "resolution": _pick(task, "resolution", default={}),
                },
            )
        )

    for artifact in _as_list(_pick(order, "outputArtifacts", "output_artifacts", default=[])):
        if not isinstance(artifact, dict):
            continue
        entries.append(
            _timeline_entry(
                _pick(artifact, "generatedAt", "generated_at", default=_pick(order, "updatedAt", "updated_at", default="")),
                "output.artifactGenerated",
                "Output artifact generated",
                {
                    "artifactId": _pick(artifact, "id", default=""),
                    "type": _pick(artifact, "type", default=""),
                    "fileName": _pick(artifact, "fileName", "file_name", default=""),
                    "blobUrl": _pick(artifact, "blobUrl", "blob_url", default=""),
                },
                _pick(order, "correlationId", "correlation_id", default=""),
            )
        )

    entries = sorted(entries, key=lambda entry: str(entry.get("timestamp") or ""))
    return {
        "orderRunId": order_id,
        "correlationId": _pick(order, "correlationId", "correlation_id", default=_pick(email or {}, "correlationId", "correlation_id", default="")),
        "processingLatencyMs": processing_latency_ms(order),
        "eventCount": len(entries),
        "events": entries,
    }
