"""Potentiometer carrier: statement parsing, value slots and guards."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from multisim_mcp.schematic_builder import (
    COMPONENT_DEFINITIONS,
    build_schematic,
    parse_netlist,
)

NETLIST = """* pot check
V1 top 0 DC 12
POT R5 top wiper bottom 50k 50
R1 bottom 0 1k
.end
"""


class PotStatementTest(unittest.TestCase):
    def test_statement_parses_three_terminals(self) -> None:
        parsed = parse_netlist(NETLIST)
        pot = [c for c in parsed.components if c.kind == "POT"]
        self.assertEqual(len(pot), 1)
        self.assertEqual(pot[0].refdes, "R5")
        self.assertEqual(pot[0].nodes, ["top", "wiper", "bottom"])
        self.assertEqual(pot[0].value, "50k")
        self.assertEqual(pot[0].parameters, ["50"])

    def test_percentage_defaults_to_fifty(self) -> None:
        parsed = parse_netlist("V1 a 0 DC 1\nPOT RV1 a w b 10k\n.end\n")
        pot = [c for c in parsed.components if c.kind == "POT"][0]
        self.assertEqual(pot.parameters, [])


class PotBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_build_writes_resistance_and_percentage(self) -> None:
        result = build_schematic(NETLIST, self.tmp / "pot.xml")
        xml = (self.tmp / "pot.xml").read_text(encoding="utf-8")

        pot = [c for c in result["components"] if c["refdes"] == "R5"]
        self.assertEqual(len(pot), 1)
        self.assertEqual(pot[0]["kind"], "POT")
        # The codec stores strings behind an "&ASC" escape, HTML-escaped in the
        # serialized XML.
        self.assertIn("ASC50k", xml)
        self.assertIn("0.000000;50.000000;", xml)
        self.assertIn("POT", COMPONENT_DEFINITIONS)

    def test_bad_percentage_fails_closed(self) -> None:
        for bad in ("abc", "150", "-5"):
            with self.subTest(percentage=bad):
                netlist = NETLIST.replace("50k 50", f"50k {bad}")
                with self.assertRaises(ValueError):
                    build_schematic(netlist, self.tmp / f"bad_{bad}.xml")


if __name__ == "__main__":
    unittest.main()
