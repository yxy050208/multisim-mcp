"""Versioned contracts for an ARTEX-style engineering Agent workflow.

This module describes orchestration state only.  It does not import MCP, a
model provider, Multisim, COM, or the UI.  A future Workbench, an ARTEX-style
task runner, the CLI, and MCP adapters can therefore share the same objects
without copying workflow semantics.

The contracts deliberately carry opaque artifact locators and approval
identities, never bearer tokens or arbitrary host paths.  They are a planning
and event boundary; executing a task still belongs to the existing application
services and approval gates.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Final, Mapping


AGENT_WORKFLOW_SCHEMA_VERSION: Final = 1
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_EVENT_ID = re.compile(r"^[0-9a-f]{32}$")
_RELATIVE_LOCATOR = re.compile(r"^(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$")

CHAIN_STATES: Final = (
    "planned",
    "ready",
    "running",
    "waiting_approval",
    "succeeded",
    "failed",
    "cancelled",
    "blocked",
)
TASK_STATES: Final = (
    "pending",
    "ready",
    "running",
    "waiting_approval",
    "succeeded",
    "failed",
    "skipped",
    "cancelled",
)
APPROVAL_STATES: Final = ("pending", "approved", "rejected", "expired")
EVENT_TYPES: Final = (
    "created",
    "task_ready",
    "task_started",
    "progress",
    "state_changed",
    "approval_required",
    "artifact_published",
    "completed",
    "failed",
)


def _text(value: object, field_name: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{field_name} is empty, too long, or contains NUL")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ValueError(f"{field_name} contains unsupported control characters")
    return value.strip()


def _identifier(value: object, field_name: str) -> str:
    normalized = _text(value, field_name, maximum=128)
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a stable identifier")
    return normalized


def _json_copy(value: object, field_name: str, *, maximum_bytes: int = 65_536) -> Any:
    """Validate and copy finite JSON data without retaining caller mutability."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError(f"{field_name} must be finite JSON data") from exc
    if len(encoded) > maximum_bytes:
        raise ValueError(f"{field_name} exceeds {maximum_bytes} bytes")
    try:
        return json.loads(encoded.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"{field_name} is not valid JSON") from exc


def _tuple_strings(value: object, field_name: str, *, maximum: int = 128) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be an array")
    if len(value) > maximum:
        raise ValueError(f"{field_name} contains too many entries")
    return tuple(_identifier(item, f"{field_name}[]") for item in value)


def _digest(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = _text(value, field_name, maximum=64).lower()
    if not _DIGEST.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return normalized


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """A bounded reference to an output that a UI or Agent can display."""

    artifact_id: str
    kind: str
    label: str
    locator: str | None = None
    media_type: str | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_id", _identifier(self.artifact_id, "artifact_id"))
        object.__setattr__(self, "kind", _identifier(self.kind, "artifact.kind"))
        object.__setattr__(self, "label", _text(self.label, "artifact.label", maximum=512))
        if self.locator is not None:
            locator = _text(self.locator, "artifact.locator", maximum=1024)
            if "://" not in locator and (
                locator.startswith(("/", "\\"))
                or "\\" in locator
                or not _RELATIVE_LOCATOR.fullmatch(locator)
                or any(part in {".", ".."} for part in locator.split("/"))
            ):
                raise ValueError("artifact.locator must be a relative locator or opaque URI")
            object.__setattr__(self, "locator", locator)
        if self.media_type is not None:
            object.__setattr__(self, "media_type", _text(self.media_type, "artifact.media_type", maximum=128))
        object.__setattr__(self, "sha256", _digest(self.sha256, "artifact.sha256"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_WORKFLOW_SCHEMA_VERSION,
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "label": self.label,
            "locator": self.locator,
            "media_type": self.media_type,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ArtifactRef":
        if not isinstance(value, Mapping):
            raise ValueError("ArtifactRef must be an object")
        allowed = {"schema_version", "artifact_id", "kind", "label", "locator", "media_type", "sha256"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"ArtifactRef contains unknown fields: {sorted(unknown)}")
        if value.get("schema_version") != AGENT_WORKFLOW_SCHEMA_VERSION:
            raise ValueError("ArtifactRef schema_version is unsupported")
        return cls(
            artifact_id=value.get("artifact_id", ""),
            kind=value.get("kind", ""),
            label=value.get("label", ""),
            locator=value.get("locator"),
            media_type=value.get("media_type"),
            sha256=value.get("sha256"),
        )


@dataclass(frozen=True, slots=True)
class ApprovalGate:
    """Approval state without exposing the bearer token used to authorize it."""

    gate_id: str
    status: str = "pending"
    reason: str = ""
    required: bool = True
    approval_id: str | None = None
    approval_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_id", _identifier(self.gate_id, "gate_id"))
        status = _identifier(self.status, "approval.status")
        if status not in APPROVAL_STATES:
            raise ValueError(f"unsupported approval status: {status}")
        object.__setattr__(self, "status", status)
        if not isinstance(self.required, bool):
            raise ValueError("approval.required must be boolean")
        object.__setattr__(self, "reason", _text(self.reason, "approval.reason", maximum=1024) if self.reason else "")
        if self.approval_id is not None:
            object.__setattr__(self, "approval_id", _identifier(self.approval_id, "approval.approval_id"))
        object.__setattr__(self, "approval_digest", _digest(self.approval_digest, "approval.approval_digest"))
        if status == "approved" and not self.approval_id:
            raise ValueError("approved approval requires approval_id")
        if status == "approved" and not self.approval_digest:
            raise ValueError("approved approval requires approval_digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_WORKFLOW_SCHEMA_VERSION,
            "gate_id": self.gate_id,
            "status": self.status,
            "reason": self.reason,
            "required": self.required,
            "approval_id": self.approval_id,
            "approval_digest": self.approval_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ApprovalGate":
        if not isinstance(value, Mapping):
            raise ValueError("ApprovalGate must be an object")
        allowed = {"schema_version", "gate_id", "status", "reason", "required", "approval_id", "approval_digest"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"ApprovalGate contains unknown fields: {sorted(unknown)}")
        if value.get("schema_version") != AGENT_WORKFLOW_SCHEMA_VERSION:
            raise ValueError("ApprovalGate schema_version is unsupported")
        return cls(
            gate_id=value.get("gate_id", ""),
            status=value.get("status", "pending"),
            reason=value.get("reason", ""),
            required=value.get("required", True),
            approval_id=value.get("approval_id"),
            approval_digest=value.get("approval_digest"),
        )


@dataclass(frozen=True, slots=True)
class AgentTask:
    """One bounded Agent action in an engineering task graph."""

    task_id: str
    task_type: str
    role: str
    title: str
    state: str = "pending"
    depends_on: tuple[str, ...] = ()
    inputs: Mapping[str, Any] = field(default_factory=dict)
    outputs: tuple[ArtifactRef, ...] = ()
    approval_gate: ApprovalGate | None = None
    model_profile: str | None = None
    attempts: int = 0
    max_attempts: int = 1
    retryable: bool = False
    progress: int = 0
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("task_id", "task_type", "role"):
            object.__setattr__(self, field_name, _identifier(getattr(self, field_name), f"task.{field_name}"))
        object.__setattr__(self, "title", _text(self.title, "task.title", maximum=512))
        state = _identifier(self.state, "task.state")
        if state not in TASK_STATES:
            raise ValueError(f"unsupported task state: {state}")
        object.__setattr__(self, "state", state)
        dependencies = _tuple_strings(self.depends_on, "task.depends_on")
        if self.task_id in dependencies:
            raise ValueError("task cannot depend on itself")
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("task.depends_on contains duplicates")
        object.__setattr__(self, "depends_on", dependencies)
        inputs = _json_copy(self.inputs, "task.inputs")
        if not isinstance(inputs, dict):
            raise ValueError("task.inputs must be an object")
        object.__setattr__(self, "inputs", inputs)
        outputs = tuple(self.outputs)
        if len(outputs) > 128 or any(not isinstance(item, ArtifactRef) for item in outputs):
            raise ValueError("task.outputs must contain at most 128 ArtifactRef objects")
        object.__setattr__(self, "outputs", outputs)
        if self.approval_gate is not None and not isinstance(self.approval_gate, ApprovalGate):
            raise ValueError("task.approval_gate must be ApprovalGate")
        if self.model_profile is not None:
            object.__setattr__(self, "model_profile", _identifier(self.model_profile, "task.model_profile"))
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int) or not 0 <= self.attempts <= 32:
            raise ValueError("task.attempts must be between 0 and 32")
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or not 1 <= self.max_attempts <= 32:
            raise ValueError("task.max_attempts must be between 1 and 32")
        if self.attempts > self.max_attempts:
            raise ValueError("task.attempts cannot exceed max_attempts")
        if not isinstance(self.retryable, bool):
            raise ValueError("task.retryable must be boolean")
        if isinstance(self.progress, bool) or not isinstance(self.progress, int) or not 0 <= self.progress <= 100:
            raise ValueError("task.progress must be between 0 and 100")
        for field_name in ("error_code", "error_message"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _text(value, f"task.{field_name}", maximum=1024))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_WORKFLOW_SCHEMA_VERSION,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "role": self.role,
            "title": self.title,
            "state": self.state,
            "depends_on": list(self.depends_on),
            "inputs": _json_copy(self.inputs, "task.inputs"),
            "outputs": [item.to_dict() for item in self.outputs],
            "approval_gate": self.approval_gate.to_dict() if self.approval_gate else None,
            "model_profile": self.model_profile,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "retryable": self.retryable,
            "progress": self.progress,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, value: object) -> "AgentTask":
        if not isinstance(value, Mapping):
            raise ValueError("AgentTask must be an object")
        allowed = {
            "schema_version", "task_id", "task_type", "role", "title", "state", "depends_on",
            "inputs", "outputs", "approval_gate", "model_profile", "attempts", "max_attempts",
            "retryable", "progress", "error_code", "error_message",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"AgentTask contains unknown fields: {sorted(unknown)}")
        if value.get("schema_version") != AGENT_WORKFLOW_SCHEMA_VERSION:
            raise ValueError("AgentTask schema_version is unsupported")
        raw_outputs = value.get("outputs", [])
        if not isinstance(raw_outputs, (list, tuple)):
            raise ValueError("AgentTask.outputs must be an array")
        gate = value.get("approval_gate")
        return cls(
            task_id=value.get("task_id", ""),
            task_type=value.get("task_type", ""),
            role=value.get("role", ""),
            title=value.get("title", ""),
            state=value.get("state", "pending"),
            depends_on=value.get("depends_on", []),
            inputs=value.get("inputs", {}),
            outputs=tuple(ArtifactRef.from_dict(item) for item in raw_outputs),
            approval_gate=ApprovalGate.from_dict(gate) if gate is not None else None,
            model_profile=value.get("model_profile"),
            attempts=value.get("attempts", 0),
            max_attempts=value.get("max_attempts", 1),
            retryable=value.get("retryable", False),
            progress=value.get("progress", 0),
            error_code=value.get("error_code"),
            error_message=value.get("error_message"),
        )


@dataclass(frozen=True, slots=True)
class TaskChain:
    """A validated DAG of AgentTask objects."""

    chain_id: str
    name: str
    tasks: tuple[AgentTask, ...]
    state: str = "planned"
    revision: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "chain_id", _identifier(self.chain_id, "chain_id"))
        object.__setattr__(self, "name", _text(self.name, "chain.name", maximum=512))
        state = _identifier(self.state, "chain.state")
        if state not in CHAIN_STATES:
            raise ValueError(f"unsupported chain state: {state}")
        object.__setattr__(self, "state", state)
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("chain.revision must be a positive integer")
        tasks = tuple(self.tasks)
        if not tasks or len(tasks) > 256 or any(not isinstance(item, AgentTask) for item in tasks):
            raise ValueError("chain.tasks must contain 1 to 256 AgentTask objects")
        object.__setattr__(self, "tasks", tasks)
        metadata = _json_copy(self.metadata, "chain.metadata")
        if not isinstance(metadata, dict):
            raise ValueError("chain.metadata must be an object")
        object.__setattr__(self, "metadata", metadata)
        self.validate()

    def validate(self) -> None:
        by_id = {task.task_id: task for task in self.tasks}
        if len(by_id) != len(self.tasks):
            raise ValueError("chain.tasks contains duplicate task_id values")
        for task in self.tasks:
            missing = sorted(set(task.depends_on) - set(by_id))
            if missing:
                raise ValueError(f"task {task.task_id} depends on missing tasks: {missing}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("chain.tasks contains a dependency cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in by_id[task_id].depends_on:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task in self.tasks:
            visit(task.task_id)

    def ready_task_ids(self) -> tuple[str, ...]:
        by_id = {task.task_id: task for task in self.tasks}
        return tuple(
            task.task_id
            for task in self.tasks
            if task.state in {"pending", "ready"}
            and all(by_id[dependency].state == "succeeded" for dependency in task.depends_on)
        )

    @property
    def digest(self) -> str:
        return _canonical_digest(self._payload_dict())

    def _payload_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_WORKFLOW_SCHEMA_VERSION,
            "chain_id": self.chain_id,
            "name": self.name,
            "tasks": [task.to_dict() for task in self.tasks],
            "state": self.state,
            "revision": self.revision,
            "metadata": _json_copy(self.metadata, "chain.metadata"),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload_dict(), "chain_digest": self.digest}

    @classmethod
    def from_dict(cls, value: object) -> "TaskChain":
        if not isinstance(value, Mapping):
            raise ValueError("TaskChain must be an object")
        allowed = {"schema_version", "chain_id", "name", "tasks", "state", "revision", "metadata", "chain_digest"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"TaskChain contains unknown fields: {sorted(unknown)}")
        if value.get("schema_version") != AGENT_WORKFLOW_SCHEMA_VERSION:
            raise ValueError("TaskChain schema_version is unsupported")
        raw_tasks = value.get("tasks", [])
        if not isinstance(raw_tasks, (list, tuple)):
            raise ValueError("TaskChain.tasks must be an array")
        chain = cls(
            chain_id=value.get("chain_id", ""),
            name=value.get("name", ""),
            tasks=tuple(AgentTask.from_dict(item) for item in raw_tasks),
            state=value.get("state", "planned"),
            revision=value.get("revision", 1),
            metadata=value.get("metadata", {}),
        )
        supplied_digest = value.get("chain_digest")
        if supplied_digest is not None and supplied_digest != chain.digest:
            raise ValueError("TaskChain.chain_digest does not match the chain")
        return chain


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """A bounded, deduplicable event for a task-chain UI or Agent client."""

    chain_id: str
    event_type: str
    state: str
    task_id: str | None = None
    progress: int = 0
    message: str = ""
    artifact_ids: tuple[str, ...] = ()
    sequence: int = 0
    updated_at: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    event_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "chain_id", _identifier(self.chain_id, "event.chain_id"))
        event_type = _identifier(self.event_type, "event.event_type")
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported event type: {event_type}")
        object.__setattr__(self, "event_type", event_type)
        state = _identifier(self.state, "event.state")
        if state not in CHAIN_STATES and state not in TASK_STATES:
            raise ValueError(f"unsupported event state: {state}")
        object.__setattr__(self, "state", state)
        if self.task_id is not None:
            object.__setattr__(self, "task_id", _identifier(self.task_id, "event.task_id"))
        if isinstance(self.progress, bool) or not isinstance(self.progress, int) or not 0 <= self.progress <= 100:
            raise ValueError("event.progress must be between 0 and 100")
        object.__setattr__(self, "message", _text(self.message, "event.message", maximum=2048) if self.message else "")
        object.__setattr__(self, "artifact_ids", _tuple_strings(self.artifact_ids, "event.artifact_ids", maximum=128))
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("event.sequence must be a non-negative integer")
        if self.updated_at is not None:
            object.__setattr__(self, "updated_at", _text(self.updated_at, "event.updated_at", maximum=64))
        details = _json_copy(self.details, "event.details", maximum_bytes=16_384)
        if not isinstance(details, dict):
            raise ValueError("event.details must be an object")
        object.__setattr__(self, "details", details)
        computed = _canonical_digest(self._identity_dict())[:32]
        if self.event_id is not None and not _EVENT_ID.fullmatch(self.event_id):
            raise ValueError("event.event_id must be a 32-character lowercase digest prefix")
        if self.event_id is not None and self.event_id != computed:
            raise ValueError("event.event_id does not match event contents")
        object.__setattr__(self, "event_id", computed)

    def _identity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_WORKFLOW_SCHEMA_VERSION,
            "chain_id": self.chain_id,
            "event_type": self.event_type,
            "state": self.state,
            "task_id": self.task_id,
            "progress": self.progress,
            "message": self.message,
            "artifact_ids": list(self.artifact_ids),
            "sequence": self.sequence,
            "updated_at": self.updated_at,
            "details": _json_copy(self.details, "event.details", maximum_bytes=16_384),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._identity_dict(), "event_id": self.event_id}

    @classmethod
    def from_dict(cls, value: object) -> "TaskEvent":
        if not isinstance(value, Mapping):
            raise ValueError("TaskEvent must be an object")
        allowed = {
            "schema_version", "chain_id", "event_type", "state", "task_id", "progress", "message",
            "artifact_ids", "sequence", "updated_at", "details", "event_id",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"TaskEvent contains unknown fields: {sorted(unknown)}")
        if value.get("schema_version") != AGENT_WORKFLOW_SCHEMA_VERSION:
            raise ValueError("TaskEvent schema_version is unsupported")
        return cls(
            chain_id=value.get("chain_id", ""),
            event_type=value.get("event_type", ""),
            state=value.get("state", ""),
            task_id=value.get("task_id"),
            progress=value.get("progress", 0),
            message=value.get("message", ""),
            artifact_ids=value.get("artifact_ids", []),
            sequence=value.get("sequence", 0),
            updated_at=value.get("updated_at"),
            details=value.get("details", {}),
            event_id=value.get("event_id"),
        )


def build_agent_workflow_capabilities() -> dict[str, Any]:
    """Return the stable discovery payload used by MCP and future UI clients."""

    return {
        "schema_version": AGENT_WORKFLOW_SCHEMA_VERSION,
        "contract": "multisim-mcp-agent-workflow",
        "objects": ["AgentTask", "TaskChain", "ArtifactRef", "ApprovalGate", "TaskEvent"],
        "chain_states": list(CHAIN_STATES),
        "task_states": list(TASK_STATES),
        "approval_states": list(APPROVAL_STATES),
        "event_types": list(EVENT_TYPES),
        "execution_boundary": {
            "direct_model_execution": False,
            "arbitrary_paths": False,
            "approval_required_for_writes": True,
        },
    }


__all__ = [
    "AGENT_WORKFLOW_SCHEMA_VERSION",
    "APPROVAL_STATES",
    "AgentTask",
    "ArtifactRef",
    "ApprovalGate",
    "CHAIN_STATES",
    "EVENT_TYPES",
    "TASK_STATES",
    "TaskChain",
    "TaskEvent",
    "build_agent_workflow_capabilities",
]
