"""Privacy-bounded read-only experiment evidence for model diagnosis.

The bindings capture a sanitized snapshot returned by
``summarize_experiment``.  Handlers cannot access the experiment registry,
files, artifact contents, a simulator, or Multisim.  Names and numeric values
remain untrusted model input even after structural validation.
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Mapping
from typing import Any, Final

from .agent_runtime import ToolBinding
from .model_provider import ModelCancelled, ToolDefinition


READ_ONLY_EXPERIMENT_TOOL_SCHEMA_VERSION: Final = 1
MAX_EVIDENCE_PAGE: Final = 20
MAX_MEASUREMENT_COLUMNS: Final = 64
MAX_REQUIREMENTS: Final = 25
MAX_ARTIFACTS: Final = 32

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_EXPERIMENT_ID_RE = re.compile(r"^exp-[0-9a-f]{24}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COLUMN_FIELDS = (
    "column",
    "count",
    "first",
    "last",
    "min",
    "max",
    "mean",
)
_REQUIREMENT_FIELDS = (
    "id",
    "metric",
    "signal",
    "status",
    "measurement_status",
    "value",
    "unit",
    "operator",
    "target",
    "lower",
    "upper",
    "allowed_absolute_error",
    "tolerance_abs",
    "tolerance_percent",
    "theoretical_value",
    "simulated_value",
    "absolute_error",
    "absolute_error_magnitude",
    "relative_error_percent",
    "reason",
)


def _check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ModelCancelled("read-only experiment inspection was cancelled")


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _nonnegative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _bounded_text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or _CONTROL_RE.search(normalized):
        raise ValueError(f"{name} is empty, too long, or contains control characters")
    return normalized


def _optional_text(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or _CONTROL_RE.search(normalized):
        return None
    return normalized[:maximum]


def _finite_scalar(value: object, *, text_limit: int = 500) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _optional_text(value, text_limit)
    return None


def _validate_empty(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    if arguments:
        raise ValueError(f"unknown arguments: {sorted(arguments)}")
    return {}


def _validate_page(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    unknown = set(arguments) - {"offset", "limit"}
    if unknown:
        raise ValueError(f"unknown arguments: {sorted(unknown)}")
    offset = arguments.get("offset", 0)
    limit = arguments.get("limit", MAX_EVIDENCE_PAGE)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_EVIDENCE_PAGE
    ):
        raise ValueError(f"limit must be between 1 and {MAX_EVIDENCE_PAGE}")
    return {"offset": offset, "limit": limit}


def _page_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "offset": {"type": "integer", "minimum": 0},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_EVIDENCE_PAGE,
            },
        },
        "additionalProperties": False,
    }


def _sanitize_items(
    value: object,
    fields: tuple[str, ...],
    maximum: int,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    result: list[dict[str, Any]] = []
    for raw in value[:maximum]:
        if not isinstance(raw, Mapping):
            continue
        item: dict[str, Any] = {}
        for key in fields:
            if key not in raw:
                continue
            sanitized = _finite_scalar(raw[key])
            if sanitized is not None:
                item[key] = sanitized
        if item:
            result.append(item)
    return tuple(result)


class ReadOnlyExperimentEvidence:
    """Build a fixed, side-effect-free surface over one completed experiment."""

    def __init__(self, summary: Mapping[str, Any]) -> None:
        root = _mapping(summary, "experiment summary")
        if root.get("schema_version") != 1:
            raise ValueError("experiment summary schema_version must be 1")
        experiment_id = _bounded_text(root.get("experiment_id"), "experiment_id", 28)
        if not _EXPERIMENT_ID_RE.fullmatch(experiment_id):
            raise ValueError("experiment_id is invalid")
        self.experiment_id = experiment_id
        self.artifact_count = _nonnegative_integer(
            root.get("artifact_count"), "artifact_count"
        )
        self.total_size = _nonnegative_integer(root.get("total_size"), "total_size")

        measurements = _mapping(root.get("measurements", {}), "measurements")
        self.measurements_available = measurements.get("available") is True
        self.plotname = _optional_text(measurements.get("plotname"), 255)
        self.point_count = _nonnegative_integer(
            measurements.get("point_count", 0), "measurements.point_count"
        )
        self.column_count = _nonnegative_integer(
            measurements.get("column_count", 0), "measurements.column_count"
        )
        self.columns = _sanitize_items(
            measurements.get("columns"), _COLUMN_FIELDS, MAX_MEASUREMENT_COLUMNS
        )
        self.columns_truncated = bool(measurements.get("columns_truncated"))

        verification_value = root.get("verification", {})
        verification = _mapping(verification_value, "verification")
        self.verification_available = verification.get("available") is True
        self.verification_valid_json = verification.get("valid_json") is True
        verification_result = verification.get("result")
        result = (
            verification_result
            if isinstance(verification_result, Mapping)
            else {}
        )
        self.overall_status = _optional_text(result.get("overall_status"), 50)
        counts = result.get("counts")
        self.counts = {
            key: value
            for key in ("pass", "fail", "unverified")
            if isinstance(counts, Mapping)
            and isinstance((value := counts.get(key)), int)
            and not isinstance(value, bool)
            and value >= 0
        }
        self.requirement_count = _nonnegative_integer(
            result.get("requirement_count", 0), "verification.requirement_count"
        )
        self.requirements = _sanitize_items(
            result.get("requirements"), _REQUIREMENT_FIELDS, MAX_REQUIREMENTS
        )
        self.requirements_truncated = bool(result.get("requirements_truncated"))

        artifacts = root.get("artifacts")
        safe_artifacts: list[dict[str, Any]] = []
        if isinstance(artifacts, list):
            for raw in artifacts[:MAX_ARTIFACTS]:
                if not isinstance(raw, Mapping):
                    continue
                name = _optional_text(raw.get("name"), 128)
                mime_type = _optional_text(raw.get("mime_type"), 128)
                size = raw.get("size")
                sha256 = raw.get("sha256")
                if (
                    name is None
                    or mime_type is None
                    or isinstance(size, bool)
                    or not isinstance(size, int)
                    or size < 0
                    or not isinstance(sha256, str)
                    or not _SHA256_RE.fullmatch(sha256)
                ):
                    continue
                safe_artifacts.append(
                    {
                        "name": name,
                        "mime_type": mime_type,
                        "size": size,
                        "sha256": sha256,
                    }
                )
        self.artifacts = tuple(safe_artifacts)

    def bindings(self) -> tuple[ToolBinding, ...]:
        return (
            ToolBinding(
                ToolDefinition(
                    "eda_get_experiment_summary",
                    (
                        "Return bounded metadata for one already completed experiment, "
                        "including measurement and requirement-verification availability."
                    ),
                    {"type": "object", "properties": {}, "additionalProperties": False},
                ),
                _validate_empty,
                self._experiment_summary,
            ),
            ToolBinding(
                ToolDefinition(
                    "eda_list_measurement_columns",
                    (
                        "List a bounded page of statistical summaries for measured "
                        "columns. It never returns raw waveform samples."
                    ),
                    _page_schema(),
                ),
                _validate_page,
                self._list_measurement_columns,
            ),
            ToolBinding(
                ToolDefinition(
                    "eda_list_requirement_results",
                    "List a bounded page of deterministic experiment requirement verdicts.",
                    _page_schema(),
                ),
                _validate_page,
                self._list_requirement_results,
            ),
            ToolBinding(
                ToolDefinition(
                    "eda_list_experiment_artifacts",
                    (
                        "List a bounded page of artifact names, media types, sizes, and "
                        "SHA-256 digests without returning content or local paths."
                    ),
                    _page_schema(),
                ),
                _validate_page,
                self._list_artifacts,
            ),
        )

    def metadata(self) -> dict[str, Any]:
        """Return the path-free evidence metadata used by CLI and audit envelopes."""
        return {
            "schema_version": READ_ONLY_EXPERIMENT_TOOL_SCHEMA_VERSION,
            "experiment_id": self.experiment_id,
            "artifact_count": self.artifact_count,
            "total_size": self.total_size,
            "measurements_available": self.measurements_available,
            "measurement_column_count": self.column_count,
            "verification_available": self.verification_available,
            "verification_valid_json": self.verification_valid_json,
            "overall_status": self.overall_status,
            "requirement_count": self.requirement_count,
            "source_paths_exposed": False,
            "artifact_content_exposed": False,
            "raw_waveform_exposed": False,
            "simulation_started": False,
            "design_association_verified": False,
        }

    def _base_result(self) -> dict[str, Any]:
        return {
            "schema_version": READ_ONLY_EXPERIMENT_TOOL_SCHEMA_VERSION,
            "experiment_id": self.experiment_id,
            "read_only": True,
            "source_paths_exposed": False,
            "artifact_content_exposed": False,
            "raw_waveform_exposed": False,
            "simulation_started": False,
            "design_association_verified": False,
        }

    def _experiment_summary(
        self,
        arguments: Mapping[str, Any],
        cancel_event: threading.Event | None,
    ) -> Any:
        del arguments
        _check_cancelled(cancel_event)
        result = self._base_result()
        result.update(
            {
                "artifact_count": self.artifact_count,
                "total_size": self.total_size,
                "measurements": {
                    "available": self.measurements_available,
                    "plotname": self.plotname,
                    "point_count": self.point_count,
                    "column_count": self.column_count,
                    "summary_count": len(self.columns),
                    "summaries_truncated": self.columns_truncated,
                },
                "verification": {
                    "available": self.verification_available,
                    "valid_json": self.verification_valid_json,
                    "overall_status": self.overall_status,
                    "counts": dict(self.counts),
                    "requirement_count": self.requirement_count,
                    "summary_count": len(self.requirements),
                    "summaries_truncated": self.requirements_truncated,
                },
            }
        )
        return result

    def _page_result(
        self,
        arguments: Mapping[str, Any],
        items: tuple[dict[str, Any], ...],
        key: str,
    ) -> dict[str, Any]:
        offset = arguments["offset"]
        limit = arguments["limit"]
        page = items[offset : offset + limit]
        next_offset = offset + len(page)
        result = self._base_result()
        result.update(
            {
                "offset": offset,
                "limit": limit,
                "available_summary_count": len(items),
                "returned_count": len(page),
                key: [dict(item) for item in page],
                "has_more": next_offset < len(items),
                "next_offset": next_offset if next_offset < len(items) else None,
            }
        )
        return result

    def _list_measurement_columns(
        self,
        arguments: Mapping[str, Any],
        cancel_event: threading.Event | None,
    ) -> Any:
        _check_cancelled(cancel_event)
        result = self._page_result(arguments, self.columns, "columns")
        result.update(
            {
                "measurements_available": self.measurements_available,
                "original_column_count": self.column_count,
                "source_summaries_truncated": self.columns_truncated,
            }
        )
        return result

    def _list_requirement_results(
        self,
        arguments: Mapping[str, Any],
        cancel_event: threading.Event | None,
    ) -> Any:
        _check_cancelled(cancel_event)
        result = self._page_result(arguments, self.requirements, "requirements")
        result.update(
            {
                "verification_available": self.verification_available,
                "verification_valid_json": self.verification_valid_json,
                "overall_status": self.overall_status,
                "counts": dict(self.counts),
                "original_requirement_count": self.requirement_count,
                "source_summaries_truncated": self.requirements_truncated,
            }
        )
        return result

    def _list_artifacts(
        self,
        arguments: Mapping[str, Any],
        cancel_event: threading.Event | None,
    ) -> Any:
        _check_cancelled(cancel_event)
        result = self._page_result(arguments, self.artifacts, "artifacts")
        result.update(
            {
                "original_artifact_count": self.artifact_count,
                "source_summaries_truncated": self.artifact_count > len(self.artifacts),
            }
        )
        return result


def create_readonly_experiment_bindings(
    summary: Mapping[str, Any],
) -> tuple[ToolBinding, ...]:
    """Return four fixed bindings over a sanitized completed-experiment snapshot."""
    return ReadOnlyExperimentEvidence(summary).bindings()


__all__ = [
    "READ_ONLY_EXPERIMENT_TOOL_SCHEMA_VERSION",
    "ReadOnlyExperimentEvidence",
    "create_readonly_experiment_bindings",
]
