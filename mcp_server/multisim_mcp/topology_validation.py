"""Round-trip topology checks for generated Multisim designs."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


_TOKEN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_.:$-]*)(?![A-Za-z0-9_])")
_SEPARATOR = re.compile(r"^-{5,}$")
_NET_ALIASES = {
    # Multisim's XSPICE digital models expose hidden supply pins as VDD/VSS
    # in ReportNetlist even when the source schematic uses high/low nets.
    "high": ("vdd",),
    "low": ("vss",),
}

# Source terminal semantics, independent of ReportNetlist row order.  A
# template inventory must confirm these names before the server uses them.
_DIGITAL_PORT_ORDER = {
    "DNOT4": ("I1", "O1", "VDD", "VSS"),
    "DAND5": ("1A", "1B", "1Y", "VDD", "VSS"),
    "DOR5": ("1A", "1B", "1Y", "VDD", "VSS"),
    "DNAND5": ("1A", "1B", "1Y", "VDD", "VSS"),
    "DNOR5": ("1A", "1B", "1Y", "VDD", "VSS"),
    "DXOR5": ("1A", "1B", "1Y", "VDD", "VSS"),
    "DXNOR5": ("1A", "1B", "1Y", "VDD", "VSS"),
    "DJK7": ("J", "K", "CLK", "SET", "RESET", "Q", "~Q"),
}

_SOURCE_PORT_ORDER = {
    **_DIGITAL_PORT_ORDER,
    # The native diode model exposes named anode/cathode ports as A/K.
    "D": ("A", "K"),
    # E/G carriers use the four MOS-shaped names for out+, out-, control+,
    # control-; schematic_builder writes the corresponding source nodes in
    # exactly this order.
    "E": ("D", "G", "S", "SUB"),
    "G": ("D", "G", "S", "SUB"),
}


def source_port_net_map(kind: str, nodes: Iterable[str]) -> dict[str, str]:
    """Return a verified source-terminal to net mapping for native models."""
    ports = _SOURCE_PORT_ORDER.get(kind, ())
    values = list(nodes)
    if not ports or len(values) != len(ports):
        return {}
    return dict(zip(ports, values))


def digital_port_net_map(kind: str, nodes: Iterable[str]) -> dict[str, str]:
    """Return known native digital terminal semantics, never guessed names."""
    if kind not in _DIGITAL_PORT_ORDER:
        return {}
    return source_port_net_map(kind, nodes)


def _net_key(value: str) -> str:
    lowered = str(value).casefold()
    return {"high": "vdd", "vdd": "vdd", "low": "vss", "vss": "vss"}.get(
        lowered, lowered
    )


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
    missing_nets: list[str] = []
    accepted_net_aliases: dict[str, str] = {}
    for item in nets:
        if _present(exported_netlist, item):
            continue
        aliases = _NET_ALIASES.get(item.casefold(), ())
        alias = next(
            (candidate for candidate in aliases if _present(exported_netlist, candidate)),
            None,
        )
        if alias is None:
            missing_nets.append(item)
        else:
            accepted_net_aliases[item] = alias
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
        "accepted_net_aliases": accepted_net_aliases,
        "evidence": "Multisim ReportNetlist text presence; internal vendor nodes are not treated as extras",
    }


def compare_pin_connections(
    expected: dict[str, list[str]],
    exported_netlist: str,
    declared_ports: dict[str, list[str]] | None = None,
    expected_named_ports: dict[str, dict[str, str]] | None = None,
    native_port_nets: dict[str, dict[str, list[str]]] | None = None,
) -> dict[str, Any]:
    """Compare pin/net connections in SPICE or Multisim report output.

    Multisim's ``ReportNetlist`` is a fixed-width connection table rather than
    SPICE text.  Its numeric pin rows can be checked exactly; XSPICE digital
    models expose named ports. Explicit terminal semantics allow those signal
    rows to be checked; missing rows and implicit power rows remain unverified.
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
        model_port_evidence: list[dict[str, Any]] = []
        checked = 0
        pin_evidence: list[dict[str, Any]] = []
        for refdes, expected_nets in expected.items():
            aliases = {refdes.casefold(), (refdes + "A").casefold()}
            matches = [row for row in rows if row[2].casefold() in aliases]
            if not matches:
                unverified.append(refdes)
                continue
            pin_rows = [(row[3], row[0]) for row in matches if len(row) >= 4]
            if declared_ports and refdes in declared_ports:
                declared = {
                    str(item).casefold() for item in declared_ports[refdes] if str(item)
                }
                named = [pin for pin, _ in pin_rows if not pin.isdigit()]
                hidden_named = [
                    row[0]
                    for row in matches
                    if len(row) == 3 and row[0].casefold() in declared
                ]
                observed_named = list(dict.fromkeys([*named, *hidden_named]))
                unknown = [pin for pin in observed_named if pin.casefold() not in declared]
                port_state = (
                    "mismatch"
                    if unknown
                    else "not_observed"
                    if not observed_named
                    else "present"
                    if {pin.casefold() for pin in observed_named} == declared
                    else "partial"
                )
                model_port_evidence.append(
                    {
                        "refdes": refdes,
                        "declared_ports": list(declared_ports[refdes]),
                        "observed_named_ports": observed_named,
                        "state": port_state,
                        "unknown_ports": unknown,
                        "mapping": "port identity only; named port to source-net mapping remains unverified",
                    }
                )
                if unknown:
                    mismatches.append({"refdes": refdes, "unknown_ports": unknown})
            named_expected = (expected_named_ports or {}).get(refdes, {})
            if named_expected:
                declared = {p.casefold() for p in (declared_ports or {}).get(refdes, [])}
                contract_valid = {p.casefold() for p in named_expected} == declared
                component_complete = contract_valid
                for pin, net in named_expected.items():
                    actual = [n for p, n in pin_rows if p.casefold() == pin.casefold()]
                    state = "unverified"
                    reason = "port not reported with a net"
                    if not contract_valid:
                        reason = "native port inventory does not confirm terminal contract"
                    elif len(actual) == 1:
                        state = "pass" if actual[0].casefold() == net.casefold() else "fail"
                        reason = "explicit named pin/net row"
                    elif len(actual) > 1:
                        state = "fail"
                        reason = "duplicate named pin rows"
                    if state == "unverified":
                        native_actual = (native_port_nets or {}).get(refdes, {}).get(pin, [])
                        if len(native_actual) == 1:
                            state = (
                                "pass"
                                if _net_key(native_actual[0]) == _net_key(net)
                                else "fail"
                            )
                            reason = "decoded native XML port/node mapping"
                            actual = list(native_actual)
                        elif len(native_actual) > 1:
                            state = "fail"
                            reason = "native XML port maps to multiple nodes"
                            actual = list(native_actual)
                    if state == "fail":
                        mismatches.append({"refdes": refdes, "pin": pin,
                                           "expected_net": net, "actual_nets": actual})
                    if state != "pass":
                        component_complete = False
                    pin_evidence.append({"refdes": refdes, "pin": pin,
                                         "expected_net": net, "actual_nets": actual,
                                         "status": state, "evidence": reason})
                if component_complete:
                    checked += 1
                else:
                    unverified.append(refdes)
                if model_port_evidence and model_port_evidence[-1]["refdes"] == refdes:
                    model_port_evidence[-1]["mapping"] = (
                        "explicit named pin/net rows checked against source terminal contract; "
                        "implicit power and missing rows remain unverified"
                    )
                continue
            if len(pin_rows) != len(expected_nets) or not all(
                pin.isdigit() for pin, _ in pin_rows
            ):
                native = (native_port_nets or {}).get(refdes, {})
                native_actual = [
                    (native.get(str(index)) or [""])[0]
                    for index in range(1, len(expected_nets) + 1)
                ]
                if all(native_actual) and all(
                    _net_key(actual) == _net_key(expected)
                    for actual, expected in zip(native_actual, expected_nets)
                ):
                    checked += 1
                    continue
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
            "model_port_evidence": model_port_evidence,
            "named_pin_connections": pin_evidence,
            "named_pin_counts": {
                state: sum(item["status"] == state for item in pin_evidence)
                for state in ("pass", "fail", "unverified")
            },
            "evidence": (
                "numeric pin rows from the Multisim ReportNetlist connection table; "
                "named signal rows checked with explicit source terminal semantics and "
                "CiPort inventory; implicit power and missing rows remain unverified"
            ),
        }

    mismatches: list[dict[str, Any]] = []
    lines = [
        line.strip()
        for line in exported_netlist.splitlines()
        if line.strip() and not line.lstrip().startswith(("*", ";", "#", "."))
    ]
    checked = 0
    unverified = []
    for refdes, expected_nets in expected.items():
        matches = [line.split() for line in lines if line.split() and line.split()[0].casefold() == refdes.casefold()]
        if not matches:
            unverified.append(refdes)
            continue
        checked += 1
        actual = matches[0][1 : 1 + len(expected_nets)]
        if len(actual) != len(expected_nets) or [item.casefold() for item in actual] != [item.casefold() for item in expected_nets]:
            mismatches.append({"refdes": refdes, "expected_nets": expected_nets, "actual_nets": actual})
    return {
        "schema_version": 1,
        "status": "fail" if mismatches else "unverified" if unverified else "pass",
        "checked_components": checked,
        "unverified_components": unverified,
        "mismatches": mismatches,
        "evidence": "ordered pin/net tokens from common SPICE ReportNetlist lines",
    }


__all__ = [
    "compare_pin_connections",
    "compare_roundtrip_topology",
    "digital_port_net_map",
    "source_port_net_map",
]
