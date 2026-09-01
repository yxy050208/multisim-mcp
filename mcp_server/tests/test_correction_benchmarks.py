"""COM-free tests for the standard correction benchmark suite."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from multisim_mcp.correction_benchmarks import (
    read_benchmark_suite,
    run_standard_benchmarks,
    standard_benchmark_catalog,
    validate_standard_benchmarks,
)
from multisim_mcp.global_optimization import GlobalDesignOptimizationService
from multisim_mcp.workspace_manifest import write_directory_manifest


class _SuccessfulBenchmarkService(GlobalDesignOptimizationService):
    def __init__(self) -> None:
        self.expected = {
            case.design.design_id: dict(case.expected_assignment)
            for case in standard_benchmark_catalog()
        }

    def run(self, design, spec, output_directory, **kwargs):  # type: ignore[no-untyped-def]
        del spec, kwargs
        root = Path(output_directory)
        root.mkdir(parents=True, exist_ok=False)
        (root / "result.json").write_text("{}\n", encoding="utf-8")
        write_directory_manifest(
            root,
            directory_kind="global-optimization",
            entity_id=design.design_id,
            state="succeeded",
            artifacts={"result.json": "result"},
        )
        return {
            "success": True,
            "status": "completed",
            "experiments_attempted": 3,
            "feasible_solution_count": 1,
            "recommended_solution": {
                "assignments": self.expected[design.design_id],
            },
        }


class CorrectionBenchmarkTest(unittest.TestCase):
    def test_catalog_spans_five_families_and_compiles_model_bound_designs(self) -> None:
        result = validate_standard_benchmarks()

        self.assertTrue(result["success"])
        self.assertFalse(result["simulation_performed"])
        self.assertEqual(result["case_count"], 5)
        self.assertEqual(
            {item["family"] for item in result["cases"]},
            {"rc", "rlc", "opamp", "bjt", "power"},
        )
        self.assertTrue(
            all(len(item["compiled_netlist_sha256"]) == 64 for item in result["cases"])
        )

    def test_case_selection_is_ordered_and_rejects_unknown_or_duplicate_ids(self) -> None:
        selected = validate_standard_benchmarks(["bjt-bias", "rc-lowpass"])
        self.assertEqual(
            [item["case_id"] for item in selected["cases"]],
            ["bjt-bias", "rc-lowpass"],
        )
        with self.assertRaisesRegex(ValueError, "unknown benchmark"):
            validate_standard_benchmarks(["not-a-case"])
        with self.assertRaisesRegex(ValueError, "duplicates"):
            validate_standard_benchmarks(["rc-lowpass", "rc-lowpass"])

    def test_runner_writes_verifiable_suite_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "suite"
            result = run_standard_benchmarks(
                _SuccessfulBenchmarkService(), str(output)
            )
            restored = read_benchmark_suite(str(output), verify=True)

        self.assertTrue(result["success"])
        self.assertEqual(result["passed_count"], 5)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(restored["status"], "passed")
        self.assertTrue(all(item["expected_assignment_selected"] for item in result["cases"]))
        manifest_paths = {
            item["path"] for item in result["manifest"]["artifacts"]
        }
        self.assertIn("rc-lowpass/directory.manifest.json", manifest_paths)


if __name__ == "__main__":
    unittest.main()
