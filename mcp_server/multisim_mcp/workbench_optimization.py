"""Bounded, manifest-backed optimization evidence for the local workbench."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from .design_optimization import (
    OPTIMIZATION_SPEC_NAME,
    OPTIMIZATION_STATE_NAME,
)
from .global_optimization import GLOBAL_STATE_NAME, PARETO_NAME
from .preferred_values import format_spice_scalar, generate_preferred_values, parse_spice_scalar
from .workspace_manifest import DirectoryManifest


WORKBENCH_OPTIMIZATION_SCHEMA_VERSION = 1
MAX_WORKBENCH_CANDIDATES = 512
_MAX_JSON_BYTES = 8 * 1024 * 1024


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"optimization evidence contains duplicate JSON key: {key}")
        value[key] = item
    return value


def _manifest_json(
    root: Path,
    manifest: DirectoryManifest,
    filename: str,
    required_role: str,
) -> dict[str, Any]:
    artifact = next((item for item in manifest.artifacts if item.path == filename), None)
    if artifact is None or artifact.role != required_role:
        raise FileNotFoundError(f"required optimization artifact is missing: {filename}")
    if artifact.size > _MAX_JSON_BYTES:
        raise ValueError(f"optimization artifact exceeds the size limit: {filename}")
    path = root / filename
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"required optimization artifact is missing: {filename}")
    content = path.read_bytes()
    if len(content) != artifact.size or hashlib.sha256(content).hexdigest() != artifact.sha256:
        raise ValueError(f"optimization artifact changed after verification: {filename}")
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {constant}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError(f"optimization artifact must be UTF-8: {filename}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"optimization artifact is not valid JSON: {filename}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"optimization artifact must contain an object: {filename}")
    return value


def _text(value: object, maximum: int = 160) -> str | None:
    if value is None:
        return None
    rendered = str(value).replace("\x00", "").replace("\r", " ").replace("\n", " ")
    return rendered if len(rendered) <= maximum else rendered[: maximum - 1] + "…"


def _number(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return value


def _integer(value: object, default: int = 0) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else default


def _assignments(value: object) -> dict[str, str | float | int | bool | None]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str | float | int | bool | None] = {}
    for raw_key in sorted(value, key=lambda item: str(item).casefold())[:32]:
        key = _text(raw_key, 64)
        if key is None:
            continue
        raw = value[raw_key]
        if raw is None or isinstance(raw, bool):
            result[key] = raw
        elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
            result[key] = _number(raw)
        else:
            result[key] = _text(raw, 120)
    return result


def _objective_definition(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    requirement_id = _text(value.get("requirement_id"), 96)
    goal = _text(value.get("goal"), 24)
    if not requirement_id or not goal:
        return None
    result: dict[str, Any] = {"requirement_id": requirement_id, "goal": goal}
    for key in ("target", "epsilon", "weight"):
        if (number := _number(value.get(key))) is not None:
            result[key] = number
    return result


def _objective_result(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {
        "requirement_id": _text(value.get("requirement_id"), 96),
        "goal": _text(value.get("goal"), 24),
        "status": _text(value.get("status"), 32) or "unverified",
        "value": _number(value.get("value")),
        "score": _number(value.get("score")),
    }
    for key in ("epsilon", "weight"):
        if (number := _number(value.get(key))) is not None:
            result[key] = number
    return result


def _candidate_base(
    value: Mapping[str, Any],
    *,
    recommended_id: str | None,
    rank: int | None,
) -> dict[str, Any]:
    evaluation_id = _text(value.get("evaluation_id"), 96) or "unknown"
    experiment = value.get("experiment") if isinstance(value.get("experiment"), Mapping) else {}
    error = value.get("error") if isinstance(value.get("error"), Mapping) else {}
    return {
        "evaluation_id": evaluation_id,
        "kind": _text(value.get("kind"), 32) or "candidate",
        "status": _text(value.get("status"), 48) or "unknown",
        "rank": rank,
        "recommended": evaluation_id == recommended_id,
        "attempt": max(0, _integer(value.get("attempt"), 0)),
        "hard_constraint_status": _text(experiment.get("overall_status"), 32),
        "assignments": _assignments(value.get("values", value.get("assignments"))),
        "error_type": _text(error.get("type"), 96),
    }


def _status_counts(evaluations: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(_text(item.get("status"), 48) or "unknown" for item in evaluations)
    return dict(sorted(counts.items()))


def _sensitivity_objectives(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = item.get("objectives")
    if isinstance(raw, list):
        return [candidate for candidate in raw if isinstance(candidate, Mapping)]
    single = item.get("objective")
    return [single] if isinstance(single, Mapping) else []


def _sensitivity_analysis(evaluations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize observed candidate-range sensitivity without claiming causality."""
    global_scores: dict[str, list[float]] = {}
    parameters: dict[str, dict[str, dict[str, Any]]] = {}
    for item in evaluations:
        objectives = _sensitivity_objectives(item)
        objective_rows: list[tuple[str, float, float | None]] = []
        for index, objective in enumerate(objectives):
            score = _number(objective.get("score"))
            if score is None:
                continue
            objective_id = _text(objective.get("requirement_id"), 96) or f"objective-{index + 1}"
            value = _number(objective.get("value"))
            objective_rows.append((objective_id, float(score), None if value is None else float(value)))
            global_scores.setdefault(objective_id, []).append(float(score))
        assignments = item.get("values", item.get("assignments"))
        if not isinstance(assignments, Mapping) or not objective_rows:
            continue
        for raw_parameter, raw_value in list(assignments.items())[:32]:
            parameter = _text(raw_parameter, 64)
            value = _text(raw_value, 96)
            if not parameter or value is None:
                continue
            bucket = parameters.setdefault(parameter, {}).setdefault(value, {"count": 0, "scores": {}})
            bucket["count"] += 1
            for objective_id, score, measured_value in objective_rows:
                bucket["scores"].setdefault(objective_id, []).append((score, measured_value))

    result: list[dict[str, Any]] = []
    for parameter, value_buckets in parameters.items():
        objective_rows: list[dict[str, Any]] = []
        influence = 0.0
        for objective_id in sorted(global_scores, key=str.casefold):
            values = [
                pair[0]
                for bucket in value_buckets.values()
                for pair in bucket["scores"].get(objective_id, [])
            ]
            if not values:
                continue
            score_span = max(values) - min(values)
            global_span = max(global_scores[objective_id]) - min(global_scores[objective_id])
            normalized_span = 0.0 if global_span <= 0 else min(1.0, score_span / global_span)
            influence = max(influence, normalized_span)
            objective_rows.append(
                {
                    "requirement_id": objective_id,
                    "score_span": score_span,
                    "normalized_span": normalized_span,
                    "sample_count": len(values),
                }
            )
        profiles: list[dict[str, Any]] = []
        for value, bucket in sorted(value_buckets.items(), key=lambda item: item[0].casefold())[:16]:
            profile: dict[str, Any] = {"value": value, "sample_count": bucket["count"]}
            profile["objectives"] = [
                {
                    "requirement_id": objective_id,
                    "mean_score": sum(pair[0] for pair in bucket["scores"].get(objective_id, []))
                    / len(bucket["scores"][objective_id]),
                }
                for objective_id in sorted(bucket["scores"], key=str.casefold)
                if bucket["scores"].get(objective_id)
            ]
            profiles.append(profile)
        result.append(
            {
                "parameter": parameter,
                "influence": influence,
                "sample_count": sum(bucket["count"] for bucket in value_buckets.values()),
                "distinct_values": len(value_buckets),
                "objectives": objective_rows,
                "profiles": profiles,
            }
        )
    result.sort(key=lambda item: (-float(item["influence"]), item["parameter"].casefold()))
    return {
        "available": bool(result),
        "method": "observed-candidate-range",
        "confidence": "descriptive",
        "parameters": result[:32],
    }


def _suggest_neighborhood(values: Sequence[str]) -> tuple[list[str], str]:
    """Suggest a small, bounded neighborhood for numeric or categorical values."""
    unique = list(dict.fromkeys(values))
    parsed: list[tuple[str, Any]] = []
    for value in unique:
        try:
            parsed.append((value, parse_spice_scalar(value)))
        except ValueError:
            parsed = []
            break
    if not parsed:
        return sorted(unique, key=str.casefold)[:16], "observed-values"
    lower = min(item[1] for item in parsed) * Decimal("0.5")
    upper = max(item[1] for item in parsed) * Decimal("2")
    try:
        generated = generate_preferred_values(
            "E24", format_spice_scalar(lower), format_spice_scalar(upper)
        )
    except ValueError:
        return sorted(unique, key=str.casefold)[:16], "observed-values"
    observed_keys = {str(item[1].normalize()) for item in parsed}
    observed_indices: list[int] = []
    numeric_generated = [parse_spice_scalar(candidate) for candidate in generated]
    for _, parsed_value in parsed:
        observed_indices.append(
            min(
                range(len(numeric_generated)),
                key=lambda index: abs(numeric_generated[index] - parsed_value),
            )
        )
    ranked: list[tuple[int, str]] = []
    for index, candidate in enumerate(generated):
        try:
            key = str(parse_spice_scalar(candidate).normalize())
        except ValueError:
            continue
        distance = (
            min(abs(index - observed_index) for observed_index in observed_indices)
            if key in observed_keys
            else 99
        )
        ranked.append((distance, candidate))
    selected = [candidate for distance, candidate in ranked if distance <= 2]
    selected.extend(
        value for value in unique if value not in selected
    )
    return list(dict.fromkeys(selected))[:16], "E24-neighborhood"


def _search_plan(
    evaluations: Sequence[Mapping[str, Any]],
    sensitivity: Mapping[str, Any],
    max_experiments: int,
    source_optimization_kind: str,
) -> dict[str, Any]:
    """Build a reviewable next-search proposal; never execute or mutate it."""
    parameters = sensitivity.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        return {
            "available": False,
            "method": "sensitivity-guided-neighborhood",
            "read_only": True,
            "priorities": [],
            "spec_draft": {"available": False},
        }
    observed: dict[str, list[str]] = {}
    for item in evaluations:
        assignments = item.get("values", item.get("assignments"))
        if not isinstance(assignments, Mapping):
            continue
        for raw_parameter, raw_value in list(assignments.items())[:32]:
            parameter = _text(raw_parameter, 64)
            value = _text(raw_value, 96)
            if parameter and value is not None:
                observed.setdefault(parameter, []).append(value)
    selected = [item for item in parameters if isinstance(item, Mapping)][:8]
    total_influence = sum(max(0.0, float(item.get("influence", 0.0))) for item in selected)
    exploration_budget = max(0, int(max_experiments) - 1)
    influences = [max(0.0, float(item.get("influence", 0.0))) for item in selected]
    shares = [0] * len(selected)
    active = [index for index, influence in enumerate(influences) if influence > 0]
    if exploration_budget and active:
        for index in sorted(active, key=lambda item: (-influences[item], item))[:exploration_budget]:
            shares[index] = 1
        remaining = exploration_budget - sum(shares)
        if remaining and total_influence > 0:
            extras = [remaining * influence / total_influence for influence in influences]
            for index, extra in enumerate(extras):
                shares[index] += int(extra)
            remaining = exploration_budget - sum(shares)
            for index in sorted(
                range(len(selected)),
                key=lambda item: (-(extras[item] - int(extras[item])), -influences[item], item),
            )[:remaining]:
                shares[index] += 1
    priorities: list[dict[str, Any]] = []
    for index, item in enumerate(selected):
        parameter = _text(item.get("parameter"), 64)
        if not parameter:
            continue
        influence = max(0.0, float(item.get("influence", 0.0)))
        values, source = _suggest_neighborhood(observed.get(parameter, []))
        priorities.append(
            {
                "parameter": parameter,
                "influence": influence,
                "budget_share": shares[index],
                "observed_values": list(dict.fromkeys(observed.get(parameter, [])))[:16],
                "suggested_values": values,
                "value_source": source,
                "reason": "prioritize high observed objective spread; review before execution",
            }
        )
    allocated_budget = sum(item["budget_share"] for item in priorities)
    preflight = {
        "status": "ready_for_review" if priorities and allocated_budget <= exploration_budget else "blocked",
        "approval_required": True,
        "execution_enabled": False,
        "approval": {
            "status": "not_issued",
            "token_issued": False,
            "issuance": "cli-only",
            "binding_fields": [
                "entry_handle",
                "optimization_id",
                "source_optimization_kind",
                "spec_draft_sha256",
                "exploration_budget",
                "max_experiments",
            ],
        },
        "checks": [
            {
                "id": "bounded-budget",
                "status": "pass" if allocated_budget <= exploration_budget else "fail",
                "summary": f"{allocated_budget} of {exploration_budget} exploration runs allocated",
            },
            {
                "id": "bounded-values",
                "status": "pass" if priorities and all(0 < len(item["suggested_values"]) <= 16 for item in priorities) else "fail",
                "summary": "Each parameter proposal is capped at 16 values",
            },
            {
                "id": "manual-approval",
                "status": "required",
                "summary": "An explicit approval step is required before submission",
            },
        ],
    }
    spec_draft = {
        "available": bool(priorities),
        "draft_kind": "sensitivity-guided-search-v1",
        "source_optimization_kind": source_optimization_kind,
        "review_required": True,
        "read_only": True,
        "executable": False,
        "max_experiments": max(1, exploration_budget + 1) if priorities else 0,
        "preflight": preflight,
        "approval_binding": {
            "status": "not_issued",
            "token_issued": False,
            "issuance": "cli-only",
            "binding_fields": preflight["approval"]["binding_fields"],
        },
        "parameters": [
            {
                "name": item["parameter"],
                "values": item["suggested_values"],
                "budget_share": item["budget_share"],
            }
            for item in priorities
        ],
    }
    return {
        "available": bool(priorities),
        "method": "sensitivity-guided-neighborhood",
        "read_only": True,
        "exploration_budget": exploration_budget,
        "priorities": priorities,
        "spec_draft": spec_draft,
    }


def _run_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    attempted = max(0, _integer(state.get("experiments_attempted"), 0))
    maximum = max(0, _integer(state.get("max_experiments"), 0))
    progress = 0 if maximum == 0 else min(100, round(attempted / maximum * 100))
    return {
        "state": _text(state.get("state"), 32) or "unknown",
        "status": _text(state.get("status"), 64) or "unknown",
        "stop_reason": _text(state.get("stop_reason"), 96),
        "started_at": _text(state.get("started_at"), 64),
        "updated_at": _text(state.get("updated_at"), 64),
        "finished_at": _text(state.get("finished_at"), 64),
        "candidate_space_size": max(0, _integer(state.get("candidate_space_size"), 0)),
        "max_experiments": maximum,
        "experiments_attempted": attempted,
        "experiment_attempt_count": max(0, _integer(state.get("experiment_attempt_count"), 0)),
        "resume_count": max(0, _integer(state.get("resume_count"), 0)),
        "feasible_solution_count": max(0, _integer(state.get("feasible_solution_count"), 0)),
        "progress_percent": progress,
    }


def _design_optimization(
    root: Path,
    manifest: DirectoryManifest,
) -> dict[str, Any]:
    state = _manifest_json(root, manifest, OPTIMIZATION_STATE_NAME, "optimization-state")
    if state.get("schema_version") != 1 or state.get("kind") != "multisim-mcp-design-optimization":
        raise ValueError("optimization state contract is invalid")
    if state.get("optimization_id") != manifest.entity_id or state.get("state") != manifest.state:
        raise ValueError("optimization state does not match its directory manifest")
    spec = _manifest_json(root, manifest, OPTIMIZATION_SPEC_NAME, "optimization-spec")
    definition = _objective_definition(spec.get("objective"))
    raw = state.get("evaluations")
    if not isinstance(raw, list) or len(raw) > MAX_WORKBENCH_CANDIDATES:
        raise ValueError("optimization evaluation list exceeds the workbench limit")
    evaluations = [item for item in raw if isinstance(item, Mapping)]
    ranked = state.get("ranked_feasible_evaluation_ids")
    rank_by_id = {
        str(evaluation_id): index + 1
        for index, evaluation_id in enumerate(ranked if isinstance(ranked, list) else [])
        if isinstance(evaluation_id, str)
    }
    best_id = _text(state.get("best_evaluation_id"), 96)
    candidates: list[dict[str, Any]] = []
    for item in evaluations:
        evaluation_id = str(item.get("evaluation_id", ""))
        candidate = _candidate_base(
            item,
            recommended_id=best_id,
            rank=rank_by_id.get(evaluation_id),
        )
        objective = _objective_result(item.get("objective"))
        if objective is not None and definition is not None:
            objective["requirement_id"] = definition["requirement_id"]
            objective["goal"] = definition["goal"]
        candidate["objectives"] = [] if objective is None else [objective]
        procurement = item.get("procurement") if isinstance(item.get("procurement"), Mapping) else {}
        candidate["procurement"] = {
            "status": _text(procurement.get("status"), 32),
            "total_unit_cost": _number(procurement.get("total_unit_cost")),
            "currency": _text(procurement.get("currency"), 16),
        }
        candidates.append(candidate)
    candidates.sort(
        key=lambda item: (
            item["rank"] is None,
            item["rank"] if item["rank"] is not None else 1_000_000,
            next(
                (
                    _integer(candidate.get("index"), 1_000_000)
                    for candidate in evaluations
                    if candidate.get("evaluation_id") == item["evaluation_id"]
                ),
                1_000_000,
            ),
        )
    )
    convergence: list[dict[str, Any]] = []
    best_score: float | int | None = None
    for index, item in enumerate(evaluations):
        objective = item.get("objective") if isinstance(item.get("objective"), Mapping) else {}
        score = _number(objective.get("score"))
        if score is not None and (best_score is None or float(score) < float(best_score)):
            best_score = score
        convergence.append(
            {
                "index": index,
                "evaluation_id": _text(item.get("evaluation_id"), 96),
                "score": score,
                "best_score": best_score,
            }
        )
    sensitivity = _sensitivity_analysis(evaluations)
    return {
        "schema_version": WORKBENCH_OPTIMIZATION_SCHEMA_VERSION,
        "optimization_kind": "design-optimization",
        "optimization_id": _text(state.get("optimization_id"), 128),
        "run": _run_summary(state),
        "search_strategy": "deterministic-bounded",
        "selection_policy": "lowest-feasible-score",
        "objective_definitions": [] if definition is None else [definition],
        "status_counts": _status_counts(evaluations),
        "recommended_evaluation_id": best_id,
        "candidates": candidates,
        "convergence": convergence,
        "sensitivity": sensitivity,
        "search_plan": _search_plan(
            evaluations,
            sensitivity,
            _integer(state.get("max_experiments"), 0),
            "design-optimization",
        ),
        "pareto": {
            "available": False,
            "objective_count": 1 if definition is not None else 0,
            "points": [],
        },
    }


def _global_optimization(
    root: Path,
    manifest: DirectoryManifest,
) -> dict[str, Any]:
    state = _manifest_json(root, manifest, GLOBAL_STATE_NAME, "global-optimization-state")
    if state.get("schema_version") != 1 or state.get("kind") != "multisim-mcp-global-optimization":
        raise ValueError("global optimization state contract is invalid")
    if state.get("global_optimization_id") != manifest.entity_id or state.get("state") != manifest.state:
        raise ValueError("global optimization state does not match its directory manifest")
    pareto = _manifest_json(root, manifest, PARETO_NAME, "pareto-front")
    if pareto.get("global_optimization_id") != manifest.entity_id:
        raise ValueError("Pareto evidence does not match its directory manifest")
    definitions = [
        item
        for raw in pareto.get("objectives", [])
        if (item := _objective_definition(raw)) is not None
    ]
    raw = state.get("evaluations")
    if not isinstance(raw, list) or len(raw) > MAX_WORKBENCH_CANDIDATES:
        raise ValueError("global optimization evaluation list exceeds the workbench limit")
    evaluations = [item for item in raw if isinstance(item, Mapping)]
    recommended_id = _text(state.get("recommended_evaluation_id"), 96)
    pareto_ids = {
        str(item)
        for item in state.get("pareto_evaluation_ids", [])
        if isinstance(item, str)
    }
    candidates: list[dict[str, Any]] = []
    points: list[dict[str, Any]] = []
    for item in evaluations:
        stored_rank = item.get("pareto_rank")
        rank = stored_rank + 1 if isinstance(stored_rank, int) and not isinstance(stored_rank, bool) and stored_rank >= 0 else None
        candidate = _candidate_base(item, recommended_id=recommended_id, rank=rank)
        objectives = [
            result
            for raw_objective in (item.get("objectives") or [])
            if (result := _objective_result(raw_objective)) is not None
        ] if isinstance(item.get("objectives"), list) else []
        candidate["objectives"] = objectives
        candidate["pareto"] = candidate["evaluation_id"] in pareto_ids
        candidates.append(candidate)
        if objectives and all(objective.get("value") is not None for objective in objectives):
            points.append(
                {
                    "evaluation_id": candidate["evaluation_id"],
                    "values": [objective["value"] for objective in objectives],
                    "scores": [objective["score"] for objective in objectives],
                    "rank": rank,
                    "pareto": candidate["pareto"],
                    "recommended": candidate["recommended"],
                }
            )
    candidates.sort(
        key=lambda item: (
            not item.get("recommended", False),
            item["rank"] is None,
            item["rank"] if item["rank"] is not None else 1_000_000,
            item["evaluation_id"],
        )
    )
    sensitivity = _sensitivity_analysis(evaluations)
    return {
        "schema_version": WORKBENCH_OPTIMIZATION_SCHEMA_VERSION,
        "optimization_kind": "global-optimization",
        "optimization_id": _text(state.get("global_optimization_id"), 128),
        "run": _run_summary(state),
        "search_strategy": _text(state.get("search_strategy"), 48) or "unknown",
        "selection_policy": _text(pareto.get("selection_policy"), 48) or "none",
        "objective_definitions": definitions,
        "status_counts": _status_counts(evaluations),
        "recommended_evaluation_id": recommended_id,
        "candidates": candidates,
        "convergence": [],
        "sensitivity": sensitivity,
        "search_plan": _search_plan(
            evaluations,
            sensitivity,
            _integer(state.get("max_experiments"), 0),
            "global-optimization",
        ),
        "pareto": {
            "available": bool(points and pareto_ids),
            "objective_count": len(definitions),
            "solution_count": len(pareto_ids),
            "points": points,
        },
    }


def summarize_optimization_entry(
    root: Path,
    manifest: DirectoryManifest,
) -> dict[str, Any]:
    """Return a compact optimization view without exposing local paths."""
    if manifest.directory_kind == "optimization":
        return _design_optimization(root, manifest)
    if manifest.directory_kind == "global-optimization":
        return _global_optimization(root, manifest)
    raise ValueError("directory is not a supported optimization result")


__all__ = [
    "MAX_WORKBENCH_CANDIDATES",
    "WORKBENCH_OPTIMIZATION_SCHEMA_VERSION",
    "summarize_optimization_entry",
]
