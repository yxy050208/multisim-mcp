"""Thin MCP adapter tests for evaluate_design_patch."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from multisim_mcp import server
from multisim_mcp.eda_core import CircuitDesign


DESIGN = {
    "schema_version": 1,
    "design_id": "server-patch-evaluation",
    "title": "Server patch evaluation",
    "revision": 2,
    "components": [
        {
            "refdes": "R1",
            "kind": "R",
            "nodes": ["in", "0"],
            "value": "1k",
            "model": None,
            "parameters": {},
        }
    ],
    "parameters": {},
    "annotations": {},
}


class DesignPatchEvaluationServerTest(unittest.TestCase):
    def test_adapter_converts_design_and_forwards_explicit_controls(self) -> None:
        service = Mock()
        service.run.return_value = {
            "success": True,
            "status": "candidate-improved-and-passed",
        }
        patch_value = {"schema_version": 1}
        spec = {"schema_version": 1}
        with patch.object(
            server, "_design_patch_evaluation_service", return_value=service
        ):
            result = server.evaluate_design_patch(
                DESIGN,
                patch_value,
                spec,
                "C:/patch-evaluation",
                regenerate_source_netlist=True,
                timeout_per_experiment=44.0,
                max_points=789,
            )

        self.assertTrue(result["success"])
        args, kwargs = service.run.call_args
        self.assertIsInstance(args[0], CircuitDesign)
        self.assertIs(args[1], patch_value)
        self.assertIs(args[2], spec)
        self.assertEqual(args[3], "C:/patch-evaluation")
        self.assertTrue(kwargs["regenerate_source_netlist"])
        self.assertEqual(kwargs["timeout_per_experiment"], 44.0)
        self.assertEqual(kwargs["max_points"], 789)


if __name__ == "__main__":
    unittest.main()
