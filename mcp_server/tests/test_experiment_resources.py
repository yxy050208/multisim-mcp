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
from multisim_mcp.job_engine import ExperimentJobManager
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

    def test_verification_is_optional_and_registered_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_experiment(root)
            without = register_experiment(str(root))
            self.assertNotIn("verification", without["resources"])
            (root / "verification.json").write_text(
                '{"schema_version": 1, "overall_status": "pass"}\n',
                encoding="utf-8",
            )
            with_verification = register_experiment(str(root))
            self.assertIn("verification", with_verification["resources"])
            manifest = experiment_manifest(with_verification["experiment_id"])
            self.assertIn(
                "verification.json",
                {item["filename"] for item in manifest["artifacts"]},
            )

    def test_formal_report_resource_paths_match_protocol_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_experiment(root)
            (root / "report.zh-CN.html").write_text("<html>中文</html>", encoding="utf-8")
            (root / "report.en.html").write_text("<html>English</html>", encoding="utf-8")
            (root / "report.zh-CN.pdf").write_bytes(b"%PDF-1.4 zh")
            (root / "report.en.pdf").write_bytes(b"%PDF-1.4 en")
            (root / "manifest.json").write_text('{"schema_version": 1}\n', encoding="utf-8")
            registered = register_experiment(str(root))
        experiment_id = registered["experiment_id"]
        self.assertEqual(
            registered["resources"]["formal_html_zh"],
            f"multisim://experiments/{experiment_id}/formal-html-zh",
        )
        self.assertEqual(
            registered["resources"]["reproducibility_manifest"],
            f"multisim://experiments/{experiment_id}/reproducibility-manifest",
        )

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

    async def test_job_tools_and_status_resource_are_protocol_readable(self) -> None:
        from multisim_mcp import server

        with tempfile.TemporaryDirectory() as tmp:
            manager = ExperimentJobManager(Path(tmp) / "state", start=False)
            previous = server._JOB_MANAGER
            server._JOB_MANAGER = manager
            try:
                async with Client(mcp) as client:
                    submitted = await client.call_tool(
                        "submit_circuit_experiment",
                        {
                            "netlist": "V1 a 0 1\nR1 a 0 1k\n.end\n",
                            "commands": "op",
                            "output_dir": str(Path(tmp) / "output"),
                            "timeout": 1,
                            "job_timeout": 10,
                            "heartbeat_timeout": 10,
                        },
                    )
                    self.assertFalse(submitted.is_error)
                    job_id = submitted.structured_content["job_id"]
                    status = await client.call_tool(
                        "get_experiment_job", {"job_id": job_id}
                    )
                    listing = await client.call_tool("list_experiment_jobs", {})
                    resource = await client.read_resource(
                        f"multisim://jobs/{job_id}"
                    )
                    cancelled = await client.call_tool(
                        "cancel_experiment_job", {"job_id": job_id}
                    )

                self.assertEqual(status.structured_content["state"], "queued")
                self.assertEqual(listing.structured_content["count"], 1)
                self.assertIsInstance(resource.contents[0], TextResourceContents)
                self.assertIn(job_id, resource.contents[0].text)
                self.assertEqual(cancelled.structured_content["state"], "cancelled")
            finally:
                server._JOB_MANAGER = previous
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
