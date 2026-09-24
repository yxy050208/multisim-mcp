"""Bounded natural-language planner for a resistive DC divider."""
from __future__ import annotations
import re
from typing import Any
from .circuit_ir import CircuitIR, IrComponent, IrConnection

def parse_natural_dc_network(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError("请输入有效的直流网络需求")
    if not re.search(r"分压|divider|直流", text, re.I):
        raise ValueError("当前入口仅支持基础直流分压网络")
    nums = re.findall(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(mV|V|kΩ|kOhm|Ω|ohm)?", text, re.I)
    voltage = next((float(v) * (1e-3 if (u or '').lower() == 'mv' else 1) for v,u in nums if (u or '').lower() in ('v','mv')), 10.0)
    target = next((float(v) for v,u in nums if (u or '').lower() in ('v','mv') and float(v) != voltage), voltage/2)
    if voltage <= 0 or target <= 0 or target >= voltage:
        raise ValueError("输出电压必须位于 0 与输入电压之间")
    r_total = 20_000.0
    r2 = r_total * target / voltage
    r1 = r_total - r2
    net = f"V1 in 0 DC {voltage:g}\nR1 in out {r1:g}\nR2 out 0 {r2:g}\n.end\n"
    ir = CircuitIR([IrComponent("V1", "V", str(voltage)), IrComponent("R1", "R", str(r1)), IrComponent("R2", "R", str(r2))], [IrConnection("in", "V1.2"), IrConnection("in", "R1.1"), IrConnection("out", "R1.2"), IrConnection("out", "R2.1"), IrConnection("0", "R2.2"), IrConnection("0", "V1.1")])
    net = ir.to_spice()
    return {"planning_method":"bounded-dc-divider-parser","text":text.strip(),"circuit_ir":ir.to_dict(),"proposal":{"title":"自然语言生成：直流电阻分压网络","application":text.strip(),"netlist":net,"probe_nets":["out"],"experiments":[{"type":"op"}],"checks":[{"analysis":"op","net":"out","quantity":"value","min":target*0.99,"max":target*1.01}]},"derived":{"input_v":voltage,"target_v":target,"r1_ohm":r1,"r2_ohm":r2}}

__all__ = ["parse_natural_dc_network"]
