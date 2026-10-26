"""Thin MCP adapter tests for compare_design_variants."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from multisim_mcp import server
from multisim_mcp.eda_core import CircuitDesign
from multisim_mcp.design_patch_service import create_design_diff_patch


def _design(design_id: str, value: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "design_id": design_id,
        "title": design_id,
        "revision": 0,
        "components": [
            {
                "refdes": "R1",
                "kind": "R",
                "nodes": ["in", "out"],
                "value": value,
                "model": None,
                "parameters": {},
            }
        ],
        "parameters": {},
        "annotations": {},
    }


class DesignComparisonServerTest(unittest.TestCase):
    def test_adapter_converts_ordered_designs_and_forwards_limits(self) -> None:
        service = Mock()
        service.run.return_value = {"success": True, "status": "ranked"}
        spec = {"schema_version": 1}
        variants = [
            {"variant_id": "one", "design": _design("one-design", "1k")},
            {"variant_id": "two", "design": _design("two-design", "2k")},
        ]
        with patch.object(
            server, "_design_comparison_service", return_value=service
        ):
            result = server.compare_design_variants(
                variants,
                spec,
                "C:/comparison-output",
                timeout_per_experiment=33.0,
                max_points=456,
            )
        self.assertTrue(result["success"])
        args, kwargs = service.run.call_args
        self.assertEqual(list(args[0]), ["one", "two"])
        self.assertTrue(all(isinstance(item, CircuitDesign) for item in args[0].values()))
        self.assertIs(args[1], spec)
        self.assertEqual(args[2], "C:/comparison-output")
        self.assertEqual(kwargs["timeout_per_experiment"], 33.0)
        self.assertEqual(kwargs["max_points"], 456)

    def test_adapter_rejects_duplicate_or_extra_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            server.compare_design_variants(
                [
                    {"variant_id": "one", "design": _design("one", "1k")},
                    {"variant_id": "one", "design": _design("two", "2k")},
                ],
                {},
                "out",
            )
        with self.assertRaisesRegex(ValueError, "exactly"):
            server.compare_design_variants(
                [
                    {
                        "variant_id": "one",
                        "design": _design("one", "1k"),
                        "extra": True,
                    }
                ],
                {},
                "out",
            )

    def test_adapter_materializes_patch_variants_without_mutating_base(self) -> None:
        service = Mock()
        service.run.return_value = {"success": True, "status": "ranked"}
        base = CircuitDesign.from_dict(_design("base-design", "1k"))
        after = CircuitDesign.from_dict(_design("base-design", "2k"))
        patch_payload = create_design_diff_patch(base, after).to_dict()
        variants = [
            {"variant_id": "baseline", "design": base.to_dict()},
            {"variant_id": "candidate", "patch": patch_payload},
        ]
        with patch.object(
            server, "_design_comparison_service", return_value=service
        ):
            result = server.compare_design_variants(
                variants,
                {"schema_version": 1},
                "C:/comparison-output",
                base_design=base.to_dict(),
            )
        self.assertTrue(result["success"])
        args, kwargs = service.run.call_args
        self.assertEqual(list(args[0]), ["baseline", "candidate"])
        self.assertEqual(args[0]["candidate"].components[0].value, "2k")
        self.assertEqual(
            kwargs["variant_metadata"]["candidate"]["patch"], patch_payload
        )
        self.assertEqual(base.components[0].value, "1k")

    def test_patch_variant_requires_base_design(self) -> None:
        with self.assertRaisesRegex(ValueError, "base_design is required"):
            server.compare_design_variants(
                [{"variant_id": "candidate", "patch": {}}],
                {"schema_version": 1},
                "out",
            )


if __name__ == "__main__":
    unittest.main()
