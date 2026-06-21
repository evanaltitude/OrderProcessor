param(
    [string]$OutputRoot = ".\samples\phase-2"
)

$ErrorActionPreference = "Stop"

function Write-TextFile {
    param(
        [string]$Path,
        [string]$Content
    )

    $folder = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $Content | Set-Content -LiteralPath $Path -Encoding UTF8
}

function New-MinimalXlsx {
    param(
        [string]$Path,
        [object[]]$Rows
    )

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("order-processor-xlsx-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $tempRoot "_rels") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $tempRoot "xl\_rels") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $tempRoot "xl\worksheets") | Out-Null

    $sheetRows = New-Object System.Collections.Generic.List[string]
    $rowNumber = 1
    foreach ($row in $Rows) {
        $cells = New-Object System.Collections.Generic.List[string]
        $columnNumber = 1
        foreach ($value in $row) {
            $columnName = [char](64 + $columnNumber)
            $escaped = [System.Security.SecurityElement]::Escape([string]$value)
            $cells.Add("<c r=`"$columnName$rowNumber`" t=`"inlineStr`"><is><t>$escaped</t></is></c>")
            $columnNumber++
        }
        $sheetRows.Add("<row r=`"$rowNumber`">$($cells -join '')</row>")
        $rowNumber++
    }

    Write-TextFile -Path (Join-Path $tempRoot "[Content_Types].xml") -Content @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>
'@
    Write-TextFile -Path (Join-Path $tempRoot "_rels\.rels") -Content @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
'@
    Write-TextFile -Path (Join-Path $tempRoot "xl\workbook.xml") -Content @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Orders" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
'@
    Write-TextFile -Path (Join-Path $tempRoot "xl\_rels\workbook.xml.rels") -Content @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
'@
    Write-TextFile -Path (Join-Path $tempRoot "xl\worksheets\sheet1.xml") -Content @"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    $($sheetRows -join [Environment]::NewLine)
  </sheetData>
</worksheet>
"@

    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $zipPath = [System.IO.Path]::ChangeExtension($Path, ".zip")
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -Path (Join-Path $tempRoot "*") -DestinationPath $zipPath -Force
    Move-Item -LiteralPath $zipPath -Destination $Path -Force
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}

$root = Join-Path (Resolve-Path ".").Path $OutputRoot
New-Item -ItemType Directory -Force -Path $root | Out-Null

Write-TextFile -Path (Join-Path $root "csv\order-csv-parse.csv") -Content @'
10001,012345678905,2
10002,000123456789,4
10003,,1
'@

Write-TextFile -Path (Join-Path $root "csv\order-csv-parse-with-header.csv") -Content @'
item_number,upc,quantity,description
10001,012345678905,2,Dog Food 25 lb
10002,000123456789,4,Cat Treats 8 oz
10003,,1,Bird Seed 5 lb
'@

New-MinimalXlsx -Path (Join-Path $root "xlsx\generic-ai-header-order.xlsx") -Rows @(
    @("SupplierCode", "Barcode", "QtyOrdered", "PackSize", "Description"),
    @("188010145", "860003377529", "1", "1", "Dog Food 25 lb"),
    @("188010146", "860003377530", "3", "1", "Cat Treats 8 oz")
)

New-MinimalXlsx -Path (Join-Path $root "customer-specific\petland-branch-order.xlsx") -Rows @(
    @("Branch", "SupplierCode", "Barcode", "QtyOrdered"),
    @("DAYTON", "188010145", "860003377529", "1"),
    @("DAYTON", "188010146", "860003377530", "2")
)

Write-TextFile -Path (Join-Path $root "xls-xlt\legacy-order.xls") -Content @'
<html><body><table>
<tr><th>OUR ITEM NO.</th><th>UPC #</th><th>Quantity</th></tr>
<tr><td>188010145</td><td>860003377529</td><td>1</td></tr>
<tr><td>188010146</td><td>860003377530</td><td>3</td></tr>
</table></body></html>
'@

Write-TextFile -Path (Join-Path $root "xls-xlt\legacy-template.xlt") -Content @'
<html><body><table>
<tr><th>Vendor item</th><th>UPC</th><th>Order Qty</th></tr>
<tr><td>188010145</td><td>860003377529</td><td>1</td></tr>
</table></body></html>
'@

Write-TextFile -Path (Join-Path $root "pdf\order-document.pdf") -Content @'
%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 134 >>
stream
BT /F1 12 Tf 72 720 Td (PO PDF-1001) Tj 0 -20 Td (Item 188010145 UPC 860003377529 Qty 1) Tj 0 -20 Td (Item 188010146 UPC 860003377530 Qty 3) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000247 00000 n 
0000000432 00000 n 
trailer
<< /Root 1 0 R /Size 6 >>
startxref
502
%%EOF
'@

Write-TextFile -Path (Join-Path $root "email-body\generic-email-body.eml") -Content @'
From: buyer@example.com
To: customer-orders@example.com
Subject: PO EB-1001

Please process this order:

Item Number | UPC | Quantity
188010145 | 860003377529 | 1
188010146 | 860003377530 | 3
'@

Write-TextFile -Path (Join-Path $root "customer-specific\market-place-pet-supplies.eml") -Content @'
From: orders@marketplace.example
To: customer-orders@example.com
Subject: Cust: 102528 Rte: 200 - market place pet supplies order - PO# Order20241120

019962896026-1
064992202408-3
064992517250-1
'@

$manifest = [pscustomobject]@{
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    purpose = "Synthetic Phase 2 fixtures for reverse engineering and future golden-file tests."
    fixtures = @(
        @{ sourceType = "CSV"; path = "csv/order-csv-parse.csv"; targetFlow = "orderProcess - CSV Parse"; notes = "Headerless CSV exercises generic Column 1..N behavior." },
        @{ sourceType = "CSV"; path = "csv/order-csv-parse-with-header.csv"; targetFlow = "orderProcess - CSV Parse"; notes = "Headered CSV exercises future robust parser behavior." },
        @{ sourceType = "XLSX"; path = "xlsx/generic-ai-header-order.xlsx"; targetFlow = "orderProcess - XLSX - AI Header - moduleID"; notes = "Generic workbook with SupplierCode, Barcode, QtyOrdered." },
        @{ sourceType = "XLS/XLT"; path = "xls-xlt/legacy-order.xls"; targetFlow = "orderProcess - XLS or XLT - AI Header - moduleID"; notes = "HTML-backed legacy Excel file placeholder." },
        @{ sourceType = "XLS/XLT"; path = "xls-xlt/legacy-template.xlt"; targetFlow = "orderProcess - XLS or XLT - AI Header - moduleID"; notes = "Template-style legacy Excel placeholder." },
        @{ sourceType = "PDF"; path = "pdf/order-document.pdf"; targetFlow = "orderProcess - Google Document AI -  PDF"; notes = "Simple PDF text fixture for Document Intelligence replacement tests." },
        @{ sourceType = "Email body"; path = "email-body/generic-email-body.eml"; targetFlow = "orderProcess - Email Body - moduleID"; notes = "Generic table-like email body." },
        @{ sourceType = "Customer-specific"; path = "customer-specific/market-place-pet-supplies.eml"; targetFlow = "orderProcess - Email Body - Market Place Pet Supplies w Item Validation"; notes = "Subject PO and itemNumber-quantity body lines." },
        @{ sourceType = "Customer-specific"; path = "customer-specific/petland-branch-order.xlsx"; targetFlow = "orderProcess - XLSX - AI Header ID - Petland"; notes = "Petland branch workbook pattern." }
    )
}

$manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $root "manifest.json") -Encoding UTF8

Write-TextFile -Path (Join-Path $root "README.md") -Content @"
# Phase 2 Sample Fixtures

Synthetic fixtures for reverse engineering and future golden-file tests.

These files are not production customer data. They are neutral examples that cover the source types required by Phase 2:

- CSV
- XLSX
- XLS/XLT
- PDF
- Email body
- Customer-specific cases

Use `manifest.json` as the machine-readable catalog.
"@

Write-Host "Wrote Phase 2 sample fixtures to $root"
