"""SPICE title-line parsing regressions."""

from __future__ import annotations

import unittest

from multisim_mcp.schematic_builder import parse_netlist


class NetlistTitleTest(unittest.TestCase):
    def test_standard_first_line_title_is_ignored(self) -> None:
        parsed = parse_netlist(
            "1 kohm resistor across a 10 V DC source\n"
            "V1 1 0 DC 10\n"
            "R1 1 0 1k\n"
            ".end\n"
        )
        self.assertEqual(parsed.title, "1 kohm resistor across a 10 V DC source")
        self.assertEqual([item.refdes for item in parsed.components], ["V1", "R1"])
        self.assertEqual(parsed.unsupported, [])

    def test_title_is_not_taken_from_a_device_record(self) -> None:
        parsed = parse_netlist("V1 in 0 DC 5\nR1 in 0 1k\n.end\n")
        self.assertIsNone(parsed.title)
        self.assertEqual([item.refdes for item in parsed.components], ["V1", "R1"])


if __name__ == "__main__":
    unittest.main()
