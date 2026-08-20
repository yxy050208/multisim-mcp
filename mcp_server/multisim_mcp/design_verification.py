"""Deterministic measurements and design-requirement verification.

The module is dependency-free and never infers a missing signal or criterion.
When the available data cannot prove a requested metric, it returns an
``unverified`` result with a concrete reason.
"""

from __future__ import annotations

import math
import re
from typing import Any, Final

from typing_extensions import NotRequired, Required, TypedDict


class MeasurementRequest(TypedDict, total=False):
    id: Required[str]
    metric: Required[str]
    signal: Required[str]
    reference_signal: NotRequired[str]
    x_signal: NotRequired[str]
    unit: NotRequired[str]
    parameters: NotRequired[dict[str, Any]]


class DesignRequirement(MeasurementRequest, total=False):
    operator: Required[str]
    target: NotRequired[float]
    lower: NotRequired[float]
    upper: NotRequired[float]
    tolerance_abs: NotRequired[float]
    tolerance_percent: NotRequired[float]
    theoretical_value: NotRequired[float]


class ExperimentSpec(TypedDict, total=False):
    schema_version: Required[int]
    title: Required[str]
    netlist: Required[str]
    commands: Required[str]
    requirements: Required[list[DesignRequirement]]
    theoretical_values: NotRequired[dict[str, float]]


class VerificationResult(TypedDict):
    schema_version: int
    overall_status: str
    counts: dict[str, int]
    requirements: list[dict[str, Any]]


METRICS: Final = frozenset(
    {
        "value_at",
        "min",
        "max",
        "mean",
        "rms",
        "peak_to_peak",
        "frequency",
        "thd",
        "gain",
        "cutoff_frequency",
        "bandwidth",
        "rise_time",
        "overshoot",
        "ripple",
        "ripple_percent",
        "power",
    }
)
OPERATORS: Final = frozenset({"at_least", "at_most", "between", "approximately"})
_ID_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_MEASUREMENT_KEYS: Final = frozenset(
    {"id", "metric", "signal", "reference_signal", "x_signal", "unit", "parameters"}
)
_REQUIREMENT_KEYS: Final = _MEASUREMENT_KEYS | frozenset(
    {
        "operator",
        "target",
        "lower",
        "upper",
        "tolerance_abs",
        "tolerance_percent",
        "theoretical_value",
    }
)
_COMMON_PARAMETERS: Final = frozenset({"start_x", "end_x"})
_METRIC_PARAMETERS: Final[dict[str, frozenset[str]]] = {
    "value_at": _COMMON_PARAMETERS | {"at_x"},
    "min": _COMMON_PARAMETERS,
    "max": _COMMON_PARAMETERS,
    "mean": _COMMON_PARAMETERS,
    "rms": _COMMON_PARAMETERS,
    "peak_to_peak": _COMMON_PARAMETERS,
    "frequency": _COMMON_PARAMETERS
    | {"threshold", "edge", "hysteresis", "min_cycles"},
    "thd": _COMMON_PARAMETERS
    | {
        "fundamental_frequency",
        "harmonics",
        "threshold",
        "edge",
        "hysteresis",
        "min_cycles",
    },
    "gain": _COMMON_PARAMETERS | {"gain_mode", "decibels"},
    "cutoff_frequency": _COMMON_PARAMETERS | {"threshold_db"},
    "bandwidth": _COMMON_PARAMETERS | {"threshold_db", "bandwidth_mode"},
    "rise_time": _COMMON_PARAMETERS
    | {"low_level", "high_level", "lower_fraction", "upper_fraction"},
    "overshoot": _COMMON_PARAMETERS | {"low_level", "high_level"},
    "ripple": _COMMON_PARAMETERS,
    "ripple_percent": _COMMON_PARAMETERS,
    "power": _COMMON_PARAMETERS | {"resistance", "power_mode"},
}


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def validate_measurement_requests(
    requests: list[MeasurementRequest], *, requirements: bool = False
) -> list[dict[str, Any]]:
    if not isinstance(requests, list) or not requests:
        raise ValueError("at least one measurement or requirement is required")
    if len(requests) > 100:
        raise ValueError("at most 100 measurements or requirements are allowed")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(requests):
        if not isinstance(raw, dict):
            raise ValueError(f"measurement {index} must be an object")
        item = dict(raw)
        allowed_keys = _REQUIREMENT_KEYS if requirements else _MEASUREMENT_KEYS
        unknown_keys = set(item) - allowed_keys
        if unknown_keys:
            raise ValueError(
                f"unknown fields for measurement {index}: "
                + ", ".join(sorted(unknown_keys))
            )
        identifier = str(item.get("id", ""))
        if not _ID_RE.fullmatch(identifier):
            raise ValueError(f"invalid measurement id: {identifier!r}")
        if identifier in seen:
            raise ValueError(f"duplicate measurement id: {identifier}")
        seen.add(identifier)
        metric = str(item.get("metric", "")).lower()
        if metric not in METRICS:
            raise ValueError(f"unsupported metric for {identifier}: {metric!r}")
        signal = str(item.get("signal", "")).strip()
        if not signal:
            raise ValueError(f"measurement {identifier} requires signal")
        item.update(id=identifier, metric=metric, signal=signal)
        if "parameters" in item and not isinstance(item["parameters"], dict):
            raise ValueError(f"parameters for {identifier} must be an object")
        parameters = item.get("parameters") or {}
        unknown_parameters = set(parameters) - _METRIC_PARAMETERS[metric]
        if unknown_parameters:
            raise ValueError(
                f"unknown parameters for {identifier}: "
                + ", ".join(sorted(unknown_parameters))
            )
        if "decibels" in parameters and not isinstance(parameters["decibels"], bool):
            raise ValueError(f"{identifier}.parameters.decibels must be a boolean")
        if metric in {"frequency", "thd"}:
            edge = str(parameters.get("edge", "rising")).lower()
            if edge not in {"rising", "falling"}:
                raise ValueError(
                    f"{identifier}.parameters.edge must be rising or falling"
                )
            if "threshold" in parameters:
                _finite_number(
                    parameters["threshold"], f"{identifier}.parameters.threshold"
                )
            if "hysteresis" in parameters:
                hysteresis = _finite_number(
                    parameters["hysteresis"],
                    f"{identifier}.parameters.hysteresis",
                )
                if hysteresis < 0:
                    raise ValueError(
                        f"{identifier}.parameters.hysteresis must not be negative"
                    )
            if "min_cycles" in parameters:
                min_cycles = parameters["min_cycles"]
                if (
                    isinstance(min_cycles, bool)
                    or not isinstance(min_cycles, int)
                    or not 1 <= min_cycles <= 10_000
                ):
                    raise ValueError(
                        f"{identifier}.parameters.min_cycles must be an integer "
                        "between 1 and 10000"
                    )
        if metric == "thd":
            if "fundamental_frequency" in parameters:
                fundamental = _finite_number(
                    parameters["fundamental_frequency"],
                    f"{identifier}.parameters.fundamental_frequency",
                )
                if fundamental <= 0:
                    raise ValueError(
                        f"{identifier}.parameters.fundamental_frequency must be positive"
                    )
            if "harmonics" in parameters:
                harmonics = parameters["harmonics"]
                if (
                    isinstance(harmonics, bool)
                    or not isinstance(harmonics, int)
                    or not 2 <= harmonics <= 50
                ):
                    raise ValueError(
                        f"{identifier}.parameters.harmonics must be an integer "
                        "between 2 and 50"
                    )
        if requirements:
            operator = str(item.get("operator", "")).lower()
            if operator not in OPERATORS:
                raise ValueError(f"unsupported operator for {identifier}: {operator!r}")
            item["operator"] = operator
            if operator in {"at_least", "at_most", "approximately"}:
                if "target" not in item:
                    raise ValueError(f"requirement {identifier} requires target")
                item["target"] = _finite_number(item["target"], f"{identifier}.target")
            if operator == "between":
                if "lower" not in item or "upper" not in item:
                    raise ValueError(f"requirement {identifier} requires lower and upper")
                item["lower"] = _finite_number(item["lower"], f"{identifier}.lower")
                item["upper"] = _finite_number(item["upper"], f"{identifier}.upper")
                if item["lower"] > item["upper"]:
                    raise ValueError(f"requirement {identifier} lower exceeds upper")
            if operator == "approximately":
                if "tolerance_abs" not in item and "tolerance_percent" not in item:
                    raise ValueError(
                        f"requirement {identifier} requires tolerance_abs or tolerance_percent"
                    )
                for name in ("tolerance_abs", "tolerance_percent"):
                    if name in item:
                        item[name] = _finite_number(item[name], f"{identifier}.{name}")
                        if item[name] < 0:
                            raise ValueError(f"{identifier}.{name} must not be negative")
            if "theoretical_value" in item:
                item["theoretical_value"] = _finite_number(
                    item["theoretical_value"], f"{identifier}.theoretical_value"
                )
        normalized.append(item)
    return normalized


def validate_experiment_spec(spec: ExperimentSpec) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    if spec.get("schema_version") != 1:
        raise ValueError("ExperimentSpec schema_version must be 1")
    unknown_keys = set(spec) - {
        "schema_version",
        "title",
        "netlist",
        "commands",
        "requirements",
        "theoretical_values",
    }
    if unknown_keys:
        raise ValueError("unknown ExperimentSpec fields: " + ", ".join(sorted(unknown_keys)))
    title = str(spec.get("title", "")).strip()
    netlist = str(spec.get("netlist", ""))
    commands = str(spec.get("commands", ""))
    if not title:
        raise ValueError("ExperimentSpec title must not be empty")
    if not netlist.strip() or not commands.strip():
        raise ValueError("ExperimentSpec netlist and commands must not be empty")
    requirements = validate_measurement_requests(
        spec.get("requirements", []), requirements=True
    )
    theories = spec.get("theoretical_values", {})
    if not isinstance(theories, dict):
        raise ValueError("theoretical_values must be an object")
    normalized_theories = {
        str(key): _finite_number(value, f"theoretical_values.{key}")
        for key, value in theories.items()
    }
    unknown = set(normalized_theories) - {item["id"] for item in requirements}
    if unknown:
        raise ValueError(
            "theoretical_values contains unknown requirement ids: "
            + ", ".join(sorted(unknown))
        )
    return {
        "schema_version": 1,
        "title": title,
        "netlist": netlist,
        "commands": commands,
        "requirements": requirements,
        "theoretical_values": normalized_theories,
    }


def _column_index(parsed: dict[str, Any], name: str) -> tuple[int | None, str | None]:
    columns = [str(item) for item in parsed.get("columns", [])]
    if name in columns:
        return columns.index(name), None
    matches = [index for index, column in enumerate(columns) if column.casefold() == name.casefold()]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, f"signal name is ambiguous: {name}"
    return None, f"signal is not present in experiment data: {name}"


def _series(parsed: dict[str, Any], name: str) -> tuple[list[float] | None, str | None]:
    index, error = _column_index(parsed, name)
    if index is None:
        return None, error
    values: list[float] = []
    for row in parsed.get("rows", []):
        if len(row) <= index or not isinstance(row[index], (int, float)):
            return None, f"signal contains a missing or non-numeric sample: {name}"
        value = float(row[index])
        if not math.isfinite(value):
            return None, f"signal contains a non-finite sample: {name}"
        values.append(value)
    if not values:
        return None, f"signal has no samples: {name}"
    return values, None


def _window(
    x: list[float], arrays: list[list[float]], parameters: dict[str, Any]
) -> tuple[list[float], list[list[float]], str | None]:
    start = parameters.get("start_x")
    end = parameters.get("end_x")
    if start is not None:
        start = _finite_number(start, "parameters.start_x")
    if end is not None:
        end = _finite_number(end, "parameters.end_x")
    if start is not None and end is not None and start > end:
        return [], [[] for _ in arrays], "start_x exceeds end_x"
    indices = [
        index
        for index, value in enumerate(x)
        if (start is None or value >= start) and (end is None or value <= end)
    ]
    if not indices:
        return [], [[] for _ in arrays], "selected measurement window contains no samples"
    return (
        [x[index] for index in indices],
        [[values[index] for index in indices] for values in arrays],
        None,
    )


def _interpolate_x(x0: float, y0: float, x1: float, y1: float, level: float) -> float:
    if math.isclose(y0, y1):
        return x0
    fraction = (level - y0) / (y1 - y0)
    if x0 > 0 and x1 > 0:
        return math.exp(math.log(x0) + fraction * (math.log(x1) - math.log(x0)))
    return x0 + fraction * (x1 - x0)


def _first_crossing(
    x: list[float], y: list[float], level: float, *, rising: bool, start: int = 1
) -> float | None:
    for index in range(max(1, start), len(y)):
        before, after = y[index - 1], y[index]
        crossed = before < level <= after if rising else before > level >= after
        if crossed:
            return _interpolate_x(x[index - 1], before, x[index], after, level)
    return None


def _last_rising_crossing_before(
    x: list[float], y: list[float], level: float, stop: int
) -> float | None:
    """Return the threshold crossing immediately to the left of a peak."""
    for index in range(min(stop, len(y) - 1), 0, -1):
        before, after = y[index - 1], y[index]
        if before <= level < after or before < level <= after:
            return _interpolate_x(x[index - 1], before, x[index], after, level)
    return None


def _threshold_crossings(
    x: list[float],
    y: list[float],
    threshold: float,
    *,
    edge: str,
    hysteresis: float,
) -> list[float]:
    """Return interpolated Schmitt-style threshold crossings."""
    half_band = hysteresis / 2.0
    low, high = threshold - half_band, threshold + half_band
    rising = edge == "rising"
    armed = y[0] <= low if rising else y[0] >= high
    crossings: list[float] = []
    for index in range(1, len(y)):
        before, after = y[index - 1], y[index]
        if not armed:
            if rising and min(before, after) <= low:
                armed = True
            elif not rising and max(before, after) >= high:
                armed = True
        crossed = before < threshold <= after if rising else before > threshold >= after
        if armed and crossed:
            if math.isclose(before, after):
                crossing = x[index - 1]
            else:
                fraction = (threshold - before) / (after - before)
                crossing = x[index - 1] + fraction * (x[index] - x[index - 1])
            crossings.append(crossing)
            armed = False
    return crossings


def _periodic_crossings(
    x: list[float], y: list[float], parameters: dict[str, Any]
) -> tuple[list[float] | None, dict[str, Any], str | None]:
    if len(y) < 4:
        return None, {}, "frequency measurement requires at least 4 samples"
    if any(after <= before for before, after in zip(x, x[1:])):
        return None, {}, "frequency measurement requires a strictly increasing x signal"
    low, high = min(y), max(y)
    span = high - low
    if math.isclose(span, 0.0, abs_tol=1e-30):
        return None, {}, "frequency measurement signal has zero amplitude"
    threshold = _finite_number(
        parameters.get("threshold", (low + high) / 2.0), "threshold"
    )
    hysteresis = _finite_number(parameters.get("hysteresis", 0.0), "hysteresis")
    if hysteresis < 0:
        return None, {}, "hysteresis must not be negative"
    if not low < threshold < high:
        return None, {}, "frequency threshold must lie inside the signal range"
    if hysteresis >= 2.0 * min(threshold - low, high - threshold):
        return None, {}, "frequency hysteresis band must lie inside the signal range"
    edge = str(parameters.get("edge", "rising")).lower()
    crossings = _threshold_crossings(
        x, y, threshold, edge=edge, hysteresis=hysteresis
    )
    minimum = int(parameters.get("min_cycles", 2))
    if len(crossings) < minimum + 1:
        return (
            None,
            {"crossing_count": len(crossings)},
            f"frequency measurement requires at least {minimum + 1} {edge} crossings",
        )
    return (
        crossings,
        {
            "threshold": threshold,
            "edge": edge,
            "hysteresis": hysteresis,
            "crossing_count": len(crossings),
        },
        None,
    )


def _trapezoid_integral(x: list[float], values: list[float]) -> float:
    return sum(
        (x[index] - x[index - 1])
        * (values[index] + values[index - 1])
        / 2.0
        for index in range(1, len(x))
    )


def _harmonic_amplitudes(
    x: list[float], y: list[float], fundamental: float, harmonics: int
) -> tuple[float, list[float]]:
    duration = x[-1] - x[0]
    mean = _trapezoid_integral(x, y) / duration
    centered = [sample - mean for sample in y]
    amplitudes: list[float] = []
    for harmonic in range(1, harmonics + 1):
        omega = 2.0 * math.pi * fundamental * harmonic
        cosine = [
            sample * math.cos(omega * (time - x[0]))
            for time, sample in zip(x, centered)
        ]
        sine = [
            sample * math.sin(omega * (time - x[0]))
            for time, sample in zip(x, centered)
        ]
        a = 2.0 * _trapezoid_integral(x, cosine) / duration
        b = 2.0 * _trapezoid_integral(x, sine) / duration
        amplitudes.append(math.hypot(a, b))
    return mean, amplitudes


def _measurement_failure(item: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "id": item["id"],
        "metric": item["metric"],
        "signal": item["signal"],
        "status": "unverified",
        "value": None,
        "unit": item.get("unit", ""),
        "reason": reason,
        "details": {},
    }


def measure_one(parsed: dict[str, Any], request: MeasurementRequest) -> dict[str, Any]:
    item = validate_measurement_requests([request])[0]
    metric = item["metric"]
    parameters = dict(item.get("parameters") or {})
    y, error = _series(parsed, item["signal"])
    if y is None:
        return _measurement_failure(item, str(error))
    columns = [str(value) for value in parsed.get("columns", [])]
    x_name = str(item.get("x_signal") or (columns[0] if columns else ""))
    x, error = _series(parsed, x_name)
    if x is None:
        return _measurement_failure(item, str(error))
    if len(x) != len(y):
        return _measurement_failure(item, "x and signal sample counts differ")
    full_x = x
    x, windowed, error = _window(full_x, [y], parameters)
    if error:
        return _measurement_failure(item, error)
    y = windowed[0]
    details: dict[str, Any] = {"x_signal": x_name, "samples": len(y)}
    value: float | None = None
    default_unit = ""

    if metric in {"cutoff_frequency", "bandwidth", "rise_time"} and any(
        after <= before for before, after in zip(x, x[1:])
    ):
        return _measurement_failure(
            item, f"{metric} requires a strictly increasing x signal"
        )

    if metric in {"min", "max", "mean", "rms", "peak_to_peak"}:
        if metric == "min":
            value = min(y)
        elif metric == "max":
            value = max(y)
        elif metric == "mean":
            value = sum(y) / len(y)
        elif metric == "rms":
            value = math.sqrt(sum(sample * sample for sample in y) / len(y))
        else:
            value = max(y) - min(y)
    elif metric == "frequency":
        crossings, crossing_details, error = _periodic_crossings(x, y, parameters)
        if crossings is None:
            return _measurement_failure(item, str(error))
        periods = [
            after - before for before, after in zip(crossings, crossings[1:])
        ]
        ordered = sorted(periods)
        middle = len(ordered) // 2
        period = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2.0
        )
        if period <= 0:
            return _measurement_failure(item, "frequency period is not positive")
        value = 1.0 / period
        default_unit = "Hz"
        details.update(
            crossing_details,
            period=period,
            cycles=len(periods),
            first_crossing=crossings[0],
            last_crossing=crossings[-1],
        )
    elif metric == "thd":
        crossings, crossing_details, error = _periodic_crossings(x, y, parameters)
        if crossings is None:
            return _measurement_failure(item, str(error))
        start, end = crossings[0], crossings[-1]
        indices = [index for index, time in enumerate(x) if start <= time <= end]
        if len(indices) < 8:
            return _measurement_failure(item, "THD window contains fewer than 8 samples")
        harmonic_x = [x[index] for index in indices]
        harmonic_y = [y[index] for index in indices]
        cycle_count = len(crossings) - 1
        estimated_fundamental = cycle_count / (end - start)
        fundamental = _finite_number(
            parameters.get("fundamental_frequency", estimated_fundamental),
            "fundamental_frequency",
        )
        harmonics = int(parameters.get("harmonics", 10))
        mean, amplitudes = _harmonic_amplitudes(
            harmonic_x, harmonic_y, fundamental, harmonics
        )
        if math.isclose(amplitudes[0], 0.0, abs_tol=1e-30):
            return _measurement_failure(item, "THD fundamental amplitude is zero")
        value = (
            math.sqrt(sum(amplitude * amplitude for amplitude in amplitudes[1:]))
            / amplitudes[0]
            * 100.0
        )
        default_unit = "%"
        details.update(
            crossing_details,
            fundamental_frequency=fundamental,
            estimated_fundamental_frequency=estimated_fundamental,
            harmonics=harmonics,
            harmonic_peak_amplitudes=amplitudes,
            dc_component=mean,
            cycles=cycle_count,
            analysis_start=harmonic_x[0],
            analysis_end=harmonic_x[-1],
        )
    elif metric == "value_at":
        if "at_x" not in parameters:
            return _measurement_failure(item, "value_at requires parameters.at_x")
        at_x = _finite_number(parameters["at_x"], "parameters.at_x")
        if at_x < min(x) or at_x > max(x):
            return _measurement_failure(item, "requested x value is outside the data range")
        ordered = sorted(zip(x, y), key=lambda pair: pair[0])
        for index, (current_x, current_y) in enumerate(ordered):
            if math.isclose(current_x, at_x, rel_tol=1e-12, abs_tol=1e-15):
                value = current_y
                break
            if current_x > at_x and index > 0:
                previous_x, previous_y = ordered[index - 1]
                if math.isclose(current_x, previous_x, abs_tol=1e-30):
                    return _measurement_failure(
                        item, "cannot interpolate across duplicate x samples"
                    )
                value = previous_y + (current_y - previous_y) * (
                    (at_x - previous_x) / (current_x - previous_x)
                )
                break
        details["at_x"] = at_x
    elif metric == "gain":
        reference_name = str(item.get("reference_signal", "")).strip()
        if not reference_name:
            return _measurement_failure(item, "gain requires reference_signal")
        reference, error = _series(parsed, reference_name)
        if reference is None:
            return _measurement_failure(item, str(error))
        _, reference_window, error = _window(full_x, [reference], parameters)
        if error:
            return _measurement_failure(item, error)
        reference = reference_window[0]
        mode = str(parameters.get("gain_mode", "amplitude_ratio")).lower()
        if mode == "amplitude_ratio":
            denominator = max(reference) - min(reference)
            numerator = max(y) - min(y)
        elif mode == "rms_ratio":
            denominator = math.sqrt(sum(v * v for v in reference) / len(reference))
            numerator = math.sqrt(sum(v * v for v in y) / len(y))
        elif mode == "mean_ratio":
            denominator = sum(reference) / len(reference)
            numerator = sum(y) / len(y)
        else:
            return _measurement_failure(item, f"unsupported gain_mode: {mode}")
        if math.isclose(denominator, 0.0, abs_tol=1e-30):
            return _measurement_failure(item, "gain reference amplitude is zero")
        value = numerator / denominator
        details.update(reference_signal=reference_name, gain_mode=mode)
        if bool(parameters.get("decibels", False)):
            if value <= 0:
                return _measurement_failure(item, "decibel gain requires a positive ratio")
            value = 20.0 * math.log10(value)
            default_unit = "dB"
        else:
            default_unit = "ratio"
    elif metric in {"cutoff_frequency", "bandwidth"}:
        reference_name = str(item.get("reference_signal", "")).strip()
        magnitude = [abs(value) for value in y]
        if reference_name:
            reference, error = _series(parsed, reference_name)
            if reference is None:
                return _measurement_failure(item, str(error))
            _, ref_window, error = _window(full_x, [reference], parameters)
            if error:
                return _measurement_failure(item, error)
            reference = ref_window[0]
            if any(math.isclose(value, 0.0, abs_tol=1e-30) for value in reference):
                return _measurement_failure(item, "frequency response reference contains zero")
            magnitude = [abs(out / ref) for out, ref in zip(y, reference)]
        if len(magnitude) < 3:
            return _measurement_failure(item, "frequency measurement requires at least 3 samples")
        peak_index = max(range(len(magnitude)), key=magnitude.__getitem__)
        peak = magnitude[peak_index]
        threshold_db = _finite_number(parameters.get("threshold_db", -3.0), "threshold_db")
        threshold = peak * (10.0 ** (threshold_db / 20.0))
        upper = _first_crossing(x, magnitude, threshold, rising=False, start=peak_index + 1)
        details.update(
            peak=peak,
            peak_frequency=x[peak_index],
            threshold=threshold,
            threshold_db=threshold_db,
        )
        default_unit = "Hz"
        if metric == "cutoff_frequency":
            if upper is None:
                return _measurement_failure(item, "no upper threshold crossing exists in the sweep")
            value = upper
        else:
            mode = str(parameters.get("bandwidth_mode", "bandpass")).lower()
            if mode == "lowpass":
                lower = 0.0
            elif mode == "bandpass":
                lower = _last_rising_crossing_before(
                    x, magnitude, threshold, peak_index
                )
            else:
                return _measurement_failure(item, f"unsupported bandwidth_mode: {mode}")
            if lower is None or upper is None:
                return _measurement_failure(item, "both declared bandwidth edges are not present")
            value = upper - lower
            details.update(lower_cutoff=lower, upper_cutoff=upper, bandwidth_mode=mode)
    elif metric in {"rise_time", "overshoot"}:
        if len(y) < 5:
            return _measurement_failure(item, "step measurement requires at least 5 samples")
        edge_count = max(1, min(len(y) // 10, 50))
        low = _finite_number(
            parameters.get("low_level", sum(y[:edge_count]) / edge_count), "low_level"
        )
        high = _finite_number(
            parameters.get("high_level", sum(y[-edge_count:]) / edge_count), "high_level"
        )
        step = high - low
        if step <= 0:
            return _measurement_failure(item, "rise metrics require a positive final step")
        details.update(low_level=low, high_level=high, level_method="explicit-or-edge-mean")
        if metric == "rise_time":
            lower_fraction = _finite_number(parameters.get("lower_fraction", 0.1), "lower_fraction")
            upper_fraction = _finite_number(parameters.get("upper_fraction", 0.9), "upper_fraction")
            if not 0 <= lower_fraction < upper_fraction <= 1:
                return _measurement_failure(item, "rise fractions must satisfy 0 <= lower < upper <= 1")
            lower_level = low + step * lower_fraction
            upper_level = low + step * upper_fraction
            lower_x = _first_crossing(x, y, lower_level, rising=True)
            upper_x = _first_crossing(x, y, upper_level, rising=True)
            if lower_x is None or upper_x is None or upper_x < lower_x:
                return _measurement_failure(item, "rise thresholds were not crossed in order")
            value = upper_x - lower_x
            default_unit = "s"
            details.update(lower_crossing=lower_x, upper_crossing=upper_x)
        else:
            value = max(0.0, (max(y) - high) / abs(step) * 100.0)
            default_unit = "%"
    elif metric in {"ripple", "ripple_percent"}:
        if "start_x" not in parameters and "end_x" not in parameters:
            start = max(0, int(len(y) * 0.8))
            y = y[start:]
            details["window_method"] = "last-20-percent"
        ripple = max(y) - min(y)
        if metric == "ripple":
            value = ripple
        else:
            mean = sum(y) / len(y)
            if math.isclose(mean, 0.0, abs_tol=1e-30):
                return _measurement_failure(item, "percentage ripple mean is zero")
            value = ripple / abs(mean) * 100.0
            default_unit = "%"
    elif metric == "power":
        reference_name = str(item.get("reference_signal", "")).strip()
        if reference_name:
            current, error = _series(parsed, reference_name)
            if current is None:
                return _measurement_failure(item, str(error))
            _, current_window, error = _window(full_x, [current], parameters)
            if error:
                return _measurement_failure(item, error)
            products = [voltage * amps for voltage, amps in zip(y, current_window[0])]
            details["current_signal"] = reference_name
        elif "resistance" in parameters:
            resistance = _finite_number(parameters["resistance"], "resistance")
            if resistance <= 0:
                return _measurement_failure(item, "power resistance must be positive")
            products = [voltage * voltage / resistance for voltage in y]
            details["resistance"] = resistance
        else:
            return _measurement_failure(item, "power requires reference_signal or parameters.resistance")
        mode = str(parameters.get("power_mode", "average")).lower()
        if mode == "average":
            value = sum(products) / len(products)
        elif mode == "absolute_average":
            value = sum(abs(sample) for sample in products) / len(products)
        elif mode == "peak":
            value = max(abs(sample) for sample in products)
        else:
            return _measurement_failure(item, f"unsupported power_mode: {mode}")
        default_unit = "W"
        details["power_mode"] = mode

    if value is None or not math.isfinite(value):
        return _measurement_failure(item, "metric did not produce a finite value")
    return {
        "id": item["id"],
        "metric": metric,
        "signal": item["signal"],
        "status": "measured",
        "value": value,
        "unit": item.get("unit") or default_unit,
        "reason": None,
        "details": details,
    }


def measure_many(
    parsed: dict[str, Any], requests: list[MeasurementRequest]
) -> list[dict[str, Any]]:
    normalized = validate_measurement_requests(requests)
    return [measure_one(parsed, item) for item in normalized]


def _criterion(item: dict[str, Any], value: float) -> tuple[bool, dict[str, Any]]:
    operator = item["operator"]
    if operator == "at_least":
        target = float(item["target"])
        return value >= target, {"operator": operator, "target": target}
    if operator == "at_most":
        target = float(item["target"])
        return value <= target, {"operator": operator, "target": target}
    if operator == "between":
        lower, upper = float(item["lower"]), float(item["upper"])
        return lower <= value <= upper, {"operator": operator, "lower": lower, "upper": upper}
    target = float(item["target"])
    limits: list[float] = []
    if "tolerance_abs" in item:
        limits.append(float(item["tolerance_abs"]))
    if "tolerance_percent" in item:
        limits.append(abs(target) * float(item["tolerance_percent"]) / 100.0)
    allowed = min(limits)
    return abs(value - target) <= allowed, {
        "operator": operator,
        "target": target,
        "allowed_absolute_error": allowed,
        "tolerance_abs": item.get("tolerance_abs"),
        "tolerance_percent": item.get("tolerance_percent"),
    }


def verify_requirements(
    parsed: dict[str, Any],
    requirements: list[DesignRequirement],
    theoretical_values: dict[str, float] | None = None,
) -> VerificationResult:
    normalized = validate_measurement_requests(requirements, requirements=True)
    if theoretical_values is not None and not isinstance(theoretical_values, dict):
        raise ValueError("theoretical_values must be an object")
    theories = {
        str(key): _finite_number(value, f"theoretical_values.{key}")
        for key, value in (theoretical_values or {}).items()
    }
    unknown = set(theories) - {item["id"] for item in normalized}
    if unknown:
        raise ValueError("unknown theoretical value ids: " + ", ".join(sorted(unknown)))
    results: list[dict[str, Any]] = []
    counts = {"pass": 0, "fail": 0, "unverified": 0}
    for item in normalized:
        measurement_request = {
            key: value for key, value in item.items() if key in _MEASUREMENT_KEYS
        }
        measurement = measure_one(parsed, measurement_request)  # type: ignore[arg-type]
        result: dict[str, Any] = {
            "id": item["id"],
            "metric": item["metric"],
            "signal": item["signal"],
            "status": "unverified",
            "measurement": measurement,
            "criterion": None,
            "comparison": None,
            "reason": measurement.get("reason"),
        }
        if measurement["status"] == "measured":
            passed, criterion = _criterion(item, float(measurement["value"]))
            result.update(
                status="pass" if passed else "fail",
                criterion=criterion,
                reason=None,
            )
            theory = item.get("theoretical_value", theories.get(item["id"]))
            if theory is not None:
                theory = _finite_number(theory, f"theory.{item['id']}")
                absolute_error = float(measurement["value"]) - theory
                result["comparison"] = {
                    "theoretical_value": theory,
                    "simulated_value": measurement["value"],
                    "absolute_error": absolute_error,
                    "absolute_error_magnitude": abs(absolute_error),
                    "relative_error_percent": (
                        abs(absolute_error / theory) * 100.0
                        if not math.isclose(theory, 0.0, abs_tol=1e-30)
                        else None
                    ),
                }
        counts[result["status"]] += 1
        results.append(result)
    overall = "fail" if counts["fail"] else "unverified" if counts["unverified"] else "pass"
    return {
        "schema_version": 1,
        "overall_status": overall,
        "counts": counts,
        "requirements": results,
    }
