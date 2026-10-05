import unittest

from multisim_mcp import server


class EngineeringMultiboardServerTest(unittest.TestCase):
    def test_plan_tool_returns_logical_only_candidates_without_com(self):
        result = server.plan_multiboard_engineering_request({
            "schema_version": 1,
            "title": "two board divider",
            "application": "test",
            "boards": [{"id": "power"}, {"id": "signal"}],
            "components": [
                {"refdes": "V1", "kind": "V", "nodes": ["bus", "0"]},
                {"refdes": "R1", "kind": "R", "nodes": ["bus", "sense"]},
            ],
        })
        self.assertEqual(result["kind"], "multisim-mcp-engineering-plan")
        self.assertTrue(result["multiboard_candidates"])
        self.assertEqual(
            result["multiboard_candidates"][0]["logical_artifacts"]["status"],
            "logical-only",
        )

    def test_plan_tool_rejects_non_object_before_planner(self):
        with self.assertRaises(ValueError):
            server.plan_multiboard_engineering_request([])  # type: ignore[arg-type]
