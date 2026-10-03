"""Auditable vendor-model overrides for native Multisim evidence runs.

Multisim 14.3 exposes no model-parameter setter through the Automation API.
For the certified local 2N3904 family we therefore patch only an allowlisted
set of scalar values in the decoded XML, encode a fresh ``.ms14`` and require
the normal Multisim open/save/model-fingerprint checks afterwards.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Mapping

from .preferred_values import format_spice_scalar, parse_spice_scalar


_QNPN_PARAMETERS = {"is": "Is", "vaf": "Vaf", "bf": "Bf"}
_MODEL_RE = re.compile(
    r"(?P<prefix>&amp;ASC\.MODEL\s+2N3904(?:__BJT_NPN__\d+)?\s+NPN\()"
    r"(?P<body>[^)]*)"
    r"(?P<suffix>\))",
    re.IGNORECASE | re.DOTALL,
)


def normalize_qnpn_model_parameters(raw: Mapping[str, Any]) -> dict[str, float]:
    """Validate the small, certified 2N3904 override surface."""
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError("2N3904 model parameters must be a non-empty object")
    normalized: dict[str, float] = {}
    for key, value in raw.items():
        canonical = _QNPN_PARAMETERS.get(str(key).casefold())
        if canonical is None:
            raise ValueError(f"unsupported 2N3904 model parameter: {key}")
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"{canonical} must be a positive finite scalar")
        try:
            number = float(parse_spice_scalar(str(value))) if isinstance(value, str) else float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{canonical} must be a positive finite scalar") from exc
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"{canonical} must be a positive finite scalar")
        limits = {"Is": (1e-30, 1e-3), "Vaf": (1e-3, 1e6), "Bf": (1.0, 1e6)}
        low, high = limits[canonical]
        if not low <= number <= high:
            raise ValueError(f"{canonical} is outside the certified range {low:g}..{high:g}")
        if canonical in normalized:
            raise ValueError(f"duplicate 2N3904 model parameter: {canonical}")
        normalized[canonical] = number
    return dict(sorted(normalized.items()))


def _format_model_value(value: float) -> str:
    # Use the same engineering suffixes as the generated templates, including
    # ``f`` for the 2N3904 saturation current.
    return format_spice_scalar(parse_spice_scalar(format(value, ".12g")))


def extract_qnpn_model_parameters(xml_text: str) -> dict[str, float]:
    """Read the first certified 2N3904 model definition from decoded XML."""
    matches = list(_MODEL_RE.finditer(str(xml_text)))
    if not matches:
        raise ValueError("decoded XML does not contain a 2N3904 model definition")
    values: dict[str, float] = {}
    for key, canonical in _QNPN_PARAMETERS.items():
        match = re.search(rf"(?i)\b{re.escape(canonical)}\s*=\s*([^\s)]+)", matches[0].group("body"))
        if match is None:
            raise ValueError(f"2N3904 model is missing {canonical}")
        values[canonical] = float(parse_spice_scalar(match.group(1)))
    return values


def apply_qnpn_model_parameters(path: str | Path, overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Patch all linked 2N3904 definitions and return before/after evidence."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    normalized = normalize_qnpn_model_parameters(overrides)
    original = source.read_text(encoding="utf-8")
    matches = list(_MODEL_RE.finditer(original))
    if len(matches) < 2:
        raise ValueError("expected both component and shared 2N3904 model definitions")
    before = extract_qnpn_model_parameters(original)

    def replace(match: re.Match[str]) -> str:
        body = match.group("body")
        for canonical, value in normalized.items():
            pattern = re.compile(rf"(\b{re.escape(canonical)}\s*=\s*)([^\s)]+)", re.IGNORECASE)
            body, count = pattern.subn(rf"\g<1>{_format_model_value(value)}", body, count=1)
            if count != 1:
                raise ValueError(f"2N3904 model is missing {canonical}")
        return match.group("prefix") + body + match.group("suffix")

    updated = _MODEL_RE.sub(replace, original)
    after = extract_qnpn_model_parameters(updated)
    for canonical, expected in normalized.items():
        if not math.isclose(after[canonical], expected, rel_tol=1e-9, abs_tol=0.0):
            raise ValueError(f"2N3904 model override failed readback for {canonical}")
    if updated != original:
        source.write_text(updated, encoding="utf-8")
    return {
        "model": "2N3904",
        "parameters": normalized,
        "before": before,
        "after": after,
        "definitions_updated": len(matches),
        "status": "xml-patched-awaiting-native-roundtrip" if updated != original else "xml-unchanged-awaiting-native-roundtrip",
    }


def verify_qnpn_model_parameters(path: str | Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a saved decoded XML contains the requested model values."""
    wanted = normalize_qnpn_model_parameters(expected)
    actual = extract_qnpn_model_parameters(Path(path).read_text(encoding="utf-8"))
    checks = {
        key: math.isclose(actual[key], value, rel_tol=1e-9, abs_tol=0.0)
        for key, value in wanted.items()
    }
    return {"model": "2N3904", "expected": wanted, "actual": actual,
            "checks": checks, "ok": all(checks.values())}


__all__ = [
    "apply_qnpn_model_parameters",
    "extract_qnpn_model_parameters",
    "normalize_qnpn_model_parameters",
    "verify_qnpn_model_parameters",
]
