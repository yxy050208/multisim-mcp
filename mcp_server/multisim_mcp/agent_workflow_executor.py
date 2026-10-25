"""Approval-aware execution state machine for engineering Agent task chains.

The executor is deliberately transport- and backend-neutral.  Callers inject
handlers for task types; handlers may delegate to existing application
services, while this module owns dependency checks, approval gates, retries,
bounded context, and versioned task events.  No handler is implied by a model
name, and a missing handler fails closed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from .agent_workflow_contract import (
    AgentTask,
    ApprovalGate,
    CHAIN_STATES,
    TaskChain,
    TaskEvent,
)
from .design_plans import plan_design_options


MAX_WORKFLOW_STEPS = 256
MAX_WORKFLOW_CONTEXT_BYTES = 262_144
TaskHandler = Callable[[AgentTask, Mapping[str, Any]], Mapping[str, Any]]
EventSink = Callable[[TaskEvent], None]


class AgentWorkflowError(RuntimeError):
    """Base error for task-chain execution state violations."""


def _copy_json(value: object, field_name: str, *, maximum_bytes: int = MAX_WORKFLOW_CONTEXT_BYTES) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise AgentWorkflowError(f"{field_name} must be finite JSON data") from exc
    if len(encoded) > maximum_bytes:
        raise AgentWorkflowError(f"{field_name} exceeds {maximum_bytes} bytes")
    return json.loads(encoded.decode("utf-8"))


def _require_digest(value: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("approval_digest must be a lowercase SHA-256 digest")
    return value


class AgentWorkflowExecutor:
    """Run one task at a time while preserving an auditable task graph."""

    def __init__(
        self,
        chain: TaskChain,
        handlers: Mapping[str, TaskHandler],
        *,
        initial_context: Mapping[str, Any] | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        if not isinstance(chain, TaskChain):
            raise ValueError("chain must be a TaskChain")
        if not isinstance(handlers, Mapping):
            raise ValueError("handlers must be an object")
        for name, handler in handlers.items():
            if not isinstance(name, str) or not name.strip() or not callable(handler):
                raise ValueError("handlers must map task types to callables")
        if event_sink is not None and not callable(event_sink):
            raise ValueError("event_sink must be callable")
        self.chain = chain
        self.handlers = dict(handlers)
        self.context: dict[str, Any] = _copy_json(initial_context or {}, "initial_context")
        if not isinstance(self.context, dict):
            raise ValueError("initial_context must be an object")
        self._events: list[TaskEvent] = []
        self._sequence = 0
        self._event_sink = event_sink

    @property
    def events(self) -> tuple[TaskEvent, ...]:
        return tuple(self._events)

    def _emit(
        self,
        event_type: str,
        *,
        task: AgentTask | None = None,
        message: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> TaskEvent:
        self._sequence += 1
        event = TaskEvent(
            chain_id=self.chain.chain_id,
            event_type=event_type,
            state=task.state if task is not None else self.chain.state,
            task_id=task.task_id if task is not None else None,
            progress=task.progress if task is not None else 0,
            message=message,
            artifact_ids=tuple(item.artifact_id for item in task.outputs) if task is not None else (),
            sequence=self._sequence,
            details=details or {},
        )
        self._events.append(event)
        if self._event_sink is not None:
            self._event_sink(event)
        return event

    def _derive_chain_state(self, tasks: tuple[AgentTask, ...]) -> str:
        states = {task.state for task in tasks}
        if "failed" in states:
            return "failed"
        if states and states.issubset({"succeeded", "skipped"}):
            return "succeeded"
        if "waiting_approval" in states:
            return "waiting_approval"
        if "running" in states:
            return "running"
        if "ready" in states:
            return "ready"
        if any(state == "succeeded" for state in states):
            return "ready"
        return "planned"

    def _replace_task(self, task_id: str, **changes: Any) -> AgentTask:
        tasks = tuple(
            replace(task, **changes) if task.task_id == task_id else task
            for task in self.chain.tasks
        )
        updated = next(task for task in tasks if task.task_id == task_id)
        self.chain = TaskChain(
            chain_id=self.chain.chain_id,
            name=self.chain.name,
            tasks=tasks,
            state=self._derive_chain_state(tasks),
            revision=self.chain.revision + 1,
            metadata=self.chain.metadata,
        )
        return updated

    def _task(self, task_id: str) -> AgentTask:
        for task in self.chain.tasks:
            if task.task_id == task_id:
                return task
        raise AgentWorkflowError(f"unknown task: {task_id}")

    def _ready_task(self) -> AgentTask | None:
        by_id = {task.task_id: task for task in self.chain.tasks}
        for task in self.chain.tasks:
            if task.state not in {"pending", "ready"}:
                continue
            if all(by_id[dependency].state == "succeeded" for dependency in task.depends_on):
                return task
        return None

    def approve_gate(self, gate_id: str, approval_id: str, approval_digest: str) -> TaskEvent:
        """Approve one pending gate; the caller owns the external approval process."""

        if not isinstance(gate_id, str) or not gate_id.strip():
            raise ValueError("gate_id must be a non-empty string")
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("approval_id must be a non-empty string")
        digest = _require_digest(approval_digest)
        for task in self.chain.tasks:
            gate = task.approval_gate
            if gate is None or gate.gate_id != gate_id:
                continue
            if gate.status != "pending":
                raise AgentWorkflowError(f"approval gate is already {gate.status}: {gate_id}")
            approved = ApprovalGate(
                gate_id=gate.gate_id,
                status="approved",
                reason=gate.reason,
                required=gate.required,
                approval_id=approval_id,
                approval_digest=digest,
            )
            updated = self._replace_task(task.task_id, approval_gate=approved, state="ready")
            return self._emit(
                "state_changed",
                task=updated,
                message="Approval gate accepted; task is ready",
                details={"approval_id": approval_id, "approval_digest": digest},
            )
        raise AgentWorkflowError(f"approval gate not found: {gate_id}")

    def step(self) -> TaskEvent | None:
        """Execute the first ready task, or emit an approval event when blocked."""

        if self.chain.state in {"failed", "succeeded", "cancelled", "blocked"}:
            return None
        task = self._ready_task()
        if task is None:
            return None
        gate = task.approval_gate
        if gate is not None and gate.required and gate.status != "approved":
            updated = self._replace_task(task.task_id, state="waiting_approval")
            return self._emit(
                "approval_required",
                task=updated,
                message=gate.reason or "Explicit approval is required before execution",
                details={"gate_id": gate.gate_id, "approval_status": gate.status},
            )

        handler = self.handlers.get(task.task_type)
        if handler is None:
            failed = self._replace_task(
                task.task_id,
                state="failed",
                error_code="handler_unavailable",
                error_message=f"no handler registered for task type {task.task_type}",
            )
            return self._emit(
                "failed",
                task=failed,
                message=failed.error_message or "Task handler is unavailable",
                details={"error_code": failed.error_code},
            )

        running = self._replace_task(task.task_id, state="running", attempts=task.attempts + 1)
        self._emit("task_started", task=running, message=running.title)
        try:
            result = handler(running, _copy_json(self.context, "workflow.context"))
            if not isinstance(result, Mapping):
                raise AgentWorkflowError("task handler must return an object")
            safe_result = _copy_json(result, f"task {running.task_id} result")
            if not isinstance(safe_result, dict):
                raise AgentWorkflowError("task handler result must be an object")
        except Exception as exc:
            retry = running.retryable and running.attempts < running.max_attempts
            failed = self._replace_task(
                running.task_id,
                state="ready" if retry else "failed",
                progress=0,
                error_code="task_failed",
                error_message=str(exc)[:1024] or type(exc).__name__,
            )
            return self._emit(
                "failed",
                task=failed,
                message=failed.error_message or "Task failed",
                details={"retrying": retry, "attempts": failed.attempts},
            )

        self.context[running.task_id] = safe_result
        succeeded = self._replace_task(
            running.task_id,
            state="succeeded",
            progress=100,
            error_code=None,
            error_message=None,
        )
        return self._emit("completed", task=succeeded, message=succeeded.title)

    def run_until_blocked(self, *, max_steps: int = MAX_WORKFLOW_STEPS) -> tuple[TaskEvent, ...]:
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 1 <= max_steps <= MAX_WORKFLOW_STEPS:
            raise ValueError(f"max_steps must be between 1 and {MAX_WORKFLOW_STEPS}")
        before = len(self._events)
        for _ in range(max_steps):
            event = self.step()
            if event is None or self.chain.state in {"failed", "succeeded", "cancelled", "blocked"}:
                break
        return tuple(self._events[before:])

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "chain": self.chain.to_dict(),
            "context": _copy_json(self.context, "workflow.context"),
            "events": [event.to_dict() for event in self._events],
            "execution_started": bool(self._events),
        }


def _handle_requirement_review(task: AgentTask, _context: Mapping[str, Any]) -> Mapping[str, Any]:
    requirements = task.inputs.get("requirements")
    if not isinstance(requirements, str) or not requirements.strip():
        raise ValueError("requirements task has no requirement text")
    return {
        "status": "reviewed",
        "requirements": requirements,
        "requirements_sha256": hashlib.sha256(requirements.encode("utf-8")).hexdigest(),
        "execution_started": False,
    }


def _handle_design_plan(_task: AgentTask, context: Mapping[str, Any]) -> Mapping[str, Any]:
    reviewed = context.get("requirements")
    if not isinstance(reviewed, Mapping) or not isinstance(reviewed.get("requirements"), str):
        raise ValueError("design plan requires a completed requirement review")
    plan = plan_design_options(str(reviewed["requirements"]))
    return {"status": "planned", "plan": plan, "execution_started": False}


def default_engineering_handlers() -> dict[str, TaskHandler]:
    """Return the first two safe handlers backed by existing planning services."""

    return {
        "requirement_review": _handle_requirement_review,
        "design_plan": _handle_design_plan,
    }


__all__ = [
    "AgentWorkflowError",
    "AgentWorkflowExecutor",
    "MAX_WORKFLOW_CONTEXT_BYTES",
    "MAX_WORKFLOW_STEPS",
    "default_engineering_handlers",
]
