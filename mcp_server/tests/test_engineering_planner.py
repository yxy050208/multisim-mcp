import unittest

from multisim_mcp.engineering_planner import build_engineering_plan


class EngineeringPlannerTest(unittest.TestCase):
    def test_builds_auditable_plan_with_digest(self) -> None:
        plan = build_engineering_plan({
            "schema_version": 1,
            "title": "divider",
            "application": "sensor",
            "boards": [{"id": "power", "role": "primary"}],
        })
        self.assertEqual(plan["kind"], "multisim-mcp-engineering-plan")
        self.assertEqual(plan["boards"][0]["id"], "power")
        self.assertEqual(len(plan["plan_digest"]), 64)
