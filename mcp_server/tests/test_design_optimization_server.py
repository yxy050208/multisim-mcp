"""Thin MCP adapter tests for optimize_design."""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from multisim_mcp import server
from multisim_mcp.eda_core import CircuitDesign


DESIGN = {
    "schema_version": 1,
    "design_id": "adapter-divider",
    "title": "Adapter divider",
    "revision": 0,
    "components": [
        {
            "refdes": "R1",
            "kind": "R",
            "nodes": ["in", "out"],
            "value": "1k",
            "model": None,
            "parameters": {},
        }
    ],
    "parameters": {},
    "annotations": {},
}

SPEC = {
    "schema_version": 1,
    "title": "Durable adapter optimization",
    "variables": [{"refdes": "R1", "values": ["1k", "2k"]}],
    "commands": "op",
    "requirements": [
        {
            "id": "vout",
            "metric": "mean",
            "signal": "V(out)",
            "operator": "between",
            "lower": 0.0,
            "upper": 10.0,
            "unit": "V",
        }
    ],
    "objective": {"requirement_id": "vout", "goal": "maximize"},
    "max_experiments": 2,
}


class DesignOptimizationServerTest(unittest.TestCase):
    def test_adapter_converts_design_and_forwards_runtime_limits(self) -> None:
        service = Mock()
        service.run.return_value = {"success": True, "status": "baseline_best"}
        spec = {"schema_version": 1}
        with patch.object(server, "_design_optimization_service", return_value=service):
            result = server.optimize_design(
                DESIGN,
                spec,
                "C:/optimization-output",
                timeout_per_experiment=45.0,
                max_points=321,
            )
        self.assertTrue(result["success"])
        args, kwargs = service.run.call_args
        self.assertIsInstance(args[0], CircuitDesign)
        self.assertIs(args[1], spec)
        self.assertEqual(args[2], "C:/optimization-output")
        self.assertEqual(kwargs["timeout_per_experiment"], 45.0)
        self.assertEqual(kwargs["max_points"], 321)

    def test_durable_adapter_validates_and_persists_transport_neutral_inputs(self) -> None:
        manager = Mock()
        manager.submit.return_value = {
            "success": True,
            "job_id": "job-" + "a" * 32,
            "state": "queued",
            "status_uri": "multisim://jobs/job-" + "a" * 32,
            "output_dir": "placeholder",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            server, "_job_manager", return_value=manager
        ):
            output = str(Path(tmp) / "optimization")
            result = server.submit_design_optimization(
                DESIGN,
                SPEC,
                output,
                timeout_per_experiment=45.0,
                max_points=321,
                job_timeout=600.0,
            )
        self.assertEqual(result["state"], "queued")
        submitted = manager.submit.call_args.args[0]
        self.assertEqual(submitted["job_kind"], "optimization")
        self.assertEqual(submitted["design"]["design_id"], "adapter-divider")
        self.assertEqual(submitted["optimization_spec"], SPEC)
        self.assertEqual(submitted["timeout_per_experiment"], 45.0)
        self.assertEqual(submitted["max_points"], 321)

    def test_durable_adapter_requires_explicit_existing_checkpoint_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "optimization"
            output.mkdir()
            (output / "unrelated.txt").write_text("occupied", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "resume_existing"):
                server.submit_design_optimization(DESIGN, SPEC, str(output))


if __name__ == "__main__":
    unittest.main()
