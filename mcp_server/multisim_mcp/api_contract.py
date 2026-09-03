"""Stable, transport-neutral contract for Agent and Workbench clients.

The MCP surface remains the compatibility boundary.  This module only describes
the machine-readable objects exchanged by the existing tools, so a future UI or
another Agent adapter can depend on a small versioned contract without copying
business logic from :mod:`server` or :mod:`cli`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final


API_CONTRACT_NAME: Final = "multisim-mcp-agent-api"
API_CONTRACT_VERSION: Final = "1"
CAPABILITIES_SCHEMA_VERSION: Final = 1
ERROR_SCHEMA_VERSION: Final = 1
TASK_EVENT_SCHEMA_VERSION: Final = 1

ERROR_CODES: Final = (
    "already_exists",
    "backend_unavailable",
    "internal_error",
    "invalid_input",
    "io_error",
    "not_found",
    "permission_denied",
    "runtime_error",
    "timeout",
)

TASK_STATES: Final = (
    "queued",
    "running",
    "cancelling",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
)

MCP_TASK_STATUS: Final = {
    "queued": "working",
    "running": "working",
    "cancelling": "working",
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "timed_out": "failed",
}

FEATURES: Final = (
    "design_diagnosis",
    "design_correction",
    "design_optimization",
    "correction_benchmarks",
    "durable_jobs",
    "experiment_resources",
    "structured_errors",
)


def build_capabilities(
    *,
    server_version: str,
    tool_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build deterministic capabilities for Agents and future UI clients.

    The output deliberately contains no host paths, timestamps, process IDs, or
    backend probe results.  Those values belong to ``runtime_status`` itself;
    this object is safe to cache and compare across calls.
    """

    profile = dict(tool_profile or {})
    profile_name = str(profile.get("name") or "full")
    try:
        tool_count = int(profile.get("tool_count", 0))
    except (TypeError, ValueError):
        tool_count = 0
    available_profiles = profile.get("available_profiles", [])
    if not isinstance(available_profiles, list):
        available_profiles = list(available_profiles) if isinstance(available_profiles, tuple) else []
    return {
        "schema_version": CAPABILITIES_SCHEMA_VERSION,
        "api_name": API_CONTRACT_NAME,
        "api_version": API_CONTRACT_VERSION,
        "server_version": str(server_version),
        "tool_profile": {
            "name": profile_name,
            "tool_count": tool_count,
            "available_profiles": [str(item) for item in available_profiles],
        },
        "features": list(FEATURES),
        "errors": {
            "schema_version": ERROR_SCHEMA_VERSION,
            "codes": list(ERROR_CODES),
            "retryable_codes": ["backend_unavailable", "io_error", "timeout"],
        },
        "tasks": {
            "schema_version": TASK_EVENT_SCHEMA_VERSION,
            "status_uri_template": "multisim://jobs/{job_id}",
            "states": list(TASK_STATES),
            "mcp_task_status": dict(MCP_TASK_STATUS),
            "event_types": ["created", "progress", "state_changed", "completed"],
        },
    }


def classify_error(exc: BaseException) -> tuple[str, bool]:
    """Return a stable error code and whether retrying may change the result."""

    if isinstance(exc, (FileNotFoundError, KeyError)):
        return "not_found", False
    if isinstance(exc, FileExistsError):
        return "already_exists", False
    if isinstance(exc, PermissionError):
        return "permission_denied", False
    if isinstance(exc, TimeoutError):
        return "timeout", True
    if isinstance(exc, ConnectionError):
        return "backend_unavailable", True
    if isinstance(exc, (ValueError, TypeError, UnicodeError, json.JSONDecodeError)):
        return "invalid_input", False
    if isinstance(exc, OSError):
        return "io_error", False
    if isinstance(exc, RuntimeError):
        return "runtime_error", False
    return "internal_error", False


def _json_safe(value: Any) -> Any:
    """Keep optional details bounded to JSON-compatible values."""

    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)
    return value


def build_error(
    exc: BaseException,
    *,
    command: str | None = None,
    details: Any = None,
) -> dict[str, Any]:
    """Build a redacted, stable error object without tracebacks or secrets."""

    code, retryable = classify_error(exc)
    error: dict[str, Any] = {
        "schema_version": ERROR_SCHEMA_VERSION,
        "code": code,
        "type": type(exc).__name__,
        "message": str(exc),
        "retryable": retryable,
    }
    if command:
        error["command"] = command
    if details is not None:
        error["details"] = _json_safe(details)
    return error


def build_error_envelope(
    exc: BaseException,
    *,
    command: str,
    schema_version: int = 1,
    details: Any = None,
) -> dict[str, Any]:
    """Build the common top-level shape used by JSON CLI commands."""

    return {
        "schema_version": schema_version,
        "command": command,
        "success": False,
        "error": build_error(exc, command=command, details=details),
    }


__all__ = [
    "API_CONTRACT_NAME",
    "API_CONTRACT_VERSION",
    "CAPABILITIES_SCHEMA_VERSION",
    "ERROR_CODES",
    "ERROR_SCHEMA_VERSION",
    "FEATURES",
    "MCP_TASK_STATUS",
    "TASK_EVENT_SCHEMA_VERSION",
    "TASK_STATES",
    "build_capabilities",
    "build_error",
    "build_error_envelope",
    "classify_error",
]
