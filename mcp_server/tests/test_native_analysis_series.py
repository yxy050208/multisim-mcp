import unittest

from multisim_mcp.native_analysis_series import (
    compare_native_series,
    extract_native_analysis_series,
    native_series_informative,
)


class NativeAnalysisSeriesTest(unittest.TestCase):
    def test_transient_comparison_checks_middle_samples(self):
        result = {
            "ready": True,
            "results": {
                "V(out)": {
                    "output": "V(out)",
                    "rows": [[0.0, 1.0, 2.0], [0.0, 1.0, 0.0]],
                    "n_points": 3,
                    "sampled_points": 3,
                }
            },
        }
        expected = extract_native_analysis_series(
            result, ["V(out)"], "tran", {"duration": 2.0}
        )["V(out)"]
        actual_payload = {
            **result,
            "results": {
                "V(out)": {
                    **result["results"]["V(out)"],
                    "rows": [[0.0, 1.0, 2.0], [0.0, 2.0, 0.0]],
                }
            },
        }
        actual = extract_native_analysis_series(
            actual_payload, ["V(out)"], "tran", {"duration": 2.0}
        )["V(out)"]
        self.assertEqual(expected["status"], "pass")
        self.assertEqual(actual["status"], "pass")
        comparison = compare_native_series(expected, actual, absolute_tolerance=1e-9)
        self.assertEqual(comparison["status"], "fail")
        self.assertEqual(comparison["worst_sample_index"], 1)

    def test_ac_comparison_checks_complex_phase(self):
        base = {
            "ready": True,
            "results": {
                "V(out)": {
                    "output": "V(out)",
                    "rows": [[10.0, 100.0], [1.0, 0.0], [0.0, 1.0]],
                    "n_points": 2,
                    "sampled_points": 2,
                }
            },
        }
        expected = extract_native_analysis_series(
            base, ["V(out)"], "ac", {"start_frequency": 10.0, "stop_frequency": 100.0}
        )["V(out)"]
        phase_shifted = extract_native_analysis_series(
            {
                **base,
                "results": {
                    "V(out)": {
                        **base["results"]["V(out)"],
                        "rows": [[10.0, 100.0], [0.0, 1.0], [1.0, 0.0]],
                    }
                },
            },
            ["V(out)"], "ac", {"start_frequency": 10.0, "stop_frequency": 100.0}
        )["V(out)"]
        self.assertEqual(expected["status"], "pass")
        self.assertEqual(phase_shifted["status"], "pass")
        self.assertEqual(
            compare_native_series(expected, phase_shifted, absolute_tolerance=1e-9)["status"],
            "fail",
        )

    def test_downsampled_and_zero_ac_series_are_not_verified(self):
        result = {
            "ready": True,
            "results": {
                "V(out)": {
                    "output": "V(out)",
                    "rows": [[0.0, 1.0], [0.0, 0.0]],
                    "n_points": 4,
                    "sampled_points": 2,
                }
            },
        }
        series = extract_native_analysis_series(
            result, ["V(out)"], "tran", {"duration": 1.0}
        )
        self.assertNotEqual(series["V(out)"]["status"], "pass")
        ac = {
            "status": "pass", "analysis": "ac", "axis": [10.0, 100.0],
            "real": [0.0, 0.0], "imaginary": [0.0, 0.0],
        }
        self.assertFalse(native_series_informative("ac", {"V(out)": ac}))


if __name__ == "__main__":
    unittest.main()
