import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.course_demo import (
    COURSE_BOM,
    COURSE_DEMO_ID,
    COURSE_WAVEFORM_CHANNELS,
    assess_course_component_evidence,
    behavioral_reference_commands,
    behavioral_reference_netlist,
    build_course_demo_manifest,
    build_course_demo_spec,
    load_course_experiment_evidence,
    validate_course_demo_manifest,
    write_course_demo_bundle,
)
from multisim_mcp.design_verification import validate_experiment_spec


class CourseDemoContractTest(unittest.TestCase):
    @staticmethod
    def _complete_component_evidence() -> dict[str, object]:
        digest = "a" * 64
        return {
            key: {
                "status": "verified",
                "implementation": "native-library",
                "model_identity": identity,
                "source": "NI Multisim local component database",
                "license": "Local licensed installation; model is not redistributed",
                "backend": "multisim",
                "artifact_sha256": digest,
            }
            for key, identity in {
                "he555": "HE555",
                "74ls74": "74LS74",
                "lm324": "LM324",
                "1n4007": "1N4007",
            }.items()
        }

    @staticmethod
    def _complete_experiment_evidence() -> dict[str, object]:
        return {
            "status": "verified",
            "backend_id": "multisim",
            "overall_status": "pass",
            "passed": 12,
            "failed": 0,
            "unverified": 0,
            "artifact_sha256": "b" * 64,
            "requirement_ids": [
                "square_i_frequency",
                "square_i_amplitude",
                "square_ii_frequency",
                "square_ii_amplitude",
                "triangle_frequency",
                "triangle_amplitude",
                "sine_i_frequency",
                "sine_i_amplitude",
                "sine_i_thd",
                "sine_ii_frequency",
                "sine_ii_amplitude",
                "sine_ii_thd",
            ],
        }

    def test_reference_fixture_has_five_loaded_channels_and_supply(self) -> None:
        netlist = behavioral_reference_netlist()
        self.assertIn("VDD vdd 0 10", netlist)
        self.assertEqual(netlist.count("RLOAD_"), 5)
        self.assertEqual(behavioral_reference_commands(), "tran 50n 400u")

    def test_spec_is_accepted_by_verified_experiment_schema(self) -> None:
        spec = build_course_demo_spec()
        normalized = validate_experiment_spec(spec)
        self.assertEqual(normalized["title"], "多种波形产生电路 / Multi-waveform generator")
        self.assertEqual(len(normalized["requirements"]), 12)
        self.assertEqual(len(normalized["theoretical_values"]), 12)
        self.assertEqual(
            {item["signal"] for item in normalized["requirements"]},
            {channel.signal for channel in COURSE_WAVEFORM_CHANNELS},
        )
        thresholds = {
            item["id"]: item["parameters"]["threshold"]
            for item in normalized["requirements"]
            if item["metric"] == "frequency"
        }
        self.assertEqual(thresholds["square_i_frequency"], 0.5)
        self.assertEqual(thresholds["triangle_frequency"], 0.0)

    def test_manifest_rejects_duplicate_or_missing_channel(self) -> None:
        manifest = build_course_demo_manifest()
        validate_course_demo_manifest(manifest)
        manifest["channels"] = manifest["channels"][:-1]
        with self.assertRaisesRegex(ValueError, "exactly five"):
            validate_course_demo_manifest(manifest)

    def test_bom_and_component_claim_gate_are_explicit(self) -> None:
        manifest = build_course_demo_manifest(netlist_kind="native-multisim")
        self.assertEqual(len(manifest["bom"]), len(COURSE_BOM))
        self.assertEqual(len(COURSE_BOM), 35)
        self.assertEqual(sum(item.quantity for item in COURSE_BOM), 55)
        self.assertFalse(manifest["evidence_scope"]["component_level_claim"])
        self.assertEqual(
            manifest["component_readiness"]["missing_models"],
            ["he555", "74ls74", "lm324", "1n4007"],
        )

    def test_native_claim_requires_models_and_exact_12_of_12_multisim_run(self) -> None:
        component_evidence = self._complete_component_evidence()
        experiment_evidence = self._complete_experiment_evidence()
        readiness = assess_course_component_evidence(
            component_evidence,
            experiment_evidence,
            netlist_kind="native-multisim",
        )
        self.assertTrue(readiness["claim_ready"])
        self.assertEqual(readiness["verified_model_count"], 4)
        manifest = build_course_demo_manifest(
            netlist_kind="native-multisim",
            component_evidence=component_evidence,
            experiment_evidence=experiment_evidence,
        )
        self.assertTrue(manifest["evidence_scope"]["component_level_claim"])
        validate_course_demo_manifest(manifest)

        experiment_evidence["unverified"] = 1
        experiment_evidence["passed"] = 11
        incomplete = build_course_demo_manifest(
            netlist_kind="native-multisim",
            component_evidence=component_evidence,
            experiment_evidence=experiment_evidence,
        )
        self.assertFalse(incomplete["evidence_scope"]["component_level_claim"])

    def test_model_identity_and_hash_are_not_optional(self) -> None:
        component_evidence = self._complete_component_evidence()
        component_evidence["lm324"]["artifact_sha256"] = "not-a-hash"  # type: ignore[index]
        readiness = assess_course_component_evidence(
            component_evidence,
            self._complete_experiment_evidence(),
            netlist_kind="native-multisim",
        )
        self.assertFalse(readiness["claim_ready"])
        lm324 = next(
            item for item in readiness["models"] if item["model_key"] == "lm324"
        )
        self.assertIn("artifact_sha256", " ".join(lm324["problems"]))

    def test_555_substitution_requires_an_explicit_compatibility_rationale(self) -> None:
        component_evidence = self._complete_component_evidence()
        component_evidence["he555"]["model_identity"] = "LM555"  # type: ignore[index]
        blocked = assess_course_component_evidence(
            component_evidence,
            self._complete_experiment_evidence(),
            netlist_kind="native-multisim",
        )
        self.assertFalse(blocked["claim_ready"])
        component_evidence["he555"]["substitution_justification"] = (  # type: ignore[index]
            "The course brief specifies a 555 timer family and the instructor approved LM555."
        )
        accepted = assess_course_component_evidence(
            component_evidence,
            self._complete_experiment_evidence(),
            netlist_kind="native-multisim",
        )
        self.assertTrue(accepted["claim_ready"])

    def test_completed_experiment_artifacts_are_loaded_with_integrity(self) -> None:
        requirements = [
            {"id": item["id"], "status": "pass"}
            for item in build_course_demo_spec()["requirements"]
        ]
        verification = {
            "overall_status": "pass",
            "counts": {"pass": 12, "fail": 0, "unverified": 0},
            "requirements": requirements,
        }
        verification_bytes = (
            json.dumps(verification, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        digest = hashlib.sha256(verification_bytes).hexdigest()
        manifest = {
            "experiment_id": "exp-course-test",
            "backend": {"backend_id": "multisim"},
            "artifacts": [
                {"filename": "verification.json", "sha256": digest}
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "verification.json").write_bytes(verification_bytes)
            (root / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            evidence = load_course_experiment_evidence(root)
        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["backend_id"], "multisim")
        self.assertEqual(evidence["passed"], 12)
        self.assertEqual(evidence["artifact_sha256"], digest)

    def test_bundle_is_deterministic_and_explicit_about_evidence_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "demo"
            result = write_course_demo_bundle(output)
            self.assertEqual(result["demo_id"], COURSE_DEMO_ID)
            self.assertEqual(result["channel_count"], 5)
            self.assertEqual(result["requirement_count"], 12)
            self.assertFalse(result["evidence_scope"]["component_level_claim"])
            spec = json.loads((output / "course-demo-spec.json").read_text(encoding="utf-8"))
            manifest = json.loads(
                (output / "course-demo-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(spec["schema_version"], 1)
            self.assertEqual(manifest["demo_id"], COURSE_DEMO_ID)
            self.assertTrue((output / "behavioral-reference.cir").is_file())
            self.assertTrue((output / "analysis-commands.txt").is_file())
            self.assertTrue((output / "course-bom.csv").is_file())
            self.assertTrue(
                (output / "course-component-evidence.template.json").is_file()
            )
            self.assertTrue((output / "course-experiment-evidence.json").is_file())
            self.assertTrue((output / "component-readiness.json").is_file())
            self.assertTrue((output / "native-implementation-plan.md").is_file())
            self.assertEqual(result["bom_row_count"], 35)
            self.assertEqual(result["bom_total_quantity"], 55)
            self.assertIn(
                "does not prove native IC",
                (output / "evidence-scope.md").read_text(encoding="utf-8"),
            )
            with self.assertRaises(FileExistsError):
                write_course_demo_bundle(output)


if __name__ == "__main__":
    unittest.main()
