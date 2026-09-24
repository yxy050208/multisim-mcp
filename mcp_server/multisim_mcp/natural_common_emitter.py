"""Bounded single-NPN planner; native measurements determine acceptance."""
from __future__ import annotations
import re
from decimal import Decimal
from typing import Any
from .transistor_acceptance import validate_common_emitter_netlist, estimate_common_emitter_bias
from .preferred_values import format_spice_scalar


def parse_natural_common_emitter(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip() or len(text) > 3000:
        raise ValueError("请输入有效的共射放大器需求")
    if not re.search(r"共射|common[ -]?emitter|npn", text, re.I):
        raise ValueError("当前入口需要明确单管共射放大器")
    if re.search(r"mos|功率|多管|差分|振荡|power|multi|温度|容差|BC547|2N2222", text, re.I):
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
    net = (
        f"VCC vcc 0 DC {voltage:g}\n"
        "VIN in 0 DC 0 AC 1 PULSE(0 1m 1m 1u 1u 1m 2m)\n"
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
        {"analysis":"ac", "net":"out", "reference_net":"in", "quantity":"magnitude", "min":gain*.9, "max":gain*1.1, "frequency_min_hz":1000, "frequency_max_hz":1000},
        {"analysis":"ac", "net":"out", "reference_net":"in", "quantity":"phase_deg", "min":-180, "max":-150, "frequency_min_hz":1000, "frequency_max_hz":1000},
        {"analysis":"tran", "net":"in", "quantity":"value", "min":.00099, "max":.00101, "time_min_s":.0011, "time_max_s":.0012},
        {"analysis":"tran", "net":"out", "quantity":"value", "min":-.001*gain*1.2, "max":-.001*gain*.8, "time_min_s":.0011, "time_max_s":.0012},
    ]
    return {
        "planning_method":"bounded-common-emitter-parser", "text":text.strip(),
        "template_family":"transistor_discrete", "topology":"single_npn_common_emitter",
        "derived":{"supply_v":voltage,"target_gain":gain,"rc_ohm":rc,"re_ohm":re_,"rb1_ohm":rb1,"rb2_ohm":rb2},
        "structural_acceptance":validate_common_emitter_netlist(net),
        "bias_estimate":estimate_common_emitter_bias(net, voltage),
        "proposal":{"title":"2N3904共射放大器", "application":text.strip(), "netlist":net,
                    "probe_nets":["in","out","base","emitter","collector","vcc"],
                    "experiments":[{"type":"op"},{"type":"ac","commands":"ac dec 40 10 100k"},{"type":"tran","commands":"tran 10u 2m"}], "checks":checks},
        "assumptions":["固定本地2N3904模型、100kΩ负载、1mV脉冲输入；增益在1kHz验收。",
                       "偏置公式只是初始估算；实际工作点、增益和脉冲响应以原生仿真为准。",
                       "未进行温度、容差、功率级、噪声和实物板验证。"],
        "status":"unverified-native-proposal",
    }
