"""Tests for the installed Multisim MCP command-line interface."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.cli import (
    REQUIRED_TEMPLATES,
    _write_config,
    collect_doctor_report,
    main,
    render_client_config,
)


class DoctorTest(unittest.TestCase):
    def test_report_has_stable_machine_readable_shape(self) -> None:
        report = collect_doctor_report("en")
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["command"], "doctor")
        self.assertTrue(report["success"])
        self.assertTrue(report["introspection_ready"])
        self.assertIsInstance(report["automation_ready"], bool)
        self.assertIn("activation_checked", report)
        self.assertIn("activation_ready", report)
        self.assertIsInstance(report["full_workflow_ready"], bool)
        ids = {item["id"] for item in report["checks"]}
        self.assertEqual(
            ids,
            {
                "python.version",
                "platform.windows",
                "python.architecture",
                "python.pywin32",
                "multisim.com_registration",
                "multisim.activation",
                "schematic.template_pack",
                "schematic.codec",
            },
        )

    def test_ready_report_requires_runtime_com_templates_and_codecs(self) -> None:
        runtime = {
            "platform": "Windows-test",
            "windows": True,
            "python": "3.10.0",
            "multisim_mcp": "test",
            "python_executable": r"C:\Python32\python.exe",
            "python_bits": 32,
            "required_python_bits": 32,
            "pywin32_available": True,
            "prog_id": "MultisimInterface.MultisimApp",
            "runtime_compatible": True,
            "runtime_mode": "automation",
            "runtime_message": "ready",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in REQUIRED_TEMPLATES:
                (root / name).write_text("fixture", encoding="utf-8")
            with (
                patch(
                    "multisim_mcp.multisim_client.runtime_diagnostics",
                    return_value=runtime,
                ),
                patch(
                    "multisim_mcp.schematic_builder.template_search_paths",
                    return_value=[root],
                ),
                patch(
                    "multisim_mcp.cli._com_registration",
                    return_value={
                        "registered": True,
                        "status": "registered",
                        "clsid": "{fixture}",
                    },
                ),
                patch(
                    "multisim_mcp.cli._codec_diagnostics",
                    return_value={
                        "ready": True,
                        "tools": {
                            "ewd": {"available": True, "command": ["node", "ewd.js"]},
                            "ewe": {"available": True, "command": ["node", "ewe.js"]},
                        },
                    },
                ),
            ):
                report = collect_doctor_report("zh")
        self.assertEqual(report["language"], "zh")
        self.assertTrue(report["automation_ready"])
        self.assertFalse(report["activation_checked"])
        self.assertIsNone(report["activation_ready"])
        self.assertTrue(report["full_workflow_ready"])

    def test_connect_flag_requests_explicit_activation_probe(self) -> None:
        report = {
            "schema_version": 1,
            "command": "doctor",
            "success": True,
            "full_workflow_ready": True,
        }
        output = io.StringIO()
        with (
            patch(
                "multisim_mcp.cli.collect_doctor_report", return_value=report
            ) as collect,
            redirect_stdout(output),
        ):
            exit_code = main(["doctor", "--json", "--connect"])
        self.assertEqual(exit_code, 0)
        collect.assert_called_once_with("auto", connect=True)

    def test_doctor_json_exits_zero_when_setup_is_incomplete(self) -> None:
        report = {
            "schema_version": 1,
            "command": "doctor",
            "success": True,
            "full_workflow_ready": False,
        }
        output = io.StringIO()
        with (
            patch("multisim_mcp.cli.collect_doctor_report", return_value=report),
            redirect_stdout(output),
        ):
            exit_code = main(["--json", "doctor"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["command"], "doctor")

    def test_doctor_strict_exits_nonzero_when_setup_is_incomplete(self) -> None:
        report = {
            "schema_version": 1,
            "command": "doctor",
            "success": True,
            "full_workflow_ready": False,
        }
        with (
            patch("multisim_mcp.cli.collect_doctor_report", return_value=report),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = main(["doctor", "--json", "--strict"])
        self.assertEqual(exit_code, 1)


class ConfigGeneratorTest(unittest.TestCase):
    def test_claude_desktop_json(self) -> None:
        content = render_client_config(
            "claude-desktop",
            python_executable=r"C:\Python32\python.exe",
            template_dir=r"C:\MultisimMcp\component-pack",
        )
        payload = json.loads(content)
        spec = payload["mcpServers"]["multisim"]
        self.assertEqual(spec["args"], ["-m", "multisim_mcp.server"])
        self.assertEqual(
            spec["env"]["MULTISIM_MCP_TEMPLATE_DIR"],
            r"C:\MultisimMcp\component-pack",
        )

    def test_codex_toml(self) -> None:
        content = render_client_config(
            "codex",
            server_name="multisim-lab",
            python_executable=r"C:\Python32\python.exe",
            work_dir=r"C:\msre_exp",
        )
        self.assertIn("[mcp_servers.multisim-lab]", content)
        self.assertIn('args = ["-m", "multisim_mcp.server"]', content)
        self.assertIn("[mcp_servers.multisim-lab.env]", content)
        self.assertIn('MULTISIM_MCP_WORKDIR = "C:\\\\msre_exp"', content)

    def test_generic_spec_is_not_wrapped_in_a_client_key(self) -> None:
        content = render_client_config(
            "generic", python_executable=r"C:\Python32\python.exe"
        )
        payload = json.loads(content)
        self.assertEqual(payload["command"], r"C:\Python32\python.exe")
        self.assertNotIn("mcpServers", payload)

    def test_rejects_unsafe_server_name_and_spaced_work_dir(self) -> None:
        with self.assertRaisesRegex(ValueError, "server name"):
            render_client_config("codex", server_name="bad name")
        with self.assertRaisesRegex(ValueError, "must not contain spaces"):
            render_client_config("generic", work_dir=r"C:\work dir")

    def test_output_does_not_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "config.json"
            _write_config(str(output), "first\n", force=False)
            with self.assertRaises(FileExistsError):
                _write_config(str(output), "second\n", force=False)
            _write_config(str(output), "second\n", force=True)
            self.assertEqual(output.read_text(encoding="utf-8"), "second\n")

    def test_no_arguments_preserves_stdio_server_entrypoint(self) -> None:
        with patch("multisim_mcp.cli._run_server") as run_server:
            self.assertEqual(main([]), 0)
        run_server.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
