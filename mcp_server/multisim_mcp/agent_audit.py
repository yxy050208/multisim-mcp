"""Privacy-bounded audit records for local model/EDA agent runs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Final


AGENT_AUDIT_SCHEMA_VERSION: Final = 1
MAX_AUDIT_EVENTS: Final = 512
MAX_AUDIT_EVENT_BYTES: Final = 64 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _json_value(value: Any, path: str = "value", depth: int = 0) -> Any:
    if depth > 24:
        raise ValueError(f"{path} exceeds the nesting limit")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{path} keys must be non-empty strings")
            result[key] = _json_value(item, f"{path}.{key}", depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(item, f"{path}[]", depth + 1) for item in value]
    raise ValueError(f"{path} must contain only JSON-compatible values")


def _canonical_json(value: Any) -> bytes:
    normalized = _json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def text_fingerprint(value: str) -> dict[str, Any]:
    """Describe text without retaining its content."""
    if not isinstance(value, str):
        raise ValueError("fingerprinted value must be text")
    encoded = value.encode("utf-8")
    return {
        "characters": len(value),
        "utf8_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def validate_agent_audit_output(path: str, *, overwrite: bool = False) -> Path:
    """Preflight one explicit audit destination without creating it."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("audit output path must not be empty")
    if not isinstance(overwrite, bool):
        raise ValueError("overwrite must be a boolean")
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise ValueError("audit output must not be a symbolic link")
    output = unresolved.resolve()
    if output == Path(output.anchor):
        raise ValueError("audit output must not be a filesystem root")
    if output.exists():
        if not output.is_file():
            raise ValueError("audit output must be a regular file")
        if not overwrite:
            raise FileExistsError(
                f"audit output already exists; pass --audit-overwrite: {output}"
            )
    return output


class AgentAuditTrail:
    """Collect a bounded event stream and publish it as one atomic JSON file."""

    def __init__(self, command: str, context: Mapping[str, Any]) -> None:
        if not isinstance(command, str) or not command.strip() or len(command) > 128:
            raise ValueError("audit command must be bounded non-empty text")
        self.run_id = f"audit-{uuid.uuid4().hex}"
        self.command = command.strip()
        self.context = _json_value(context, "context")
        self.started_at = _utc_now()
        self._started_monotonic = monotonic()
        self._events: list[dict[str, Any]] = []
        self._status = "running"
        self._completed_at: str | None = None
        self._duration_ms: int | None = None
        self._summary: dict[str, Any] | None = None
        self._error: dict[str, Any] | None = None

    @property
    def event_count(self) -> int:
        return len(self._events)

    def record(self, event_type: str, details: Mapping[str, Any]) -> None:
        if self._status != "running":
            raise RuntimeError("cannot append to a finalized audit trail")
        if (
            not isinstance(event_type, str)
            or not event_type.strip()
            or len(event_type) > 128
        ):
            raise ValueError("audit event type must be bounded non-empty text")
        if len(self._events) >= MAX_AUDIT_EVENTS:
            raise RuntimeError("agent audit event limit exceeded")
        normalized = _json_value(details, "event.details")
        event = {
            "sequence": len(self._events) + 1,
            "timestamp": _utc_now(),
            "elapsed_ms": max(0, round((monotonic() - self._started_monotonic) * 1000)),
            "type": event_type.strip(),
            "details": normalized,
        }
        if len(_canonical_json(event)) > MAX_AUDIT_EVENT_BYTES:
            raise ValueError("agent audit event exceeds 64 KiB")
        self._events.append(event)

    def succeed(self, summary: Mapping[str, Any]) -> None:
        self._finalize("succeeded", summary=summary)

    def fail(self, error: BaseException) -> None:
        if not isinstance(error, BaseException):
            raise ValueError("audit failure requires an exception")
        message = str(error).replace("\x00", "")[:1000]
        self._finalize(
            "failed",
            error={
                "type": type(error).__name__,
                "message_recorded": False,
                "message": text_fingerprint(message),
            },
        )

    def _finalize(
        self,
        status: str,
        *,
        summary: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        if self._status != "running":
            raise RuntimeError("audit trail is already finalized")
        if status not in {"succeeded", "failed"}:
            raise ValueError("audit status is invalid")
        self._status = status
        self._completed_at = _utc_now()
        self._duration_ms = max(
            0, round((monotonic() - self._started_monotonic) * 1000)
        )
        self._summary = (
            _json_value(summary, "summary") if summary is not None else None
        )
        self._error = dict(error) if error is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_AUDIT_SCHEMA_VERSION,
            "kind": "multisim-mcp-agent-audit",
            "run_id": self.run_id,
            "command": self.command,
            "status": self._status,
            "started_at": self.started_at,
            "completed_at": self._completed_at,
            "duration_ms": (
                self._duration_ms
                if self._duration_ms is not None
                else max(0, round((monotonic() - self._started_monotonic) * 1000))
            ),
            "context": self.context,
            "privacy": {
                "prompt_content_recorded": False,
                "assistant_content_recorded": False,
                "reasoning_content_recorded": False,
                "tool_result_content_recorded": False,
                "validated_tool_arguments_recorded": True,
                "credential_values_recorded": False,
            },
            "event_count": len(self._events),
            "events": list(self._events),
            "summary": self._summary,
            "error": self._error,
        }

    def write(self, path: str, *, overwrite: bool = False) -> dict[str, Any]:
        if self._status == "running":
            raise RuntimeError("cannot write an unfinished audit trail")
        output = validate_agent_audit_output(path, overwrite=overwrite)
        output.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            self.to_dict(), ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True
        ) + "\n"
        encoded = content.encode("utf-8")
        temporary = output.parent / f".{output.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if overwrite:
                os.replace(temporary, output)
            else:
                os.link(temporary, output)
                temporary.unlink()
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "path": str(output),
            "run_id": self.run_id,
            "status": self._status,
            "event_count": len(self._events),
            "size": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "content_recorded": False,
        }


__all__ = [
    "AGENT_AUDIT_SCHEMA_VERSION",
    "AgentAuditTrail",
    "MAX_AUDIT_EVENTS",
    "text_fingerprint",
    "validate_agent_audit_output",
]
