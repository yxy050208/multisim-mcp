"""Requested NPN part names replace the carrier's built-in 2N3904 card."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from multisim_mcp.schematic_builder import build_schematic

NETLIST = "V1 c 0 DC 12\nR1 c b 100k\nQ1 c b 0 2N3903\n.end\n"


class NativeModelCardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_requested_part_renames_element_and_bound_model(self) -> None:
        result = build_schematic(NETLIST, self.tmp / "q.xml")
        xml = (self.tmp / "q.xml").read_text(encoding="utf-8")

        self.assertEqual(result["model_card_overrides"], ["Q1"])
        self.assertIn(".MODEL 2N3903", xml)
        self.assertNotIn(".MODEL 2N3904", xml)
        # The substituted card is the 2N3903-class one, not the 2N3904 defaults.
        self.assertIn("Bf=140.6", xml)
        self.assertNotIn("Bf=416.4", xml)

    def test_override_replaces_the_placeholder_warning(self) -> None:
        result = build_schematic(NETLIST, self.tmp / "w.xml")
        warnings = result["model_warnings"]
        self.assertTrue(
            any("generic published family model" in text for text in warnings),
            warnings,
        )
        self.assertFalse(
            any("is represented by the native" in text for text in warnings), warnings
        )

    def test_unknown_part_keeps_the_carrier_and_still_warns(self) -> None:
        netlist = NETLIST.replace("2N3903", "2N9999")
        result = build_schematic(netlist, self.tmp / "u.xml")
        self.assertEqual(result["model_card_overrides"], [])
        self.assertTrue(
            any("is represented by the native" in text for text in result["model_warnings"])
        )


if __name__ == "__main__":
    unittest.main()
