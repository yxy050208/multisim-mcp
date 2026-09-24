import unittest

from multisim_mcp.natural_rectifier import parse_natural_rectifier


class NaturalRectifierTest(unittest.TestCase):
    def test_bridge_plan_is_bounded(self):
        result = parse_natural_rectifier("设计12V 50Hz桥式整流，负载100mA")
        self.assertEqual(result["topology"], "single_phase_bridge_with_reservoir")
        self.assertEqual(result["derived"]["diode_count"], 4)
        self.assertIn("D4", result["proposal"]["netlist"])

    def test_defaults_are_explicit(self):
        result = parse_natural_rectifier("设计一个桥式整流电源")
        self.assertEqual(result["derived"]["ac_rms_v"], 12)
        self.assertTrue(result["assumptions"])

    def test_rejects_unsupported_switching_and_ambiguous_values(self):
        with self.assertRaises(ValueError):
            parse_natural_rectifier("设计24V开关电源")
        with self.assertRaises(ValueError):
            parse_natural_rectifier("桥式整流 12V 24V")
