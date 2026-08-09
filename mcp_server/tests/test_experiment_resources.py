"""COM-free tests for experiment resource handles and MCP resource reads."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import Client
from mcp.types import BlobResourceContents, TextResourceContents

from multisim_mcp.experiment_resources import (
    clear_experiment_registry,
    experiment_manifest,
    read_binary_artifact,
    read_text_artifact,
    register_experiment,
)
from multisim_mcp.server import mcp

TEXT_ARTIFACTS = {
    "circuit.ms14.xml": "<xml />\n",
    "data.csv": "time,V(out)\n0,0\n1,1\n",
    "run.log": "simulation complete\n",
    "run.txt": "tran 1u 1m\n",
    "circuit.cir": "V1 in 0 1\nR1 in 0 1k\n.end\n",
    "plot.svg": '<svg xmlns="http://www.w3.org/2000/svg" />\n',
    "report.md": "# Experiment\n\nPassed.\n",
}
BINARY_ARTIFACTS = {
    "circuit.ms14": b"MS14-fixture",
    "schematic.png": b"\x89PNG\r\n\x1a\nfixture",
    "result.raw": b"raw-fixture",
}


def _write_experiment(root: Path) -> None:
    for name, content in TEXT_ARTIFACTS.items():
        (root / name).write_text(content, encoding="utf-8")
    for name, content in BINARY_ARTIFACTS.items():
        (root / name).write_bytes(content)


class ExperimentResourceRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_experiment_registry()

    def tearDown(self) -> None:
        clear_experiment_registry()

    def test_register_returns_stable_opaque_handles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_experiment(root)
            first = register_experiment(str(root))
            second = register_experiment(str(root))

            self.assertTrue(first["success"])
            self.assertEqual(first["experiment_id"], second["experiment_id"])
            self.assertRegex(first["experiment_id"], r"^exp-[0-9a-f]{24}$")
            self.assertEqual(
                first["resources"]["report"],
                f"multisim://experiments/{first['experiment_id']}/report",
            )
            self.assertNotIn(str(root), first["resources"]["report"])

    def test_manifest_hashes_and_allowlisted_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_experiment(root)
            registered = register_experiment(str(root))
            experiment_id = registered["experiment_id"]

            manifest = experiment_manifest(experiment_id)
            by_filename = {item["filename"]: item for item in manifest["artifacts"]}
            self.assertEqual(
                by_filename["report.md"]["sha256"],
                hashlib.sha256((root / "report.md").read_bytes()).hexdigest(),
            )
            self.assertEqual(
                read_text_artifact(experiment_id, "report"),
                (root / "report.md").read_bytes().decode("utf-8"),
            )
            self.assertEqual(
                read_binary_artifact(experiment_id, "schematic"),
                BINARY_ARTIFACTS["schematic.png"],
            )

    def test_missing_or_unknown_experiment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_experiment(root)
            (root / "report.md").unlink()
            with self.assertRaises(FileNotFoundError):
                register_experiment(str(root))
        with self.assertRaises(ValueError):
            read_text_artifact("../../secret", "report")
        with self.assertRaises(KeyError):
            read_text_artifact("exp-" + "0" * 24, "report")

    def test_resource_size_limit_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_experiment(root)
            experiment_id = register_experiment(str(root))["experiment_id"]
            with patch.dict(os.environ, {"MULTISIM_MCP_RESOURCE_MAX_BYTES": "4"}):
                with self.assertRaises(ValueError):
                    read_binary_artifact(experiment_id, "schematic")


class McpExperimentResourceTest(unittest.IsolatedAsyncioTestCase):
    async def test_text_and_binary_resources_are_protocol_readable(self) -> None:
        clear_experiment_registry()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write_experiment(root)

                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "register_experiment_artifacts", {"output_dir": str(root)}
                    )
                    self.assertFalse(result.is_error)
                    registered = result.structured_content
                    self.assertIsNotNone(registered)
                    resources = registered["resources"]
                    report = await client.read_resource(resources["report"])
                    schematic = await client.read_resource(resources["schematic"])
                    manifest = await client.read_resource(resources["manifest"])

                self.assertIsInstance(report.contents[0], TextResourceContents)
                self.assertEqual(report.contents[0].mime_type, "text/markdown")
                self.assertIn("# Experiment", report.contents[0].text)
                self.assertIsInstance(schematic.contents[0], BlobResourceContents)
                self.assertEqual(schematic.contents[0].mime_type, "image/png")
                self.assertIsInstance(manifest.contents[0], TextResourceContents)
                self.assertIn(registered["experiment_id"], manifest.contents[0].text)
        finally:
            clear_experiment_registry()

    async def test_tools_run_on_the_dedicated_com_thread(self) -> None:
        from multisim_mcp import server

        fake_runtime = lambda: {"thread": threading.current_thread().name}
        with patch.object(server, "runtime_diagnostics", side_effect=fake_runtime):
            async with Client(mcp) as client:
                first = await client.call_tool("runtime_status", {})
                second = await client.call_tool("runtime_status", {})

        first_payload = json.loads(first.content[0].text)
        second_payload = json.loads(second.content[0].text)
        self.assertEqual(first_payload["thread"], second_payload["thread"])
        self.assertTrue(first_payload["thread"].startswith("multisim-com"))


if __name__ == "__main__":
    unittest.main()
