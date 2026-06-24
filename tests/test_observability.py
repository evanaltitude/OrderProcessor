from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "functions"))

from function_app import _chunk_import_payloads, _import_response_mode, _payload_with_headers
from order_processor.api import OrderProcessorApi
from order_processor.models import OrderRun, ProcessingStatus, to_dict
from order_processor.storage import InMemoryRepository


TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


class FakeRequest:
    def __init__(self, body: dict, headers: dict, params: dict | None = None) -> None:
        self._body = body
        self.headers = headers
        self.params = params or {}

    def get_json(self) -> dict:
        return self._body


class ObservabilityTests(unittest.TestCase):
    def _api(self) -> tuple[OrderProcessorApi, InMemoryRepository]:
        repo = InMemoryRepository()
        return OrderProcessorApi(repo), repo

    def test_payload_with_headers_preserves_trace_and_power_automate_context(self) -> None:
        payload = _payload_with_headers(
            FakeRequest(
                {"tenantId": "altitude"},
                {
                    "traceparent": TRACEPARENT,
                    "x-ms-client-request-id": "client-request-1",
                    "x-ms-workflow-run-id": "flow-run-1",
                },
            )
        )

        self.assertEqual(payload["headers"]["traceparent"], TRACEPARENT)
        self.assertEqual(payload["headers"]["x-ms-workflow-run-id"], "flow-run-1")

    def test_import_response_mode_defaults_to_queued_with_inline_override(self) -> None:
        self.assertEqual(_import_response_mode(FakeRequest({}, {}), {}), "queued")
        self.assertEqual(_import_response_mode(FakeRequest({}, {}, {"responseMode": "inline"}), {}), "inline")
        self.assertEqual(
            _import_response_mode(
                FakeRequest({}, {"x-order-processor-response-mode": "sync"}),
                {},
            ),
            "sync",
        )

    def test_queued_item_import_payloads_are_chunked_by_row_count(self) -> None:
        payload = {
            "tenantId": "test-customer",
            "sourceName": "items.json",
            "sourceMetadata": {"source": "unit-test"},
            "rows": [{"part_code": f"PART-{index}"} for index in range(5)],
        }

        with patch.dict(
            "os.environ",
            {"ORDER_PROCESSOR_ITEM_IMPORT_JOB_CHUNK_SIZE": "2"},
            clear=False,
        ):
            chunks = _chunk_import_payloads("items", payload)

        self.assertEqual(len(chunks), 3)
        self.assertEqual([len(chunk["rows"]) for chunk in chunks], [2, 2, 1])
        self.assertEqual(chunks[0]["importChunk"]["rowStart"], 0)
        self.assertEqual(chunks[1]["importChunk"]["rowStart"], 2)
        self.assertEqual(chunks[2]["importChunk"]["rowEndExclusive"], 5)
        self.assertEqual(chunks[0]["importChunk"]["chunkCount"], 3)
        self.assertEqual(chunks[0]["sourceMetadata"]["source"], "unit-test")
        self.assertEqual(chunks[0]["sourceMetadata"]["importChunk"], chunks[0]["importChunk"])
        self.assertEqual({chunk["importBatchId"] for chunk in chunks}, {chunks[0]["importBatchId"]})

    def test_customer_import_payloads_are_not_chunked(self) -> None:
        payload = {"tenantId": "test-customer", "rows": [{"cust_code": str(index)} for index in range(5)]}

        with patch.dict(
            "os.environ",
            {"ORDER_PROCESSOR_IMPORT_JOB_CHUNK_SIZE": "2"},
            clear=False,
        ):
            chunks = _chunk_import_payloads("customers", payload)

        self.assertEqual(chunks, [payload])

    def test_ingest_persists_correlation_context_to_email_order_and_audit(self) -> None:
        api, repo = self._api()
        api.upsert_routing_rule(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "name": "pilot csv",
                "outcome": "knownOrder",
                "attachmentExtensions": ["csv"],
            }
        )

        result = api.ingest_email(
            {
                "tenantId": "altitude",
                "mailbox": "orders@example.com",
                "messageId": "message-1",
                "sender": "buyer@example.com",
                "subject": "PO 123",
                "headers": {
                    "traceparent": TRACEPARENT,
                    "x-ms-client-request-id": "client-request-1",
                    "x-ms-workflow-run-id": "flow-run-1",
                    "x-order-processor-ingress": "api-management",
                },
                "attachments": [{"name": "order.csv", "contentType": "text/csv"}],
            }
        )

        email = repo.get("emailMessages", result["emailMessage"]["id"])
        order = repo.get("orderRuns", result["orderRun"]["id"])
        audit = [event for event in repo.query_by_tenant("auditEvents", "altitude") if event["eventType"] == "email.ingested"][-1]

        self.assertEqual(result["observability"]["correlationId"], "client-request-1")
        self.assertEqual(result["observability"]["traceId"], "4bf92f3577b34da6a3ce929d0e0e4736")
        self.assertEqual(result["observability"]["powerAutomateFlowRunId"], "flow-run-1")
        self.assertEqual(email["correlationId"], "client-request-1")
        self.assertEqual(order["correlationId"], "client-request-1")
        self.assertEqual(audit["correlationId"], "client-request-1")
        self.assertEqual(audit["traceId"], "4bf92f3577b34da6a3ce929d0e0e4736")
        self.assertEqual(audit["emailMessageId"], email["id"])

    def test_order_timeline_contains_processing_decisions_and_artifacts(self) -> None:
        api, repo = self._api()
        repo.upsert(
            "items",
            {
                "id": "item-1",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "internalItemNumber": "10001",
                "description": "Dog Food",
                "customerItemNumbers": ["PILOT123"],
            },
        )

        api.process_order(
            "order-run-1",
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "processorType": "csv",
                "sourceContent": "item_number,quantity,description\nPILOT123,2,Dog Food\n",
                "headers": {"x-ms-client-request-id": "process-correlation-1"},
            },
        )
        timeline = api.order_observability_timeline("order-run-1", {"tenantId": "altitude"})["timeline"]
        event_types = [event["eventType"] for event in timeline["events"]]

        self.assertIn("order.processingStarted", event_types)
        self.assertIn("order.processed", event_types)
        self.assertIn("output.artifactGenerated", event_types)
        self.assertEqual(timeline["correlationId"], "process-correlation-1")
        self.assertIsNotNone(timeline["processingLatencyMs"])

    def test_console_dashboard_observability_metrics_count_failures_and_latency(self) -> None:
        api, repo = self._api()
        repo.upsert(
            "orderRuns",
            to_dict(
                OrderRun(
                    id="failed-run",
                    tenant_id="altitude",
                    email_message_id="email-1",
                    customer_id="pilot-customer",
                    status=ProcessingStatus.FAILED,
                    processing_started_at="2026-06-21T10:00:00+00:00",
                    processing_completed_at="2026-06-21T10:00:03+00:00",
                    errors=[{"code": "parserFailure"}],
                )
            ),
        )
        api._create_exception(
            tenant_id="altitude",
            task_type="parserFailure",
            prompt="Parser failed",
            order_run_id="failed-run",
            customer_id="pilot-customer",
            correlation_id="failed-run-correlation",
        )
        api._create_exception(
            tenant_id="altitude",
            task_type="customerIdentification",
            prompt="Resolve customer",
            email_message_id="email-2",
            correlation_id="customer-id-correlation",
        )

        dashboard = api.console_dashboard({"tenantId": "altitude", "email": "connect@focuseautomate.com"})
        metrics = dashboard["observabilityMetrics"]

        self.assertEqual(metrics["processorFailureCount"], 1)
        self.assertEqual(metrics["customerIdentificationFailureCount"], 1)
        self.assertEqual(metrics["processingLatency"]["averageMs"], 3000)
        self.assertEqual(dashboard["summary"]["processorFailureCount"], 1)
        self.assertGreaterEqual(metrics["auditEventCount"], 2)


if __name__ == "__main__":
    unittest.main()
