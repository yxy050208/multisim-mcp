"""Conservative evidence classification for digital output observations.

Multisim may enumerate a native digital component while exposing no digital
output through its COM/raw-data surface.  This module keeps that distinction
machine-readable: a component can be present, an output can be observed, and
the two facts are never silently conflated.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .schematic_builder import parse_netlist


DIGITAL_OBSERVATION_SCHEMA_VERSION = 1

# Output positions are zero-based in the parsed component node list.
_DIGITAL_OUTPUTS: dict[str, tuple[tuple[int, str], ...]] = {
    "DFF8": ((4, "q"), (5, "qbar")),
    "DJK7": ((5, "q"), (6, "qbar")),
    "DNOT4": ((1, "out"),),
    "DAND5": ((2, "out"),),
    "DOR5": ((2, "out"),),
    "DNAND5": ((2, "out"),),
    "DNOR5": ((2, "out"),),
    "DXOR5": ((2, "out"),),
    "DXNOR5": ((2, "out"),),
}


def _canonical(value: str) -> str:
    return "".join(value.casefold().split())


def _net_aliases(value: str) -> frozenset[str]:
    canonical = _canonical(value)
    aliases = {canonical}
    match = re.fullmatch(r"[vi]\((.+)\)", canonical)
    if match:
        aliases.add(match.group(1))
    else:
        aliases.add(f"v({canonical})")
    return frozenset(aliases)


def _raw_column_for_net(columns: Sequence[str], net: str) -> str | None:
    expected = _net_aliases(net)
    for column in columns:
        if expected.intersection(_net_aliases(str(column))):
            return str(column)
    return None


def _native_presence(
    value: Mapping[str, Any] | None,
    refdes: str,
) -> bool | None:
    if value is None or refdes not in value:
        return None
    raw = value[refdes]
    return raw if isinstance(raw, bool) else None


def build_digital_observation_evidence(
    netlist: str,
    columns: Sequence[str],
    *,
    backend_id: str,
    native_component_presence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify digital output nets against the columns returned by a backend.

    The function does not infer a missing output, and it does not claim that a
    behavioral backend proves a native component.  It only joins parsed output
    pins with observed raw columns and records the available evidence scope.
    """

    if not isinstance(netlist, str):
        raise ValueError("netlist must be a string")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
        raise ValueError("columns must be an array of names")
    if not isinstance(backend_id, str) or not backend_id.strip():
        raise ValueError("backend_id must be a non-empty string")
    parsed = parse_netlist(netlist)
    signals: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for component in parsed.components:
        outputs = _DIGITAL_OUTPUTS.get(component.kind.upper())
        if not outputs:
            continue
        for position, pin in outputs:
            if position >= len(component.nodes):
                continue
            net = str(component.nodes[position])
            key = (component.refdes, pin, _canonical(net))
            if key in seen:
                continue
            seen.add(key)
            raw_column = _raw_column_for_net(columns, net)
            native_present = _native_presence(native_component_presence, component.refdes)
            if raw_column is None:
                status = "unobserved"
                claim = "unobserved"
                reason = "backend raw output did not expose the digital output net"
            else:
                status = "observed"
                if backend_id.casefold() == "multisim" and native_present is True:
                    claim = "native-component-output-observed"
                elif backend_id.casefold() == "ngspice":
                    claim = "behavioral-reference-output-observed"
                else:
                    claim = "backend-output-observed"
                reason = "matched backend raw output column"
            signals.append(
                {
                    "component": component.refdes,
                    "kind": component.kind,
                    "pin": pin,
                    "net": net,
                    "status": status,
                    "raw_column": raw_column,
                    "native_component_present": native_present,
                    "claim": claim,
                    "reason": reason,
                }
            )

    counts = {
        "observed": sum(item["status"] == "observed" for item in signals),
        "unobserved": sum(item["status"] == "unobserved" for item in signals),
    }
    normalized_backend = backend_id.strip().casefold()
    if not signals:
        overall = "not-applicable"
    elif counts["unobserved"] == 0:
        overall = "complete"
    elif counts["observed"] == 0:
        overall = "unobserved"
    else:
        overall = "partial"
    native_evidence = any(
        item.get("native_component_present") is True for item in signals
    )
    if normalized_backend == "multisim" and counts["unobserved"]:
        routing = {
            "recommended_backend": "ngspice",
            "mode": "explicit-rerun",
            "automatic_switch": False,
            "reason": (
                "Multisim did not expose one or more digital output nets; "
                "rerun explicitly with backend='ngspice' for behavioral-reference evidence."
            ),
        }
    else:
        routing = {
            "recommended_backend": normalized_backend,
            "mode": "none",
            "automatic_switch": False,
            "reason": "No digital-output fallback is required for this result.",
        }
    return {
        "schema_version": DIGITAL_OBSERVATION_SCHEMA_VERSION,
        "backend_id": normalized_backend,
        "overall_status": overall,
        "counts": counts,
        "signals": signals,
        "scope": (
            "native-multisim-output"
            if normalized_backend == "multisim" and native_evidence
            else "behavioral-reference-output"
            if normalized_backend == "ngspice"
            else "backend-output"
        ),
        "routing": routing,
        "limitations": (
            [
                "Component enumeration does not prove that a digital output is observable.",
                "Behavioral-reference output does not prove native component timing or electrical behavior.",
            ]
            if signals
            else []
        ),
    }


__all__ = [
    "DIGITAL_OBSERVATION_SCHEMA_VERSION",
    "build_digital_observation_evidence",
]
