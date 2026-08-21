from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.experiment_pipeline import MultisimExperimentPipeline
from multisim_mcp.workspace_manifest import (
    DIRECTORY_MANIFEST_NAME,
    read_directory_manifest,
)


RAW_FIXTURE = """Title: fixture
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

ARTIFACT_NAMES = (
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
    "report.zh-CN.html",
    "report.en.html",
    "report.zh-CN.pdf",
    "report.en.pdf",
    "manifest.json",
    DIRECTORY_MANIFEST_NAME,
)


def _schematic_executor(
    netlist: str, output_ms14: str, **kwargs: object
) -> dict[str, object]:
    design = Path(output_ms14)
    design.write_bytes(b"new-ms14")
    xml = Path(str(design) + ".xml")
    xml.write_text("<xml />", encoding="utf-8")
    image = Path(str(kwargs["image_path"]))
    image.write_bytes(b"new-png")
    return {
        "success": True,
        "ms14": str(design),
        "xml": str(xml),
        "image": str(image),
        "build": {"model_warnings": []},
    }


def _simulation_executor(
    netlist: str, commands: str, **kwargs: object
) -> dict[str, object]:
    root = Path(str(kwargs["output_dir"]))
    paths = {
        "raw": root / "result.raw",
        "csv": root / "data.csv",
        "log": root / "run.log",
        "commands": root / "run.txt",
        "netlist": root / "circuit.cir",
    }
    paths["raw"].write_text(RAW_FIXTURE, encoding="utf-8")
    paths["csv"].write_text("V(in),V(out)\n5,2.5\n", encoding="utf-8")
    paths["log"].write_text("ok\n", encoding="utf-8")
    paths["commands"].write_text(commands + "\n", encoding="utf-8")
    paths["netlist"].write_text(netlist, encoding="utf-8")
    return {
        "success": True,
        **{name: str(path) for name, path in paths.items()},
        "columns": ["V(in)", "V(out)"],
        "rows": [[5.0, 2.5]],
        "n_points": 1,
        "measurements": [],
    }


def _report_exporter(root: Path, experiment_id: str) -> dict[str, object]:
    for name in (
        "report.zh-CN.html",
        "report.en.html",
        "report.zh-CN.pdf",
        "report.en.pdf",
        "manifest.json",
    ):
        (root / name).write_bytes(f"new-{name}".encode("utf-8"))
    return {"success": True, "experiment_id": experiment_id}


def _resource_registrar(root: str) -> dict[str, object]:
    return {
        "success": True,
        "experiment_id": "exp-pipeline-test",
        "resources": {"report": "multisim://experiments/test/report"},
        "output_dir": root,
    }


class ExperimentPipelineTest(unittest.TestCase):
    def _pipeline(self) -> MultisimExperimentPipeline:
        return MultisimExperimentPipeline(
            _schematic_executor,
            _simulation_executor,
            report_exporter=_report_exporter,
            resource_registrar=_resource_registrar,
        )

    def test_pipeline_publishes_complete_transaction_and_cleans_stage(self) -> None:
        progress: list[tuple[str, int]] = []
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "experiment"
            result = self._pipeline().run(
                "V1 in 0 5\nR1 in out 1k\nR2 out 0 1k\n.end\n",
                "op",
                str(output),
                checkpoint=lambda stage, value, message: progress.append(
                    (stage, value)
                ),
            )
            self.assertEqual(
                {path.name for path in output.iterdir() if path.is_file()},
                set(ARTIFACT_NAMES),
            )
            self.assertFalse(
                list(output.parent.glob(f".{output.name}.multisim-mcp-*"))
            )
            directory_manifest = read_directory_manifest(output)
            self.assertEqual(directory_manifest.directory_kind, "experiment")
            self.assertEqual(directory_manifest.state, "succeeded")
            self.assertEqual(directory_manifest.metadata["verified"], False)
            self.assertEqual(
                {item.path for item in directory_manifest.artifacts},
                set(ARTIFACT_NAMES) - {DIRECTORY_MANIFEST_NAME},
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["experiment_id"], "exp-pipeline-test")
        self.assertEqual(progress[0], ("preflight", 3))
        self.assertEqual(progress[-1], ("complete", 100))

    def test_mid_publish_failure_restores_every_previous_artifact(self) -> None:
        real_replace = os.replace
        publish_count = 0

        def fail_third_publication(source: object, destination: object) -> None:
            nonlocal publish_count
            if str(source).endswith(".tmp"):
                publish_count += 1
                if publish_count == 3:
                    raise OSError("injected publication failure")
            real_replace(source, destination)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "experiment"
            output.mkdir()
            for name in ARTIFACT_NAMES:
                (output / name).write_bytes(f"old-{name}".encode("utf-8"))
            with patch(
                "multisim_mcp.experiment_pipeline.os.replace",
                side_effect=fail_third_publication,
            ):
                with self.assertRaisesRegex(OSError, "injected publication"):
                    self._pipeline().run(
                        "V1 in 0 5\nR1 in out 1k\nR2 out 0 1k\n.end\n",
                        "op",
                        str(output),
                        overwrite=True,
                    )

            for name in ARTIFACT_NAMES:
                self.assertEqual(
                    (output / name).read_bytes(),
                    f"old-{name}".encode("utf-8"),
                )
            self.assertFalse(list(output.glob(".*.tmp")))
            self.assertFalse(
                list(output.parent.glob(f".{output.name}.multisim-mcp-*"))
            )


if __name__ == "__main__":
    unittest.main()
