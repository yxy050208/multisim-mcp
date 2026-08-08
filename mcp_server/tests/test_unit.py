"""COM-free unit tests for the Multisim MCP server."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.multisim_client import (
    CODEC_PACKAGE,
    Ms14Codec,
    MultisimClient,
    clean_error,
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


class RuntimeDiagnosticsTest(unittest.TestCase):
    def test_reports_interpreter_architecture(self) -> None:
        result = runtime_diagnostics()
        self.assertIn(result["python_bits"], (32, 64))
        self.assertEqual(result["required_python_bits"], 32)
        self.assertIn("python_executable", result)


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
