"""Bounded natural-language contract for a passive second-order RLC low-pass."""
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
        raise ValueError(f"请只提供一个 {unit} 数值")
    if not matches:
        return None
    value, suffix = float(matches[0].group(1)), matches[0].group(2)
    allowed = {"ohm": {"", "ohm", "ω"}, "h": {"", "h"}, "f": {"", "f"}, "hz": {"", "hz"}, "v": {"", "v"}}[unit]
    if suffix.casefold() not in allowed and suffix[:1] in _SCALES:
        value *= _SCALES[suffix[0]]
        suffix = suffix[1:]
    if suffix.casefold() not in allowed or not math.isfinite(value) or value <= 0:
        raise ValueError(f"无效的 {unit} 数值：{matches[0].group(0)}")
    return value


def _fmt(value: float) -> str:
    return format_spice_scalar(Decimal(format(value, ".12g")))


def parse_natural_rlc_request(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError("请输入 1–4000 字的工程需求")
    if not re.search(r"\bRLC\b|二阶低通|rlc", text, re.I):
        raise ValueError("当前 RLC 入口需要明确写出 RLC 或二阶低通")
    if re.search(r"带通|运放|有源|负载|容差|公差|温度|多板|固定|禁止|PCB|band.pass|op.amp|load|tolerance", text, re.I):
        raise ValueError("需求包含当前 RLC 合同尚未支持的约束")
    resistance = _quantity(text, r"(?:resistance|电阻|(?<![A-Za-z])R1?)", "ohm")
    inductance = _quantity(text, r"(?:inductance|电感|(?<![A-Za-z])L1?)", "h")
    capacitance = _quantity(text, r"(?:capacitance|电容|(?<![A-Za-z])C1?)", "f")
    target = _quantity(text, r"(?:谐振(?:频率)?|截止(?:频率)?|目标(?:频率)?|resonance(?: frequency)?|cutoff(?: frequency)?)", "hz")
    if target is None:
        frequency_matches = list(re.finditer(_NUMBER, text, re.I))
        hz_values = [m for m in frequency_matches if m.group(2).casefold().endswith("hz")]
        if len(hz_values) == 1:
            suffix = hz_values[0].group(2).casefold()
            target = float(hz_values[0].group(1)) * ({"hz": 1.0, "khz": 1e3, "mhz": 1e6}.get(suffix, 1.0))
        elif len(hz_values) > 1:
            raise ValueError("多个频率条件需要进一步澄清")
    amplitude = _quantity(text, r"(?:输入(?:幅值|电压)?|input(?: amplitude)?|amplitude)", "v") or 1.0
    automatic = bool(re.search(r"自动(?:选择|选值|设计)|优化|选值|auto(?:matic)?\s+(?:select|design)|optimi", text, re.I))
    if target is None:
        raise ValueError("RLC 需求需要目标谐振/截止频率")
    if not 10 <= target <= 100_000 or not .001 <= amplitude <= 100:
        raise ValueError("RLC 首版范围：目标 10 Hz–100 kHz，输入 1 mV–100 V")
    if capacitance is None and inductance is None:
        raise ValueError("RLC 至少需要指定 L 或 C 之一")
    if capacitance is None:
        capacitance = 1 / ((2 * math.pi * target) ** 2 * inductance)  # type: ignore[operator]
    if inductance is None:
        inductance = 1 / ((2 * math.pi * target) ** 2 * capacitance)
    if not 1e-9 <= inductance <= 10 or not 1e-12 <= capacitance <= 1e-3:
        raise ValueError("RLC 范围：L 1 nH–10 H，C 1 pF–1 mF")
    ideal_r = 2 * math.pi * target * inductance / 2
    nearby = [float(parse_spice_scalar(v)) for v in generate_preferred_values("E24", _fmt(ideal_r / 2), _fmt(ideal_r * 2))]
    nearby = sorted(sorted(nearby, key=lambda v: abs(math.log(v / ideal_r)))[:3])
    if resistance is None:
        if not automatic:
            raise ValueError("需要明确电阻值，或要求自动选值")
        resistance = min(nearby, key=lambda v: abs(math.log(v / ideal_r)))
    if not 1 <= resistance <= 1e8:
        raise ValueError("RLC 电阻范围为 1 Ω–100 MΩ")
    candidates = sorted(set([resistance] + (nearby if automatic else [])))
    explicit = {k: v for k, v in {"resistance_ohm": _quantity(text, r"(?:resistance|电阻|(?<![A-Za-z])R1?)", "ohm"), "inductance_h": _quantity(text, r"(?:inductance|电感|(?<![A-Za-z])L1?)", "h"), "capacitance_f": _quantity(text, r"(?:capacitance|电容|(?<![A-Za-z])C1?)", "f"), "target_frequency_hz": target}.items() if v is not None}
    assumptions = ["采用理想无负载 RLC 二阶低通；输出取电容节点，不覆盖容差、负载和 PCB 规则。", "该阶段只生成合同和网表；原生 RLC 验收尚未启用。"]
    netlist = (f"* RLC second-order low-pass\nV1 in 0 DC {amplitude:g} AC 1\n"
               f"R1 in n1 {_fmt(resistance)}\nL1 n1 out {_fmt(inductance)}\nC1 out 0 {_fmt(capacitance)}\n.end\n")
    request = {"schema_version": 1, "title": "自然语言生成 RLC 二阶低通工程", "application": text.strip(),
               "constraints": [{"text": item} for item in assumptions], "boards": [{"id": "main", "role": "primary"}],
               "experiments": [{"type": "op", "outputs": ["V(OutProbe)", "V(OutProbe1)"]},
                               {"type": "ac", "commands": f"ac dec 40 {_fmt(target/100)} {_fmt(target*100)}", "outputs": ["V(OutProbe)", "V(OutProbe1)"]}],
               "objectives": [{"metric": "target_frequency_hz", "direction": "target", "target": target, "tolerance": .02}]}
    return {"text": text, "planning_method": "bounded-rlc-rule-parser", "request": request, "netlist": netlist,
            "requirement_contract": {"schema_version": 1, "topology": "rlc_second_order_low_pass", "explicit_parameters": explicit, "automatic_selection": automatic},
            "assumptions": assumptions, "automatic_selection": automatic, "candidate_resistances_ohm": candidates,
            "derived": {"resistance_ohm": resistance, "inductance_h": inductance, "capacitance_f": capacitance, "target_frequency_hz": target, "input_amplitude_v": amplitude},
            "plan": build_engineering_plan(request)}


__all__ = ["parse_natural_rlc_request"]
