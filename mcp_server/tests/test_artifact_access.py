"""COM-free tests for Tool-friendly experiment artifact access."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.experiment_resources import (
    ARTIFACT_EXPORT_DIR_ENV,
    clear_experiment_registry,
    export_artifact,
    list_artifacts,
    read_artifact_page,
    register_experiment,
    summarize_experiment,
)


_REQUIRED = (
    "circuit.ms14",
    "circuit.ms14.xml",
    "schematic.png",
    "data.csv",
    "result.raw",
    "run.log",
    "run.txt",
    "circuit.cir",
    "plot.svg",
    "report.md",
)

_RAW = """Title: fixture
Plotname: Operating Point
Flags: real
No. Variables: 2
No. Points: 1
Variables:
0 x voltage V(in)
1 y voltage V(out)
Values:
0 5
 2.5
"""


def _write_experiment(root: Path, report: str = "abcdef") -> None:
    for filename in _REQUIRED:
        path = root / filename
        if filename == "report.md":
            path.write_text(report, encoding="utf-8")
        elif filename == "result.raw":
            path.write_text(_RAW, encoding="utf-8")
        else:
            path.write_bytes((filename + " fixture\n").encode("utf-8"))


class ArtifactAccessTest(unittest.TestCase):
    def tearDown(self) -> None:
        clear_experiment_registry()

    def test_listing_and_summary_are_metadata_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_experiment(root, report="R" * 5_000)
            (root / "verification.json").write_text(
                json.dumps({"overall_status": "pass"}), encoding="utf-8"
            )
            experiment_id = register_experiment(str(root))["experiment_id"]

            listing = list_artifacts(experiment_id)
            summary = summarize_experiment(experiment_id)

        by_name = {item["name"]: item for item in listing["artifacts"]}
        self.assertTrue(by_name["report"]["text_readable"])
        self.assertFalse(by_name["schematic"]["text_readable"])
        self.assertTrue(by_name["schematic"]["exportable"])
        self.assertFalse(by_name["circuit.ms14.xml"]["exportable"])
        self.assertEqual(summary["verification"]["result"]["overall_status"], "pass")
        self.assertTrue(summary["measurements"]["available"])
        self.assertEqual(summary["measurements"]["point_count"], 1)
        self.assertEqual(summary["measurements"]["columns"][1]["mean"], 2.5)
        self.assertEqual(len(summary["report_excerpt"]), 4_000)
        self.assertTrue(summary["report_truncated"])
        self.assertNotIn("content", summary["artifacts"][0])

    def test_text_pagination_and_binary_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_experiment(root)
            experiment_id = register_experiment(str(root))["experiment_id"]
            page = read_artifact_page(experiment_id, "report", offset=2, max_chars=2)

            self.assertEqual(page["content"], "cd")
            self.assertEqual(page["next_offset"], 4)
            self.assertTrue(page["truncated"])
            with self.assertRaisesRegex(ValueError, "text resource"):
                read_artifact_page(experiment_id, "schematic")
            with self.assertRaisesRegex(ValueError, "offset"):
                read_artifact_page(experiment_id, "report", offset=-1)
            with self.assertRaisesRegex(ValueError, "max_chars"):
                read_artifact_page(experiment_id, "report", max_chars=100_001)

    def test_summary_bounds_untrusted_verification_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_experiment(root)
            requirements = [
                {
                    "id": f"requirement-{index}",
                    "status": "pass",
                    "reason": "x" * 1_000,
                    "value": {"unexpected": "nested"},
                }
                for index in range(30)
            ]
            (root / "verification.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "overall_status": "pass",
                        "counts": {"pass": 30, "extra": "ignored"},
                        "requirements": requirements,
                    }
                ),
                encoding="utf-8",
            )
            experiment_id = register_experiment(str(root))["experiment_id"]
            result = summarize_experiment(experiment_id)["verification"]["result"]

        self.assertEqual(result["requirement_count"], 30)
        self.assertEqual(len(result["requirements"]), 25)
        self.assertTrue(result["requirements_truncated"])
        self.assertEqual(len(result["requirements"][0]["reason"]), 503)
        self.assertNotIn("value", result["requirements"][0])
        self.assertEqual(result["counts"], {"pass": 30})

    def test_export_requires_an_approved_root_and_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            root = Path(tmp)
            export_root = Path(out)
            _write_experiment(root)
            experiment_id = register_experiment(str(root))["experiment_id"]

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, ARTIFACT_EXPORT_DIR_ENV):
                    export_artifact(experiment_id, "schematic")

            with patch.dict(
                os.environ, {ARTIFACT_EXPORT_DIR_ENV: str(export_root)}, clear=False
            ):
                result = export_artifact(
                    experiment_id, "schematic", destination_subdir="images"
                )
                destination = Path(result["destination"])
                self.assertEqual(destination.read_bytes(), (root / "schematic.png").read_bytes())
                self.assertEqual(
                    result["sha256"],
                    hashlib.sha256(destination.read_bytes()).hexdigest(),
                )
                self.assertFalse(list(destination.parent.glob(".*.tmp")))
                with self.assertRaises(FileExistsError):
                    export_artifact(
                        experiment_id, "schematic", destination_subdir="images"
                    )
                replaced = export_artifact(
                    experiment_id,
                    "schematic",
                    destination_subdir="images",
                    overwrite=True,
                )
                self.assertTrue(replaced["success"])

    def test_export_rejects_traversal_absolute_paths_and_source_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            root = Path(tmp)
            _write_experiment(root)
            experiment_id = register_experiment(str(root))["experiment_id"]
            with patch.dict(
                os.environ, {ARTIFACT_EXPORT_DIR_ENV: out}, clear=False
            ):
                with self.assertRaisesRegex(ValueError, "escapes"):
                    export_artifact(experiment_id, "report", "../outside")
                with self.assertRaisesRegex(ValueError, "must be relative"):
                    export_artifact(experiment_id, "report", str(Path(out).resolve()))
            with patch.dict(
                os.environ, {ARTIFACT_EXPORT_DIR_ENV: str(root)}, clear=False
            ):
                with self.assertRaisesRegex(ValueError, "must differ"):
                    export_artifact(experiment_id, "report")


if __name__ == "__main__":
    unittest.main()
