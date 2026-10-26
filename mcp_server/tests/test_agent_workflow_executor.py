from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace

from multisim_mcp.agent_workflow import build_engineering_task_chain
from multisim_mcp.agent_workflow_executor import (
    AgentWorkflowError,
    AgentWorkflowExecutor,
    default_engineering_handlers,
)


class AgentWorkflowExecutorTest(unittest.TestCase):
    def test_default_handlers_execute_requirement_and_plan_only(self) -> None:
        chain = build_engineering_task_chain("设计 RC 低通滤波器", include_optimization=False)
        executor = AgentWorkflowExecutor(chain, default_engineering_handlers())
        events = executor.run_until_blocked(max_steps=2)
        self.assertEqual([event.event_type for event in events], [
            "task_started", "completed", "task_started", "completed",
        ])
        self.assertEqual(executor.chain.tasks[0].state, "succeeded")
        self.assertEqual(executor.chain.tasks[1].state, "succeeded")
        self.assertEqual(executor.chain.state, "ready")
        self.assertIn("plan", executor.context["design-plan"])
        self.assertEqual(executor.snapshot()["execution_started"], True)

    def test_approval_blocks_and_then_releases_task(self) -> None:
        chain = build_engineering_task_chain("RC low pass", include_optimization=False)
        handlers = {
            task.task_type: (lambda _task, _context: {})
            for task in chain.tasks
        }
        executor = AgentWorkflowExecutor(chain, handlers)
        events = executor.run_until_blocked(max_steps=20)
        self.assertEqual(executor.chain.state, "waiting_approval")
        self.assertIn("approval_required", [event.event_type for event in events])
        self.assertEqual(
            next(task for task in executor.chain.tasks if task.task_id == "compile-netlist").state,
            "waiting_approval",
        )
        approval_digest = hashlib.sha256(b"netlist-review").hexdigest()
        executor.approve_gate("netlist-review", "approval-netlist-1", approval_digest)
        events = executor.run_until_blocked(max_steps=20)
        self.assertEqual(executor.chain.state, "waiting_approval")
        self.assertIn("native-experiment-review", [
            task.approval_gate.gate_id
            for task in executor.chain.tasks
            if task.approval_gate and task.approval_gate.status == "pending"
        ])
        self.assertIn("approval_required", [event.event_type for event in events])

    def test_missing_handler_fails_closed(self) -> None:
        chain = build_engineering_task_chain("RC", include_optimization=False)
        executor = AgentWorkflowExecutor(chain, {})
        executor.step()
        self.assertEqual(executor.chain.state, "failed")
        task = executor.chain.tasks[0]
        self.assertEqual(task.error_code, "handler_unavailable")

    def test_retryable_handler_is_retried_with_bounded_attempts(self) -> None:
        source = build_engineering_task_chain("RC", include_optimization=False)
        chain = type(source)(
            chain_id=source.chain_id,
            name=source.name,
            tasks=(replace(source.tasks[0], retryable=True, max_attempts=2), *source.tasks[1:]),
            state=source.state,
            revision=source.revision,
            metadata=source.metadata,
        )
        calls = {"count": 0}

        def fail_once(_task, _context):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("transient")
            return {"ok": True}

        executor = AgentWorkflowExecutor(
            chain,
            {"requirement_review": fail_once},
        )
        executor.step()
        self.assertEqual(executor.chain.state, "ready")
        self.assertEqual(executor.chain.tasks[0].attempts, 1)
        executor.step()
        self.assertEqual(executor.chain.tasks[0].state, "succeeded")
        self.assertEqual(calls["count"], 2)

    def test_invalid_approval_is_rejected(self) -> None:
        chain = build_engineering_task_chain("RC")
        executor = AgentWorkflowExecutor(chain, {})
        with self.assertRaises(AgentWorkflowError):
            executor.approve_gate("missing", "approval", "0" * 64)


if __name__ == "__main__":
    unittest.main()
