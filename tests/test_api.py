from __future__ import annotations

import sys
import unittest
from pathlib import Path

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
                "customerId": "pilot-customer",
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

    def test_ingest_uses_mailbox_customer_scope_and_filters_other_customer_rules(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "mailboxAddress": "pilot-orders@example.com",
            }
        )["mailboxAccount"]
        repo.upsert(
            "routingRules",
            {
                "id": "wrong-customer-rule",
                "tenantId": "altitude",
                "customerId": "other-customer",
                "name": "other customer catch all",
                "outcome": "knownOrder",
                "priority": 1,
            },
        )
        repo.upsert(
            "routingRules",
            {
                "id": "pilot-rule",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "name": "pilot xlsx",
                "outcome": "knownOrder",
                "priority": 2,
                "attachmentExtensions": ["xlsx"],
                "requiredAttachment": True,
            },
        )

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

        self.assertEqual(result["routingDecision"]["ruleId"], "pilot-rule")
        self.assertEqual(result["routingDecision"]["customerId"], "pilot-customer")
        self.assertIsNotNone(result["orderRun"])

    def test_disabled_mailbox_is_ignored_without_exception_or_order_run(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
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

    def test_mailbox_customer_conflict_creates_human_review_exception(self) -> None:
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
        self.assertEqual(result["emailMessage"]["customerId"], "pilot-customer")
        self.assertEqual(result["exceptionTask"]["type"], "routing")
        self.assertIsNone(result["orderRun"])

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
                "customerId": "pilot-customer",
                "mailboxAddress": "PilotOrders@Example.com",
                "displayName": "Pilot Orders",
                "connectionId": "m365-pilot",
            }
        )["mailboxAccount"]
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
        self.assertEqual(connection_test["connectionStatus"]["status"], "notTested")
        self.assertIn("platformAdmin", console_user["roles"])
        self.assertEqual(assignment["customerId"], "pilot-customer")
        self.assertEqual(assignment["roles"], ["orderViewer"])


if __name__ == "__main__":
    unittest.main()
