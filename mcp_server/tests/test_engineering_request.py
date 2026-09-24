import unittest

from multisim_mcp.engineering_request import validate_engineering_request


class EngineeringRequestTest(unittest.TestCase):
    def test_normalizes_minimal_request(self) -> None:
        result = validate_engineering_request({
            "schema_version": 1,
            "title": "5V divider",
            "application": "sensor bias",
            "objectives": [{"metric": "vout", "direction": "target", "target": 5}],
        })
        self.assertEqual(result["boards"][0]["id"], "main")
        self.assertEqual(result["objectives"][0]["direction"], "target")

    def test_rejects_duplicate_boards(self) -> None:
        with self.assertRaises(ValueError):
            validate_engineering_request({
                "schema_version": 1, "title": "x", "application": "y",
                "boards": [{"id": "a"}, {"id": "a"}],
            })
