import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from multisim_mcp.engineering_task_contract import finalize_task_result, normalize_task_result


class RunnerContractTest(unittest.TestCase):
    CASES = (
        ('natural_engineering_run', 'run_natural_engineering', 'RC R=1k C=100nF'),
        ('natural_rlc_run', 'run_natural_rlc_engineering', 'design 1kHz RLC low-pass, L=10mH, C=2.5uF, automatic select'),
        ('natural_opamp_run', 'run_natural_opamp_engineering', '设计增益 11 的非反相运放'),
    )

    def check_envelope(self, result):
        for key in ('task_id', 'stage', 'proposal', 'native_project', 'topology_acceptance',
                    'measurement_acceptance', 'optimization', 'artifacts', 'error'):
            self.assertIn(key, result)

    def test_all_previews_are_side_effect_free(self):
        for module, function, text in self.CASES:
            with self.subTest(module=module), tempfile.TemporaryDirectory() as tmp:
                mod = importlib.import_module('multisim_mcp.' + module)
                root = Path(tmp) / 'preview'
                with patch.object(mod, 'build_schematic', side_effect=AssertionError('unexpected build')):
                    result = getattr(mod, function)(text, str(root))
                self.check_envelope(result)
                self.assertEqual(result['stage'], 'preview')
                self.assertIsNone(result['measurement_acceptance'])
                self.assertFalse(root.exists())

    def test_all_build_failures_persist_same_envelope_and_valid_hashes(self):
        for module, function, text in self.CASES:
            with self.subTest(module=module), tempfile.TemporaryDirectory() as tmp:
                mod = importlib.import_module('multisim_mcp.' + module)
                root = Path(tmp) / 'failed'
                with patch.object(mod, 'detect_multisim_version', return_value='14.3'), \
                     patch.object(mod, 'build_schematic', side_effect=RuntimeError('injected failure')):
                    result = getattr(mod, function)(text, str(root), execute=True)
                self.check_envelope(result)
                self.assertEqual(result['stage'], 'failed')
                self.assertEqual(result, json.loads((root / 'acceptance.json').read_text(encoding='utf-8')))
                self.assertIn('injected failure', result['error']['message'])
                self.assertIsNone(result['measurement_acceptance'])
                for item in json.loads((root / 'manifest.json').read_text(encoding='utf-8'))['artifacts']:
                    self.assertEqual(item['sha256'], hashlib.sha256((root / item['path']).read_bytes()).hexdigest())

    def test_terminal_success_replaces_last_stage_and_preserves_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'checkpoint.json').write_text('{}')
            result = finalize_task_result(root, {
                'mode': 'execute', 'success': True, 'output_dir': str(root),
                'verification_status': 'passed-supported-opamp-contract',
                'stage': 'native-netlist', 'acceptance': {'passed': True, 'measured_gain': 11.0},
            })
            self.assertEqual(result['stage'], 'complete')
            self.assertEqual(result['last_stage'], 'native-netlist')
            self.assertEqual(result['measurement_acceptance']['measured_gain'], 11.0)
            self.assertEqual(json.loads((root / 'checkpoint.json').read_text())['stage'], 'complete')

    def test_path_is_not_measurement_data(self):
        result = normalize_task_result({'mode': 'execute', 'success': False, 'output_dir': 'x',
                                       'verification_status': 'failed', 'acceptance': 'x/acceptance.json'})
        self.assertIsNone(result['measurement_acceptance'])
