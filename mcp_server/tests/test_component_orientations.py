"""Component orientation support: symbol matrix, pin follow-through, guards."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from multisim_mcp.schematic_builder import _orientation_matrix, build_schematic

NETLIST = "V1 in 0 DC 0 AC 1\nR1 in out 1k\nC1 out 0 10n\n.end\n"


def _build(tmp: str, name: str, orientations: dict[str, str] | None) -> dict:
    return build_schematic(
        NETLIST,
        Path(tmp) / f"{name}.xml",
        component_orientations=orientations,
    )


def _pins(result: dict) -> dict:
    return result["geometry"]["pins"]


class OrientationMatrixTest(unittest.TestCase):
    def test_aliases_resolve_to_the_expected_matrix(self) -> None:
        vertical = {"M00": "0", "M01": "1", "M10": "-1", "M11": "0"}
        self.assertEqual(_orientation_matrix("vertical"), vertical)
        self.assertEqual(_orientation_matrix("V"), vertical)
        self.assertEqual(_orientation_matrix(" 90 "), vertical)

    def test_native_keeps_the_carrier_orientation(self) -> None:
        for value in ("native", "h", "horizontal", "0", ""):
            self.assertIsNone(_orientation_matrix(value))

    def test_unknown_orientation_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _orientation_matrix("diagonal")


class OrientationBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_vertical_resistor_moves_its_pins(self) -> None:
        native = _build(self.tmp, "native", None)
        rotated = _build(self.tmp, "rotated", {"R1": "vertical"})

        for result in (native, rotated):
            self.assertEqual(result["layout_validation"]["status"], "pass")

        self.assertNotEqual(
            sorted(native["geometry"]["pins"]["in"]),
            sorted(rotated["geometry"]["pins"]["in"]),
            "a rotated symbol must present different pin coordinates",
        )
        # The netlist is untouched: same components, same nets.
        self.assertEqual(
            [c["refdes"] for c in native["components"]],
            [c["refdes"] for c in rotated["components"]],
        )
        self.assertEqual(native["nets"], rotated["nets"])

    def test_unknown_refdes_is_rejected_before_writing(self) -> None:
        with self.assertRaises(ValueError):
            _build(self.tmp, "bad_refdes", {"R99": "vertical"})

    def test_bad_orientation_value_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _build(self.tmp, "bad_value", {"R1": "diagonal"})


if __name__ == "__main__":
    unittest.main()
