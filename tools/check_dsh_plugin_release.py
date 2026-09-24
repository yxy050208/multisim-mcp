"""Preflight the independently published DeepSeek Harness npm bundle."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import check_deepseek_harness_compat as compatibility  # noqa: E402


DEFAULT_TIMEOUT_SECONDS = 20.0
EXPECTED_PACKAGE_FILES = {
    "LICENSE",
    "README.md",
    "cordis.patch.yml",
    "package.json",
}
RegistryLoader = Callable[[str, float], dict[str, Any] | None]


def _finding(check: str, message: str) -> dict[str, str]:
    return {"level": "error", "check": check, "message": message}


def _load_registry_package(name: str, timeout: float) -> dict[str, Any] | None:
    encoded_name = urllib.parse.quote(name, safe="")
    request = urllib.request.Request(
        f"https://registry.npmjs.org/{encoded_name}",
        headers={"User-Agent": "multisim-mcp-dsh-release-check/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _repository_url(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("url")
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized.startswith("git+"):
        normalized = normalized[4:]
    if normalized.startswith("git@github.com:"):
        normalized = "https://github.com/" + normalized.split(":", 1)[1]
    if normalized.startswith("git://github.com/"):
        normalized = "https://github.com/" + normalized.removeprefix(
            "git://github.com/"
        )
    return normalized.removesuffix("/").removesuffix(".git")


def _registry_repository(payload: dict[str, Any]) -> str | None:
    direct = _repository_url(payload.get("repository"))
    if direct:
        return direct
    latest = payload.get("dist-tags", {}).get("latest")
    if isinstance(latest, str):
        version = payload.get("versions", {}).get(latest, {})
        return _repository_url(version.get("repository"))
    return None


def _check_package_boundary(
    repo_root: Path,
    package: dict[str, Any],
    contract: dict[str, Any],
    expected_version: str | None,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    package_root = repo_root / contract["bundle_path"]
    comparisons = {
        "package-name": (contract["bundle_package"], package.get("name")),
        "package-version": (contract["bundle_version"], package.get("version")),
        "package-license": ("MIT", package.get("license")),
        "package-access": ("public", package.get("publishConfig", {}).get("access")),
    }
    if expected_version is not None:
        comparisons["requested-version"] = (expected_version, package.get("version"))
    for check, (wanted, actual) in comparisons.items():
        if wanted != actual:
            findings.append(
                _finding(check, f"expected {wanted!r}, found {actual!r}")
            )

    if package.get("private") is True:
        findings.append(_finding("package-private", "publishable bundle cannot be private"))

    declared = package.get("files")
    if not isinstance(declared, list) or any(
        not isinstance(item, str) for item in declared
    ):
        findings.append(_finding("package-files", "files must be a string array"))
        declared_files: set[str] = set()
    else:
        declared_files = set(declared) | {"package.json"}
        if declared_files != EXPECTED_PACKAGE_FILES:
            findings.append(
                _finding(
                    "package-files",
                    f"expected {sorted(EXPECTED_PACKAGE_FILES)}, found {sorted(declared_files)}",
                )
            )

    for relative in declared_files:
        path = package_root / relative
        if path.is_symlink():
            findings.append(
                _finding("package-files", f"symbolic links are not publishable: {relative}")
            )
        elif not path.is_file():
            findings.append(_finding("package-files", f"missing file: {relative}"))

    repository = _repository_url(package.get("repository"))
    if repository != "https://github.com/yxy050208/multisim-mcp":
        findings.append(
            _finding(
                "package-repository",
                "package repository must be https://github.com/yxy050208/multisim-mcp",
            )
        )
    return findings


def run_checks(
    repo_root: Path,
    *,
    expected_version: str | None = None,
    check_registry: bool = False,
    require_existing_package: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    registry_loader: RegistryLoader = _load_registry_package,
) -> dict[str, Any]:
    compatibility_result = compatibility.run_checks(repo_root)
    findings = list(compatibility_result["findings"])
    registry_state: str | None = None

    manifest_path = repo_root / "compatibility" / "deepseek-harness.json"
    try:
        manifest = compatibility.load_compatibility_manifest(manifest_path)
        contract = manifest["contract"]
        package_path = repo_root / contract["bundle_path"] / "package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        findings.append(_finding("release-metadata", str(exc)))
        package = {}
        contract = {}
    else:
        findings.extend(
            _check_package_boundary(repo_root, package, contract, expected_version)
        )

    if require_existing_package and not check_registry:
        findings.append(
            _finding(
                "registry-mode",
                "require_existing_package requires check_registry",
            )
        )

    if check_registry and package.get("name") and package.get("version"):
        try:
            registry_package = registry_loader(package["name"], timeout)
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            findings.append(_finding("registry-fetch", f"cannot query npm registry: {exc}"))
            registry_state = "unavailable"
        else:
            if registry_package is None:
                registry_state = "unclaimed"
                if require_existing_package:
                    findings.append(
                        _finding(
                            "registry-package",
                            "staged publishing requires an existing npm package",
                        )
                    )
            else:
                registry_state = "existing"
                versions = registry_package.get("versions", {})
                if package["version"] in versions:
                    findings.append(
                        _finding(
                            "registry-version",
                            f"{package['name']}@{package['version']} already exists",
                        )
                    )
                local_repository = _repository_url(package.get("repository"))
                remote_repository = _registry_repository(registry_package)
                if remote_repository != local_repository:
                    findings.append(
                        _finding(
                            "registry-ownership",
                            "existing package repository does not match this project",
                        )
                    )

    errors = sum(item.get("level") == "error" for item in findings)
    return {
        "schema_version": 1,
        "success": errors == 0,
        "package": package.get("name"),
        "version": package.get("version"),
        "registry_checked": check_registry,
        "registry_state": registry_state,
        "errors": errors,
        "findings": findings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the DeepSeek Harness npm bundle before release"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument("--expected-version")
    parser.add_argument("--check-registry", action="store_true")
    parser.add_argument("--require-existing-package", action="store_true")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    result = run_checks(
        args.repo_root.resolve(),
        expected_version=args.expected_version,
        check_registry=args.check_registry,
        require_existing_package=args.require_existing_package,
        timeout=args.timeout,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        state = "PASS" if result["success"] else "FAIL"
        print(f"DeepSeek Harness plugin release: {state}")
        for finding in result["findings"]:
            print(f"[{finding['level']}] {finding['check']}: {finding['message']}")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
