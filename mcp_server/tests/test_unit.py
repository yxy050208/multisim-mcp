"""COM-free unit tests for the Multisim MCP server."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from multisim_mcp.multisim_client import (
    CODEC_PACKAGE,
    Ms14Codec,
    MultisimClient,
    clean_error,
    require_compatible_runtime,
    runtime_diagnostics,
)


class CleanErrorTest(unittest.TestCase):
    def test_empty_and_plain(self) -> None:
        self.assertEqual(clean_error(""), "")
        self.assertEqual(clean_error("ok"), "ok")

    def test_utf8_bytes_wrapped_in_bstr(self) -> None:
        raw = "输出未找到。".encode("utf-8").decode("latin-1")
        self.assertEqual(clean_error(raw), "输出未找到。")

    def test_invalid_bytes_do_not_crash(self) -> None:
        raw = "测试".encode("gbk").decode("latin-1")
        self.assertIsInstance(clean_error(raw), str)


class RowsToDictTest(unittest.TestCase):
    def test_empty_rows(self) -> None:
        client = MultisimClient()
        result = client._rows_to_dict(())
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["shape"], [0, 0])

    def test_shape_and_sampling(self) -> None:
        client = MultisimClient()
        rows = ([0.0, 1.0, 2.0, 3.0, 4.0], [0.5, 0.6, 0.7, 0.8, 0.9])
        result = client._rows_to_dict(rows, max_points=2)
        self.assertEqual(result["shape"], [2, 5])
        self.assertEqual(result["n_points"], 5)
        self.assertLessEqual(result["sampled_points"], 2)
        self.assertEqual(len(result["rows"][0]), len(result["rows"][1]))


class CommandCancellationTest(unittest.TestCase):
    def test_checkpoint_cancellation_stops_simulation_before_return(self) -> None:
        class Circuit:
            SimulationState = 1
            LastErrorMessage = ""
            stop_calls = 0

            def DoCommandLine(self, _command: str, _log: str) -> None:
                return None

            def StopSimulation(self) -> None:
                self.stop_calls += 1
                self.SimulationState = 0

        client = MultisimClient()
        circuit = Circuit()
        client._circuit = circuit
        with tempfile.TemporaryDirectory() as tmp:
            result = client.run_command_file(
                str(Path(tmp) / "run.txt"),
                str(Path(tmp) / "run.log"),
                timeout=2,
                heartbeat=lambda: (_ for _ in ()).throw(
                    InterruptedError("cancel")
                ),
            )
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["timed_out"])
        self.assertEqual(circuit.stop_calls, 1)


class DispatchFallbackTest(unittest.TestCase):
    def test_corrupt_generated_wrapper_falls_back_to_dynamic_dispatch(self) -> None:
        app = object()
        fake_client = SimpleNamespace(
            gencache=SimpleNamespace(
                EnsureDispatch=lambda _prog_id: (_ for _ in ()).throw(
                    AttributeError("CLSIDToClassMap")
                )
            ),
            dynamic=SimpleNamespace(Dispatch=lambda _prog_id: app),
        )
        client = MultisimClient()
        with (
            patch("multisim_mcp.multisim_client.require_compatible_runtime"),
            patch("multisim_mcp.multisim_client.win32_client", fake_client),
            patch.object(client, "_ensure_com"),
        ):
            result = client._ensure_app()
        self.assertIs(result, app)
    def test_dynamic_dispatch_failure_preserves_both_errors(self) -> None:
        def fail_generated(_prog_id: str) -> object:
            raise AttributeError("CLSIDToClassMap")

        def fail_dynamic(_prog_id: str) -> object:
            raise OSError("dynamic failure")

        fake_client = SimpleNamespace(
            gencache=SimpleNamespace(EnsureDispatch=fail_generated),
            dynamic=SimpleNamespace(Dispatch=fail_dynamic),
        )
        client = MultisimClient()
        with (
            patch("multisim_mcp.multisim_client.require_compatible_runtime"),
            patch("multisim_mcp.multisim_client.win32_client", fake_client),
            patch.object(client, "_ensure_com"),
            self.assertRaisesRegex(
                RuntimeError, "Generated-wrapper error: CLSIDToClassMap"
            ),
        ):
            client._ensure_app()


class ConnectionOwnershipTest(unittest.TestCase):
    class App:
        VersionInfo = "Multisim test"
        Path = "Multisim.exe"

        def __init__(self, connected: bool) -> None:
            self.IsConnected = connected
            self.connect_calls = 0
            self.disconnect_calls = 0

        def Connect(self) -> None:
            self.connect_calls += 1
            self.IsConnected = True

        def Disconnect(self) -> None:
            self.disconnect_calls += 1
            self.IsConnected = False

    def test_close_preserves_a_preexisting_connection(self) -> None:
        app = self.App(connected=True)
        client = MultisimClient()
        client._app = app
        with (
            patch("multisim_mcp.multisim_client.require_compatible_runtime"),
            patch.object(client, "_ensure_com"),
        ):
            client.connect()
        client.close()
        self.assertEqual(app.connect_calls, 0)
        self.assertEqual(app.disconnect_calls, 0)

    def test_close_disconnects_a_session_created_by_this_client(self) -> None:
        app = self.App(connected=False)
        client = MultisimClient()
        client._app = app
        with (
            patch("multisim_mcp.multisim_client.require_compatible_runtime"),
            patch.object(client, "_ensure_com"),
        ):
            client.connect()
        client.close()
        self.assertEqual(app.connect_calls, 1)
        self.assertEqual(app.disconnect_calls, 1)


class RuntimeDiagnosticsTest(unittest.TestCase):
    def test_reports_interpreter_architecture(self) -> None:
        result = runtime_diagnostics()
        self.assertIn(result["python_bits"], (32, 64))
        self.assertEqual(result["required_python_bits"], 32)
        self.assertIn("python_executable", result)
        self.assertIn("pywin32_available", result)
        self.assertIn(result["runtime_mode"], ("automation", "introspection-only"))

    def test_non_windows_runtime_supports_introspection_only(self) -> None:
        with (
            patch("multisim_mcp.multisim_client.os.name", "posix"),
            patch("multisim_mcp.multisim_client.pythoncom", None),
            patch("multisim_mcp.multisim_client.win32_client", None),
        ):
            result = runtime_diagnostics()
            self.assertFalse(result["runtime_compatible"])
            self.assertEqual(result["runtime_mode"], "introspection-only")
            with self.assertRaisesRegex(RuntimeError, "requires Windows"):
                require_compatible_runtime()

    def test_server_imports_when_pywin32_is_unavailable(self) -> None:
        script = """
import builtins

real_import = builtins.__import__

def import_without_pywin32(name, *args, **kwargs):
    if name == "pythoncom" or name.startswith("win32com"):
        raise ImportError("blocked for portable introspection test")
    return real_import(name, *args, **kwargs)

builtins.__import__ = import_without_pywin32
from multisim_mcp.multisim_client import runtime_diagnostics
from multisim_mcp import server

result = runtime_diagnostics()
assert result["pywin32_available"] is False
assert result["runtime_mode"] == "introspection-only"
assert server.mcp is not None
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


class SpiceValueTest(unittest.TestCase):
    def test_signed_and_scientific_values(self) -> None:
        from multisim_mcp.schematic_builder import parse_spice_value

        self.assertEqual(parse_spice_value("-15")[0], -15.0)
        self.assertEqual(parse_spice_value("1e-6")[0], 1e-6)
        self.assertEqual(parse_spice_value("4.7k")[0], 4700.0)


class CodecCommandTest(unittest.TestCase):
    @staticmethod
    def _which(name: str) -> str | None:
        return r"C:\tools\npx.cmd" if name == "npx" else None

    @patch("multisim_mcp.multisim_client.shutil.which", side_effect=_which)
    @patch.dict("os.environ", {}, clear=True)
    def test_runtime_download_is_disabled_by_default(self, _mock_which) -> None:
        with self.assertRaises(RuntimeError):
            Ms14Codec._base_cmd("ewd")

    @patch("multisim_mcp.multisim_client.shutil.which", side_effect=_which)
    @patch.dict("os.environ", {"MULTISIM_MCP_ALLOW_NPX_DOWNLOAD": "1"}, clear=True)
    def test_windows_npx_batch_fallback_is_rejected(self, _mock_which) -> None:
        with patch("multisim_mcp.multisim_client.os.name", "nt"):
            with self.assertRaises(RuntimeError):
                Ms14Codec._base_cmd("ewd")

    def test_npm_batch_shim_is_resolved_to_node_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shim = root / "ewd.cmd"
            script = root / "node_modules" / "electronics-workbench-decoder" / "dist" / "ewd.js"
            node = root / "node.exe"
            script.parent.mkdir(parents=True)
            shim.write_text("unsafe shim", encoding="utf-8")
            script.write_text("// codec", encoding="utf-8")
            node.write_bytes(b"MZ")

            def which(name: str) -> str | None:
                return str(node) if name == "node" else str(shim) if name == "ewd" else None

            with patch("multisim_mcp.multisim_client.shutil.which", side_effect=which):
                command = Ms14Codec._base_cmd("ewd")

        self.assertEqual(command, [str(node), str(script)])
        self.assertFalse(any(item.lower().endswith((".cmd", ".bat")) for item in command))

    def test_unresolvable_batch_override_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / "ewd.cmd"
            shim.write_text("unsafe shim", encoding="utf-8")
            with patch.dict(os.environ, {"MULTISIM_MCP_EWD": str(shim)}, clear=True):
                with self.assertRaises(RuntimeError):
                    Ms14Codec._base_cmd("ewd")


if __name__ == "__main__":
    unittest.main()
