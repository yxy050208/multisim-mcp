import unittest
from unittest.mock import Mock, call

from multisim_mcp.multisim_client import MultisimClient


class TransientOutputsTest(unittest.TestCase):
    def test_registers_all_outputs_before_one_run_and_cleans_up(self):
        client = MultisimClient()
        circuit = Mock(SimulationState=0)
        client._circuit = circuit
        client._collect_analysis_outputs = Mock(return_value={'ready': True})
        self.assertTrue(client.run_transient_outputs(['V(a)', 'V(b)'], 1000, 11, .01)['ready'])
        calls = circuit.method_calls
        run_index = next(i for i, item in enumerate(calls) if item[0] == 'RunSimulation')
        self.assertEqual(sum(item[0] == 'SetOutputRequest' for item in calls[:run_index]), 2)
        circuit.RunSimulation.assert_called_once_with(.01, False)
        self.assertEqual(calls[-2:], [call.ClearOutputRequest('V(a)'), call.ClearOutputRequest('V(b)')])

    def test_collect_failure_still_clears_requests(self):
        client = MultisimClient()
        client._circuit = Mock(SimulationState=0)
        client._collect_analysis_outputs = Mock(side_effect=RuntimeError('failed'))
        with self.assertRaises(RuntimeError):
            client.run_transient_outputs(['V(a)'], 1000, 11, .01)
        self.assertEqual(client._circuit.method_calls[-1], call.ClearOutputRequest('V(a)'))
