"""Opt-in real Multisim gate for the verified DesignPatch workflow."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.design_patch_workflow import (
    approve_verified_patch_application,
    execute_verified_patch_application,
    read_verified_patch_workflow,
)
from multisim_mcp.design_patch_transactions import read_design_document
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
class RealVerifiedPatchWorkflowTest(unittest.TestCase):
    def test_voltage_divider_candidate_passes_before_commit(self) -> None:
        from multisim_mcp.server import _experiment_application_service

        design_value = CircuitDesign(
            design_id="real-verified-divider",
            title="Real verified divider",
            revision=0,
            components=(
                CircuitComponent("V1", "V", ("in", "0"), value="10"),
                CircuitComponent("R1", "R", ("in", "out"), value="1k"),
                CircuitComponent("R2", "R", ("out", "0"), value="1k"),
            ),
            source_netlist="V1 in 0 10\nR1 in out 1k\nR2 out 0 1k\n.end\n",
        )
        patch_value = DesignPatch(
            patch_id="real-divider-r2-2k",
            design_id=design_value.design_id,
            base_revision=design_value.revision,
            description="Verify a 2 kOhm lower divider resistor",
            operations=(
                PatchOperation(
                    "set_component_value",
                    "R2.value",
                    "1k",
                    "2k",
                    "exercise the verified patch commit boundary",
                ),
            ),
        )
        plan_value = {
            "schema_version": 1,
            "title": "Real verified divider candidate",
            "commands": "op",
            "requirements": [
                {
                    "id": "divider-output",
                    "metric": "mean",
                    "signal": "V(out)",
                    "operator": "approximately",
                    "target": 6.6666666667,
                    "tolerance_percent": 2.0,
                    "unit": "V",
                }
            ],
            "theoretical_values": {"divider-output": 6.6666666667},
        }
        with tempfile.TemporaryDirectory(prefix="multisim-mcp-real-workflow-") as tmp:
            root = Path(tmp)
            design = root / "design.json"
            patch_file = root / "patch.json"
            plan = root / "plan.json"
            receipt = root / "receipt.json"
            experiment = root / "experiment"
            manifest = root / "workflow.json"
            store = root / "approvals"
            design.write_text(
                json.dumps(design_value.to_dict(), indent=2), encoding="utf-8"
            )
            patch_file.write_text(
                json.dumps(patch_value.to_dict(), indent=2), encoding="utf-8"
            )
            plan.write_text(json.dumps(plan_value, indent=2), encoding="utf-8")
            common = {
                "output_path": None,
                "in_place": True,
                "receipt_path": str(receipt),
                "regenerate_source_netlist": True,
                "verification_plan_path": str(plan),
                "experiment_output": str(experiment),
                "workflow_manifest": str(manifest),
                "timeout_seconds": 120.0,
                "max_points": 2000,
                "approval_store": str(store),
            }
            approval = approve_verified_patch_application(
                str(design), str(patch_file), **common
            )
            result = execute_verified_patch_application(
                _experiment_application_service(),
                str(design),
                str(patch_file),
                approval_token=approval["approval_token"],
                **common,
            )
            _, final_design = read_design_document(str(design))
            _, workflow = read_verified_patch_workflow(str(manifest))
            self.assertTrue(result["success"], result)
            self.assertEqual(result["verification_status"], "pass")
            self.assertEqual(final_design.revision, 1)
            self.assertEqual(final_design.components[2].value, "2k")
            self.assertEqual(workflow["state"], "committed")


if __name__ == "__main__":
    unittest.main()
