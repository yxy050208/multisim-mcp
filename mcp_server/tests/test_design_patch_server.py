"""MCP adapters for approval-gated DesignPatch persistence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import multisim_mcp.server as server
from multisim_mcp.experiment_service import ExperimentApplicationService
from multisim_mcp.workspace_manifest import write_directory_manifest


def _documents(root: Path) -> tuple[Path, Path, Path]:
    design = root / "design.json"
    patch_file = root / "patch.json"
    plan = root / "verification-plan.json"
    design.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "design_id": "mcp-filter",
                "title": "MCP RC filter",
                "revision": 2,
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
                ],
                "parameters": {},
                "annotations": {},
                "source_netlist": "V1 in 0 1\nR1 in out 1030\n.end\n",
            }
        ),
        encoding="utf-8",
    )
    patch_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "patch_id": "mcp-e24-r1",
                "design_id": "mcp-filter",
                "base_revision": 2,
                "description": "Use a stocked E24 resistor",
                "operations": [
                    {
                        "operation": "set_component_value",
                        "target": "R1.value",
                        "before": "1030",
                        "after": "1k",
                        "reason": "stocked value",
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
                "title": "MCP candidate verification",
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
        output = Path(str(kwargs["output_dir"]))
        output.mkdir()
        verification = {
            "schema_version": 1,
            "overall_status": verdict,
            "counts": {
                "pass": int(verdict == "pass"),
                "fail": int(verdict == "fail"),
                "unverified": int(verdict == "unverified"),
            },
            "requirements": [{"id": "vout", "status": verdict}],
        }
        verification_path = output / "verification.json"
        verification_path.write_text(json.dumps(verification), encoding="utf-8")
        write_directory_manifest(
            output,
            directory_kind="experiment",
            entity_id="mcp-experiment",
            state="succeeded",
            artifacts={"verification.json": "verification"},
        )
        return {
            "success": True,
            "experiment_id": "mcp-experiment",
            "resources": {},
            "schematic": {"success": True},
            "simulation": {"success": True},
            "report": str(output / "report.md"),
            "plot": str(output / "plot.svg"),
            "output_dir": str(output),
            "verification": verification,
            "verification_path": str(verification_path),
        }

    return ExperimentApplicationService(runner)


class DesignPatchServerTest(unittest.TestCase):
    def test_basic_mcp_flow_uses_file_backed_token_and_separate_revert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, _ = _documents(root)
            store = root / "approvals"
            apply_receipt = root / "apply-receipt.json"
            apply_token = root / "apply.token"
            approved = server.approve_design_patch(
                str(design),
                str(apply_token),
                str(apply_receipt),
                patch_path=str(patch_file),
                in_place=True,
                regenerate_source_netlist=True,
                approval_store=str(store),
            )
            bearer = apply_token.read_text(encoding="ascii").strip()
            self.assertNotIn("approval_token", approved)
            self.assertNotIn(bearer, json.dumps(approved))
            self.assertFalse(approved["approval_token_exposed"])
            self.assertFalse(approved["approval_consumed"])
            self.assertFalse(approved["design_written"])
            self.assertEqual(approved["receipt"], str(apply_receipt.resolve()))

            applied = server.apply_design_patch(
                str(design),
                str(patch_file),
                str(apply_token),
                str(apply_receipt),
                in_place=True,
                regenerate_source_netlist=True,
                approval_store=str(store),
            )
            self.assertTrue(applied["design_written"])
            self.assertTrue(applied["commit_performed"])
            self.assertTrue(applied["approval_consumed"])
            self.assertNotIn(bearer, json.dumps(applied))
            self.assertEqual(json.loads(design.read_text())["revision"], 3)

            revert_receipt = root / "revert-receipt.json"
            revert_token = root / "revert.token"
            server.approve_design_patch(
                str(design),
                str(revert_token),
                str(revert_receipt),
                revert_transaction=str(apply_receipt),
                in_place=True,
                regenerate_source_netlist=True,
                approval_store=str(store),
            )
            reverted = server.revert_design_patch(
                str(design),
                str(apply_receipt),
                str(revert_token),
                str(revert_receipt),
                in_place=True,
                regenerate_source_netlist=True,
                approval_store=str(store),
            )
            self.assertTrue(reverted["design_written"])
            self.assertEqual(json.loads(design.read_text())["revision"], 4)
            self.assertEqual(json.loads(design.read_text())["components"][1]["value"], "1030")

    def test_verified_mcp_flow_commits_only_a_passing_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, plan = _documents(root)
            paths = {
                "receipt_path": root / "verified-receipt.json",
                "experiment_output": root / "experiment",
                "workflow_manifest": root / "workflow.json",
                "token_output": root / "verified.token",
            }
            approved = server.approve_verified_design_patch(
                str(design),
                str(patch_file),
                str(paths["token_output"]),
                str(paths["receipt_path"]),
                str(plan),
                str(paths["experiment_output"]),
                str(paths["workflow_manifest"]),
                in_place=True,
                regenerate_source_netlist=True,
                approval_store=str(root / "approvals"),
            )
            self.assertFalse(approved["approval_token_exposed"])
            self.assertFalse(approved["approval_consumed"])
            self.assertEqual(approved["receipt"], str(paths["receipt_path"].resolve()))
            self.assertEqual(
                approved["workflow_manifest"],
                str(paths["workflow_manifest"].resolve()),
            )
            bearer = paths["token_output"].read_text(encoding="ascii").strip()
            with patch.object(server, "_experiment_application_service", return_value=_experiment_service("pass")):
                result = server.apply_verified_design_patch(
                    str(design),
                    str(patch_file),
                    str(paths["token_output"]),
                    str(paths["receipt_path"]),
                    str(plan),
                    str(paths["experiment_output"]),
                    str(paths["workflow_manifest"]),
                    in_place=True,
                    regenerate_source_netlist=True,
                    approval_store=str(root / "approvals"),
                )
            self.assertTrue(result["design_written"])
            self.assertTrue(result["simulation_performed"])
            self.assertTrue(result["commit_performed"])
            self.assertEqual(result["state"], "committed")
            self.assertNotIn(bearer, json.dumps(result))

    def test_verified_mcp_failure_preserves_design_and_recovery_is_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, plan = _documents(root)
            original = design.read_bytes()
            token = root / "verified.token"
            receipt = root / "receipt.json"
            experiment = root / "experiment"
            manifest = root / "workflow.json"
            server.approve_verified_design_patch(
                str(design), str(patch_file), str(token), str(receipt), str(plan),
                str(experiment), str(manifest), in_place=True,
                regenerate_source_netlist=True, approval_store=str(root / "approvals"),
            )
            with patch.object(server, "_experiment_application_service", return_value=_experiment_service("fail")):
                result = server.apply_verified_design_patch(
                    str(design), str(patch_file), str(token), str(receipt), str(plan),
                    str(experiment), str(manifest), in_place=True,
                    regenerate_source_netlist=True, approval_store=str(root / "approvals"),
                )
            self.assertFalse(result["design_written"])
            self.assertTrue(result["failure_candidate_preserved"])
            self.assertEqual(result["state"], "rejected")
            self.assertEqual(design.read_bytes(), original)
            recovered = server.recover_verified_design_patch(str(manifest))
            self.assertEqual(recovered["action"], "confirmed-no-commit")
            self.assertFalse(recovered["design_written"])


if __name__ == "__main__":
    unittest.main()
