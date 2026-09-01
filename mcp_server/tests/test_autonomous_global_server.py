from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from multisim_mcp import server
from multisim_mcp.eda_core import CircuitDesign
from tests.test_design_optimization_server import DESIGN


class AutonomousGlobalServerTest(unittest.TestCase):
    def test_global_adapter_forwards_transport_neutral_inputs(self) -> None:
        service = Mock()
        service.run.return_value = {"success": True, "status": "completed"}
        spec = {"schema_version": 1}
        with patch.object(server, "_global_optimization_service", return_value=service):
            result = server.global_optimize_design(
                DESIGN,
                spec,
                "C:/global-output",
                timeout_per_experiment=33.0,
                max_points=456,
            )
        self.assertTrue(result["success"])
        args, kwargs = service.run.call_args
        self.assertIsInstance(args[0], CircuitDesign)
        self.assertIs(args[1], spec)
        self.assertEqual(args[2], "C:/global-output")
        self.assertEqual(kwargs["timeout_per_experiment"], 33.0)
        self.assertEqual(kwargs["max_points"], 456)

    def test_autonomous_adapter_builds_model_planner_without_exposing_secrets(self) -> None:
        registry = object()
        planner = object()
        correction = Mock()
        correction.run.return_value = {"success": True, "status": "corrected"}
        with patch.object(
            server, "read_provider_config", return_value={"providers": []}
        ) as read_config, patch.object(
            server.ModelProviderRegistry, "from_config", return_value=registry
        ), patch.object(
            server, "ModelRepairPlanner", return_value=planner
        ) as planner_type, patch.object(
            server, "AutonomousDesignCorrectionService", return_value=correction
        ):
            result = server.autonomous_correct_design(
                DESIGN,
                {"schema_version": 1},
                "C:/correction-output",
                provider_config_path="C:/provider.json",
                provider="deepseek",
                fallback_providers=["local"],
                allow_failover=True,
                model_timeout=25.0,
                timeout_per_experiment=44.0,
                max_points=789,
            )
        self.assertTrue(result["success"])
        read_config.assert_called_once_with("C:/provider.json")
        planner_type.assert_called_once_with(
            registry,
            provider_id="deepseek",
            fallback_provider_ids=("local",),
            allow_failover=True,
            timeout=25.0,
        )
        args, kwargs = correction.run.call_args
        self.assertIsInstance(args[0], CircuitDesign)
        self.assertEqual(args[2], "C:/correction-output")
        self.assertEqual(kwargs["timeout_per_experiment"], 44.0)
        self.assertEqual(kwargs["max_points"], 789)

    def test_submit_global_optimization_persists_durable_request(self) -> None:
        manager = Mock()
        manager.submit.return_value = {"job_id": "job-global", "state": "queued"}
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            server, "validate_global_optimization_spec"
        ) as validate, patch.object(server, "_job_manager", return_value=manager):
            output = Path(tmp) / "global"
            result = server.submit_global_optimization(
                DESIGN,
                {"schema_version": 1, "title": "durable"},
                str(output),
                timeout_per_experiment=35.0,
                max_points=456,
                job_timeout=3600.0,
                heartbeat_timeout=90.0,
            )

        self.assertEqual(result["job_id"], "job-global")
        validate.assert_called_once()
        request = manager.submit.call_args.args[0]
        self.assertEqual(request["job_kind"], "global_optimization")
        self.assertEqual(request["timeout_per_experiment"], 35.0)
        self.assertEqual(request["max_points"], 456)
        self.assertFalse(request["resume_existing"])

    def test_submit_autonomous_correction_persists_no_secret_value(self) -> None:
        manager = Mock()
        manager.submit.return_value = {"job_id": "job-auto", "state": "queued"}
        registry = Mock()
        registry.provider_ids.return_value = ["deepseek", "local"]
        provider_config = {
            "schema_version": 1,
            "active_provider": "deepseek",
            "providers": {
                "deepseek": {
                    "id": "deepseek",
                    "provider": "deepseek",
                    "api_format": "openai-compatible",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "models_path": "/models",
                    "credential": {
                        "source": "environment",
                        "name": "DEEPSEEK_API_KEY",
                    },
                },
                "local": {
                    "id": "local",
                    "provider": "ollama",
                    "api_format": "openai-compatible",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "model": "qwen3",
                    "models_path": "/models",
                    "credential": None,
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            server, "validate_autonomous_correction_spec"
        ), patch.object(
            server, "read_provider_config", return_value=provider_config
        ), patch.object(
            server.ModelProviderRegistry, "from_config", return_value=registry
        ), patch.object(server, "_job_manager", return_value=manager):
            result = server.submit_autonomous_correction(
                DESIGN,
                {"schema_version": 1},
                str(Path(tmp) / "autonomous"),
                provider_config_path="C:/providers.json",
                provider="deepseek",
                fallback_providers=["local"],
                allow_failover=True,
                model_timeout=25.0,
                heartbeat_timeout=90.0,
            )

        self.assertEqual(result["job_id"], "job-auto")
        request = manager.submit.call_args.args[0]
        self.assertEqual(request["job_kind"], "autonomous_correction")
        self.assertEqual(request["provider"], "deepseek")
        self.assertEqual(request["fallback_providers"], ["local"])
        serialized = json.dumps(request, ensure_ascii=False)
        self.assertIn("DEEPSEEK_API_KEY", serialized)
        self.assertNotIn("secret-value", serialized)

    def test_submit_autonomous_requires_heartbeat_beyond_model_timeout(self) -> None:
        registry = Mock()
        registry.provider_ids.return_value = ["deepseek"]
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            server, "validate_autonomous_correction_spec"
        ), patch.object(
            server, "read_provider_config", return_value={"providers": {}}
        ), patch.object(
            server.ModelProviderRegistry, "from_config", return_value=registry
        ):
            with self.assertRaisesRegex(ValueError, "exceed model_timeout"):
                server.submit_autonomous_correction(
                    DESIGN,
                    {"schema_version": 1},
                    str(Path(tmp) / "autonomous"),
                    provider="deepseek",
                    model_timeout=90.0,
                    heartbeat_timeout=90.0,
                )


if __name__ == "__main__":
    unittest.main()
