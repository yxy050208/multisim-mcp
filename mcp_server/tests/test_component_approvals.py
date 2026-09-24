"""Tests for the explicit component-resolution approval boundary."""

from __future__ import annotations

import json
import unittest

from multisim_mcp.component_approvals import (
    approve_component_resolution,
    validate_component_approval,
)
from multisim_mcp.component_resolution import resolve_component_requirements
from multisim_mcp.design_plans import plan_design_options, select_design_option
from multisim_mcp.design_specifications import prepare_design_specification
from multisim_mcp.netlist_drafts import prepare_netlist_draft


def _ready(option_id: str = "signal-passive"):
    plan = plan_design_options("设计一个传感器信号调理电路")
    selected = select_design_option(plan, option_id)
    specification = prepare_design_specification(
        selected,
        {
            "supply_voltage_v": 5,
            "input_min_v": 0.1,
            "input_max_v": 1,
            "output_min_v": 0.2,
            "output_max_v": 3.3,
            "source_impedance_ohm": 100,
            "load_impedance_ohm": 100_000,
            "cutoff_frequency_hz": 1_000,
        },
    )
    approval = {
        "approved": True,
        "specification_id": specification["specification_id"],
        "specification_digest": specification["specification_digest"],
    }
    draft = prepare_netlist_draft(selected, specification, approval)
    return draft


class ComponentApprovalTest(unittest.TestCase):
    def _primitive_resolution(self):
        draft = _ready()
        resolution = resolve_component_requirements(
            draft,
            {
                "cr-01": {"family": "series-resistor", "voltage_rating_v": 10},
                "cr-02": {"family": "resistor", "voltage_rating_v": 10},
                "cr-03": {"family": "resistor-divider", "voltage_rating_v": 10},
            },
        )
        return draft, resolution

    def test_rejects_recommended_only_resolution(self) -> None:
        draft = _ready()
        resolution = resolve_component_requirements(draft)
        with self.assertRaisesRegex(ValueError, "no explicit component selection"):
            approve_component_resolution(
                draft,
                resolution,
                {
                    "approved": True,
                    "resolution_id": resolution["resolution_id"],
                    "resolution_digest": resolution["resolution_digest"],
                    "confirm_topology": True,
                    "confirm_ratings": True,
                    "confirm_model_provenance": True,
                },
            )

    def test_approves_complete_primitive_resolution_without_execution(self) -> None:
        draft, resolution = self._primitive_resolution()
        result = approve_component_resolution(
            json.loads(json.dumps(draft)),
            json.loads(json.dumps(resolution)),
            {
                "approved": True,
                "resolution_id": resolution["resolution_id"],
                "resolution_digest": resolution["resolution_digest"],
                "confirm_topology": True,
                "confirm_ratings": True,
                "confirm_model_provenance": True,
                "review_note": "Reviewed in local workbench",
            },
        )
        self.assertEqual(result["kind"], "multisim-mcp-component-resolution-approval")
        self.assertTrue(result["ready_for_executable_netlist"])
        self.assertFalse(result["ready_for_schematic"])
        self.assertFalse(result["ready_for_simulation"])
        self.assertFalse(result["execution_boundary"]["spice_netlist_generated"])
        self.assertEqual(result["next_step"], "compile_executable_netlist")
        self.assertEqual(len(result["approved_requirements"]), 3)
        self.assertEqual(validate_component_approval(draft, result), result)

    def test_validator_rejects_tampered_readiness_flag(self) -> None:
        draft, resolution = self._primitive_resolution()
        result = approve_component_resolution(
            draft,
            resolution,
            {
                "approved": True,
                "resolution_id": resolution["resolution_id"],
                "resolution_digest": resolution["resolution_digest"],
                "confirm_topology": True,
                "confirm_ratings": True,
                "confirm_model_provenance": True,
            },
        )
        result["ready_for_simulation"] = True
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_component_approval(draft, result)

    def test_rejects_failed_rating(self) -> None:
        draft = _ready()
        resolution = resolve_component_requirements(
            draft,
            {
                "cr-01": {"family": "series-resistor", "voltage_rating_v": 1},
                "cr-02": {"family": "resistor", "voltage_rating_v": 10},
                "cr-03": {"family": "resistor-divider", "voltage_rating_v": 10},
            },
        )
        with self.assertRaisesRegex(ValueError, "ratings are not approved"):
            approve_component_resolution(
                draft,
                resolution,
                {
                    "approved": True,
                    "resolution_id": resolution["resolution_id"],
                    "resolution_digest": resolution["resolution_digest"],
                    "confirm_topology": True,
                    "confirm_ratings": True,
                    "confirm_model_provenance": True,
                },
            )

    def test_rejects_tampered_snapshot(self) -> None:
        draft, resolution = self._primitive_resolution()
        resolution["selection_snapshot"]["cr-01"]["voltage_rating_v"] = 999
        with self.assertRaisesRegex(ValueError, "resolution digest"):
            approve_component_resolution(
                draft,
                resolution,
                {
                    "approved": True,
                    "resolution_id": resolution["resolution_id"],
                    "resolution_digest": resolution["resolution_digest"],
                    "confirm_topology": True,
                    "confirm_ratings": True,
                    "confirm_model_provenance": True,
                },
            )

    def test_external_model_requires_reviewed_license(self) -> None:
        draft = _ready("signal-active")
        resolution = resolve_component_requirements(
            draft,
            {
                "cr-01": {"family": "series-resistor", "voltage_rating_v": 10},
                "cr-02": {"family": "rail-to-rail-op-amp", "part_number": "U1", "voltage_rating_v": 10},
                "cr-03": {"family": "precision-resistor-network", "voltage_rating_v": 10},
                "cr-04": {"family": "op-amp-buffer", "part_number": "U2", "voltage_rating_v": 10},
                "cr-05": {"family": "ceramic-capacitor", "voltage_rating_v": 10},
            },
        )
        with self.assertRaisesRegex(ValueError, "model provenance"):
            approve_component_resolution(
                draft,
                resolution,
                {
                    "approved": True,
                    "resolution_id": resolution["resolution_id"],
                    "resolution_digest": resolution["resolution_digest"],
                    "confirm_topology": True,
                    "confirm_ratings": True,
                    "confirm_model_provenance": True,
                },
            )


if __name__ == "__main__":
    unittest.main()
