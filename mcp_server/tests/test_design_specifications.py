"""Tests for the selected-plan to electrical-specification boundary."""

from __future__ import annotations

import unittest

from multisim_mcp import server
from multisim_mcp.design_plans import plan_design_options, select_design_option
from multisim_mcp.design_specifications import prepare_design_specification


def _selected(requirements: str, *, option_id: str | None = None) -> dict[str, object]:
    plan = plan_design_options(requirements)
    return select_design_option(plan, option_id or plan["recommended_option_id"])


def _completion_values(draft: dict[str, object]) -> dict[str, object]:
    values: dict[str, object] = {}
    for item in draft["parameter_requirements"]:
        if item["status"] != "missing":
            continue
        if item["suggested_value"] is not None:
            value = item["suggested_value"]
        elif item["value_type"] == "choice":
            value = item["choices"][0]
        elif item["value_type"] == "text":
            value = "required output"
        elif item["value_type"] == "integer":
            value = int(max(1, item["minimum"] or 1))
        else:
            value = max(1.0, item["minimum"] or 1.0)
        values[item["parameter_id"]] = value
    return values


class DesignSpecificationTest(unittest.TestCase):
    def test_requires_a_complete_selected_plan_envelope(self) -> None:
        proposed = plan_design_options("设计 RC 低通滤波器")
        with self.assertRaisesRegex(ValueError, "must be selected"):
            prepare_design_specification(proposed)

    def test_waveform_requirements_are_inferred_without_creating_artifacts(self) -> None:
        selected = _selected(
            "使用 +10V 单电源，频率 20kHz~50kHz，输出负载 600 欧姆的多波形电路"
        )
        result = prepare_design_specification(selected)
        self.assertEqual(result["state"], "needs-input")
        self.assertFalse(result["ready_for_netlist_draft"])
        self.assertEqual(result["artifacts_generated"], [])
        self.assertFalse(result["execution_boundary"]["netlist_generated"])
        self.assertFalse(result["execution_boundary"]["files_written"])
        self.assertEqual(result["resolved_parameters"]["supply_voltage_v"], 10.0)
        self.assertEqual(result["resolved_parameters"]["frequency_min_hz"], 20_000.0)
        self.assertEqual(result["resolved_parameters"]["frequency_max_hz"], 50_000.0)
        self.assertEqual(result["resolved_parameters"]["load_resistance_ohm"], 600.0)

    def test_complete_parameters_produce_a_stable_reviewable_specification(self) -> None:
        selected = _selected("设计一个传感器低通滤波和 ADC 调理电路")
        draft = prepare_design_specification(selected)
        values = _completion_values(draft)
        ready = prepare_design_specification(selected, values)
        repeated = prepare_design_specification(selected, dict(reversed(list(values.items()))))
        self.assertEqual(ready["state"], "ready")
        self.assertTrue(ready["ready_for_netlist_draft"])
        self.assertEqual(ready["missing_parameter_ids"], [])
        self.assertEqual(ready["next_step"], "review_specification_before_netlist")
        self.assertEqual(ready["specification_digest"], repeated["specification_digest"])
        self.assertGreaterEqual(len(ready["modules"]), 3)
        self.assertGreaterEqual(len(ready["analysis_plan"]), 4)
        self.assertTrue(ready["approval"]["required_before_netlist"])
        self.assertFalse(ready["approval"]["specification_approved"])

    def test_invalid_values_and_unknown_fields_fail_closed(self) -> None:
        selected = _selected("设计一个同步 Buck 电源", option_id="power-buck")
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            prepare_design_specification(selected, {"secret_command": "run"})
        with self.assertRaisesRegex(ValueError, "must be >="):
            prepare_design_specification(selected, {"output_voltage_v": -1})

    def test_tampered_selection_digest_is_rejected(self) -> None:
        selected = _selected("设计机器人底盘电机闭环控制")
        selected["selection_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "selection_digest"):
            prepare_design_specification(selected)

    def test_mcp_adapter_exposes_the_same_read_only_contract(self) -> None:
        selected = _selected("设计一个运放有源滤波器", option_id="signal-active")
        result = server.prepare_design_specification(selected)
        self.assertEqual(result["kind"], "multisim-mcp-design-specification")
        self.assertEqual(result["selected_option_id"], "signal-active")
        self.assertFalse(result["execution_boundary"]["circuit_design_created"])


if __name__ == "__main__":
    unittest.main()
