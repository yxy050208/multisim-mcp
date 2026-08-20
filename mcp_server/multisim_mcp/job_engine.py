"""Persistent, process-isolated experiment jobs for Multisim MCP.

The public API deliberately uses ordinary MCP tools today.  The storage and
state-machine boundary is independent of the transport so it can be adapted to
the official MCP Tasks extension without changing persisted jobs.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterator

from typing_extensions import TypedDict


JOB_SCHEMA_VERSION: Final = 1
JOB_ID_PATTERN: Final = re.compile(r"^job-[0-9a-f]{32}$")
TERMINAL_STATES: Final = frozenset({"succeeded", "failed", "cancelled", "timed_out"})
ACTIVE_STATES: Final = frozenset({"queued", "running", "cancelling"})
VALID_STATES: Final = ACTIVE_STATES | TERMINAL_STATES
MCP_TASK_STATUS: Final[dict[str, str]] = {
    "queued": "working",
    "running": "working",
    "cancelling": "working",
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "timed_out": "failed",
}


def _valid_record_shape(record: dict[str, Any]) -> bool:
    return (
        record.get("schema_version") == JOB_SCHEMA_VERSION
        and bool(JOB_ID_PATTERN.fullmatch(str(record.get("job_id", ""))))
        and str(record.get("state", "")) in VALID_STATES
        and isinstance(record.get("spec"), dict)
        and isinstance(record.get("created_at"), str)
        and isinstance(record.get("output_dir"), str)
    )


class JobSubmission(TypedDict):
    success: bool
    job_id: str
    state: str
    status_uri: str
    output_dir: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        # A second manager may briefly have the destination open while it
        # refreshes the shared queue.  Windows then reports access denied or a
        # sharing violation even though the atomic replace is otherwise valid.
        # Retry only those transient errors and keep the delay bounded so real
        # permission failures are still surfaced promptly.
        delay = 0.005
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except OSError as exc:
                transient = isinstance(exc, PermissionError) or getattr(
                    exc, "winerror", None
                ) in {5, 32, 33}
                if not transient or attempt == 7:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.08)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) is not a read-only existence probe on Windows: Python
        # may route non-console signals through TerminateProcess. Query a
        # limited-information handle instead.
        try:
            import ctypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                ctypes.c_uint32,
                ctypes.c_int,
                ctypes.c_uint32,
            ]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            handle = kernel32.OpenProcess(
                process_query_limited_information, False, pid
            )
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except (AttributeError, OSError):
            return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _claim_file(path: Path, owner: str) -> bool:
    """Atomically claim a lease, removing it only when its owner is dead."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"pid": os.getpid(), "owner": owner, "created_at": _utc_now()},
        ensure_ascii=True,
    ).encode("utf-8")
    for _ in range(2):
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                existing = _read_json(path)
                live = _pid_alive(int(existing.get("pid", -1)))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                live = True
            if live:
                return False
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return True
    return False


def _live_lease(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        existing = _read_json(path)
        return _pid_alive(int(existing.get("pid", -1)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        # A malformed lease is never removed automatically; fail closed.
        return True


@contextmanager
def output_lease(output_dir: str, owner: str) -> Iterator[None]:
    """Hold a cross-process lease for one experiment output directory."""
    root = Path(output_dir).expanduser().resolve()
    lock_path = root.parent / f".{root.name}.multisim-mcp.lock"
    if not _claim_file(lock_path, owner):
        raise RuntimeError(f"Experiment output directory is already in use: {root}")
    try:
        yield
    finally:
        try:
            current = _read_json(lock_path)
            if current.get("owner") == owner and int(current.get("pid", -1)) == os.getpid():
                lock_path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass


def default_job_dir() -> Path:
    configured = os.environ.get("MULTISIM_MCP_JOB_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        return (base / "multisim-mcp" / "jobs").resolve()
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    return (base / "multisim-mcp" / "jobs").resolve()


class ExperimentJobManager:
    """A single-worker persistent queue with crash and hang containment."""

    def __init__(
        self,
        state_dir: Path | None = None,
        *,
        start: bool = True,
        worker_command: list[str] | None = None,
    ) -> None:
        self.state_dir = (state_dir or default_job_dir()).resolve()
        self.records_dir = self.state_dir / "records"
        self.runtime_dir = self.state_dir / "runtime"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        for directory in (self.state_dir, self.records_dir, self.runtime_dir):
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._records: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        # Embedded/portable Windows Python builds may ignore PYTHONPATH via a
        # ``._pth`` file. Bootstrap the isolated worker with the exact import
        # roots of the already-running MCP frontend so it uses the same SDK,
        # pywin32, and project version instead of an older global installation.
        worker_paths = list(dict.fromkeys(item for item in sys.path if item))
        worker_bootstrap = (
            "import runpy,sys;"
            f"sys.path[:0]={worker_paths!r};"
            "runpy.run_module('multisim_mcp.job_worker',run_name='__main__')"
        )
        self._worker_command = worker_command or [
            sys.executable,
            "-c",
            worker_bootstrap,
        ]
        self._load_records()
        self._thread: threading.Thread | None = None
        if start:
            self._thread = threading.Thread(
                target=self._scheduler,
                name="multisim-job-scheduler",
                daemon=True,
            )
            self._thread.start()

    def _record_path(self, job_id: str) -> Path:
        if not JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError("Invalid experiment job handle")
        return self.records_dir / f"{job_id}.json"

    def _load_records(self) -> None:
        for path in sorted(self.records_dir.glob("job-*.json")):
            try:
                record = _read_json(path)
                job_id = str(record.get("job_id", ""))
                if not _valid_record_shape(record):
                    continue
                state = str(record.get("state", ""))
                lease = self.runtime_dir / f"{job_id}.lease"
                if state in {"running", "cancelling"} and not _live_lease(lease):
                    record.update(
                        state="queued",
                        stage="recovered",
                        message="Recovered after the previous MCP worker stopped",
                        updated_at=_utc_now(),
                        recovery_count=int(record.get("recovery_count", 0)) + 1,
                    )
                    record["progress"] = 0
                    self._persist(record)
                self._records[job_id] = record
                if state == "succeeded" and Path(str(record.get("output_dir", ""))).is_dir():
                    try:
                        if record.get("spec", {}).get("job_kind") == "sweep":
                            from multisim_mcp.sweep_resources import register_sweep

                            registered = register_sweep(str(record["output_dir"]))
                            id_key = "sweep_id"
                        else:
                            from multisim_mcp.experiment_resources import register_experiment

                            registered = register_experiment(str(record["output_dir"]))
                            id_key = "experiment_id"
                        if isinstance(record.get("result"), dict):
                            record["result"][id_key] = registered[id_key]
                            record["result"]["resources"] = registered["resources"]
                    except (OSError, ValueError):
                        pass
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                # A corrupt record is isolated; other durable jobs remain usable.
                continue

    def _refresh_records(self) -> None:
        """Observe records written by another MCP process sharing this queue."""
        for path in sorted(self.records_dir.glob("job-*.json")):
            try:
                record = _read_json(path)
                job_id = str(record.get("job_id", ""))
                state = str(record.get("state", ""))
                if not _valid_record_shape(record):
                    continue
                lease = self.runtime_dir / f"{job_id}.lease"
                if state in {"running", "cancelling"} and not _live_lease(lease):
                    record.update(
                        state="queued",
                        stage="recovered",
                        progress=0,
                        message="Recovered after an experiment worker stopped",
                        updated_at=_utc_now(),
                        recovery_count=int(record.get("recovery_count", 0)) + 1,
                    )
                    self._persist(record)
                self._records[job_id] = record
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue

    def _persist(self, record: dict[str, Any]) -> None:
        _atomic_json(self._record_path(str(record["job_id"])), record)

    @staticmethod
    def _public(record: dict[str, Any], *, include_result: bool = True) -> dict[str, Any]:
        hidden = {"spec"}
        if not include_result:
            hidden.add("result")
        public = {key: value for key, value in record.items() if key not in hidden}
        public["mcp_task_status"] = MCP_TASK_STATUS[str(record["state"])]
        return deepcopy(public)

    def submit(self, spec: dict[str, Any]) -> JobSubmission:
        output_dir = str(Path(str(spec["output_dir"])).expanduser().resolve())
        now = _utc_now()
        with self._lock:
            self._refresh_records()
            conflict = next(
                (
                    item
                    for item in self._records.values()
                    if item.get("state") in ACTIVE_STATES
                    and os.path.normcase(str(item.get("output_dir")))
                    == os.path.normcase(output_dir)
                ),
                None,
            )
            if conflict:
                raise RuntimeError(
                    "An active experiment job already owns this output directory: "
                    f"{conflict['job_id']}"
                )
            job_id = f"job-{uuid.uuid4().hex}"
            normalized_spec = {**spec, "output_dir": output_dir}
            record: dict[str, Any] = {
                "schema_version": JOB_SCHEMA_VERSION,
                "job_id": job_id,
                "state": "queued",
                "stage": "queued",
                "progress": 0,
                "message": "Waiting for the Multisim worker",
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "finished_at": None,
                "attempt": 0,
                "recovery_count": 0,
                "output_dir": output_dir,
                "status_uri": f"multisim://jobs/{job_id}",
                "failure": None,
                "result": None,
                "spec": normalized_spec,
            }
            self._persist(record)
            self._records[job_id] = record
            self._wake.set()
        return {
            "success": True,
            "job_id": job_id,
            "state": "queued",
            "status_uri": record["status_uri"],
            "output_dir": output_dir,
        }

    def get(self, job_id: str) -> dict[str, Any]:
        if not JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError("Invalid experiment job handle")
        with self._lock:
            self._refresh_records()
            record = self._records.get(job_id)
            if record is None:
                raise KeyError("Unknown experiment job handle")
            return self._public(record)

    def list(self, state: str = "", limit: int = 50) -> dict[str, Any]:
        if state and state not in VALID_STATES:
            raise ValueError("state must be a valid experiment job state")
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        with self._lock:
            self._refresh_records()
            records = sorted(
                self._records.values(), key=lambda item: str(item["created_at"]), reverse=True
            )
            if state:
                records = [item for item in records if item["state"] == state]
            selected = records[:limit]
            return {
                "jobs": [self._public(item, include_result=False) for item in selected],
                "count": len(selected),
                "total": len(records),
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        if not JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError("Invalid experiment job handle")
        with self._lock:
            self._refresh_records()
            record = self._records.get(job_id)
            if record is None:
                raise KeyError("Unknown experiment job handle")
            state = str(record["state"])
            if state in TERMINAL_STATES:
                return self._public(record)
            if state == "queued":
                record.update(
                    state="cancelled",
                    stage="cancelled",
                    progress=record.get("progress", 0),
                    message="Cancelled before execution",
                    updated_at=_utc_now(),
                    finished_at=_utc_now(),
                )
            else:
                record.update(
                    state="cancelling",
                    message="Cancellation requested; stopping the isolated worker",
                    updated_at=_utc_now(),
                )
                (self.runtime_dir / f"{job_id}.cancel").touch()
            self._persist(record)
            self._wake.set()
            return self._public(record)

    def retry(self, job_id: str) -> JobSubmission:
        """Create a new queued attempt from a failed/cancelled durable spec."""
        if not JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError("Invalid experiment job handle")
        with self._lock:
            self._refresh_records()
            record = self._records.get(job_id)
            if record is None:
                raise KeyError("Unknown experiment job handle")
            if record["state"] not in {"failed", "cancelled", "timed_out"}:
                raise RuntimeError("Only failed, cancelled, or timed-out jobs can be retried")
            spec = deepcopy(record["spec"])
        submitted = self.submit(spec)
        with self._lock:
            retry_record = self._records[submitted["job_id"]]
            retry_record["retry_of"] = job_id
            self._persist(retry_record)
        return submitted

    def _next_job(self) -> str | None:
        with self._lock:
            self._refresh_records()
            queued = [item for item in self._records.values() if item["state"] == "queued"]
            if not queued:
                return None
            queued.sort(key=lambda item: str(item["created_at"]))
            return str(queued[0]["job_id"])

    def _scheduler(self) -> None:
        while not self._stop.is_set():
            job_id = self._next_job()
            if job_id is None:
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            worker_lease = self.runtime_dir / "worker.lease"
            if not _claim_file(worker_lease, "global-worker"):
                time.sleep(0.2)
                continue
            # Another MCP process may have completed or claimed the candidate
            # while this scheduler was waiting for the one-worker lease.
            job_id = self._next_job()
            if job_id is None:
                worker_lease.unlink(missing_ok=True)
                continue
            lease = self.runtime_dir / f"{job_id}.lease"
            if not _claim_file(lease, job_id):
                worker_lease.unlink(missing_ok=True)
                time.sleep(0.2)
                continue
            try:
                try:
                    self._run_one(job_id)
                except Exception as exc:
                    with self._lock:
                        state = self._records.get(job_id, {}).get("state")
                    if state in ACTIVE_STATES:
                        self._finish_failure(
                            job_id,
                            "failed",
                            "SCHEDULER_ERROR",
                            f"Experiment scheduler failed safely: {exc}",
                        )
            finally:
                try:
                    lease.unlink(missing_ok=True)
                except OSError:
                    pass
                try:
                    worker_lease.unlink(missing_ok=True)
                except OSError:
                    pass

    def _update_progress(self, job_id: str, progress_path: Path) -> float:
        try:
            update = _read_json(progress_path)
            heartbeat = progress_path.stat().st_mtime
        except (OSError, ValueError, json.JSONDecodeError):
            return time.time()
        with self._lock:
            record = self._records[job_id]
            if record["state"] == "running":
                record.update(
                    stage=str(update.get("stage", record["stage"])),
                    progress=max(0, min(100, int(update.get("progress", record["progress"])))),
                    message=str(update.get("message", record["message"])),
                    updated_at=_utc_now(),
                )
                self._persist(record)
        return heartbeat

    def _terminate(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def _finish_failure(
        self,
        job_id: str,
        state: str,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            record = self._records[job_id]
            record.update(
                state=state,
                stage=state,
                message=message,
                updated_at=_utc_now(),
                finished_at=_utc_now(),
                failure={
                    "code": code,
                    "message": message,
                    "recoverable": code
                    in {"WORKER_CRASH", "WORKER_UNRESPONSIVE", "JOB_TIMEOUT"},
                    "last_checkpoint": record.get("stage"),
                    **({"details": details} if details else {}),
                },
            )
            self._persist(record)

    def _run_one(self, job_id: str) -> None:
        request_path = self.runtime_dir / f"{job_id}.request.json"
        progress_path = self.runtime_dir / f"{job_id}.progress.json"
        result_path = self.runtime_dir / f"{job_id}.result.json"
        cancel_path = self.runtime_dir / f"{job_id}.cancel"
        for path in (progress_path, result_path, cancel_path):
            path.unlink(missing_ok=True)
        with self._lock:
            self._refresh_records()
            record = self._records[job_id]
            if record["state"] != "queued":
                return
            now = _utc_now()
            record.update(
                state="running",
                stage="starting_worker",
                progress=1,
                message="Starting isolated Multisim worker",
                updated_at=now,
                started_at=now,
                finished_at=None,
                attempt=int(record.get("attempt", 0)) + 1,
                failure=None,
                result=None,
            )
            self._persist(record)
            spec = dict(record["spec"])
        _atomic_json(
            request_path,
            {
                "schema_version": JOB_SCHEMA_VERSION,
                "job_id": job_id,
                "spec": spec,
                "progress_path": str(progress_path),
                "result_path": str(result_path),
                "cancel_path": str(cancel_path),
                "parent_pid": os.getpid(),
            },
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                [*self._worker_command, str(request_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                creationflags=creationflags,
            )
        except OSError as exc:
            self._finish_failure(
                job_id,
                "failed",
                "WORKER_START_FAILED",
                f"Could not start isolated Multisim worker: {exc}",
            )
            request_path.unlink(missing_ok=True)
            return
        with self._lock:
            self._processes[job_id] = process
        started = time.monotonic()
        last_heartbeat = started
        last_progress_mtime = 0.0
        timeout = float(spec.get("job_timeout", 600.0))
        heartbeat_timeout = float(spec.get("heartbeat_timeout", 180.0))
        terminal_failure: tuple[str, str, str] | None = None
        while process.poll() is None:
            time.sleep(0.2)
            if progress_path.exists():
                try:
                    progress_mtime = progress_path.stat().st_mtime
                except OSError:
                    progress_mtime = 0.0
                if progress_mtime > last_progress_mtime:
                    heartbeat_wall = self._update_progress(job_id, progress_path)
                    age = max(0.0, time.time() - heartbeat_wall)
                    last_heartbeat = time.monotonic() - age
                    last_progress_mtime = progress_mtime
            with self._lock:
                current_state = self._records[job_id]["state"]
                if cancel_path.exists() and current_state == "running":
                    self._records[job_id].update(
                        state="cancelling",
                        message="Cancellation requested; stopping the isolated worker",
                        updated_at=_utc_now(),
                    )
                    self._persist(self._records[job_id])
                    current_state = "cancelling"
            if current_state == "cancelling":
                if time.monotonic() - last_heartbeat > 2.0:
                    terminal_failure = (
                        "cancelled",
                        "CANCELLED",
                        "Experiment cancelled and isolated worker stopped",
                    )
                    self._terminate(process)
                    break
            elif time.monotonic() - started > timeout:
                terminal_failure = (
                    "timed_out",
                    "JOB_TIMEOUT",
                    f"Experiment exceeded its {timeout:g} second job timeout",
                )
                cancel_path.touch()
                self._terminate(process)
                break
            elif time.monotonic() - last_heartbeat > heartbeat_timeout:
                terminal_failure = (
                    "failed",
                    "WORKER_UNRESPONSIVE",
                    f"Multisim worker produced no heartbeat for {heartbeat_timeout:g} seconds",
                )
                cancel_path.touch()
                self._terminate(process)
                break
        with self._lock:
            self._processes.pop(job_id, None)
        if terminal_failure:
            self._finish_failure(job_id, *terminal_failure)
        elif result_path.is_file():
            try:
                worker_result = _read_json(result_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._finish_failure(job_id, "failed", "INVALID_WORKER_RESULT", str(exc))
            else:
                if worker_result.get("success"):
                    result = worker_result.get("result")
                    if isinstance(result, dict):
                        try:
                            if spec.get("job_kind") == "sweep":
                                from multisim_mcp.sweep_resources import register_sweep

                                registered = register_sweep(str(result["output_dir"]))
                                id_key = "sweep_id"
                            else:
                                from multisim_mcp.experiment_resources import register_experiment

                                registered = register_experiment(str(result["output_dir"]))
                                id_key = "experiment_id"
                            result[id_key] = registered[id_key]
                            result["resources"] = registered["resources"]
                        except (KeyError, OSError, ValueError) as exc:
                            self._finish_failure(
                                job_id,
                                "failed",
                                "RESOURCE_REGISTRATION_FAILED",
                                str(exc),
                            )
                            result = None
                    if result is None:
                        return
                    with self._lock:
                        record = self._records[job_id]
                        record.update(
                            state="succeeded",
                            stage="complete",
                            progress=100,
                            message=(
                                "Sweep completed"
                                if spec.get("job_kind") == "sweep"
                                else "Experiment completed"
                            ),
                            updated_at=_utc_now(),
                            finished_at=_utc_now(),
                            result=result,
                            failure=None,
                        )
                        self._persist(record)
                else:
                    failure = worker_result.get("failure") or {}
                    state = "cancelled" if failure.get("code") == "CANCELLED" else "failed"
                    self._finish_failure(
                        job_id,
                        state,
                        str(failure.get("code", "EXPERIMENT_FAILED")),
                        str(failure.get("message", "Experiment worker failed")),
                        {
                            key: failure[key]
                            for key in ("type", "traceback")
                            if key in failure
                        },
                    )
        else:
            self._finish_failure(
                job_id,
                "failed",
                "WORKER_CRASH",
                f"Isolated Multisim worker exited with code {process.returncode}",
            )
        for path in (request_path, progress_path, result_path, cancel_path):
            path.unlink(missing_ok=True)

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            processes = list(self._processes.values())
        for process in processes:
            self._terminate(process)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
