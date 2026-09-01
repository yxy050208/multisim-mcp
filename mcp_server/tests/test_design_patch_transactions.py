"""COM-free tests for approval-gated DesignPatch persistence and rollback."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.design_patch_transactions import (
    PatchApprovalStore,
    apply_patch_transaction,
    approve_patch_apply,
    approve_patch_revert,
    find_patch_transaction_journals,
    read_design_document,
    read_transaction_receipt,
    recover_patch_transaction,
    revert_patch_transaction,
)
from multisim_mcp.eda_core import CircuitComponent, CircuitDesign, DesignPatch, PatchOperation


def _design() -> CircuitDesign:
    return CircuitDesign(
        design_id="filter-v1",
        title="RC filter",
        revision=3,
        components=(
            CircuitComponent("R1", "R", ("in", "out"), value="1030"),
            CircuitComponent("C1", "C", ("out", "0"), value="10n"),
        ),
        source_netlist="R1 in out 1030\nC1 out 0 10n\n.end\n",
    )


def _design_patch() -> DesignPatch:
    return DesignPatch(
        patch_id="patch-e24-r1",
        design_id="filter-v1",
        base_revision=3,
        description="Move R1 to an E24 value",
        operations=(
            PatchOperation(
                "set_component_value",
                "R1.value",
                "1030",
                "1k",
                "Use an available E24 value",
            ),
        ),
    )


def _write_inputs(root: Path) -> tuple[Path, Path, Path]:
    design = root / "design.json"
    patch_file = root / "patch.json"
    store = root / "approvals"
    design.write_text(json.dumps(_design().to_dict()), encoding="utf-8")
    patch_file.write_text(json.dumps(_design_patch().to_dict()), encoding="utf-8")
    return design, patch_file, store


class PatchTransactionTest(unittest.TestCase):
    def test_in_place_apply_and_separately_approved_revert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, store = _write_inputs(root)
            apply_receipt = root / "apply-receipt.json"
            approval = approve_patch_apply(
                str(design),
                str(patch_file),
                output_path=None,
                in_place=True,
                receipt_path=str(apply_receipt),
                regenerate_source_netlist=True,
                approval_store=str(store),
            )
            store_content = next(store.glob("approval-*.json")).read_text(
                encoding="utf-8"
            )
            self.assertNotIn(approval["approval_token"], store_content)
            self.assertFalse(approval["token_persisted"])

            applied = apply_patch_transaction(
                str(design),
                str(patch_file),
                output_path=None,
                in_place=True,
                receipt_path=str(apply_receipt),
                regenerate_source_netlist=True,
                approval_token=approval["approval_token"],
                approval_store=str(store),
            )
            _, candidate = read_design_document(str(design))
            self.assertEqual(candidate.revision, 4)
            self.assertEqual(candidate.components[0].value, "1k")
            self.assertIn("R1 in out 1k", candidate.source_netlist)
            self.assertTrue(applied["approval_consumed"])
            self.assertTrue(applied["source_netlist_regenerated"])
            self.assertFalse(applied["journal"]["retained"])
            self.assertFalse(Path(applied["journal"]["path"]).exists())

            with self.assertRaisesRegex(ValueError, "base_revision|consumed"):
                apply_patch_transaction(
                    str(design),
                    str(patch_file),
                    output_path=None,
                    in_place=True,
                    receipt_path=str(root / "replay.json"),
                    regenerate_source_netlist=True,
                    approval_token=approval["approval_token"],
                    approval_store=str(store),
                )

            revert_receipt = root / "revert-receipt.json"
            revert_approval = approve_patch_revert(
                str(design),
                str(apply_receipt),
                output_path=None,
                in_place=True,
                receipt_path=str(revert_receipt),
                regenerate_source_netlist=True,
                approval_store=str(store),
            )
            reverted = revert_patch_transaction(
                str(design),
                str(apply_receipt),
                output_path=None,
                in_place=True,
                receipt_path=str(revert_receipt),
                regenerate_source_netlist=True,
                approval_token=revert_approval["approval_token"],
                approval_store=str(store),
            )
            _, restored = read_design_document(str(design))
            self.assertEqual(restored.revision, 5)
            self.assertEqual(restored.components[0].value, "1030")
            self.assertIn("R1 in out 1030", restored.source_netlist)
            self.assertEqual(reverted["operation"], "revert")

    def test_output_mode_preserves_source_and_binds_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, store = _write_inputs(root)
            original = design.read_bytes()
            output = root / "candidate.json"
            wrong_output = root / "other-candidate.json"
            receipt = root / "receipt.json"
            approval = approve_patch_apply(
                str(design),
                str(patch_file),
                output_path=str(output),
                in_place=False,
                receipt_path=str(receipt),
                regenerate_source_netlist=True,
                approval_store=str(store),
            )
            with self.assertRaisesRegex(ValueError, "target_path_sha256"):
                apply_patch_transaction(
                    str(design),
                    str(patch_file),
                    output_path=str(wrong_output),
                    in_place=False,
                    receipt_path=str(receipt),
                    regenerate_source_netlist=True,
                    approval_token=approval["approval_token"],
                    approval_store=str(store),
                )
            result = apply_patch_transaction(
                str(design),
                str(patch_file),
                output_path=str(output),
                in_place=False,
                receipt_path=str(receipt),
                regenerate_source_netlist=True,
                approval_token=approval["approval_token"],
                approval_store=str(store),
            )
            self.assertEqual(design.read_bytes(), original)
            self.assertTrue(output.is_file())
            self.assertEqual(result["output"], str(output.resolve()))

    def test_source_regeneration_is_explicit_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, store = _write_inputs(root)
            with self.assertRaisesRegex(ValueError, "source netlist"):
                approve_patch_apply(
                    str(design),
                    str(patch_file),
                    output_path=None,
                    in_place=True,
                    receipt_path=str(root / "receipt.json"),
                    regenerate_source_netlist=False,
                    approval_store=str(store),
                )

    def test_receipt_publication_failure_restores_design_and_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, store = _write_inputs(root)
            receipt = root / "receipt.json"
            original = design.read_bytes()
            approval = approve_patch_apply(
                str(design),
                str(patch_file),
                output_path=None,
                in_place=True,
                receipt_path=str(receipt),
                regenerate_source_netlist=True,
                approval_store=str(store),
            )
            with patch(
                "multisim_mcp.design_patch_transactions.os.link",
                side_effect=OSError("injected receipt failure"),
            ):
                with self.assertRaisesRegex(OSError, "injected receipt failure"):
                    apply_patch_transaction(
                        str(design),
                        str(patch_file),
                        output_path=None,
                        in_place=True,
                        receipt_path=str(receipt),
                        regenerate_source_netlist=True,
                        approval_token=approval["approval_token"],
                        approval_store=str(store),
                    )
            self.assertEqual(design.read_bytes(), original)
            self.assertFalse(receipt.exists())
            self.assertFalse(list(root.glob(".*.tmp")))
            self.assertFalse(list(root.glob(".*.backup")))

            retried = apply_patch_transaction(
                str(design),
                str(patch_file),
                output_path=None,
                in_place=True,
                receipt_path=str(receipt),
                regenerate_source_netlist=True,
                approval_token=approval["approval_token"],
                approval_store=str(store),
            )
            self.assertTrue(retried["success"])

    def test_tampered_receipt_inverse_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, store = _write_inputs(root)
            receipt = root / "receipt.json"
            approval = approve_patch_apply(
                str(design),
                str(patch_file),
                output_path=None,
                in_place=True,
                receipt_path=str(receipt),
                regenerate_source_netlist=True,
                approval_store=str(store),
            )
            apply_patch_transaction(
                str(design),
                str(patch_file),
                output_path=None,
                in_place=True,
                receipt_path=str(receipt),
                regenerate_source_netlist=True,
                approval_token=approval["approval_token"],
                approval_store=str(store),
            )
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            payload["inverse_patch"]["operations"][0]["after"] = "999"
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inverse patch mismatch"):
                read_transaction_receipt(str(receipt))

    def test_expired_or_secret_tampered_approval_cannot_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, store = _write_inputs(root)
            receipt = root / "receipt.json"
            approval = approve_patch_apply(
                str(design),
                str(patch_file),
                output_path=None,
                in_place=True,
                receipt_path=str(receipt),
                regenerate_source_netlist=True,
                approval_store=str(store),
                ttl_seconds=60,
            )
            token = approval["approval_token"]
            tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
            with self.assertRaisesRegex(ValueError, "approval token is invalid"):
                PatchApprovalStore(store).claim(tampered, {})

            future = datetime.now(timezone.utc) + timedelta(minutes=2)
            with patch(
                "multisim_mcp.design_patch_transactions._utc_now",
                return_value=future,
            ):
                with self.assertRaisesRegex(ValueError, "expired"):
                    apply_patch_transaction(
                        str(design),
                        str(patch_file),
                        output_path=None,
                        in_place=True,
                        receipt_path=str(receipt),
                        regenerate_source_netlist=True,
                        approval_token=token,
                        approval_store=str(store),
                    )
            self.assertEqual(read_design_document(str(design))[1].revision, 3)
            self.assertFalse(receipt.exists())

    def test_store_rejects_invalid_token_and_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PatchApprovalStore(tmp)
            with self.assertRaisesRegex(ValueError, "ttl_seconds"):
                store.create({}, ttl_seconds=1)
            with self.assertRaisesRegex(ValueError, "approval token is invalid"):
                store.claim("not-a-token", {})

    def test_existing_target_lock_blocks_write_without_consuming_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, store = _write_inputs(root)
            receipt = root / "receipt.json"
            approval = approve_patch_apply(
                str(design),
                str(patch_file),
                output_path=None,
                in_place=True,
                receipt_path=str(receipt),
                regenerate_source_netlist=True,
                approval_store=str(store),
            )
            lock = root / f".{design.name}.multisim-patch.lock"
            lock.write_text("pid=fixture\n", encoding="ascii")
            with self.assertRaisesRegex(RuntimeError, "already locked"):
                apply_patch_transaction(
                    str(design),
                    str(patch_file),
                    output_path=None,
                    in_place=True,
                    receipt_path=str(receipt),
                    regenerate_source_netlist=True,
                    approval_token=approval["approval_token"],
                    approval_store=str(store),
                )
            self.assertEqual(read_design_document(str(design))[1].revision, 3)
            lock.unlink()
            result = apply_patch_transaction(
                str(design),
                str(patch_file),
                output_path=None,
                in_place=True,
                receipt_path=str(receipt),
                regenerate_source_netlist=True,
                approval_token=approval["approval_token"],
                approval_store=str(store),
            )
            self.assertTrue(result["approval_consumed"])

    def test_output_creation_race_preserves_unrelated_file_and_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, store = _write_inputs(root)
            output = root / "candidate.json"
            receipt = root / "receipt.json"
            approval = approve_patch_apply(
                str(design),
                str(patch_file),
                output_path=str(output),
                in_place=False,
                receipt_path=str(receipt),
                regenerate_source_netlist=True,
                approval_store=str(store),
            )
            real_link = os.link

            def race_link(source: object, destination: object) -> None:
                # Windows Python versions may resolve the user profile to an
                # 8.3 path while the test fixture keeps the long spelling.
                if Path(destination).resolve() == output.resolve():
                    output.write_text("unrelated", encoding="utf-8")
                real_link(source, destination)

            with patch(
                "multisim_mcp.design_patch_transactions.os.link",
                side_effect=race_link,
            ):
                with self.assertRaises(FileExistsError):
                    apply_patch_transaction(
                        str(design),
                        str(patch_file),
                        output_path=str(output),
                        in_place=False,
                        receipt_path=str(receipt),
                        regenerate_source_netlist=True,
                        approval_token=approval["approval_token"],
                        approval_store=str(store),
                    )
            self.assertEqual(output.read_text(encoding="utf-8"), "unrelated")
            self.assertFalse(receipt.exists())
            output.unlink()
            result = apply_patch_transaction(
                str(design),
                str(patch_file),
                output_path=str(output),
                in_place=False,
                receipt_path=str(receipt),
                regenerate_source_netlist=True,
                approval_token=approval["approval_token"],
                approval_store=str(store),
            )
            self.assertTrue(result["approval_consumed"])

    def test_auto_recovery_rolls_back_partial_and_commits_complete_states(self) -> None:
        class SimulatedCrash(BaseException):
            pass

        expectations = {
            "journal_created": "rollback",
            "prepared": "rollback",
            "target_published": "rollback",
            "receipt_published": "commit",
            "approval_consumed": "commit",
        }
        for crash_stage, recovery_action in expectations.items():
            with self.subTest(crash_stage=crash_stage):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    design, patch_file, store = _write_inputs(root)
                    receipt = root / "receipt.json"
                    approval = approve_patch_apply(
                        str(design),
                        str(patch_file),
                        output_path=None,
                        in_place=True,
                        receipt_path=str(receipt),
                        regenerate_source_netlist=True,
                        approval_store=str(store),
                    )

                    def crash(point: str) -> None:
                        if point == crash_stage:
                            raise SimulatedCrash(point)

                    with patch(
                        "multisim_mcp.design_patch_transactions._patch_crash_point",
                        side_effect=crash,
                    ):
                        with self.assertRaises(SimulatedCrash):
                            apply_patch_transaction(
                                str(design),
                                str(patch_file),
                                output_path=None,
                                in_place=True,
                                receipt_path=str(receipt),
                                regenerate_source_netlist=True,
                                approval_token=approval["approval_token"],
                                approval_store=str(store),
                            )
                    journals = find_patch_transaction_journals(str(design))
                    self.assertEqual(len(journals), 1)
                    with patch(
                        "multisim_mcp.design_patch_transactions._pid_alive",
                        return_value=False,
                    ):
                        recovered = recover_patch_transaction(
                            journal_path=journals[0], action="auto"
                        )
                    self.assertEqual(recovered["action"], recovery_action)
                    self.assertFalse(Path(journals[0]).exists())
                    _, current = read_design_document(str(design))
                    if recovery_action == "rollback":
                        self.assertEqual(current.revision, 3)
                        self.assertFalse(receipt.exists())
                        retried = apply_patch_transaction(
                            str(design),
                            str(patch_file),
                            output_path=None,
                            in_place=True,
                            receipt_path=str(receipt),
                            regenerate_source_netlist=True,
                            approval_token=approval["approval_token"],
                            approval_store=str(store),
                        )
                        self.assertTrue(retried["approval_consumed"])
                    else:
                        self.assertEqual(current.revision, 4)
                        self.assertTrue(receipt.is_file())
                        self.assertTrue(recovered["approval_consumed"])

    def test_explicit_recovery_can_resume_after_target_publication(self) -> None:
        class SimulatedCrash(BaseException):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, store = _write_inputs(root)
            receipt = root / "receipt.json"
            approval = approve_patch_apply(
                str(design),
                str(patch_file),
                output_path=None,
                in_place=True,
                receipt_path=str(receipt),
                regenerate_source_netlist=True,
                approval_store=str(store),
            )

            def crash(point: str) -> None:
                if point == "target_published":
                    raise SimulatedCrash(point)

            with patch(
                "multisim_mcp.design_patch_transactions._patch_crash_point",
                side_effect=crash,
            ):
                with self.assertRaises(SimulatedCrash):
                    apply_patch_transaction(
                        str(design),
                        str(patch_file),
                        output_path=None,
                        in_place=True,
                        receipt_path=str(receipt),
                        regenerate_source_netlist=True,
                        approval_token=approval["approval_token"],
                        approval_store=str(store),
                    )
            journal = find_patch_transaction_journals(str(design))[0]
            with self.assertRaisesRegex(RuntimeError, "still running"):
                recover_patch_transaction(journal_path=journal, action="commit")
            with patch(
                "multisim_mcp.design_patch_transactions._pid_alive",
                return_value=False,
            ):
                recovered = recover_patch_transaction(
                    target_path=str(design), action="commit"
                )
            self.assertEqual(recovered["action"], "commit")
            self.assertTrue(recovered["approval_consumed_during_recovery"])
            self.assertEqual(read_design_document(str(design))[1].revision, 4)
            self.assertTrue(receipt.is_file())

    def test_recovery_fails_closed_when_published_design_is_replaced(self) -> None:
        class SimulatedCrash(BaseException):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design, patch_file, store = _write_inputs(root)
            receipt = root / "receipt.json"
            approval = approve_patch_apply(
                str(design),
                str(patch_file),
                output_path=None,
                in_place=True,
                receipt_path=str(receipt),
                regenerate_source_netlist=True,
                approval_store=str(store),
            )

            def crash(point: str) -> None:
                if point == "target_published":
                    raise SimulatedCrash(point)

            with patch(
                "multisim_mcp.design_patch_transactions._patch_crash_point",
                side_effect=crash,
            ):
                with self.assertRaises(SimulatedCrash):
                    apply_patch_transaction(
                        str(design),
                        str(patch_file),
                        output_path=None,
                        in_place=True,
                        receipt_path=str(receipt),
                        regenerate_source_netlist=True,
                        approval_token=approval["approval_token"],
                        approval_store=str(store),
                    )
            journal = find_patch_transaction_journals(str(design))[0]
            design.write_text("not a CircuitDesign", encoding="utf-8")
            with patch(
                "multisim_mcp.design_patch_transactions._pid_alive",
                return_value=False,
            ):
                with self.assertRaisesRegex(RuntimeError, "no longer matches"):
                    recover_patch_transaction(journal_path=journal, action="auto")
            self.assertTrue(Path(journal).is_file())


if __name__ == "__main__":
    unittest.main()
