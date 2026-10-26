"""Offline contract and fault-injection tests for the bounded LM324AJ path."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.multiboard_plan import materialize_circuit_design_partition
from multisim_mcp.multiboard_fixtures import materialize_multiboard_fixture_artifacts
from multisim_mcp.spice_adapter import circuit_design_from_spice
from multisim_mcp.natural_analog_frontend import parse_natural_analog_frontend
from multisim_mcp.vendor_multiboard import (
    _board_vendor_parts,
    prepare_lm324aj_multiboard_contract,
    run_lm324aj_multiboard_acceptance,
    validate_lm324aj_design,
    validate_reopened_lm324aj_contract,
)


def _design():
    proposal = parse_natural_analog_frontend(
        "传感器输入 50mV，总增益 5，截止频率 500Hz，±12V 供电，47kΩ 负载"
    )["proposal"]
    return circuit_design_from_spice(proposal["netlist"], design_id="lm324-two-board", title="LM324 two board")


class _Client:
    def __init__(self, values):
        self.values = values

    def get_rlc_value(self, refdes):
        return {"value": self.values.get(refdes)}


class VendorMultiboardContractTest(unittest.TestCase):
    def test_contract_derives_fixed_partition_and_four_pin_interface(self):
        contract = prepare_lm324aj_multiboard_contract(_design())
        self.assertEqual(contract["profile"], "approved-lm324aj-two-board-v1")
        self.assertEqual(contract["target_multisim_version"], "14.3")
        self.assertEqual(contract["partition"]["connector_contract_status"], "valid")
        self.assertEqual(contract["partition"]["cross_board_nets"], [
            {"net": "0", "boards": ["conditioner", "sensor"], "connector_required": True},
            {"net": "stage1", "boards": ["conditioner", "sensor"], "connector_required": True},
            {"net": "vcc", "boards": ["conditioner", "sensor"], "connector_required": True},
            {"net": "vss", "boards": ["conditioner", "sensor"], "connector_required": True},
        ])
        pins = contract["partition"]["connector_contract"]["connectors"][0]["pins"]
        self.assertEqual([(item["number"], item["net"]) for item in pins], [
            (1, "stage1"), (2, "vcc"), (3, "vss"), (4, "0")
        ])
        fragment = next(item for item in contract["fixtures"] if item["id"] == "conditioner-upstream-lm324aj")
        self.assertEqual(fragment["model_scope"], "explicit-native-vendor-fixture")
        self.assertIn("LM324AJ", [item["kind"] for item in fragment["components"]])
        load = next(item for item in contract["fixtures"] if item["id"] == "sensor-downstream-input-load")
        self.assertEqual(load["model_scope"], "explicit-native-vendor-load-fixture")
        self.assertIn("LM324AJ", [item["kind"] for item in load["components"]])
        self.assertEqual(
            {item["refdes"]: item.get("value") for item in load["components"]},
            {"R2": "10k", "C2": "31.830989n", "U2": None,
             "RF2": "12.36068k", "RG2": "10k", "RL": "47k"},
        )

    def test_model_version_pin_and_topology_changes_fail_closed(self):
        design = _design()
        with self.assertRaisesRegex(ValueError, "only for Multisim 14.3"):
            validate_lm324aj_design(design, target_multisim_version="14.2")
        changed = copy.deepcopy(design.to_dict())
        next(item for item in changed["components"] if item["refdes"] == "U2")["model"] = "LM324M"
        with self.assertRaisesRegex(ValueError, "model identity"):
            validate_lm324aj_design(changed)
        changed = copy.deepcopy(design.to_dict())
        next(item for item in changed["components"] if item["refdes"] == "U1")["nodes"] = [
            "lp1", "fb1", "vss", "vcc", "stage1"
        ]
        with self.assertRaisesRegex(ValueError, "pin order"):
            validate_lm324aj_design(changed)
        changed = copy.deepcopy(design.to_dict())
        changed["components"] = [item for item in changed["components"] if item["refdes"] != "U2"]
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_lm324aj_design(changed)

    def test_preview_is_com_free_and_does_not_create_output(self):
        contract = prepare_lm324aj_multiboard_contract(_design())
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "preview"
            result = run_lm324aj_multiboard_acceptance(contract, output, execute=False, analysis="ac")
            self.assertEqual(result["status"], "logical-only")
            self.assertEqual(result["verification_status"], "unverified")
            self.assertFalse(result["execution_started"])
            self.assertFalse(output.exists())
            self.assertEqual(result["prepared_artifacts"]["fixture_contract"]["status"], "valid")
            self.assertEqual(result["prepared_artifacts"]["fixture_contract"]["coverage_status"], "complete")

    def _prepared_board_and_report(self):
        contract = prepare_lm324aj_multiboard_contract(_design())
        logical = materialize_circuit_design_partition(
            _design(), contract["partition"], target_multisim_version="14.3"
        )
        prepared = materialize_multiboard_fixture_artifacts(logical, contract["fixtures"])
        board = next(item for item in prepared["boards"] if item["board_id"] == "conditioner")
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        parts = _board_vendor_parts(board)
        expected = {}
        from multisim_mcp.linear_reference import expected_native_pins
        expected.update(expected_native_pins(parts))
        for item in board["design"]["components"]:
            if item["kind"] == "HDR1X4":
                expected[item["refdes"]] = {f"P{i}": node for i, node in enumerate(item["nodes"], 1)}
        rows = ["header", "--------------------------------------"]
        for refdes, pins in expected.items():
            for pin, net in pins.items():
                rows.append(f"{net} circuit {refdes} {pin}")
        rows.append("--------------------------------------")
        (root / "reopened-report-netlist.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
        (root / "circuit.ms14").write_bytes(b"placeholder")
        values = {part.refdes: float(part.value[:-1]) * 1000 if part.value and part.value.endswith("k") else 1.0
                  for part in parts if part.kind in {"R", "C"}}
        # Exact SI values are easier to obtain from the parser than from suffixes.
        from multisim_mcp.linear_reference import scalar
        values = {part.refdes: scalar(part.value or "0") for part in parts if part.kind in {"R", "C"}}
        return board, root, values

    def test_reopened_contract_accepts_all_pins_values_and_model_identity(self):
        board, root, values = self._prepared_board_and_report()
        with patch("multisim_mcp.multisim_client.Ms14Codec") as codec, patch(
            "multisim_mcp.vendor_multiboard.vendor_model_fingerprints",
            return_value={"U1": {"model_name": "LM158_4"}, "U2": {"model_name": "LM158_4"}},
        ):
            codec.return_value.decode.return_value = {"xml": str(root / "reopened-model.xml")}
            result = validate_reopened_lm324aj_contract(board, _Client(values), root)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["pins"]["ok"])
        self.assertEqual(set(result["vendor_model_fingerprints"]), {"U1", "U2"})

    def test_reopened_contract_rejects_pin_value_and_model_faults(self):
        board, root, values = self._prepared_board_and_report()
        report = (root / "reopened-report-netlist.txt").read_text(encoding="utf-8")
        (root / "reopened-report-netlist.txt").write_text(report.replace("stage1 circuit U1A 1OUT", "wrong circuit U1A 1OUT"), encoding="utf-8")
        with patch("multisim_mcp.multisim_client.Ms14Codec") as codec, patch(
            "multisim_mcp.vendor_multiboard.vendor_model_fingerprints", return_value={"U1": {}, "U2": {}}
        ):
            codec.return_value.decode.return_value = {"xml": str(root / "reopened-model.xml")}
            self.assertEqual(validate_reopened_lm324aj_contract(board, _Client(values), root)["status"], "fail")
        (root / "reopened-report-netlist.txt").write_text(report, encoding="utf-8")
        values["R2"] = "not-a-number"
        with patch("multisim_mcp.multisim_client.Ms14Codec") as codec, patch(
            "multisim_mcp.vendor_multiboard.vendor_model_fingerprints", return_value={"U1": {}, "U2": {}}
        ):
            codec.return_value.decode.return_value = {"xml": str(root / "reopened-model.xml")}
            result = validate_reopened_lm324aj_contract(board, _Client(values), root)
        self.assertEqual(result["status"], "fail")
        self.assertIsNone(result["rlc_values"]["R2"]["actual_si"])
        with patch("multisim_mcp.multisim_client.Ms14Codec") as codec, patch(
            "multisim_mcp.vendor_multiboard.vendor_model_fingerprints", side_effect=ValueError("changed model")
        ):
            codec.return_value.decode.return_value = {"xml": str(root / "reopened-model.xml")}
            result = validate_reopened_lm324aj_contract(board, _Client({k: v for k, v in values.items() if k != "R2"}), root)
        self.assertEqual(result["status"], "fail")
        self.assertIn("changed model", result["vendor_model_error"])


if __name__ == "__main__":
    unittest.main()
