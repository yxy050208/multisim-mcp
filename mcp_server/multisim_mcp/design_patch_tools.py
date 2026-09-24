"""In-memory, read-only previews for bounded :class:`DesignPatch` proposals.

The preview tool validates one model-authored patch against a fixed immutable
``CircuitDesign``, constructs a candidate only in memory, derives an explicit
inverse patch, and compares deterministic structural diagnostics.  It never
writes a file, updates the source design, calls an EDA backend, or approves the
candidate for simulation or manufacture.
"""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Mapping
from typing import Any, Final

from .agent_runtime import ToolBinding
from .design_patch_service import (
    MAX_DESIGN_PATCH_OPERATIONS,
    prepare_design_patch,
    validate_design_patch,
)
from .eda_agent_tools import run_readonly_structural_checks
from .eda_core import CircuitDesign, PatchOperation
from .model_provider import ModelCancelled, ToolDefinition


DESIGN_PATCH_PREVIEW_SCHEMA_VERSION: Final = 1
MAX_PREVIEW_OPERATIONS: Final = MAX_DESIGN_PATCH_OPERATIONS
MAX_CAPTURED_PREVIEWS: Final = 16
# Leave headroom for the audit event envelope, whose complete event cap is 64 KiB.
MAX_PATCH_ARGUMENT_BYTES: Final = 48 * 1024
MAX_PATCH_PREVIEW_BYTES: Final = 256 * 1024

def _check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ModelCancelled("design patch preview was cancelled")


def _normalize_json(value: object, depth: int = 0) -> Any:
    if depth > 24:
        raise ValueError("patch exceeds the JSON nesting limit")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("patch must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("patch keys must be non-empty strings")
            result[key] = _normalize_json(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item, depth + 1) for item in value]
    raise ValueError("patch must contain finite JSON values")


def _plain_json(value: object, maximum_bytes: int = MAX_PATCH_PREVIEW_BYTES) -> Any:
    try:
        normalized = _normalize_json(value)
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("patch must contain finite JSON values") from exc
    if len(encoded) > maximum_bytes:
        raise ValueError("patch preview JSON exceeds its size limit")
    return json.loads(encoded.decode("utf-8"))


def _change_view(operation: PatchOperation) -> dict[str, Any]:
    value = operation.to_dict()
    return {
        "operation": value["operation"],
        "target": value["target"],
        "before": value["before"],
        "after": value["after"],
        "reason": value["reason"],
    }


class ReadOnlyDesignPatchPreview:
    """Validate and preview patches against one captured immutable design."""

    def __init__(self, design: CircuitDesign) -> None:
        if not isinstance(design, CircuitDesign):
            raise ValueError("design must be CircuitDesign")
        self.design = design
        self._lock = threading.RLock()
        self._captured: list[dict[str, Any]] = []

    def bindings(self) -> tuple[ToolBinding, ...]:
        return (
            ToolBinding(
                ToolDefinition(
                    "eda_preview_design_patch",
                    (
                        "Validate and preview one bounded DesignPatch against the fixed "
                        "design in memory. It returns an inverse patch and structural "
                        "diagnostic deltas but never persists or applies the proposal."
                    ),
                    {
                        "type": "object",
                        "properties": {
                            "patch": {
                                "type": "object",
                                "properties": {
                                    "schema_version": {"const": 1},
                                    "patch_id": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 128,
                                    },
                                    "design_id": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 128,
                                    },
                                    "base_revision": {
                                        "type": "integer",
                                        "minimum": 0,
                                    },
                                    "description": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 4096,
                                    },
                                    "operations": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": MAX_PREVIEW_OPERATIONS,
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "operation": {
                                                    "type": "string",
                                                    "enum": [
                                                        "set_component_value",
                                                        "set_component_nodes",
                                                        "set_component_model",
                                                        "add_component",
                                                        "remove_component",
                                                        "replace_component",
                                                        "add_net",
                                                        "remove_net",
                                                        "set_parameter",
                                                        "set_annotation",
                                                    ],
                                                },
                                                "target": {
                                                    "type": "string",
                                                    "minLength": 1,
                                                    "maxLength": 255,
                                                },
                                                "before": {},
                                                "after": {},
                                                "reason": {
                                                    "type": "string",
                                                    "minLength": 1,
                                                    "maxLength": 4096,
                                                },
                                            },
                                            "required": [
                                                "operation",
                                                "target",
                                                "before",
                                                "after",
                                                "reason",
                                            ],
                                            "additionalProperties": False,
                                        },
                                    },
                                    "metadata": {"type": "object"},
                                },
                                "required": [
                                    "schema_version",
                                    "patch_id",
                                    "design_id",
                                    "base_revision",
                                    "description",
                                    "operations",
                                    "metadata",
                                ],
                                "additionalProperties": False,
                            }
                        },
                        "required": ["patch"],
                        "additionalProperties": False,
                    },
                ),
                self._validate_arguments,
                self._preview,
            ),
        )

    def _validate_arguments(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        unknown = set(arguments) - {"patch"}
        if unknown:
            raise ValueError(f"unknown arguments: {sorted(unknown)}")
        if "patch" not in arguments:
            raise ValueError("patch is required")
        raw_patch = _plain_json(arguments["patch"], MAX_PATCH_ARGUMENT_BYTES)
        patch = validate_design_patch(self.design, raw_patch)
        return {"patch": patch.to_dict()}

    @staticmethod
    def _structural_delta(
        before: Mapping[str, Any], after: Mapping[str, Any]
    ) -> dict[str, Any]:
        before_codes = before.get("code_counts", {})
        after_codes = after.get("code_counts", {})
        keys = sorted(set(before_codes) | set(after_codes))
        introduced = {
            key: after_codes.get(key, 0) - before_codes.get(key, 0)
            for key in keys
            if after_codes.get(key, 0) > before_codes.get(key, 0)
        }
        resolved = {
            key: before_codes.get(key, 0) - after_codes.get(key, 0)
            for key in keys
            if before_codes.get(key, 0) > after_codes.get(key, 0)
        }
        return {
            "before_diagnostic_count": before.get("diagnostic_count", 0),
            "after_diagnostic_count": after.get("diagnostic_count", 0),
            "before_severity_counts": dict(before.get("severity_counts", {})),
            "after_severity_counts": dict(after.get("severity_counts", {})),
            "introduced_code_counts": introduced,
            "resolved_code_counts": resolved,
            "topology_changed": False,
            "simulation_performed": False,
            "electrical_correctness_proven": False,
        }

    def _preview(
        self,
        arguments: Mapping[str, Any],
        cancel_event: threading.Event | None,
    ) -> Any:
        _check_cancelled(cancel_event)
        prepared = prepare_design_patch(self.design, arguments["patch"])
        patch = prepared.patch
        candidate = prepared.candidate
        _check_cancelled(cancel_event)
        before_checks = run_readonly_structural_checks(self.design, cancel_event)
        after_checks = run_readonly_structural_checks(candidate, cancel_event)
        topology_operations = {
            "set_component_nodes",
            "add_component",
            "remove_component",
            "replace_component",
            "add_net",
            "remove_net",
        }
        structural_delta = self._structural_delta(before_checks, after_checks)
        structural_delta["topology_changed"] = any(
            item.operation in topology_operations for item in patch.operations
        )
        result = {
            "schema_version": DESIGN_PATCH_PREVIEW_SCHEMA_VERSION,
            "preview_valid": True,
            "patch": patch.to_dict(),
            "inverse_patch": prepared.inverse_patch.to_dict(),
            "changes": [_change_view(item) for item in patch.operations],
            "candidate": {
                "design_id": candidate.design_id,
                "base_revision": self.design.revision,
                "candidate_revision": candidate.revision,
                "component_count": len(candidate.components),
                "net_count": len(candidate.nets),
                "source_netlist_present": candidate.source_netlist is not None,
                "source_netlist_updated": False,
                "source_netlist_update_required": (
                    prepared.source_netlist_update_required
                ),
                "source_netlist_consistent": (
                    not prepared.source_netlist_update_required
                ),
            },
            "structural_delta": structural_delta,
            "original_design_unchanged": True,
            "persisted": False,
            "backend_called": False,
            "simulation_performed": False,
            "electrical_correctness_proven": False,
            "approval_required_before_apply": True,
        }
        captured = _plain_json(result)
        with self._lock:
            if len(self._captured) >= MAX_CAPTURED_PREVIEWS:
                raise ValueError("too many patch previews were captured")
            self._captured.append(captured)
        return _plain_json(result)

    def captured_previews(self) -> tuple[dict[str, Any], ...]:
        """Return detached JSON copies for a CLI or UI review envelope."""
        with self._lock:
            return tuple(_plain_json(item) for item in self._captured)


def create_readonly_design_patch_bindings(
    design: CircuitDesign,
) -> tuple[ToolBinding, ...]:
    """Return the single in-memory DesignPatch preview binding."""
    return ReadOnlyDesignPatchPreview(design).bindings()


__all__ = [
    "DESIGN_PATCH_PREVIEW_SCHEMA_VERSION",
    "MAX_PREVIEW_OPERATIONS",
    "ReadOnlyDesignPatchPreview",
    "create_readonly_design_patch_bindings",
]
