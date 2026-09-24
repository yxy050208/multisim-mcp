"""Stable catalog for parameterized circuit template families.

The catalog is metadata only; each family is implemented behind a planner,
netlist builder, native runner, and acceptance contract.
"""
from __future__ import annotations

from typing import Any

TEMPLATE_FAMILIES: tuple[dict[str, Any], ...] = (
    {"id": "dc_network", "name": "基础直流网络", "status": "next", "analyses": ("op",)},
    {"id": "analog_signal_conditioning", "name": "模拟信号调理", "status": "verified", "analyses": ("op", "ac", "tran")},
    {"id": "filtering", "name": "有源/无源滤波", "status": "verified", "analyses": ("op", "ac", "tran")},
    {"id": "waveform_shaping", "name": "波形产生与整形", "status": "planned", "analyses": ("op", "tran", "ac")},
    {"id": "rectifier_supply", "name": "整流与电源基础", "status": "active", "analyses": ("op", "tran")},
    {"id": "transistor_discrete", "name": "晶体管与分立器件", "status": "active", "analyses": ("op", "ac", "tran")},
    {"id": "digital_logic", "name": "数字逻辑与时序", "status": "planned", "analyses": ("op", "tran")},
    {"id": "system_multi_module", "name": "系统级多模块电路", "status": "planned", "analyses": ("op", "ac", "tran")},
)

def list_template_families() -> list[dict[str, Any]]:
    return [dict(item, analyses=list(item["analyses"])) for item in TEMPLATE_FAMILIES]

def get_template_family(template_id: str) -> dict[str, Any]:
    for item in TEMPLATE_FAMILIES:
        if item["id"] == template_id:
            return dict(item, analyses=list(item["analyses"]))
    raise KeyError(f"unknown circuit template family: {template_id}")
