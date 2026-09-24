import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from multisim_mcp.native_action_plan import execute_native_action_plan, validate_native_action_plan


class NativeActionPlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "input.ms14"
        self.source.write_bytes(b"native")
        self.actions = [
            {"op": "open_project", "path": str(self.source)},
            {"op": "set_component_value", "refdes": "R1", "value": "2k"},
            {"op": "run_analysis", "analysis": "dc", "commands": "dc V1 0 10 1", "outputs": ["V(out)"]},
            {"op": "measure", "signal": "V(out)"},
        ]
        self.plan = {"schema_version": 1, "actions": self.actions}

    def test_dc_keeps_dc_command_and_spice_units(self):
        normalized = validate_native_action_plan(self.plan)
        self.assertEqual(normalized["actions"][1]["value"], 2000)
        self.assertEqual(normalized["actions"][2]["commands"], "dc V1 0 10 1")

    def test_invalid_late_action_makes_no_com_calls(self):
        client = Mock()
        self.actions.append({"op": "run_command_file", "command_file": "danger.txt", "log_file": "x"})
        with self.assertRaises(ValueError):
            execute_native_action_plan(client, self.plan)
        self.assertEqual(client.mock_calls, [])

    def test_unknown_fields_nan_and_bad_order_are_rejected(self):
        for value in [float("nan"), True, "inf", "-1"]:
            self.actions[1]["value"] = value
            with self.assertRaises(ValueError):
                validate_native_action_plan(self.plan)
        self.actions[1]["value"] = 1000
        self.actions[2]["shell"] = "x"
        with self.assertRaises(ValueError):
            validate_native_action_plan(self.plan)
        del self.actions[2]["shell"]
        self.actions.insert(3, {"op": "set_component_value", "refdes": "R1", "value": 900})
        with self.assertRaisesRegex(ValueError, "measure"):
            validate_native_action_plan(self.plan)

    def _client(self):
        client = Mock()
        values = {"R1": 1000.0}
        client.enum_components.return_value = ["R1"]
        client.get_rlc_value.side_effect = lambda ref: {"value": values[ref]}
        def set_value(ref, value):
            values[ref] = value
            return {"value": value}
        client.set_rlc_value.side_effect = set_value
        return client, values

    def test_success_restores_parameters_and_measures_cached_evidence(self):
        client, values = self._client()
        def runner(c, action):
            self.assertEqual(values["R1"], 2000)
            return {"success": True, "signals": {"V(out)": [3.3, 3.4]}}
        result = execute_native_action_plan(client, self.plan, analysis_runner=runner)
        self.assertTrue(result["success"])
        self.assertEqual(values["R1"], 1000)
        self.assertAlmostEqual(result["measurements"][0]["mean"], 3.35)
        client.get_output_data.assert_not_called()

    def test_timeout_never_reports_success_and_restores(self):
        client, values = self._client()
        result = execute_native_action_plan(
            client, self.plan, analysis_runner=lambda *args: {"success": True, "timed_out": True},
        )
        self.assertFalse(result["success"])
        self.assertEqual(values["R1"], 1000)
        self.assertEqual(result["measurements"], [])

    def test_restore_failure_is_visible(self):
        client, values = self._client()
        setter = client.set_rlc_value.side_effect
        def set_value(ref, value):
            if value == 1000:
                raise RuntimeError("restore failed")
            return setter(ref, value)
        client.set_rlc_value.side_effect = set_value
        result = execute_native_action_plan(
            client, self.plan, analysis_runner=lambda *args: {"success": True, "signals": {"V(out)": [5]}},
        )
        self.assertFalse(result["success"])
        self.assertFalse(result["restored_original_values"])
        self.assertTrue(result["restore_errors"])
