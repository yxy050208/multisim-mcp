"""Discovery must distinguish recorded cases, profile visibility and execution."""

from __future__ import annotations

import hashlib
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from multisim_mcp import __version__
from multisim_mcp.api_contract import build_capabilities
from multisim_mcp.cli import main
from multisim_mcp.circuit_template_registry import get_template_family
from multisim_mcp.engineering_capabilities import (
    build_engineering_capabilities,
    component_mapping_capabilities,
    engineering_runtime_readiness,
)
from multisim_mcp.executable_netlists import COMPILER_SUPPORT_MATRIX
from multisim_mcp.tool_profiles import ALL_TOOL_NAMES, tool_profile_status


class EngineeringCapabilitiesTest(unittest.TestCase):
    def test_legacy_labels_do_not_hide_workflows_or_certify_families(self) -> None:
        payload = build_engineering_capabilities(target_version="14.3")
        families = {item["id"]: item for item in payload["families"]}
        workflows = {item["id"]: item for item in payload["workflows"]}
        self.assertEqual(families["digital_logic"]["catalog_status"], "planned")
        self.assertIn("digital-netlist", families["digital_logic"]["workflow_ids"])
        self.assertEqual(workflows["digital-netlist"]["target_evidence_status"], "documented-cases-only")
        self.assertEqual(workflows["basic-multiboard"]["target_evidence_status"], "documented-cases-only")
        self.assertEqual(workflows["approved-signal-two-board"]["target_evidence_status"], "documented-cases-only")
        self.assertEqual(workflows["natural-dc"]["target_evidence_status"], "unverified")
        self.assertEqual(workflows["natural-opamp"]["target_evidence_status"], "unverified")
        self.assertEqual(workflows["natural-frontend"]["native_record_scope"], "related-backend-cases")
        self.assertEqual(get_template_family("dc_network")["status"], "next")
        self.assertTrue(all(not item["whole_family_verified"] for item in payload["families"]))

    def test_other_versions_never_borrow_mapping_or_case_evidence(self) -> None:
        for version in ("14.0", "14.2", "14.3.1", "15.0"):
            with self.subTest(version=version):
                payload = build_engineering_capabilities(target_version=version)
                mappings = payload["component_mappings"]
                self.assertEqual(mappings["status"], "manifest-unavailable")
                self.assertEqual(mappings["components"], [])
                self.assertEqual(mappings["available_manifest_versions"], ["14.3"])
                self.assertTrue(all(item["target_evidence_status"] == "unverified" for item in payload["workflows"]))
                self.assertTrue(all(not item["target_version_match"] for item in payload["native_records"]))

    def test_no_target_is_not_an_implicit_14_3_selection(self) -> None:
        payload = build_engineering_capabilities()
        self.assertIsNone(payload["component_mappings"]["target_version"])
        self.assertEqual(payload["component_mappings"]["status"], "not-selected")
        self.assertEqual(payload["component_mappings"]["components"], [])
        self.assertTrue(all(item["target_evidence_status"] == "not-selected" for item in payload["workflows"]))

    def test_target_normalization_and_invalid_inputs(self) -> None:
        canonical = component_mapping_capabilities("14.3")
        self.assertEqual(component_mapping_capabilities("NI Multisim 14.3.0"), canonical)
        for value in ("", "banana", "14.3/../models", "14.3\n15", True, 14.3):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "target_version"):
                component_mapping_capabilities(value)

    def test_manifest_provenance_and_unverified_carrier_are_preserved(self) -> None:
        payload = component_mapping_capabilities("Multisim 14.3")
        self.assertEqual(payload["status"], "exact-manifest")
        manifest = Path(__file__).parents[1] / "multisim_mcp" / "compatibility" / "components-14.3.json"
        self.assertEqual(payload["manifests"][0]["sha256"], hashlib.sha256(manifest.read_bytes()).hexdigest())
        mappings = {item["logical_family"]: item for item in payload["components"]}
        self.assertEqual(mappings["resistor"]["status"], "recorded-native-verified")
        self.assertEqual(mappings["vendor-opamp5-virtual"]["status"], "unverified")
        self.assertFalse(payload["current_machine_verified"])

    def test_profile_visibility_is_independent_of_implementation_and_evidence(self) -> None:
        core = build_engineering_capabilities(profile="core", target_version="14.3")
        full = build_engineering_capabilities(profile="full", target_version="14.3")
        core_multi = next(item for item in core["workflows"] if item["id"] == "basic-multiboard")
        full_multi = next(item for item in full["workflows"] if item["id"] == "basic-multiboard")
        self.assertEqual(core_multi["entrypoints_in_profile"], [])
        self.assertEqual(full_multi["entrypoints_in_profile"], full_multi["entrypoints"])
        self.assertEqual(core_multi["target_evidence_status"], full_multi["target_evidence_status"])
        self.assertFalse(core["generic_compiler"]["entrypoint_in_profile"])
        for item in full["workflows"]:
            self.assertTrue(set(item["entrypoints"]) <= ALL_TOOL_NAMES)

    def test_generic_compiler_is_not_promoted_by_specialized_runners(self) -> None:
        payload = build_engineering_capabilities()
        compiler = payload["generic_compiler"]
        self.assertEqual(compiler["supported_options"], COMPILER_SUPPORT_MATRIX)
        self.assertEqual(set(compiler["supported_options"]), {"signal-passive", "signal-active"})
        self.assertEqual(compiler["supported_options"]["signal-active"]["model_scope"], "explicit-ideal-vcvs-only")
        self.assertEqual(compiler["supported_options"]["signal-active"]["native_validation_status"], "unverified-for-this-template")
        self.assertEqual(compiler["unsupported_options"], "rejected")
        self.assertEqual(compiler["fixture_support"]["options"], ["signal-active"])
        self.assertEqual(compiler["fixture_support"]["native_record_ids"], ["ideal-signal-fixture"])

    def test_records_match_public_matrices_without_claiming_fresh_native_runs(self) -> None:
        from multisim_mcp.digital_regression import digital_regression_matrix
        from multisim_mcp.hybrid_regression import hybrid_regression_matrix
        from multisim_mcp.multiboard_digital_regression import multiboard_digital_regression_matrix

        payload = build_engineering_capabilities(target_version="14.3")
        records = {item["id"]: item for item in payload["native_records"]}
        matrices = {
            "digital-matrix": digital_regression_matrix(),
            "hybrid-matrix": hybrid_regression_matrix(),
            "multiboard-digital": multiboard_digital_regression_matrix(),
        }
        root = Path(__file__).parents[2]
        for record_id, cases in matrices.items():
            record = records[record_id]
            self.assertEqual(set(record["cases"]), {case.case_id for case in cases})
        for record in records.values():
            self.assertTrue((root / record["document"]).is_file())
            self.assertFalse(record["raw_artifacts_checked"])
            self.assertFalse(record["current_machine_verified"])
        self.assertEqual(payload["runtime"]["status"], "not-probed")

    def test_returned_metadata_cannot_change_compiler_or_other_calls(self) -> None:
        first = build_engineering_capabilities(target_version="14.3")
        expected = build_engineering_capabilities(target_version="14.3")
        first["generic_compiler"]["supported_options"]["signal-passive"]["required_families"].clear()
        first["workflows"][0]["entrypoints"].clear()
        first["native_records"][0]["analyses"].clear()
        first["component_mappings"]["components"][0]["pin_signature"].clear()
        self.assertEqual(build_engineering_capabilities(target_version="14.3"), expected)
        json.dumps(expected, allow_nan=False)

    def test_positive_prerequisites_never_certify_execution(self) -> None:
        payload = engineering_runtime_readiness({"runtime_compatible": True, "schematic_templates_ready": True})
        self.assertEqual(payload["status"], "unverified")
        self.assertFalse(payload["activation_checked"])
        self.assertFalse(payload["current_machine_native_verified"])
        self.assertIsNone(payload["detected_version"])
        self.assertEqual(engineering_runtime_readiness({})["status"], "unverified")
        self.assertEqual(engineering_runtime_readiness({"runtime_compatible": False})["status"], "blocked-runtime")

    def test_optional_missing_templates_do_not_disable_every_workflow(self) -> None:
        payload = engineering_runtime_readiness({"runtime_compatible": True, "schematic_templates_ready": False})
        self.assertEqual(payload["status"], "incomplete-template-pack")
        self.assertEqual(payload["blocked_by"], [])


class CapabilitiesCliTest(unittest.TestCase):
    def test_cli_reuses_shared_contract_without_backend_or_model_probe(self) -> None:
        stdout = io.StringIO()
        with (
            redirect_stdout(stdout),
            patch("multisim_mcp.cli.collect_doctor_report", side_effect=AssertionError("no runtime probe")),
            patch("multisim_mcp.com_worker_client.MultisimWorkerProcess._start_locked", side_effect=AssertionError("no worker")),
        ):
            code = main(["capabilities", "--profile", "experiment", "--target-version", "14.3", "--json"])
        self.assertEqual(code, 0)
        # Chinese family names must survive a cp936/UTF-8 shell pipe unchanged.
        self.assertTrue(stdout.getvalue().isascii())
        self.assertEqual(json.loads(stdout.getvalue()), build_capabilities(
            server_version=__version__, tool_profile=tool_profile_status("experiment"), target_version="14.3",
        ))

    def test_invalid_version_returns_a_structured_cli_error(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["--json", "capabilities", "--target-version", "banana"])
        self.assertEqual(code, 2)
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "invalid_input")


if __name__ == "__main__":
    unittest.main()
