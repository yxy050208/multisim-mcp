"""Bounded single-NPN planner; native measurements determine acceptance."""
from __future__ import annotations
import re
from decimal import Decimal
from typing import Any
from .transistor_acceptance import validate_common_emitter_netlist, estimate_common_emitter_bias
from .preferred_values import format_spice_scalar, generate_preferred_values, parse_spice_scalar


def _candidate_resistors(value: float, *, limit: int = 5) -> list[float]:
    """Return a small deterministic E24 neighbourhood around an estimated RE."""
    if value <= 0 or not isinstance(value, (int, float)):
        raise ValueError("estimated emitter resistance must be positive")
    lower = format_spice_scalar(Decimal(str(value * 0.7)))
    upper = format_spice_scalar(Decimal(str(value * 1.3)))
    preferred = [float(parse_spice_scalar(item)) for item in generate_preferred_values("E24", lower, upper)]
    if not preferred:
        preferred = [value]
    preferred.sort(key=lambda item: (abs(item - value) / value, item))
    return sorted(preferred[:limit])


def _engineering_number(value: str, suffix: str | None) -> float:
    scales = {"": 1.0, "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9}
    normalized = (suffix or "").lower()
    if normalized not in scales:
        raise ValueError(f"不支持的工程单位: {suffix}")
    return float(value) * scales[normalized]


def parse_natural_common_emitter(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip() or len(text) > 3000:
        raise ValueError("请输入有效的共射放大器需求")
    if not re.search(r"共射|common[ -]?emitter|npn", text, re.I):
        raise ValueError("当前入口需要明确单管共射放大器")
    if re.search(r"mos|功率|多管|差分|振荡|power|multi|温度|BC547|2N2222", text, re.I):
        raise ValueError("需求超出单NPN共射放大器合同范围")
    supplies = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*V(?![a-z])", text, re.I)
    gains = re.findall(r"(?:增益|gain)\s*(?:约|为|=)?\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    if len(supplies) > 1 or len(gains) > 1:
        raise ValueError("请提供一个电源电压和一个增益")
    voltage = float(supplies[0]) if supplies else 12.0
    gain = float(gains[0]) if gains else 10.0
    if not 5 <= voltage <= 30 or not 2 <= gain <= 50:
        raise ValueError("单电源需为5–30V，目标增益需为2–50倍")
    ic, load = .001, 100_000.0
    rc = voltage / (2 * ic)
    re_ = 1 / (1 / rc + 1 / load) / gain - .026 / ic
    if re_ <= 0:
        raise ValueError("目标增益超出此发射极退化拓扑范围")
    divider_current = .0002
    vb = ic * re_ + .7
    rb2 = vb / divider_current
    rb1 = (voltage - vb) / divider_current
    if rb1 <= 0:
        raise ValueError("偏置网络无法满足此电源与增益组合")
    rc, re_, rb1, rb2 = [float(f"{v:.4g}") for v in (rc, re_, rb1, rb2)]
    def resistor(value: float) -> str:
        return format_spice_scalar(Decimal(str(value)))
    sine_requested = bool(re.search(r"正弦|THD|失真|无失真", text, re.I))
    thd_matches = re.findall(r"(?:THD|总谐波失真)\s*(?:不超过|小于|<=|≤|约为|为|=)?\s*([0-9]+(?:\.[0-9]+)?)\s*%?", text, re.I)
    thd_limit_percent = float(thd_matches[0]) if thd_matches else 1.0
    if not 0 < thd_limit_percent <= 100:
        raise ValueError("THD 限值需为0–100%")
    tolerance_matches = re.findall(
        r"(?:容差|tolerance)\s*(?:不超过|小于|<=|≤|约为|为|=)?\s*([0-9]+(?:\.[0-9]+)?)\s*%?",
        text, re.I,
    )
    tolerance_percent = float(tolerance_matches[0]) if tolerance_matches else None
    if tolerance_percent is not None and not 0 < tolerance_percent <= 20:
        raise ValueError("元件容差需为0–20%")
    frequency_matches = re.findall(
        r"(?:频率|frequency)\s*(?:为|=|约为|约)?\s*([0-9]+(?:\.[0-9]+)?)\s*([kmun]?)\s*Hz",
        text, re.I,
    )
    sine_frequency_hz = _engineering_number(*frequency_matches[0]) if frequency_matches else 1000.0
    if not 1 <= sine_frequency_hz <= 1e6:
        raise ValueError("正弦频率需在1Hz–1MHz范围内")
    amplitude_matches = re.findall(
        r"(?:输入(?:峰值|幅值)?|振幅|amplitude)\s*(?:为|=|约为|约)?\s*([0-9]+(?:\.[0-9]+)?)\s*([munk]?)\s*V"
        r"|([0-9]+(?:\.[0-9]+)?)\s*([munk]?)\s*V\s*(?:输入(?:峰值|幅值)?|振幅)",
        text, re.I,
    )
    if amplitude_matches:
        match = amplitude_matches[0]
        amplitude_value, amplitude_suffix = (match[0], match[1]) if match[0] else (match[2], match[3])
        sine_amplitude_v = _engineering_number(amplitude_value, amplitude_suffix)
    else:
        sine_amplitude_v = 1e-3
    if not 1e-6 <= sine_amplitude_v <= 1:
        raise ValueError("正弦输入峰值需在1uV–1V范围内")
    gain_frequency_min = sine_frequency_hz / 1.1 if sine_requested else 1000.0
    gain_frequency_max = sine_frequency_hz * 1.1 if sine_requested else 1000.0
    input_source = (
        f"VIN in 0 DC 0 AC 1 SIN(0 {resistor(sine_amplitude_v)} {resistor(sine_frequency_hz)})"
        if sine_requested else "VIN in 0 DC 0 AC 1 PULSE(0 1m 1m 1u 1u 1m 2m)"
    )
    net = (
        f"VCC vcc 0 DC {voltage:g}\n"
        f"{input_source}\n"
        f"RBIAS1 vcc base {resistor(rb1)}\nRBIAS2 base 0 {resistor(rb2)}\n"
        f"RC vcc collector {resistor(rc)}\nRE emitter 0 {resistor(re_)}\n"
        "Q1 collector base emitter 2N3904\nCIN in base 10u\n"
        "COUT collector out 10u\nRLOAD out 0 100k\n.end\n"
    )
    checks = [
        {"analysis":"op", "net":"vcc", "quantity":"value", "min":voltage*.99, "max":voltage*1.01},
        {"analysis":"op", "net":"collector", "quantity":"value", "min":voltage*.35, "max":voltage*.7},
        {"analysis":"op", "net":"base", "subtract_net":"emitter", "quantity":"value", "min":.5, "max":.85},
        {"analysis":"op", "net":"collector", "subtract_net":"base", "quantity":"value", "min":.3, "max":voltage},
        {"analysis":"ac", "net":"out", "reference_net":"in", "quantity":"magnitude", "min":gain*.9, "max":gain*1.1, "frequency_min_hz":gain_frequency_min, "frequency_max_hz":gain_frequency_max},
        {"analysis":"ac", "net":"out", "reference_net":"in", "quantity":"phase_deg", "min":-180, "max":-150, "frequency_min_hz":gain_frequency_min, "frequency_max_hz":gain_frequency_max},
        {"analysis":"tran", "net":"in", "quantity":"value",
         "min":-sine_amplitude_v*1.1 if sine_requested else .00099,
         "max":sine_amplitude_v*1.1 if sine_requested else .00101,
         "time_min_s":.001 if sine_requested else .0011,
         "time_max_s":.002 if sine_requested else .0012},
        {"analysis":"tran", "net":"out", "quantity":"value",
         "min":-2*sine_amplitude_v*gain if sine_requested else -.001*gain*1.2,
         "max":2*sine_amplitude_v*gain if sine_requested else -.001*gain*.8,
         "time_min_s":.001 if sine_requested else .0011,
         "time_max_s":.002 if sine_requested else .0012},
    ]
    candidate_resistors = _candidate_resistors(re_)
    return {
        "planning_method":"bounded-common-emitter-parser", "text":text.strip(),
        "template_family":"transistor_discrete", "topology":"single_npn_common_emitter",
        "derived":{"supply_v":voltage,"target_gain":gain,"rc_ohm":rc,"re_ohm":re_,"rb1_ohm":rb1,"rb2_ohm":rb2},
        "candidate_resistors_ohm": candidate_resistors,
        "structural_acceptance":validate_common_emitter_netlist(net),
        "bias_estimate":estimate_common_emitter_bias(net, voltage),
        "waveform":"sine" if sine_requested else "pulse",
        "sine_frequency_hz": sine_frequency_hz if sine_requested else None,
        "sine_amplitude_v": sine_amplitude_v if sine_requested else None,
        "thd_limit_percent":thd_limit_percent if sine_requested else None,
        "tolerance_percent": tolerance_percent,
        "proposal":{"title":"2N3904共射放大器", "application":text.strip(), "netlist":net,
                    "probe_nets":["in","out","base","emitter","collector","vcc"],
                    "experiments":[{"type":"op"},{"type":"ac","commands":"ac dec 40 10 100k"},{"type":"tran","commands":"tran 10u 3m" if sine_requested else "tran 10u 2m"}], "checks":checks},
        "assumptions":[f"固定本地2N3904模型、100kΩ负载、{resistor(sine_amplitude_v)}正弦输入、{resistor(sine_frequency_hz)}；增益在请求频率邻域验收。" if sine_requested else "固定本地2N3904模型、100kΩ负载、1mV脉冲输入；增益在1kHz验收。",
                       "候选搜索仅比较估算值附近最多5个E24发射极电阻，最终以原生实测增益选择。",
                       "偏置公式只是初始估算；实际工作点、增益和波形响应以原生仿真为准。",
                       "正弦模式的THD仅在原生稳态窗口和声明谐波数内验收；容差扫描仅覆盖声明的电阻角落，未进行温度、功率级、噪声和实物板验证。" if sine_requested else "容差扫描仅覆盖声明的电阻角落，未进行温度、功率级、噪声和实物板验证。"],
        "status":"unverified-native-proposal",
    }
