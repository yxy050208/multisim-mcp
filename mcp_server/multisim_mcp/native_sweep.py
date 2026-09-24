"""Validation helpers for transactional native Multisim parameter sweeps."""

from __future__ import annotations

import itertools
import hashlib
import json
import math
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .eda_core import DesignPatch, PatchOperation
from .preferred_values import format_spice_scalar, parse_spice_scalar


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _validate_embedded_digest(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    digest = value.get(field)
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError(f"value must include a valid {field}")
    unsigned = dict(value)
    unsigned.pop(field, None)
    if digest != _digest(unsigned):
        raise ValueError(f"{field} does not match value")
    return dict(value)


def prepare_native_sweep(
    readiness: Mapping[str, Any],
    candidates: list[Any],
    approval: Mapping[str, Any],
    *,
    max_combinations: int = 64,
) -> tuple[list[dict[str, float]], list[str]]:
    """Validate approval/readiness and expand bounded candidate value grids."""
    if not isinstance(readiness, Mapping) or readiness.get("state") != "ready-for-com-parameter-sweep":
        raise ValueError("readiness must be ready-for-com-parameter-sweep")
    _validate_embedded_digest(readiness, "readiness_digest")
    if not isinstance(approval, Mapping):
        raise ValueError("approval must be an object")
    allowed = {"approved", "runtime_gate", "restore_original_values", "review_note"}
    unknown = set(approval) - allowed
    if unknown:
        raise ValueError(f"approval contains unknown fields: {sorted(unknown)}")
    for key in ("approved", "runtime_gate", "restore_original_values"):
        if approval.get(key) is not True:
            raise ValueError(f"approval.{key} must be true")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates must be a non-empty array")
    if len(candidates) > 16:
        raise ValueError("candidates must contain at most 16 parameters")
    allowed_targets = {
        str(item.get("refdes") or item.get("target", "")).removesuffix(".value").casefold()
        for item in readiness.get("candidates", [])
        if isinstance(item, Mapping)
    }
    normalized: list[tuple[str, list[float]]] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, Mapping):
            raise ValueError("each candidate must be an object")
        refdes = item.get("refdes")
        values = item.get("values")
        if not isinstance(refdes, str) or not refdes.strip():
            raise ValueError("candidate.refdes must be a non-empty string")
        key = refdes.strip().casefold()
        if key in seen or key not in allowed_targets:
            raise ValueError(f"candidate {refdes!r} is not in readiness candidates")
        if not isinstance(values, list) or not values or len(values) > 16:
            raise ValueError(f"candidate {refdes}.values must contain 1..16 numbers")
        parsed: list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"candidate {refdes}.values must contain finite numbers")
            numeric = float(value)
            if not math.isfinite(numeric) or numeric <= 0:
                raise ValueError(f"candidate {refdes}.values must be positive finite numbers")
            parsed.append(numeric)
        normalized.append((refdes.strip(), parsed))
        seen.add(key)
    total = math.prod(len(values) for _, values in normalized)
    if total > max_combinations:
        raise ValueError(f"candidate grid contains {total} combinations; maximum is {max_combinations}")
    combinations = [
        {refdes: value for (refdes, _), value in zip(normalized, product)}
        for product in itertools.product(*(values for _, values in normalized))
    ]
    return combinations, [refdes for refdes, _ in normalized]


_METRICS = frozenset({"mean", "min", "max", "peak_to_peak", "rms", "final", "abs_max"})
_DIRECTIONS = frozenset({"minimize", "maximize", "target"})


def _numeric_series(payload: Any, signal: str) -> list[float]:
    """Extract one output series from the normalized COM result envelope."""
    if not isinstance(payload, Mapping):
        return []
    rows = payload.get("rows")
    if isinstance(rows, (list, tuple)):
        numeric_rows = [
            [float(value) for value in row if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))]
            for row in rows
            if isinstance(row, (list, tuple))
        ]
        numeric_rows = [row for row in numeric_rows if row]
        if numeric_rows:
            # A single-output request normally has one row; for multi-row envelopes
            # the final row is the requested signal rather than the shared x-axis.
            return numeric_rows[-1]
    results = payload.get("results")
    if isinstance(results, Mapping):
        nested = results.get(signal)
        if nested is None and len(results) == 1:
            nested = next(iter(results.values()))
        return _numeric_series(nested, signal)
    nested = payload.get(signal)
    return _numeric_series(nested, signal)


def _metric_value(series: list[float], metric: str) -> float:
    if metric == "mean":
        return sum(series) / len(series)
    if metric == "min":
        return min(series)
    if metric == "max":
        return max(series)
    if metric == "peak_to_peak":
        return max(series) - min(series)
    if metric == "rms":
        return math.sqrt(sum(value * value for value in series) / len(series))
    if metric == "final":
        return series[-1]
    return max(abs(value) for value in series)


def rank_native_sweep_results(
    sweep_result: Mapping[str, Any], objective: Mapping[str, Any]
) -> dict[str, Any]:
    """Rank completed native sweep records against one explicit scalar objective."""
    if not isinstance(sweep_result, Mapping) or sweep_result.get("state") != "completed":
        raise ValueError("sweep_result must be a completed native sweep result")
    records = sweep_result.get("results")
    if not isinstance(records, list) or not records or len(records) > 64:
        raise ValueError("sweep_result.results must contain 1..64 records")
    if not isinstance(objective, Mapping):
        raise ValueError("objective must be an object")
    allowed = {"signal", "metric", "direction", "target"}
    unknown = set(objective) - allowed
    if unknown:
        raise ValueError(f"objective contains unknown fields: {sorted(unknown)}")
    signal = objective.get("signal")
    metric = str(objective.get("metric", "")).strip().lower()
    direction = str(objective.get("direction", "")).strip().lower()
    if not isinstance(signal, str) or not signal.strip() or len(signal) > 256 or "\x00" in signal:
        raise ValueError("objective.signal must be a non-empty signal name")
    if metric not in _METRICS:
        raise ValueError(f"objective.metric must be one of: {', '.join(sorted(_METRICS))}")
    if direction not in _DIRECTIONS:
        raise ValueError("objective.direction must be minimize, maximize, or target")
    target = objective.get("target")
    if direction == "target":
        if isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(float(target)):
            raise ValueError("objective.target must be a finite number for target direction")
        target = float(target)
    elif target is not None:
        raise ValueError("objective.target is only valid with target direction")

    ranked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            skipped.append({"index": index, "reason": "record is not an object"})
            continue
        series = _numeric_series(record.get("analysis"), signal.strip())
        if not series:
            skipped.append({"index": index, "reason": "signal has no finite samples"})
            continue
        value = _metric_value(series, metric)
        score = value if direction == "minimize" else -value if direction == "maximize" else abs(value - float(target))
        minimum = min(series)
        maximum = max(series)
        dynamic_range = maximum - minimum
        all_zero = all(sample == 0.0 for sample in series)
        constant = len(series) > 1 and dynamic_range <= max(1e-12, max(abs(sample) for sample in series) * 1e-9)
        quality = {
            "state": "degenerate-zero" if all_zero else "low-information" if len(series) < 2 else "usable",
            "sample_count": len(series),
            "finite": True,
            "all_zero": all_zero,
            "constant": constant,
            "dynamic_range": dynamic_range,
        }
        ranked.append(
            {
                "index": index,
                "parameters": record.get("parameters", {}),
                "signal": signal.strip(),
                "metric": metric,
                "direction": direction,
                "sample_count": len(series),
                "value": value,
                "score": score,
                "quality": quality,
            }
        )
    ranked.sort(key=lambda item: (float(item["score"]), int(item["index"])))
    quality_counts = {
        "usable": sum(item["quality"]["state"] == "usable" for item in ranked),
        "low_information": sum(item["quality"]["state"] == "low-information" for item in ranked),
        "degenerate_zero": sum(item["quality"]["state"] == "degenerate-zero" for item in ranked),
    }
    warnings: list[str] = []
    if ranked and quality_counts["degenerate_zero"] == len(ranked):
        warnings.append("all scored outputs are exactly zero; verify source excitation and output probe")
    payload: dict[str, Any] = {
        "state": "completed" if ranked else "no-scorable-results",
        "quality_state": "degenerate-output" if ranked and quality_counts["degenerate_zero"] == len(ranked) else "valid",
        "objective": {"signal": signal.strip(), "metric": metric, "direction": direction, **({"target": target} if target is not None else {})},
        "ranked_results": ranked,
        "skipped_results": skipped,
        "quality_counts": quality_counts,
        "warnings": warnings,
        "best": ranked[0] if ranked else None,
        "source_mutated": False,
    }
    original_values = sweep_result.get("original_values")
    if isinstance(original_values, Mapping):
        payload["original_values"] = dict(original_values)
    circuit = sweep_result.get("circuit")
    if isinstance(circuit, Mapping):
        payload["circuit"] = dict(circuit)
    payload["ranking_digest"] = _digest(payload)
    return payload


def prepare_native_sweep_patch(
    readiness: Mapping[str, Any], ranking: Mapping[str, Any]
) -> dict[str, Any]:
    """Convert the best verified native result into a standard reversible DesignPatch."""
    if not isinstance(readiness, Mapping) or readiness.get("state") != "ready-for-com-parameter-sweep":
        raise ValueError("readiness must be ready-for-com-parameter-sweep")
    verified_readiness = _validate_embedded_digest(readiness, "readiness_digest")
    if not isinstance(ranking, Mapping) or ranking.get("state") != "completed":
        raise ValueError("ranking must be a completed native sweep ranking")
    verified_ranking = _validate_embedded_digest(ranking, "ranking_digest")
    if verified_ranking.get("quality_state") == "degenerate-output":
        raise ValueError("ranking contains only zero outputs; review excitation and probes before patching")
    design_id = verified_readiness.get("design_id")
    revision = verified_readiness.get("design_revision")
    if not isinstance(design_id, str) or not design_id.strip():
        raise ValueError("readiness.design_id is required")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("readiness.design_revision must be a non-negative integer")
    best = verified_ranking.get("best")
    originals = verified_ranking.get("original_values")
    if not isinstance(best, Mapping) or not isinstance(best.get("parameters"), Mapping):
        raise ValueError("ranking.best.parameters is required")
    if not isinstance(originals, Mapping):
        raise ValueError("ranking.original_values is required")
    allowed = {
        str(item.get("refdes") or item.get("target", "")).removesuffix(".value").casefold(): item
        for item in verified_readiness.get("candidates", [])
        if isinstance(item, Mapping)
    }
    operations: list[PatchOperation] = []
    for refdes, after in best["parameters"].items():
        if not isinstance(refdes, str) or refdes.casefold() not in allowed:
            raise ValueError(f"ranked parameter {refdes!r} is not an approved readiness candidate")
        original_numeric = originals.get(refdes)
        if original_numeric is None:
            original_numeric = next(
                (value for key, value in originals.items() if isinstance(key, str) and key.casefold() == refdes.casefold()),
                None,
            )
        for label, value in (("before", original_numeric), ("after", after)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{refdes} {label} value must be a positive finite number")
        before = allowed[refdes.casefold()].get("value")
        if not isinstance(before, str) or not before.strip():
            raise ValueError(f"readiness candidate {refdes} must preserve its design value text")
        before_numeric = float(parse_spice_scalar(before.strip()))
        if not math.isclose(before_numeric, float(original_numeric), rel_tol=1e-9, abs_tol=1e-18):
            raise ValueError(f"readiness candidate {refdes} does not match the measured COM value")
        if float(original_numeric) == float(after):
            continue
        operations.append(
            PatchOperation(
                operation="set_component_value",
                target=f"{refdes}.value",
                before=before.strip(),
                after=format_spice_scalar(Decimal(str(float(after)))),
                reason="Selected by an integrity-checked native Multisim parameter sweep ranking.",
            )
        )
    if not operations:
        raise ValueError("best native sweep result does not change any component value")
    patch = DesignPatch(
        patch_id=f"native-sweep-{verified_ranking['ranking_digest'][:24]}",
        design_id=design_id.strip(),
        base_revision=revision,
        operations=tuple(operations),
        description="Apply the best reviewed native Multisim parameter sweep candidate.",
        metadata={
            "source": "native-multisim-parameter-sweep",
            "readiness_digest": verified_readiness["readiness_digest"],
            "ranking_digest": verified_ranking["ranking_digest"],
            "objective": verified_ranking.get("objective", {}),
        },
    )
    payload: dict[str, Any] = {
        "state": "ready-for-approval",
        "patch": patch.to_dict(),
        "readiness_digest": verified_readiness["readiness_digest"],
        "ranking_digest": verified_ranking["ranking_digest"],
        "source_mutated": False,
        "next_step": "approve_verified_patch_application",
    }
    if isinstance(verified_ranking.get("circuit"), Mapping):
        payload["circuit"] = dict(verified_ranking["circuit"])
    payload["draft_digest"] = _digest(payload)
    return payload


def validate_native_sweep_patch_draft(draft: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a non-mutating native sweep patch draft before copy application."""
    if not isinstance(draft, Mapping) or draft.get("state") != "ready-for-approval":
        raise ValueError("draft must be ready-for-approval")
    verified = _validate_embedded_digest(draft, "draft_digest")
    patch = DesignPatch.from_dict(verified.get("patch"))
    if patch.metadata.get("source") != "native-multisim-parameter-sweep":
        raise ValueError("draft patch source is invalid")
    circuit = verified.get("circuit")
    if not isinstance(circuit, Mapping) or not isinstance(circuit.get("file"), str) or not circuit["file"].strip():
        raise ValueError("draft.circuit.file is required")
    verified["_patch"] = patch
    return verified


__all__ = [
    "prepare_native_sweep",
    "prepare_native_sweep_patch",
    "rank_native_sweep_results",
    "validate_native_sweep_patch_draft",
]
