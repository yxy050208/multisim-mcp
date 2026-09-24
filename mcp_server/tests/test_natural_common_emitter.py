import unittest
from multisim_mcp.natural_common_emitter import parse_natural_common_emitter

class CommonEmitterTest(unittest.TestCase):
    def test_bounded_plan(self):
        p = parse_natural_common_emitter("设计一个12V单电源、增益10倍的NPN共射放大器")
        self.assertEqual(p["topology"], "single_npn_common_emitter")
        self.assertEqual(p["derived"]["target_gain"], 10)
    def test_rejects_power_stage(self):
        with self.assertRaises(ValueError):
            parse_natural_common_emitter("设计一个24V MOSFET功率放大器")
