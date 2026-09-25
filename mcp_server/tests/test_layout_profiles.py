from __future__ import annotations

import unittest

from multisim_mcp.schematic_builder import ComponentSpec, _digital_stage_profile


class DigitalLayoutProfileTest(unittest.TestCase):
    def test_places_low_fanout_logic_in_signal_order_and_keeps_load_near_host(self) -> None:
        specs = [
            ComponentSpec("DNOT4", "A1", ["in", "n1"]),
            ComponentSpec("DAND5", "A2", ["n1", "n2", "vdd"]),
            ComponentSpec("DOR5", "A3", ["n2", "out", "vdd"]),
            ComponentSpec("R", "R1", ["out", "0"], value="1k"),
            ComponentSpec("GND", "0", ["0"]),
        ]

        profile = _digital_stage_profile(specs)

        self.assertIsNotNone(profile)
        positions = profile["positions"]  # type: ignore[index]
        self.assertLess(positions["A1"][0], positions["A2"][0])
        self.assertLess(positions["A2"][0], positions["A3"][0])
        self.assertEqual(positions["R1"][0], positions["A3"][0] + 270)
        self.assertIn("0", positions)

    def test_digital_profile_does_not_override_analog_active_layouts(self) -> None:
        specs = [
            ComponentSpec("DNOT4", "A1", ["in", "n1"]),
            ComponentSpec("DNOT4", "A2", ["n1", "out"]),
            ComponentSpec("DNOT4", "A3", ["out", "n2"]),
            ComponentSpec("QNPN", "Q1", ["c", "b", "e"]),
        ]

        self.assertIsNone(_digital_stage_profile(specs))


if __name__ == "__main__":
    unittest.main()
