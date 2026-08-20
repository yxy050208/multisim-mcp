"""Thread-safe frontend proxy for the isolated 32-bit Multisim worker."""

from __future__ import annotations

import atexit
import json
import os
import queue
import shutil
import struct
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from .worker_protocol import PROTOCOL_VERSION


WORKER_PYTHON_ENV: Final = "MULTISIM_MCP_WORKER_PYTHON"
WORKER_TIMEOUT_ENV: Final = "MULTISIM_MCP_WORKER_RPC_TIMEOUT"


class WorkerUnavailableError(RuntimeError):
    """Raised when a compatible 32-bit worker cannot be started."""


def _python_prefix() -> list[str]:
    configured = os.environ.get(WORKER_PYTHON_ENV, "").strip().strip('"')
    if configured:
        resolved = shutil.which(configured)
        path = Path(resolved or configured).expanduser()
        if not path.is_file():
            raise WorkerUnavailableError(
                f"{WORKER_PYTHON_ENV} does not identify a Python executable: {configured}"
            )
        return [str(path.resolve())]
    if os.name != "nt":
        raise WorkerUnavailableError("Multisim automation requires Windows")
    if struct.calcsize("P") * 8 == 32:
        return [sys.executable]
    launcher = shutil.which("py")
    if launcher:
        return [launcher, "-3-32"]
    raise WorkerUnavailableError(
        "No 32-bit Python worker was found. Install 32-bit Python or set "
        f"{WORKER_PYTHON_ENV}."
    )


def _bootstrap_command(prefix: list[str]) -> list[str]:
    package_root = Path(__file__).resolve().parents[1]
    roots = [] if package_root.name.lower() == "site-packages" else [str(package_root)]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path[:0]={roots!r};"
        "runpy.run_module('multisim_mcp.com_worker',run_name='__main__')"
    )
    return [*prefix, "-c", bootstrap, "--parent-pid", str(os.getpid())]


def _timeout_default() -> float:
    raw = os.environ.get(WORKER_TIMEOUT_ENV, "").strip()
    if not raw:
        return 300.0
    try:
        value = float(raw)
    except ValueError as exc:
        raise WorkerUnavailableError(f"{WORKER_TIMEOUT_ENV} must be numeric") from exc
    if value <= 0 or value > 3600:
        raise WorkerUnavailableError(
            f"{WORKER_TIMEOUT_ENV} must be greater than 0 and no more than 3600"
        )
    return value


def _remote_exception(error: object) -> BaseException:
    details = error if isinstance(error, dict) else {}
    kind = str(details.get("type", "RuntimeError"))
    message = str(details.get("message", "Multisim worker operation failed"))
    classes: dict[str, type[BaseException]] = {
        "FileExistsError": FileExistsError,
        "FileNotFoundError": FileNotFoundError,
        "InterruptedError": InterruptedError,
        "PermissionError": PermissionError,
        "TimeoutError": TimeoutError,
        "ValueError": ValueError,
    }
    return classes.get(kind, RuntimeError)(message)


class MultisimWorkerProcess:
    """Own one lazy worker process and serialize stateful Automation calls."""

    def __init__(
        self,
        worker_command: list[str] | None = None,
        *,
        require_32bit: bool = True,
        startup_timeout: float = 15.0,
    ) -> None:
        self._worker_command = list(worker_command) if worker_command else None
        self._require_32bit = require_32bit
        self._startup_timeout = startup_timeout
        self._call_lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._ready: dict[str, Any] | None = None
        self._stderr_tail: list[str] = []
        atexit.register(self.close)

    def _command(self) -> list[str]:
        return self._worker_command or _bootstrap_command(_python_prefix())

    def _read_output(
        self,
        process: subprocess.Popen[str],
        messages: queue.Queue[dict[str, Any]],
    ) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = {
                    "event": "protocol_error",
                    "message": line.rstrip()[:2000],
                }
            if not isinstance(payload, dict):
                payload = {"event": "protocol_error", "message": "Non-object response"}
            messages.put(payload)
        messages.put({"event": "eof", "returncode": process.poll()})

    def _read_error(self, process: subprocess.Popen[str]) -> None:
        assert process.stderr is not None
        for line in process.stderr:
            self._stderr_tail.append(line.rstrip())
            del self._stderr_tail[:-40]

    def _startup_detail(self) -> str:
        if not self._stderr_tail:
            return ""
        return ": " + " | ".join(self._stderr_tail[-8:])[-4000:]

    def _start_locked(self) -> dict[str, Any]:
        if self._process is not None and self._process.poll() is None:
            assert self._ready is not None
            return self._ready
        self._clear_locked()
        messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_tail = []
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        )
        try:
            process = subprocess.Popen(
                self._command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise WorkerUnavailableError(
                f"Could not start the Multisim worker: {exc}"
            ) from exc
        self._process = process
        self._messages = messages
        threading.Thread(
            target=self._read_output,
            args=(process, messages),
            name="multisim-com-worker-output",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_error,
            args=(process,),
            name="multisim-com-worker-error",
            daemon=True,
        ).start()
        try:
            ready = messages.get(timeout=self._startup_timeout)
        except queue.Empty as exc:
            self._terminate_locked()
            raise WorkerUnavailableError(
                "Multisim worker startup timed out" + self._startup_detail()
            ) from exc
        if ready.get("event") != "ready":
            self._terminate_locked()
            raise WorkerUnavailableError(
                "Multisim worker did not produce a valid ready message"
                + self._startup_detail()
            )
        if ready.get("protocol_version") != PROTOCOL_VERSION:
            self._terminate_locked()
            raise WorkerUnavailableError("Multisim worker protocol version mismatch")
        diagnostics = ready.get("diagnostics")
        if not isinstance(diagnostics, dict):
            self._terminate_locked()
            raise WorkerUnavailableError("Multisim worker omitted runtime diagnostics")
        bits = int(diagnostics.get("python_bits", 0))
        if self._require_32bit and bits != 32:
            self._terminate_locked()
            raise WorkerUnavailableError(
                f"Multisim worker must use 32-bit Python; selected runtime is {bits}-bit"
            )
        self._ready = ready
        return ready

    def diagnostics(self) -> dict[str, Any]:
        with self._call_lock:
            ready = self._start_locked()
            diagnostics = dict(ready["diagnostics"])
            diagnostics.update(
                worker_isolated=True,
                worker_pid=int(ready["pid"]),
                worker_protocol_version=PROTOCOL_VERSION,
                frontend_python_bits=struct.calcsize("P") * 8,
                frontend_python_executable=sys.executable,
            )
            return diagnostics

    def call(
        self,
        target: str,
        method: str,
        *args: object,
        rpc_timeout: float | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        heartbeat: Callable[[], None] | None = None,
        **kwargs: object,
    ) -> Any:
        request_id = f"rpc-{uuid.uuid4().hex}"
        with self._call_lock:
            self._start_locked()
            assert self._process is not None and self._process.stdin is not None
            request = {
                "protocol_version": PROTOCOL_VERSION,
                "id": request_id,
                "target": target,
                "method": method,
                "args": list(args),
                "kwargs": kwargs,
            }
            try:
                self._process.stdin.write(
                    json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._clear_locked()
                raise WorkerUnavailableError("Multisim worker pipe closed") from exc
            deadline = time.monotonic() + (rpc_timeout or _timeout_default())
            cancel_sent = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._send_cancel_locked(request_id)
                    self._terminate_locked()
                    raise TimeoutError(
                        f"Multisim worker call {target}.{method} timed out"
                    )
                try:
                    response = self._messages.get(timeout=min(0.25, remaining))
                except queue.Empty:
                    response = {}
                if cancel_requested is not None and cancel_requested() and not cancel_sent:
                    self._send_cancel_locked(request_id)
                    cancel_sent = True
                event = response.get("event")
                if event == "heartbeat" and response.get("id") == request_id:
                    if heartbeat is not None:
                        try:
                            heartbeat()
                        except InterruptedError:
                            if not cancel_sent:
                                self._send_cancel_locked(request_id)
                                cancel_sent = True
                    continue
                if event == "eof":
                    code = response.get("returncode")
                    self._clear_locked()
                    raise WorkerUnavailableError(
                        f"Multisim worker exited during {target}.{method} (code {code})"
                    )
                if event == "protocol_error":
                    self._terminate_locked()
                    raise WorkerUnavailableError(
                        "Multisim worker emitted invalid protocol output"
                    )
                if response.get("id") != request_id:
                    continue
                if response.get("protocol_version") != PROTOCOL_VERSION:
                    self._terminate_locked()
                    raise WorkerUnavailableError("Multisim worker response version mismatch")
                if response.get("ok") is True:
                    return response.get("result")
                raise _remote_exception(response.get("error"))

    def _send_cancel_locked(self, request_id: str) -> None:
        if self._process is None or self._process.stdin is None:
            return
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "id": request_id,
            "target": "control",
            "method": "cancel",
        }
        try:
            self._process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _terminate_locked(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        self._clear_locked()

    def _clear_locked(self) -> None:
        process = self._process
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        self._process = None
        self._ready = None
        self._messages = queue.Queue()

    def close(self) -> None:
        with self._call_lock:
            process = self._process
            if process is None or process.poll() is not None:
                self._clear_locked()
                return
            try:
                assert process.stdin is not None
                request = {
                    "protocol_version": PROTOCOL_VERSION,
                    "id": f"shutdown-{uuid.uuid4().hex}",
                    "target": "control",
                    "method": "shutdown",
                }
                process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
                process.stdin.flush()
                process.wait(timeout=3)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                self._terminate_locked()
            finally:
                self._clear_locked()


class WorkerMultisimClient:
    """Signature-compatible proxy for :class:`MultisimClient`."""

    def __init__(self, worker: MultisimWorkerProcess) -> None:
        self._worker = worker

    @property
    def circuit(self) -> object:
        self.circuit_info()
        return self

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_"):
            raise AttributeError(name)

        def invoke(*args: object, **kwargs: object) -> Any:
            return self._worker.call("client", name, *args, **kwargs)

        return invoke

    def run_command_file(
        self,
        command_file: str,
        log_file: str,
        timeout: float = 60.0,
        cancel_requested: Callable[[], bool] | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        result = self._worker.call(
            "client",
            "run_command_file",
            command_file,
            log_file,
            timeout,
            rpc_timeout=max(timeout + 30.0, _timeout_default()),
            cancel_requested=cancel_requested,
            heartbeat=heartbeat,
        )
        if not isinstance(result, dict):
            raise RuntimeError("Multisim worker returned an invalid command result")
        return result


class WorkerMs14Codec:
    """Signature-compatible proxy for `.ms14` encode/decode operations."""

    def __init__(self, worker: MultisimWorkerProcess) -> None:
        self._worker = worker

    def decode(self, source: str, output_xml: str | None = None) -> dict[str, Any]:
        result = self._worker.call(
            "codec", "decode", source, output_xml, rpc_timeout=180.0
        )
        if not isinstance(result, dict):
            raise RuntimeError("Multisim worker returned an invalid decode result")
        return result

    def encode(self, source_xml: str, output_ms14: str | None = None) -> dict[str, Any]:
        result = self._worker.call(
            "codec", "encode", source_xml, output_ms14, rpc_timeout=180.0
        )
        if not isinstance(result, dict):
            raise RuntimeError("Multisim worker returned an invalid encode result")
        return result


def worker_runtime_diagnostics(worker: MultisimWorkerProcess) -> dict[str, Any]:
    """Return worker compatibility without activating Multisim itself."""
    try:
        return worker.diagnostics()
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "platform": sys.platform,
            "windows": os.name == "nt",
            "python": sys.version.split()[0],
            "python_executable": sys.executable,
            "python_bits": 0,
            "required_python_bits": 32,
            "frontend_python_bits": struct.calcsize("P") * 8,
            "frontend_python_executable": sys.executable,
            "pywin32_available": False,
            "runtime_compatible": False,
            "runtime_mode": "introspection-only",
            "runtime_message": str(exc),
            "worker_isolated": True,
            "worker_error": str(exc),
            "worker_protocol_version": PROTOCOL_VERSION,
        }


__all__ = [
    "MultisimWorkerProcess",
    "WORKER_PYTHON_ENV",
    "WORKER_TIMEOUT_ENV",
    "WorkerMs14Codec",
    "WorkerMultisimClient",
    "WorkerUnavailableError",
    "worker_runtime_diagnostics",
]
