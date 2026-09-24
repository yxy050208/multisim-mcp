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
