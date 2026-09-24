"""CLI contract tests for compare-designs."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from multisim_mcp import cli


class DesignComparisonCliTest(unittest.TestCase):
    def _arguments(self) -> list[str]:
        return [
            "compare-designs",
            "--variant",
            "first=first.json",
            "--variant",
            "second=second.json",
            "--spec",
            "comparison.json",
            "--output",
            "results",
        ]

    def test_success_prints_status_output_and_selected_variant(self) -> None:
        result = {
            "success": True,
            "status": "ranked",
            "output_dir": "C:/results",
            "selected_variant": {"variant_id": "second"},
        }
        stdout = io.StringIO()
        with patch.object(
            cli, "_run_compare_designs", return_value=result
        ), redirect_stdout(stdout):
            code = cli.main(self._arguments())
        self.assertEqual(code, 0)
        self.assertEqual(
            stdout.getvalue().splitlines(), ["ranked", "C:/results", "second"]
        )

    def test_no_feasible_variant_is_not_a_command_error(self) -> None:
        result = {
            "success": False,
            "status": "no_feasible_variant",
            "output_dir": "C:/results",
            "selected_variant": None,
        }
        stdout = io.StringIO()
        with patch.object(
            cli, "_run_compare_designs", return_value=result
        ), redirect_stdout(stdout):
            code = cli.main(self._arguments())
        self.assertEqual(code, 1)
        self.assertIn("no_feasible_variant", stdout.getvalue())

    def test_validation_error_uses_stable_json_envelope(self) -> None:
        stdout = io.StringIO()
        with patch.object(
            cli, "_run_compare_designs", side_effect=ValueError("bad variants")
        ), redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            code = cli.main([*self._arguments(), "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["command"], "compare-designs")
        self.assertEqual(payload["error"]["type"], "ValueError")


if __name__ == "__main__":
    unittest.main()
