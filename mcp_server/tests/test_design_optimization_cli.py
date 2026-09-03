"""CLI contract tests for the bounded optimize-design command."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from multisim_mcp import cli


class DesignOptimizationCliTest(unittest.TestCase):
    def _arguments(self) -> list[str]:
        return [
            "optimize-design",
            "--design",
            "design.json",
            "--spec",
            "optimization.json",
            "--output",
            "results",
        ]

    def test_success_prints_status_output_and_selected_patch(self) -> None:
        result = {
            "success": True,
            "status": "optimized",
            "output_dir": "C:/results",
            "best_solution": {"patch_path": "C:/results/best-patch.json"},
        }
        stdout = io.StringIO()
        with patch.object(cli, "_run_optimize_design", return_value=result), redirect_stdout(stdout):
            code = cli.main(self._arguments())
        self.assertEqual(code, 0)
        self.assertEqual(
            stdout.getvalue().splitlines(),
            ["optimized", "C:/results", "C:/results/best-patch.json"],
        )

    def test_no_feasible_solution_is_not_a_command_error(self) -> None:
        result = {
            "success": False,
            "status": "no_feasible_candidate",
            "output_dir": "C:/results",
            "best_solution": None,
        }
        stdout = io.StringIO()
        with patch.object(cli, "_run_optimize_design", return_value=result), redirect_stdout(stdout):
            code = cli.main(self._arguments())
        self.assertEqual(code, 1)
        self.assertIn("no_feasible_candidate", stdout.getvalue())

    def test_validation_error_uses_stable_json_envelope(self) -> None:
        stdout = io.StringIO()
        with patch.object(
            cli, "_run_optimize_design", side_effect=ValueError("bad spec")
        ), redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            code = cli.main([*self._arguments(), "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["command"], "optimize-design")
        self.assertEqual(payload["error"]["type"], "ValueError")
        self.assertEqual(payload["error"]["code"], "invalid_input")

    def test_resume_reads_validated_raw_spec_and_forwards_checkpoint_mode(self) -> None:
        args = cli.build_parser().parse_args([*self._arguments(), "--resume"])
        design = object()
        raw_spec = {"schema_version": 1, "variables": []}
        service = Mock()
        service.run.return_value = {"success": True, "status": "optimized"}
        with patch.object(
            cli, "read_design_document", return_value=(Path("design.json"), design)
        ), patch.object(
            cli,
            "read_optimization_spec",
            return_value=(Path("optimization.json"), raw_spec),
        ) as read_spec, patch.object(
            cli, "_verified_patch_experiment_service", return_value=object()
        ), patch.object(
            cli, "DesignOptimizationService", return_value=service
        ):
            cli._run_optimize_design(args)

        read_spec.assert_called_once_with("optimization.json", design, normalize=False)
        self.assertTrue(service.run.call_args.kwargs["resume"])


if __name__ == "__main__":
    unittest.main()
