import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from multisim_mcp.engineering_planner import build_engineering_plan
from multisim_mcp.plan_actions import build_action_plan


class PlanActionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "x.ms14"
        self.source.write_bytes(b"native")
        self.plan = build_engineering_plan({
            "schema_version": 1, "title": "x", "application": "y",
            "experiments": [{"type": "tran", "commands": "tran 1u 1m", "outputs": ["V(out)"]}],
        })

    def build(self, plan):
        return build_action_plan(plan, project_path=str(self.source), output_directory=str(Path(self.tmp.name) / "out"))

    def test_preserves_actual_experiment_instead_of_hardcoding_op(self):
        actions = self.build(self.plan)
        self.assertEqual(actions["actions"][1]["commands"], "tran 1u 1m")
        self.assertEqual(actions["action_count"], 4)

    def test_tampering_with_plan_experiments_is_rejected(self):
        plan = deepcopy(self.plan)
        # Break any shared reference with request.experiments.
        plan["experiments"] = [{"type": "op"}]
        with self.assertRaises(ValueError):
            self.build(plan)

    def test_missing_experiments_or_multiboard_are_not_silently_executed(self):
        for extra in [{}, {"boards": [{"id": "a"}, {"id": "b"}]}]:
            plan = build_engineering_plan({"schema_version": 1, "title": "x", "application": "y", **extra})
            with self.assertRaises(ValueError):
                self.build(plan)
