"""Requirement variants and corrupted evidence must not become native passes."""
import copy
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.executable_approvals import approve_executable_netlist
from multisim_mcp.executable_netlists import compile_executable_netlist
from multisim_mcp.linear_reference import solve_linear, validated_components
from multisim_mcp.signal_multiboard import compare_native_to_linear, run_approved_signal_multiboard
from multisim_mcp.signal_multiboard_regression import (
    select_signal_multiboard_regression_cases, signal_multiboard_regression_cases,
)
from tests.test_active_signal_compiler import active_draft, component_review
from tests.test_experiment_fixtures import fixture_review
from tests import test_signal_multiboard as signal_tests


def reviewed_case(case):
    """Synthetic test approvals only; never used by the public runner."""
    draft = active_draft(**case.specification_inputs)
    preview = compile_executable_netlist(draft, component_review(draft), experiment_fixture=case.experiment_fixture)
    return preview, approve_executable_netlist(preview, fixture_review(preview))


class SignalRequirementMatrixTest(unittest.TestCase):
    def test_all_variants_preserve_partition_and_independent_transfer(self):
        cases = signal_multiboard_regression_cases()
        self.assertEqual(len(cases), 6)
        with tempfile.TemporaryDirectory() as folder:
            for case in cases:
                with self.subTest(case=case.case_id):
                    preview, approval = reviewed_case(case)
                    case.validate_preview(preview, approval)
                    output = Path(folder) / case.case_id
                    result = run_approved_signal_multiboard(None, preview, approval, output, analysis="ac")
                    self.assertEqual(result["linear_preflight"]["status"], "pass")
                    self.assertFalse(output.exists())
                    physical = result["physical_artifacts"]["boards"]
                    self.assertEqual(sum(p["refdes"] == "RLOAD" for b in physical for p in b["design"]["components"]), 1)
                    for b in physical:
                        self.assertEqual(next(p for p in b["design"]["components"] if p["refdes"] == "J1")["nodes"],
                                         ["path_02", "rail_positive", "0", "0"])
                    parts = validated_components(preview["spice_netlist"])
                    target = preview["synthesis"]["target_transfer"]
                    fixture = case.experiment_fixture
                    for factor in (0.01, 1, 10):
                        frequency = factor * case.specification_inputs["cutoff_frequency_hz"]
                        expected = target["gain_v_per_v"] * fixture["ac_amplitude_v"] * complex(
                            math.cos(math.radians(fixture["ac_phase_deg"])), math.sin(math.radians(fixture["ac_phase_deg"]))) / complex(1, factor)
                        self.assertLess(abs(solve_linear(parts, frequency)["path_04"] - expected), 1e-7)

    def test_dc_endpoints_and_midpoints_for_every_variant(self):
        for case in signal_multiboard_regression_cases():
            inputs = case.specification_inputs
            for vin in (inputs["input_min_v"], (inputs["input_min_v"] + inputs["input_max_v"]) / 2, inputs["input_max_v"]):
                with self.subTest(case=case.case_id, vin=vin):
                    draft = active_draft(**inputs)
                    preview = compile_executable_netlist(draft, component_review(draft),
                        experiment_fixture={**case.experiment_fixture, "dc_voltage_v": vin})
                    target = preview["synthesis"]["target_transfer"]
                    self.assertLess(abs(solve_linear(validated_components(preview["spice_netlist"]))["path_04"]
                        - (target["gain_v_per_v"] * vin + target["offset_v"])), 1e-7)

    def test_selection_is_exact_and_does_not_share_mutable_cases(self):
        for ids in ([], ["nominal", "nominal"], ["unknown"]):
            with self.assertRaises(ValueError):
                select_signal_multiboard_regression_cases(ids)
        first = signal_multiboard_regression_cases()[0]
        first.manifest()["specification_inputs"]["supply_voltage_v"] = 99
        self.assertEqual(first.specification_inputs["supply_voltage_v"], 5)
        first.specification_inputs["supply_voltage_v"] = 99
        self.assertEqual(signal_multiboard_regression_cases()[0].specification_inputs["supply_voltage_v"], 5)

    def test_wrong_case_approval_is_rejected_even_when_approval_is_valid(self):
        nominal, phase = signal_multiboard_regression_cases()[:2]
        preview, approval = reviewed_case(nominal)
        with self.assertRaisesRegex(ValueError, "regression requirements"):
            phase.validate_preview(preview, approval)

    def test_runner_prevalidates_all_inputs_without_granting_approval(self):
        tool = Path(__file__).resolve().parents[2] / "tools" / "run_signal_multiboard_regression.py"
        spec = importlib.util.spec_from_file_location("signal_regression_runner_test", tool)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            entries = []
            cases = signal_multiboard_regression_cases()[:2]
            for case in cases:
                preview, approval = reviewed_case(case)
                for name, data in (("preview", preview), ("approval", approval)):
                    (folder / f"{case.case_id}-{name}.json").write_text(json.dumps(data), encoding="utf-8")
                entries.append({"case_id": case.case_id, "preview": f"{case.case_id}-preview.json",
                                "approval": f"{case.case_id}-approval.json"})
            entries[-1].update(preview=entries[0]["preview"], approval=entries[0]["approval"])
            source = folder / "inputs.json"
            source.write_text(json.dumps(entries), encoding="utf-8")
            destination = folder / "not-created"
            with patch.object(runner, "run_approved_signal_multiboard", return_value={}) as gate:
                with self.assertRaisesRegex(ValueError, "regression requirements"):
                    runner.main(["--inputs", str(source), "--output", str(destination), "--cases",
                                 *[c.case_id for c in cases], "--execute"])
            self.assertTrue(all(not c.kwargs.get("execute") for c in gate.call_args_list))
            self.assertFalse(destination.exists())


class SignalEvidenceFaultTest(unittest.TestCase):
    def setUp(self):
        self.preview, self.approval = reviewed_case(signal_multiboard_regression_cases()[1])
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def evidence(self, analysis="ac"):
        axes = [10, 1000, 10000] if analysis == "ac" else [0]
        parts = validated_components(self.preview["spice_netlist"])
        solutions = [solve_linear(parts, f if analysis == "ac" else None) for f in axes]
        nets = ["path_02", "rail_positive", "path_04"]
        mapping = {n: f"V({n})" for n in nets}
        series = {mapping[n]: {"status": "pass", "axis": list(axes), "sample_count": len(axes),
                  "real": [s[n].real for s in solutions], "imaginary": [s[n].imag for s in solutions]} for n in nets}
        reference = self.root / "full-reference"
        reference.mkdir(exist_ok=True)
        build = {"build": {"probes": [{"net": n, "voltage_output": o} for n, o in mapping.items()]}}
        (reference / "build.json").write_text(json.dumps(build), encoding="utf-8")
        stem = "native-ac" if analysis == "ac" else "native-op"
        (reference / f"{stem}-series.json").write_text(json.dumps(series), encoding="utf-8")
        return {"output_directory": str(self.root), "analysis": analysis,
            "analysis_options": {"start_frequency": 10, "stop_frequency": 10000} if analysis == "ac" else {},
            "boards": [{"board_id": b, "probe_map": {n: mapping[n] for n in wanted},
                        "native_analysis_series": copy.deepcopy(series)}
                       for b, wanted in (("sensor", nets[:2]), ("conditioner", nets))]}

    def test_missing_extra_duplicate_or_renamed_board_cannot_pass(self):
        for fault in (lambda r: r["boards"].pop(),
                      lambda r: r["boards"].append(copy.deepcopy(r["boards"][0])),
                      lambda r: r["boards"][1].update(board_id="sensor"),
                      lambda r: r["boards"][0].update(board_id="other")):
            result = self.evidence()
            self.assertEqual(compare_native_to_linear(result, self.preview)["status"], "pass")
            fault(result)
            self.assertEqual(compare_native_to_linear(result, self.preview)["status"], "fail")

    def test_missing_supply_or_signal_and_aliased_probes_fail(self):
        for fault in (lambda b: b["probe_map"].pop("rail_positive"),
                      lambda b: b["probe_map"].pop("path_02"),
                      lambda b: b["probe_map"].update(path_02=b["probe_map"]["rail_positive"]),
                      lambda b: b["probe_map"].update(unrequested="V(extra)"),
                      lambda b: b["native_analysis_series"].pop("V(path_02)")):
            result = self.evidence()
            fault(result["boards"][0])
            comparison = compare_native_to_linear(result, self.preview)
            self.assertEqual(comparison["status"], "fail")
            self.assertEqual(comparison["target_transfer_status"], "fail")

    def test_corrupt_series_flags_shapes_numeric_values_and_axes_fail(self):
        def shift_axis(s):
            s["axis"][1] = 900
            solved = solve_linear(validated_components(self.preview["spice_netlist"]), 900)["path_02"]
            s["real"][1], s["imaginary"][1] = solved.real, solved.imag
        faults = [lambda s: s.update(status="unverified"), lambda s: s.update(analysis="tran"),
                  lambda s: s.update(output="V(wrong)"), lambda s: s["real"].pop(),
                  lambda s: s.update(sample_count=4), lambda s: s["axis"].__setitem__(1, 0),
                  lambda s: s["axis"].__setitem__(0, 11), lambda s: s["real"].__setitem__(1, float("nan")),
                  lambda s: s["imaginary"].__setitem__(1, float("inf")), lambda s: s["real"].__setitem__(1, True),
                  lambda s: s["imaginary"].__setitem__(1, s["imaginary"][1] + 0.001), shift_axis]
        for fault in faults:
            result = self.evidence()
            fault(result["boards"][0]["native_analysis_series"]["V(path_02)"])
            self.assertEqual(compare_native_to_linear(result, self.preview)["status"], "fail")

    def test_dc_multiple_samples_and_nonzero_imaginary_fail(self):
        for fault in (lambda s: s["imaginary"].__setitem__(0, 1),
                      lambda s: s.update(axis=[0, 1], real=s["real"] * 2, imaginary=[0, 0], sample_count=2)):
            result = self.evidence("dc")
            self.assertEqual(compare_native_to_linear(result, self.preview)["status"], "pass")
            fault(result["boards"][0]["native_analysis_series"]["V(path_02)"])
            self.assertEqual(compare_native_to_linear(result, self.preview)["status"], "fail")

    def test_full_reference_missing_observation_or_duplicate_net_fails(self):
        for duplicate in (False, True):
            result = self.evidence()
            path = self.root / "full-reference" / "build.json"
            build = json.loads(path.read_text(encoding="utf-8"))
            if duplicate:
                build["build"]["probes"].append(build["build"]["probes"][0])
            else:
                build["build"]["probes"].pop(1)
            path.write_text(json.dumps(build), encoding="utf-8")
            self.assertEqual(compare_native_to_linear(result, self.preview)["status"], "fail")

    def test_invalid_rlc_readbacks_persist_as_explicit_json_safe_failures(self):
        from multisim_mcp.signal_multiboard import validate_reopened_signal_contract
        helper = signal_tests.SignalMultiboardTest("test_reopened_changed_ideal_model_fails")
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        board, root, client, values = helper._reopen_board()
        for invalid in (float("nan"), float("inf"), None, True, "invalid"):
            values["CFLT"] = invalid
            with patch("multisim_mcp.multisim_client.Ms14Codec"), patch(
                    "multisim_mcp.signal_multiboard.ideal_model_fingerprints", return_value={"XU1": "ok", "XU2": "ok"}):
                result = validate_reopened_signal_contract(board, client, root)
            self.assertEqual(result["status"], "fail")
            self.assertIsNone(result["rlc_values"]["CFLT"]["actual_si"])
            json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
