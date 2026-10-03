"""Conservative temperature-capability diagnostics for native Multisim runs.

Multisim's native COM API and its XSPICE command engine expose different
surfaces.  A SPICE ``.TEMP`` directive being accepted by a generic netlist
backend must not be treated as proof that a saved ``.ms14`` project can carry
or replay a temperature corner.  This module keeps that distinction explicit
and provides a small parser for command-engine probe logs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


NATIVE_TEMPERATURE_UNVERIFIED_REASON = (
    "Multisim native COM has no verified temperature setter and its XSPICE "
    "command engine does not provide a .temp command; ordinary OP/AC/TRAN "
    "results therefore cannot be labelled as temperature-corner evidence"
)

_UNSUPPORTED_TEMP_RE = re.compile(
    r"(?:\.temp|temperature).*no such command(?: available)?",
    re.IGNORECASE,
)


def classify_temperature_command_log(log: str) -> dict[str, Any]:
    """Classify a ``DoCommandLine`` log without treating a silent run as pass.

    The command engine can return a completed state even when it prints an
    unsupported-command diagnostic.  A probe is therefore only useful when
    the log is retained and inspected.  Unknown or empty logs remain
    ``unverified`` rather than being promoted to ``supported``.
    """

    text = str(log or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    unsupported = [line for line in lines if _UNSUPPORTED_TEMP_RE.search(line)]
    if unsupported:
        return {
            "status": "unsupported",
            "supported": False,
            "directive": ".temp",
            "reason": NATIVE_TEMPERATURE_UNVERIFIED_REASON,
            "diagnostics": unsupported,
        }
    return {
        "status": "unverified",
        "supported": False,
        "directive": ".temp",
        "reason": NATIVE_TEMPERATURE_UNVERIFIED_REASON,
        "diagnostics": [],
    }


def temperature_capability(
    *, native_writer: bool,
    command_probe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an auditable capability record for a native project.

    ``native_writer`` comes from the COM type-library probe.  A command probe
    may add evidence, but cannot turn a completed command with no explicit
    temperature readback into a verified capability.
    """

    if native_writer:
        return {
            "status": "candidate",
            "verified": False,
            "native_writer": True,
            "command_probe": dict(command_probe or {}),
            "reason": (
                "A temperature writer was exposed by the COM type library, "
                "but open/save/reopen and response-difference evidence is still required"
            ),
        }
    probe = dict(command_probe or {})
    return {
        "status": "unsupported" if probe.get("status") == "unsupported" else "unverified",
        "verified": False,
        "native_writer": False,
        "command_probe": probe,
        "reason": NATIVE_TEMPERATURE_UNVERIFIED_REASON,
    }


__all__ = [
    "NATIVE_TEMPERATURE_UNVERIFIED_REASON",
    "classify_temperature_command_log",
    "temperature_capability",
]
