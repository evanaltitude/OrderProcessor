from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.imports import normalize_customer_row, normalize_item_row
from order_processor.imports import InMemorySourceRowArchive
from order_processor.customer_vector_store import CustomerVectorStoreManager, customer_vector_store_reference_id
from order_processor.data_model import GLOBAL_CUSTOMER_ID
from order_processor.models import ItemRecord, OrderRun, ProcessingStatus
from order_processor.order_processing import CsvOrderProcessor, validate_order_lines
from order_processor.output_generation import order_to_json, order_to_line_csv
from order_processor.api import OrderProcessorApi
from order_processor.storage import InMemoryRepository


class FakeEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        return [float(len(text or "")), 1.0]


class FakeCustomerVectorStoreClient:
    def __init__(self, fail_create: bool = False) -> None:
        self.fail_create = fail_create
        self.created: list[dict[str, object]] = []
        self.deleted_files: list[str] = []
        self.deleted_vector_stores: list[str] = []

    def create_customer_vector_store(
        self,
        *,
        name: str,
        filename: str,
        content: bytes,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, object]:
        if self.fail_create:
            raise RuntimeError("vector store create failed")
        records = [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
        self.created.append(
            {
                "name": name,
                "filename": filename,
                "metadata": metadata or {},
                "records": records,
            }
        )
        sequence = len(self.created)
        return {
            "vectorStoreId": f"vs-new-{sequence}",
            "fileId": f"file-new-{sequence}",
            "fileBatchId": f"batch-new-{sequence}",
            "fileCounts": {"completed": 1, "failed": 0},
        }

    def delete_file(self, file_id: str) -> None:
        self.deleted_files.append(file_id)

    def delete_vector_store(self, vector_store_id: str) -> None:
        self.deleted_vector_stores.append(vector_store_id)


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
                "part_code": "100510100",
                "upc_code": "031865BRN4R",
                "alt_parts_combined": [
                    {"alt_part": "031865BRN4R"},
                    {"alt_part": "10004120"},
                ],
                "part_desc": "Bed-r Nest Kraft Irradiated 4 gram 1600 per case",
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
        self.assertEqual(item.internal_item_number, "100510100")
        self.assertEqual(item.upc, "031865BRN4R")
        self.assertEqual(item.alt_parts_combined, ["031865BRN4R", "10004120"])
        self.assertIn("10004120", item.customer_item_numbers)
        self.assertEqual(item.raw_source["alt_parts_combined"][0]["alt_part"], "031865BRN4R")

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

    def test_import_customers_rotates_vector_store_after_customer_update(self) -> None:
        repo = InMemoryRepository()
        fake_vector_store = FakeCustomerVectorStoreClient()
        manager = CustomerVectorStoreManager(repo, fake_vector_store)
        reference_id = customer_vector_store_reference_id("altitude")
        repo.upsert(
            "customers",
            {
                "id": "existing-customer",
                "tenantId": "altitude",
                "customerCode": "OLD",
                "name": "Existing Customer",
                "aliases": ["Existing Alias"],
            },
        )
        repo.upsert(
            "customerAliases",
            {
                "id": "existing-alias",
                "tenantId": "altitude",
                "customerId": "existing-customer",
                "aliasType": "senderDomain",
                "value": "existing.example",
                "normalizedValue": "existing.example",
            },
        )
        repo.upsert(
            "customerVectorStores",
            {
                "id": reference_id,
                "tenantId": "altitude",
                "referenceType": "customerListFileSearch",
                "status": "active",
                "vectorStoreId": "vs-old",
                "fileId": "file-old",
            },
        )
        api = OrderProcessorApi(
            repo,
            source_archive=InMemorySourceRowArchive(),
            customer_vector_store_manager=manager,
        )

        result = api.import_customers(
            {
                "tenantId": "altitude",
                "rows": [
                    {
                        "cust_code": "NEW",
                        "customer_name": "New Customer",
                        "customer_store_number": "101",
                        "location_city": "Grand Rapids",
                    }
                ],
            }
        )

        reference = repo.get("customerVectorStores", reference_id)
        records = fake_vector_store.created[0]["records"]
        self.assertEqual(result["customerVectorStore"]["status"], "active")
        self.assertEqual(reference["vectorStoreId"], "vs-new-1")
        self.assertEqual(reference["fileId"], "file-new-1")
        self.assertEqual(reference["previousVectorStoreId"], "vs-old")
        self.assertEqual(fake_vector_store.deleted_files, ["file-old"])
        self.assertEqual(fake_vector_store.deleted_vector_stores, ["vs-old"])
        self.assertEqual({record["cust_code"] for record in records}, {"OLD", "NEW"})
        existing_record = next(record for record in records if record["cust_code"] == "OLD")
        self.assertIn("existing.example", existing_record["aliases"])

    def test_import_customers_preserves_old_vector_store_reference_when_rotation_fails(self) -> None:
        repo = InMemoryRepository()
        fake_vector_store = FakeCustomerVectorStoreClient(fail_create=True)
        manager = CustomerVectorStoreManager(repo, fake_vector_store)
        reference_id = customer_vector_store_reference_id("altitude")
        repo.upsert(
            "customerVectorStores",
            {
                "id": reference_id,
                "tenantId": "altitude",
                "referenceType": "customerListFileSearch",
                "status": "active",
                "vectorStoreId": "vs-old",
                "fileId": "file-old",
            },
        )
        api = OrderProcessorApi(
            repo,
            source_archive=InMemorySourceRowArchive(),
            customer_vector_store_manager=manager,
        )

        result = api.import_customers(
            {
                "tenantId": "altitude",
                "rows": [{"cust_code": "NEW", "customer_name": "New Customer"}],
            }
        )

        reference = repo.get("customerVectorStores", reference_id)
        self.assertEqual(result["customerVectorStore"]["status"], "failed")
        self.assertEqual(reference["vectorStoreId"], "vs-old")
        self.assertEqual(reference["fileId"], "file-old")
        self.assertEqual(fake_vector_store.deleted_files, [])
        self.assertEqual(fake_vector_store.deleted_vector_stores, [])

    def test_import_items_resolves_customer_code_to_imported_customer_id(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo, source_archive=InMemorySourceRowArchive())
        customers = api.import_customers(
            {
                "tenantId": "altitude",
                "rows": [{"customerCode": "102914", "name": "Hollywood Feed"}],
            }
        )
        customer_id = customers["customers"][0]["id"]

        items = api.import_items(
            {
                "tenantId": "altitude",
                "customerCode": "102914",
                "rows": [{"internalItemNumber": "10001", "description": "Test Item"}],
            }
        )

        self.assertEqual(items["customerId"], customer_id)
        self.assertEqual(items["customerCode"], "102914")
        self.assertEqual(items["items"][0]["customerId"], customer_id)
        self.assertEqual(repo.get("items", items["items"][0]["id"])["customerId"], customer_id)

    def test_import_items_without_customer_scope_uses_master_catalog(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo, source_archive=InMemorySourceRowArchive())

        items = api.import_items(
            {
                "tenantId": "altitude",
                "sourceName": "itemNumbers.json",
                "rows": [
                    {
                        "part_code": "100510100",
                        "upc_code": "031865BRN4R",
                        "alt_parts_combined": [
                            {"alt_part": "031865BRN4R"},
                            {"alt_part": "10004120"},
                        ],
                        "part_desc": "Bed-r Nest Kraft Irradiated 4 gram 1600 per case",
                    }
                ],
            }
        )

        self.assertEqual(items["customerId"], GLOBAL_CUSTOMER_ID)
        self.assertEqual(items["customerCode"], "")
        self.assertEqual(items["items"][0]["customerId"], GLOBAL_CUSTOMER_ID)
        self.assertEqual(items["items"][0]["altPartsCombined"], ["031865BRN4R", "10004120"])

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
