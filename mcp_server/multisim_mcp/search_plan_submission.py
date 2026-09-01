"""Approved search-plan submission into the existing durable job queue."""

from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Any, Mapping

from .design_optimization import validate_optimization_spec
from .eda_core import CircuitDesign
from .global_optimization import validate_global_optimization_spec
from .job_engine import ExperimentJobManager, JobSubmission
from .search_plan_approval import (
    SearchPlanApprovalStore,
    build_search_plan_binding,
    search_plan_digest,
)


def _clone(value: object) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _draft_parameters(spec_draft: Mapping[str, Any]) -> dict[str, list[Any]]:
    raw = spec_draft.get("parameters")
    if not isinstance(raw, list) or not raw:
        raise ValueError("search draft has no parameters")
    result: dict[str, list[Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("search draft parameter is invalid")
        name = item.get("name")
        values = item.get("values")
        if not isinstance(name, str) or not name.strip() or not isinstance(values, list):
            raise ValueError("search draft parameter is invalid")
        key = name.strip().casefold()
        if key in result:
            raise ValueError(f"search draft contains duplicate parameter: {name}")
        result[key] = list(values)
    return result


def derive_approved_search_spec(
    source_spec: Mapping[str, Any],
    spec_draft: Mapping[str, Any],
    design: CircuitDesign,
    source_optimization_kind: str,
) -> dict[str, Any]:
    """Turn a review-only draft into a bounded formal optimization spec."""
    parameters = _draft_parameters(spec_draft)
    derived = _clone(source_spec)
    max_experiments = spec_draft.get("max_experiments")
    if isinstance(max_experiments, bool) or not isinstance(max_experiments, int):
        raise ValueError("search draft max_experiments is invalid")
    derived["max_experiments"] = max_experiments

    if source_optimization_kind == "design-optimization":
        variables = derived.get("variables")
        if not isinstance(variables, list):
            raise ValueError("source OptimizationSpec variables are invalid")
        seen: set[str] = set()
        for index, raw in enumerate(variables):
            if not isinstance(raw, Mapping):
                raise ValueError("source OptimizationSpec variable is invalid")
            refdes = raw.get("refdes")
            if not isinstance(refdes, str):
                raise ValueError("source OptimizationSpec variable refdes is invalid")
            key = refdes.casefold()
            seen.add(key)
            if key not in parameters:
                continue
            updated = dict(raw)
            updated.pop("series", None)
            updated["values"] = parameters[key]
            variables[index] = updated
        unknown = sorted(set(parameters) - seen)
        if unknown:
            raise ValueError(f"search draft parameter is not in source variables: {unknown[0]}")
        return validate_optimization_spec(derived, design)

    if source_optimization_kind == "global-optimization":
        dimensions = derived.get("dimensions")
        if not isinstance(dimensions, list):
            raise ValueError("source GlobalOptimizationSpec dimensions are invalid")
        seen: set[str] = set()
        for index, raw in enumerate(dimensions):
            if not isinstance(raw, Mapping):
                raise ValueError("source GlobalOptimizationSpec dimension is invalid")
            dimension_id = raw.get("id")
            if not isinstance(dimension_id, str):
                raise ValueError("source GlobalOptimizationSpec dimension id is invalid")
            key = dimension_id.casefold()
            seen.add(key)
            if key not in parameters:
                continue
            if raw.get("kind") != "component_value":
                raise ValueError(
                    "topology search proposals require explicit operations and cannot "
                    "be derived from scalar search values"
                )
            updated = dict(raw)
            updated.pop("series", None)
            updated.pop("range", None)
            updated["values"] = parameters[key]
            dimensions[index] = updated
        unknown = sorted(set(parameters) - seen)
        if unknown:
            raise ValueError(f"search draft parameter is not in source dimensions: {unknown[0]}")
        return validate_global_optimization_spec(derived, design)

    raise ValueError("source_optimization_kind is invalid")


def _output_path(output_dir: str, resume_existing: bool) -> Path:
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ValueError("output_dir must not be empty")
    unresolved = Path(output_dir).expanduser()
    if unresolved.is_symlink():
        raise ValueError("output_dir must not be a symbolic link")
    result = unresolved.resolve()
    if result == Path(result.anchor):
        raise ValueError("output_dir must not be a filesystem root")
    if result.exists():
        if not result.is_dir():
            raise ValueError("output_dir exists and is not a directory")
        if any(result.iterdir()) and not resume_existing:
            raise FileExistsError(
                "output_dir is not empty; set resume_existing only for a matching "
                "interrupted search submission"
            )
    return result


def _validate_runtime(
    timeout_per_experiment: float,
    max_points: int,
    job_timeout: float,
    heartbeat_timeout: float,
) -> None:
    if (
        isinstance(timeout_per_experiment, bool)
        or not isinstance(timeout_per_experiment, (int, float))
        or not math.isfinite(float(timeout_per_experiment))
        or not 0 < float(timeout_per_experiment) <= 3600
    ):
        raise ValueError("timeout_per_experiment must be between 0 and 3600")
    if isinstance(max_points, bool) or not isinstance(max_points, int) or not 1 <= max_points <= 100_000:
        raise ValueError("max_points must be between 1 and 100000")
    if (
        isinstance(job_timeout, bool)
        or not isinstance(job_timeout, (int, float))
        or not math.isfinite(float(job_timeout))
        or not 1 <= float(job_timeout) <= 86_400
    ):
        raise ValueError("job_timeout must be between 1 and 86400 seconds")
    if float(job_timeout) <= float(timeout_per_experiment):
        raise ValueError("job_timeout must exceed timeout_per_experiment")
    if (
        isinstance(heartbeat_timeout, bool)
        or not isinstance(heartbeat_timeout, (int, float))
        or not math.isfinite(float(heartbeat_timeout))
        or not 10 <= float(heartbeat_timeout) <= 900
    ):
        raise ValueError("heartbeat_timeout must be between 10 and 900 seconds")


def submit_approved_search_plan(
    *,
    design: Mapping[str, Any],
    source_spec: Mapping[str, Any],
    spec_draft: Mapping[str, Any],
    approval_token: str,
    entry_handle: str,
    optimization_id: str,
    source_optimization_kind: str,
    exploration_budget: int,
    max_experiments: int,
    output_dir: str,
    approval_store: str | None = None,
    job_manager: ExperimentJobManager | None = None,
    timeout_per_experiment: float = 120.0,
    max_points: int = 2000,
    job_timeout: float = 7200.0,
    heartbeat_timeout: float = 180.0,
    resume_existing: bool = False,
) -> JobSubmission:
    """Consume an exact approval and queue one derived bounded optimization.

    The queue record includes only the approval ID and binding digests. The
    bearer token is never passed to the worker or persisted in the job spec.
    """
    if not isinstance(design, Mapping) or not isinstance(source_spec, Mapping):
        raise ValueError("design and source_spec must be JSON objects")
    if not isinstance(spec_draft, Mapping):
        raise ValueError("spec_draft must be a JSON object")
    normalized_design = CircuitDesign.from_dict(dict(design))
    source_design = normalized_design.to_dict()
    binding = build_search_plan_binding(
        entry_handle=entry_handle,
        optimization_id=optimization_id,
        source_optimization_kind=source_optimization_kind,
        source_design=source_design,
        source_spec=source_spec,
        spec_draft=spec_draft,
        exploration_budget=exploration_budget,
        max_experiments=max_experiments,
    )
    derived_spec = derive_approved_search_spec(
        source_spec, spec_draft, normalized_design, source_optimization_kind
    )
    _validate_runtime(
        timeout_per_experiment, max_points, job_timeout, heartbeat_timeout
    )
    if not isinstance(resume_existing, bool):
        raise ValueError("resume_existing must be a boolean")
    root = _output_path(output_dir, resume_existing)
    manager = job_manager or ExperimentJobManager()
    approval = SearchPlanApprovalStore(approval_store)
    consumer_id = f"search-submit-{uuid.uuid4().hex}"
    job_kind = "optimization" if source_optimization_kind == "design-optimization" else "global_optimization"
    spec_key = "optimization_spec" if job_kind == "optimization" else "global_optimization_spec"
    approval_record_id: str | None = None
    with approval.claim(approval_token, binding) as claim:
        approval_record_id = str(claim.record["approval_id"])
        queue_spec: dict[str, Any] = {
            "job_kind": job_kind,
            "design": normalized_design.to_dict(),
            spec_key: _clone(derived_spec),
            "output_dir": str(root),
            "timeout_per_experiment": float(timeout_per_experiment),
            "max_points": max_points,
            "job_timeout": float(job_timeout),
            "heartbeat_timeout": float(heartbeat_timeout),
            "resume_existing": resume_existing,
            "approval_id": claim.record["approval_id"],
            "search_plan_binding": _clone(binding),
            "search_plan_spec_sha256": search_plan_digest(derived_spec),
        }
        submission = manager.submit(queue_spec)
        claim.consume(consumer_id)
    return {
        **submission,
        "approval_id": approval_record_id,
        "approval_consumed": True,
        "search_plan_spec_sha256": search_plan_digest(derived_spec),
        "execution_started": False,
    }


__all__ = ["derive_approved_search_spec", "submit_approved_search_plan"]
