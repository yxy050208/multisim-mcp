import unittest

from multisim_mcp.engineering_planner import build_engineering_plan


class EngineeringMultiboardPlanTest(unittest.TestCase):
    def test_plan_includes_ranked_multiboard_candidates(self):
        request = {"schema_version": 1, "title": "two board", "application": "test",
                   "boards": [{"id": "power"}, {"id": "signal"}],
                   "components": [{"refdes": "V1", "nodes": ["out", "0"]},
                                  {"refdes": "R1", "nodes": ["out", "0"]}]}
        plan = build_engineering_plan(request)
        self.assertEqual(plan["multiboard_candidates"][0]["score"]["cost"], 0)
        self.assertEqual(plan["multiboard_candidates"][0]["logical_artifacts"]["status"], "logical-only")
        self.assertEqual(
            plan["multiboard_candidates"][0]["logical_artifacts"]["verification_status"],
            "unverified",
        )
        self.assertFalse(plan["multiboard_candidates"][0]["structurally_ready"])
        self.assertIsNotNone(plan["recommended_multiboard_candidate"])

    def test_plan_recommends_a_populated_structural_candidate(self):
        plan = build_engineering_plan({
            "schema_version": 1, "title": "two board", "application": "test",
            "boards": [{"id": "power"}, {"id": "signal"}],
            "components": [
                {"refdes": "V1", "nodes": ["bus", "0"]},
                {"refdes": "R1", "nodes": ["bus", "sense"]},
            ],
        })
        self.assertIsNotNone(plan["recommended_multiboard_candidate"])
        selected = plan["multiboard_candidates"][plan["recommended_multiboard_candidate"]]
        self.assertTrue(selected["structurally_ready"])
        self.assertTrue(all(item["components"] for item in selected["logical_artifacts"]["boards"]))
