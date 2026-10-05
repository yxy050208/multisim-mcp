import tempfile
import unittest
from pathlib import Path

from multisim_mcp.eda_core import CircuitComponent, CircuitDesign
from multisim_mcp.multiboard_plan import plan_multiboard_partition
from multisim_mcp.native_multiboard_acceptance import (
    _write_acceptance_report,
    _write_native_op_csv,
    run_native_multiboard_acceptance,
)


class NativeMultiboardAcceptanceTest(unittest.TestCase):
    def test_preview_is_com_free_and_preserves_unverified_boundary(self):
        design = CircuitDesign(
            design_id="native-preview",
            title="Native preview",
            components=(
                CircuitComponent("V1", "V", ("bus", "0"), value="5"),
                CircuitComponent("R1", "R", ("bus", "sense"), value="1k"),
                CircuitComponent("R2", "R", ("sense", "0"), value="1k"),
            ),
        )
        partition = plan_multiboard_partition(
            [
                {"refdes": "V1", "board": "power", "nodes": ["bus", "0"]},
                {"refdes": "R1", "board": "signal", "nodes": ["bus", "sense"]},
                {"refdes": "R2", "board": "signal", "nodes": ["sense", "0"]},
            ],
            [{"id": "power"}, {"id": "signal"}],
        )
        fixtures = [
            {"id": "power-observe", "board_id": "power", "kind": "observation", "net": "bus"},
            {"id": "power-anchor", "board_id": "power", "kind": "resistor_termination", "net": "bus", "reference_net": "0", "refdes": "RFIX1", "value": "1G"},
            {"id": "power-ground", "board_id": "power", "kind": "ground_reference", "net": "0"},
            {"id": "signal-drive", "board_id": "signal", "kind": "voltage_source", "net": "bus", "reference_net": "0", "refdes": "VFIX1", "value": "5"},
            {"id": "signal-observe-bus", "board_id": "signal", "kind": "observation", "net": "bus"},
            {"id": "signal-ground", "board_id": "signal", "kind": "ground_reference", "net": "0"},
            {"id": "signal-observe-sense", "board_id": "signal", "kind": "observation", "net": "sense"},
        ]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "acceptance"
            result = run_native_multiboard_acceptance(
                design, partition, fixtures, output, execute=False, target_multisim_version="14.3"
            )
            self.assertEqual(result["status"], "logical-only")
            self.assertEqual(result["verification_status"], "unverified")
            self.assertFalse(result["execution_started"])
            self.assertFalse(output.exists())
            self.assertEqual(result["prepared_artifacts"]["fixture_contract"]["coverage_status"], "complete")

    def test_evidence_helpers_export_machine_and_human_readable_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            csv_path = root / "native-op.csv"
            report_path = root / "acceptance-report.md"
            _write_native_op_csv(csv_path, {"V(z)": 2.5, "V(a)": 5.0})
            _write_acceptance_report(report_path, {
                "status": "accepted",
                "verification_status": "native-verified",
                "target_multisim_version": "14.3",
                "multisim_version": "Multisim 14.3",
                "native_acceptance": {"all_native_op_ready": True},
                "observations": {"signal": {"z": 2.5}},
                "interface_comparison": {"status": "pass"},
                "full_reference": {"status": "pass"},
            })
            self.assertEqual(csv_path.read_text(encoding="utf-8").splitlines(), [
                "output,value", "V(a),5", "V(z),2.5",
            ])
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("`accepted`", report)
            self.assertIn("`all_native_op_ready`", report)
            self.assertIn("`signal`", report)


if __name__ == "__main__":
    unittest.main()
