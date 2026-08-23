"""Validate the pinned DeepSeek Harness integration contract.

Local checks are deterministic and suitable for every pull request. Upstream
checks are opt-in because DeepSeek Harness is still a Developer Preview and may
change independently of this repository.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 20.0
JsonLoader = Callable[[str, float], dict[str, Any]]


def _load_json_url(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "multisim-mcp-harness-compatibility-check/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _finding(level: str, check: str, message: str) -> dict[str, str]:
    return {"level": level, "check": check, "message": message}


def load_compatibility_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported DeepSeek Harness compatibility schema")
    if payload.get("status") != "developer-preview":
        raise ValueError("Harness status must explicitly remain developer-preview")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(payload.get("verified_date", ""))):
        raise ValueError("verified_date must use YYYY-MM-DD")
    upstream = payload.get("upstream")
    if not isinstance(upstream, dict):
        raise ValueError("compatibility manifest is missing upstream metadata")
    required_upstream = (
        "repository",
        "root_package_url",
        "dsh_cli_package_url",
        "mcp_client_package_url",
        "harness_version",
        "dsh_cli_package",
        "dsh_cli_version",
        "mcp_client_package",
        "mcp_client_version",
        "node_engine",
        "package_manager",
        "mcp_sdk_range",
    )
    missing_upstream = [key for key in required_upstream if not upstream.get(key)]
    if missing_upstream:
        raise ValueError(f"upstream metadata is missing: {', '.join(missing_upstream)}")
    official_raw_prefix = "https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/"
    for key in (
        "root_package_url",
        "dsh_cli_package_url",
        "mcp_client_package_url",
    ):
        if not str(upstream[key]).startswith(official_raw_prefix):
            raise ValueError(f"{key} must reference the official DeepSeek repository")

    contract = payload.get("contract")
    if not isinstance(contract, dict):
        raise ValueError("compatibility manifest is missing the local contract")
    if contract.get("transport") != "stdio":
        raise ValueError("the verified Harness transport must be stdio")
    if contract.get("mcp_resources_bridged") is not False:
        raise ValueError("MCP Resources must remain explicitly marked unbridged")
    if contract.get("mcp_prompts_bridged") is not False:
        raise ValueError("MCP Prompts must remain explicitly marked unbridged")
    for key in (
        "tool_name_prefix",
        "skill_discovery_root",
        "bundle_package",
        "bundle_version",
        "bundle_path",
    ):
        if not isinstance(contract.get(key), str) or not contract[key]:
            raise ValueError(f"contract is missing {key}")
    try:
        re.compile(contract["server_name_pattern"])
    except (KeyError, re.error) as exc:
        raise ValueError("invalid or missing server_name_pattern") from exc
    profiles = contract.get("tool_profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("contract must contain tool profile counts")
    if any(not isinstance(value, int) or value <= 0 for value in profiles.values()):
        raise ValueError("tool profile counts must be positive integers")
    skills = contract.get("skills")
    if (
        not isinstance(skills, list)
        or not skills
        or any(not isinstance(name, str) or not name for name in skills)
        or len(skills) != len(set(skills))
    ):
        raise ValueError("contract skills must be a non-empty unique list")
    return payload


def check_local_contract(repo_root: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    package_root = repo_root / "mcp_server"
    sys.path.insert(0, str(package_root))
    try:
        from multisim_mcp.harness_skills import bundled_harness_skill_manifest
        from multisim_mcp.tool_profiles import PROFILE_TOOL_NAMES
    except Exception as exc:  # pragma: no cover - defensive environment boundary
        return [_finding("error", "local-import", f"cannot import local package: {exc}")]
    finally:
        if sys.path and sys.path[0] == str(package_root):
            sys.path.pop(0)

    contract = manifest["contract"]
    expected_profiles = contract.get("tool_profiles", {})
    actual_profiles = {name: len(names) for name, names in PROFILE_TOOL_NAMES.items()}
    if actual_profiles != expected_profiles:
        findings.append(
            _finding(
                "error",
                "tool-profiles",
                f"expected {expected_profiles}, found {actual_profiles}",
            )
        )

    skill_manifest = bundled_harness_skill_manifest()
    expected_skills = contract.get("skills", [])
    if skill_manifest.get("skills") != expected_skills:
        findings.append(
            _finding(
                "error",
                "harness-skills",
                "packaged Harness skill names or order differ from the contract",
            )
        )
    discovery_root = skill_manifest.get("upstream", {}).get("discovery_root")
    if discovery_root != contract.get("skill_discovery_root"):
        findings.append(
            _finding(
                "error",
                "skill-discovery-root",
                f"expected {contract.get('skill_discovery_root')!r}, found {discovery_root!r}",
            )
        )

    bundle_root = repo_root / contract["bundle_path"]
    bundle_manifest_path = bundle_root / "package.json"
    bundle_patch_path = bundle_root / "cordis.patch.yml"
    bundle_license_path = bundle_root / "LICENSE"
    try:
        bundle_manifest = json.loads(bundle_manifest_path.read_text(encoding="utf-8"))
        bundle_patch = bundle_patch_path.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(
            _finding("error", "bundle-package", f"cannot read Harness bundle: {exc}")
        )
    else:
        bundle_comparisons = {
            "name": (contract["bundle_package"], bundle_manifest.get("name")),
            "version": (contract["bundle_version"], bundle_manifest.get("version")),
            "mcp-client": (
                manifest["upstream"]["mcp_client_version"],
                bundle_manifest.get("dependencies", {}).get(
                    manifest["upstream"]["mcp_client_package"]
                ),
            ),
            "patch": (
                "./cordis.patch.yml",
                bundle_manifest.get("dsh", {}).get("bundle", {}).get("patch"),
            ),
        }
        for field, (wanted, actual) in bundle_comparisons.items():
            if wanted != actual:
                findings.append(
                    _finding(
                        "error",
                        "bundle-package",
                        f"{field} expected {wanted!r}, found {actual!r}",
                    )
                )
        pyproject = (package_root / "pyproject.toml").read_text(encoding="utf-8")
        version_match = re.search(
            r'(?m)^version\s*=\s*"([^"]+)"\s*$', pyproject
        )
        python_version = version_match.group(1) if version_match else None
        if bundle_manifest.get("version") != python_version:
            findings.append(
                _finding(
                    "error",
                    "bundle-version-sync",
                    "Harness bundle and Python package versions must match",
                )
            )
        required_patch_fragments = (
            "- insert:",
            '- id: "mcp-multisim"',
            'name: "@deepseek-ai/dsh-mcp-client"',
            'serverName: "multisim"',
            'transport: "stdio"',
            "MULTISIM_MCP_PYTHON",
            "failOnStartupError: true",
        )
        for fragment in required_patch_fragments:
            if fragment not in bundle_patch:
                findings.append(
                    _finding(
                        "error", "bundle-patch", f"missing fragment: {fragment}"
                    )
                )
        if not re.search(
            r'(?m)^- insert:\s*\r?\n\s+- id: "mcp-multisim"\s*$',
            bundle_patch,
        ):
            findings.append(
                _finding(
                    "error",
                    "bundle-patch",
                    "mcp-multisim must be nested under an insert operation",
                )
            )
        if "DEEPSEEK_API_KEY" in bundle_patch:
            findings.append(
                _finding(
                    "error",
                    "bundle-credential-boundary",
                    "bundle must not forward DEEPSEEK_API_KEY to the MCP child",
                )
            )
        if not bundle_license_path.is_file():
            findings.append(
                _finding("error", "bundle-license", "Harness bundle is missing LICENSE")
            )

    source = (package_root / "multisim_mcp" / "cli.py").read_text(encoding="utf-8")
    expected_package = manifest["upstream"].get("mcp_client_package")
    required_fragments = (
        f'name: "{expected_package}"',
        f'transport: "{contract.get("transport")}"',
        "serverName:",
        "TOOL_PROFILE_ENV",
    )
    for fragment in required_fragments:
        if fragment not in source:
            findings.append(
                _finding("error", "cordis-config", f"missing fragment: {fragment}")
            )

    pattern = contract.get("server_name_pattern")
    if pattern not in source:
        findings.append(
            _finding(
                "error",
                "server-name-pattern",
                "CLI server-name validation differs from the pinned contract",
            )
        )
    return findings


def check_upstream_contract(
    manifest: dict[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    loader: JsonLoader = _load_json_url,
) -> list[dict[str, str]]:
    expected = manifest["upstream"]
    try:
        root = loader(expected["root_package_url"], timeout)
        cli = loader(expected["dsh_cli_package_url"], timeout)
        client = loader(expected["mcp_client_package_url"], timeout)
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        return [_finding("drift", "upstream-fetch", f"cannot read upstream metadata: {exc}")]

    comparisons = {
        "harness-version": (expected["harness_version"], root.get("version")),
        "dsh-cli-package": (expected["dsh_cli_package"], cli.get("name")),
        "dsh-cli-version": (expected["dsh_cli_version"], cli.get("version")),
        "node-engine": (expected["node_engine"], root.get("engines", {}).get("node")),
        "package-manager": (expected["package_manager"], root.get("packageManager")),
        "mcp-client-package": (expected["mcp_client_package"], client.get("name")),
        "mcp-client-version": (expected["mcp_client_version"], client.get("version")),
        "mcp-sdk-range": (
            expected["mcp_sdk_range"],
            client.get("dependencies", {}).get("@modelcontextprotocol/sdk"),
        ),
    }
    findings = []
    for check, (wanted, actual) in comparisons.items():
        if wanted != actual:
            findings.append(
                _finding("drift", check, f"expected {wanted!r}, upstream has {actual!r}")
            )
    return findings


def run_checks(
    repo_root: Path,
    *,
    check_upstream: bool = False,
    warn_only: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    loader: JsonLoader = _load_json_url,
) -> dict[str, Any]:
    manifest_path = repo_root / "compatibility" / "deepseek-harness.json"
    try:
        manifest = load_compatibility_manifest(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        findings = [_finding("error", "manifest", str(exc))]
        return {
            "schema_version": SCHEMA_VERSION,
            "success": False,
            "upstream_checked": False,
            "upstream_match": None,
            "findings": findings,
        }

    findings = check_local_contract(repo_root, manifest)
    upstream_findings: list[dict[str, str]] = []
    if check_upstream:
        upstream_findings = check_upstream_contract(
            manifest, timeout=timeout, loader=loader
        )
        for finding in upstream_findings:
            finding["level"] = "warning" if warn_only else "error"
        findings.extend(upstream_findings)

    errors = sum(item["level"] == "error" for item in findings)
    warnings = sum(item["level"] == "warning" for item in findings)
    return {
        "schema_version": SCHEMA_VERSION,
        "success": errors == 0,
        "verified_date": manifest["verified_date"],
        "pinned_harness_version": manifest["upstream"]["harness_version"],
        "upstream_checked": check_upstream,
        "upstream_match": not upstream_findings if check_upstream else None,
        "errors": errors,
        "warnings": warnings,
        "findings": findings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the pinned DeepSeek Harness integration contract"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root (default: inferred from this script)",
    )
    parser.add_argument(
        "--check-upstream",
        action="store_true",
        help="compare the pinned versions with the official upstream package files",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="report upstream drift as warnings; local contract errors still fail",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.warn_only and not args.check_upstream:
        raise SystemExit("--warn-only requires --check-upstream")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    result = run_checks(
        args.repo_root.resolve(),
        check_upstream=args.check_upstream,
        warn_only=args.warn_only,
        timeout=args.timeout,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        state = "PASS" if result["success"] else "FAIL"
        print(f"DeepSeek Harness compatibility: {state}")
        for finding in result["findings"]:
            print(f"[{finding['level']}] {finding['check']}: {finding['message']}")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
