from __future__ import annotations

import json
import unittest

from multisim_mcp.agent_workflow import build_engineering_task_chain
from multisim_mcp.agent_workflow_contract import TaskChain


class AgentWorkflowPlannerTest(unittest.TestCase):
    def test_builds_reviewable_chain_without_execution(self) -> None:
        chain = build_engineering_task_chain(
            "设计一个 5V 传感器低通前端，截止频率 1kHz，输出误差小于 1%",
            model_profiles={"design": "qwen-planner", "experiment": "native-verifier"},
        )
        self.assertEqual(chain.state, "planned")
        self.assertEqual(chain.ready_task_ids(), ("requirements",))
        self.assertEqual(chain.metadata["execution_enabled"], False)
        self.assertEqual(chain.tasks[-1].task_id, "report")
        self.assertEqual(chain.tasks[-2].task_id, "optimization")
        self.assertEqual(chain.tasks[3].approval_gate.status, "pending")
        self.assertEqual(chain.tasks[5].approval_gate.status, "pending")
        restored = TaskChain.from_dict(json.loads(json.dumps(chain.to_dict(), ensure_ascii=False)))
        self.assertEqual(restored.digest, chain.digest)

    def test_can_omit_optimization_and_is_deterministic(self) -> None:
        first = build_engineering_task_chain("RC low pass", include_optimization=False)
        second = build_engineering_task_chain("RC low pass", include_optimization=False)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual([task.task_id for task in first.tasks], [
            "requirements", "design-plan", "component-resolution", "compile-netlist",
            "layout", "native-experiment", "report",
        ])
        self.assertEqual(first.tasks[-1].depends_on, ("native-experiment",))

    def test_rejects_unbounded_inputs_and_invalid_profiles(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-empty"):
            build_engineering_task_chain(" ")
        with self.assertRaisesRegex(ValueError, "size limit"):
            build_engineering_task_chain("x" * 12_001)
        with self.assertRaisesRegex(ValueError, "model_profiles.design"):
            build_engineering_task_chain("RC", model_profiles={"design": ""})


if __name__ == "__main__":
    unittest.main()
