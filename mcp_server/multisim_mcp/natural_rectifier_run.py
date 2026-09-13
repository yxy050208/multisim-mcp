"""Native bridge execution with saved parameter, steady-state and load checks."""
import html
import csv
import json
import math
import re
from pathlib import Path

from .engineering_task_contract import finalize_task_result
from .generated_analog_run import run_generated_analog_project
from .native_xml import parse_native_xml
from .natural_rectifier import parse_natural_rectifier, validate_rectifier_netlist


def verify_rectifier_presentation(path: Path, plan: dict, native_values: dict | None = None) -> dict:
    root = parse_native_xml(path).getroot()
    components = {c.get('LocalName','').removeprefix('&ASC'):c for c in root.iter('CiComponent')}
    derived = plan['derived']
    checks = {}
    cache_differences = {}
    expected = {'V1':{1:derived['ac_peak_v'],3:0.,5:derived['frequency_hz'],7:0.,9:0.,11:0.},
                'RLOAD':{1:derived['load_resistance_ohm']}, 'C1':{1:derived['capacitance_f']}}
    for ref,parameters in expected.items():
        component = components.get(ref)
        values = component.findall('.//CiaParamList/doubles/Item') if component is not None else []
        checks[ref] = all(index<len(values) and math.isclose(float(values[index].get('Value')),value,rel_tol=1e-9,abs_tol=1e-12)
                          for index,value in parameters.items())
        if ref in {'RLOAD','C1'} and native_values is not None:
            from .linear_reference import scalar
            strings=component.findall('.//CiaParamList/parameters/Item') if component is not None else []
            expected_value=parameters[1]
            if not checks[ref]:
                cache_differences[ref]={'cached_double':float(values[1].get('Value')) if len(values)>1 else None,'expected':expected_value}
            checks[ref] = (ref in native_values and math.isclose(native_values[ref],expected_value,rel_tol=1e-9,abs_tol=1e-12)
                           and len(strings)>1 and math.isclose(scalar(strings[1].get('Value','').removeprefix('&ASC')),expected_value,rel_tol=1e-9,abs_tol=1e-12))
    source = components.get('V1')
    names = source.findall('./Attributes/Item/CiaCollString/strings/Item') if source is not None else []
    checks['source_identity'] = len(names)>1 and names[1].get('Value') == '&ASCAC_VOLTAGE'
    expression = source.find('.//CiaSpiceTmpltExprt') if source is not None else None
    checks['source_expression'] = expression is not None and expression.get('String') == '&ASCv%p %t1 %t2 dc #3 sin(#3 #1 #5 #7 #9 #11)'
    probes = list(root.iter('CIITProbeExtComponent'))
    checks['probe_panels_hidden'] = len(probes)==len(plan['proposal']['probe_nets']) and all(p.get('Hidden')=='1' and p.get('ShowInfo')=='0' for p in probes)
    return {'ok':all(checks.values()),'checks':checks,'cached_double_differences':cache_differences,
            'scope':'saved native parameter strings plus native RLCValue readback when available; otherwise strict cached doubles; sine and probe checks; visual review is separate'}


def rectifier_measurements(result: dict, plan: dict) -> dict:
    checks = result['measurement_acceptance']['checks']
    mean = checks[2]['measured_value']
    current = mean/plan['derived']['load_resistance_ohm']
    delta = abs(checks[5]['measured_value']-checks[6]['measured_value'])
    target = plan['derived']['load_a']
    return {'ok':abs(current-target)<=target*.10 and delta<=max(.01,abs(mean)*.002),
            'input_rms_v':checks[1]['measured_value'],'output_mean_v':mean,
            'output_ripple_vpp':checks[3]['measured_value'],
            'load_current_a':current,'load_current_method':'native output voltage / saved validated resistive load',
            'load_target_a':target,'load_tolerance_fraction':.10,
            'adjacent_window_mean_delta_v':delta,'settling_limit_v':max(.01,abs(mean)*.002)}


def electrical_stress_review(plan: dict, measurements: dict) -> dict:
    derived=plan['derived']
    # These are design review values, not claims about unmodeled ratings.
    actual_power=measurements['output_mean_v']*measurements['load_current_a']
    return {'status':'requires-rated-part-selection',
            'load_power_w':actual_power,
            'recommended_resistor_power_w':derived['recommended_resistor_power_w'],
            'recommended_capacitor_voltage_v':derived['recommended_capacitor_voltage_v'],
            'estimated_bridge_piv_v':derived['estimated_bridge_piv_v'],
            'diode_model':derived['diode_model'],
            'checks':{'load_power_is_finite':math.isfinite(actual_power),
                      'resistor_rating_required':derived['recommended_resistor_power_w'],
                      'capacitor_voltage_rating_required':derived['recommended_capacitor_voltage_v'],
                      'diode_piv_required':derived['estimated_bridge_piv_v']},
            'reason':'Native model does not expose purchased resistor power, capacitor voltage/ESR or diode surge/PIV ratings; select parts from a datasheet before hardware use.'}


def verify_rectifier_waveform(path: Path, plan: dict) -> dict:
    with path.open(encoding='utf-8',newline='') as stream:
        rows = list(csv.DictReader(stream))
    derived = plan['derived']
    times = [float(row['time_s']) for row in rows]
    voltages = [float(row['V(OutProbe).value'])-float(row['V(OutProbe1).value']) for row in rows]
    if len(times)<3 or any(not math.isfinite(x) for x in times+voltages) or any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError('invalid native rectifier waveform samples')
    error = max(abs(v-derived['ac_peak_v']*math.sin(2*math.pi*derived['frequency_hz']*t)) for t,v in zip(times,voltages))
    gap = max(b-a for a,b in zip(times,times[1:]))
    return {'ok':error<=1e-5 and gap<=1/(derived['frequency_hz']*20)*(1+1e-9),
            'source_fit_max_error_v':error,'max_raw_time_gap_s':gap,
            'max_allowed_time_gap_s':1/(derived['frequency_hz']*20),
            'scope':'raw differential input matches declared sine polarity/amplitude/frequency; sampled ripple remains resolution-limited'}


def export_rectifier_summary(root: Path, result: dict, plan: dict) -> str:
    """Readable engineering summary and plots derived only from the native CSV."""
    from .spice_raw import plot_svg
    with (root/'native'/'analysis-002'/'data.csv').open(encoding='utf-8',newline='') as stream:
        rows = list(csv.DictReader(stream))
    times = [float(row['time_s']) for row in rows]
    inputs = [float(row['V(OutProbe).value'])-float(row['V(OutProbe1).value']) for row in rows]
    outputs = [float(row['V(OutProbe2).value']) for row in rows]
    plot_svg(str(root/'waveform.svg'),[{'name':'AC input (differential)','x':times,'y':inputs},{'name':'DC output','x':times,'y':outputs}],
             title='Native Multisim bridge transient',x_label='Time (s)',y_label='Voltage (V)')
    start = plan['proposal']['checks'][3]['time_min_s']
    selected = [(t,v) for t,v in zip(times,outputs) if t>=start]
    plot_svg(str(root/'ripple.svg'),[{'name':'Output','x':[t for t,_ in selected],'y':[v for _,v in selected]}],
             title='Output ripple: raw solver samples',x_label='Time (s)',y_label='Voltage (V)')
    measured=result['rectifier_acceptance'];derived=plan['derived']
    items=[('输入有效值',f"{derived['ac_rms_v']:g} V",f"{measured['input_rms_v']:.4f} V"),
           ('输出平均电压','按桥式整流及压降估算',f"{measured['output_mean_v']:.4f} V"),
           ('输出峰峰纹波',f"≤ {derived['ripple_limit_v']:g} V",f"{measured['output_ripple_vpp']:.4f} V"),
           ('电阻负载电流',f"{derived['load_a']*1000:g} mA ±10%",f"{measured['load_current_a']*1000:.3f} mA"),
           ('相邻稳态窗口均值差',f"≤ {measured['settling_limit_v']:.4f} V",f"{measured['adjacent_window_mean_delta_v']:.6f} V")]
    table='<h2>实验结果</h2><table border="1" cellpadding="8"><tr><th>指标</th><th>要求 / 说明</th><th>原生结果</th></tr>'
    table+=''.join('<tr>'+''.join('<td>'+html.escape(cell)+'</td>' for cell in row)+'</tr>' for row in items)+'</table>'
    table+='<p>负载电流由原生测得的输出电压除以保存后核验的电阻得到；非独立电流探针测量。原始求解器时间点不均匀，平均值按时间积分，纹波为采样极值。</p>'
    table+='<p>电容 '+f"{derived['capacitance_f']*1e6:g} μF；负载电阻 {derived['load_resistance_ohm']:g} Ω。"+'</p>'
    table+='<img src="waveform.svg" alt="Native transient"><img src="ripple.svg" alt="Native ripple">'
    return table


def run_natural_rectifier(text: str, output: str, *, execute: bool = False) -> dict:
    plan = parse_natural_rectifier(text)
    return run_rectifier_plan(plan,output,execute=execute)


def run_rectifier_plan(plan: dict, output: str, *, execute: bool = False,
                       native_source: str | None = None, native_parameters: dict | None = None) -> dict:
    validate_rectifier_netlist(plan)
    options = {} if native_source is None else {'native_source':native_source,'native_parameters':native_parameters}
    result = run_generated_analog_project(plan['proposal'],output,execute=execute,**options)
    result['natural_language_plan'] = plan
    if not execute:
        return result
    root = Path(result['output_dir'])
    summary = ''
    if (result.get('measurement_acceptance') or {}).get('checks') or result['success']:
        try:
            values=None
            if native_source is not None:
                readings=[json.loads((root/'native'/f'analysis-{i:03d}'/'native-parameters.json').read_text(encoding='utf-8')) for i in (1,2)]
                if readings[0] != readings[1]:
                    raise ValueError('native RLC values changed between analyses')
                values=readings[0]
                result['native_parameter_readback']=values
            result['presentation_acceptance'] = verify_rectifier_presentation(root/'native-model.xml',plan,values)
            result['rectifier_acceptance'] = rectifier_measurements(result,plan)
            result['electrical_stress_review'] = electrical_stress_review(plan,result['rectifier_acceptance'])
            result['waveform_acceptance'] = verify_rectifier_waveform(root/'native'/'analysis-002'/'data.csv',plan)
            summary = export_rectifier_summary(root,result,plan)
            if not all(result[key]['ok'] for key in ('presentation_acceptance','rectifier_acceptance','waveform_acceptance')):
                result.update(success=False,verification_status='rectifier-contract-not-met')
        except Exception as exc:
            result.update(success=False,verification_status='rectifier-contract-not-met',error={'type':type(exc).__name__,'message':str(exc)})
    result['delivery_status'] = 'requires-visual-review' if result['success'] else 'not-ready'
    result['limitations'] = plan['assumptions']
    report = root/'report.html'
    if report.is_file():
        final_summary = html.escape(json.dumps({key:result.get(key) for key in ('success','verification_status','error')},ensure_ascii=False,indent=2))
        report_text = re.sub(r'<pre>.*?</pre>',lambda _:summary+'<pre>'+final_summary+'</pre>',report.read_text(encoding='utf-8'),count=1,flags=re.S)
        report.write_text(report_text + '<h2>整流电源验收</h2><pre>' + html.escape(json.dumps(
            {key:result.get(key) for key in ('rectifier_acceptance','electrical_stress_review','presentation_acceptance','waveform_acceptance','delivery_status','limitations')},
            ensure_ascii=False,indent=2)) + '</pre>',encoding='utf-8')
    return finalize_task_result(root,result)
