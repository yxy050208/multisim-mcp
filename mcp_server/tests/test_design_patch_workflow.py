"""COM-free tests for the approval-bound verified DesignPatch workflow."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.design_patch_transactions import PatchApprovalStore
from multisim_mcp.design_patch_workflow import (
    approve_verified_patch_application,
    execute_verified_patch_application,
    read_verified_patch_workflow,
    recover_verified_patch_workflow,
)
from multisim_mcp.experiment_service import ExperimentApplicationService
from multisim_mcp.workspace_manifest import write_directory_manifest


class SimulatedWorkflowCrash(BaseException):
    pass


def _documents(root: Path) -> tuple[Path, Path, Path]:
    design = root / "design.json"
    patch_file = root / "patch.json"
    plan = root / "verification-plan.json"
    design.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "design_id": "verified-filter",
                "title": "Verified RC filter",
                "revision": 4,
                "components": [
                    {
                        "refdes": "V1",
                        "kind": "V",
                        "nodes": ["in", "0"],
                        "value": "1",
                        "model": None,
                        "parameters": {},
                    },
                    {
                        "refdes": "R1",
                        "kind": "R",
                        "nodes": ["in", "out"],
                        "value": "1030",
                        "model": None,
                        "parameters": {},
                    },
                    {
                        "refdes": "C1",
                        "kind": "C",
                        "nodes": ["out", "0"],
                        "value": "10n",
                        "model": None,
                        "parameters": {},
                    },
                ],
                "parameters": {},
                "annotations": {},
                "source_netlist": (
                    "V1 in 0 1\nR1 in out 1030\nC1 out 0 10n\n.end\n"
                ),
            }
        ),
        encoding="utf-8",
    )
    patch_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "patch_id": "verified-e24-r1",
                "design_id": "verified-filter",
                "base_revision": 4,
                "description": "Move R1 to E24",
                "operations": [
                    {
                        "operation": "set_component_value",
                        "target": "R1.value",
                        "before": "1030",
                        "after": "1k",
                        "reason": "use a stocked E24 value",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    plan.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "title": "Candidate verification",
                "commands": "op",
                "requirements": [
                    {
                        "id": "vout",
                        "metric": "mean",
                        "signal": "V(out)",
                        "operator": "approximately",
                        "target": 0.5,
                        "tolerance_percent": 5.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return design, patch_file, plan


def _experiment_service(verdict: str) -> ExperimentApplicationService:
    def runner(**kwargs: object) -> dict[str, object]:
        root = Path(str(kwargs["output_dir"]))
        root.mkdir()
        counts = {
            "pass": 1 if verdict == "pass" else 0,
            "fail": 1 if verdict == "fail" else 0,
            "unverified": 1 if verdict == "unverified" else 0,
        }
        verification = {
            "schema_version": 1,
            "overall_status": verdict,
            "counts": counts,
            "requirements": [
                {
                    "id": "vout",
                    "metric": "mean",
                    "signal": "V(out)",
                    "status": verdict,
                }
            ],
        }
        verification_path = root / "verification.json"
        verification_path.write_text(
            json.dumps(verification, sort_keys=True), encoding="utf-8"
        )
        write_directory_manifest(
            root,
            directory_kind="experiment",
            entity_id="exp-workflow-test",
            state="succeeded",
            artifacts={"verification.json": "verification"},
        )
        return {
            "success": True,
            "experiment_id": "exp-workflow-test",
            "resources": {},
            "schematic": {"success": True},
            "simulation": {"success": True},
            "report": str(root / "report.md"),
            "plot": str(root / "plot.svg"),
            "output_dir": str(root),
            "verification": verification,
            "verification_path": str(verification_path),
        }

    return ExperimentApplicationService(runner)


def _workflow_paths(root: Path) -> dict[str, str]:
    return {
        "receipt_path": str(root / "apply-receipt.json"),
        "verification_plan_path": str(root / "verification-plan.json"),
        "experiment_output": str(root / "experiment"),
        "workflow_manifest": str(root / "workflow.json"),
    }


class VerifiedPatchWorkflowTest(unittest.TestCase):
    def _approve(
        self, root: Path, design: Path, patch_file: Path
    ) -> tuple[dict[str, object], dict[str, str]]:
        paths = _workflow_paths(root)
        approval = approve_verified_patch_application(
            str(design),
            str(patch_file),
            output_path=None,
            in_place=True,
            regenerate_source_netlist=True,
            approval_store=str(root / "approvals"),
            **paths,
        )
        return approval, paths

    def test_passing_candidate_is_committed_with_cross_linked_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, _ = _documents(root)
            approval, paths = self._approve(root, design, patch_file)
            result = execute_verified_patch_application(
                _experiment_service("pass"),
                str(design),
                str(patch_file),
                output_path=None,
                in_place=True,
                regenerate_source_netlist=True,
                approval_token=str(approval["approval_token"]),
                approval_store=str(root / "approvals"),
                **paths,
            )

            current = json.loads(design.read_text(encoding="utf-8"))
            _, manifest = read_verified_patch_workflow(paths["workflow_manifest"])
            self.assertTrue(result["success"])
            self.assertEqual(result["state"], "committed")
            self.assertEqual(current["revision"], 5)
            self.assertEqual(current["components"][1]["value"], "1k")
            self.assertEqual(manifest["state"], "committed")
            self.assertEqual(
                manifest["transaction"]["transaction_id"],
                result["transaction"]["transaction_id"],
            )
            self.assertTrue(Path(paths["receipt_path"]).is_file())

    def test_failed_or_unverified_candidate_never_changes_design(self) -> None:
        for verdict in ("fail", "unverified"):
            with self.subTest(verdict=verdict), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                design, patch_file, _ = _documents(root)
                original = design.read_bytes()
                approval, paths = self._approve(root, design, patch_file)
                result = execute_verified_patch_application(
                    _experiment_service(verdict),
                    str(design),
                    str(patch_file),
                    output_path=None,
                    in_place=True,
                    regenerate_source_netlist=True,
                    approval_token=str(approval["approval_token"]),
                    approval_store=str(root / "approvals"),
                    **paths,
                )

                _, manifest = read_verified_patch_workflow(paths["workflow_manifest"])
                record = PatchApprovalStore(root / "approvals")._read_record(
                    str(approval["approval_id"])
                )
                self.assertFalse(result["success"])
                self.assertEqual(result["state"], "rejected")
                self.assertEqual(design.read_bytes(), original)
                self.assertFalse(Path(paths["receipt_path"]).exists())
                self.assertEqual(manifest["state"], "rejected")
                self.assertEqual(record["status"], "approved")

    def test_approval_binds_verification_plan_before_simulation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, plan = _documents(root)
            approval, paths = self._approve(root, design, patch_file)
            changed = json.loads(plan.read_text(encoding="utf-8"))
            changed["requirements"][0]["tolerance_percent"] = 99.0
            plan.write_text(json.dumps(changed), encoding="utf-8")
            calls = 0

            def runner(**kwargs: object) -> dict[str, object]:
                nonlocal calls
                calls += 1
                return {}

            with self.assertRaisesRegex(ValueError, "authorization_context_digest"):
                execute_verified_patch_application(
                    ExperimentApplicationService(runner),
                    str(design),
                    str(patch_file),
                    output_path=None,
                    in_place=True,
                    regenerate_source_netlist=True,
                    approval_token=str(approval["approval_token"]),
                    approval_store=str(root / "approvals"),
                    **paths,
                )
            self.assertEqual(calls, 0)
            self.assertFalse(Path(paths["workflow_manifest"]).exists())

    def test_crash_before_commit_recovers_as_safe_abort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, _ = _documents(root)
            original = design.read_bytes()
            approval, paths = self._approve(root, design, patch_file)

            def crash(stage: str) -> None:
                if stage == "verification_recorded":
                    raise SimulatedWorkflowCrash(stage)

            with patch(
                "multisim_mcp.design_patch_workflow._workflow_crash_point",
                side_effect=crash,
            ):
                with self.assertRaises(SimulatedWorkflowCrash):
                    execute_verified_patch_application(
                        _experiment_service("pass"),
                        str(design),
                        str(patch_file),
                        output_path=None,
                        in_place=True,
                        regenerate_source_netlist=True,
                        approval_token=str(approval["approval_token"]),
                        approval_store=str(root / "approvals"),
                        **paths,
                    )
            recovered = recover_verified_patch_workflow(paths["workflow_manifest"])
            self.assertEqual(recovered["state"], "aborted")
            self.assertEqual(design.read_bytes(), original)
            self.assertFalse(Path(paths["receipt_path"]).exists())

    def test_crash_after_commit_is_finalized_from_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, _ = _documents(root)
            approval, paths = self._approve(root, design, patch_file)

            def crash(stage: str) -> None:
                if stage == "patch_committed":
                    raise SimulatedWorkflowCrash(stage)

            with patch(
                "multisim_mcp.design_patch_workflow._workflow_crash_point",
                side_effect=crash,
            ):
                with self.assertRaises(SimulatedWorkflowCrash):
                    execute_verified_patch_application(
                        _experiment_service("pass"),
                        str(design),
                        str(patch_file),
                        output_path=None,
                        in_place=True,
                        regenerate_source_netlist=True,
                        approval_token=str(approval["approval_token"]),
                        approval_store=str(root / "approvals"),
                        **paths,
                    )
            _, before = read_verified_patch_workflow(paths["workflow_manifest"])
            self.assertEqual(before["state"], "verification_passed")
            recovered = recover_verified_patch_workflow(paths["workflow_manifest"])
            _, after = read_verified_patch_workflow(paths["workflow_manifest"])
            self.assertEqual(recovered["action"], "finalized-committed")
            self.assertEqual(after["state"], "committed")
            self.assertTrue(Path(paths["receipt_path"]).is_file())

    def test_recovery_rejects_tampered_passing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, _ = _documents(root)
            approval, paths = self._approve(root, design, patch_file)

            def crash(stage: str) -> None:
                if stage == "patch_committed":
                    raise SimulatedWorkflowCrash(stage)

            with patch(
                "multisim_mcp.design_patch_workflow._workflow_crash_point",
                side_effect=crash,
            ):
                with self.assertRaises(SimulatedWorkflowCrash):
                    execute_verified_patch_application(
                        _experiment_service("pass"),
                        str(design),
                        str(patch_file),
                        output_path=None,
                        in_place=True,
                        regenerate_source_netlist=True,
                        approval_token=str(approval["approval_token"]),
                        approval_store=str(root / "approvals"),
                        **paths,
                    )
            (Path(paths["experiment_output"]) / "verification.json").write_text(
                '{"overall_status":"fail"}\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "verification_sha256"):
                recover_verified_patch_workflow(paths["workflow_manifest"])


if __name__ == "__main__":
    unittest.main()
