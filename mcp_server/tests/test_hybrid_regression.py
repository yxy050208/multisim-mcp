from __future__ import annotations

import unittest

from multisim_mcp.hybrid_regression import (
    hybrid_regression_matrix,
    select_hybrid_regression_cases,
)
from tools.run_hybrid_regression import _observed_outputs


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
            ],
        )
        self.assertEqual(cases[0].manifest()["output_nets"], ["dout", "filt"])

    def test_selection_rejects_unknown_case(self) -> None:
        with self.assertRaises(ValueError):
            select_hybrid_regression_cases("missing")

    def test_observation_uses_native_voltage_columns(self) -> None:
        case = select_hybrid_regression_cases()[0]
        result = _observed_outputs(
            case,
            {"simulation": {"columns": ["time", "V(dout)", "V(filt)", "I(vin)"]}},
        )
        self.assertEqual(result["observed_outputs"], ["dout", "filt"])
        self.assertEqual(result["missing_outputs"], [])


if __name__ == "__main__":
    unittest.main()
