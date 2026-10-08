import unittest

from multisim_mcp.engineering_planner import build_engineering_plan
from multisim_mcp.multiboard_digital_regression import (
    multiboard_digital_regression_matrix,
    select_multiboard_digital_regression_cases,
)
from multisim_mcp.server import _engineering_request_design
from multisim_mcp.spice_adapter import circuit_design_to_spice


class MultiboardDigitalRegressionTest(unittest.TestCase):
    def test_matrix_is_structurally_ready_and_has_explicit_four_pin_connector(self):
        case = multiboard_digital_regression_matrix()[0]
        plan = build_engineering_plan(case.request)
        candidate = plan["multiboard_candidates"][plan["recommended_multiboard_candidate"]]
        self.assertTrue(candidate["structurally_ready"])
        self.assertEqual(candidate["fixture_contract"]["coverage_status"], "complete")
        self.assertEqual(candidate["connector_contract_status"], "valid")
        self.assertEqual(
            [pin["net"] for pin in case.request["connectors"][0]["pins"]],
            ["clk", "data", "high", "0"],
        )

    def test_serialized_design_retains_digital_models_and_remote_load(self):
        case = multiboard_digital_regression_matrix()[0]
        plan = build_engineering_plan(case.request)
        design = _engineering_request_design(plan["request"], plan["plan_digest"])
        netlist = circuit_design_to_spice(design)
        self.assertIn("A1 data n1 high 0 NOT", netlist)
        self.assertIn("A2 n1 clk logic_out high 0 AND2", netlist)
        self.assertIn("A3 data n2 high 0 NOT", netlist)
        self.assertIn("A4 n2 clk io_out high 0 AND2", netlist)
        self.assertIn("RLOAD io_out 0 1k", netlist)
        self.assertIn("PULSE(0 5", netlist)
        self.assertIn("AC 1", netlist)

    def test_selector_rejects_unknown_case(self):
        with self.assertRaisesRegex(ValueError, "unknown multi-board digital case"):
            select_multiboard_digital_regression_cases("missing")


if __name__ == "__main__":
    unittest.main()
