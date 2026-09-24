import unittest
from multisim_mcp.natural_analog_frontend import parse_natural_analog_frontend, generate_analog_candidates
class NaturalAnalogFrontendTests(unittest.TestCase):
    def test_chinese_sentence_becomes_vendor_proposal(self):
        p=parse_natural_analog_frontend("设计一个传感器模拟前端，输入100mV，总增益10，截止频率1kHz，±15V供电，负载100kΩ")
        self.assertEqual(p["planning_method"],"bounded-sensor-frontend-parser"); self.assertIn("LM324AJ",p["proposal"]["netlist"])
        self.assertEqual({e["type"] for e in p["proposal"]["experiments"]},{"op","ac","tran"}); self.assertEqual(len(p["proposal"]["checks"]),5)
        self.assertAlmostEqual(p["derived"]["input_v"],.1)
        self.assertAlmostEqual(p["proposal"]["checks"][0]["max"], 1.01)
        candidates=generate_analog_candidates(p)
        self.assertEqual(len(candidates),5)
        self.assertIn("RF1 stage1 fb1",candidates[1]["proposal"]["netlist"])
    def test_requirements_are_not_silently_dropped(self):
        with self.assertRaises(ValueError): parse_natural_analog_frontend("传感器前端接入ADC并设计多板开关电源")
    def test_english_units_and_bounds(self):
        p=parse_natural_analog_frontend("sensor signal conditioning, input=0.2V, gain=4, cutoff=800Hz, load=20kOhm")
        self.assertAlmostEqual(p["derived"]["cutoff_hz"],800)
        with self.assertRaises(ValueError): parse_natural_analog_frontend("sensor low pass input=200V")
if __name__ == "__main__": unittest.main()
