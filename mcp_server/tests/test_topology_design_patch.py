from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.design_patch_service import create_design_diff_patch, prepare_design_patch
from multisim_mcp.design_patch_transactions import (
    apply_patch_transaction,
    approve_patch_apply,
    approve_patch_revert,
    read_design_document,
    revert_patch_transaction,
)
from multisim_mcp.eda_core import CircuitComponent, CircuitDesign


def _design() -> CircuitDesign:
    return CircuitDesign(
        design_id="topology-v1",
        title="Topology patch fixture",
        revision=7,
        components=(
            CircuitComponent("V1", "V", ("in", "0"), value="5"),
            CircuitComponent("R1", "R", ("in", "out"), value="1k"),
            CircuitComponent("R2", "R", ("out", "0"), value="1k"),
        ),
        source_netlist="V1 in 0 5\nR1 in out 1k\nR2 out 0 1k\n.end\n",
    )


def _patch(design: CircuitDesign) -> dict[str, object]:
    r2 = design.components[2].to_dict()
    return {
        "schema_version": 1,
        "patch_id": "topology-repair",
        "design_id": design.design_id,
        "base_revision": design.revision,
        "description": "Rewire the divider and add a measurement load",
        "operations": [
            {
                "operation": "add_net",
                "target": "sense",
                "before": None,
                "after": "sense",
                "reason": "Create a dedicated sensing node",
            },
            {
                "operation": "set_component_nodes",
                "target": "R2.nodes",
                "before": ["out", "0"],
                "after": ["sense", "0"],
                "reason": "Move R2 to the sensing branch",
            },
            {
                "operation": "add_component",
                "target": "R3",
                "before": None,
                "after": {
                    "refdes": "R3",
                    "kind": "R",
                    "nodes": ["out", "sense"],
                    "value": "100",
                    "model": None,
                    "parameters": {},
                    "annotations": {},
                },
                "reason": "Connect the sensing branch",
            },
            {
                "operation": "replace_component",
                "target": "R1",
                "before": design.components[1].to_dict(),
                "after": {
                    **design.components[1].to_dict(),
                    "value": "2k",
                },
                "reason": "Use the reviewed divider value",
            },
        ],
        "metadata": {"scope": "topology"},
    }


class TopologyDesignPatchTest(unittest.TestCase):
    def test_topology_patch_uses_existing_approval_apply_and_revert_boundary(self) -> None:
        original = _design()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design_path = root / "design.json"
            patch_path = root / "patch.json"
            apply_receipt = root / "apply-receipt.json"
            revert_receipt = root / "revert-receipt.json"
            approvals = root / "approvals"
            design_path.write_text(
                json.dumps(original.to_dict()), encoding="utf-8"
            )
            patch_path.write_text(
                json.dumps(_patch(original)), encoding="utf-8"
            )
            approval = approve_patch_apply(
                str(design_path),
                str(patch_path),
                output_path=None,
                in_place=True,
                receipt_path=str(apply_receipt),
                regenerate_source_netlist=True,
                approval_store=str(approvals),
            )
            apply_patch_transaction(
                str(design_path),
                str(patch_path),
                output_path=None,
                in_place=True,
                receipt_path=str(apply_receipt),
                regenerate_source_netlist=True,
                approval_token=approval["approval_token"],
                approval_store=str(approvals),
            )
            _, applied = read_design_document(str(design_path))
            self.assertEqual(applied.revision, 8)
            self.assertIn("sense", applied.nets)
            self.assertIn("R3 out sense 100", applied.source_netlist or "")

            revert_approval = approve_patch_revert(
                str(design_path),
                str(apply_receipt),
                output_path=None,
                in_place=True,
                receipt_path=str(revert_receipt),
                regenerate_source_netlist=True,
                approval_store=str(approvals),
            )
            revert_patch_transaction(
                str(design_path),
                str(apply_receipt),
                output_path=None,
                in_place=True,
                receipt_path=str(revert_receipt),
                regenerate_source_netlist=True,
                approval_token=revert_approval["approval_token"],
                approval_store=str(approvals),
            )
            _, reverted = read_design_document(str(design_path))
            self.assertEqual(reverted.revision, 9)
            self.assertEqual(
                [item.to_dict() for item in reverted.components],
                [item.to_dict() for item in original.components],
            )
            self.assertEqual(reverted.nets, original.nets)

    def test_prepare_and_inverse_round_trip(self) -> None:
        design = _design()
        prepared = prepare_design_patch(
            design, _patch(design), regenerate_source_netlist=True
        )
        self.assertEqual(prepared.candidate.revision, 8)
        self.assertEqual(prepared.candidate.nets, ("in", "0", "out", "sense"))
        self.assertEqual(len(prepared.candidate.components), 4)
        self.assertEqual(prepared.candidate.components[1].value, "2k")
        self.assertEqual(prepared.candidate.components[2].nodes, ("sense", "0"))
        self.assertIn("R3 out sense 100", prepared.candidate.source_netlist or "")
        self.assertTrue(prepared.source_netlist_regenerated)

        reverted = prepare_design_patch(
            prepared.candidate,
            prepared.inverse_patch,
            regenerate_source_netlist=True,
        ).candidate
        reverted_payload = reverted.to_dict()
        original_payload = design.to_dict()
        reverted_payload["revision"] = original_payload["revision"]
        self.assertEqual(reverted_payload, original_payload)

    def test_rejects_unknown_nets_and_removing_used_nets(self) -> None:
        design = _design()
        patch = _patch(design)
        patch["operations"] = [
            {
                "operation": "set_component_nodes",
                "target": "R2.nodes",
                "before": ["out", "0"],
                "after": ["missing", "0"],
                "reason": "Invalid rewire",
            }
        ]
        with self.assertRaisesRegex(ValueError, "unknown nets"):
            prepare_design_patch(design, patch)

        patch = _patch(design)
        patch["operations"] = [
            {
                "operation": "remove_net",
                "target": "out",
                "before": "out",
                "after": None,
                "reason": "Invalid removal",
            }
        ]
        with self.assertRaisesRegex(ValueError, "still used"):
            prepare_design_patch(design, patch)

    def test_remove_component_then_unused_net(self) -> None:
        design = _design()
        patch = _patch(design)
        patch["operations"] = [
            {
                "operation": "remove_component",
                "target": "R2",
                "before": design.components[2].to_dict(),
                "after": None,
                "reason": "Remove the branch",
            },
        ]
        prepared = prepare_design_patch(design, patch, regenerate_source_netlist=True)
        self.assertEqual([item.refdes for item in prepared.candidate.components], ["V1", "R1"])

    def test_consolidates_multiple_rounds_into_one_original_revision_patch(self) -> None:
        design = _design()
        first = prepare_design_patch(
            design, _patch(design), regenerate_source_netlist=True
        ).candidate
        second_patch = {
            "schema_version": 1,
            "patch_id": "second-round",
            "design_id": first.design_id,
            "base_revision": first.revision,
            "description": "Remove the original divider branch",
            "operations": [
                {
                    "operation": "remove_component",
                    "target": "R1",
                    "before": first.components[1].to_dict(),
                    "after": None,
                    "reason": "Second autonomous repair round",
                }
            ],
            "metadata": {},
        }
        final = prepare_design_patch(
            first, second_patch, regenerate_source_netlist=True
        ).candidate
        consolidated = create_design_diff_patch(
            design, final, patch_id="consolidated-repair"
        )
        self.assertEqual(consolidated.base_revision, design.revision)
        reproduced = prepare_design_patch(
            design, consolidated, regenerate_source_netlist=True
        ).candidate
        expected = final.to_dict()
        actual = reproduced.to_dict()
        expected["revision"] = actual["revision"]
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
