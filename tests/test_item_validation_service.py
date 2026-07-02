from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.api import OrderProcessorApi
from order_processor.models import OrderLine, OrderRun, to_dict
from order_processor.storage import InMemoryRepository


class ItemValidationServiceTests(unittest.TestCase):
    def _api_with_item(self) -> tuple[OrderProcessorApi, InMemoryRepository]:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        repo.upsert(
            "items",
            {
                "id": "item-1",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "internalItemNumber": "10001",
                "description": "Dog Food 25 lb",
                "upc": "012345678905",
                "customerItemNumbers": ["PILOT123"],
            },
        )
        return api, repo

    def test_validate_item_endpoint_matches_and_updates_order_line(self) -> None:
        api, repo = self._api_with_item()
        repo.upsert(
            "orderRuns",
            to_dict(
                OrderRun(
                    id="order-run-1",
                    tenant_id="altitude",
                    email_message_id="email-1",
                    customer_id="pilot-customer",
                    lines=[OrderLine(line_number=1, provided_upc="012345678905", quantity=2)],
                )
            ),
        )

        result = api.validate_item(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "providedUpc": "012345678905",
                "description": "Dog Food 25 lb",
                "orderRunId": "order-run-1",
                "lineNumber": 1,
            }
        )

        self.assertEqual(result["result"]["status"], "matched")
        self.assertEqual(result["result"]["matchedInternalItemNumber"], "10001")
        self.assertEqual(result["result"]["matchMethod"], "upcExact")
        self.assertIsNone(result["exceptionTask"])
        self.assertEqual(result["updatedOrderLine"]["validationStatus"], "matched")
        stored_order = repo.get("orderRuns", "order-run-1")
        self.assertEqual(stored_order["lines"][0]["matchedInternalItemNumber"], "10001")
        self.assertEqual(stored_order["status"], "completed")
        audit_events = repo.query_by_tenant("auditEvents", "altitude")
        self.assertEqual(audit_events[-1]["eventType"], "item.validated")

    def test_validate_item_endpoint_uses_row_context_and_candidate_limit(self) -> None:
        api, _ = self._api_with_item()

        result = api.validate_item(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "rowContext": {"Vendor Item": "PILOT-123"},
                "candidateLimit": 1,
            }
        )

        self.assertEqual(result["result"]["status"], "matched")
        self.assertEqual(len(result["result"]["candidates"]), 1)
        self.assertEqual(result["result"]["matchedInternalItemNumber"], "10001")

    def test_validate_item_endpoint_accepts_legacy_power_automate_field_names(self) -> None:
        api, repo = self._api_with_item()
        repo.upsert(
            "items",
            {
                "id": "item-2",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "internalItemNumber": "00123456789",
                "description": "Cat Food 10 lb",
                "upc": "012345678905",
            },
        )

        result = api.validate_item(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "varItemNumber": "123456789",
                "varItemUPC": "",
                "varItemDescription": "",
            }
        )

        self.assertEqual(result["result"]["status"], "matched")
        self.assertEqual(result["result"]["matchedInternalItemNumber"], "00123456789")
        self.assertEqual(result["result"]["matchMethod"], "itemNumberLeadingZeroTolerant")

    def test_validate_item_endpoint_creates_exception_and_marks_line_unresolved(self) -> None:
        api, repo = self._api_with_item()
        repo.upsert(
            "orderRuns",
            to_dict(
                OrderRun(
                    id="order-run-2",
                    tenant_id="altitude",
                    email_message_id="email-2",
                    customer_id="pilot-customer",
                    lines=[OrderLine(line_number=1, provided_item_number="UNKNOWN", quantity=1)],
                )
            ),
        )

        result = api.validate_item(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "providedItemNumber": "UNKNOWN",
                "orderRunId": "order-run-2",
                "lineNumber": 1,
                "rowContext": {"Column 1": "UNKNOWN", "Column 3": "1"},
            }
        )

        self.assertEqual(result["result"]["status"], "unresolved")
        self.assertEqual(result["exceptionTask"]["type"], "itemValidation")
        self.assertEqual(result["exceptionTask"]["lineNumber"], 1)
        self.assertEqual(result["updatedOrderLine"]["validationStatus"], "unresolved")
        stored_order = repo.get("orderRuns", "order-run-2")
        self.assertEqual(stored_order["status"], "needsReview")
        self.assertEqual(stored_order["lines"][0]["validationErrors"][0]["code"], "unresolvedItem")

    def test_validate_item_endpoint_creates_task_for_possible_match(self) -> None:
        api, _ = self._api_with_item()

        result = api.validate_item(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "description": "Dog Food",
                "confidenceThreshold": 0.9,
            }
        )

        self.assertEqual(result["result"]["status"], "possibleMatch")
        self.assertEqual(result["exceptionTask"]["type"], "itemValidation")
        self.assertEqual(
            result["exceptionTask"]["context"]["result"]["unresolvedReason"],
            "best candidate below confidence threshold",
        )


if __name__ == "__main__":
    unittest.main()
