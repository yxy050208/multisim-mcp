"""COM-free tests for explicit, read-only design patch evaluation."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.design_patch_evaluation import (
    DesignPatchEvaluationService,
    read_design_patch_evaluation,
)
from multisim_mcp.eda_core import CircuitDesign
from multisim_mcp.experiment_service import ExperimentApplicationService
from multisim_mcp.workspace_manifest import write_directory_manifest


def _design() -> CircuitDesign:
    return CircuitDesign.from_dict(
        {
            "schema_version": 1,
            "design_id": "divider-patch-evaluation",
            "title": "10 V divider",
            "revision": 3,
            "components": [
                {
                    "refdes": "V1",
                    "kind": "V",
                    "nodes": ["in", "0"],
                    "value": "10",
                    "model": None,
                    "parameters": {},
                },
                {
                    "refdes": "R1",
                    "kind": "R",
                    "nodes": ["in", "out"],
                    "value": "1k",
                    "model": None,
                    "parameters": {},
                },
                {
                    "refdes": "R2",
                    "kind": "R",
                    "nodes": ["out", "0"],
                    "value": "1k",
                    "model": None,
                    "parameters": {},
                },
            ],
            "parameters": {},
            "annotations": {},
            "source_netlist": "V1 in 0 10\nR1 in out 1k\nR2 out 0 1k\n.end\n",
        }
    )


def _patch(after: str = "2k") -> dict[str, object]:
    return {
        "schema_version": 1,
        "patch_id": "set-r2-for-vout",
        "design_id": "divider-patch-evaluation",
        "base_revision": 3,
        "description": "Set the divider ratio for the required output",
        "operations": [
            {
                "operation": "set_component_value",
                "target": "R2.value",
                "before": "1k",
                "after": after,
                "reason": "Meet the verified V(out) interval",
            }
        ],
        "metadata": {},
    }


def _spec(lower: float = 6.5, upper: float = 6.8) -> dict[str, object]:
    return {
        "schema_version": 1,
        "title": "Verify divider repair",
        "commands": "op",
        "requirements": [
            {
                "id": "vout",
                "metric": "mean",
                "signal": "V(out)",
                "operator": "between",
                "lower": lower,
                "upper": upper,
                "unit": "V",
            }
        ],
    }


def _number(value: str) -> float:
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))([A-Za-z]*)", value)
    assert match is not None
    return float(match.group(1)) * {"": 1.0, "k": 1000.0}[match.group(2).lower()]


def _service(
    *, interrupt_at: int | None = None, calls: list[str] | None = None
) -> ExperimentApplicationService:
    sequence = 0

    def runner(**kwargs: object) -> dict[str, object]:
        nonlocal sequence
        sequence += 1
        if calls is not None:
            calls.append(str(kwargs["output_dir"]))
        if interrupt_at is not None and sequence == interrupt_at:
            raise InterruptedError("injected correction worker interruption")
        netlist = str(kwargs["netlist"])
        match = re.search(r"(?mi)^R2\s+out\s+0\s+(\S+)\s*$", netlist)
        assert match is not None
        r2 = _number(match.group(1))
        value = 10.0 * r2 / (1000.0 + r2)
        requirement = list(kwargs["requirements"])[0]  # type: ignore[arg-type]
        passed = float(requirement["lower"]) <= value <= float(requirement["upper"])
        status = "pass" if passed else "fail"
        verification = {
            "schema_version": 1,
            "overall_status": status,
            "counts": {
                "pass": int(passed),
                "fail": int(not passed),
                "unverified": 0,
            },
            "requirements": [
                {
                    "id": "vout",
                    "metric": "mean",
                    "signal": "V(out)",
                    "status": status,
                    "measurement": {
                        "id": "vout",
                        "metric": "mean",
                        "signal": "V(out)",
                        "status": "measured",
                        "value": value,
                        "unit": "V",
                        "reason": None,
                    },
                    "criterion": {
                        "operator": "between",
                        "lower": requirement["lower"],
                        "upper": requirement["upper"],
                    },
                    "comparison": None,
                    "reason": None,
                }
            ],
        }
        root = Path(str(kwargs["output_dir"])).resolve()
        root.mkdir(parents=True)
        (root / "circuit.cir").write_text(netlist, encoding="utf-8")
        verification_path = root / "verification.json"
        verification_path.write_text(
            json.dumps(verification, sort_keys=True), encoding="utf-8"
        )
        experiment_id = f"exp-patch-evaluation-{sequence}"
        write_directory_manifest(
            root,
            directory_kind="experiment",
            entity_id=experiment_id,
            state="succeeded",
            artifacts={
                "circuit.cir": "netlist",
                "verification.json": "verification",
            },
        )
        return {
            "success": True,
            "experiment_id": experiment_id,
            "resources": {},
            "schematic": {"success": True},
            "simulation": {"success": True},
            "report": str(root / "report.html"),
            "plot": str(root / "plot.svg"),
            "output_dir": str(root),
            "verification": verification,
            "verification_path": str(verification_path),
        }

    return ExperimentApplicationService(runner)


class DesignPatchEvaluationTest(unittest.TestCase):
    def test_candidate_passes_and_source_is_not_modified(self) -> None:
        design = _design()
        before = design.to_dict()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "evaluation"
            result = DesignPatchEvaluationService(_service()).run(
                design,
                _patch(),
                _spec(),
                str(output),
                regenerate_source_netlist=True,
            )
            stored = read_design_patch_evaluation(str(output))

            self.assertTrue(result["success"])
            self.assertEqual(result["status"], "candidate-improved-and-passed")
            self.assertTrue(result["adoption_eligible"])
            self.assertTrue(result["approval_required_before_apply"])
            self.assertFalse(result["source_design_modified"])
            self.assertFalse(result["candidate_persisted_as_source"])
            self.assertEqual(design.to_dict(), before)
            self.assertEqual(stored["comparison"]["baseline"]["status"], "fail")
            self.assertEqual(stored["comparison"]["candidate"]["status"], "feasible")
            self.assertEqual(stored["diagnosis_delta"]["resolved_finding_count"], 1)
            self.assertTrue(Path(result["inverse_patch"]).is_file())

    def test_authoritative_source_regeneration_must_be_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "evaluation"
            with self.assertRaisesRegex(ValueError, "source_netlist would be stale"):
                DesignPatchEvaluationService(_service()).run(
                    _design(), _patch(), _spec(), str(output)
                )
            self.assertFalse(output.exists())

    def test_candidate_failure_is_never_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = DesignPatchEvaluationService(_service()).run(
                _design(),
                _patch("4k"),
                _spec(),
                str(Path(tmp) / "evaluation"),
                regenerate_source_netlist=True,
            )
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "candidate-failed-requirements")
            self.assertFalse(result["adoption_eligible"])

    def test_non_electrical_patch_is_rejected_before_output(self) -> None:
        patch = {
            "schema_version": 1,
            "patch_id": "annotation-only",
            "design_id": "divider-patch-evaluation",
            "base_revision": 3,
            "description": "Change only a note",
            "operations": [
                {
                    "operation": "set_annotation",
                    "target": "review",
                    "before": None,
                    "after": "done",
                    "reason": "Record review",
                }
            ],
            "metadata": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "evaluation"
            with self.assertRaisesRegex(ValueError, "electrical design"):
                DesignPatchEvaluationService(_service()).run(
                    _design(), patch, _spec(), str(output)
                )
            self.assertFalse(output.exists())

    def test_recursive_manifest_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "evaluation"
            DesignPatchEvaluationService(_service()).run(
                _design(),
                _patch(),
                _spec(),
                str(output),
                regenerate_source_netlist=True,
            )
            (output / "candidate-design.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "mismatch|integrity"):
                read_design_patch_evaluation(str(output))


if __name__ == "__main__":
    unittest.main()
