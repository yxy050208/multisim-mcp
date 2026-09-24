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
