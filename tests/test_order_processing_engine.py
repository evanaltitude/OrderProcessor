from __future__ import annotations

import base64
import io
import sys
import types
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.api import OrderProcessorApi
from order_processor.google_document_ai import google_document_ai_jwt_from_repository
from order_processor.models import OrderRun, ProcessingStatus
from order_processor.order_processing import (
    CsvOrderProcessor,
    CustomerOverrideOrderProcessor,
    EmailBodyOrderProcessor,
    LegacyWorkbookOrderProcessor,
    OrderProcessorContext,
    PdfOrderProcessor,
    SpreadsheetOrderProcessor,
    XlsxOrderProcessor,
    process_order_payload,
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

    def test_xlsx_processor_maps_product_upc_header(self) -> None:
        source = _xlsx_bytes(
            [
                ["PO", "Product UPC", "Vendor Sku", "QTY", "Description"],
                ["443713", "072705121854", "", "2.0000", "Fromm Treats"],
            ]
        )
        order = XlsxOrderProcessor().parse(_order(), {"sourceContent": source})

        self.assertEqual(order.po_number, "443713")
        self.assertEqual(order.lines[0].provided_upc, "072705121854")
        self.assertEqual(order.lines[0].quantity, 2.0)

    def test_xlsx_processor_falls_back_when_header_is_below_metadata(self) -> None:
        source = _xlsx_bytes(
            [
                ["Customer", "Premier Pet Supply"],
                ["PO", "443713"],
                [],
                ["Supplier Code", "Product UPC", "Product", "Qty Ordered"],
                ["FRM-100", "072705121854", "Fromm Treats", "2.0000"],
            ]
        )
        order = XlsxOrderProcessor().parse(_order(), {"sourceContent": source})

        self.assertEqual(order.source_type, "spreadsheet")
        self.assertEqual(order.po_number, "443713")
        self.assertEqual(order.lines[0].provided_item_number, "FRM100")
        self.assertEqual(order.lines[0].provided_upc, "072705121854")
        self.assertEqual(order.lines[0].quantity, 2.0)
        self.assertIn("xlsxSimpleParserFallback", {warning["code"] for warning in order.parse_warnings})

    def test_canonical_lines_map_raw_product_upc_and_vendor_sku(self) -> None:
        order = process_order_payload(
            _order(),
            {
                "lines": [
                    {
                        "quantity": "2.0000",
                        "description": "Fromm Treats",
                        "raw": {
                            "Product UPC": "072705121854",
                            "Vendor Sku": "FRM-100",
                        },
                    }
                ]
            },
        )

        self.assertEqual(order.source_type, "canonicalLines")
        self.assertEqual(order.lines[0].provided_item_number, "FRM-100")
        self.assertEqual(order.lines[0].provided_upc, "072705121854")
        self.assertEqual(order.lines[0].quantity, 2.0)

    def test_xlsx_processor_accepts_legacy_xlt_attachment(self) -> None:
        source = (ROOT / "samples/phase-2/xls-xlt/legacy-template.xlt").read_bytes()
        order = XlsxOrderProcessor().parse(
            _order(),
            {
                "sourceContent": source,
                "sourceFileName": "legacy-template.xlt",
                "contentType": "application/vnd.ms-excel",
            },
        )

        self.assertEqual(order.status, ProcessingStatus.PROCESSING)
        self.assertEqual(order.source_type, "spreadsheet")
        self.assertEqual(order.lines[0].provided_item_number, "188010145")
        self.assertEqual(order.lines[0].provided_upc, "860003377529")

    def test_legacy_xls_xlt_processor_reads_html_backed_workbook_exports(self) -> None:
        source = (ROOT / "samples/phase-2/xls-xlt/legacy-order.xls").read_bytes()
        order = LegacyWorkbookOrderProcessor().parse(_order(), {"sourceContent": source})

        self.assertEqual(order.source_type, "legacyWorkbook")
        self.assertEqual(order.lines[0].provided_item_number, "188010145")
        self.assertEqual(order.lines[1].quantity, 3.0)

    def test_spreadsheet_processor_extracts_csv_layout_with_quoted_commas_and_repeated_po(self) -> None:
        source = "\n".join(
            [
                "Purchase Order,Vendor/Supplier,Product,Variant,SKU,Barcode,Supplier Code,ASIN,Qty Ordered,Pack Size,Qty (packs) Ordered",
                '22316,Frontier Distributing,"ACANA Classics Dog, Red Meat Recipe 25lb",25lb,2715625BAS,064992715625,187520015,B00X,2,1,2',
                '22316,Frontier Distributing,"ORIJEN Dog, Original Recipe 13lb",13lb,2715630BAS,064992715632,187520016,B01X,3,1,3',
            ]
        )
        order = SpreadsheetOrderProcessor().parse(
            _order(),
            {
                "sourceContent": source,
                "sourceFileName": "purchase_order_22316.csv",
                "email": {
                    "sender": "buyer@examplepet.com",
                    "subject": "PO 22316 for Marysville",
                    "body": "Please ship to Classic Pet II - Marysville.",
                },
            },
        )

        self.assertEqual(order.source_type, "spreadsheet")
        self.assertEqual(order.status, ProcessingStatus.PROCESSING)
        self.assertEqual(order.po_number, "22316")
        self.assertEqual(len(order.lines), 2)
        self.assertEqual(order.lines[0].provided_item_number, "187520015")
        self.assertEqual(order.lines[0].provided_upc, "064992715625")
        self.assertEqual(order.lines[0].quantity, 2.0)
        self.assertIn("Dog, Red Meat", order.lines[0].description)
        customer_search = order.source_metadata["spreadsheet"]["customerIdentification"]["customerSearchText"]
        self.assertIn("Classic Pet II", customer_search)
        self.assertIn("buyer@examplepet.com", customer_search)

    def test_spreadsheet_processor_detects_xlsx_table_after_ship_to_preamble(self) -> None:
        source = _xlsx_bytes(
            [
                ["Customer Order Export"],
                ["Ship To", "CLASSIC PET II - MARYSVILLE"],
                ["Delivery City", "MARYSVILLE", "State", "MI"],
                [],
                ["Supplier Code", "Barcode", "Product", "Qty Ordered"],
                ["188010145", "860003377529", "Treats", "1"],
                ["188010146", "860003377530", "Food", "3"],
            ]
        )
        order = SpreadsheetOrderProcessor().parse(
            _order(),
            {"sourceContent": source, "sourceFileName": "order.xlsx"},
        )

        self.assertEqual(order.status, ProcessingStatus.PROCESSING)
        self.assertEqual(order.lines[0].source_row_index, 6)
        self.assertEqual(order.lines[1].provided_item_number, "188010146")
        customer_search = order.source_metadata["spreadsheet"]["customerIdentification"]["customerSearchText"]
        self.assertIn("CLASSIC PET II - MARYSVILLE", customer_search)

    def test_email_body_processor_reads_pipe_tables_and_po_number(self) -> None:
        source = (ROOT / "samples/phase-2/email-body/generic-email-body.eml").read_text()
        order = EmailBodyOrderProcessor().parse(_order(), {"bodyText": source})

        self.assertEqual(order.source_type, "emailBody")
        self.assertEqual(order.po_number, "EB-1001")
        self.assertEqual(order.lines[0].provided_item_number, "188010145")
        self.assertEqual(order.lines[0].provided_upc, "860003377529")

    def test_email_body_processor_extracts_customer_identification_context(self) -> None:
        source = "\n".join(
            [
                "Subject: PO EB-1002",
                "",
                "Ship To: CLASSIC PET II - MARYSVILLE",
                "3180 GRATIOT BLVD",
                "Marysville MI 48040",
                "",
                "Item Number | UPC | Quantity | Description",
                "188010145 | 860003377529 | 2 | Treats",
            ]
        )
        order = EmailBodyOrderProcessor().parse(_order(), {"bodyText": source, "sender": "buyer@classicpet.example"})

        customer_search = order.source_metadata["emailBody"]["customerIdentification"]["customerSearchText"]
        self.assertIn("CLASSIC PET II - MARYSVILLE", customer_search)
        self.assertIn("3180 GRATIOT BLVD", customer_search)
        self.assertIn("buyer@classicpet.example", customer_search)
        self.assertEqual(order.lines[0].description, "Treats")

    def test_email_body_processor_reads_flattened_fieldstack_rows(self) -> None:
        source = (
            "From: FieldStack Automation <orders@FieldStack.com> Purchase Order: 480913369 "
            "Vendor: Frontier Distributing Order Date: 03/05/2025 Ship To: HollywoodFeed "
            "480 MCCANDLESS 9190 Covenant Ave Pittsburgh, PA 15237 Account: "
            "Qty UoM UPC Description Ext Weight(lbs) Price Ext Price "
            "1 EA 064992205409 ORIJEN CAT TUNDRA 4#/ (6499220540) 4.09 $29.45 $29.45 "
            "2 EA 072635010082 TUCKER'S SALMON & PUMPKIN 6#/ (7263501008) 0.00 $24.22 $48.44 "
            "113 Total 119.77 $1121.51"
        )
        order = EmailBodyOrderProcessor().parse(
            _order(),
            {"subject": "Hollywood Feed PO#480913369", "bodyText": source},
        )

        self.assertEqual(order.status, ProcessingStatus.PROCESSING)
        self.assertEqual(order.po_number, "480913369")
        self.assertEqual(len(order.lines), 2)
        self.assertEqual(order.lines[0].quantity, 1.0)
        self.assertEqual(order.lines[0].provided_upc, "064992205409")
        self.assertEqual(order.lines[0].provided_item_number, "6499220540")
        self.assertIn("ORIJEN CAT TUNDRA", order.lines[0].description)
        self.assertEqual(order.lines[1].quantity, 2.0)
        self.assertEqual(order.lines[1].provided_upc, "072635010082")

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

    def test_pdf_processor_accepts_google_document_ai_response_with_ship_to_context(self) -> None:
        order = PdfOrderProcessor().parse(_order(), {"googleDocumentAiResult": _google_document_ai_response()})

        self.assertEqual(order.source_type, "pdf")
        self.assertEqual(order.po_number, "PO-9001")
        self.assertEqual(order.lines[0].provided_item_number, "188010145")
        self.assertEqual(order.lines[0].provided_upc, "860003377529")
        self.assertEqual(order.lines[0].quantity, 2.0)
        customer_search = order.source_metadata["googleDocumentAi"]["customerIdentification"]["customerSearchText"]
        self.assertIn("CLASSIC PET II - MARYSVILLE", customer_search)
        self.assertIn("3180 GRATIOT BLVD", customer_search)

    def test_pdf_processor_marks_raw_pdf_for_document_intelligence_extraction(self) -> None:
        order = PdfOrderProcessor().parse(_order(), {"sourceContent": b"%PDF-1.4"})

        self.assertEqual(order.status, ProcessingStatus.FAILED)
        self.assertEqual(order.errors[0]["code"], "documentExtractionRequired")
        self.assertEqual(order.errors[0]["integration"], "googleDocumentAi")

    def test_google_document_ai_jwt_comes_from_third_party_authentication_document(self) -> None:
        repo = InMemoryRepository()
        repo.upsert(
            "tenants",
            {
                "id": "third-party-service-authentication",
                "authentications": [
                    {"id": "microsoft", "serviceId": "microsoft", "jwt": "ignored"},
                    {"id": "google", "serviceId": "google", "jwt": "jwt-for-google"},
                ],
            },
        )

        jwt = google_document_ai_jwt_from_repository(repo, "altitude", {})

        self.assertEqual(jwt, "jwt-for-google")

    def test_google_document_ai_jwt_can_come_from_direct_google_document(self) -> None:
        repo = InMemoryRepository()
        repo.upsert("tenants", {"id": "google", "serviceId": "google", "jwt": "direct-google-jwt"})

        jwt = google_document_ai_jwt_from_repository(repo, "altitude", {})

        self.assertEqual(jwt, "direct-google-jwt")

    def test_google_document_ai_jwt_can_come_from_tenant_settings_authentication_map(self) -> None:
        repo = InMemoryRepository()
        repo.upsert(
            "tenants",
            {
                "id": "altitude",
                "settings": {
                    "authentications": {
                        "google": {
                            "serviceId": "google",
                            "jwt": "tenant-settings-google-jwt",
                        }
                    }
                },
            },
        )

        jwt = google_document_ai_jwt_from_repository(repo, "altitude", {})

        self.assertEqual(jwt, "tenant-settings-google-jwt")

    def test_google_document_ai_jwt_scans_live_cosmos_containers_for_auth_document(self) -> None:
        repo = _FakeCosmosAuthRepository(
            {
                "serviceAuthentications": [
                    {
                        "id": "third-party-service-authentication",
                        "authentications": [
                            {"id": "microsoft", "serviceId": "microsoft", "jwt": "ignored"},
                            {"id": "google", "serviceId": "google", "jwt": "cosmos-google-jwt"},
                        ],
                    }
                ]
            }
        )

        jwt = google_document_ai_jwt_from_repository(repo, "altitude", {})

        self.assertEqual(jwt, "cosmos-google-jwt")

    def test_google_document_ai_jwt_can_come_from_external_auth_cosmos(self) -> None:
        original_cosmos = sys.modules.get("azure.cosmos")
        original_identity = sys.modules.get("azure.identity")
        sys.modules["azure.cosmos"] = types.SimpleNamespace(CosmosClient=_FakeExternalAuthCosmosClient)
        sys.modules["azure.identity"] = types.SimpleNamespace(DefaultAzureCredential=lambda: "credential")
        try:
            jwt = google_document_ai_jwt_from_repository(
                None,
                "altitude",
                {"googleAuthCosmosEndpoint": "https://auth.example.com:443/"},
            )
        finally:
            if original_cosmos is None:
                sys.modules.pop("azure.cosmos", None)
            else:
                sys.modules["azure.cosmos"] = original_cosmos
            if original_identity is None:
                sys.modules.pop("azure.identity", None)
            else:
                sys.modules["azure.identity"] = original_identity

        self.assertEqual(jwt, "external-google-jwt")


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

    def test_xlsx_order_keeps_unmatched_item_on_order_without_creating_exception(self) -> None:
        repo = InMemoryRepository()
        api = OrderProcessorApi(repo)
        repo.upsert(
            "items",
            {
                "id": "known-item",
                "tenantId": "altitude",
                "customerId": "_global",
                "internalItemNumber": "KNOWN-100",
                "description": "Known item",
                "customerItemNumbers": ["KNOWN-100"],
            },
        )
        repo.upsert(
            "processorProfiles",
            {
                "id": "unmatched-xlsx",
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "name": "Unmatched workbook processor",
                "processorType": "xlsx",
            },
        )
        source = base64.b64encode(
            _xlsx_bytes(
                [
                    ["Item Number", "Quantity", "Description"],
                    ["UNKNOWN-XLSX", "2", "Unknown item"],
                ]
            )
        ).decode("ascii")

        result = api.process_order(
            "order-run-unmatched-xlsx",
            {
                "tenantId": "altitude",
                "customerId": "pilot-customer",
                "processorProfileId": "unmatched-xlsx",
                "sourceContentBase64": source,
                "sourceFileName": "unmatched-order.xlsx",
            },
        )

        line = result["orderRun"]["lines"][0]
        self.assertEqual(result["orderRun"]["status"], "completed")
        self.assertEqual(result["unresolvedLineCount"], 1)
        self.assertEqual(line["validationStatus"], "unresolved")
        self.assertFalse(line.get("matchedInternalItemNumber"))
        self.assertEqual(repo.query_by_tenant("exceptionTasks", "altitude"), [])

    def test_spreadsheet_api_normalizes_and_extracts_order_lines(self) -> None:
        api = OrderProcessorApi(InMemoryRepository())
        source = "Intro row\n\nSupplier Code,Barcode,Product,Qty Ordered\n188010145,860003377529,Treats,4\n"

        normalization = api.normalize_spreadsheet({"sourceContent": source, "sourceFileName": "order.csv"})["normalization"]
        extraction = api.extract_spreadsheet_order_lines({"normalization": normalization})["extraction"]

        self.assertEqual(normalization["status"], "ready")
        self.assertEqual(extraction["status"], "ready")
        self.assertEqual(extraction["lines"][0]["providedItemNumber"], "188010145")
        self.assertEqual(extraction["lines"][0]["quantity"], 4.0)

    def test_email_body_api_extracts_order_lines_and_customer_context(self) -> None:
        api = OrderProcessorApi(InMemoryRepository())
        body = "\n".join(
            [
                "Ship To: CLASSIC PET II - MARYSVILLE",
                "3180 GRATIOT BLVD",
                "Marysville MI 48040",
                "",
                "Item Number | UPC | Quantity",
                "188010145 | 860003377529 | 4",
            ]
        )

        extraction = api.extract_email_body_order({"subject": "PO EB-1003", "bodyText": body})["extraction"]

        self.assertEqual(extraction["status"], "ready")
        self.assertEqual(extraction["purchaseOrder"], "EB-1003")
        self.assertEqual(extraction["lines"][0]["providedUpc"], "860003377529")
        self.assertIn("CLASSIC PET II", extraction["customerIdentification"]["customerSearchText"])

    def test_generated_xlsx_artifact_is_a_valid_open_xml_package(self) -> None:
        workbook = order_to_xlsx_bytes(CsvOrderProcessor().parse(_order(), "item_number,quantity\nABC,2\n"))

        with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
            self.assertIn("xl/worksheets/sheet1.xml", archive.namelist())
            self.assertIn(b"provided_item_number", archive.read("xl/worksheets/sheet1.xml"))


class _FakeCosmosAuthRepository:
    def __init__(self, documents_by_container: dict[str, list[dict]]) -> None:
        self.database = _FakeCosmosDatabase(documents_by_container)

    def get(self, container: str, document_id: str) -> None:
        raise ValueError(f"Unknown Cosmos container: {container}")


class _FakeCosmosDatabase:
    def __init__(self, documents_by_container: dict[str, list[dict]]) -> None:
        self.documents_by_container = documents_by_container

    def list_containers(self) -> list[dict[str, str]]:
        return [{"id": name} for name in self.documents_by_container]

    def get_container_client(self, name: str) -> "_FakeCosmosContainer":
        return _FakeCosmosContainer(self.documents_by_container.get(name, []))


class _FakeCosmosContainer:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    def query_items(
        self,
        *,
        query: str,
        parameters: list[dict[str, str]],
        enable_cross_partition_query: bool,
    ) -> list[dict]:
        document_id = next((item["value"] for item in parameters if item.get("name") == "@id"), "")
        return [item for item in self.documents if item.get("id") == document_id]


class _FakeExternalAuthCosmosClient:
    def __init__(self, endpoint: str, credential: object) -> None:
        self.endpoint = endpoint
        self.credential = credential

    def get_database_client(self, name: str) -> "_FakeExternalAuthDatabase":
        return _FakeExternalAuthDatabase()


class _FakeExternalAuthDatabase:
    def get_container_client(self, name: str) -> "_FakeExternalAuthContainer":
        return _FakeExternalAuthContainer()


class _FakeExternalAuthContainer:
    def query_items(
        self,
        *,
        query: str,
        parameters: list[dict[str, str]],
        enable_cross_partition_query: bool,
    ) -> list[dict]:
        return [{"id": "google", "serviceId": "google", "jwt": "external-google-jwt"}]


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            cells.append(
                f'<c r="{_xlsx_column(column_index)}{row_index}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        + "".join(sheet_rows)
        + "</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return output.getvalue()


def _xlsx_column(index: int) -> str:
    column = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        column = chr(ord("A") + remainder) + column
    return column


def _google_document_ai_response() -> dict:
    return {
        "document": {
            "mimeType": "application/pdf",
            "text": "Ship To CLASSIC PET II - MARYSVILLE 3180 GRATIOT BLVD Marysville MI 48040",
            "entities": [
                {"id": "1", "type": "purchase_order", "mentionText": "PO-9001", "confidence": 0.99},
                {"id": "2", "type": "ship_to_name", "mentionText": "CLASSIC PET II - MARYSVILLE", "confidence": 0.98},
                {"id": "3", "type": "ship_to_address", "mentionText": "3180 GRATIOT BLVD\nMarysville MI 48040", "confidence": 0.98},
                {"id": "4", "type": "remit_to_name", "mentionText": "Classic Pet HQ", "confidence": 0.9},
                {"id": "5", "type": "remit_to_address", "mentionText": "100 Billing St", "confidence": 0.9},
                {
                    "id": "6",
                    "type": "line_item",
                    "mentionText": "188010145 860003377529 Treats 2",
                    "confidence": 0.97,
                    "properties": [
                        {"type": "item_number", "mentionText": "188010145", "confidence": 0.99},
                        {"type": "upc_number", "mentionText": "860003377529", "confidence": 0.99},
                        {"type": "description", "mentionText": "Treats", "confidence": 0.95},
                        {"type": "quantity", "mentionText": "2", "confidence": 0.99},
                    ],
                },
            ],
            "pages": [{"pageNumber": 1}],
        }
    }


if __name__ == "__main__":
    unittest.main()
