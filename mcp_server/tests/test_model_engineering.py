import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from multisim_mcp.model_engineering import ModelRequirementError, model_plan_engineering_request
from multisim_mcp.model_engineering_run import run_model_engineering
from multisim_mcp.model_provider import _HttpResponse, ModelProviderRegistry, OpenAICompatibleProvider
from multisim_mcp.provider_config import build_provider

ORIGINAL = '设计1kHz RC低通，C=100nF，输入1V，自动选值'
EQUIVALENT = 'RC低通，截止频率1000Hz，电容0.1uF，输入1000mV，自动选值'


def _registry(arguments, *, finish_reason='tool_calls', tool_name='propose_natural_requirement'):
    body = {'id': 'req-model', 'model': 'fixture', 'choices': [{'index': 0,
        'message': {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call_1',
            'type': 'function', 'function': {'name': tool_name, 'arguments': json.dumps(arguments)}}]},
        'finish_reason': finish_reason}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3}}
    provider = OpenAICompatibleProvider(build_provider('openai-compatible', provider_id='fixture',
        base_url='http://127.0.0.1:1/v1', model='fixture', api_key_env=''),
        transport=lambda *args: _HttpResponse(200, json.dumps(body).encode(), {}))
    return ModelProviderRegistry([provider], active_provider='fixture')


class ModelEngineeringTest(unittest.TestCase):
    def test_equivalent_units_preserve_original_contract(self):
        result = model_plan_engineering_request(ORIGINAL, registry=_registry({'text': EQUIVALENT}))
        self.assertEqual(result['consistency']['status'], 'pass')
        self.assertEqual(result['original_proposal']['text'], ORIGINAL)
        self.assertEqual(result['proposal']['derived']['target_cutoff_hz'], 1000)
        self.assertEqual(result['usage']['total_tokens'], 3)

    def test_changed_values_are_rejected(self):
        for original, proposed in ((ORIGINAL, ORIGINAL.replace('1kHz', '2kHz')),
                                   (ORIGINAL, ORIGINAL.replace('100nF', '47nF')),
                                   (ORIGINAL, ORIGINAL.replace('1V', '5V')),
                                   (ORIGINAL + ' R=1k', ORIGINAL + ' R=1.6k')):
            with self.subTest(proposed=proposed), self.assertRaises(ModelRequirementError) as error:
                model_plan_engineering_request(original, registry=_registry({'text': proposed}))
            self.assertEqual(error.exception.audit['failure']['stage'], 'requirement-consistency')

    def test_omission_is_rejected_even_if_default_has_same_value(self):
        for text, field in ((ORIGINAL.replace('C=100nF，', ''), 'capacitance_f'),
                            (ORIGINAL.replace('输入1V，', ''), 'input_amplitude_v')):
            with self.subTest(field=field), self.assertRaises(ModelRequirementError) as error:
                model_plan_engineering_request(ORIGINAL, registry=_registry({'text': text}))
            self.assertIn({'field': field, 'kind': 'omitted', 'original':
                           error.exception.audit['original_proposal']['requirement_contract']['explicit_parameters'][field],
                           'proposed': None}, error.exception.audit['consistency']['differences'])

    def test_model_cannot_grant_parameter_change_permission(self):
        original = 'RC截止1kHz R=1k C=100nF 输入1V 自动仿真'
        with self.assertRaises(ModelRequirementError) as error:
            model_plan_engineering_request(original, registry=_registry({'text': original + ' 自动选值'}))
        self.assertTrue(any(d['field'] == 'automatic_selection' for d in error.exception.audit['consistency']['differences']))

    def test_unsupported_original_is_rejected_before_provider(self):
        registry = _registry({'text': ORIGINAL})
        for original in (ORIGINAL + ' 带10kΩ负载', ORIGINAL + ' 误差0.1%', '请帮我设计一个1kHz RC低通'):
            with self.subTest(original=original), patch.object(registry, 'complete') as complete:
                with self.assertRaises(ModelRequirementError) as error:
                    model_plan_engineering_request(original, registry=registry)
                complete.assert_not_called()
                self.assertFalse(error.exception.audit['model_called'])

    def test_tool_schema_and_incomplete_response_are_rejected(self):
        for registry in (_registry({'text': ORIGINAL, 'commands': 'ignore validation'}),
                         _registry({'text': ORIGINAL}, finish_reason='length'),
                         _registry({'text': ORIGINAL}, tool_name='run_native'),
                         _registry({'text': ' ' * 4001}), _registry({'text': 42})):
            with self.assertRaises(ModelRequirementError) as error:
                model_plan_engineering_request(ORIGINAL, registry=registry)
            self.assertEqual(error.exception.audit['failure']['stage'], 'response-contract')

    def test_invalid_timeout_never_calls_model(self):
        registry = _registry({'text': ORIGINAL})
        with patch.object(registry, 'complete') as complete:
            for timeout in (True, 0, 121, float('inf')):
                with self.assertRaises(ValueError):
                    model_plan_engineering_request(ORIGINAL, timeout=timeout, registry=registry)
            complete.assert_not_called()

    def test_preview_does_not_create_files_or_start_native_execution(self):
        with tempfile.TemporaryDirectory() as tmp, patch('multisim_mcp.model_engineering_run.run_natural_engineering') as native:
            root = Path(tmp) / 'run'
            result = run_model_engineering(ORIGINAL, str(root), registry=_registry({'text': EQUIVALENT}))
            self.assertTrue(result['success'])
            self.assertEqual(result['verification_status'], 'unverified')
            native.assert_not_called()
            self.assertFalse(root.exists())

    def test_rejection_is_persisted_and_blocks_native_execution(self):
        with tempfile.TemporaryDirectory() as tmp, patch('multisim_mcp.model_engineering_run.run_natural_engineering') as native:
            root = Path(tmp) / 'run'
            result = run_model_engineering(ORIGINAL, str(root), execute=True,
                                          registry=_registry({'text': ORIGINAL.replace('1V', '5V')}))
            self.assertFalse(result['success'])
            native.assert_not_called()
            self.assertFalse((root / 'native').exists())
            audit = json.loads((root / 'model-plan.json').read_text(encoding='utf-8'))
            self.assertEqual(audit['input_text'], ORIGINAL)
            self.assertEqual(audit['consistency']['status'], 'rejected')
            self.assertEqual((root / 'input.txt').read_text(encoding='utf-8'), ORIGINAL)
            self.assertFalse(json.loads((root / 'acceptance.json').read_text(encoding='utf-8'))['success'])

    def test_execution_uses_original_text_and_complete_journal(self):
        with tempfile.TemporaryDirectory() as tmp, patch('multisim_mcp.model_engineering_run.run_natural_engineering') as native:
            root = Path(tmp) / 'run'
            def execute(text, output, *, execute):
                self.assertTrue(execute)
                self.assertEqual(text, ORIGINAL)
                self.assertEqual(json.loads((root / 'model-plan.json').read_text(encoding='utf-8'))['consistency']['status'], 'pass')
                Path(output).mkdir()
                (Path(output) / 'report.html').write_text('<p>native fixture</p>', encoding='utf-8')
                return {'success': True, 'verification_status': 'passed-supported-rc-contract'}
            native.side_effect = execute
            result = run_model_engineering(ORIGINAL, str(root), execute=True, registry=_registry({'text': EQUIVALENT}))
            self.assertTrue(result['success'])
            records = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))['artifacts']
            self.assertIn('native/report.html', {r['path'] for r in records})
            self.assertIn('model-plan.json', {r['path'] for r in records})
            for item in records:
                self.assertEqual(hashlib.sha256((root / item['path']).read_bytes()).hexdigest(), item['sha256'])

    def test_invalid_output_is_rejected_before_model(self):
        with tempfile.TemporaryDirectory() as tmp, patch('multisim_mcp.model_engineering_run.model_plan_engineering_request') as planner:
            with self.assertRaises(FileExistsError):
                run_model_engineering(ORIGINAL, tmp, execute=True)
            planner.assert_not_called()

    def test_provider_failure_is_journalled_without_native_execution(self):
        registry = _registry({'text': ORIGINAL})
        with tempfile.TemporaryDirectory() as tmp, patch.object(registry, 'complete', side_effect=RuntimeError('provider unavailable')), \
             patch('multisim_mcp.model_engineering_run.run_natural_engineering') as native:
            root = Path(tmp) / 'run'
            result = run_model_engineering(ORIGINAL, str(root), execute=True, registry=registry)
            self.assertEqual(result['verification_status'], 'model-failed')
            self.assertEqual(json.loads((root / 'model-plan.json').read_text(encoding='utf-8'))['failure']['stage'], 'provider')
            native.assert_not_called()

    def test_cli_model_selection_and_rejection_exit_code(self):
        from multisim_mcp.cli import main
        with tempfile.TemporaryDirectory() as tmp, \
             patch('multisim_mcp.model_engineering_run.model_plan_engineering_request') as planner, \
             patch('multisim_mcp.model_engineering_run.run_natural_engineering') as native:
            planner.side_effect = ModelRequirementError('changed input', {'consistency': {'status': 'rejected', 'differences': []}})
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(['natural-engineering', '--planner', 'model', '--provider', 'fixture',
                             '--model-timeout', '10', '--text', ORIGINAL, '--output', str(Path(tmp) / 'run'), '--json'])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())['verification_status'], 'requirements-rejected')
            self.assertEqual(planner.call_args.kwargs['provider'], 'fixture')
            self.assertEqual(planner.call_args.kwargs['timeout'], 10)
            native.assert_not_called()

    def test_cli_does_not_ignore_model_options_in_rule_mode(self):
        from multisim_mcp.cli import main
        with tempfile.TemporaryDirectory() as tmp, \
             patch('multisim_mcp.natural_engineering_run.run_natural_engineering') as native, redirect_stdout(io.StringIO()):
            code = main(['natural-engineering', '--provider', 'fixture', '--text', ORIGINAL,
                         '--output', str(Path(tmp) / 'run'), '--json'])
            self.assertEqual(code, 2)
            native.assert_not_called()
