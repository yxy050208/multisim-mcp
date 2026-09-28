from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
