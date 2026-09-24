"""Backend-neutral numerical comparison of completed experiment raw data."""

from __future__ import annotations

import bisect
import json
import math
from pathlib import Path
from typing import Any

from .experiment_resources import experiment_manifest, registered_experiment_root
from .spice_raw import parse_raw
from .spice_provenance import compare_spice_compatibility_audits


def _canonical_signal(value: str) -> str:
    return "".join(value.casefold().split())


def _series(parsed: dict[str, Any], column_index: int) -> tuple[list[float], list[float]]:
    points: dict[float, float] = {}
    for row in parsed.get("rows", []):
        if len(row) <= column_index:
            continue
        x = float(row[0])
        y = float(row[column_index])
        if math.isfinite(x) and math.isfinite(y):
            points[x] = y
    ordered = sorted(points.items())
    return [item[0] for item in ordered], [item[1] for item in ordered]


def _interpolate(xs: list[float], ys: list[float], x: float) -> float | None:
    if not xs or x < xs[0] or x > xs[-1]:
        return None
    index = bisect.bisect_left(xs, x)
    if index < len(xs) and xs[index] == x:
        return ys[index]
    if index == 0 or index >= len(xs):
        return None
    x0, x1 = xs[index - 1], xs[index]
    y0, y1 = ys[index - 1], ys[index]
    if x1 == x0:
        return y1
    ratio = (x - x0) / (x1 - x0)
    return y0 + ratio * (y1 - y0)


def _sample_indices(count: int, maximum: int) -> list[int]:
    if count <= maximum:
        return list(range(count))
    if maximum == 1:
        return [0]
    return [round(index * (count - 1) / (maximum - 1)) for index in range(maximum)]


def compare_raw_results(
    reference_raw: str | Path,
    candidate_raw: str | Path,
    *,
    signals: list[str] | None = None,
    absolute_tolerance: float = 1e-6,
    relative_tolerance_percent: float = 1.0,
    max_points: int = 2000,
) -> dict[str, Any]:
    """Compare common signals after linear alignment on the first raw column."""
    for value, name in (
        (absolute_tolerance, "absolute_tolerance"),
        (relative_tolerance_percent, "relative_tolerance_percent"),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
            raise ValueError(f"{name} must be a finite non-negative number")
    if isinstance(max_points, bool) or not isinstance(max_points, int) or not 1 <= max_points <= 100_000:
        raise ValueError("max_points must be between 1 and 100000")
    if signals is not None:
        if not isinstance(signals, list) or not 1 <= len(signals) <= 64:
            raise ValueError("signals must contain between 1 and 64 names")
        if any(not isinstance(item, str) or not item.strip() for item in signals):
            raise ValueError("signals must contain non-empty strings")

    reference = parse_raw(str(Path(reference_raw).expanduser().resolve()))
    candidate = parse_raw(str(Path(candidate_raw).expanduser().resolve()))
    reference_columns = {
        _canonical_signal(name): (index, name)
        for index, name in enumerate(reference["columns"])
    }
    candidate_columns = {
        _canonical_signal(name): (index, name)
        for index, name in enumerate(candidate["columns"])
    }
    common = set(reference_columns) & set(candidate_columns)
    common.discard(_canonical_signal(reference["columns"][0]))
    common.discard(_canonical_signal(candidate["columns"][0]))
    requested = (
        [_canonical_signal(item) for item in signals]
        if signals is not None
        else sorted(common)
    )
    missing = [item for item in requested if item not in common]
    if missing:
        raise ValueError("signals are not common to both experiments: " + ", ".join(missing))

    results: list[dict[str, Any]] = []
    for canonical in requested:
        reference_index, reference_name = reference_columns[canonical]
        candidate_index, candidate_name = candidate_columns[canonical]
        reference_x, reference_y = _series(reference, reference_index)
        candidate_x, candidate_y = _series(candidate, candidate_index)
        aligned: list[tuple[float, float, float]] = []
        for index in _sample_indices(len(reference_x), max_points):
            compared = _interpolate(candidate_x, candidate_y, reference_x[index])
            if compared is not None:
                aligned.append((reference_x[index], reference_y[index], compared))
        if not aligned:
            results.append(
                {
                    "signal": reference_name,
                    "candidate_signal": candidate_name,
                    "status": "unverified",
                    "point_count": 0,
                    "reason": "The signal domains do not overlap.",
                }
            )
            continue
        errors = [abs(reference_value - candidate_value) for _, reference_value, candidate_value in aligned]
        squared = [error * error for error in errors]
        reference_scale = max(abs(reference_value) for _, reference_value, _ in aligned)
        violations = sum(
            error
            > float(absolute_tolerance)
            + abs(reference_value) * float(relative_tolerance_percent) / 100.0
            for error, (_, reference_value, _) in zip(errors, aligned)
        )
        rmse = math.sqrt(sum(squared) / len(squared))
        results.append(
            {
                "signal": reference_name,
                "candidate_signal": candidate_name,
                "status": "pass" if violations == 0 else "fail",
                "point_count": len(aligned),
                "domain": {"start": aligned[0][0], "stop": aligned[-1][0]},
                "mean_absolute_error": sum(errors) / len(errors),
                "root_mean_square_error": rmse,
                "max_absolute_error": max(errors),
                "normalized_rmse_percent": (
                    rmse / reference_scale * 100.0 if reference_scale > 0 else None
                ),
                "violation_count": violations,
            }
        )
    counts = {
        status: sum(item["status"] == status for item in results)
        for status in ("pass", "fail", "unverified")
    }
    overall = "fail" if counts["fail"] else "pass" if counts["pass"] and not counts["unverified"] else "unverified"
    return {
        "schema_version": 1,
        "overall_status": overall,
        "reference": {
            "plotname": reference.get("header", {}).get("plotname", ""),
            "x_column": reference["columns"][0],
            "point_count": reference["n_points"],
        },
        "candidate": {
            "plotname": candidate.get("header", {}).get("plotname", ""),
            "x_column": candidate["columns"][0],
            "point_count": candidate["n_points"],
        },
        "tolerances": {
            "absolute": float(absolute_tolerance),
            "relative_percent": float(relative_tolerance_percent),
        },
        "counts": counts,
        "signals": results,
    }


def compare_registered_experiments(
    reference_experiment_id: str,
    candidate_experiment_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compare two registered experiment handles without accepting raw paths."""
    reference_root = registered_experiment_root(reference_experiment_id)
    candidate_root = registered_experiment_root(candidate_experiment_id)
    result = compare_raw_results(
        reference_root / "result.raw",
        candidate_root / "result.raw",
        **kwargs,
    )
    result["reference"].update(
        {
            "experiment_id": reference_experiment_id,
            "backend_id": experiment_manifest(reference_experiment_id)["backend_id"],
        }
    )
    result["candidate"].update(
        {
            "experiment_id": candidate_experiment_id,
            "backend_id": experiment_manifest(candidate_experiment_id)["backend_id"],
        }
    )
    audits: list[dict[str, Any] | None] = []
    for root in (reference_root, candidate_root):
        path = root / "spice-compatibility.json"
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            audits.append(None)
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = None
        audits.append(value if isinstance(value, dict) else None)
    result["input_and_solver_evidence"] = compare_spice_compatibility_audits(
        audits[0], audits[1]
    )
    return result


__all__ = ["compare_raw_results", "compare_registered_experiments"]
