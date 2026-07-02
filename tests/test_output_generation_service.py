from __future__ import annotations

import json
import os
import sys
import unittest
import zipfile
from io import BytesIO
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
        self.assertEqual(request.get_header("Content-type"), "text/csv")
        self.assertIn(b"po_number,line_number,matched_internal_item_number,quantity", request.data)
        self.assertIn(b"PO-400,1,10001,5.0", request.data)
        csv_artifact = next(artifact for artifact in result["orderRun"]["outputArtifacts"] if artifact["type"] == "lineCsv")
        self.assertEqual(csv_artifact["delivery"]["status"], "delivered")
        self.assertEqual(csv_artifact["delivery"]["statusCode"], 202)
        self.assertEqual(csv_artifact["delivery"]["url"], "https://customer.example/orders")
        self.assertEqual(csv_artifact["metadata"]["deliveryStatus"], "delivered")

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
