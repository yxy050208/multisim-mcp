"""Explicit conversion of supported native carriers to behavioral references.

The conversion is intentionally opt-in.  It is useful when a native Multisim
component opens and enumerates correctly but its digital outputs are not
available through the native/raw-data surface.  The returned netlist is a
portable source netlist for the existing ``@DFF`` adapter; it is not a claim
that the native vendor model and the behavioral model are electrically or
timing-equivalent.
"""

from __future__ import annotations

import re
from typing import Any

from .safety import validate_spice_netlist
from .schematic_builder import parse_netlist


BEHAVIORAL_REFERENCE_SCHEMA_VERSION = 1
_MAX_NETLIST_BYTES = 2_000_000
_NATIVE_DFF_MODELS = frozenset(
    {"DFF8", "7474N", "7474", "74LS74N", "74LS74D"}
)
_REFDES_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:$-]*$")


def _native_dff_line(parts: list[str]) -> tuple[str, list[str], str] | None:
    """Return refdes/nodes/model for a strict native 8-pin DFF carrier."""

    if len(parts) < 2 or parts[0][:1].upper() not in {"X", "U"}:
        return None
    # A native 8-terminal X/U carrier can only place its model at token 9;
    # token 8 is also treated as a malformed candidate so we fail closed on a
    # missing terminal. Ignore earlier occurrences because a legal net name
    # may coincidentally equal ``7474N``.
    model_positions = [
        index for index, token in enumerate(parts[8:], start=8)
        if token.upper() in _NATIVE_DFF_MODELS
    ]
    if not model_positions:
        return None
    model_index = model_positions[0]
    model = parts[model_index]
    if len(parts) != 10:
        raise ValueError(
            f"{parts[0]} {model} must have exactly 8 terminals and one model token"
        )
    if model_index != 9:
        raise ValueError(
            f"{parts[0]} {model} must place the model after exactly 8 terminals"
        )
    if not _REFDES_RE.fullmatch(parts[0]):
        raise ValueError(f"unsafe native DFF reference designator: {parts[0]!r}")
    return parts[0], parts[1:9], model


def build_behavioral_reference_netlist(netlist: str) -> dict[str, Any]:
    """Convert supported native DFF carriers into explicit ``@DFF`` adapters.

    Native pin order is ``D, ~PR, ~CLR, CLK, Q, ~Q, GND, VCC``.  The portable
    adapter order is ``D, CLK, SET, RESET, Q, QBAR, HIGH, LOW``.  Because the
    XSPICE asynchronous set/reset inputs are asserted high, the generated
    reference inserts explicit NOT devices for the native active-low ~PR/~CLR
    pins. The result must be passed explicitly to the ngspice backend by the
    caller.
    """

    if not isinstance(netlist, str):
        raise ValueError("netlist must be a string")
    if not netlist.strip():
        raise ValueError("netlist must not be empty")
    if len(netlist.encode("utf-8")) > _MAX_NETLIST_BYTES:
        raise ValueError("netlist exceeds the 2 MB safety limit")
    validate_spice_netlist(netlist)

    rendered: list[str] = []
    converted: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(netlist.splitlines(), start=1):
        stripped = raw_line.strip()
        parts = stripped.split()
        parsed = _native_dff_line(parts) if stripped and not stripped.startswith(("*", ";", "#", ".")) else None
        if parsed is None:
            rendered.append(raw_line)
            continue
        refdes, nodes, model = parsed
        d, preset, clear, clk, q, qbar, ground, supply = nodes
        stem = re.sub(r"[^A-Za-z0-9_]", "_", refdes) or "DFF"
        reference_refdes = f"X{stem}_BEHAVIORAL"
        set_reference = f"n_{stem}_pr_bar"
        reset_reference = f"n_{stem}_clr_bar"
        rendered.append(
            f"* Multisim MCP behavioral reference: {refdes} {model} -> @DFF; "
            "native ~PR/~CLR are active-low and explicitly inverted"
        )
        rendered.append(
            f"A{stem}PRINV {preset} {set_reference} {supply} {ground} NOT"
        )
        rendered.append(
            f"A{stem}CLRINV {clear} {reset_reference} {supply} {ground} NOT"
        )
        rendered.append(
            f"{reference_refdes} {d} {clk} {set_reference} {reset_reference} "
            f"{q} {qbar} {supply} {ground} @DFF"
        )
        converted.append(
            {
                "line": line_number,
                "source_refdes": refdes,
                "source_model": model,
                "reference_refdes": reference_refdes,
                "source_pin_order": ["D", "~PR", "~CLR", "CLK", "Q", "~Q", "GND", "VCC"],
                "reference_pin_order": ["D", "CLK", "SET", "RESET", "Q", "QBAR", "HIGH", "LOW"],
                "pin_mapping": {
                    "d": d,
                    "clk": clk,
                    "set": preset,
                    "reset": clear,
                    "q": q,
                    "qbar": qbar,
                    "high": supply,
                    "low": ground,
                },
                "reference_control_nets": {
                    "set": set_reference,
                    "reset": reset_reference,
                },
                "control_inverters": [
                    {"input": preset, "output": set_reference, "polarity": "active-low"},
                    {"input": clear, "output": reset_reference, "polarity": "active-low"},
                ],
                "preset_polarity": "active-low",
                "clear_polarity": "active-low",
                "claim": "behavioral-reference-only",
            }
        )

    converted_netlist = "\n".join(rendered)
    if netlist.endswith(("\n", "\r")):
        converted_netlist += "\n"
    validate_spice_netlist(converted_netlist)
    parsed_reference = parse_netlist(converted_netlist)
    reference_count = sum(
        item.kind == "DJK7" for item in parsed_reference.components
    )
    if reference_count < len(converted):
        raise ValueError(
            "behavioral reference conversion did not produce a JK-backed DFF for: "
            + ", ".join(item["source_refdes"] for item in converted)
        )
    return {
        "schema_version": BEHAVIORAL_REFERENCE_SCHEMA_VERSION,
        "source_backend": "multisim",
        "target_backend": "ngspice",
        "converted": bool(converted),
        "converted_count": len(converted),
        "components": converted,
        "netlist": converted_netlist,
        "limitations": [
            "This is a behavioral reference, not native vendor-model evidence.",
            "Timing, thresholds, propagation delays, power behavior, and loading may differ.",
            "The caller must explicitly choose backend='ngspice' to run the reference netlist.",
        ],
    }


__all__ = [
    "BEHAVIORAL_REFERENCE_SCHEMA_VERSION",
    "build_behavioral_reference_netlist",
]
