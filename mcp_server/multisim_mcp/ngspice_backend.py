"""Safe open-source ngspice implementation of the EDA backend contract."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .eda_backend import (
    BackendCapabilities,
    BackendDiagnostic,
    BackendExecution,
    SchematicRequest,
    SimulationRequest,
)
from .eda_core import Artifact, ArtifactSet, CircuitDesign, _derived_identifier
from .safety import validate_analysis_commands, validate_spice_netlist
from .schematic_builder import prepare_simulation_netlist
from .spice_adapter import circuit_design_to_spice
from .spice_raw import limit_points, parse_raw, summarize_columns, write_ascii_raw, write_csv


NGSPICE_EXECUTABLE_ENV = "MULTISIM_MCP_NGSPICE"
_ARTIFACT_TYPES = {
    ".cir": ("netlist", "text/x-spice"),
    ".csv": ("data", "text/csv"),
    ".log": ("log", "text/plain"),
    ".raw": ("simulation-data", "application/octet-stream"),
    ".txt": ("commands", "text/plain"),
}

ProcessRunner = Callable[[list[str], Path, float], subprocess.CompletedProcess[str]]


def _default_process_runner(
    argv: list[str], cwd: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
    )


def cancellable_process_runner(
    cancel_requested: Callable[[], bool] | None = None,
    heartbeat: Callable[[], None] | None = None,
    *,
    poll_interval: float = 0.25,
) -> ProcessRunner:
    """Build a process runner that polls durable cancellation and heartbeat hooks."""
    if not 0.05 <= poll_interval <= 5.0:
        raise ValueError("poll_interval must be between 0.05 and 5 seconds")

    def run(
        argv: list[str], cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        started = time.monotonic()

        def stop() -> tuple[str, str]:
            process.terminate()
            try:
                return process.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                return process.communicate()

        while True:
            if cancel_requested is not None and cancel_requested():
                stop()
                raise InterruptedError("Experiment cancellation requested")
            if heartbeat is not None:
                heartbeat()
            remaining = float(timeout) - (time.monotonic() - started)
            if remaining <= 0:
                stdout, stderr = stop()
                raise subprocess.TimeoutExpired(
                    argv,
                    timeout,
                    output=stdout,
                    stderr=stderr,
                )
            try:
                stdout, stderr = process.communicate(
                    timeout=min(poll_interval, remaining)
                )
                return subprocess.CompletedProcess(
                    argv,
                    process.returncode,
                    stdout,
                    stderr,
                )
            except subprocess.TimeoutExpired:
                continue

    return run


def resolve_ngspice_executable(
    executable: str | os.PathLike[str] | None = None,
    *,
    required: bool = True,
) -> Path | None:
    """Resolve an explicitly configured executable or ngspice on PATH."""
    configured = str(executable or os.environ.get(NGSPICE_EXECUTABLE_ENV, "")).strip()
    located: str | None
    if configured:
        candidate = Path(configured).expanduser()
        located = str(candidate.resolve()) if candidate.is_file() else shutil.which(configured)
    else:
        located = shutil.which("ngspice")
    if located is None:
        if required:
            raise RuntimeError(
                f"ngspice executable was not found; install ngspice or set {NGSPICE_EXECUTABLE_ENV}"
            )
        return None
    resolved = Path(located).expanduser().resolve()
    if not resolved.is_file():
        if required:
            raise RuntimeError(f"ngspice executable is not a regular file: {resolved}")
        return None
    return resolved


def probe_ngspice(
    executable: str | os.PathLike[str] | None = None,
    *,
    timeout: float = 5.0,
    process_runner: ProcessRunner = _default_process_runner,
) -> dict[str, Any]:
    """Return bounded runtime availability/version evidence without simulation."""
    resolved = resolve_ngspice_executable(executable, required=False)
    if resolved is None:
        return {"available": False, "executable": None, "version": None}
    try:
        completed = process_runner([str(resolved), "--version"], resolved.parent, timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "executable": str(resolved),
            "version": None,
            "error": str(exc)[:1000],
        }
    output = "\n".join(value.strip() for value in (completed.stdout, completed.stderr) if value.strip())
    return {
        "available": completed.returncode == 0,
        "executable": str(resolved),
        "version": output[:2000] or None,
        "returncode": completed.returncode,
    }


def prepare_ngspice_deck(netlist: str, commands: str) -> tuple[str, list[str]]:
    """Build an internal batch deck from independently validated user inputs."""
    validate_spice_netlist(netlist)
    accepted = validate_analysis_commands(commands)
    # A legal source may leave comments after .end. Remove the terminal card
    # wherever it appears so the generated, trusted control section is never
    # accidentally placed behind an earlier end-of-deck marker.
    source_lines = [
        line for line in netlist.splitlines() if line.strip().casefold() != ".end"
    ]
    while source_lines and not source_lines[-1].strip():
        source_lines.pop()
    analysis_lines = [f".{command}" for command in accepted]
    deck = "\n".join(
        [
            "* Multisim MCP generated ngspice batch deck",
            *source_lines,
            ".save all",
            *analysis_lines,
            ".control",
            "set filetype=ascii",
            "run",
            "write result.raw all",
            "quit",
            ".endc",
            ".end",
            "",
        ]
    )
    return deck, accepted


def _require_output_directory(value: str | None) -> tuple[Path, bool]:
    if value is None:
        return Path(tempfile.mkdtemp(prefix="multisim-mcp-ngspice-")).resolve(), True
    candidate = Path(value).expanduser()
    if candidate.exists() and candidate.is_symlink():
        raise ValueError("output_directory must not be a symbolic link")
    root = candidate.resolve()
    if root == Path(root.anchor):
        raise ValueError("output_directory must not be a filesystem root")
    if root.exists() and not root.is_dir():
        raise ValueError("output_directory must be a directory")
    root.mkdir(parents=True, exist_ok=True)
    return root, False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_set(design: CircuitDesign, root: Path, paths: list[Path]) -> ArtifactSet:
    artifacts: list[Artifact] = []
    for index, path in enumerate(sorted(paths, key=lambda item: item.name), start=1):
        if not path.is_file():
            continue
        kind, media_type = _ARTIFACT_TYPES.get(path.suffix.casefold(), ("artifact", "application/octet-stream"))
        artifacts.append(
            Artifact(
                artifact_id=_derived_identifier(design.design_id, "simulate", index),
                name=path.name,
                kind=kind,
                location=str(path),
                media_type=media_type,
                size=path.stat().st_size,
                sha256=_sha256(path),
            )
        )
    return ArtifactSet(
        artifact_set_id=_derived_identifier(design.design_id, "simulate", hashlib.sha256(str(root).encode()).hexdigest()[:12]),
        design_id=design.design_id,
        producer="ngspice",
        artifacts=tuple(artifacts),
        metadata={"output_directory": str(root), "storage_location": str(root)},
    )


class NgspiceBackend:
    """Cross-platform simulation backend using a local ngspice process."""

    backend_id = "ngspice"

    def __init__(
        self,
        executable: str | os.PathLike[str] | None = None,
        *,
        process_runner: ProcessRunner = _default_process_runner,
    ) -> None:
        if not callable(process_runner):
            raise ValueError("process_runner must be callable")
        self._configured_executable = executable
        self._process_runner = process_runner

    def discover_capabilities(self) -> BackendCapabilities:
        resolved = resolve_ngspice_executable(self._configured_executable, required=False)
        return BackendCapabilities(
            backend_id=self.backend_id,
            display_name="ngspice open-source simulator",
            operations=("validate", "simulate"),
            analyses=("op", "dc", "ac", "tran"),
            platforms=("linux", "macos", "windows"),
            requires_local_runtime=True,
            supports_editable_schematic=False,
            supports_vendor_models=False,
            supports_batch=True,
            metadata={
                "adapter_schema_version": 1,
                "runtime_available": resolved is not None,
                "configured_by": NGSPICE_EXECUTABLE_ENV,
                "safe_command_surface": True,
                "raw_format": "spice3-ascii",
            },
        )

    def probe_runtime(self, *, timeout: float = 5.0) -> dict[str, Any]:
        return probe_ngspice(
            self._configured_executable,
            timeout=timeout,
            process_runner=self._process_runner,
        )

    def validate_design(self, design: CircuitDesign) -> tuple[BackendDiagnostic, ...]:
        if not isinstance(design, CircuitDesign):
            raise ValueError("design must be CircuitDesign")
        try:
            circuit_design_to_spice(design)
        except ValueError as exc:
            return (
                BackendDiagnostic(
                    severity="error",
                    code="ngspice-compile-failed",
                    message=str(exc),
                ),
            )
        return ()

    def create_schematic(self, request: SchematicRequest) -> BackendExecution:
        raise ValueError("ngspice does not provide an editable schematic operation")

    def simulate(self, request: SimulationRequest) -> BackendExecution:
        if not isinstance(request, SimulationRequest):
            raise ValueError("request must be SimulationRequest")
        if request.unsafe_commands:
            raise ValueError("ngspice backend supports only the validated safe command surface")
        diagnostics = list(self.validate_design(request.design))
        if diagnostics:
            raise ValueError("; ".join(item.message for item in diagnostics))
        executable = resolve_ngspice_executable(self._configured_executable)
        assert executable is not None
        netlist = prepare_simulation_netlist(
            circuit_design_to_spice(request.design), ngspice_compatible=True
        )
        deck, accepted = prepare_ngspice_deck(netlist, request.commands)
        root, managed = _require_output_directory(request.output_directory)
        names = ("circuit.cir", "run.txt", "run.log", "result.raw", "data.csv")
        if not managed:
            for name in names:
                target = root / name
                if target.exists() and not request.overwrite:
                    raise FileExistsError(f"ngspice artifact already exists: {target}")
                if target.is_symlink() or (target.exists() and not target.is_file()):
                    raise ValueError(f"ngspice artifact target must be a regular file: {target}")
        stage = root if managed else root / f".ngspice-{uuid.uuid4().hex}"
        stage.mkdir(parents=managed, exist_ok=managed)
        circuit_path = stage / "circuit.cir"
        commands_path = stage / "run.txt"
        log_path = stage / "run.log"
        raw_path = stage / "result.raw"
        csv_path = stage / "data.csv"
        circuit_path.write_text(deck, encoding="utf-8", newline="\n")
        commands_path.write_text("\n".join(accepted) + "\n", encoding="utf-8", newline="\n")

        success = False
        error: str | None = None
        returncode: int | None = None
        stdout = ""
        stderr = ""
        measurements: list[dict[str, Any]] = []
        point_count = 0
        try:
            completed = self._process_runner(
                [str(executable), "-n", "-b", "-o", "run.log", "circuit.cir"],
                stage,
                float(request.timeout_seconds),
            )
            returncode = completed.returncode
            stdout = (completed.stdout or "")[:4000]
            stderr = (completed.stderr or "")[:4000]
            if not log_path.exists():
                log_path.write_text(stdout + ("\n" if stdout and stderr else "") + stderr, encoding="utf-8")
            if completed.returncode != 0:
                error = f"ngspice exited with status {completed.returncode}"
            elif not raw_path.is_file() or raw_path.stat().st_size <= 0:
                error = "ngspice did not produce result.raw"
            else:
                parsed = parse_raw(str(raw_path))
                parsed = limit_points(parsed, request.max_points)
                write_ascii_raw(str(raw_path), parsed)
                write_csv(str(csv_path), parsed)
                measurements = summarize_columns(parsed)
                point_count = int(parsed["n_points"])
                success = True
        except subprocess.TimeoutExpired:
            error = f"ngspice exceeded the {float(request.timeout_seconds):g}s timeout"
        except (OSError, ValueError) as exc:
            error = str(exc)
        finally:
            if not log_path.exists():
                log_path.write_text((error or "ngspice execution failed") + "\n", encoding="utf-8")

        if not managed:
            for name in names:
                source = stage / name
                if source.is_file():
                    os.replace(source, root / name)
            shutil.rmtree(stage, ignore_errors=True)
        paths = [root / name for name in names if (root / name).is_file()]
        if not success:
            diagnostics.append(
                BackendDiagnostic(
                    severity="error",
                    code="ngspice-simulation-failed",
                    message=error or "ngspice simulation failed",
                    details={"returncode": returncode, "stderr": stderr[:1000]},
                )
            )
        compatibility_result: dict[str, Any] = {
            "success": success,
            "backend_id": self.backend_id,
            "work_dir": str(root),
            "output_dir": str(root),
            "netlist": str(root / "circuit.cir"),
            "commands": str(root / "run.txt"),
            "log": str(root / "run.log"),
            "raw": str(root / "result.raw") if (root / "result.raw").is_file() else None,
            "csv": str(root / "data.csv") if (root / "data.csv").is_file() else None,
            "artifacts": [str(path) for path in paths],
            "accepted_commands": accepted,
            "returncode": returncode,
            "point_count": point_count,
            "measurements": measurements,
            "stdout": stdout,
            "stderr": stderr,
            "error": error,
        }
        return BackendExecution(
            backend_id=self.backend_id,
            operation="simulate",
            success=success,
            artifacts=_artifact_set(request.design, root, paths),
            diagnostics=tuple(diagnostics),
            payload={
                "compatibility_result": compatibility_result,
                "commands": request.commands,
                "max_points": request.max_points,
                "timeout_seconds": float(request.timeout_seconds),
                "unsafe_commands": False,
            },
        )


__all__ = [
    "NGSPICE_EXECUTABLE_ENV",
    "NgspiceBackend",
    "cancellable_process_runner",
    "prepare_ngspice_deck",
    "probe_ngspice",
    "resolve_ngspice_executable",
]
