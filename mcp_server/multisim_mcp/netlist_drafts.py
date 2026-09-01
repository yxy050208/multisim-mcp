"""Read-only logical netlist drafts from approved electrical specifications.

The draft intentionally stops before component selection, executable SPICE,
``CircuitDesign`` creation, schematic rendering, file writes, or simulation.
It gives every planning option one stable module/net/connection contract so a
human can review the topology before the more expensive synthesis stages.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Final

from .design_plans import validate_selected_design_plan
from .design_specifications import prepare_design_specification


NETLIST_DRAFT_SCHEMA_VERSION: Final = 1
NETLIST_DRAFT_GENERATOR_VERSION: Final = "0.1.0"


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _digest_inputs(values: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize JSON number spellings before binding a browser handoff digest."""
    normalized: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, bool):
            normalized[str(key)] = value
        elif isinstance(value, (int, float)):
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"design input {key} must be finite")
            normalized[str(key)] = format(number, ".15g")
        else:
            normalized[str(key)] = value
    return normalized


def _reviewed_specification(
    plan: Mapping[str, Any],
    specification: Mapping[str, Any],
) -> dict[str, Any]:
    selected_plan = validate_selected_design_plan(plan)
    if not isinstance(specification, Mapping):
        raise ValueError("specification must be an object")
    if specification.get("kind") != "multisim-mcp-design-specification":
        raise ValueError("specification kind is invalid")
    resolved = specification.get("resolved_parameters")
    if not isinstance(resolved, Mapping):
        raise ValueError("specification.resolved_parameters must be an object")
    regenerated = prepare_design_specification(plan, resolved)
    comparisons = {
        "specification_id": regenerated["specification_id"],
        "specification_digest": regenerated["specification_digest"],
        "plan_id": selected_plan.plan_id,
        "selected_option_id": selected_plan.selected_option_id,
        "selected_plan_digest": plan["selected_plan_digest"],
        "selection_digest": plan["selection_digest"],
        "domain": selected_plan.domain,
    }
    for field_name, expected in comparisons.items():
        if specification.get(field_name) != expected:
            raise ValueError(f"specification.{field_name} does not match selected plan")
    if specification.get("state") != "ready" or not specification.get(
        "ready_for_netlist_draft"
    ):
        raise ValueError("specification must be complete before preparing a netlist draft")
    if specification.get("missing_parameter_ids") != []:
        raise ValueError("specification still contains missing parameters")
    if specification.get("modules") != regenerated["modules"]:
        raise ValueError("specification.modules does not match selected plan")
    if specification.get("analysis_plan") != regenerated["analysis_plan"]:
        raise ValueError("specification.analysis_plan does not match selected plan")
    if specification.get("validation_gates") != regenerated["validation_gates"]:
        raise ValueError("specification.validation_gates does not match selected plan")
    return regenerated


def _approval(
    approval: Mapping[str, Any],
    specification: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(approval, Mapping):
        raise ValueError("approval must be an object")
    allowed = {"approved", "specification_id", "specification_digest", "review_note"}
    unknown = set(approval) - allowed
    if unknown:
        raise ValueError(f"approval contains unknown fields: {sorted(unknown)}")
    if approval.get("approved") is not True:
        raise ValueError("approval.approved must be true after explicit human review")
    for field_name in ("specification_id", "specification_digest"):
        if approval.get(field_name) != specification[field_name]:
            raise ValueError(f"approval.{field_name} does not match specification")
    review_note = approval.get("review_note", "")
    if not isinstance(review_note, str) or "\x00" in review_note or len(review_note) > 1024:
        raise ValueError("approval.review_note is invalid")
    payload = {
        "approved": True,
        "specification_id": specification["specification_id"],
        "specification_digest": specification["specification_digest"],
        "review_note": review_note.strip(),
    }
    payload["approval_digest"] = _digest(payload)
    return payload


# Each tuple is ``(module index, role, candidate component families)``.  These
# are requirements, not silently selected part numbers.  Keeping that boundary
# explicit prevents a block diagram from masquerading as an electrically valid
# or simulator-ready circuit.
_COMPONENT_REQUIREMENTS: Final[
    dict[str, tuple[tuple[int, str, tuple[str, ...]], ...]]
] = {
    "control-pid-feedforward": (
        (0, "motor and load plant", ("dc-motor-model", "bldc-plant-model")),
        (1, "real-time PID controller", ("mcu", "dsp")),
        (2, "feed-forward path", ("firmware-function", "analog-summing-stage")),
        (3, "position or speed feedback", ("encoder", "hall-sensor")),
        (1, "power actuation stage", ("half-bridge", "three-phase-inverter")),
    ),
    "control-robust-pid": (
        (0, "motor and load plant", ("dc-motor-model", "bldc-plant-model")),
        (1, "cascaded PID controller", ("mcu", "dsp")),
        (2, "anti-windup limiter", ("firmware-function", "analog-limiter")),
        (3, "measurement filter and gain schedule", ("firmware-function", "active-filter")),
        (1, "protected power stage", ("half-bridge", "three-phase-inverter")),
    ),
    "control-mpc": (
        (0, "state-space plant", ("identified-plant-model", "motor-model")),
        (1, "state estimator", ("kalman-filter", "observer")),
        (2, "real-time optimizer", ("mcu", "dsp", "soc")),
        (3, "constraint and fallback controller", ("firmware-function",)),
        (2, "power actuation stage", ("half-bridge", "three-phase-inverter")),
    ),
    "power-linear": (
        (0, "input reverse-polarity and surge protection", ("fuse", "tvs-diode", "ideal-diode")),
        (1, "linear regulator", ("ldo", "series-pass-regulator")),
        (2, "input and output decoupling", ("ceramic-capacitor", "electrolytic-capacitor")),
        (1, "feedback and set-point network", ("resistor-divider",)),
    ),
    "power-buck": (
        (0, "input protection and EMI filter", ("fuse", "tvs-diode", "lc-filter")),
        (1, "buck controller", ("pwm-controller", "integrated-buck-regulator")),
        (1, "high-side and low-side switches", ("nmos-pair", "integrated-power-stage")),
        (2, "energy storage filter", ("power-inductor", "low-esr-capacitor")),
        (3, "feedback compensation", ("type-ii-network", "type-iii-network")),
        (1, "current sensing and protection", ("shunt-amplifier", "controller-current-sense")),
    ),
    "power-hybrid": (
        (0, "input protection", ("fuse", "tvs-diode", "ideal-diode")),
        (1, "buck pre-regulator", ("integrated-buck-regulator", "controller-and-mosfets")),
        (2, "low-noise linear post-regulator", ("ldo", "series-pass-regulator")),
        (3, "stage decoupling", ("ceramic-capacitor", "low-esr-capacitor")),
        (1, "pre-regulator feedback", ("resistor-divider", "compensation-network")),
    ),
    "signal-passive": (
        (0, "input protection", ("series-resistor", "clamp-diode", "tvs-diode")),
        (1, "passive low-pass network", ("resistor", "capacitor")),
        (2, "bias and ADC interface", ("resistor-divider", "rc-charge-bucket")),
    ),
    "signal-active": (
        (0, "input protection", ("series-resistor", "clamp-diode")),
        (1, "active filter", ("rail-to-rail-op-amp", "instrumentation-amplifier")),
        (2, "gain and level shift network", ("precision-resistor-network", "voltage-reference")),
        (3, "ADC driver and charge bucket", ("op-amp-buffer", "rc-network")),
        (1, "supply decoupling", ("ceramic-capacitor",)),
    ),
    "signal-digital": (
        (0, "input protection", ("series-resistor", "clamp-diode")),
        (1, "anti-alias filter", ("rc-filter", "active-low-pass")),
        (2, "analog-to-digital conversion", ("mcu-adc", "external-adc")),
        (3, "digital filter and calibration", ("mcu", "dsp")),
        (2, "voltage reference and decoupling", ("voltage-reference", "ceramic-capacitor")),
    ),
    "waveform-analog-555": (
        (0, "astable timing element", ("ne555", "cmos-555")),
        (1, "frequency-setting network", ("resistor", "potentiometer", "timing-capacitor")),
        (2, "amplitude and waveform shaping", ("resistor-divider", "op-amp-integrator", "active-shaper")),
        (3, "loaded output buffer", ("rail-to-rail-op-amp", "transistor-buffer")),
        (0, "supply and control-pin decoupling", ("ceramic-capacitor",)),
    ),
    "waveform-mcu-dds": (
        (0, "clock and timing source", ("crystal-oscillator", "mcu-timer")),
        (1, "waveform synthesis", ("mcu", "dds-ic")),
        (2, "digital-to-analog path", ("external-dac", "pwm-filter")),
        (2, "reconstruction filter", ("active-low-pass", "passive-low-pass")),
        (3, "loaded output buffer", ("rail-to-rail-op-amp", "line-driver")),
    ),
    "waveform-opamp": (
        (0, "Schmitt-trigger oscillator", ("rail-to-rail-op-amp", "comparator")),
        (1, "integrator", ("op-amp-integrator",)),
        (2, "sine shaping network", ("diode-shaper", "active-shaper")),
        (3, "loaded output buffer", ("rail-to-rail-op-amp", "line-driver")),
        (0, "reference and decoupling", ("voltage-reference", "ceramic-capacitor")),
    ),
    "general-minimal": (
        (0, "input interface", ("connector", "input-protection")),
        (1, "minimum functional network", ("passive-network", "general-purpose-ic")),
        (2, "output and load interface", ("buffer", "connector")),
    ),
    "general-robust": (
        (0, "protected input interface", ("tvs-diode", "current-limiter", "filter")),
        (1, "functional core", ("general-purpose-ic", "passive-network")),
        (2, "protection and diagnostics", ("supervisor", "current-monitor", "test-point")),
        (3, "protected output interface", ("buffer", "current-limiter", "connector")),
    ),
    "general-performance": (
        (0, "precision input interface", ("low-noise-amplifier", "precision-passive-network")),
        (1, "high-performance functional core", ("high-speed-ic", "precision-analog-ic")),
        (2, "calibration and compensation", ("digital-potentiometer", "precision-reference")),
        (3, "high-drive output interface", ("line-driver", "power-buffer")),
    ),
}


def _logical_topology(modules: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    nets: list[dict[str, str]] = [
        {"net_id": "gnd", "label": "参考地", "kind": "ground"},
        {"net_id": "rail-positive", "label": "正电源轨", "kind": "power"},
    ]
    for index in range(len(modules) + 1):
        nets.append(
            {
                "net_id": f"path-{index:02d}",
                "label": "外部输入" if index == 0 else (
                    "外部输出" if index == len(modules) else f"模块间网络 {index}"
                ),
                "kind": "signal-or-power-path",
            }
        )
    rendered_modules: list[dict[str, Any]] = []
    connections: list[dict[str, Any]] = []
    for index, module in enumerate(modules):
        module_id = str(module["module_id"])
        input_net = f"path-{index:02d}"
        output_net = f"path-{index + 1:02d}"
        rendered_modules.append(
            {
                "module_id": module_id,
                "instance_id": f"B{index + 1:02d}",
                "name": module["name"],
                "input_nets": [input_net],
                "output_nets": [output_net],
                "supply_nets": ["rail-positive", "gnd"],
                "status": "logical-only",
            }
        )
        connections.append(
            {
                "connection_id": f"conn-{index + 1:02d}",
                "net_id": output_net,
                "from_module": module_id,
                "to_module": (
                    str(modules[index + 1]["module_id"])
                    if index + 1 < len(modules)
                    else "external-output"
                ),
                "status": "planned",
            }
        )
    return {"nets": nets, "modules": rendered_modules, "connections": connections}


def _component_requirements(option_id: str, modules: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    template = _COMPONENT_REQUIREMENTS.get(option_id)
    if template is None:
        raise ValueError(f"selected option has no logical topology template: {option_id}")
    requirements: list[dict[str, Any]] = []
    for index, (module_index, role, families) in enumerate(template, start=1):
        if module_index >= len(modules):
            raise ValueError(f"logical topology template is incompatible with option {option_id}")
        primitive_only = all(
            family in {
                "resistor",
                "capacitor",
                "ceramic-capacitor",
                "electrolytic-capacitor",
                "low-esr-capacitor",
                "power-inductor",
                "potentiometer",
                "timing-capacitor",
                "series-resistor",
                "resistor-divider",
                "precision-resistor-network",
                "passive-network",
            }
            for family in families
        )
        requirements.append(
            {
                "requirement_id": f"cr-{index:02d}",
                "module_id": modules[module_index]["module_id"],
                "role": role,
                "candidate_families": list(families),
                "selection_status": "unresolved",
                "rating_status": "unchecked",
                "model_requirement": "primitive" if primitive_only else "verified-model-required",
                "model_status": "not-applicable" if primitive_only else "unresolved",
            }
        )
    return requirements


def _calculated(
    constraint_id: str,
    label: str,
    value: float,
    unit: str,
    basis: str,
) -> dict[str, Any]:
    if not math.isfinite(value):
        raise ValueError(f"derived constraint {constraint_id} is not finite")
    return {
        "constraint_id": constraint_id,
        "label": label,
        "value": round(value, 9),
        "unit": unit,
        "basis": basis,
        "status": "calculated-not-verified",
    }


def _derived_constraints(domain: str, values: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if domain == "waveform-generation":
        f_min = float(values["frequency_min_hz"])
        f_max = float(values["frequency_max_hz"])
        load = float(values["load_resistance_ohm"])
        amplitude = float(values["output_amplitude_vpp"])
        if f_max < f_min:
            raise ValueError("frequency_max_hz must be >= frequency_min_hz")
        result.extend(
            [
                _calculated("frequency-span", "频率调节比", f_max / f_min, "ratio", "f_max / f_min"),
                _calculated("peak-load-current", "按峰峰值估算的峰值负载电流", amplitude / (2 * load), "A", "Vpp / (2·Rload)"),
                _calculated("sine-load-power", "正弦输出等效负载功率", amplitude * amplitude / (8 * load), "W", "Vpp² / (8·Rload)"),
            ]
        )
    elif domain == "power-electronics":
        v_min = float(values["input_voltage_min_v"])
        v_max = float(values["input_voltage_max_v"])
        continuous = float(values["continuous_current_a"])
        peak = float(values["peak_current_a"])
        if v_max < v_min:
            raise ValueError("input_voltage_max_v must be >= input_voltage_min_v")
        if peak < continuous:
            raise ValueError("peak_current_a must be >= continuous_current_a")
        result.extend(
            [
                _calculated("input-span", "输入电压范围", v_max - v_min, "V", "Vin,max - Vin,min"),
                _calculated("peak-current-ratio", "峰值/连续电流比", peak / continuous if continuous else 0, "ratio", "Ipeak / Icontinuous"),
                _calculated("nominal-output-power", "连续输出功率基线", float(values["output_voltage_v"]) * continuous, "W", "Vout · Icontinuous"),
            ]
        )
    elif domain == "signal-conditioning":
        input_span = float(values["input_max_v"]) - float(values["input_min_v"])
        output_span = float(values["output_max_v"]) - float(values["output_min_v"])
        if input_span <= 0 or output_span <= 0:
            raise ValueError("signal input and output ranges must have positive spans")
        result.append(
            _calculated("span-gain", "量程映射增益", output_span / input_span, "V/V", "output span / input span")
        )
        if "sample_rate_hz" in values:
            result.append(
                _calculated("sample-cutoff-ratio", "采样率/截止频率比", float(values["sample_rate_hz"]) / float(values["cutoff_frequency_hz"]), "ratio", "fs / fc")
            )
    elif domain == "robot-control":
        continuous = float(values["continuous_current_a"])
        stall = float(values["stall_current_a"])
        if stall < continuous:
            raise ValueError("stall_current_a must be >= continuous_current_a")
        loop_frequency = float(values["control_loop_frequency_hz"])
        result.extend(
            [
                _calculated("pwm-loop-ratio", "PWM/控制环频率比", float(values["pwm_frequency_hz"]) / loop_frequency, "ratio", "fpwm / floop"),
                _calculated("stall-current-ratio", "堵转/连续电流比", stall / continuous if continuous else 0, "ratio", "Istall / Icontinuous"),
                _calculated("latency-period-budget", "延迟占控制周期比例", float(values["max_latency_ms"]) * loop_frequency / 1000, "ratio", "latency · floop"),
            ]
        )
    else:
        input_span = float(values["input_max_v"]) - float(values["input_min_v"])
        output_span = float(values["output_max_v"]) - float(values["output_min_v"])
        if input_span <= 0 or output_span <= 0:
            raise ValueError("general input and output ranges must have positive spans")
        result.append(
            _calculated("span-transfer", "输出/输入量程比", output_span / input_span, "ratio", "output span / input span")
        )
    return result


def _preview(topology: Mapping[str, Any], requirements: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# multisim-mcp logical-netlist-draft v1",
        "# NOT SPICE / NOT EXECUTABLE / COMPONENTS UNRESOLVED",
    ]
    for module in topology["modules"]:
        lines.append(
            "MODULE "
            + " ".join(
                [
                    str(module["instance_id"]),
                    f'name="{module["name"]}"',
                    f'in={module["input_nets"][0]}',
                    f'out={module["output_nets"][0]}',
                    "supply=rail-positive,gnd",
                ]
            )
        )
    for item in requirements:
        lines.append(
            f'REQUIRE {item["requirement_id"]} module={item["module_id"]} '
            f'family={"|".join(item["candidate_families"])}'
        )
    return "\n".join(lines) + "\n"


def prepare_netlist_draft(
    plan: Mapping[str, Any],
    specification: Mapping[str, Any],
    approval: Mapping[str, Any],
) -> dict[str, Any]:
    """Prepare a stable logical netlist after explicit specification approval."""
    selected_plan = validate_selected_design_plan(plan)
    reviewed_spec = _reviewed_specification(plan, specification)
    reviewed_approval = _approval(approval, reviewed_spec)
    topology = _logical_topology(reviewed_spec["modules"])
    requirements = _component_requirements(
        selected_plan.selected_option_id or "",
        reviewed_spec["modules"],
    )
    derived = _derived_constraints(
        selected_plan.domain,
        reviewed_spec["resolved_parameters"],
    )
    digest_payload = {
        "generator_version": NETLIST_DRAFT_GENERATOR_VERSION,
        "selected_plan_digest": plan["selected_plan_digest"],
        "specification_digest": reviewed_spec["specification_digest"],
        "approval_digest": reviewed_approval["approval_digest"],
        "topology": topology,
        "component_requirements": requirements,
        "derived_constraints": derived,
        "design_inputs": _digest_inputs(reviewed_spec["resolved_parameters"]),
    }
    draft_digest = _digest(digest_payload)
    return {
        "schema_version": NETLIST_DRAFT_SCHEMA_VERSION,
        "generator_version": NETLIST_DRAFT_GENERATOR_VERSION,
        "kind": "multisim-mcp-logical-netlist-draft",
        "draft_id": f"draft-{draft_digest[:32]}",
        "draft_digest": draft_digest,
        "plan_id": selected_plan.plan_id,
        "selected_option_id": selected_plan.selected_option_id,
        "selected_plan_digest": plan["selected_plan_digest"],
        "specification_id": reviewed_spec["specification_id"],
        "specification_digest": reviewed_spec["specification_digest"],
        "approval": reviewed_approval,
        "title": f"{reviewed_spec['title']} · 逻辑网表草案",
        "domain": selected_plan.domain,
        "state": "review",
        "topology_level": "logical-block-netlist",
        "topology": topology,
        "component_requirements": requirements,
        "derived_constraints": derived,
        "design_inputs": dict(reviewed_spec["resolved_parameters"]),
        "logical_netlist_preview": _preview(topology, requirements),
        "review_gates": [
            {"gate_id": "specification-approval", "status": "passed"},
            {"gate_id": "logical-topology", "status": "prepared-not-verified"},
            {"gate_id": "component-selection", "status": "pending"},
            {"gate_id": "component-ratings", "status": "pending"},
            {"gate_id": "model-provenance", "status": "pending"},
            {"gate_id": "executable-netlist", "status": "pending"},
            {"gate_id": "human-netlist-approval", "status": "pending"},
        ],
        "ready_for_component_resolution": True,
        "ready_for_schematic": False,
        "ready_for_simulation": False,
        "next_step": "resolve_components_ratings_and_models",
        "execution_boundary": {
            "circuit_design_created": False,
            "spice_netlist_generated": False,
            "schematic_generated": False,
            "simulation_started": False,
            "files_written": False,
        },
        "artifacts_generated": [],
    }


__all__ = [
    "NETLIST_DRAFT_GENERATOR_VERSION",
    "NETLIST_DRAFT_SCHEMA_VERSION",
    "prepare_netlist_draft",
]
