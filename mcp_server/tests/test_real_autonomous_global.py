"""Opt-in real Multisim gates for topology search and autonomous correction."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.autonomous_correction import AutonomousDesignCorrectionService
from multisim_mcp.eda_core import CircuitComponent, CircuitDesign
from multisim_mcp.global_optimization import GlobalDesignOptimizationService


@unittest.skipUnless(
    os.environ.get("MULTISIM_MCP_RUN_REAL_TESTS") == "1",
    "set MULTISIM_MCP_RUN_REAL_TESTS=1 on a licensed Multisim workstation",
)
class RealAutonomousGlobalTest(unittest.TestCase):
    @staticmethod
    def _design() -> CircuitDesign:
        return CircuitDesign(
            design_id="real-topology-divider",
            title="Real topology divider",
            revision=0,
            components=(
                CircuitComponent("V1", "V", ("in", "0"), value="10"),
                CircuitComponent("R1", "R", ("in", "out"), value="1k"),
            ),
            source_netlist="V1 in 0 10\nR1 in out 1k\n.end\n",
        )

    @staticmethod
    def _load_component() -> dict[str, object]:
        return {
            "refdes": "R2",
            "kind": "R",
            "nodes": ["out", "0"],
            "value": "2k",
            "model": None,
            "parameters": {},
            "annotations": {},
        }

    @classmethod
    def _verification(cls) -> dict[str, object]:
        return {
            "schema_version": 1,
            "title": "Real topology correction",
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

    def test_real_global_search_selects_added_load_topology(self) -> None:
        from multisim_mcp.server import _experiment_application_service

        design = self._design()
        original = design.to_dict()
        spec = {
            **self._verification(),
            "dimensions": [
                {
                    "id": "load-topology",
                    "kind": "topology_choice",
                    "include_baseline": True,
                    "choices": [
                        {
                            "choice_id": "add-2k-load",
                            "operations": [
                                {
                                    "operation": "add_component",
                                    "target": "R2",
                                    "before": None,
                                    "after": self._load_component(),
                                    "reason": "Close the divider to ground",
                                }
                            ],
                        }
                    ],
                }
            ],
            "objectives": [
                {
                    "requirement_id": "divider-output",
                    "goal": "target",
                    "target": 6.6666666667,
                }
            ],
            "max_experiments": 2,
            "search_strategy": "exhaustive",
            "selection_policy": "weighted_compromise",
        }
        with tempfile.TemporaryDirectory(prefix="multisim-mcp-real-global-") as tmp:
            result = GlobalDesignOptimizationService(
                _experiment_application_service()
            ).run(design, spec, str(Path(tmp) / "global"), timeout_per_experiment=120)
            self.assertTrue(result["success"], result)
            self.assertEqual(result["experiments_attempted"], 2)
            self.assertEqual(result["recommended_solution"]["kind"], "candidate")
            patch = json.loads(
                Path(result["recommended_solution"]["patch_path"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(patch["operations"][0]["operation"], "add_component")
            self.assertEqual(design.to_dict(), original)

    def test_real_autonomous_loop_accepts_added_load_topology(self) -> None:
        from multisim_mcp.server import _experiment_application_service

        design = self._design()
        original = design.to_dict()

        def planner(
            current: CircuitDesign,
            diagnosis: object,
            spec: object,
            history: object,
            round_number: int,
        ) -> list[dict[str, object]]:
            del diagnosis, spec, history
            return [
                {
                    "schema_version": 1,
                    "patch_id": f"real-add-load-{round_number}",
                    "design_id": current.design_id,
                    "base_revision": current.revision,
                    "description": "Add the missing divider load",
                    "operations": [
                        {
                            "operation": "add_component",
                            "target": "R2",
                            "before": None,
                            "after": self._load_component(),
                            "reason": "Correct the measured divider output",
                        }
                    ],
                }
            ]

        spec = {
            **self._verification(),
            "objectives": [
                {
                    "requirement_id": "divider-output",
                    "goal": "target",
                    "target": 6.6666666667,
                }
            ],
            "max_rounds": 2,
            "max_candidates_per_round": 2,
            "require_strict_improvement": True,
            "stop_on_first_pass": True,
        }
        with tempfile.TemporaryDirectory(prefix="multisim-mcp-real-correction-") as tmp:
            result = AutonomousDesignCorrectionService(
                _experiment_application_service(), planner
            ).run(
                design,
                spec,
                str(Path(tmp) / "correction"),
                timeout_per_experiment=120,
            )
            self.assertTrue(result["success"], result)
            self.assertEqual(result["experiments_attempted"], 2)
            self.assertTrue(result["adoption_eligible"])
            patch = json.loads(
                Path(result["final_patch_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(patch["operations"][0]["operation"], "add_component")
            self.assertEqual(design.to_dict(), original)


if __name__ == "__main__":
    unittest.main()
