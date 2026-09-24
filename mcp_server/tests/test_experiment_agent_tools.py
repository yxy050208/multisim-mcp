"""Tests for privacy-bounded completed-experiment model tools."""

from __future__ import annotations

import json
import threading
import unittest
from collections.abc import Mapping

from multisim_mcp.agent_runtime import ToolBinding
from multisim_mcp.experiment_agent_tools import (
    ReadOnlyExperimentEvidence,
    create_readonly_experiment_bindings,
)
from multisim_mcp.model_provider import ModelCancelled


def _summary() -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": "exp-0123456789abcdef01234567",
        "artifact_count": 2,
        "total_size": 321,
        "report_excerpt": "PRIVATE REPORT BODY",
        "report_truncated": False,
        "local_path": "C:/private/experiment",
        "measurements": {
            "available": True,
            "plotname": "Transient Analysis",
            "point_count": 1000,
            "column_count": 2,
            "columns": [
                {
                    "column": "time",
                    "count": 1000,
                    "first": 0.0,
                    "last": 0.001,
                    "min": 0.0,
                    "max": 0.001,
                    "mean": 0.0005,
                    "private": "ignore",
                },
                {
                    "column": "V(out)",
                    "count": 1000,
                    "first": 0.0,
                    "last": 1.0,
                    "min": -1.0,
                    "max": 1.0,
                    "mean": 0.0,
                },
            ],
            "columns_truncated": False,
        },
        "verification": {
            "available": True,
            "valid_json": True,
            "result": {
                "overall_status": "pass",
                "counts": {"pass": 1, "fail": 0, "unverified": 0},
                "requirement_count": 1,
                "requirements": [
                    {
                        "id": "frequency",
                        "metric": "frequency",
                        "signal": "V(out)",
                        "status": "pass",
                        "value": 20000.0,
                        "unit": "Hz",
                        "operator": "between",
                        "lower": 19000.0,
                        "upper": 21000.0,
                        "relative_error_percent": 0.0,
                        "nested": {"ignore": True},
                    }
                ],
                "requirements_truncated": False,
            },
        },
        "artifacts": [
            {
                "name": "data",
                "mime_type": "text/csv",
                "size": 123,
                "sha256": "a" * 64,
                "path": "C:/private/data.csv",
            },
            {
                "name": "raw",
                "mime_type": "application/octet-stream",
                "size": 198,
                "sha256": "b" * 64,
            },
        ],
    }


def _binding_map(summary: Mapping[str, object] | None = None) -> dict[str, ToolBinding]:
    return {
        binding.definition.name: binding
        for binding in create_readonly_experiment_bindings(summary or _summary())
    }


def _invoke(
    binding: ToolBinding,
    arguments: Mapping[str, object],
    cancel_event: threading.Event | None = None,
) -> object:
    validated = binding.validate_arguments(arguments)
    return binding.handler(validated, cancel_event)


class ReadOnlyExperimentEvidenceTest(unittest.TestCase):
    def test_factory_exposes_only_four_evidence_tools(self) -> None:
        bindings = _binding_map()
        self.assertEqual(
            set(bindings),
            {
                "eda_get_experiment_summary",
                "eda_list_measurement_columns",
                "eda_list_requirement_results",
                "eda_list_experiment_artifacts",
            },
        )
        self.assertTrue(
            all(
                binding.definition.parameters["additionalProperties"] is False
                for binding in bindings.values()
            )
        )

    def test_snapshot_never_exposes_report_paths_or_raw_samples(self) -> None:
        source = _summary()
        evidence = ReadOnlyExperimentEvidence(source)
        source["report_excerpt"] = "CHANGED PRIVATE BODY"
        bindings = {
            item.definition.name: item for item in evidence.bindings()
        }
        results = [
            _invoke(bindings["eda_get_experiment_summary"], {}),
            _invoke(bindings["eda_list_measurement_columns"], {}),
            _invoke(bindings["eda_list_requirement_results"], {}),
            _invoke(bindings["eda_list_experiment_artifacts"], {}),
        ]
        encoded = json.dumps(results)
        self.assertNotIn("PRIVATE REPORT BODY", encoded)
        self.assertNotIn("CHANGED PRIVATE BODY", encoded)
        self.assertNotIn("C:/private", encoded)
        self.assertNotIn("rows", encoded)
        self.assertTrue(all(result["read_only"] for result in results))
        self.assertTrue(all(not result["simulation_started"] for result in results))
        self.assertTrue(
            all(not result["design_association_verified"] for result in results)
        )

    def test_summary_and_pages_preserve_bounded_engineering_evidence(self) -> None:
        bindings = _binding_map()
        summary = _invoke(bindings["eda_get_experiment_summary"], {})
        self.assertEqual(summary["measurements"]["point_count"], 1000)
        self.assertEqual(summary["verification"]["overall_status"], "pass")

        columns = _invoke(
            bindings["eda_list_measurement_columns"], {"offset": 1, "limit": 1}
        )
        self.assertEqual(columns["columns"][0]["column"], "V(out)")
        self.assertEqual(columns["columns"][0]["max"], 1.0)
        self.assertNotIn("private", columns["columns"][0])

        requirements = _invoke(bindings["eda_list_requirement_results"], {})
        verdict = requirements["requirements"][0]
        self.assertEqual(verdict["status"], "pass")
        self.assertEqual(verdict["value"], 20000.0)
        self.assertEqual(verdict["lower"], 19000.0)
        self.assertNotIn("nested", verdict)

        artifacts = _invoke(bindings["eda_list_experiment_artifacts"], {})
        self.assertEqual(artifacts["artifacts"][0]["sha256"], "a" * 64)
        self.assertNotIn("path", artifacts["artifacts"][0])

    def test_argument_validation_snapshot_validation_and_cancellation(self) -> None:
        binding = _binding_map()["eda_list_measurement_columns"]
        with self.assertRaisesRegex(ValueError, "unknown arguments"):
            binding.validate_arguments({"path": "secret.raw"})
        with self.assertRaisesRegex(ValueError, "between 1 and 20"):
            binding.validate_arguments({"limit": 21})
        invalid = _summary()
        invalid["experiment_id"] = "../../secret"
        with self.assertRaisesRegex(ValueError, "experiment_id is invalid"):
            ReadOnlyExperimentEvidence(invalid)
        event = threading.Event()
        event.set()
        with self.assertRaises(ModelCancelled):
            _invoke(binding, {}, event)


if __name__ == "__main__":
    unittest.main()
