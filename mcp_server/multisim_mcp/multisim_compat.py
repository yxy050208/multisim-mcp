"""Version-aware Multisim capability profiles and fallback policy."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


_VERSION_RE = re.compile(r"(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?")


def parse_multisim_version(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.search(str(value or ""))
    if not match:
        return (0, 0, 0)
    return tuple(int(match.group(name) or 0) for name in ("major", "minor", "patch"))


def capability_profile(version: str) -> dict[str, Any]:
    """Return conservative capabilities for a detected Multisim version."""
    parsed = parse_multisim_version(version)
    known = parsed[0] > 0
    return {
        "schema_version": 1,
        "version": str(version),
        "version_tuple": list(parsed),
        "known_version": known,
        "capabilities": {
            "com_connect": known,
            "open_save_ms14": known,
            "native_component_enumeration": known,
            "native_parameter_write": known,
            # These stay conservative until a version-specific native setter
            # is observed by the COM capability probe.
            "native_temperature_control": False,
            "native_model_parameter_write": False,
            "circuit_parameter_readback": known and parsed >= (14, 3, 0),
            "direct_output_requests": known and parsed >= (14, 3, 0),
            "command_engine": known,
            "roundtrip_netlist": known,
            "native_sweep": known and parsed >= (14, 0, 0),
        },
        "fallbacks": {
            "analysis": "command-engine",
            "direct_output_requests": "command-engine",
            "unknown_version": "introspection-only",
        },
    }


def select_analysis_backend(version: str, *, direct_output_error: bool = False) -> str:
    profile = capability_profile(version)
    if direct_output_error or not profile["capabilities"]["direct_output_requests"]:
        return "command-engine"
    return "native-com"


def compatibility_matrix_entry(
    target_version: str,
    *,
    detected_version: str = "",
    native_probe: Mapping[str, Any] | None = None,
    evidence_path: str | None = None,
) -> dict[str, Any]:
    """Describe the evidence level for one requested Multisim version.

    A type-library probe proves the installed API surface only.  It does not
    certify every component family or a complete circuit workflow, so the
    resulting status is deliberately named ``api-verified``.  A requested
    version that differs from the installed version remains ``unverified``;
    capabilities are never copied from a nearby release.
    """

    target_text = str(target_version or "").strip()
    detected_text = str(detected_version or "").strip()
    target_tuple = parse_multisim_version(target_text)
    detected_tuple = parse_multisim_version(detected_text)
    target_known = target_tuple[0] > 0
    same_version = bool(target_known and detected_tuple[0] > 0 and target_tuple == detected_tuple)
    has_probe = isinstance(native_probe, Mapping)
    if not target_known:
        status = "unsupported"
    elif same_version and has_probe:
        status = "api-verified"
    else:
        status = "unverified"
    capabilities = dict(native_probe.get("capabilities", {})) if has_probe else {}
    return {
        "schema_version": 1,
        "target_version": target_text,
        "target_version_tuple": list(target_tuple),
        "detected_version": detected_text or None,
        "detected_version_tuple": list(detected_tuple),
        "version_match": same_version,
        "status": status,
        "verification_scope": "native-api-introspection" if status == "api-verified" else None,
        "evidence_path": evidence_path,
        "profile": capability_profile(target_text),
        "observed_capabilities": capabilities,
        "reason": (
            "The requested version matches the installed version and has a native API probe"
            if status == "api-verified" else
            "No native probe exists for the requested Multisim version"
            if status == "unverified" else
            "The requested version string is not recognized"
        ),
    }


def build_compatibility_matrix(
    target_versions: Iterable[str],
    *,
    detected_version: str = "",
    native_probe: Mapping[str, Any] | None = None,
    evidence_path: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic, JSON-safe matrix for requested versions."""

    targets = [str(item).strip() for item in target_versions if str(item).strip()]
    if not targets:
        raise ValueError("target_versions must contain at least one version")
    entries = [
        compatibility_matrix_entry(
            target,
            detected_version=detected_version,
            native_probe=native_probe,
            evidence_path=evidence_path,
        )
        for target in dict.fromkeys(targets)
    ]
    return {
        "schema_version": 1,
        "detected_version": str(detected_version or "") or None,
        "entries": entries,
        "verified_api_versions": [
            item["target_version"] for item in entries if item["status"] == "api-verified"
        ],
        "unverified_versions": [
            item["target_version"] for item in entries if item["status"] == "unverified"
        ],
        "unsupported_versions": [
            item["target_version"] for item in entries if item["status"] == "unsupported"
        ],
    }


__all__ = [
    "build_compatibility_matrix",
    "capability_profile",
    "compatibility_matrix_entry",
    "parse_multisim_version",
    "select_analysis_backend",
]
