"""COM-free coverage for data-backed virtual instruments."""

import math
import unittest

from multisim_mcp.virtual_instruments import bode_plotter, logic_analyzer, multimeter


class VirtualInstrumentTest(unittest.TestCase):
    def test_multimeter(self) -> None:
        parsed = {"columns": ["time", "V(out)", "V(ref)"], "rows": [[0, 1, .5], [1, 3, .5], [2, 5, .5]]}
        result = multimeter(parsed, "v(OUT)", "V(ref)")
        self.assertAlmostEqual(result["dc"], 2.5)
        self.assertAlmostEqual(result["peak_to_peak"], 4)
        self.assertAlmostEqual(result["rms"], math.sqrt((.25 + 6.25 + 20.25) / 3))

    def test_bode_cutoff(self) -> None:
        parsed = {
            "columns": ["frequency", "V(in)", "V(out)"],
            "rows": [[10, 1, 1], [100, 1, .70710678], [1000, 1, .1]],
        }
        result = bode_plotter(parsed, "V(in)", "V(out)")
        self.assertAlmostEqual(result["upper_cutoff_hz"], 100, delta=.01)
        self.assertFalse(result["phase_available"])

    def test_bode_uses_complex_transfer_phase(self) -> None:
        parsed = {
            "columns": ["frequency", "V(in)", "V(out)"],
            "rows": [[100, 1, 1], [1000, 1, .70710678]],
            "real_rows": [[100, 1, 1], [1000, 1, .5]],
            "imaginary_rows": [[0, 0, 0], [0, 0, -.5]],
        }
        result = bode_plotter(parsed, "V(in)", "V(out)")
        self.assertTrue(result["phase_available"])
        self.assertAlmostEqual(result["points"][1]["phase_degrees"], -45)

    def test_logic_analyzer_reports_edges_and_compressed_events(self) -> None:
        parsed = {
            "columns": ["time", "V(a)", "V(b)"],
            "rows": [[0, 0, 5], [1, 0, 5], [2, 5, 5], [3, 5, 0], [4, 0, 0]],
        }
        result = logic_analyzer(parsed, ["V(a)", "V(b)"], threshold=2.5)
        self.assertEqual(len(result["events"]), 4)
        self.assertEqual(result["signals"][0]["rising_edges"], 1)
        self.assertEqual(result["signals"][0]["falling_edges"], 1)


if __name__ == "__main__":
    unittest.main()
