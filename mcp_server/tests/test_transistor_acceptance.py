import csv
import tempfile
import unittest
from pathlib import Path
from multisim_mcp.natural_common_emitter import parse_natural_common_emitter
from multisim_mcp.transistor_acceptance import validate_common_emitter_netlist, estimate_common_emitter_bias
from multisim_mcp.generated_analog_run import validate_proposal, evaluate_evidence
from multisim_mcp.linear_reference import expected_native_pins, solve_linear
from multisim_mcp.natural_common_emitter_run import run_natural_common_emitter


def proposal():
    return parse_natural_common_emitter('12V单电源增益10倍NPN共射放大器')['proposal']


class TransistorAcceptanceTest(unittest.TestCase):
    def test_input_stimulus_and_gain_are_real_design_inputs(self):
        p = proposal()
        self.assertIn('VCC vcc 0 DC 12\n', p['netlist'])
        self.assertIn('VIN in 0 DC 0 AC 1 PULSE', p['netlist'])
        changed = parse_natural_common_emitter('12V单电源增益20倍NPN共射放大器')
        self.assertNotEqual(p['netlist'], changed['proposal']['netlist'])
        self.assertEqual(validate_common_emitter_netlist(p['netlist'])['pins'], {'C':'collector','B':'base','E':'emitter'})

    def test_reversed_bias_and_reversed_source_are_rejected(self):
        for before, after in [('RBIAS2 base 0','RBIAS2 collector 0'), ('VIN in 0', 'VIN 0 in'), ('DC 12\n','DC 12 AC 1\n')]:
            with self.subTest(before=before), self.assertRaises(ValueError):
                validate_common_emitter_netlist(proposal()['netlist'].replace(before,after))

    def test_vendor_model_cannot_be_replaced_or_overridden(self):
        p = proposal()
        with self.assertRaises(ValueError):
            validate_proposal(dict(p,netlist=p['netlist'].replace('2N3904','BC547')))
        with self.assertRaises(ValueError):
            validate_proposal(dict(p,netlist=p['netlist'].replace('.end','.model 2N3904 NPN(BF=10)\n.end')))

    def test_native_transistor_never_uses_linear_reference(self):
        plan, parts = validate_proposal(proposal())
        self.assertEqual(plan['verification_method'], 'native-vendor-sampled')
        self.assertEqual(expected_native_pins(parts)['Q1'], {'C':'collector','B':'base','E':'emitter'})
        with self.assertRaises(ValueError):
            solve_linear(parts)
        with self.assertRaises(ValueError):
            validate_proposal(dict(proposal(),checks=[]))

    def test_cutoff_or_saturation_bias_is_rejected(self):
        for resistance in ('1', '1Meg'):
            text = 'RBIAS1 vcc base 100k\nRBIAS2 base 0 '+resistance+'\nRC vcc collector 4.7k\nRE emitter 0 1k'
            with self.assertRaises(ValueError):
                estimate_common_emitter_bias(text, 12)
        bias = estimate_common_emitter_bias(proposal()['netlist'],12)
        self.assertGreater(bias['collector_v'],bias['base_v'])
        self.assertIn('approximation',bias['method'])

    def test_preview_has_no_native_or_filesystem_side_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'preview'
            result=run_natural_common_emitter('12V增益10倍共射放大器',str(path))
            self.assertFalse(path.exists())
            self.assertFalse(result['simulation_started'])
            self.assertEqual(result['verification_status'],'unverified')

    def test_native_differential_voltage_and_negative_gain_checks(self):
        plan,parts=validate_proposal(proposal())
        channels = plan['experiments'][0]['outputs']
        # in, out, base, emitter, collector, vcc
        rows = {'op':[[0,0,0,1.2,.54,6,12]],
                'ac':[[1000,1,0,-10,0,0,0,0,0,0,0,0,0]],
                'tran':[[.00115,.001,-.01,1.2,.54,6,12]]}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for index,exp in enumerate(plan['experiments'],1):
                p=root/f'analysis-{index:03d}';p.mkdir()
                fields=[{'op':'op_index','ac':'frequency_hz','tran':'time_s'}[exp['type']]]
                for channel in channels:
                    fields += [channel+'.real',channel+'.imaginary'] if exp['type']=='ac' else [channel+'.value']
                with (p/'data.csv').open('w',newline='',encoding='utf-8') as f:
                    w=csv.writer(f);w.writerow(fields);w.writerows(rows[exp['type']])
            evidence=evaluate_evidence(root,plan,parts)
            # atan2(+0,-10) is +180, equivalent to -180 degrees.
            self.assertTrue(evidence['requirements_verified'])
            self.assertIsNone(evidence['reference_passed'])
            self.assertTrue(evidence['checks'][2]['passed'])
            self.assertTrue(evidence['checks'][-1]['passed'])
            plan['checks'][2]['min']=.8
            self.assertFalse(evaluate_evidence(root,plan,parts)['checks'][2]['passed'])
