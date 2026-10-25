"""Regression tests for the DeepSeek Harness schema boundary."""

from __future__ import annotations

import unittest

from multisim_mcp.dsh_schema import normalize_dsh_tool_schema


class DshSchemaTest(unittest.TestCase):
    def test_expands_refs_and_rewrites_supported_shapes(self) -> None:
        source = {
            "type": "object",
            "$defs": {
                "Options": {
                    "type": "object",
                    "properties": {
                        "mode": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        "values": {"additionalProperties": {"type": "number"}},
                    },
                }
            },
            "properties": {
                "options": {"$ref": "#/$defs/Options"},
            },
        }
        normalized = normalize_dsh_tool_schema(source)
        self.assertNotIn("$defs", normalized)
        options = normalized["properties"]["options"]
        self.assertNotIn("$ref", options)
        self.assertIn("oneOf", options["properties"]["mode"])
        self.assertEqual(options["properties"]["values"]["additionalProperties"], True)
        self.assertEqual(source["properties"]["options"], {"$ref": "#/$defs/Options"})

    def test_keeps_existing_one_of_and_handles_nested_lists(self) -> None:
        source = {
            "type": "array",
            "items": {
                "anyOf": [
                    {"type": "string"},
                    {"additionalProperties": {"type": "integer"}},
                ]
            },
        }
        normalized = normalize_dsh_tool_schema(source)
        self.assertNotIn("anyOf", normalized["items"])
        self.assertIn("oneOf", normalized["items"])
        self.assertEqual(
            normalized["items"]["oneOf"][1]["additionalProperties"], True
        )


if __name__ == "__main__":
    unittest.main()
