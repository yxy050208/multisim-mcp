"""Data-backed virtual instruments for completed Multisim experiments."""

from __future__ import annotations

import math
from typing import Any


def _series(parsed: dict[str, Any], name: str) -> tuple[str, list[float]]:
    columns = [str(item) for item in parsed.get("columns", [])]
    exact = [i for i, item in enumerate(columns) if item == name]
    matches = exact or [i for i, item in enumerate(columns) if item.casefold() == name.casefold()]
    if len(matches) != 1:
        available = ", ".join(columns)
        raise ValueError(f"signal {name!r} was not found uniquely; available: {available}")
    index = matches[0]
    values: list[float] = []
    for row in parsed.get("rows", []):
        if index >= len(row):
            raise ValueError(f"signal {name!r} contains a missing sample")
        value = float(row[index])
        if not math.isfinite(value):
            raise ValueError(f"signal {name!r} contains a non-finite sample")
        values.append(value)
    if not values:
        raise ValueError(f"signal {name!r} has no samples")
    return columns[index], values


def _complex_series(parsed: dict[str, Any], name: str) -> tuple[str, list[complex]] | None:
    real_rows = parsed.get("real_rows")
    imaginary_rows = parsed.get("imaginary_rows")
    if not isinstance(real_rows, list) or not isinstance(imaginary_rows, list):
        return None
    columns = [str(item) for item in parsed.get("columns", [])]
    matches = [i for i, item in enumerate(columns) if item == name] or [
        i for i, item in enumerate(columns) if item.casefold() == name.casefold()
    ]
    if len(matches) != 1:
        return None
    index = matches[0]
    values = [complex(float(real[index]), float(imag[index])) for real, imag in zip(real_rows, imaginary_rows)]
    return columns[index], values


def multimeter(
    parsed: dict[str, Any], signal: str, reference_signal: str | None = None
) -> dict[str, Any]:
    """Measure DC, true RMS, AC RMS and range for one voltage/current trace."""
    resolved, values = _series(parsed, signal)
    reference = None
    if reference_signal:
        reference, reference_values = _series(parsed, reference_signal)
        if len(values) != len(reference_values):
            raise ValueError("signal and reference_signal have different sample counts")
        values = [left - right for left, right in zip(values, reference_values)]
    dc = sum(values) / len(values)
    rms = math.sqrt(sum(item * item for item in values) / len(values))
    ac_rms = math.sqrt(sum((item - dc) ** 2 for item in values) / len(values))
    return {
        "schema_version": 1,
        "instrument": "multimeter",
        "signal": resolved,
        "reference_signal": reference,
        "samples": len(values),
        "dc": dc,
        "rms": rms,
        "ac_rms": ac_rms,
        "minimum": min(values),
        "maximum": max(values),
        "peak_to_peak": max(values) - min(values),
    }


def bode_plotter(
    parsed: dict[str, Any], input_signal: str, output_signal: str,
    frequency_signal: str | None = None, max_points: int = 2000,
) -> dict[str, Any]:
    """Compute a magnitude response and standard -3 dB landmarks."""
    if max_points < 1 or max_points > 10_000:
        raise ValueError("max_points must be between 1 and 10000")
    columns = [str(item) for item in parsed.get("columns", [])]
    if not columns:
        raise ValueError("experiment contains no data columns")
    frequency_name, frequency = _series(parsed, frequency_signal or columns[0])
    input_name, input_values = _series(parsed, input_signal)
    output_name, output_values = _series(parsed, output_signal)
    if len({len(frequency), len(input_values), len(output_values)}) != 1:
        raise ValueError("frequency, input, and output sample counts differ")
    if any(item <= 0 for item in frequency):
        raise ValueError("Bode frequency samples must be positive")
    if any(after <= before for before, after in zip(frequency, frequency[1:])):
        raise ValueError("Bode frequency samples must be strictly increasing")
    complex_input = _complex_series(parsed, input_name)
    complex_output = _complex_series(parsed, output_name)
    complex_available = complex_input is not None and complex_output is not None
    input_components = complex_input[1] if complex_input else []
    output_components = complex_output[1] if complex_output else []
    gain: list[float] = []
    gain_db: list[float] = []
    phase_degrees: list[float | None] = []
    for index, (source, output) in enumerate(zip(input_values, output_values)):
        complex_source = input_components[index] if complex_available else complex(source)
        complex_result = output_components[index] if complex_available else complex(output)
        if abs(complex_source) <= 1e-30:
            gain.append(math.nan)
            gain_db.append(math.nan)
            phase_degrees.append(None)
            continue
        transfer = complex_result / complex_source
        ratio = abs(transfer)
        gain.append(ratio)
        gain_db.append(20 * math.log10(ratio) if ratio > 0 else -math.inf)
        phase_degrees.append(math.degrees(math.atan2(transfer.imag, transfer.real)) if complex_available else None)
    finite = [(i, value) for i, value in enumerate(gain_db) if math.isfinite(value)]
    if not finite:
        raise ValueError("Bode response has no finite gain samples")
    peak_index, peak_db = max(finite, key=lambda item: item[1])
    target = peak_db - 3.01029995664
    crossings: list[float] = []
    for index in range(1, len(frequency)):
        y0, y1 = gain_db[index - 1], gain_db[index]
        if not (math.isfinite(y0) and math.isfinite(y1)) or math.isclose(y0, y1):
            continue
        if (y0 - target) * (y1 - target) <= 0:
            fraction = (target - y0) / (y1 - y0)
            value = math.exp(math.log(frequency[index - 1]) + fraction * (math.log(frequency[index]) - math.log(frequency[index - 1])))
            crossings.append(value)
    lower = max((item for item in crossings if item <= frequency[peak_index]), default=None)
    upper = min((item for item in crossings if item >= frequency[peak_index]), default=None)
    all_points = [
        {"frequency_hz": f, "gain": g, "gain_db": db, "phase_degrees": phase}
        for f, g, db, phase in zip(frequency, gain, gain_db, phase_degrees)
    ]
    step = max(1, math.ceil(len(all_points) / max_points))
    points = all_points[::step]
    if all_points and points[-1] is not all_points[-1]:
        points.append(all_points[-1])
    return {
        "schema_version": 1,
        "instrument": "bode_plotter",
        "frequency_signal": frequency_name,
        "input_signal": input_name,
        "output_signal": output_name,
        "samples": len(all_points),
        "points_returned": len(points),
        "peak_gain_db": peak_db,
        "peak_frequency_hz": frequency[peak_index],
        "lower_cutoff_hz": lower,
        "upper_cutoff_hz": upper,
        "bandwidth_hz": (upper - lower) if lower is not None and upper is not None else None,
        "phase_available": complex_available,
        "phase_note": (
            "Phase is computed from the complex output/input transfer function."
            if complex_available
            else "The raw data contains real-valued traces only; phase is not inferred."
        ),
        "points": points,
    }


def logic_analyzer(
    parsed: dict[str, Any], signals: list[str], threshold: float = 2.5,
    time_signal: str | None = None, max_events: int = 10_000,
) -> dict[str, Any]:
    """Digitize analog traces and return transitions plus VCD-compatible data."""
    if not signals or len(signals) > 64:
        raise ValueError("signals must contain between 1 and 64 entries")
    if len(set(item.casefold() for item in signals)) != len(signals):
        raise ValueError("signals must not contain duplicates")
    if not math.isfinite(threshold):
        raise ValueError("threshold must be finite")
    if max_events < 1 or max_events > 100_000:
        raise ValueError("max_events must be between 1 and 100000")
    columns = [str(item) for item in parsed.get("columns", [])]
    if not columns:
        raise ValueError("experiment contains no data columns")
    resolved_time, times = _series(parsed, time_signal or columns[0])
    if any(after < before for before, after in zip(times, times[1:])):
        raise ValueError("logic analyzer time samples must be non-decreasing")
    resolved: list[str] = []
    digital: list[list[int]] = []
    for name in signals:
        actual, values = _series(parsed, name)
        if len(values) != len(times):
            raise ValueError(f"signal {name!r} and time sample counts differ")
        resolved.append(actual)
        digital.append([1 if item >= threshold else 0 for item in values])
    events: list[dict[str, Any]] = []
    previous: tuple[int, ...] | None = None
    truncated = False
    for index, stamp in enumerate(times):
        state = tuple(values[index] for values in digital)
        if state == previous:
            continue
        if len(events) >= max_events:
            truncated = True
            break
        changes = {
            resolved[position]: state[position]
            for position in range(len(state))
            if previous is None or state[position] != previous[position]
        }
        events.append({"sample": index, "time": stamp, "state": "".join(str(bit) for bit in state), "changes": changes})
        previous = state
    summaries = []
    for name, values in zip(resolved, digital):
        rising = sum(left == 0 and right == 1 for left, right in zip(values, values[1:]))
        falling = sum(left == 1 and right == 0 for left, right in zip(values, values[1:]))
        summaries.append({"signal": name, "initial": values[0], "final": values[-1], "rising_edges": rising, "falling_edges": falling})
    return {
        "schema_version": 1,
        "instrument": "logic_analyzer",
        "time_signal": resolved_time,
        "threshold": threshold,
        "samples": len(times),
        "signals": summaries,
        "events": events,
        "events_truncated": truncated,
    }
