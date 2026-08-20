"""COM-free tests for durable and process-isolated experiment jobs."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp import job_engine
from multisim_mcp.job_engine import ExperimentJobManager, output_lease


SUCCESS_WORKER = r"""
import json, sys
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
root = Path(request['spec']['output_dir'])
root.mkdir(parents=True, exist_ok=True)
for name in ('circuit.ms14', 'circuit.ms14.xml', 'schematic.png', 'data.csv',
             'result.raw', 'run.log', 'run.txt', 'circuit.cir', 'plot.svg', 'report.md'):
    (root / name).write_bytes(b'fixture')
Path(request['progress_path']).write_text(
    json.dumps({'stage': 'complete', 'progress': 100, 'message': 'done'}),
    encoding='utf-8',
)
result = {
    'success': True,
    'experiment_id': 'worker-placeholder',
    'resources': {},
    'schematic': {'success': True},
    'simulation': {'success': True},
    'report': str(root / 'report.md'),
    'plot': str(root / 'plot.svg'),
    'output_dir': str(root),
}
Path(request['result_path']).write_text(
    json.dumps({'success': True, 'result': result}), encoding='utf-8'
)
"""

SERIAL_WORKER = r"""
import json, os, sys, time
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
marker = Path(request['spec']['serial_marker'])
violation = marker.with_suffix('.violation')
try:
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    os.close(descriptor)
except FileExistsError:
    violation.write_text('workers overlapped', encoding='utf-8')
time.sleep(0.4)
marker.unlink(missing_ok=True)
""" + SUCCESS_WORKER

SUCCESS_SWEEP_WORKER = r"""
import json, sys
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
root = Path(request['spec']['output_dir'])
root.mkdir(parents=True, exist_ok=True)
(root / 'summary.json').write_text(
    json.dumps({'schema_version': 1, 'result_type': 'sweep', 'run_count': 2}),
    encoding='utf-8',
)
(root / 'data.csv').write_text('run_id,status\nrun-0001,measured\n', encoding='utf-8')
result = {
    'success': True,
    'result_type': 'sweep',
    'sweep_id': 'worker-placeholder',
    'resources': {},
    'summary': str(root / 'summary.json'),
    'data': str(root / 'data.csv'),
    'output_dir': str(root),
    'run_count': 2,
}
Path(request['result_path']).write_text(
    json.dumps({'success': True, 'result': result}), encoding='utf-8'
)
"""


def _spec(output_dir: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "netlist": "V1 a 0 1\nR1 a 0 1k\n.end\n",
        "commands": "op",
        "output_dir": output_dir,
        "title": "job test",
        "timeout": 1.0,
        "max_points": 10,
        "overwrite": False,
        "job_timeout": 5.0,
        "heartbeat_timeout": 2.0,
    }
    value.update(overrides)
    return value


def _wait_terminal(manager: ExperimentJobManager, job_id: str, seconds: float = 8) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        record = manager.get(job_id)
        if record["state"] in {"succeeded", "failed", "cancelled", "timed_out"}:
            return record
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {manager.get(job_id)}")


class DurableJobStateTest(unittest.TestCase):
    def test_atomic_json_retries_transient_replace_denial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "record.json"
            real_replace = os.replace
            attempts = 0

            def transient_replace(source: str | bytes, destination: str | bytes) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError(13, "simulated sharing violation", destination)
                real_replace(source, destination)

            with patch.object(job_engine.os, "replace", side_effect=transient_replace):
                job_engine._atomic_json(target, {"state": "queued"})

            self.assertEqual(attempts, 2)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"state": "queued"},
            )

    def test_default_worker_inherits_frontend_import_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = ExperimentJobManager(Path(tmp) / "state", start=False)
            try:
                self.assertEqual(manager._worker_command[:2], [sys.executable, "-c"])
                bootstrap = manager._worker_command[2]
                self.assertIn("sys.path[:0]", bootstrap)
                self.assertIn("multisim_mcp.job_worker", bootstrap)
            finally:
                manager.shutdown()

    def test_submit_hides_spec_rejects_duplicate_and_cancels_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ExperimentJobManager(root / "state", start=False)
            try:
                submitted = manager.submit(_spec(str(root / "out")))
                record = manager.get(submitted["job_id"])
                self.assertEqual(record["state"], "queued")
                self.assertNotIn("spec", record)
                self.assertNotIn("V1 a 0", json.dumps(record))
                with self.assertRaisesRegex(RuntimeError, "already owns"):
                    manager.submit(_spec(str(root / "out")))
                cancelled = manager.cancel(submitted["job_id"])
                self.assertEqual(cancelled["state"], "cancelled")
                self.assertIsNotNone(cancelled["finished_at"])
                retried = manager.retry(submitted["job_id"])
                retry_record = manager.get(retried["job_id"])
                self.assertEqual(retry_record["retry_of"], submitted["job_id"])
                self.assertEqual(retry_record["mcp_task_status"], "working")
            finally:
                manager.shutdown()

    def test_interrupted_running_job_is_requeued_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            first = ExperimentJobManager(state, start=False)
            submitted = first.submit(_spec(str(Path(tmp) / "out")))
            record_path = state / "records" / f"{submitted['job_id']}.json"
            stored = json.loads(record_path.read_text(encoding="utf-8"))
            stored["state"] = "running"
            stored["stage"] = "simulation"
            record_path.write_text(json.dumps(stored), encoding="utf-8")
            first.shutdown()

            second = ExperimentJobManager(state, start=False)
            try:
                recovered = second.get(submitted["job_id"])
                self.assertEqual(recovered["state"], "queued")
                self.assertEqual(recovered["stage"], "recovered")
                self.assertEqual(recovered["recovery_count"], 1)
            finally:
                second.shutdown()

    def test_output_lease_is_exclusive_and_released(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / "same-name")
            with output_lease(output, "first"):
                with self.assertRaisesRegex(RuntimeError, "already in use"):
                    with output_lease(output, "second"):
                        pass
            with output_lease(output, "third"):
                self.assertTrue(True)


class WorkerRecoveryTest(unittest.TestCase):
    def test_sweep_worker_registers_and_restores_sweep_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            manager = ExperimentJobManager(
                state, worker_command=[sys.executable, "-c", SUCCESS_SWEEP_WORKER]
            )
            try:
                submitted = manager.submit(
                    _spec(
                        str(Path(tmp) / "sweep"),
                        job_kind="sweep",
                        sweep_spec={"schema_version": 1},
                    )
                )
                complete = _wait_terminal(manager, submitted["job_id"])
                self.assertEqual(complete["state"], "succeeded")
                self.assertRegex(complete["result"]["sweep_id"], r"^sweep-[0-9a-f]{24}$")
                self.assertIn("summary", complete["result"]["resources"])
            finally:
                manager.shutdown()
            restored = ExperimentJobManager(state, start=False)
            try:
                self.assertEqual(
                    restored.get(submitted["job_id"])["result"]["sweep_id"],
                    complete["result"]["sweep_id"],
                )
            finally:
                restored.shutdown()

    def test_two_managers_share_one_global_worker_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            marker = Path(tmp) / "worker.marker"
            command = [sys.executable, "-c", SERIAL_WORKER]
            first_manager = ExperimentJobManager(state, worker_command=command)
            second_manager = ExperimentJobManager(state, worker_command=command)
            try:
                first = first_manager.submit(
                    _spec(str(Path(tmp) / "one"), serial_marker=str(marker))
                )
                second = second_manager.submit(
                    _spec(str(Path(tmp) / "two"), serial_marker=str(marker))
                )
                first_result = _wait_terminal(first_manager, first["job_id"])
                second_result = _wait_terminal(second_manager, second["job_id"])
                self.assertEqual(first_result["state"], "succeeded", first_result)
                self.assertEqual(second_result["state"], "succeeded", second_result)
                self.assertFalse(marker.with_suffix(".violation").exists())
            finally:
                first_manager.shutdown()
                second_manager.shutdown()

    def test_successful_worker_registers_resources_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            manager = ExperimentJobManager(
                state, worker_command=[sys.executable, "-c", SUCCESS_WORKER]
            )
            try:
                submitted = manager.submit(_spec(str(Path(tmp) / "out")))
                complete = _wait_terminal(manager, submitted["job_id"])
                self.assertEqual(complete["state"], "succeeded")
                self.assertRegex(
                    complete["result"]["experiment_id"], r"^exp-[0-9a-f]{24}$"
                )
                self.assertIn("report", complete["result"]["resources"])
                self.assertEqual(complete["mcp_task_status"], "completed")
            finally:
                manager.shutdown()

            restored = ExperimentJobManager(state, start=False)
            try:
                after_restart = restored.get(submitted["job_id"])
                self.assertEqual(after_restart["state"], "succeeded")
                self.assertEqual(
                    after_restart["result"]["experiment_id"],
                    complete["result"]["experiment_id"],
                )
            finally:
                restored.shutdown()

    def test_worker_crash_is_structured_and_next_job_can_run(self) -> None:
        crash_command = [sys.executable, "-c", "raise RuntimeError('worker boom')"]
        with tempfile.TemporaryDirectory() as tmp:
            manager = ExperimentJobManager(
                Path(tmp) / "state", worker_command=crash_command
            )
            try:
                first = manager.submit(_spec(str(Path(tmp) / "one")))
                failed = _wait_terminal(manager, first["job_id"])
                self.assertEqual(failed["state"], "failed")
                self.assertEqual(failed["failure"]["code"], "WORKER_CRASH")
                self.assertTrue(failed["failure"]["recoverable"])

                second = manager.submit(_spec(str(Path(tmp) / "two")))
                failed_again = _wait_terminal(manager, second["job_id"])
                self.assertEqual(failed_again["failure"]["code"], "WORKER_CRASH")
                self.assertEqual(failed_again["attempt"], 1)
            finally:
                manager.shutdown()

    def test_unresponsive_worker_is_terminated(self) -> None:
        sleep_command = [sys.executable, "-c", "import time; time.sleep(30)"]
        with tempfile.TemporaryDirectory() as tmp:
            manager = ExperimentJobManager(
                Path(tmp) / "state", worker_command=sleep_command
            )
            try:
                submitted = manager.submit(
                    _spec(
                        str(Path(tmp) / "out"),
                        heartbeat_timeout=0.3,
                        job_timeout=3.0,
                    )
                )
                failed = _wait_terminal(manager, submitted["job_id"], seconds=5)
                self.assertEqual(failed["state"], "failed")
                self.assertEqual(failed["failure"]["code"], "WORKER_UNRESPONSIVE")
            finally:
                manager.shutdown()

    def test_worker_start_failure_does_not_kill_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "missing-worker.exe")
            manager = ExperimentJobManager(
                Path(tmp) / "state", worker_command=[missing]
            )
            try:
                submitted = manager.submit(_spec(str(Path(tmp) / "out")))
                failed = _wait_terminal(manager, submitted["job_id"])
                self.assertEqual(failed["failure"]["code"], "WORKER_START_FAILED")
                self.assertEqual(failed["state"], "failed")
            finally:
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
