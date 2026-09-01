"""Tests for the logical-draft to component-candidate review boundary."""

from __future__ import annotations

import unittest
import json

from multisim_mcp import server
from multisim_mcp.component_resolution import resolve_component_requirements
from multisim_mcp.design_plans import plan_design_options, select_design_option
from multisim_mcp.design_specifications import prepare_design_specification
from multisim_mcp.netlist_drafts import prepare_netlist_draft


def _ready(requirements: str, option_id: str | None = None):
    plan = plan_design_options(requirements)
    selected = select_design_option(plan, option_id or plan["recommended_option_id"])
    first = prepare_design_specification(selected)
    values = {
        "supply_voltage_v": 5,
        "frequency_min_hz": 20_000,
        "frequency_max_hz": 50_000,
        "output_amplitude_vpp": 3,
        "load_resistance_ohm": 600,
        "waveform_targets": "square, triangle, sine",
        "max_frequency_error_percent": 5,
        "max_amplitude_error_percent": 5,
        "timing_tolerance_percent": 5,
        "input_min_v": 0.1,
        "input_max_v": 1,
        "output_min_v": 0.2,
        "output_max_v": 3.3,
        "source_impedance_ohm": 100,
        "load_impedance_ohm": 100_000,
        "cutoff_frequency_hz": 1_000,
        "dc_bus_voltage_v": 24,
        "motor_rated_voltage_v": 24,
        "continuous_current_a": 5,
        "stall_current_a": 20,
        "pwm_frequency_hz": 20_000,
        "control_loop_frequency_hz": 1_000,
        "feedback_sensor": "encoder",
        "max_latency_ms": 0.2,
        "ambient_temperature_c": 60,
        "max_current_a": 1,
        "bandwidth_hz": 100_000,
    }
    known = {item["parameter_id"] for item in first["parameter_requirements"]}
    specification = prepare_design_specification(
        selected,
        {key: value for key, value in values.items() if key in known},
    )
    approval = {
        "approved": True,
        "specification_id": specification["specification_id"],
        "specification_digest": specification["specification_digest"],
    }
    return selected, specification, prepare_netlist_draft(selected, specification, approval)


class ComponentResolutionTest(unittest.TestCase):
    def test_recommendations_are_bounded_and_non_executable(self) -> None:
        _, _, draft = _ready("设计 555 多波形发生器", "waveform-analog-555")
        result = resolve_component_requirements(draft)
        self.assertEqual(result["kind"], "multisim-mcp-component-resolution")
        self.assertEqual(result["draft_digest"], draft["draft_digest"])
        self.assertEqual(result["summary"]["requirement_count"], 5)
        self.assertEqual(result["summary"]["unresolved_selection_count"], 5)
        self.assertTrue(result["summary"]["recommended_only"])
        self.assertEqual(result["state"], "candidate-review")
        self.assertFalse(result["ready_for_executable_netlist"])
        self.assertFalse(result["ready_for_schematic"])
        self.assertFalse(result["ready_for_simulation"])
        self.assertFalse(result["execution_boundary"]["spice_netlist_generated"])
        self.assertTrue(result["requirements"][0]["recommended_candidate_id"])

    def test_design_inputs_are_carried_for_transparent_rating_basis(self) -> None:
        _, _, draft = _ready("设计 555 多波形发生器", "waveform-analog-555")
        result = resolve_component_requirements(draft)
        self.assertEqual(result["design_inputs"]["supply_voltage_v"], 5.0)
        first = result["requirements"][0]["candidates"][0]
        voltage = next(item for item in first["rating_requirements"] if item["metric"] == "voltage_rating_v")
        self.assertEqual(voltage["minimum"], 6.25)
        self.assertEqual(voltage["status"], "calculated-not-verified")

    def test_draft_tampering_is_rejected(self) -> None:
        _, _, draft = _ready("设计传感器有源滤波 ADC 调理", "signal-active")
        draft["design_inputs"]["supply_voltage_v"] = 99
        with self.assertRaisesRegex(ValueError, "draft_digest"):
            resolve_component_requirements(draft)

    def test_browser_json_round_trip_preserves_digest_validation(self) -> None:
        _, _, draft = _ready("设计 555 多波形发生器", "waveform-analog-555")
        round_tripped = json.loads(json.dumps(draft, ensure_ascii=False))
        result = resolve_component_requirements(round_tripped)
        self.assertEqual(result["draft_digest"], draft["draft_digest"])

    def test_explicit_primitive_selections_can_pass_rating_gate(self) -> None:
        _, _, draft = _ready("设计传感器无源滤波", "signal-passive")
        selections = {
            "cr-01": {"family": "series-resistor", "voltage_rating_v": 10},
            "cr-02": {"family": "resistor", "voltage_rating_v": 10},
            "cr-03": {"family": "resistor-divider", "voltage_rating_v": 10},
        }
        result = resolve_component_requirements(draft, selections)
        self.assertEqual(result["summary"]["unresolved_selection_count"], 0)
        self.assertEqual(result["summary"]["model_pending_count"], 0)
        self.assertEqual(result["summary"]["ratings_pending_count"], 0)
        self.assertEqual(result["state"], "ready-for-netlist-review")
        self.assertEqual(result["next_step"], "compile_executable_netlist_after_human_approval")
        self.assertFalse(result["ready_for_executable_netlist"])
        self.assertEqual(result["requirements"][0]["rating_status"], "passed")

    def test_model_provenance_requires_a_sha256_digest(self) -> None:
        _, _, draft = _ready("设计机器人电机编码器闭环控制", "control-pid-feedforward")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            resolve_component_requirements(
                draft,
                {"cr-01": {"family": "dc-motor-model", "model_source": {"name": "local-model", "sha256": "bad"}}},
            )

    def test_provided_model_provenance_enters_human_approval_gate(self) -> None:
        _, _, draft = _ready("设计 555 多波形发生器", "waveform-analog-555")
        baseline = resolve_component_requirements(draft)
        selections = {}
        for item in baseline["requirements"]:
            candidate = item["candidates"][0]
            selected = {"family": candidate["family"]}
            for rating in candidate.get("rating_requirements", []):
                if rating.get("minimum") is not None:
                    selected[rating["metric"]] = rating["minimum"]
            if candidate["model_requirement"] == "verified-model-required":
                selected["model_source"] = {
                    "name": f"model-{item['requirement_id']}",
                    "uri": f"models/{item['requirement_id']}.lib",
                    "sha256": "a" * 64,
                    "license": "internal-review",
                }
            selections[item["requirement_id"]] = selected
        result = resolve_component_requirements(draft, json.loads(json.dumps(selections)))
        self.assertEqual(result["summary"]["unresolved_selection_count"], 0)
        self.assertEqual(result["summary"]["model_pending_count"], 0)
        self.assertEqual(result["next_step"], "compile_executable_netlist_after_human_approval")
        self.assertEqual(
            {item["model_status"] for item in result["requirements"] if item["selected_candidate"]["model_requirement"] == "verified-model-required"},
            {"provided-not-verified"},
        )

    def test_unknown_requirement_selection_is_rejected(self) -> None:
        _, _, draft = _ready("设计传感器无源滤波", "signal-passive")
        with self.assertRaisesRegex(ValueError, "unknown requirement"):
            resolve_component_requirements(draft, {"cr-99": "resistor"})

    def test_mcp_adapter_exposes_the_same_read_only_boundary(self) -> None:
        _, _, draft = _ready("设计通用接口电路", "general-minimal")
        result = server.resolve_component_requirements(draft)
        self.assertEqual(result["draft_id"], draft["draft_id"])
        self.assertFalse(result["execution_boundary"]["simulation_started"])


if __name__ == "__main__":
    unittest.main()
