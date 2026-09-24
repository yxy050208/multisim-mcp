"""Opt-in real Multisim gate for the five-family correction benchmark."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.correction_benchmarks import run_standard_benchmarks
from multisim_mcp.global_optimization import GlobalDesignOptimizationService


@unittest.skipUnless(
    os.environ.get("MULTISIM_MCP_RUN_REAL_TESTS") == "1",
    "set MULTISIM_MCP_RUN_REAL_TESTS=1 on a licensed Multisim workstation",
)
class RealCorrectionBenchmarkTest(unittest.TestCase):
    def test_standard_cross_family_suite(self) -> None:
        from multisim_mcp.server import _experiment_application_service

        with tempfile.TemporaryDirectory(prefix="multisim-mcp-real-benchmark-") as tmp:
            result = run_standard_benchmarks(
                GlobalDesignOptimizationService(_experiment_application_service()),
                str(Path(tmp) / "suite"),
                timeout_per_experiment=120.0,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["case_count"], 5)
        self.assertEqual(result["passed_count"], 5)


if __name__ == "__main__":
    unittest.main()
