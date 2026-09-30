from __future__ import annotations

import unittest

from tools.run_digital_regression import _pin_evidence_summary
from multisim_mcp.digital_regression import (
    digital_regression_matrix,
    select_digital_regression_cases,
)


class DigitalRegressionMatrixTest(unittest.TestCase):
    def test_matrix_contains_sequential_and_fanout_cases(self) -> None:
        cases = digital_regression_matrix()
        names = {item.case_id for item in cases}
        self.assertEqual(
            names,
            {
                "logic_chain_load",
                "dff_load",
                "counter4_load",
                "shift4_load",
                "counter4_decode_load",
            },
        )
        self.assertTrue(all(item.output_nets for item in cases))

    def test_selection_rejects_unknown_case(self) -> None:
        selected = select_digital_regression_cases("counter4_load")
        self.assertEqual([item.case_id for item in selected], ["counter4_load"])
        with self.assertRaises(ValueError):
            select_digital_regression_cases("missing")

    def test_adapter_invocations_are_source_level_requirements(self) -> None:
        case = select_digital_regression_cases("counter4_load")[0]
        self.assertIn("XCNT", case.required_components)
        self.assertEqual(case.manifest()["output_nets"], ["q0", "q1", "q2", "q3"])

    def test_pin_evidence_summary_keeps_unverified_state_explicit(self) -> None:
        summary = _pin_evidence_summary(
            {
                "pin_connections": {
                    "status": "unverified",
                    "checked_components": 2,
                    "unverified_components": ["A1"],
                    "mismatches": [],
                    "model_port_evidence": [
                        {"state": "present"},
                        {"state": "not_observed"},
                    ],
                }
            }
        )
        self.assertFalse(summary["fully_verified"])
        self.assertEqual(summary["mismatch_count"], 0)
        self.assertEqual(summary["model_port_states"], {"present": 1, "not_observed": 1})


if __name__ == "__main__":
    unittest.main()
