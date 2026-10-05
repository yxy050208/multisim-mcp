import unittest

from multisim_mcp.multiboard_plan import plan_multiboard_partition, score_multiboard_partition, rank_partition_candidates


class MultiboardPlanTest(unittest.TestCase):
    def test_cross_board_net_requires_connector(self):
        result = plan_multiboard_partition([
            {"refdes": "V1", "board": "power", "nodes": ["out", "0"]},
            {"refdes": "R1", "board": "signal", "nodes": ["out", "0"]},
        ], [{"id": "power"}, {"id": "signal"}])
        self.assertEqual(result["connector_count"], 2)
        self.assertEqual(result["connectors"][0]["pin"], 1)
        self.assertEqual(result["connectors"][0]["id"], "J1")
        score = score_multiboard_partition(result)
        self.assertEqual(score["cost"], 28)
        self.assertEqual(result["boards"][0]["component_count"], 1)
        self.assertEqual(result["connector_pins_by_board"], {"power": 2, "signal": 2})
        self.assertEqual(len(result["board_interfaces"]["power"]), 2)
        self.assertTrue(result["feasible"])

    def test_board_capacity_is_explicitly_infeasible(self):
        result = plan_multiboard_partition([
            {"refdes": "V1", "board": "power", "nodes": ["out", "0"]},
            {"refdes": "R1", "board": "signal", "nodes": ["out", "0"]},
        ], [
            {"id": "power", "max_connector_pins": 1},
            {"id": "signal", "max_connector_pins": 1},
        ])
        self.assertEqual(result["status"], "infeasible")
        self.assertFalse(result["feasible"])
        self.assertEqual(len(result["violations"]), 2)
        score = score_multiboard_partition(result)
        self.assertFalse(score["feasible"])
        self.assertGreater(score["cost"], 1_000_000)

    def test_ground_aliases_are_classified_as_ground(self):
        result = plan_multiboard_partition([
            {"refdes": "V1", "board": "a", "nodes": ["GND", "signal"]},
            {"refdes": "R1", "board": "b", "nodes": ["GND", "signal"]},
        ], [{"id": "a"}, {"id": "b"}])
        self.assertEqual([item["signal_type"] for item in result["connectors"]], ["ground", "signal"])

    def test_invalid_limits_and_empty_nodes_are_rejected(self):
        with self.assertRaises(ValueError):
            plan_multiboard_partition([], [{"id": "a", "max_components": 0}])
        with self.assertRaises(ValueError):
            plan_multiboard_partition([{"refdes": "R1", "nodes": ["", "b"]}], [{"id": "a"}])

    def test_invalid_board_rejected(self):
        with self.assertRaises(ValueError):
            plan_multiboard_partition([{"refdes": "R1", "board": "missing", "nodes": ["a", "b"]}], [{"id": "main"}])

    def test_candidates_are_ranked(self):
        candidates = rank_partition_candidates([
            {"refdes": "V1", "nodes": ["out", "0"]},
            {"refdes": "R1", "nodes": ["out", "0"]},
        ], [{"id": "a"}, {"id": "b"}])
        self.assertEqual(candidates[0]["score"]["cost"], 0)
        self.assertEqual(len(candidates), 4)

    def test_candidate_ranking_prefers_feasible_partition(self):
        candidates = rank_partition_candidates([
            {"refdes": "V1", "nodes": ["out", "0"]},
            {"refdes": "R1", "nodes": ["out", "0"]},
        ], [
            {"id": "a", "max_connector_pins": 1},
            {"id": "b", "max_connector_pins": 1},
        ])
        self.assertTrue(candidates[0]["score"]["feasible"])
        self.assertEqual(candidates[0]["connector_count"], 0)
        self.assertFalse(candidates[-1]["score"]["feasible"])

    def test_fixed_component_board_is_preserved_during_enumeration(self):
        candidates = rank_partition_candidates([
            {"refdes": "V1", "board": "power", "nodes": ["out", "0"]},
            {"refdes": "R1", "nodes": ["out", "0"]},
        ], [{"id": "power"}, {"id": "signal"}])
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(item["component_assignment"]["V1"] == "power" for item in candidates))

    def test_invalid_fixed_component_board_is_rejected(self):
        with self.assertRaises(ValueError):
            rank_partition_candidates([
                {"refdes": "V1", "board": "missing", "nodes": ["out", "0"]},
            ], [{"id": "power"}, {"id": "signal"}])
