from __future__ import annotations

import json
import unittest

from multisim_mcp.agent_workflow_contract import (
    AgentTask,
    ArtifactRef,
    ApprovalGate,
    TaskChain,
    TaskEvent,
    build_agent_workflow_capabilities,
)


class AgentWorkflowContractTest(unittest.TestCase):
    def _chain(self) -> TaskChain:
        schematic = ArtifactRef(
            artifact_id="artifact-schematic",
            kind="schematic",
            label="Generated schematic",
            locator="artifacts/schematic.ms14",
            media_type="application/octet-stream",
        )
        plan = AgentTask(
            task_id="plan",
            task_type="design_plan",
            role="planner",
            title="Plan the circuit",
            state="succeeded",
            inputs={"requirements": "RC low-pass"},
        )
        build = AgentTask(
            task_id="build",
            task_type="schematic",
            role="builder",
            title="Build schematic",
            depends_on=("plan",),
            outputs=(schematic,),
            approval_gate=ApprovalGate(gate_id="schematic-review", reason="Review before writing"),
            model_profile="local-qwen",
            retryable=True,
            max_attempts=3,
        )
        return TaskChain(
            chain_id="chain-demo",
            name="RC engineering demo",
            tasks=(plan, build),
            metadata={"domain": "analog"},
        )

    def test_round_trip_and_ready_tasks(self) -> None:
        chain = self._chain()
        self.assertEqual(chain.ready_task_ids(), ("build",))
        payload = chain.to_dict()
        self.assertEqual(payload["chain_digest"], chain.digest)
        restored = TaskChain.from_dict(json.loads(json.dumps(payload, ensure_ascii=False)))
        self.assertEqual(restored, chain)
        self.assertEqual(restored.digest, chain.digest)

    def test_missing_dependency_and_cycle_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing tasks"):
            TaskChain(
                chain_id="chain-missing",
                name="missing",
                tasks=(AgentTask("a", "plan", "planner", "A", depends_on=("b",)),),
            )
        a = AgentTask("a", "plan", "planner", "A", depends_on=("b",))
        b = AgentTask("b", "plan", "planner", "B", depends_on=("a",))
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            TaskChain(chain_id="chain-cycle", name="cycle", tasks=(a, b))

    def test_approval_and_artifact_boundaries(self) -> None:
        with self.assertRaisesRegex(ValueError, "approved approval requires approval_id"):
            ApprovalGate("gate", status="approved")
        with self.assertRaisesRegex(ValueError, "relative locator"):
            ArtifactRef("a", "plot", "Plot", locator="C:/secret/result.csv")
        with self.assertRaisesRegex(ValueError, "relative locator"):
            ArtifactRef("a", "plot", "Plot", locator="../result.csv")

    def test_event_is_deduplicable_and_tampering_is_rejected(self) -> None:
        event = TaskEvent(
            chain_id="chain-demo",
            event_type="progress",
            state="running",
            task_id="build",
            progress=42,
            message="building",
            sequence=3,
            details={"backend": "multisim"},
        )
        restored = TaskEvent.from_dict(json.loads(json.dumps(event.to_dict())))
        self.assertEqual(restored, event)
        tampered = event.to_dict()
        tampered["progress"] = 43
        with self.assertRaisesRegex(ValueError, "event_id"):
            TaskEvent.from_dict(tampered)

    def test_capabilities_are_json_safe(self) -> None:
        capabilities = build_agent_workflow_capabilities()
        self.assertIn("TaskChain", capabilities["objects"])
        self.assertFalse(capabilities["execution_boundary"]["direct_model_execution"])
        json.dumps(capabilities, ensure_ascii=False, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
