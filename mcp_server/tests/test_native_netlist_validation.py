import unittest

from multisim_mcp.native_netlist_validation import parse_native_netlist, validate_native_netlist


class NativeNetlistValidationTest(unittest.TestCase):
    def test_parse_and_match(self):
        text = """0 design V1 1\nin design V1 2\nin design R1 1\nout design R1 2\nout design R2 1\n0 design R2 2\n"""
        self.assertEqual(parse_native_netlist(text)["R1"], {1: "in", 2: "out"})
        result = validate_native_netlist(text, {"R1": {1: "in", 2: "out"}})
        self.assertTrue(result["ok"])

    def test_wrong_pin_is_rejected(self):
        text = "in design R1 1\nout design R1 2\n"
        result = validate_native_netlist(text, {"R1": {1: "out"}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["findings"][0]["code"], "pin-net-mismatch")


if __name__ == "__main__":
    unittest.main()
