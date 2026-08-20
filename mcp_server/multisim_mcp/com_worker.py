"""32-bit subprocess host for all Multisim COM and codec operations.

The protocol is deliberately small and private to this package.  A reader
thread accepts newline-delimited JSON requests while the main thread owns the
COM apartment and executes one allowlisted operation at a time.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import struct
import sys
import threading
import traceback
from collections.abc import Callable, Mapping
from typing import Any, Final, TextIO

from .job_engine import _pid_alive
from .multisim_client import Ms14Codec, MultisimClient, runtime_diagnostics
from .worker_protocol import MAX_REQUEST_BYTES, PROTOCOL_VERSION


_CLIENT_METHODS: Final = frozenset(
    {
        "connect",
        "disconnect",
        "open_circuit",
        "new_circuit",
        "circuit_info",
        "enum_components",
        "enum_inputs",
        "enum_outputs",
        "set_output_request",
        "clear_output_request",
        "get_output_data",
        "wait_ready",
        "run_transient",
        "run_dc_operating_point",
        "run_ac_sweep",
        "run_ac_single_frequency",
        "set_input_data_sampled",
        "set_input_data_raw",
        "clear_input_data",
        "stop_simulation",
        "save_circuit",
        "get_circuit_image",
        "report_netlist",
        "report_bom",
        "generate_report",
        "do_command_line",
        "run_command_file",
        "get_rlc_value",
        "set_rlc_value",
    }
)
_CODEC_METHODS: Final = frozenset({"decode", "encode"})


class _WorkerService:
    """Dispatch validated protocol requests on the COM-owning thread."""

    def __init__(
        self,
        client: MultisimClient | None = None,
        codec: Ms14Codec | None = None,
    ) -> None:
        self.client = client or MultisimClient()
        self.codec = codec or Ms14Codec()

    def dispatch(
        self,
        request: Mapping[str, Any],
        cancellation: threading.Event,
        emit_heartbeat: Callable[[], None],
    ) -> Any:
        target = request.get("target")
        method = request.get("method")
        args = request.get("args", [])
        kwargs = request.get("kwargs", {})
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValueError("Worker args must be a list and kwargs must be an object")
        if target == "system":
            if method == "ping":
                return {
                    "protocol_version": PROTOCOL_VERSION,
                    "pid": os.getpid(),
                    "python_bits": struct.calcsize("P") * 8,
                }
            raise PermissionError("System worker method is not allowlisted")
        if target == "client":
            if method not in _CLIENT_METHODS:
                raise PermissionError("Multisim client method is not allowlisted")
            function = getattr(self.client, str(method))
            if method == "run_command_file":
                if len(args) > 3:
                    raise ValueError("run_command_file accepts three worker arguments")
                if "cancel_requested" in kwargs or "heartbeat" in kwargs:
                    raise ValueError("Worker callbacks are managed by the protocol")
                kwargs = {
                    **kwargs,
                    "cancel_requested": cancellation.is_set,
                    "heartbeat": emit_heartbeat,
                }
            return function(*args, **kwargs)
        if target == "codec":
            if method not in _CODEC_METHODS:
                raise PermissionError("Multisim codec method is not allowlisted")
            return getattr(self.codec, str(method))(*args, **kwargs)
        raise ValueError("Unknown worker target")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            close = getattr(self.client, "close", self.client.disconnect)
            close()


def _emit(stream: TextIO, lock: threading.Lock, payload: object) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with lock:
        stream.write(encoded + "\n")
        stream.flush()


def _error_payload(request_id: str, exc: BaseException) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "id": request_id,
        "ok": False,
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=12)[-8000:],
        },
    }


def _read_requests(
    stream: TextIO,
    requests: queue.Queue[dict[str, Any] | None],
    cancellations: dict[str, threading.Event],
    cancellation_lock: threading.Lock,
) -> None:
    for line in stream:
        if len(line.encode("utf-8", errors="replace")) > MAX_REQUEST_BYTES:
            requests.put(
                {
                    "id": "oversized",
                    "target": "invalid",
                    "method": "invalid",
                    "_reader_error": "Worker request exceeded the size limit",
                }
            )
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            requests.put(
                {
                    "id": "invalid-json",
                    "target": "invalid",
                    "method": "invalid",
                    "_reader_error": str(exc),
                }
            )
            continue
        if not isinstance(value, dict):
            continue
        if value.get("target") == "control" and value.get("method") == "cancel":
            request_id = str(value.get("id", ""))
            with cancellation_lock:
                event = cancellations.get(request_id)
            if event is not None:
                event.set()
            continue
        requests.put(value)
    requests.put(None)


def _watch_parent(parent_pid: int) -> None:
    while True:
        threading.Event().wait(0.5)
        if not _pid_alive(parent_pid):
            os._exit(70)


def run_worker(parent_pid: int = 0) -> int:
    """Serve requests until stdin closes, shutdown is requested, or parent exits."""
    protocol_output = sys.stdout
    emit_lock = threading.Lock()
    requests: queue.Queue[dict[str, Any] | None] = queue.Queue()
    cancellations: dict[str, threading.Event] = {}
    cancellation_lock = threading.Lock()
    service = _WorkerService()

    if parent_pid > 0:
        threading.Thread(
            target=_watch_parent,
            args=(parent_pid,),
            name="multisim-com-parent-watch",
            daemon=True,
        ).start()
    threading.Thread(
        target=_read_requests,
        args=(sys.stdin, requests, cancellations, cancellation_lock),
        name="multisim-com-protocol-reader",
        daemon=True,
    ).start()

    diagnostics = runtime_diagnostics()
    _emit(
        protocol_output,
        emit_lock,
        {
            "protocol_version": PROTOCOL_VERSION,
            "event": "ready",
            "pid": os.getpid(),
            "diagnostics": diagnostics,
        },
    )
    try:
        while True:
            request = requests.get()
            if request is None:
                return 0
            request_id = str(request.get("id", ""))
            if not request_id:
                continue
            if request.get("protocol_version") != PROTOCOL_VERSION:
                _emit(
                    protocol_output,
                    emit_lock,
                    _error_payload(
                        request_id,
                        ValueError("Worker request protocol version mismatch"),
                    ),
                )
                continue
            if request.get("target") == "control" and request.get("method") == "shutdown":
                _emit(
                    protocol_output,
                    emit_lock,
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "id": request_id,
                        "ok": True,
                        "result": {"shutdown": True},
                    },
                )
                return 0
            cancellation = threading.Event()
            with cancellation_lock:
                cancellations[request_id] = cancellation

            def heartbeat() -> None:
                _emit(
                    protocol_output,
                    emit_lock,
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "event": "heartbeat",
                        "id": request_id,
                    },
                )

            try:
                if request.get("_reader_error"):
                    raise ValueError(str(request["_reader_error"]))
                with contextlib.redirect_stdout(sys.stderr):
                    result = service.dispatch(request, cancellation, heartbeat)
                response = {
                    "protocol_version": PROTOCOL_VERSION,
                    "id": request_id,
                    "ok": True,
                    "result": result,
                }
            except BaseException as exc:
                response = _error_payload(request_id, exc)
            finally:
                with cancellation_lock:
                    cancellations.pop(request_id, None)
            _emit(protocol_output, emit_lock, response)
    finally:
        service.close()


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    parent_pid = 0
    if values:
        if len(values) != 2 or values[0] != "--parent-pid":
            return 2
        try:
            parent_pid = int(values[1])
        except ValueError:
            return 2
    return run_worker(parent_pid)


if __name__ == "__main__":
    raise SystemExit(main())
