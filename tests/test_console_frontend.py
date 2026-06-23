from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "apps" / "console"


class ConsoleFrontendTests(unittest.TestCase):
    def test_console_assets_exist_and_reference_guarded_routes(self) -> None:
        index = (CONSOLE / "index.html").read_text(encoding="utf-8")
        app = (CONSOLE / "app.js").read_text(encoding="utf-8")
        server = (CONSOLE / "server.js").read_text(encoding="utf-8")

        self.assertIn('<script src="./app.js" type="module"></script>', index)
        self.assertIn('href="./assets/brand/focus-automate-symbol.svg"', index)
        self.assertIn('src="./assets/brand/focus-automate-main-logo.svg"', index)
        self.assertTrue((CONSOLE / "assets" / "brand" / "focus-automate-symbol.svg").exists())
        self.assertTrue((CONSOLE / "assets" / "brand" / "focus-automate-main-logo.svg").exists())
        self.assertTrue((CONSOLE / "assets" / "brand" / "focus-automate-inverted-logo.svg").exists())
        self.assertIn("/console/dashboard", app)
        self.assertIn("/console/mailboxes", app)
        self.assertIn("/console/microsoft-auth/start", app)
        self.assertIn("/test-connection", app)
        self.assertIn("actionFailed", app)
        self.assertIn("redirectToMicrosoftSignIn", app)
        self.assertIn("/.auth/login/aad", app)
        self.assertIn("busyOverlay", index)
        self.assertIn("beginBusy", app)
        self.assertIn("endBusy", app)
        self.assertIn("closeDetailsButton", app)
        self.assertIn("clearDetails", app)
        self.assertIn("unhandledrejection", app)
        self.assertIn("slugifyId", app)
        self.assertIn("connectionIdFor", app)
        self.assertIn("authorizedUserEmail", app)
        self.assertIn("distributorSelector", index)
        self.assertIn("customerContextLine", index)
        self.assertIn("switchDistributor", app)
        self.assertNotIn("customerFilter", index)
        self.assertNotIn("All downstream customers", index)
        self.assertIn("/console/tenants", app)
        self.assertIn("/console/customers/", app)
        self.assertIn("/console/exceptions/", app)
        self.assertIn("/console/orders/", app)
        self.assertIn("/timeline", app)
        self.assertIn("customerIdentificationFailureCount", app)
        self.assertIn("averageProcessingLatencyMs", app)
        self.assertNotIn('post("/mailboxes"', app)
        self.assertNotIn('post(`/customers/${value(form, "customerId")}/users`', app)
        self.assertIn("x-ms-client-principal", server)
        self.assertIn("ORDER_PROCESSOR_API_BASE_URL", server)
        self.assertIn("/auth/microsoft/callback", server)
        self.assertIn('".svg": "image/svg+xml; charset=utf-8"', server)
        self.assertIn('"Content-Length": Buffer.byteLength(body)', server)
        self.assertIn("proxyRequest.write(body)", server)
        self.assertIn("Distributor Customers", index)
        self.assertIn("Shared Mailbox", index)
        self.assertIn("Mailbox automation", index)
        self.assertIn("Authorized Microsoft login", index)
        self.assertIn("Microsoft Graph Access", index)
        self.assertIn("Downstream Customer List", index)
        self.assertIn("Item List", index)
        self.assertIn("customerListTarget", index)
        self.assertIn("itemListTarget", index)
        self.assertIn("openDownstreamCustomerListButton", index)
        self.assertIn("downstreamCustomerListPage", index)
        self.assertIn("downstreamCustomerSearch", index)
        self.assertIn("exportDownstreamCustomersButton", index)
        self.assertIn("openItemListButton", index)
        self.assertIn("itemListPage", index)
        self.assertIn("itemListSearch", index)
        self.assertIn("exportItemsButton", index)
        self.assertIn("importTargetHtml", app)
        self.assertIn("renderImportTargets", app)
        self.assertIn("renderCustomerDataList", app)
        self.assertIn("exportCustomerDataList", app)
        self.assertIn("rawSourceRow", app)
        self.assertIn("asList", app)
        self.assertIn("itemAlternateIds", app)
        self.assertIn("No records match the current filters.", app)
        self.assertIn("Power Automate POST", app)
        self.assertIn("Customer Automation Settings", index)
        self.assertNotIn("Order attachment types", index)
        self.assertIn("System Settings", index)
        self.assertIn("Supported File Types", index)
        self.assertIn("systemSettingsForm", app)
        self.assertIn("SUPPORTED_FILE_TYPE_OPTIONS", app)
        self.assertIn("SYSTEM_TENANT_ID", app)
        self.assertIn("Excel XLT", index)
        self.assertIn("Follow-up email move", index)
        self.assertIn("nonOrderMoveMode", app)
        self.assertIn('"knownCustomerNonOrder"', app)
        self.assertIn("Processor Profile", index)
        self.assertIn("Output Profile", index)
        self.assertIn("outputFieldChoices", index)
        self.assertIn("processorProfileForm", app)
        self.assertIn("outputProfileForm", app)
        self.assertIn("automationSettingsFromForm", app)
        self.assertIn("loadRoutingRule", app)
        self.assertNotIn('data-view="routing"', index)
        detail_page = index[index.index('id="distributorDetailPage"') : index.index('id="distributorEditPage"')]
        edit_page = index[index.index('id="distributorEditPage"') :]
        self.assertIn("authorizeMicrosoftButton", detail_page)
        self.assertNotIn("authorizeMicrosoftButton", edit_page)
        self.assertNotIn('id="customerForm"', index)
        self.assertNotIn('id="customerRuleForm"', index)
        mailbox_form = index[index.index('<form id="mailboxForm"') : index.index('<section class="editor">')]
        self.assertNotIn('name="customerId" placeholder="Customer id"', index)
        self.assertNotIn('name="customerId"', mailbox_form)

    def test_console_javascript_parses(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("Node.js is not installed")
        subprocess.run(["node", "--check", str(CONSOLE / "app.js")], check=True)
        subprocess.run(["node", "--check", str(CONSOLE / "server.js")], check=True)


if __name__ == "__main__":
    unittest.main()
