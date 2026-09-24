"""COM-free tests for evidence-backed complete-design comparison."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.design_comparison import (
    DesignVariantComparisonService,
    read_comparison_spec,
    read_design_comparison,
    validate_comparison_spec,
)
from multisim_mcp.eda_core import CircuitDesign
from multisim_mcp.experiment_service import ExperimentApplicationService
from multisim_mcp.workspace_manifest import write_directory_manifest


def _design(variant: str, r2: str) -> CircuitDesign:
    return CircuitDesign.from_dict(
        {
            "schema_version": 1,
            "design_id": f"divider-{variant}",
            "title": f"10 V divider {variant}",
            "revision": 1,
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
                    "value": r2,
                    "model": None,
                    "parameters": {},
                },
            ],
            "parameters": {},
            "annotations": {"variant": variant},
            "source_netlist": f"V1 in 0 10\nR1 in out 1k\nR2 out 0 {r2}\n.end\n",
        }
    )


def _variants() -> dict[str, CircuitDesign]:
    return {
        "balanced": _design("balanced", "1k"),
        "target": _design("target", "2k"),
        "high": _design("high", "4k"),
    }


def _spec(*, lower: float = 4.0, upper: float = 9.0) -> dict[str, object]:
    return {
        "schema_version": 1,
        "title": "Compare divider variants",
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
        "objective": {
            "requirement_id": "vout",
            "goal": "target",
            "target": 6.6666666667,
        },
    }


def _spice_number(value: str) -> float:
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))([A-Za-z]*)", value)
    assert match is not None
    return float(match.group(1)) * {"": 1.0, "k": 1e3}[match.group(2).casefold()]


def _service(
    *, fail_value: str | None = None, omit_measurement: bool = False
) -> ExperimentApplicationService:
    sequence = 0

    def runner(**kwargs: object) -> dict[str, object]:
        nonlocal sequence
        sequence += 1
        netlist = str(kwargs["netlist"])
        match = re.search(r"(?mi)^R2\s+out\s+0\s+(\S+)\s*$", netlist)
        assert match is not None
        r2_text = match.group(1)
        if fail_value is not None and r2_text.casefold() == fail_value.casefold():
            raise RuntimeError("injected variant failure")
        value = 10.0 * _spice_number(r2_text) / (1000.0 + _spice_number(r2_text))
        requirement = list(kwargs["requirements"])[0]  # type: ignore[arg-type]
        passed = float(requirement["lower"]) <= value <= float(requirement["upper"])
        verdict = "pass" if passed else "fail"
        requirement_result: dict[str, object] = {
            "id": "vout",
            "metric": "mean",
            "signal": "V(out)",
            "status": verdict,
            "measurement": {
                "id": "vout",
                "metric": "mean",
                "signal": "V(out)",
                "status": "measured",
                "value": value,
                "unit": "V",
                "reason": None,
                "details": {},
            },
            "criterion": {
                "operator": "between",
                "lower": requirement["lower"],
                "upper": requirement["upper"],
            },
            "comparison": None,
            "reason": None,
        }
        if omit_measurement:
            requirement_result.pop("measurement")
        verification = {
            "schema_version": 1,
            "overall_status": verdict,
            "counts": {
                "pass": int(passed),
                "fail": int(not passed),
                "unverified": 0,
            },
            "requirements": [requirement_result],
        }
        root = Path(str(kwargs["output_dir"])).resolve()
        root.mkdir(parents=True)
        verification_path = root / "verification.json"
        verification_path.write_text(
            json.dumps(verification, sort_keys=True), encoding="utf-8"
        )
        experiment_id = f"exp-compare-{sequence:03d}"
        write_directory_manifest(
            root,
            directory_kind="experiment",
            entity_id=experiment_id,
            state="succeeded",
            artifacts={"verification.json": "verification"},
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


class DesignComparisonTest(unittest.TestCase):
    def test_ranks_complete_designs_without_mutating_sources(self) -> None:
        variants = _variants()
        before = {name: design.to_dict() for name, design in variants.items()}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "comparison"
            result = DesignVariantComparisonService(_service()).run(
                variants, _spec(), str(output)
            )
            stored = read_design_comparison(str(output))

            self.assertTrue(result["success"])
            self.assertEqual(result["status"], "ranked")
            self.assertEqual(result["selected_variant"]["variant_id"], "target")
            self.assertTrue(result["selected_variant"]["requires_manual_adoption"])
            self.assertEqual(stored["ranked_feasible_variant_ids"][0], "target")
            self.assertEqual(result["experiments_attempted"], 3)
            self.assertEqual(result["feasible_variant_count"], 3)
            self.assertEqual(
                {name: design.to_dict() for name, design in variants.items()}, before
            )
            self.assertTrue(Path(result["data"]).is_file())
            self.assertTrue(Path(result["directory_manifest"]).is_file())

    def test_no_hard_constraint_pass_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = DesignVariantComparisonService(_service()).run(
                _variants(), _spec(lower=9.0, upper=10.0), str(Path(tmp) / "run")
            )
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "no_feasible_variant")
            self.assertIsNone(result["selected_variant"])

    def test_one_error_does_not_hide_later_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = DesignVariantComparisonService(_service(fail_value="1k")).run(
                _variants(), _spec(), str(Path(tmp) / "run")
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["status"], "ranked_with_errors")
            self.assertEqual(result["error_count"], 1)
            self.assertEqual(result["selected_variant"]["variant_id"], "target")

    def test_claimed_pass_without_measurement_is_never_ranked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = DesignVariantComparisonService(
                _service(omit_measurement=True)
            ).run(_variants(), _spec(), str(Path(tmp) / "run"))
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "no_feasible_variant")
            self.assertEqual(result["error_count"], 3)

    def test_duplicate_electrical_designs_are_rejected_before_output(self) -> None:
        variants = {
            "first": _design("first", "1k"),
            "copy": _design("copy", "1k"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "run"
            with self.assertRaisesRegex(ValueError, "electrically identical"):
                DesignVariantComparisonService(_service()).run(
                    variants, _spec(), str(output)
                )
            self.assertFalse(output.exists())

    def test_manifest_detects_recursive_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "run"
            DesignVariantComparisonService(_service()).run(
                _variants(), _spec(), str(output)
            )
            path = output / "variants" / "target.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["title"] = "tampered"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "mismatch|integrity"):
                read_design_comparison(str(output))

    def test_invalid_objective_and_variant_identifier_are_rejected(self) -> None:
        invalid = _spec()
        invalid["objective"] = {"requirement_id": "missing", "goal": "minimize"}
        with self.assertRaisesRegex(ValueError, "hard requirement"):
            validate_comparison_spec(invalid, _variants())
        with self.assertRaisesRegex(ValueError, "variant id"):
            validate_comparison_spec(
                _spec(), {"../first": _design("first", "1k"), "two": _design("two", "2k")}
            )

    def test_normal_cancellation_publishes_a_verifiable_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "run"
            result = DesignVariantComparisonService(_service()).run(
                _variants(), _spec(), str(output), cancel_requested=lambda: True
            )
            stored = read_design_comparison(str(output), verify=True)
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(result["experiments_attempted"], 0)
            self.assertEqual(stored["state"], "cancelled")

    def test_spec_reader_rejects_duplicate_keys_and_nonfinite_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            duplicate = root / "duplicate.json"
            duplicate.write_text(
                '{"schema_version":1,"schema_version":1}', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "duplicate"):
                read_comparison_spec(str(duplicate))
            nonfinite = root / "nonfinite.json"
            nonfinite.write_text('{"schema_version":1,"value":NaN}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-finite"):
                read_comparison_spec(str(nonfinite))


if __name__ == "__main__":
    unittest.main()
