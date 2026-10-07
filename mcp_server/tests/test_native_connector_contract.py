import unittest

from multisim_mcp.schematic_builder import COMPONENT_DEFINITIONS, parse_netlist


class NativeConnectorContractTest(unittest.TestCase):
    def test_hdr1x4_is_an_explicit_native_family(self):
        definition = COMPONENT_DEFINITIONS["HDR1X4"]
        self.assertEqual(definition.port_templates, (
            "hdr1x4_port1.xml",
            "hdr1x4_port2.xml",
            "hdr1x4_port3.xml",
            "hdr1x4_port4.xml",
        ))

    def test_portable_xj1_syntax_preserves_four_pin_order(self):
        parsed = parse_netlist("XJ1 a b c d HDR1X4\n.end")
        self.assertEqual(parsed.unsupported, [])
        self.assertEqual(len(parsed.components), 1)
        component = parsed.components[0]
        self.assertEqual(component.kind, "HDR1X4")
        self.assertEqual(component.refdes, "J1")
        self.assertEqual(component.nodes, ["a", "b", "c", "d"])
        self.assertEqual(component.model, "HDR1X4")


if __name__ == "__main__":
    unittest.main()
