import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from multisim_mcp import cli
from multisim_mcp.native_project_run import run_native_project


REQUEST = {"schema_version": 1, "title": "Divider", "application": "test",
           "experiments": [{"type": "op", "outputs": ["V(out)"]}]}


class NativeProjectRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "source.ms14"
        self.source.write_bytes(b"original")
        self.output = Path(self.tmp.name) / "out"
        self.client = Mock()
        self.client.connect.return_value = {"version": "Multisim test"}
        self.client.enum_components.return_value = ["R1"]
        self.values = {"R1": 1000.0}
        self.client.get_rlc_value.side_effect = lambda ref: {"value": self.values[ref]}
        def set_value(ref, value):
            self.values[ref] = value
            return {"value": value}
        self.client.set_rlc_value.side_effect = set_value
        self.client.save_circuit.side_effect = lambda path: Path(path).write_bytes(str(self.values).encode())
        self.client.report_netlist.side_effect = lambda path, *args: Path(path).write_text("R1 in out 2000\n")
        self.client.report_bom.side_effect = lambda path: Path(path).write_text("R1")
        self.client.get_circuit_image.side_effect = lambda path: Path(path).write_bytes(b"image")

    def test_preview_does_not_touch_com_or_create_outputs(self):
        result = run_native_project(REQUEST, str(self.source), str(self.output), client=self.client)
        self.assertEqual(result["mode"], "preview")
        self.assertEqual(self.client.mock_calls, [])
        self.assertFalse(self.output.exists())

    def test_execute_records_parameter_application_restore_and_source_hash(self):
        def analyze(client, action, root):
            self.assertEqual(self.values["R1"], 2000)
            root.mkdir()
            return {"success": True, "signals": {"V(out)": [10 / 3]}}
        result = run_native_project(REQUEST, str(self.source), str(self.output), execute=True,
                                    parameters={"R1": "2k"}, client=self.client, analyzer=analyze)
        self.assertTrue(result["success"])
        self.assertEqual(self.values["R1"], 1000)
        self.assertEqual(self.source.read_bytes(), b"original")
        record = json.loads((self.output / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["verification_status"], "unverified")
        self.assertIn(b"2000", (self.output / "circuit.ms14").read_bytes())
        manifest = json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))
        for artifact in manifest["artifacts"]:
            self.assertEqual(artifact["sha256"], hashlib.sha256((self.output / artifact["path"]).read_bytes()).hexdigest())

    def test_failure_is_persisted_without_false_completed_status(self):
        result = run_native_project(REQUEST, str(self.source), str(self.output), execute=True,
                                    parameters={"R1": "2k"}, client=self.client,
                                    analyzer=Mock(side_effect=TimeoutError("not ready")))
        self.assertFalse(result["success"])
        self.assertEqual(self.values["R1"], 1000)
        record = json.loads((self.output / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")

    def test_cli_preview_uses_source_package_and_does_not_connect(self):
        request = Path(self.tmp.name) / "request.json"
        request.write_text(json.dumps(REQUEST), encoding="utf-8")
        with patch("builtins.print"), patch("multisim_mcp.com_worker_client.MultisimWorkerProcess") as worker:
            code = cli.main(["native-project-run", "--request", str(request), "--source", str(self.source),
                             "--output", str(self.output), "--set", "R1=2k", "--json"])
        self.assertEqual(code, 0)
        worker.assert_not_called()
