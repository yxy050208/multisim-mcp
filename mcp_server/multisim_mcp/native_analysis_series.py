"""Strict sampled comparisons of native OP, transient and complex AC results.

Interpolation is disabled by default. A different native sampling axis,
missing data or downsampled response stays unverified unless a caller
explicitly opts into bounded linear resampling for independent board runs.
Passing this comparison proves agreement of returned samples, not continuous
time behavior, physical board behavior or an undeclared fixture condition.
"""

from __future__ import annotations

import csv
import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def _finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def extract_native_analysis_series(
    result: Mapping[str, Any], outputs: Sequence[str], analysis: str,
    options: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Decode native rows, retaining full axes, real values and AC imaginary values."""
    if analysis not in {"dc", "tran", "ac"}:
        raise ValueError("analysis must be one of: dc, tran, ac")
    nested = result.get("results")
    channels = nested if isinstance(nested, Mapping) else (
        {outputs[0]: result} if len(outputs) == 1 and result.get("output") == outputs[0] else {}
    )
    series: dict[str, dict[str, Any]] = {}
    for output in outputs:
        item: dict[str, Any] = {
            "status": "unverified", "analysis": analysis, "output": output,
            "axis": [], "real": [], "imaginary": [],
            "reason": "native output missing or not ready",
        }
        series[output] = item
        payload = channels.get(output)
        if result.get("ready") is not True or not isinstance(payload, Mapping):
            continue
        if payload.get("output", output) != output:
            item.update(status="invalid", reason="native output identity mismatch")
            continue
        rows = payload.get("rows")
        if not isinstance(rows, list) or len(rows) != (3 if analysis == "ac" else 2):
            item.update(status="invalid", reason="unexpected native row shape")
            continue
        if not all(isinstance(row, list) for row in rows):
            item.update(status="invalid", reason="native rows must be arrays")
            continue
        count = len(rows[0])
        if count == 0 or any(len(row) != count for row in rows):
            item.update(status="invalid", reason="empty or unequal native row lengths")
            continue
        if not all(_finite(value) for row in rows for value in row):
            item.update(status="invalid", reason="native rows contain non-finite or non-numeric data")
            continue
        axis, real = [list(map(float, row)) for row in rows[:2]]
        imaginary = list(map(float, rows[2])) if analysis == "ac" else [0.0] * count
        item.update(axis=axis, real=real, imaginary=imaginary, sample_count=count)
        native_count = payload.get("n_points", count)
        sampled_count = payload.get("sampled_points", count)
        if (
            isinstance(native_count, bool) or not isinstance(native_count, int)
            or isinstance(sampled_count, bool) or not isinstance(sampled_count, int)
            or sampled_count != count or native_count < count
        ):
            item.update(status="invalid", reason="native sample metadata contradicts rows")
            continue
        if native_count != count:
            item["reason"] = "native response was downsampled; increase max_points"
            continue
        if analysis == "dc":
            if count != 1:
                item.update(status="invalid", reason="operating point must contain one sample")
                continue
        elif (
            count < 2 or any(b <= a for a, b in zip(axis, axis[1:]))
            or axis[0] < 0 or (analysis == "ac" and axis[0] == 0)
        ):
            item.update(status="invalid", reason="native axis must be strictly increasing")
            continue
        bounds = None
        if analysis == "tran" and options and "duration" in options:
            bounds = (0.0, options["duration"])
        elif analysis == "ac" and options and {
            "start_frequency", "stop_frequency"
        } <= set(options):
            bounds = (options["start_frequency"], options["stop_frequency"])
        if bounds and not all(
            math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-12)
            for actual, expected in zip((axis[0], axis[-1]), bounds)
        ):
            item["reason"] = "native axis does not cover the requested analysis range"
            continue
        item.update(status="pass", reason="complete native sampled response")
    return series


def native_series_informative(analysis: str, series: Mapping[str, Mapping[str, Any]]) -> bool:
    """An all-zero AC trace cannot prove that AC excitation reached the board."""
    if not series or any(item.get("status") != "pass" for item in series.values()):
        return False
    if analysis != "ac":
        return True
    return any(
        math.hypot(real, imaginary) > 1e-15
        for item in series.values()
        for real, imaginary in zip(item["real"], item["imaginary"])
    )


def _resample_series(
    series: Mapping[str, Any], target_axis: Sequence[float],
) -> tuple[list[float], list[float]] | None:
    """Linearly resample one complete native series onto a checked axis.

    Native Multisim transient output uses an adaptive axis.  Resampling is an
    explicit opt-in for comparing independently opened board projects; the
    default comparison remains strict and refuses mismatched axes.
    """
    axis = [float(value) for value in series.get("axis", [])]
    real = [float(value) for value in series.get("real", [])]
    imaginary = [float(value) for value in series.get("imaginary", [])]
    if len(axis) < 2 or len(axis) != len(real) or len(axis) != len(imaginary):
        return None
    if any(not _finite(value) for row in (axis, real, imaginary) for value in row):
        return None
    if any(b <= a for a, b in zip(axis, axis[1:])):
        return None
    if not target_axis or target_axis[0] < axis[0] - 1e-12 or target_axis[-1] > axis[-1] + 1e-12:
        return None
    out_real: list[float] = []
    out_imaginary: list[float] = []
    for point in target_axis:
        x = float(point)
        if x <= axis[0]:
            index = 0
            out_real.append(real[index])
            out_imaginary.append(imaginary[index])
            continue
        if x >= axis[-1]:
            index = -1
            out_real.append(real[index])
            out_imaginary.append(imaginary[index])
            continue
        right = bisect_right(axis, x)
        left = right - 1
        span = axis[right] - axis[left]
        ratio = (x - axis[left]) / span
        out_real.append(real[left] + ratio * (real[right] - real[left]))
        out_imaginary.append(imaginary[left] + ratio * (imaginary[right] - imaginary[left]))
    return out_real, out_imaginary


def compare_native_series(
    expected: Mapping[str, Any] | None, actual: Mapping[str, Any] | None,
    *, absolute_tolerance: float = 1e-9,
    allow_resampling: bool = False,
) -> dict[str, Any]:
    """Compare native samples, optionally resampling an adaptive axis.

    ``allow_resampling`` is deliberately false by default.  Callers must opt
    in when comparing independently simulated board projects whose native
    transient solver chose different adaptive time points.
    """
    if not _finite(absolute_tolerance) or absolute_tolerance < 0:
        raise ValueError("absolute_tolerance must be finite and non-negative")
    result: dict[str, Any] = {
        "status": "unverified", "passed": False,
        "absolute_tolerance": float(absolute_tolerance), "sample_count": 0,
        "comparison_basis": "all-native-samples-real-and-imaginary",
    }
    if not expected or not actual or expected.get("status") != "pass" or actual.get("status") != "pass":
        result["reason"] = "complete native series required at both endpoints"
        return result
    if expected.get("analysis") != actual.get("analysis"):
        result.update(status="invalid", reason="analysis kinds differ")
        return result
    axis_a, axis_b = expected["axis"], actual["axis"]
    actual_real, actual_imaginary = actual["real"], actual["imaginary"]
    aligned = len(axis_a) == len(axis_b) and all(
        math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12) for a, b in zip(axis_a, axis_b)
    )
    if not aligned:
        if not allow_resampling:
            result["reason"] = "native sampling axes differ; no interpolation performed"
            return result
        sampled = _resample_series(actual, axis_a)
        if sampled is None:
            result["reason"] = "native sampling axes differ and actual series cannot be resampled"
            return result
        actual_real, actual_imaginary = sampled
        result["comparison_basis"] = "expected native samples versus linearly resampled actual native series"
    differences = [
        math.hypot(real_b - real_a, imaginary_b - imaginary_a)
        for real_a, real_b, imaginary_a, imaginary_b in zip(
            expected["real"], actual_real, expected["imaginary"], actual_imaginary
        )
    ]
    maximum = max(differences)
    worst = differences.index(maximum)
    passed = maximum <= absolute_tolerance
    result.update(
        status="pass" if passed else "fail", passed=passed,
        sample_count=len(differences), max_absolute_difference=maximum,
        worst_sample_index=worst, worst_axis_value=axis_a[worst],
        reason=("every native sample compared on matching axes" if aligned else "actual native series resampled onto expected axis"),
    )
    return result


def write_native_series_csv(path: Path, series: Mapping[str, Mapping[str, Any]]) -> None:
    """Export full samples beside the retained scalar summary and raw native JSON."""
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("output", "analysis", "axis", "real", "imaginary"))
        for output in sorted(series):
            item = series[output]
            for axis, real, imaginary in zip(item["axis"], item["real"], item["imaginary"]):
                writer.writerow((output, item["analysis"], *(
                    format(value, ".17g") for value in (axis, real, imaginary)
                )))


__all__ = [
    "compare_native_series",
    "extract_native_analysis_series",
    "native_series_informative",
    "write_native_series_csv",
]
