"""Natural-language planner for a bounded two-stage sensor analog front end."""
from __future__ import annotations
import math, re
from typing import Any

_NUM = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-zΩωµμ]*)"
_SCALE = {"k":1e3,"m":1e-3,"u":1e-6,"µ":1e-6,"μ":1e-6,"n":1e-9,"p":1e-12,"M":1e6}

def _quantity(text: str, labels: str, units: set[str], *, signed=False) -> float | None:
    hits = list(re.finditer(labels + r"\s*(?:[:=：]|为|是|取)?\s*" + _NUM, text, re.I))
    if len(hits) > 1: raise ValueError("同一工程参数出现多个数值，无法安全选择")
    if not hits: return None
    value, suffix = float(hits[0].group(1)), hits[0].group(2)
    if suffix not in units and suffix[:1] in _SCALE:
        value *= _SCALE[suffix[:1]]; suffix = suffix[1:]
    if suffix.casefold() not in {u.casefold() for u in units} or not math.isfinite(value) or (not signed and value <= 0):
        raise ValueError("自然语言参数的数值或单位无效")
    return value

def _spice(value: float) -> str:
    for suffix, scale in (("M",1e6),("k",1e3),("m",1e-3),("u",1e-6),("n",1e-9),("p",1e-12)):
        scaled=value/scale
        if 1 <= abs(scaled) < 1000: return f"{scaled:.8g}{suffix}"
    return f"{value:.8g}"

def parse_natural_analog_frontend(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip() or len(text) > 4000: raise ValueError("请输入 1–4000 字的工程需求")
    if not re.search(r"传感器|模拟前端|signal conditioning|sensor|低通|抗混叠|filter", text, re.I): raise ValueError("当前入口需要传感器模拟前端或低通滤波场景")
    if re.search(r"数字|开关电源|PCB|多板|晶体管|ADC|DAC|digital|switching|transistor|multi.?board", text, re.I): raise ValueError("当前入口尚不支持数字、开关电源、分立晶体管或多板约束")
    vin = _quantity(text, r"(?:输入(?:幅值|电压)?|input(?: amplitude| voltage)?)", {"","v"}) or .1
    gain = _quantity(text, r"(?:总?增益|gain)", {"","x"}) or 10.
    cutoff = _quantity(text, r"(?:截止(?:频率)?|抗混叠(?:频率)?|cutoff(?: frequency)?)", {"","hz"}) or 1000.
    vcc = _quantity(text, r"(?:正电源|正轨|vcc|positive supply)", {"","v"}) or 15.
    vss = _quantity(text, r"(?:负电源|负轨|vss|negative supply)", {"","v"}, signed=True)
    load = _quantity(text, r"(?:负载|load)", {"","ohm","ω"}) or 100000.
    if vin > 100 or gain < 1 or gain > 100 or cutoff < 10 or cutoff > 100000 or vcc <= 0 or (vss is not None and vss >= 0) or load < 10000:
        raise ValueError("首版范围：输入≤100V、增益1–100、截止10Hz–100kHz、负载至少10kΩ")
    if vss is None: vss = -vcc
    rg = 10000.; stage_gain = math.sqrt(gain); rf = (stage_gain - 1) * rg; c = 1 / (2 * math.pi * rg * cutoff)
    stop_start, stop_end = max(cutoff * 10, 10), cutoff * 100; pass_start, pass_end = max(cutoff / 100, 10), max(cutoff / 10, 100)
    # Simulate five time constants so the post-step window is settled.
    duration, delay, rise = 5 / cutoff, .1 / cutoff, .001 / cutoff
    netlist = (f"V1 in 0 DC {vin:g} AC 1 PULSE(0 {vin:g} {_spice(delay)} {_spice(rise)} {_spice(rise)} {_spice(2*duration)} {_spice(4*duration)})\nVCC vcc 0 DC {vcc:g}\nVSS vss 0 DC {vss:g}\n"
               f"R1 in lp1 10k\nC1 lp1 0 {_spice(c)}\nXU1 lp1 fb1 vcc vss stage1 LM324AJ\nRF1 stage1 fb1 {_spice(rf)}\nRG1 fb1 0 10k\nR2 stage1 lp2 10k\nC2 lp2 0 {_spice(c)}\nXU2 lp2 fb2 vcc vss out LM324AJ\nRF2 out fb2 {_spice(rf)}\nRG2 fb2 0 10k\nRL out 0 {_spice(load)}\n.end\n")
    experiments = [{"type":"op"},{"type":"ac","commands":f"ac dec 40 {_spice(pass_start)} {_spice(stop_end)}"},{"type":"tran","commands":f"tran {_spice(duration/500)} {_spice(duration)}"}]
    checks = [
        {"analysis":"op","net":"out","quantity":"value","min":vin*gain*.99,"max":vin*gain*1.01},
        {"analysis":"ac","net":"out","reference_net":"in","quantity":"magnitude","min":gain*.99,"max":gain*1.01,"frequency_min_hz":pass_start,"frequency_max_hz":pass_end},
        {"analysis":"ac","net":"out","reference_net":"in","quantity":"magnitude","min":0,"max":.26,"frequency_min_hz":stop_start,"frequency_max_hz":stop_end},
        {"analysis":"tran","net":"out","quantity":"value","min":-vin*gain*.1,"max":vin*gain*.1,"time_min_s":0,"time_max_s":delay*.9},
        {"analysis":"tran","net":"out","quantity":"value","min":vin*gain*.99,"max":vin*gain*1.01,"time_min_s":duration*.6,"time_max_s":duration},
    ]
    proposal = {"title":"自然语言生成：LM324AJ 传感器信号调理与抗混叠滤波","application":text.strip(),"netlist":netlist,"probe_nets":["in","stage1","out"],"experiments":experiments,"checks":checks}
    return {"planning_method":"bounded-sensor-frontend-parser","text":text,"proposal":proposal,"derived":{"input_v":vin,"gain":gain,"cutoff_hz":cutoff,"supply_v":[vss,vcc],"load_ohm":load,"stage_gain":stage_gain,"rf_ohm":rf,"c_f":c},"assumptions":["两级一阶 RC 与两个独立 LM324AJ A 单元；真实模型依赖用户授权的本地模板包。","初始验收要求输出误差不超过 1%；失败后由原生测量驱动零点校准候选。","本入口只生成单板模拟原型，不代表实物或全局最优。"]}


def generate_analog_candidates(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Create a small, auditable RF/C grid; values are tested natively later."""
    proposal = plan["proposal"]
    base_rf, base_c = plan["derived"]["rf_ohm"], plan["derived"]["c_f"]
    candidates = []
    for rf_scale, c_scale in ((1.0,1.0), (0.98,1.0), (1.02,1.0), (1.0,0.95), (1.0,1.05)):
        rf, cap = base_rf * rf_scale, base_c * c_scale
        text = proposal["netlist"]
        text = re.sub(r"RF1 stage1 fb1 \\S+", f"RF1 stage1 fb1 {_spice(rf)}", text)
        text = re.sub(r"RF2 out fb2 \\S+", f"RF2 out fb2 {_spice(rf)}", text)
        text = re.sub(r"C1 lp1 0 \\S+", f"C1 lp1 0 {_spice(cap)}", text)
        text = re.sub(r"C2 lp2 0 \\S+", f"C2 lp2 0 {_spice(cap)}", text)
        item = dict(proposal, netlist=text)
        item["application"] += f"；反馈电阻比例 {rf_scale:g}，滤波电容比例 {c_scale:g}。"
        candidates.append({"rf_scale":rf_scale,"c_scale":c_scale,"rf_ohm":rf,"c_f":cap,"proposal":item})
    return candidates

__all__=["parse_natural_analog_frontend"]
