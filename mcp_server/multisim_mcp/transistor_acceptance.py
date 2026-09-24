"""Structural gates and explicitly approximate bias screening for one NPN stage."""
from __future__ import annotations
import math
from .schematic_builder import parse_netlist
from .preferred_values import parse_spice_scalar


def validate_common_emitter_netlist(netlist: str) -> dict[str, object]:
    if not isinstance(netlist, str):
        raise ValueError("netlist must be text")
    parsed = parse_netlist(netlist)
    if parsed.unsupported or not parsed.grounded:
        raise ValueError("common-emitter circuit must be supported and grounded")
    parts = parsed.components
    refs = {p.refdes.upper(): p for p in parts}
    if len(refs) != len(parts):
        raise ValueError("duplicate component reference")
    q = [p for p in parts if p.kind in {'QNPN', 'QPNP'}]
    if len(q) != 1 or q[0].kind != 'QNPN' or q[0].model.upper() != '2N3904' or q[0].model_definition or q[0].parameters:
        raise ValueError("exactly one unmodified local 2N3904 NPN is required")
    collector, base, emitter = q[0].nodes
    if len({collector, base, emitter, '0'}) != 4:
        raise ValueError("C/B/E must be distinct non-ground nets")
    required = {
        'RBIAS1': ('R', ['vcc', base]), 'RBIAS2': ('R', [base, '0']),
        'RC': ('R', ['vcc', collector]), 'RE': ('R', [emitter, '0']),
        'CIN': ('C', ['in', base]), 'COUT': ('C', [collector, 'out']),
        'RLOAD': ('R', ['out', '0']), 'VCC': ('V', ['vcc', '0']), 'VIN': ('V', ['in', '0']),
    }
    if set(refs) != set(required) | {q[0].refdes.upper()}:
        raise ValueError("missing or extra components in bounded common-emitter topology")
    for name, (kind, nodes) in required.items():
        part = refs[name]
        if part.kind != kind or part.nodes != nodes:
            raise ValueError(f"{name} is connected to incorrect pins/nets")
        if kind in {'R', 'C'} and float(parse_spice_scalar(part.value)) <= 0:
            raise ValueError(f"{name} must have a positive value")
    from .linear_reference import source_values, validate_native_source
    dc, ac = source_values(refs['VCC'])
    if dc <= 0 or ac != 0:
        raise ValueError("VCC must be a positive DC supply without AC stimulus")
    validate_native_source(refs['VIN'])
    if not refs['VIN'].model or 'AC ' not in refs['VIN'].model.upper():
        raise ValueError("VIN must provide the input stimulus")
    return {'valid': True, 'refdes': q[0].refdes, 'model': q[0].model,
            'pins': {'C': collector, 'B': base, 'E': emitter}, 'missing': [],
            'scope': 'topology-only; vendor model body is checked during native execution'}


def estimate_common_emitter_bias(netlist: str, supply_v: float) -> dict[str, object]:
    if not math.isfinite(supply_v) or supply_v <= 0:
        raise ValueError('supply must be finite and positive')
    values = {}
    for p in parse_netlist(netlist).components:
        if p.refdes.upper() in {'RBIAS1','RBIAS2','RC','RE'}:
            values[p.refdes.upper()] = float(parse_spice_scalar(p.value))
    if set(values) != {'RBIAS1','RBIAS2','RC','RE'} or min(values.values()) <= 0:
        raise ValueError('bias estimate requires positive RBIAS1/RBIAS2/RC/RE')
    r1, r2 = values['RBIAS1'], values['RBIAS2']
    vth, rth = supply_v*r2/(r1+r2), r1*r2/(r1+r2)
    beta, vbe = 100., .7
    ib = max(0., (vth-vbe)/(rth+(beta+1)*values['RE']))
    ve, vc = (beta+1)*ib*values['RE'], supply_v-beta*ib*values['RC']
    if ib <= 0 or vc <= ve+.2:
        raise ValueError('estimated transistor bias is saturated or cut off')
    return {'base_v': ve+vbe, 'emitter_v': ve, 'collector_v': vc,
            'emitter_current_a': (beta+1)*ib, 'assumed_beta': beta, 'assumed_vbe_v': vbe,
            'method': 'loaded-divider approximation; not native simulation'}
