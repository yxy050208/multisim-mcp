import tempfile
import unittest
from pathlib import Path

from multisim_mcp import server


class EngineeringMultiboardServerTest(unittest.TestCase):
    def test_plan_tool_returns_logical_only_candidates_without_com(self):
        result = server.plan_multiboard_engineering_request({
            "schema_version": 1,
            "title": "two board divider",
            "application": "test",
            "boards": [{"id": "power"}, {"id": "signal"}],
            "components": [
                {"refdes": "V1", "kind": "V", "nodes": ["bus", "0"]},
                {"refdes": "R1", "kind": "R", "nodes": ["bus", "sense"]},
            ],
        })
        self.assertEqual(result["kind"], "multisim-mcp-engineering-plan")
        self.assertTrue(result["multiboard_candidates"])
        self.assertEqual(
            result["multiboard_candidates"][0]["logical_artifacts"]["status"],
            "logical-only",
        )

    def test_plan_tool_rejects_non_object_before_planner(self):
        with self.assertRaises(ValueError):
            server.plan_multiboard_engineering_request([])  # type: ignore[arg-type]

    def test_native_acceptance_tool_preview_is_com_free(self):
        request = {
            "schema_version": 1,
            "title": "two board divider",
            "application": "native acceptance preview",
            "boards": [{"id": "power"}, {"id": "signal"}],
            "components": [
                {"refdes": "V1", "kind": "V", "nodes": ["bus", "0"], "value": "5", "board": "power"},
                {"refdes": "R1", "kind": "R", "nodes": ["bus", "sense"], "value": "1k", "board": "signal"},
                {"refdes": "R2", "kind": "R", "nodes": ["sense", "0"], "value": "1k", "board": "signal"},
            ],
            "fixtures": [
                {"id": "power-observe", "board_id": "power", "kind": "observation", "net": "bus"},
                {"id": "power-anchor", "board_id": "power", "kind": "resistor_termination", "net": "bus", "reference_net": "0", "refdes": "RFIX1", "value": "1G"},
                {"id": "power-ground", "board_id": "power", "kind": "ground_reference", "net": "0"},
                {"id": "signal-drive", "board_id": "signal", "kind": "voltage_source", "net": "bus", "reference_net": "0", "refdes": "VFIX1", "value": "5"},
                {"id": "signal-observe-bus", "board_id": "signal", "kind": "observation", "net": "bus"},
                {"id": "signal-ground", "board_id": "signal", "kind": "ground_reference", "net": "0"},
                {"id": "signal-observe-sense", "board_id": "signal", "kind": "observation", "net": "sense"},
            ],
        }
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "native-acceptance"
            result = server.run_native_multiboard_acceptance(
                request, str(output), execute=False, target_multisim_version="Multisim 14.3"
            )
        self.assertEqual(result["status"], "logical-only")
        self.assertEqual(result["verification_status"], "unverified")
        self.assertEqual(result["target_multisim_version"], "Multisim 14.3")
        self.assertIsInstance(result["engineering_plan_digest"], str)
        self.assertFalse(output.exists())
