"""Regression cases that vary topology, wiring obstacles and acceptance targets."""
import cmath
import csv
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.generated_analog_run import evaluate_evidence, run_generated_analog_project, validate_proposal
from multisim_mcp.linear_reference import expected_native_pins, solve_linear, validated_components
from multisim_mcp.layout_validation import validate_schematic_geometry
from multisim_mcp.native_netlist_validation import validate_native_netlist
from multisim_mcp.orthogonal_routing import crosses_box, route, route_pins, segment_relation
from multisim_mcp.schematic_builder import _pick_probe_point, build_schematic, template_search_paths

ROOT = Path(__file__).resolve().parents[2]


def example():
    return json.loads((ROOT/'examples/generated-analog/two-stage-lowpass.json').read_text())


class RouterTests(unittest.TestCase):
    def test_aligned_endpoints_route_around_blocker(self):
        box={'refdes':'R1','x':40,'y':-10,'width':20,'height':20}
        path=route((0,0),(100,0),[box])
        self.assertTrue(all(not crosses_box(a,b,(40,-10,60,10)) for a,b in zip(path,path[1:])))
        self.assertEqual((path[0],path[-1]),((0,0),(100,0)))

    def test_unreachable_endpoint_is_not_returned_as_crossing(self):
        with self.assertRaisesRegex(ValueError,'inside symbol'):
            route((50,0),(100,0),[{'x':40,'y':-10,'width':20,'height':20}])

    def test_left_pin_escapes_left_even_when_destination_is_right(self):
        path=route_pins({'refdes':'U1','x':18,'y':36},{'x':200,'y':36},[{'refdes':'U1','x':0,'y':0}])
        self.assertLess(path[1][0],18)
        result=validate_schematic_geometry([{'refdes':'U1','x':0,'y':0}],{'n':[path]},pin_points={'n':[(18,36),(200,36)]})
        self.assertEqual(result['status'],'pass',result)

    def test_validator_rejects_wrong_side_pin_exit_and_shared_foreign_wire(self):
        result=validate_schematic_geometry([{'refdes':'U1','x':0,'y':0}],{'n':[[(18,36),(200,36)]]},pin_points={'n':[(18,36),(200,36)]})
        self.assertEqual(result['status'],'fail')
        result=validate_schematic_geometry([],{'a':[[(0,0),(30,0)]],'b':[[(10,0),(40,0)]]})
        self.assertIn('different-nets-overlap',{f['code'] for f in result['findings']})

    def test_probe_uses_pin_not_rightmost_bend(self):
        self.assertEqual(_pick_probe_point([[(0,0),(100,0),(100,30),(30,30)]],[(0,0),(30,30)]),(30,30))


class ReferenceTests(unittest.TestCase):
    def test_two_active_poles_against_closed_form_complex_transfer(self):
        parts=validated_components(example()['netlist'])
        k1,k2=2/(1+2e-5),5/(1+5e-5)
        for f in (10,100,1591.54943,10000,100000):
            values=solve_linear(parts,f)
            expected=k1*k2/(1+2j*math.pi*f*1e-4)**2
            self.assertLess(abs(values['out']-expected),1e-9)
        self.assertAlmostEqual(solve_linear(parts)['out'].real,.1*k1*k2)

    def test_loaded_passive_ladder_requires_coupled_nodal_solution(self):
        p=validated_components('V1 in 0 DC 6 AC 1\nR1 in a 1k\nR2 a out 1k\nR3 out 0 1k\nC1 a 0 1u\n.end')
        dc=solve_linear(p)
        self.assertAlmostEqual(dc['out'].real,2)
        self.assertAlmostEqual(dc['a'].real,4)
        f=200
        self.assertLess(abs(solve_linear(p,f)['out']-1/(3+2j*math.pi*f*.002)),1e-12)

    def test_rlc_branch_dc_and_ac(self):
        p=validated_components('V1 in 0 DC -2 AC 1 90\nR1 in a 10\nL1 a out 10m\nC1 out 0 1u\n.end')
        self.assertAlmostEqual(solve_linear(p)['out'].real,-2)
        f=1000;s=2j*math.pi*f
        self.assertLess(abs(solve_linear(p,f)['out']-1j/(1+10e-6*s+1e-8*s*s)),1e-10)

    def test_singular_and_vendor_models_rejected_before_execution(self):
        with self.assertRaisesRegex(ValueError,'singular'):
            solve_linear(validated_components('V1 in 0 DC 1\nR1 a b 1k\n.end'))
        q=example();q['netlist']=q['netlist'].replace('OPAMP5','LM741')
        with self.assertRaisesRegex(ValueError,'vendor'):
            validate_proposal(q)


class NativeTopologyTests(unittest.TestCase):
    def test_named_opamp_pins_and_missing_supply_rejected(self):
        table='in design XU1 IN+\nfb design XU1 IN-\nout design XU1 OUT\nvcc design XU1 VS+\n'
        expected={'XU1':{'IN+':'in','IN-':'fb','OUT':'out','VS+':'vcc','VS-':'vss'}}
        self.assertFalse(validate_native_netlist(table,expected,strict=True)['ok'])
        self.assertTrue(validate_native_netlist(table+'vss design XU1 VS-\n',expected,strict=True)['ok'])

    def test_duplicate_and_unexpected_pin_fail(self):
        self.assertFalse(validate_native_netlist('a d R1 1\nb d R1 1\n',{'R1':{1:'a'}},strict=True)['ok'])
        self.assertFalse(validate_native_netlist('a d R1 1\nb d R1 2\n',{'R1':{1:'a'}},strict=True)['ok'])


class GeneratedWorkflowTests(unittest.TestCase):
    def test_unmeasured_band_is_rejected_before_native_execution(self):
        q=example();q['checks'][-1]['frequency_max_hz']=1e9
        with self.assertRaisesRegex(ValueError,'inside the requested native sweep'):
            validate_proposal(q)

    def test_preview_has_no_com_or_output_side_effect(self):
        with tempfile.TemporaryDirectory() as tmp, patch('multisim_mcp.generated_analog_run.build_schematic',side_effect=AssertionError('build not expected')):
            target=Path(tmp)/'preview'
            result=run_generated_analog_project(example(),str(target))
            self.assertTrue(result['success'])
            self.assertFalse(target.exists())

    def test_geometry_and_export_dimensions_for_composed_design(self):
        if not any((p/'minimal.ms14.xml').is_file() for p in template_search_paths()):
            self.skipTest('requires licensed local template pack')
        import xml.etree.ElementTree as ET
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.xml'
            b=build_schematic(example()['netlist'],path,probe_nets=['in','stage1','out'])
            self.assertEqual(b['layout_validation']['status'],'pass',b['layout_validation'])
            self.assertEqual(b['counts']['components'],15)
            for probe in b['probes']:
                self.assertIn((probe['x'],probe['y']),b['geometry']['pins'][probe['net']])
            settings={e.get('Key'):e.find('Item').get('Value') for e in ET.parse(path).findall('.//CircPrefs/CIITCircuitPrefs/Settings/Element') if e.find('Item') is not None}
            self.assertGreater(float(settings['&ASCSheet Width'].removeprefix('&ASC')),960)
            self.assertGreater(float(settings['&ASCSheet Height'].removeprefix('&ASC')),720)

    def test_target_miss_and_corrupted_phase_do_not_pass(self):
        plan,parts=validate_proposal(example())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for i,exp in enumerate(plan['experiments'],1):
                folder=root/f'analysis-{i:03d}';folder.mkdir()
                freqs=[None] if exp['type']=='op' else [10,100,10000,100000]
                with (folder/'data.csv').open('w',newline='') as out:
                    writer=csv.writer(out)
                    fields=['value'] if exp['type']=='op' else ['real','imaginary']
                    writer.writerow(['op_index' if exp['type']=='op' else 'frequency_hz']+[f'{s}.{f}' for s in exp['outputs'] for f in fields])
                    for freq in freqs:
                        values=solve_linear(parts,freq)
                        writer.writerow([freq or 0]+[v for n in plan['probe_nets'] for v in ([values[n].real] if freq is None else [values[n].real,values[n].imag])])
            accepted=evaluate_evidence(root,plan,parts)
            self.assertTrue(accepted['requirements_verified'])
            plan['checks'][0]['min']=2
            self.assertFalse(evaluate_evidence(root,plan,parts)['requirements_verified'])
            ac=root/'analysis-002/data.csv'
            with ac.open(newline='') as stream:
                rows=list(csv.reader(stream))
            rows[-1][-1]=str(-float(rows[-1][-1]))
            with ac.open('w',newline='') as out:csv.writer(out).writerows(rows)
            self.assertFalse(evaluate_evidence(root,plan,parts)['reference_passed'])


if __name__=='__main__':unittest.main()
