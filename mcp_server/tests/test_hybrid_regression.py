from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from multisim_mcp.hybrid_regression import (
    ConverterTransferContract,
    evaluate_converter_transfer,
    hybrid_regression_matrix,
    select_hybrid_regression_cases,
)
from tools.run_hybrid_regression import _native_observation, _observed_outputs
from tools.run_hybrid_regression import _write_matrix_manifest


class HybridRegressionTest(unittest.TestCase):
    def test_matrix_contains_digital_to_analog_bridge(self) -> None:
        cases = hybrid_regression_matrix()
        self.assertEqual(
            [case.case_id for case in cases],
            [
                "not_rc_load",
                "logic_chain_rc_load",
                "counter_q0_rc_load",
                "shift_s0_rc_load",
                "diode_rc_shaper",
                "counter_q0_q1_rc_load",
                "vcvs_rc_bridge",
                "vccs_rc_bridge",
                "dac_rc_bridge",
                "adc_rc_bridge",
                "adc4_rc_bridge",
                "dac4_rc_bridge",
                "adc4_dac4_transfer",
            ],
        )
        self.assertEqual(cases[0].manifest()["output_nets"], ["dout", "filt"])

    def test_selection_rejects_unknown_case(self) -> None:
        with self.assertRaises(ValueError):
            select_hybrid_regression_cases("missing")

    def test_invalid_observation_contracts_are_rejected(self) -> None:
        case = select_hybrid_regression_cases("adc4_dac4_transfer")[0]
        for invalid in (
            replace(case, output_nets=case.output_nets + ("d0",)),
            replace(case, output_pairs=()),
            replace(case, output_pairs=(("d0", "unknown"),)),
            replace(case, converter_contract=ConverterTransferContract("unknown", ("d0",), "analog_out")),
        ):
            with self.subTest(case=invalid), self.assertRaises(ValueError):
                invalid.validate()

    def test_observation_uses_native_voltage_columns(self) -> None:
        case = select_hybrid_regression_cases()[0]
        result = _observed_outputs(
            case,
            {"simulation": {"columns": ["time", "V(dout)", "V(filt)", "I(vin)"]}},
        )
        self.assertEqual(result["observed_outputs"], ["dout", "filt"])
        self.assertEqual(result["missing_outputs"], [])


class ConverterTransferTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = ConverterTransferContract("input", ("d0", "d1", "d2", "d3"), "output")
        # Independent truth table covers both directions and every bin center.
        self.codes = list(range(16)) + list(reversed(range(16)))
        self.series = {
            "input": [5 * (code + 0.5) / 16 for code in self.codes],
            "output": [5 * code / 15 for code in self.codes],
            **{f"d{bit}": [5.0 if code & (1 << bit) else 0.0 for code in self.codes] for bit in range(4)},
        }

    def test_sweep_checks_every_code_and_reconstruction(self) -> None:
        result = evaluate_converter_transfer(self.contract, self.series)
        self.assertTrue(result["passed"])
        self.assertEqual(result["samples_per_code"], [2] * 16)
        self.assertEqual(result["max_dac_error_voltage"], 0)

    def test_swapped_bits_fail_even_with_full_swing(self) -> None:
        self.series["d0"], self.series["d1"] = self.series["d1"], self.series["d0"]
        result = evaluate_converter_transfer(self.contract, self.series)
        self.assertFalse(result["passed"])
        self.assertTrue(result["checks"]["digital_levels"])
        self.assertTrue(result["checks"]["all_codes_observed"])
        self.assertFalse(result["checks"]["adc_code_matches"])

    def test_wrong_dac_gain_fails(self) -> None:
        self.series["output"] = [value * 0.8 for value in self.series["output"]]
        self.assertFalse(evaluate_converter_transfer(self.contract, self.series)["checks"]["dac_weighting_matches"])

    def test_invalid_transfer_contracts_are_rejected(self) -> None:
        for invalid in (
            replace(self.contract, digital_bits=("d0", "d0")),
            replace(self.contract, digital_bits=()),
            replace(self.contract, high_voltage=0),
            replace(self.contract, high_voltage=float("nan")),
            replace(self.contract, voltage_tolerance=0.2),
            replace(self.contract, boundary_guard_voltage=-0.01),
            replace(self.contract, boundary_guard_voltage=0.2),
        ):
            with self.subTest(contract=invalid), self.assertRaises(ValueError):
                invalid.validate()

    def test_endpoint_only_test_cannot_claim_all_codes(self) -> None:
        for net in self.series:
            self.series[net] = self.series[net][:1] + self.series[net][15:16]
        result = evaluate_converter_transfer(self.contract, self.series)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["all_codes_observed"])

    def test_missing_misaligned_and_nonfinite_samples_fail_closed(self) -> None:
        for broken in ([], [0.0], [float("nan")] * 32, [float("inf")] * 32):
            with self.subTest(values=broken[:1]):
                series = dict(self.series, output=broken)
                self.assertFalse(evaluate_converter_transfer(self.contract, series)["checks"]["finite_samples"])

    def test_intermediate_digital_voltage_fails(self) -> None:
        self.series["d0"][0] = 1.0
        self.assertFalse(evaluate_converter_transfer(self.contract, self.series)["checks"]["digital_levels"])

    def test_boundary_guard_is_explicit_and_does_not_prove_coverage(self) -> None:
        series = {net: values[:1] for net, values in self.series.items()}
        series["input"] = [5 / 16]
        result = evaluate_converter_transfer(self.contract, series)
        self.assertFalse(result["passed"])
        self.assertEqual(result["boundary_samples_excluded"], 1)
        self.assertEqual(result["checked_samples"], 0)

    def test_transfer_gate_reads_native_csv_with_explicit_output_pairs(self) -> None:
        case = select_hybrid_regression_cases("adc4_dac4_transfer")[0]
        native_series = dict(self.series, analog=self.series["input"], analog_out=self.series["output"], filt=self.series["output"])
        signals = [f"V(Probe{index})" for index in range(len(case.output_nets))]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "analysis-001").mkdir()
            with (root / "analysis-001" / "data.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow([f"{signal}.value" for signal in signals])
                writer.writerows(zip(*(native_series[net] for net in case.output_nets)))
            result = _native_observation(case, root, signals)
        self.assertTrue(result["converter_transfer"]["passed"])
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(len(result["pair_checks"]), 4)
        self.assertEqual(result["missing_outputs"], [])

    def test_matrix_manifest_binds_summary_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matrix = root / "matrix.json"
            matrix.write_text('{"passed": true}\n', encoding="utf-8")
            manifest = _write_matrix_manifest(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["kind"], "hybrid-regression-manifest")
            self.assertEqual(payload["files"][0]["path"], "matrix.json")
            self.assertEqual(
                payload["files"][0]["sha256"],
                hashlib.sha256(matrix.read_bytes()).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
