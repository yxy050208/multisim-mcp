"""Native-data acceptance helpers for the bounded RLC low-pass contract."""
from __future__ import annotations

import csv
import cmath
import math
from pathlib import Path
from typing import Any

VIN = "V(OutProbe)"
VOUT = "V(OutProbe1)"


def _csv(path: Path) -> list[dict[str, float]]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = [{key: float(value) for key, value in row.items()} for row in csv.DictReader(stream)]
    if len(rows) < 2 or any(not math.isfinite(value) for row in rows for value in row.values()):
        raise ValueError(f"missing or nonfinite native RLC measurements: {path}")
    return rows


def _ac_file(directory: Path) -> Path:
    candidates = sorted(directory.glob("analysis-*/data.csv"))
    for candidate in candidates:
        with candidate.open(encoding="utf-8", newline="") as stream:
            header = next(csv.reader(stream), [])
        if "frequency_hz" in header and f"{VOUT}.real" in header:
            return candidate
    raise ValueError("native RLC result has no AC frequency matrix")


def evaluate_rlc(directory: Path, resistance: float, inductance: float, capacitance: float,
                 *, target_hz: float) -> dict[str, Any]:
    if not all(math.isfinite(value) and value > 0 for value in (resistance, inductance, capacitance, target_hz)):
        raise ValueError("RLC values must be positive finite numbers")
    rows = _csv(_ac_file(directory))
    frequencies: list[float] = []
    gains: list[float] = []
    errors: list[float] = []
    for row in rows:
        frequency = row["frequency_hz"]
        if frequency <= 0:
            raise ValueError("native RLC frequencies must be positive")
        input_value = complex(row[f"{VIN}.real"], row[f"{VIN}.imaginary"])
        output_value = complex(row[f"{VOUT}.real"], row[f"{VOUT}.imaginary"])
        if abs(input_value) == 0:
            raise ValueError("native RLC AC input is zero")
        gain = output_value / input_value
        omega = 2 * math.pi * frequency
        reference = 1 / (1 - omega * omega * inductance * capacitance + 1j * omega * resistance * capacitance)
        frequencies.append(frequency)
        gains.append(abs(gain))
        errors.append(abs(gain - reference))
    peak_index = max(range(len(gains)), key=gains.__getitem__)
    peak_hz = frequencies[peak_index]
    peak_error = abs(peak_hz / target_hz - 1)
    checks = {"ac_complex_response": max(errors) < 1e-3, "target_frequency": peak_error <= .02}
    return {"resistance_ohm": resistance, "inductance_h": inductance, "capacitance_f": capacitance,
            "peak_frequency_hz": peak_hz, "target_error_fraction": peak_error,
            "max_ac_complex_error": max(errors), "ac_points": len(rows), "checks": checks,
            "passed": all(checks.values())}


__all__ = ["evaluate_rlc"]
