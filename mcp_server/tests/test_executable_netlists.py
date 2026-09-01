"""Tests for the bounded approved-draft compiler."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.component_approvals import approve_component_resolution
from multisim_mcp.component_resolution import resolve_component_requirements
from multisim_mcp.design_plans import plan_design_options, select_design_option
from multisim_mcp.design_specifications import prepare_design_specification
from multisim_mcp.eda_core import CircuitDesign
from multisim_mcp.executable_netlists import (
    COMPILER_SUPPORT_MATRIX,
    compile_executable_netlist,
    verify_approved_model_files,
)
from multisim_mcp.netlist_drafts import prepare_netlist_draft
from multisim_mcp.spice_adapter import circuit_design_from_spice


def _draft(*, option_id: str = "signal-passive", amplifying: bool = False):
    plan = plan_design_options("设计一个传感器信号调理电路")
    selected = select_design_option(plan, option_id)
    parameters = {
        "supply_voltage_v": 5,
        "input_min_v": 0,
        "input_max_v": 5,
        "output_min_v": 0.5,
        "output_max_v": 2.5,
        "source_impedance_ohm": 100,
        "load_impedance_ohm": 100_000,
        "cutoff_frequency_hz": 1_000,
    }
    if amplifying:
        parameters.update(
            {
                "input_min_v": 0.1,
                "input_max_v": 1,
                "output_min_v": 0.2,
                "output_max_v": 3.3,
            }
        )
    specification = prepare_design_specification(selected, parameters)
    return prepare_netlist_draft(
        selected,
        specification,
        {
            "approved": True,
            "specification_id": specification["specification_id"],
            "specification_digest": specification["specification_digest"],
        },
    )


def _approved(draft, *, filter_family: str = "capacitor"):
    resolution = resolve_component_requirements(
        draft,
        {
            "cr-01": {"family": "series-resistor", "voltage_rating_v": 10},
            "cr-02": {"family": filter_family, "voltage_rating_v": 10},
            "cr-03": {"family": "resistor-divider", "voltage_rating_v": 10},
        },
    )
    return approve_component_resolution(
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


class ExecutableNetlistCompilerTest(unittest.TestCase):
    def test_compiles_supported_template_without_execution(self) -> None:
        draft = _draft()
        approval = _approved(draft)
        result = compile_executable_netlist(
            json.loads(json.dumps(draft)),
            json.loads(json.dumps(approval)),
        )
        repeated = compile_executable_netlist(draft, approval)

        self.assertEqual(result["kind"], "multisim-mcp-executable-netlist-preview")
        self.assertEqual(result["compiled_digest"], repeated["compiled_digest"])
        self.assertTrue(result["ready_for_netlist_approval"])
        self.assertFalse(result["ready_for_schematic"])
        self.assertFalse(result["ready_for_simulation"])
        self.assertTrue(result["execution_boundary"]["circuit_design_created"])
        self.assertTrue(result["execution_boundary"]["spice_netlist_generated"])
        self.assertFalse(result["execution_boundary"]["files_written"])
        self.assertEqual(result["next_step"], "approve_executable_netlist")
        self.assertEqual(len(result["circuit_design"]["components"]), 6)
        CircuitDesign.from_dict(result["circuit_design"])
        parsed = circuit_design_from_spice(result["spice_netlist"])
        self.assertEqual(len(parsed.components), 6)
        self.assertAlmostEqual(
            result["synthesis"]["target_transfer"]["estimated_output_min_v"],
            0.5,
        )
        self.assertAlmostEqual(
            result["synthesis"]["target_transfer"]["estimated_output_max_v"],
            2.5,
        )

    def test_rejects_component_family_that_does_not_bind_template(self) -> None:
        draft = _draft()
        approval = _approved(draft, filter_family="resistor")
        with self.assertRaisesRegex(ValueError, "exact physical families"):
            compile_executable_netlist(draft, approval)

    def test_rejects_passive_amplification_request(self) -> None:
        draft = _draft(amplifying=True)
        approval = _approved(draft)
        with self.assertRaisesRegex(ValueError, "cannot provide voltage gain"):
            compile_executable_netlist(draft, approval)

    def test_rejects_option_without_pin_level_compiler(self) -> None:
        draft = _draft()
        approval = _approved(draft)
        with patch.dict(COMPILER_SUPPORT_MATRIX, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "no pin-level compiler"):
                compile_executable_netlist(draft, approval)

    def test_rehashes_approved_model_and_rejects_traversal(self) -> None:
        content = b".model TEST D\n"
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test.lib").write_bytes(content)
            snapshot = {
                "cr-01": {
                    "family": "external",
                    "model_source": {
                        "name": "TEST",
                        "uri": "test.lib",
                        "sha256": digest,
                        "license": "MIT",
                    },
                }
            }
            verified = verify_approved_model_files(snapshot, root)
            self.assertEqual(verified[0]["sha256"], digest)
            snapshot["cr-01"]["model_source"]["uri"] = "../test.lib"
            with self.assertRaisesRegex(ValueError, "model root"):
                verify_approved_model_files(snapshot, root)

    def test_rejects_model_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test.lib").write_text("changed", encoding="utf-8")
            snapshot = {
                "cr-01": {
                    "model_source": {
                        "name": "TEST",
                        "uri": "test.lib",
                        "sha256": "0" * 64,
                        "license": "MIT",
                    }
                }
            }
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                verify_approved_model_files(snapshot, root)


if __name__ == "__main__":
    unittest.main()
