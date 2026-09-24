"""Tests for safe model-provider discovery, storage, and diagnostics."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.provider_config import (
    _NoRedirectHandler,
    PROVIDER_CONFIG_SCHEMA_VERSION,
    build_provider,
    default_provider_config_path,
    discover_provider_config,
    make_provider_config,
    probe_provider,
    read_provider_config,
    validate_provider_config,
    write_provider_config,
)


class _Response:
    status = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self._body[:limit]


class ProviderConfigTest(unittest.TestCase):
    def test_windows_default_path_uses_local_app_data(self) -> None:
        path = default_provider_config_path(
            {"LOCALAPPDATA": r"C:\LocalAppData"}, os_name="nt"
        )
        self.assertEqual(path.name, "providers.json")
        self.assertEqual(path.parent.name, "multisim-mcp")

    def test_deepseek_discovery_never_returns_key_value(self) -> None:
        secret = "test-secret-that-must-not-leak"
        result = discover_provider_config({"DEEPSEEK_API_KEY": secret})
        serialized = json.dumps(result)
        provider = result["config"]["providers"]["deepseek"]
        self.assertNotIn(secret, serialized)
        self.assertEqual(
            provider["credential"],
            {"source": "environment", "name": "DEEPSEEK_API_KEY"},
        )
        self.assertEqual(provider["model"], "deepseek-v4-flash")
        self.assertFalse(result["credential_values_exposed"])

    def test_discovery_reports_incomplete_openai_without_guessing_model(self) -> None:
        result = discover_provider_config({"OPENAI_API_KEY": "secret"})
        self.assertEqual(result["detected"], [])
        self.assertEqual(
            result["skipped"],
            [{"provider": "openai", "missing": ["OPENAI_MODEL"]}],
        )

    def test_custom_remote_plain_http_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            build_provider(
                "openai-compatible",
                base_url="http://models.example.test/v1",
                model="fixture-model",
            )

    def test_plaintext_secret_field_is_rejected(self) -> None:
        provider = build_provider("deepseek")
        provider["api_key"] = "must-not-be-here"
        raw = {
            "schema_version": PROVIDER_CONFIG_SCHEMA_VERSION,
            "active_provider": "deepseek",
            "providers": {"deepseek": provider},
        }
        with self.assertRaisesRegex(ValueError, "plaintext"):
            validate_provider_config(raw)

    def test_extra_credential_field_is_rejected(self) -> None:
        provider = build_provider("deepseek")
        provider["credential"]["value"] = "must-not-be-here"
        raw = {
            "schema_version": PROVIDER_CONFIG_SCHEMA_VERSION,
            "active_provider": "deepseek",
            "providers": {"deepseek": provider},
        }
        with self.assertRaisesRegex(ValueError, "only source"):
            validate_provider_config(raw)

    def test_atomic_round_trip_contains_reference_not_secret(self) -> None:
        provider = build_provider("deepseek")
        config = make_provider_config([provider], "deepseek")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "providers.json"
            written = write_provider_config(config, path)
            content = written.read_text(encoding="utf-8")
            loaded = read_provider_config(path)
            leftovers = list(path.parent.glob("*.tmp"))
        self.assertEqual(loaded, config)
        self.assertIn("DEEPSEEK_API_KEY", content)
        self.assertNotIn("must-not-be-here", content)
        self.assertEqual(leftovers, [])

    def test_probe_uses_bearer_but_redacts_it_from_result(self) -> None:
        secret = "probe-secret-value"
        provider = build_provider("deepseek")
        captured: dict[str, object] = {}

        def open_fixture(request: object, timeout: float) -> _Response:
            captured["authorization"] = request.get_header("Authorization")
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return _Response(
                {"data": [{"id": "deepseek-v4-flash"}, {"id": "other"}]}
            )

        with patch(
            "multisim_mcp.provider_config._open_without_redirect",
            side_effect=open_fixture,
        ):
            result = probe_provider(
                provider, environ={"DEEPSEEK_API_KEY": secret}, timeout=1.25
            )
        self.assertEqual(captured["authorization"], f"Bearer {secret}")
        self.assertEqual(captured["url"], "https://api.deepseek.com/models")
        self.assertEqual(captured["timeout"], 1.25)
        self.assertTrue(result["success"])
        self.assertTrue(result["model_available"])
        self.assertNotIn(secret, json.dumps(result))

    def test_probe_does_not_connect_when_credential_is_missing(self) -> None:
        provider = build_provider("openai", model="fixture-model")
        with patch(
            "multisim_mcp.provider_config._open_without_redirect"
        ) as urlopen:
            result = probe_provider(provider, environ={})
        urlopen.assert_not_called()
        self.assertEqual(result["status"], "missing_credential")

    def test_probe_fails_when_configured_model_is_not_listed(self) -> None:
        provider = build_provider("ollama", model="missing-model")
        with patch(
            "multisim_mcp.provider_config._open_without_redirect",
            return_value=_Response({"data": [{"id": "other-model"}]}),
        ):
            result = probe_provider(provider, environ={})
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "model_missing")
        self.assertFalse(result["model_available"])

    def test_probe_redacts_secret_if_it_appears_in_an_endpoint(self) -> None:
        secret = "secret-in-path"
        provider = build_provider(
            "openai-compatible",
            provider_id="fixture",
            base_url=f"https://models.example.test/{secret}/v1",
            model="fixture-model",
            api_key_env="FIXTURE_API_KEY",
        )
        with patch(
            "multisim_mcp.provider_config._open_without_redirect",
            side_effect=OSError(f"failed with {secret}"),
        ):
            result = probe_provider(
                provider, environ={"FIXTURE_API_KEY": secret}
            )
        self.assertNotIn(secret, json.dumps(result))
        self.assertIn("[REDACTED]", result["endpoint"])

    def test_probe_does_not_follow_redirects(self) -> None:
        handler = _NoRedirectHandler()
        self.assertIsNone(
            handler.redirect_request(
                object(), None, 302, "Found", {}, "https://other.example.test"
            )
        )

    def test_ollama_loopback_provider_needs_no_credential(self) -> None:
        provider = build_provider("ollama", model="qwen3:8b")
        self.assertIsNone(provider["credential"])
        self.assertEqual(provider["base_url"], "http://127.0.0.1:11434/v1")


if __name__ == "__main__":
    unittest.main()
