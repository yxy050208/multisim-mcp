"""Run an isolated end-to-end smoke test with the official DeepSeek Harness."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _tail(path: Path, limit: int = 8000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:]


def _pnpm_command(package: str, version: str, *args: str) -> list[str]:
    executable = shutil.which("pnpm")
    if not executable:
        raise RuntimeError(
            "pnpm is unavailable; install the packageManager version pinned "
            "by compatibility/deepseek-harness.json"
        )
    return [executable, "dlx", f"{package}@{version}", *args]


def _process_group_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _wait_for_web(
    process: subprocess.Popen[Any], port: int, timeout: float
) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/"
    last_error = "web endpoint did not respond"
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            return False, f"dsh exited before readiness with code {return_code}"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return True, f"HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    return False, last_error


def _stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        except OSError:
            process.terminate()
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _run_bounded(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_process_group_options(),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_process(process)
        process.communicate()
        raise
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout,
        stderr,
    )


def run_smoke(
    repo_root: Path,
    *,
    install_timeout: float = 300,
    startup_timeout: float = 120,
) -> dict[str, Any]:
    started = time.monotonic()
    compatibility = json.loads(
        (repo_root / "compatibility" / "deepseek-harness.json").read_text(
            encoding="utf-8"
        )
    )
    upstream = compatibility["upstream"]
    contract = compatibility["contract"]
    package = upstream["dsh_cli_package"]
    version = upstream["dsh_cli_version"]
    patch = repo_root / contract["bundle_path"] / "cordis.patch.yml"
    if not patch.is_file():
        raise RuntimeError(f"Harness bundle patch is missing: {patch}")

    with tempfile.TemporaryDirectory(prefix="multisim-mcp-dsh-smoke-") as temporary:
        temp_root = Path(temporary)
        home = temp_root / "dsh-home"
        workspace = temp_root / "workspace"
        home.mkdir()
        workspace.mkdir()
        stdout_path = temp_root / "dsh.stdout.log"
        stderr_path = temp_root / "dsh.stderr.log"
        environment = os.environ.copy()
        environment.update(
            {
                "DSH_HOME": str(home),
                "DSH_TELEMETRY_DISABLED": "1",
                "MULTISIM_MCP_PYTHON": sys.executable,
                "MULTISIM_MCP_TOOL_PROFILE": "experiment",
                "PYTHONUTF8": "1",
            }
        )
        environment.pop("DEEPSEEK_API_KEY", None)

        version_check = _run_bounded(
            _pnpm_command(package, version, "--version"),
            cwd=workspace,
            env=environment,
            timeout=install_timeout,
        )
        if version_check.returncode != 0:
            raise RuntimeError(
                "official dsh version check failed: "
                + (version_check.stderr or version_check.stdout)[-4000:]
            )
        reported_version = version_check.stdout.strip()
        if version not in reported_version:
            raise RuntimeError(
                f"expected dsh {version}, version command returned {reported_version!r}"
            )

        dump = _run_bounded(
            _pnpm_command(
                package,
                version,
                "web",
                "--patch",
                str(patch),
                "--dump-config",
            ),
            cwd=workspace,
            env=environment,
            timeout=install_timeout,
        )
        if dump.returncode != 0:
            raise RuntimeError(
                "dsh rejected the Multisim Cordis patch: "
                + (dump.stderr or dump.stdout)[-4000:]
            )
        for fragment in ("mcp-multisim", "@deepseek-ai/dsh-mcp-client"):
            if fragment not in dump.stdout:
                raise RuntimeError(f"composed dsh config is missing {fragment!r}")

        port = _free_loopback_port()
        command = _pnpm_command(
            package,
            version,
            "web",
            "--patch",
            str(patch),
            "--no-open",
            "--port",
            str(port),
        )
        with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                env=environment,
                text=True,
                stdout=stdout_handle,
                stderr=stderr_handle,
                **_process_group_options(),
            )
            try:
                web_ready, readiness = _wait_for_web(
                    process, port, startup_timeout
                )
            finally:
                _stop_process(process)

        stdout_tail = _tail(stdout_path)
        stderr_tail = _tail(stderr_path)
        if not web_ready:
            raise RuntimeError(
                f"official dsh web smoke failed: {readiness}\n"
                f"stdout tail:\n{stdout_tail}\n"
                f"stderr tail:\n{stderr_tail}"
            )
        failure_markers = (
            "initial connection failed",
            "failed to list tools",
            "plugin activation failed",
        )
        combined = f"{stdout_tail}\n{stderr_tail}".lower()
        found_failures = [marker for marker in failure_markers if marker in combined]
        if found_failures:
            raise RuntimeError(
                "dsh became ready but reported MCP activation failure: "
                + ", ".join(found_failures)
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "success": True,
        "dsh_package": package,
        "dsh_version": version,
        "reported_version": reported_version,
        "config_dump": True,
        "mcp_fail_on_startup_error": True,
        "web_ready": True,
        "readiness": readiness,
        "api_key_forwarded": False,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Smoke-test Multisim MCP with the pinned official dsh CLI"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument("--install-timeout", type=float, default=300)
    parser.add_argument("--startup-timeout", type=float, default=120)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.install_timeout <= 0 or args.startup_timeout <= 0:
        raise SystemExit("timeouts must be positive")
    try:
        result = run_smoke(
            args.repo_root.resolve(),
            install_timeout=args.install_timeout,
            startup_timeout=args.startup_timeout,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "success": False,
            "error": {"type": type(exc).__name__, "message": str(exc)[-12000:]},
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("DeepSeek Harness smoke: " + ("PASS" if result["success"] else "FAIL"))
        if not result["success"]:
            print(result["error"]["message"])
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
