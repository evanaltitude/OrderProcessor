from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.imports import normalize_customer_row, normalize_item_row
from order_processor.imports import InMemorySourceRowArchive
from order_processor.models import ItemRecord, OrderRun, ProcessingStatus
from order_processor.order_processing import CsvOrderProcessor, validate_order_lines
from order_processor.output_generation import order_to_json, order_to_line_csv
from order_processor.api import OrderProcessorApi
from order_processor.storage import InMemoryRepository


class FakeEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        return [float(len(text or "")), 1.0]


class ImportsOutputTests(unittest.TestCase):
    def test_customer_and_item_rows_normalize(self) -> None:
        customer = normalize_customer_row(
            "altitude",
            {
                "Customer Code": "PILOT",
                "Customer Name": "Pilot Customer",
                "sender_domains": "pilot.example;orders.pilot.example",
                "known_subject_patterns": "Pilot Order;Pilot Weekly",
            },
            {"customer_code": "Customer Code", "name": "Customer Name"},
        )
        item = normalize_item_row(
            "altitude",
            customer.id,
            {
                "Item": "10001",
                "Description": "Dog Food",
                "customer_item_numbers": "PILOT-123",
            },
            {"internal_item_number": "Item", "description": "Description"},
        )

        self.assertEqual(customer.customer_code, "PILOT")
        self.assertEqual(customer.sender_domains, ["pilot.example", "orders.pilot.example"])
        self.assertEqual(customer.known_subject_patterns, ["Pilot Order", "Pilot Weekly"])
        self.assertEqual(item.customer_item_numbers, ["PILOT123"])

    def test_universal_customer_and_item_source_shapes_normalize_without_field_map(self) -> None:
        customer = normalize_customer_row(
            "altitude",
            {
                "customer_name": "CHOW HOUND #4",
                "customer_store_number": "504",
                "location_address1": "734 28TH ST SE",
                "location_city": "GRAND RAPIDS",
                "location_state": "MI",
                "location_zip": "49548",
                "phone": "616-452-7877",
                "customer_website": "WWW.CHOWHOUNDPET.COM",
                "customer_email": "GREGC@CHOWHOUNDPET.COM",
                "cust_code": "100029",
            },
            {},
        )
        item = normalize_item_row(
            "altitude",
            customer.id,
            {
                "part_code": "200510610",
                "upc_code": "849910140402",
                "alt_parts_combined": "849910140402|CLR LG TCT BK",
                "part_desc": "ALCOTT LARGE DOG SOLID BLACK TACTICAL COLLAR EA",
            },
            {},
        )

        self.assertEqual(customer.customer_code, "100029")
        self.assertEqual(customer.name, "CHOW HOUND #4")
        self.assertEqual(customer.store_number, "504")
        self.assertEqual(customer.address1, "734 28TH ST SE")
        self.assertEqual(customer.city, "GRAND RAPIDS")
        self.assertEqual(customer.postal_code, "49548")
        self.assertEqual(customer.customer_email, "GREGC@CHOWHOUNDPET.COM")
        self.assertEqual(item.internal_item_number, "200510610")
        self.assertEqual(item.upc, "849910140402")
        self.assertEqual(item.alt_parts_combined, ["849910140402", "CLR LG TCT BK"])
        self.assertIn("CLRLGTCTBK", item.customer_item_numbers)

    def test_csv_processor_outputs_universal_formats(self) -> None:
        order = OrderRun(
            id="order-run-1",
            tenant_id="altitude",
            email_message_id="email-1",
            customer_id="pilot-customer",
        )
        parsed = CsvOrderProcessor().parse(
            order,
            "item_number,quantity,description\nPILOT123,2,Dog Food 25 lb\n",
        )
        validated = validate_order_lines(
            parsed,
            [
                ItemRecord(
                    id="item-1",
                    tenant_id="altitude",
                    customer_id="pilot-customer",
                    internal_item_number="10001",
                    description="Dog Food 25 lb",
                    customer_item_numbers=["PILOT123"],
                )
            ],
        )

        self.assertEqual(validated.status, ProcessingStatus.PROCESSING)
        self.assertEqual(validated.lines[0].matched_internal_item_number, "10001")
        self.assertIn("PILOT123", order_to_line_csv(validated))
        self.assertEqual(json.loads(order_to_json(validated))["id"], "order-run-1")

    def test_import_customers_from_csv_archives_rows_aliases_and_daily_schedule(self) -> None:
        repo = InMemoryRepository()
        archive = InMemorySourceRowArchive()
        api = OrderProcessorApi(repo, source_archive=archive, import_embedding_client=FakeEmbeddingClient())

        result = api.import_customers(
            {
                "tenantId": "altitude",
                "sourceName": "pilot-customers.csv",
                "contentType": "text/csv",
                "parserModule": "genericCustomerCsv",
                "sourceContent": (
                    "Customer Code,Customer Name,Domains,Store,Route,Subject Patterns\n"
                    "PILOT,Pilot Customer,pilot.example,101,R12,Pilot Weekly\n"
                ),
                "fieldMap": {
                    "customer_code": "Customer Code",
                    "name": "Customer Name",
                    "sender_domains": "Domains",
                    "store_number": "Store",
                    "route_number": "Route",
                    "known_subject_patterns": "Subject Patterns",
                },
            }
        )

        stored_customer = repo.get("customers", result["customers"][0]["id"])
        aliases = repo.query_by_customer("customerAliases", "altitude", stored_customer["id"])
        self.assertEqual(result["importedCount"], 1)
        self.assertEqual(result["createdCount"], 1)
        self.assertEqual(result["refreshPolicy"]["intervalDays"], 1)
        self.assertTrue(result["sourceRowsBlobUrl"].startswith("memory://source-rows/"))
        self.assertIn(result["sourceRowsBlobUrl"], archive.objects)
        self.assertEqual(stored_customer["sourceRowsBlobUrl"], result["sourceRowsBlobUrl"])
        self.assertEqual(stored_customer["sourceName"], "pilot-customers.csv")
        self.assertEqual(len(stored_customer["embedding"]), 2)
        self.assertGreater(stored_customer["embedding"][0], 0)
        self.assertIn("senderDomain", {alias["aliasType"] for alias in aliases})
        self.assertIn("knownSubjectPattern", {alias["aliasType"] for alias in aliases})

    def test_import_items_honors_customer_refresh_override_and_incremental_updates(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo, source_archive=InMemorySourceRowArchive())
        first = api.import_items(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "sourceName": "pilot-items.json",
                "contentType": "application/json",
                "sourceContent": json.dumps(
                    [
                        {
                            "Item": "10001",
                            "Description": "Old Dog Food",
                            "UPC": "012345678905",
                            "Customer Item": "PILOT-123",
                        }
                    ]
                ),
                "fieldMap": {
                    "internal_item_number": "Item",
                    "description": "Description",
                    "upc": "UPC",
                    "customer_item_numbers": "Customer Item",
                },
            }
        )
        second = api.import_items(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "sourceName": "pilot-items.json",
                "contentType": "application/json",
                "customerConfig": {"itemRefreshIntervalDays": 3},
                "sourceContent": json.dumps(
                    [
                        {
                            "Item": "10001",
                            "Description": "New Dog Food",
                            "UPC": "012345678905",
                            "Customer Item": "PILOT-123",
                        }
                    ]
                ),
                "fieldMap": {
                    "internal_item_number": "Item",
                    "description": "Description",
                    "upc": "UPC",
                    "customer_item_numbers": "Customer Item",
                },
            }
        )

        stored_item = repo.get("items", first["items"][0]["id"])
        self.assertEqual(first["refreshPolicy"]["intervalDays"], 7)
        self.assertEqual(second["refreshPolicy"]["intervalDays"], 3)
        self.assertEqual(second["updatedCount"], 1)
        self.assertEqual(stored_item["description"], "New Dog Food")
        self.assertEqual(stored_item["customerItemNumbers"], ["PILOT123"])

    def test_import_items_reports_missing_identifier_and_archives_original_rows(self) -> None:
        archive = InMemorySourceRowArchive()
        api = OrderProcessorApi(InMemoryRepository(), source_archive=archive)

        result = api.import_items(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "rows": [{"Description": "No identifiers"}],
                "fieldMap": {"description": "Description"},
            }
        )

        self.assertEqual(result["importedCount"], 0)
        self.assertEqual(result["skippedCount"], 1)
        self.assertEqual(result["errorCount"], 1)
        self.assertEqual(result["errors"][0]["code"], "missingItemIdentifier")
        self.assertIn(result["sourceRowsBlobUrl"], archive.objects)

    def test_malformed_json_source_returns_parse_error_without_throwing(self) -> None:
        api = OrderProcessorApi(InMemoryRepository(), source_archive=InMemorySourceRowArchive())

        result = api.import_customers(
            {
                "tenantId": "altitude",
                "contentType": "application/json",
                "sourceName": "bad-customers.json",
                "sourceContent": "[",
            }
        )

        self.assertEqual(result["importedCount"], 0)
        self.assertEqual(result["errorCount"], 1)
        self.assertEqual(result["errors"][0]["code"], "jsonParseError")
        self.assertEqual(result["parserModule"], "json")

    def test_import_customers_from_jsonl(self) -> None:
        api = OrderProcessorApi(InMemoryRepository(), source_archive=InMemorySourceRowArchive())

        result = api.import_customers(
            {
                "tenantId": "altitude",
                "parserModule": "jsonl",
                "sourceName": "customers.jsonl",
                "sourceContent": json.dumps({"customer_code": "PILOT", "name": "Pilot Customer"}) + "\n",
            }
        )

        self.assertEqual(result["importedCount"], 1)
        self.assertEqual(result["customers"][0]["customerCode"], "PILOT")


if __name__ == "__main__":
    unittest.main()
