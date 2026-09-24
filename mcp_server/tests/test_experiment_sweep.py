"""COM-free tests for deterministic sweep expansion."""

from __future__ import annotations

import unittest
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.experiment_sweep import plan_experiment_sweep
from multisim_mcp.sweep_resources import (
    clear_sweep_registry,
    read_sweep_summary,
    read_sweep_text,
    register_sweep,
)


BASE = {
    "schema_version": 1,
    "netlist_template": "V1 in 0 5\nR1 in out {{R1}}\nR2 out 0 1k\n.end\n",
    "commands": "op",
    "measurements": [{"id": "vout", "metric": "mean", "signal": "V(out)"}],
}


class ExperimentSweepPlanTest(unittest.TestCase):
    def test_parameter_cartesian_product_is_stable(self) -> None:
        spec = {
            **BASE,
            "mode": "parameter",
            "netlist_template": BASE["netlist_template"].replace("1k\n.end", "{{R2}}\n.end"),
            "parameters": [
                {"name": "R1", "values": [1000, 2000]},
                {"name": "R2", "values": [500, 1000]},
            ],
        }
        plan = plan_experiment_sweep(spec)
        self.assertEqual(plan["run_count"], 4)
        self.assertEqual(plan["runs"][0]["variables"], {"R1": 1000.0, "R2": 500.0})
        self.assertNotIn("{{", plan["runs"][-1]["netlist"])

    def test_tolerance_includes_nominal_and_all_corners(self) -> None:
        spec = {
            **BASE,
            "mode": "tolerance",
            "parameters": [{"name": "R1", "nominal": 1000, "tolerance_percent": 10}],
        }
        plan = plan_experiment_sweep(spec)
        self.assertEqual([run["variables"]["R1"] for run in plan["runs"]], [1000.0, 900.0, 1100.0])

    def test_temperature_injects_safe_directive(self) -> None:
        spec = {
            **BASE,
            "mode": "temperature",
            "netlist_template": BASE["netlist_template"].replace("{{R1}}", "1k"),
            "parameters": [],
            "temperatures": [-40, 25, 85],
        }
        plan = plan_experiment_sweep(spec)
        self.assertEqual(plan["run_count"], 3)
        self.assertIn(".temp -40", plan["runs"][0]["netlist"])

    def test_monte_carlo_is_seeded_and_bounded(self) -> None:
        spec = {
            **BASE,
            "mode": "monte_carlo",
            "parameters": [
                {"name": "R1", "nominal": 1000, "distribution": "normal", "sigma_percent": 5, "minimum": 800},
            ],
            "runs": 5,
            "seed": 42,
        }
        first = plan_experiment_sweep(spec)
        second = plan_experiment_sweep(spec)
        self.assertEqual(first["runs"], second["runs"])
        self.assertTrue(all(run["variables"]["R1"] >= 800 for run in first["runs"]))

    def test_injection_unknown_placeholders_and_run_explosion_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            plan_experiment_sweep(
                {
                    **BASE,
                    "mode": "parameter",
                    "parameters": [{"name": "R1", "values": ["1k; shell"]}],
                }
            )
        with self.assertRaises(ValueError):
            plan_experiment_sweep(
                {
                    **BASE,
                    "mode": "parameter",
                    "netlist_template": BASE["netlist_template"] + "* {{UNKNOWN}}\n",
                    "parameters": [{"name": "R1", "values": [1000]}],
                }
            )
        with self.assertRaises(ValueError):
            plan_experiment_sweep(
                {
                    **BASE,
                    "mode": "parameter",
                    "parameters": [{"name": "R1", "values": list(range(101))}],
                }
            )
        with self.assertRaisesRegex(ValueError, "unknown SweepSpec"):
            plan_experiment_sweep(
                {
                    **BASE,
                    "mode": "parameter",
                    "parameters": [{"name": "R1", "values": [1000]}],
                    "typo": True,
                }
            )


class SweepResourceTest(unittest.TestCase):
    def tearDown(self) -> None:
        clear_sweep_registry()

    def test_register_and_read_fixed_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "summary.json").write_text(
                json.dumps({"schema_version": 1, "result_type": "sweep"}),
                encoding="utf-8",
            )
            (root / "data.csv").write_text("run_id,status\n1,measured\n", encoding="utf-8")
            registered = register_sweep(str(root))
            self.assertRegex(registered["sweep_id"], r"^sweep-[0-9a-f]{24}$")
            self.assertEqual(
                read_sweep_summary(registered["sweep_id"])["result_type"], "sweep"
            )
            self.assertIn("run_id", read_sweep_text(registered["sweep_id"], "data"))
            with self.assertRaises(ValueError):
                read_sweep_text("../../secret", "data")
            with patch.dict(
                os.environ, {"MULTISIM_MCP_RESOURCE_MAX_BYTES": "4"}
            ):
                with self.assertRaisesRegex(ValueError, "size limit"):
                    read_sweep_text(registered["sweep_id"], "data")


if __name__ == "__main__":
    unittest.main()
