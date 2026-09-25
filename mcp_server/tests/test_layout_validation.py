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

    def test_crossing_rate_is_a_hard_geometry_finding(self) -> None:
        result = validate_schematic_geometry(
            [],
            {
                "a": [[(0, 10), (30, 10)]],
                "b": [[(15, 0), (15, 20)]],
            },
            max_crossings_per_wire=0.5,
        )
        self.assertEqual(result["different_net_crossings"], 1)
        self.assertEqual(result["crossings_per_wire"], 0.5)
        self.assertEqual(result["crossings_limit_per_wire"], 0.5)
        self.assertEqual(result["status"], "pass")

        result = validate_schematic_geometry(
            [],
            {
                "a": [[(0, 10), (30, 10)]],
                "b": [[(15, 0), (15, 20)]],
            },
            max_crossings_per_wire=0.49,
        )
        self.assertEqual(result["status"], "fail")
        finding = next(item for item in result["findings"] if item["code"] == "excessive-wire-crossings")
        self.assertEqual(finding["crossings"], 1)
        self.assertEqual(finding["wires"], 2)

    def test_crossing_limit_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            validate_schematic_geometry([], {}, max_crossings_per_wire=-1)
        with self.assertRaises(ValueError):
            validate_schematic_geometry([], {}, max_crossings_per_wire=True)


if __name__ == "__main__":
    unittest.main()
