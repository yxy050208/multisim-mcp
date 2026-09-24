from __future__ import annotations

import unittest

from multisim_mcp.layout_validation import validate_schematic_geometry


class LayoutValidationTest(unittest.TestCase):
    def test_reports_overlapping_components(self) -> None:
        result = validate_schematic_geometry(
            [{"refdes": "R1", "x": 0, "y": 0}, {"refdes": "R2", "x": 50, "y": 0}],
            {},
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["findings"][0]["code"], "component-overlap")

    def test_accepts_separated_components_and_wires(self) -> None:
        result = validate_schematic_geometry(
            [{"refdes": "R1", "x": 0, "y": 0}, {"refdes": "R2", "x": 300, "y": 0}],
            {"n1": [[(126, 54), (300, 54)]]},
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["wire_count"], 1)

    def test_rejects_wire_endpoint_that_misses_pin(self) -> None:
        result = validate_schematic_geometry(
            [{"refdes": "R1", "x": 0, "y": 0}],
            {"n1": [[(126, 54), (220, 54)]]},
            pin_points={"n1": [(126, 54)]},
        )
        self.assertEqual(result["status"], "fail")
        self.assertIn("wire-endpoint-off-pin", {item["code"] for item in result["findings"]})


if __name__ == "__main__":
    unittest.main()
