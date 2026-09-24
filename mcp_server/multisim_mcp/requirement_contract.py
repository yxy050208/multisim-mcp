"""Requirement contracts and pre-flight conflict analysis.

This module deliberately stops before simulation.  It turns a set of measured
hard constraints and soft objectives into a small, immutable review envelope
that can be handed to the existing verification and optimisation services.
The checks are conservative: a clean result means only that the declared
contract is internally coherent, not that a circuit is physically feasible.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from .design_verification import METRICS, OPERATORS, validate_measurement_requests


REQUIREMENT_CONTRACT_SCHEMA_VERSION: Final = 1
REQUIREMENT_CONTRACT_KIND: Final = "multisim-mcp-requirement-review"
MAX_HARD_CONSTRAINTS: Final = 100
MAX_SOFT_OBJECTIVES: Final = 32
MAX_PREFERENCES: Final = 32
MAX_ASSUMPTIONS: Final = 32
MAX_TEXT_LENGTH: Final = 16_384
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SOFT_GOALS: Final = frozenset({"minimize", "maximize", "target"})


def _digest(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _bounded_text(value: object, name: str, *, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    result = value.strip()
    if len(result) > maximum or "\x00" in result:
        raise ValueError(f"{name} is too long or contains NUL")
    return result


def _identity(item: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(item["metric"]),
        str(item["signal"]),
        str(item.get("unit") or ""),
    )


def _interval(item: Mapping[str, Any]) -> tuple[float, float]:
    operator = item["operator"]
    if operator == "at_least":
        return float(item["target"]), math.inf
    if operator == "at_most":
        return -math.inf, float(item["target"])
    if operator == "between":
        return float(item["lower"]), float(item["upper"])
    target = float(item["target"])
    tolerances: list[float] = []
    if "tolerance_abs" in item:
        tolerances.append(float(item["tolerance_abs"]))
    if "tolerance_percent" in item:
        tolerances.append(abs(target) * float(item["tolerance_percent"]) / 100.0)
    # This mirrors the verification engine: when both are supplied, both are
    # limits and the stricter one applies.
    allowance = min(tolerances)
    return target - allowance, target + allowance


def _relaxation_suggestions(
    items: Sequence[Mapping[str, Any]], lower: float, upper: float
) -> list[dict[str, Any]]:
    """Suggest the smallest bound changes that could restore an interval.

    Suggestions are deliberately local and conservative.  They do not assert
    physical feasibility and are only intended to help a user decide which
    requirement to review before running a new baseline experiment.
    """
    suggestions: list[dict[str, Any]] = []
    for item in items:
        operator = item["operator"]
        identifier = item["id"]
        item_low, item_high = _interval(item)
        if item_low > upper:
            if operator == "at_least" and math.isfinite(upper):
                suggestions.append(
                    {
                        "id": identifier,
                        "field": "target",
                        "current": item["target"],
                        "suggested": upper,
                        "reason": "降低最低值，使其不高于其他硬约束的上限",
                    }
                )
            elif operator == "between" and math.isfinite(upper):
                suggestions.append(
                    {
                        "id": identifier,
                        "field": "lower",
                        "current": item["lower"],
                        "suggested": upper,
                        "reason": "降低区间下限，使其与其他硬约束相交",
                    }
                )
            elif operator == "approximately" and math.isfinite(upper):
                needed = abs(float(item["target"]) - upper)
                suggestions.append(
                    {
                        "id": identifier,
                        "field": "tolerance_abs",
                        "current": item.get("tolerance_abs"),
                        "suggested": needed,
                        "reason": "增大允许误差，使目标区间覆盖其他硬约束上限",
                    }
                )
        if item_high < lower:
            if operator == "at_most" and math.isfinite(lower):
                suggestions.append(
                    {
                        "id": identifier,
                        "field": "target",
                        "current": item["target"],
                        "suggested": lower,
                        "reason": "提高最高值，使其不低于其他硬约束的下限",
                    }
                )
            elif operator == "between" and math.isfinite(lower):
                suggestions.append(
                    {
                        "id": identifier,
                        "field": "upper",
                        "current": item["upper"],
                        "suggested": lower,
                        "reason": "提高区间上限，使其与其他硬约束相交",
                    }
                )
            elif operator == "approximately" and math.isfinite(lower):
                needed = abs(lower - float(item["target"]))
                suggestions.append(
                    {
                        "id": identifier,
                        "field": "tolerance_abs",
                        "current": item.get("tolerance_abs"),
                        "suggested": needed,
                        "reason": "增大允许误差，使目标区间覆盖其他硬约束下限",
                    }
                )
    # Keep the result deterministic and bounded when many constraints share a
    # signal.  Duplicates can arise when two equivalent bounds are declared.
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for suggestion in suggestions:
        key = (
            suggestion["id"],
            suggestion["field"],
            json.dumps(suggestion.get("suggested"), ensure_ascii=False, sort_keys=True),
        )
        unique.setdefault(key, suggestion)
    return list(unique.values())[:16]


def _normalise_preferences(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("preferences must be an array")
    if len(value) > MAX_PREFERENCES:
        raise ValueError(f"preferences must contain at most {MAX_PREFERENCES} items")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ValueError(f"preferences[{index}] must be an object")
        item = dict(raw)
        unknown = set(item) - {"id", "kind", "description", "weight", "value"}
        if unknown:
            raise ValueError(
                f"unknown fields for preferences[{index}]: " + ", ".join(sorted(unknown))
            )
        identifier = _bounded_text(item.get("id"), f"preferences[{index}].id", maximum=64)
        if not _ID_RE.fullmatch(identifier):
            raise ValueError(f"invalid preference id: {identifier!r}")
        if identifier in seen:
            raise ValueError(f"duplicate requirement id: {identifier}")
        seen.add(identifier)
        kind = _bounded_text(item.get("kind"), f"preferences[{identifier}].kind", maximum=64)
        description = _bounded_text(
            item.get("description", kind), f"preferences[{identifier}].description", maximum=512
        )
        weight = _finite(item.get("weight", 1.0), f"preferences[{identifier}].weight")
        if weight < 0:
            raise ValueError(f"preferences[{identifier}].weight must not be negative")
        result.append(
            {
                "id": identifier,
                "kind": kind,
                "description": description,
                "weight": round(weight, 8),
                "value": item.get("value"),
            }
        )
    total = sum(item["weight"] for item in result)
    if result and total <= 0:
        raise ValueError("preferences must contain a positive weight")
    if total > 0:
        for item in result:
            item["weight"] = round(item["weight"] / total, 8)
    return result


def _normalise_objectives(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("soft_objectives must be an array")
    if len(value) > MAX_SOFT_OBJECTIVES:
        raise ValueError(f"soft_objectives must contain at most {MAX_SOFT_OBJECTIVES} items")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ValueError(f"soft_objectives[{index}] must be an object")
        item = dict(raw)
        unknown = set(item) - {
            "id", "metric", "signal", "reference_signal", "x_signal", "unit",
            "parameters", "goal", "target", "weight", "description",
        }
        if unknown:
            raise ValueError(
                f"unknown fields for soft_objectives[{index}]: " + ", ".join(sorted(unknown))
            )
        identifier = _bounded_text(item.get("id"), f"soft_objectives[{index}].id", maximum=64)
        if not _ID_RE.fullmatch(identifier):
            raise ValueError(f"invalid objective id: {identifier!r}")
        if identifier in seen:
            raise ValueError(f"duplicate requirement id: {identifier}")
        seen.add(identifier)
        metric = _bounded_text(item.get("metric"), f"soft_objectives[{identifier}].metric", maximum=64).lower()
        if metric not in METRICS:
            raise ValueError(f"unsupported metric for objective {identifier}: {metric!r}")
        signal = _bounded_text(item.get("signal"), f"soft_objectives[{identifier}].signal", maximum=256)
        goal = _bounded_text(item.get("goal"), f"soft_objectives[{identifier}].goal", maximum=16).lower()
        if goal not in _SOFT_GOALS:
            raise ValueError(f"objective {identifier}.goal must be minimize, maximize, or target")
        if goal == "target" and "target" not in item:
            raise ValueError(f"objective {identifier} requires target")
        target = _finite(item["target"], f"soft_objectives.{identifier}.target") if "target" in item else None
        weight = _finite(item.get("weight", 1.0), f"soft_objectives.{identifier}.weight")
        if weight < 0:
            raise ValueError(f"soft_objectives.{identifier}.weight must not be negative")
        result.append(
            {
                "id": identifier,
                "metric": metric,
                "signal": signal,
                "reference_signal": item.get("reference_signal"),
                "x_signal": item.get("x_signal"),
                "unit": item.get("unit") or "",
                "parameters": dict(item.get("parameters") or {}),
                "goal": goal,
                "target": target,
                "weight": round(weight, 8),
                "description": str(item.get("description") or "").strip(),
            }
        )
    # Reuse the same metric/signal/parameter contract as experiments so an
    # objective can be handed to the optimisation services without a second
    # interpretation of its measurement request.
    validate_measurement_requests(
        [
            {
                key: item[key]
                for key in ("id", "metric", "signal", "reference_signal", "x_signal", "unit", "parameters")
                if item.get(key) not in (None, "")
            }
            for item in result
        ]
    )
    total = sum(item["weight"] for item in result)
    if result and total <= 0:
        raise ValueError("soft_objectives must contain a positive weight")
    if total > 0:
        for item in result:
            item["weight"] = round(item["weight"] / total, 8)
    return result


def _optimization_handoff(
    hard_constraints: Sequence[Mapping[str, Any]],
    objectives: Sequence[Mapping[str, Any]],
    conflicts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project review data into an explicit optimizer handoff.

    Ranking services measure hard requirements, so a soft objective can only
    be promoted automatically when it identifies exactly one matching hard
    measurement. Ambiguous and unmeasured objectives remain visible instead
    of being guessed or silently dropped.
    """
    by_identity: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for item in hard_constraints:
        by_identity.setdefault(_identity(item), []).append(item)
    single_candidates: list[dict[str, Any]] = []
    multi_candidates: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    for objective in objectives:
        matches = by_identity.get(_identity(objective), [])
        if len(matches) == 1:
            candidate: dict[str, Any] = {
                "objective_id": objective["id"],
                "requirement_id": matches[0]["id"],
                "goal": objective["goal"],
                "weight": objective["weight"],
            }
            if objective.get("goal") == "target":
                candidate["target"] = objective["target"]
            single_candidates.append(candidate)
            multi_candidates.append({**candidate, "epsilon": 0.0})
        elif not matches:
            unmapped.append(
                {
                    "objective_id": objective["id"],
                    "reason": "no_matching_hard_measurement",
                    "message": "软目标没有同指标的硬测量约束，无法直接进入当前验收型优化器。",
                }
            )
        else:
            unmapped.append(
                {
                    "objective_id": objective["id"],
                    "reason": "ambiguous_hard_measurement",
                    "requirement_ids": [item["id"] for item in matches],
                    "message": "软目标匹配到多个硬测量约束，需要人工指定绑定关系。",
                }
            )
    if conflicts:
        state = "blocked"
    elif not objectives:
        state = "needs-objectives"
    elif unmapped:
        state = "partial"
    else:
        state = "ready"
    return {
        "state": state,
        "requirements": [dict(item) for item in hard_constraints],
        "single_objective_candidates": single_candidates,
        "multi_objective_candidates": multi_candidates,
        "unmapped_objectives": unmapped,
        "manual_review_required": bool(conflicts or unmapped),
    }


def review_design_requirements(
    hard_constraints: list[dict[str, Any]],
    *,
    soft_objectives: list[dict[str, Any]] | None = None,
    preferences: list[dict[str, Any]] | None = None,
    assumptions: list[str] | None = None,
    summary: str = "",
    title: str = "需求契约审查",
) -> dict[str, Any]:
    """Normalise and pre-flight a requirement contract without simulation."""
    if not isinstance(hard_constraints, list) or not hard_constraints:
        raise ValueError("hard_constraints must contain at least one requirement")
    if len(hard_constraints) > MAX_HARD_CONSTRAINTS:
        raise ValueError(f"hard_constraints must contain at most {MAX_HARD_CONSTRAINTS} items")
    if not isinstance(summary, str) or len(summary) > MAX_TEXT_LENGTH or "\x00" in summary:
        raise ValueError("summary is too long or contains NUL")
    if not isinstance(title, str) or not title.strip() or len(title) > 256:
        raise ValueError("title must be non-empty and at most 256 characters")
    normalized_hard = validate_measurement_requests(hard_constraints, requirements=True)
    objectives = _normalise_objectives(soft_objectives)
    prefs = _normalise_preferences(preferences)
    if assumptions is None:
        normalized_assumptions: list[str] = []
    else:
        if not isinstance(assumptions, list):
            raise ValueError("assumptions must be an array")
        if len(assumptions) > MAX_ASSUMPTIONS:
            raise ValueError(f"assumptions must contain at most {MAX_ASSUMPTIONS} items")
        normalized_assumptions = [
            _bounded_text(item, f"assumptions[{index}]", maximum=1024)
            for index, item in enumerate(assumptions)
        ]

    identifiers = {item["id"] for item in normalized_hard}
    for item in objectives + prefs:
        if item["id"] in identifiers:
            raise ValueError(f"duplicate requirement id: {item['id']}")
        identifiers.add(item["id"])

    conflicts: list[dict[str, Any]] = []
    relaxation_suggestions: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in normalized_hard:
        grouped.setdefault(_identity(item), []).append(item)
    for identity, items in grouped.items():
        lower = max(_interval(item)[0] for item in items)
        upper = min(_interval(item)[1] for item in items)
        if lower > upper:
            suggestions = _relaxation_suggestions(items, lower, upper)
            relaxation_suggestions.extend(suggestions)
            conflicts.append(
                {
                    "type": "non_overlapping_hard_constraints",
                    "ids": [item["id"] for item in items],
                    "metric": identity[0],
                    "signal": identity[1],
                    "unit": identity[2],
                    "intersection": None,
                    "relaxation_suggestions": suggestions,
                    "message": "同一测量信号的硬约束没有共同可行区间",
                }
            )

    warnings: list[dict[str, Any]] = []
    if not objectives:
        warnings.append(
            {
                "code": "no_soft_objectives",
                "message": "未声明软目标；优化器只能判断是否满足硬约束，无法选择更优候选。",
            }
        )
    for item in normalized_hard:
        if item["metric"] in {"frequency", "thd", "gain", "cutoff_frequency", "bandwidth"} and not item.get("unit"):
            warnings.append(
                {
                    "code": "missing_unit",
                    "id": item["id"],
                    "message": "建议为频率、增益或带宽类指标明确单位，避免跨工具解释不一致。",
                }
            )
    optimization_handoff = _optimization_handoff(
        normalized_hard,
        objectives,
        conflicts,
    )
    state = "conflict" if conflicts else ("ready-for-baseline" if normalized_hard else "needs-input")
    envelope: dict[str, Any] = {
        "schema_version": REQUIREMENT_CONTRACT_SCHEMA_VERSION,
        "kind": REQUIREMENT_CONTRACT_KIND,
        "title": title.strip(),
        "summary": summary.strip(),
        "state": state,
        "hard_constraints": normalized_hard,
        "soft_objectives": objectives,
        "preferences": prefs,
        "assumptions": normalized_assumptions,
        "conflicts": conflicts,
        "relaxation_suggestions": relaxation_suggestions,
        "optimization_handoff": optimization_handoff,
        "warnings": warnings,
        "simulation_started": False,
        "artifacts_generated": [],
        "next_step": "resolve_conflicts_before_baseline" if conflicts else "run_baseline_experiment",
    }
    envelope["contract_digest"] = _digest(envelope)
    return envelope


def validate_requirement_review(review: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an immutable requirement-review envelope before handoff.

    The digest protects the review boundary when a client stores or forwards
    the JSON between planning and optimization. It is an integrity check, not
    a proof that the circuit can satisfy the contract.
    """
    if not isinstance(review, Mapping):
        raise ValueError("requirement review must be an object")
    supplied = review.get("contract_digest")
    if not isinstance(supplied, str) or not re.fullmatch(r"[0-9a-f]{64}", supplied):
        raise ValueError("requirement review contract_digest is invalid")
    payload = dict(review)
    payload.pop("contract_digest", None)
    expected = _digest(payload)
    if not hmac.compare_digest(supplied, expected):
        raise ValueError("requirement review digest mismatch")
    if payload.get("schema_version") != REQUIREMENT_CONTRACT_SCHEMA_VERSION:
        raise ValueError("unsupported requirement review schema_version")
    if payload.get("kind") != REQUIREMENT_CONTRACT_KIND:
        raise ValueError("invalid requirement review kind")
    state = payload.get("state")
    if state not in {"conflict", "ready-for-baseline", "needs-input"}:
        raise ValueError("invalid requirement review state")
    return dict(review)


def apply_requirement_review_to_optimization_spec(
    spec: Mapping[str, Any],
    review: Mapping[str, Any],
    *,
    global_mode: bool = False,
    require_objectives: bool = True,
) -> dict[str, Any]:
    """Fill an optimization spec from a verified requirement review.

    Existing explicit requirements/objectives are preserved only when they
    agree with the review. Automatic objective projection is intentionally
    strict: a single-objective run needs exactly one matched objective, while
    a global run needs all objectives to be measurable and unambiguous.
    """
    if not isinstance(spec, Mapping):
        raise ValueError("optimization spec must be an object")
    verified = validate_requirement_review(review)
    if verified["state"] != "ready-for-baseline":
        raise ValueError("requirement review must be ready-for-baseline before optimization")
    handoff = verified.get("optimization_handoff")
    if not isinstance(handoff, Mapping):
        raise ValueError("requirement review has no complete optimizer handoff")
    handoff_state = handoff.get("state")
    if handoff_state not in ({"ready", "needs-objectives"} if not require_objectives else {"ready"}):
        raise ValueError("requirement review has no complete optimizer handoff")
    requirements = handoff.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("requirement review optimizer handoff has no requirements")
    result = dict(spec)
    explicit_requirements = result.get("requirements")
    if explicit_requirements not in (None, [], requirements):
        if _digest(explicit_requirements) != _digest(requirements):
            raise ValueError("optimization spec requirements do not match requirement review")
    result["requirements"] = [dict(item) for item in requirements]
    unmapped = handoff.get("unmapped_objectives") or []
    if unmapped and require_objectives:
        raise ValueError("requirement review contains unmapped objectives")
    if global_mode:
        candidates = handoff.get("multi_objective_candidates")
        if (not isinstance(candidates, list) or not candidates) and require_objectives:
            raise ValueError("requirement review has no multi-objective candidates")
        if candidates and not result.get("objectives"):
            result["objectives"] = [
                {
                    key: item[key]
                    for key in ("requirement_id", "goal", "target", "epsilon", "weight")
                    if key in item
                }
                for item in candidates
            ]
    elif not result.get("objective"):
        candidates = handoff.get("single_objective_candidates")
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise ValueError(
                "single-objective optimization requires exactly one matched soft objective"
            )
        candidate = candidates[0]
        result["objective"] = {
            key: candidate[key]
            for key in ("requirement_id", "goal", "target")
            if key in candidate
        }
    return result


__all__ = [
    "MAX_ASSUMPTIONS",
    "MAX_HARD_CONSTRAINTS",
    "MAX_PREFERENCES",
    "MAX_SOFT_OBJECTIVES",
    "REQUIREMENT_CONTRACT_KIND",
    "REQUIREMENT_CONTRACT_SCHEMA_VERSION",
    "apply_requirement_review_to_optimization_spec",
    "review_design_requirements",
    "validate_requirement_review",
]
