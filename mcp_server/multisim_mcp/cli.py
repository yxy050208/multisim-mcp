"""Command-line diagnostics and MCP client configuration helpers."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import locale
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from multisim_mcp import __version__
from multisim_mcp.com_worker_client import WORKER_PYTHON_ENV
from multisim_mcp.experiment_resources import ARTIFACT_EXPORT_DIR_ENV
from multisim_mcp.harness_skills import install_harness_skills
from multisim_mcp.tool_profiles import TOOL_PROFILE_ENV, TOOL_PROFILES

SCHEMA_VERSION = 1
LOCAL_PACK_SCHEMA_VERSION = 2
REQUIRED_TEMPLATES = ("minimal.ms14.xml", "wire.xml", "r_element.xml")
CLIENTS = ("claude-desktop", "codex", "deepseek-harness", "generic")
_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_HARNESS_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _load_module(name: str) -> Any:
    """Keep third-party import chatter away from stdout protocols."""
    with contextlib.redirect_stdout(sys.stderr):
        return importlib.import_module(name)


def _preferred_language(requested: str) -> str:
    if requested in {"zh", "en"}:
        return requested
    language = locale.getlocale()[0] or ""
    return "zh" if language.lower().startswith("zh") else "en"


def _message(language: str, zh: str, en: str) -> str:
    return zh if language == "zh" else en


def _check(
    check_id: str,
    status: str,
    message: str,
    repair: str | None = None,
    **details: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": check_id,
        "status": status,
        "message": message,
    }
    if repair:
        result["repair"] = repair
    result.update(details)
    return result


def _local_pack_status(paths: list[Path]) -> dict[str, Any]:
    """Reject generated packs whose extraction contract predates this release."""
    for root in paths:
        manifest = root / "local-pack-manifest.json"
        if not manifest.is_file():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            schema_version = int(payload.get("schema_version", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return {
                "managed": True,
                "compatible": False,
                "manifest": str(manifest),
                "schema_version": None,
                "error": str(exc),
            }
        return {
            "managed": True,
            "compatible": schema_version == LOCAL_PACK_SCHEMA_VERSION,
            "manifest": str(manifest),
            "schema_version": schema_version,
            "required_schema_version": LOCAL_PACK_SCHEMA_VERSION,
            "generator": payload.get("generator"),
        }
    return {
        "managed": False,
        "compatible": True,
        "manifest": None,
        "schema_version": None,
        "required_schema_version": LOCAL_PACK_SCHEMA_VERSION,
    }


def _com_registration() -> dict[str, Any]:
    """Inspect the 32-bit COM registration without activating Multisim."""
    if os.name != "nt":
        return {"registered": False, "status": "skipped", "clsid": None}
    prog_id = _load_module("multisim_mcp.multisim_client").PROG_ID
    try:
        import winreg
    except ImportError:
        return {
            "registered": False,
            "status": "unknown",
            "clsid": None,
            "error": "winreg is unavailable",
        }

    access_modes = [winreg.KEY_READ]
    wow64_32 = getattr(winreg, "KEY_WOW64_32KEY", 0)
    if wow64_32:
        access_modes.insert(0, winreg.KEY_READ | wow64_32)
    errors: list[str] = []
    for access in access_modes:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CLASSES_ROOT,
                rf"{prog_id}\CLSID",
                0,
                access,
            ) as key:
                clsid = str(winreg.QueryValue(key, None)).strip()
            return {
                "registered": bool(clsid),
                "status": "registered" if clsid else "missing",
                "clsid": clsid or None,
            }
        except OSError as exc:
            errors.append(str(exc))
    return {
        "registered": False,
        "status": "missing",
        "clsid": None,
        "error": errors[-1] if errors else "COM registration was not found",
    }


def _codec_diagnostics() -> dict[str, Any]:
    codec_class = _load_module("multisim_mcp.multisim_client").Ms14Codec
    tools: dict[str, Any] = {}
    for tool in ("ewd", "ewe"):
        try:
            command = codec_class._base_cmd(tool)
            tools[tool] = {"available": True, "command": command}
        except Exception as exc:
            tools[tool] = {"available": False, "error": str(exc)}
    return {
        "ready": all(item["available"] for item in tools.values()),
        "tools": tools,
    }


def _activation_diagnostics() -> dict[str, Any]:
    """Explicitly probe COM inside a disposable isolated worker."""
    worker_module = _load_module("multisim_mcp.com_worker_client")
    worker = worker_module.MultisimWorkerProcess()
    client = worker_module.WorkerMultisimClient(worker)
    try:
        details = client.connect()
        return {"checked": True, "ready": True, "details": details}
    except Exception as exc:
        return {"checked": True, "ready": False, "error": str(exc)}
    finally:
        worker.close()


def _worker_runtime_diagnostics() -> dict[str, Any]:
    worker_module = _load_module("multisim_mcp.com_worker_client")
    worker = worker_module.MultisimWorkerProcess()
    try:
        return worker_module.worker_runtime_diagnostics(worker)
    finally:
        worker.close()


def collect_doctor_report(
    language: str = "auto", connect: bool = False
) -> dict[str, Any]:
    """Collect side-effect-free setup diagnostics for people and agents."""
    language = _preferred_language(language)
    client_module = _load_module("multisim_mcp.multisim_client")
    runtime = _worker_runtime_diagnostics()
    prog_id = client_module.PROG_ID
    checks: list[dict[str, Any]] = []

    checks.append(
        _check(
            "python.version",
            "pass" if sys.version_info >= (3, 10) else "fail",
            _message(
                language,
                f"Worker Python {runtime['python']}（要求 3.10 或更高版本）",
                f"Worker Python {runtime['python']} (3.10 or newer required)",
            ),
            (
                None
                if sys.version_info >= (3, 10)
                else _message(language, "安装 Python 3.10+。", "Install Python 3.10+.")
            ),
        )
    )
    checks.append(
        _check(
            "platform.windows",
            "pass" if runtime["windows"] else "fail",
            _message(
                language,
                (
                    "当前系统是 Windows。"
                    if runtime["windows"]
                    else "当前系统不是 Windows。"
                ),
                (
                    "Windows detected."
                    if runtime["windows"]
                    else "Windows was not detected."
                ),
            ),
            (
                None
                if runtime["windows"]
                else _message(
                    language,
                    "真实 Multisim 自动化必须在 Windows 上运行；当前环境只能发现 MCP 工具。",
                    "Run real Multisim automation on Windows; this environment "
                    "supports MCP introspection only.",
                )
            ),
        )
    )
    bits_ok = runtime["python_bits"] == runtime["required_python_bits"]
    checks.append(
        _check(
            "python.architecture",
            "pass" if bits_ok else "fail",
            _message(
                language,
                f"Multisim worker 为 {runtime['python_bits']} 位；主进程可为 32/64 位。",
                f"The Multisim worker is {runtime['python_bits']}-bit; the "
                "frontend may be 32/64-bit.",
            ),
            (
                None
                if bits_ok
                else _message(
                    language,
                    f"安装 32 位 Python，或设置 {WORKER_PYTHON_ENV}。",
                    f"Install 32-bit Python or set {WORKER_PYTHON_ENV}.",
                )
            ),
            executable=runtime["python_executable"],
            frontend_bits=runtime.get("frontend_python_bits"),
            frontend_executable=runtime.get("frontend_python_executable"),
        )
    )
    pywin32_ok = bool(runtime["pywin32_available"])
    checks.append(
        _check(
            "python.pywin32",
            "pass" if pywin32_ok else "fail",
            _message(
                language,
                (
                    "pywin32 已安装。"
                    if pywin32_ok
                    else "32 位 worker 中没有可用的 pywin32。"
                ),
                (
                    "pywin32 is available."
                    if pywin32_ok
                    else "pywin32 is unavailable in the 32-bit worker."
                ),
            ),
            (
                None
                if pywin32_ok
                else _message(
                    language,
                    "在 32 位 worker Python 中安装 multisim-mcp 与 pywin32。",
                    "Install multisim-mcp and pywin32 in the 32-bit worker Python.",
                )
            ),
        )
    )

    com = _com_registration()
    if com["status"] == "skipped":
        com_status = "skipped"
    elif com["registered"]:
        com_status = "pass"
    elif com["status"] == "unknown":
        com_status = "warn"
    else:
        com_status = "fail"
    checks.append(
        _check(
            "multisim.com_registration",
            com_status,
            _message(
                language,
                (
                    "已找到 Multisim COM 注册。"
                    if com["registered"]
                    else (
                        "非 Windows 环境，已跳过 Multisim COM 注册检查。"
                        if com_status == "skipped"
                        else "未找到 Multisim COM 注册。"
                    )
                ),
                (
                    "Multisim COM registration was found."
                    if com["registered"]
                    else (
                        "The Multisim COM registration check was skipped outside Windows."
                        if com_status == "skipped"
                        else "Multisim COM registration was not found."
                    )
                ),
            ),
            (
                None
                if com["registered"] or com_status == "skipped"
                else _message(
                    language,
                    "确认已安装并授权 Multisim 14+，然后从当前 Windows 用户启动一次 Multisim。",
                    "Verify that licensed Multisim 14+ is installed, then start "
                    "it once as the current Windows user.",
                )
            ),
            prog_id=prog_id,
            clsid=com.get("clsid"),
            registry_status=com["status"],
        )
    )

    automation_ready = bool(runtime["runtime_compatible"] and com["registered"])
    if connect and automation_ready:
        activation = _activation_diagnostics()
        activation_status = "pass" if activation["ready"] else "fail"
        activation_message = _message(
            language,
            (
                "Multisim COM 激活和连接成功。"
                if activation["ready"]
                else "Multisim COM 激活或连接失败。"
            ),
            (
                "Multisim COM activation and connection succeeded."
                if activation["ready"]
                else "Multisim COM activation or connection failed."
            ),
        )
        activation_repair = (
            None
            if activation["ready"]
            else _message(
                language,
                "启动并激活已授权的 Multisim，然后重新运行 doctor --connect。",
                "Start and activate licensed Multisim, then rerun doctor --connect.",
            )
        )
    else:
        activation = {"checked": False, "ready": None}
        activation_status = "skipped"
        activation_message = _message(
            language,
            (
                "未执行 Multisim 启动检查；传入 --connect 可显式验证。"
                if automation_ready
                else "运行时前置检查未通过，已跳过 Multisim 启动检查。"
            ),
            (
                "The Multisim launch check was not run; pass --connect to verify it."
                if automation_ready
                else "The Multisim launch check was skipped because runtime prerequisites failed."
            ),
        )
        activation_repair = None
    checks.append(
        _check(
            "multisim.activation",
            activation_status,
            activation_message,
            activation_repair,
            checked=activation["checked"],
            ready=activation["ready"],
            details=activation.get("details"),
            error=activation.get("error"),
        )
    )

    paths = _load_module("multisim_mcp.schematic_builder").template_search_paths()
    missing_templates = [
        name
        for name in REQUIRED_TEMPLATES
        if not any((path / name).is_file() for path in paths)
    ]
    local_pack = _local_pack_status(paths)
    templates_ready = not missing_templates and bool(local_pack["compatible"])
    incompatible_pack = not local_pack["compatible"]
    if templates_ready:
        template_message = _message(
            language,
            "原理图模板包可用。",
            "The schematic template pack is ready.",
        )
    elif incompatible_pack:
        template_message = _message(
            language,
            "本地模板包版本过旧或 manifest 无效。",
            "The local template pack is outdated or has an invalid manifest.",
        )
    else:
        template_message = _message(
            language,
            "原理图模板包不完整。",
            "The schematic template pack is incomplete.",
        )
    checks.append(
        _check(
            "schematic.template_pack",
            "pass" if templates_ready else "fail",
            template_message,
            (
                None
                if templates_ready
                else _message(
                    language,
                    "使用 1.0 源码重新运行 "
                    "tools/bootstrap_local_component_pack.py，并设置 "
                    "MULTISIM_MCP_TEMPLATE_DIR。",
                    "Rebuild the pack with the 1.0 "
                    "tools/bootstrap_local_component_pack.py and set "
                    "MULTISIM_MCP_TEMPLATE_DIR.",
                )
            ),
            search_paths=[str(path) for path in paths],
            missing=missing_templates,
            local_pack=local_pack,
        )
    )

    codec = _codec_diagnostics()
    checks.append(
        _check(
            "schematic.codec",
            "pass" if codec["ready"] else "fail",
            _message(
                language,
                ".ms14 编解码器可用。" if codec["ready"] else ".ms14 编解码器不完整。",
                (
                    "The .ms14 codecs are available."
                    if codec["ready"]
                    else "The .ms14 codecs are incomplete."
                ),
            ),
            (
                None
                if codec["ready"]
                else _message(
                    language,
                    "安装 electronics-workbench-decoder@0.2.0，并确保 ewd/ewe 与 node 可用。",
                    "Install electronics-workbench-decoder@0.2.0 and make "
                    "ewd/ewe plus node available.",
                )
            ),
            tools=codec["tools"],
        )
    )

    full_workflow_ready = bool(
        automation_ready
        and templates_ready
        and codec["ready"]
        and activation["ready"] is not False
    )
    summary = _message(
        language,
        (
            (
                "Multisim COM 实际连接以及完整原理图与实验工作流已就绪。"
                if activation["ready"]
                else "静态前置检查已通过；运行 doctor --connect 可继续验证 Multisim 实际连接。"
            )
            if full_workflow_ready
            else "MCP 工具发现可用，但完整 Multisim 工作流仍有未通过的检查。"
        ),
        (
            (
                "The real Multisim COM connection and complete experiment workflow are ready."
                if activation["ready"]
                else "Static prerequisites passed; run doctor --connect to "
                "verify the real Multisim connection."
            )
            if full_workflow_ready
            else (
                "MCP introspection is available, but the complete Multisim "
                "workflow still has failing checks."
            )
        ),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "doctor",
        "success": True,
        "language": language,
        "introspection_ready": True,
        "automation_ready": automation_ready,
        "activation_checked": activation["checked"],
        "activation_ready": activation["ready"],
        "full_workflow_ready": full_workflow_ready,
        "summary": summary,
        "runtime": runtime,
        "checks": checks,
    }


def _server_spec(
    python_executable: str,
    worker_python: str | None,
    template_dir: str | None,
    work_dir: str | None,
    tool_profile: str | None,
    artifact_export_dir: str | None,
) -> dict[str, Any]:
    def normalize(value: str) -> str:
        return os.path.abspath(os.path.expanduser(value))

    environment: dict[str, str] = {}
    if worker_python:
        environment[WORKER_PYTHON_ENV] = normalize(worker_python)
    if template_dir:
        environment["MULTISIM_MCP_TEMPLATE_DIR"] = normalize(template_dir)
    if work_dir:
        resolved_work_dir = normalize(work_dir)
        if " " in resolved_work_dir:
            raise ValueError("work directory must not contain spaces")
        environment["MULTISIM_MCP_WORKDIR"] = resolved_work_dir
    if tool_profile:
        environment[TOOL_PROFILE_ENV] = tool_profile
    if artifact_export_dir:
        resolved_export_dir = Path(normalize(artifact_export_dir))
        if resolved_export_dir == Path(resolved_export_dir.anchor):
            raise ValueError("artifact export directory must not be a filesystem root")
        environment[ARTIFACT_EXPORT_DIR_ENV] = str(resolved_export_dir)
    result: dict[str, Any] = {
        "command": normalize(python_executable),
        "args": ["-m", "multisim_mcp.server"],
    }
    if environment:
        result["env"] = environment
    return result


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_deepseek_harness_config(
    server_name: str, spec: dict[str, Any]
) -> str:
    """Render a Cordis plugin row for the official DeepSeek Harness MCP client."""
    if not _HARNESS_SERVER_NAME_RE.fullmatch(server_name):
        raise ValueError(
            "DeepSeek Harness server name must be 1-32 characters and contain "
            "only letters, digits, underscore, and hyphen"
        )

    # JSON string literals are valid YAML scalars and avoid path escaping bugs on
    # Windows. Keep this fragment dependency-free so it works in the 32-bit host.
    lines = [
        f"- id: {_toml_string(f'mcp-{server_name}')}",
        '  name: "@deepseek-ai/dsh-mcp-client"',
        "  config:",
        f"    serverName: {_toml_string(server_name)}",
        '    transport: "stdio"',
        f"    command: {_toml_string(spec['command'])}",
        "    args:",
    ]
    lines.extend(f"      - {_toml_string(item)}" for item in spec["args"])
    if spec.get("env"):
        lines.append("    env:")
        lines.extend(
            f"      {key}: {_toml_string(value)}"
            for key, value in sorted(spec["env"].items())
        )
    lines.extend(
        [
            "    failOnStartupError: true",
            "    toolCallTimeoutMs: 120000",
            "    reconnect:",
            "      enabled: true",
            "      initialDelayMs: 500",
            "      maxDelayMs: 30000",
            "      maxAttempts: 10",
        ]
    )
    return "\n".join(lines) + "\n"


def render_client_config(
    client: str,
    server_name: str = "multisim",
    python_executable: str | None = None,
    worker_python: str | None = None,
    template_dir: str | None = None,
    work_dir: str | None = None,
    tool_profile: str | None = None,
    artifact_export_dir: str | None = None,
) -> str:
    """Render a copy-pasteable MCP client configuration fragment."""
    if client not in CLIENTS:
        raise ValueError(f"unsupported client: {client}")
    if not _SERVER_NAME_RE.fullmatch(server_name):
        raise ValueError(
            "server name may contain only letters, digits, dot, underscore, and hyphen"
        )
    if tool_profile is not None and tool_profile not in TOOL_PROFILES:
        raise ValueError(f"unsupported tool profile: {tool_profile}")
    spec = _server_spec(
        python_executable or sys.executable,
        worker_python,
        template_dir,
        work_dir,
        tool_profile,
        artifact_export_dir,
    )
    if client == "deepseek-harness":
        return _render_deepseek_harness_config(server_name, spec)
    if client == "codex":
        lines = [
            f"[mcp_servers.{server_name}]",
            f"command = {_toml_string(spec['command'])}",
            "args = [" + ", ".join(_toml_string(item) for item in spec["args"]) + "]",
        ]
        if spec.get("env"):
            lines.append("")
            lines.append(f"[mcp_servers.{server_name}.env]")
            lines.extend(
                f"{key} = {_toml_string(value)}"
                for key, value in sorted(spec["env"].items())
            )
        return "\n".join(lines) + "\n"
    payload: dict[str, Any]
    if client == "claude-desktop":
        payload = {"mcpServers": {server_name: spec}}
    else:
        payload = spec
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _write_config(path: str, content: str, force: bool) -> Path:
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if force else "x"
    try:
        with output.open(mode, encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except FileExistsError as exc:
        raise FileExistsError(
            f"output already exists; pass --force to replace it: {output}"
        ) from exc
    return output


def _print_doctor_human(report: dict[str, Any]) -> None:
    symbols = {"pass": "OK", "fail": "FAIL", "warn": "WARN", "skipped": "SKIP"}
    print(report["summary"])
    for check in report["checks"]:
        print(f"[{symbols[check['status']]}] {check['message']}")
        if check.get("repair"):
            print(f"       -> {check['repair']}")


def _run_server() -> None:
    _load_module("multisim_mcp.server").main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multisim-mcp",
        description="NI Multisim MCP server, diagnostics, and client configuration",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--json", dest="json_global", action="store_true", help="emit stable JSON"
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("serve", help="start the MCP stdio server")

    doctor = subparsers.add_parser("doctor", help="diagnose local Multisim MCP setup")
    doctor.add_argument(
        "--json", dest="json_command", action="store_true", help="emit stable JSON"
    )
    doctor.add_argument("--lang", choices=("auto", "zh", "en"), default="auto")
    doctor.add_argument(
        "--connect",
        action="store_true",
        help="explicitly activate and connect to Multisim in a disposable worker",
    )
    doctor.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero unless the complete Multisim workflow is ready",
    )

    config = subparsers.add_parser(
        "config", help="generate an MCP client configuration fragment"
    )
    config.add_argument("--client", choices=CLIENTS, required=True)
    config.add_argument("--name", default="multisim", help="MCP server name")
    config.add_argument(
        "--python", default=sys.executable, help="MCP frontend Python executable"
    )
    config.add_argument(
        "--worker-python",
        help="32-bit Python executable (auto-discovered through py launcher by default)",
    )
    config.add_argument("--template-dir", help="local component template pack")
    config.add_argument(
        "--work-dir", help="space-free Multisim experiment work directory"
    )
    config.add_argument(
        "--tool-profile",
        choices=TOOL_PROFILES,
        help="limit tools/list to a task-oriented profile (default: full)",
    )
    config.add_argument(
        "--artifact-export-dir",
        help="approved root for export_experiment_artifact",
    )
    config.add_argument("--output", help="write the fragment to this file")
    config.add_argument(
        "--force", action="store_true", help="replace an existing output file"
    )
    config.add_argument(
        "--json",
        dest="json_command",
        action="store_true",
        help="emit a JSON result envelope",
    )

    harness_skills = subparsers.add_parser(
        "harness-skills",
        help="install the bundled DeepSeek Harness skills into a project",
    )
    harness_skills.add_argument(
        "--output",
        default=".dsh/skills",
        help="Harness skill discovery root (default: .dsh/skills)",
    )
    harness_skills.add_argument(
        "--force",
        action="store_true",
        help="replace existing copies of the five bundled skills",
    )
    harness_skills.add_argument(
        "--json",
        dest="json_command",
        action="store_true",
        help="emit a JSON result envelope",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        _run_server()
        return 0
    parser = build_parser()
    args = parser.parse_args(arguments)
    json_output = bool(
        getattr(args, "json_global", False) or getattr(args, "json_command", False)
    )

    if args.command == "serve":
        _run_server()
        return 0
    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "doctor":
        report = collect_doctor_report(args.lang, connect=args.connect)
        if json_output:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _print_doctor_human(report)
        return 1 if args.strict and not report["full_workflow_ready"] else 0
    if args.command == "harness-skills":
        try:
            result = install_harness_skills(args.output, force=args.force)
        except (OSError, ValueError) as exc:
            if json_output:
                print(
                    json.dumps(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "command": "harness-skills",
                            "success": False,
                            "error": {"type": type(exc).__name__, "message": str(exc)},
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                parser.error(str(exc))
            return 2
        if json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(result["output_dir"])
        return 0
    if args.command == "config":
        try:
            content = render_client_config(
                args.client,
                server_name=args.name,
                python_executable=args.python,
                worker_python=args.worker_python,
                template_dir=args.template_dir,
                work_dir=args.work_dir,
                tool_profile=args.tool_profile,
                artifact_export_dir=args.artifact_export_dir,
            )
            output = (
                _write_config(args.output, content, args.force) if args.output else None
            )
        except (OSError, ValueError) as exc:
            if json_output:
                print(
                    json.dumps(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "command": "config",
                            "success": False,
                            "error": {"type": type(exc).__name__, "message": str(exc)},
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                parser.error(str(exc))
            return 2
        if json_output:
            print(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "command": "config",
                        "success": True,
                        "client": args.client,
                        "server_name": args.name,
                        "output": str(output) if output else None,
                        "content": content,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif output:
            print(output)
        else:
            print(content, end="")
        return 0
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
