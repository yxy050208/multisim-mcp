"""Tests for the DeepSeek Harness npm release guard."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "check_dsh_plugin_release.py"
SPEC = importlib.util.spec_from_file_location("dsh_plugin_release_check", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)

PACK_SCRIPT = REPO_ROOT / "tools" / "check_npm_pack_result.py"
PACK_SPEC = importlib.util.spec_from_file_location("npm_pack_result_check", PACK_SCRIPT)
assert PACK_SPEC is not None and PACK_SPEC.loader is not None
pack_check = importlib.util.module_from_spec(PACK_SPEC)
PACK_SPEC.loader.exec_module(pack_check)


def existing_package(*, version: str = "0.9.0", repository: str | None = None) -> dict:
    payload = {
        "name": "multisim-mcp-dsh-plugin",
        "dist-tags": {"latest": version},
        "versions": {
            version: {
                "name": "multisim-mcp-dsh-plugin",
                "version": version,
            }
        },
    }
    if repository is not None:
        payload["repository"] = {"type": "git", "url": repository}
    return payload


def npm_pack_package() -> dict:
    return {
        "id": "multisim-mcp-dsh-plugin@1.1.0",
        "name": "multisim-mcp-dsh-plugin",
        "version": "1.1.0",
        "filename": "multisim-mcp-dsh-plugin-1.1.0.tgz",
        "size": 100,
        "unpackedSize": 200,
        "files": [
            {"path": "LICENSE"},
            {"path": "README.md"},
            {"path": "cordis.patch.yml"},
            {"path": "package.json"},
        ],
    }


class DshPluginReleaseTest(unittest.TestCase):
    def test_local_package_boundary_passes(self) -> None:
        result = release.run_checks(REPO_ROOT, expected_version="1.1.0")
        self.assertTrue(result["success"])
        self.assertFalse(result["registry_checked"])
        self.assertIsNone(result["registry_state"])

    def test_unclaimed_name_is_valid_for_first_publication(self) -> None:
        result = release.run_checks(
            REPO_ROOT,
            expected_version="1.1.0",
            check_registry=True,
            registry_loader=lambda name, timeout: None,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["registry_state"], "unclaimed")

    def test_stage_mode_requires_an_existing_package(self) -> None:
        result = release.run_checks(
            REPO_ROOT,
            check_registry=True,
            require_existing_package=True,
            registry_loader=lambda name, timeout: None,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["findings"][0]["check"], "registry-package")

    def test_existing_package_from_same_repository_allows_new_version(self) -> None:
        remote = existing_package(
            repository="git+https://github.com/yxy050208/multisim-mcp.git"
        )
        result = release.run_checks(
            REPO_ROOT,
            check_registry=True,
            require_existing_package=True,
            registry_loader=lambda name, timeout: remote,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["registry_state"], "existing")

    def test_existing_target_version_is_rejected(self) -> None:
        remote = existing_package(
            version="1.1.0",
            repository="https://github.com/yxy050208/multisim-mcp",
        )
        result = release.run_checks(
            REPO_ROOT,
            check_registry=True,
            registry_loader=lambda name, timeout: remote,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["findings"][0]["check"], "registry-version")

    def test_existing_package_from_another_repository_is_rejected(self) -> None:
        remote = existing_package(repository="https://github.com/example/squatted")
        result = release.run_checks(
            REPO_ROOT,
            check_registry=True,
            registry_loader=lambda name, timeout: remote,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["findings"][0]["check"], "registry-ownership")

    def test_requested_version_must_match_package(self) -> None:
        result = release.run_checks(REPO_ROOT, expected_version="1.1.1")
        self.assertFalse(result["success"])
        self.assertEqual(result["findings"][0]["check"], "requested-version")

    def test_npm_11_array_pack_output_is_supported(self) -> None:
        result = pack_check.validate_pack_result(
            [npm_pack_package()],
            expected_name="multisim-mcp-dsh-plugin",
            expected_version="1.1.0",
        )
        self.assertEqual(result["filename"], "multisim-mcp-dsh-plugin-1.1.0.tgz")

    def test_npm_12_object_pack_output_is_supported(self) -> None:
        result = pack_check.validate_pack_result(
            {"multisim-mcp-dsh-plugin": npm_pack_package()},
            expected_name="multisim-mcp-dsh-plugin",
            expected_version="1.1.0",
        )
        self.assertEqual(result["files"], sorted(pack_check.EXPECTED_FILES))

    def test_unexpected_npm_pack_file_is_rejected(self) -> None:
        package = npm_pack_package()
        package["files"].append({"path": "secret.env"})
        with self.assertRaisesRegex(ValueError, "unexpected npm package files"):
            pack_check.validate_pack_result(
                {"multisim-mcp-dsh-plugin": package},
                expected_name="multisim-mcp-dsh-plugin",
                expected_version="1.1.0",
            )


if __name__ == "__main__":
    unittest.main()
