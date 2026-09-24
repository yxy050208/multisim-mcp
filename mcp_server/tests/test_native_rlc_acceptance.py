import csv
import cmath
import math
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.native_rlc_acceptance import evaluate_rlc


class NativeRlcAcceptanceTest(unittest.TestCase):
    def test_evaluates_aligned_native_frequency_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "analysis-002"
            directory.mkdir(parents=True)
            r, l, c = 10.0, 10e-3, 2.5e-6
            rows = []
            for frequency in (500.0, 800.0, 1000.0, 1250.0, 1600.0):
                omega = 2 * math.pi * frequency
                gain = 1 / (1 - omega * omega * l * c + 1j * omega * r * c)
                rows.append({"frequency_hz": frequency, "V(OutProbe).real": 1.0, "V(OutProbe).imaginary": 0.0,
                             "V(OutProbe1).real": gain.real, "V(OutProbe1).imaginary": gain.imag})
            with (directory / "data.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader(); writer.writerows(rows)
            result = evaluate_rlc(directory.parent, r, l, c, target_hz=1000.0)
            self.assertTrue(result["checks"]["ac_complex_response"])
            self.assertTrue(result["passed"])

    def test_rejects_missing_frequency_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                evaluate_rlc(Path(tmp), 10, 1e-3, 1e-6, target_hz=1000)


if __name__ == "__main__":
    unittest.main()
