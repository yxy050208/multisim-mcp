"""Read-only preparation of an electrical design specification.

This stage sits between a selected :class:`DesignPlan` and any generated
``CircuitDesign`` or SPICE netlist.  It turns the chosen architecture into a
bounded checklist of electrical parameters, modules, analyses, and validation
gates.  It never creates circuit artifacts or starts an EDA backend.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from .design_plans import DesignPlan, validate_selected_design_plan


DESIGN_SPECIFICATION_SCHEMA_VERSION: Final = 1
SPECIFICATION_PREPARER_VERSION: Final = "0.1.0"
MAX_SPECIFICATION_PARAMETERS: Final = 32

_PARAMETER_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_FREQUENCY_RANGE = re.compile(
    r"(?P<low>\d+(?:\.\d+)?)\s*(?P<low_prefix>[kKmM]?)\s*Hz\s*"
    r"(?:~|～|至|到|-)\s*(?P<high>\d+(?:\.\d+)?)\s*"
    r"(?P<high_prefix>[kKmM]?)\s*Hz",
    re.IGNORECASE,
)
_SUPPLY_VOLTAGE = re.compile(
    r"(?:"
    r"(?:供电|电源|single[ -]?supply|supply)[^\d+]{0,20}\+?\s*(\d+(?:\.\d+)?)\s*V"
    r"|\+?\s*(\d+(?:\.\d+)?)\s*V[^\d]{0,12}(?:单电源|供电|supply)"
    r")",
    re.IGNORECASE,
)
_LOAD_RESISTANCE = re.compile(
    r"(?:负载|load)[^\d]{0,20}(\d+(?:\.\d+)?)\s*([kKmM]?)\s*(?:Ω|欧姆|ohm)",
    re.IGNORECASE,
)


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _scale(prefix: str) -> float:
    if prefix in {"k", "K"}:
        return 1e3
    if prefix == "M":
        return 1e6
    if prefix == "m":
        return 1e-3
    return 1.0


@dataclass(frozen=True, slots=True)
class ParameterDefinition:
    parameter_id: str
    label: str
    description: str
    value_type: str = "number"
    unit: str = ""
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    suggested_value: Any = None

    def __post_init__(self) -> None:
        if not _PARAMETER_ID.fullmatch(self.parameter_id):
            raise ValueError("parameter_id is invalid")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("parameter label must not be empty")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("parameter description must not be empty")
        if self.value_type not in {"number", "integer", "choice", "text"}:
            raise ValueError("parameter value_type is invalid")
        if self.value_type == "choice" and not self.choices:
            raise ValueError("choice parameter requires choices")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("parameter minimum must not exceed maximum")

    def resolve(self, raw_value: Any, *, provided: bool, source: str | None) -> dict[str, Any]:
        value = raw_value
        if provided:
            if self.value_type == "number":
                if isinstance(value, bool):
                    raise ValueError(f"parameter_values.{self.parameter_id} must be a number")
                try:
                    value = float(value)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(
                        f"parameter_values.{self.parameter_id} must be a number"
                    ) from exc
                if not math.isfinite(value):
                    raise ValueError(f"parameter_values.{self.parameter_id} must be finite")
            elif self.value_type == "integer":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(f"parameter_values.{self.parameter_id} must be an integer")
            elif self.value_type == "choice":
                if not isinstance(value, str) or value not in self.choices:
                    raise ValueError(
                        f"parameter_values.{self.parameter_id} must be one of: "
                        + ", ".join(self.choices)
                    )
            else:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"parameter_values.{self.parameter_id} must be non-empty text")
                if len(value) > 1024 or "\x00" in value:
                    raise ValueError(f"parameter_values.{self.parameter_id} is invalid")
                value = value.strip()
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if self.minimum is not None and value < self.minimum:
                    raise ValueError(
                        f"parameter_values.{self.parameter_id} must be >= {self.minimum}"
                    )
                if self.maximum is not None and value > self.maximum:
                    raise ValueError(
                        f"parameter_values.{self.parameter_id} must be <= {self.maximum}"
                    )
        status = "provided" if provided else ("missing" if self.required else "optional")
        return {
            "parameter_id": self.parameter_id,
            "label": self.label,
            "description": self.description,
            "value_type": self.value_type,
            "unit": self.unit,
            "required": self.required,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "choices": list(self.choices),
            "suggested_value": self.suggested_value,
            "value": value if provided else None,
            "source": source,
            "status": status,
        }


def _p(
    parameter_id: str,
    label: str,
    description: str,
    *,
    value_type: str = "number",
    unit: str = "",
    required: bool = True,
    minimum: float | None = None,
    maximum: float | None = None,
    choices: Sequence[str] = (),
    suggested_value: Any = None,
) -> ParameterDefinition:
    return ParameterDefinition(
        parameter_id=parameter_id,
        label=label,
        description=description,
        value_type=value_type,
        unit=unit,
        required=required,
        minimum=minimum,
        maximum=maximum,
        choices=tuple(choices),
        suggested_value=suggested_value,
    )


def _domain_parameters(domain: str, option_id: str) -> tuple[ParameterDefinition, ...]:
    if domain == "waveform-generation":
        base = [
            _p("supply_voltage_v", "供电电压", "电路使用的标称电源电压。", unit="V", minimum=0.1, maximum=1000),
            _p("frequency_min_hz", "最低输出频率", "连续调节范围的下限。", unit="Hz", minimum=0.001),
            _p("frequency_max_hz", "最高输出频率", "连续调节范围的上限。", unit="Hz", minimum=0.001),
            _p("output_amplitude_vpp", "目标输出峰峰值", "主要输出端在额定负载下的目标幅度。", unit="Vpp", minimum=0.001),
            _p("load_resistance_ohm", "负载电阻", "每个输出通道的等效负载。", unit="Ω", minimum=0.001),
            _p("waveform_targets", "目标波形", "使用逗号分隔需要输出的波形。", value_type="text", suggested_value="方波, 三角波, 正弦波"),
            _p("max_frequency_error_percent", "最大频率误差", "允许的频率相对误差。", unit="%", minimum=0, maximum=100, suggested_value=5),
            _p("max_amplitude_error_percent", "最大幅度误差", "允许的幅度相对误差。", unit="%", minimum=0, maximum=100, suggested_value=5),
        ]
        if option_id == "waveform-mcu-dds":
            base.extend([
                _p("sample_rate_hz", "波形更新率", "DDS、PWM 或 DAC 的更新频率。", unit="Hz", minimum=1),
                _p("dac_resolution_bits", "DAC 分辨率", "用于幅度量化预算。", value_type="integer", unit="bit", minimum=4, maximum=32, suggested_value=12),
            ])
        else:
            base.append(
                _p("timing_tolerance_percent", "定时元件容差", "用于频率角落与蒙特卡洛分析。", unit="%", minimum=0, maximum=100, suggested_value=5)
            )
        return tuple(base)
    if domain == "power-electronics":
        base = [
            _p("input_voltage_min_v", "最低输入电压", "供电输入范围下限。", unit="V", minimum=0),
            _p("input_voltage_max_v", "最高输入电压", "供电输入范围上限。", unit="V", minimum=0.001),
            _p("output_voltage_v", "目标输出电压", "额定工作点的输出电压。", unit="V", minimum=0.001),
            _p("continuous_current_a", "连续输出电流", "持续负载条件下的电流。", unit="A", minimum=0),
            _p("peak_current_a", "峰值输出电流", "启动、堵转或瞬态期间的峰值电流。", unit="A", minimum=0),
            _p("ripple_max_mv", "最大允许纹波", "额定负载下允许的输出纹波峰峰值。", unit="mVpp", minimum=0),
            _p("ambient_temperature_c", "最高环境温度", "热额定值校核使用的环境温度。", unit="°C", minimum=-80, maximum=200, suggested_value=50),
        ]
        if option_id in {"power-buck", "power-hybrid"}:
            base.append(
                _p("switching_frequency_hz", "开关频率", "磁性元件、损耗和纹波设计基准。", unit="Hz", minimum=100, suggested_value=500000)
            )
        return tuple(base)
    if domain == "signal-conditioning":
        base = [
            _p("supply_voltage_v", "供电电压", "模拟前端或转换器的标称供电。", unit="V", minimum=0.1, maximum=1000),
            _p("input_min_v", "最小输入电压", "传感器或信号源的最小输出。", unit="V"),
            _p("input_max_v", "最大输入电压", "传感器或信号源的最大输出。", unit="V"),
            _p("output_min_v", "目标最小输出", "下游接口可接受的最小输入。", unit="V"),
            _p("output_max_v", "目标最大输出", "下游接口可接受的最大输入。", unit="V"),
            _p("source_impedance_ohm", "信号源阻抗", "滤波器和保护网络设计所需的源阻抗。", unit="Ω", minimum=0),
            _p("load_impedance_ohm", "负载阻抗", "ADC 或后级电路的等效输入阻抗。", unit="Ω", minimum=0.001),
            _p("cutoff_frequency_hz", "目标截止频率", "滤波器的关键频率指标。", unit="Hz", minimum=0.001),
        ]
        if option_id == "signal-digital":
            base.append(
                _p("sample_rate_hz", "采样率", "用于抗混叠、量化和数字延迟预算。", unit="Hz", minimum=0.001)
            )
        return tuple(base)
    if domain == "robot-control":
        return (
            _p("dc_bus_voltage_v", "直流母线电压", "电机驱动功率级的标称母线电压。", unit="V", minimum=0.1, maximum=1000),
            _p("motor_rated_voltage_v", "电机额定电压", "电机铭牌额定电压。", unit="V", minimum=0.1, maximum=1000),
            _p("continuous_current_a", "连续相电流", "持续工况的驱动电流。", unit="A", minimum=0),
            _p("stall_current_a", "堵转/峰值电流", "驱动器、连接器和保护设计的峰值依据。", unit="A", minimum=0),
            _p("pwm_frequency_hz", "PWM 频率", "开关损耗、噪声和控制分辨率的折中。", unit="Hz", minimum=100),
            _p("control_loop_frequency_hz", "控制环频率", "控制器执行与反馈采样频率。", unit="Hz", minimum=1),
            _p("feedback_sensor", "反馈传感器", "闭环反馈的主要传感器类型。", value_type="choice", choices=("encoder", "hall", "resolver", "sensorless", "other")),
            _p("max_latency_ms", "最大允许延迟", "采样到执行的端到端时延上限。", unit="ms", minimum=0, suggested_value=3),
            _p("ambient_temperature_c", "最高环境温度", "比赛现场与封闭机舱热校核条件。", unit="°C", minimum=-80, maximum=200, suggested_value=60),
        )
    return (
        _p("supply_voltage_v", "供电电压", "电路的标称供电。", unit="V", minimum=0.1, maximum=1000),
        _p("input_min_v", "最小输入", "正常工作输入范围下限。", unit="V"),
        _p("input_max_v", "最大输入", "正常工作输入范围上限。", unit="V"),
        _p("output_min_v", "目标最小输出", "目标输出范围下限。", unit="V"),
        _p("output_max_v", "目标最大输出", "目标输出范围上限。", unit="V"),
        _p("max_current_a", "最大电流", "供电、走线、器件和保护额定值依据。", unit="A", minimum=0),
        _p("bandwidth_hz", "目标带宽", "系统需要覆盖的最高有效频率。", unit="Hz", minimum=0.001),
        _p("load_impedance_ohm", "负载阻抗", "输出端等效负载。", unit="Ω", minimum=0.001),
    )


_CONSTRAINT_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "supply_voltage_v": ("supply_voltage_v", "supply_v", "voltage_v"),
    "dc_bus_voltage_v": ("dc_bus_voltage_v", "bus_voltage_v", "supply_voltage_v"),
    "max_latency_ms": ("max_latency_ms", "latency_ms"),
    "continuous_current_a": ("continuous_current_a", "load_current_a", "rated_current_a"),
    "load_resistance_ohm": ("load_resistance_ohm", "load_ohm"),
    "frequency_min_hz": ("frequency_min_hz", "min_frequency_hz"),
    "frequency_max_hz": ("frequency_max_hz", "max_frequency_hz"),
}


def _inferred_values(plan: DesignPlan) -> dict[str, tuple[Any, str]]:
    inferred: dict[str, tuple[Any, str]] = {}
    text = plan.requirement_summary
    frequency = _FREQUENCY_RANGE.search(text)
    if frequency:
        inferred["frequency_min_hz"] = (
            float(frequency.group("low")) * _scale(frequency.group("low_prefix")),
            "requirement",
        )
        inferred["frequency_max_hz"] = (
            float(frequency.group("high")) * _scale(frequency.group("high_prefix")),
            "requirement",
        )
    supply = _SUPPLY_VOLTAGE.search(text)
    if supply:
        value = float(supply.group(1) or supply.group(2))
        inferred["supply_voltage_v"] = (value, "requirement")
        inferred["dc_bus_voltage_v"] = (value, "requirement")
    load = _LOAD_RESISTANCE.search(text)
    if load:
        inferred["load_resistance_ohm"] = (
            float(load.group(1)) * _scale(load.group(2)),
            "requirement",
        )
    constraints = dict(plan.hard_constraints)
    for parameter_id, aliases in _CONSTRAINT_ALIASES.items():
        for alias in aliases:
            if alias in constraints:
                inferred[parameter_id] = (constraints[alias], "constraint")
                break
    return inferred


def _analysis_plan(domain: str) -> list[dict[str, str]]:
    names = {
        "waveform-generation": (
            ("transient", "验证启动、稳态频率、幅度和波形关系"),
            ("fft", "检查正弦失真和谐波"),
            ("tolerance", "扫描定时与整形元件容差"),
            ("load-sweep", "验证额定负载下的幅频保持能力"),
        ),
        "power-electronics": (
            ("operating-point", "核对静态工作点和器件应力"),
            ("line-load-transient", "验证输入与负载阶跃"),
            ("loop-stability", "检查反馈环路裕量"),
            ("tolerance-thermal", "覆盖器件容差和热角落"),
        ),
        "signal-conditioning": (
            ("operating-point", "核对偏置、共模和输出摆幅"),
            ("ac-sweep", "验证增益、截止频率和相位"),
            ("transient", "检查大信号响应、压摆率和恢复"),
            ("noise-tolerance", "评估噪声、容差和采样边界"),
        ),
        "robot-control": (
            ("plant-model", "建立电机、负载和传感器模型"),
            ("closed-loop-transient", "验证跟踪、超调和扰动恢复"),
            ("latency-saturation", "覆盖延迟、限幅和抗积分饱和"),
            ("hil-faults", "在 HIL 中检查传感器和功率级故障"),
        ),
        "general-circuit": (
            ("operating-point", "验证静态工作点"),
            ("transient", "验证时域功能和边界"),
            ("ac-sweep", "验证频域性能"),
            ("corners", "覆盖电源、负载、温度和容差角落"),
        ),
    }[domain]
    return [
        {"analysis_id": analysis_id, "purpose": purpose, "status": "planned"}
        for analysis_id, purpose in names
    ]


def _validation_gates() -> list[dict[str, Any]]:
    return [
        {"gate_id": "parameter-completeness", "label": "关键参数完整", "required": True},
        {"gate_id": "topology-integrity", "label": "拓扑和网络完整性", "required": True},
        {"gate_id": "component-ratings", "label": "器件额定值与降额", "required": True},
        {"gate_id": "model-provenance", "label": "模型来源与许可证", "required": True},
        {"gate_id": "analysis-coverage", "label": "分析覆盖关键指标", "required": True},
        {"gate_id": "human-netlist-approval", "label": "成图前人工确认网表", "required": True},
    ]


def prepare_design_specification(
    plan: Mapping[str, Any],
    parameter_values: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Prepare a bounded specification from an explicitly selected plan."""
    selected_plan = validate_selected_design_plan(plan)
    values = {} if parameter_values is None else parameter_values
    if not isinstance(values, Mapping):
        raise ValueError("parameter_values must be an object")
    if len(values) > MAX_SPECIFICATION_PARAMETERS:
        raise ValueError(
            f"parameter_values must contain at most {MAX_SPECIFICATION_PARAMETERS} items"
        )
    definitions = _domain_parameters(
        selected_plan.domain,
        selected_plan.selected_option_id or "",
    )
    known = {item.parameter_id for item in definitions}
    unknown = set(values) - known
    if unknown:
        raise ValueError(f"parameter_values contains unknown fields: {sorted(unknown)}")
    inferred = _inferred_values(selected_plan)
    parameters: list[dict[str, Any]] = []
    resolved_values: dict[str, Any] = {}
    for definition in definitions:
        if definition.parameter_id in values:
            raw_value, source, provided = values[definition.parameter_id], "user", True
        elif definition.parameter_id in inferred:
            raw_value, source = inferred[definition.parameter_id]
            provided = True
        else:
            raw_value, source, provided = None, None, False
        resolved = definition.resolve(raw_value, provided=provided, source=source)
        parameters.append(resolved)
        if provided:
            resolved_values[definition.parameter_id] = resolved["value"]
    missing = [
        item["parameter_id"]
        for item in parameters
        if item["required"] and item["status"] == "missing"
    ]
    selected_option = next(
        item
        for item in selected_plan.options
        if item.option_id == selected_plan.selected_option_id
    )
    selected_digest = str(plan["selected_plan_digest"])
    spec_digest = _canonical_digest(
        {
            "preparer_version": SPECIFICATION_PREPARER_VERSION,
            "selected_plan_digest": selected_digest,
            "parameter_values": resolved_values,
        }
    )
    return {
        "schema_version": DESIGN_SPECIFICATION_SCHEMA_VERSION,
        "preparer_version": SPECIFICATION_PREPARER_VERSION,
        "kind": "multisim-mcp-design-specification",
        "specification_id": f"spec-{spec_digest[:32]}",
        "specification_digest": spec_digest,
        "plan_id": selected_plan.plan_id,
        "selected_option_id": selected_plan.selected_option_id,
        "selected_plan_digest": selected_digest,
        "selection_digest": plan["selection_digest"],
        "title": f"{selected_option.title} · 电气规格草案",
        "domain": selected_plan.domain,
        "state": "ready" if not missing else "needs-input",
        "ready_for_netlist_draft": not missing,
        "modules": [
            {
                "module_id": f"module-{index:02d}",
                "name": name,
                "status": "planned",
            }
            for index, name in enumerate(selected_option.architecture, start=1)
        ],
        "parameter_requirements": parameters,
        "resolved_parameters": resolved_values,
        "missing_parameter_ids": missing,
        "analysis_plan": _analysis_plan(selected_plan.domain),
        "validation_gates": _validation_gates(),
        "approval": {
            "required_before_netlist": True,
            "specification_approved": False,
        },
        "next_step": (
            "review_specification_before_netlist"
            if not missing
            else "collect_missing_parameters"
        ),
        "execution_boundary": {
            "circuit_design_created": False,
            "netlist_generated": False,
            "schematic_generated": False,
            "simulation_started": False,
            "files_written": False,
        },
        "artifacts_generated": [],
    }


__all__ = [
    "DESIGN_SPECIFICATION_SCHEMA_VERSION",
    "MAX_SPECIFICATION_PARAMETERS",
    "ParameterDefinition",
    "prepare_design_specification",
]
