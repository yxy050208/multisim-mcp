import unittest
import math

from multisim_mcp.eda_core import CircuitComponent, CircuitDesign
from multisim_mcp.multiboard_fixtures import (
    compare_multiboard_interface_observations,
    materialize_multiboard_fixture_artifacts,
    validate_multiboard_fixture_contract,
)
from multisim_mcp.multiboard_plan import materialize_circuit_design_partition, plan_multiboard_partition


class MultiboardFixtureTest(unittest.TestCase):
    def _divider_artifacts(self):
        design = CircuitDesign(
            design_id="fixture-divider",
            title="Fixture divider",
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
        return materialize_circuit_design_partition(design, partition)

    def _fixtures(self):
        return [
            {"id": "power-bus-observe", "board_id": "power", "kind": "observation", "net": "bus"},
            {"id": "power-bus-anchor", "board_id": "power", "kind": "resistor_termination", "net": "bus", "reference_net": "0", "refdes": "RFIX1", "value": "1G"},
            {"id": "power-ground", "board_id": "power", "kind": "ground_reference", "net": "0"},
            {"id": "signal-bus-drive", "board_id": "signal", "kind": "voltage_source", "net": "bus", "reference_net": "0", "refdes": "VFIX1", "value": "5"},
            {"id": "signal-bus-observe", "board_id": "signal", "kind": "observation", "net": "bus"},
            {"id": "signal-ground", "board_id": "signal", "kind": "ground_reference", "net": "0"},
            {"id": "signal-sense-observe", "board_id": "signal", "kind": "observation", "net": "sense", "label": "divider output"},
        ]

    def test_contract_requires_explicit_boundary_conditions(self):
        artifacts = self._divider_artifacts()
        result = validate_multiboard_fixture_contract(artifacts, self._fixtures())
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["coverage_status"], "complete")
        self.assertEqual(result["uncovered_interfaces"], [])
        self.assertEqual(result["fixture_count"], 7)

    def test_missing_fixture_is_reported_and_materialization_is_blocked(self):
        artifacts = self._divider_artifacts()
        fixtures = [
            item for item in self._fixtures()
            if item["id"] not in {"signal-bus-drive", "signal-bus-observe"}
        ]
        result = validate_multiboard_fixture_contract(artifacts, fixtures)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["coverage_status"], "incomplete")
        self.assertIn({"board_id": "signal", "net": "bus"}, result["uncovered_interfaces"])
        with self.assertRaisesRegex(ValueError, "leaves interfaces uncovered"):
            materialize_multiboard_fixture_artifacts(artifacts, fixtures)

    def test_fixture_materialization_adds_only_declared_components_and_probes(self):
        artifacts = self._divider_artifacts()
        result = materialize_multiboard_fixture_artifacts(artifacts, self._fixtures())
        self.assertEqual(result["status"], "logical-only")
        self.assertEqual(result["verification_status"], "unverified")
        power = next(item for item in result["boards"] if item["board_id"] == "power")
        signal = next(item for item in result["boards"] if item["board_id"] == "signal")
        self.assertEqual(power["probe_nets"], ["bus"])
        self.assertEqual(signal["probe_nets"], ["bus", "sense"])
        self.assertIn("VFIX1 bus 0 5", signal["spice_netlist"])
        self.assertNotIn("VFIX1 bus 0 5", power["spice_netlist"])
        self.assertIn("R1 bus sense 1k", signal["spice_netlist"])
        self.assertIn("fixture signal-bus-drive", signal["spice_netlist"])
        self.assertEqual(len(signal["fixture_components"]), 1)
        self.assertIsNotNone(result["artifact_digest"])

    def test_refdes_and_driver_collisions_are_rejected(self):
        artifacts = self._divider_artifacts()
        fixtures = self._fixtures() + [
            {"id": "signal-bus-drive-2", "board_id": "signal", "kind": "voltage_source", "net": "bus", "reference_net": "0", "refdes": "VFIX2", "value": "3"},
            {"id": "signal-r-collision", "board_id": "signal", "kind": "resistor_termination", "net": "sense", "reference_net": "0", "refdes": "R1", "value": "10k"},
        ]
        result = validate_multiboard_fixture_contract(artifacts, fixtures)
        constraints = {item["constraint"] for item in result["violations"]}
        self.assertIn("multiple_drivers", constraints)
        self.assertIn("refdes", constraints)
        with self.assertRaisesRegex(ValueError, "structurally invalid"):
            materialize_multiboard_fixture_artifacts(artifacts, fixtures)

    def test_observation_on_single_terminal_net_is_rejected(self):
        artifacts = self._divider_artifacts()
        fixtures = [
            {"id": "power-bus-observe", "board_id": "power", "kind": "observation", "net": "bus"},
            {"id": "power-ground", "board_id": "power", "kind": "ground_reference", "net": "0"},
            {"id": "signal-bus-drive", "board_id": "signal", "kind": "voltage_source", "net": "bus", "reference_net": "0", "refdes": "VFIX1", "value": "5"},
            {"id": "signal-bus-observe", "board_id": "signal", "kind": "observation", "net": "bus"},
            {"id": "signal-ground", "board_id": "signal", "kind": "ground_reference", "net": "0"},
            {"id": "signal-sense-observe", "board_id": "signal", "kind": "observation", "net": "sense"},
        ]
        result = validate_multiboard_fixture_contract(artifacts, fixtures)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("observation_anchor", {item["constraint"] for item in result["violations"]})

    def test_interface_comparison_never_treats_missing_endpoint_as_zero(self):
        artifacts = self._divider_artifacts()
        passed = compare_multiboard_interface_observations(
            artifacts, {"power": {"bus": 5.0}, "signal": {"bus": 5.0, "sense": 2.5}}, nets=["bus"]
        )
        self.assertEqual(passed["status"], "pass")
        self.assertEqual(passed["comparisons"][0]["max_absolute_difference"], 0.0)
        missing = compare_multiboard_interface_observations(
            artifacts, {"power": {"bus": 5.0}}, nets=["bus"]
        )
        self.assertEqual(missing["status"], "unverified")
        self.assertEqual(missing["comparisons"], [])
        self.assertIn({"board_id": "signal", "net": "bus"}, missing["missing"])

    def test_interface_comparison_fails_out_of_tolerance(self):
        artifacts = self._divider_artifacts()
        result = compare_multiboard_interface_observations(
            artifacts, {"power": {"bus": 5.0}, "signal": {"bus": 4.9}}, nets=["bus"], absolute_tolerance=0.01
        )
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["comparisons"][0]["passed"])

    def test_interface_comparison_rejects_non_finite_values(self):
        artifacts = self._divider_artifacts()
        result = compare_multiboard_interface_observations(
            artifacts, {"power": {"bus": math.nan}, "signal": {"bus": 5.0}}, nets=["bus"]
        )
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["comparisons"], [])
        self.assertEqual(result["invalid_values"], [{"board_id": "power", "net": "bus"}])


if __name__ == "__main__":
    unittest.main()
