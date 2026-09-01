"""Read-only technical方案规划契约与确定性候选生成器。

The planner intentionally stops before any SPICE netlist, schematic, file write,
or simulation.  It gives an Agent or UI a small, comparable set of implementation
options.  A later, explicit selection step can turn one option into a
``CircuitDesign`` and hand it to the existing schematic/experiment services.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from .eda_core import (
    JsonValue,
    _freeze_mapping,
    _freeze_json,
    _require_identifier,
    _require_text,
    _thaw_json,
)


DESIGN_PLAN_SCHEMA_VERSION: Final = 1
PLANNER_VERSION: Final = "0.1.0"
MAX_PLAN_OPTIONS: Final = 4
MIN_PLAN_OPTIONS: Final = 2
MAX_PLAN_ASSUMPTIONS: Final = 16
MAX_PLAN_LIST_ITEMS: Final = 16
MAX_PLAN_OBJECT_KEYS: Final = 32
MAX_PLAN_JSON_DEPTH: Final = 6
MAX_REQUIREMENTS_LENGTH: Final = 16_384

_OPTION_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_PLAN_ID = re.compile(r"^plan-[0-9a-f]{32}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_KNOWN_OBJECTIVES: Final = frozenset(
    {
        "performance",
        "robustness",
        "cost",
        "power",
        "complexity",
        "implementation_speed",
        "latency",
        "safety",
    }
)

DEFAULT_OBJECTIVES: Final[dict[str, float]] = {
    "performance": 0.26,
    "robustness": 0.30,
    "cost": 0.12,
    "power": 0.08,
    "complexity": 0.14,
    "implementation_speed": 0.10,
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _selection_digest(source_plan_digest: str, plan_id: str, option_id: str) -> str:
    return _digest(
        {
            "source_plan_digest": source_plan_digest,
            "plan_id": plan_id,
            "option_id": option_id,
        }
    )


def _normalize_list(value: object, name: str, *, maximum: int = MAX_PLAN_LIST_ITEMS) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an array")
    if len(value) > maximum:
        raise ValueError(f"{name} must contain at most {maximum} items")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_require_text(item, f"{name}[{index}]", maximum=1024))
    return tuple(result)


def _validate_plan_json(value: object, name: str, *, depth: int = 0) -> None:
    """Bound planner metadata before recursively freezing it."""
    if depth > MAX_PLAN_JSON_DEPTH:
        raise ValueError(f"{name} exceeds the maximum nesting depth")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} must contain only finite numbers")
        return
    if isinstance(value, str):
        if len(value) > 2048:
            raise ValueError(f"{name} contains a string longer than 2048 characters")
        return
    if isinstance(value, Mapping):
        if len(value) > MAX_PLAN_OBJECT_KEYS:
            raise ValueError(f"{name} must contain at most {MAX_PLAN_OBJECT_KEYS} keys")
        for key, item in value.items():
            if not isinstance(key, str) or not key or "\x00" in key:
                raise ValueError(f"{name} keys must be non-empty strings")
            _validate_plan_json(item, f"{name}.{key}", depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_PLAN_LIST_ITEMS:
            raise ValueError(f"{name} must contain at most {MAX_PLAN_LIST_ITEMS} items")
        for index, item in enumerate(value):
            _validate_plan_json(item, f"{name}[{index}]", depth=depth + 1)
        return
    raise ValueError(f"{name} is not JSON-compatible")


def _normalize_object(value: object, name: str) -> dict[str, JsonValue]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    _validate_plan_json(value, name)
    frozen = _freeze_json(dict(value), name)
    assert isinstance(frozen, Mapping)
    return dict(_thaw_json(frozen))


def _normalize_objectives(value: object) -> dict[str, float]:
    if value is None:
        value = DEFAULT_OBJECTIVES
    if not isinstance(value, Mapping):
        raise ValueError("objectives must be an object")
    if not value:
        raise ValueError("objectives must not be empty")
    weights: dict[str, float] = {}
    for raw_key, raw_weight in value.items():
        if not isinstance(raw_key, str) or raw_key not in _KNOWN_OBJECTIVES:
            choices = ", ".join(sorted(_KNOWN_OBJECTIVES))
            raise ValueError(f"objectives key must be one of: {choices}")
        if isinstance(raw_weight, bool):
            raise ValueError(f"objectives.{raw_key} must be a number")
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"objectives.{raw_key} must be a number") from exc
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"objectives.{raw_key} must be finite and non-negative")
        weights[raw_key] = weight
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("objectives must contain a positive weight")
    return {key: round(value / total, 8) for key, value in sorted(weights.items())}


def _domain_for(requirements: str, context: Mapping[str, JsonValue]) -> str:
    haystack = requirements.casefold() + " " + json.dumps(
        context, ensure_ascii=False, sort_keys=True
    ).casefold()
    if any(
        token in haystack
        for token in (
            "电机",
            "云台",
            "底盘",
            "机器人",
            "控制环",
            "pid",
            "mpc",
            "motor",
            "gimbal",
            "chassis",
            "encoder",
            "编码器",
            "imu",
        )
    ):
        return "robot-control"
    # Strong function words win over generic supply/context words.  A waveform
    # generator normally mentions its power rail, but that must not turn the
    # whole request into a power-converter plan.
    if any(
        token in haystack
        for token in (
            "多波形",
            "方波",
            "三角波",
            "正弦波",
            "振荡器",
            "555",
            "waveform generator",
            "square wave",
            "triangle wave",
            "sine wave",
        )
    ):
        return "waveform-generation"
    if any(
        token in haystack
        for token in (
            "电源",
            "稳压",
            "电池",
            "buck",
            "boost",
            "dc-dc",
            "mosfet",
            "过流",
            "欠压",
            "power",
            "regulator",
        )
    ):
        return "power-electronics"
    if any(
        token in haystack
        for token in (
            "传感",
            "滤波",
            "放大",
            "运放",
            "adc",
            "sensor",
            "filter",
            "signal",
        )
    ):
        return "signal-conditioning"
    if any(
        token in haystack
        for token in (
            "波形",
            "方波",
            "三角",
            "正弦",
            "555",
            "waveform",
            "square",
            "triangle",
            "sine",
        )
    ):
        return "waveform-generation"
    return "general-circuit"


_DOMAIN_TITLES: Final[dict[str, str]] = {
    "robot-control": "机器人控制技术方案",
    "power-electronics": "电源与功率级技术方案",
    "signal-conditioning": "传感器信号链技术方案",
    "waveform-generation": "波形产生技术方案",
    "general-circuit": "电路实现技术方案",
}

_DOMAIN_ASSUMPTIONS: Final[dict[str, tuple[str, ...]]] = {
    "robot-control": (
        "电机、负载和传感器参数需要由用户提供或通过台架测量。",
        "控制器候选先做仿真和 HIL，再决定是否进入实机测试。",
    ),
    "power-electronics": (
        "输入电压、最大负载、电流峰值和热环境尚未由仿真验证。",
        "器件额定值和保护阈值需要结合实际 BOM 复核。",
    ),
    "signal-conditioning": (
        "传感器输出范围、噪声、采样频率和输入阻抗需要明确。",
        "信号质量结论必须通过噪声和容差仿真确认。",
    ),
    "waveform-generation": (
        "频率、幅度、负载和供电条件以用户需求为准。",
        "方案阶段只比较实现路径，不声明任何实测通过。",
    ),
    "general-circuit": (
        "需求中的未明确电气参数需要在生成电路前补齐。",
        "候选评分是规划启发式，不替代电气仿真。",
    ),
}


def _option(
    option_id: str,
    title: str,
    summary: str,
    architecture: Sequence[str],
    implementation_path: Sequence[str],
    advantages: Sequence[str],
    tradeoffs: Sequence[str],
    risks: Sequence[str],
    metrics: Mapping[str, JsonValue],
    profile: Mapping[str, float],
) -> dict[str, Any]:
    return {
        "option_id": option_id,
        "title": title,
        "summary": summary,
        "architecture": list(architecture),
        "implementation_path": list(implementation_path),
        "advantages": list(advantages),
        "tradeoffs": list(tradeoffs),
        "risks": list(risks),
        "estimated_metrics": dict(metrics),
        "profile": dict(profile),
    }


def _catalog(domain: str) -> list[dict[str, Any]]:
    if domain == "robot-control":
        return [
            _option(
                "control-pid-feedforward",
                "级联 PID + 前馈",
                "以成熟的反馈控制为主，加入可解释的速度或负载前馈。",
                ("电机与负载模型", "级联 PID", "前馈补偿", "编码器反馈"),
                ("建立负载模型", "在控制器中实现", "软件在环", "HIL 与台架回归"),
                ("实现路径短", "参数可解释", "便于 MCU 部署"),
                ("需要人工调参", "复杂约束处理能力有限"),
                ("负载模型不准确会造成前馈偏差", "采样延迟需要实测"),
                {"performance": "中高", "robustness": "高", "latency": "低", "complexity": "中"},
                {"performance": 82, "robustness": 88, "cost": 92, "power": 90, "complexity": 86, "implementation_speed": 94, "latency": 92, "safety": 90},
            ),
            _option(
                "control-robust-pid",
                "带抗饱和和增益调度的鲁棒 PID",
                "在级联控制基础上处理饱和、工况变化和传感器噪声。",
                ("电机与负载模型", "级联 PID", "抗积分饱和", "滤波与增益调度"),
                ("建立多工况模型", "定义保护和限幅", "软件在环", "HIL 与覆盖工况台架"),
                ("鲁棒性更好", "仍可解释和实时部署", "适合比赛现场调参"),
                ("参数数量增加", "需要更多工况数据"),
                ("调度边界不合理会产生切换突变", "传感器异常需要故障策略"),
                {"performance": "高", "robustness": "很高", "latency": "低", "complexity": "中高"},
                {"performance": 90, "robustness": 94, "cost": 84, "power": 87, "complexity": 76, "implementation_speed": 82, "latency": 90, "safety": 94},
            ),
            _option(
                "control-mpc",
                "带约束的 MPC",
                "显式处理电流、速度、位置和热约束，适合研究型高性能控制。",
                ("状态空间模型", "状态估计", "滚动优化", "约束管理"),
                ("辨识模型", "离线验证求解器", "软件在环", "实时目标机 HIL"),
                ("多变量约束处理强", "可统一表达性能目标"),
                ("计算量和实现复杂度高", "对模型和算力敏感"),
                ("模型失配会降低收益", "实时超时或数值问题需要降级控制"),
                {"performance": "很高", "robustness": "中高", "latency": "中", "complexity": "高"},
                {"performance": 97, "robustness": 78, "cost": 56, "power": 62, "complexity": 48, "implementation_speed": 45, "latency": 65, "safety": 78},
            ),
        ]
    if domain == "power-electronics":
        return [
            _option(
                "power-linear",
                "线性稳压",
                "用线性器件提供低噪声、低复杂度的电源轨。",
                ("输入保护", "线性调整器", "去耦与滤波"),
                ("确定输入输出范围", "选择额定器件", "瞬态和热仿真", "样机测量"),
                ("噪声低", "电路简单", "易于调试"),
                ("效率受压差影响", "大电流下温升明显"),
                ("散热不足会触发保护", "输入范围变化会影响余量"),
                {"performance": "中", "robustness": "高", "power": "低效率", "complexity": "低"},
                {"performance": 70, "robustness": 88, "cost": 88, "power": 42, "complexity": 92, "implementation_speed": 94, "safety": 88},
            ),
            _option(
                "power-buck",
                "同步 Buck 开关电源",
                "以开关变换提高效率，适合较大功率和宽输入范围。",
                ("输入保护", "同步开关级", "电感电容滤波", "反馈补偿"),
                ("确定开关频率", "选择功率器件", "环路与热仿真", "负载阶跃测试"),
                ("效率高", "功率密度高", "输入范围适应性好"),
                ("EMI 和补偿更复杂", "布局对性能敏感"),
                ("开关尖峰和热应力需要实测", "控制器启动行为需要验证"),
                {"performance": "高", "robustness": "中高", "power": "高效率", "complexity": "高"},
                {"performance": 91, "robustness": 82, "cost": 72, "power": 94, "complexity": 62, "implementation_speed": 66, "safety": 80},
            ),
            _option(
                "power-hybrid",
                "开关预稳压 + 线性后级",
                "用开关级降低压差，再用线性后级改善噪声和负载品质。",
                ("输入保护", "Buck 预稳压", "线性后级", "多级去耦"),
                ("分配功率预算", "确定两级余量", "瞬态、噪声和热联合仿真", "样机回归"),
                ("兼顾效率和噪声", "对敏感模拟负载友好"),
                ("器件和调试工作量增加", "两级环路需要配合"),
                ("级间交互和热分布需要验证", "成本通常高于单级方案"),
                {"performance": "高", "robustness": "高", "power": "中高效率", "complexity": "高"},
                {"performance": 91, "robustness": 91, "cost": 66, "power": 88, "complexity": 60, "implementation_speed": 58, "safety": 88},
            ),
        ]
    if domain == "signal-conditioning":
        return [
            _option(
                "signal-passive",
                "被动 RC 调理",
                "以最少元件完成限带、去噪或偏置。",
                ("输入保护", "RC 滤波", "偏置与采样"),
                ("确认源阻抗", "选择截止频率", "AC/噪声仿真", "实测校准"),
                ("成本低", "延迟小", "失效模式简单"),
                ("驱动能力和滤波斜率有限", "负载会影响截止频率"),
                ("输入过压和器件容差需要复核", "高阻节点易受干扰"),
                {"performance": "中", "robustness": "高", "power": "低", "complexity": "低"},
                {"performance": 72, "robustness": 86, "cost": 94, "power": 96, "complexity": 94, "implementation_speed": 96, "safety": 82},
            ),
            _option(
                "signal-active",
                "有源运放滤波与放大",
                "用运放同时完成增益、偏置和更陡的频率选择。",
                ("输入保护", "有源滤波器", "增益与偏置", "ADC 驱动"),
                ("定义信号范围", "选择运放和拓扑", "AC/瞬态/噪声仿真", "ADC 实测"),
                ("增益和截止频率可控", "更适合 ADC 前端", "便于补偿传感器范围"),
                ("需要电源和稳定性设计", "运放带宽与摆幅有限"),
                ("输入共模和输出摆幅可能成为瓶颈", "电源噪声会进入信号链"),
                {"performance": "高", "robustness": "高", "power": "中", "complexity": "中"},
                {"performance": 90, "robustness": 88, "cost": 78, "power": 72, "complexity": 78, "implementation_speed": 82, "safety": 86},
            ),
            _option(
                "signal-digital",
                "ADC + 数字滤波",
                "把可调滤波和校准放到固件，保留模拟前端保护和抗混叠。",
                ("输入保护", "抗混叠 RC", "ADC", "数字滤波与校准"),
                ("确定采样率", "定义量化和延迟预算", "软件在环", "固件与台架回归"),
                ("参数可更新", "可做校准和复杂算法", "便于记录数据"),
                ("增加采样延迟", "依赖 MCU 和固件质量"),
                ("混叠、丢样和时序抖动需要实测", "模拟保护不能省略"),
                {"performance": "可调", "robustness": "中高", "power": "中", "complexity": "中高"},
                {"performance": 88, "robustness": 80, "cost": 70, "power": 68, "complexity": 66, "implementation_speed": 64, "latency": 70, "safety": 78},
            ),
        ]
    if domain == "waveform-generation":
        return [
            _option(
                "waveform-analog-555",
                "555 模拟振荡器",
                "用经典定时器和 RC 网络产生可调基础波形。",
                ("555 振荡器", "RC 定时网络", "幅度整形", "输出缓冲"),
                ("确定频率范围", "选择定时元件", "瞬态与容差仿真", "实测校准"),
                ("器件直观", "无需固件", "适合教学和快速验证"),
                ("频率和幅度耦合", "高精度和多波形扩展有限"),
                ("器件容差和负载会影响输出", "高频性能需要本机验证"),
                {"performance": "中", "robustness": "高", "power": "中", "complexity": "低"},
                {"performance": 72, "robustness": 86, "cost": 94, "power": 76, "complexity": 90, "implementation_speed": 92, "safety": 86},
            ),
            _option(
                "waveform-mcu-dds",
                "MCU 定时器或 DDS",
                "用数字时基和查表/定时器产生可编程波形。",
                ("数字时基", "查表或 PWM", "DAC/滤波", "输出缓冲"),
                ("定义时钟和分辨率", "实现波形任务", "软件在环", "示波器与负载测试"),
                ("频率可编程", "多波形扩展容易", "可记录和联动控制"),
                ("依赖固件和时钟", "会引入量化与更新纹波"),
                ("时序抖动、滤波和 DAC 误差需要测量", "软件故障需要降级策略"),
                {"performance": "高", "robustness": "中高", "power": "中", "complexity": "中"},
                {"performance": 92, "robustness": 82, "cost": 74, "power": 72, "complexity": 72, "implementation_speed": 72, "latency": 78, "safety": 80},
            ),
            _option(
                "waveform-opamp",
                "运放积分与整形",
                "用模拟积分、比较和整形级联得到连续可调波形。",
                ("比较器/方波", "积分器/三角波", "正弦整形", "输出缓冲"),
                ("确定幅频关系", "选择运放和偏置", "AC/瞬态仿真", "失真和负载实测"),
                ("波形链路直观", "可在模拟域完成", "便于观察各级信号"),
                ("级间误差累积", "运放带宽和摆幅限制明显"),
                ("积分器漂移和失真需要控制", "电源与负载会影响幅度"),
                {"performance": "中高", "robustness": "中高", "power": "中", "complexity": "中高"},
                {"performance": 84, "robustness": 80, "cost": 78, "power": 70, "complexity": 68, "implementation_speed": 70, "safety": 82},
            ),
        ]
    return [
        _option(
            "general-minimal",
            "最小可行实现",
            "使用最少器件完成明确功能，优先验证核心电气关系。",
            ("核心功能级", "必要保护", "输出接口"),
            ("补齐参数", "生成原理图", "基础仿真", "小范围实测"),
            ("成本和复杂度低", "验证速度快"),
            ("扩展性和裕量有限"),
            ("需求变化时可能需要重新设计", "保护与边界条件需补齐"),
            {"performance": "中", "robustness": "中", "power": "低", "complexity": "低"},
            {"performance": 72, "robustness": 72, "cost": 95, "power": 90, "complexity": 94, "implementation_speed": 96, "safety": 72},
        ),
        _option(
            "general-robust",
            "保护和裕量优先",
            "在核心功能外加入保护、监测和容差预算，优先保证可复现性。",
            ("输入保护", "核心功能级", "监测与保护", "输出接口"),
            ("定义故障边界", "建立容差预算", "全角落仿真", "台架回归"),
            ("鲁棒性更好", "便于故障诊断", "适合长期运行"),
            ("器件数和成本增加", "需要更多验证时间"),
            ("保护阈值与真实负载需匹配", "器件来源要固定"),
            {"performance": "中高", "robustness": "高", "power": "中", "complexity": "中"},
            {"performance": 84, "robustness": 94, "cost": 76, "power": 78, "complexity": 76, "implementation_speed": 78, "safety": 94},
        ),
        _option(
            "general-performance",
            "性能优先实现",
            "把带宽、响应和精度作为优先目标，允许更高器件和调试复杂度。",
            ("高性能核心级", "有源补偿", "监测与保护", "输出接口"),
            ("量化性能目标", "选择高性能器件", "边界仿真", "高密度实测"),
            ("性能上限高", "可扩展到复杂功能"),
            ("成本、功耗和调试难度增加", "对模型准确性敏感"),
            ("稳定性、热和 EMI 需要额外验证", "不适合参数缺失的需求"),
            {"performance": "高", "robustness": "中高", "power": "中高", "complexity": "高"},
            {"performance": 96, "robustness": 80, "cost": 58, "power": 62, "complexity": 56, "implementation_speed": 54, "safety": 80},
        ),
    ]


def _score(profile: Mapping[str, float], objectives: Mapping[str, float]) -> tuple[float, dict[str, float]]:
    breakdown: dict[str, float] = {}
    for key, weight in objectives.items():
        value = float(profile.get(key, 70.0))
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError(f"planner profile {key} must be between 0 and 100")
        breakdown[key] = round(value * weight, 2)
    return round(sum(breakdown.values()), 2), breakdown


@dataclass(frozen=True, slots=True)
class DesignOption:
    """One implementation path that has not yet produced a circuit."""

    option_id: str
    title: str
    summary: str
    architecture: tuple[str, ...]
    implementation_path: tuple[str, ...]
    advantages: tuple[str, ...]
    tradeoffs: tuple[str, ...]
    risks: tuple[str, ...]
    estimated_metrics: Mapping[str, JsonValue]
    score: float
    score_breakdown: Mapping[str, float]
    evidence_status: str = "planning-only"
    recommendation_reason: str = ""

    def __post_init__(self) -> None:
        if not _OPTION_ID.fullmatch(self.option_id):
            raise ValueError("DesignOption.option_id is invalid")
        for name in ("title", "summary"):
            object.__setattr__(self, name, _require_text(getattr(self, name), f"option.{name}"))
        for name in ("architecture", "implementation_path", "advantages", "tradeoffs", "risks"):
            object.__setattr__(
                self,
                name,
                _normalize_list(getattr(self, name), f"option.{name}"),
            )
        if not math.isfinite(self.score) or not 0 <= self.score <= 100:
            raise ValueError("DesignOption.score must be between 0 and 100")
        if self.evidence_status not in {"planning-only", "unverified", "measured"}:
            raise ValueError("DesignOption.evidence_status is invalid")
        object.__setattr__(self, "estimated_metrics", _freeze_mapping(self.estimated_metrics, "option.estimated_metrics"))
        raw_breakdown = dict(self.score_breakdown)
        normalized_breakdown: dict[str, float] = {}
        for key, value in raw_breakdown.items():
            if not isinstance(key, str):
                raise ValueError("option.score_breakdown keys must be strings")
            numeric = float(value)
            if not math.isfinite(numeric) or numeric < 0:
                raise ValueError("option.score_breakdown values must be finite and non-negative")
            normalized_breakdown[key] = round(numeric, 2)
        object.__setattr__(self, "score_breakdown", _freeze_mapping(normalized_breakdown, "option.score_breakdown"))
        object.__setattr__(self, "recommendation_reason", _require_text(self.recommendation_reason, "option.recommendation_reason"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "title": self.title,
            "summary": self.summary,
            "architecture": list(self.architecture),
            "implementation_path": list(self.implementation_path),
            "advantages": list(self.advantages),
            "tradeoffs": list(self.tradeoffs),
            "risks": list(self.risks),
            "estimated_metrics": _thaw_json(self.estimated_metrics),
            "score": self.score,
            "score_breakdown": _thaw_json(self.score_breakdown),
            "evidence_status": self.evidence_status,
            "recommendation_reason": self.recommendation_reason,
        }

    @classmethod
    def from_dict(cls, value: object) -> "DesignOption":
        if not isinstance(value, Mapping):
            raise ValueError("DesignOption must be an object")
        allowed = {
            "option_id", "title", "summary", "architecture", "implementation_path",
            "advantages", "tradeoffs", "risks", "estimated_metrics", "score",
            "score_breakdown", "evidence_status", "recommendation_reason",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"DesignOption contains unknown fields: {sorted(unknown)}")
        return cls(
            option_id=value.get("option_id", ""),
            title=value.get("title", ""),
            summary=value.get("summary", ""),
            architecture=tuple(value.get("architecture", [])),
            implementation_path=tuple(value.get("implementation_path", [])),
            advantages=tuple(value.get("advantages", [])),
            tradeoffs=tuple(value.get("tradeoffs", [])),
            risks=tuple(value.get("risks", [])),
            estimated_metrics=_normalize_object(value.get("estimated_metrics", {}), "option.estimated_metrics"),
            score=value.get("score", 0),
            score_breakdown=_normalize_object(value.get("score_breakdown", {}), "option.score_breakdown"),
            evidence_status=value.get("evidence_status", "planning-only"),
            recommendation_reason=value.get("recommendation_reason", "planning heuristic"),
        )


@dataclass(frozen=True, slots=True)
class DesignPlan:
    """A bounded, selectable plan with no generated circuit artifacts."""

    plan_id: str
    title: str
    requirement_summary: str
    domain: str
    assumptions: tuple[str, ...]
    hard_constraints: Mapping[str, JsonValue]
    objectives: Mapping[str, float]
    options: tuple[DesignOption, ...]
    recommended_option_id: str
    state: str = "proposed"
    selected_option_id: str | None = None
    planner_version: str = PLANNER_VERSION
    schema_version: int = DESIGN_PLAN_SCHEMA_VERSION
    selection_policy: str = "declared-constraints-retained-then-weighted-heuristic"

    def __post_init__(self) -> None:
        if self.schema_version != DESIGN_PLAN_SCHEMA_VERSION:
            raise ValueError(f"DesignPlan schema_version must be {DESIGN_PLAN_SCHEMA_VERSION}")
        if not _PLAN_ID.fullmatch(self.plan_id):
            raise ValueError("DesignPlan.plan_id is invalid")
        object.__setattr__(self, "title", _require_text(self.title, "plan.title"))
        object.__setattr__(self, "requirement_summary", _require_text(self.requirement_summary, "plan.requirement_summary", maximum=MAX_REQUIREMENTS_LENGTH))
        object.__setattr__(self, "domain", _require_identifier(self.domain, "plan.domain"))
        assumptions = _normalize_list(self.assumptions, "plan.assumptions", maximum=MAX_PLAN_ASSUMPTIONS)
        if not assumptions:
            raise ValueError("plan.assumptions must not be empty")
        object.__setattr__(self, "assumptions", assumptions)
        object.__setattr__(self, "hard_constraints", _freeze_mapping(self.hard_constraints, "plan.hard_constraints"))
        object.__setattr__(self, "objectives", _freeze_mapping(_normalize_objectives(self.objectives), "plan.objectives"))
        options = tuple(self.options)
        if not MIN_PLAN_OPTIONS <= len(options) <= MAX_PLAN_OPTIONS:
            raise ValueError(f"plan.options must contain between {MIN_PLAN_OPTIONS} and {MAX_PLAN_OPTIONS} items")
        if any(not isinstance(item, DesignOption) for item in options):
            raise ValueError("plan.options must contain DesignOption")
        ids = [item.option_id for item in options]
        if len({item.casefold() for item in ids}) != len(ids):
            raise ValueError("plan.options contains duplicate option_id")
        if self.recommended_option_id not in ids:
            raise ValueError("plan.recommended_option_id must match an option")
        if self.selected_option_id is not None and self.selected_option_id not in ids:
            raise ValueError("plan.selected_option_id must match an option")
        if self.state not in {"proposed", "selected"}:
            raise ValueError("plan.state is invalid")
        if self.state == "selected" and self.selected_option_id is None:
            raise ValueError("selected plan must contain selected_option_id")
        object.__setattr__(self, "options", options)
        object.__setattr__(self, "planner_version", _require_text(self.planner_version, "plan.planner_version", maximum=32))
        object.__setattr__(self, "selection_policy", _require_text(self.selection_policy, "plan.selection_policy", maximum=128))

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def select(self, option_id: str) -> "DesignPlan":
        if option_id not in {item.option_id for item in self.options}:
            raise ValueError(f"unknown plan option: {option_id}")
        return DesignPlan(
            plan_id=self.plan_id,
            title=self.title,
            requirement_summary=self.requirement_summary,
            domain=self.domain,
            assumptions=self.assumptions,
            hard_constraints=_thaw_json(self.hard_constraints),
            objectives=_thaw_json(self.objectives),
            options=self.options,
            recommended_option_id=self.recommended_option_id,
            state="selected",
            selected_option_id=option_id,
            planner_version=self.planner_version,
            schema_version=self.schema_version,
            selection_policy=self.selection_policy,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "planner_version": self.planner_version,
            "plan_id": self.plan_id,
            "title": self.title,
            "requirement_summary": self.requirement_summary,
            "domain": self.domain,
            "assumptions": list(self.assumptions),
            "hard_constraints": _thaw_json(self.hard_constraints),
            "objectives": _thaw_json(self.objectives),
            "selection_policy": self.selection_policy,
            "options": [item.to_dict() for item in self.options],
            "recommended_option_id": self.recommended_option_id,
            "state": self.state,
            "selected_option_id": self.selected_option_id,
            "execution_boundary": {
                "schematic_generated": False,
                "simulation_started": False,
                "files_written": False,
            },
        }

    @classmethod
    def from_dict(cls, value: object) -> "DesignPlan":
        if not isinstance(value, Mapping):
            raise ValueError("DesignPlan must be an object")
        allowed = {
            "schema_version", "planner_version", "plan_id", "title", "requirement_summary",
            "domain", "assumptions", "hard_constraints", "objectives", "selection_policy",
            "options", "recommended_option_id", "state", "selected_option_id", "execution_boundary",
            "request_digest", "plan_digest", "next_step", "artifacts_generated",
            "source_plan_digest", "selected_plan_digest", "selection_digest",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"DesignPlan contains unknown fields: {sorted(unknown)}")
        boundary = value.get("execution_boundary", {})
        if not isinstance(boundary, Mapping):
            raise ValueError("DesignPlan.execution_boundary must be an object")
        if boundary != {"schematic_generated": False, "simulation_started": False, "files_written": False}:
            raise ValueError("DesignPlan.execution_boundary must remain planning-only")
        raw_options = value.get("options", [])
        if not isinstance(raw_options, (list, tuple)):
            raise ValueError("DesignPlan.options must be an array")
        plan = cls(
            schema_version=value.get("schema_version", 0),
            planner_version=value.get("planner_version", PLANNER_VERSION),
            plan_id=value.get("plan_id", ""),
            title=value.get("title", ""),
            requirement_summary=value.get("requirement_summary", ""),
            domain=value.get("domain", ""),
            assumptions=tuple(value.get("assumptions", [])),
            hard_constraints=_normalize_object(value.get("hard_constraints", {}), "plan.hard_constraints"),
            objectives=_normalize_object(value.get("objectives", {}), "plan.objectives"),
            selection_policy=value.get("selection_policy", ""),
            options=tuple(DesignOption.from_dict(item) for item in raw_options),
            recommended_option_id=value.get("recommended_option_id", ""),
            state=value.get("state", "proposed"),
            selected_option_id=value.get("selected_option_id"),
        )
        if "request_digest" in value and value["request_digest"] != plan.plan_id.removeprefix("plan-"):
            raise ValueError("DesignPlan.request_digest does not match plan_id")
        if "plan_digest" in value and value["plan_digest"] != plan.digest:
            raise ValueError("DesignPlan.plan_digest does not match plan")
        for field_name in ("source_plan_digest", "selected_plan_digest", "selection_digest"):
            if field_name in value and (
                not isinstance(value[field_name], str) or not _DIGEST.fullmatch(value[field_name])
            ):
                raise ValueError(f"DesignPlan.{field_name} must be a SHA-256 digest")
        if "selected_plan_digest" in value and value["selected_plan_digest"] != plan.digest:
            raise ValueError("DesignPlan.selected_plan_digest does not match plan")
        if "next_step" in value and value["next_step"] not in {
            "select_option_before_schematic",
            "prepare_netlist_after_confirmation",
        }:
            raise ValueError("DesignPlan.next_step is invalid")
        if "artifacts_generated" in value and value["artifacts_generated"] != []:
            raise ValueError("DesignPlan.artifacts_generated must remain empty")
        return plan


def build_design_plan(
    requirements: str,
    *,
    constraints: Mapping[str, Any] | None = None,
    objectives: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    max_options: int = 3,
) -> DesignPlan:
    """Build a stable, bounded plan without generating or executing a circuit."""
    normalized_requirements = _require_text(requirements, "requirements", maximum=MAX_REQUIREMENTS_LENGTH)
    normalized_constraints = _normalize_object(constraints, "constraints")
    normalized_context = _normalize_object(context, "context")
    normalized_objectives = _normalize_objectives(objectives)
    if isinstance(max_options, bool) or not isinstance(max_options, int):
        raise ValueError("max_options must be an integer")
    if not MIN_PLAN_OPTIONS <= max_options <= MAX_PLAN_OPTIONS:
        raise ValueError(f"max_options must be between {MIN_PLAN_OPTIONS} and {MAX_PLAN_OPTIONS}")
    domain = _domain_for(normalized_requirements, normalized_context)
    catalog = _catalog(domain)[:max_options]
    scored: list[tuple[dict[str, Any], float, dict[str, float]]] = []
    for item in catalog:
        score, breakdown = _score(item.pop("profile"), normalized_objectives)
        scored.append((item, score, breakdown))
    highest = max(score for _, score, _ in scored)
    recommended = next(item["option_id"] for item, score, _ in scored if score == highest)
    options: list[DesignOption] = []
    for item, score, breakdown in scored:
        options.append(
            DesignOption(
                option_id=item["option_id"],
                title=item["title"],
                summary=item["summary"],
                architecture=tuple(item["architecture"]),
                implementation_path=tuple(item["implementation_path"]),
                advantages=tuple(item["advantages"]),
                tradeoffs=tuple(item["tradeoffs"]),
                risks=tuple(item["risks"]),
                estimated_metrics=item["estimated_metrics"],
                score=score,
                score_breakdown=breakdown,
                recommendation_reason=(
                    "默认推荐：声明的约束已保留但尚未执行器件/电气校验，先按加权启发式综合分排序；"
                    "该分数不是仿真或实机结论。"
                    if item["option_id"] == recommended
                    else "备选路径：具备不同的性能、成本、复杂度或鲁棒性取舍，需由使用者选择。"
                ),
            )
        )
    request_payload = {
        "planner_version": PLANNER_VERSION,
        "requirements": normalized_requirements,
        "constraints": normalized_constraints,
        "objectives": normalized_objectives,
        "context": normalized_context,
        "domain": domain,
        "option_ids": [item.option_id for item in options],
    }
    request_digest = _digest(request_payload)
    plan = DesignPlan(
        plan_id=f"plan-{request_digest[:32]}",
        title=_DOMAIN_TITLES[domain],
        requirement_summary=normalized_requirements,
        domain=domain,
        assumptions=_DOMAIN_ASSUMPTIONS[domain],
        hard_constraints=normalized_constraints,
        objectives=normalized_objectives,
        options=tuple(options),
        recommended_option_id=recommended,
    )
    return plan


def plan_design_options(
    requirements: str,
    *,
    constraints: Mapping[str, Any] | None = None,
    objectives: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    max_options: int = 3,
) -> dict[str, Any]:
    """Return a transport-friendly planning envelope for MCP and the UI."""
    plan = build_design_plan(
        requirements,
        constraints=constraints,
        objectives=objectives,
        context=context,
        max_options=max_options,
    )
    return {
        **plan.to_dict(),
        "request_digest": plan.plan_id.removeprefix("plan-"),
        "plan_digest": plan.digest,
        "next_step": "select_option_before_schematic",
        "artifacts_generated": [],
    }


def select_design_option(plan: Mapping[str, Any], option_id: str) -> dict[str, Any]:
    """Lock one planning option without generating or executing a circuit."""
    if not isinstance(plan, Mapping):
        raise ValueError("plan must be an object")
    if not isinstance(option_id, str) or not option_id:
        raise ValueError("option_id must be a non-empty string")
    source = DesignPlan.from_dict(plan)
    if source.state == "selected" and source.selected_option_id != option_id:
        raise ValueError("plan already has a different selected option")
    selected = source if source.state == "selected" else source.select(option_id)
    if source.state == "selected":
        source_digest = plan.get("source_plan_digest")
        selection_digest = plan.get("selection_digest")
        if not isinstance(source_digest, str) or not _DIGEST.fullmatch(source_digest):
            raise ValueError("selected plan requires source_plan_digest")
        expected_selection_digest = _selection_digest(
            source_digest,
            selected.plan_id,
            option_id,
        )
        if selection_digest != expected_selection_digest:
            raise ValueError("DesignPlan.selection_digest does not match selection")
    else:
        source_digest = source.digest
        selection_digest = _selection_digest(
            source_digest,
            selected.plan_id,
            option_id,
        )
    selected_digest = selected.digest
    return {
        **selected.to_dict(),
        "request_digest": selected.plan_id.removeprefix("plan-"),
        "source_plan_digest": source_digest,
        "plan_digest": selected_digest,
        "selected_plan_digest": selected_digest,
        "selection_digest": selection_digest,
        "next_step": "prepare_netlist_after_confirmation",
        "artifacts_generated": [],
    }


def validate_selected_design_plan(plan: Mapping[str, Any]) -> DesignPlan:
    """Validate a complete selection envelope before downstream preparation."""
    if not isinstance(plan, Mapping):
        raise ValueError("plan must be an object")
    parsed = DesignPlan.from_dict(plan)
    if parsed.state != "selected" or parsed.selected_option_id is None:
        raise ValueError("plan must be selected before preparing a design specification")
    # Reselecting the same option performs the complete source/selection digest
    # verification while preserving the original selection chain.
    select_design_option(plan, parsed.selected_option_id)
    if plan.get("next_step") != "prepare_netlist_after_confirmation":
        raise ValueError("selected plan next_step is invalid")
    return parsed


__all__ = [
    "DESIGN_PLAN_SCHEMA_VERSION",
    "DEFAULT_OBJECTIVES",
    "DesignOption",
    "DesignPlan",
    "build_design_plan",
    "plan_design_options",
    "select_design_option",
    "validate_selected_design_plan",
]
