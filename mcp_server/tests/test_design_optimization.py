"""COM-free tests for bounded, hard-constraint design optimization."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.design_optimization import (
    DesignOptimizationService,
    read_design_optimization,
    validate_optimization_spec,
)
from multisim_mcp.eda_core import CircuitDesign
from multisim_mcp.experiment_service import ExperimentApplicationService
from multisim_mcp.workspace_manifest import write_directory_manifest


def _design() -> CircuitDesign:
    return CircuitDesign.from_dict(
        {
            "schema_version": 1,
            "design_id": "divider-optimization",
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


def _spec(
    values: list[str],
    *,
    lower: float = 5.0,
    upper: float = 8.0,
    target: float = 6.6666666667,
    budget: int = 8,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "title": "Optimize divider output",
        "variables": [{"refdes": "R2", "values": values}],
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
            "target": target,
        },
        "max_experiments": budget,
    }


def _spice_number(value: str) -> float:
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))([A-Za-z]*)", value)
    assert match is not None
    suffixes = {"": 1.0, "k": 1e3, "meg": 1e6, "m": 1e-3}
    return float(match.group(1)) * suffixes[match.group(2).casefold()]


def _service(
    *,
    fail_value: str | None = None,
    omit_measurement: bool = False,
    constant_value: float | None = None,
    calls: list[str] | None = None,
) -> ExperimentApplicationService:
    sequence = 0

    def runner(**kwargs: object) -> dict[str, object]:
        nonlocal sequence
        sequence += 1
        netlist = str(kwargs["netlist"])
        match = re.search(r"(?mi)^R2\s+out\s+0\s+(\S+)\s*$", netlist)
        assert match is not None
        r2_text = match.group(1)
        if calls is not None:
            calls.append(r2_text)
        if fail_value is not None and r2_text.casefold() == fail_value.casefold():
            raise RuntimeError("injected candidate failure")
        r2 = _spice_number(r2_text)
        value = constant_value if constant_value is not None else 10.0 * r2 / (1000.0 + r2)
        requirement = list(kwargs["requirements"])[0]  # type: ignore[arg-type]
        passed = float(requirement["lower"]) <= value <= float(requirement["upper"])
        verdict = "pass" if passed else "fail"
        verification = {
            "schema_version": 1,
            "overall_status": verdict,
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
            ],
        }
        if omit_measurement:
            verification["requirements"][0].pop("measurement")
        root = Path(str(kwargs["output_dir"])).resolve()
        root.mkdir(parents=True)
        verification_path = root / "verification.json"
        verification_path.write_text(
            json.dumps(verification, sort_keys=True), encoding="utf-8"
        )
        experiment_id = f"exp-opt-{sequence:03d}"
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


class DesignOptimizationTest(unittest.TestCase):
    def test_selects_deterministic_feasible_candidate_without_mutating_source(self) -> None:
        design = _design()
        before = design.to_dict()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "optimization"
            result = DesignOptimizationService(_service()).run(
                design,
                _spec(["500", "2k", "4k"], budget=4),
                str(output),
            )
            stored = read_design_optimization(str(output))

            self.assertTrue(result["success"])
            self.assertEqual(result["status"], "optimized")
            self.assertEqual(result["best_solution"]["values"], {"R2": "2k"})
            self.assertTrue(result["best_solution"]["requires_approval_to_persist"])
            self.assertTrue(result["best_solution"]["regenerate_source_netlist"])
            self.assertTrue(
                Path(result["best_solution"]["verification_plan_path"]).is_file()
            )
            self.assertEqual(result["experiments_attempted"], 4)
            self.assertEqual(stored["best_evaluation_id"], "candidate-002")
            self.assertEqual(design.to_dict(), before)
            best_patch = json.loads(
                Path(result["best_solution"]["patch_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(best_patch["operations"][0]["after"], "2k")

    def test_baseline_can_be_the_best_solution_without_a_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = DesignOptimizationService(_service()).run(
                _design(),
                _spec(["2k", "4k"], target=5.0, budget=3),
                str(Path(tmp) / "optimization"),
            )
            self.assertEqual(result["status"], "baseline_best")
            self.assertEqual(result["best_solution"]["kind"], "baseline")
            self.assertIsNone(result["best_solution"]["patch_path"])
            self.assertFalse(result["best_solution"]["requires_approval_to_persist"])

    def test_no_hard_constraint_solution_is_an_explicit_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = DesignOptimizationService(_service()).run(
                _design(),
                _spec(["500", "2k", "4k"], lower=9.0, upper=10.0, budget=4),
                str(Path(tmp) / "optimization"),
            )
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "no_feasible_candidate")
            self.assertIsNone(result["best_solution"])
            self.assertEqual(result["feasible_solution_count"], 0)

    def test_budget_is_hard_and_stopping_reason_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = DesignOptimizationService(_service()).run(
                _design(),
                _spec(["500", "2k", "4k", "10k"], target=8.0, budget=3),
                str(Path(tmp) / "optimization"),
            )
            self.assertEqual(result["experiments_attempted"], 3)
            self.assertEqual(result["candidate_space_size"], 4)
            self.assertEqual(result["stop_reason"], "budget_exhausted")
            self.assertEqual(result["best_solution"]["values"], {"R2": "2k"})

    def test_one_candidate_error_is_audited_and_later_candidates_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "optimization"
            result = DesignOptimizationService(_service(fail_value="500")).run(
                _design(),
                _spec(["500", "2k"], budget=3),
                str(output),
            )
            stored = read_design_optimization(str(output))
            self.assertEqual(result["status"], "optimized")
            self.assertEqual(stored["evaluations"][1]["status"], "error")
            self.assertEqual(stored["evaluations"][2]["status"], "feasible")

    def test_tampered_artifact_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "optimization"
            DesignOptimizationService(_service()).run(
                _design(), _spec(["2k"], budget=2), str(output)
            )
            (output / "candidates.csv").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "mismatch"):
                read_design_optimization(str(output))

    def test_claimed_pass_without_measured_hard_constraint_is_not_feasible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "optimization"
            result = DesignOptimizationService(
                _service(omit_measurement=True)
            ).run(_design(), _spec(["2k"], budget=2), str(output))
            stored = read_design_optimization(str(output))
            self.assertEqual(result["status"], "no_feasible_candidate")
            self.assertTrue(
                all(item["status"] == "error" for item in stored["evaluations"])
            )
            self.assertIn(
                "measured evidence", stored["evaluations"][0]["error"]["message"]
            )

    def test_spec_rejects_unknown_targets_injection_and_invalid_objective(self) -> None:
        design = _design()
        with self.assertRaisesRegex(ValueError, "does not exist"):
            validate_optimization_spec(_spec(["2k"]) | {
                "variables": [{"refdes": "R99", "values": ["2k"]}]
            }, design)
        with self.assertRaisesRegex(ValueError, "scalar SPICE value"):
            validate_optimization_spec(_spec(["2k\n.end"]), design)
        with self.assertRaisesRegex(ValueError, "hard requirement"):
            validate_optimization_spec(
                _spec(["2k"]) | {
                    "objective": {
                        "requirement_id": "missing",
                        "goal": "minimize",
                    }
                },
                design,
            )

    def test_e_series_is_generated_before_any_experiment(self) -> None:
        normalized = validate_optimization_spec(
            _spec(["2k"]) | {
                "variables": [
                    {
                        "refdes": "R2",
                        "series": {
                            "name": "E12",
                            "minimum": "1k",
                            "maximum": "3.3k",
                        },
                    }
                ]
            },
            _design(),
        )
        self.assertEqual(
            normalized["variables"][0]["values"],
            ["1k", "1.2k", "1.5k", "1.8k", "2.2k", "2.7k", "3.3k"],
        )
        self.assertEqual(
            normalized["variables"][0]["value_source"]["kind"],
            "preferred_series",
        )

    def test_stock_and_cost_are_hard_constraints_with_audited_rejections(self) -> None:
        spec = _spec(["2k", "4k"], budget=3) | {
            "variables": [
                {
                    "refdes": "R2",
                    "values": ["2k", "4k"],
                    "inventory": [
                        {
                            "value": "1k",
                            "part_number": "R-1K",
                            "supplier": "demo",
                            "unit_cost": 0.02,
                            "stock": 0,
                        },
                        {
                            "value": "2k",
                            "part_number": "R-2K",
                            "supplier": "demo",
                            "unit_cost": 0.04,
                            "stock": 25,
                        },
                        {
                            "value": "4k",
                            "part_number": "R-4K",
                            "supplier": "demo",
                            "unit_cost": 0.10,
                            "stock": 25,
                        },
                    ],
                }
            ],
            "procurement": {
                "currency": "CNY",
                "require_in_stock": True,
                "max_total_unit_cost": 0.05,
                "prefer_lower_cost": True,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "optimization"
            result = DesignOptimizationService(_service()).run(
                _design(), spec, str(output)
            )
            stored = read_design_optimization(str(output))
            self.assertEqual(result["status"], "optimized")
            self.assertEqual(result["best_solution"]["values"], {"R2": "2k"})
            self.assertEqual(
                result["best_solution"]["procurement"]["total_unit_cost"], 0.04
            )
            self.assertEqual(
                result["best_solution"]["procurement"]["selections"][0]["part_number"],
                "R-2K",
            )
            self.assertEqual(result["procurement_rejected_count"], 2)
            self.assertEqual(stored["evaluations"][0]["status"], "procurement_fail")
            self.assertEqual(stored["evaluations"][2]["status"], "procurement_fail")
            header = (output / "candidates.csv").read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("part_numbers", header)

    def test_lower_cost_breaks_an_equal_measured_objective_tie(self) -> None:
        inventory = [
            {
                "value": value,
                "part_number": part,
                "unit_cost": cost,
                "stock": 10,
            }
            for value, part, cost in (
                ("1k", "R-1K", 0.5),
                ("2k", "R-2K", 0.2),
                ("4k", "R-4K", 0.1),
            )
        ]
        spec = _spec(["2k", "4k"], target=6.0, budget=3) | {
            "variables": [{"refdes": "R2", "values": ["2k", "4k"], "inventory": inventory}],
            "procurement": {
                "currency": "USD",
                "require_in_stock": True,
                "prefer_lower_cost": True,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            result = DesignOptimizationService(
                _service(constant_value=6.0)
            ).run(_design(), spec, str(Path(tmp) / "optimization"))
            self.assertEqual(result["best_solution"]["values"], {"R2": "4k"})
            self.assertEqual(
                result["best_solution"]["procurement"]["currency"], "USD"
            )

    def test_resume_reuses_completed_candidates_without_repeating_experiments(self) -> None:
        first_calls: list[str] = []
        second_calls: list[str] = []
        spec = _spec(["500", "2k", "4k"], budget=4)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "optimization"
            interrupted = DesignOptimizationService(
                _service(calls=first_calls)
            ).run(
                _design(),
                spec,
                str(output),
                cancel_requested=lambda: len(first_calls) >= 2,
            )
            self.assertEqual(interrupted["status"], "cancelled")
            self.assertEqual(first_calls, ["1k", "500"])

            resumed = DesignOptimizationService(
                _service(calls=second_calls)
            ).run(_design(), spec, str(output), resume=True)
            stored = read_design_optimization(str(output))

            self.assertEqual(second_calls, ["2k", "4k"])
            self.assertEqual(resumed["status"], "optimized")
            self.assertEqual(resumed["resume_count"], 1)
            self.assertEqual(resumed["experiment_attempt_count"], 4)
            self.assertEqual(stored["resume_count"], 1)

    def test_resume_reruns_only_uncommitted_candidate_in_new_attempt_directory(self) -> None:
        first_calls: list[str] = []
        second_calls: list[str] = []
        spec = _spec(["500", "2k"], budget=3)

        def interrupt_candidate(stage: str, _progress: int, message: str) -> None:
            if stage == "optimization_experiment" and "candidate-001" in message:
                raise InterruptedError("simulated worker loss")

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "optimization"
            with self.assertRaisesRegex(InterruptedError, "worker loss"):
                DesignOptimizationService(_service(calls=first_calls)).run(
                    _design(),
                    spec,
                    str(output),
                    checkpoint=interrupt_candidate,
                )
            checkpoint = json.loads(
                (output / "optimization.json").read_text(encoding="utf-8")
            )
            self.assertEqual(first_calls, ["1k"])
            self.assertEqual(checkpoint["evaluations"][1]["status"], "running")

            resumed = DesignOptimizationService(
                _service(calls=second_calls)
            ).run(_design(), spec, str(output), resume=True)
            stored = read_design_optimization(str(output))

            self.assertEqual(second_calls, ["500", "2k"])
            self.assertEqual(resumed["experiment_attempt_count"], 4)
            recovered = stored["evaluations"][1]
            self.assertEqual(recovered["attempt"], 2)
            self.assertEqual(
                recovered["experiment_output"],
                "experiments/candidate-001-attempt-002",
            )
            self.assertEqual(len(recovered["interrupted_attempts"]), 1)

    def test_resume_rejects_tampered_completed_evidence(self) -> None:
        spec = _spec(["500", "2k"], budget=3)

        def interrupt_candidate(stage: str, _progress: int, message: str) -> None:
            if stage == "optimization_experiment" and "candidate-001" in message:
                raise InterruptedError("simulated worker loss")

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "optimization"
            with self.assertRaises(InterruptedError):
                DesignOptimizationService(_service()).run(
                    _design(),
                    spec,
                    str(output),
                    checkpoint=interrupt_candidate,
                )
            verification = output / "experiments" / "baseline" / "verification.json"
            verification.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex((ValueError, RuntimeError), "mismatch|verification"):
                DesignOptimizationService(_service()).run(
                    _design(), spec, str(output), resume=True
                )

    def test_equivalent_values_and_malformed_procurement_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "equivalent duplicate"):
            validate_optimization_spec(_spec(["1k", "1000"]), _design())
        with self.assertRaisesRegex(ValueError, "unit_cost must be >= 0"):
            validate_optimization_spec(
                _spec(["2k"]) | {
                    "variables": [
                        {
                            "refdes": "R2",
                            "values": ["2k"],
                            "inventory": [
                                {
                                    "value": "2k",
                                    "part_number": "BAD",
                                    "unit_cost": -1,
                                    "stock": 1,
                                }
                            ],
                        }
                    ]
                },
                _design(),
            )


if __name__ == "__main__":
    unittest.main()
