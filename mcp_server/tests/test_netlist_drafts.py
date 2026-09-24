"""Tests for the approved-specification to logical-netlist boundary."""

from __future__ import annotations

import unittest

from multisim_mcp import server
from multisim_mcp.design_plans import plan_design_options, select_design_option
from multisim_mcp.design_specifications import prepare_design_specification
from multisim_mcp.netlist_drafts import prepare_netlist_draft


_VALUES = {
    "waveform-generation": {
        "supply_voltage_v": 10,
        "frequency_min_hz": 20_000,
        "frequency_max_hz": 50_000,
        "output_amplitude_vpp": 3,
        "load_resistance_ohm": 600,
        "waveform_targets": "square, triangle, sine",
        "max_frequency_error_percent": 5,
        "max_amplitude_error_percent": 5,
        "timing_tolerance_percent": 5,
        "sample_rate_hz": 1_000_000,
        "dac_resolution_bits": 12,
    },
    "power-electronics": {
        "input_voltage_min_v": 12,
        "input_voltage_max_v": 24,
        "output_voltage_v": 5,
        "continuous_current_a": 2,
        "peak_current_a": 4,
        "ripple_max_mv": 50,
        "ambient_temperature_c": 60,
        "switching_frequency_hz": 500_000,
    },
    "signal-conditioning": {
        "supply_voltage_v": 5,
        "input_min_v": 0.1,
        "input_max_v": 1,
        "output_min_v": 0.2,
        "output_max_v": 3.3,
        "source_impedance_ohm": 100,
        "load_impedance_ohm": 100_000,
        "cutoff_frequency_hz": 1_000,
        "sample_rate_hz": 10_000,
    },
    "robot-control": {
        "dc_bus_voltage_v": 24,
        "motor_rated_voltage_v": 24,
        "continuous_current_a": 5,
        "stall_current_a": 20,
        "pwm_frequency_hz": 20_000,
        "control_loop_frequency_hz": 1_000,
        "feedback_sensor": "encoder",
        "max_latency_ms": 0.2,
        "ambient_temperature_c": 60,
    },
    "general-circuit": {
        "supply_voltage_v": 5,
        "input_min_v": 0,
        "input_max_v": 3.3,
        "output_min_v": 0,
        "output_max_v": 5,
        "max_current_a": 1,
        "bandwidth_hz": 100_000,
        "load_impedance_ohm": 1_000,
    },
}


def _ready(requirements: str, option_id: str | None = None):
    plan = plan_design_options(requirements)
    selected = select_design_option(plan, option_id or plan["recommended_option_id"])
    first = prepare_design_specification(selected)
    known = {item["parameter_id"] for item in first["parameter_requirements"]}
    values = {key: value for key, value in _VALUES[plan["domain"]].items() if key in known}
    specification = prepare_design_specification(selected, values)
    approval = {
        "approved": True,
        "specification_id": specification["specification_id"],
        "specification_digest": specification["specification_digest"],
    }
    return selected, specification, approval


class NetlistDraftTest(unittest.TestCase):
    def test_explicit_approval_is_required(self) -> None:
        plan, specification, approval = _ready("设计 555 多波形发生器")
        approval["approved"] = False
        with self.assertRaisesRegex(ValueError, "must be true"):
            prepare_netlist_draft(plan, specification, approval)

    def test_logical_draft_is_stable_and_non_executable(self) -> None:
        plan, specification, approval = _ready("设计 555 多波形发生器")
        result = prepare_netlist_draft(plan, specification, approval)
        repeated = prepare_netlist_draft(plan, specification, dict(approval))
        self.assertEqual(result["draft_digest"], repeated["draft_digest"])
        self.assertEqual(result["kind"], "multisim-mcp-logical-netlist-draft")
        self.assertEqual(result["topology_level"], "logical-block-netlist")
        self.assertGreaterEqual(len(result["topology"]["modules"]), 3)
        self.assertGreaterEqual(len(result["component_requirements"]), 3)
        self.assertIn("NOT SPICE", result["logical_netlist_preview"])
        self.assertTrue(result["ready_for_component_resolution"])
        self.assertFalse(result["ready_for_schematic"])
        self.assertFalse(result["ready_for_simulation"])
        self.assertFalse(result["execution_boundary"]["circuit_design_created"])
        self.assertFalse(result["execution_boundary"]["spice_netlist_generated"])
        self.assertFalse(result["execution_boundary"]["files_written"])
        self.assertEqual(result["artifacts_generated"], [])

    def test_specification_and_approval_tampering_are_rejected(self) -> None:
        plan, specification, approval = _ready("设计有源运放滤波 ADC 调理", "signal-active")
        specification["selected_option_id"] = "signal-passive"
        with self.assertRaisesRegex(ValueError, "selected_option_id"):
            prepare_netlist_draft(plan, specification, approval)
        plan, specification, approval = _ready("设计有源运放滤波 ADC 调理", "signal-active")
        approval["specification_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "specification_digest"):
            prepare_netlist_draft(plan, specification, approval)

    def test_cross_parameter_constraints_fail_closed(self) -> None:
        plan, specification, approval = _ready("设计同步 Buck 电源", "power-buck")
        values = dict(specification["resolved_parameters"])
        values["peak_current_a"] = 1
        values["continuous_current_a"] = 2
        invalid = prepare_design_specification(plan, values)
        invalid_approval = {
            "approved": True,
            "specification_id": invalid["specification_id"],
            "specification_digest": invalid["specification_digest"],
        }
        with self.assertRaisesRegex(ValueError, "peak_current_a"):
            prepare_netlist_draft(plan, invalid, invalid_approval)

    def test_all_catalog_options_have_a_bounded_topology_template(self) -> None:
        requirements = (
            "设计 555 方波三角波正弦波发生器",
            "设计 Buck 稳压电源",
            "设计传感器有源滤波 ADC 调理",
            "设计机器人电机编码器闭环控制",
            "设计通用电路接口",
        )
        seen: set[str] = set()
        for requirement in requirements:
            proposed = plan_design_options(requirement)
            for option in proposed["options"]:
                plan, specification, approval = _ready(requirement, option["option_id"])
                result = prepare_netlist_draft(plan, specification, approval)
                seen.add(option["option_id"])
                self.assertLessEqual(len(result["topology"]["modules"]), 8)
                self.assertLessEqual(len(result["component_requirements"]), 12)
        self.assertEqual(len(seen), 15)

    def test_mcp_adapter_exposes_the_same_boundary(self) -> None:
        plan, specification, approval = _ready("设计机器人电机编码器闭环控制")
        result = server.prepare_netlist_draft(plan, specification, approval)
        self.assertEqual(result["kind"], "multisim-mcp-logical-netlist-draft")
        self.assertFalse(result["execution_boundary"]["simulation_started"])


if __name__ == "__main__":
    unittest.main()
