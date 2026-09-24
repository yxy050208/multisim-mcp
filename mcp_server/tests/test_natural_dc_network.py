import unittest
from multisim_mcp.natural_dc_network import parse_natural_dc_network
from multisim_mcp.natural_dc_network_run import run_natural_dc_network

class NaturalDcNetworkTest(unittest.TestCase):
    def test_divider_plan(self):
        p = parse_natural_dc_network("设计一个10V输入、5V输出的直流分压电路")
        self.assertAlmostEqual(p["derived"]["target_v"], 5)
        self.assertIn("R1", p["proposal"]["netlist"])
    def test_scope(self):
        with self.assertRaises(ValueError):
            parse_natural_dc_network("设计一个数字计数器")
    def test_runner_preview(self):
        result = run_natural_dc_network("10V输入、5V输出的直流分压电路", "unused", execute=False)
        self.assertEqual(result["verification_status"], "unverified")
