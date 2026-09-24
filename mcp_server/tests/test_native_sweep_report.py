from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.native_sweep import rank_native_sweep_results
from multisim_mcp.native_sweep_report import (
    compare_native_sweep_baseline,
    export_native_sweep_report,
)


def _ranking() -> dict:
    return rank_native_sweep_results(
        {
            "state": "completed",
            "circuit": {"file": "C:/circuits/source.ms14"},
            "original_values": {"R1": 50.0},
            "results": [
                {"parameters": {"R1": 40.0}, "analysis": {"rows": [[0.0, 0.4]]}},
                {"parameters": {"R1": 50.0}, "analysis": {"rows": [[0.0, 10.8]]}},
                {"parameters": {"R1": 60.0}, "analysis": {"rows": [[0.0, 0.75]]}},
            ],
        },
        {
            "signal": "V(out)",
            "metric": "peak_to_peak",
            "direction": "target",
            "target": 1.0,
        },
    )


def _sweep() -> dict:
    return {
        "state": "completed",
        "circuit": {"file": "C:/circuits/source.ms14"},
        "original_values": {"R1": 50.0},
        "results": [
            {"parameters": {"R1": 40.0}, "analysis": {"rows": [[0.0, 0.4]]}},
            {"parameters": {"R1": 50.0}, "analysis": {"rows": [[0.0, 10.8]]}},
            {"parameters": {"R1": 60.0}, "analysis": {"rows": [[0.0, 0.75]]}},
        ],
    }


class NativeSweepReportTest(unittest.TestCase):
    def test_compares_best_candidate_with_original_baseline(self) -> None:
        comparison = compare_native_sweep_baseline(_ranking())
        self.assertEqual(comparison["state"], "improved")
        self.assertEqual(comparison["baseline"]["parameters"], {"R1": 50.0})
        self.assertEqual(comparison["best"]["parameters"], {"R1": 60.0})
        self.assertEqual(
            comparison["parameter_changes"],
            [{"refdes": "R1", "before": 50.0, "after": 60.0}],
        )
        self.assertAlmostEqual(comparison["metric_improvement"], 9.55)
        self.assertAlmostEqual(comparison["relative_improvement_percent"], 97.44897959183673)
        self.assertFalse(comparison["source_mutated"])
        self.assertRegex(comparison["comparison_digest"], r"^[0-9a-f]{64}$")

    def test_requires_complete_original_value_candidate(self) -> None:
        ranking = rank_native_sweep_results(
            {
                "state": "completed",
                "original_values": {"R1": 50.0, "C1": 1e-6},
                "results": [
                    {"parameters": {"R1": 50.0}, "analysis": {"rows": [[1.0, 2.0]]}},
                ],
            },
            {"signal": "V(out)", "metric": "max", "direction": "maximize"},
        )
        with self.assertRaisesRegex(ValueError, "original-value baseline"):
            compare_native_sweep_baseline(ranking)

    def test_rejects_degenerate_or_tampered_ranking(self) -> None:
        degenerate = rank_native_sweep_results(
            {
                "state": "completed",
                "original_values": {"R1": 50.0},
                "results": [
                    {"parameters": {"R1": 50.0}, "analysis": {"rows": [[0.0, 0.0]]}},
                ],
            },
            {"signal": "V(out)", "metric": "max", "direction": "maximize"},
        )
        with self.assertRaisesRegex(ValueError, "quality must be valid"):
            compare_native_sweep_baseline(degenerate)
        tampered = _ranking()
        tampered["best"]["value"] = 99.0
        with self.assertRaisesRegex(ValueError, "ranking_digest"):
            compare_native_sweep_baseline(tampered)

    def test_exports_bilingual_report_and_integrity_manifest(self) -> None:
        comparison = compare_native_sweep_baseline(_ranking())
        with tempfile.TemporaryDirectory() as root:
            output_dir = Path(root) / "report"
            result = export_native_sweep_report(comparison, str(output_dir))
            self.assertEqual(result["state"], "completed")
            self.assertFalse(result["source_mutated"])
            report = Path(result["report_path"]).read_text(encoding="utf-8")
            self.assertIn("Multisim 原生参数优化报告", report)
            self.assertIn("English summary", report)
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["comparison_digest"], comparison["comparison_digest"])
            self.assertEqual(len(manifest["files"]), 2)
            for entry in manifest["files"]:
                payload = (output_dir / entry["name"]).read_bytes()
                self.assertEqual(entry["size"], len(payload))
                self.assertEqual(entry["sha256"], hashlib.sha256(payload).hexdigest())

    def test_rejects_tampered_comparison_and_nonempty_output(self) -> None:
        comparison = compare_native_sweep_baseline(_ranking())
        tampered = dict(comparison)
        tampered["metric_improvement"] = -1.0
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "comparison_digest"):
                export_native_sweep_report(tampered, str(Path(root) / "tampered"))
            occupied = Path(root) / "occupied"
            occupied.mkdir()
            (occupied / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "must be empty"):
                export_native_sweep_report(comparison, str(occupied))

    def test_packages_optimized_copy_with_relative_report_link(self) -> None:
        comparison = compare_native_sweep_baseline(_ranking())
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "optimized.ms14"
            source.write_bytes(b"optimized-circuit")
            output_dir = Path(root) / "package"
            result = export_native_sweep_report(
                comparison, str(output_dir), str(source)
            )
            packaged = output_dir / "optimized-circuit.ms14"
            self.assertEqual(packaged.read_bytes(), source.read_bytes())
            self.assertEqual(result["optimized_copy_path"], str(packaged.resolve()))
            report = Path(result["report_path"]).read_text(encoding="utf-8")
            self.assertIn("optimized-circuit.ms14", report)
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["files"]), 3)
            self.assertEqual(manifest["optimized_copy"]["name"], "optimized-circuit.ms14")
            self.assertEqual(
                manifest["optimized_copy"]["sha256"],
                hashlib.sha256(packaged.read_bytes()).hexdigest(),
            )

    def test_exports_waveform_csv_and_svg_when_sweep_is_attached(self) -> None:
        sweep = _sweep()
        comparison = compare_native_sweep_baseline(_ranking())
        with tempfile.TemporaryDirectory() as root:
            output_dir = Path(root) / "waveforms"
            result = export_native_sweep_report(
                comparison, str(output_dir), sweep_result=sweep
            )
            csv_path = Path(result["waveform_csv_path"])
            svg_path = Path(result["waveform_svg_path"])
            self.assertTrue(csv_path.is_file())
            self.assertTrue(svg_path.is_file())
            self.assertIn("sample_index,baseline,best", csv_path.read_text(encoding="utf-8"))
            self.assertIn("Baseline vs optimized waveform", svg_path.read_text(encoding="utf-8"))
            report = Path(result["report_path"]).read_text(encoding="utf-8")
            self.assertIn("waveform-comparison.csv", report)
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["waveform_evidence"]["sample_limit"], 1000)
            self.assertEqual(len(manifest["files"]), 4)

    def test_rejects_sweep_with_mismatched_ranking(self) -> None:
        comparison = compare_native_sweep_baseline(_ranking())
        sweep = _sweep()
        sweep["results"][2]["analysis"]["rows"] = [[0.0, 1.25]]
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "ranking_digest"):
                export_native_sweep_report(
                    comparison, str(Path(root) / "mismatch"), sweep_result=sweep
                )

    def test_rejects_invalid_optimized_copy(self) -> None:
        comparison = compare_native_sweep_baseline(_ranking())
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "must end with .ms14"):
                export_native_sweep_report(
                    comparison, str(Path(root) / "report"), str(Path(root) / "bad.txt")
                )


if __name__ == "__main__":
    unittest.main()
