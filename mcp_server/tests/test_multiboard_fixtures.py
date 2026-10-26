import unittest
import math
import hashlib
import json

from multisim_mcp.eda_core import CircuitComponent, CircuitDesign
from multisim_mcp.multiboard_fixtures import (
    compare_multiboard_interface_observations,
    compare_multiboard_interface_series,
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

    def test_interface_series_comparison_checks_every_native_sample(self):
        artifacts = materialize_multiboard_fixture_artifacts(
            self._divider_artifacts(), self._fixtures()
        )
        power = {
            "status": "pass", "analysis": "tran", "axis": [0.0, 1.0, 2.0],
            "real": [5.0, 5.0, 5.0], "imaginary": [0.0, 0.0, 0.0],
        }
        signal = {**power}
        passed = compare_multiboard_interface_series(
            artifacts, {"power": {"bus": power}, "signal": {"bus": signal}}, nets=["bus"]
        )
        self.assertEqual(passed["status"], "pass")
        changed = {**signal, "real": [5.0, 4.0, 5.0]}
        failed = compare_multiboard_interface_series(
            artifacts, {"power": {"bus": power}, "signal": {"bus": changed}}, nets=["bus"]
        )
        self.assertEqual(failed["status"], "fail")

    def test_interface_series_resampling_is_explicit_and_bounded(self):
        artifacts = materialize_multiboard_fixture_artifacts(
            self._divider_artifacts(), self._fixtures()
        )
        expected = {
            "status": "pass", "analysis": "tran", "axis": [0.0, 1.0, 2.0],
            "real": [5.0, 5.0, 5.0], "imaginary": [0.0, 0.0, 0.0],
        }
        actual = {
            **expected,
            "axis": [0.0, 0.5, 1.0, 1.5, 2.0],
            "real": [5.0, 5.0, 5.0, 5.0, 5.0],
            "imaginary": [0.0, 0.0, 0.0, 0.0, 0.0],
        }
        strict = compare_multiboard_interface_series(
            artifacts, {"power": {"bus": expected}, "signal": {"bus": actual}}, nets=["bus"]
        )
        self.assertEqual(strict["status"], "fail")
        self.assertEqual(
            strict["comparisons"][0]["endpoint_comparisons"]["signal"]["status"],
            "unverified",
        )
        aligned = compare_multiboard_interface_series(
            artifacts, {"power": {"bus": expected}, "signal": {"bus": actual}},
            nets=["bus"], allow_resampling=True,
        )
        self.assertEqual(aligned["status"], "pass")
        self.assertIn("linear resampling", aligned["comparison_basis"])

    def test_voltage_source_can_declare_explicit_ac_model(self):
        artifacts = self._divider_artifacts()
        fixtures = self._fixtures()
        source = next(item for item in fixtures if item["id"] == "signal-bus-drive")
        source.pop("value")
        source["model"] = "DC 0 AC 1"
        result = materialize_multiboard_fixture_artifacts(artifacts, fixtures)
        signal = next(item for item in result["boards"] if item["board_id"] == "signal")
        self.assertIn("VFIX1 bus 0 DC 0 AC 1", signal["spice_netlist"])

    def test_digital_fixture_reuses_bounded_adapter_contract(self):
        artifacts = self._divider_artifacts()
        signal_board = next(item for item in artifacts["boards"] if item["board_id"] == "signal")
        signal_board["design"]["components"].append(
            CircuitComponent("RBIAS", "R", ("high", "0"), value="1G").to_dict()
        )
        signal_board["design"]["nets"].append("high")
        fixtures = self._fixtures()
        fixtures = [item for item in fixtures if item["id"] not in {"signal-bus-drive", "signal-bus-observe"}]
        fixtures.extend([
            {
                "id": "signal-bus-digital-fixture",
                "board_id": "signal",
                "kind": "circuit_fragment",
                "net": "bus",
                "ports": ["bus", "high", "0"],
                "source_compiled_digest": "0" * 64,
                "model_scope": "explicit-digital-fixture",
                "components": [
                    {"refdes": "VFIXD", "kind": "V", "nodes": ["din", "0"],
                     "model": "PULSE(0 5 0 1n 1n 20u 40u)"},
                    {"refdes": "AFIXD", "kind": "DNOT4", "nodes": ["din", "bus", "high", "0"],
                     "model": "NOT"},
                ],
            },
            {"id": "signal-bus-observe", "board_id": "signal", "kind": "observation", "net": "bus"},
            {"id": "signal-high-anchor", "board_id": "signal", "kind": "voltage_source", "net": "high",
             "reference_net": "0", "refdes": "VHIGH", "value": "5"},
        ])
        result = materialize_multiboard_fixture_artifacts(artifacts, fixtures)
        signal = next(item for item in result["boards"] if item["board_id"] == "signal")
        self.assertIn("AFIXD din bus high 0 NOT", signal["spice_netlist"])
        self.assertIn("VFIXD din 0 PULSE(0 5 0 1n 1n 20u 40u)", signal["spice_netlist"])

    def test_digital_fixture_accepts_bv_adapter_with_real_digest(self):
        artifacts = self._divider_artifacts()
        signal_board = next(item for item in artifacts["boards"] if item["board_id"] == "signal")
        signal_board["design"]["components"].append(
            CircuitComponent("RBIAS", "R", ("high", "0"), value="1G").to_dict()
        )
        signal_board["design"]["nets"].append("high")
        fixtures = [item for item in self._fixtures() if item["id"] not in {"signal-bus-drive", "signal-bus-observe"}]
        components = [
            {"refdes": "VFIXA", "kind": "V", "nodes": ["analog_fix", "0"],
             "model": "PULSE(0 5 0 1n 1n 20u 40u)"},
            {"refdes": "BFIXD", "kind": "BV", "nodes": ["bus", "0"],
             "model": "V={if(V(analog_fix)>2.5,5,0)}"},
            {"refdes": "RFIXA", "kind": "R", "nodes": ["analog_fix", "0"], "value": "1G"},
            {"refdes": "RFIXHIGH", "kind": "R", "nodes": ["high", "0"], "value": "1G"},
        ]
        ports = ["bus", "high", "0"]
        digest = hashlib.sha256(
            json.dumps({"ports": ports, "components": components}, ensure_ascii=False,
                       sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        fixtures.extend([
            {"id": "signal-bus-bv-fixture", "board_id": "signal", "kind": "circuit_fragment",
             "net": "bus", "ports": ports, "source_compiled_digest": digest,
             "model_scope": "explicit-digital-fixture", "components": components},
            {"id": "signal-bus-observe", "board_id": "signal", "kind": "observation", "net": "bus"},
            {"id": "signal-high-anchor", "board_id": "signal", "kind": "voltage_source", "net": "high",
             "reference_net": "0", "refdes": "VHIGH", "value": "5"},
        ])
        result = materialize_multiboard_fixture_artifacts(artifacts, fixtures)
        signal = next(item for item in result["boards"] if item["board_id"] == "signal")
        self.assertIn("BFIXD bus 0 V={if(V(analog_fix)>2.5,5,0)}", signal["spice_netlist"])


if __name__ == "__main__":
    unittest.main()
