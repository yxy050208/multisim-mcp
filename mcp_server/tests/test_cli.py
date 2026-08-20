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
    LOCAL_PACK_SCHEMA_VERSION,
    REQUIRED_TEMPLATES,
    _local_pack_status,
    _read_spice_design,
    _write_config,
    collect_doctor_report,
    main,
    render_client_config,
)
from multisim_mcp.model_provider import (
    ModelMessage,
    ModelProviderError,
    ModelResponse,
    ModelUsage,
    ToolCall,
)


class DoctorTest(unittest.TestCase):
    def test_generated_local_pack_schema_rejects_legacy_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "local-pack-manifest.json"
            manifest.write_text('{"schema_version": 1}', encoding="utf-8")
            legacy = _local_pack_status([root])
            manifest.write_text(
                json.dumps({"schema_version": LOCAL_PACK_SCHEMA_VERSION}),
                encoding="utf-8",
            )
            current = _local_pack_status([root])
        self.assertTrue(legacy["managed"])
        self.assertFalse(legacy["compatible"])
        self.assertTrue(current["compatible"])

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
                    "multisim_mcp.cli._worker_runtime_diagnostics",
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

    def test_64_bit_frontend_can_configure_a_separate_32_bit_worker(self) -> None:
        content = render_client_config(
            "claude-desktop",
            python_executable=r"C:\Python64\python.exe",
            worker_python=r"C:\Python32\python.exe",
        )
        spec = json.loads(content)["mcpServers"]["multisim"]
        self.assertEqual(spec["command"], r"C:\Python64\python.exe")
        self.assertEqual(
            spec["env"]["MULTISIM_MCP_WORKER_PYTHON"],
            r"C:\Python32\python.exe",
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

    def test_deepseek_harness_cordis_yaml(self) -> None:
        content = render_client_config(
            "deepseek-harness",
            server_name="multisim_lab",
            python_executable=r"C:\Python32\python.exe",
            template_dir=r"C:\MultisimMcp\component-pack",
            work_dir=r"C:\msre_exp",
            tool_profile="experiment",
            artifact_export_dir=r"C:\MultisimMcp\exports",
        )
        self.assertIn('- id: "mcp-multisim_lab"', content)
        self.assertIn('name: "@deepseek-ai/dsh-mcp-client"', content)
        self.assertIn('serverName: "multisim_lab"', content)
        self.assertIn('transport: "stdio"', content)
        self.assertIn('command: "C:\\\\Python32\\\\python.exe"', content)
        self.assertIn('- "multisim_mcp.server"', content)
        self.assertIn("toolCallTimeoutMs: 120000", content)
        self.assertIn("maxAttempts: 10", content)
        self.assertIn("MULTISIM_MCP_TEMPLATE_DIR:", content)
        self.assertIn('MULTISIM_MCP_TOOL_PROFILE: "experiment"', content)
        self.assertIn(
            'MULTISIM_MCP_ARTIFACT_EXPORT_DIR: "C:\\\\MultisimMcp\\\\exports"',
            content,
        )
        self.assertNotIn("DEEPSEEK_API_KEY", content)

    def test_deepseek_harness_enforces_upstream_server_name_rules(self) -> None:
        with self.assertRaisesRegex(ValueError, "DeepSeek Harness server name"):
            render_client_config("deepseek-harness", server_name="multisim.lab")
        with self.assertRaisesRegex(ValueError, "DeepSeek Harness server name"):
            render_client_config("deepseek-harness", server_name="m" * 33)

    def test_deepseek_harness_cli_json_envelope(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "config",
                    "--client",
                    "deepseek-harness",
                    "--python",
                    r"C:\Python32\python.exe",
                    "--tool-profile",
                    "core",
                    "--json",
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["client"], "deepseek-harness")
        self.assertIn("@deepseek-ai/dsh-mcp-client", payload["content"])
        self.assertIn('MULTISIM_MCP_TOOL_PROFILE: "core"', payload["content"])

    def test_direct_renderer_rejects_unknown_tool_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported tool profile"):
            render_client_config("generic", tool_profile="everything")

    def test_rejects_filesystem_root_as_artifact_export_dir(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be a filesystem root"):
            render_client_config(
                "generic", artifact_export_dir=Path.cwd().anchor
            )

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


class ProviderConfigureCliTest(unittest.TestCase):
    def test_auto_preview_does_not_write_or_expose_secret(self) -> None:
        secret = "cli-secret-must-not-leak"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "providers.json"
            output = io.StringIO()
            with (
                patch.dict(
                    "os.environ",
                    {"DEEPSEEK_API_KEY": secret},
                    clear=True,
                ),
                redirect_stdout(output),
            ):
                exit_code = main(
                    ["configure", "--auto", "--path", str(target), "--json"]
                )
            exists = target.exists()
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertFalse(exists)
        self.assertFalse(payload["applied"])
        self.assertEqual(payload["detected"], ["deepseek"])
        self.assertNotIn(secret, output.getvalue())
        self.assertEqual(
            payload["config"]["providers"]["deepseek"]["credential"]["name"],
            "DEEPSEEK_API_KEY",
        )

    def test_manual_apply_and_show_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "providers.json"
            with redirect_stdout(io.StringIO()):
                apply_exit = main(
                    [
                        "configure",
                        "--provider",
                        "ollama",
                        "--model",
                        "qwen3:8b",
                        "--path",
                        str(target),
                        "--apply",
                        "--json",
                    ]
                )
            output = io.StringIO()
            with redirect_stdout(output):
                show_exit = main(
                    ["configure", "--show", "--path", str(target), "--json"]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(apply_exit, 0)
        self.assertEqual(show_exit, 0)
        self.assertEqual(payload["mode"], "show")
        self.assertEqual(
            payload["config"]["providers"]["ollama"]["model"], "qwen3:8b"
        )
        self.assertIsNone(
            payload["config"]["providers"]["ollama"]["credential"]
        )

    def test_apply_merges_existing_providers_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "providers.json"
            commands = (
                [
                    "configure",
                    "--provider",
                    "ollama",
                    "--model",
                    "qwen3:8b",
                    "--path",
                    str(target),
                    "--apply",
                    "--json",
                ],
                [
                    "configure",
                    "--provider",
                    "deepseek",
                    "--path",
                    str(target),
                    "--apply",
                    "--json",
                ],
            )
            for command in commands:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(command), 0)
            content = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(set(content["providers"]), {"ollama", "deepseek"})
        self.assertEqual(content["active_provider"], "deepseek")

    def test_probe_failure_returns_one_without_exposing_credential(self) -> None:
        probe = {
            "provider": "deepseek",
            "success": False,
            "status": "unreachable",
            "error": "fixture",
        }
        output = io.StringIO()
        with (
            patch(
                "multisim_mcp.cli.probe_provider_config", return_value=[probe]
            ),
            redirect_stdout(output),
        ):
            exit_code = main(
                [
                    "configure",
                    "--provider",
                    "deepseek",
                    "--probe",
                    "--json",
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["success"])
        self.assertFalse(payload["credential_values_exposed"])

    def test_replace_requires_apply(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["configure", "--auto", "--replace", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertIn("requires --apply", payload["error"]["message"])

    def test_manual_options_require_provider_mode(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["configure", "--model", "ignored", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertIn("require --provider", payload["error"]["message"])


class ModelCliTest(unittest.TestCase):
    @staticmethod
    def _response(*, tool_call: bool = False) -> ModelResponse:
        calls = (
            (ToolCall("call_1", "unexpected", {}),) if tool_call else ()
        )
        return ModelResponse(
            provider_id="fixture",
            requested_model="fixture-model",
            model="fixture-model",
            message=ModelMessage(
                "assistant", "" if tool_call else "fixture answer", tool_calls=calls
            ),
            finish_reason="tool_calls" if tool_call else "stop",
            usage=ModelUsage(2, 3, 5),
        )

    def test_model_reads_prompt_file_and_returns_stable_json(self) -> None:
        captured: dict[str, object] = {}

        class Registry:
            def complete(self, messages: object, **kwargs: object) -> ModelResponse:
                captured["messages"] = messages
                captured["kwargs"] = kwargs
                return ModelCliTest._response()

        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "prompt.txt"
            prompt.write_text("Analyze the divider.", encoding="utf-8")
            output = io.StringIO()
            with (
                patch("multisim_mcp.cli.read_provider_config", return_value={}),
                patch(
                    "multisim_mcp.cli.ModelProviderRegistry.from_config",
                    return_value=Registry(),
                ),
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "model",
                        "--input",
                        str(prompt),
                        "--provider",
                        "fixture",
                        "--max-tokens",
                        "200",
                        "--json",
                    ]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["response"]["message"]["content"], "fixture answer")
        self.assertEqual(captured["messages"][0].content, "Analyze the divider.")
        self.assertEqual(captured["kwargs"]["provider_id"], "fixture")
        self.assertEqual(captured["kwargs"]["max_tokens"], 200)

    def test_model_requires_explicit_failover_authorization(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "model",
                    "--stdin",
                    "--fallback",
                    "secondary",
                    "--json",
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertIn("allow-failover", payload["error"]["message"])

    def test_model_stdin_does_not_expose_any_tools(self) -> None:
        class Registry:
            def complete(self, messages: object, **kwargs: object) -> ModelResponse:
                self.messages = messages
                self.kwargs = kwargs
                return ModelCliTest._response()

        registry = Registry()
        output = io.StringIO()
        with (
            patch("multisim_mcp.cli.read_provider_config", return_value={}),
            patch(
                "multisim_mcp.cli.ModelProviderRegistry.from_config",
                return_value=registry,
            ),
            patch("sys.stdin", io.StringIO("Hello from stdin")),
            redirect_stdout(output),
        ):
            exit_code = main(["model", "--stdin"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue().strip(), "fixture answer")
        self.assertNotIn("tools", registry.kwargs)

    def test_model_rejects_unrequested_tool_calls(self) -> None:
        class Registry:
            def complete(self, messages: object, **kwargs: object) -> ModelResponse:
                return ModelCliTest._response(tool_call=True)

        output = io.StringIO()
        with (
            patch("multisim_mcp.cli.read_provider_config", return_value={}),
            patch(
                "multisim_mcp.cli.ModelProviderRegistry.from_config",
                return_value=Registry(),
            ),
            patch("sys.stdin", io.StringIO("hello")),
            redirect_stdout(output),
        ):
            exit_code = main(["model", "--stdin", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertIn("exposes no tools", payload["error"]["message"])

    def test_model_provider_error_is_sanitized_and_exits_one(self) -> None:
        class Registry:
            def complete(self, messages: object, **kwargs: object) -> ModelResponse:
                raise ModelProviderError(
                    "sanitized failure",
                    provider_id="fixture",
                    retryable=False,
                )

        output = io.StringIO()
        with (
            patch("multisim_mcp.cli.read_provider_config", return_value={}),
            patch(
                "multisim_mcp.cli.ModelProviderRegistry.from_config",
                return_value=Registry(),
            ),
            patch("sys.stdin", io.StringIO("hello")),
            redirect_stdout(output),
        ):
            exit_code = main(["model", "--stdin", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["error"]["message"], "sanitized failure")
        self.assertFalse(payload["credential_values_exposed"])

    def test_model_diagnose_runs_only_fixed_read_only_bindings(self) -> None:
        captured: dict[str, object] = {}

        class Run:
            def to_dict(self) -> dict[str, object]:
                return {
                    "final_response": ModelCliTest._response().to_dict(),
                    "rounds": 2,
                    "tool_call_count": 1,
                    "provider_ids": ["fixture", "fixture"],
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                    "usage_complete": True,
                    "transcript_message_count": 5,
                }

        class Loop:
            def __init__(
                self,
                registry: object,
                bindings: object,
                **kwargs: object,
            ) -> None:
                captured["registry"] = registry
                captured["tool_names"] = [
                    item.definition.name for item in bindings
                ]
                captured["loop_kwargs"] = kwargs

            def run(self, messages: object, **kwargs: object) -> Run:
                captured["messages"] = messages
                captured["run_kwargs"] = kwargs
                return Run()

        design = {
            "schema_version": 1,
            "design_id": "divider-v1",
            "title": "Divider",
            "revision": 0,
            "components": [
                {
                    "refdes": "R1",
                    "kind": "R",
                    "nodes": ["in", "out"],
                    "value": "1k",
                    "model": None,
                    "parameters": {},
                    "annotations": {},
                }
            ],
            "nets": ["in", "out"],
            "parameters": {},
            "model_references": [],
            "annotations": {"private": "not returned"},
            "source_netlist": "R1 in out 1k\n.end\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design_path = root / "design.json"
            prompt_path = root / "prompt.txt"
            design_path.write_text(json.dumps(design), encoding="utf-8")
            prompt_path.write_text("Check the topology.", encoding="utf-8")
            output = io.StringIO()
            with (
                patch("multisim_mcp.cli.read_provider_config", return_value={}),
                patch(
                    "multisim_mcp.cli.ModelProviderRegistry.from_config",
                    return_value="registry",
                ),
                patch("multisim_mcp.cli.BoundedToolLoop", Loop),
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "model-diagnose",
                        "--input",
                        str(prompt_path),
                        "--design",
                        str(design_path),
                        "--provider",
                        "fixture",
                        "--max-rounds",
                        "4",
                        "--max-tool-calls",
                        "6",
                        "--json",
                    ]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["command"], "model-diagnose")
        self.assertFalse(payload["design"]["source_netlist_exposed"])
        self.assertNotIn("R1 in out 1k", output.getvalue())
        self.assertEqual(
            set(captured["tool_names"]),
            {
                "eda_get_design_summary",
                "eda_list_components",
                "eda_inspect_net",
                "eda_run_structural_checks",
            },
        )
        self.assertEqual(captured["loop_kwargs"], {"max_rounds": 4, "max_tool_calls": 6})
        messages = captured["messages"]
        self.assertEqual(messages[-1].content, "Check the topology.")
        self.assertIn("untrusted data", messages[0].content)

    def test_model_diagnose_rejects_duplicate_design_fields_before_provider(self) -> None:
        duplicate = (
            '{"schema_version":1,"design_id":"d","title":"one",'
            '"title":"two","source_netlist":"R1 a 0 1k"}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.json"
            path.write_text(duplicate, encoding="utf-8")
            output = io.StringIO()
            with (
                patch("sys.stdin", io.StringIO("inspect")),
                patch("multisim_mcp.cli.read_provider_config") as read_config,
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "model-diagnose",
                        "--stdin",
                        "--design",
                        str(path),
                        "--json",
                    ]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertIn("duplicate field", payload["error"]["message"])
        read_config.assert_not_called()

    def test_model_diagnose_parses_safe_netlist_and_rejects_includes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            safe = root / "divider.cir"
            unsafe = root / "include.cir"
            safe.write_text(
                "V1 in 0 10\nR1 in out 1k\nR2 out 0 1k\n.end\n",
                encoding="utf-8",
            )
            unsafe.write_text(".include vendor.lib\n.end\n", encoding="utf-8")
            design = _read_spice_design(str(safe))
            self.assertEqual([item.refdes for item in design.components], ["V1", "R1", "R2"])
            output = io.StringIO()
            with (
                patch("sys.stdin", io.StringIO("inspect")),
                patch("multisim_mcp.cli.read_provider_config") as read_config,
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "model-diagnose",
                        "--stdin",
                        "--netlist",
                        str(unsafe),
                        "--json",
                    ]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertIn("invalid safe SPICE netlist", payload["error"]["message"])
        read_config.assert_not_called()


class HarnessSkillsCliTest(unittest.TestCase):
    def test_harness_skills_json_installs_five_bundled_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "harness-skills",
                        "--output",
                        str(Path(tmp) / ".dsh" / "skills"),
                        "--json",
                    ]
                )
            payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["skill_count"], 5)
        self.assertEqual(payload["command"], "harness-skills")


if __name__ == "__main__":
    unittest.main()
