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

    def test_native_acceptance_preview_materializes_verified_hdr1x4(self):
        request = {
            "schema_version": 1,
            "title": "two board native header",
            "application": "native connector preview",
            "boards": [{"id": "power"}, {"id": "signal"}],
            "components": [
                {"refdes": "V1", "kind": "V", "nodes": ["bus", "0"], "value": "5", "board": "power"},
                {"refdes": "R3", "kind": "R", "nodes": ["clk", "0"], "value": "1k", "board": "power"},
                {"refdes": "R5", "kind": "R", "nodes": ["sense", "0"], "value": "1k", "board": "power"},
                {"refdes": "R1", "kind": "R", "nodes": ["bus", "sense"], "value": "1k", "board": "signal"},
                {"refdes": "R2", "kind": "R", "nodes": ["sense", "0"], "value": "1k", "board": "signal"},
                {"refdes": "R4", "kind": "R", "nodes": ["clk", "0"], "value": "1k", "board": "signal"},
            ],
            "connectors": [{
                "id": "J1", "part": "HDR1X4", "boards": ["power", "signal"],
                "instances": [{"board": "power", "refdes": "J1"}, {"board": "signal", "refdes": "J1"}],
                "pins": [
                    {"number": 1, "net": "bus", "signal_type": "signal", "direction": "bidirectional"},
                    {"number": 2, "net": "0", "signal_type": "ground", "direction": "passive"},
                    {"number": 3, "net": "clk", "signal_type": "clock", "direction": "bidirectional"},
                    {"number": 4, "net": "sense", "signal_type": "analog", "direction": "bidirectional"},
                ],
            }],
            "fixtures": [
                {"id": "power-bus", "board_id": "power", "kind": "observation", "net": "bus"},
                {"id": "power-bus-anchor", "board_id": "power", "kind": "resistor_termination", "net": "bus", "reference_net": "0", "refdes": "RFP1", "value": "1G"},
                {"id": "power-ground", "board_id": "power", "kind": "ground_reference", "net": "0"},
                {"id": "power-clk", "board_id": "power", "kind": "observation", "net": "clk"},
                {"id": "power-clk-anchor", "board_id": "power", "kind": "resistor_termination", "net": "clk", "reference_net": "0", "refdes": "RFP2", "value": "1G"},
                {"id": "power-sense", "board_id": "power", "kind": "observation", "net": "sense"},
                {"id": "power-sense-anchor", "board_id": "power", "kind": "resistor_termination", "net": "sense", "reference_net": "0", "refdes": "RFP3", "value": "1G"},
                {"id": "signal-bus", "board_id": "signal", "kind": "observation", "net": "bus"},
                {"id": "signal-bus-anchor", "board_id": "signal", "kind": "resistor_termination", "net": "bus", "reference_net": "0", "refdes": "RFS1", "value": "1G"},
                {"id": "signal-ground", "board_id": "signal", "kind": "ground_reference", "net": "0"},
                {"id": "signal-clk", "board_id": "signal", "kind": "observation", "net": "clk"},
                {"id": "signal-clk-anchor", "board_id": "signal", "kind": "resistor_termination", "net": "clk", "reference_net": "0", "refdes": "RFS2", "value": "1G"},
                {"id": "signal-sense", "board_id": "signal", "kind": "observation", "net": "sense"},
                {"id": "signal-sense-anchor", "board_id": "signal", "kind": "resistor_termination", "net": "sense", "reference_net": "0", "refdes": "RFS3", "value": "1G"},
            ],
        }
        with tempfile.TemporaryDirectory() as temp:
            result = server.run_native_multiboard_acceptance(
                request, str(Path(temp) / "native-header"), execute=False,
                target_multisim_version="14.3",
            )
        prepared = result["prepared_artifacts"]
        self.assertEqual(prepared["native_connector_status"], "native-verified")
        self.assertTrue(prepared["native_connector_ready"])
        for board in prepared["boards"]:
            self.assertIn("XJ1 bus 0", board["spice_netlist"])
            self.assertEqual(board["connector_components"][0]["kind"], "HDR1X4")
