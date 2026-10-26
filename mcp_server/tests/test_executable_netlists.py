"""Tests for the bounded approved-draft compiler."""

from __future__ import annotations

import hashlib
import io
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
    _model_relative_path,
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


class ModelFileBoundaryTest(unittest.TestCase):
    def test_model_paths_reject_windows_aliases_on_all_hosts(self) -> None:
        for uri in (
            "C:/models/test.lib", "C:test.lib", "//host/models/test.lib",
            "file:///test.lib", "https://example.invalid/test.lib", "/test.lib",
            "../test.lib", "models/../test.lib", "models//test.lib", "./test.lib",
            "test.lib:stream", "NUL.lib", "models/COM1", "models/name./test.lib",
            "models/name /test.lib", "models/test\x00.lib",
        ):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                _model_relative_path(uri)
        self.assertEqual(_model_relative_path("models\\测试.lib").as_posix(), "models/测试.lib")

    def test_rehash_is_readonly_and_returns_no_content_or_absolute_path(self) -> None:
        content = b"* PRIVATE MODEL BODY\n.model TEST D\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "模型").mkdir()
            model = root / "模型" / "测试.lib"
            model.write_bytes(content)
            snapshot = {"cr-01": {"model_source": {
                "name": "TEST", "uri": "模型/测试.lib",
                "sha256": hashlib.sha256(content).hexdigest(), "license": "local-use-only",
            }}}
            before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            result = verify_approved_model_files(snapshot, root)
            encoded = json.dumps(result, ensure_ascii=False)
            self.assertNotIn("PRIVATE MODEL BODY", encoded)
            self.assertNotIn(str(root), encoded)
            self.assertEqual(result[0]["license"], "local-use-only")
            self.assertEqual(result[0]["status"], "bytes-rehashed")
            self.assertEqual(model.read_bytes(), content)
            self.assertEqual(before, sorted(path.relative_to(root).as_posix() for path in root.rglob("*")))
            model.write_bytes(content + b"changed")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                verify_approved_model_files(snapshot, root)

    def test_placeholder_license_rejected_before_file_read(self) -> None:
        for license_value in ("pending", " NOASSERTION ", "待核对", "unknown", "unspecified"):
            with self.subTest(license=license_value):
                snapshot = {"cr-01": {"model_source": {
                    "name": "TEST", "uri": "test.lib", "sha256": "a" * 64, "license": license_value,
                }}}
                with patch.object(Path, "open", side_effect=AssertionError("must not read")):
                    with self.assertRaisesRegex(ValueError, "reviewed license"):
                        verify_approved_model_files(snapshot, "missing-root")

    def test_read_is_bounded_even_when_file_grows_after_stat(self) -> None:
        content = b"123456789"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test.lib").write_bytes(content)
            snapshot = {"cr-01": {"model_source": {
                "name": "TEST", "uri": "test.lib", "sha256": hashlib.sha256(content).hexdigest(),
                "license": "MIT",
            }}}
            with patch("multisim_mcp.executable_netlists.MAX_MODEL_FILE_BYTES", 8):
                with self.assertRaisesRegex(ValueError, "exceeds 8 bytes"):
                    verify_approved_model_files(snapshot, root)
                (root / "test.lib").write_bytes(b"1")
                with patch.object(Path, "open", return_value=io.BytesIO(content)) as opened:
                    with self.assertRaisesRegex(ValueError, "exceeds 8 bytes"):
                        verify_approved_model_files(snapshot, root)
                    opened.assert_called_once_with("rb")

    def test_symlink_outside_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            outside = Path(directory) / "outside.lib"
            outside.write_bytes(b".model TEST D\n")
            try:
                (root / "test.lib").symlink_to(outside)
            except OSError:
                self.skipTest("symlink creation is unavailable on this host")
            snapshot = {"cr-01": {"model_source": {
                "name": "TEST", "uri": "test.lib", "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                "license": "MIT",
            }}}
            with self.assertRaisesRegex(ValueError, "outside model_root"):
                verify_approved_model_files(snapshot, root)

    def test_report_size_matches_bytes_actually_hashed(self) -> None:
        content = b".model TEST D\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test.lib").write_bytes(b"1")
            digest = hashlib.sha256(content).hexdigest()
            snapshot = {"cr-01": {"model_source": {
                "name": "TEST", "uri": "test.lib", "sha256": digest, "license": "MIT",
            }}}
            with patch.object(Path, "open", return_value=io.BytesIO(content)):
                verified = verify_approved_model_files(snapshot, root)
            self.assertEqual(verified[0]["sha256"], digest)
            self.assertEqual(verified[0]["size_bytes"], len(content))


if __name__ == "__main__":
    unittest.main()
