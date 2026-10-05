import unittest

from multisim_mcp.engineering_planner import (
    build_engineering_plan,
    select_multiboard_engineering_candidate,
)


class EngineeringMultiboardSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = build_engineering_plan({
            "schema_version": 1,
            "title": "two board divider",
            "application": "test",
            "boards": [{"id": "power"}, {"id": "signal"}],
            "components": [
                {"refdes": "V1", "nodes": ["bus", "0"]},
                {"refdes": "R1", "nodes": ["bus", "sense"]},
            ],
        })

    def test_selects_recommended_structurally_ready_candidate(self) -> None:
        selected = select_multiboard_engineering_candidate(self.plan)
        self.assertEqual(selected["state"], "selected")
        self.assertEqual(selected["source_plan_digest"], self.plan["plan_digest"])
        self.assertTrue(selected["candidate"]["structurally_ready"])
        self.assertEqual(len(selected["selection_digest"]), 64)

    def test_rejects_empty_board_candidate(self) -> None:
        with self.assertRaisesRegex(ValueError, "structurally ready"):
            select_multiboard_engineering_candidate(self.plan, 0)
