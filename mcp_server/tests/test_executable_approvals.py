"""Tests for the explicit executable-netlist approval gate."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp import server
from multisim_mcp.component_approvals import approve_component_resolution
from multisim_mcp.component_resolution import resolve_component_requirements
from multisim_mcp.design_plans import plan_design_options, select_design_option
from multisim_mcp.design_specifications import prepare_design_specification
from multisim_mcp.executable_approvals import (
    approve_executable_netlist,
    validate_executable_netlist_approval,
)
from multisim_mcp.executable_netlists import compile_executable_netlist
from multisim_mcp.netlist_drafts import prepare_netlist_draft


def _compiled() -> dict[str, object]:
    plan = plan_design_options("设计一个传感器信号调理电路")
    selected = select_design_option(plan, "signal-passive")
    specification = prepare_design_specification(
        selected,
        {
            "supply_voltage_v": 5,
            "input_min_v": 0,
            "input_max_v": 5,
            "output_min_v": 0.5,
            "output_max_v": 2.5,
            "source_impedance_ohm": 100,
            "load_impedance_ohm": 100_000,
            "cutoff_frequency_hz": 1_000,
        },
    )
    draft = prepare_netlist_draft(
        selected,
        specification,
        {
            "approved": True,
            "specification_id": specification["specification_id"],
            "specification_digest": specification["specification_digest"],
        },
    )
    resolution = resolve_component_requirements(
        draft,
        {
            "cr-01": {"family": "series-resistor", "voltage_rating_v": 10},
            "cr-02": {"family": "capacitor", "voltage_rating_v": 10},
            "cr-03": {"family": "resistor-divider", "voltage_rating_v": 10},
        },
    )
    component_approval = approve_component_resolution(
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
    return compile_executable_netlist(draft, component_approval)


class ExecutableNetlistApprovalTest(unittest.TestCase):
    def test_binds_human_review_to_compiled_preview(self) -> None:
        preview = _compiled()
        artifact = approve_executable_netlist(
            json.loads(json.dumps(preview)),
            {
                "approved": True,
                "compiled_id": preview["compiled_id"],
                "compiled_digest": preview["compiled_digest"],
                "confirm_components": True,
                "confirm_topology": True,
                "confirm_calculated_values": True,
                "confirm_spice": True,
                "review_note": "已审阅引脚、计算值和 SPICE 预览",
            },
        )
        self.assertEqual(artifact["kind"], "multisim-mcp-executable-netlist-approval")
        self.assertTrue(artifact["ready_for_schematic"])
        self.assertFalse(artifact["ready_for_simulation"])
        self.assertEqual(artifact["next_step"], "create_schematic_after_netlist_approval")
        self.assertFalse(artifact["execution_boundary"]["files_written"])
        self.assertEqual(
            validate_executable_netlist_approval(preview, json.loads(json.dumps(artifact))),
            artifact,
        )

    def test_rejects_tampering_after_review(self) -> None:
        preview = _compiled()
        artifact = approve_executable_netlist(
            preview,
            {
                "approved": True,
                "compiled_id": preview["compiled_id"],
                "compiled_digest": preview["compiled_digest"],
                "confirm_components": True,
                "confirm_topology": True,
                "confirm_calculated_values": True,
                "confirm_spice": True,
            },
        )
        tampered = json.loads(json.dumps(artifact))
        tampered["spice_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "does not match the compiled preview"):
            validate_executable_netlist_approval(preview, tampered)

    def test_requires_every_explicit_confirmation(self) -> None:
        preview = _compiled()
        with self.assertRaisesRegex(ValueError, "confirm_spice"):
            approve_executable_netlist(
                preview,
                {
                    "approved": True,
                    "compiled_id": preview["compiled_id"],
                    "compiled_digest": preview["compiled_digest"],
                    "confirm_components": True,
                    "confirm_topology": True,
                    "confirm_calculated_values": True,
                    "confirm_spice": False,
                },
            )

    def test_accepts_transport_envelopes(self) -> None:
        preview = _compiled()
        artifact = approve_executable_netlist(
            {"service": "multisim-mcp-workbench", "success": True, **preview},
            {
                "approved": True,
                "compiled_id": preview["compiled_id"],
                "compiled_digest": preview["compiled_digest"],
                "confirm_components": True,
                "confirm_topology": True,
                "confirm_calculated_values": True,
                "confirm_spice": True,
            },
        )
        self.assertEqual(
            validate_executable_netlist_approval(
                preview,
                {"service": "multisim-mcp-workbench", "approval_only": True, **artifact},
            ),
            artifact,
        )

    def test_accepts_javascript_number_normalization(self) -> None:
        preview = _compiled()

        def javascript_numbers(value):
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if isinstance(value, dict):
                return {key: javascript_numbers(item) for key, item in value.items()}
            if isinstance(value, list):
                return [javascript_numbers(item) for item in value]
            return value

        transported = javascript_numbers(json.loads(json.dumps(preview)))
        artifact = approve_executable_netlist(
            transported,
            {
                "approved": True,
                "compiled_id": preview["compiled_id"],
                "compiled_digest": preview["compiled_digest"],
                "confirm_components": True,
                "confirm_topology": True,
                "confirm_calculated_values": True,
                "confirm_spice": True,
            },
        )
        self.assertTrue(artifact["ready_for_schematic"])

    def test_approved_preview_can_feed_schematic_generation(self) -> None:
        preview = _compiled()
        artifact = approve_executable_netlist(
            preview,
            {
                "approved": True,
                "compiled_id": preview["compiled_id"],
                "compiled_digest": preview["compiled_digest"],
                "confirm_components": True,
                "confirm_topology": True,
                "confirm_calculated_values": True,
                "confirm_spice": True,
            },
        )
        native_result = {
            "success": True,
            "build": {"editable_model_coverage": {"status": "not_applicable"}},
            "verification": {"native_netlist_complete": True},
            "ms14": "approved.ms14",
            "xml": "approved.ms14.xml",
            "experimental_probes": False,
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "approved schematic.ms14"
            with patch.object(
                server, "_create_schematic_impl", return_value=native_result
            ) as executor:
                result = server.create_schematic_from_netlist(
                    preview["spice_netlist"],
                    str(output),
                    open_after_build=False,
                    executable_netlist=preview,
                    netlist_approval=artifact,
                )

        self.assertEqual(result["success"], True)
        self.assertEqual(
            result["netlist_approval"]["approval_id"], artifact["approval_id"]
        )
        self.assertFalse(result["netlist_approval"]["simulation_started"])
        self.assertEqual(executor.call_args.args[0], preview["spice_netlist"])

    def test_schematic_generation_rejects_partial_approval_handoff(self) -> None:
        preview = _compiled()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            server, "_create_schematic_impl"
        ) as executor:
            with self.assertRaisesRegex(ValueError, "must be provided together"):
                server.create_schematic_from_netlist(
                    preview["spice_netlist"],
                    str(Path(tmp) / "partial.ms14"),
                    executable_netlist=preview,
                )
        executor.assert_not_called()

    def test_schematic_generation_rejects_netlist_changed_after_approval(self) -> None:
        preview = _compiled()
        artifact = approve_executable_netlist(
            preview,
            {
                "approved": True,
                "compiled_id": preview["compiled_id"],
                "compiled_digest": preview["compiled_digest"],
                "confirm_components": True,
                "confirm_topology": True,
                "confirm_calculated_values": True,
                "confirm_spice": True,
            },
        )
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            server, "_create_schematic_impl"
        ) as executor:
            with self.assertRaisesRegex(ValueError, "does not match"):
                server.create_schematic_from_netlist(
                    preview["spice_netlist"] + "* changed\n",
                    str(Path(tmp) / "changed.ms14"),
                    executable_netlist=preview,
                    netlist_approval=artifact,
                )
        executor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
