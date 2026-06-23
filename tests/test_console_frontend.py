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
        self.assertIn("/console/dashboard", app)
        self.assertIn("/console/mailboxes", app)
        self.assertIn("/console/microsoft-auth/start", app)
        self.assertIn("/test-connection", app)
        self.assertIn("actionFailed", app)
        self.assertIn("redirectToMicrosoftSignIn", app)
        self.assertIn("/.auth/login/aad", app)
        self.assertIn("slugifyId", app)
        self.assertIn("connectionIdFor", app)
        self.assertIn("authorizedUserEmail", app)
        self.assertIn("includeCustomerFilter: false", app)
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
        self.assertIn("Distributor Customers", index)
        self.assertIn("Shared Mailbox", index)
        self.assertIn("Authorized Microsoft login", index)
        self.assertIn("Microsoft Graph Access", index)
        self.assertIn("Downstream Customer List", index)
        self.assertIn("Item List", index)
        self.assertIn("Customer Automation Settings", index)
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
