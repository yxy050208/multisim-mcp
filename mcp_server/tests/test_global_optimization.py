from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.eda_core import CircuitDesign
from multisim_mcp.experiment_service import ExperimentApplicationService
from multisim_mcp.global_optimization import (
    GlobalDesignOptimizationService,
    read_global_optimization,
    validate_global_optimization_spec,
)
from multisim_mcp.workspace_manifest import write_directory_manifest


def _design() -> CircuitDesign:
    return CircuitDesign.from_dict(
        {
            "schema_version": 1,
            "design_id": "global-divider",
            "title": "Global divider fixture",
            "revision": 2,
            "components": [
                {
                    "refdes": "V1",
                    "kind": "V",
                    "nodes": ["in", "0"],
                    "value": "10",
                    "model": None,
                    "parameters": {},
                    "annotations": {},
                },
                {
                    "refdes": "R1",
                    "kind": "R",
                    "nodes": ["in", "out"],
                    "value": "1k",
                    "model": None,
                    "parameters": {},
                    "annotations": {},
                },
                {
                    "refdes": "R2",
                    "kind": "R",
                    "nodes": ["out", "0"],
                    "value": "1k",
                    "model": None,
                    "parameters": {},
                    "annotations": {},
                },
            ],
            "parameters": {},
            "annotations": {},
            "source_netlist": "V1 in 0 10\nR1 in out 1k\nR2 out 0 1k\n.end\n",
        }
    )


def _spec(*, strategy: str = "exhaustive", budget: int = 8) -> dict[str, object]:
    load = {
        "refdes": "R3",
        "kind": "R",
        "nodes": ["out", "0"],
        "value": "10k",
        "model": None,
        "parameters": {},
        "annotations": {},
    }
    return {
        "schema_version": 1,
        "title": "Global divider trade-off",
        "dimensions": [
            {
                "id": "divider_resistance",
                "kind": "component_value",
                "refdes": "R2",
                "values": ["1k", "2k", "4k"],
            },
            {
                "id": "output_load",
                "kind": "topology_choice",
                "include_baseline": True,
                "choices": [
                    {
                        "choice_id": "loaded",
                        "operations": [
                            {
                                "operation": "add_component",
                                "target": "R3",
                                "before": None,
                                "after": load,
                                "reason": "Explore the declared output load topology",
                            }
                        ],
                    }
                ],
            },
        ],
        "commands": "op",
        "requirements": [
            {
                "id": "vout",
                "metric": "mean",
                "signal": "V(out)",
                "operator": "between",
                "lower": 1.0,
                "upper": 9.0,
                "unit": "V",
            },
            {
                "id": "bandwidth",
                "metric": "mean",
                "signal": "V(bandwidth)",
                "operator": "between",
                "lower": 1.0,
                "upper": 2000.0,
                "unit": "Hz",
            },
        ],
        "objectives": [
            {"requirement_id": "vout", "goal": "maximize", "weight": 1},
            {"requirement_id": "bandwidth", "goal": "maximize", "weight": 1},
        ],
        "max_experiments": budget,
        "search_strategy": strategy,
        "selection_policy": "weighted_compromise",
    }


def _number(value: str) -> float:
    match = re.fullmatch(r"([0-9.]+)([A-Za-z]*)", value)
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
            raise InterruptedError("injected global worker interruption")
        netlist = str(kwargs["netlist"])
        r2_match = re.search(r"(?mi)^R2\s+out\s+0\s+(\S+)$", netlist)
        assert r2_match is not None
        r2 = _number(r2_match.group(1))
        r3_match = re.search(r"(?mi)^R3\s+out\s+0\s+(\S+)$", netlist)
        equivalent = r2
        if r3_match is not None:
            r3 = _number(r3_match.group(1))
            equivalent = 1.0 / (1.0 / r2 + 1.0 / r3)
        measurements = {
            "vout": 10.0 * equivalent / (1000.0 + equivalent),
            "bandwidth": 1_000_000.0 / equivalent,
        }
        requirements = list(kwargs["requirements"])  # type: ignore[arg-type]
        verification_items = []
        for requirement in requirements:
            value = measurements[str(requirement["id"])]
            passed = float(requirement["lower"]) <= value <= float(requirement["upper"])
            verification_items.append(
                {
                    "id": requirement["id"],
                    "metric": requirement["metric"],
                    "signal": requirement["signal"],
                    "status": "pass" if passed else "fail",
                    "measurement": {
                        "status": "measured",
                        "value": value,
                        "unit": requirement["unit"],
                    },
                    "criterion": {
                        "operator": "between",
                        "lower": requirement["lower"],
                        "upper": requirement["upper"],
                    },
                }
            )
        passed_count = sum(item["status"] == "pass" for item in verification_items)
        verification = {
            "schema_version": 1,
            "overall_status": "pass" if passed_count == len(verification_items) else "fail",
            "counts": {
                "pass": passed_count,
                "fail": len(verification_items) - passed_count,
                "unverified": 0,
            },
            "requirements": verification_items,
        }
        root = Path(str(kwargs["output_dir"])).resolve()
        root.mkdir(parents=True)
        verification_path = root / "verification.json"
        verification_path.write_text(json.dumps(verification), encoding="utf-8")
        experiment_id = f"global-exp-{sequence:03d}"
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


class GlobalOptimizationTest(unittest.TestCase):
    def test_resume_revalidates_completed_candidates_and_reruns_only_interrupted_work(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "global"
            first_calls: list[str] = []
            first = GlobalDesignOptimizationService(
                _service(interrupt_at=3, calls=first_calls)
            ).run(_design(), _spec(), str(output))
            self.assertEqual(first["status"], "cancelled")
            self.assertEqual(len(first_calls), 3)

            resumed_calls: list[str] = []
            resumed = GlobalDesignOptimizationService(
                _service(calls=resumed_calls)
            ).run(_design(), _spec(), str(output), resume=True)
            self.assertTrue(resumed["success"])
            self.assertEqual(resumed["resume_count"], 1)
            self.assertEqual(resumed["experiments_attempted"], 6)
            self.assertEqual(resumed["experiment_attempt_count"], 7)
            self.assertEqual(len(resumed_calls), 4)
            self.assertTrue(any("attempt-002" in item for item in resumed_calls))

    def test_resume_rejects_tampered_evidence_and_mismatched_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "global"
            GlobalDesignOptimizationService(_service()).run(
                _design(), _spec(), str(output)
            )
            with self.assertRaisesRegex(ValueError, "runtime"):
                GlobalDesignOptimizationService(_service()).run(
                    _design(),
                    _spec(),
                    str(output),
                    timeout_per_experiment=121,
                    resume=True,
                )
            verification = output / "experiments" / "baseline" / "verification.json"
            payload = json.loads(verification.read_text(encoding="utf-8"))
            payload["counts"]["pass"] = 999
            verification.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "evidence|verification|artifact"):
                GlobalDesignOptimizationService(_service()).run(
                    _design(), _spec(), str(output), resume=True
                )

    def test_exhaustive_mixed_topology_search_returns_pareto_evidence(self) -> None:
        design = _design()
        before = design.to_dict()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "global"
            result = GlobalDesignOptimizationService(_service()).run(
                design, _spec(), str(output)
            )
            stored = read_global_optimization(str(output))
            self.assertTrue(result["success"])
            self.assertEqual(result["search_strategy"], "exhaustive")
            self.assertEqual(result["candidate_space_size"], 5)
            self.assertEqual(result["experiments_attempted"], 6)
            self.assertGreaterEqual(result["pareto_solution_count"], 2)
            self.assertIsNotNone(result["recommended_solution"])
            self.assertTrue(
                result["recommended_solution"]["requires_approval_to_persist"]
                or result["recommended_solution"]["kind"] == "baseline"
            )
            self.assertEqual(stored["status"], "completed")
            self.assertEqual(design.to_dict(), before)
            self.assertTrue((output / "pareto-front.json").is_file())
            self.assertTrue((output / "global-candidates.csv").is_file())

    def test_auto_uses_halton_when_domain_exceeds_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = GlobalDesignOptimizationService(_service()).run(
                _design(), _spec(strategy="auto", budget=4), str(Path(tmp) / "global")
            )
            self.assertEqual(result["search_strategy"], "halton")
            self.assertEqual(result["experiments_attempted"], 4)
            self.assertEqual(result["stop_reason"], "budget_exhausted")

    def test_rejects_unbounded_contracts_and_unknown_targets(self) -> None:
        spec = _spec()
        spec["dimensions"][0]["refdes"] = "R99"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "does not exist"):
            validate_global_optimization_spec(spec, _design())
        spec = _spec()
        spec["max_experiments"] = 513
        with self.assertRaisesRegex(ValueError, "max_experiments"):
            validate_global_optimization_spec(spec, _design())


if __name__ == "__main__":
    unittest.main()
