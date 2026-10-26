"""Electrical and approval boundaries of the fixed ideal two-board profile."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from multisim_mcp.eda_core import CircuitComponent
from multisim_mcp.executable_approvals import approve_executable_netlist
from multisim_mcp.linear_fixture_fragments import normalize_linear_fragment
from multisim_mcp.linear_reference import expected_native_pins, scalar, solve_linear
from multisim_mcp.multiboard_fixtures import validate_multiboard_fixture_contract
from multisim_mcp.multiboard_plan import plan_multiboard_partition, rank_partition_candidates
from multisim_mcp.native_multiboard_acceptance import run_native_multiboard_acceptance, _require_native_probe_outputs
from multisim_mcp.engineering_planner import build_engineering_plan, select_multiboard_engineering_candidate
from multisim_mcp.signal_multiboard import (
    bind_signal_multiboard_request, linear_board_parts, linear_preflight,
    prepare_signal_multiboard_request, run_approved_signal_multiboard,
    validate_reopened_signal_contract, compare_native_to_linear,
)
from tests.test_experiment_fixtures import compiled_fixture, fixture_review


class SignalMultiboardTest(unittest.TestCase):
    def setUp(self):
        self.preview = compiled_fixture()
        self.approval = approve_executable_netlist(self.preview, fixture_review(self.preview))
        self.request = prepare_signal_multiboard_request(self.preview, self.approval)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "new"

    def prepared(self):
        return run_approved_signal_multiboard(None, self.preview, self.approval, self.output)

    def test_json_roundtrip_preview_has_no_files_or_backend(self):
        with patch("multisim_mcp.server._create_schematic_impl", side_effect=AssertionError("backend")):
            decoded = json.loads(json.dumps([self.request, self.preview, self.approval]))
            result = run_approved_signal_multiboard(*decoded, self.output)
        self.assertEqual(result["linear_preflight"]["status"], "pass")
        self.assertEqual(result["verification_status"], "unverified")
        self.assertFalse(self.output.exists())
        self.assertFalse(result["approval_provenance"]["simulation_plan_approval_present"])

    def test_physical_boards_partition_load_once_and_exclude_upstream_fixture(self):
        result = self.prepared()
        physical = {b["board_id"]: b for b in result["physical_artifacts"]["boards"]}
        receiver = physical["conditioner"]["design"]["components"]
        self.assertNotIn("VINPUT", {p["refdes"] for p in receiver})
        all_parts = [p for b in physical.values() for p in b["design"]["components"]]
        self.assertEqual(sum(p["refdes"] == "RLOAD" for p in all_parts), 1)
        self.assertEqual(sum(p["kind"] == "HDR1X4" for p in all_parts), 2)
        experiment = next(b for b in result["prepared_artifacts"]["boards"] if b["board_id"] == "conditioner")
        self.assertIn("VINPUT", {p["refdes"] for p in experiment["fixture_components"]})

    def test_four_physical_pins_keep_both_ground_connections(self):
        result = self.prepared()
        for board in result["physical_artifacts"]["boards"]:
            connector = next(p for p in board["design"]["components"] if p["kind"] == "HDR1X4")
            self.assertEqual(connector["nodes"], ["path_02", "rail_positive", "0", "0"])
            self.assertEqual(len(board["interfaces"]), 4)

    def test_duplicate_signal_or_supply_pin_rejected(self):
        for net in ("path_02", "rail_positive"):
            request = copy.deepcopy(self.request)
            request["connectors"][0]["pins"][3]["net"] = net
            result = plan_multiboard_partition(request["components"], request["boards"], request["connectors"])
            self.assertFalse(result["feasible"])
            self.assertIn("duplicate_net", {v["constraint"] for v in result["violations"]})

    def test_constant_receiver_source_cannot_preserve_rc_phase(self):
        prepared = self.prepared()["prepared_artifacts"]
        receiver = next(b for b in prepared["boards"] if b["board_id"] == "conditioner")
        original = solve_linear(linear_board_parts(receiver), 1000)["path_04"]
        receiver = copy.deepcopy(receiver)
        receiver["design"]["components"] = [p for p in receiver["design"]["components"]
            if p["refdes"] not in {"VINPUT", "RSOURCE", "RPROT", "CFLT", "XU1", "CDEC"}]
        receiver["design"]["components"].append(CircuitComponent("VFIX", "V", ("path_02", "0"), model="DC 2.5 AC 1").to_dict())
        self.assertGreater(abs(solve_linear(linear_board_parts(receiver), 1000)["path_04"] - original), 0.2)
        self.assertEqual(linear_preflight(prepared, self.preview)["status"], "pass")

    def test_request_tampering_fails_before_files_or_native_gate(self):
        changes = [
            lambda r: r["components"][0].update(board="conditioner"),
            lambda r: r["components"][1].update(value="999"),
            lambda r: r["connectors"][0]["pins"][0].update(net="path_04"),
            lambda r: r["fixtures"][0]["components"][0].update(value="9"),
            lambda r: r["fixtures"][0].update(source_compiled_digest="0" * 64),
            lambda r: r["fixtures"][0]["ports"].remove("0"),
        ]
        for change in changes:
            r = copy.deepcopy(self.request)
            change(r)
            with self.subTest(r=r), patch("multisim_mcp.signal_multiboard.run_native_multiboard_acceptance") as gate:
                with self.assertRaisesRegex(ValueError, "differs"):
                    run_approved_signal_multiboard(r, self.preview, self.approval, self.output, execute=True)
                gate.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_half_approval_and_missing_fixture_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "together"):
            bind_signal_multiboard_request(None, self.preview, None)
        base = self.preview["experiment_fixture"]["base_preview"]
        from tests.test_active_signal_compiler import netlist_review
        approval = approve_executable_netlist(base, netlist_review(base))
        with self.assertRaisesRegex(ValueError, "explicit experiment_fixture"):
            prepare_signal_multiboard_request(base, approval)

    def test_analysis_contract_rejects_unreviewed_modes_and_unbounded_work(self):
        for kw in ({"analysis": "tran"}, {"target_multisim_version": "14.2"},
                   {"analysis": "ac", "analysis_options": {"max_points": 2001}},
                   {"analysis": "ac", "analysis_options": {"series_alignment": "linear"}}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                run_approved_signal_multiboard(None, self.preview, self.approval, self.output, **kw)
        self.assertFalse(self.output.exists())

    def test_fixed_large_partition_is_allowed_but_search_is_still_bounded(self):
        self.assertEqual(len(rank_partition_candidates(self.request["components"], self.request["boards"], self.request["connectors"])), 1)
        components = copy.deepcopy(self.request["components"])
        components[0].pop("board")
        with self.assertRaisesRegex(ValueError, "fully fixed"):
            rank_partition_candidates(components, self.request["boards"], self.request["connectors"])

    def test_fragment_rejects_vendor_model_singular_source_and_invalid_ports(self):
        fragment = self.request["fixtures"][0]
        cases = []
        vendor = copy.deepcopy(fragment)
        next(p for p in vendor["components"] if p["refdes"] == "XU1")["model"] = "LM324"
        cases.append(vendor)
        singular = copy.deepcopy(fragment)
        singular["components"].append(CircuitComponent("RFLOAT", "R", ("floating_a", "floating_b"), value="1k").to_dict())
        cases.append(singular)
        cases.append(dict(fragment, ports=["path_02", "rail_positive", "missing"]))
        cases.append(dict(fragment, source_compiled_digest="invalid"))
        cases.append(dict(fragment, model_scope="vendor-equivalent"))
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_linear_fragment(raw, "path_02")

    def test_fragment_detects_board_refdes_ports_and_driver_collisions(self):
        artifacts = self.prepared()["physical_artifacts"]
        def duplicate_ref(fragment):
            next(p for p in fragment["components"] if p["refdes"] == "RPROT")["refdes"] = "RLOAD"

        def duplicate_driver(fragment):
            fragment["ports"].append("path_04")
            fragment["components"].append(CircuitComponent("VOTHER", "V", ("path_04", "0"), value="1").to_dict())

        for modify, constraint in (
            (duplicate_ref, "refdes"),
            (lambda f: f.update(ports=["path_02", "0"]), "fragment_ports"),
            (duplicate_driver, "multiple_drivers"),
        ):
            fixtures = copy.deepcopy(self.request["fixtures"])
            modify(fixtures[0])
            result = validate_multiboard_fixture_contract(artifacts, fixtures)
            self.assertIn(constraint, {v["constraint"] for v in result["violations"]})

    def _reopen_board(self):
        board = self.prepared()["prepared_artifacts"]["boards"][1]
        root = Path(self.temp.name)
        expected = expected_native_pins(linear_board_parts(board))
        expected["J1"] = dict(zip(["P1", "P2", "P3", "P4"], ["path_02", "rail_positive", "0", "0"]))
        report = "\n".join(f"{n} circuit {r} {pin}" for r, pins in expected.items() for pin, n in pins.items())
        (root / "reopened-report-netlist.txt").write_text(report, encoding="utf-8")
        values = {p.refdes: scalar(p.value) for p in linear_board_parts(board) if p.kind in {"R", "C", "L"}}
        client = Mock()
        client.get_rlc_value.side_effect = lambda ref: {"value": values[ref]}
        return board, root, client, values

    def test_reopened_strict_pins_include_connector_and_tiny_capacitor(self):
        board, root, client, values = self._reopen_board()
        with patch("multisim_mcp.multisim_client.Ms14Codec"), patch("multisim_mcp.signal_multiboard.ideal_model_fingerprints", return_value={"XU1": "ok", "XU2": "ok"}):
            self.assertEqual(validate_reopened_signal_contract(board, client, root)["status"], "pass")
            text = (root / "reopened-report-netlist.txt").read_text(encoding="utf-8")
            (root / "reopened-report-netlist.txt").write_text(text.replace("0 circuit J1 P4", "rail_positive circuit J1 P4"), encoding="utf-8")
            self.assertEqual(validate_reopened_signal_contract(board, client, root)["status"], "fail")
            (root / "reopened-report-netlist.txt").write_text(text, encoding="utf-8")
            values["CFLT"] *= 1.0001
            self.assertEqual(validate_reopened_signal_contract(board, client, root)["status"], "fail")
            next(p for p in board["design"]["components"] if p["refdes"] == "CFLT")["value"] = "1e-15"
            values["CFLT"] = 2e-15
            self.assertEqual(validate_reopened_signal_contract(board, client, root)["status"], "fail")

    def test_reopened_changed_ideal_model_fails(self):
        board, root, client, values = self._reopen_board()
        with patch("multisim_mcp.multisim_client.Ms14Codec"), patch("multisim_mcp.signal_multiboard.ideal_model_fingerprints", side_effect=ValueError("changed model")):
            result = validate_reopened_signal_contract(board, client, root)
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["ideal_models_pass"])

    def test_native_linear_oracle_rejects_missing_reference(self):
        result = {"boards": [], "output_directory": str(self.output), "analysis": "dc"}
        self.assertEqual(compare_native_to_linear(result, self.preview)["status"], "fail")

    def test_every_complex_native_sample_and_transfer_target_are_checked(self):
        from multisim_mcp.linear_reference import validated_components
        parts = validated_components(self.preview["spice_netlist"])
        frequencies = [10, 1000, 10000]
        solutions = [solve_linear(parts, f) for f in frequencies]
        nets = ["path_02", "rail_positive", "path_04"]
        series = {f"V({net})": {"status": "pass", "axis": frequencies,
            "real": [s[net].real for s in solutions], "imaginary": [s[net].imag for s in solutions]}
            for net in nets}
        mapping = {net: f"V({net})" for net in nets}
        reference = self.output / "full-reference"
        reference.mkdir(parents=True)
        (reference / "build.json").write_text(json.dumps({"build": {"probes": [
            {"net": net, "voltage_output": output} for net, output in mapping.items()]}}), encoding="utf-8")
        (reference / "native-ac-series.json").write_text(json.dumps(series), encoding="utf-8")
        result = {"output_directory": str(self.output), "analysis": "ac", "boards": [
            {"board_id": "sensor", "probe_map": {n: mapping[n] for n in nets[:2]}, "native_analysis_series": copy.deepcopy(series)},
            {"board_id": "conditioner", "probe_map": mapping, "native_analysis_series": copy.deepcopy(series)}]}
        passed = compare_native_to_linear(result, self.preview)
        self.assertEqual(passed["status"], "pass")
        self.assertEqual(passed["target_transfer_status"], "pass")
        result["boards"][1]["native_analysis_series"]["V(path_04)"]["imaginary"][1] += 0.001
        failed = compare_native_to_linear(result, self.preview)
        self.assertEqual(failed["status"], "fail")
        self.assertEqual(failed["target_transfer_status"], "fail")

    def test_missing_reopened_probe_is_rejected_before_analysis(self):
        build = {"build": {"probes": [{"net": "out", "voltage_output": "V(OutProbe)"}]}}
        with self.assertRaisesRegex(RuntimeError, "missing requested native voltage probes"):
            _require_native_probe_outputs(build, ["out"], ["V(OutProbe1)"])
        with self.assertRaises(RuntimeError):
            _require_native_probe_outputs(build, ["out", "supply"], ["V(OutProbe)"])
        _require_native_probe_outputs(build, ["out"], ["V(OutProbe)"])

    def test_failed_reopened_callback_stops_before_native_analysis(self):
        from multisim_mcp.eda_core import CircuitDesign
        design = CircuitDesign.from_dict(self.preview["circuit_design"])
        partition = select_multiboard_engineering_candidate(build_engineering_plan(self.request))["candidate"]
        client = Mock()
        client.connect.return_value = {"version": "14.3"}
        client.report_netlist.side_effect = lambda path, *args: Path(path).write_text("invalid", encoding="utf-8")
        def build(netlist, output, **kwargs):
            Path(output).write_bytes(b"mock-project")
            return {"success": True}
        with self.assertRaisesRegex(RuntimeError, "electrical contract failed"):
            run_native_multiboard_acceptance(design, partition, self.request["fixtures"], self.output,
                execute=True, schematic_executor=build, client=client,
                reopened_contract_validator=lambda *args: {"status": "fail"})
        client.run_dc_operating_point.assert_not_called()
        client.enum_outputs.assert_not_called()
        self.assertEqual(json.loads((self.output / "sensor" / "reopened-contract.json").read_text())["status"], "fail")

    def test_output_race_does_not_write_into_another_directory(self):
        from multisim_mcp.signal_multiboard import run_native_multiboard_acceptance as gate
        def raced(*args, **kwargs):
            if not kwargs.get("execute"):
                return gate(*args, **kwargs)
            self.output.mkdir()
            (self.output / "user.txt").write_text("preserve", encoding="utf-8")
            raise ValueError("output already exists")
        with patch("multisim_mcp.signal_multiboard.run_native_multiboard_acceptance", side_effect=raced):
            with self.assertRaisesRegex(ValueError, "already exists"):
                run_approved_signal_multiboard(None, self.preview, self.approval, self.output, execute=True)
        self.assertEqual({p.name for p in self.output.iterdir()}, {"user.txt"})

    def test_owned_execution_failure_persists_provenance_and_failed_report(self):
        client = Mock()
        client.connect.side_effect = RuntimeError("native unavailable")
        result = run_approved_signal_multiboard(None, self.preview, self.approval, self.output,
            execute=True, schematic_executor=Mock(), client=client)
        self.assertEqual(result["status"], "failed")
        self.assertIn("native unavailable", result["error"])
        self.assertTrue((self.output / "signal-report.html").is_file())
        self.assertTrue((self.output / "netlist-approval.json").is_file())

    def test_existing_server_tools_accept_approved_preview_without_new_tool(self):
        from multisim_mcp import server
        plan = server.plan_multiboard_engineering_request(executable_netlist=self.preview, netlist_approval=self.approval)
        self.assertEqual(plan["request"], self.request)
        result = server.run_native_multiboard_acceptance(None, str(self.output), executable_netlist=self.preview, netlist_approval=self.approval)
        self.assertEqual(result["profile"], "approved-ideal-signal-two-board-v1")


if __name__ == "__main__":
    unittest.main()
