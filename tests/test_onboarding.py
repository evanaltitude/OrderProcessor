from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.onboarding import (
    load_onboarding_package,
    validate_onboarding_package,
    validate_onboarding_package_data,
)


ROOT = Path(__file__).resolve().parents[1]
PILOT_PACKAGE = ROOT / "onboarding" / "reference" / "pilot-csv-parse" / "onboarding-package.json"
TEMPLATE_PACKAGE = ROOT / "onboarding" / "templates" / "customer-onboarding-template.json"


class OnboardingTests(unittest.TestCase):
    def test_reference_pilot_onboarding_package_passes_with_fixture_execution(self) -> None:
        result = validate_onboarding_package(PILOT_PACKAGE)
        checks = {check["name"]: check for check in result["checks"]}

        self.assertTrue(result["passed"], result["checks"])
        self.assertEqual(result["customerId"], "pilot-customer")
        self.assertEqual(result["summary"]["fixtureCount"], 1)
        self.assertTrue(result["fixtureResults"][0]["passed"])

        for required_check in [
            "customerProfile",
            "microsoftAuthConnections",
            "monitoredMailboxes",
            "consoleUserAssignments",
            "routingRules",
            "processorProfiles",
            "outputProfiles",
            "importSources",
            "csrRouting",
            "testFixtures",
            "batchMigrationPlan",
        ]:
            self.assertIn(required_check, checks)
            self.assertTrue(checks[required_check]["passed"], checks[required_check])

    def test_validator_reports_missing_customer_item_import_source(self) -> None:
        package, base_path = load_onboarding_package(PILOT_PACKAGE)
        broken = copy.deepcopy(package)
        broken["importSources"].pop("itemList")

        result = validate_onboarding_package_data(broken, base_path, run_fixtures=False)
        import_check = next(check for check in result["checks"] if check["name"] == "importSources")

        self.assertFalse(result["passed"])
        self.assertFalse(import_check["passed"])

    def test_validator_rejects_plain_secret_values(self) -> None:
        package, base_path = load_onboarding_package(PILOT_PACKAGE)
        broken = copy.deepcopy(package)
        broken["microsoftAuthConnections"]["items"][0]["clientSecret"] = "not-allowed"

        result = validate_onboarding_package_data(broken, base_path, run_fixtures=False)
        auth_check = next(check for check in result["checks"] if check["name"] == "microsoftAuthConnections")

        self.assertFalse(result["passed"])
        self.assertFalse(auth_check["passed"])

    def test_onboarding_template_contains_required_sections(self) -> None:
        package, _ = load_onboarding_package(TEMPLATE_PACKAGE)

        for required_section in [
            "customerProfile",
            "microsoftAuthConnections",
            "monitoredMailboxes",
            "consoleUsers",
            "customerUserAssignments",
            "routingRules",
            "processorProfiles",
            "outputProfiles",
            "importSources",
            "csrRouting",
            "testFixtures",
            "migrationPlan",
            "cutoverGates",
        ]:
            self.assertIn(required_section, package)


if __name__ == "__main__":
    unittest.main()
