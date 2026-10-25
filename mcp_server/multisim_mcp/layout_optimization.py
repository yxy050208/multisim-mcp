"""Bounded single-board layout candidate search and ranking.

This module deliberately sits below MCP and above the schematic builder.  It
does not invent electrical connectivity or edit an ``.ms14`` file.  Callers
provide a validator (normally ``validate_layout_positions``) and receive a
deterministic ranking of bounded position candidates.  The scope is basic
single-board readability and routing quality; cross-board joint placement and
global routing remain outside this open-source layer.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


def _normalise_positions(
    positions: Mapping[str, Sequence[float] | Mapping[str, float]],
) -> dict[str, list[float]]:
    if not isinstance(positions, Mapping) or not positions:
        raise ValueError("base_positions must be a non-empty mapping")
    result: dict[str, list[float]] = {}
    for refdes, value in positions.items():
        name = str(refdes).strip()
        if not name:
            raise ValueError("every position must be [x, y] or {x, y}")
        # Accept both the optimization input shape and the ``placements``
        # shape returned by ``validate_layout_positions``.  This keeps the
        # normal validate -> optimize loop lossless for MCP callers.
        if isinstance(value, Mapping):
            value = (value.get("x"), value.get("y"))
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
            raise ValueError("every position must be [x, y] or {x, y}")
        try:
            x, y = float(value[0]), float(value[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("every position must contain finite numbers") from exc
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("every position must contain finite numbers")
        result[name] = [x, y]
    return result


def generate_local_layout_candidates(
    base_positions: Mapping[str, Sequence[float] | Mapping[str, float]],
    *,
    step: float = 270.0,
    moves: int = 1,
    max_candidates: int = 32,
    fixed_refdes: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Generate deterministic one-component local moves around a baseline.

    The baseline is always first.  Each movable component is shifted in the
    order ``left, right, up, down`` for each distance from one to ``moves``.
    The candidate budget is a hard bound, so a caller cannot accidentally
    turn a local layout check into an unbounded search.
    """
    if not isinstance(step, (int, float)) or isinstance(step, bool) or not math.isfinite(float(step)) or step <= 0:
        raise ValueError("step must be a finite positive number")
    if isinstance(moves, bool) or not isinstance(moves, int) or moves < 1:
        raise ValueError("moves must be a positive integer")
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or max_candidates < 1:
        raise ValueError("max_candidates must be a positive integer")
    base = _normalise_positions(base_positions)
    # Ground is a board reference anchor.  Moving it changes every return
    # path and is never a useful local readability move, so keep both common
    # spellings fixed even when the caller omits ``fixed_refdes``.
    fixed = {"0", "GND"}
    fixed.update(str(item).strip() for item in fixed_refdes)
    refs = [refdes for refdes in sorted(base) if refdes not in fixed]
    candidates: list[dict[str, Any]] = [
        {"candidate_id": "baseline", "positions": {key: value[:] for key, value in base.items()}}
    ]
    directions = ((-1.0, 0.0, "left"), (1.0, 0.0, "right"), (0.0, -1.0, "up"), (0.0, 1.0, "down"))
    for distance in range(1, moves + 1):
        for refdes in refs:
            for dx, dy, label in directions:
                if len(candidates) >= max_candidates:
                    return candidates
                positions = {key: value[:] for key, value in base.items()}
                positions[refdes][0] += dx * float(step) * distance
                positions[refdes][1] += dy * float(step) * distance
                candidates.append({
                    "candidate_id": f"{refdes}:{label}:{distance}",
                    "positions": positions,
                })
    return candidates


def score_layout_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a builder/validator result into a stable layout score."""
    if not isinstance(result, Mapping):
        raise ValueError("layout result must be an object")
    validation = result.get("layout_validation", result)
    if not isinstance(validation, Mapping):
        raise ValueError("layout_validation must be an object")
    findings = validation.get("findings", [])
    if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes)):
        findings = []
    error_count = sum(
        1 for item in findings
        if isinstance(item, Mapping) and item.get("severity") == "error"
    )
    raw_crossings = validation.get("crossings_per_wire", math.inf)
    try:
        crossings = float(raw_crossings)
    except (TypeError, ValueError):
        crossings = math.inf
    if not math.isfinite(crossings) or crossings < 0:
        crossings = math.inf
    raw_fallbacks = result.get("wire_fallbacks", result.get("fallback_shifted_wires", 0))
    try:
        fallbacks = int(raw_fallbacks)
    except (TypeError, ValueError):
        fallbacks = 0
    fallbacks = max(0, fallbacks)
    raw_electrical_score = result.get("electrical_score", math.inf)
    try:
        electrical_score = float(raw_electrical_score)
    except (TypeError, ValueError):
        electrical_score = math.inf
    if not math.isfinite(electrical_score):
        electrical_score = math.inf
    def _finite_metric(name: str, default: float) -> float:
        try:
            value = float(result.get(name, default))
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) and value >= 0 else default

    wire_length = _finite_metric("wire_length", math.inf)
    wire_bends = _finite_metric("wire_bends", math.inf)
    placement_span = _finite_metric("placement_span", math.inf)
    status = str(validation.get("status", "fail"))
    functional_status = result.get("functional_status", result.get("electrical_status"))
    if functional_status is None:
        functional_status = result.get("verification_status")
    if functional_status is None:
        evidence = result.get("experiment_evidence")
        if isinstance(evidence, Mapping):
            functional_status = evidence.get("overall_status")
    if functional_status is None:
        verification = result.get("verification")
        if isinstance(verification, Mapping):
            functional_status = verification.get("overall_status")
    functional_text = None if functional_status is None else str(functional_status)
    accepted_functional_statuses = {"pass", "accepted", "verified", "native-verified"}
    functional_pass = (
        functional_text is None
        or functional_text in accepted_functional_statuses
        or functional_text.startswith("passed-")
    )
    # A fallback elbow is still syntactically connected, but it means the
    # obstacle-aware router could not prove a clean route.  Keep that
    # candidate in the evidence list while excluding it from delivery.
    hard_pass = status == "pass" and error_count == 0 and functional_pass and fallbacks == 0
    return {
        "hard_pass": hard_pass,
        "status": status,
        "error_count": error_count,
        "wire_fallbacks": fallbacks,
        "electrical_score": electrical_score,
        "crossings_per_wire": crossings,
        "score": [
            0 if hard_pass else 1,
            electrical_score,
            fallbacks,
            crossings,
            wire_bends,
            wire_length,
            placement_span,
            error_count,
        ],
        "wire_length": wire_length,
        "wire_bends": wire_bends,
        "placement_span": placement_span,
        "functional_status": functional_text,
    }


def rank_layout_candidates(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Rank evaluated candidates, preferring valid clean routes deterministically."""
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)) or not candidates:
        raise ValueError("candidates must be a non-empty list")
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, Mapping):
            raise ValueError("layout candidate must be an object")
        candidate_id = str(item.get("candidate_id", "")).strip()
        if not candidate_id or candidate_id in seen:
            raise ValueError("candidate_id must be unique and non-empty")
        seen.add(candidate_id)
        result = item.get("result")
        if not isinstance(result, Mapping):
            raise ValueError("layout candidate result must be an object")
        record = {
            "candidate_id": candidate_id,
            "positions": item.get("positions"),
            "result": result,
            "quality": score_layout_result(result),
        }
        ranked.append(record)
    ranked.sort(
        key=lambda item: (
            item["quality"]["score"],
            0 if item["candidate_id"] == "baseline" else 1,
            item["candidate_id"],
        )
    )
    selected = ranked[0]
    feasible = any(item["quality"]["hard_pass"] for item in ranked)
    return {
        "schema_version": 1,
        "status": "pass" if feasible else "no-feasible-layout",
        "selected": selected if feasible else None,
        "candidates": ranked,
        "evaluated_count": len(ranked),
    }


def rank_electrical_layout_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Rank parameter candidates after attaching their layout evaluations.

    Each item must contain ``candidate_id``, ``positions``, ``electrical_status``
    and a normalized ``electrical_score`` (lower is better), plus a
    ``layout_result`` returned by the geometry/materialization evaluator.
    Electrical failure is a hard gate; passing electrical candidates are then
    ranked with the same deterministic layout rules as the single-board path.
    """
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)) or not candidates:
        raise ValueError("candidates must be a non-empty list")
    merged: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, Mapping):
            raise ValueError("electrical-layout candidate must be an object")
        layout_result = item.get("layout_result")
        if not isinstance(layout_result, Mapping):
            raise ValueError("electrical-layout candidate requires layout_result")
        electrical_status = item.get("electrical_status")
        if electrical_status is None:
            raise ValueError("electrical-layout candidate requires electrical_status")
        result = dict(layout_result)
        result["functional_status"] = electrical_status
        result["electrical_score"] = item.get("electrical_score", math.inf)
        merged.append({
            "candidate_id": item.get("candidate_id"),
            "positions": item.get("positions"),
            "result": result,
        })
    return rank_layout_candidates(merged)


def rank_optimization_directory_candidates(
    optimization_output_dir: str | Path,
    layout_results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Bridge a persisted parameter optimization to layout/materialization results.

    ``optimization.json`` remains the source of truth for electrical
    candidate status and objective score.  ``layout_results`` is keyed by the
    persisted ``evaluation_id`` and may contain either a direct layout result
    or the ``materialized`` result returned by the MCP adapter.  Missing layout
    evidence fails closed instead of allowing an electrical-only candidate to
    win the combined ranking.  The prerelease ``optimization-state.json``
    spelling is accepted as a read-only compatibility fallback.
    """
    root = Path(optimization_output_dir).expanduser().resolve()
    # ``DesignOptimizationService`` writes ``optimization.json``.  Keep the
    # earlier ``optimization-state.json`` spelling as a read-only compatibility
    # fallback for directories produced by the prerelease bridge.
    state_path = root / "optimization.json"
    if state_path.is_symlink() or not state_path.is_file():
        legacy_state_path = root / "optimization-state.json"
        if legacy_state_path.is_symlink() or not legacy_state_path.is_file():
            raise ValueError(f"optimization state is missing: {state_path}")
        state_path = legacy_state_path
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("optimization state is unreadable") from exc
    if not isinstance(state, Mapping) or not isinstance(state.get("evaluations"), list):
        raise ValueError("optimization state evaluations are invalid")
    if not isinstance(layout_results, Mapping):
        raise ValueError("layout_results must be an object keyed by evaluation_id")

    candidates: list[dict[str, Any]] = []
    for evaluation in state["evaluations"]:
        if not isinstance(evaluation, Mapping):
            raise ValueError("optimization evaluation must be an object")
        evaluation_id = evaluation.get("evaluation_id")
        if not isinstance(evaluation_id, str) or not evaluation_id.strip():
            raise ValueError("optimization evaluation_id is invalid")
        objective = evaluation.get("objective")
        score = objective.get("score", math.inf) if isinstance(objective, Mapping) else math.inf
        electrical_status = "pass" if evaluation.get("status") == "feasible" else "target-not-met"
        supplied = layout_results.get(evaluation_id)
        if (
            isinstance(supplied, Mapping)
            and "layout_validation" not in supplied
            and isinstance(supplied.get("materialized"), Mapping)
        ):
            supplied = supplied["materialized"]
        if not isinstance(supplied, Mapping):
            supplied = {
                "layout_validation": {
                    "status": "fail",
                    "findings": [{
                        "severity": "error",
                        "code": "layout-evidence-missing",
                        "message": "no materialized layout result was supplied for this electrical candidate",
                    }],
                }
            }
        positions = supplied.get("positions", {})
        candidates.append({
            "candidate_id": evaluation_id,
            "positions": positions,
            "electrical_status": electrical_status,
            "electrical_score": score,
            "layout_result": supplied,
        })
    ranked = rank_electrical_layout_candidates(candidates)
    ranked["optimization_output_dir"] = str(root)
    ranked["electrical_candidate_count"] = len(candidates)
    return ranked


def optimize_single_board_layout(
    base_positions: Mapping[str, Sequence[float] | Mapping[str, float]],
    evaluator: Callable[[Mapping[str, Sequence[float]]], Mapping[str, Any]],
    *,
    step: float = 270.0,
    moves: int = 1,
    max_candidates: int = 32,
    fixed_refdes: Sequence[str] = (),
) -> dict[str, Any]:
    """Evaluate a bounded local layout search using an injected validator."""
    if not callable(evaluator):
        raise ValueError("evaluator must be callable")
    evaluated: list[dict[str, Any]] = []
    for candidate in generate_local_layout_candidates(
        base_positions,
        step=step,
        moves=moves,
        max_candidates=max_candidates,
        fixed_refdes=fixed_refdes,
    ):
        positions = candidate["positions"]
        try:
            result = evaluator(positions)
            if not isinstance(result, Mapping):
                raise ValueError("evaluator must return an object")
        except Exception as exc:  # noqa: BLE001 - preserve failed candidate evidence
            result = {
                "layout_validation": {
                    "status": "fail",
                    "findings": [{"severity": "error", "code": "evaluator-error", "message": str(exc)}],
                }
            }
        evaluated.append({
            "candidate_id": candidate["candidate_id"],
            "positions": positions,
            "result": result,
        })
    return rank_layout_candidates(evaluated)


__all__ = [
    "generate_local_layout_candidates",
    "score_layout_result",
    "rank_layout_candidates",
    "rank_electrical_layout_candidates",
    "rank_optimization_directory_candidates",
    "optimize_single_board_layout",
]
