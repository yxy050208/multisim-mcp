"""Native common-emitter execution with saved-file presentation checks."""
from pathlib import Path
import html
import csv
import json
import math
import re
from typing import Any

from .engineering_task_contract import finalize_task_result
from .generated_analog_run import run_generated_analog_project
from .native_xml import parse_native_xml
from .natural_common_emitter import parse_natural_common_emitter
from .schematic_builder import parse_netlist, voltage_source_stem


def _candidate_proposal(plan: dict[str, Any], resistance: float) -> dict[str, Any]:
    """Copy the bounded proposal and change only the declared RE value."""
    proposal = dict(plan['proposal'])
    netlist = re.sub(
        r'(?im)^RE\s+emitter\s+0\s+\S+',
        f"RE emitter 0 {format_resistance(resistance)}",
        plan['proposal']['netlist'],
        count=1,
    )
    if netlist == plan['proposal']['netlist']:
        raise ValueError('common-emitter proposal has no RE line')
    proposal['netlist'] = netlist
    return proposal


def format_resistance(value: float) -> str:
    from decimal import Decimal
    from .preferred_values import format_spice_scalar
    return format_spice_scalar(Decimal(str(value)))


def _measured_gain(result: dict[str, Any], target: float) -> tuple[float | None, float | None]:
    acceptance = result.get('measurement_acceptance') or {}
    for check in acceptance.get('checks', []):
        requirement = check.get('requirement', {})
        if requirement.get('analysis') == 'ac' and requirement.get('quantity') == 'magnitude':
            measured = check.get('measured_max')
            if isinstance(measured, (int, float)) and measured == measured:
                return float(measured), abs(float(measured) - target) / max(target, 1e-12)
    return None, None


def _frequency_response_acceptance(candidate_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Measure the available native AC sweep and report honest -3 dB evidence.

    This is deliberately a derived report metric rather than a new hard gate:
    the current common-emitter contract requests 10 Hz..100 kHz, and some
    valid devices do not reach either -3 dB edge in that window.  In that case
    the result is ``unverified`` with the missing edge recorded explicitly.
    """
    experiments = plan["proposal"].get("experiments", [])
    ac_index = next((i for i, item in enumerate(experiments, 1) if item.get("type") == "ac"), None)
    if ac_index is None:
        return {"status": "unverified", "reason": "no native AC sweep requested"}
    csv_path = candidate_dir / "native" / f"analysis-{ac_index:03d}" / "data.csv"
    if not csv_path.is_file():
        return {"status": "unverified", "reason": "native AC data.csv is missing"}
    probe_nets = plan["proposal"].get("probe_nets", [])
    try:
        in_index = probe_nets.index("in")
        out_index = probe_nets.index("out")
    except ValueError:
        return {"status": "unverified", "reason": "in/out probes are not declared"}
    def signal(index: int) -> str:
        return f"V(OutProbe{index if index else ''})"
    in_signal, out_signal = signal(in_index), signal(out_index)
    rows = []
    try:
        with csv_path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                frequency = float(row["frequency_hz"])
                input_value = complex(float(row[f"{in_signal}.real"]), float(row[f"{in_signal}.imaginary"]))
                output_value = complex(float(row[f"{out_signal}.real"]), float(row[f"{out_signal}.imaginary"]))
                if frequency > 0 and math.isfinite(frequency) and abs(input_value) > 1e-15:
                    gain = abs(output_value / input_value)
                    if math.isfinite(gain):
                        rows.append((frequency, gain))
    except (KeyError, ValueError, TypeError) as exc:
        return {"status": "unverified", "reason": f"native AC data is invalid: {exc}"}
    if len(rows) < 3:
        return {"status": "unverified", "reason": "native AC sweep has fewer than three usable samples", "samples": len(rows)}
    rows.sort()
    peak_frequency, peak_gain = max(rows, key=lambda item: item[1])
    threshold = peak_gain / (10 ** (3 / 20))
    peak_index = next(index for index, item in enumerate(rows) if item == (peak_frequency, peak_gain))

    def crossing(left: tuple[float, float], right: tuple[float, float]) -> float | None:
        f1, g1 = left
        f2, g2 = right
        if (g1 - threshold) * (g2 - threshold) > 0 or g1 == g2 or f1 == f2:
            return None
        fraction = (threshold - g1) / (g2 - g1)
        # Frequency sweeps are logarithmic in the native contract.  Log-space
        # interpolation avoids overstating an edge between decades.
        return 10 ** (math.log10(f1) + fraction * (math.log10(f2) - math.log10(f1)))

    lower = next((crossing(rows[index - 1], rows[index]) for index in range(peak_index, 0, -1)
                  if crossing(rows[index - 1], rows[index]) is not None), None)
    upper = next((crossing(rows[index], rows[index + 1]) for index in range(peak_index, len(rows) - 1)
                  if crossing(rows[index], rows[index + 1]) is not None), None)
    result = {
        "status": "passed-sweep-minus3db" if lower is not None and upper is not None else "unverified",
        "criterion": "peak gain minus 3 dB",
        "samples": len(rows),
        "sweep_start_hz": rows[0][0],
        "sweep_stop_hz": rows[-1][0],
        "peak_frequency_hz": peak_frequency,
        "peak_gain": peak_gain,
        "threshold_gain": threshold,
        "lower_cutoff_hz": lower,
        "upper_cutoff_hz": upper,
        "bandwidth_hz": upper - lower if lower is not None and upper is not None else None,
    }
    if lower is None or upper is None:
        result["reason"] = "one or both -3 dB edges lie outside the requested native sweep"
    return result


def _distortion_acceptance(plan: dict[str, Any]) -> dict[str, Any]:
    """State why THD is not claimed until a sinusoidal native source exists."""
    return {
        "status": "unverified",
        "reason": "the current native transient experiment uses a pulse source; THD requires a VSIN transient run with declared steady-state window and harmonics",
        "required_next_step": "add and verify a native VSIN carrier, then run a dedicated transient experiment",
    }


def verify_ce_presentation(path: Path, plan: dict) -> dict:
    """Inspect the file Multisim actually opened/saved, not a label-only patch."""
    root = parse_native_xml(path).getroot()
    components = {c.get('LocalName', '').removeprefix('&ASC'): c for c in root.iter('CiComponent')}
    checks = {}
    for ref, identity, parameters in (
        ('VCC', 'DC_POWER', {1: plan['derived']['supply_v']}),
        ('VIN', 'PULSE_VOLTAGE', {1: 0., 3: .001, 5: .001, 7: .000001, 9: .000001, 11: .001, 13: .002}),
    ):
        component = components.get(ref)
        if component is None:
            checks[ref] = False
            continue
        names = [e.get('Value', '').removeprefix('&ASC') for e in component.findall('./Attributes/Item/CiaCollString/strings/Item')]
        values = component.findall('.//CiaParamList/doubles/Item')
        checks[ref] = len(names) > 1 and names[1] == identity and all(
            index < len(values) and abs(float(values[index].get('Value'))-value) <= max(1e-12,abs(value)*1e-9)
            for index,value in parameters.items())
    probes = list(root.iter('CIITProbeExtComponent'))
    checks['probe_panels_hidden'] = len(probes) == len(plan['proposal']['probe_nets']) and all(
        p.get('Hidden') == '1' and p.get('ShowInfo') == '0' for p in probes)
    checks['no_ac_example_label'] = not any('5kHz' in e.get('Output','') for e in root.iter('CIITSymTextCompValue'))
    return {'ok': all(checks.values()), 'checks': checks,
            'scope': 'native source identities/parameters and probe visibility; human schematic review remains required'}


def run_natural_common_emitter(text: str, output: str, *, execute: bool = False) -> dict:
    plan = parse_natural_common_emitter(text)
    if execute:
        sources = {p.refdes: voltage_source_stem(p) for p in parse_netlist(plan['proposal']['netlist']).components if p.kind == 'V'}
        if sources != {'VCC': 'vdc', 'VIN': 'vpulse'}:
            raise ValueError('Rebuild the local component pack: native VDC and VPULSE carriers are required for this workflow')
    root = Path(output).expanduser().resolve()
    if root.exists() or root == Path(root.anchor):
        raise FileExistsError('output must be a new directory')
    if not execute:
        result = {
            'success': True, 'mode': 'preview', 'output_dir': str(root),
            'proposal': plan['proposal'], 'natural_language_plan': plan,
            'candidates': [{'resistance_ohm': value, 'status': 'planned'}
                           for value in plan['candidate_resistors_ohm']],
            'optimization': {'method': 'native-measured-gain-neighbourhood',
                             'candidate_count': len(plan['candidate_resistors_ohm'])},
            'verification_status': 'unverified', 'simulation_started': False,
        }
        from .engineering_task_contract import normalize_task_result
        return normalize_task_result(result)

    root.mkdir(parents=True, exist_ok=False)
    (root / 'input.txt').write_text(text, encoding='utf-8')
    (root / 'proposal.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    result: dict[str, Any] = {
        'success': False, 'mode': 'execute', 'output_dir': str(root),
        'proposal': plan['proposal'], 'natural_language_plan': plan, 'candidates': [],
        'verification_status': 'failed', 'simulation_started': False,
    }
    try:
        for index, resistance in enumerate(plan['candidate_resistors_ohm'], 1):
            proposal = _candidate_proposal(plan, resistance)
            candidate_dir = root / f'candidate-{index:03d}'
            candidate = run_generated_analog_project(proposal, str(candidate_dir), execute=True)
            measured_gain, target_error = _measured_gain(candidate, plan['derived']['target_gain'])
            candidate_record: dict[str, Any] = {
                'directory': candidate_dir.name,
                'project': f'{candidate_dir.name}/native/circuit.ms14',
                'resistance_ohm': resistance,
                'success': bool(candidate.get('success')),
                'measured_gain': measured_gain,
                'target_error_fraction': target_error,
                'verification_status': candidate.get('verification_status'),
                'result': candidate,
                'frequency_response': _frequency_response_acceptance(candidate_dir, plan),
                'distortion_acceptance': _distortion_acceptance(plan),
            }
            if candidate_record['success']:
                model_xml = candidate_dir / 'native-model.xml'
                if not model_xml.is_file():
                    candidate_record['success'] = False
                    candidate_record['verification_status'] = 'native-presentation-missing'
                else:
                    presentation = verify_ce_presentation(model_xml, plan)
                    candidate_record['presentation_acceptance'] = presentation
                    candidate_record['success'] = presentation['ok']
                    if not presentation['ok']:
                        candidate_record['verification_status'] = 'native-presentation-mismatch'
            result['candidates'].append(candidate_record)
        feasible = [item for item in result['candidates']
                    if item['success'] and item['target_error_fraction'] is not None]
        if feasible:
            selected = min(feasible, key=lambda item: (item['target_error_fraction'], item['resistance_ohm']))
            result['selected'] = selected
            result['success'] = True
            result['verification_status'] = 'passed-common-emitter-candidate-search'
            result['simulation_started'] = True
            result['delivery_status'] = 'requires-visual-review'
        else:
            result['error'] = {'type': 'RuntimeError', 'message': 'no common-emitter candidate passed native acceptance'}
            result['delivery_status'] = 'not-ready'
    except Exception as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        result['delivery_status'] = 'not-ready'
    report_rows = ''.join(
        '<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
            item['resistance_ohm'], item.get('measured_gain') if item.get('measured_gain') is not None else '—',
            f"{100 * item['target_error_fraction']:.3f}%" if item.get('target_error_fraction') is not None else '—',
            html.escape(str(item.get('frequency_response', {}).get('status', 'unverified'))),
            html.escape(str(item.get('verification_status'))), '是' if item['success'] else '否')
        for item in result['candidates'])
    selected = result.get('selected')
    conclusion = (f"选中 RE={selected['resistance_ohm']:g} Ω，实测增益 {selected['measured_gain']:.6g}。"
                  if selected else '没有候选通过原生验收。')
    (root / 'report.html').write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>共射放大器候选搜索</title>'
        '<style>body{font:16px/1.7 system-ui;max-width:1100px;margin:40px auto;padding:20px}'
        'table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px}</style>'
        '<h1>2N3904 共射放大器候选搜索</h1>'
        f'<p>状态：{html.escape(result["verification_status"])}；{html.escape(conclusion)}</p>'
        '<table><tr><th>RE</th><th>实测增益</th><th>目标误差</th><th>频带证据</th><th>状态</th><th>通过</th></tr>'
        + report_rows + '</table><p>每个候选均为独立原生工程副本，保留其 Multisim 工程、CSV、图纸和 manifest。</p>'
        '<p>频带指标从原生 AC 扫频派生；当前脉冲瞬态输入不能用于 THD，失真状态保持 unverified，直到 VSIN 原生载体完成验证。</p>'
        '<p><a href="proposal.json">需求与候选</a> · <a href="acceptance.json">验收记录</a> · <a href="manifest.json">完整性清单</a></p></html>',
        encoding='utf-8')
    return finalize_task_result(root, result)
