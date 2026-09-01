"""Opt-in real Multisim gate for read-only DesignPatch evaluation."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.design_patch_evaluation import (
    DesignPatchEvaluationService,
    read_design_patch_evaluation,
)
from multisim_mcp.eda_core import (
    CircuitComponent,
    CircuitDesign,
    DesignPatch,
    PatchOperation,
)


@unittest.skipUnless(
    os.environ.get("MULTISIM_MCP_RUN_REAL_TESTS") == "1",
    "set MULTISIM_MCP_RUN_REAL_TESTS=1 on a licensed Multisim workstation",
)
class RealDesignPatchEvaluationTest(unittest.TestCase):
    def test_divider_patch_resolves_real_requirement_without_source_mutation(
        self,
    ) -> None:
        from multisim_mcp.server import _experiment_application_service

        design = CircuitDesign(
            design_id="real-readonly-patch-evaluation",
            title="Real read-only patch evaluation",
            revision=0,
            components=(
                CircuitComponent("V1", "V", ("in", "0"), value="10"),
                CircuitComponent("R1", "R", ("in", "out"), value="1k"),
                CircuitComponent("R2", "R", ("out", "0"), value="1k"),
            ),
            source_netlist="V1 in 0 10\nR1 in out 1k\nR2 out 0 1k\n.end\n",
        )
        patch = DesignPatch(
            patch_id="real-readonly-r2-2k",
            design_id=design.design_id,
            base_revision=design.revision,
            description="Retest a 2 kOhm lower divider resistor without adoption",
            operations=(
                PatchOperation(
                    "set_component_value",
                    "R2.value",
                    "1k",
                    "2k",
                    "move V(out) from 5 V to approximately 6.667 V",
                ),
            ),
        )
        spec = {
            "schema_version": 1,
            "title": "Real read-only patch retest",
            "commands": "op",
            "requirements": [
                {
                    "id": "divider-output",
                    "metric": "mean",
                    "signal": "V(out)",
                    "operator": "between",
                    "lower": 6.5,
                    "upper": 6.8,
                    "unit": "V",
                }
            ],
            "theoretical_values": {"divider-output": 6.6666666667},
        }
        original = design.to_dict()
        with tempfile.TemporaryDirectory(
            prefix="multisim-mcp-real-patch-eval-"
        ) as tmp:
            output = Path(tmp) / "evaluation"
            result = DesignPatchEvaluationService(
                _experiment_application_service()
            ).run(
                design,
                patch,
                spec,
                str(output),
                regenerate_source_netlist=True,
                timeout_per_experiment=120.0,
                max_points=2000,
            )
            stored = read_design_patch_evaluation(str(output), verify=True)
            before = json.loads(
                (output / "comparison" / "experiments" / "baseline" / "verification.json").read_text(
                    encoding="utf-8"
                )
            )
            after = json.loads(
                (output / "comparison" / "experiments" / "candidate" / "verification.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["status"], "candidate-improved-and-passed")
        self.assertTrue(result["adoption_eligible"])
        self.assertTrue(result["approval_required_before_apply"])
        self.assertFalse(result["source_design_modified"])
        self.assertFalse(result["candidate_persisted_as_source"])
        self.assertEqual(before["overall_status"], "fail")
        self.assertEqual(after["overall_status"], "pass")
        self.assertAlmostEqual(
            before["requirements"][0]["measurement"]["value"], 5.0, places=4
        )
        self.assertAlmostEqual(
            after["requirements"][0]["measurement"]["value"],
            6.6666666667,
            places=4,
        )
        self.assertGreaterEqual(
            stored["diagnosis_delta"]["resolved_finding_count"], 1
        )
        self.assertEqual(design.to_dict(), original)


if __name__ == "__main__":
    unittest.main()
