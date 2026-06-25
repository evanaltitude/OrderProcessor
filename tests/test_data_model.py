from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.data_model import (
    CONTAINER_BY_NAME,
    CONTAINER_NAMES,
    GLOBAL_CUSTOMER_ID,
    UNASSIGNED_CUSTOMER_ID,
    container_manifest,
    normalize_document_for_storage,
    partition_key_value,
)
from order_processor.storage import InMemoryRepository


class DataModelTests(unittest.TestCase):
    def test_container_manifest_contains_phase_four_containers(self) -> None:
        self.assertEqual(
            set(CONTAINER_NAMES),
            {
                "tenants",
                "customers",
                "customerVectorStores",
                "aiCostSources",
                "aiCostEvents",
                "customerAliases",
                "items",
                "routingRules",
                "processorProfiles",
                "outputProfiles",
                "mailboxAccounts",
                "microsoftAuthConnections",
                "consoleUsers",
                "customerUserAssignments",
                "emailMessages",
                "orderRuns",
                "orderLines",
                "exceptionTasks",
                "monitorRecords",
                "auditEvents",
            },
        )
        self.assertEqual(len(container_manifest()), 20)

    def test_customer_scoped_partition_contract(self) -> None:
        self.assertEqual(CONTAINER_BY_NAME["customers"].partition_key_paths, ("/tenantId",))
        self.assertEqual(CONTAINER_BY_NAME["customerVectorStores"].partition_key_paths, ("/tenantId",))
        self.assertEqual(CONTAINER_BY_NAME["aiCostSources"].partition_key_paths, ("/tenantId",))
        self.assertEqual(CONTAINER_BY_NAME["aiCostEvents"].partition_key_paths, ("/tenantId",))
        self.assertEqual(CONTAINER_BY_NAME["items"].partition_key_paths, ("/tenantId", "/customerId"))
        self.assertEqual(CONTAINER_BY_NAME["mailboxAccounts"].partition_key_paths, ("/tenantId", "/customerId"))
        self.assertTrue(CONTAINER_BY_NAME["items"].uses_hierarchical_partition_key)
        self.assertFalse(CONTAINER_BY_NAME["emailMessages"].uses_hierarchical_partition_key)

    def test_storage_normalizes_snake_case_to_cosmos_keys(self) -> None:
        document = normalize_document_for_storage(
            "items",
            {
                "id": "item-1",
                "tenant_id": "altitude",
                "customer_id": "customer-1",
                "internal_item_number": "10001",
            },
        )

        self.assertEqual(document["tenantId"], "altitude")
        self.assertEqual(document["customerId"], GLOBAL_CUSTOMER_ID)
        self.assertEqual(document["internalItemNumber"], "10001")
        self.assertEqual(partition_key_value("items", document), ["altitude", GLOBAL_CUSTOMER_ID])

    def test_global_and_unassigned_customer_partition_defaults(self) -> None:
        routing_rule = normalize_document_for_storage(
            "routingRules",
            {
                "id": "rule-1",
                "tenantId": "altitude",
                "name": "tenant-wide rule",
            },
        )
        order_line = normalize_document_for_storage(
            "orderLines",
            {
                "id": "line-1",
                "tenantId": "altitude",
                "lineNumber": 1,
            },
        )

        self.assertEqual(routing_rule["customerId"], GLOBAL_CUSTOMER_ID)
        self.assertEqual(order_line["customerId"], UNASSIGNED_CUSTOMER_ID)

    def test_required_customer_id_is_enforced(self) -> None:
        mailbox = normalize_document_for_storage(
            "mailboxAccounts",
            {
                "id": "mailbox-1",
                "tenantId": "altitude",
                "mailboxAddress": "orders@example.com",
            },
        )

        self.assertEqual(mailbox["customerId"], GLOBAL_CUSTOMER_ID)

    def test_in_memory_repository_queries_by_customer(self) -> None:
        repo = InMemoryRepository()
        repo.upsert(
            "items",
            {
                "id": "item-1",
                "tenant_id": "altitude",
                "customer_id": "customer-1",
                "internal_item_number": "10001",
            },
        )
        repo.upsert(
            "items",
            {
                "id": "item-2",
                "tenant_id": "altitude",
                "customer_id": "customer-2",
                "internal_item_number": "20001",
            },
        )

        items = repo.query_by_customer("items", "altitude", GLOBAL_CUSTOMER_ID)
        self.assertEqual(len(items), 2)
        self.assertEqual({item["internalItemNumber"] for item in items}, {"10001", "20001"})


if __name__ == "__main__":
    unittest.main()
