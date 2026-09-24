"""Natural RC requirements to generated native projects, measurements and report."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Callable

from .engineering_task_contract import normalize_task_result, finalize_task_result

from .natural_engineering import parse_natural_request
from .native_analog_acceptance import evaluate_rc
from .native_project_run import run_native_project
from .native_netlist_validation import validate_native_project_netlist
from .schematic_builder import build_schematic
from .component_compat import detect_multisim_version, load_manifest_for_version, require_verified_mappings


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')


def run_natural_engineering(text: str, output: str, *, execute: bool = False,
                            cancel_requested: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Preview without filesystem/COM mutation; execute in a new directory.

    Acceptance is confined to the supported ideal RC contract and explicitly
    disclosed assumptions. It is not an unrestricted natural-language AI agent.
    """
    proposal = parse_natural_request(text)
    root = Path(output).expanduser().resolve()
    if root.exists() or root == Path(root.anchor):
        raise FileExistsError('output must be a new directory')
    if not execute:
        return normalize_task_result({'success': True, 'mode': 'preview', 'output_dir': str(root), 'proposal': proposal,
                'simulation_started': False, 'verification_status': 'unverified'})
    root.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {'success': False, 'mode': 'execute', 'output_dir': str(root),
                              'verification_status': 'failed', 'proposal': proposal, 'candidates': []}
    _write(root / 'proposal.json', proposal)
    (root / 'input.txt').write_text(text, encoding='utf-8')
    (root / 'source.cir').write_text(proposal['netlist'], encoding='utf-8')
    try:
        multisim_version = detect_multisim_version()
        manifest = load_manifest_for_version(Path(__file__).resolve().parent / "compatibility", multisim_version)
        result["multisim_version"] = multisim_version
        result["component_mappings"] = require_verified_mappings(manifest, multisim_version, {
            "resistor": ["1", "2"], "capacitor": ["1", "2"], "voltage-source": ["-", "+"]})
        from .multisim_client import Ms14Codec
        build = build_schematic(proposal['netlist'], root / 'source.xml', probe_nets=['in', 'out'])
        _write(root / 'build.json', build)
        if build['unsupported'] or build['layout_validation']['status'] != 'pass' or len(build['probes']) != 2:
            raise RuntimeError('generated schematic failed layout/probe preflight')
        Ms14Codec().encode(str(root / 'source.xml'), str(root / 'source.ms14'))
        d = proposal['derived']
        for index, resistance in enumerate(proposal['candidate_resistances_ohm'], 1):
            if cancel_requested and cancel_requested():
                raise RuntimeError('engineering task cancelled before candidate execution')
            name = f'candidate-{index:03d}'
            execution = run_native_project(proposal['request'], str(root / 'source.ms14'), str(root / name),
                                           parameters={'R1': resistance}, execute=True)
            if not execution['success'] or not execution['simulation_completed']:
                raise RuntimeError(f'{name}: native execution failed: {execution.get("error")}')
            topology = validate_native_project_netlist(
                str(root / name), 'analysis-003.ms14', {'R1': {1: 'in', 2: 'out'}})
            _write(root / name / 'topology-acceptance.json', topology)
            if not topology['ok']:
                raise RuntimeError(f'{name}: native netlist topology failed')
            measurement = evaluate_rc(root / name, resistance, capacitance=d['capacitance_f'],
                amplitude=d['input_amplitude_v'], target_hz=d['target_cutoff_hz'], delay=d['delay_s'], rise=d['rise_s'])
            measurement['topology_acceptance'] = topology
            measurement.update(directory=name, project=f'{name}/analysis-003.ms14')
            result['candidates'].append(measurement)
            if cancel_requested and cancel_requested():
                raise RuntimeError('engineering task cancelled after candidate execution')
            if not measurement['passed']:
                raise RuntimeError(f'{name}: native measurements disagree with the RC reference')
        selected = min(result['candidates'], key=lambda c: c['target_error_fraction'])
        result['selected'] = selected
        result['success'] = selected['target_error_fraction'] <= .02
        result['verification_status'] = 'passed-supported-rc-contract' if result['success'] else 'target-not-met'
        if not result['success']:
            result['error'] = {'type': 'AcceptanceFailure', 'message': 'No tested candidate meets the 2% cutoff target; no unverified candidate is substituted.'}
    except Exception as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    result['report'] = str(root / 'report.html')
    result['acceptance'] = str(root / 'acceptance.json')
    _write(root / 'acceptance.json', result)
    _report(root, result)
    return finalize_task_result(root, result)


def _report(root: Path, result: dict[str, Any]) -> None:
    proposal = result['proposal']
    selected = result.get('selected')
    rows = ''.join(f'<tr><td>{c["resistance_ohm"]:g} Ω</td><td>{c["cutoff_hz"]:.4f} Hz</td>'
                   f'<td>{100*c["target_error_fraction"]:.3f}%</td><td>{c["max_transient_error_v"]:.6g} V</td><td>PASS</td>'
                   f'<td><a href="{c["directory"]}/report.html">完整实验</a></td></tr>' for c in result['candidates'])
    assumption_list = ''.join('<li>' + html.escape(a) + '</li>' for a in proposal['assumptions'])
    detail = (f'<p>已测最佳候选：R1={selected["resistance_ohm"]:g} Ω，C1={proposal["derived"]["capacitance_f"]:g} F。'
              f'<a href="{selected["project"]}">原生工程</a></p>'
              f'<img alt="Multisim 原生电路图" src="{selected["directory"]}/schematic.png" style="max-width:100%">' if selected else '')
    text = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>自然语言 RC 工程报告</title>'
            '<style>body{font:16px/1.7 system-ui;max-width:1080px;margin:auto;padding:30px}table{border-collapse:collapse;width:100%}'
            'td,th{padding:10px;border:1px solid #ddd}</style><h1>自然语言 RC 工程报告</h1>'
            f'<p>需求：{html.escape(proposal["text"])}</p><p>验收：{html.escape(result["verification_status"])}</p>'
            '<p>当前入口使用有限规则解析需求。生成 .ms14 后由 Multisim COM 仿真，依据实测频响选值；'
            '解析解只用于验错。本页记录本地执行与测量；模型参与时，其提案及一致性检查见上级实验记录。</p>'
            '<h2>方案假设</h2><ul>' + assumption_list + '</ul>'
            f'<p>截止目标 {proposal["derived"]["target_cutoff_hz"]:g} Hz，允许误差 2%。'
            'AC 小信号为 1 V；输入幅值用于 DC 和脉冲分析。仿真命令和完整激励保存在 proposal.json/source.cir。</p>'
            '<table><tr><th>R1</th><th>实测截止频率</th><th>目标误差</th><th>最大瞬态误差</th><th>原生拓扑</th><th>证据</th></tr>' + rows + '</table>'
            + ('<p>' + html.escape(str(result['error'])) + '</p>' if result.get('error') else '') + detail
            + '<p>验收只针对上述理想 RC 条件及本次实际运行的 Multisim 版本。其它拓扑、器件容差和跨版本结果尚未验证。</p>'
            '<p><a href="proposal.json">需求与计划</a> · <a href="acceptance.json">验收记录</a> · <a href="manifest.json">证据校验清单</a></p></html>')
    (root / 'report.html').write_text(text, encoding='utf-8')
