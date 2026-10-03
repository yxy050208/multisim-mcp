"""Native-data acceptance helpers for the bounded RLC low-pass contract."""
from __future__ import annotations

import csv
import cmath
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

VIN = "V(OutProbe)"
VOUT = "V(OutProbe1)"


def validate_rlc_presentation(xml_path: Path) -> dict[str, Any]:
    """Reject the generic voltage-source example label in an RLC drawing.

    The extracted Multisim voltage carrier contains a harmless but misleading
    ``10Vpk/5kHz`` example label.  It must never survive into a generated RLC
    deliverable because the RLC request uses an AC small-signal source and the
    visible label is part of the engineering artifact.  This check is kept
    separate from the native solver checks: a numerically correct netlist can
    still be an ambiguous drawing.
    """
    root = ET.parse(xml_path).getroot()
    values = [str(item.get("Output", "")) for item in root.iter("CIITSymTextCompValue")]
    stale = [value for value in values if re.search(r"10\s*Vpk|5\s*kHz", value, re.I)]
    source_labels = [value for value in values if re.search(r"\b(?:DC|AC)\b", value, re.I)]
    return {
        "ok": bool(source_labels) and not stale,
        "source_labels": source_labels,
        "stale_template_labels": stale,
        "checked_values": len(values),
    }


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


def _peak_frequency(frequencies: list[float], gains: list[float], index: int) -> tuple[float, str]:
    """Estimate a native AC peak between adjacent logarithmic samples.

    Multisim's ``ac dec`` sweep reports a finite grid.  Ranking nearby E24
    damping values from the grid index alone makes candidates tie whenever
    the requested frequency is itself a sample.  Fit a quadratic to log
    magnitude versus log frequency around the largest sample; this preserves
    the native measurement while estimating the sub-grid vertex.  Fall back
    to the measured grid point when the maximum is at a boundary or the local
    fit is not a concave, in-range peak.
    """
    if index <= 0 or index >= len(frequencies) - 1:
        return frequencies[index], "native-grid"
    x = [math.log(frequencies[pos]) for pos in (index - 1, index, index + 1)]
    y = [math.log(max(gains[pos], 1e-300)) for pos in (index - 1, index, index + 1)]
    denominator = [
        (x[pos] - x[(pos + 1) % 3]) * (x[pos] - x[(pos + 2) % 3])
        for pos in range(3)
    ]
    if any(abs(value) < 1e-30 for value in denominator):
        return frequencies[index], "native-grid"
    coefficient_a = sum(y[pos] / denominator[pos] for pos in range(3))
    coefficient_b = -sum(
        y[pos] * (x[(pos + 1) % 3] + x[(pos + 2) % 3]) / denominator[pos]
        for pos in range(3)
    )
    if not math.isfinite(coefficient_a) or not math.isfinite(coefficient_b) or coefficient_a >= 0:
        return frequencies[index], "native-grid"
    vertex = -coefficient_b / (2 * coefficient_a)
    if not math.isfinite(vertex) or not x[0] <= vertex <= x[2]:
        return frequencies[index], "native-grid"
    return math.exp(vertex), "native-log-quadratic"


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
    peak_grid_hz = frequencies[peak_index]
    peak_hz, peak_method = _peak_frequency(frequencies, gains, peak_index)
    peak_error = abs(peak_hz / target_hz - 1)
    checks = {"ac_complex_response": max(errors) < 1e-3, "target_frequency": peak_error <= .02}
    return {"resistance_ohm": resistance, "inductance_h": inductance, "capacitance_f": capacitance,
            "peak_frequency_hz": peak_hz, "target_error_fraction": peak_error,
            "peak_frequency_grid_hz": peak_grid_hz, "peak_frequency_method": peak_method,
            "max_ac_complex_error": max(errors), "ac_points": len(rows), "checks": checks,
            "passed": all(checks.values())}


__all__ = ["evaluate_rlc", "validate_rlc_presentation"]
