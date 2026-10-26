"""Synthetic archives exercise the verifier; these are never native evidence.

Model text below is an intentionally tiny test double, not an NI model asset.
"""
import copy
import csv
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.generated_analog_run import vendor_model_fingerprints
from multisim_mcp.linear_reference import expected_native_pins, scalar
from multisim_mcp.multiboard_fixtures import materialize_multiboard_fixture_artifacts, compare_multiboard_interface_series
from multisim_mcp.multiboard_plan import materialize_circuit_design_partition
from multisim_mcp.native_analysis_series import extract_native_analysis_series, write_native_series_csv
from multisim_mcp.native_multiboard_acceptance import _normalize_analysis_options, _compare_full_reference_series
from multisim_mcp.vendor_multiboard import (
    _board_vendor_parts, _persist_vendor_evidence, _write_physical_manifest,
    prepare_lm324aj_multiboard_contract, run_lm324aj_multiboard_acceptance,
)
from multisim_mcp.vendor_multiboard_evidence import verify_vendor_multiboard_evidence, _GATES
from multisim_mcp.native_netlist_validation import validate_native_netlist
from tests.test_vendor_multiboard import _design


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _synthetic_project(local, board):
    local.mkdir(parents=True)
    (local / "circuit.ms14").write_bytes(b"synthetic-test-project")
    (local / "schematic.png").write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    parts = _board_vendor_parts(board)
    pins = expected_native_pins(parts)
    for p in board["design"]["components"]:
        if p["kind"] == "HDR1X4":
            pins[p["refdes"]] = {f"P{i}": n for i, n in enumerate(p["nodes"], 1)}
    report = "\n".join(f"{net} circuit {ref} {pin}" for ref, ps in pins.items() for pin, net in ps.items())
    (local / "reopened-report-netlist.txt").write_text(report, encoding="utf-8")
    tree = ET.Element("Synthetic")
    model = ET.SubElement(ET.SubElement(tree, "Item", CiID="test-model"), "CiModel", LocalName="&ASCtest_subckt")
    ET.SubElement(model, "CiaCString", String="&ASC.SUBCKT test_subckt a b c d e\n* TEST MODEL\n.ENDS")
    for part in parts:
        if part.kind != "LM324AJ":
            continue
        component = ET.SubElement(tree, "CiComponent", LocalName="&ASC" + part.refdes, Model="test-model")
        item = ET.SubElement(ET.SubElement(component, "Attributes"), "Item")
        strings = ET.SubElement(ET.SubElement(item, "CiaCollString"), "strings")
        for value in ("", "LM324AJ", "OPAMP", "LM158_4", "", ".SUBCKT LM158_4 a b c d e\n* TEST MODEL\n.ENDS"):
            ET.SubElement(strings, "Item", Value="&ASC" + value)
        ET.SubElement(item, "CiaSpiceTmpltExprt", String="&ASCsynthetic expression")
    for name in ("circuit.ms14.xml", "reopened-model.xml"):
        ET.ElementTree(tree).write(local / name, encoding="utf-8", xml_declaration=True)
    models = vendor_model_fingerprints(local / "reopened-model.xml", parts)
    write(local / "reopened-contract.json", {
        "status": "pass", "pins": validate_native_netlist(report, pins, strict=True),
        "rlc_values": {p.refdes: {"actual_si": scalar(p.value), "expected_si": scalar(p.value), "passed": True}
                       for p in parts if p.kind in {"R", "C"}},
        "vendor_model_fingerprints": models, "source_vendor_model_fingerprints": models,
        "vendor_models_pass": True, "vendor_model_error": None,
    })
    return {"success": True, "layout_validation": {"status": "pass"}, "build": {"probes": []}}


def synthetic_archive(root, analysis="ac"):
    root.mkdir()
    contract = prepare_lm324aj_multiboard_contract(_design())
    from multisim_mcp.eda_core import CircuitDesign
    design = CircuitDesign.from_dict(contract["design"])
    physical = materialize_circuit_design_partition(design, contract["partition"], target_multisim_version="14.3")
    prepared = materialize_multiboard_fixture_artifacts(physical, contract["fixtures"])
    write(root / "fixture-artifacts.json", prepared)
    options_raw = {"start_frequency": 10., "stop_frequency": 1000.} if analysis == "ac" else (
        {"duration": .002} if analysis == "tran" else {})
    _, options = _normalize_analysis_options(analysis, options_raw)
    stem = "native-op" if analysis == "dc" else "native-" + analysis
    axis = [0.] if analysis == "dc" else [0., .001, .002] if analysis == "tran" else [10., 100., 1000.]
    values = {"stage1": .2, "out": .4, "vcc": 12., "vss": -12.}
    records, observations, drawings = [], {}, []
    reference = {}
    reference_nets = ["out", "stage1", "vcc", "vss"]
    for board in [*prepared["boards"], {"board_id": "full-reference", "design": design.to_dict(), "probe_nets": reference_nets}]:
        local = root / board["board_id"]
        build = _synthetic_project(local, board)
        mapping = {net: f"V({net})" for net in board["probe_nets"]}
        build["build"]["probes"] = [{"net": n, "voltage_output": o} for n, o in mapping.items()]
        write(local / "build.json", build)
        raw = {"ready": True, "requested_outputs": list(mapping.values()), "results": {}}
        for net, output in mapping.items():
            rows = [axis, [values[net]] * len(axis)]
            if analysis == "ac":
                rows.append([0.] * len(axis))
            raw["results"][output] = {"output": output, "rows": rows, "n_points": len(axis)}
        series = extract_native_analysis_series(raw, list(mapping.values()), analysis, options)
        write(local / f"{stem}.json", raw)
        write(local / f"{stem}-series.json", series)
        write_native_series_csv(local / f"{stem}-series.csv", series)
        if board["board_id"] != "full-reference":
            records.append({"board_id": board["board_id"], "probe_map": mapping,
                            "reopened_outputs": list(mapping.values()), "native_analysis_series": series})
            observations[board["board_id"]] = {n: series[o] for n, o in mapping.items()}
        else:
            reference = {n: series[o] for n, o in mapping.items()}
    for board in physical["boards"]:
        local = root / "physical" / board["board_id"]
        write(local / "build.json", _synthetic_project(local, board))
        write(local / "design.json", board["design"])
        (local / "physical.cir").write_text(board["spice_netlist"], encoding="utf-8")
        with (local / "bom.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["refdes", "kind", "value", "model", "scope"])
            for p in board["design"]["components"]:
                writer.writerow([p["refdes"], p["kind"], p.get("value"), p.get("model"), "physical-design"])
        import hashlib
        drawings.append({"board_id": board["board_id"], "status": "pass", "simulation_performed": False,
                         "saved_sha256": hashlib.sha256((local / "circuit.ms14").read_bytes()).hexdigest()})
    comparison = _compare_full_reference_series(records, reference, reference_nets, absolute_tolerance=1e-7)
    interfaces = compare_multiboard_interface_series(prepared, observations, nets=reference_nets, absolute_tolerance=1e-7)
    result = {"status": "accepted", "verification_status": "native-verified", "profile": contract["profile"],
              "multisim_version": "Multisim 14.3", "target_multisim_version": "14.3", "comparison_tolerance": 1e-7,
              "analysis": analysis, "analysis_options": options, "contract_digest": contract["contract_digest"],
              "model_contract": contract["model_contract"], "native_acceptance": {k: True for k in sorted(_GATES)},
              "boards": records, "physical_board_drawings": drawings, "full_reference": comparison,
              "series_interface_comparison": interfaces}
    _persist_vendor_evidence(root, result, contract)


class VendorEvidenceArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "archive"
        synthetic_archive(self.root)

    def change_json(self, name, change):
        path = self.root / name
        value = read(path)
        change(value)
        write(path, value)

    def test_control_passes_without_com_for_each_analysis(self):
        with patch("multisim_mcp.multisim_client.Ms14Codec", side_effect=AssertionError("COM forbidden")):
            for analysis in ("dc", "ac", "tran"):
                root = Path(self.temp.name) / analysis
                synthetic_archive(root, analysis)
                result = verify_vendor_multiboard_evidence(root)
                self.assertEqual(result["status"], "pass")
                self.assertFalse(result["current_machine_native_verified"])

    def test_hash_or_omitted_file_rejected(self):
        (self.root / "vendor-report.md").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_vendor_multiboard_evidence(self.root)
        self.change_json("sha256-manifest.json", lambda value: value["files"].pop("vendor-report.md"))
        with self.assertRaisesRegex(ValueError, "every archive file"):
            verify_vendor_multiboard_evidence(self.root)

    def test_rehashed_fault_matrix_is_rejected(self):
        baseline = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        def json_fault(name, change):
            return lambda: self.change_json(name, change)
        faults = {
            "rlc": json_fault("sensor/reopened-contract.json", lambda d: d["rlc_values"]["C1"].update(actual_si=1e-6)),
            "fixture": json_fault("fixture-artifacts.json", lambda d: d["boards"][0]["design"]["components"][0].update(value="999")),
            "missing-probe": json_fault("sensor/build.json", lambda d: d["build"]["probes"].pop()),
            "aliased-probe": json_fault("sensor/build.json", lambda d: d["build"]["probes"][0].update(voltage_output=d["build"]["probes"][1]["voltage_output"])),
            "imaginary-sample": json_fault("sensor/native-ac.json", lambda d: d["results"]["V(stage1)"]["rows"][2].__setitem__(1, .05)),
            "axis": json_fault("sensor/native-ac.json", lambda d: d["results"]["V(stage1)"]["rows"][0].__setitem__(2, 900.)),
            "duplicate-board": json_fault("vendor-acceptance.json", lambda d: d["boards"][1].update(board_id="sensor")),
            "tolerance": json_fault("vendor-acceptance.json", lambda d: d.update(comparison_tolerance=1.)),
            "physical-fixture": json_fault("physical/conditioner/design.json", lambda d: d["components"].append(copy.deepcopy(d["components"][0]))),
            "pin": lambda: (self.root / "sensor/reopened-report-netlist.txt").write_text(
                (self.root / "sensor/reopened-report-netlist.txt").read_text(encoding="utf-8").replace("stage1 circuit J1 P1", "vcc circuit J1 P1"), encoding="utf-8"),
            "model": lambda: (self.root / "sensor/reopened-model.xml").write_text(
                (self.root / "sensor/reopened-model.xml").read_text(encoding="utf-8").replace("TEST MODEL", "CHANGED MODEL"), encoding="utf-8"),
            "report": lambda: (self.root / "vendor-report.md").write_text("forged pass", encoding="utf-8"),
            "csv": lambda: (self.root / "sensor/native-ac-series.csv").write_text("changed rows", encoding="utf-8"),
        }
        for name, change in faults.items():
            with self.subTest(fault=name):
                for path, data in baseline.items():
                    (self.root / path).write_bytes(data)
                change()
                # Keep the two acceptance copies consistent to reach the
                # semantic gate, rather than failing only a duplicate file.
                (self.root / "acceptance.json").write_bytes((self.root / "vendor-acceptance.json").read_bytes())
                _write_physical_manifest(self.root)
                with self.assertRaises(ValueError):
                    verify_vendor_multiboard_evidence(self.root)

    def test_all_copies_of_corrupted_samples_still_fail_reference_comparison(self):
        local = self.root / "sensor"
        raw = read(local / "native-ac.json")
        raw["results"]["V(stage1)"]["rows"][2][1] = .05
        write(local / "native-ac.json", raw)
        result = read(self.root / "vendor-acceptance.json")
        record = next(b for b in result["boards"] if b["board_id"] == "sensor")
        series = extract_native_analysis_series(raw, raw["requested_outputs"], "ac", result["analysis_options"])
        record["native_analysis_series"] = series
        write(local / "native-ac-series.json", series)
        write_native_series_csv(local / "native-ac-series.csv", series)
        for name in ("vendor-acceptance.json", "acceptance.json"):
            write(self.root / name, result)
        _write_physical_manifest(self.root)
        with self.assertRaisesRegex(ValueError, "interfaces or full reference"):
            verify_vendor_multiboard_evidence(self.root)


if __name__ == "__main__":
    unittest.main()
