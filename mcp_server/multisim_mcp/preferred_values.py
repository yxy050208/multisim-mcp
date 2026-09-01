"""IEC 60063-style preferred-value generation for bounded optimization."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Final


PREFERRED_SERIES: Final[dict[str, tuple[int, ...]]] = {
    "E12": (10, 12, 15, 18, 22, 27, 33, 39, 47, 56, 68, 82),
    "E24": (
        10, 11, 12, 13, 15, 16, 18, 20, 22, 24, 27, 30,
        33, 36, 39, 43, 47, 51, 56, 62, 68, 75, 82, 91,
    ),
    "E48": (
        100, 105, 110, 115, 121, 127, 133, 140, 147, 154, 162, 169,
        178, 187, 196, 205, 215, 226, 237, 249, 261, 274, 287, 301,
        316, 332, 348, 365, 383, 402, 422, 442, 464, 487, 511, 536,
        562, 590, 619, 649, 681, 715, 750, 787, 825, 866, 909, 953,
    ),
    "E96": (
        100, 102, 105, 107, 110, 113, 115, 118, 121, 124, 127, 130,
        133, 137, 140, 143, 147, 150, 154, 158, 162, 165, 169, 174,
        178, 182, 187, 191, 196, 200, 205, 210, 215, 221, 226, 232,
        237, 243, 249, 255, 261, 267, 274, 280, 287, 294, 301, 309,
        316, 324, 332, 340, 348, 357, 365, 374, 383, 392, 402, 412,
        422, 432, 442, 453, 464, 475, 487, 499, 511, 523, 536, 549,
        562, 576, 590, 604, 619, 634, 649, 665, 681, 698, 715, 732,
        750, 768, 787, 806, 825, 845, 866, 887, 909, 931, 953, 976,
    ),
}

_SERIES_DIVISOR: Final = {"E12": 10, "E24": 10, "E48": 100, "E96": 100}
_SCALAR_RE = re.compile(
    r"^([+]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?)"
    r"([A-Za-z\u00b5\u03bc]*)$"
)
_SUFFIX_SCALE: Final = {
    "": Decimal("1"),
    "t": Decimal("1e12"),
    "g": Decimal("1e9"),
    "meg": Decimal("1e6"),
    "k": Decimal("1e3"),
    "m": Decimal("1e-3"),
    "u": Decimal("1e-6"),
    "µ": Decimal("1e-6"),
    "μ": Decimal("1e-6"),
    "n": Decimal("1e-9"),
    "p": Decimal("1e-12"),
    "f": Decimal("1e-15"),
}
_EXPONENT_SUFFIX: Final = {
    -15: "f",
    -12: "p",
    -9: "n",
    -6: "u",
    -3: "m",
    0: "",
    3: "k",
    6: "Meg",
    9: "G",
    12: "T",
}


def parse_spice_scalar(value: str) -> Decimal:
    """Parse one positive scalar with a standard SPICE engineering suffix."""
    if not isinstance(value, str):
        raise ValueError("SPICE scalar must be a string")
    normalized = value.strip()
    match = _SCALAR_RE.fullmatch(normalized)
    if match is None:
        raise ValueError(f"unsupported SPICE scalar: {value!r}")
    suffix = match.group(2)
    key = suffix if suffix in {"µ", "μ"} else suffix.casefold()
    scale = _SUFFIX_SCALE.get(key)
    if scale is None:
        raise ValueError(f"unsupported SPICE suffix: {suffix!r}")
    try:
        result = Decimal(match.group(1)) * scale
    except InvalidOperation as exc:
        raise ValueError(f"invalid SPICE scalar: {value!r}") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("preferred values must be finite and greater than zero")
    return result


def spice_value_key(value: str) -> str:
    """Return a representation-independent key (for example 1k == 1000)."""
    return str(parse_spice_scalar(value).normalize())


def format_spice_scalar(value: Decimal) -> str:
    """Format a positive decimal using a portable engineering suffix."""
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError("value must be a positive finite Decimal")
    exponent = (value.adjusted() // 3) * 3
    if exponent not in _EXPONENT_SUFFIX:
        raise ValueError("preferred value is outside supported f-to-T range")
    scaled = value.scaleb(-exponent)
    text = format(scaled.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}{_EXPONENT_SUFFIX[exponent]}"


def generate_preferred_values(series: str, minimum: str, maximum: str) -> list[str]:
    """Generate an inclusive, ascending preferred-value range."""
    normalized_series = str(series).strip().upper()
    values = PREFERRED_SERIES.get(normalized_series)
    if values is None:
        choices = ", ".join(PREFERRED_SERIES)
        raise ValueError(f"series must be one of: {choices}")
    lower = parse_spice_scalar(minimum)
    upper = parse_spice_scalar(maximum)
    if lower > upper:
        raise ValueError("series.minimum must not exceed series.maximum")
    divisor = Decimal(_SERIES_DIVISOR[normalized_series])
    generated: list[Decimal] = []
    for decade in range(lower.adjusted() - 1, upper.adjusted() + 2):
        scale = Decimal(10) ** decade
        for preferred in values:
            candidate = Decimal(preferred) / divisor * scale
            if lower <= candidate <= upper:
                generated.append(candidate)
    unique = sorted(set(generated))
    return [format_spice_scalar(item) for item in unique]


__all__ = [
    "PREFERRED_SERIES",
    "format_spice_scalar",
    "generate_preferred_values",
    "parse_spice_scalar",
    "spice_value_key",
]
