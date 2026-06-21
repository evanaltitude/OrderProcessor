from __future__ import annotations

import base64
import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.api import OrderProcessorApi
from order_processor.models import OrderRun, ProcessingStatus
from order_processor.order_processing import (
    CsvOrderProcessor,
    CustomerOverrideOrderProcessor,
    EmailBodyOrderProcessor,
    LegacyWorkbookOrderProcessor,
    OrderProcessorContext,
    PdfOrderProcessor,
    XlsxOrderProcessor,
)
from order_processor.output_generation import order_to_xlsx_bytes
from order_processor.storage import InMemoryRepository


ROOT = Path(__file__).resolve().parents[1]


def _order() -> OrderRun:
    return OrderRun(
        id="order-run-1",
        tenant_id="altitude",
        email_message_id="email-1",
        customer_id="pilot-customer",
    )


class OrderProcessingEngineTests(unittest.TestCase):
    def test_csv_parse_supports_active_headerless_csv_without_plumsail(self) -> None:
        source = (ROOT / "samples/phase-2/csv/order-csv-parse.csv").read_text()
        order = CsvOrderProcessor().parse(_order(), source)

        self.assertEqual(order.source_type, "csv")
        self.assertEqual(order.status, ProcessingStatus.PROCESSING)
        self.assertEqual(order.lines[0].provided_item_number, "10001")
        self.assertEqual(order.lines[0].provided_upc, "012345678905")
        self.assertEqual(order.lines[0].quantity, 2.0)

    def test_xlsx_processor_reads_simple_workbook_with_standard_library(self) -> None:
        source = (ROOT / "samples/phase-2/xlsx/generic-ai-header-order.xlsx").read_bytes()
        order = XlsxOrderProcessor().parse(_order(), {"sourceContent": source})

        self.assertEqual(order.source_type, "xlsx")
        self.assertEqual(order.lines[0].provided_item_number, "188010145")
        self.assertEqual(order.lines[0].provided_upc, "860003377529")
        self.assertEqual(order.lines[0].quantity, 1.0)

    def test_legacy_xls_xlt_processor_reads_html_backed_workbook_exports(self) -> None:
        source = (ROOT / "samples/phase-2/xls-xlt/legacy-order.xls").read_bytes()
        order = LegacyWorkbookOrderProcessor().parse(_order(), {"sourceContent": source})

        self.assertEqual(order.source_type, "legacyWorkbook")
        self.assertEqual(order.lines[0].provided_item_number, "188010145")
        self.assertEqual(order.lines[1].quantity, 3.0)

    def test_email_body_processor_reads_pipe_tables_and_po_number(self) -> None:
        source = (ROOT / "samples/phase-2/email-body/generic-email-body.eml").read_text()
        order = EmailBodyOrderProcessor().parse(_order(), {"bodyText": source})

        self.assertEqual(order.source_type, "emailBody")
        self.assertEqual(order.po_number, "EB-1001")
        self.assertEqual(order.lines[0].provided_item_number, "188010145")
        self.assertEqual(order.lines[0].provided_upc, "860003377529")

    def test_customer_override_delegates_to_configured_email_body_pattern(self) -> None:
        source = (ROOT / "samples/phase-2/customer-specific/market-place-pet-supplies.eml").read_text()
        context = OrderProcessorContext(
            tenant_id="altitude",
            customer_id="pilot-customer",
            settings={
                "baseProcessorType": "emailBody",
                "linePattern": r"(?P<provided_upc>\d{12})-(?P<quantity>\d+)",
            },
        )
        order = CustomerOverrideOrderProcessor().parse(_order(), {"bodyText": source}, context)

        self.assertEqual(order.source_type, "emailBody")
        self.assertEqual(order.lines[0].provided_upc, "019962896026")
        self.assertEqual(order.lines[0].quantity, 1.0)
        self.assertEqual(order.source_metadata["customerOverride"]["baseProcessorType"], "emailBody")

    def test_pdf_processor_accepts_azure_document_intelligence_table_result(self) -> None:
        document_result = {
            "tables": [
                {
                    "cells": [
                        {"rowIndex": 0, "columnIndex": 0, "content": "Item Number"},
                        {"rowIndex": 0, "columnIndex": 1, "content": "UPC"},
                        {"rowIndex": 0, "columnIndex": 2, "content": "Quantity"},
                        {"rowIndex": 1, "columnIndex": 0, "content": "188010145"},
                        {"rowIndex": 1, "columnIndex": 1, "content": "860003377529"},
                        {"rowIndex": 1, "columnIndex": 2, "content": "1"},
                    ]
                }
            ]
        }
        order = PdfOrderProcessor().parse(_order(), {"documentIntelligenceResult": document_result})

        self.assertEqual(order.source_type, "pdf")
        self.assertEqual(order.lines[0].provided_item_number, "188010145")
        self.assertEqual(order.lines[0].provided_upc, "860003377529")

    def test_pdf_processor_marks_raw_pdf_for_document_intelligence_extraction(self) -> None:
        order = PdfOrderProcessor().parse(_order(), {"sourceContent": b"%PDF-1.4"})

        self.assertEqual(order.status, ProcessingStatus.FAILED)
        self.assertEqual(order.errors[0]["code"], "documentIntelligenceExtractionRequired")
        self.assertEqual(order.errors[0]["integration"], "azureDocumentIntelligence")

    def test_process_order_uses_processor_profile_and_generates_xlsx_output_when_requested(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        repo.upsert(
            "items",
            {
                "id": "item-1",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "internalItemNumber": "188010145",
                "description": "Fixture item",
                "upc": "860003377529",
                "customerItemNumbers": ["188010145"],
            },
        )
        repo.upsert(
            "items",
            {
                "id": "item-2",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "internalItemNumber": "188010146",
                "description": "Fixture item 2",
                "upc": "860003377530",
                "customerItemNumbers": ["188010146"],
            },
        )
        repo.upsert(
            "outputProfiles",
            {
                "id": "xlsx-output",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "name": "Pilot XLSX",
                "outputType": "xlsx",
            },
        )
        repo.upsert(
            "processorProfiles",
            {
                "id": "pilot-xlsx",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "name": "Pilot workbook processor",
                "processorType": "xlsx",
                "outputProfileId": "xlsx-output",
            },
        )
        source = base64.b64encode((ROOT / "samples/phase-2/xlsx/generic-ai-header-order.xlsx").read_bytes()).decode("ascii")

        result = api.process_order(
            "order-run-xlsx",
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "processorProfileId": "pilot-xlsx",
                "sourceContentBase64": source,
                "sourceFileName": "generic-ai-header-order.xlsx",
            },
        )

        self.assertEqual(result["orderRun"]["status"], "completed")
        self.assertEqual(result["orderRun"]["processorType"], "xlsx")
        self.assertEqual(result["orderRun"]["sourceType"], "xlsx")
        self.assertEqual(result["orderRun"]["lines"][0]["validationStatus"], "matched")
        self.assertIn("lineXlsx", {artifact["type"] for artifact in result["orderRun"]["outputArtifacts"]})

    def test_generated_xlsx_artifact_is_a_valid_open_xml_package(self) -> None:
        workbook = order_to_xlsx_bytes(CsvOrderProcessor().parse(_order(), "item_number,quantity\nABC,2\n"))

        with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
            self.assertIn("xl/worksheets/sheet1.xml", archive.namelist())
            self.assertIn(b"provided_item_number", archive.read("xl/worksheets/sheet1.xml"))


if __name__ == "__main__":
    unittest.main()
