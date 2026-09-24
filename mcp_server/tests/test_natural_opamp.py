import unittest
from multisim_mcp.natural_opamp import parse_natural_opamp_request

class NaturalOpampTest(unittest.TestCase):
    def test_builds_non_inverting_contract(self):
        result=parse_natural_opamp_request("design non-inverting op amp gain=5, input=0.1V, VCC=15V, VSS=-15V, auto select")
        self.assertEqual(result["requirement_contract"]["topology"],"opamp_non_inverting")
        self.assertIn("OPAMP5",result["netlist"])
        self.assertTrue(result["automatic_selection"])
    def test_rejects_unsupported_load(self):
        with self.assertRaises(ValueError): parse_natural_opamp_request("non-inverting op amp gain=5 with 1k load")

if __name__ == "__main__": unittest.main()
