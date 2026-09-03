from __future__ import annotations

import json
import unittest

from multisim_mcp.api_contract import (
    API_CONTRACT_NAME,
    API_CONTRACT_VERSION,
    ERROR_CODES,
    build_capabilities,
    build_error,
    build_error_envelope,
    classify_error,
)


class ApiContractTest(unittest.TestCase):
    def test_capabilities_are_deterministic_and_json_safe(self) -> None:
        profile = {
            "name": "full",
            "tool_count": 78,
            "available_profiles": ["core", "experiment", "optimization", "full"],
        }
        first = build_capabilities(server_version="1.2.0", tool_profile=profile)
        second = build_capabilities(server_version="1.2.0", tool_profile=profile)

        self.assertEqual(first, second)
        self.assertEqual(first["api_name"], API_CONTRACT_NAME)
        self.assertEqual(first["api_version"], API_CONTRACT_VERSION)
        self.assertEqual(first["tool_profile"]["tool_count"], 78)
        self.assertEqual(first["errors"]["codes"], list(ERROR_CODES))
        self.assertIn("status_uri_template", first["tasks"])
        json.dumps(first, ensure_ascii=False, allow_nan=False)

    def test_error_classification_preserves_legacy_fields(self) -> None:
        cases = (
            (FileNotFoundError("missing"), "not_found", False),
            (FileExistsError("exists"), "already_exists", False),
            (PermissionError("denied"), "permission_denied", False),
            (TimeoutError("slow"), "timeout", True),
            (ConnectionError("offline"), "backend_unavailable", True),
            (ValueError("bad input"), "invalid_input", False),
            (RuntimeError("failed"), "runtime_error", False),
            (OSError("disk"), "io_error", False),
            (Exception("unexpected"), "internal_error", False),
        )
        for exc, expected_code, expected_retryable in cases:
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(classify_error(exc), (expected_code, expected_retryable))
                payload = build_error(exc, command="fixture")
                self.assertEqual(payload["code"], expected_code)
                self.assertEqual(payload["retryable"], expected_retryable)
                self.assertEqual(payload["type"], type(exc).__name__)
                self.assertEqual(payload["message"], str(exc))

    def test_error_envelope_is_backward_compatible(self) -> None:
        payload = build_error_envelope(ValueError("bad"), command="fixture")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["command"], "fixture")
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["type"], "ValueError")
        self.assertEqual(payload["error"]["code"], "invalid_input")

