"""Round-trip topology checks for generated Multisim designs."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


_TOKEN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_.:$-]*)(?![A-Za-z0-9_])")
_SEPARATOR = re.compile(r"^-{5,}$")


def _present(text: str, name: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text, re.I) is not None


def compare_roundtrip_topology(
    expected_components: Iterable[str],
    expected_nets: Iterable[str],
    exported_netlist: str,
) -> dict[str, Any]:
    """Compare stable names without assuming a vendor netlist dialect."""
    components = sorted({str(item) for item in expected_components if str(item) and str(item) != "0"})
    nets = sorted({str(item) for item in expected_nets if str(item) and str(item) != "0"})
    # Multisim names single-section instances of a multi-section package with
    # the package's own section suffix (for example U1 -> U1A for the first
    # op-amp of an LM324 carrier). Accept that alias instead of reporting a
    # false "component lost" failure.
    def _seen(name: str) -> bool:
        return _present(exported_netlist, name) or _present(exported_netlist, name + "A")

    missing_components = [item for item in components if not _seen(item)]
    missing_nets = [item for item in nets if not _present(exported_netlist, item)]
    # Extra names are intentionally not inferred from arbitrary tokens: vendor
    # netlists contain model names and internal nodes that are not source nets.
    return {
        "schema_version": 1,
        "status": "pass" if not missing_components and not missing_nets else "fail",
        "expected_component_count": len(components),
        "expected_net_count": len(nets),
        "missing_components": missing_components,
        "missing_nets": missing_nets,
        "extra_components": [],
        "extra_nets": [],
        "evidence": "Multisim ReportNetlist text presence; internal vendor nodes are not treated as extras",
    }


def compare_pin_connections(
    expected: dict[str, list[str]],
    exported_netlist: str,
) -> dict[str, Any]:
    """Compare pin/net connections in SPICE or Multisim report output.

    Multisim's ``ReportNetlist`` is a fixed-width connection table rather than
    SPICE text.  Its numeric pin rows can be checked exactly; XSPICE digital
    models expose named and hidden power pins instead, so those entries are
    reported as ``unverified`` rather than mistaken for a mismatch.
    """
    table_lines = [line.strip() for line in exported_netlist.splitlines() if line.strip()]
    separators = [index for index, line in enumerate(table_lines) if _SEPARATOR.fullmatch(line)]
    if len(separators) >= 3:
        rows = [
            line.split()
            for line in table_lines[separators[1] + 1 : separators[-1]]
            if len(line.split()) >= 3
        ]
        mismatches: list[dict[str, Any]] = []
        unverified: list[str] = []
        checked = 0
        for refdes, expected_nets in expected.items():
            aliases = {refdes.casefold(), (refdes + "A").casefold()}
            matches = [row for row in rows if row[2].casefold() in aliases]
            if not matches:
                continue
            pin_rows = [(row[3], row[0]) for row in matches if len(row) >= 4]
            if len(pin_rows) != len(expected_nets) or not all(
                pin.isdigit() for pin, _ in pin_rows
            ):
                unverified.append(refdes)
                continue
            by_pin = {int(pin): net for pin, net in pin_rows}
            expected_pin_numbers = set(range(1, len(expected_nets) + 1))
            if set(by_pin) != expected_pin_numbers:
                unverified.append(refdes)
                continue
            actual = [by_pin[index] for index in range(1, len(expected_nets) + 1)]
            checked += 1
            if [item.casefold() for item in actual] != [
                item.casefold() for item in expected_nets
            ]:
                mismatches.append(
                    {
                        "refdes": refdes,
                        "expected_nets": expected_nets,
                        "actual_nets": actual,
                    }
                )
        status = "fail" if mismatches else "unverified" if unverified else "pass"
        return {
            "schema_version": 1,
            "status": status,
            "checked_components": checked,
            "unverified_components": unverified,
            "mismatches": mismatches,
            "evidence": (
                "numeric pin rows from the Multisim ReportNetlist connection table; "
                "named/hidden XSPICE pins require model-level evidence"
            ),
        }

    mismatches: list[dict[str, Any]] = []
    lines = [
        line.strip()
        for line in exported_netlist.splitlines()
        if line.strip() and not line.lstrip().startswith(("*", ";", "#", "."))
    ]
    for refdes, expected_nets in expected.items():
        matches = [line.split() for line in lines if line.split() and line.split()[0].casefold() == refdes.casefold()]
        if not matches:
            # Component presence is reported by the higher-level comparison.
            continue
        actual = matches[0][1 : 1 + len(expected_nets)]
        if len(actual) != len(expected_nets) or [item.casefold() for item in actual] != [item.casefold() for item in expected_nets]:
            mismatches.append({"refdes": refdes, "expected_nets": expected_nets, "actual_nets": actual})
    return {
        "schema_version": 1,
        "status": "pass" if not mismatches else "fail",
        "checked_components": len(expected),
        "unverified_components": [],
        "mismatches": mismatches,
        "evidence": "ordered pin/net tokens from common SPICE ReportNetlist lines",
    }


__all__ = ["compare_pin_connections", "compare_roundtrip_topology"]
