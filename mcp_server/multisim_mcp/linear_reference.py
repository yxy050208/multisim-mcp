"""Independent nodal reference for R/C/L, DC/AC voltage sources and ideal opamps.

This is a verification oracle, never a substitute for native Multisim evidence.
No vendor macromodel, clipping, bandwidth, noise or semiconductor is implied.
"""
from __future__ import annotations

import cmath
import math
import re
from typing import Any

from .preferred_values import parse_spice_scalar
from .schematic_builder import parse_netlist

OPEN_LOOP_GAIN = 100000.0


def scalar(value: str) -> float:
    text = value.strip()
    sign = -1 if text.startswith("-") else 1
    unsigned = text.lstrip("+-")
    if re.fullmatch(r"(?:0+(?:\.0*)?|\.0+)(?:[eE][+-]?\d+)?", unsigned):
        return 0.0
    number = sign * float(parse_spice_scalar(unsigned))
    if not math.isfinite(number):
        raise ValueError("nonfinite component value")
    return number


def source_values(spec: Any) -> tuple[float, complex]:
    tokens = (spec.model or f"DC {spec.value}").split()
    if tokens and tokens[0].upper() != "DC":
        tokens.insert(0, "DC")
    if len(tokens) not in (2, 4, 5) or tokens[0].upper() != "DC" or (len(tokens) > 2 and tokens[2].upper() != "AC"):
        raise ValueError(f"{spec.refdes}: this native verification path supports DC value [AC magnitude [phase]] only")
    dc = scalar(tokens[1])
    ac = cmath.rect(scalar(tokens[3]), math.radians(scalar(tokens[4]) if len(tokens) == 5 else 0)) if len(tokens) > 2 else 0j
    return dc, ac


def validated_components(netlist: str, *, allow_vendor: bool = False) -> list[Any]:
    if not isinstance(netlist, str) or len(netlist) > 64000:
        raise ValueError("netlist must contain at most 64000 characters")
    parsed = parse_netlist(netlist)
    if parsed.unsupported or parsed.subcircuit_expansion_failures:
        raise ValueError("unsupported netlist statements or unexpanded subcircuits")
    parts = parsed.components
    if not parsed.grounded or not 1 <= len(parts) <= 64:
        raise ValueError("a ground and 1..64 components are required")
    refs = [p.refdes.casefold() for p in parts]
    if len(set(refs)) != len(refs):
        raise ValueError("duplicate component reference")
    nodes = {n for p in parts for n in p.nodes}
    if len(nodes) > 96 or any(not re.fullmatch(r"[A-Za-z0-9_]+", n) for n in nodes):
        raise ValueError("use at most 96 simple alphanumeric net names")
    for p in parts:
        if p.parameters:
            raise ValueError(f"{p.refdes}: extra component parameters are outside the ideal linear contract")
        if p.kind not in ({"R", "C", "L", "V", "OPAMP5", "LM324AJ", "QNPN", "D"} if allow_vendor else {"R", "C", "L", "V", "OPAMP5"}):
            raise ValueError(f"{p.refdes}: {p.kind} has no native linear-reference acceptance yet")
        if p.kind in {"R", "C", "L"} and scalar(p.value) <= 0:
            raise ValueError("passive component values must be positive")
        if p.kind == "V":
            validate_native_source(p) if allow_vendor else source_values(p)
        if p.kind == "QNPN" and (p.model.upper() != "2N3904" or p.model_definition):
            raise ValueError("QNPN requires the unmodified local 2N3904 vendor model")
        if p.kind == "D" and (p.model.upper() != "1N4001GP" or p.model_definition):
            raise ValueError("D requires the unmodified local 1N4001GP vendor model")
        if p.kind == "OPAMP5" and p.model.upper() not in {"OPAMP5", "IDEALOPAMP"}:
            raise ValueError("vendor opamp names must not be silently replaced with an ideal model")
    return parts


def validate_native_source(spec: Any) -> None:
    """Bounded DC/AC source with optional seven-parameter transient pulse."""
    expression = spec.model or f"DC {spec.value}"
    sine = re.fullmatch(r"(?i)DC\s+(\S+)\s+SIN\s*\(([^()]*)\)", expression)
    if sine:
        values = [scalar(v) for v in sine[2].split()]
        if len(values) != 3 or values[1] <= 0 or values[2] <= 0 or scalar(sine[1]) != values[0]:
            raise ValueError("SIN requires offset, positive peak amplitude and frequency; DC must equal offset")
        return
    pulse = re.search(r"(?i)\bPULSE\s*\(([^()]*)\)\s*$", expression)
    if not pulse:
        source_values(spec)
        return
    from dataclasses import replace
    source_values(replace(spec, model=expression[:pulse.start()].strip()))
    values = [scalar(v) for v in pulse[1].split()]
    if len(values) != 7:
        raise ValueError("PULSE requires low high delay rise fall width period")
    _, _, delay, rise, fall, width, period = values
    if delay < 0 or min(rise, fall, width, period) <= 0 or rise + width + fall > period:
        raise ValueError("PULSE timing must be positive, with a nonnegative delay and edges/width inside period")


def expected_native_pins(parts: list[Any]) -> dict[str, dict[int | str, str]]:
    result = {}
    for p in parts:
        pins = ["1IN+", "1IN-", "VS+", "VS-", "1OUT"] if p.kind == "LM324AJ" else ["IN+", "IN-", "VS+", "VS-", "OUT"] if p.kind == "OPAMP5" else [2, 1] if p.kind == "V" else [1, 2]
        if p.kind == "V":
            from .schematic_builder import voltage_pin_order
            pins = voltage_pin_order(p)
        if p.kind == "QNPN":
            pins = ["C", "B", "E"]
        if p.kind == "D":
            pins = ["A", "K"]
        result[p.refdes + ("A" if p.kind == "LM324AJ" else "")] = dict(zip(pins, p.nodes))
    return result


def _solve(matrix: list[list[complex]], rhs: list[complex]) -> list[complex]:
    n = len(rhs)
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(matrix[row][col]))
        if abs(matrix[pivot][col]) < 1e-20:
            raise ValueError("singular linear circuit: floating node or conflicting ideal constraints")
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        rhs[col], rhs[pivot] = rhs[pivot], rhs[col]
        scale = matrix[col][col]
        for row in range(col+1, n):
            factor = matrix[row][col]/scale
            if factor:
                for k in range(col+1, n):
                    matrix[row][k] -= factor*matrix[col][k]
                rhs[row] -= factor*rhs[col]
                matrix[row][col] = 0j
    solution = [0j]*n
    for row in range(n-1, -1, -1):
        solution[row] = (rhs[row]-sum(matrix[row][k]*solution[k] for k in range(row+1,n)))/matrix[row][row]
    if any(not math.isfinite(abs(v)) for v in solution):
        raise ValueError("nonfinite linear solution")
    return solution


def solve_linear(parts: list[Any], frequency_hz: float | None = None) -> dict[str, complex]:
    if any(p.kind not in {"R", "C", "L", "V", "OPAMP5"} for p in parts):
        raise ValueError("vendor models have no ideal linear-reference solution")
    if frequency_hz is not None and (not math.isfinite(frequency_hz) or frequency_hz <= 0):
        raise ValueError("frequency must be positive and finite")
    nodes = sorted({node for p in parts for node in p.nodes} - {"0"})
    indexes = {node: i for i,node in enumerate(nodes)}
    branches = [p for p in parts if p.kind in {"V", "L", "OPAMP5"}]
    size = len(nodes)+len(branches)
    matrix = [[0j]*size for _ in range(size)]
    rhs = [0j]*size
    def stamp(row: int | None, col: int | None, value: complex) -> None:
        if row is not None and col is not None:
            matrix[row][col] += value
    for p in parts:
        if p.kind not in {"R", "C"}:
            continue
        y = 1/scalar(p.value) if p.kind == "R" else (0j if frequency_hz is None else 2j*math.pi*frequency_hz*scalar(p.value))
        a,b = [indexes.get(n) for n in p.nodes]
        stamp(a,a,y);stamp(b,b,y);stamp(a,b,-y);stamp(b,a,-y)
    for k,p in enumerate(branches, len(nodes)):
        a,b = (indexes.get(p.nodes[4]),None) if p.kind == "OPAMP5" else [indexes.get(n) for n in p.nodes[:2]]
        stamp(a,k,1);stamp(b,k,-1);stamp(k,a,1);stamp(k,b,-1)
        if p.kind == "V":
            dc,ac = source_values(p)
            rhs[k] = dc if frequency_hz is None else ac
        elif p.kind == "L" and frequency_hz is not None:
            stamp(k,k,-2j*math.pi*frequency_hz*scalar(p.value))
        elif p.kind == "OPAMP5":
            stamp(k,indexes.get(p.nodes[0]),-OPEN_LOOP_GAIN)
            stamp(k,indexes.get(p.nodes[1]),OPEN_LOOP_GAIN)
    solution = _solve(matrix,rhs)
    return {"0": 0j, **{node:solution[i] for node,i in indexes.items()}}
