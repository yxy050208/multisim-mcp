"""COM-free dispatch tests for the isolated durable job worker."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp import autonomous_correction, job_worker, model_provider, server
from multisim_mcp.eda_core import CircuitDesign


DESIGN = {
    "schema_version": 1,
    "design_id": "worker-optimization",
    "title": "Worker optimization",
    "revision": 0,
    "components": [
        {
            "refdes": "R1",
            "kind": "R",
            "nodes": ["in", "0"],
            "value": "1k",
            "model": None,
            "parameters": {},
        }
    ],
    "parameters": {},
    "annotations": {},
}


class _OptimizationService:
    def __init__(
        self,
        *,
        status: str = "optimized",
        result_id: str = "optimization-worker-test",
    ) -> None:
        self.call: tuple[tuple[object, ...], dict[str, object]] | None = None
        self.status = status
        self.result_id = result_id

    def run(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.call = (args, kwargs)
        checkpoint = kwargs["checkpoint"]
        assert callable(checkpoint)
        checkpoint("optimization_experiment", 50, "running candidate")
        return {
            "schema_version": 1,
            "success": True,
            "status": self.status,
            "optimization_id": self.result_id,
            "output_dir": str(args[2]),
        }


class DurableJobWorkerTest(unittest.TestCase):
    def _run_request(
        self,
        root: Path,
        spec: dict[str, object],
    ) -> tuple[int, dict[str, object], dict[str, object]]:
        request_path = root / "request.json"
        progress_path = root / "progress.json"
        result_path = root / "result.json"
        request_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "job_id": "job-" + "b" * 32,
                    "parent_pid": 0,
                    "progress_path": str(progress_path),
                    "result_path": str(result_path),
                    "cancel_path": str(root / "cancel"),
                    "spec": spec,
                }
            ),
            encoding="utf-8",
        )
        with patch.object(sys, "argv", ["job_worker", str(request_path)]):
            exit_code = job_worker.main()
        return (
            exit_code,
            json.loads(result_path.read_text(encoding="utf-8")),
            json.loads(progress_path.read_text(encoding="utf-8")),
        )

    def test_optimization_dispatch_enables_checkpoint_resume(self) -> None:
        service = _OptimizationService()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_path = root / "request.json"
            progress_path = root / "progress.json"
            result_path = root / "result.json"
            cancel_path = root / "cancel"
            request_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "job_id": "job-" + "a" * 32,
                        "parent_pid": 0,
                        "progress_path": str(progress_path),
                        "result_path": str(result_path),
                        "cancel_path": str(cancel_path),
                        "spec": {
                            "job_kind": "optimization",
                            "design": DESIGN,
                            "optimization_spec": {"schema_version": 1},
                            "output_dir": str(root / "optimization"),
                            "timeout_per_experiment": 45.0,
                            "max_points": 321,
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(
                server, "_design_optimization_service", return_value=service
            ), patch.object(sys, "argv", ["job_worker", str(request_path)]):
                exit_code = job_worker.main()

            self.assertEqual(exit_code, 0)
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["success"])
            self.assertEqual(payload["result"]["status"], "optimized")
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            self.assertEqual(progress["stage"], "optimization_experiment")
            assert service.call is not None
            args, kwargs = service.call
            self.assertIsInstance(args[0], CircuitDesign)
            self.assertEqual(args[2], str(root / "optimization"))
            self.assertTrue(kwargs["resume"])
            self.assertEqual(kwargs["timeout_per_experiment"], 45.0)
            self.assertEqual(kwargs["max_points"], 321)

    def test_global_dispatch_enables_candidate_resume(self) -> None:
        service = _OptimizationService(status="completed", result_id="global-test")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(
                server, "_global_optimization_service", return_value=service
            ):
                exit_code, payload, progress = self._run_request(
                    root,
                    {
                        "job_kind": "global_optimization",
                        "design": DESIGN,
                        "global_optimization_spec": {"schema_version": 1},
                        "output_dir": str(root / "global"),
                        "timeout_per_experiment": 46.0,
                        "max_points": 654,
                    },
                )

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["success"])
        self.assertEqual(progress["stage"], "optimization_experiment")
        assert service.call is not None
        args, kwargs = service.call
        self.assertIsInstance(args[0], CircuitDesign)
        self.assertTrue(kwargs["resume"])
        self.assertEqual(kwargs["timeout_per_experiment"], 46.0)
        self.assertEqual(kwargs["max_points"], 654)

    def test_autonomous_dispatch_rebuilds_planner_and_enables_round_resume(self) -> None:
        service = _OptimizationService(status="corrected", result_id="auto-test")
        registry = object()
        planner = object()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(
                model_provider.ModelProviderRegistry,
                "from_config",
                return_value=registry,
            ), patch.object(
                autonomous_correction, "ModelRepairPlanner", return_value=planner
            ) as planner_type, patch.object(
                autonomous_correction,
                "AutonomousDesignCorrectionService",
                return_value=service,
            ), patch.object(server, "_experiment_application_service", return_value=object()):
                exit_code, payload, progress = self._run_request(
                    root,
                    {
                        "job_kind": "autonomous_correction",
                        "design": DESIGN,
                        "autonomous_correction_spec": {"schema_version": 1},
                        "provider_config": {
                            "schema_version": 1,
                            "active_provider": "deepseek",
                            "providers": {},
                        },
                        "provider": "deepseek",
                        "fallback_providers": ["local"],
                        "allow_failover": True,
                        "model_timeout": 23.0,
                        "output_dir": str(root / "autonomous"),
                        "timeout_per_experiment": 47.0,
                        "max_points": 987,
                    },
                )

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["success"])
        self.assertEqual(progress["stage"], "optimization_experiment")
        planner_type.assert_called_once_with(
            registry,
            provider_id="deepseek",
            fallback_provider_ids=("local",),
            allow_failover=True,
            timeout=23.0,
        )
        assert service.call is not None
        args, kwargs = service.call
        self.assertIsInstance(args[0], CircuitDesign)
        self.assertTrue(kwargs["resume"])
        self.assertEqual(kwargs["timeout_per_experiment"], 47.0)
        self.assertEqual(kwargs["max_points"], 987)


if __name__ == "__main__":
    unittest.main()
