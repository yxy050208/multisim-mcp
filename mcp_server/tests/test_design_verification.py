"""COM-free tests for deterministic measurements and requirement verdicts."""

from __future__ import annotations

import math
import unittest

from multisim_mcp.design_verification import (
    measure_many,
    validate_experiment_spec,
    verify_requirements,
)


class DesignVerificationTest(unittest.TestCase):
    def test_scalar_gain_step_ripple_and_power_metrics(self) -> None:
        rows = []
        for index in range(101):
            time = index / 100.0
            source = math.sin(2 * math.pi * 5 * time)
            output = 2 * source
            step = 0.0 if index < 20 else 1.0 + (0.2 if index == 25 else 0.0)
            supply = 5.0 + 0.05 * math.sin(2 * math.pi * 10 * time)
            current = 0.01
            rows.append([time, source, output, step, supply, current])
        parsed = {
            "columns": ["time", "V(IN)", "V(out)", "V(step)", "VCC", "I(V1)"],
            "rows": rows,
        }
        measured = measure_many(
            parsed,
            [
                {"id": "gain", "metric": "gain", "signal": "v(OUT)", "reference_signal": "V(IN)"},
                {"id": "rms", "metric": "rms", "signal": "V(out)"},
                {"id": "rise", "metric": "rise_time", "signal": "V(step)"},
                {"id": "over", "metric": "overshoot", "signal": "V(step)"},
                {"id": "ripple", "metric": "ripple_percent", "signal": "VCC"},
                {"id": "power", "metric": "power", "signal": "VCC", "reference_signal": "I(V1)"},
                {"id": "at", "metric": "value_at", "signal": "V(step)", "parameters": {"at_x": 0.25}},
            ],
        )
        by_id = {item["id"]: item for item in measured}
        self.assertAlmostEqual(by_id["gain"]["value"], 2.0)
        self.assertGreater(by_id["rms"]["value"], 1.3)
        self.assertGreater(by_id["rise"]["value"], 0.0)
        self.assertAlmostEqual(by_id["over"]["value"], 20.0)
        self.assertGreater(by_id["ripple"]["value"], 1.0)
        self.assertAlmostEqual(by_id["power"]["value"], 0.05, places=3)
        self.assertAlmostEqual(by_id["at"]["value"], 1.2)

    def test_cutoff_and_bandwidth_use_interpolated_edges(self) -> None:
        frequencies = [1, 2, 4, 8, 16, 32, 64, 128]
        lowpass = [1 / math.sqrt(1 + (f / 16) ** 2) for f in frequencies]
        bandpass = [
            1 / math.sqrt((1 + (4 / f) ** 4) * (1 + (f / 32) ** 4))
            for f in frequencies
        ]
        parsed = {
            "columns": ["frequency", "V(lp)", "V(bp)"],
            "rows": [list(row) for row in zip(frequencies, lowpass, bandpass)],
        }
        measured = measure_many(
            parsed,
            [
                {"id": "fc", "metric": "cutoff_frequency", "signal": "V(lp)"},
                {"id": "bw", "metric": "bandwidth", "signal": "V(bp)"},
            ],
        )
        self.assertEqual(measured[0]["status"], "measured")
        self.assertGreater(measured[0]["value"], 8)
        self.assertLess(measured[0]["value"], 32)
        self.assertEqual(measured[1]["status"], "measured")
        self.assertGreater(measured[1]["value"], 0)

    def test_verdicts_are_pass_fail_or_unverified_without_guessing(self) -> None:
        parsed = {"columns": ["x", "V(out)"], "rows": [[0, 0], [1, 5], [2, 10]]}
        result = verify_requirements(
            parsed,
            [
                {"id": "minimum", "metric": "max", "signal": "V(out)", "operator": "at_least", "target": 9},
                {"id": "maximum", "metric": "max", "signal": "V(out)", "operator": "at_most", "target": 8},
                {"id": "missing", "metric": "mean", "signal": "V(nope)", "operator": "between", "lower": 0, "upper": 1},
            ],
            {"minimum": 9.5},
        )
        self.assertEqual(result["overall_status"], "fail")
        self.assertEqual(result["counts"], {"pass": 1, "fail": 1, "unverified": 1})
        self.assertAlmostEqual(
            result["requirements"][0]["comparison"]["absolute_error"], 0.5
        )
        self.assertIn("not present", result["requirements"][2]["reason"])

    def test_experiment_spec_rejects_ambiguous_or_nonfinite_contracts(self) -> None:
        spec = {
            "schema_version": 1,
            "title": "Divider",
            "netlist": "V1 in 0 5\nR1 in out 1k\nR2 out 0 1k\n.end\n",
            "commands": "op",
            "requirements": [
                {"id": "vout", "metric": "mean", "signal": "V(out)", "operator": "approximately", "target": 2.5, "tolerance_percent": 2},
            ],
            "theoretical_values": {"vout": 2.5},
        }
        normalized = validate_experiment_spec(spec)
        self.assertEqual(normalized["requirements"][0]["target"], 2.5)
        bad = {**spec, "theoretical_values": {"vout": float("nan")}}
        with self.assertRaises(ValueError):
            validate_experiment_spec(bad)
        with self.assertRaisesRegex(ValueError, "unknown ExperimentSpec"):
            validate_experiment_spec({**spec, "typo": True})
        with self.assertRaisesRegex(ValueError, "unknown parameters"):
            measure_many(
                {"columns": ["x", "y"], "rows": [[0, 1]]},
                [{"id": "bad", "metric": "mean", "signal": "y", "parameters": {"start": 0}}],
            )

    def test_crossing_metrics_require_monotonic_x(self) -> None:
        result = measure_many(
            {"columns": ["frequency", "V(out)"], "rows": [[1, 1], [4, 0.8], [2, 0.5]]},
            [{"id": "fc", "metric": "cutoff_frequency", "signal": "V(out)"}],
        )[0]
        self.assertEqual(result["status"], "unverified")
        self.assertIn("strictly increasing", result["reason"])


if __name__ == "__main__":
    unittest.main()
