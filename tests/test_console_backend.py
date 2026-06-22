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
        self.assertTrue(mailbox["session"]["authorized"])
        self.assertEqual(mailbox["mailboxAccount"]["tenantId"], "test-distributor")
        self.assertEqual(mailbox["mailboxAccount"]["mailboxAddress"], "orders@example.com")
        cloned_admin = next(
            user
            for user in repo.query_by_tenant("consoleUsers", "test-distributor")
            if user["email"] == "admin@example.com"
        )
        self.assertEqual(cloned_admin["roles"], ["platformAdmin"])

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
                    "redirectUri": "https://console.example.com/auth/microsoft/callback",
                }
            )

        self.assertIn("https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize", result["authorizationUrl"])
        self.assertIn("Mail.ReadWrite.Shared", result["authorizationUrl"])
        stored = repo.get("microsoftAuthConnections", "m365-orders")
        self.assertEqual(stored["status"], "needsConsent")
        self.assertEqual(stored["metadata"]["mailboxAddress"], "orders@example.com")

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
                "requestedBy": "connect@focuseautomate.com",
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
        ):
            result = api.console_complete_microsoft_auth({"state": state, "code": "auth-code"})

        self.assertEqual(result["microsoftAuthConnection"]["status"], "active")
        self.assertEqual(result["mailboxAccount"]["permissionStatus"], "active")
        self.assertEqual(repo.get("mailboxAccounts", mailbox["id"])["settings"]["authorizedBy"], "connect@focuseautomate.com")
        self.assertEqual(api.secret_store.get_secret("msgraph-m365-orders-refresh-token"), "refresh-token")
        self.assertEqual(api.secret_store.get_secret("msgraph-m365-orders-access-token"), "access-token")

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
                "outcome": "knownOrder",
                "customerCodeSource": "bodyText",
                "customerCodeRegex": r"Customer:\s*(?P<customerCode>\d+)",
                "subjectTemplate": "Cust: {customerCode} - {originalSubject}",
                "categoryCsrField": "csrFolder",
                "categoryTemplates": ["CSR: {csrName}"],
                "processedMoveMode": "customerField",
                "processedMoveCustomerField": "csrFolder",
            }
        )

        stored = repo.get("routingRules", result["routingRule"]["id"])
        self.assertEqual(stored["phase"], "webstoreOrder")
        self.assertEqual(stored["customerCodeExtraction"]["source"], "bodyText")
        self.assertEqual(stored["subjectUpdate"]["template"], "Cust: {customerCode} - {originalSubject}")
        self.assertEqual(stored["emailActions"]["moves"]["processedOrder"]["field"], "csrFolder")

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
            customer_task["id"], {"resolution": {"selectedCustomerId": "pilot-customer"}}
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
