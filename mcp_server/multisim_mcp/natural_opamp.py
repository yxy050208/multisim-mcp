"""Bounded natural-language contract for a non-inverting OPAMP5 stage."""
from __future__ import annotations
import math, re
from decimal import Decimal
from typing import Any
from .engineering_planner import build_engineering_plan
from .preferred_values import format_spice_scalar, parse_spice_scalar, generate_preferred_values

_NUM=r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-zΩωµμ]*)"
_S={"k":1e3,"K":1e3,"M":1e6,"m":1e-3,"u":1e-6,"µ":1e-6,"μ":1e-6}
def _q(text,label,unit):
    m=list(re.finditer(label+r"\s*(?:[:=：]|为|是|取)?\s*"+_NUM,text,re.I))
    if len(m)>1: raise ValueError(f"请只提供一个{unit}数值")
    if not m:return None
    v,s=float(m[0].group(1)),m[0].group(2)
    allowed={"ohm":{"","ohm","ω"},"v":{"","v"},"gain":{"","x"}}[unit]
    if s.casefold() not in allowed and s[:1] in _S:v*= _S[s[0]];s=s[1:]
    if s.casefold() not in allowed or not math.isfinite(v) or (v == 0 and unit != "v"): raise ValueError(f"无效的{unit}数值")
    return v
def _f(v): return format_spice_scalar(Decimal(format(v,'.12g')))
def parse_natural_opamp_request(text:str)->dict[str,Any]:
    if not isinstance(text,str) or not text.strip() or len(text)>4000: raise ValueError("请输入1–4000字的工程需求")
    if not re.search(r"运放|op[ -]?amp|非反相|non[ -]?inverting",text,re.I): raise ValueError("当前运放入口需要明确非反相运放")
    if re.search(r"负载|容差|温度|多板|积分|微分|比较器|差分|load|tolerance|integrator|comparator",text,re.I): raise ValueError("需求包含当前运放合同尚未支持的约束")
    gain=_q(text,r"(?:增益|gain)","gain")
    rin=_q(text,r"(?:输入(?:幅值|电压)?|input(?: amplitude)?)","v") or .1
    vcc=_q(text,r"(?:正电源|正轨|vcc|supply)","v") or 15.
    vss=_q(text,r"(?:负电源|负轨|vss)","v")
    if vss is None:vss=-15.
    if gain is None: raise ValueError("需要明确闭环增益")
    if gain<1 or gain>1000 or rin<=0 or vcc<=0 or vss>=0: raise ValueError("增益或电源范围无效")
    automatic=bool(re.search(r"自动|选值|优化|auto|optimi",text,re.I))
    rg=_q(text,r"(?:反馈下电阻|增益下电阻|r[_ ]?g|rg)","ohm") or 10_000.
    ideal_rf=(gain-1)*rg
    vals=[float(parse_spice_scalar(v)) for v in generate_preferred_values("E24",_f(ideal_rf/2),_f(ideal_rf*2))]
    candidates=sorted(set([ideal_rf]+(vals[:3] if automatic else [])))
    explicit={k:v for k,v in {"gain":gain,"input_amplitude_v":_q(text,r"(?:输入(?:幅值|电压)?|input(?: amplitude)?)","v"),"vcc_v":_q(text,r"(?:正电源|正轨|vcc|supply)","v"),"vss_v":_q(text,r"(?:负电源|负轨|vss)","v")}.items() if v is not None}
    net=f"V1 in 0 DC 0 AC 1\nVCC vcc 0 DC {vcc:g}\nVSS vss 0 DC {vss:g}\nR1 in 0 1G\nRG nfb 0 {_f(rg)}\nRF out nfb {_f(ideal_rf)}\nXU1 in nfb vcc vss out OPAMP5\n.end\n"
    req={"schema_version":1,"title":"自然语言非反相运放工程","application":text.strip(),"constraints":[{"text":"理想OPAMP5、双电源、无负载非反相闭环；暂不覆盖饱和、噪声和容差。"}],"boards":[{"id":"main","role":"primary"}],"experiments":[{"type":"op","outputs":["V(OutProbe)","V(OutProbe1)"]},{"type":"ac","commands":"ac dec 40 1 1Meg","outputs":["V(OutProbe)","V(OutProbe1)"]}],"objectives":[{"metric":"closed_loop_gain","direction":"target","target":gain,"tolerance":.02}]}
    return {"text":text,"planning_method":"bounded-opamp-rule-parser","request":req,"netlist":net,"requirement_contract":{"schema_version":1,"topology":"opamp_non_inverting","explicit_parameters":explicit,"automatic_selection":automatic},"assumptions":req["constraints"],"automatic_selection":automatic,"candidate_feedback_resistances_ohm":candidates,"derived":{"gain":gain,"input_amplitude_v":rin,"vcc_v":vcc,"vss_v":vss,"rg_ohm":rg,"rf_ohm":ideal_rf},"plan":build_engineering_plan(req)}
__all__=["parse_natural_opamp_request"]
