"""End-to-end CLI tests for approval-gated DesignPatch transactions."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.cli import main
from multisim_mcp.experiment_service import ExperimentApplicationService
from multisim_mcp.workspace_manifest import write_directory_manifest


def _documents(root: Path) -> tuple[Path, Path]:
    design = root / "design.json"
    patch_file = root / "patch.json"
    design.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "design_id": "cli-filter",
                "title": "CLI filter",
                "revision": 2,
                "components": [
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
                "annotations": {},
                "source_netlist": "R1 in out 1030\nC1 out 0 10n\n.end\n",
            }
        ),
        encoding="utf-8",
    )
    patch_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "patch_id": "cli-patch-e24",
                "design_id": "cli-filter",
                "base_revision": 2,
                "description": "Use an E24 resistor",
                "operations": [
                    {
                        "operation": "set_component_value",
                        "target": "R1.value",
                        "before": "1030",
                        "after": "1k",
                        "reason": "available E24 value",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return design, patch_file


class PatchTransactionCliTest(unittest.TestCase):
    @staticmethod
    def _verified_service(verdict: str) -> ExperimentApplicationService:
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
                "requirements": [{"id": "r1", "status": verdict}],
            }
            verification_path = root / "verification.json"
            verification_path.write_text(
                json.dumps(verification, sort_keys=True), encoding="utf-8"
            )
            write_directory_manifest(
                root,
                directory_kind="experiment",
                entity_id="exp-cli-verified",
                state="succeeded",
                artifacts={"verification.json": "verification"},
            )
            return {
                "success": True,
                "experiment_id": "exp-cli-verified",
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

    def test_cli_approval_apply_and_revert_never_print_bearer_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file = _documents(root)
            store = root / "approvals"
            apply_token = root / "apply.token"
            apply_receipt = root / "apply-receipt.json"
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "patch-approve", "--design", str(design),
                        "--patch", str(patch_file), "--in-place",
                        "--receipt", str(apply_receipt),
                        "--regenerate-source-netlist",
                        "--approval-store", str(store),
                        "--token-output", str(apply_token), "--json",
                    ]
                )
            approved_output = output.getvalue()
            approved = json.loads(approved_output)
            self.assertEqual(exit_code, 0, approved_output)
            bearer = apply_token.read_text(encoding="ascii").strip()
            self.assertTrue(bearer.startswith("mspat_"))
            self.assertNotIn(bearer, approved_output)
            self.assertFalse(approved["approval_token_exposed"])

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "patch-apply", "--design", str(design),
                        "--patch", str(patch_file), "--in-place",
                        "--receipt", str(apply_receipt),
                        "--regenerate-source-netlist",
                        "--approval-store", str(store),
                        "--approval-token-file", str(apply_token), "--json",
                    ]
                )
            applied_output = output.getvalue()
            applied = json.loads(applied_output)
            self.assertEqual(exit_code, 0, applied_output)
            self.assertNotIn(bearer, applied_output)
            self.assertTrue(applied["approval_consumed"])
            self.assertEqual(json.loads(design.read_text())["revision"], 3)

            revert_token = root / "revert.token"
            revert_receipt = root / "revert-receipt.json"
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "patch-approve", "--design", str(design),
                        "--revert-transaction", str(apply_receipt), "--in-place",
                        "--receipt", str(revert_receipt),
                        "--regenerate-source-netlist",
                        "--approval-store", str(store),
                        "--token-output", str(revert_token), "--json",
                    ]
                )
            self.assertEqual(exit_code, 0, output.getvalue())
            revert_bearer = revert_token.read_text(encoding="ascii").strip()
            self.assertNotIn(revert_bearer, output.getvalue())

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "patch-revert", "--design", str(design),
                        "--transaction", str(apply_receipt), "--in-place",
                        "--receipt", str(revert_receipt),
                        "--regenerate-source-netlist",
                        "--approval-store", str(store),
                        "--approval-token-file", str(revert_token), "--json",
                    ]
                )
            reverted_output = output.getvalue()
            reverted = json.loads(reverted_output)
            restored = json.loads(design.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0, reverted_output)
            self.assertNotIn(revert_bearer, reverted_output)
            self.assertEqual(reverted["operation"], "revert")
            self.assertEqual(restored["revision"], 4)
            self.assertEqual(restored["components"][0]["value"], "1030")

    def test_token_output_cannot_alias_a_transaction_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file = _documents(root)
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "patch-approve", "--design", str(design),
                        "--patch", str(patch_file), "--in-place",
                        "--receipt", str(root / "receipt.json"),
                        "--token-output", str(design), "--json",
                    ]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertFalse(payload["success"])
            self.assertIn("distinct", payload["error"]["message"])

    def test_verified_patch_cli_approves_runs_and_commits_only_on_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file = _documents(root)
            plan = root / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "title": "CLI verified patch",
                        "commands": "op",
                        "requirements": [
                            {
                                "id": "r1",
                                "metric": "mean",
                                "signal": "V(out)",
                                "operator": "approximately",
                                "target": 1.0,
                                "tolerance_percent": 5.0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            store = root / "approvals"
            token = root / "verified.token"
            receipt = root / "verified-receipt.json"
            experiment = root / "verified-experiment"
            manifest = root / "verified-workflow.json"
            common = [
                "--design", str(design),
                "--patch", str(patch_file),
                "--in-place",
                "--receipt", str(receipt),
                "--regenerate-source-netlist",
                "--approval-store", str(store),
                "--verification-plan", str(plan),
                "--experiment-output", str(experiment),
                "--workflow-manifest", str(manifest),
            ]
            output = io.StringIO()
            with redirect_stdout(output):
                approved_code = main(
                    [
                        "patch-verify-approve",
                        *common,
                        "--token-output", str(token),
                        "--json",
                    ]
                )
            approved = json.loads(output.getvalue())
            bearer = token.read_text(encoding="ascii").strip()
            self.assertEqual(approved_code, 0, output.getvalue())
            self.assertNotIn(bearer, output.getvalue())
            self.assertEqual(approved["commit_policy"], "all-requirements-pass")

            output = io.StringIO()
            with patch(
                "multisim_mcp.cli._verified_patch_experiment_service",
                return_value=self._verified_service("pass"),
            ), redirect_stdout(output):
                applied_code = main(
                    [
                        "patch-verify-apply",
                        *common,
                        "--approval-token-file", str(token),
                        "--json",
                    ]
                )
            applied = json.loads(output.getvalue())
            current = json.loads(design.read_text(encoding="utf-8"))
            self.assertEqual(applied_code, 0, output.getvalue())
            self.assertTrue(applied["success"])
            self.assertEqual(applied["state"], "committed")
            self.assertEqual(current["revision"], 3)
            self.assertTrue(manifest.is_file())
            self.assertTrue(receipt.is_file())

            output = io.StringIO()
            with redirect_stdout(output):
                recovery_code = main(
                    [
                        "patch-verify-recover",
                        "--workflow-manifest", str(manifest),
                        "--json",
                    ]
                )
            recovery = json.loads(output.getvalue())
            self.assertEqual(recovery_code, 0, output.getvalue())
            self.assertEqual(recovery["action"], "finalized-committed")

    def test_patch_recover_cli_discovers_and_rolls_back_partial_journal(self) -> None:
        class SimulatedCrash(BaseException):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file = _documents(root)
            store = root / "approvals"
            token = root / "apply.token"
            receipt = root / "receipt.json"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "patch-approve", "--design", str(design),
                            "--patch", str(patch_file), "--in-place",
                            "--receipt", str(receipt),
                            "--regenerate-source-netlist",
                            "--approval-store", str(store),
                            "--token-output", str(token), "--json",
                        ]
                    ),
                    0,
                )

            def crash(point: str) -> None:
                if point == "target_published":
                    raise SimulatedCrash(point)

            with patch(
                "multisim_mcp.design_patch_transactions._patch_crash_point",
                side_effect=crash,
            ):
                with self.assertRaises(SimulatedCrash):
                    main(
                        [
                            "patch-apply", "--design", str(design),
                            "--patch", str(patch_file), "--in-place",
                            "--receipt", str(receipt),
                            "--regenerate-source-netlist",
                            "--approval-store", str(store),
                            "--approval-token-file", str(token), "--json",
                        ]
                    )
            output = io.StringIO()
            with (
                patch(
                    "multisim_mcp.design_patch_transactions._pid_alive",
                    return_value=False,
                ),
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "patch-recover", "--target", str(design),
                        "--action", "auto", "--approval-store", str(store),
                        "--json",
                    ]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0, output.getvalue())
            self.assertEqual(payload["action"], "rollback")
            self.assertFalse(receipt.exists())
            self.assertEqual(json.loads(design.read_text())["revision"], 2)
            self.assertFalse(list(root.glob(".*.multisim-patch-journal.json")))


if __name__ == "__main__":
    unittest.main()
