from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from multisim_mcp import cli


class CorrectionBenchmarkCliTest(unittest.TestCase):
    def test_offline_validation_is_default_and_machine_readable(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.main(
                ["benchmark-suite", "--case", "rc-lowpass", "--json"]
            )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["mode"], "validate")
        self.assertEqual(payload["cases"][0]["case_id"], "rc-lowpass")

    def test_real_mode_requires_output(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            code = cli.main(["benchmark-suite", "--run-real", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertIn("requires --output", payload["error"]["message"])
        self.assertEqual(payload["error"]["code"], "invalid_input")
        self.assertFalse(payload["error"]["retryable"])

    def test_real_result_prints_case_status(self) -> None:
        result = {
            "success": True,
            "mode": "real-multisim",
            "status": "passed",
            "output_dir": "C:/benchmark",
            "cases": [
                {"case_id": "rc-lowpass", "passed": True, "status": "completed"}
            ],
        }
        stdout = io.StringIO()
        with patch.object(cli, "_run_benchmark_suite", return_value=result), redirect_stdout(
            stdout
        ):
            code = cli.main(
                ["benchmark-suite", "--run-real", "--output", "results"]
            )
        self.assertEqual(code, 0)
        self.assertIn("rc-lowpass\tpass\tcompleted", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
