"""Tests for the version-gated DeepSeek Harness compatibility contract."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "check_deepseek_harness_compat.py"
SPEC = importlib.util.spec_from_file_location("harness_compatibility_check", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
compat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compat)


def matching_upstream_loader(url: str, timeout: float) -> dict:
    if timeout <= 0:
        raise AssertionError("timeout must be positive")
    if url.endswith("/packages/mcp/mcp-client/package.json"):
        return {
            "name": "@deepseek-ai/dsh-mcp-client",
            "version": "0.1.1-rc.2",
            "dependencies": {"@modelcontextprotocol/sdk": "^1.12.0"},
        }
    if url.endswith("/apps/cli/package.json"):
        return {"name": "@deepseek-ai/dsh", "version": "0.1.1-rc.2"}
    return {
        "version": "0.1.1-rc.2",
        "packageManager": "pnpm@11.7.0",
        "engines": {"node": "^22.19.0 || >=24.0.0"},
    }


class HarnessCompatibilityTest(unittest.TestCase):
    def test_local_contract_matches_packaged_integration(self) -> None:
        result = compat.run_checks(REPO_ROOT)
        self.assertTrue(result["success"])
        self.assertEqual(result["errors"], 0)
        self.assertIsNone(result["upstream_match"])

    def test_matching_upstream_metadata_passes_strict_check(self) -> None:
        result = compat.run_checks(
            REPO_ROOT,
            check_upstream=True,
            loader=matching_upstream_loader,
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["upstream_match"])
        self.assertEqual(result["findings"], [])

    def test_upstream_drift_can_fail_strict_or_warn_in_monitor_mode(self) -> None:
        def drifted_loader(url: str, timeout: float) -> dict:
            payload = matching_upstream_loader(url, timeout)
            if url.endswith("/master/package.json"):
                payload["version"] = "0.1.0-rc.8"
            return payload

        strict = compat.run_checks(
            REPO_ROOT,
            check_upstream=True,
            loader=drifted_loader,
        )
        monitored = compat.run_checks(
            REPO_ROOT,
            check_upstream=True,
            warn_only=True,
            loader=drifted_loader,
        )
        self.assertFalse(strict["success"])
        self.assertFalse(strict["upstream_match"])
        self.assertEqual(strict["errors"], 1)
        self.assertTrue(monitored["success"])
        self.assertFalse(monitored["upstream_match"])
        self.assertEqual(monitored["warnings"], 1)

    def test_invalid_manifest_fails_before_local_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_dir = root / "compatibility"
            manifest_dir.mkdir()
            (manifest_dir / "deepseek-harness.json").write_text(
                '{"schema_version": 999}', encoding="utf-8"
            )
            result = compat.run_checks(root)
        self.assertFalse(result["success"])
        self.assertEqual(result["findings"][0]["check"], "manifest")


if __name__ == "__main__":
    unittest.main()
