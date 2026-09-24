import unittest

from multisim_mcp.engineering_run import build_engineering_run


class EngineeringRunTest(unittest.TestCase):
    def test_builds_completed_auditable_record(self) -> None:
        result = build_engineering_run(
            request={"title": "x"},
            plan={"plan_digest": "a"},
            action_plan={"action_count": 1},
            runtime={"version": "Multisim 14.3"},
            results={"success": True},
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["run_digest"]), 64)
