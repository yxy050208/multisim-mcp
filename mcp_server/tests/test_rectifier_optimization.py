"""Optimization must prove feasibility/minimality with native evidence, not ranking guesses."""
import copy
import csv
import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.generated_analog_run import run_generated_analog_project
from multisim_mcp.natural_rectifier import parse_natural_rectifier
from multisim_mcp.rectifier_optimization import CAPACITORS_UF, CORNERS, NOMINAL, build_optimization_plan, case_plan, optimize_natural_rectifier, startup_evidence


class RectifierOptimizationTest(unittest.TestCase):
    text='设计12V交流输入、100mA负载的桥式整流电路，纹波不超过0.3V，尽量减小滤波电容'

    def test_candidates_keep_fixed_load_and_hard_targets(self):
        base=parse_natural_rectifier(self.text)
        for cap in CAPACITORS_UF:
            plan=case_plan(base,cap)
            self.assertEqual(plan['derived']['load_resistance_ohm'],base['derived']['load_resistance_ohm'])
            self.assertEqual(plan['proposal']['checks'],base['proposal']['checks'])
            for corner in CORNERS:
                c=case_plan(base,cap,corner)
                self.assertEqual(c['proposal']['checks'][3]['max'],.3)
                self.assertAlmostEqual(c['derived']['load_resistance_ohm'],base['derived']['load_resistance_ohm']/corner['load_fraction'])
                self.assertAlmostEqual(c['derived']['capacitance_f'],cap*1e-6*corner['capacitance_factor'])
        self.assertEqual(len(CORNERS),8)

    def test_preview_is_side_effect_free_and_rejects_fixed_capacitance(self):
        with tempfile.TemporaryDirectory() as tmp,patch('multisim_mcp.rectifier_optimization.run_rectifier_plan',side_effect=AssertionError('native')):
            out=Path(tmp)/'preview'
            result=optimize_natural_rectifier(self.text,str(out))
            self.assertFalse(out.exists());self.assertFalse(result['simulation_started'])
            self.assertIsNone(result['winner'])
        with self.assertRaises(ValueError):
            build_optimization_plan(self.text+' 3300uF')

    def fake_native(self, *, no_feasible=False, interrupted=False, replay_failure=False, source_tamper=False):
        def native(plan,output,*,execute,native_source=None,native_parameters=None):
            root=Path(output);(root/'native'/'analysis-002').mkdir(parents=True)
            case=plan.get('case',NOMINAL)
            nominal_cap=case.get('nominal_capacitance_uf',round(plan['derived']['capacitance_f']*1e6))
            corner=case['id'].startswith('vin')
            passed=(not no_feasible and nominal_cap >= (3900 if corner else 2700))
            if replay_failure and root.name=='delivery':passed=False
            measurements={'ok':True,'output_mean_v':15.,'output_ripple_vpp':.2 if passed else .4,'load_current_a':.1}
            with (root/'native'/'analysis-002'/'data.csv').open('w',encoding='utf-8',newline='') as f:
                writer=csv.writer(f);writer.writerow(['time_s','V(OutProbe2).value'])
                writer.writerows([(i/2000,15*(1-math.exp(-i/2000/.002))) for i in range(801)])
            (root/'native'/'circuit.ms14').write_bytes(str(plan['derived']).encode())
            (root/'report.html').write_text('Native fixture',encoding='utf-8')
            reads={'sha256':hashlib.sha256(Path(native_source).read_bytes()).hexdigest(),'unchanged':True} if native_source else None
            if source_tamper and root.name=='delivery':Path(native_source).write_bytes(b'changed')
            result={'success':passed,'rectifier_acceptance':measurements,'error':None,'simulation_started':True,
                    'measurement_acceptance':{'checks':[{'passed':passed,'requirement':{'analysis':'tran'}} for _ in range(7)]},'reopened_source':reads}
            for key in ('model_acceptance','topology_acceptance','presentation_acceptance','waveform_acceptance'):
                result[key]={'ok':True}
            if interrupted and root.parent.name=='candidate-470':
                result.update(success=False,error={'type':'TimeoutError','message':'worker stopped'})
            return result
        return native

    def execute_fake(self,root,**options):
        with patch('multisim_mcp.rectifier_optimization.run_rectifier_plan',side_effect=self.fake_native(**options)):
            return optimize_natural_rectifier(self.text,str(root),execute=True)

    def test_smallest_nominal_and_robust_candidates_are_distinct_and_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'opt';r=self.execute_fake(root)
            self.assertTrue(r['success']);self.assertEqual(r['nominal_best_uf'],2700)
            self.assertEqual(r['winner']['capacitance_uf'],3900)
            self.assertEqual(r['winner']['verified_cases'],9)
            self.assertEqual(r['candidate_results'][5]['status'],'corner-failed')
            self.assertEqual(len(r['candidate_results'][5]['corners']),1)
            self.assertEqual(len(r['candidate_results'][7]['corners']),8)
            self.assertEqual(r['candidate_results'][-1]['status'],'nominal-passed')
            self.assertIn('delivery',r['native_project'])
            self.assertEqual(json.loads((root/'acceptance.json').read_text(encoding='utf-8')),r)
            for entry in json.loads((root/'manifest.json').read_text())['artifacts']:
                self.assertEqual(hashlib.sha256((root/entry['path']).read_bytes()).hexdigest(),entry['sha256'])

    def test_no_feasible_candidate_preserves_failed_experiments_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'opt';r=self.execute_fake(root,no_feasible=True)
            self.assertFalse(r['success']);self.assertIsNone(r['winner'])
            self.assertEqual(r['verification_status'],'no-feasible-candidate')
            self.assertEqual(len(r['candidate_results']),len(CAPACITORS_UF))
            self.assertTrue((root/'report.html').is_file())

    def test_missing_evidence_is_not_treated_as_electrical_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=self.execute_fake(Path(tmp)/'opt',interrupted=True)
            self.assertFalse(r['success']);self.assertIsNone(r['winner'])
            self.assertEqual(r['verification_status'],'incomplete-native-evidence')
            self.assertLess(r['native_runs'],len(CAPACITORS_UF))

    def test_report_export_failure_still_persists_failed_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp,patch('multisim_mcp.rectifier_optimization._comparison_report',side_effect=OSError('export failed')):
            root=Path(tmp)/'opt';r=self.execute_fake(root)
            self.assertFalse(r['success']);self.assertIsNone(r['winner'])
            self.assertEqual(r['verification_status'],'report-export-failed')
            self.assertEqual(json.loads((root/'acceptance.json').read_text(encoding='utf-8')),r)

    def test_failed_or_tampered_saved_replay_cannot_deliver_winner(self):
        for options in ({'replay_failure':True},{'source_tamper':True}):
            with self.subTest(options=options),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'opt';r=self.execute_fake(root,**options)
                self.assertFalse(r['success']);self.assertIsNone(r['winner']);self.assertIsNone(r['native_project'])
                self.assertEqual(r['delivery_status'],'not-ready')

    def test_native_parameter_mutations_must_match_proposal_before_any_execution(self):
        proposal=parse_natural_rectifier(self.text)['proposal']
        for params in ({'C1':.001},{'V1':12},{'missing':1}):
            with self.assertRaises(ValueError):
                run_generated_analog_project(proposal,'unused',native_source='missing.ms14',native_parameters=params)

    def test_startup_checks_reject_missing_zero_or_no_settling(self):
        plan=parse_natural_rectifier(self.text)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'data.csv'
            for start in (0,.1):
                with path.open('w',encoding='utf-8',newline='') as f:
                    writer=csv.writer(f);writer.writerow(['time_s','V(OutProbe2).value'])
                    writer.writerows([(start+(0.4-start)*i/100,0) for i in range(101)])
                if start:
                    with self.assertRaises(ValueError):startup_evidence(path,plan,15)
                else:self.assertFalse(startup_evidence(path,plan,15)['ok'])
