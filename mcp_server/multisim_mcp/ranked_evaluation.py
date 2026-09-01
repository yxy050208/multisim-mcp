"""Shared evidence validation for deterministic design ranking workflows."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence, Set
from pathlib import Path
from typing import Any

from .workspace_manifest import DIRECTORY_MANIFEST_NAME, read_directory_manifest


OBJECTIVE_GOALS = frozenset({"minimize", "maximize", "target"})
MAX_OBJECTIVES = 8


def finite_number(value: object, name: str) -> float:
    """Return one finite float while rejecting booleans and non-numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    return normalized


def normalize_single_objective(
    objective: object, known_requirement_ids: Set[str]
) -> dict[str, Any]:
    """Validate the common single-objective contract used by ranking workflows."""
    if not isinstance(objective, Mapping):
        raise ValueError("objective must be an object")
    allowed = {"requirement_id", "goal", "target"}
    if set(objective) - allowed:
        raise ValueError("objective contains unknown fields")
    requirement_id = str(objective.get("requirement_id", ""))
    if requirement_id not in known_requirement_ids:
        raise ValueError("objective.requirement_id must name one hard requirement")
    goal = str(objective.get("goal", "")).lower()
    if goal not in OBJECTIVE_GOALS:
        raise ValueError("objective.goal must be minimize, maximize, or target")
    normalized: dict[str, Any] = {
        "requirement_id": requirement_id,
        "goal": goal,
    }
    if goal == "target":
        if "target" not in objective:
            raise ValueError("target objective requires objective.target")
        normalized["target"] = finite_number(objective["target"], "objective.target")
    elif "target" in objective:
        raise ValueError("objective.target is allowed only for a target objective")
    return normalized


def normalize_objectives(
    objectives: object, known_requirement_ids: Set[str]
) -> list[dict[str, Any]]:
    """Validate a bounded multi-objective contract.

    Scores are always minimized. Optional epsilon values define when two
    measured scores are practically equivalent for Pareto dominance.
    """
    if not isinstance(objectives, (list, tuple)) or not (
        1 <= len(objectives) <= MAX_OBJECTIVES
    ):
        raise ValueError(f"objectives must contain between 1 and {MAX_OBJECTIVES} items")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(objectives):
        if not isinstance(raw, Mapping):
            raise ValueError(f"objectives[{index}] must be an object")
        unknown = set(raw) - {"requirement_id", "goal", "target", "epsilon", "weight"}
        if unknown:
            raise ValueError(f"objectives[{index}] contains unknown fields")
        base = normalize_single_objective(
            {
                key: raw[key]
                for key in ("requirement_id", "goal", "target")
                if key in raw
            },
            known_requirement_ids,
        )
        requirement_id = str(base["requirement_id"])
        if requirement_id in seen:
            raise ValueError("objectives must reference distinct requirements")
        seen.add(requirement_id)
        epsilon = finite_number(raw.get("epsilon", 0.0), f"objectives[{index}].epsilon")
        weight = finite_number(raw.get("weight", 1.0), f"objectives[{index}].weight")
        if epsilon < 0:
            raise ValueError(f"objectives[{index}].epsilon must be >= 0")
        if weight <= 0:
            raise ValueError(f"objectives[{index}].weight must be > 0")
        base["epsilon"] = epsilon
        base["weight"] = weight
        normalized.append(base)
    return normalized


def objective_result(
    verification: Mapping[str, Any], objective: Mapping[str, Any]
) -> dict[str, Any]:
    """Extract a finite measured objective and convert it to a sortable score."""
    requirement_id = str(objective["requirement_id"])
    item = next(
        (
            candidate
            for candidate in verification.get("requirements", [])
            if isinstance(candidate, Mapping) and candidate.get("id") == requirement_id
        ),
        None,
    )
    if not isinstance(item, Mapping):
        return {"status": "unverified", "value": None, "score": None}
    measurement = item.get("measurement")
    if not isinstance(measurement, Mapping) or measurement.get("status") != "measured":
        return {"status": "unverified", "value": None, "score": None}
    try:
        value = finite_number(measurement.get("value"), "objective measurement")
    except ValueError:
        return {"status": "unverified", "value": None, "score": None}
    goal = str(objective["goal"])
    score = (
        value
        if goal == "minimize"
        else -value
        if goal == "maximize"
        else abs(value - float(objective["target"]))
    )
    return {"status": "measured", "value": value, "score": score}


def objective_vector(
    verification: Mapping[str, Any], objectives: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Extract every objective measurement in declared order."""
    return [
        {
            **objective_result(verification, objective),
            "requirement_id": str(objective["requirement_id"]),
            "goal": str(objective["goal"]),
            "epsilon": float(objective.get("epsilon", 0.0)),
            "weight": float(objective.get("weight", 1.0)),
        }
        for objective in objectives
    ]


def pareto_dominates(
    left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]
) -> bool:
    """Return whether ``left`` epsilon-dominates ``right``.

    Both vectors must contain finite measured minimization scores in the same
    order. Each objective uses the larger declared epsilon so comparisons stay
    symmetric even when imported evidence was produced by an older writer.
    """
    if len(left) != len(right) or not left:
        raise ValueError("objective vectors must have the same non-zero length")
    strictly_better = False
    for index, (left_item, right_item) in enumerate(zip(left, right)):
        if left_item.get("requirement_id") != right_item.get("requirement_id"):
            raise ValueError(f"objective vector mismatch at index {index}")
        left_score = finite_number(left_item.get("score"), "left objective score")
        right_score = finite_number(right_item.get("score"), "right objective score")
        epsilon = max(
            finite_number(left_item.get("epsilon", 0.0), "left objective epsilon"),
            finite_number(right_item.get("epsilon", 0.0), "right objective epsilon"),
        )
        if left_score > right_score + epsilon:
            return False
        if left_score < right_score - epsilon:
            strictly_better = True
    return strictly_better


def pareto_fronts(
    evaluations: Sequence[Mapping[str, Any]], *, vector_key: str = "objectives"
) -> list[list[int]]:
    """Return deterministic non-dominated fronts as evaluation indexes."""
    if not evaluations:
        return []
    domination_counts = [0] * len(evaluations)
    dominates: list[list[int]] = [[] for _ in evaluations]
    for left_index, left in enumerate(evaluations):
        left_vector = left.get(vector_key)
        if not isinstance(left_vector, (list, tuple)):
            raise ValueError("evaluation objective vector is missing")
        for right_index in range(left_index + 1, len(evaluations)):
            right_vector = evaluations[right_index].get(vector_key)
            if not isinstance(right_vector, (list, tuple)):
                raise ValueError("evaluation objective vector is missing")
            if pareto_dominates(left_vector, right_vector):
                dominates[left_index].append(right_index)
                domination_counts[right_index] += 1
            elif pareto_dominates(right_vector, left_vector):
                dominates[right_index].append(left_index)
                domination_counts[left_index] += 1
    current = [index for index, count in enumerate(domination_counts) if count == 0]
    fronts: list[list[int]] = []
    while current:
        fronts.append(current)
        following: list[int] = []
        for left_index in current:
            for right_index in dominates[left_index]:
                domination_counts[right_index] -= 1
                if domination_counts[right_index] == 0:
                    following.append(right_index)
        current = sorted(following)
    return fronts


def weighted_compromise(
    evaluations: Sequence[Mapping[str, Any]], *, vector_key: str = "objectives"
) -> int | None:
    """Select a deterministic normalized weighted compromise from one Pareto set."""
    if not evaluations:
        return None
    vectors = [item.get(vector_key) for item in evaluations]
    if any(not isinstance(vector, (list, tuple)) for vector in vectors):
        raise ValueError("evaluation objective vector is missing")
    width = len(vectors[0])  # type: ignore[arg-type]
    if width == 0 or any(len(vector) != width for vector in vectors):  # type: ignore[arg-type]
        raise ValueError("objective vectors must have the same non-zero length")
    ranges: list[tuple[float, float]] = []
    for objective_index in range(width):
        scores = [
            finite_number(vector[objective_index].get("score"), "objective score")  # type: ignore[index]
            for vector in vectors
        ]
        ranges.append((min(scores), max(scores)))
    ranked: list[tuple[float, int]] = []
    for index, vector in enumerate(vectors):
        total = 0.0
        total_weight = 0.0
        for objective_index, item in enumerate(vector):  # type: ignore[union-attr]
            score = finite_number(item.get("score"), "objective score")
            weight = finite_number(item.get("weight", 1.0), "objective weight")
            low, high = ranges[objective_index]
            normalized = 0.0 if high == low else (score - low) / (high - low)
            total += normalized * weight
            total_weight += weight
        ranked.append((total / total_weight, index))
    return min(ranked)[1]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_ranked_experiment_evidence(
    root: Path,
    result: Mapping[str, Any],
    objective: Mapping[str, Any],
    *,
    workflow_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind a successful verified experiment to files before it can be ranked."""
    verification = result.get("verification")
    if not isinstance(verification, Mapping):
        raise RuntimeError(f"{workflow_name} experiment returned no verification result")
    output = Path(str(result["output_dir"])).resolve()
    try:
        relative_output = output.relative_to(root).as_posix()
    except ValueError as exc:
        raise RuntimeError(f"{workflow_name} experiment output escapes its run root") from exc
    verification_path = Path(str(result["verification_path"])).resolve()
    try:
        relative_verification = verification_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise RuntimeError(
            f"{workflow_name} verification artifact escapes its run root"
        ) from exc
    if verification_path.is_symlink() or not verification_path.is_file():
        raise RuntimeError(f"{workflow_name} verification artifact is missing")
    try:
        stored = json.loads(verification_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{workflow_name} verification artifact is unreadable") from exc
    if _canonical_bytes(stored) != _canonical_bytes(verification):
        raise RuntimeError(
            f"{workflow_name} verification artifact does not match result"
        )
    if verification.get("overall_status") == "pass":
        for item in verification.get("requirements", []):
            if not isinstance(item, Mapping):
                raise RuntimeError("passing hard-constraint evidence is malformed")
            measurement = item.get("measurement")
            if (
                item.get("status") != "pass"
                or not isinstance(measurement, Mapping)
                or measurement.get("status") != "measured"
            ):
                raise RuntimeError("passing hard constraint lacks measured evidence")
            try:
                finite_number(measurement.get("value"), "hard-constraint measurement")
            except ValueError as exc:
                raise RuntimeError(
                    "passing hard constraint lacks a finite measured value"
                ) from exc
    manifest_path = output / DIRECTORY_MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError(f"{workflow_name} experiment directory manifest is missing")
    manifest = read_directory_manifest(output, verify=True)
    verification_inside_output = verification_path.relative_to(output).as_posix()
    if (
        manifest.directory_kind != "experiment"
        or manifest.state != "succeeded"
        or manifest.entity_id != result["experiment_id"]
        or verification_inside_output not in {item.path for item in manifest.artifacts}
    ):
        raise RuntimeError("experiment directory manifest does not bind verification")
    counts = verification["counts"]
    evidence = {
        "experiment_id": result["experiment_id"],
        "output_directory": relative_output,
        "verification_path": relative_verification,
        "verification_sha256": _sha256_file(verification_path),
        "directory_manifest_path": manifest_path.relative_to(root).as_posix(),
        "directory_manifest_sha256": _sha256_file(manifest_path),
        "overall_status": verification["overall_status"],
        "counts": {name: int(counts[name]) for name in ("pass", "fail", "unverified")},
    }
    return evidence, objective_result(verification, objective)


__all__ = [
    "OBJECTIVE_GOALS",
    "MAX_OBJECTIVES",
    "finite_number",
    "normalize_single_objective",
    "normalize_objectives",
    "objective_result",
    "objective_vector",
    "pareto_dominates",
    "pareto_fronts",
    "weighted_compromise",
    "validate_ranked_experiment_evidence",
]
