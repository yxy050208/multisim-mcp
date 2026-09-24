"""Bounded bridge planner with explicit native model, resistive load and sampled goals."""
from __future__ import annotations

import math
import re
from typing import Any


def parse_natural_rectifier(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip() or len(text) > 3000:
        raise ValueError("请输入有效的整流电源需求")
    if not re.search(r"整流|桥式|rectif|bridge", text, re.I):
        raise ValueError("当前入口需要明确桥式整流需求")
    if re.search(r"开关|switching|逆变|inverter|三相|three[- ]phase|半波|half.wave|稳压|regulated|恒流|constant.current|市电|mains|变压器|transformer|输出\s*\d|output\s*\d|Vpk|峰值|peak|负\d", text, re.I):
        raise ValueError("当前只支持隔离低压交流输入、桥式整流和电阻负载；不支持稳压/恒流/变压器设计或峰值输入")
    remaining = text
    remaining = re.sub(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*(?:mA|A))\s*负载',r'负载\1',remaining,flags=re.I)
    number = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))"
    def extract(pattern: str, default: float | None, scale: float = 1.) -> float | None:
        nonlocal remaining
        matches = list(re.finditer(pattern, remaining, re.I))
        if len(matches) > 1:
            raise ValueError("同一参数出现多个值，请消除歧义")
        if not matches:
            return default
        match = matches[0]
        remaining = remaining[:match.start()] + " " + remaining[match.end():]
        return float(match[1]) * scale
    remaining = re.sub(r"(?i)1N4001GP", " ", remaining)
    if re.search(r"(?i)\b(?:1N|UF|FR|BAT|SB)\d", remaining):
        raise ValueError("本地受验模型仅为1N4001GP，其他二极管型号不能替换")
    ripple = extract(r"(?:纹波|ripple)\s*(?:不超过|小于等于|小于|<=|≤|为|=|:)?\s*"+number+r"\s*V(?:pp)?", 1.)
    capacitor = extract(number+r"\s*(?:uF|µF|μF|微法)", None, 1e-6)
    rms = extract(number+r"\s*V(?:rms)?", 12.)
    frequency = extract(number+r"\s*Hz", 50.)
    current = extract(r"(?:负载(?:电流)?|load(?:\s+current)?)\s*(?:为|=|:)?\s*"+number+r"\s*mA", None, .001)
    if current is None:
        current = extract(r"(?:负载(?:电流)?|load(?:\s+current)?)\s*(?:为|=|:)?\s*"+number+r"\s*A", .1)
    if re.search(r"\d", remaining):
        raise ValueError("存在未识别的数值约束；支持输入Vrms、50/60Hz、负载mA、纹波V和电容uF")
    if not 4 <= rms <= 18 or not .005 <= current <= .2 or frequency not in (50.,60.) or not .1 <= ripple <= 2:
        raise ValueError("范围：4–18Vrms，50/60Hz，5–200mA，纹波上限0.1–2V")
    if capacitor is None:
        capacitor = next((c*1e-6 for c in (470,680,1000,1500,2200,3300,4700) if c*1e-6 >= 1.3*current/(2*frequency*ripple)), 0.)
    if not 470e-6 <= capacitor <= 4700e-6 or current/(2*frequency*capacitor) > ripple:
        raise ValueError("所需电容超出470–4700uF或给定电容不足以满足纹波估算")
    peak = round(rms * math.sqrt(2), 4)
    estimated_ripple = current/(2*frequency*capacitor)
    estimated_dc = peak - 1.5 - estimated_ripple/2
    resistance = round(estimated_dc/current, 2)
    load_power = estimated_dc * current
    stop, middle, start = 20/frequency, 17/frequency, 14/frequency
    voltage_low, voltage_high = estimated_dc*.85, min(peak,estimated_dc*1.15)
    def check(net: str, quantity: str, low: float, high: float, **extra: Any) -> dict:
        return {"analysis":"tran", "net":net,"quantity":quantity,"min":low,"max":high,
                "time_min_s":start,"time_max_s":stop,**extra}
    return {
        "planning_method":"bounded-diode-bridge-parser", "text":text.strip(),
        "template_family":"rectifier_supply", "topology":"single_phase_bridge_with_reservoir",
        "derived":{"ac_rms_v":rms,"ac_peak_v":peak,"frequency_hz":frequency,"load_a":current,
                   "load_resistance_ohm":resistance,"capacitance_f":capacitor,"ripple_limit_v":ripple,
                   "estimated_no_load_dc_v":peak-1.5,"estimated_loaded_dc_v":estimated_dc,
                   "estimated_ripple_vpp":estimated_ripple,"load_power_w":load_power,
                   "recommended_resistor_power_w":load_power*1.5,
                   "recommended_capacitor_voltage_v":peak*1.5,
                   "estimated_bridge_piv_v":peak*2,
                   "diode_count":4,"diode_model":"1N4001GP"},
        "proposal":{
            "title":"单相桥式整流与滤波", "application":text.strip(),
            "netlist":(f"V1 ac_p ac_n DC 0 SIN(0 {peak:g} {frequency:g})\n"
                       "D1 ac_p vraw 1N4001GP\nD2 ac_n vraw 1N4001GP\n"
                       "D3 0 ac_p 1N4001GP\nD4 0 ac_n 1N4001GP\n"
                       f"C1 vraw 0 {capacitor*1e6:g}u\nRLOAD vraw 0 {resistance:g}\n.end\n"),
            "probe_nets":["ac_p","ac_n","vraw"],
            "experiments":[{"type":"op"},{"type":"tran","commands":f"tran {1/(frequency*400):.17g} {stop:.17g}"}],
            "checks":[{"analysis":"op","net":"vraw","quantity":"value","min":-.001,"max":.001},
                      check("ac_p","rms",rms*.99,rms*1.01,subtract_net="ac_n"),
                      check("vraw","mean",voltage_low,voltage_high),
                      check("vraw","ripple_vpp",0.,ripple),
                      check("vraw","value",0.,peak),
                      check("vraw","mean",voltage_low,voltage_high,time_max_s=middle),
                      check("vraw","mean",voltage_low,voltage_high,time_min_s=middle)]},
        "assumptions":["未指定时采用12Vrms、50Hz、100mA、纹波上限1V；输入是隔离低压交流。",
                       "采用本地1N4001GP模型；负载电流用于设计电阻，不是恒流负载。",
                       "按每只二极管0.75V初估负载电阻，按纹波选470–4700uF；最终数值以原生仿真为准。",
                       "工程选型估算：电阻功率至少为负载平均功率的1.5倍，电容耐压至少为输入峰值的1.5倍，二极管PIV至少为输入峰值的2倍；需按实际器件数据手册复核。",
                       "原理图使用理想源/电容和精确计算的电阻；尚未验证源阻抗、浪涌、温升、容差、器件功率或实物安全。"],
        "status":"unverified-native-proposal"}


def validate_rectifier_netlist(plan: dict) -> None:
    from .linear_reference import scalar, validated_components
    parts = {p.refdes:p for p in validated_components(plan['proposal']['netlist'],allow_vendor=True)}
    expected = {'V1':('V',['ac_p','ac_n']), 'D1':('D',['ac_p','vraw']), 'D2':('D',['ac_n','vraw']),
                'D3':('D',['0','ac_p']), 'D4':('D',['0','ac_n']), 'C1':('C',['vraw','0']), 'RLOAD':('R',['vraw','0'])}
    if {r:(p.kind,p.nodes) for r,p in parts.items()} != expected:
        raise ValueError('bridge topology differs from the declared diode orientation/load contract')
    source = f"DC 0 SIN(0 {plan['derived']['ac_peak_v']:g} {plan['derived']['frequency_hz']:g})"
    if parts['V1'].model != source:
        raise ValueError('bridge source differs from the calculated design')
    for ref,key in [('C1','capacitance_f'),('RLOAD','load_resistance_ohm')]:
        if not math.isclose(scalar(parts[ref].value),plan['derived'][key],rel_tol=1e-12):
            raise ValueError('bridge netlist value differs from the calculated design')
