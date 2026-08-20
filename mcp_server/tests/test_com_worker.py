"""COM-free coverage for the isolated Multisim worker protocol."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from typing import Any

from multisim_mcp.com_worker import _WorkerService
from multisim_mcp.com_worker_client import (
    MultisimWorkerProcess,
    WorkerMs14Codec,
    WorkerMultisimClient,
)
from multisim_mcp.worker_protocol import PROTOCOL_VERSION


class FakeClient:
    def connect(self) -> dict[str, object]:
        return {"connected": True, "version": "test"}

    def run_command_file(
        self,
        command_file: str,
        log_file: str,
        timeout: float,
        *,
        cancel_requested: Any,
        heartbeat: Any,
    ) -> dict[str, object]:
        heartbeat()
        return {
            "command_file": command_file,
            "log_file": log_file,
            "timeout": timeout,
            "cancelled": bool(cancel_requested()),
        }

    def disconnect(self) -> dict[str, bool]:
        return {"connected": False}


class FakeCodec:
    def encode(self, source: str, output: str) -> dict[str, str]:
        return {"source": source, "output": output}


class WorkerServiceTest(unittest.TestCase):
    def test_dispatches_only_allowlisted_methods(self) -> None:
        service = _WorkerService(FakeClient(), FakeCodec())
        connected = service.dispatch(
            {"target": "client", "method": "connect", "args": [], "kwargs": {}},
            threading.Event(),
            lambda: None,
        )
        self.assertEqual(connected, {"connected": True, "version": "test"})
        with self.assertRaisesRegex(PermissionError, "not allowlisted"):
            service.dispatch(
                {
                    "target": "client",
                    "method": "_ensure_app",
                    "args": [],
                    "kwargs": {},
                },
                threading.Event(),
                lambda: None,
            )

    def test_run_command_file_bridges_heartbeat_and_cancellation(self) -> None:
        service = _WorkerService(FakeClient(), FakeCodec())
        cancellation = threading.Event()
        cancellation.set()
        heartbeats: list[bool] = []
        result = service.dispatch(
            {
                "target": "client",
                "method": "run_command_file",
                "args": ["run.txt", "run.log", 4.0],
                "kwargs": {},
            },
            cancellation,
            lambda: heartbeats.append(True),
        )
        self.assertEqual(heartbeats, [True])
        self.assertTrue(result["cancelled"])

    def test_rejects_caller_supplied_worker_callbacks(self) -> None:
        service = _WorkerService(FakeClient(), FakeCodec())
        with self.assertRaisesRegex(ValueError, "managed by the protocol"):
            service.dispatch(
                {
                    "target": "client",
                    "method": "run_command_file",
                    "args": ["run.txt", "run.log", 4.0],
                    "kwargs": {"heartbeat": "untrusted"},
                },
                threading.Event(),
                lambda: None,
            )


class RecordingWorker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[object, ...], dict[str, object]]] = []

    def call(
        self, target: str, method: str, *args: object, **kwargs: object
    ) -> object:
        self.calls.append((target, method, args, kwargs))
        if target == "codec":
            return {"operation": method}
        if method == "circuit_info":
            return {"name": "test"}
        if method == "run_command_file":
            return {"state": 0, "cancelled": False}
        return {"method": method}


class ProxyTest(unittest.TestCase):
    def test_proxy_preserves_timeout_keyword_for_remote_method(self) -> None:
        worker = RecordingWorker()
        client = WorkerMultisimClient(worker)  # type: ignore[arg-type]
        result = client.run_transient("V(out)", timeout=7.5)
        self.assertEqual(result, {"method": "run_transient"})
        self.assertEqual(worker.calls[0][3]["timeout"], 7.5)

    def test_circuit_property_checks_remote_state(self) -> None:
        worker = RecordingWorker()
        client = WorkerMultisimClient(worker)  # type: ignore[arg-type]
        self.assertIs(client.circuit, client)
        self.assertEqual(worker.calls[0][1], "circuit_info")

    def test_codec_proxy_uses_codec_target(self) -> None:
        worker = RecordingWorker()
        codec = WorkerMs14Codec(worker)  # type: ignore[arg-type]
        self.assertEqual(codec.encode("a.xml", "a.ms14"), {"operation": "encode"})
        self.assertEqual(worker.calls[0][0:2], ("codec", "encode"))


class WorkerProcessTest(unittest.TestCase):
    @staticmethod
    def _worker() -> MultisimWorkerProcess:
        root = Path(__file__).resolve().parents[1]
        bootstrap = (
            "import runpy,sys;"
            f"sys.path.insert(0,{str(root)!r});"
            "runpy.run_module('multisim_mcp.com_worker',run_name='__main__')"
        )
        return MultisimWorkerProcess(
            [sys.executable, "-c", bootstrap],
            require_32bit=False,
        )

    def test_real_protocol_starts_lazily_and_reuses_one_process(self) -> None:
        worker = self._worker()
        try:
            first = worker.call("system", "ping")
            second = worker.call("system", "ping")
            self.assertEqual(first["protocol_version"], PROTOCOL_VERSION)
            self.assertEqual(first["pid"], second["pid"])
            self.assertNotEqual(first["pid"], 0)
        finally:
            worker.close()

    def test_remote_allowlist_error_is_preserved(self) -> None:
        worker = self._worker()
        try:
            with self.assertRaisesRegex(PermissionError, "not allowlisted"):
                worker.call("client", "_ensure_app")
        finally:
            worker.close()

    def test_worker_rejects_a_mismatched_request_version(self) -> None:
        worker = self._worker()
        try:
            worker._start_locked()
            assert worker._process is not None and worker._process.stdin is not None
            worker._process.stdin.write(
                '{"protocol_version":99,"id":"bad-version",'
                '"target":"system","method":"ping","args":[],"kwargs":{}}\n'
            )
            worker._process.stdin.flush()
            response = worker._messages.get(timeout=3)
            self.assertEqual(response["id"], "bad-version")
            self.assertFalse(response["ok"])
            self.assertIn("version mismatch", response["error"]["message"])
        finally:
            worker.close()

    def test_crashed_worker_is_restarted_for_the_next_call(self) -> None:
        worker = self._worker()
        try:
            first = worker.call("system", "ping")
            assert worker._process is not None
            worker._process.kill()
            worker._process.wait(timeout=3)
            second = worker.call("system", "ping")
            self.assertNotEqual(first["pid"], second["pid"])
        finally:
            worker.close()

    def test_concurrent_callers_share_one_serialized_worker(self) -> None:
        worker = self._worker()
        results: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def ping() -> None:
            try:
                results.append(worker.call("system", "ping"))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=ping) for _ in range(6)]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 6)
            self.assertEqual(len({item["pid"] for item in results}), 1)
        finally:
            worker.close()

    def test_rpc_timeout_terminates_an_unresponsive_worker(self) -> None:
        script = r'''
import json,struct,sys,time
print(json.dumps({
    "protocol_version": 1,
    "event": "ready",
    "pid": 1,
    "diagnostics": {"python_bits": struct.calcsize("P") * 8},
}), flush=True)
sys.stdin.readline()
time.sleep(60)
'''
        worker = MultisimWorkerProcess(
            [sys.executable, "-u", "-c", script],
            require_32bit=False,
        )
        try:
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                worker.call("system", "ping", rpc_timeout=0.2)
            self.assertIsNone(worker._process)
        finally:
            worker.close()

    def test_cancellation_is_forwarded_after_a_worker_heartbeat(self) -> None:
        script = r'''
import json,struct,sys
print(json.dumps({
    "protocol_version": 1,
    "event": "ready",
    "pid": 2,
    "diagnostics": {"python_bits": struct.calcsize("P") * 8},
}), flush=True)
request = json.loads(sys.stdin.readline())
print(json.dumps({
    "protocol_version": 1,
    "event": "heartbeat",
    "id": request["id"],
}), flush=True)
control = json.loads(sys.stdin.readline())
print(json.dumps({
    "protocol_version": 1,
    "id": request["id"],
    "ok": True,
    "result": {"cancelled": control["method"] == "cancel"},
}), flush=True)
'''
        worker = MultisimWorkerProcess(
            [sys.executable, "-u", "-c", script],
            require_32bit=False,
        )
        try:
            result = worker.call(
                "client",
                "run_command_file",
                cancel_requested=lambda: True,
            )
            self.assertEqual(result, {"cancelled": True})
        finally:
            worker.close()


if __name__ == "__main__":
    unittest.main()
