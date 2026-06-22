from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InfraContractTests(unittest.TestCase):
    def test_foundation_bicep_contains_required_resource_types(self) -> None:
        bicep = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")

        required_fragments = [
            "Microsoft.Web/sites@",
            "Microsoft.DocumentDB/databaseAccounts@",
            "Microsoft.Storage/storageAccounts@",
            "Microsoft.KeyVault/vaults@",
            "Microsoft.Insights/components@",
            "Microsoft.ApiManagement/service@",
            "Microsoft.Web/sites/config@",
            "kind: 'AIServices'",
            "kind: 'OpenAI'",
            "kind: 'FormRecognizer'",
            "Microsoft.Authorization/roleAssignments@",
            "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@",
        ]
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, bicep)

        self.assertIn("param deployAzureOpenAI bool = true", bicep)
        self.assertIn("if (deployAzureOpenAI)", bicep)
        self.assertIn("output azureOpenAiDeployed bool = deployAzureOpenAI", bicep)
        self.assertIn("param consoleLocation string = location", bicep)
        self.assertIn("location: consoleLocation", bicep)
        self.assertIn("param functionPackageUrl string = ''", bicep)
        self.assertIn("WEBSITE_RUN_FROM_PACKAGE_BLOB_MI_RESOURCE_ID", bicep)
        self.assertIn("param microsoftGraphAuthClientId string = ''", bicep)
        self.assertIn("ORDER_PROCESSOR_MICROSOFT_AUTH_CLIENT_ID", bicep)
        self.assertIn("ORDER_PROCESSOR_MICROSOFT_AUTH_REDIRECT_URI", bicep)
        self.assertIn("keyVaultSecretsOfficerRoleId", bicep)

    def test_function_storage_uses_identity_settings(self) -> None:
        bicep = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")

        self.assertIn("AzureWebJobsStorage__accountName", bicep)
        self.assertIn("AzureWebJobsStorage__credential", bicep)
        self.assertIn("managedidentity", bicep)
        self.assertIn("allowSharedKeyAccess: false", bicep)
        self.assertNotIn("DefaultEndpointsProtocol=https;AccountName=", bicep)
        self.assertNotIn("AccountKey=${storageAccount", bicep)

    def test_customer_scoped_cosmos_containers_use_hierarchical_partition_keys(self) -> None:
        bicep = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")
        customers_block = bicep[bicep.index("resource customersContainer") : bicep.index("resource itemsContainer")]
        items_block = bicep[bicep.index("resource itemsContainer") : bicep.index("resource keyVault")]

        self.assertIn("partitionKeyPaths", bicep)
        self.assertIn("'/customerId'", bicep)
        self.assertIn("kind: length(container.partitionKeyPaths) > 1 ? 'MultiHash' : 'Hash'", bicep)
        self.assertIn("kind: 'MultiHash'", bicep)
        self.assertIn("version: 2", bicep)
        self.assertNotIn("'/customerId'", customers_block)
        self.assertIn("'/customerId'", items_block)

    def test_apim_imports_all_public_contract_paths(self) -> None:
        openapi = (ROOT / "infra" / "openapi" / "order-processor-api.yaml").read_text(encoding="utf-8")
        expected_paths = [
            "/emails/ingest",
            "/orders/{orderRunId}/process",
            "/orders/{orderRunId}/timeline",
            "/customers/identify",
            "/items/validate",
            "/imports/customers",
            "/imports/items",
            "/mailboxes",
            "/mailboxes/{id}/test-connection",
            "/console/session",
            "/console/dashboard",
            "/console/artifacts/download",
            "/console/mailboxes",
            "/console/mailboxes/{id}/test-connection",
            "/console/microsoft-auth/start",
            "/console/microsoft-auth/callback",
            "/console/tenants",
            "/console/customer-identification-rules",
            "/console/routing-rules",
            "/console/customers",
            "/console/processor-profiles",
            "/console/output-profiles",
            "/console/users",
            "/console/customers/{customerId}/users",
            "/console/exceptions/{id}/resolve",
            "/console/orders/{orderRunId}/reprocess",
            "/console/orders/{orderRunId}/timeline",
            "/customers/{customerId}/users",
            "/exceptions/{id}/resolve",
            "/orders/{orderRunId}/reprocess",
        ]
        for path in expected_paths:
            with self.subTest(path=path):
                self.assertIn(path, openapi)

    def test_apim_policy_uses_key_vault_backed_shared_key(self) -> None:
        bicep = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")

        self.assertIn("function-host-key-apim", bicep)
        self.assertIn("functionSharedKey", bicep)
        self.assertIn("console-web-app", bicep)
        self.assertIn("ORDER_PROCESSOR_APIM_SUBSCRIPTION_KEY", bicep)
        self.assertIn("authsettingsV2", bicep)
        self.assertIn("secretIdentifier: apimFunctionKeySecret.properties.secretUriWithVersion", bicep)
        self.assertIn('<set-header name="x-order-processor-function-key"', bicep)
        self.assertIn("loadTextContent('openapi/order-processor-api.yaml')", bicep)
        self.assertIn("subscriptionRequired: true", bicep)

    def test_durable_functions_host_configuration_exists(self) -> None:
        host = json.loads((ROOT / "apps" / "functions" / "host.json").read_text(encoding="utf-8"))
        requirements = (ROOT / "apps" / "functions" / "requirements.txt").read_text(encoding="utf-8")

        self.assertEqual(host["extensions"]["durableTask"]["hubName"], "OrderProcessorLocal")
        self.assertIn("azure-functions-durable", requirements)

    def test_subscription_template_creates_resource_group(self) -> None:
        subscription_bicep = (ROOT / "infra" / "subscription.bicep").read_text(encoding="utf-8")

        self.assertIn("targetScope = 'subscription'", subscription_bicep)
        self.assertIn("Microsoft.Resources/resourceGroups@", subscription_bicep)
        self.assertIn("module foundation 'main.bicep'", subscription_bicep)
        self.assertIn("deployAzureOpenAI: deployAzureOpenAI", subscription_bicep)
        self.assertIn("consoleLocation: consoleLocation", subscription_bicep)
        self.assertIn("functionPackageUrl: functionPackageUrl", subscription_bicep)


if __name__ == "__main__":
    unittest.main()
