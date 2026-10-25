"""First-order affine conditioning with two explicit finite-gain ideal opamps.

Synthesis is deterministic SI arithmetic, not simulation or hardware selection.
Supply-referenced resistor branches include their loading in the equations.
"""

from __future__ import annotations

import math
from decimal import Decimal, localcontext
from collections.abc import Mapping
from typing import Any

from .eda_core import CircuitComponent
from .ideal_models import IDEAL_OPAMP5_SHA256, OPEN_LOOP_GAIN, ideal_opamp5_contract


ACTIVE_SIGNAL_TEMPLATE = "ideal-opamp-affine-low-pass-v1"


def synthesize_active_signal(
    values: Mapping[str, float],
) -> tuple[tuple[CircuitComponent, ...], dict[str, Any]]:
    """Synthesize an input RC, buffer and weighted non-inverting output stage."""
    supply = values["supply_voltage_v"]
    source = values["source_impedance_ohm"]
    load = values["load_impedance_ohm"]
    cutoff = values["cutoff_frequency_hz"]
    # Decimal endpoint products prevent a pure decimal gain (e.g. 0.1..0.3
    # -> 0.3..0.9) producing a tiny floating-point bias and a spurious T-ohm
    # resistor. Compute the intercept numerator before dividing; do not erase
    # a small, actually requested offset with an arbitrary epsilon.
    with localcontext() as context:
        context.prec = 50
        imin, imax, omin, omax = (
            Decimal(str(values[key])) for key in (
                "input_min_v", "input_max_v", "output_min_v", "output_max_v",
            )
        )
        gain = float((omax - omin) / (imax - imin))
        offset = float((omin * imax - omax * imin) / (imax - imin))
    if not 1e-6 <= gain <= 100:
        raise ValueError(f"{ACTIVE_SIGNAL_TEMPLATE} supports gain from 1e-6 to 100 V/V")
    follower_gain = OPEN_LOOP_GAIN / (OPEN_LOOP_GAIN + 1)
    closed_loop_gain = 2 * (1 + gain / follower_gain + abs(offset) / supply)
    if not 1 < closed_loop_gain < OPEN_LOOP_GAIN:
        raise ValueError("active synthesis requires a finite closed-loop gain below the open-loop gain")

    # Fractions of total conductance at each opamp input. The finite-A term
    # belongs in the feedback weight, otherwise the target affine gain drifts.
    input_weight = gain / (closed_loop_gain * follower_gain)
    positive_bias_weight = max(offset, 0) / (closed_loop_gain * supply)
    plus_ground_weight = 1 - input_weight - positive_bias_weight
    feedback_weight = 1 / closed_loop_gain - 1 / OPEN_LOOP_GAIN
    negative_bias_weight = max(-offset, 0) / (closed_loop_gain * supply)
    minus_ground_weight = 1 - feedback_weight - negative_bias_weight
    calculated: dict[str, float] = {
        "RPROT_ohm": max(1000, 10 * source),
        "RIN_ohm": 10000 / input_weight,
        "RPG_ohm": 10000 / plus_ground_weight,
        "RF_ohm": 10000 / feedback_weight,
        "RMG_ohm": 10000 / minus_ground_weight,
        "RLOAD_ohm": load,
        "CDEC_f": 100e-9,
    }
    calculated["CFLT_f"] = 1 / (
        2 * math.pi * (source + calculated["RPROT_ohm"]) * cutoff
    )
    if positive_bias_weight:
        calculated["RPB_ohm"] = 10000 / positive_bias_weight
    if negative_bias_weight:
        calculated["RMB_ohm"] = 10000 / negative_bias_weight
    for name, value in calculated.items():
        minimum, maximum = (1e-3, 1e12) if name.endswith("_ohm") else (1e-15, 1)
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"calculated {name} is outside the bounded SI component range [{minimum:g}, {maximum:g}]")

    def passive(ref: str, kind: str, nodes: tuple[str, str], requirement: str, role: str) -> CircuitComponent:
        unit = "ohm" if kind == "R" else "f"
        return CircuitComponent(
            refdes=ref, kind=kind, nodes=nodes,
            value=format(calculated[f"{ref}_{unit}"], ".12g"),
            annotations={**({"requirement_id": requirement} if requirement else {}), "role": role, "value_unit": unit},
        )

    def opamp(ref: str, plus: str, minus: str, out: str, requirement: str) -> CircuitComponent:
        return CircuitComponent(
            refdes=ref, kind="OPAMP5", nodes=(plus, minus, "rail-positive", "gnd", out),
            model="OPAMP5", annotations={
                "requirement_id": requirement, "model_scope": "explicit-ideal-vcvs",
                "model_contract_sha256": IDEAL_OPAMP5_SHA256,
                "pin_order": ideal_opamp5_contract()["pin_order"],
            },
        )

    components = [
        CircuitComponent("VCC", "V", ("rail-positive", "gnd"), value=format(supply, ".12g"),
                         annotations={"role": "approved positive supply", "value_unit": "V"}),
        passive("RPROT", "R", ("path-00", "path-01"), "cr-01", "input series protection"),
        passive("CFLT", "C", ("path-01", "gnd"), "cr-06", "first-order input low-pass"),
        opamp("XU1", "path-01", "path-02", "path-02", "cr-02"),
        passive("RIN", "R", ("path-02", "path-03"), "cr-03", "weighted signal input"),
        passive("RPG", "R", ("path-03", "gnd"), "cr-03", "non-inverting node ground weight"),
        passive("RF", "R", ("path-04", "feedback"), "cr-03", "negative feedback"),
        passive("RMG", "R", ("feedback", "gnd"), "cr-03", "inverting node ground weight"),
        opamp("XU2", "path-03", "feedback", "path-04", "cr-04"),
        passive("RLOAD", "R", ("path-04", "gnd"), "", "declared output load; do not duplicate externally"),
        passive("CDEC", "C", ("rail-positive", "gnd"), "cr-05", "supply decoupling"),
    ]
    if positive_bias_weight:
        components.append(passive("RPB", "R", ("rail-positive", "path-03"), "cr-03", "positive offset conductance"))
    if negative_bias_weight:
        components.append(passive("RMB", "R", ("rail-positive", "feedback"), "cr-03", "negative offset conductance"))

    synthesis = {
        "template": ACTIVE_SIGNAL_TEMPLATE,
        "calculation_status": "deterministic-finite-gain-ideal-not-simulated",
        "spice_dialect": "multisim-mcp-native-carriers",
        "target_transfer": {
            "gain_v_per_v": gain, "offset_v": offset,
            "input_min_v": values["input_min_v"], "input_max_v": values["input_max_v"],
            "estimated_output_min_v": gain * values["input_min_v"] + offset,
            "estimated_output_max_v": gain * values["input_max_v"] + offset,
        },
        "calculated_values": calculated,
        "estimated_cutoff_hz": cutoff,
        "model_contract": {**ideal_opamp5_contract(), "sha256": IDEAL_OPAMP5_SHA256},
        "module_ports": [
            {"module_id": "module-01", "input_net": "path-00", "output_net": "path-01", "components": ["RPROT", "CFLT"]},
            {"module_id": "module-02", "input_net": "path-01", "output_net": "path-02", "components": ["XU1", "CDEC"]},
            {"module_id": "module-03", "input_net": "path-02", "output_net": "path-03", "components": ["RIN", "RPG", "RF", "RMG"] + (["RPB"] if positive_bias_weight else []) + (["RMB"] if negative_bias_weight else [])},
            {"module_id": "module-04", "input_net": "path-03", "output_net": "path-04", "components": ["XU2", "RLOAD"]},
        ],
        "interface_contract": {
            "input_net": "path-00", "output_net": "path-04", "ground_net": "gnd",
            "supply_net": "rail-positive", "source_impedance_ohm": source,
            "included_load_ohm": load, "input_source_included": False,
            "analysis_commands_included": False,
        },
        "interface_conditions": [
            "External source must drive path-00 through exactly the declared source impedance.",
            "RLOAD already represents the declared output load; an added load changes the reviewed circuit.",
            "OPAMP5 requires the Multisim MCP ideal carrier; other SPICE engines need an explicit compatible model.",
            "Series protection does not implement surge clamping or reverse-polarity protection.",
            "ADC driver is an ideal output stage with a resistive load, not a sampling ADC or charge-bucket model.",
            "Calculated values are SI numbers, not preferred-series or tolerance-selected physical parts.",
            "Supply-derived offset follows supply variation; no precision reference is implemented.",
        ],
    }
    return tuple(components), synthesis
