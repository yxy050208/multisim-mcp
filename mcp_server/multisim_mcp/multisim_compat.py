"""Version-aware Multisim capability profiles and fallback policy."""

from __future__ import annotations

import re
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


__all__ = ["capability_profile", "parse_multisim_version", "select_analysis_backend"]
