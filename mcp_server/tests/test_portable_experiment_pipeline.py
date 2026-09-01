from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp import server
from multisim_mcp.backend_selection import (
    EXPERIMENT_BACKEND_ENV,
    normalize_experiment_backend,
    selected_experiment_backend,
)
from multisim_mcp.experiment_pipeline import MultisimExperimentPipeline
from multisim_mcp.experiment_resources import (
    clear_experiment_registry,
    list_artifacts,
    read_text_artifact,
)
from multisim_mcp.portable_schematic import render_portable_schematic
from multisim_mcp.workspace_manifest import read_directory_manifest


RAW = """Title: portable
Plotname: Operating Point
Flags: real
No. Variables: 2
No. Points: 1
Variables:
0 v(in) voltage
1 v(out) voltage
Values:
0 5
 2.5
"""


def _simulation_executor(netlist: str, commands: str, **kwargs: object) -> dict[str, object]:
    root = Path(str(kwargs["output_dir"]))
    paths = {
        "raw": root / "result.raw",
        "csv": root / "data.csv",
        "log": root / "run.log",
        "commands": root / "run.txt",
        "netlist": root / "circuit.cir",
    }
    paths["raw"].write_text(RAW, encoding="utf-8")
    paths["csv"].write_text("v(in),v(out)\n5,2.5\n", encoding="utf-8")
    paths["log"].write_text("ok\n", encoding="utf-8")
    paths["commands"].write_text(commands + "\n", encoding="utf-8")
    paths["netlist"].write_text(netlist, encoding="utf-8")
    return {
        "success": True,
        **{name: str(path) for name, path in paths.items()},
        "columns": ["v(in)", "v(out)"],
        "rows": [[5.0, 2.5]],
        "n_points": 1,
        "measurements": [],
        "timed_out": False,
        "last_error": "",
    }


class PortableExperimentPipelineTest(unittest.TestCase):
    def tearDown(self) -> None:
        clear_experiment_registry()

    def test_complete_open_backend_package_is_honest_and_registerable(self) -> None:
        pipeline = MultisimExperimentPipeline(
            render_portable_schematic,
            _simulation_executor,
            backend_id="ngspice",
            backend_display_name="ngspice open-source simulator",
            schematic_artifact_names=("schematic.svg", "schematic.png", "backend.json"),
            design_filename="schematic.svg",
            editable_schematic=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "portable"
            result = pipeline.run(
                "V1 in 0 5\nR1 in out 1k\nR2 out 0 1k\n.end\n",
                "op",
                str(output),
            )
            names = {path.name for path in output.iterdir() if path.is_file()}
            self.assertNotIn("circuit.ms14", names)
            self.assertIn("schematic.svg", names)
            self.assertIn("spice-compatibility.json", names)
            self.assertTrue((output / "schematic.png").read_bytes().startswith(b"\x89PNG"))
            backend = json.loads((output / "backend.json").read_text(encoding="utf-8"))
            self.assertEqual(backend["backend_id"], "ngspice")
            compatibility = json.loads(
                (output / "spice-compatibility.json").read_text(encoding="utf-8")
            )
            self.assertEqual(compatibility["backend"]["backend_id"], "ngspice")
            html = (output / "report.zh-CN.html").read_text(encoding="utf-8")
            self.assertIn("data:image/svg+xml;base64,", html)
            directory_manifest = read_directory_manifest(output)
            self.assertEqual(directory_manifest.metadata["backend_id"], "ngspice")
            listing = list_artifacts(result["experiment_id"])
            self.assertEqual(listing["backend_id"], "ngspice")
            self.assertIn("schematic_svg", {item["name"] for item in listing["artifacts"]})
            self.assertEqual(
                json.loads(read_text_artifact(result["experiment_id"], "backend"))["backend_id"],
                "ngspice",
            )
            self.assertEqual(
                json.loads(
                    read_text_artifact(
                        result["experiment_id"], "spice_compatibility"
                    )
                )["schema_version"],
                1,
            )
        self.assertEqual(result["backend_id"], "ngspice")
        self.assertNotIn("circuit", result["resources"])
        self.assertIn("schematic", result["resources"])

    def test_backend_selection_is_validated_and_server_wires_portable_profile(self) -> None:
        self.assertEqual(selected_experiment_backend({}), "multisim")
        self.assertEqual(normalize_experiment_backend(" NGSPICE "), "ngspice")
        with self.assertRaises(ValueError):
            normalize_experiment_backend("shell")
        expected = {
            "success": True,
            "experiment_id": "exp-test",
            "resources": {},
            "schematic": {},
            "simulation": {},
            "report": "report.md",
            "plot": "plot.svg",
            "output_dir": "output",
        }
        with patch.dict(os.environ, {EXPERIMENT_BACKEND_ENV: "ngspice"}), patch.object(
            server, "MultisimExperimentPipeline"
        ) as pipeline_type:
            pipeline_type.return_value.run.return_value = expected
            result = server._run_circuit_experiment_transaction(
                "V1 in 0 1\nR1 in 0 1k\n.end\n", "op", "output"
            )
        self.assertEqual(result, expected)
        self.assertEqual(pipeline_type.call_args.kwargs["backend_id"], "ngspice")
        self.assertFalse(pipeline_type.call_args.kwargs["editable_schematic"])


if __name__ == "__main__":
    unittest.main()
