"""Safely probe one Multisim database replacement on a disposable copy.

The Automation API can terminate the native Multisim process for an
incompatible carrier instead of returning a COM exception.  This helper runs
the operation in a separate 32-bit Python process, never opens the source file
directly, and removes only a newly-created Multisim process after a crash or
timeout.  It reports metadata, BOM, and native-netlist paths; it never prints
or publishes model bodies.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


# MultisimDB.MultisimMasterDB enum value.  The typelib variable memid is
# 1073741824, but that is not the value accepted by ReplaceComponent.
MASTER_DB = 0
_TOKEN_RE = r"^[A-Za-z0-9_.+/-]{1,128}$"


def _validate_token(value: str, label: str, *, allow_empty: bool = False) -> str:
    import re

    value = value.strip()
    if allow_empty and not value:
        return value
    if not re.fullmatch(_TOKEN_RE, value):
        raise ValueError(f"{label} contains unsupported characters")
    return value


def _safe_output(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path == Path(path.anchor) or path.suffix.casefold() != ".ms14":
        raise ValueError("output must be a non-root .ms14 path")
    return path


def _process_snapshot() -> dict[int, dict[str, Any]]:
    """Return Multisim PID/path/start-time metadata without third-party deps."""
    if os.name != "nt":
        return {}
    command = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "Get-Process -Name Multisim | "
        "Select-Object Id,Path,StartTime | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return {}
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return {}
    rows = payload if isinstance(payload, list) else [payload]
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row["Id"])
        except (KeyError, TypeError, ValueError):
            continue
        result[pid] = row
    return result


def _cleanup_new_processes(before: set[int]) -> list[int]:
    killed: list[int] = []
    for pid, row in _process_snapshot().items():
        if pid in before:
            continue
        path = str(row.get("Path") or "")
        if Path(path).name.casefold() != "multisim.exe":
            continue
        try:
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=False,
            )
            killed.append(pid)
        except (OSError, subprocess.SubprocessError):
            continue
    return killed


def _python_command() -> list[str]:
    configured = os.environ.get("MULTISIM_MCP_WORKER_PYTHON", "").strip().strip('"')
    if configured:
        executable = shutil.which(configured) or configured
        if not Path(executable).is_file():
            raise ValueError("MULTISIM_MCP_WORKER_PYTHON is not an executable")
        return [str(Path(executable).resolve())]
    if struct.calcsize("P") * 8 == 32:
        return [sys.executable]
    launcher = shutil.which("py")
    if launcher:
        return [launcher, "-3-32"]
    raise RuntimeError("a 32-bit Python worker is required")


def _child(args: argparse.Namespace) -> int:
    from multisim_mcp.multisim_client import MultisimClient

    source = Path(args.source).resolve()
    output = _safe_output(Path(args.output))
    client = MultisimClient()
    result: dict[str, Any] = {
        "status": "started",
        "source_copy": str(source),
        "output": str(output),
        "carrier": {"refdes": args.component, "section": args.section},
        "target": {
            "database": args.database,
            "group": args.group,
            "family": args.family,
            "source": args.source_name,
            "model": args.model,
        },
    }
    try:
        result["connect"] = client.connect()
        result["open"] = client.open_circuit(str(source))
        result["before_components"] = client.enum_components(0)
        try:
            result["before_sections"] = list(
                client.circuit.EnumSections(args.component) or ()
            )
        except Exception as exc:
            # Some vendor components reject EnumSections even though the
            # replacement API accepts an explicit empty section. Preserve the
            # diagnostic and continue with the documented operation.
            result["before_sections_error"] = f"{type(exc).__name__}: {exc}"
        result["replace"] = client.circuit.ReplaceComponent(
            args.component,
            args.section,
            int(args.database),
            args.group,
            args.family,
            args.source_name,
            args.model,
        )
        result["after_components"] = client.enum_components(0)
        try:
            result["after_sections"] = list(
                client.circuit.EnumSections(args.component) or ()
            )
        except Exception as exc:
            result["after_sections_error"] = f"{type(exc).__name__}: {exc}"
        output.parent.mkdir(parents=True, exist_ok=True)
        result["save"] = client.save_circuit(str(output))
        bom = output.with_name(output.stem + "-bom.txt")
        netlist = output.with_suffix(".net")
        result["bom"] = client.report_bom(str(bom))
        result["netlist"] = client.report_netlist(str(netlist), False, 0)
        result["artifacts"] = {
            "ms14": str(output),
            "bom": str(bom),
            "netlist": str(netlist),
        }
        result["status"] = "success"
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def probe_replacement(
    source: Path,
    output: Path,
    *,
    component: str,
    section: str,
    database: int,
    group: str,
    family: str,
    source_name: str,
    model: str,
    timeout: float = 120.0,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    output = _safe_output(output)
    if not source.is_file() or source.suffix.casefold() != ".ms14":
        raise ValueError("source must be an existing .ms14 file")
    if source == output:
        raise ValueError("output must differ from source")
    if database != MASTER_DB:
        raise ValueError("only the Multisim master database is supported")
    component = _validate_token(component, "component")
    section = _validate_token(section, "section", allow_empty=True)
    group = _validate_token(group, "group")
    family = _validate_token(family, "family")
    source_name = _validate_token(source_name, "source_name")
    model = _validate_token(model, "model", allow_empty=True)
    if timeout <= 0 or timeout > 900:
        raise ValueError("timeout must be between 0 and 900 seconds")

    before = set(_process_snapshot())
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="multisim-native-probe-") as temp:
        source_copy = Path(temp) / source.name
        shutil.copy2(source, source_copy)
        command = [
            *_python_command(), str(Path(__file__).resolve()), "--child",
            "--source", str(source_copy), "--output", str(output),
            "--component", component, "--section", section,
            "--database", str(database), "--group", group, "--family", family,
            "--source-name", source_name, "--model", model,
        ]
        env = dict(os.environ)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            _cleanup_new_processes(before)
            return {
                "status": "timed_out",
                "returncode": process.returncode,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "stderr_tail": stderr[-2000:],
            }
        if process.returncode == 0 and stdout.strip():
            try:
                payload = json.loads(stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                payload = {"status": "invalid_worker_output", "stdout_tail": stdout[-2000:]}
            payload["elapsed_seconds"] = round(time.monotonic() - started, 3)
            # Probes are headless and operate on disposable copies. Release
            # the newly-started Multisim instance even after success so a
            # batch of candidates cannot accumulate native processes.
            payload["cleaned_multisim_pids"] = _cleanup_new_processes(before)
            if stderr.strip():
                payload["stderr_tail"] = stderr[-2000:]
            return payload
        killed = _cleanup_new_processes(before)
        crash_code = process.returncode is not None and (
            process.returncode < 0 or process.returncode >= 0xC0000000
        )
        return {
            "status": "worker_crashed" if crash_code else "worker_failed",
            "returncode": process.returncode,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "stderr_tail": stderr[-2000:],
            "stdout_tail": stdout[-2000:],
            "cleaned_multisim_pids": killed,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--component", required=True)
    parser.add_argument("--section", default="")
    parser.add_argument("--database", type=int, default=MASTER_DB)
    parser.add_argument("--group", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        raise SystemExit(_child(args))
    try:
        result = probe_replacement(
            args.source,
            args.output,
            component=args.component,
            section=args.section,
            database=args.database,
            group=args.group,
            family=args.family,
            source_name=args.source_name,
            model=args.model,
            timeout=args.timeout,
        )
    except Exception as exc:
        result = {"status": "invalid_request", "error": f"{type(exc).__name__}: {exc}"}
    # Windows consoles may still use an active GBK code page.  Keep the CLI
    # transport ASCII-safe; artifact files retain their original Unicode data.
    print(json.dumps(result, ensure_ascii=True, indent=2))
    raise SystemExit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
