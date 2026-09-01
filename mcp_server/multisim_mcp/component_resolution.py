"""Read-only component candidates, ratings, and model provenance checks.

This module consumes an approved logical netlist draft and makes the next
handoff explicit: which component families could satisfy each logical role,
what electrical ratings are implied by the design inputs, and whether a
portable primitive, adapter, or verified external model is still needed.

It deliberately does not pick a silent part number, create ``CircuitDesign``
objects, emit SPICE, write files, render a schematic, or start a simulation.
Selections supplied by a caller are treated as review data.  A supplied
model name, URI, SHA-256, and license can satisfy the human provenance
declaration gate, but the model bytes remain unverified until a later
compiler re-hashes and loads them.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final


COMPONENT_RESOLUTION_SCHEMA_VERSION: Final = 1
COMPONENT_RESOLUTION_GENERATOR_VERSION: Final = "0.1.0"
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_SELECTIONS = 32


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
    """Normalize integral JSON numbers preserved across browser round-trips."""
    normalized: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, bool):
            normalized[str(key)] = value
        elif isinstance(value, (int, float)):
            number = _finite(value, f"design_inputs.{key}")
            normalized[str(key)] = format(number, ".15g")
        else:
            normalized[str(key)] = value
    return normalized


# ``native_kind`` refers to a schematic-builder family, not a guaranteed
# Multisim database symbol.  ``adapter_kind`` refers to a portable primitive
# expansion from component_adapters.py.  Both are deliberately conservative.
_NATIVE_KIND_BY_FAMILY: Final[dict[str, str]] = {
    "resistor": "R",
    "series-resistor": "R",
    "resistor-divider": "R",
    "precision-resistor-network": "R",
    "precision-passive-network": "R",
    "passive-network": "R",
    "capacitor": "C",
    "ceramic-capacitor": "C",
    "electrolytic-capacitor": "C",
    "low-esr-capacitor": "C",
    "timing-capacitor": "C",
    "rc-charge-bucket": "C",
    "rc-network": "C",
    "rc-filter": "C",
    "passive-low-pass": "C",
    "power-inductor": "L",
    "power-diode": "D",
    "clamp-diode": "D",
    "tvs-diode": "D",
    "nmos-pair": "MNMOS",
    "transistor-buffer": "QNPN",
    "connector": "XSUB2",
    "op-amp-integrator": "OPAMP5",
    "rail-to-rail-op-amp": "OPAMP5",
    "instrumentation-amplifier": "OPAMP5",
    "op-amp-buffer": "OPAMP5",
    "active-low-pass": "OPAMP5",
    "active-shaper": "OPAMP5",
    "comparator": "OPAMP5",
    "voltage-reference": "XSUB3",
    "precision-reference": "XSUB3",
    "line-driver": "XSUB5",
    "buffer": "XSUB5",
    "power-buffer": "XSUB5",
    "general-purpose-ic": "XSUB5",
    "high-speed-ic": "XSUB5",
    "precision-analog-ic": "XSUB5",
    "input-protection": "XSUB2",
    "filter": "XSUB2",
    "current-limiter": "XSUB2",
    "supervisor": "XSUB5",
    "current-monitor": "XSUB5",
    "test-point": "XSUB2",
    "ne555": "TIMER8",
    "cmos-555": "TIMER8",
    "crystal-oscillator": "OSC6",
    "mcu-timer": "XSUBN",
    "dds-ic": "XSUBN",
    "external-dac": "XSUBN",
    "external-adc": "XSUBN",
    "mcu-adc": "XSUBN",
    "mcu": "XSUBN",
    "dsp": "XSUBN",
    "soc": "XSUBN",
    "encoder": "XSUBN",
    "hall-sensor": "XSUBN",
    "half-bridge": "XSUBN",
    "three-phase-inverter": "XSUBN",
    "integrated-buck-regulator": "XSUBN",
    "integrated-power-stage": "XSUBN",
    "pwm-controller": "XSUBN",
    "controller-current-sense": "XSUBN",
    "shunt-amplifier": "OPAMP5",
    "dc-motor-model": "XSUBN",
    "bldc-plant-model": "XSUBN",
    "identified-plant-model": "XSUBN",
    "motor-model": "XSUBN",
    "kalman-filter": "XSUBN",
    "observer": "XSUBN",
    "firmware-function": "XSUBN",
    "analog-summing-stage": "OPAMP5",
    "analog-limiter": "OPAMP5",
    "diode-shaper": "D",
    "pwm-filter": "C",
    "compensation-network": "R",
    "type-ii-network": "R",
    "type-iii-network": "R",
    "potentiometer": "R",
    "fuse": "R",
    "ideal-diode": "D",
    "lc-filter": "L",
    "ldo": "XSUB5",
    "series-pass-regulator": "XSUB5",
    "active-filter": "OPAMP5",
    "low-noise-amplifier": "OPAMP5",
    "digital-potentiometer": "XSUBN",
    "controller-and-mosfets": "XSUBN",
}

_ADAPTER_KIND_BY_FAMILY: Final[dict[str, str]] = {
    "potentiometer": "POTENTIOMETER",
    "power-diode": "POWER_DIODE",
    "clamp-diode": "POWER_DIODE",
    "tvs-diode": "POWER_DIODE",
    "nmos-pair": "POWER_NMOS",
    "transistor-buffer": "POWER_NMOS",
    "mcu": "DFF",
    "dsp": "DFF",
    "encoder": "COUNTER4",
    "hall-sensor": "ADC1",
    "external-adc": "ADC1",
    "external-dac": "DAC1",
}

_PRIMITIVE_FAMILIES: Final[frozenset[str]] = frozenset(
    {
        "resistor",
        "series-resistor",
        "resistor-divider",
        "precision-resistor-network",
        "precision-passive-network",
        "passive-network",
        "capacitor",
        "ceramic-capacitor",
        "electrolytic-capacitor",
        "low-esr-capacitor",
        "timing-capacitor",
        "rc-charge-bucket",
        "rc-network",
        "rc-filter",
        "passive-low-pass",
        "power-inductor",
        "pwm-filter",
        "compensation-network",
        "type-ii-network",
        "type-iii-network",
    }
)

_FAMILY_LABELS: Final[dict[str, tuple[str, str]]] = {
    "resistor": ("电阻", "Resistor"),
    "series-resistor": ("串联电阻", "Series resistor"),
    "resistor-divider": ("电阻分压网络", "Resistor divider"),
    "precision-resistor-network": ("精密电阻网络", "Precision resistor network"),
    "precision-passive-network": ("精密无源网络", "Precision passive network"),
    "passive-network": ("无源网络", "Passive network"),
    "capacitor": ("电容", "Capacitor"),
    "ceramic-capacitor": ("陶瓷电容", "Ceramic capacitor"),
    "electrolytic-capacitor": ("电解电容", "Electrolytic capacitor"),
    "low-esr-capacitor": ("低 ESR 电容", "Low-ESR capacitor"),
    "timing-capacitor": ("定时电容", "Timing capacitor"),
    "rc-charge-bucket": ("RC 充电桶", "RC charge bucket"),
    "rc-network": ("RC 网络", "RC network"),
    "rc-filter": ("RC 滤波器", "RC filter"),
    "passive-low-pass": ("无源低通", "Passive low-pass"),
    "power-inductor": ("功率电感", "Power inductor"),
    "pwm-filter": ("PWM 滤波器", "PWM filter"),
    "power-diode": ("功率二极管", "Power diode"),
    "clamp-diode": ("钳位二极管", "Clamp diode"),
    "tvs-diode": ("TVS 二极管", "TVS diode"),
    "nmos-pair": ("NMOS 半桥", "NMOS pair"),
    "transistor-buffer": ("晶体管缓冲", "Transistor buffer"),
    "potentiometer": ("电位器", "Potentiometer"),
    "rail-to-rail-op-amp": ("轨到轨运放", "Rail-to-rail op-amp"),
    "op-amp-integrator": ("运放积分器", "Op-amp integrator"),
    "instrumentation-amplifier": ("仪表放大器", "Instrumentation amplifier"),
    "op-amp-buffer": ("运放缓冲", "Op-amp buffer"),
    "active-low-pass": ("有源低通", "Active low-pass"),
    "active-shaper": ("有源整形器", "Active shaper"),
    "comparator": ("比较器", "Comparator"),
    "voltage-reference": ("电压基准", "Voltage reference"),
    "precision-reference": ("精密基准", "Precision reference"),
    "line-driver": ("线路驱动器", "Line driver"),
    "buffer": ("缓冲器", "Buffer"),
    "power-buffer": ("功率缓冲", "Power buffer"),
    "general-purpose-ic": ("通用 IC", "General-purpose IC"),
    "high-speed-ic": ("高速 IC", "High-speed IC"),
    "precision-analog-ic": ("精密模拟 IC", "Precision analog IC"),
    "input-protection": ("输入保护", "Input protection"),
    "filter": ("滤波器", "Filter"),
    "current-limiter": ("限流器", "Current limiter"),
    "supervisor": ("电源监控器", "Supervisor"),
    "current-monitor": ("电流监测器", "Current monitor"),
    "test-point": ("测试点", "Test point"),
    "ne555": ("NE555", "NE555"),
    "cmos-555": ("CMOS 555", "CMOS 555"),
    "crystal-oscillator": ("晶体振荡器", "Crystal oscillator"),
    "mcu-timer": ("MCU 定时器", "MCU timer"),
    "dds-ic": ("DDS 芯片", "DDS IC"),
    "external-dac": ("外部 DAC", "External DAC"),
    "external-adc": ("外部 ADC", "External ADC"),
    "mcu-adc": ("MCU ADC", "MCU ADC"),
    "mcu": ("微控制器", "MCU"),
    "dsp": ("数字信号处理器", "DSP"),
    "soc": ("SoC", "SoC"),
    "encoder": ("编码器", "Encoder"),
    "hall-sensor": ("霍尔传感器", "Hall sensor"),
    "half-bridge": ("半桥功率级", "Half bridge"),
    "three-phase-inverter": ("三相逆变器", "Three-phase inverter"),
    "integrated-buck-regulator": ("集成 Buck 稳压器", "Integrated buck regulator"),
    "integrated-power-stage": ("集成功率级", "Integrated power stage"),
    "pwm-controller": ("PWM 控制器", "PWM controller"),
    "controller-current-sense": ("控制器电流检测", "Controller current sense"),
    "shunt-amplifier": ("分流器放大器", "Shunt amplifier"),
    "dc-motor-model": ("直流电机模型", "DC motor model"),
    "bldc-plant-model": ("无刷电机模型", "BLDC plant model"),
    "identified-plant-model": ("辨识对象模型", "Identified plant model"),
    "motor-model": ("电机模型", "Motor model"),
    "kalman-filter": ("卡尔曼滤波器", "Kalman filter"),
    "observer": ("状态观测器", "Observer"),
    "firmware-function": ("固件函数", "Firmware function"),
    "analog-summing-stage": ("模拟求和级", "Analog summing stage"),
    "analog-limiter": ("模拟限幅器", "Analog limiter"),
    "diode-shaper": ("二极管整形器", "Diode shaper"),
    "connector": ("连接器", "Connector"),
    "active-filter": ("有源滤波器", "Active filter"),
    "compensation-network": ("补偿网络", "Compensation network"),
    "controller-and-mosfets": ("控制器与 MOSFET", "Controller and MOSFETs"),
    "digital-potentiometer": ("数字电位器", "Digital potentiometer"),
    "fuse": ("保险丝", "Fuse"),
    "ideal-diode": ("理想二极管", "Ideal diode"),
    "lc-filter": ("LC 滤波器", "LC filter"),
    "ldo": ("LDO", "LDO"),
    "low-noise-amplifier": ("低噪声放大器", "Low-noise amplifier"),
    "series-pass-regulator": ("串联调整器", "Series-pass regulator"),
    "type-ii-network": ("II 型补偿网络", "Type-II network"),
    "type-iii-network": ("III 型补偿网络", "Type-III network"),
}

_VOLTAGE_FAMILIES: Final[frozenset[str]] = frozenset(
    {
        "tvs-diode", "clamp-diode", "power-diode", "capacitor", "ceramic-capacitor",
        "electrolytic-capacitor", "low-esr-capacitor", "timing-capacitor", "resistor",
        "series-resistor", "resistor-divider", "precision-resistor-network", "power-inductor",
        "nmos-pair", "transistor-buffer", "rail-to-rail-op-amp", "op-amp-integrator",
        "instrumentation-amplifier", "op-amp-buffer", "active-low-pass", "active-shaper",
        "comparator", "voltage-reference", "precision-reference", "line-driver", "buffer",
        "power-buffer", "ne555", "cmos-555", "integrated-buck-regulator", "integrated-power-stage",
        "half-bridge", "three-phase-inverter", "input-protection", "current-limiter",
        "supervisor", "current-monitor", "external-adc", "external-dac", "mcu-adc",
    }
)
_CURRENT_FAMILIES: Final[frozenset[str]] = frozenset(
    {
        "power-diode", "nmos-pair", "transistor-buffer", "power-inductor", "half-bridge",
        "three-phase-inverter", "integrated-buck-regulator", "integrated-power-stage", "shunt-amplifier",
        "controller-current-sense", "current-limiter", "current-monitor", "line-driver", "power-buffer",
    }
)
_FREQUENCY_FAMILIES: Final[frozenset[str]] = frozenset(
    {
        "ne555", "cmos-555", "crystal-oscillator", "mcu-timer", "dds-ic", "mcu", "dsp", "soc",
        "external-dac", "external-adc", "mcu-adc", "pwm-controller", "integrated-buck-regulator",
        "integrated-power-stage", "half-bridge", "three-phase-inverter", "active-low-pass", "passive-low-pass",
    }
)


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _validate_draft(draft: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(draft, Mapping):
        raise ValueError("draft must be an object")
    if draft.get("kind") != "multisim-mcp-logical-netlist-draft":
        raise ValueError("draft.kind is invalid")
    if draft.get("schema_version") != 1:
        raise ValueError("draft.schema_version must be 1")
    for field in ("draft_id", "draft_digest", "specification_digest", "selected_plan_digest"):
        value = draft.get(field)
        if not isinstance(value, str) or not _HEX_DIGEST.fullmatch(value) and field != "draft_id":
            raise ValueError(f"draft.{field} is invalid")
    if not isinstance(draft.get("approval"), Mapping) or draft["approval"].get("approved") is not True:
        raise ValueError("draft approval is required")
    if draft.get("state") != "review":
        raise ValueError("draft must remain in review state")
    if draft.get("ready_for_component_resolution") is not True:
        raise ValueError("draft is not ready for component resolution")
    if draft.get("ready_for_schematic") is not False or draft.get("ready_for_simulation") is not False:
        raise ValueError("draft execution boundary is invalid")
    boundary = draft.get("execution_boundary")
    if not isinstance(boundary, Mapping) or any(boundary.get(key) is not False for key in (
        "circuit_design_created", "spice_netlist_generated", "schematic_generated", "simulation_started", "files_written"
    )):
        raise ValueError("draft execution boundary must remain false")
    topology = draft.get("topology")
    requirements = draft.get("component_requirements")
    derived = draft.get("derived_constraints")
    if not isinstance(topology, Mapping) or not isinstance(requirements, list) or not isinstance(derived, list):
        raise ValueError("draft topology, component_requirements, and derived_constraints are invalid")
    digest_payload = {
        "generator_version": draft.get("generator_version"),
        "selected_plan_digest": draft.get("selected_plan_digest"),
        "specification_digest": draft.get("specification_digest"),
        "approval_digest": draft["approval"].get("approval_digest"),
        "topology": topology,
        "component_requirements": requirements,
        "derived_constraints": derived,
        "design_inputs": _digest_inputs(draft.get("design_inputs", {})),
    }
    if draft.get("draft_digest") != _digest(digest_payload):
        raise ValueError("draft_digest does not match the logical draft contents")
    return dict(draft)


def _design_context(draft: Mapping[str, Any]) -> dict[str, Any]:
    raw = draft.get("design_inputs", {})
    if not isinstance(raw, Mapping):
        raise ValueError("draft.design_inputs must be an object")
    context: dict[str, Any] = {}
    for key, value in raw.items():
        name = str(key)
        if isinstance(value, bool):
            raise ValueError(f"draft.design_inputs.{name} must not be boolean")
        if isinstance(value, (int, float)):
            context[name] = _finite(value, f"draft.design_inputs.{name}")
        elif isinstance(value, str):
            if len(value) > 1024 or "\x00" in value:
                raise ValueError(f"draft.design_inputs.{name} is invalid")
            context[name] = value
        else:
            raise ValueError(f"draft.design_inputs.{name} must be scalar")
    return context


def _derived_value(draft: Mapping[str, Any], constraint_id: str) -> float | None:
    for item in draft.get("derived_constraints", []):
        if isinstance(item, Mapping) and item.get("constraint_id") == constraint_id:
            return _finite(item.get("value"), f"derived_constraints.{constraint_id}")
    return None


def _rating_requirements(family: str, context: Mapping[str, Any], draft: Mapping[str, Any]) -> list[dict[str, Any]]:
    voltage_values = [
        abs(context[key]) for key in (
            "supply_voltage_v", "dc_bus_voltage_v", "motor_rated_voltage_v", "input_min_v", "input_max_v", "output_min_v", "output_max_v"
        ) if key in context
    ]
    voltage = max(voltage_values, default=0.0)
    current_values = [
        context[key] for key in ("continuous_current_a", "peak_current_a", "stall_current_a", "max_current_a") if key in context
    ]
    peak_load = _derived_value(draft, "peak-load-current")
    if peak_load is not None:
        current_values.append(peak_load)
    current = max(current_values, default=0.0)
    frequency_values = [
        context[key] for key in (
            "frequency_min_hz", "frequency_max_hz", "sample_rate_hz", "cutoff_frequency_hz", "switching_frequency_hz",
            "pwm_frequency_hz", "control_loop_frequency_hz", "bandwidth_hz"
        ) if key in context
    ]
    frequency = max(frequency_values, default=0.0)
    ratings: list[dict[str, Any]] = []

    if family in _VOLTAGE_FAMILIES:
        ratings.append({
            "metric": "voltage_rating_v",
            "minimum": round(voltage * 1.25, 6) if voltage > 0 else None,
            "unit": "V",
            "margin": 1.25,
            "basis": "max absolute design voltage × 1.25",
            "status": "calculated-not-verified" if voltage > 0 else "needs-input",
        })
    if family in _CURRENT_FAMILIES:
        ratings.append({
            "metric": "current_rating_a",
            "minimum": round(current * 1.5, 6) if current > 0 else None,
            "unit": "A",
            "margin": 1.5,
            "basis": "max design current × 1.5",
            "status": "calculated-not-verified" if current > 0 else "needs-input",
        })
    if family in _FREQUENCY_FAMILIES:
        ratings.append({
            "metric": "frequency_rating_hz",
            "minimum": round(frequency * 1.2, 6) if frequency > 0 else None,
            "unit": "Hz",
            "margin": 1.2,
            "basis": "max requested operating frequency × 1.2",
            "status": "calculated-not-verified" if frequency > 0 else "needs-input",
        })
    if "ambient_temperature_c" in context and family not in _PRIMITIVE_FAMILIES:
        ratings.append({
            "metric": "operating_temperature_c",
            "minimum": context["ambient_temperature_c"],
            "unit": "°C",
            "margin": None,
            "basis": "maximum ambient temperature; junction rise not modeled",
            "status": "calculated-not-verified",
        })
    return ratings


def _catalog_candidate(requirement_id: str, family: str, context: Mapping[str, Any], draft: Mapping[str, Any]) -> dict[str, Any]:
    if family not in _FAMILY_LABELS:
        raise ValueError(f"component family is not in the bounded catalog: {family}")
    native_kind = _NATIVE_KIND_BY_FAMILY.get(family)
    adapter_kind = _ADAPTER_KIND_BY_FAMILY.get(family)
    if family in _PRIMITIVE_FAMILIES:
        implementation = "native-primitive"
        model_requirement = "not-required"
        model_status = "not-applicable"
        confidence = "high"
    elif adapter_kind:
        implementation = "portable-adapter"
        model_requirement = "portable-adapter"
        model_status = "portable-adapter-available"
        confidence = "medium"
    else:
        implementation = "native-carrier" if native_kind else "external-model"
        model_requirement = "verified-model-required"
        model_status = "pending-model-source"
        confidence = "low"
    label_zh, label_en = _FAMILY_LABELS[family]
    return {
        "candidate_id": f"cand-{requirement_id}-{family}",
        "family": family,
        "label_zh": label_zh,
        "label_en": label_en,
        "implementation_kind": implementation,
        "native_kind": native_kind,
        "adapter_kind": adapter_kind,
        "model_requirement": model_requirement,
        "model_status": model_status,
        "confidence": confidence,
        "provenance": {
            "source": "built-in-catalog",
            "source_status": "catalog-only",
            "model_sha256": None,
            "license_status": "pending" if model_requirement == "verified-model-required" else "not-applicable",
        },
        "rating_requirements": _rating_requirements(family, context, draft),
        "part_number": None,
        "notes": "候选族仅用于审阅；尚未锁定具体型号。",
    }


def _selection(value: Any, candidate_list: Sequence[Mapping[str, Any]], requirement_id: str) -> dict[str, Any]:
    if isinstance(value, str):
        chosen_family = value
        payload: dict[str, Any] = {"family": chosen_family}
    elif isinstance(value, Mapping):
        allowed = {
            "family", "candidate_id", "part_number", "model_source", "voltage_rating_v", "current_rating_a",
            "frequency_rating_hz", "temperature_rating_c", "review_note",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"selection {requirement_id} contains unknown fields: {sorted(unknown)}")
        payload = dict(value)
        chosen_family = payload.get("family")
        if chosen_family is None and isinstance(payload.get("candidate_id"), str):
            matching = [item for item in candidate_list if item.get("candidate_id") == payload["candidate_id"]]
            if matching:
                chosen_family = matching[0]["family"]
    else:
        raise ValueError(f"selection {requirement_id} must be a family string or object")
    if not isinstance(chosen_family, str):
        raise ValueError(f"selection {requirement_id} requires a candidate family")
    matching = [item for item in candidate_list if item.get("family") == chosen_family]
    if not matching:
        raise ValueError(f"selection {requirement_id} family is not one of the candidates: {chosen_family}")
    candidate = dict(matching[0])
    candidate_id = candidate["candidate_id"]
    if payload.get("candidate_id") is not None and payload.get("candidate_id") != candidate_id:
        raise ValueError(f"selection {requirement_id}.candidate_id does not match family")
    part_number = payload.get("part_number")
    if part_number is not None:
        if not isinstance(part_number, str) or not part_number.strip() or len(part_number) > 160 or "\x00" in part_number:
            raise ValueError(f"selection {requirement_id}.part_number is invalid")
        candidate["part_number"] = part_number.strip()
    model_source = payload.get("model_source")
    if model_source is not None:
        if not isinstance(model_source, Mapping):
            raise ValueError(f"selection {requirement_id}.model_source must be an object")
        allowed_source = {"name", "uri", "sha256", "license"}
        unknown_source = set(model_source) - allowed_source
        if unknown_source:
            raise ValueError(f"selection {requirement_id}.model_source contains unknown fields: {sorted(unknown_source)}")
        if not isinstance(model_source.get("name"), str) or not model_source["name"].strip():
            raise ValueError(f"selection {requirement_id}.model_source.name is required")
        sha256 = model_source.get("sha256")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            raise ValueError(f"selection {requirement_id}.model_source.sha256 must be a SHA-256 digest")
        candidate["provenance"] = {
            "source": model_source["name"].strip()[:160],
            "source_status": "provided-not-verified",
            "model_sha256": sha256.lower(),
            "license_status": str(model_source.get("license", "pending"))[:80],
            "uri": str(model_source.get("uri", ""))[:300],
        }
        candidate["model_status"] = "provided-not-verified"
    rating_values: dict[str, float] = {}
    for key in ("voltage_rating_v", "current_rating_a", "frequency_rating_hz", "temperature_rating_c"):
        if key in payload:
            rating_values[key] = _finite(payload[key], f"selection {requirement_id}.{key}")
    candidate["declared_ratings"] = rating_values
    candidate["review_note"] = str(payload.get("review_note", ""))[:512]
    return candidate


def _rating_status(candidate: Mapping[str, Any]) -> str:
    requirements = candidate.get("rating_requirements", [])
    declared = candidate.get("declared_ratings", {})
    if not requirements:
        return "not-required"
    if not isinstance(declared, Mapping):
        return "pending"
    statuses: list[str] = []
    for item in requirements:
        metric = item.get("metric")
        minimum = item.get("minimum")
        if minimum is None or metric not in declared:
            statuses.append("pending")
        elif float(declared[metric]) >= float(minimum):
            statuses.append("passed")
        else:
            statuses.append("failed")
    if "failed" in statuses:
        return "failed"
    if "pending" in statuses:
        return "pending"
    return "passed"


def _selection_snapshot(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded caller selection used by later approval gates.

    The snapshot intentionally contains review inputs rather than the whole
    catalog candidate.  Replaying it through :func:`resolve_component_requirements`
    must produce the same resolution digest, including after a JSON/browser
    round-trip.
    """
    snapshot: dict[str, Any] = {
        "family": candidate["family"],
        "candidate_id": candidate["candidate_id"],
    }
    part_number = candidate.get("part_number")
    if isinstance(part_number, str) and part_number:
        snapshot["part_number"] = part_number
    provenance = candidate.get("provenance")
    if (
        candidate.get("model_status") == "provided-not-verified"
        and isinstance(provenance, Mapping)
    ):
        snapshot["model_source"] = {
            "name": provenance.get("source", ""),
            "uri": provenance.get("uri", ""),
            "sha256": provenance.get("model_sha256", ""),
            "license": provenance.get("license_status", "pending"),
        }
    declared = candidate.get("declared_ratings")
    if isinstance(declared, Mapping):
        for key in (
            "voltage_rating_v",
            "current_rating_a",
            "frequency_rating_hz",
            "temperature_rating_c",
        ):
            if key in declared:
                snapshot[key] = _finite(declared[key], f"selection_snapshot.{key}")
    review_note = candidate.get("review_note")
    if isinstance(review_note, str) and review_note:
        snapshot["review_note"] = review_note
    return snapshot


def resolve_component_requirements(
    draft: Mapping[str, Any],
    selections: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return bounded candidates and rating gates for a logical netlist draft."""
    validated = _validate_draft(draft)
    selection_map = {} if selections is None else selections
    if not isinstance(selection_map, Mapping):
        raise ValueError("selections must be an object")
    if len(selection_map) > _MAX_SELECTIONS:
        raise ValueError(f"selections must contain at most {_MAX_SELECTIONS} items")
    context = _design_context(validated)
    resolved_requirements: list[dict[str, Any]] = []
    unresolved = 0
    model_pending = 0
    ratings_pending = 0
    normalized_selections: dict[str, dict[str, Any]] = {}
    for raw in validated["component_requirements"]:
        if not isinstance(raw, Mapping):
            raise ValueError("draft component requirement must be an object")
        requirement_id = raw.get("requirement_id")
        families = raw.get("candidate_families")
        if not isinstance(requirement_id, str) or not isinstance(families, list) or not families:
            raise ValueError("draft component requirement is malformed")
        candidates = [_catalog_candidate(requirement_id, str(family), context, validated) for family in families]
        chosen = None
        if requirement_id in selection_map:
            chosen = _selection(selection_map[requirement_id], candidates, requirement_id)
            chosen["rating_status"] = _rating_status(chosen)
        else:
            chosen = None
        recommended = candidates[0]
        if chosen is None:
            unresolved += 1
            selected_candidate_id = None
            selection_status = "recommended-awaiting-human-selection"
            rating_status = "pending"
            model_status = recommended["model_status"]
        else:
            selected_candidate_id = chosen["candidate_id"]
            selection_status = "selected-awaiting-verification"
            rating_status = chosen["rating_status"]
            model_status = chosen["model_status"]
            normalized_selections[requirement_id] = _selection_snapshot(chosen)
        # ``provided-not-verified`` means the reviewer supplied bounded
        # provenance (name/URI/SHA-256/license).  It is enough to enter the
        # human approval gate, but the future compiler must hash the actual
        # model bytes again before inclusion.
        if model_status == "pending-model-source":
            model_pending += 1
        if rating_status in {"pending", "failed"}:
            ratings_pending += 1
        resolved_requirements.append(
            {
                "requirement_id": requirement_id,
                "module_id": raw.get("module_id"),
                "role": raw.get("role"),
                "candidate_families": list(families),
                "selection_status": selection_status,
                "selected_candidate_id": selected_candidate_id,
                "recommended_candidate_id": recommended["candidate_id"],
                "selected_candidate": chosen,
                "candidates": candidates,
                "rating_status": rating_status,
                "model_status": model_status,
            }
        )
    if set(selection_map) - {item["requirement_id"] for item in resolved_requirements}:
        unknown = sorted(set(selection_map) - {item["requirement_id"] for item in resolved_requirements})
        raise ValueError(f"selections contain unknown requirement IDs: {unknown}")
    digest_payload = {
        "generator_version": COMPONENT_RESOLUTION_GENERATOR_VERSION,
        "draft_digest": validated["draft_digest"],
        "selections": normalized_selections,
        "requirements": resolved_requirements,
    }
    resolution_digest = _digest(digest_payload)
    all_selected = unresolved == 0
    all_ratings_passed = all(item["rating_status"] in {"passed", "not-required"} for item in resolved_requirements)
    models_ready = all(
        item["model_status"]
        in {"not-applicable", "portable-adapter-available", "provided-not-verified"}
        for item in resolved_requirements
    )
    if not all_selected:
        next_step = "select_components_and_confirm_ratings"
        state = "candidate-review"
    elif not all_ratings_passed:
        next_step = "confirm_component_ratings"
        state = "ratings-review"
    elif not models_ready:
        next_step = "provide_model_provenance"
        state = "model-review"
    else:
        next_step = "compile_executable_netlist_after_human_approval"
        state = "ready-for-netlist-review"
    return {
        "schema_version": COMPONENT_RESOLUTION_SCHEMA_VERSION,
        "generator_version": COMPONENT_RESOLUTION_GENERATOR_VERSION,
        "kind": "multisim-mcp-component-resolution",
        "resolution_id": f"resolution-{resolution_digest[:32]}",
        "resolution_digest": resolution_digest,
        "draft_id": validated["draft_id"],
        "draft_digest": validated["draft_digest"],
        "selected_option_id": validated.get("selected_option_id"),
        "state": state,
        "design_inputs": dict(context),
        "selection_snapshot": normalized_selections,
        "requirements": resolved_requirements,
        "summary": {
            "requirement_count": len(resolved_requirements),
            "unresolved_selection_count": unresolved,
            "model_pending_count": model_pending,
            "ratings_pending_count": ratings_pending,
            "recommended_only": not all_selected,
        },
        "review_gates": [
            {"gate_id": "logical-draft-integrity", "status": "passed"},
            {"gate_id": "component-family-selection", "status": "passed" if all_selected else "pending"},
            {"gate_id": "component-ratings", "status": "passed" if all_ratings_passed else "pending"},
            {"gate_id": "model-provenance", "status": "passed" if models_ready else "pending"},
            {"gate_id": "executable-netlist", "status": "pending"},
            {"gate_id": "human-netlist-approval", "status": "pending"},
        ],
        "ready_for_executable_netlist": False,
        "ready_for_schematic": False,
        "ready_for_simulation": False,
        "next_step": next_step,
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
    "COMPONENT_RESOLUTION_GENERATOR_VERSION",
    "COMPONENT_RESOLUTION_SCHEMA_VERSION",
    "resolve_component_requirements",
]
