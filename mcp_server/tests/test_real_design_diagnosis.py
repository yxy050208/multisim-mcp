"""Opt-in real Multisim gate for deterministic experiment-backed diagnosis."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.design_diagnosis import (
    DesignDiagnosisService,
    load_experiment_diagnosis_evidence,
)
from multisim_mcp.eda_core import CircuitComponent, CircuitDesign
from multisim_mcp.experiment_service import ExperimentRequest


@unittest.skipUnless(
    os.environ.get("MULTISIM_MCP_RUN_REAL_TESTS") == "1",
    "set MULTISIM_MCP_RUN_REAL_TESTS=1 on a licensed Multisim workstation",
)
class RealDesignDiagnosisTest(unittest.TestCase):
    def test_divider_failure_is_bound_to_real_operating_point_evidence(self) -> None:
        from multisim_mcp.server import _experiment_application_service

        design = CircuitDesign(
            design_id="real-diagnosis-divider",
            title="Real diagnosis divider",
            revision=0,
            components=(
                CircuitComponent("V1", "V", ("in", "0"), value="10"),
                CircuitComponent("R1", "R", ("in", "out"), value="1k"),
                CircuitComponent("R2", "R", ("out", "0"), value="1k"),
            ),
            source_netlist="V1 in 0 10\nR1 in out 1k\nR2 out 0 1k\n.end\n",
        )
        original = design.to_dict()
        requirement = {
            "id": "intentionally-failing-output",
            "metric": "mean",
            "signal": "V(out)",
            "operator": "between",
            "lower": 8.0,
            "upper": 9.0,
            "unit": "V",
        }
        with tempfile.TemporaryDirectory(prefix="multisim-mcp-real-diagnosis-") as tmp:
            output = Path(tmp) / "experiment"
            experiment = _experiment_application_service().run(
                ExperimentRequest(
                    design=design,
                    commands="op",
                    output_directory=str(output),
                    title="Real deterministic diagnosis gate",
                    requirements=(requirement,),
                )
            )
            self.assertTrue(experiment["success"], experiment)
            self.assertEqual(experiment["verification"]["overall_status"], "fail")
            evidence = load_experiment_diagnosis_evidence(design, str(output))
            diagnosis = DesignDiagnosisService().run(
                design, experiment_evidence=evidence
            )

        self.assertEqual(evidence["design_binding"], "verified-netlist-match")
        self.assertAlmostEqual(evidence["operating_point"]["V(out)"], 5.0, places=4)
        self.assertIn(
            "requirement-failed",
            {finding["code"] for finding in diagnosis["findings"]},
        )
        self.assertEqual(diagnosis["overall_status"], "error")
        self.assertTrue(diagnosis["read_only"])
        self.assertFalse(diagnosis["source_design_modified"])
        self.assertEqual(design.to_dict(), original)


if __name__ == "__main__":
    unittest.main()
