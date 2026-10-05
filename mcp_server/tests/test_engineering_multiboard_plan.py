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

    def test_plan_gates_native_candidate_on_explicit_fixture_contract(self):
        plan = build_engineering_plan({
            "schema_version": 1, "title": "two board divider", "application": "boundary test",
            "boards": [{"id": "power"}, {"id": "signal"}],
            "components": [
                {"refdes": "V1", "board": "power", "nodes": ["bus", "0"]},
                {"refdes": "R1", "board": "signal", "nodes": ["bus", "sense"]},
                {"refdes": "R2", "board": "signal", "nodes": ["sense", "0"]},
            ],
            "fixtures": [
                {"id": "power-bus-observe", "board_id": "power", "kind": "observation", "net": "bus"},
                {"id": "power-bus-anchor", "board_id": "power", "kind": "resistor_termination", "net": "bus", "reference_net": "0", "refdes": "RFIX1", "value": "1G"},
                {"id": "power-ground", "board_id": "power", "kind": "ground_reference", "net": "0"},
                {"id": "signal-bus-drive", "board_id": "signal", "kind": "voltage_source", "net": "bus", "reference_net": "0", "refdes": "VFIX1", "value": "5"},
                {"id": "signal-ground", "board_id": "signal", "kind": "ground_reference", "net": "0"},
                {"id": "signal-sense-observe", "board_id": "signal", "kind": "observation", "net": "sense"},
            ],
        })
        candidate = plan["multiboard_candidates"][plan["recommended_multiboard_candidate"]]
        self.assertEqual(candidate["structural_status"], "valid")
        self.assertEqual(candidate["fixture_contract"]["coverage_status"], "complete")

    def test_plan_does_not_recommend_uncovered_fixture_candidate(self):
        plan = build_engineering_plan({
            "schema_version": 1, "title": "two board divider", "application": "boundary test",
            "boards": [{"id": "power"}, {"id": "signal"}],
            "components": [
                {"refdes": "V1", "board": "power", "nodes": ["bus", "0"]},
                {"refdes": "R1", "board": "signal", "nodes": ["bus", "sense"]},
            ],
            "fixtures": [
                {"id": "signal-bus-drive", "board_id": "signal", "kind": "voltage_source", "net": "bus", "reference_net": "0", "refdes": "VFIX1", "value": "5"},
            ],
        })
        self.assertIsNone(plan["recommended_multiboard_candidate"])
        self.assertTrue(all(item["structural_status"] != "valid" for item in plan["multiboard_candidates"]))
