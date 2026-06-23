from __future__ import annotations

import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


ROOT = Path(__file__).resolve().parents[1]
SHELL_ROOT = ROOT / "power-automate" / "solutions" / "OrderProcessor"


class PowerAutomateShellTests(unittest.TestCase):
    def test_manifest_contains_required_shell_flows(self) -> None:
        manifest = json.loads((SHELL_ROOT / "shell-solution-manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["solution"]["uniqueName"], "OrderProcessor")
        self.assertEqual(manifest["solution"]["targetEnvironmentId"], "abbd708f-4eaf-e875-a282-e1207f4e370c")
        self.assertEqual(
            {flow["name"] for flow in manifest["flows"]},
            {
                "OrderProcessor - Mailbox Trigger Template",
                "OrderProcessor - Customer Import Adapter Template",
                "OrderProcessor - Item Import Adapter Template",
                "OrderProcessor - Output Delivery Adapter Template",
            },
        )

    def test_import_status_records_live_shell_import(self) -> None:
        import_status = json.loads((SHELL_ROOT / "import-status.json").read_text(encoding="utf-8"))

        self.assertTrue(import_status["import"]["imported"])
        self.assertEqual(import_status["environment"]["id"], "abbd708f-4eaf-e875-a282-e1207f4e370c")
        self.assertEqual(import_status["solution"]["uniqueName"], "OrderProcessor")
        self.assertEqual(import_status["solution"]["solutionId"], "660bea3d-196c-f111-a826-7c1e5281c285")
        self.assertEqual(import_status["import"]["importId"], "74e34f63-cb6c-f111-ab0d-7c1e5281c285")
        self.assertEqual(len(import_status["components"]["workflows"]), 4)
        self.assertEqual(
            import_status["components"]["connectionReferences"][0]["logicalName"],
            "alt_sharedoffice365_orderprocessor",
        )

    def test_shell_templates_call_apim_not_legacy_dependencies(self) -> None:
        disallowed = [
            "shared_sharepointonline",
            "shared_plumsail",
            "googleapis.com",
            "api.openai.com",
            "environment.api.powerplatform.com",
            "FlowV1DocumentsJobsParseCsvPost",
            "FlowV1DocumentsJobsXls2XlsxPost",
        ]

        for definition_path in (SHELL_ROOT / "flow-templates").glob("*/definition.json"):
            with self.subTest(definition=definition_path):
                text = definition_path.read_text(encoding="utf-8")
                self.assertIn("OrderProcessorApiBaseUrl", text)
                for blocked in disallowed:
                    self.assertNotIn(blocked, text)

    def test_mailbox_template_is_thin_m365_trigger_only(self) -> None:
        definition = json.loads(
            (
                SHELL_ROOT
                / "flow-templates"
                / "orderprocessor-mailbox-trigger-template"
                / "definition.json"
            ).read_text(encoding="utf-8")
        )

        connection_refs = definition["properties"]["connectionReferences"]
        actions = definition["properties"]["definition"]["actions"]
        trigger = definition["properties"]["definition"]["triggers"][
            "When_a_new_email_arrives_in_configured_shared_mailbox"
        ]

        self.assertEqual(set(connection_refs), {"shared_office365"})
        self.assertEqual(trigger["inputs"]["host"]["operationId"], "SharedMailboxOnNewEmailV2")
        self.assertIn("Post_Email_Metadata_To_Order_Processor", actions)
        self.assertEqual(actions["Post_Email_Metadata_To_Order_Processor"]["type"], "Http")

    def test_import_templates_respond_before_posting_to_backend(self) -> None:
        for slug, post_action in [
            ("orderprocessor-customer-import-adapter-template", "Post_To_Order_Processor_Customer_Import"),
            ("orderprocessor-item-import-adapter-template", "Post_To_Order_Processor_Item_Import"),
        ]:
            with self.subTest(slug=slug):
                definition = json.loads(
                    (SHELL_ROOT / "flow-templates" / slug / "definition.json").read_text(encoding="utf-8")
                )
                actions = definition["properties"]["definition"]["actions"]

                self.assertEqual(actions["Respond_Accepted"]["type"], "Response")
                self.assertEqual(actions["Respond_Accepted"]["runAfter"], {})
                self.assertEqual(actions["Respond_Accepted"]["inputs"]["statusCode"], 202)
                self.assertEqual(actions[post_action]["runAfter"], {"Respond_Accepted": ["Succeeded"]})
                self.assertNotIn("backendResponse", actions["Respond_Accepted"]["inputs"]["body"])

    def test_packaged_solution_contains_all_shell_workflows(self) -> None:
        zip_path = SHELL_ROOT / "exports" / "OrderProcessor_1.0.0.0_unmanaged.zip"
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())

        expected_workflows = {
            "Workflows/OP-MailboxTrigger-7F52D9D6-8EB1-4AD7-B2F6-89DD55DC4E01.json",
            "Workflows/OP-CustomerImport-06B7F4FC-5F13-4A6F-8742-03F19C301902.json",
            "Workflows/OP-ItemImport-7AA4AB3F-D509-4866-98A0-FCCE8DC79B03.json",
            "Workflows/OP-OutputDelivery-92DF442B-2360-42DD-B787-CE339813D877.json",
        }
        self.assertTrue(expected_workflows.issubset(names))

    def test_solution_root_components_include_four_workflows(self) -> None:
        solution_xml = (SHELL_ROOT / "DataverseProject" / "src" / "Other" / "Solution.xml").read_text(
            encoding="utf-8"
        )

        self.assertEqual(solution_xml.count('<RootComponent type="29"'), 4)

    def test_outlook_connection_reference_is_defined_in_solution(self) -> None:
        customizations_xml = (
            SHELL_ROOT / "DataverseProject" / "src" / "Other" / "Customizations.xml"
        ).read_text(encoding="utf-8")

        self.assertIn('connectionreferencelogicalname="alt_sharedoffice365_orderprocessor"', customizations_xml)
        self.assertIn("/providers/Microsoft.PowerApps/apis/shared_office365", customizations_xml)


if __name__ == "__main__":
    unittest.main()
