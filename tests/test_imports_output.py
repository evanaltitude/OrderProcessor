from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.imports import (
    item_record_id,
    legacy_item_record_id,
    normalize_customer_alias_rows,
    normalize_customer_row,
    normalize_item_row,
    scoped_item_record_id,
)
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
                "internal_route_code": "340",
                "stop_no": 60,
                "cust_code": "100025",
                "bus_name": "CLASSIC PET II - MARYSVILLE",
                "address1": "3180 GRATIOT BLVD",
                "city": "MARYSVILLE",
                "state": "MI",
                "zip": "48040",
                "phone": "616-452-7877",
                "cust_csr": "mwoodward",
                "csr_email": "melissa.woodward@frontierdistributing.com",
                "process_day": "Tuesday",
                "email_addresses": "amyfowler1986@yahoo.com; classicpets@comcast.net",
                "store_website": "WWW.CLASSICPETSUPPLY.NET",
                "store_email": "CLASSICPETS@COMCAST.NET",
                "mailblast_addr": "classic-pets@updates.example",
                "route_code": "300",
                "rank": 1,
            },
            {},
        )
        aliases = normalize_customer_alias_rows(
            "altitude",
            customer,
            customer.raw_source,
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

        self.assertEqual(customer.customer_code, "100025")
        self.assertEqual(customer.name, "CLASSIC PET II - MARYSVILLE")
        self.assertEqual(customer.route_number, "300")
        self.assertEqual(customer.csr_name, "mwoodward")
        self.assertEqual(customer.csr_folder, "mwoodward")
        self.assertEqual(customer.csr_email, "melissa.woodward@frontierdistributing.com")
        self.assertEqual(customer.address1, "3180 GRATIOT BLVD")
        self.assertEqual(customer.city, "MARYSVILLE")
        self.assertEqual(customer.postal_code, "48040")
        self.assertEqual(customer.website, "WWW.CLASSICPETSUPPLY.NET")
        self.assertEqual(customer.customer_email, "CLASSICPETS@COMCAST.NET")
        self.assertEqual(customer.custom_fields["internalRouteCode"], "340")
        sender_email_aliases = {alias.value.lower() for alias in aliases if alias.alias_type == "senderEmail"}
        sender_domain_aliases = {alias.normalized_value for alias in aliases if alias.alias_type == "senderDomain"}
        self.assertIn("amyfowler1986@yahoo.com", sender_email_aliases)
        self.assertIn("classicpets@comcast.net", sender_email_aliases)
        self.assertIn("classic-pets@updates.example", sender_email_aliases)
        self.assertIn("yahoo.com", sender_domain_aliases)
        self.assertIn("comcast.net", sender_domain_aliases)
        self.assertIn("updates.example", sender_domain_aliases)
        self.assertEqual(item.internal_item_number, "100510100")
        self.assertEqual(item.upc, "031865BRN4R")
        self.assertEqual(item.alt_parts_combined, ["031865BRN4R", "10004120"])
        self.assertIn("10004120", item.customer_item_numbers)
        self.assertEqual(item.raw_source["alt_parts_combined"][0]["alt_part"], "031865BRN4R")

    def test_legacy_customer_file_uses_email_folder_as_route_and_preserves_internal_route(self) -> None:
        customer = normalize_customer_row(
            "altitude",
            {
                "route_code": "245",
                "cust_code": "100028",
                "bus_name": "CHOW HOUND #3",
                "cust_csr": "rrussell",
                "shipto_storenumb": "503",
                "email_folder": "200",
            },
            {},
        )

        self.assertEqual(customer.route_number, "200")
        self.assertEqual(customer.custom_fields["internalRouteCode"], "245")
        self.assertEqual(customer.csr_name, "rrussell")
        self.assertEqual(customer.csr_folder, "rrussell")
        self.assertEqual(customer.store_number, "503")

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
                        "bus_name": "New Customer",
                        "route_code": "300",
                        "cust_csr": "mwoodward",
                        "store_email": "orders@new.example",
                        "email_addresses": "buyer@new.example; owner@retail.example",
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
        self.assertIn("existing.example", existing_record["sender_domains"])
        new_record = next(record for record in records if record["cust_code"] == "NEW")
        self.assertEqual(new_record["route_number"], "300")
        self.assertIn("orders@new.example", new_record["sender_emails"])
        self.assertIn("buyer@new.example", new_record["sender_emails"])
        self.assertIn("retail.example", new_record["sender_domains"])
        self.assertNotIn("csr_email", new_record)
        self.assertNotIn("csr_folder", new_record)

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

    def test_import_items_ignores_downstream_customer_scope_for_distributor_catalog(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo, source_archive=InMemorySourceRowArchive())
        api.import_customers({"tenantId": "altitude", "rows": [{"customerCode": "102914", "name": "Hollywood Feed"}]})

        items = api.import_items(
            {
                "tenantId": "altitude",
                "customerCode": "102914",
                "rows": [{"internalItemNumber": "10001", "description": "Test Item"}],
            }
        )

        expected_id = item_record_id("altitude", "10001")
        self.assertEqual(items["customerId"], GLOBAL_CUSTOMER_ID)
        self.assertEqual(items["customerCode"], "")
        self.assertEqual(items["items"][0]["id"], expected_id)
        self.assertEqual(items["items"][0]["customerId"], GLOBAL_CUSTOMER_ID)
        self.assertEqual(repo.get("items", expected_id)["customerId"], GLOBAL_CUSTOMER_ID)

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

    def test_import_item_audit_omits_embedded_item_records(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(
            repo,
            source_archive=InMemorySourceRowArchive(),
            import_embedding_client=FakeEmbeddingClient(),
        )

        result = api.import_items(
            {
                "tenantId": "altitude",
                "rows": [
                    {"part_code": "10001", "part_desc": "Dog Food"},
                    {"part_code": "10002", "part_desc": "Cat Food"},
                ],
            }
        )

        audits = [event for event in repo.query_by_tenant("auditEvents", "altitude") if event["eventType"] == "items.imported"]
        details = audits[-1]["details"]
        self.assertEqual(len(result["items"]), 2)
        self.assertNotIn("items", details)
        self.assertEqual(details["importedCount"], 2)
        self.assertEqual(details["suppressedRecordDetails"][0]["field"], "items")
        self.assertEqual(details["suppressedRecordDetails"][0]["count"], 2)

    def test_import_customer_audit_omits_embedded_customer_records(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(
            repo,
            source_archive=InMemorySourceRowArchive(),
            import_embedding_client=FakeEmbeddingClient(),
        )

        result = api.import_customers(
            {
                "tenantId": "altitude",
                "rows": [{"cust_code": "10001", "bus_name": "Pilot Store"}],
            }
        )

        audits = [
            event
            for event in repo.query_by_tenant("auditEvents", "altitude")
            if event["eventType"] == "customers.imported"
        ]
        details = audits[-1]["details"]
        self.assertEqual(len(result["customers"]), 1)
        self.assertNotIn("customers", details)
        self.assertNotIn("customerAliases", details)
        self.assertEqual(details["importedCount"], 1)

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

    def test_import_items_uses_part_code_as_unique_id_without_upc(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo, source_archive=InMemorySourceRowArchive())
        first = api.import_items(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "rows": [{"part_code": "10001", "upc_code": "012345678905", "part_desc": "Old Dog Food"}],
            }
        )
        second = api.import_items(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "rows": [{"part_code": "10001", "upc_code": "999999999999", "part_desc": "New Dog Food"}],
            }
        )

        expected_id = item_record_id("altitude", "10001")
        self.assertEqual(first["items"][0]["id"], expected_id)
        self.assertEqual(second["items"][0]["id"], expected_id)
        self.assertEqual(second["updatedCount"], 1)
        self.assertEqual(repo.get("items", expected_id)["upc"], "999999999999")
        self.assertEqual(repo.get("items", expected_id)["customerId"], GLOBAL_CUSTOMER_ID)
        self.assertEqual(len(repo.query_by_customer("items", "altitude", GLOBAL_CUSTOMER_ID)), 1)

    def test_import_items_rekeys_legacy_customer_scoped_item_ids(self) -> None:
        repo = InMemoryRepository()
        legacy_id = legacy_item_record_id("altitude", "pilot-customer", "10001", "012345678905")
        scoped_id = scoped_item_record_id("altitude", "pilot-customer", "10001")
        repo.upsert(
            "items",
            {
                "id": legacy_id,
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "internalItemNumber": "10001",
                "upc": "012345678905",
                "description": "Old Dog Food",
            },
        )
        repo.upsert(
            "items",
            {
                "id": scoped_id,
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "internalItemNumber": "10001",
                "description": "Scoped Dog Food",
            },
        )
        api = OrderProcessorApi(repo, source_archive=InMemorySourceRowArchive())

        result = api.import_items(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "rows": [{"part_code": "10001", "upc_code": "012345678905", "part_desc": "New Dog Food"}],
            }
        )

        expected_id = item_record_id("altitude", "10001")
        self.assertEqual(result["items"][0]["id"], expected_id)
        self.assertEqual(result["items"][0]["customerId"], GLOBAL_CUSTOMER_ID)
        self.assertEqual(result["legacyRekeyedCount"], 2)
        self.assertIsNone(repo.get("items", legacy_id))
        self.assertIsNone(repo.get("items", scoped_id))
        self.assertEqual(repo.get("items", expected_id)["description"], "New Dog Food")

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
