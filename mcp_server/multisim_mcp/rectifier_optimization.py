"""Bounded native capacitor search with fixed loads, explicit corners and saved-file replay."""
from __future__ import annotations

import copy
import csv
import hashlib
import html
import itertools
import json
import math
import re
from pathlib import Path
from typing import Any

from .engineering_task_contract import finalize_task_result, normalize_task_result
from .natural_rectifier import parse_natural_rectifier, validate_rectifier_netlist
from .natural_rectifier_run import run_rectifier_plan

CAPACITORS_UF = (470,680,1000,1500,2200,2700,3300,3900,4700)
CORNERS = tuple({'id':f'vin{v:g}-load{load:g}-cap{cap:g}', 'input_factor':v,'load_fraction':load,'capacitance_factor':cap}
                for v,load,cap in itertools.product((.9,1.1),(.5,1.),(.8,1.2)))
NOMINAL = {'id':'nominal','input_factor':1.,'load_fraction':1.,'capacitance_factor':1.}


def case_plan(base: dict, capacitance_uf: int, corner: dict = NOMINAL) -> dict:
    """Vary only declared case parameters; never redesign RLOAD for a candidate C."""
    plan=copy.deepcopy(base)
    original=base['derived'];derived=plan['derived']
    derived['capacitance_f']=capacitance_uf*1e-6*corner['capacitance_factor']
    derived['ac_rms_v']=original['ac_rms_v']*corner['input_factor']
    derived['ac_peak_v']=round(derived['ac_rms_v']*math.sqrt(2),4)
    derived['load_resistance_ohm']=original['load_resistance_ohm']/corner['load_fraction']
    # Corner load is resistive, so its nominal current varies with input voltage.
    derived['load_a']=original['load_a']*corner['load_fraction']*corner['input_factor']
    netlist=base['proposal']['netlist']
    netlist=re.sub(r'(?m)^C1 .*$',f"C1 vraw 0 {derived['capacitance_f']*1e6:g}u",netlist)
    netlist=re.sub(r'(?m)^RLOAD .*$',f"RLOAD vraw 0 {derived['load_resistance_ohm']:g}",netlist)
    netlist=re.sub(r'(?m)^V1 .*$',f"V1 ac_p ac_n DC 0 SIN(0 {derived['ac_peak_v']:g} {derived['frequency_hz']:g})",netlist)
    plan['proposal']['netlist']=netlist
    checks=plan['proposal']['checks']
    checks[1].update(min=derived['ac_rms_v']*.99,max=derived['ac_rms_v']*1.01)
    checks[4]['max']=derived['ac_peak_v']
    if corner != NOMINAL:
        # One disclosed output envelope for ALL tolerance cases, frozen to baseline.
        for index in (2,5,6):
            checks[index].update(min=original['estimated_loaded_dc_v']*.8,max=original['ac_peak_v']*1.1)
    plan['case']=dict(corner,nominal_capacitance_uf=capacitance_uf)
    validate_rectifier_netlist(plan)
    return plan


def startup_evidence(path: Path, plan: dict, steady_v: float) -> dict:
    with path.open(encoding='utf-8',newline='') as stream:
        rows=list(csv.DictReader(stream))
    points=[(float(r['time_s']),float(r['V(OutProbe2).value'])) for r in rows]
    frequency=plan['derived']['frequency_hz'];stop=20/frequency
    if len(points)<3 or points[0][0] > 1e-9 or points[-1][0]<stop-1e-9 or any(not math.isfinite(x) for p in points for x in p) or any(b[0]<=a[0] for a,b in zip(points,points[1:])):
        raise ValueError('startup evidence must cover zero through the requested duration')
    band=max(abs(steady_v)*.1,plan['derived']['ripple_limit_v'])
    bad=[i for i,(_,v) in enumerate(points) if abs(v-steady_v)>band]
    last=bad[-1] if bad else -1
    settled=points[last+1][0] if last+1<len(points) else None
    maximum=max(v for _,v in points)
    return {'ok':settled is not None and settled<=5/frequency and min(v for _,v in points)>=-.001 and maximum<=plan['derived']['ac_peak_v']*1.01,
            'settling_time_s':settled,'settling_deadline_s':5/frequency,'steady_band_v':band,
            'startup_peak_v':maximum,'scope':'zero-state sampled voltage startup; no surge current or thermal certification'}


def summarize_run(root: Path, folder: Path, plan: dict, result: dict) -> dict:
    measurements=result.get('rectifier_acceptance')
    checks=result.get('measurement_acceptance') or {}
    integrity = all((result.get(key) or {}).get('ok') is True for key in ('model_acceptance','topology_acceptance','presentation_acceptance','waveform_acceptance'))
    if result.get('error') or not integrity or not measurements or len(checks.get('checks',[]))!=7:
        raise RuntimeError('native evidence incomplete; cannot classify candidate as electrically infeasible: '+str(result.get('error')))
    startup=startup_evidence(folder/'native'/'analysis-002'/'data.csv',plan,measurements['output_mean_v'])
    return {'case':plan.get('case',NOMINAL),'path':folder.relative_to(root).as_posix(),
            'passed':result['success'] and startup['ok'], 'measurements':measurements,'startup':startup,
            'failed_checks':[c['requirement'] for c in checks['checks'] if not c['passed']],
            'source_reopen':result.get('reopened_source'),
            'project_sha256':hashlib.sha256((folder/'native'/'circuit.ms14').read_bytes()).hexdigest()}


def build_optimization_plan(text: str) -> dict:
    if re.search(r'(?i)\d\s*(?:uF|µF|μF|微法)',text):
        raise ValueError('capacitor optimization cannot override an explicitly fixed capacitance; omit the fixed value')
    base=parse_natural_rectifier(text)
    return {'baseline':base,'candidate_capacitances_uf':list(CAPACITORS_UF),'corners':list(CORNERS),
            'objective':'minimum nominal capacitance among the declared discrete candidates passing every declared case',
            'frozen_load_resistance_ohm':base['derived']['load_resistance_ohm'],
            'nominal_current_tolerance_fraction':.1,
            'corner_output_mean_range_v':[base['derived']['estimated_loaded_dc_v']*.8,base['derived']['ac_peak_v']*1.1],
            'maximum_native_runs':len(CAPACITORS_UF)*(1+len(CORNERS))+4,
            'limitations':['仅证明列出的离散电容候选和九个工况；不证明连续范围、频率漂移或全局最优。',
                           '工况固定为输入±10%、半载/满载、电容±20%；电阻负载随电压变化，不是恒流负载。',
                           '启动验收为零初始条件的电压建立时间，不包括浪涌电流、源阻抗、温升、ESR、PCB或实物验证。']}


def _comparison_report(root: Path, result: dict) -> None:
    from .spice_raw import plot_svg
    plan=result['optimization_plan']
    rows=[]
    for candidate in result['candidate_results']:
        m=candidate['nominal']['measurements']
        rows.append([str(candidate['capacitance_uf']),f"{m['output_mean_v']:.4f}",f"{m['output_ripple_vpp']:.4f}",
                     f"{m['load_current_a']*1000:.3f}",candidate['status'],candidate['nominal']['path']+'/report.html'])
    with (root/'comparison.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['capacitance_uF','output_mean_V','ripple_Vpp','load_mA','status','report']);writer.writerows(rows)
    winner=result.get('winner')
    charts=''
    if winner:
        series=[]
        for name,folder in [('Baseline','baseline'),('Selected replay','delivery')]:
            with (root/folder/'native'/'analysis-002'/'data.csv').open(encoding='utf-8',newline='') as f:
                data=list(csv.DictReader(f))
            data=[r for r in data if float(r['time_s'])>=plan['baseline']['proposal']['checks'][3]['time_min_s']]
            series.append({'name':name,'x':[float(r['time_s']) for r in data],'y':[float(r['V(OutProbe2).value']) for r in data]})
        plot_svg(str(root/'comparison.svg'),series,title='Baseline vs selected: native sampled output',x_label='Time (s)',y_label='Voltage (V)')
        charts='<img src="comparison.svg" alt="Native comparison"><p><a href="delivery/native/circuit.ms14">优化后原生工程</a> · <a href="delivery/report.html">保存后复验报告</a></p><img src="delivery/native/schematic.png" alt="Selected schematic">'
    table='<table border="1" cellpadding="8"><tr><th>电容 μF</th><th>平均输出 V</th><th>纹波 Vpp</th><th>负载 mA</th><th>验收状态</th><th>报告</th></tr>'
    for row in rows:
        table+='<tr>'+''.join('<td>'+html.escape(c)+'</td>' for c in row[:5])+f'<td><a href="{row[5]}">查看</a></td></tr>'
    case_rows=[]
    for candidate in result['candidate_results']:
        for case in candidate.get('corners',[]):
            case_rows.append(f"<tr><td>{candidate['capacitance_uf']}</td><td>{html.escape(case['case']['id'])}</td><td>{case['measurements']['output_ripple_vpp']:.4f}</td><td>{'通过' if case['passed'] else '未达标'}</td><td><a href=\"{case['path']}/report.html\">证据</a></td></tr>")
    summary={k:result.get(k) for k in ('success','verification_status','nominal_best_uf','winner','optimization','error')}
    document='<!doctype html><html lang="zh"><meta charset="utf-8"><title>整流电容优化报告</title><style>body{font:16px system-ui;max-width:1100px;margin:32px auto;padding:16px}pre{white-space:pre-wrap}img{max-width:100%}table{border-collapse:collapse}</style><h1>原生整流电容优化</h1>'
    document+='<p>所有候选固定同一个标称负载电阻和纹波阈值；角点为输入±10%、半载/满载、电容±20%。工况电流按电阻负载推算，允许相对该工况目标±10%。角点平均输出范围为 '+html.escape(str(plan['corner_output_mean_range_v']))+' V。</p>'
    document+='<pre>'+html.escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>'+table+'</table>'
    document+='<h2>工况验证</h2><p>某个工况失败即可排除候选；剩余未执行工况不会标为通过。标称通过的最小值与全部工况通过的最小值分开报告。</p><table border="1" cellpadding="8"><tr><th>电容 μF</th><th>工况</th><th>纹波 Vpp</th><th>结果</th><th>报告</th></tr>'+''.join(case_rows)+'</table>'
    document+='<p><a href="baseline/native/circuit.ms14">原始工程</a> · <a href="baseline/report.html">基线报告</a> · <a href="comparison.csv">候选数据</a> · <a href="acceptance.json">完整验收记录</a></p>'+charts
    document+='<h2>适用边界</h2><ul>'+''.join('<li>'+html.escape(x)+'</li>' for x in plan['limitations'])+'</ul></html>'
    (root/'report.html').write_text(document,encoding='utf-8')


def optimize_natural_rectifier(text: str, output: str, *, execute: bool = False) -> dict[str, Any]:
    plan=build_optimization_plan(text)
    root=Path(output).expanduser().resolve()
    if root.exists() or root==Path(root.anchor):
        raise FileExistsError('output must be a new directory')
    result={'success':True,'mode':'preview','output_dir':str(root),'verification_status':'unverified',
            'simulation_started':False,'optimization_plan':plan,'candidate_results':[],'winner':None,'nominal_best_uf':None}
    if not execute:
        return normalize_task_result(result)
    root.mkdir(parents=True)
    result.update(success=False,mode='execute',verification_status='failed',native_runs=0)
    base=plan['baseline'];source_variants={}
    def run(p: dict, relative: str, source: Path | None = None, parameters: dict | None = None) -> dict:
        folder=root/relative
        result['native_runs']+=1
        native=run_rectifier_plan(p,str(folder),execute=True,native_source=str(source) if source else None,native_parameters=parameters)
        result['simulation_started']=result['simulation_started'] or bool(native.get('simulation_started'))
        summary=summarize_run(root,folder,p,native)
        (root/'checkpoint.json').write_text(json.dumps({'stage':'acceptance','last_run':relative,'native_runs':result['native_runs']},ensure_ascii=False),encoding='utf-8')
        return summary
    try:
        baseline=run(base,'baseline');result['baseline']=baseline
        nominal_source=root/'baseline'/'native'/'circuit.ms14'
        baseline_c=int(round(base['derived']['capacitance_f']*1e6))
        for capacitance in CAPACITORS_UF:
            p=case_plan(base,capacitance)
            nominal=baseline if capacitance==baseline_c else run(p,f'candidate-{capacitance}/nominal',nominal_source,{'C1':p['derived']['capacitance_f']})
            entry={'capacitance_uf':capacitance,'nominal':nominal,'corners':[],'status':'nominal-failed' if not nominal['passed'] else 'nominal-passed'}
            result['candidate_results'].append(entry)
        eligible=[c for c in result['candidate_results'] if c['nominal']['passed']]
        result['nominal_best_uf']=eligible[0]['capacitance_uf'] if eligible else None
        chosen=None
        for entry in eligible:
            for corner in CORNERS:
                factor=corner['input_factor']
                if factor not in source_variants:
                    source_plan=case_plan(base,baseline_c,dict(NOMINAL,id=f'source-{factor:g}',input_factor=factor))
                    source_run=run(source_plan,f'sources/input-{factor:g}')
                    source_variants[factor]=root/source_run['path']/'native'/'circuit.ms14'
                p=case_plan(base,entry['capacitance_uf'],corner)
                evidence=run(p,f"candidate-{entry['capacitance_uf']}/{corner['id']}",source_variants[factor],
                             {'C1':p['derived']['capacitance_f'],'RLOAD':p['derived']['load_resistance_ohm']})
                entry['corners'].append(evidence)
                if not evidence['passed']:
                    entry['status']='corner-failed';break
            else:
                entry['status']='all-declared-cases-passed';chosen=entry;break
        if chosen is None:
            result.update(verification_status='no-feasible-candidate',error={'type':'TargetNotMet','message':'No candidate passed every declared case'})
        else:
            source=root/chosen['nominal']['path']/'native'/'circuit.ms14'
            p=case_plan(base,chosen['capacitance_uf'])
            replay=run(p,'delivery',source)
            result['delivery_replay']=replay
            unchanged=hashlib.sha256(source.read_bytes()).hexdigest()==chosen['nominal']['project_sha256']
            old=chosen['nominal']['measurements'];new=replay['measurements']
            repeatable=all(abs(old[k]-new[k])<=max(.005,abs(old[k])*.02) for k in ('output_mean_v','output_ripple_vpp'))
            copied=(replay.get('source_reopen') or {}).get('sha256')==chosen['nominal']['project_sha256']
            if not replay['passed'] or not unchanged or not repeatable or not copied:
                raise RuntimeError('saved selected circuit failed replay, identity or repeatability acceptance')
            result.update(success=True,verification_status='passed-declared-cases-and-saved-replay',
                          native_project=str(root/'delivery'/'native'/'circuit.ms14'),
                          winner={'capacitance_uf':chosen['capacitance_uf'],'verified_cases':1+len(chosen['corners']),
                                  'baseline_capacitance_uf':baseline_c,'capacitance_reduction_percent':100*(1-chosen['capacitance_uf']/baseline_c)},
                          optimization={'objective':plan['objective'],'minimality_proven_in_declared_set':True,
                                        'saved_source_unchanged':unchanged,'replay_repeatable':repeatable})
    except Exception as exc:
        result.update(success=False,verification_status='incomplete-native-evidence',winner=None,native_project=None,
                      error={'type':type(exc).__name__,'message':str(exc)})
    result['delivery_status']='requires-visual-review' if result['success'] else 'not-ready'
    result['report']=str(root/'report.html')
    try:
        _comparison_report(root,result)
    except Exception as exc:
        result.update(success=False,verification_status='report-export-failed',winner=None,native_project=None,delivery_status='not-ready',
                      error={'type':type(exc).__name__,'message':str(exc)})
        (root/'report.html').write_text('<!doctype html><meta charset="utf-8"><h1>优化报告导出失败</h1><pre>'+html.escape(str(exc))+'</pre>',encoding='utf-8')
    return finalize_task_result(root,result)
