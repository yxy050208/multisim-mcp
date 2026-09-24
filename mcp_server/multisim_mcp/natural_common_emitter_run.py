"""Native common-emitter execution with saved-file presentation checks."""
from pathlib import Path
import html
import json

from .engineering_task_contract import finalize_task_result
from .generated_analog_run import run_generated_analog_project
from .native_xml import parse_native_xml
from .natural_common_emitter import parse_natural_common_emitter
from .schematic_builder import parse_netlist, voltage_source_stem


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
    result = run_generated_analog_project(plan['proposal'], output, execute=execute)
    result['natural_language_plan'] = plan
    if not execute:
        return result
    root = Path(result['output_dir'])
    if result['success']:
        result['presentation_acceptance'] = verify_ce_presentation(root/'native-model.xml', plan)
        if not result['presentation_acceptance']['ok']:
            result['success'] = False
            result['verification_status'] = 'native-presentation-mismatch'
    result['delivery_status'] = 'requires-visual-review' if result['success'] else 'not-ready'
    result['limitations'] = plan['assumptions']
    report = root/'report.html'
    if report.is_file():
        report.write_text(report.read_text(encoding='utf-8') + '<h2>工程图文件核查</h2><pre>' + html.escape(json.dumps(
            {'presentation_acceptance':result.get('presentation_acceptance'), 'delivery_status':result['delivery_status']},
            ensure_ascii=False,indent=2)) + '</pre>', encoding='utf-8')
    return finalize_task_result(root, result)
