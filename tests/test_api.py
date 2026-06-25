from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.customer_identification import CustomerVectorCandidate
from order_processor.models import CustomerProfile
from order_processor.api import OrderProcessorApi
from order_processor.storage import InMemoryRepository


class FakeCustomerVectorSearch:
    def __init__(self, candidates: list[CustomerVectorCandidate]) -> None:
        self.candidates = candidates

    def search(
        self,
        tenant_id: str,
        query_text: str,
        limit: int = 5,
    ) -> list[CustomerVectorCandidate]:
        return self.candidates[:limit]


class ApiTests(unittest.TestCase):
    def test_ingest_and_process_known_csv_order(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "mailboxAddress": "orders@example.com",
                "displayName": "Pilot Orders",
            }
        )["mailboxAccount"]
        repo.upsert(
            "routingRules",
            {
                "id": "rule-1",
                "tenant_id": "altitude",
                "name": "pilot csv",
                "outcome": "knownOrder",
                "priority": 1,
                "customer_id": "pilot-customer",
                "processor_profile_id": "csv-default",
                "mailbox_account_ids": [mailbox["id"]],
                "sender_domains": ["pilot.example"],
                "attachment_extensions": ["csv"],
                "required_attachment": True,
            },
        )
        api.import_items(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "rows": [
                    {
                        "internal_item_number": "10001",
                        "description": "Dog Food 25 lb",
                        "customer_item_numbers": "PILOT123",
                    }
                ],
            }
        )

        ingest = api.ingest_email(
            {
                "tenantId": "altitude",
                "mailbox": "orders@example.com",
                "mailboxAccountId": mailbox["id"],
                "messageId": "message-1",
                "sender": "buyer@pilot.example",
                "subject": "PO 12345",
                "attachments": [
                    {
                        "name": "order.csv",
                        "contentType": "text/csv",
                        "size": 1200,
                        "blobUrl": "https://storage.example/order.csv",
                    }
                ],
            }
        )
        order_run_id = ingest["orderRun"]["id"]
        processed = api.process_order(
            order_run_id,
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "processorType": "csv",
                "sourceContent": "item_number,quantity,description\nPILOT123,2,Dog Food 25 lb\n",
            },
        )

        self.assertEqual(ingest["routingDecision"]["outcome"], "knownOrder")
        self.assertEqual(ingest["emailMessage"]["customerId"], "pilot-customer")
        self.assertEqual(ingest["emailMessage"]["mailboxAccountId"], mailbox["id"])
        self.assertEqual(ingest["emailMessage"]["attachments"][0]["blobUrl"], "https://storage.example/order.csv")
        self.assertEqual(repo.get("emailMessages", ingest["emailMessage"]["id"])["routing"]["outcome"], "knownOrder")
        self.assertEqual(processed["unresolvedLineCount"], 0)
        self.assertEqual(processed["orderRun"]["status"], "completed")

    def test_ingest_does_not_use_mailbox_as_downstream_customer_scope(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "mailboxAddress": "pilot-orders@example.com",
            }
        )["mailboxAccount"]

        result = api.ingest_email(
            {
                "tenantId": "altitude",
                "mailbox": "pilot-orders@example.com",
                "mailboxAccountId": mailbox["id"],
                "messageId": "message-2",
                "sender": "buyer@example.com",
                "subject": "Order 12345",
                "attachments": [{"name": "order.xlsx"}],
            }
        )

        self.assertEqual(mailbox["customerId"], "_global")
        self.assertEqual(mailbox["settings"]["deprecatedMailboxCustomerId"], "pilot-customer")
        self.assertEqual(result["routingDecision"]["outcome"], "needsCustomerIdentification")
        self.assertIsNone(result["emailMessage"].get("customerId"))
        self.assertIsNone(result["orderRun"])

    def test_disabled_mailbox_is_ignored_without_exception_or_order_run(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "mailboxAddress": "disabled-orders@example.com",
                "enabled": False,
            }
        )["mailboxAccount"]

        result = api.ingest_email(
            {
                "tenantId": "altitude",
                "mailbox": "disabled-orders@example.com",
                "mailboxAccountId": mailbox["id"],
                "messageId": "message-disabled",
                "sender": "buyer@example.com",
                "subject": "PO 12345",
                "attachments": [{"name": "order.csv"}],
            }
        )

        self.assertEqual(result["routingDecision"]["outcome"], "ignored")
        self.assertEqual(result["emailMessage"]["status"], "ignored")
        self.assertIsNone(result["orderRun"])
        self.assertIsNone(result["exceptionTask"])

    def test_payload_customer_id_is_not_replaced_by_mailbox_configuration(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "mailboxAddress": "pilot-orders@example.com",
            }
        )["mailboxAccount"]

        result = api.ingest_email(
            {
                "tenantId": "altitude",
                "mailbox": "pilot-orders@example.com",
                "mailboxAccountId": mailbox["id"],
                "customerId": "other-customer",
                "messageId": "message-conflict",
                "sender": "buyer@example.com",
                "subject": "PO 12345",
            }
        )

        self.assertEqual(result["routingDecision"]["outcome"], "needsHumanReview")
        self.assertEqual(result["emailMessage"]["status"], "needsReview")
        self.assertEqual(result["emailMessage"]["customerId"], "other-customer")
        self.assertEqual(result["exceptionTask"]["type"], "routing")
        self.assertIsNone(result["orderRun"])

    def test_webstore_triage_rule_extracts_downstream_customer_and_email_actions(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "mailboxAddress": "orders@example.com",
            }
        )["mailboxAccount"]
        api.upsert_customer_config(
            {
                "tenantId": "altitude",
                "id": "chow-hound-4",
                "customerCode": "100029",
                "name": "CHOW HOUND #4",
                "routeNumber": "R12",
                "csrFolder": "CSR/Jane",
            }
        )
        api.upsert_routing_rule(
            {
                "tenantId": "altitude",
                "customerId": "_global",
                "id": "webstore-orders",
                "name": "Webstore orders",
                "phase": "webstoreOrder",
                "outcome": "knownOrder",
                "priority": 1,
                "processorProfileId": "csv-default",
                "mailboxAccountIds": [mailbox["id"]],
                "senderEquals": ["orders@webstore.example"],
                "knownWebstorePatterns": ["webstore order"],
                "customerCodeSource": "bodyText",
                "customerCodeRegex": r"Customer\s*Code:\s*(?<customerCode>\d+)",
                "subjectTemplate": "Cust: {customerCode} Rte: {routeNumber} - {originalSubject}",
                "categoryCsrField": "csrFolder",
                "categoryTemplates": ["CSR: {csrName}", "Status: {status}"],
                "processedMoveMode": "customerField",
                "processedMoveCustomerField": "csrFolder",
            }
        )

        result = api.ingest_email(
            {
                "tenantId": "altitude",
                "mailboxAccountId": mailbox["id"],
                "messageId": "message-webstore",
                "sender": "orders@webstore.example",
                "subject": "New webstore order",
                "bodyText": "Webstore order received. Customer Code: 100029",
            }
        )

        actions = result["routingDecision"]["matchedSignals"]["emailActions"]
        self.assertEqual(result["routingDecision"]["outcome"], "knownOrder")
        self.assertEqual(result["routingDecision"]["customerId"], "chow-hound-4")
        self.assertEqual(result["orderRun"]["customerId"], "chow-hound-4")
        self.assertEqual(actions["subject"]["value"], "Cust: 100029 Rte: R12 - New webstore order")
        self.assertIn("CSR: CSR/Jane", actions["categories"])
        self.assertEqual(actions["move"]["folderName"], "CSR/Jane")

    def test_previously_processed_subject_rule_extracts_customer_for_non_order_routing(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "mailboxAddress": "orders@example.com",
            }
        )["mailboxAccount"]
        api.upsert_customer_config(
            {
                "tenantId": "altitude",
                "id": "chow-hound-4",
                "customerCode": "100029",
                "name": "CHOW HOUND #4",
                "csrFolder": "CSR/Jane",
            }
        )
        api.upsert_routing_rule(
            {
                "tenantId": "altitude",
                "customerId": "_global",
                "id": "processed-subject",
                "name": "Already identified subject",
                "phase": "previouslyProcessed",
                "outcome": "knownCustomerNonOrder",
                "priority": 1,
                "mailboxAccountIds": [mailbox["id"]],
                "priorProcessedSubjectRegex": [r"Cust:\s*\d+.*Rte:"],
                "customerCodeSource": "subject",
                "customerCodeRegex": r"Cust:\s*(?P<customerCode>\d+)",
                "nonOrderMoveMode": "customerField",
                "nonOrderMoveCustomerField": "csrFolder",
                "categoryCsrField": "csrFolder",
                "categoryTemplates": ["CSR: {csrName}", "Review reply"],
            }
        )

        result = api.ingest_email(
            {
                "tenantId": "altitude",
                "mailboxAccountId": mailbox["id"],
                "messageId": "message-reply",
                "sender": "store@example.com",
                "subject": "RE: Cust: 100029 Rte: R12 - PO 123",
            }
        )

        actions = result["routingDecision"]["matchedSignals"]["emailActions"]
        self.assertEqual(result["routingDecision"]["outcome"], "knownCustomerNonOrder")
        self.assertEqual(result["routingDecision"]["customerId"], "chow-hound-4")
        self.assertEqual(result["emailMessage"]["customerId"], "chow-hound-4")
        self.assertIsNone(result["orderRun"])
        self.assertEqual(actions["actionKey"], "nonOrder")
        self.assertEqual(actions["move"]["folderName"], "CSR/Jane")

    def test_general_non_order_rule_identifies_customer_before_email_actions(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "mailboxAddress": "orders@example.com",
            }
        )["mailboxAccount"]
        api.upsert_customer_config(
            {
                "tenantId": "altitude",
                "id": "pilot-customer",
                "customerCode": "PILOT",
                "name": "Pilot Customer",
                "senderDomains": ["pilot.example"],
                "routeNumber": "R7",
                "csrFolder": "CSR/Pilot",
            }
        )
        api.upsert_routing_rule(
            {
                "tenantId": "altitude",
                "customerId": "_global",
                "id": "general-non-order",
                "name": "General non-order",
                "phase": "general",
                "outcome": "knownCustomerNonOrder",
                "priority": 10,
                "mailboxAccountIds": [mailbox["id"]],
                "subjectRegex": ["Question"],
                "subjectTemplate": "Cust: {customerCode} Rte: {routeNumber} - {originalSubject}",
                "categoryTemplates": ["CSR: {csrName}", "Process"],
                "nonOrderMoveMode": "customerField",
                "nonOrderMoveCustomerField": "csrFolder",
            }
        )

        result = api.ingest_email(
            {
                "tenantId": "altitude",
                "mailboxAccountId": mailbox["id"],
                "messageId": "message-general",
                "sender": "buyer@pilot.example",
                "subject": "Question about order timing",
            }
        )

        decision = result["routingDecision"]
        actions = decision["matchedSignals"]["emailActions"]
        self.assertEqual(decision["outcome"], "knownCustomerNonOrder")
        self.assertEqual(decision["customerId"], "pilot-customer")
        self.assertEqual(result["emailMessage"]["customerId"], "pilot-customer")
        self.assertEqual(actions["subject"]["value"], "Cust: PILOT Rte: R7 - Question about order timing")
        self.assertEqual(actions["move"]["folderName"], "CSR/Pilot")
        self.assertIsNone(result["orderRun"])
        self.assertIsNone(result["exceptionTask"])

    def test_order_rule_without_customer_match_creates_exception_instead_of_order_run(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "mailboxAddress": "orders@example.com",
            }
        )["mailboxAccount"]
        api.upsert_routing_rule(
            {
                "tenantId": "altitude",
                "customerId": "_global",
                "id": "generic-order",
                "name": "Generic order",
                "phase": "orderCandidate",
                "outcome": "knownOrder",
                "priority": 10,
                "processorProfileId": "csv-default",
                "mailboxAccountIds": [mailbox["id"]],
                "subjectRegex": ["Purchase Order"],
                "attachmentExtensions": ["csv"],
            }
        )

        result = api.ingest_email(
            {
                "tenantId": "altitude",
                "mailboxAccountId": mailbox["id"],
                "messageId": "message-order-unknown",
                "sender": "buyer@unknown.example",
                "subject": "Purchase Order 123",
                "attachments": [{"name": "po.csv"}],
            }
        )

        self.assertEqual(result["routingDecision"]["outcome"], "needsCustomerIdentification")
        self.assertIsNone(result["orderRun"])
        self.assertEqual(result["exceptionTask"]["type"], "routing")
        self.assertIn("customerIdentification", result["routingDecision"]["matchedSignals"])

    def test_graph_ingest_applies_routing_email_actions(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "mailboxAddress": "orders@example.com",
            }
        )["mailboxAccount"]
        api.upsert_customer_config(
            {
                "tenantId": "altitude",
                "id": "chow-hound-4",
                "customerCode": "100029",
                "name": "CHOW HOUND #4",
                "routeNumber": "R12",
                "csrFolder": "Jane",
            }
        )
        api.upsert_routing_rule(
            {
                "tenantId": "altitude",
                "customerId": "chow-hound-4",
                "id": "non-order-actions",
                "name": "Non-order actions",
                "phase": "nonOrder",
                "outcome": "knownCustomerNonOrder",
                "priority": 1,
                "mailboxAccountIds": [mailbox["id"]],
                "senderEquals": ["orders@webstore.example"],
                "subjectTemplate": "Cust: {customerCode} Rte: {routeNumber} - {originalSubject}",
                "categoryTemplates": ["CSR: {csrName}", "Process"],
                "nonOrderMoveMode": "staticFolder",
                "nonOrderMoveFolder": "CSR/Jane",
            }
        )
        patch_calls: list[tuple[str, dict[str, object]]] = []
        move_calls: list[tuple[str, dict[str, object]]] = []

        def graph_get_response(_token: str, url: str) -> dict[str, object]:
            if "/mailFolders/inbox/childFolders" in url:
                return {"value": [{"id": "folder-csr", "displayName": "CSR"}]}
            if "/mailFolders/folder-csr/childFolders" in url:
                return {"value": [{"id": "folder-jane", "displayName": "Jane"}]}
            return {"value": []}

        def graph_patch_response(_token: str, url: str, payload: dict[str, object]) -> dict[str, object]:
            patch_calls.append((url, payload))
            return {}

        def graph_post_response(_token: str, url: str, payload: dict[str, object]) -> dict[str, object]:
            move_calls.append((url, payload))
            return {"id": "moved-message-1"}

        with patch("order_processor.api.graph_get", side_effect=graph_get_response), patch(
            "order_processor.api.graph_patch",
            side_effect=graph_patch_response,
        ), patch("order_processor.api.graph_post", side_effect=graph_post_response):
            result = api._ingest_graph_message(
                "access-token",
                mailbox,
                {
                    "id": "graph-message-1",
                    "internetMessageId": "<graph-message-1@example.com>",
                    "subject": "New webstore order",
                    "from": {"emailAddress": {"address": "orders@webstore.example"}},
                    "receivedDateTime": "2026-06-24T12:00:00Z",
                    "body": {"contentType": "text", "content": "Not an attachment order."},
                    "categories": ["Existing"],
                    "hasAttachments": False,
                    "isRead": False,
                },
            )

        self.assertEqual(result["status"], "ingested")
        self.assertEqual(result["processingCategoryResult"]["status"], "applied")
        self.assertEqual(result["emailActionResult"]["status"], "applied")
        self.assertEqual(len(patch_calls), 2)
        self.assertEqual(patch_calls[0][1], {"categories": ["Existing", "Processing"]})
        self.assertEqual(
            patch_calls[1][1],
            {
                "categories": ["CSR: Jane", "Process"],
                "subject": "Cust: 100029 Rte: R12 - New webstore order",
            },
        )
        self.assertEqual(len(move_calls), 1)
        self.assertTrue(move_calls[0][0].endswith("/messages/graph-message-1/move"))
        self.assertEqual(move_calls[0][1], {"destinationId": "folder-jane"})

    def test_graph_email_actions_can_be_explicitly_disabled(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        result = api._apply_graph_email_actions(
            "access-token",
            "orders@example.com",
            "graph-message-1",
            {
                "emailMessage": {"id": "email-1", "tenantId": "altitude"},
                "routingDecision": {
                    "matchedSignals": {
                        "emailActions": {
                            "productionActionsEnabled": False,
                            "subject": {"value": "Updated"},
                            "categories": ["Process"],
                        }
                    }
                },
            },
            [],
        )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "production email actions disabled")

    def test_graph_email_actions_move_to_existing_root_csr_folder(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        get_calls: list[str] = []
        post_calls: list[tuple[str, dict[str, object]]] = []

        def graph_get_response(_token: str, url: str) -> dict[str, object]:
            get_calls.append(url)
            if "/mailFolders?" in url:
                return {"value": [{"id": "folder-jane", "displayName": "Jane"}]}
            return {"value": []}

        def graph_post_response(_token: str, url: str, payload: dict[str, object]) -> dict[str, object]:
            post_calls.append((url, payload))
            return {"id": "moved-message-1"}

        with patch("order_processor.api.graph_get", side_effect=graph_get_response), patch(
            "order_processor.api.graph_post",
            side_effect=graph_post_response,
        ):
            result = api._apply_graph_email_actions(
                "access-token",
                "orders@example.com",
                "graph-message-1",
                {
                    "emailMessage": {"id": "email-1", "tenantId": "altitude"},
                    "routingDecision": {
                        "matchedSignals": {
                            "emailActions": {
                                "move": {"enabled": True, "folderName": "Jane"},
                            }
                        }
                    },
                },
                [],
            )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(len(post_calls), 1)
        self.assertTrue(post_calls[0][0].endswith("/messages/graph-message-1/move"))
        self.assertEqual(post_calls[0][1], {"destinationId": "folder-jane"})
        self.assertTrue(any("/mailFolders?" in url for url in get_calls))
        self.assertFalse(any("/mailFolders/inbox/childFolders" in url for url in get_calls))

    def test_graph_email_actions_create_missing_root_csr_folder_before_move(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        create_calls: list[tuple[str, dict[str, object]]] = []
        move_calls: list[tuple[str, dict[str, object]]] = []

        def graph_post_response(_token: str, url: str, payload: dict[str, object]) -> dict[str, object]:
            if url.endswith("/mailFolders"):
                create_calls.append((url, payload))
                return {"id": "folder-jane", "displayName": payload.get("displayName", "")}
            move_calls.append((url, payload))
            return {"id": "moved-message-1"}

        with patch("order_processor.api.graph_get", return_value={"value": []}), patch(
            "order_processor.api.graph_post",
            side_effect=graph_post_response,
        ):
            result = api._apply_graph_email_actions(
                "access-token",
                "orders@example.com",
                "graph-message-1",
                {
                    "emailMessage": {"id": "email-1", "tenantId": "altitude"},
                    "routingDecision": {
                        "matchedSignals": {
                            "emailActions": {
                                "move": {"enabled": True, "folderName": "Jane"},
                            }
                        }
                    },
                },
                [],
            )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(len(create_calls), 1)
        self.assertTrue(create_calls[0][0].endswith("/mailFolders"))
        self.assertEqual(create_calls[0][1], {"displayName": "Jane"})
        self.assertEqual(len(move_calls), 1)
        self.assertTrue(move_calls[0][0].endswith("/messages/graph-message-1/move"))
        self.assertEqual(move_calls[0][1], {"destinationId": "folder-jane"})

    def test_unknown_mailbox_account_id_creates_human_review_exception(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)

        result = api.ingest_email(
            {
                "tenantId": "altitude",
                "mailbox": "unknown@example.com",
                "mailboxAccountId": "missing-mailbox",
                "messageId": "message-missing-mailbox",
                "sender": "buyer@example.com",
                "subject": "PO 12345",
            }
        )

        self.assertEqual(result["routingDecision"]["outcome"], "needsHumanReview")
        self.assertEqual(result["routingDecision"]["mailboxAccountId"], "missing-mailbox")
        self.assertEqual(result["exceptionTask"]["context"]["mailboxAccountId"], "missing-mailbox")

    def test_identify_customer_updates_stored_email_and_order_run(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        repo.upsert(
            "customers",
            {
                "id": "pilot-customer",
                "tenantId": "altitude",
                "customerCode": "PILOT",
                "name": "Pilot Customer",
            },
        )
        repo.upsert(
            "emailMessages",
            {
                "id": "email-identified",
                "tenantId": "altitude",
                "mailbox": "orders@example.com",
                "messageId": "message-identified",
                "sender": "buyer@example.com",
                "subject": "Customer: PILOT PO 12345",
                "receivedAt": "2026-06-19T12:00:00Z",
                "status": "needsReview",
                "orderRunId": "order-run-identified",
            },
        )
        repo.upsert(
            "orderRuns",
            {
                "id": "order-run-identified",
                "tenantId": "altitude",
                "emailMessageId": "email-identified",
                "status": "received",
            },
        )

        result = api.identify_customer(
            {
                "emailMessage": {
                    "id": "email-identified",
                    "tenantId": "altitude",
                    "mailbox": "orders@example.com",
                    "messageId": "message-identified",
                    "sender": "buyer@example.com",
                    "subject": "Customer: PILOT PO 12345",
                }
            }
        )

        stored_email = repo.get("emailMessages", "email-identified")
        stored_order = repo.get("orderRuns", "order-run-identified")
        self.assertEqual(result["result"]["status"], "matched")
        self.assertEqual(result["exceptionTask"], None)
        self.assertEqual(stored_email["customerId"], "pilot-customer")
        self.assertEqual(stored_email["customerIdentification"]["matchMethod"], "customerCode")
        self.assertEqual(stored_order["customerId"], "pilot-customer")

    def test_customer_identification_hard_rule_matches_sender_email(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        api.upsert_customer_config(
            {
                "tenantId": "altitude",
                "id": "pilot-customer",
                "customerCode": "PILOT",
                "name": "Pilot Customer",
            }
        )
        rule = api.upsert_customer_identification_rule(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "aliasType": "senderEmail",
                "value": "buyer@example.com",
            }
        )["customerIdentificationRule"]

        result = api.identify_customer(
            {
                "emailMessage": {
                    "id": "email-sender-rule",
                    "tenantId": "altitude",
                    "mailbox": "orders@example.com",
                    "messageId": "message-sender-rule",
                    "sender": "Buyer <buyer@example.com>",
                    "subject": "Please process this order",
                }
            }
        )

        self.assertEqual(rule["normalizedValue"], "buyer@example.com")
        self.assertEqual(result["result"]["status"], "matched")
        self.assertEqual(result["result"]["customerId"], "pilot-customer")
        self.assertEqual(result["result"]["matchMethod"], "senderEmail")

    def test_identify_customer_uses_aliases_and_creates_exception_for_ambiguous_match(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        for customer_id in ["customer-1", "customer-2"]:
            repo.upsert(
                "customers",
                {
                    "id": customer_id,
                    "tenantId": "altitude",
                    "customerCode": customer_id.upper(),
                    "name": customer_id,
                },
            )
            repo.upsert(
                "customerAliases",
                {
                    "id": f"alias-{customer_id}",
                    "tenantId": "altitude",
                    "customerId": customer_id,
                    "aliasType": "routeNumber",
                    "value": "10",
                    "normalizedValue": "10",
                },
            )

        result = api.identify_customer(
            {
                "emailMessage": {
                    "id": "email-ambiguous",
                    "tenantId": "altitude",
                    "mailbox": "orders@example.com",
                    "messageId": "message-ambiguous",
                    "sender": "buyer@example.com",
                    "subject": "Route 10 order",
                }
            }
        )

        self.assertEqual(result["result"]["status"], "possibleMatch")
        self.assertEqual(result["exceptionTask"]["type"], "customerIdentification")
        self.assertEqual(len(result["result"]["candidates"]), 2)

    def test_identify_customer_vector_low_confidence_creates_exception(self) -> None:
        repo = InMemoryRepository()
        customer = CustomerProfile(
            id="pilot-customer",
            tenant_id="altitude",
            customer_code="PILOT",
            name="Pilot Customer",
        )
        api = OrderProcessorApi(
            repo,
            customer_vector_search=FakeCustomerVectorSearch(
                [CustomerVectorCandidate(customer=customer, confidence=0.7)]
            ),
        )
        repo.upsert(
            "customers",
            {
                "id": "pilot-customer",
                "tenantId": "altitude",
                "customerCode": "PILOT",
                "name": "Pilot Customer",
            },
        )

        result = api.identify_customer(
            {
                "emailMessage": {
                    "id": "email-low-confidence",
                    "tenantId": "altitude",
                    "mailbox": "orders@example.com",
                    "messageId": "message-low-confidence",
                    "sender": "buyer@example.com",
                    "subject": "Please process this order",
                }
            }
        )

        self.assertEqual(result["result"]["status"], "possibleMatch")
        self.assertEqual(result["result"]["matchMethod"], "cosmosVectorSearch")
        self.assertIsNotNone(result["exceptionTask"])
        self.assertEqual(result["exceptionTask"]["context"]["result"]["confidence"], 0.7)

    def test_mailbox_and_console_configuration_endpoints(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)

        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "mailboxAddress": "PilotOrders@Example.com",
                "displayName": "Pilot Orders",
                "connectionId": "m365-pilot",
            }
        )["mailboxAccount"]
        tenant = api.upsert_tenant_config(
            {
                "tenantId": "altitude",
                "name": "Altitude Distribution",
                "environment": "prod",
            }
        )["tenant"]
        connection_test = api.test_mailbox_connection(mailbox["id"], {"requestedBy": "connect@focuseautomate.com"})
        console_user = api.upsert_console_user(
            {
                "tenantId": "altitude",
                "email": "connect@focuseautomate.com",
                "roles": [],
            }
        )["consoleUser"]
        assignment = api.assign_customer_user(
            "pilot-customer",
            {
                "tenantId": "altitude",
                "email": "buyer@example.com",
                "roles": ["orderViewer"],
            },
        )["customerUserAssignment"]

        self.assertEqual(mailbox["mailboxAddress"], "pilotorders@example.com")
        self.assertEqual(mailbox["customerId"], "_global")
        self.assertEqual(tenant["name"], "Altitude Distribution")
        self.assertEqual(connection_test["connectionStatus"]["status"], "needsConsent")
        self.assertIn("platformAdmin", console_user["roles"])
        self.assertEqual(assignment["customerId"], "pilot-customer")
        self.assertEqual(assignment["roles"], ["orderViewer"])


if __name__ == "__main__":
    unittest.main()
