from __future__ import annotations

import csv
import json
import os
import sys
import unittest
import zipfile
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.api import OrderProcessorApi
from order_processor import output_generation
from order_processor.output_generation import InMemoryOutputArtifactStore
from order_processor.storage import InMemoryRepository


class OutputGenerationServiceTests(unittest.TestCase):
    def _api(self) -> tuple[OrderProcessorApi, InMemoryRepository, InMemoryOutputArtifactStore]:
        repo = InMemoryRepository()
        store = InMemoryOutputArtifactStore()
        api = OrderProcessorApi(repo, output_artifact_store=store)
        repo.upsert(
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
        return api, repo, store

    def test_output_artifact_store_backend_accepts_artifact_env_name(self) -> None:
        with patch.dict(os.environ, {"ORDER_PROCESSOR_OUTPUT_ARTIFACT_BACKEND": "blob"}, clear=False), patch(
            "order_processor.output_generation.AzureBlobOutputArtifactStore"
        ) as blob_store:
            store = output_generation.output_artifact_store_from_environment()

        self.assertIs(store, blob_store.return_value)
        blob_store.assert_called_once_with()

    def test_process_order_stores_default_universal_json_and_line_csv_artifacts(self) -> None:
        api, repo, store = self._api()

        result = api.process_order(
            "order-run-default-output",
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "processorType": "csv",
                "poNumber": "PO-100",
                "sourceContent": "item_number,quantity,description\nPILOT123,2,Dog Food 25 lb\n",
            },
        )

        artifacts = result["orderRun"]["outputArtifacts"]
        self.assertEqual({artifact["type"] for artifact in artifacts}, {"universalOrderJson", "lineCsv"})
        self.assertTrue(all(artifact["blobUrl"].startswith("memory://order-artifacts/") for artifact in artifacts))
        self.assertTrue(all("content" not in artifact and "contentBase64" not in artifact for artifact in artifacts))
        self.assertEqual(len(store.objects), 2)
        universal = next(artifact for artifact in artifacts if artifact["type"] == "universalOrderJson")
        universal_body = json.loads(store.objects[universal["blobUrl"]].decode("utf-8"))
        self.assertEqual(universal_body["id"], "order-run-default-output")
        stored_order = repo.get("orderRuns", "order-run-default-output")
        self.assertEqual(stored_order["outputArtifacts"][0]["checksum"], artifacts[0]["checksum"])

    def test_unvalidated_lines_complete_without_exception_and_with_blank_internal_item(self) -> None:
        api, repo, store = self._api()

        result = api.process_order(
            "order-run-unvalidated-output",
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "processorType": "csv",
                "poNumber": "PO-101",
                "sourceContent": "item_number,upc,quantity,description\n12345678,999999999,2,Unknown Item\n",
            },
        )

        self.assertEqual(result["orderRun"]["status"], "completed")
        self.assertEqual(result["unresolvedLineCount"], 1)
        exceptions = repo.query_by_tenant("exceptionTasks", "altitude")
        self.assertEqual(exceptions, [])
        csv_artifact = next(artifact for artifact in result["orderRun"]["outputArtifacts"] if artifact["type"] == "lineCsv")
        csv_body = store.objects[csv_artifact["blobUrl"]].decode("utf-8")
        row = next(csv.DictReader(StringIO(csv_body)))
        self.assertEqual(row["provided_item_number"], "12345678")
        self.assertEqual(row["provided_upc"], "999999999")
        self.assertEqual(row["matched_internal_item_number"], "")

    def test_customer_output_profiles_generate_csv_text_and_api_payload_artifacts(self) -> None:
        api, _, store = self._api()
        for profile in [
            {
                "id": "pilot-csv",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "name": "Pilot CSV",
                "outputType": "csv",
                "destination": {"adapter": "blob", "folder": "pilot/outbound"},
                "settings": {
                    "fields": ["po_number", "line_number", "matched_internal_item_number", "quantity"],
                    "fileNameTemplate": "{customerId}-{poNumber}.csv",
                },
            },
            {
                "id": "pilot-text",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "name": "Pilot Text",
                "outputType": "text",
                "destination": {"adapter": "m365", "flow": "optional-output-adapter"},
                "settings": {"template": "{line_number}|{matched_internal_item_number}|{quantity}"},
            },
            {
                "id": "pilot-api",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "name": "Pilot API",
                "outputType": "api",
                "destination": {"adapter": "api", "url": "https://customer.example/orders"},
                "settings": {"bodyMode": "summary", "method": "post"},
            },
        ]:
            api.repository.upsert("outputProfiles", profile)

        result = api.process_order(
            "order-run-profile-output",
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "processorType": "csv",
                "poNumber": "PO-200",
                "sourceContent": "item_number,quantity,description\nPILOT123,3,Dog Food 25 lb\n",
            },
        )

        artifacts = result["orderRun"]["outputArtifacts"]
        self.assertEqual(
            {artifact["type"] for artifact in artifacts},
            {"universalOrderJson", "lineCsv", "text", "apiPayload"},
        )
        csv_artifact = next(artifact for artifact in artifacts if artifact["outputProfileId"] == "pilot-csv")
        csv_body = store.objects[csv_artifact["blobUrl"]].decode("utf-8")
        self.assertIn("matched_internal_item_number", csv_body)
        self.assertIn("10001", csv_body)
        self.assertEqual(csv_artifact["destination"]["folder"], "pilot/outbound")

        text_artifact = next(artifact for artifact in artifacts if artifact["type"] == "text")
        self.assertEqual(store.objects[text_artifact["blobUrl"]].decode("utf-8"), "1|10001|3.0\n")

        api_artifact = next(artifact for artifact in artifacts if artifact["type"] == "apiPayload")
        api_payload = json.loads(store.objects[api_artifact["blobUrl"]].decode("utf-8"))
        self.assertEqual(api_payload["method"], "POST")
        self.assertEqual(api_payload["url"], "https://customer.example/orders")
        self.assertEqual(api_payload["body"]["orderRunId"], "order-run-profile-output")
        self.assertEqual(api_artifact["metadata"]["deliveryStatus"], "skipped")
        self.assertEqual(api_artifact["delivery"]["reason"], "productionDeliveryDisabled")

    def test_api_destination_posts_generated_csv_when_production_delivery_is_enabled(self) -> None:
        api, _, _ = self._api()
        api.repository.upsert(
            "outputProfiles",
            {
                "id": "pilot-api-csv",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "name": "Pilot API CSV",
                "outputType": "csv",
                "destination": {
                    "adapter": "api",
                    "url": "https://customer.example/orders?sig=secret",
                    "productionDeliveryEnabled": True,
                },
                "settings": {
                    "fields": ["po_number", "line_number", "matched_internal_item_number", "quantity"],
                },
            },
        )

        class Response:
            status = 202
            headers = {"Content-Type": "application/json"}

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, _limit: int = -1) -> bytes:
                return b'{"accepted":true}'

        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

        with patch("order_processor.api.urlrequest.urlopen", side_effect=fake_urlopen) as urlopen:
            result = api.process_order(
                "order-run-api-delivery",
                {
                    "tenantId": "altitude",
                    "customerId": "pilot-customer",
                    "processorType": "csv",
                    "poNumber": "PO-400",
                    "sourceContent": "item_number,quantity,description\nPILOT123,5,Dog Food 25 lb\n",
                },
            )

        urlopen.assert_called_once()
        request = captured["request"]
        self.assertEqual(request.full_url, "https://customer.example/orders?sig=secret")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["subject"], "")
        self.assertEqual(payload["orderRunId"], "order-run-api-delivery")
        self.assertEqual(payload["poNumber"], "PO-400")
        self.assertEqual(payload["processorType"], "csv")
        self.assertEqual(payload["artifactType"], "lineCsv")
        self.assertEqual(payload["contentType"], "text/csv")
        self.assertEqual(payload["contentEncoding"], "utf-8")
        self.assertIn("po_number,line_number,matched_internal_item_number,quantity", payload["content"])
        self.assertIn("PO-400,1,10001,5.0", payload["content"])
        csv_artifact = next(artifact for artifact in result["orderRun"]["outputArtifacts"] if artifact["type"] == "lineCsv")
        self.assertEqual(csv_artifact["delivery"]["status"], "delivered")
        self.assertEqual(csv_artifact["delivery"]["statusCode"], 202)
        self.assertEqual(csv_artifact["delivery"]["url"], "https://customer.example/orders")
        self.assertEqual(csv_artifact["metadata"]["deliveryStatus"], "delivered")

    def test_failed_parser_does_not_generate_or_deliver_empty_output(self) -> None:
        api, _, _ = self._api()
        api.repository.upsert(
            "outputProfiles",
            {
                "id": "pilot-api-csv",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "name": "Pilot API CSV",
                "outputType": "csv",
                "destination": {
                    "adapter": "api",
                    "url": "https://customer.example/orders",
                    "productionDeliveryEnabled": True,
                },
            },
        )

        with patch("order_processor.api.urlrequest.urlopen") as urlopen:
            result = api.process_order(
                "order-run-failed-parser",
                {
                    "tenantId": "altitude",
                    "customerId": "pilot-customer",
                    "processorType": "xlsx",
                    "sourceContent": b"not an xlsx workbook",
                    "sourceFileName": "bad.xlsx",
                },
            )

        self.assertEqual(result["orderRun"]["status"], "failed")
        self.assertEqual(result["orderRun"]["outputArtifacts"], [])
        urlopen.assert_not_called()

    def test_missing_customer_skips_item_validation_and_output_delivery(self) -> None:
        api, repo, store = self._api()
        repo.upsert(
            "items",
            {
                "id": "global-item-1",
                "tenantId": "altitude",
                "customerId": "_global",
                "internalItemNumber": "10001",
                "description": "Dog Food 25 lb",
                "customerItemNumbers": ["PILOT123"],
            },
        )
        api.repository.upsert(
            "outputProfiles",
            {
                "id": "pilot-api-csv",
                "tenantId": "altitude",
                "customerId": "_global",
                "name": "Pilot API CSV",
                "outputType": "csv",
                "destination": {
                    "adapter": "api",
                    "url": "https://customer.example/orders",
                    "productionDeliveryEnabled": True,
                },
            },
        )
        api._create_exception(
            tenant_id="altitude",
            task_type="itemValidation",
            prompt="Resolve item match.",
            order_run_id="order-run-missing-customer",
            line_number=1,
            context={"line": {"lineNumber": 1}},
        )

        with patch("order_processor.api.urlrequest.urlopen") as urlopen:
            result = api.process_order(
                "order-run-missing-customer",
                {
                    "tenantId": "altitude",
                    "processorType": "csv",
                    "sourceContent": "item_number,quantity,description\nPILOT123,5,Dog Food 25 lb\n",
                },
            )

        self.assertEqual(result["orderRun"]["status"], "needsReview")
        self.assertIsNone(result["orderRun"]["customerId"])
        self.assertEqual(result["orderRun"]["outputArtifacts"], [])
        self.assertEqual(result["unresolvedLineCount"], 0)
        self.assertEqual(store.objects, {})
        urlopen.assert_not_called()
        exceptions = repo.query_by_tenant("exceptionTasks", "altitude")
        self.assertEqual(
            [(task["type"], task["status"]) for task in exceptions],
            [("itemValidation", "resolved"), ("customerIdentification", "open")],
        )
        self.assertEqual(result["orderRun"]["lines"][0]["validationErrors"], [])

    def test_successful_reprocess_resolves_stale_parser_failure_exception(self) -> None:
        api, repo, _ = self._api()
        api._create_exception(
            tenant_id="altitude",
            task_type="parserFailure",
            prompt="Review order parser failure.",
            order_run_id="order-run-reparsed",
            context={"errors": [{"code": "processorFailed"}]},
        )

        result = api.process_order(
            "order-run-reparsed",
            {
                "tenantId": "altitude",
                "processorType": "csv",
                "sourceContent": "item_number,quantity,description\nPILOT123,5,Dog Food 25 lb\n",
            },
        )

        parser_tasks = [
            task
            for task in repo.query_by_tenant("exceptionTasks", "altitude")
            if task["type"] == "parserFailure"
        ]
        self.assertEqual(result["orderRun"]["status"], "needsReview")
        self.assertEqual(parser_tasks[0]["status"], "resolved")

    def test_requested_xlsx_output_generates_stored_workbook_reference(self) -> None:
        api, _, store = self._api()

        result = api.process_order(
            "order-run-xlsx-output",
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "processorType": "csv",
                "poNumber": "PO-300",
                "sourceContent": "item_number,quantity,description\nPILOT123,4,Dog Food 25 lb\n",
                "outputTypes": ["xlsx"],
                "outputSettings": {"fileNameTemplate": "{customerId}-{poNumber}.xlsx"},
            },
        )

        xlsx_artifact = next(artifact for artifact in result["orderRun"]["outputArtifacts"] if artifact["type"] == "lineXlsx")
        self.assertEqual(xlsx_artifact["fileName"], "pilot-customer-PO-300.xlsx")
        with zipfile.ZipFile(BytesIO(store.objects[xlsx_artifact["blobUrl"]])) as archive:
            self.assertIn("xl/worksheets/sheet1.xml", archive.namelist())
            self.assertIn(b"10001", archive.read("xl/worksheets/sheet1.xml"))


if __name__ == "__main__":
    unittest.main()
