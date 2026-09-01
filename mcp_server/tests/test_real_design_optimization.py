"""Opt-in real Multisim gate for bounded component-value optimization."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from multisim_mcp.design_optimization import (
    DesignOptimizationService,
    read_design_optimization,
)
from multisim_mcp.eda_core import CircuitComponent, CircuitDesign
from multisim_mcp.job_engine import ExperimentJobManager


@unittest.skipUnless(
    os.environ.get("MULTISIM_MCP_RUN_REAL_TESTS") == "1",
    "set MULTISIM_MCP_RUN_REAL_TESTS=1 on a licensed Multisim workstation",
)
class RealDesignOptimizationTest(unittest.TestCase):
    def test_voltage_divider_selects_two_kilohm_candidate(self) -> None:
        from multisim_mcp.server import _experiment_application_service

        design = CircuitDesign(
            design_id="real-optimization-divider",
            title="Real optimization divider",
            revision=0,
            components=(
                CircuitComponent("V1", "V", ("in", "0"), value="10"),
                CircuitComponent("R1", "R", ("in", "out"), value="1k"),
                CircuitComponent("R2", "R", ("out", "0"), value="1k"),
            ),
            source_netlist="V1 in 0 10\nR1 in out 1k\nR2 out 0 1k\n.end\n",
        )
        original = design.to_dict()
        spec = {
            "schema_version": 1,
            "title": "Real divider optimization",
            "variables": [
                {
                    "refdes": "R2",
                    "series": {
                        "name": "E24",
                        "minimum": "1.8k",
                        "maximum": "2.2k",
                    },
                    "inventory": [
                        {
                            "value": "1k",
                            "part_number": "REAL-R-1K",
                            "unit_cost": 0.01,
                            "stock": 0,
                        },
                        {
                            "value": "1.8k",
                            "part_number": "REAL-R-1K8",
                            "unit_cost": 0.03,
                            "stock": 20,
                        },
                        {
                            "value": "2k",
                            "part_number": "REAL-R-2K",
                            "unit_cost": 0.04,
                            "stock": 20,
                        },
                        {
                            "value": "2.2k",
                            "part_number": "REAL-R-2K2",
                            "unit_cost": 0.08,
                            "stock": 20,
                        },
                    ],
                }
            ],
            "commands": "op",
            "requirements": [
                {
                    "id": "divider-output",
                    "metric": "mean",
                    "signal": "V(out)",
                    "operator": "between",
                    "lower": 4.9,
                    "upper": 8.1,
                    "unit": "V",
                }
            ],
            "theoretical_values": {"divider-output": 6.6666666667},
            "objective": {
                "requirement_id": "divider-output",
                "goal": "target",
                "target": 6.6666666667,
            },
            "max_experiments": 4,
            "procurement": {
                "currency": "CNY",
                "require_in_stock": True,
                "max_total_unit_cost": 0.05,
                "prefer_lower_cost": True,
            },
        }
        with tempfile.TemporaryDirectory(prefix="multisim-mcp-real-opt-") as tmp:
            output = Path(tmp) / "optimization"
            result = DesignOptimizationService(_experiment_application_service()).run(
                design,
                spec,
                str(output),
                timeout_per_experiment=120.0,
                max_points=2000,
            )
            stored = read_design_optimization(str(output), verify=True)
            self.assertTrue(result["success"], result)
            self.assertEqual(result["status"], "optimized")
            self.assertEqual(result["best_solution"]["values"], {"R2": "2k"})
            self.assertEqual(
                result["best_solution"]["procurement"]["selections"][0]["part_number"],
                "REAL-R-2K",
            )
            self.assertEqual(result["experiments_attempted"], 4)
            self.assertEqual(result["procurement_rejected_count"], 2)
            self.assertEqual(stored["best_evaluation_id"], "candidate-002")
            self.assertEqual(design.to_dict(), original)

            manager = ExperimentJobManager(Path(tmp) / "job-state")
            try:
                submitted = manager.submit(
                    {
                        "job_kind": "optimization",
                        "design": design.to_dict(),
                        "optimization_spec": spec,
                        "output_dir": str(Path(tmp) / "durable-optimization"),
                        "timeout_per_experiment": 120.0,
                        "max_points": 2000,
                        "job_timeout": 900.0,
                        "heartbeat_timeout": 180.0,
                    }
                )
                deadline = time.monotonic() + 900.0
                durable = manager.get(submitted["job_id"])
                while (
                    durable["state"]
                    not in {"succeeded", "failed", "cancelled", "timed_out"}
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.25)
                    durable = manager.get(submitted["job_id"])
                self.assertEqual(durable["state"], "succeeded", durable)
                self.assertEqual(durable["result"]["status"], "optimized")
                self.assertEqual(
                    durable["result"]["best_solution"]["values"], {"R2": "2k"}
                )
                self.assertEqual(durable["result"]["resume_count"], 0)
            finally:
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
