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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class DigitalEdgeComparisonContract:
    """Explicit contract for comparing logic transitions on adaptive axes.

    This contract compares threshold-crossing events only. It does not
    interpolate the complete waveform and therefore cannot certify analog
    shape, propagation delay through an undeclared load, or continuous-time
    behavior.
    """

    low_voltage: float = 0.0
    high_voltage: float = 5.0
    threshold_voltage: float = 2.5
    level_tolerance: float = 0.05
    edge_time_tolerance: float = 1e-9
    minimum_edge_separation: float = 0.0

    def validate(self) -> None:
        values = (
            self.low_voltage,
            self.high_voltage,
            self.threshold_voltage,
            self.level_tolerance,
            self.edge_time_tolerance,
            self.minimum_edge_separation,
        )
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(float(value)) for value in values):
            raise ValueError("digital edge contract values must be finite numbers")
        if not self.low_voltage < self.threshold_voltage < self.high_voltage:
            raise ValueError("digital edge threshold must lie between low and high rails")
        if self.level_tolerance < 0 or self.edge_time_tolerance < 0 or self.minimum_edge_separation < 0:
            raise ValueError("digital edge tolerances must be non-negative")
        if self.level_tolerance >= min(
            self.threshold_voltage - self.low_voltage,
            self.high_voltage - self.threshold_voltage,
        ):
            raise ValueError("digital edge level tolerance must leave a threshold band")


def _digital_edge_events(
    series: Mapping[str, Any], contract: DigitalEdgeComparisonContract,
) -> dict[str, Any]:
    """Extract threshold crossings without resampling an adaptive axis."""
    if not isinstance(series, Mapping) or series.get("status") != "pass":
        return {"status": "unverified", "reason": "complete native series is required"}
    if series.get("analysis") != "tran":
        return {"status": "invalid", "reason": "digital edge comparison requires transient series"}
    axis = series.get("axis")
    real = series.get("real")
    if not isinstance(axis, list) or not isinstance(real, list) or len(axis) < 2 or len(axis) != len(real):
        return {"status": "invalid", "reason": "transient axis and real samples are incomplete"}
    if any(not _finite(value) for value in (*axis, *real)):
        return {"status": "invalid", "reason": "transient axis or real samples are non-finite"}
    if any(float(after) <= float(before) for before, after in zip(axis, axis[1:])):
        return {"status": "invalid", "reason": "transient axis is not strictly increasing"}

    low, high, threshold, tolerance = (
        contract.low_voltage,
        contract.high_voltage,
        contract.threshold_voltage,
        contract.level_tolerance,
    )
    states: list[int | None] = []
    for value in real:
        if abs(float(value) - low) <= tolerance:
            states.append(0)
        elif abs(float(value) - high) <= tolerance:
            states.append(1)
        else:
            states.append(None)

    events: list[dict[str, Any]] = []
    transition_indices: set[int] = set()
    for index, (x0, x1, y0, y1) in enumerate(zip(axis, axis[1:], real, real[1:])):
        y0, y1 = float(y0), float(y1)
        if y0 == y1:
            continue
        if y0 < threshold <= y1:
            direction = "rising"
        elif y0 > threshold >= y1:
            direction = "falling"
        else:
            continue
        fraction = (threshold - y0) / (y1 - y0)
        event_time = float(x0) + fraction * (float(x1) - float(x0))
        if events and event_time - float(events[-1]["time"]) < contract.minimum_edge_separation:
            return {
                "status": "invalid",
                "reason": "multiple digital edges violate minimum separation",
                "events": events,
            }
        events.append({
            "direction": direction,
            "time": event_time,
            "sample_index": index,
        })
        transition_indices.update((index, index + 1))

    invalid_levels = [
        index for index, state in enumerate(states)
        if state is None and index not in transition_indices
    ]
    if invalid_levels:
        return {
            "status": "invalid",
            "reason": "non-digital level samples are outside declared transition brackets",
            "invalid_level_indices": invalid_levels[:16],
            "events": events,
        }
    if states[0] is None or states[-1] is None:
        return {"status": "invalid", "reason": "initial or final sample is not a digital level", "events": events}
    return {
        "status": "pass",
        "initial_level": states[0],
        "final_level": states[-1],
        "events": events,
        "sample_count": len(real),
        "axis_start": float(axis[0]),
        "axis_end": float(axis[-1]),
    }


def compare_digital_edge_series(
    expected: Mapping[str, Any] | None,
    actual: Mapping[str, Any] | None,
    *,
    contract: DigitalEdgeComparisonContract | None = None,
) -> dict[str, Any]:
    """Compare digital edge timing on two independently sampled series.

    Missing or incomplete native responses remain ``unverified``. A malformed
    response is ``invalid``. A complete response with mismatched levels,
    directions, counts, or edge times is ``fail``.
    """
    contract = contract or DigitalEdgeComparisonContract()
    contract.validate()
    left = _digital_edge_events(expected, contract)
    right = _digital_edge_events(actual, contract)
    result: dict[str, Any] = {
        "status": "unverified",
        "passed": False,
        "edge_time_tolerance": float(contract.edge_time_tolerance),
        "comparison_basis": "threshold-crossing events; no waveform resampling",
        "expected": left,
        "actual": right,
    }
    if left.get("status") == "unverified" or right.get("status") == "unverified":
        result["reason"] = "complete native series required at both endpoints"
        return result
    if left.get("status") != "pass" or right.get("status") != "pass":
        result.update(status="invalid", reason="digital edge extraction failed")
        return result
    if left["initial_level"] != right["initial_level"] or left["final_level"] != right["final_level"]:
        result.update(status="fail", reason="initial or final digital levels differ")
        return result
    expected_events = left["events"]
    actual_events = right["events"]
    if len(expected_events) != len(actual_events):
        result.update(
            status="fail",
            reason="digital edge counts differ",
            expected_edge_count=len(expected_events),
            actual_edge_count=len(actual_events),
        )
        return result
    timing_errors: list[float] = []
    mismatches: list[dict[str, Any]] = []
    for index, (expected_event, actual_event) in enumerate(zip(expected_events, actual_events)):
        error = abs(float(expected_event["time"]) - float(actual_event["time"]))
        timing_errors.append(error)
        if expected_event["direction"] != actual_event["direction"] or error > contract.edge_time_tolerance:
            mismatches.append({
                "index": index,
                "expected_direction": expected_event["direction"],
                "actual_direction": actual_event["direction"],
                "expected_time": expected_event["time"],
                "actual_time": actual_event["time"],
                "absolute_time_error": error,
            })
    result.update(
        status="pass" if not mismatches else "fail",
        passed=not mismatches,
        edge_count=len(expected_events),
        maximum_edge_time_error=max(timing_errors, default=0.0),
        mismatches=mismatches[:16],
        reason=("all edge directions and threshold times matched" if not mismatches
                else "one or more edge directions or threshold times differed"),
    )
    return result


@dataclass(frozen=True)
class ContinuousResponseContract:
    """Compare bounded transient response features on adaptive native axes.

    This is intentionally a feature contract rather than a resampled waveform
    comparison.  It verifies the initial/final values, global peak and valley,
    response range, and the time positions of those extrema.  A complete
    native series is still required; missing, downsampled, malformed, or
    non-finite data fails closed.  Passing this contract does not certify
    component tolerance, continuous-time behaviour between native samples, or
    physical-board performance.
    """

    final_value_tolerance: float = 0.05
    extrema_value_tolerance: float = 0.05
    response_range_tolerance: float = 0.05
    time_tolerance: float = 1e-6
    minimum_samples: int = 3

    def validate(self) -> None:
        values = (
            self.final_value_tolerance,
            self.extrema_value_tolerance,
            self.response_range_tolerance,
            self.time_tolerance,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in values
        ):
            raise ValueError("continuous response tolerances must be finite numbers")
        if any(float(value) < 0 for value in values):
            raise ValueError("continuous response tolerances must be non-negative")
        if (
            isinstance(self.minimum_samples, bool)
            or not isinstance(self.minimum_samples, int)
            or self.minimum_samples < 3
        ):
            raise ValueError("continuous response minimum_samples must be an integer >= 3")


def _continuous_response_metrics(
    series: Mapping[str, Any] | None, contract: ContinuousResponseContract,
) -> dict[str, Any]:
    """Extract response features without interpolating or resampling samples."""
    if not isinstance(series, Mapping) or series.get("status") != "pass":
        return {"status": "unverified", "reason": "complete native series is required"}
    if series.get("analysis") != "tran":
        return {"status": "invalid", "reason": "continuous response requires transient series"}
    axis = series.get("axis")
    real = series.get("real")
    if (
        not isinstance(axis, list)
        or not isinstance(real, list)
        or len(axis) != len(real)
        or len(axis) < contract.minimum_samples
    ):
        return {
            "status": "invalid",
            "reason": "transient response has too few or incomplete samples",
        }
    declared_count = series.get("sample_count")
    if declared_count is not None and (
        isinstance(declared_count, bool)
        or not isinstance(declared_count, int)
        or declared_count != len(real)
    ):
        return {"status": "invalid", "reason": "transient sample metadata contradicts samples"}
    if any(not _finite(value) for value in (*axis, *real)):
        return {"status": "invalid", "reason": "transient response contains non-finite samples"}
    if any(float(after) <= float(before) for before, after in zip(axis, axis[1:])):
        return {"status": "invalid", "reason": "transient axis is not strictly increasing"}
    peak_index = max(range(len(real)), key=lambda index: float(real[index]))
    valley_index = min(range(len(real)), key=lambda index: float(real[index]))
    peak_value = float(real[peak_index])
    valley_value = float(real[valley_index])
    return {
        "status": "pass",
        "sample_count": len(real),
        "axis_start": float(axis[0]),
        "axis_end": float(axis[-1]),
        "initial_value": float(real[0]),
        "final_value": float(real[-1]),
        "peak_value": peak_value,
        "peak_time": float(axis[peak_index]),
        "valley_value": valley_value,
        "valley_time": float(axis[valley_index]),
        "response_range": peak_value - valley_value,
    }


def compare_continuous_response_series(
    expected: Mapping[str, Any] | None,
    actual: Mapping[str, Any] | None,
    *,
    contract: ContinuousResponseContract | None = None,
) -> dict[str, Any]:
    """Compare RC-like transient features while preserving adaptive axes.

    The comparator never interpolates a response.  Time differences are
    bounded only at the declared extrema and analysis endpoints, so an
    independently sampled native response cannot pass by hiding a topology
    error behind a full-waveform resampling step.
    """
    contract = contract or ContinuousResponseContract()
    contract.validate()
    left = _continuous_response_metrics(expected, contract)
    right = _continuous_response_metrics(actual, contract)
    result: dict[str, Any] = {
        "status": "unverified",
        "passed": False,
        "comparison_basis": "native RC response features; no waveform resampling",
        "tolerances": {
            "final_value": float(contract.final_value_tolerance),
            "extrema_value": float(contract.extrema_value_tolerance),
            "response_range": float(contract.response_range_tolerance),
            "time": float(contract.time_tolerance),
            "minimum_samples": contract.minimum_samples,
        },
        "expected": left,
        "actual": right,
    }
    if left.get("status") == "unverified" or right.get("status") == "unverified":
        result["reason"] = "complete native series required at both endpoints"
        return result
    if left.get("status") != "pass" or right.get("status") != "pass":
        result.update(status="invalid", reason="continuous response feature extraction failed")
        return result
    value_checks = {
        "initial_value": abs(left["initial_value"] - right["initial_value"])
        <= contract.extrema_value_tolerance,
        "final_value": abs(left["final_value"] - right["final_value"])
        <= contract.final_value_tolerance,
        "peak_value": abs(left["peak_value"] - right["peak_value"])
        <= contract.extrema_value_tolerance,
        "valley_value": abs(left["valley_value"] - right["valley_value"])
        <= contract.extrema_value_tolerance,
        "response_range": abs(left["response_range"] - right["response_range"])
        <= contract.response_range_tolerance,
    }
    time_errors = {
        "axis_start": abs(left["axis_start"] - right["axis_start"]),
        "axis_end": abs(left["axis_end"] - right["axis_end"]),
        "peak_time": abs(left["peak_time"] - right["peak_time"]),
        "valley_time": abs(left["valley_time"] - right["valley_time"]),
    }
    time_checks = {
        name: error <= contract.time_tolerance for name, error in time_errors.items()
    }
    checks = {**value_checks, **time_checks}
    result.update(
        status="pass" if all(checks.values()) else "fail",
        passed=all(checks.values()),
        checks=checks,
        value_errors={
            "initial_value": abs(left["initial_value"] - right["initial_value"]),
            "final_value": abs(left["final_value"] - right["final_value"]),
            "peak_value": abs(left["peak_value"] - right["peak_value"]),
            "valley_value": abs(left["valley_value"] - right["valley_value"]),
            "response_range": abs(left["response_range"] - right["response_range"]),
        },
        time_errors=time_errors,
        maximum_time_axis_error=max(time_errors.values()),
        reason=(
            "initial/final values, peak, valley, range and adaptive-axis times matched"
            if all(checks.values())
            else "one or more continuous response features exceeded the declared tolerance"
        ),
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
    "ContinuousResponseContract",
    "DigitalEdgeComparisonContract",
    "compare_continuous_response_series",
    "compare_native_series",
    "compare_digital_edge_series",
    "extract_native_analysis_series",
    "native_series_informative",
    "write_native_series_csv",
]
