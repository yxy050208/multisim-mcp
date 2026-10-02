import unittest

from multisim_mcp.natural_rlc import parse_natural_rlc_request


class NaturalRlcTest(unittest.TestCase):
    def test_builds_bounded_rlc_contract(self):
        result = parse_natural_rlc_request("design 1kHz RLC low-pass, L=10mH, C=2.5uF, automatic select, input 2V")
        self.assertEqual(result["requirement_contract"]["topology"], "rlc_second_order_low_pass")
        self.assertEqual(result["derived"]["input_amplitude_v"], 2.0)
        self.assertIn("L1", result["netlist"])
        self.assertIn("C1", result["netlist"])
        self.assertTrue(result["automatic_selection"])
        # The damping-aware search must include the low-R region needed to
        # place the measured pass-band peak near 1 kHz.  The former impedance
        # heuristic searched only 30--36 ohm and could not meet the target.
        self.assertIn(10.0, result["candidate_resistances_ohm"])
        self.assertLess(result["derived"]["damping_resistance_ohm"], 12.0)

    def test_rejects_unsupported_load_and_execution_claims(self):
        with self.assertRaises(ValueError):
            parse_natural_rlc_request("design 1kHz RLC band-pass with 100 ohm load")

    def test_requires_explicit_or_automatic_resistance(self):
        with self.assertRaises(ValueError):
            parse_natural_rlc_request("design 1kHz RLC low-pass, L=10mH, C=2.5uF")


if __name__ == "__main__":
    unittest.main()
