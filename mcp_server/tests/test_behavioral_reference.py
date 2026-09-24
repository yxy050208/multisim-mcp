import unittest

from multisim_mcp.behavioral_reference import build_behavioral_reference_netlist
from multisim_mcp.schematic_builder import parse_netlist


class BehavioralReferenceTest(unittest.TestCase):
    def test_maps_native_dff_pin_order_and_active_low_controls(self) -> None:
        result = build_behavioral_reference_netlist(
            "VCC vcc 0 5\n"
            "XU1 d pr clr clk q nq 0 vcc 7474N\n"
            ".end\n"
        )
        self.assertTrue(result["converted"])
        self.assertEqual(result["converted_count"], 1)
        component = result["components"][0]
        self.assertEqual(component["source_refdes"], "XU1")
        self.assertEqual(component["source_model"], "7474N")
        self.assertEqual(component["pin_mapping"]["set"], "pr")
        self.assertEqual(component["pin_mapping"]["reset"], "clr")
        self.assertEqual(
            component["reference_control_nets"]["set"], "n_XU1_pr_bar"
        )
        self.assertEqual(
            component["reference_control_nets"]["reset"], "n_XU1_clr_bar"
        )
        self.assertEqual(component["preset_polarity"], "active-low")
        self.assertEqual(component["clear_polarity"], "active-low")
        self.assertIn("@DFF", result["netlist"])
        self.assertIn("AXU1PRINV pr n_XU1_pr_bar vcc 0 NOT", result["netlist"])
        self.assertIn("AXU1CLRINV clr n_XU1_clr_bar vcc 0 NOT", result["netlist"])
        parsed = parse_netlist(result["netlist"])
        self.assertEqual(sum(item.kind == "DJK7" for item in parsed.components), 1)
        self.assertEqual(sum(item.kind == "DFF8" for item in parsed.components), 0)

    def test_supports_u_prefixed_native_carrier_alias(self) -> None:
        result = build_behavioral_reference_netlist(
            "U2 d pr clr clk q nq 0 vcc DFF8\n.end\n"
        )
        self.assertEqual(result["converted_count"], 1)
        self.assertIn(
            "XU2_BEHAVIORAL d clk n_U2_pr_bar n_U2_clr_bar q nq vcc 0 @DFF",
            result["netlist"],
        )

    def test_rejects_wrong_native_terminal_count_instead_of_guessing(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly 8 terminals"):
            build_behavioral_reference_netlist(
                "XU1 d pr clr clk q nq 0 7474N EXTRA\n.end\n"
            )

    def test_non_native_netlist_is_unchanged_and_not_claimed_as_converted(self) -> None:
        source = "V1 in 0 5\nR1 in out 1k\n.end\n"
        result = build_behavioral_reference_netlist(source)
        self.assertFalse(result["converted"])
        self.assertEqual(result["converted_count"], 0)
        self.assertEqual(result["netlist"], source)

    def test_rejects_unsafe_control_directive(self) -> None:
        with self.assertRaises(ValueError):
            build_behavioral_reference_netlist(
                "XU1 d pr clr clk q nq 0 vcc 7474N\n.control\nrun\n.endc\n.end\n"
            )


if __name__ == "__main__":
    unittest.main()
