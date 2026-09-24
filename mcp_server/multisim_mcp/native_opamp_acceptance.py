"""Acceptance checks for the bounded ideal OPAMP5 experiment."""
from __future__ import annotations

from typing import Any, Mapping


def evaluate_opamp_ac(result: Mapping[str, Any], target_gain: float, tolerance: float = .02) -> dict[str, Any]:
    """Evaluate low-frequency gain from a Multisim AC result matrix."""
    outputs = result.get("results", {})
    if not isinstance(outputs, Mapping) or len(outputs) < 2:
        return {"passed": False, "reason": "missing input/output AC outputs"}
    names = list(outputs)
    input_rows = outputs[names[0]].get("rows", [])
    output_rows = outputs[names[1]].get("rows", [])
    try:
        input_real = float(input_rows[1][0])
        output_real = float(output_rows[1][0])
    except (IndexError, TypeError, ValueError):
        return {"passed": False, "reason": "invalid AC result matrix"}
    measured = abs(output_real / input_real) if input_real else 0.0
    error = abs(measured - target_gain) / max(abs(target_gain), 1e-12)
    return {"passed": error <= tolerance, "measured_gain": measured,
            "target_gain": target_gain, "relative_error": error,
            "input_output": [names[0], names[1]]}


__all__ = ["evaluate_opamp_ac"]
