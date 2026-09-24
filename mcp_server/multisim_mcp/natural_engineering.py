"""Bounded Chinese/English RC requirements; a pure, model-independent contract."""
from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Any

from .engineering_planner import build_engineering_plan
from .preferred_values import format_spice_scalar, generate_preferred_values, parse_spice_scalar

_NUMBER = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-zΩωµμ]*)"
_SCALES = {"k": 1e3, "K": 1e3, "M": 1e6, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "n": 1e-9, "p": 1e-12}


def _quantity(text: str, label: str, unit: str) -> float | None:
    matches = list(re.finditer(label + r"\s*(?:[:=：]|为|是|取)?\s*" + _NUMBER, text, re.I))
    if len(matches) > 1:
        raise ValueError(f"请只提供一个 {unit} 数值，多个条件需要进一步澄清")
    if not matches:
        if re.search(label + r"\s*[:=：]", text, re.I):
            raise ValueError(f"无法解析显式指定的 {unit} 数值")
        return None
    match = matches[0]
    value, suffix = float(match.group(1)), match.group(2)
    allowed = {"ohm": {"", "ohm", "ω"}, "f": {"", "f"}, "hz": {"", "hz"}, "v": {"", "v"}}[unit]
    if suffix.casefold() not in allowed and suffix[:1] in _SCALES:
        value *= _SCALES[suffix[0]]
        suffix = suffix[1:]
    if suffix.casefold() not in allowed or not math.isfinite(value) or value <= 0:
        raise ValueError(f"无效的 {unit} 数值或单位：{match.group(0)}")
    return value


def _fmt(value: float) -> str:
    return format_spice_scalar(Decimal(format(value, '.12g')))


def parse_natural_request(text: str) -> dict[str, Any]:
    """Plan ideal unloaded RC low-pass requests; explicitly expose assumptions.

    This rule parser is not an AI model. It supports named R/C, cutoff, input
    amplitude and automatic E24 selection. Execution always uses native evidence.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError("请输入 1–4000 字的工程需求")
    explicit_rc = re.search(r"(?<![A-Za-z])RC(?![A-Za-z])|低通|low[ -]?pass", text, re.I)
    implicit_rc = re.search(r"(?:电容|capacitance|(?<![A-Za-z])C1?)", text, re.I) and re.search(r"截止|cutoff|目标", text, re.I) and re.search(r"自动|选值|optimi|auto", text, re.I)
    if not explicit_rc and not implicit_rc:
        raise ValueError("当前自然语言执行入口只支持理想无负载 RC 低通")
    if re.search(r"高通|带通|运放|放大器|负载|容差|公差|误差|精度|温度|多板|固定|禁止|不要|不得|不能|不是|并非|电感|有源|二阶|三阶|增益|RLC|[%±]|high.pass|band.pass|op.amp|load|tolerance|accuracy|fixed|do not|inductor", text, re.I):
        raise ValueError("需求包含此 RC 入口尚不能兑现的约束，请使用结构化工程流程")
    if re.search(r"千欧|兆欧|毫欧|微欧|微法|纳法|皮法|毫法|千赫|兆赫|毫伏|微伏", text):
        raise ValueError("请使用明确的 SI 单位写法，例如 kΩ、nF、kHz、mV")
    automatic = bool(re.search(r"自动(?:选择|选值|选型|设计)|优化|选值|auto(?:matic)?\s+(?:select|design)|optimi", text, re.I))
    resistance = _quantity(text, r"(?:resistance|电阻|(?<![A-Za-z])R1?)", "ohm")
    capacitance = _quantity(text, r"(?:capacitance|电容|(?<![A-Za-z])C1?)", "f")
    target = _quantity(text, r"(?:截止(?:频率)?|cutoff(?: frequency)?|目标(?:频率)?)", "hz")
    frequencies = list(re.finditer(_NUMBER + r"(?![A-Za-z])", text))
    frequencies = [m for m in frequencies if m.group(2).lower().endswith('hz')]
    if len(frequencies) > 1:
        raise ValueError("多个频率条件需要进一步澄清")
    if target is None:
        if frequencies:
            target = _quantity("f=" + frequencies[0].group(0), "f", "hz")
    amplitude = _quantity(text, r"(?:输入(?:幅值|电压)?|input(?: amplitude)?|amplitude)", "v")
    # Capture what the user actually specified before applying defaults. Equal
    # effective values do not excuse a model dropping an explicit requirement.
    explicit_parameters = {key: value for key, value in {
        'resistance_ohm': resistance, 'capacitance_f': capacitance,
        'target_cutoff_hz': target, 'input_amplitude_v': amplitude,
    }.items() if value is not None}
    assumptions = ["采用理想单级无负载 RC 低通；不覆盖器件容差和 PCB 规则。",
                   "AC 使用 1 V 小信号；瞬态使用与输入幅值一致的延迟脉冲。"]
    if amplitude is None:
        amplitude = 1.0
        assumptions.append("未指定输入幅值，采用 1 V。")
    if capacitance is None and automatic and target is not None:
        capacitance = 100e-9
        assumptions.append("自动选值未指定电容，候选设计采用 C1=100 nF。")
    if capacitance is None:
        raise ValueError("需要明确电容值，或给出截止频率并要求自动选值")
    if target is None and resistance is not None:
        target = 1 / (2 * math.pi * resistance * capacitance)
        assumptions.append("未指定截止频率，目标由给定 R、C 推导。")
    if target is None:
        raise ValueError("需要截止频率目标或完整 R、C 数值")
    if not 10 <= target <= 100000 or not 1e-12 <= capacitance <= 1e-3 or not .001 <= amplitude <= 100:
        raise ValueError("首版范围：截止 10 Hz–100 kHz、电容 1 pF–1 mF、输入 1 mV–100 V")
    ideal = 1 / (2 * math.pi * target * capacitance)
    values = [float(parse_spice_scalar(v)) for v in generate_preferred_values('E24', _fmt(ideal / 2), _fmt(ideal * 2))]
    nearby = sorted(sorted(values, key=lambda v: abs(math.log(v / ideal)))[:3])
    if resistance is None:
        if not automatic:
            raise ValueError("需要明确电阻值，或要求自动选值")
        resistance = min(nearby, key=lambda v: abs(math.log(v / ideal)))
        assumptions.append("电阻由截止目标计算初值，并用相邻 E24 值进行原生实测比较。")
    if not 1 <= resistance <= 1e8 or any(not 1 <= v <= 1e8 for v in nearby):
        raise ValueError("当前电阻范围为 1 Ω–100 MΩ")
    candidates = sorted(set([resistance] + (nearby if automatic else [])))
    duration, delay, rise = 1 / target, .1 / target, .001 / target
    title = "自然语言生成 RC 低通工程"
    netlist = (f"* RC low-pass from natural requirements\nV1 in 0 DC {amplitude:g} AC 1 "
               f"PULSE(0 {amplitude:g} {_fmt(delay)} {_fmt(rise)} {_fmt(rise)} {_fmt(2*duration)} {_fmt(4*duration)})\n"
               f"R1 in out {_fmt(resistance)}\nC1 out 0 {_fmt(capacitance)}\n.end\n")
    request = {"schema_version": 1, "title": title, "application": text.strip(),
               "constraints": [{"text": assumption} for assumption in assumptions],
               "boards": [{"id": "main", "role": "primary"}],
               "experiments": [{"type": "op", "outputs": ["V(OutProbe)", "V(OutProbe1)"]},
                               {"type": "ac", "commands": f"ac dec 40 {_fmt(target/100)} {_fmt(target*100)}", "outputs": ["V(OutProbe)", "V(OutProbe1)"]},
                               {"type": "tran", "commands": f"tran {_fmt(duration/500)} {_fmt(duration)}", "outputs": ["V(OutProbe)", "V(OutProbe1)"]}],
               "objectives": [{"metric": "cutoff_frequency_hz", "direction": "target", "target": target, "tolerance": .02}]}
    return {"text": text, "planning_method": "bounded-rule-parser", "request": request, "netlist": netlist,
            "requirement_contract": {"schema_version": 1, "topology": "rc_low_pass",
                                     "explicit_parameters": explicit_parameters,
                                     "automatic_selection": automatic},
            "assumptions": assumptions, "automatic_selection": automatic, "candidate_resistances_ohm": candidates,
            "derived": {"resistance_ohm": resistance, "capacitance_f": capacitance,
                        "target_cutoff_hz": target, "input_amplitude_v": amplitude, "delay_s": delay, "rise_s": rise},
            "plan": build_engineering_plan(request)}


__all__ = ["parse_natural_request"]
