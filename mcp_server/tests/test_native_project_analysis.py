import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from multisim_mcp.native_project_analysis import analyze_current_project


class NativeProjectAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / 'analysis'
        self.client = Mock()
        self.client.enum_outputs.return_value = ['V(OutProbe)']
        self.client.report_netlist.side_effect = lambda path, *args: Path(path).write_text('connectivity table')
        self.client.run_dc_operating_point.return_value = {
            'ready': True, 'output': 'V(OutProbe)', 'n_points': 1, 'rows': [[0.0], [3.0]]}
        self.action = {'analysis': 'op', 'commands': 'op', 'outputs': ['V(OutProbe)'],
                       'timeout': 10, 'max_points': 200}

    def test_native_matrix_is_preserved_and_csv_uses_value_row(self):
        result = analyze_current_project(self.client, self.action, self.output)
        self.assertEqual(result['signals'], {'V(OutProbe)': [3.0]})
        self.assertEqual(result['execution_backend'], 'native-com')
        self.assertIn('3.0', (self.output / 'data.csv').read_text())
        self.assertEqual(json.loads((self.output / 'native-result.json').read_text())['rows'], [[0.0], [3.0]])
        self.client.run_command_file.assert_not_called()

    def test_export_precedes_output_enumeration(self):
        # Real Multisim invalidates output handles when ReportNetlist runs.
        analyze_current_project(self.client, self.action, self.output)
        names = [item[0] for item in self.client.mock_calls]
        self.assertLess(names.index('report_netlist'), names.index('enum_outputs'))

    def test_native_topology_mismatch_blocks_solver(self):
        self.client.report_netlist.side_effect = lambda path,*args: Path(path).write_text('in design X1 IN+\nwrong design X1 OUT\n')
        with self.assertRaisesRegex(RuntimeError,'pin connectivity'):
            analyze_current_project(self.client,self.action,self.output,
                expected_pins={'X1':{'IN+':'in','OUT':'out'}})
        self.client.enum_outputs.assert_not_called()
        self.client.run_dc_operating_point.assert_not_called()
        self.assertFalse(json.loads((self.output/'topology.json').read_text())['ok'])

    def test_missing_output_rejects_before_simulation(self):
        self.client.enum_outputs.return_value = []
        with self.assertRaisesRegex(ValueError, 'output channels missing'):
            analyze_current_project(self.client, self.action, self.output)
        self.client.run_dc_operating_point.assert_not_called()
        self.client.run_command_file.assert_not_called()

    def test_native_timeout_never_falls_back(self):
        self.client.run_dc_operating_point.return_value = {'ready': False, 'timed_out': True}
        with self.assertRaisesRegex(RuntimeError, 'did not complete'):
            analyze_current_project(self.client, self.action, self.output)
        self.assertFalse((self.output / 'data.csv').exists())
        self.client.run_command_file.assert_not_called()

    def test_unsupported_analysis_does_not_touch_client(self):
        self.action.update(analysis='noise', commands='noise 1u 1m')
        with self.assertRaises(ValueError):
            analyze_current_project(self.client, self.action, self.output)
        self.assertEqual(self.client.mock_calls, [])

    def test_incomplete_or_wrong_output_matrix_is_rejected(self):
        for result in ({'ready': True, 'output': 'V(wrong)', 'rows': [[0], [1]], 'n_points': 1},
                       {'ready': True, 'output': 'V(OutProbe)', 'rows': [[0]], 'n_points': 1}):
            with self.subTest(result=result):
                self.client.run_dc_operating_point.return_value = result
                with self.assertRaises(RuntimeError):
                    analyze_current_project(self.client, self.action, self.output / str(len(result['rows'])))

    def test_ac_uses_native_sweep_api(self):
        self.action.update(analysis='ac', commands='ac lin 2 10 100')
        self.client.run_ac_sweep.return_value = {
            'ready': True, 'results': {'V(OutProbe)': {
                'output': 'V(OutProbe)', 'rows': [[10, 100], [3.0, 0.0], [4.0, -2.0]], 'n_points': 2}}}
        result = analyze_current_project(self.client, self.action, self.output)
        self.assertEqual(result['signals']['V(OutProbe)'], [5.0, 2.0])
        self.assertEqual(result['series']['V(OutProbe)']['phase_deg'][1], -90.0)
        self.assertEqual(self.client.run_ac_sweep.call_args.kwargs['sweep_type'], 2)
        self.assertEqual(len((self.output / 'data.csv').read_text().splitlines()), 3)
        self.client.run_ac_sweep.assert_called_once()

    def test_transient_uses_native_transient_api(self):
        self.action.update(analysis='tran', commands='tran 1u 10u')
        self.client.run_transient_outputs.return_value = {
            'ready': True, 'output': 'V(OutProbe)',
            'rows': [[0, 10e-6], [0.5, 0.6]], 'n_points': 2}
        result = analyze_current_project(self.client, self.action, self.output)
        self.assertEqual(result['signals']['V(OutProbe)'], [0.5, 0.6])
        self.client.run_transient_outputs.assert_called_once()

    def test_rejects_truncated_data_even_if_ready(self):
        self.client.run_dc_operating_point.return_value['n_points'] = 2
        with self.assertRaisesRegex(RuntimeError, 'truncated'):
            analyze_current_project(self.client, self.action, self.output)
        self.assertFalse((self.output / 'data.csv').exists())

    def test_transient_rejects_timeout_inside_channel(self):
        self.action.update(analysis='tran', commands='tran 1u 10u')
        self.client.run_transient_outputs.return_value = {'ready': True, 'results': {
            'V(OutProbe)': {'output': 'V(OutProbe)', 'ready': False, 'timed_out': True}}}
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            analyze_current_project(self.client, self.action, self.output)

    def test_multiple_channels_must_share_axis(self):
        self.action.update(analysis='tran', commands='tran 1u 10u', outputs=['V(OutProbe)', 'I(OutProbe)'])
        self.client.enum_outputs.return_value = self.action['outputs']
        self.client.run_transient_outputs.return_value = {'ready': True, 'results': {
            name: {'output': name, 'rows': [[0, end/2 if name.startswith('V') else end/3, end], [1, 2, 3]], 'n_points': 3}
            for name, end in zip(self.action['outputs'], [10e-6, 10e-6])}}
        with self.assertRaisesRegex(RuntimeError, 'different axes'):
            analyze_current_project(self.client, self.action, self.output)

    def test_optional_transient_flags_are_not_silently_ignored(self):
        self.action.update(analysis='tran', commands='tran 1u 10u 0 uic')
        with self.assertRaises(ValueError):
            analyze_current_project(self.client, self.action, self.output)
        self.assertEqual(self.client.mock_calls, [])


if __name__ == '__main__':
    unittest.main()
