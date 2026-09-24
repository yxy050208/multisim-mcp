"""Tests for planning-only design options and selection boundaries."""

from __future__ import annotations

import unittest

from multisim_mcp.design_plans import (
    DESIGN_PLAN_SCHEMA_VERSION,
    DesignPlan,
    build_design_plan,
    plan_design_options,
    select_design_option,
)
from multisim_mcp import server


class DesignPlanContractTest(unittest.TestCase):
    def test_mcp_adapter_exposes_planning_only_result(self) -> None:
        result = server.plan_design_options(
            "为机器人底盘设计低延迟电机控制方案",
            constraints={"max_latency_ms": 3},
        )
        self.assertEqual(result["domain"], "robot-control")
        self.assertEqual(result["next_step"], "select_option_before_schematic")
        self.assertEqual(result["artifacts_generated"], [])

    def test_robot_plan_is_bounded_and_does_not_create_artifacts(self) -> None:
        result = plan_design_options(
            "为机甲大师云台设计低延迟、抗饱和的电机位置控制器",
            constraints={"max_latency_ms": 2, "requires": ["encoder", "CAN"]},
            context={"platform": "MCU", "stage": "prototype"},
        )
        self.assertEqual(result["schema_version"], DESIGN_PLAN_SCHEMA_VERSION)
        self.assertEqual(result["domain"], "robot-control")
        self.assertEqual(len(result["options"]), 3)
        self.assertEqual(result["state"], "proposed")
        self.assertEqual(result["next_step"], "select_option_before_schematic")
        self.assertEqual(result["artifacts_generated"], [])
        self.assertFalse(result["execution_boundary"]["schematic_generated"])
        self.assertFalse(result["execution_boundary"]["simulation_started"])
        self.assertFalse(result["execution_boundary"]["files_written"])
        self.assertEqual(result["recommended_option_id"], "control-robust-pid")
        for option in result["options"]:
            self.assertEqual(option["evidence_status"], "planning-only")
            self.assertGreaterEqual(option["score"], 0)
            self.assertLessEqual(option["score"], 100)

    def test_same_request_has_stable_plan_id_and_digest(self) -> None:
        first = plan_design_options("设计 20 kHz 到 50 kHz 可调方波源")
        second = plan_design_options(" 设计 20 kHz 到 50 kHz 可调方波源 ")
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(first["plan_digest"], second["plan_digest"])
        self.assertEqual(first["request_digest"], second["request_digest"])

    def test_objective_weight_changes_recommendation_without_claiming_measurement(self) -> None:
        result = plan_design_options(
            "机器人电机控制",
            objectives={"cost": 1},
        )
        self.assertEqual(result["recommended_option_id"], "control-pid-feedforward")
        self.assertTrue(all(item["evidence_status"] == "planning-only" for item in result["options"]))
        self.assertIn("不是仿真或实机结论", next(item for item in result["options"] if item["option_id"] == result["recommended_option_id"])["recommendation_reason"])

    def test_select_locks_option_but_keeps_execution_boundary(self) -> None:
        plan = build_design_plan("设计一个传感器抗混叠滤波器")
        selected = plan.select(plan.recommended_option_id)
        self.assertEqual(selected.state, "selected")
        self.assertEqual(selected.selected_option_id, plan.recommended_option_id)
        payload = selected.to_dict()
        self.assertFalse(payload["execution_boundary"]["schematic_generated"])
        self.assertFalse(payload["execution_boundary"]["simulation_started"])
        self.assertFalse(payload["execution_boundary"]["files_written"])
        self.assertEqual(DesignPlan.from_dict(payload).selected_option_id, plan.recommended_option_id)

    def test_selection_envelope_binds_source_and_selected_digests(self) -> None:
        source = plan_design_options("设计一个传感器抗混叠滤波器")
        option_id = source["options"][1]["option_id"]
        selected = select_design_option(source, option_id)
        self.assertEqual(selected["plan_id"], source["plan_id"])
        self.assertEqual(selected["state"], "selected")
        self.assertEqual(selected["selected_option_id"], option_id)
        self.assertEqual(selected["next_step"], "prepare_netlist_after_confirmation")
        self.assertEqual(selected["source_plan_digest"], source["plan_digest"])
        self.assertEqual(selected["selected_plan_digest"], selected["plan_digest"])
        self.assertRegex(selected["selection_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(DesignPlan.from_dict(selected).selected_option_id, option_id)
        repeated = select_design_option(selected, option_id)
        self.assertEqual(repeated["plan_digest"], selected["plan_digest"])
        self.assertEqual(repeated["source_plan_digest"], selected["source_plan_digest"])
        self.assertEqual(repeated["selection_digest"], selected["selection_digest"])

    def test_selection_rejects_tampering_and_conflicting_reselection(self) -> None:
        source = plan_design_options("机器人电机控制")
        tampered = dict(source)
        tampered["plan_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "plan_digest"):
            select_design_option(tampered, source["recommended_option_id"])
        selected = select_design_option(source, source["recommended_option_id"])
        other = next(item["option_id"] for item in source["options"] if item["option_id"] != source["recommended_option_id"])
        with self.assertRaisesRegex(ValueError, "different selected option"):
            select_design_option(selected, other)
        tampered_selection = dict(selected)
        tampered_selection["selection_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "selection_digest"):
            select_design_option(tampered_selection, source["recommended_option_id"])

    def test_invalid_requests_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "requirements must not be empty"):
            build_design_plan(" ")
        with self.assertRaisesRegex(ValueError, "max_options must be between"):
            build_design_plan("test", max_options=1)
        with self.assertRaisesRegex(ValueError, "objectives key"):
            build_design_plan("test", objectives={"speed": 1})
        with self.assertRaisesRegex(ValueError, "maximum nesting depth"):
            build_design_plan("test", context={"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}})

    def test_unknown_or_executable_plan_fields_are_rejected(self) -> None:
        payload = plan_design_options("RC 滤波器")
        payload["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            DesignPlan.from_dict(payload)
        executable = plan_design_options("RC 滤波器")
        executable["execution_boundary"]["files_written"] = True
        with self.assertRaisesRegex(ValueError, "planning-only"):
            DesignPlan.from_dict(executable)


if __name__ == "__main__":
    unittest.main()
