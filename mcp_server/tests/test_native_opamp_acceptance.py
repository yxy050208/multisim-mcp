import unittest

from multisim_mcp.native_opamp_acceptance import evaluate_opamp_ac


class NativeOpampAcceptanceTest(unittest.TestCase):
    def test_gain_passes(self):
        result = {"results": {"in": {"rows": [[1], [1]]}, "out": {"rows": [[1], [11]]}}}
        self.assertTrue(evaluate_opamp_ac(result, 11)["passed"])

    def test_zero_output_fails(self):
        result = {"results": {"in": {"rows": [[1], [1]]}, "out": {"rows": [[1], [0]]}}}
        self.assertFalse(evaluate_opamp_ac(result, 11)["passed"])
