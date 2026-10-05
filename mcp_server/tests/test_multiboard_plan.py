import unittest

from multisim_mcp.multiboard_plan import (
    materialize_circuit_design_partition,
    materialize_multiboard_partition,
    plan_multiboard_partition,
    score_multiboard_partition,
    rank_partition_candidates,
    validate_multiboard_logical_artifacts,
)
from multisim_mcp.eda_core import CircuitComponent, CircuitDesign


class MultiboardPlanTest(unittest.TestCase):
    def _explicit_connector_contract(self):
        return [{
            "id": "J1",
            "part": "HEADER_1X2",
            "boards": ["power", "signal"],
            "instances": [
                {"board": "power", "refdes": "J1P"},
                {"board": "signal", "refdes": "J1S"},
            ],
            "pins": [
                {"number": 1, "net": "bus", "signal_type": "analog", "direction": "bidirectional"},
                {"number": 2, "net": "0", "signal_type": "ground", "direction": "passive"},
            ],
        }]

    def test_explicit_connector_contract_expands_physical_pins(self):
        result = plan_multiboard_partition([
            {"refdes": "V1", "board": "power", "nodes": ["bus", "0"]},
            {"refdes": "R1", "board": "signal", "nodes": ["bus", "0"]},
        ], [{"id": "power"}, {"id": "signal"}], self._explicit_connector_contract())
        self.assertEqual(result["connector_contract_status"], "valid")
        self.assertEqual(result["connector_count"], 2)
        self.assertEqual(result["physical_connector_count"], 1)
        self.assertEqual(result["connector_pins_by_board"], {"power": 2, "signal": 2})
        self.assertTrue(result["feasible"])
        power_bus = next(item for item in result["board_interfaces"]["power"] if item["net"] == "bus")
        self.assertEqual(power_bus["part"], "HEADER_1X2")
        self.assertEqual(power_bus["instance"]["refdes"], "J1P")

    def test_explicit_connector_contract_rejects_duplicate_pin_numbers(self):
        contract = self._explicit_connector_contract()
        contract[0]["pins"][1]["number"] = 1
        with self.assertRaisesRegex(ValueError, "duplicate pin number"):
            plan_multiboard_partition(
                [{"refdes": "V1", "board": "power", "nodes": ["bus", "0"]},
                 {"refdes": "R1", "board": "signal", "nodes": ["bus", "0"]}],
                [{"id": "power"}, {"id": "signal"}], contract,
            )

    def test_explicit_connector_contract_requires_every_cross_board_pin(self):
        contract = self._explicit_connector_contract()
        contract[0]["pins"] = contract[0]["pins"][:1]
        result = plan_multiboard_partition(
            [{"refdes": "V1", "board": "power", "nodes": ["bus", "0"]},
             {"refdes": "R1", "board": "signal", "nodes": ["bus", "0"]}],
            [{"id": "power"}, {"id": "signal"}], contract,
        )
        self.assertFalse(result["feasible"])
        self.assertTrue(any(item["constraint"] == "missing_connector_pin" for item in result["violations"]))

    def test_explicit_connector_contract_rejects_endpoint_direction_conflict(self):
        contract = self._explicit_connector_contract()
        contract[0]["pins"][0]["board_directions"] = {"power": "out", "signal": "out"}
        result = plan_multiboard_partition(
            [{"refdes": "V1", "board": "power", "nodes": ["bus", "0"]},
             {"refdes": "R1", "board": "signal", "nodes": ["bus", "0"]}],
            [{"id": "power"}, {"id": "signal"}], contract,
        )
        self.assertFalse(result["feasible"])
        self.assertTrue(any(item["constraint"] == "direction_conflict" for item in result["violations"]))

    def test_explicit_connector_contract_rejects_ground_signal_mismatch(self):
        contract = self._explicit_connector_contract()
        contract[0]["pins"][1]["signal_type"] = "digital"
        result = plan_multiboard_partition(
            [{"refdes": "V1", "board": "power", "nodes": ["bus", "0"]},
             {"refdes": "R1", "board": "signal", "nodes": ["bus", "0"]}],
            [{"id": "power"}, {"id": "signal"}], contract,
        )
        self.assertFalse(result["feasible"])
        self.assertTrue(any(item["constraint"] == "signal_type" for item in result["violations"]))

    def test_explicit_connector_contract_obeys_pin_capacity(self):
        result = plan_multiboard_partition(
            [{"refdes": "V1", "board": "power", "nodes": ["bus", "0"]},
             {"refdes": "R1", "board": "signal", "nodes": ["bus", "0"]}],
            [{"id": "power", "max_connector_pins": 1}, {"id": "signal", "max_connector_pins": 1}],
            self._explicit_connector_contract(),
        )
        self.assertFalse(result["feasible"])
        self.assertEqual({item["constraint"] for item in result["violations"]}, {"max_connector_pins"})

    def test_materialized_artifact_retains_explicit_connector_contract(self):
        components = [
            {"refdes": "V1", "kind": "V", "nodes": ["bus", "0"]},
            {"refdes": "R1", "kind": "R", "nodes": ["bus", "0"]},
        ]
        partition = plan_multiboard_partition(
            [dict(components[0], board="power"), dict(components[1], board="signal")],
            [{"id": "power"}, {"id": "signal"}],
            self._explicit_connector_contract(),
        )
        artifacts = materialize_multiboard_partition(components, partition)
        self.assertEqual(artifacts["connector_contract"]["status"], "valid")
        self.assertEqual(artifacts["interface_validation"]["status"], "valid")

    def test_cross_board_net_requires_connector(self):
        result = plan_multiboard_partition([
            {"refdes": "V1", "board": "power", "nodes": ["out", "0"]},
            {"refdes": "R1", "board": "signal", "nodes": ["out", "0"]},
        ], [{"id": "power"}, {"id": "signal"}])
        self.assertEqual(result["connector_count"], 2)
        self.assertEqual(result["connectors"][0]["pin"], 1)
        self.assertEqual(result["connectors"][0]["id"], "J1")
        score = score_multiboard_partition(result)
        self.assertEqual(score["cost"], 28)
        self.assertEqual(result["boards"][0]["component_count"], 1)
        self.assertEqual(result["connector_pins_by_board"], {"power": 2, "signal": 2})
        self.assertEqual(len(result["board_interfaces"]["power"]), 2)
        self.assertTrue(result["feasible"])

    def test_board_capacity_is_explicitly_infeasible(self):
        result = plan_multiboard_partition([
            {"refdes": "V1", "board": "power", "nodes": ["out", "0"]},
            {"refdes": "R1", "board": "signal", "nodes": ["out", "0"]},
        ], [
            {"id": "power", "max_connector_pins": 1},
            {"id": "signal", "max_connector_pins": 1},
        ])
        self.assertEqual(result["status"], "infeasible")
        self.assertFalse(result["feasible"])
        self.assertEqual(len(result["violations"]), 2)
        score = score_multiboard_partition(result)
        self.assertFalse(score["feasible"])
        self.assertGreater(score["cost"], 1_000_000)

    def test_ground_aliases_are_classified_as_ground(self):
        result = plan_multiboard_partition([
            {"refdes": "V1", "board": "a", "nodes": ["GND", "signal"]},
            {"refdes": "R1", "board": "b", "nodes": ["GND", "signal"]},
        ], [{"id": "a"}, {"id": "b"}])
        self.assertEqual([item["signal_type"] for item in result["connectors"]], ["ground", "signal"])

    def test_invalid_limits_and_empty_nodes_are_rejected(self):
        with self.assertRaises(ValueError):
            plan_multiboard_partition([], [{"id": "a", "max_components": 0}])
        with self.assertRaises(ValueError):
            plan_multiboard_partition([{"refdes": "R1", "nodes": ["", "b"]}], [{"id": "a"}])

    def test_invalid_board_rejected(self):
        with self.assertRaises(ValueError):
            plan_multiboard_partition([{"refdes": "R1", "board": "missing", "nodes": ["a", "b"]}], [{"id": "main"}])

    def test_candidates_are_ranked(self):
        candidates = rank_partition_candidates([
            {"refdes": "V1", "nodes": ["out", "0"]},
            {"refdes": "R1", "nodes": ["out", "0"]},
        ], [{"id": "a"}, {"id": "b"}])
        self.assertEqual(candidates[0]["score"]["cost"], 0)
        self.assertEqual(len(candidates), 4)

    def test_candidate_ranking_prefers_feasible_partition(self):
        candidates = rank_partition_candidates([
            {"refdes": "V1", "nodes": ["out", "0"]},
            {"refdes": "R1", "nodes": ["out", "0"]},
        ], [
            {"id": "a", "max_connector_pins": 1},
            {"id": "b", "max_connector_pins": 1},
        ])
        self.assertTrue(candidates[0]["score"]["feasible"])
        self.assertEqual(candidates[0]["connector_count"], 0)
        self.assertFalse(candidates[-1]["score"]["feasible"])

    def test_fixed_component_board_is_preserved_during_enumeration(self):
        candidates = rank_partition_candidates([
            {"refdes": "V1", "board": "power", "nodes": ["out", "0"]},
            {"refdes": "R1", "nodes": ["out", "0"]},
        ], [{"id": "power"}, {"id": "signal"}])
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(item["component_assignment"]["V1"] == "power" for item in candidates))

    def test_invalid_fixed_component_board_is_rejected(self):
        with self.assertRaises(ValueError):
            rank_partition_candidates([
                {"refdes": "V1", "board": "missing", "nodes": ["out", "0"]},
            ], [{"id": "power"}, {"id": "signal"}])

    def test_materializes_board_local_components_and_cross_board_interfaces(self):
        components = [
            {"refdes": "V1", "kind": "V", "nodes": ["bus", "0"]},
            {"refdes": "R1", "kind": "R", "nodes": ["bus", "sense"]},
            {"refdes": "R2", "kind": "R", "nodes": ["sense", "0"]},
        ]
        partition = plan_multiboard_partition(
            [dict(components[0], board="power"), dict(components[1], board="signal"), dict(components[2], board="signal")],
            [{"id": "power"}, {"id": "signal"}],
        )
        artifacts = materialize_multiboard_partition(components, partition)
        self.assertEqual(artifacts["status"], "logical-only")
        self.assertEqual([item["board_id"] for item in artifacts["boards"]], ["power", "signal"])
        power = artifacts["boards"][0]
        signal = artifacts["boards"][1]
        self.assertEqual([item["refdes"] for item in power["components"]], ["V1"])
        self.assertEqual([item["refdes"] for item in signal["components"]], ["R1", "R2"])
        self.assertEqual(next(item for item in power["nets"] if item["name"] == "bus")["scope"], "cross-board")
        self.assertIn("bus", [item["net"] for item in signal["interfaces"]])
        self.assertEqual(artifacts["interface_validation"]["status"], "valid")
        self.assertIsNotNone(artifacts["artifact_digest"])

    def test_infeasible_partition_cannot_be_materialized(self):
        partition = plan_multiboard_partition(
            [{"refdes": "R1", "board": "a", "nodes": ["x", "0"]}],
            [{"id": "a", "max_components": 1}],
        )
        # Make the infeasible state explicit without relying on an invalid board limit.
        partition["feasible"] = False
        partition["violations"] = [{"board": "a", "constraint": "test"}]
        with self.assertRaises(ValueError):
            materialize_multiboard_partition(
                [{"refdes": "R1", "nodes": ["x", "0"]}], partition)

    def test_materializes_circuit_design_and_board_spice_previews(self):
        design = CircuitDesign(
            design_id="divider",
            title="Divider",
            components=(
                CircuitComponent("V1", "V", ("bus", "0"), value="5"),
                CircuitComponent("R1", "R", ("bus", "sense"), value="1k"),
                CircuitComponent("R2", "R", ("sense", "0"), value="1k"),
            ),
        )
        partition = plan_multiboard_partition(
            [
                {"refdes": "V1", "board": "power", "nodes": ["bus", "0"]},
                {"refdes": "R1", "board": "signal", "nodes": ["bus", "sense"]},
                {"refdes": "R2", "board": "signal", "nodes": ["sense", "0"]},
            ],
            [{"id": "power"}, {"id": "signal"}],
        )
        artifacts = materialize_circuit_design_partition(design, partition)
        self.assertEqual(artifacts["parent_design_id"], "divider")
        power = artifacts["boards"][0]
        signal = artifacts["boards"][1]
        self.assertIn("V1 bus 0 5", power["spice_netlist"])
        self.assertNotIn("R1 bus sense 1k", power["spice_netlist"])
        self.assertIn("R1 bus sense 1k", signal["spice_netlist"])
        self.assertIn("external interface", signal["spice_netlist"])
        self.assertEqual(power["design"]["components"][0]["refdes"], "V1")
        self.assertNotIn("V1 bus 0 5", signal["design"]["source_netlist"])
        self.assertEqual(signal["design"]["source_netlist"], signal["spice_netlist"])

    def test_empty_board_is_structurally_blocked(self):
        artifacts = materialize_multiboard_partition(
            [{"refdes": "R1", "nodes": ["a", "0"]}],
            plan_multiboard_partition(
                [{"refdes": "R1", "board": "a", "nodes": ["a", "0"]}],
                [{"id": "a"}, {"id": "b"}],
            ),
        )
        self.assertEqual(artifacts["interface_validation"]["status"], "invalid")
        self.assertEqual(artifacts["interface_validation"]["native_status"], "unverified")
        self.assertTrue(any(item["constraint"] == "non_empty_board" for item in artifacts["interface_validation"]["violations"]))
