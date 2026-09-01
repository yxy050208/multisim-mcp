from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from multisim_mcp.backend_differential import compare_raw_results


def _raw(rows: list[tuple[float, float]], signal: str = "V(out)") -> str:
    values = []
    for index, (x, y) in enumerate(rows):
        values.extend((f"{index} {x}", f" {y}"))
    return "\n".join(
        [
            "Title: differential",
            "Plotname: Transient Analysis",
            "Flags: real",
            "No. Variables: 2",
            f"No. Points: {len(rows)}",
            "Variables:",
            "0 time time",
            f"1 {signal} voltage",
            "Values:",
            *values,
            "",
        ]
    )


class BackendDifferentialTest(unittest.TestCase):
    def test_interpolates_case_insensitive_common_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "reference.raw"
            candidate = Path(tmp) / "candidate.raw"
            reference.write_text(_raw([(0, 0), (1, 1), (2, 2)]), encoding="utf-8")
            candidate.write_text(_raw([(0, 0), (2, 2.01)], "v(OUT)"), encoding="utf-8")
            result = compare_raw_results(
                reference,
                candidate,
                absolute_tolerance=0.02,
                relative_tolerance_percent=0,
            )
        self.assertEqual(result["overall_status"], "pass")
        self.assertEqual(result["signals"][0]["point_count"], 3)
        self.assertAlmostEqual(result["signals"][0]["max_absolute_error"], 0.01)

    def test_reports_tolerance_failure_and_rejects_missing_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "reference.raw"
            candidate = Path(tmp) / "candidate.raw"
            reference.write_text(_raw([(0, 0), (1, 1)]), encoding="utf-8")
            candidate.write_text(_raw([(0, 0), (1, 2)]), encoding="utf-8")
            result = compare_raw_results(reference, candidate)
            with self.assertRaises(ValueError):
                compare_raw_results(reference, candidate, signals=["V(missing)"])
        self.assertEqual(result["overall_status"], "fail")
        self.assertEqual(result["signals"][0]["violation_count"], 1)


if __name__ == "__main__":
    unittest.main()
