from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.api import OrderProcessorApi
from order_processor.microsoft_graph import InMemorySecretStore, sign_state
from order_processor.models import EmailMessage, MatchStatus, OrderLine, OrderRun, ProcessingStatus, to_dict, utc_now
from order_processor.output_generation import InMemoryOutputArtifactStore
from order_processor.storage import InMemoryRepository


def _easy_auth_header(email: str, oid: str = "oid-1") -> str:
    principal = {
        "auth_typ": "aad",
        "name": email,
        "claims": [
            {"typ": "preferred_username", "val": email},
            {"typ": "email", "val": email},
            {"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier", "val": oid},
        ],
    }
    return base64.b64encode(json.dumps(principal).encode("utf-8")).decode("ascii")


class ConsoleBackendTests(unittest.TestCase):
    def _api(self) -> tuple[OrderProcessorApi, InMemoryRepository, InMemoryOutputArtifactStore]:
        repo = InMemoryRepository()
        store = InMemoryOutputArtifactStore()
        api = OrderProcessorApi(repo, output_artifact_store=store, secret_store=InMemorySecretStore())
        return api, repo, store

    def test_console_session_bootstraps_only_connect_admin(self) -> None:
        api, repo, _ = self._api()

        missing = api.console_session({"tenantId": "altitude", "email": "user@example.com"})
        admin = api.console_session(
            {
                "tenantId": "altitude",
                "headers": {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")},
            }
        )

        self.assertFalse(missing["authorized"])
        self.assertEqual(missing["reason"], "consoleUserNotAssigned")
        self.assertTrue(admin["authorized"])
        self.assertTrue(admin["isPlatformAdmin"])
        self.assertEqual(repo.query_by_tenant("consoleUsers", "altitude")[0]["email"], "connect@focuseautomate.com")

    def test_console_mutations_require_admin_and_prefer_easy_auth_identity(self) -> None:
        api, repo, _ = self._api()
        admin_headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}

        created_user = api.console_upsert_console_user(
            {
                "tenantId": "altitude",
                "headers": admin_headers,
                "email": "buyer@example.com",
                "roles": ["customerUser"],
            }
        )
        denied = api.console_upsert_customer_config(
            {
                "tenantId": "altitude",
                "principalEmail": "buyer@example.com",
                "id": "pilot-customer",
                "customerCode": "PILOT",
                "name": "Pilot",
            }
        )
        created_customer = api.console_upsert_customer_config(
            {
                "tenantId": "altitude",
                "headers": admin_headers,
                "id": "pilot-customer",
                "customerCode": "PILOT",
                "name": "Pilot",
            }
        )

        self.assertEqual(created_user["consoleUser"]["email"], "buyer@example.com")
        self.assertEqual(created_user["session"]["consoleUser"]["email"], "connect@focuseautomate.com")
        self.assertEqual(denied["error"], "forbidden")
        self.assertEqual(created_customer["customer"]["id"], "pilot-customer")
        self.assertEqual(repo.get("customers", "pilot-customer")["customerCode"], "PILOT")

    def test_console_dashboard_filters_customer_scoped_views(self) -> None:
        api, repo, _ = self._api()
        api.upsert_console_user({"tenantId": "altitude", "email": "buyer@example.com", "roles": ["customerUser"]})
        api.upsert_console_user({"tenantId": "altitude", "email": "tenant-admin@example.com", "roles": ["tenantAdmin"]})
        api.assign_customer_user(
            "pilot-customer",
            {"tenantId": "altitude", "email": "buyer@example.com", "roles": ["orderViewer", "exceptionResolver"]},
        )
        api.upsert_customer_config(
            {"tenantId": "altitude", "id": "pilot-customer", "customerCode": "PILOT", "name": "Pilot"}
        )
        api.upsert_customer_config(
            {"tenantId": "altitude", "id": "other-customer", "customerCode": "OTHER", "name": "Other"}
        )
        api.upsert_mailbox({"tenantId": "altitude", "mailboxAddress": "orders@example.com"})
        api.upsert_routing_rule(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "name": "pilot csv",
                "outcome": "knownOrder",
                "attachmentExtensions": ["csv"],
            }
        )
        repo.upsert(
            "orderRuns",
            to_dict(
                OrderRun(
                    id="pilot-run",
                    tenant_id="altitude",
                    email_message_id="email-1",
                    customer_id="pilot-customer",
                    status=ProcessingStatus.NEEDS_REVIEW,
                    lines=[OrderLine(line_number=1, validation_status=MatchStatus.UNRESOLVED)],
                )
            ),
        )
        repo.upsert(
            "orderRuns",
            to_dict(
                OrderRun(
                    id="other-run",
                    tenant_id="altitude",
                    email_message_id="email-2",
                    customer_id="other-customer",
                    status=ProcessingStatus.COMPLETED,
                )
            ),
        )
        api._create_exception(tenant_id="altitude", task_type="itemValidation", prompt="Resolve", order_run_id="pilot-run")

        dashboard = api.console_dashboard({"tenantId": "altitude", "email": "buyer@example.com"})
        tenant_dashboard = api.console_dashboard({"tenantId": "altitude", "email": "tenant-admin@example.com"})

        self.assertTrue(dashboard["session"]["authorized"])
        self.assertEqual([customer["id"] for customer in dashboard["customers"]], ["pilot-customer"])
        self.assertEqual([mailbox["customerId"] for mailbox in dashboard["mailboxes"]], ["_global"])
        self.assertEqual([run["id"] for run in dashboard["activeRuns"]], ["pilot-run"])
        self.assertEqual(dashboard["summary"]["openExceptionCount"], 1)
        self.assertEqual(dashboard["summary"]["unresolvedLineCount"], 1)
        self.assertEqual({customer["id"] for customer in tenant_dashboard["customers"]}, {"pilot-customer", "other-customer"})
        self.assertIn("distributorCustomers", tenant_dashboard)
        self.assertEqual(tenant_dashboard["tenant"]["tenantId"], "altitude")

    def test_console_monitor_sections_include_email_lifecycle_rows(self) -> None:
        api, repo, _ = self._api()
        headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}
        api.upsert_customer_config(
            {
                "tenantId": "altitude",
                "id": "pilot-customer",
                "customerCode": "PILOT",
                "name": "Pilot",
                "csrFolder": "Jane",
                "csrEmail": "jane@example.com",
            }
        )
        repo.upsert(
            "emailMessages",
            {
                "id": "email-active",
                "tenantId": "altitude",
                "mailbox": "orders@example.com",
                "messageId": "message-active",
                "sender": "store@example.com",
                "subject": "Question",
                "receivedAt": "2026-06-24T12:00:00Z",
                "status": "processing",
                "customerId": "pilot-customer",
                "routing": {"outcome": "knownCustomerNonOrder", "matchedSignals": {"triagePhase": "nonOrder"}},
                "source": {"processing": {"pathway": "nonOrder", "stage": "identifyingCustomer"}},
            },
        )
        repo.upsert(
            "emailMessages",
            {
                "id": "email-webstore",
                "tenantId": "altitude",
                "mailbox": "orders@example.com",
                "messageId": "message-webstore",
                "sender": "webstore@example.com",
                "subject": "Cust: PILOT - Web order",
                "receivedAt": "2026-06-24T12:05:00Z",
                "status": "completed",
                "customerId": "pilot-customer",
                "categories": ["CSR: Jane"],
                "routing": {"outcome": "knownCustomerNonOrder", "matchedSignals": {"triagePhase": "webstoreOrder"}},
                "source": {
                    "webLink": "https://outlook.office.com/mail/id/1",
                    "graphEmailActions": {
                        "status": "applied",
                        "patched": {"categories": ["CSR: Jane"]},
                        "applied": [{"action": "move", "folderName": "Jane"}],
                    },
                },
            },
        )
        api._create_exception(
            tenant_id="altitude",
            task_type="routing",
            prompt="Resolve customer",
            email_message_id="email-active",
            customer_id="pilot-customer",
        )

        dashboard = api.console_dashboard({"tenantId": "altitude", "headers": headers, "view": "monitor"})
        monitor = dashboard["monitor"]

        self.assertEqual(dashboard["dashboardView"], "monitor")
        self.assertEqual(dashboard["items"], [])
        self.assertEqual(len(monitor["active"]), 0)
        self.assertEqual(len(monitor["exceptions"]), 1)
        self.assertEqual(monitor["exceptions"][0]["customerCode"], "PILOT")
        self.assertEqual(len(monitor["webstoreOrders"]), 1)
        self.assertEqual(monitor["webstoreOrders"][0]["movedTo"], "Jane")
        self.assertEqual(monitor["webstoreOrders"][0]["emailUrl"], "https://outlook.office.com/mail/id/1")

    def test_console_dashboard_lists_distributor_customers_and_read_only_import_lists(self) -> None:
        api, repo, _ = self._api()
        admin_headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}
        api.upsert_tenant_config({"tenantId": "altitude", "name": "Altitude Distribution", "environment": "prod"})
        api.upsert_tenant_config({"tenantId": "beta", "name": "Beta Distribution", "environment": "test"})
        api.upsert_customer_config(
            {"tenantId": "altitude", "id": "store-100", "customerCode": "100", "name": "Store 100"}
        )
        repo.upsert(
            "items",
            {
                "id": "item-100",
                "tenantId": "altitude",
                "customerId": "store-100",
                "internalItemNumber": "10001",
                "description": "Test Item",
                "lastImportedAt": "2026-06-20T10:00:00Z",
            },
        )

        dashboard = api.console_dashboard({"tenantId": "altitude", "headers": admin_headers})

        self.assertEqual(
            [tenant["tenantId"] for tenant in dashboard["distributorCustomers"]],
            ["altitude", "beta"],
        )
        self.assertEqual([customer["id"] for customer in dashboard["customers"]], ["store-100"])
        self.assertEqual([item["id"] for item in dashboard["items"]], ["item-100"])
        self.assertEqual(dashboard["importTargets"]["tenantId"], "altitude")
        self.assertEqual(dashboard["importTargets"]["customerList"]["containerName"], "customers")
        self.assertEqual(dashboard["importTargets"]["customerList"]["partitionKeyValue"], "altitude")
        self.assertEqual(dashboard["importTargets"]["customerList"]["apiPath"], "/imports/customers")
        self.assertEqual(dashboard["importTargets"]["itemList"]["containerName"], "items")
        self.assertEqual(dashboard["importTargets"]["itemList"]["partitionKeyValue"][0], "altitude")
        self.assertEqual(dashboard["importTargets"]["itemList"]["partitionKeyValue"][1], "_global")
        self.assertEqual(dashboard["importTargets"]["itemList"]["apiPath"], "/imports/items")
        self.assertNotIn("customerCode", dashboard["importTargets"]["itemList"]["minimumBody"])
        self.assertEqual(
            dashboard["importTargets"]["itemList"]["minimumBody"]["rows"][0]["alt_parts_combined"][0]["alt_part"],
            "031865BRN4R",
        )

    def test_console_dashboard_omits_customer_and_item_embeddings(self) -> None:
        api, repo, _ = self._api()
        admin_headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}
        repo.upsert(
            "customers",
            {
                "id": "pilot-customer",
                "tenantId": "altitude",
                "customerCode": "PILOT",
                "name": "Pilot",
                "embedding": [0.1, 0.2],
                "rawSource": {"cust_code": "PILOT"},
            },
        )
        repo.upsert(
            "items",
            {
                "id": "item-1",
                "tenantId": "altitude",
                "customerId": "_global",
                "internalItemNumber": "10001",
                "description": "Dog Food",
                "embedding": [0.3, 0.4],
                "rawSource": {"part_code": "10001"},
            },
        )

        dashboard = api.console_dashboard({"tenantId": "altitude", "headers": admin_headers})

        self.assertEqual(dashboard["customers"][0]["rawSource"]["custCode"], "PILOT")
        self.assertEqual(dashboard["items"][0]["rawSource"]["partCode"], "10001")
        self.assertNotIn("embedding", dashboard["customers"][0])
        self.assertNotIn("embedding", dashboard["items"][0])

    def test_platform_admin_can_manage_new_distributor_tenant_after_create(self) -> None:
        api, repo, _ = self._api()
        api.upsert_console_user(
            {
                "tenantId": "default",
                "email": "admin@example.com",
                "displayName": "Admin",
                "roles": ["platformAdmin"],
            }
        )

        created = api.console_upsert_tenant_config(
            {
                "tenantId": "default",
                "email": "admin@example.com",
                "targetTenantId": "test-distributor",
                "name": "Test Distributor",
            }
        )
        mailbox = api.console_upsert_mailbox(
            {
                "tenantId": "test-distributor",
                "email": "admin@example.com",
                "mailboxAddress": "orders@example.com",
                "displayName": "Orders",
                "connectionId": "m365-test",
            }
        )

        self.assertEqual(created["tenant"]["tenantId"], "test-distributor")
        self.assertEqual(created["aiCostSource"]["provider"], "microsoft")
        self.assertEqual(created["aiCostSource"]["projectTagKey"], "project")
        self.assertIsNotNone(repo.get("aiCostSources", created["aiCostSource"]["id"]))
        self.assertTrue(mailbox["session"]["authorized"])
        self.assertEqual(mailbox["mailboxAccount"]["tenantId"], "test-distributor")
        self.assertEqual(mailbox["mailboxAccount"]["mailboxAddress"], "orders@example.com")
        cloned_admin = next(
            user
            for user in repo.query_by_tenant("consoleUsers", "test-distributor")
            if user["email"] == "admin@example.com"
        )
        self.assertEqual(cloned_admin["roles"], ["platformAdmin"])

    def test_ai_cost_events_are_grouped_for_console_and_billing_api(self) -> None:
        api, _, _ = self._api()
        admin_headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}
        api.upsert_tenant_config({"tenantId": "altitude", "name": "Altitude Distribution"})
        api.record_ai_cost_event(
            {
                "tenantId": "altitude",
                "customerId": "store-100",
                "provider": "microsoft",
                "processorType": "customerIdentification",
                "operationType": "foundryConsensus",
                "usage": {"inputTokens": 1200, "outputTokens": 90},
                "costUsd": 0.012345,
            }
        )
        api.record_ai_cost_event(
            {
                "tenantId": "altitude",
                "customerId": "store-100",
                "provider": "microsoft",
                "processorType": "pdf",
                "operationType": "azureDocumentIntelligence",
                "runCount": 3,
                "usage": {"documentPages": 9},
                "costUsd": 1.25,
            }
        )

        summary = api.cost_summary({"tenantId": "altitude", "period": "currentMonth"})
        console = api.console_data("costs", {"tenantId": "altitude", "headers": admin_headers})

        self.assertEqual(summary["totalRunCount"], 4)
        self.assertEqual(summary["totalCostUsd"], 1.262345)
        self.assertEqual({row["processorType"] for row in summary["rows"]}, {"customerIdentification", "pdf"})
        self.assertEqual(console["section"], "costs")
        self.assertEqual(console["costs"]["totalRunCount"], 4)
        self.assertEqual(console["costSources"][0]["provider"], "microsoft")

    def test_exception_can_be_disregarded_as_manual_override(self) -> None:
        api, repo, _ = self._api()
        repo.upsert(
            "emailMessages",
            {
                "id": "email-1",
                "tenantId": "altitude",
                "mailbox": "orders@example.com",
                "messageId": "graph-1",
                "sender": "store@example.com",
                "subject": "Needs review",
                "receivedAt": "2026-06-24T12:00:00Z",
                "status": "needsReview",
            },
        )
        task = api._create_exception(
            tenant_id="altitude",
            task_type="customerIdentification",
            prompt="Resolve customer",
            email_message_id="email-1",
        )

        result = api.resolve_exception(
            task["id"],
            {
                "tenantId": "altitude",
                "actor": "admin@example.com",
                "resolution": {"action": "disregard", "notes": "Handled manually"},
            },
        )

        stored_task = repo.get("exceptionTasks", task["id"])
        stored_email = repo.get("emailMessages", "email-1")
        self.assertEqual(result["resolutionResult"]["status"], "manualOverride")
        self.assertTrue(result["resolutionResult"]["manualOverride"])
        self.assertEqual(stored_task["status"], "resolved")
        self.assertEqual(stored_email["status"], ProcessingStatus.COMPLETED.value)
        self.assertEqual(stored_email["source"]["manualOverride"]["notes"], "Handled manually")

    def test_console_microsoft_auth_start_requires_configuration(self) -> None:
        api, _, _ = self._api()
        admin_headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}
        mailbox = api.upsert_mailbox(
            {"tenantId": "altitude", "mailboxAddress": "orders@example.com", "connectionId": "m365-orders"}
        )["mailboxAccount"]

        with patch.dict(os.environ, {"ORDER_PROCESSOR_MICROSOFT_AUTH_CLIENT_ID": ""}, clear=False):
            result = api.console_start_microsoft_auth(
                {
                    "tenantId": "altitude",
                    "headers": admin_headers,
                    "mailboxAccountId": mailbox["id"],
                    "redirectUri": "https://console.example.com/auth/microsoft/callback",
                }
            )

        self.assertEqual(result["error"], "microsoftAuthNotConfigured")

    def test_console_microsoft_auth_start_creates_authorization_url_and_connection(self) -> None:
        api, repo, _ = self._api()
        admin_headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}
        mailbox = api.upsert_mailbox(
            {"tenantId": "altitude", "mailboxAddress": "orders@example.com", "connectionId": "m365-orders"}
        )["mailboxAccount"]

        with patch.dict(
            os.environ,
            {
                "ORDER_PROCESSOR_MICROSOFT_AUTH_CLIENT_ID": "client-id",
                "ORDER_PROCESSOR_MICROSOFT_AUTH_TENANT_ID": "organizations",
                "ORDER_PROCESSOR_MICROSOFT_AUTH_STATE_SECRET": "state-secret",
            },
            clear=False,
        ):
            result = api.console_start_microsoft_auth(
                {
                    "tenantId": "altitude",
                    "headers": admin_headers,
                    "mailboxAccountId": mailbox["id"],
                    "mailboxAddress": "orders@example.com",
                    "connectionId": "m365-orders",
                    "authorizedUserEmail": "mailbox-admin@example.com",
                    "redirectUri": "https://console.example.com/auth/microsoft/callback",
                }
            )

        self.assertIn("https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize", result["authorizationUrl"])
        self.assertIn("Mail.ReadWrite.Shared", result["authorizationUrl"])
        self.assertIn("prompt=select_account", result["authorizationUrl"])
        self.assertIn("login_hint=mailbox-admin%40example.com", result["authorizationUrl"])
        stored = repo.get("microsoftAuthConnections", "m365-orders")
        self.assertEqual(stored["status"], "needsConsent")
        self.assertEqual(stored["metadata"]["mailboxAddress"], "orders@example.com")
        self.assertEqual(stored["ownerEmail"], "mailbox-admin@example.com")

    def test_console_microsoft_auth_callback_stores_tokens_and_updates_mailbox(self) -> None:
        api, repo, _ = self._api()
        mailbox = api.upsert_mailbox(
            {"tenantId": "altitude", "mailboxAddress": "orders@example.com", "connectionId": "m365-orders"}
        )["mailboxAccount"]
        repo.upsert(
            "microsoftAuthConnections",
            {
                "id": "m365-orders",
                "tenantId": "altitude",
                "customerId": "_global",
                "provider": "microsoft365",
                "displayName": "Orders",
                "status": "needsConsent",
                "metadata": {},
            },
        )
        state = sign_state(
            {
                "tenantId": "altitude",
                "connectionId": "m365-orders",
                "mailboxAccountId": mailbox["id"],
                "mailboxAddress": "orders@example.com",
                "requestedBy": "mailbox-admin@example.com",
                "initiatedBy": "connect@focuseautomate.com",
                "authorizedUserEmail": "mailbox-admin@example.com",
                "redirectUri": "https://console.example.com/auth/microsoft/callback",
            },
            "state-secret",
        )

        with patch.dict(
            os.environ,
            {
                "ORDER_PROCESSOR_MICROSOFT_AUTH_CLIENT_ID": "client-id",
                "ORDER_PROCESSOR_MICROSOFT_AUTH_CLIENT_SECRET": "client-secret",
                "ORDER_PROCESSOR_MICROSOFT_AUTH_TENANT_ID": "organizations",
                "ORDER_PROCESSOR_MICROSOFT_AUTH_STATE_SECRET": "state-secret",
            },
            clear=False,
        ), patch(
            "order_processor.api.exchange_authorization_code",
            return_value={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_in": 3600,
                "scope": "openid profile offline_access User.Read Mail.ReadWrite.Shared Mail.Send.Shared",
            },
        ), patch(
            "order_processor.api.test_shared_mailbox_access",
            return_value={"canAccess": True, "status": "active", "checkedAt": "2026-06-22T12:00:00Z"},
        ), patch(
            "order_processor.api.graph_get",
            return_value={
                "id": "user-id",
                "displayName": "Mailbox Admin",
                "userPrincipalName": "mailbox-admin@example.com",
                "mail": "mailbox-admin@example.com",
            },
        ):
            result = api.console_complete_microsoft_auth({"state": state, "code": "auth-code"})

        self.assertEqual(result["microsoftAuthConnection"]["status"], "active")
        self.assertEqual(result["microsoftAuthConnection"]["ownerEmail"], "mailbox-admin@example.com")
        self.assertEqual(result["mailboxAccount"]["permissionStatus"], "active")
        self.assertEqual(repo.get("mailboxAccounts", mailbox["id"])["settings"]["authorizedBy"], "mailbox-admin@example.com")
        self.assertEqual(api.secret_store.get_secret("msgraph-m365-orders-refresh-token"), "refresh-token")
        self.assertEqual(api.secret_store.get_secret("msgraph-m365-orders-access-token"), "access-token")

    def test_mailbox_poll_ingests_graph_message_and_skips_duplicates(self) -> None:
        api, repo, _ = self._api()
        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "mailboxAddress": "orders@example.com",
                "connectionId": "m365-orders",
            }
        )["mailboxAccount"]
        repo.upsert(
            "microsoftAuthConnections",
            {
                "id": "m365-orders",
                "tenantId": "altitude",
                "customerId": "_global",
                "provider": "microsoft365",
                "displayName": "Orders",
                "status": "active",
                "keyVaultSecretNames": {"refreshToken": "refresh-secret"},
                "metadata": {},
            },
        )
        api.secret_store.set_secret("refresh-secret", "refresh-token")
        api.upsert_customer_config(
            {
                "tenantId": "altitude",
                "id": "frontier-102598",
                "customerCode": "102598",
                "name": "Frontier",
            }
        )
        api.upsert_processor_profile(
            {
                "tenantId": "altitude",
                "customerId": "_global",
                "id": "csv-default",
                "name": "CSV",
                "processorType": "csv",
            }
        )
        api.upsert_routing_rule(
            {
                "tenantId": "altitude",
                "customerId": "_global",
                "name": "Webstore orders",
                "phase": "webstoreOrder",
                "outcome": "knownOrder",
                "mailboxAccountIds": [mailbox["id"]],
                "subjectRegex": ["Order"],
                "customerCodeSource": "subject",
                "customerCodeRegex": r"Order\s*#\s*(?P<customerCode>\d+)",
                "processorProfileId": "csv-default",
            }
        )
        csv_body = base64.b64encode(b"item_number,quantity,description\nSKU-1,2,Test Item\n").decode("ascii")

        def graph_response(_token: str, url: str) -> dict[str, object]:
            if url.endswith("/attachments"):
                return {
                    "value": [
                        {
                            "id": "attachment-1",
                            "name": "order.csv",
                            "contentType": "text/csv",
                            "size": 48,
                            "isInline": False,
                            "contentBytes": csv_body,
                        }
                    ]
                }
            return {
                "value": [
                    {
                        "id": "graph-message-1",
                        "internetMessageId": "<message-1@example.com>",
                        "subject": "Frontier Distributing. Purchase Receipt for Order #  102598",
                        "from": {"emailAddress": {"address": "buyer@example.com"}},
                        "receivedDateTime": "2026-06-23T12:00:00Z",
                        "body": {"contentType": "text", "content": "Order attached."},
                        "hasAttachments": True,
                        "isRead": False,
                    }
                ]
            }

        with patch.dict(os.environ, {"ORDER_PROCESSOR_MICROSOFT_AUTH_CLIENT_SECRET": "client-secret"}, clear=False), patch(
            "order_processor.api.refresh_access_token",
            return_value={"access_token": "access-token", "refresh_token": "next-refresh-token"},
        ), patch("order_processor.api.graph_get", side_effect=graph_response):
            first = api.poll_mailboxes({"tenantId": "altitude", "limit": 5})
            second = api.poll_mailboxes({"tenantId": "altitude", "limit": 5})

        emails = repo.query_by_tenant("emailMessages", "altitude")
        orders = repo.query_by_tenant("orderRuns", "altitude")

        self.assertEqual(first["mailboxPoll"]["ingestedCount"], 1)
        self.assertEqual(first["mailboxPoll"]["processedCount"], 1)
        self.assertEqual(second["mailboxPoll"]["ingestedCount"], 0)
        self.assertEqual(second["mailboxPoll"]["skippedCount"], 1)
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]["mailboxAccountId"], mailbox["id"])
        self.assertEqual(emails[0]["customerId"], "frontier-102598")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["customerId"], "frontier-102598")
        self.assertEqual(orders[0]["sourceFileName"], "order.csv")

    def test_graph_webhook_subscription_processes_message_notification(self) -> None:
        api, repo, _ = self._api()
        mailbox = api.upsert_mailbox(
            {
                "tenantId": "altitude",
                "mailboxAddress": "orders@example.com",
                "connectionId": "m365-orders",
            }
        )["mailboxAccount"]
        api.upsert_customer_config(
            {
                "tenantId": "altitude",
                "id": "frontier-102598",
                "customerCode": "102598",
                "name": "Frontier",
            }
        )
        api.upsert_processor_profile(
            {
                "tenantId": "altitude",
                "customerId": "_global",
                "id": "csv-default",
                "name": "CSV",
                "processorType": "csv",
            }
        )
        api.upsert_routing_rule(
            {
                "tenantId": "altitude",
                "customerId": "_global",
                "name": "Webstore orders",
                "phase": "webstoreOrder",
                "outcome": "knownOrder",
                "mailboxAccountIds": [mailbox["id"]],
                "subjectRegex": ["Order"],
                "customerCodeSource": "subject",
                "customerCodeRegex": r"Order\s*#\s*(?P<customerCode>\d+)",
                "processorProfileId": "csv-default",
            }
        )
        created_payloads: list[dict[str, object]] = []

        def graph_post_response(_token: str, url: str, payload: dict[str, object]) -> dict[str, object]:
            created_payloads.append(payload)
            self.assertEqual(url, "https://graph.microsoft.com/v1.0/subscriptions")
            return {
                "id": "subscription-1",
                "clientState": payload["clientState"],
                "resource": payload["resource"],
                "changeType": payload["changeType"],
                "expirationDateTime": "2026-06-30T12:00:00Z",
            }

        csv_body = base64.b64encode(b"item_number,quantity,description\nSKU-1,2,Test Item\n").decode("ascii")

        def graph_get_response(_token: str, url: str) -> dict[str, object]:
            if url.endswith("/attachments"):
                return {
                    "value": [
                        {
                            "id": "attachment-1",
                            "name": "order.csv",
                            "contentType": "text/csv",
                            "size": 48,
                            "isInline": False,
                            "contentBytes": csv_body,
                        }
                    ]
                }
            return {
                "id": "graph-message-1",
                "internetMessageId": "<message-1@example.com>",
                "subject": "Frontier Distributing. Purchase Receipt for Order #  102598",
                "from": {"emailAddress": {"address": "buyer@example.com"}},
                "receivedDateTime": "2026-06-23T12:00:00Z",
                "body": {"contentType": "text", "content": "Order attached."},
                "hasAttachments": True,
                "isRead": False,
            }

        with patch.dict(os.environ, {"ORDER_PROCESSOR_MICROSOFT_AUTH_CLIENT_SECRET": "client-secret"}, clear=False), patch(
            "order_processor.api.client_credentials_access_token",
            return_value={"access_token": "app-access-token"},
        ), patch("order_processor.api.graph_post", side_effect=graph_post_response), patch(
            "order_processor.api.graph_get",
            side_effect=graph_get_response,
        ):
            subscription_result = api.sync_mailbox_subscriptions(
                {
                    "tenantId": "altitude",
                    "notificationUrl": "https://functions.example.com/graph/notifications",
                    "authMode": "application",
                }
            )
            stored_mailbox = repo.get("mailboxAccounts", mailbox["id"])
            graph_subscription = stored_mailbox["settings"]["graphSubscription"]
            notification = {
                "subscriptionId": "subscription-1",
                "clientState": graph_subscription["clientState"],
                "changeType": "created",
                "resourceData": {"id": "graph-message-1"},
            }
            first = api.process_graph_notifications({"notifications": [notification]})
            second = api.process_graph_notifications({"notifications": [notification]})

        self.assertEqual(subscription_result["mailboxSubscriptions"]["createdCount"], 1)
        self.assertEqual(created_payloads[0]["resource"], "users/orders%40example.com/mailFolders('inbox')/messages")
        self.assertEqual(first["graphNotifications"]["ingestedCount"], 1)
        self.assertEqual(second["graphNotifications"]["skippedCount"], 1)
        orders = repo.query_by_tenant("orderRuns", "altitude")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["customerId"], "frontier-102598")
        self.assertEqual(orders[0]["sourceFileName"], "order.csv")

    def test_console_upserts_tenant_mailbox_and_customer_identification_rule(self) -> None:
        api, repo, _ = self._api()
        admin_headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}

        tenant = api.console_upsert_tenant_config(
            {
                "tenantId": "altitude",
                "headers": admin_headers,
                "name": "Altitude Distribution",
                "environment": "prod",
            }
        )
        mailbox = api.console_upsert_mailbox(
            {
                "tenantId": "altitude",
                "headers": admin_headers,
                "mailboxAddress": "Orders@Example.com",
                "connectionId": "m365-orders",
            }
        )
        rule = api.console_upsert_customer_identification_rule(
            {
                "tenantId": "altitude",
                "headers": admin_headers,
                "customerId": "pilot-customer",
                "aliasType": "accountNumber",
                "value": "PILOT-001",
            }
        )

        self.assertEqual(tenant["tenant"]["name"], "Altitude Distribution")
        self.assertEqual(mailbox["mailboxAccount"]["customerId"], "_global")
        self.assertEqual(mailbox["mailboxAccount"]["mailboxAddress"], "orders@example.com")
        self.assertEqual(rule["customerIdentificationRule"]["normalizedValue"], "PILOT001")
        self.assertIsNotNone(repo.get("customerAliases", rule["customerIdentificationRule"]["id"]))

    def test_console_tenant_upsert_rejects_empty_payload(self) -> None:
        api, repo, _ = self._api()
        admin_headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}

        result = api.console_upsert_tenant_config({"headers": admin_headers})

        self.assertEqual(result["error"], "tenantIdRequired")
        self.assertIsNone(repo.get("tenants", "default"))

    def test_console_dashboard_exposes_system_settings_without_listing_system_tenant(self) -> None:
        api, _, _ = self._api()
        admin_headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}
        api.console_upsert_tenant_config(
            {
                "tenantId": "altitude",
                "headers": admin_headers,
                "targetTenantId": "__system__",
                "name": "System Settings",
                "environment": "system",
                "status": "active",
                "settings": {"supportedFileTypes": {"orderInputExtensions": ["csv", "xlsx", "xlt"]}},
            }
        )

        dashboard = api.console_dashboard({"tenantId": "altitude", "headers": admin_headers})

        self.assertEqual(dashboard["systemSettings"]["supportedFileTypes"]["orderInputExtensions"], ["csv", "xlsx", "xlt"])
        self.assertNotIn("__system__", [tenant["tenantId"] for tenant in dashboard["distributorCustomers"]])

    def test_console_upserts_email_triage_policy_fields(self) -> None:
        api, repo, _ = self._api()
        admin_headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}

        result = api.console_upsert_routing_rule(
            {
                "tenantId": "altitude",
                "headers": admin_headers,
                "id": "webstore-orders",
                "customerId": "_global",
                "name": "Webstore orders",
                "phase": "webstoreOrder",
                "outcome": "knownCustomerNonOrder",
                "customerCodeSource": "bodyText",
                "customerCodeRegex": r"Customer:\s*(?P<customerCode>\d+)",
                "subjectTemplate": "Cust: {customerCode} - {originalSubject}",
                "categoryCsrField": "csrFolder",
                "categoryTemplates": ["CSR: {csrName}"],
                "nonOrderMoveMode": "customerField",
                "nonOrderMoveCustomerField": "csrFolder",
            }
        )

        stored = repo.get("routingRules", result["routingRule"]["id"])
        self.assertEqual(stored["phase"], "webstoreOrder")
        self.assertEqual(stored["customerCodeExtraction"]["source"], "bodyText")
        self.assertEqual(stored["subjectUpdate"]["template"], "Cust: {customerCode} - {originalSubject}")
        self.assertEqual(stored["emailActions"]["moves"]["nonOrder"]["field"], "csrFolder")

    def test_console_upsert_routing_rule_normalizes_js_named_regex_groups(self) -> None:
        api, repo, _ = self._api()
        admin_headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}

        result = api.console_upsert_routing_rule(
            {
                "tenantId": "altitude",
                "headers": admin_headers,
                "id": "webstore-orders",
                "customerId": "_global",
                "name": "Webstore orders",
                "phase": "webstoreOrder",
                "outcome": "knownCustomerNonOrder",
                "subjectRegex": [r"Purchase\s+Receipt"],
                "customerCodeSource": "subject",
                "customerCodeRegex": r"Order\s*#\s*(?<customerCode>\d+)",
                "priorProcessedSubjectRegex": [r"Cust:\s*(?<customerCode>\d+)"],
            }
        )

        self.assertNotIn("error", result)
        stored = repo.get("routingRules", "webstore-orders")
        self.assertEqual(stored["customerCodeExtraction"]["regex"], r"Order\s*#\s*(?P<customerCode>\d+)")
        self.assertEqual(stored["priorProcessedSubjectRegex"], [r"Cust:\s*(?P<customerCode>\d+)"])

    def test_console_upsert_routing_rule_generates_id_when_console_sends_blank_id(self) -> None:
        api, repo, _ = self._api()
        admin_headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}

        result = api.console_upsert_routing_rule(
            {
                "tenantId": "altitude",
                "headers": admin_headers,
                "id": "",
                "customerId": "_global",
                "name": "Webstore orders",
                "phase": "webstoreOrder",
                "outcome": "knownCustomerNonOrder",
            }
        )

        self.assertNotIn("error", result)
        self.assertTrue(result["routingRule"]["id"])
        self.assertIsNotNone(repo.get("routingRules", result["routingRule"]["id"]))

    def test_console_upsert_routing_rule_returns_validation_error_for_bad_regex(self) -> None:
        api, repo, _ = self._api()
        admin_headers = {"x-ms-client-principal": _easy_auth_header("connect@focuseautomate.com")}

        result = api.console_upsert_routing_rule(
            {
                "tenantId": "altitude",
                "headers": admin_headers,
                "id": "bad-webstore-orders",
                "customerId": "_global",
                "name": "Bad webstore orders",
                "phase": "webstoreOrder",
                "outcome": "knownCustomerNonOrder",
                "subjectRegex": ["["],
            }
        )

        self.assertEqual(result["error"], "invalidRegex")
        self.assertEqual(result["field"], "subjectRegex")
        self.assertIsNone(repo.get("routingRules", "bad-webstore-orders"))

    def test_console_upserts_profiles_and_downloads_output_artifact_content(self) -> None:
        api, _, _ = self._api()
        api.upsert_customer_config(
            {"tenantId": "altitude", "id": "pilot-customer", "customerCode": "PILOT", "name": "Pilot"}
        )
        api.upsert_output_profile(
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "id": "pilot-text",
                "name": "Pilot Text",
                "outputType": "text",
                "settings": {"template": "{line_number}|{matched_internal_item_number}|{quantity}"},
            }
        )
        api.repository.upsert(
            "items",
            {
                "id": "item-1",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "internalItemNumber": "10001",
                "description": "Dog Food 25 lb",
                "customerItemNumbers": ["PILOT123"],
            },
        )
        result = api.process_order(
            "order-run-artifact",
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "processorType": "csv",
                "sourceContent": "item_number,quantity,description\nPILOT123,2,Dog Food 25 lb\n",
            },
        )
        text_artifact = next(artifact for artifact in result["orderRun"]["outputArtifacts"] if artifact["type"] == "text")

        download = api.console_output_artifact(
            {
                "tenantId": "altitude",
                "email": "connect@focuseautomate.com",
                "orderRunId": "order-run-artifact",
                "artifactId": text_artifact["id"],
            }
        )

        self.assertEqual(download["artifact"]["id"], text_artifact["id"])
        self.assertEqual(download["content"], "1|10001|2.0\n")

    def test_exception_resolution_updates_customer_item_and_reprocess_state(self) -> None:
        api, repo, _ = self._api()
        repo.upsert(
            "emailMessages",
            to_dict(
                EmailMessage(
                    id="email-1",
                    tenant_id="altitude",
                    mailbox="orders@example.com",
                    message_id="message-1",
                    subject="Order",
                    sender="buyer@example.com",
                    received_at=utc_now(),
                    order_run_id="order-run-1",
                )
            ),
        )
        repo.upsert(
            "orderRuns",
            to_dict(
                OrderRun(
                    id="order-run-1",
                    tenant_id="altitude",
                    email_message_id="email-1",
                    status=ProcessingStatus.NEEDS_REVIEW,
                    lines=[OrderLine(line_number=1, provided_item_number="UNKNOWN", validation_status=MatchStatus.UNRESOLVED)],
                )
            ),
        )
        repo.upsert("customers", {"tenantId": "altitude", "id": "pilot-customer", "customerCode": "PILOT", "name": "Pilot"})
        customer_task = api._create_exception(
            tenant_id="altitude",
            task_type="customerIdentification",
            prompt="Resolve customer",
            order_run_id="order-run-1",
            email_message_id="email-1",
        )
        item_task = api._create_exception(
            tenant_id="altitude",
            task_type="itemValidation",
            prompt="Resolve item",
            order_run_id="order-run-1",
            line_number=1,
        )
        parser_task = api._create_exception(
            tenant_id="altitude",
            task_type="parserFailure",
            prompt="Parser failed",
            order_run_id="order-run-1",
        )

        customer_resolution = api.resolve_exception(
            customer_task["id"], {"resolution": {"customerCode": "PILOT"}}
        )
        item_resolution = api.resolve_exception(
            item_task["id"], {"resolution": {"matchedInternalItemNumber": "10001", "lineNumber": 1}}
        )
        parser_resolution = api.resolve_exception(parser_task["id"], {"resolution": {"reprocess": True}})

        self.assertEqual(customer_resolution["resolutionResult"]["customerId"], "pilot-customer")
        self.assertEqual(repo.get("emailMessages", "email-1")["customerId"], "pilot-customer")
        self.assertEqual(repo.get("orderRuns", "order-run-1")["lines"][0]["matchedInternalItemNumber"], "10001")
        self.assertEqual(item_resolution["resolutionResult"]["matchedInternalItemNumber"], "10001")
        self.assertEqual(parser_resolution["resolutionResult"]["reprocess"]["orderRun"]["status"], "received")

    def test_customer_exception_bad_customer_code_stays_open(self) -> None:
        api, _, _ = self._api()
        task = api._create_exception(
            tenant_id="altitude",
            task_type="customerIdentification",
            prompt="Resolve customer",
            email_message_id="email-1",
        )

        result = api.resolve_exception(task["id"], {"resolution": {"customerCode": "MISSING"}})

        self.assertEqual(result["exceptionTask"]["status"], "open")
        self.assertEqual(result["resolutionResult"]["status"], "notFound")

    def test_csr_exception_resolution_updates_customer_subject_and_moves_email(self) -> None:
        api, repo, _ = self._api()
        repo.upsert(
            "customers",
            {
                "tenantId": "altitude",
                "id": "pilot-customer",
                "customerCode": "PILOT",
                "name": "Pilot",
            },
        )
        api.upsert_mailbox({"tenantId": "altitude", "id": "mailbox-1", "mailboxAddress": "orders@example.com"})
        repo.upsert(
            "emailMessages",
            to_dict(
                EmailMessage(
                    id="email-1",
                    tenant_id="altitude",
                    mailbox="orders@example.com",
                    mailbox_account_id="mailbox-1",
                    message_id="message-1",
                    subject="Original",
                    sender="buyer@example.com",
                    received_at=utc_now(),
                    customer_id="pilot-customer",
                    source={"graphMessageId": "graph-message-1"},
                )
            ),
        )
        task = api._create_exception(
            tenant_id="altitude",
            task_type="routing",
            prompt="Missing CSR",
            email_message_id="email-1",
            customer_id="pilot-customer",
        )
        patch_calls: list[tuple[str, dict[str, object]]] = []
        post_calls: list[tuple[str, dict[str, object]]] = []

        def graph_post_response(_token: str, url: str, payload: dict[str, object]) -> dict[str, object]:
            post_calls.append((url, payload))
            if url.endswith("/mailFolders"):
                return {"id": "folder-jane"}
            return {"id": "moved-message-1"}

        with patch.object(
            api,
            "_graph_access_token_candidates",
            return_value=[{"accessToken": "access-token", "authMethod": "test"}],
        ), patch("order_processor.api.graph_get", return_value={"value": []}), patch(
            "order_processor.api.graph_patch",
            side_effect=lambda token, url, payload: patch_calls.append((url, payload)) or {},
        ), patch("order_processor.api.graph_post", side_effect=graph_post_response):
            result = api.resolve_exception(
                task["id"],
                {
                    "resolution": {
                        "action": "csr",
                        "csrName": "Jane",
                        "csrFolder": "Jane",
                        "subject": "Cust: PILOT - Original",
                        "forceMoveToCsr": True,
                    }
                },
            )

        stored_customer = repo.get("customers", "pilot-customer")
        stored_email = repo.get("emailMessages", "email-1")
        self.assertEqual(result["exceptionTask"]["status"], "resolved")
        self.assertEqual(stored_customer["csrFolder"], "Jane")
        self.assertEqual(stored_email["subject"], "Cust: PILOT - Original")
        self.assertEqual(stored_email["source"]["graphMessageId"], "moved-message-1")
        self.assertEqual(repo.get("tenants", "altitude")["settings"]["csrDirectory"][0]["name"], "Jane")
        self.assertEqual(patch_calls[0][1], {"subject": "Cust: PILOT - Original"})
        self.assertTrue(post_calls[-1][0].endswith("/messages/graph-message-1/move"))

    def test_force_order_exception_resolution_creates_order_run_with_selected_processor(self) -> None:
        api, repo, _ = self._api()
        api.upsert_processor_profile(
            {
                "tenantId": "altitude",
                "id": "email-body-profile",
                "name": "Email Body",
                "processorType": "emailBody",
            }
        )
        repo.upsert(
            "emailMessages",
            to_dict(
                EmailMessage(
                    id="email-1",
                    tenant_id="altitude",
                    mailbox="orders@example.com",
                    message_id="message-1",
                    subject="Please process",
                    sender="buyer@example.com",
                    received_at=utc_now(),
                    customer_id="pilot-customer",
                    body_text="PO: TEST-1\nItem|Qty\n188010145|2\n",
                    source={"graphMessageId": "graph-message-1"},
                )
            ),
        )
        task = api._create_exception(
            tenant_id="altitude",
            task_type="routing",
            prompt="Force order",
            email_message_id="email-1",
            customer_id="pilot-customer",
        )

        result = api.resolve_exception(
            task["id"],
            {"resolution": {"action": "forceOrder", "processorProfileId": "email-body-profile"}},
        )

        order_run = result["resolutionResult"]["orderRun"]
        self.assertEqual(result["exceptionTask"]["status"], "resolved")
        self.assertEqual(order_run["processorProfileId"], "email-body-profile")
        self.assertEqual(repo.get("emailMessages", "email-1")["orderRunId"], order_run["id"])
        self.assertEqual(repo.get("orderRuns", order_run["id"])["processorType"], "emailBody")

    def test_power_automate_webhook_processor_posts_graph_email_context(self) -> None:
        api, repo, _ = self._api()
        repo.upsert(
            "customers",
            {
                "tenantId": "altitude",
                "id": "pilot-customer",
                "customerCode": "PILOT",
                "name": "Pilot Customer",
                "routeNumber": "500",
                "csrFolder": "Jane",
            },
        )
        api.upsert_processor_profile(
            {
                "tenantId": "altitude",
                "id": "webhook-profile",
                "name": "Custom PA Flow",
                "processorType": "powerAutomateWebhook",
                "settings": {"webhookUrl": "https://example.test/flow", "timeoutSeconds": 10},
            }
        )
        repo.upsert(
            "emailMessages",
            to_dict(
                EmailMessage(
                    id="email-1",
                    tenant_id="altitude",
                    mailbox="orders@example.com",
                    mailbox_account_id="mailbox-1",
                    message_id="<internet-message-id>",
                    subject="Custom order",
                    sender="buyer@example.com",
                    received_at="2026-06-25T14:00:00Z",
                    customer_id="pilot-customer",
                    source={
                        "graphMessageId": "graph-message-1",
                        "webLink": "https://outlook.office.com/mail/id/graph-message-1",
                        "toRecipients": ["orders@example.com"],
                    },
                    routing={"outcome": "knownOrder"},
                )
            ),
        )
        captured: dict[str, object] = {}

        class FakeResponse:
            status = 202
            headers = {"Content-Type": "application/json"}

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, _limit: int = -1) -> bytes:
                return b'{"accepted":true}'

        def fake_urlopen(request: object, timeout: int = 0) -> FakeResponse:
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        with patch("order_processor.api.urlrequest.urlopen", side_effect=fake_urlopen):
            result = api.process_order(
                "order-run-1",
                {
                    "tenantId": "altitude",
                    "emailMessageId": "email-1",
                    "customerId": "pilot-customer",
                    "processorProfileId": "webhook-profile",
                },
            )

        body = captured["body"]
        order_run = result["orderRun"]
        self.assertEqual(captured["url"], "https://example.test/flow")
        self.assertEqual(captured["timeout"], 10)
        self.assertEqual(body["graphMessageId"], "graph-message-1")
        self.assertEqual(body["internetMessageId"], "<internet-message-id>")
        self.assertEqual(body["customerCode"], "PILOT")
        self.assertEqual(body["csrFolder"], "Jane")
        self.assertEqual(order_run["status"], "completed")
        self.assertEqual(order_run["processorType"], "powerAutomateWebhook")
        self.assertEqual(order_run["sourceMetadata"]["webhookProcessor"]["statusCode"], 202)

    def test_power_automate_webhook_failure_creates_exception(self) -> None:
        api, repo, _ = self._api()
        api.upsert_processor_profile(
            {
                "tenantId": "altitude",
                "id": "webhook-profile",
                "name": "Custom PA Flow",
                "processorType": "powerAutomateWebhook",
                "settings": {"webhookUrl": "https://example.test/flow"},
            }
        )

        with patch("order_processor.api.urlrequest.urlopen", side_effect=OSError("connection failed")):
            result = api.process_order(
                "order-run-1",
                {
                    "tenantId": "altitude",
                    "emailMessageId": "email-1",
                    "processorProfileId": "webhook-profile",
                },
            )

        exceptions = repo.query_by_tenant("exceptionTasks", "altitude")
        self.assertEqual(result["orderRun"]["status"], "failed")
        self.assertEqual(exceptions[0]["type"], "webhookProcessor")
        self.assertEqual(exceptions[0]["context"]["handoff"]["status"], "failed")

    def test_console_customer_user_can_resolve_and_reprocess_assigned_customer_only(self) -> None:
        api, repo, _ = self._api()
        api.upsert_console_user({"tenantId": "altitude", "email": "buyer@example.com", "roles": ["customerUser"]})
        api.assign_customer_user(
            "pilot-customer",
            {"tenantId": "altitude", "email": "buyer@example.com", "roles": ["exceptionResolver", "orderManager"]},
        )
        repo.upsert(
            "orderRuns",
            to_dict(
                OrderRun(
                    id="pilot-run",
                    tenant_id="altitude",
                    email_message_id="email-1",
                    customer_id="pilot-customer",
                    status=ProcessingStatus.NEEDS_REVIEW,
                    lines=[OrderLine(line_number=1, validation_status=MatchStatus.UNRESOLVED)],
                )
            ),
        )
        task = api._create_exception(
            tenant_id="altitude",
            task_type="itemValidation",
            prompt="Resolve item",
            order_run_id="pilot-run",
            line_number=1,
        )
        headers = {"x-ms-client-principal": _easy_auth_header("buyer@example.com")}

        resolved = api.console_resolve_exception(
            task["id"],
            {
                "tenantId": "altitude",
                "headers": headers,
                "resolution": {"matchedInternalItemNumber": "10001", "lineNumber": 1},
            },
        )
        reprocess = api.console_reprocess_order("pilot-run", {"tenantId": "altitude", "headers": headers})

        self.assertEqual(resolved["exceptionTask"]["status"], "resolved")
        self.assertEqual(reprocess["orderRun"]["status"], "received")


if __name__ == "__main__":
    unittest.main()
