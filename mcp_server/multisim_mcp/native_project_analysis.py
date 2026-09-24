"""Native COM analysis with complete, aligned evidence and explicit semantics."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .preferred_values import parse_spice_scalar
from .safety import validate_analysis_commands

# Verified against the installed MSInterface.dll SweepType enum (Multisim 14.3).
SWEEP_TYPES = {"dec": 0, "oct": 1, "lin": 2}
MAX_NATIVE_POINTS = 100000


def _positive(token: str) -> float:
    value = float(parse_spice_scalar(token))
    if not math.isfinite(value) or value <= 0:
        raise ValueError("analysis values must be finite and positive")
    return value


def analysis_parameters(action: Mapping[str, Any]) -> dict[str, Any]:
    commands = validate_analysis_commands(action["commands"])
    if len(commands) != 1:
        raise ValueError("exactly one native analysis is required")
    tokens = commands[0].lower().split()
    kind = action["analysis"]
    if tokens[0] != kind:
        raise ValueError("native command does not match analysis")
    if kind == "op" and len(tokens) == 1:
        return {}
    if kind == "ac" and len(tokens) == 5 and tokens[1] in SWEEP_TYPES:
        if not tokens[2].isdigit() or not 1 <= int(tokens[2]) <= 10000:
            raise ValueError("AC point count must be an integer between 1 and 10000")
        count, start, stop = int(tokens[2]), _positive(tokens[3]), _positive(tokens[4])
        if stop <= start or (tokens[1] == "lin" and count < 2):
            raise ValueError("AC range must increase, with at least two linear points")
        expected = count if tokens[1] == "lin" else math.ceil(count * math.log(stop / start, 10 if tokens[1] == "dec" else 2)) + 1
        if expected > MAX_NATIVE_POINTS:
            raise ValueError("AC sweep exceeds native point limit")
        return {"sweep_type": SWEEP_TYPES[tokens[1]], "num_points": count,
                "start_frequency": start, "stop_frequency": stop}
    if kind == "tran" and len(tokens) == 3:
        step, stop = _positive(tokens[1]), _positive(tokens[2])
        if step > stop or stop / step > MAX_NATIVE_POINTS - 1:
            raise ValueError("transient sampling request exceeds duration or point limit")
        return {"sample_rate": 1.0 / step, "num_samples": math.ceil(stop / step) + 1, "duration": stop}
    raise ValueError("unsupported native analysis; use op, ac, or tran step stop without optional flags")


def validate_native_analysis(action: Mapping[str, Any]) -> None:
    analysis_parameters(action)


def analyze_current_project(client: Any, action: Mapping[str, Any], output: Path, *,
                            expected_pins: Mapping[str, Any] | None = None) -> dict[str, Any]:
    parameters = analysis_parameters(action)
    output.mkdir(parents=True, exist_ok=False)
    connectivity = output / "native-connectivity.txt"
    client.report_netlist(str(connectivity), False, 0)
    if not connectivity.is_file() or not connectivity.stat().st_size:
        raise RuntimeError("native connectivity export is missing or empty")
    if expected_pins is not None:
        from .native_netlist_validation import validate_native_netlist
        topology = validate_native_netlist(connectivity.read_text(encoding="utf-8", errors="replace"),
                                           expected_pins, strict=True)
        (output / "topology.json").write_text(json.dumps(topology, ensure_ascii=False, indent=2), encoding="utf-8")
        if not topology["ok"]:
            raise RuntimeError("native pin connectivity does not match the complete proposed circuit")
    # ReportNetlist invalidates native output handles; enumerate after export.
    available = client.enum_outputs()
    (output / "available-outputs.json").write_text(json.dumps(available, ensure_ascii=False, indent=2), encoding="utf-8")
    missing = [name for name in action["outputs"] if name not in available]
    if missing:
        raise ValueError(f"native API output channels missing: {missing}; available: {available}")
    kind = action["analysis"]
    common = dict(timeout=action["timeout"], max_points=MAX_NATIVE_POINTS)
    if kind == "op":
        result = client.run_dc_operating_point(action["outputs"], **common)
    elif kind == "ac":
        result = client.run_ac_sweep(action["outputs"], **parameters, **common)
    else:
        result = client.run_transient_outputs(action["outputs"], **parameters, **common)
    (output / "native-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    if result.get("timed_out") or result.get("ready") is not True:
        raise RuntimeError("native analysis did not complete with ready outputs")
    signals, series, shared_axis = {}, {}, None
    for signal in action["outputs"]:
        payload = result.get("results", {}).get(signal) if "results" in result else result
        if not isinstance(payload, Mapping) or payload.get("output") != signal:
            raise RuntimeError(f"native analysis did not return requested output: {signal}")
        if payload.get("timed_out") or payload.get("ready") is False:
            raise RuntimeError(f"native channel failed or timed out: {signal}")
        rows = payload.get("rows")
        expected_rows = 3 if kind == "ac" else 2
        if (not isinstance(rows, list) or len(rows) != expected_rows or
                any(not isinstance(row, list) or not row or len(row) != len(rows[0]) for row in rows)):
            raise RuntimeError(f"native analysis returned an incomplete matrix: {signal}")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for row in rows for value in row):
            raise RuntimeError(f"invalid native data: {signal}")
        if payload.get("n_points") != len(rows[0]) or len(rows[0]) > MAX_NATIVE_POINTS:
            raise RuntimeError(f"native data was truncated or exceeds point limit: {signal}")
        axis = list(rows[0])
        if kind == "op" and len(axis) != 1:
            raise RuntimeError("operating point must contain exactly one sample")
        if kind != "op" and (len(axis) < 2 or any(b <= a for a, b in zip(axis, axis[1:]))):
            raise RuntimeError("native axis must contain increasing samples")
        if kind == "ac":
            start, stop, count = parameters['start_frequency'], parameters['stop_frequency'], parameters['num_points']
            if parameters['sweep_type'] == SWEEP_TYPES['lin']:
                expected_axis = [start + (stop - start) * i / (count - 1) for i in range(count)]
            else:
                base = 10 if parameters['sweep_type'] == SWEEP_TYPES['dec'] else 2
                total = math.floor(count * math.log(stop / start, base) + 1e-9) + 1
                expected_axis = [start * base ** (i / count) for i in range(total)]
            if len(axis) != len(expected_axis) or any(not math.isclose(a, b, rel_tol=1e-7, abs_tol=0) for a, b in zip(axis, expected_axis)):
                raise RuntimeError("native frequency axis does not match requested sweep")
        if kind == "tran" and (abs(axis[0]) > 1e-12 or not math.isclose(axis[-1], parameters['duration'], rel_tol=1e-7, abs_tol=1e-12)):
            raise RuntimeError("native transient did not cover requested time range")
        if shared_axis is not None and (len(axis) != len(shared_axis) or any(not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-15) for a, b in zip(axis, shared_axis))):
            raise RuntimeError("native channels have different axes; no implicit interpolation is allowed")
        shared_axis = axis
        real = [float(value) for value in rows[1]]
        if kind == "ac":
            imaginary = [float(value) for value in rows[2]]
            magnitude = [math.hypot(r, i) for r, i in zip(real, imaginary)]
            phase = [math.degrees(math.atan2(i, r)) for r, i in zip(real, imaginary)]
            series[signal] = {"real": real, "imaginary": imaginary, "magnitude": magnitude, "phase_deg": phase}
            signals[signal] = magnitude
        else:
            series[signal] = {"value": real}
            signals[signal] = real
    axis_name = {"op": "op_index", "ac": "frequency_hz", "tran": "time_s"}[kind]
    csv_columns = [axis_name]
    csv_series = [shared_axis]
    for signal, fields in series.items():
        for label, values in fields.items():
            csv_columns.append(f"{signal}.{label}")
            csv_series.append(values)
    with (output / "data.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(csv_columns)
        writer.writerows(zip(*csv_series))
    return {"success": True, "ready": True, "execution_backend": "native-com",
            "source_kind": "open-native-project", "analysis": kind, "commands": action["commands"],
            "signals": signals, "series": series, "axis": {"name": axis_name, "values": shared_axis},
            "measurement_quantity": "magnitude" if kind == "ac" else "value",
            "columns": csv_columns, "n_points": len(shared_axis), "data_complete": True,
            "sampling_semantics": "COM raw solver time points; step requests acquisition rate, not solver integration step" if kind == "tran" else None,
            "connectivity_sha256": hashlib.sha256(connectivity.read_bytes()).hexdigest()}
