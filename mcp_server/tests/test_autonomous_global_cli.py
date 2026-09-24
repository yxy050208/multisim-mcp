from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from multisim_mcp import cli


class AutonomousGlobalCliTest(unittest.TestCase):
    def test_global_success_prints_pareto_recommendation(self) -> None:
        result = {
            "success": True,
            "status": "completed",
            "output_dir": "C:/global",
            "recommended_solution": {"patch_path": "C:/global/patches/candidate.json"},
        }
        stdout = io.StringIO()
        with patch.object(
            cli, "_run_global_optimize_design", return_value=result
        ), redirect_stdout(stdout):
            code = cli.main(
                [
                    "global-optimize-design",
                    "--design",
                    "design.json",
                    "--spec",
                    "global.json",
                    "--output",
                    "results",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            stdout.getvalue().splitlines(),
            ["completed", "C:/global", "C:/global/patches/candidate.json"],
        )

    def test_autonomous_success_prints_final_patch(self) -> None:
        result = {
            "success": True,
            "status": "corrected",
            "output_dir": "C:/correction",
            "final_patch_path": "C:/correction/final-candidate-patch.json",
        }
        stdout = io.StringIO()
        with patch.object(
            cli, "_run_autonomous_correct_design", return_value=result
        ), redirect_stdout(stdout):
            code = cli.main(
                [
                    "autonomous-correct-design",
                    "--design",
                    "design.json",
                    "--spec",
                    "correction.json",
                    "--output",
                    "results",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("final-candidate-patch.json", stdout.getvalue())

    def test_global_validation_error_uses_stable_json(self) -> None:
        stdout = io.StringIO()
        with patch.object(
            cli, "_run_global_optimize_design", side_effect=ValueError("bad domain")
        ), redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            code = cli.main(
                [
                    "global-optimize-design",
                    "--design",
                    "design.json",
                    "--spec",
                    "global.json",
                    "--output",
                    "results",
                    "--json",
                ]
            )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["command"], "global-optimize-design")
        self.assertEqual(payload["error"]["type"], "ValueError")


if __name__ == "__main__":
    unittest.main()
