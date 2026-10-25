"""Electrical and approval regressions for the explicit ideal active template.

Independent nodal solves prove software-level OP/AC behavior only. Fixtures are
added only to oracle copies, never to the reviewed compiled preview.
"""

from __future__ import annotations

import cmath
import copy
import json
import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from multisim_mcp.component_approvals import approve_component_resolution
from multisim_mcp.component_resolution import resolve_component_requirements
from multisim_mcp.design_plans import plan_design_options, select_design_option
from multisim_mcp.design_specifications import prepare_design_specification
from multisim_mcp.eda_core import CircuitDesign
from multisim_mcp.executable_approvals import (
    approve_executable_netlist, validate_executable_netlist_approval,
)
from multisim_mcp.executable_netlists import (
    COMPILER_SUPPORT_MATRIX, compile_executable_netlist, validate_compiled_round_trip,
)
from multisim_mcp.ideal_models import IDEAL_OPAMP5_SHA256, ideal_opamp5_contract
from multisim_mcp.linear_reference import solve_linear
from multisim_mcp.netlist_drafts import prepare_netlist_draft
from multisim_mcp.schematic_builder import parse_netlist
from multisim_mcp.spice_adapter import circuit_design_from_spice, circuit_design_to_spice


DEFAULT_INPUTS = {
    "supply_voltage_v": 5, "input_min_v": 0, "input_max_v": 5,
    "output_min_v": 0.5, "output_max_v": 2.5, "source_impedance_ohm": 100,
    "load_impedance_ohm": 100_000, "cutoff_frequency_hz": 1_000,
}


def active_draft(**overrides):
    selected = select_design_option(plan_design_options("设计传感器信号调理电路"), "signal-active")
    spec = prepare_design_specification(selected, {**DEFAULT_INPUTS, **overrides})
    return prepare_netlist_draft(selected, spec, {
        "approved": True, "specification_id": spec["specification_id"],
        "specification_digest": spec["specification_digest"],
    })


def active_selections():
    return {
        "cr-01": {"family": "series-resistor", "voltage_rating_v": 10},
        "cr-02": {"family": "ideal-opamp5"},
        "cr-03": {"family": "precision-resistor-network", "voltage_rating_v": 10},
        "cr-04": {"family": "ideal-opamp5"},
        "cr-05": {"family": "ceramic-capacitor", "voltage_rating_v": 10},
        "cr-06": {"family": "capacitor", "voltage_rating_v": 10},
    }


def component_review(draft, selections=None):
    resolved = resolve_component_requirements(draft, selections or active_selections())
    return approve_component_resolution(draft, resolved, {
        "approved": True, "resolution_id": resolved["resolution_id"],
        "resolution_digest": resolved["resolution_digest"], "confirm_topology": True,
        "confirm_ratings": True, "confirm_model_provenance": True,
    })


def netlist_review(preview):
    return {
        "approved": True, "compiled_id": preview["compiled_id"],
        "compiled_digest": preview["compiled_digest"], "confirm_components": True,
        "confirm_topology": True, "confirm_calculated_values": True, "confirm_spice": True,
    }


def solve_fixture(preview, vin, frequency=None):
    """Feed an exact compiled copy with a known DC/AC source and source R."""
    source = preview["synthesis"]["interface_contract"]["source_impedance_ohm"]
    source_net = "external-source" if source else "path-00"
    fixture = f"VINPUT {source_net} 0 DC {vin:.15g} AC 1\n"
    if source:
        fixture += f"RSOURCE {source_net} path-00 {source:.15g}\n"
    oracle = preview["spice_netlist"].removesuffix(".end\n") + fixture + ".end\n"
    parsed = parse_netlist(oracle)
    if parsed.unsupported or parsed.subcircuit_expansion_failures or not parsed.grounded:
        raise AssertionError("oracle did not parse the exact circuit and external fixture")
    return solve_linear(parsed.components, frequency)


class ActiveSignalCompilerTest(unittest.TestCase):
    def test_explicit_ideal_model_selection_is_not_a_vendor_device(self):
        draft = active_draft()
        unselected = resolve_component_requirements(draft)
        self.assertGreater(unselected["summary"]["unresolved_selection_count"], 0)
        self.assertGreater(unselected["summary"]["model_pending_count"], 0)
        result = resolve_component_requirements(draft, active_selections())
        self.assertEqual(result["state"], "ready-for-netlist-review")
        ideal = result["requirements"][1]["selected_candidate"]
        self.assertEqual(ideal["model_status"], "built-in-ideal-model")
        self.assertEqual(ideal["rating_status"], "not-required")
        self.assertEqual(ideal["provenance"]["model_sha256"], IDEAL_OPAMP5_SHA256)
        self.assertIsNone(ideal["part_number"])
        self.assertEqual(ideal["model_contract"]["open_loop_gain_v_per_v"], 100000)
        approved = component_review(draft)
        self.assertEqual(approved["approved_requirements"][1]["model_provenance_status"], "built-in-ideal-model-reviewed")

    def test_ideal_family_rejects_vendor_identity_models_and_physical_ratings(self):
        for key, value in {
            "part_number": "OPA333", "model_source": {}, "voltage_rating_v": 5,
            "current_rating_a": 1, "frequency_rating_hz": 1000, "temperature_rating_c": 85,
        }.items():
            with self.subTest(key=key):
                selections = active_selections()
                selections["cr-02"][key] = value
                with self.assertRaisesRegex(ValueError, "cannot declare a vendor part"):
                    resolve_component_requirements(active_draft(), selections)

    def test_real_opamp_stays_blocked_without_model(self):
        draft = active_draft()
        selections = active_selections()
        selections["cr-02"] = {"family": "rail-to-rail-op-amp", "voltage_rating_v": 10}
        result = resolve_component_requirements(draft, selections)
        self.assertEqual(result["state"], "model-review")
        with self.assertRaisesRegex(ValueError, "provenance is not approvable"):
            component_review(draft, selections)

    def test_real_opamp_with_provenance_is_not_silently_compiled_as_ideal(self):
        draft = active_draft()
        selections = active_selections()
        selections["cr-02"] = {
            "family": "rail-to-rail-op-amp", "voltage_rating_v": 10,
            "model_source": {"name": "OPA333", "uri": "test.lib", "sha256": "a" * 64, "license": "MIT"},
        }
        approval = component_review(draft, selections)
        with patch("multisim_mcp.executable_netlists.verify_approved_model_files", return_value=[]):
            with self.assertRaisesRegex(ValueError, "exact physical families"):
                compile_executable_netlist(draft, approval)

    def test_filter_capacitance_has_a_separate_rating_gate(self):
        draft = active_draft()
        selections = active_selections()
        selections["cr-06"] = {"family": "capacitor", "voltage_rating_v": 4}
        with self.assertRaisesRegex(ValueError, "cr-06 ratings are not approved"):
            component_review(draft, selections)
        selections.pop("cr-06")
        result = resolve_component_requirements(draft, selections)
        self.assertEqual(result["summary"]["unresolved_selection_count"], 1)

    def test_review_chain_survives_json_and_keeps_execution_pending(self):
        draft = active_draft()
        approval = component_review(draft)
        preview = compile_executable_netlist(json.loads(json.dumps(draft)), json.loads(json.dumps(approval)))
        self.assertEqual(preview["compiled_digest"], compile_executable_netlist(draft, approval)["compiled_digest"])
        artifact = approve_executable_netlist(preview, netlist_review(preview))
        self.assertEqual(validate_executable_netlist_approval(preview, json.loads(json.dumps(artifact))), artifact)
        self.assertFalse(preview["ready_for_schematic"])
        self.assertFalse(preview["ready_for_simulation"])
        self.assertFalse(preview["execution_boundary"]["files_written"])
        self.assertFalse(preview["execution_boundary"]["simulation_started"])
        self.assertNotIn("VINPUT", preview["spice_netlist"])
        self.assertEqual(preview["verified_models"], [])
        self.assertEqual(preview["support"]["native_validation_status"], "unverified-for-this-template")

    def test_supply_pin_order_units_and_module_contracts(self):
        draft = active_draft()
        preview = compile_executable_netlist(draft, component_review(draft))
        design = CircuitDesign.from_dict(preview["circuit_design"])
        parts = {p.refdes: p for p in design.components}
        self.assertEqual(parts["XU1"].nodes, ("path-01", "path-02", "rail-positive", "gnd", "path-02"))
        self.assertEqual(parts["XU2"].nodes, ("path-03", "feedback", "rail-positive", "gnd", "path-04"))
        for p in design.components:
            if p.kind in {"R", "C"}:
                self.assertGreater(float(p.value), 0)
        self.assertEqual(parts["CDEC"].value, "1e-07")
        self.assertEqual(design.model_references[0].sha256, IDEAL_OPAMP5_SHA256)
        for original, actual in zip(draft["topology"]["modules"], preview["synthesis"]["module_ports"]):
            self.assertEqual(actual["module_id"], original["module_id"])
            self.assertEqual([actual["input_net"]], original["input_nets"])
            self.assertEqual([actual["output_net"]], original["output_nets"])
            self.assertTrue(set(actual["components"]) <= set(parts))
        validate_compiled_round_trip(design, circuit_design_from_spice(preview["spice_netlist"]))
        self.assertEqual(circuit_design_to_spice(design, prefer_source=False), preview["spice_netlist"])

    def test_independent_op_affine_targets_with_source_and_load_variations(self):
        cases = [
            {}, {"source_impedance_ohm": 0}, {"source_impedance_ohm": 5000},
            {"load_impedance_ohm": 10},
            {"input_min_v": 1, "input_max_v": 3, "output_min_v": 0.2, "output_max_v": 4.2},
            {"input_min_v": 0.1, "input_max_v": 1, "output_min_v": 0.5, "output_max_v": 4.1},
            {"input_min_v": 0, "input_max_v": 3, "output_min_v": 0, "output_max_v": 3},
            {"input_min_v": 0.01, "input_max_v": 0.04, "output_min_v": 0.1, "output_max_v": 3.1},
            {"input_min_v": 0, "input_max_v": 1, "output_min_v": 0, "output_max_v": 1e-6},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                values = {**DEFAULT_INPUTS, **overrides}
                draft = active_draft(**overrides)
                preview = compile_executable_netlist(draft, component_review(draft))
                for t in (0, 0.25, 0.5, 0.75, 1):
                    vin = values["input_min_v"] + t * (values["input_max_v"] - values["input_min_v"])
                    target = values["output_min_v"] + t * (values["output_max_v"] - values["output_min_v"])
                    solved = solve_fixture(preview, vin)
                    self.assertAlmostEqual(solved["path-04"].real, target, delta=1e-8)
                    self.assertAlmostEqual(solved["rail-positive"].real, values["supply_voltage_v"])

    def test_independent_ac_pole_gain_and_phase(self):
        for overrides in ({}, {"source_impedance_ohm": 0, "cutoff_frequency_hz": 1234},
                          {"input_min_v": 1, "input_max_v": 3, "output_min_v": 0.2, "output_max_v": 4.2,
                           "source_impedance_ohm": 5000, "load_impedance_ohm": 10, "cutoff_frequency_hz": 23.5}):
            values = {**DEFAULT_INPUTS, **overrides}
            draft = active_draft(**overrides)
            preview = compile_executable_netlist(draft, component_review(draft))
            gain = (values["output_max_v"] - values["output_min_v"]) / (values["input_max_v"] - values["input_min_v"])
            for ratio in (0.01, 0.1, 1, 10, 100):
                with self.subTest(overrides=overrides, ratio=ratio):
                    voltage = solve_fixture(preview, values["input_min_v"], values["cutoff_frequency_hz"] * ratio)["path-04"]
                    self.assertAlmostEqual(abs(voltage), gain / math.sqrt(1 + ratio ** 2), delta=gain * 1e-8)
                    self.assertAlmostEqual(cmath.phase(voltage), -math.atan(ratio), delta=1e-8)

    def test_bias_branches_match_sign_and_zero_offset(self):
        for params, expected, absent in (
            ({}, "RPB", "RMB"),
            ({"input_min_v": 1, "input_max_v": 3, "output_min_v": 0.2, "output_max_v": 4.2}, "RMB", "RPB"),
            ({"output_min_v": 0, "output_max_v": 5}, None, None),
        ):
            draft = active_draft(**params)
            refs = {p["refdes"] for p in compile_executable_netlist(draft, component_review(draft))["circuit_design"]["components"]}
            if expected:
                self.assertIn(expected, refs)
                self.assertNotIn(absent, refs)
            else:
                self.assertFalse(refs & {"RPB", "RMB"})

    def test_decimal_pure_gain_does_not_create_a_roundoff_bias_resistor(self):
        draft = active_draft(input_min_v=0.1, input_max_v=0.3, output_min_v=0.3, output_max_v=0.9)
        preview = compile_executable_netlist(draft, component_review(draft))
        self.assertEqual(preview["synthesis"]["target_transfer"]["gain_v_per_v"], 3)
        self.assertEqual(preview["synthesis"]["target_transfer"]["offset_v"], 0)
        self.assertFalse({p["refdes"] for p in preview["circuit_design"]["components"]} & {"RPB", "RMB"})
        for vin, target in ((0.1, 0.3), (0.2, 0.6), (0.3, 0.9)):
            self.assertAlmostEqual(solve_fixture(preview, vin)["path-04"].real, target, delta=1e-8)

    def test_a_real_small_bias_is_rejected_by_value_bounds_rather_than_erased(self):
        draft = active_draft(input_min_v=0.1, input_max_v=0.3,
                             output_min_v=0.300000000001, output_max_v=0.900000000001)
        with self.assertRaisesRegex(ValueError, "RPB_ohm is outside the bounded SI"):
            compile_executable_netlist(draft, component_review(draft))

    def test_out_of_rail_ranges_and_high_gain_are_rejected(self):
        for params, message in (
            ({"input_min_v": -1}, "active input range"),
            ({"input_max_v": 6}, "active input range"),
            ({"output_min_v": -1}, "active output range"),
            ({"output_max_v": 6}, "active output range"),
            ({"input_max_v": 0.001}, "supports gain"),
        ):
            with self.subTest(params=params):
                draft = active_draft(**params)
                with self.assertRaisesRegex(ValueError, message):
                    compile_executable_netlist(draft, component_review(draft))

    def test_invalid_units_nonfinite_booleans_and_ranges_do_not_compile(self):
        for params in (
            {"input_max_v": 0}, {"output_max_v": 0.5}, {"source_impedance_ohm": -1},
            {"load_impedance_ohm": 0}, {"cutoff_frequency_hz": 0},
            {"cutoff_frequency_hz": float("inf")}, {"supply_voltage_v": float("nan")},
            {"input_max_v": "5mV"}, {"source_impedance_ohm": True},
        ):
            with self.subTest(params=params):
                with self.assertRaises(ValueError):
                    draft = active_draft(**params)
                    compile_executable_netlist(draft, component_review(draft))

    def test_extreme_calculated_values_are_rejected(self):
        for params in ({"source_impedance_ohm": 1e15}, {"cutoff_frequency_hz": 1e15}):
            draft = active_draft(**params)
            with self.assertRaisesRegex(ValueError, "bounded SI component range"):
                compile_executable_netlist(draft, component_review(draft))

    def test_missing_approval_and_changed_draft_are_rejected(self):
        draft = active_draft()
        with self.assertRaises(ValueError):
            compile_executable_netlist(draft, {})
        approval = component_review(draft)
        draft["design_inputs"]["cutoff_frequency_hz"] = 100
        with self.assertRaisesRegex(ValueError, "draft_digest"):
            compile_executable_netlist(draft, approval)

    def test_swapped_opamp_pins_or_changed_values_fail_same_count_round_trip(self):
        draft = active_draft()
        preview = compile_executable_netlist(draft, component_review(draft))
        design = CircuitDesign.from_dict(preview["circuit_design"])
        parsed = circuit_design_from_spice(preview["spice_netlist"])
        for index, replacement in (
            (3, replace(parsed.components[3], nodes=("path-02", "path-01", "rail-positive", "0", "path-02"))),
            (3, replace(parsed.components[3], model="LM358")),
            (1, replace(parsed.components[1], value="10000")),
        ):
            parts = list(parsed.components)
            parts[index] = replacement
            with self.assertRaisesRegex(ValueError, "round-trip changed"):
                validate_compiled_round_trip(design, replace(parsed, components=tuple(parts)))
        with patch("multisim_mcp.executable_netlists.circuit_design_from_spice", return_value=replace(parsed, components=tuple(parts))):
            with self.assertRaisesRegex(ValueError, "round-trip changed"):
                compile_executable_netlist(draft, component_review(draft))

    def test_preview_pin_tampering_is_rejected_before_approval(self):
        draft = active_draft()
        preview = compile_executable_netlist(draft, component_review(draft))
        tampered = copy.deepcopy(preview)
        tampered["circuit_design"]["components"][3]["nodes"][:2] = ["path-02", "path-01"]
        with self.assertRaisesRegex(ValueError, "SPICE does not match CircuitDesign"):
            approve_executable_netlist(tampered, netlist_review(tampered))

    def test_returned_support_and_model_contract_do_not_mutate_shared_state(self):
        draft = active_draft()
        preview = compile_executable_netlist(draft, component_review(draft))
        preview["support"]["required_families"]["cr-02"] = "rail-to-rail-op-amp"
        preview["synthesis"]["model_contract"]["pin_order"].reverse()
        self.assertEqual(COMPILER_SUPPORT_MATRIX["signal-active"]["required_families"]["cr-02"], "ideal-opamp5")
        self.assertEqual(ideal_opamp5_contract()["pin_order"][0], "IN+")
        with self.assertRaisesRegex(ValueError, "support metadata"):
            approve_executable_netlist(preview, netlist_review(preview))

    def test_passive_compiled_digest_remains_compatible(self):
        from tests.test_executable_netlists import _draft, _approved
        draft = _draft()
        preview = compile_executable_netlist(draft, _approved(draft))
        self.assertEqual(preview["compiled_digest"], "6f2e69b43b0469938dabac8c12aedae630d2195b8b2699dfa8c61d13dce41a5a")

    def test_mcp_adapter_uses_the_same_active_compiler(self):
        from multisim_mcp import server
        draft = active_draft()
        approval = component_review(draft)
        preview = server.compile_executable_netlist(draft, approval)
        self.assertEqual(preview["kind"], "multisim-mcp-executable-netlist-preview")
        self.assertEqual(preview["compiled_digest"], compile_executable_netlist(draft, approval)["compiled_digest"])
        artifact = server.approve_executable_netlist(preview, netlist_review(preview))
        self.assertEqual(artifact["kind"], "multisim-mcp-executable-netlist-approval")


if __name__ == "__main__":
    unittest.main()
