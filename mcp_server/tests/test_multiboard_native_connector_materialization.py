import unittest

from multisim_mcp.eda_core import CircuitComponent, CircuitDesign
from multisim_mcp.multiboard_plan import (
    materialize_circuit_design_partition,
    plan_multiboard_partition,
)


class MultiboardNativeConnectorMaterializationTest(unittest.TestCase):
    def _design_and_partition(self, part="HDR1X4"):
        components = [
            {"refdes": "V1", "board": "power", "nodes": ["bus", "0"]},
            {"refdes": "R3", "board": "power", "nodes": ["clk", "0"]},
            {"refdes": "R4", "board": "power", "nodes": ["data", "0"]},
            {"refdes": "R1", "board": "signal", "nodes": ["bus", "sense"]},
            {"refdes": "R2", "board": "signal", "nodes": ["sense", "0"]},
            {"refdes": "R5", "board": "signal", "nodes": ["clk", "0"]},
            {"refdes": "R6", "board": "signal", "nodes": ["data", "0"]},
        ]
        connector = {
            "id": "J1",
            "part": part,
            "boards": ["power", "signal"],
            "instances": [
                {"board": "power", "refdes": "J1"},
                {"board": "signal", "refdes": "J1"},
            ],
            "pins": [
                {"number": 1, "net": "bus", "signal_type": "signal", "direction": "bidirectional"},
                {"number": 2, "net": "0", "signal_type": "ground", "direction": "passive"},
                {"number": 3, "net": "clk", "signal_type": "clock", "direction": "bidirectional"},
                {"number": 4, "net": "data", "signal_type": "digital", "direction": "bidirectional"},
            ],
        }
        partition = plan_multiboard_partition(
            components, [{"id": "power"}, {"id": "signal"}], [connector]
        )
        design = CircuitDesign(
            design_id="dual-board-header",
            title="Dual-board header",
            components=tuple(
                CircuitComponent(
                    item["refdes"],
                    "V" if item["refdes"] == "V1" else "R",
                    tuple(item["nodes"]),
                    value="5" if item["refdes"] == "V1" else "1k",
                )
                for item in components
            ),
        )
        return design, partition

    def test_verified_hdr1x4_is_emitted_on_each_board(self):
        design, partition = self._design_and_partition()
        artifacts = materialize_circuit_design_partition(
            design, partition, target_multisim_version="14.3"
        )
        self.assertEqual(artifacts["native_connector_status"], "native-verified")
        self.assertTrue(artifacts["native_connector_ready"])
        for board in artifacts["boards"]:
            self.assertEqual(board["native_connector_status"], "native-verified")
            self.assertEqual(
                [item["refdes"] for item in board["connector_components"]], ["J1"]
            )
            self.assertIn("XJ1 bus 0 clk data HDR1X4", board["spice_netlist"])
            self.assertEqual(
                next(
                    item for item in board["design"]["components"]
                    if item["kind"] == "HDR1X4"
                )["nodes"],
                ["bus", "0", "clk", "data"],
            )

    def test_unmapped_part_stays_pending_and_is_not_emitted(self):
        design, partition = self._design_and_partition(part="HEADER_1X4")
        artifacts = materialize_circuit_design_partition(
            design, partition, target_multisim_version="14.3"
        )
        self.assertEqual(artifacts["native_connector_status"], "mapping-pending")
        self.assertFalse(artifacts["native_connector_ready"])
        for board in artifacts["boards"]:
            self.assertEqual(board["connector_components"], [])
            self.assertNotIn("HEADER_1X4", board["spice_netlist"])

    def test_pending_mapping_is_preserved_without_target_version(self):
        design, partition = self._design_and_partition()
        artifacts = materialize_circuit_design_partition(design, partition)
        self.assertEqual(artifacts["native_connector_status"], "mapping-pending")
        self.assertFalse(artifacts["native_connector_ready"])
        self.assertEqual(artifacts["connector_resolutions"][0]["status"], "mapping-pending")

    def test_legacy_inferred_crossing_does_not_open_native_gate(self):
        design = CircuitDesign(
            design_id="legacy-crossing",
            title="Legacy crossing",
            components=(
                CircuitComponent("V1", "V", ("bus", "0"), value="5"),
                CircuitComponent("R1", "R", ("bus", "0"), value="1k"),
            ),
        )
        partition = plan_multiboard_partition(
            [
                {"refdes": "V1", "board": "power", "nodes": ["bus", "0"]},
                {"refdes": "R1", "board": "signal", "nodes": ["bus", "0"]},
            ],
            [{"id": "power"}, {"id": "signal"}],
        )
        artifacts = materialize_circuit_design_partition(
            design, partition, target_multisim_version="14.3"
        )
        self.assertEqual(artifacts["native_connector_status"], "unverified-inferred")
        self.assertFalse(artifacts["native_connector_ready"])


if __name__ == "__main__":
    unittest.main()
