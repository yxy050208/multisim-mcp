"""Rectifier regressions for previously ignored values, unsupported models and false evidence."""
import copy
import csv
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.generated_analog_run import transient_statistic, validate_proposal, evaluate_evidence, vendor_model_fingerprints
from multisim_mcp.linear_reference import expected_native_pins
from multisim_mcp.native_xml import parse_native_xml, write_native_xml
from multisim_mcp.natural_rectifier import parse_natural_rectifier, validate_rectifier_netlist
from multisim_mcp.natural_rectifier_run import run_natural_rectifier, verify_rectifier_presentation, rectifier_measurements, verify_rectifier_waveform, electrical_stress_review
from multisim_mcp.schematic_builder import build_schematic, template_search_paths


class RectifierAcceptanceTest(unittest.TestCase):
    def test_load_and_ripple_change_actual_component_values(self):
        a = parse_natural_rectifier('12Vrms 50Hz桥式整流 负载100mA 纹波1V')
        b = parse_natural_rectifier('12Vrms 50Hz桥式整流 负载50mA 纹波0.2V')
        self.assertNotEqual(a['derived']['load_resistance_ohm'],b['derived']['load_resistance_ohm'])
        self.assertGreater(b['derived']['capacitance_f'],a['derived']['capacitance_f'])
        self.assertIn('RLOAD vraw 0 151.37',a['proposal']['netlist'])
        for plan in (a,b):
            validate_rectifier_netlist(plan)
            normalized,parts = validate_proposal(plan['proposal'])
            self.assertEqual(normalized['verification_method'],'native-vendor-sampled')
            self.assertEqual(expected_native_pins(parts)['D1'],{'A':'ac_p','K':'vraw'})
            self.assertEqual(expected_native_pins(parts)['V1'],{1:'ac_p',2:'ac_n'})

    def test_rejects_ignored_and_unsupported_constraints(self):
        for suffix in ('负载0mA','负载-1000mA','负载201mA','0uF','-1000000uF','100uF','47Hz','负载100mA负载0.2A','1N4007','24V','纹波0V','纹波0.01V','输出12V','100kHz','温度85度','12Vpk','稳压5V'):
            with self.subTest(suffix=suffix),self.assertRaises(ValueError):
                parse_natural_rectifier('桥式整流 '+suffix)

    def test_diode_orientation_load_and_stimulus_cannot_drift(self):
        plan = parse_natural_rectifier('桥式整流')
        for before,after in [('D1 ac_p vraw','D1 vraw ac_p'),('151.37','10'),('SIN(0 16.9706 50)','SIN(0 16.9706 60)'),('1N4001GP','1N4007')]:
            changed=copy.deepcopy(plan)
            changed['proposal']['netlist']=changed['proposal']['netlist'].replace(before,after)
            with self.subTest(after=after), self.assertRaises(ValueError):
                validate_rectifier_netlist(changed)

    def test_vendor_definition_and_source_extra_arguments_rejected(self):
        p=parse_natural_rectifier('桥式整流')['proposal']
        for before,after in [('.end','.model 1N4001GP D(IS=1)\n.end'),('SIN(0 16.9706 50)','SIN(0 16.9706 50 1)'),('DC 0 SIN','DC 1 SIN')]:
            with self.subTest(after=after),self.assertRaises(ValueError):
                validate_proposal(dict(p,netlist=p['netlist'].replace(before,after)))

    def test_preview_does_not_build_or_start_worker(self):
        with tempfile.TemporaryDirectory() as tmp,patch('multisim_mcp.generated_analog_run.build_schematic',side_effect=AssertionError('native execution')):
            path=Path(tmp)/'preview'
            r=run_natural_rectifier('桥式整流',str(path))
            self.assertFalse(path.exists())
            self.assertFalse(r['simulation_started'])
            self.assertEqual(r['verification_status'],'unverified')

    def test_report_does_not_keep_success_after_saved_parameter_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'report.html').write_text('<html><pre>{"success": true}</pre></html>',encoding='utf-8')
            base={'success':True,'mode':'execute','output_dir':str(root),'verification_status':'passed-declared-sampled-requirements','report':str(root/'report.html')}
            with patch('multisim_mcp.natural_rectifier_run.run_generated_analog_project',return_value=base),patch('multisim_mcp.natural_rectifier_run.verify_rectifier_presentation',side_effect=ValueError('corrupt native parameters')):
                result=run_natural_rectifier('桥式整流',str(root),execute=True)
            self.assertFalse(result['success'])
            self.assertEqual(result['delivery_status'],'not-ready')
            self.assertNotIn('"success": true',(root/'report.html').read_text(encoding='utf-8'))
            self.assertIn('corrupt native parameters',(root/'report.html').read_text(encoding='utf-8'))

    def test_time_weighted_statistics_on_irregular_grid(self):
        points=[((i/100)**2,(i/100)**2) for i in range(101)]
        self.assertAlmostEqual(transient_statistic(points,.123,.876,'mean')['measured_value'],(.123+.876)/2)
        self.assertAlmostEqual(transient_statistic(points,0,1,'rms')['measured_value'],math.sqrt(1/3))
        self.assertAlmostEqual(transient_statistic(points,.123,.876,'ripple_vpp')['measured_value'],.753)

    def test_truncated_sparse_unsorted_nonfinite_waveforms_rejected(self):
        points=[(i/100,i/100) for i in range(101)]
        for changed in (points[2:],points[:-2],points[::10],list(reversed(points)),points+[(1,1)],[(0,float('nan'))]+points[1:]):
            with self.assertRaises(ValueError):
                transient_statistic(changed,0,1,'mean')

    def test_constant_fake_dc_and_wrong_frequency_do_not_meet_input_waveform(self):
        plan=parse_natural_rectifier('桥式整流')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'data.csv'
            for frequency in (0,50,60):
                with path.open('w',encoding='utf-8',newline='') as f:
                    writer=csv.writer(f);writer.writerow(['time_s','V(OutProbe).value','V(OutProbe1).value'])
                    writer.writerows([(i*.0001,16.9706*math.sin(2*math.pi*frequency*i*.0001),0) for i in range(4001)])
                self.assertEqual(verify_rectifier_waveform(path,plan)['ok'],frequency==50)

    def test_current_and_settling_are_acceptance_conditions(self):
        plan=parse_natural_rectifier('桥式整流')
        checks=[{} for _ in range(7)]
        for i,value in [(1,12),(2,15),(3,.5),(5,15),(6,15)]:
            checks[i]={'measured_value':value}
        result={'measurement_acceptance':{'checks':checks}}
        self.assertTrue(rectifier_measurements(result,plan)['ok'])
        checks[6]['measured_value']=14
        self.assertFalse(rectifier_measurements(result,plan)['ok'])
        checks[6]['measured_value']=15
        checks[2]['measured_value']=12
        self.assertFalse(rectifier_measurements(result,plan)['ok'])

    def test_stress_review_exposes_ratings_without_faking_certification(self):
        plan=parse_natural_rectifier('桥式整流')
        review=electrical_stress_review(plan,{'output_mean_v':15.,'load_current_a':.1})
        self.assertEqual(review['status'],'requires-rated-part-selection')
        self.assertAlmostEqual(review['load_power_w'],1.5)
        self.assertGreater(review['recommended_resistor_power_w'],review['load_power_w'])
        self.assertGreater(review['recommended_capacitor_voltage_v'],plan['derived']['ac_peak_v'])
        self.assertIn('datasheet',review['reason'])
    def test_aggregate_checks_use_native_csv_and_reject_excess_ripple(self):
        raw=parse_natural_rectifier('桥式整流')['proposal']
        plan,parts=validate_proposal(raw)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for index,experiment in enumerate(plan['experiments'],1):
                directory=root/f'analysis-{index:03d}';directory.mkdir()
                kind=experiment['type']
                with (directory/'data.csv').open('w',encoding='utf-8',newline='') as f:
                    writer=csv.writer(f);writer.writerow([{'op':'op_index','tran':'time_s'}[kind]]+[s+'.value' for s in experiment['outputs']])
                    writer.writerows([[0,0,0,0]] if kind=='op' else [(i/10000,16.9706*math.sin(2*math.pi*50*i/10000),0,15+math.sin(2*math.pi*100*i/10000)) for i in range(4001)])
            evidence=evaluate_evidence(root,plan,parts)
            self.assertFalse(evidence['requirements_verified'])
            self.assertFalse(evidence['checks'][3]['passed'])
            self.assertIsNone(evidence['reference_passed'])


class RectifierNativeCarrierTest(unittest.TestCase):
    def setUp(self):
        if not any((p/'d_model.xml').is_file() for p in template_search_paths()):
            self.skipTest('requires local licensed diode model pack')
        self.plan=parse_natural_rectifier('桥式整流')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'source.xml'
        self.build=build_schematic(self.plan['proposal']['netlist'],self.path,probe_nets=self.plan['proposal']['probe_nets'])

    def test_geometry_and_native_parameter_tables(self):
        self.assertEqual(self.build['layout_profile'],'bridge_rectifier')
        self.assertEqual(self.build['layout_validation']['status'],'pass')
        self.assertTrue(verify_rectifier_presentation(self.path,self.plan)['ok'])

    def test_correct_label_cannot_mask_wrong_resistor_capacitor_or_frequency(self):
        for ref,index in [('RLOAD',1),('C1',1),('V1',5)]:
            with self.subTest(ref=ref):
                tree=parse_native_xml(self.path)
                c=next(c for c in tree.getroot().iter('CiComponent') if c.get('LocalName')=='&ASC'+ref)
                parameter=c.findall('.//CiaParamList/doubles/Item')[index]
                old=parameter.get('Value');parameter.set('Value','10000')
                write_native_xml(tree,self.path)
                self.assertFalse(verify_rectifier_presentation(self.path,self.plan)['ok'])
                parameter.set('Value',old);write_native_xml(tree,self.path)

    def test_missing_linked_diode_model_fails(self):
        _,parts=validate_proposal(self.plan['proposal'])
        self.assertEqual(len(vendor_model_fingerprints(self.path,parts)),4)
        tree=parse_native_xml(self.path)
        c=next(c for c in tree.getroot().iter('CiComponent') if c.get('LocalName')=='&ASCD1')
        c.set('Model','missing');write_native_xml(tree,self.path)
        with self.assertRaises(ValueError):
            vendor_model_fingerprints(self.path,parts)

    def test_native_effective_value_resolves_stale_cache_but_not_wrong_parameter(self):
        tree=parse_native_xml(self.path)
        c=next(c for c in tree.getroot().iter('CiComponent') if c.get('LocalName')=='&ASCC1')
        c.findall('.//CiaParamList/doubles/Item')[1].set('Value','0.0047')
        write_native_xml(tree,self.path)
        values={'C1':self.plan['derived']['capacitance_f'],'RLOAD':self.plan['derived']['load_resistance_ohm']}
        self.assertFalse(verify_rectifier_presentation(self.path,self.plan)['ok'])
        effective=verify_rectifier_presentation(self.path,self.plan,values)
        self.assertTrue(effective['ok']);self.assertIn('C1',effective['cached_double_differences'])
        self.assertFalse(verify_rectifier_presentation(self.path,self.plan,dict(values,C1=.0047))['ok'])
        c.findall('.//CiaParamList/parameters/Item')[1].set('Value','&ASC4.7m')
        write_native_xml(tree,self.path)
        self.assertFalse(verify_rectifier_presentation(self.path,self.plan,values)['ok'])
