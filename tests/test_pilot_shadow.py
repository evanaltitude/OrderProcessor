from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from order_processor.pilot_shadow import load_pilot_shadow_case, run_pilot_shadow_case


ROOT = Path(__file__).resolve().parents[1]
PILOT_MANIFEST = ROOT / "samples" / "pilot" / "pilot-shadow-manifest.json"


class PilotShadowTests(unittest.TestCase):
    def test_phase_14_pilot_shadow_run_passes_all_acceptance_gates(self) -> None:
        result = run_pilot_shadow_case(PILOT_MANIFEST)
        checks = {check["name"]: check for check in result["checks"]}

        self.assertTrue(result["passed"], result["checks"])
        self.assertEqual(result["mode"], "shadow")
        self.assertEqual(result["ingest"]["routingOutcome"], "knownOrder")
        self.assertEqual(result["orderRun"]["status"], "completed")
        self.assertEqual(result["exceptionTasks"], [])
        self.assertEqual(result["seedSummary"]["routingRules"], 1)

        for required_check in [
            "routingOutcome",
            "customerIdentification",
            "itemValidation",
            "outputFiles",
            "routingTags",
            "csrFolder",
            "shadowIsolation",
            "plumsailReplacement",
            "errorHandling",
        ]:
            self.assertIn(required_check, checks)
            self.assertTrue(checks[required_check]["passed"], checks[required_check])

        artifact_types = {artifact["type"] for artifact in result["outputArtifacts"]}
        self.assertEqual(artifact_types, {"universalOrderJson", "lineCsv"})

    def test_shadow_run_reports_output_mismatch(self) -> None:
        case = load_pilot_shadow_case(PILOT_MANIFEST)
        case.manifest = copy.deepcopy(case.manifest)
        case.manifest["expected"]["lineOutputText"] = "wrong output\n"
        case.manifest["expected"].pop("lineOutputFile", None)

        result = run_pilot_shadow_case(case)
        output_check = next(check for check in result["checks"] if check["name"] == "outputFiles")

        self.assertFalse(result["passed"])
        self.assertFalse(output_check["passed"])


if __name__ == "__main__":
    unittest.main()
