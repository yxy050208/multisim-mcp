import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import json
from multisim_mcp.natural_engineering import parse_natural_request
from multisim_mcp.natural_engineering_run import run_natural_engineering


class NaturalEngineeringTest(unittest.TestCase):
    def setUp(self):
        detected = patch('multisim_mcp.natural_engineering_run.detect_multisim_version', return_value='14.3')
        detected.start()
        self.addCleanup(detected.stop)
        no_worker = patch('multisim_mcp.com_worker_client.MultisimWorkerProcess._start_locked',
                          side_effect=AssertionError('unit tests must not activate COM'))
        no_worker.start()
        self.addCleanup(no_worker.stop)

    def test_rc_request_becomes_valid_plan_and_netlist(self):
        result = parse_natural_request("设计一个截止频率 1 kHz 的 RC 低通，R=1.5kΩ，C=100nF，输入 1 V")
        self.assertAlmostEqual(result["derived"]["target_cutoff_hz"], 1000)
        self.assertIn("R1 in out 1.5k", result["netlist"])
        self.assertIn("C1 out 0 100n", result["netlist"])
        self.assertEqual(result["plan"]["request"]["schema_version"], 1)

    def test_implicit_cutoff_is_derived(self):
        result = parse_natural_request("RC low-pass R=1k C=100nF")
        self.assertAlmostEqual(result["derived"]["target_cutoff_hz"], 1591.549, places=2)

    def test_unsupported_or_ambiguous_request_fails_closed(self):
        with self.assertRaises(ValueError):
            parse_natural_request("设计一个放大器")
        with self.assertRaises(ValueError):
            parse_natural_request("RC 低通 C=100nF")

    def test_auto_selection_without_named_parts_has_explicit_assumptions(self):
        result = parse_natural_request("设计 1kHz RC 低通，输入 1V，自动选值")
        self.assertEqual(result['candidate_resistances_ohm'], [1500, 1600, 1800])
        self.assertAlmostEqual(result['derived']['capacitance_f'], 100e-9)
        self.assertTrue(any('C1=100 nF' in item for item in result['assumptions']))

    def test_units_and_unsupported_constraints_are_rejected(self):
        for text in ('RC R=1kHz C=100nF', 'RC R=-1k C=100nF',
                     'RC R=1k C=100nF，带负载', 'RC R=1k R=2k C=100nF',
                     'RC R=1k C=1e999F', 'RC 1kHz R=bad 自动选值',
                     'RC 截止1kHz 输入1V 在5kHz衰减40dB 自动选值',
                     'RC 1kHz 自动选值，误差0.1%', 'RC R=1兆欧 C=100nF'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_natural_request(text)

    def test_unit_prefix_case_and_no_implicit_component_changes(self):
        result = parse_natural_request('RC R=1MΩ C=100pF')
        self.assertEqual(result['derived']['resistance_ohm'], 1e6)
        self.assertEqual(result['candidate_resistances_ohm'], [1e6])

    def test_chinese_copula_does_not_hide_input_value(self):
        result = parse_natural_request('RC低通，截止频率为800Hz，输入电压为2.5V，自动选值')
        self.assertEqual(result['derived']['target_cutoff_hz'], 800)
        self.assertEqual(result['derived']['input_amplitude_v'], 2.5)

    def test_automatic_simulation_does_not_authorize_component_changes(self):
        result = parse_natural_request('RC截止1kHz，R=1k，C=100nF，自动仿真并导出报告')
        self.assertFalse(result['automatic_selection'])
        self.assertEqual(result['candidate_resistances_ohm'], [1000])

    def test_preview_has_no_filesystem_or_native_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'new'
            result = run_natural_engineering('1kHz RC低通，输入1V，自动选值', str(root))
            self.assertEqual(result['mode'], 'preview')
            self.assertFalse(root.exists())
            self.assertFalse(result['simulation_started'])

    def test_failed_native_execution_keeps_failed_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, patch('multisim_mcp.natural_engineering_run.build_schematic') as build:
            build.side_effect = RuntimeError('template mapping unavailable')
            root = Path(tmp) / 'new'
            result = run_natural_engineering('1kHz RC低通自动选值', str(root), execute=True)
            self.assertFalse(result['success'])
            self.assertEqual(result['verification_status'], 'failed')
            self.assertIn('template mapping unavailable', result['error']['message'])
            self.assertTrue((root / 'acceptance.json').is_file())

    def test_met_reference_but_missed_target_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('multisim_mcp.natural_engineering_run.build_schematic') as build, \
             patch('multisim_mcp.multisim_client.Ms14Codec') as codec, \
             patch('multisim_mcp.natural_engineering_run.run_native_project') as run, \
             patch('multisim_mcp.natural_engineering_run.validate_native_project_netlist', return_value={'ok': True}), \
             patch('multisim_mcp.natural_engineering_run.evaluate_rc') as measure:
            build.return_value = {'unsupported': [], 'layout_validation': {'status': 'pass'}, 'probes': [{}, {}]}
            def fake_run(request, source, output, **kwargs):
                Path(output).mkdir()
                return {'success': True, 'simulation_completed': True}
            run.side_effect = fake_run
            measure.return_value = {'passed': True, 'resistance_ohm': 1000, 'cutoff_hz': 1591,
                                    'target_error_fraction': .591, 'max_transient_error_v': .001}
            root = Path(tmp) / 'new'
            result = run_natural_engineering('RC截止1kHz R=1k C=100nF', str(root), execute=True)
            self.assertFalse(result['success'])
            self.assertEqual(result['verification_status'], 'target-not-met')
            self.assertEqual(run.call_count, 1)
            self.assertFalse(json.loads((root / 'acceptance.json').read_text(encoding='utf-8'))['success'])
