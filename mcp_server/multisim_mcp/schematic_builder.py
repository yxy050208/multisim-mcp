"""Build editable Multisim XML from simple SPICE netlists.

This module intentionally stays pure standard library so it can be unit-tested
without Multisim, COM, or the MCP runtime.  The generated XML is encoded to
``.ms14`` by the ``ewe`` tool and then opened through the Automation API.
"""

from __future__ import annotations

import copy
import itertools
import math
import os
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from multisim_mcp.component_adapters import expand_component_adapters


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_PACK_ENV = "MULTISIM_MCP_TEMPLATE_DIR"
TEMPLATE_ONLY_ENV = "MULTISIM_MCP_TEMPLATE_ONLY"

ID_ATTRS = frozenset(
    {
        "ID",
        "CiID",
        "Circuit",
        "Component",
        "PortID",
        "CiComponent",
        "Connect1",
        "Connect2",
        "Node",
        "NodeText",
    }
)
GUID_ATTRS = frozenset({"Guid", "InstanceID"})

_VALUE_RE = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([A-Za-z]*)$"
)
_SUFFIX_SCALE = {
    "": 1.0,
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}


class IdAllocator:
    """Allocates globally unique numeric IDs and GUIDs for generated XML."""

    def __init__(self, start: int = 900_000_000) -> None:
        self._counter = itertools.count(start)

    def next_id(self) -> str:
        return str(next(self._counter))

    def next_guid(self) -> str:
        return "{" + str(uuid.uuid4()).upper() + "}"


@dataclass
class ComponentSpec:
    kind: str
    refdes: str
    nodes: list[str]
    value: str | None = None
    model: str | None = None
    model_definition: str | None = None
    parameters: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ComponentDefinition:
    """Native Multisim template and placement metadata for a SPICE family."""

    kind: str
    element_template: str
    symbol_template: str
    port_templates: tuple[str, ...]
    origin_x: int
    origin_y: int
    origin_step_y: int
    value_unit: str = ""


COMPONENT_DEFINITIONS: dict[str, ComponentDefinition] = {
    "R": ComponentDefinition(
        "R", "r_element.xml", "sym_r.xml", ("r_port1.xml", "r_port2.xml"),
        306, 189, 135, "ohm",
    ),
    "C": ComponentDefinition(
        "C", "c_element.xml", "sym_c.xml", ("c_port1.xml", "c_port2.xml"),
        306, 324, 135, "F",
    ),
    "L": ComponentDefinition(
        "L", "l_element.xml", "sym_l.xml", ("l_port1.xml", "l_port2.xml"),
        306, 459, 135, "H",
    ),
    "V": ComponentDefinition(
        "V", "v_element.xml", "sym_v.xml", ("v_port1.xml", "v_port2.xml"),
        36, 216, 180, "V",
    ),
    "I": ComponentDefinition(
        "I", "i_element.xml", "sym_i.xml", ("i_port2.xml", "i_port1.xml"),
        36, 369, 180, "A",
    ),
    "E": ComponentDefinition(
        "E", "mnmos_element.xml", "sym_mnmos.xml",
        ("mnmos_port1.xml", "mnmos_port2.xml", "mnmos_port3.xml", "mnmos_port4.xml"),
        270, 657, 180, "V/V",
    ),
    "F": ComponentDefinition(
        "F", "i_element.xml", "sym_i.xml", ("i_port2.xml", "i_port1.xml"),
        36, 657, 180, "A/A",
    ),
    "G": ComponentDefinition(
        "G", "mnmos_element.xml", "sym_mnmos.xml",
        ("mnmos_port1.xml", "mnmos_port2.xml", "mnmos_port3.xml", "mnmos_port4.xml"),
        270, 837, 180, "S",
    ),
    "H": ComponentDefinition(
        "H", "v_element.xml", "sym_v.xml", ("v_port1.xml", "v_port2.xml"),
        36, 837, 180, "ohm",
    ),
    "BV": ComponentDefinition(
        "BV", "v_element.xml", "sym_v.xml", ("v_port1.xml", "v_port2.xml"),
        36, 1017, 180, "expression",
    ),
    "BI": ComponentDefinition(
        "BI", "i_element.xml", "sym_i.xml", ("i_port2.xml", "i_port1.xml"),
        36, 1197, 180, "expression",
    ),
    "T": ComponentDefinition(
        "T", "mnmos_element.xml", "sym_mnmos.xml",
        ("mnmos_port1.xml", "mnmos_port2.xml", "mnmos_port3.xml", "mnmos_port4.xml"),
        270, 1017, 180, "transmission-line",
    ),
    "XSUB2": ComponentDefinition(
        "XSUB2", "r_element.xml", "sym_r.xml", ("r_port1.xml", "r_port2.xml"),
        432, 1017, 180, "subcircuit",
    ),
    "XSUB3": ComponentDefinition(
        "XSUB3", "qnpn_element.xml", "sym_qnpn.xml",
        ("qnpn_port2.xml", "qnpn_port1.xml", "qnpn_port3.xml"),
        432, 1197, 180, "subcircuit",
    ),
    "XSUB4": ComponentDefinition(
        "XSUB4", "mnmos_element.xml", "sym_mnmos.xml",
        ("mnmos_port1.xml", "mnmos_port2.xml", "mnmos_port3.xml", "mnmos_port4.xml"),
        432, 1377, 180, "subcircuit",
    ),
    "XSUB5": ComponentDefinition(
        "XSUB5", "opamp5_element.xml", "sym_opamp5.xml",
        (
            "opamp5_port1.xml",
            "opamp5_port2.xml",
            "opamp5_port4.xml",
            "opamp5_port5.xml",
            "opamp5_port3.xml",
        ),
        432, 1557, 180, "subcircuit",
    ),
    "XSUBN": ComponentDefinition(
        "XSUBN", "xsub16_element.xml", "sym_djk7.xml", (),
        432, 1737, 180, "variable-subcircuit",
    ),
    "S": ComponentDefinition(
        "S", "mnmos_element.xml", "sym_mnmos.xml",
        ("mnmos_port1.xml", "mnmos_port2.xml", "mnmos_port3.xml", "mnmos_port4.xml"),
        639, 1017, 180, "switch-model",
    ),
    "JN": ComponentDefinition(
        "JN", "qnpn_element.xml", "sym_qnpn.xml",
        ("qnpn_port2.xml", "qnpn_port1.xml", "qnpn_port3.xml"),
        639, 1197, 180, "JFET-model",
    ),
    "JP": ComponentDefinition(
        "JP", "qpnp_element.xml", "sym_qpnp.xml",
        ("qpnp_port2.xml", "qpnp_port1.xml", "qpnp_port3.xml"),
        639, 1377, 180, "JFET-model",
    ),
    "ZN": ComponentDefinition(
        "ZN", "qnpn_element.xml", "sym_qnpn.xml",
        ("qnpn_port2.xml", "qnpn_port1.xml", "qnpn_port3.xml"),
        639, 1557, 180, "MESFET-model",
    ),
    "ZP": ComponentDefinition(
        "ZP", "qpnp_element.xml", "sym_qpnp.xml",
        ("qpnp_port2.xml", "qpnp_port1.xml", "qpnp_port3.xml"),
        639, 1737, 180, "MESFET-model",
    ),
    "W": ComponentDefinition(
        "W", "r_element.xml", "sym_r.xml", ("r_port1.xml", "r_port2.xml"),
        639, 1917, 180, "switch-model",
    ),
    "K": ComponentDefinition(
        "K", "r_element.xml", "sym_r.xml", (),
        639, 2097, 180, "coupling",
    ),
    "O": ComponentDefinition(
        "O", "mnmos_element.xml", "sym_mnmos.xml",
        ("mnmos_port1.xml", "mnmos_port2.xml", "mnmos_port3.xml", "mnmos_port4.xml"),
        639, 2277, 180, "LTRA-model",
    ),
    "U": ComponentDefinition(
        "U", "qnpn_element.xml", "sym_qnpn.xml",
        ("qnpn_port2.xml", "qnpn_port1.xml", "qnpn_port3.xml"),
        639, 2457, 180, "URC-model",
    ),
    "OSC6": ComponentDefinition(
        "OSC6", "osc6_element.xml", "sym_osc6.xml",
        (
            # User-facing order: A, B, C, D, EXT+, EXT-. Native terminal
            # numbers are laid out as 1,4,2,5,3,6 on the instrument symbol.
            "osc6_port1.xml", "osc6_port4.xml", "osc6_port2.xml",
            "osc6_port5.xml", "osc6_port3.xml", "osc6_port6.xml",
        ),
        1053, 1017, 180, "virtual-instrument",
    ),
    "XFG3": ComponentDefinition(
        "XFG3", "xfg3_element.xml", "sym_xfg3.xml",
        ("xfg3_port1.xml", "xfg3_port2.xml", "xfg3_port3.xml"),
        1053, 1197, 180, "virtual-instrument-source",
    ),
    "DNOT4": ComponentDefinition(
        "DNOT4", "dnot4_element.xml", "sym_dnot4.xml",
        ("dnot4_port1.xml", "dnot4_port2.xml", "dnot4_port3.xml", "dnot4_port4.xml"),
        846, 1017, 180, "digital",
    ),
    "DAND5": ComponentDefinition(
        "DAND5", "dand5_element.xml", "sym_dand5.xml",
        (
            "dand5_port1.xml", "dand5_port2.xml", "dand5_port3.xml",
            "dand5_port4.xml", "dand5_port5.xml",
        ),
        846, 1197, 180, "digital",
    ),
    "DOR5": ComponentDefinition(
        "DOR5", "dor5_element.xml", "sym_dor5.xml",
        (
            "dor5_port1.xml", "dor5_port2.xml", "dor5_port3.xml",
            "dor5_port4.xml", "dor5_port5.xml",
        ),
        846, 1377, 180, "digital",
    ),
    "DNAND5": ComponentDefinition(
        "DNAND5", "dand5_element.xml", "sym_dand5.xml",
        (
            "dand5_port1.xml", "dand5_port2.xml", "dand5_port3.xml",
            "dand5_port4.xml", "dand5_port5.xml",
        ),
        846, 1467, 180, "digital-preview-carrier",
    ),
    "DNOR5": ComponentDefinition(
        "DNOR5", "dor5_element.xml", "sym_dor5.xml",
        (
            "dor5_port1.xml", "dor5_port2.xml", "dor5_port3.xml",
            "dor5_port4.xml", "dor5_port5.xml",
        ),
        846, 1557, 180, "digital-preview-carrier",
    ),
    "DXOR5": ComponentDefinition(
        "DXOR5", "dor5_element.xml", "sym_dor5.xml",
        (
            "dor5_port1.xml", "dor5_port2.xml", "dor5_port3.xml",
            "dor5_port4.xml", "dor5_port5.xml",
        ),
        846, 1647, 180, "digital-preview-carrier",
    ),
    "DXNOR5": ComponentDefinition(
        "DXNOR5", "dor5_element.xml", "sym_dor5.xml",
        (
            "dor5_port1.xml", "dor5_port2.xml", "dor5_port3.xml",
            "dor5_port4.xml", "dor5_port5.xml",
        ),
        846, 1737, 180, "digital-preview-carrier",
    ),
    "DJK7": ComponentDefinition(
        "DJK7", "djk7_element.xml", "sym_djk7.xml",
        (
            "djk7_port2.xml", "djk7_port3.xml", "djk7_port1.xml",
            "djk7_port6.xml", "djk7_port5.xml", "djk7_port4.xml",
            "djk7_port7.xml",
        ),
        846, 1557, 180, "digital",
    ),
    "D": ComponentDefinition(
        "D", "d_element.xml", "sym_d.xml", ("d_port1.xml", "d_port2.xml"),
        540, 189, 135,
    ),
    "QNPN": ComponentDefinition(
        "QNPN", "qnpn_element.xml", "sym_qnpn.xml",
        ("qnpn_port2.xml", "qnpn_port1.xml", "qnpn_port3.xml"),
        540, 324, 180,
    ),
    "QPNP": ComponentDefinition(
        "QPNP", "qpnp_element.xml", "sym_qpnp.xml",
        ("qpnp_port2.xml", "qpnp_port1.xml", "qpnp_port3.xml"),
        540, 504, 180,
    ),
    "MNMOS": ComponentDefinition(
        "MNMOS", "mnmos_element.xml", "sym_mnmos.xml",
        ("mnmos_port1.xml", "mnmos_port2.xml", "mnmos_port3.xml", "mnmos_port4.xml"),
        720, 324, 180,
    ),
    "MPMOS": ComponentDefinition(
        "MPMOS", "mpmos_element.xml", "sym_mpmos.xml",
        ("mpmos_port1.xml", "mpmos_port2.xml", "mpmos_port3.xml", "mpmos_port4.xml"),
        720, 504, 180,
    ),
    "OPAMP5": ComponentDefinition(
        "OPAMP5", "opamp5_element.xml", "sym_opamp5.xml",
        (
            "opamp5_port1.xml",
            "opamp5_port2.xml",
            "opamp5_port4.xml",
            "opamp5_port5.xml",
            "opamp5_port3.xml",
        ),
        432, 657, 180,
    ),
    "GND": ComponentDefinition(
        "GND", "gnd_element.xml", "sym_gnd.xml", ("gnd_port.xml",),
        234, 594, 135,
    ),
}

DIGITAL_MODEL_KINDS: dict[str, str] = {
    "NOT": "DNOT4",
    "INV": "DNOT4",
    "4069": "DNOT4",
    "4069B": "DNOT4",
    "7404": "DNOT4",
    "74HC04": "DNOT4",
    "AND": "DAND5",
    "AND2": "DAND5",
    "4081": "DAND5",
    "4081B": "DAND5",
    "OR": "DOR5",
    "OR2": "DOR5",
    "4071": "DOR5",
    "4071B": "DOR5",
    "NAND": "DNAND5",
    "NAND2": "DNAND5",
    "NOR": "DNOR5",
    "NOR2": "DNOR5",
    "XOR": "DXOR5",
    "XOR2": "DXOR5",
    "XNOR": "DXNOR5",
    "XNOR2": "DXNOR5",
    "JK": "DJK7",
    "JKFF": "DJK7",
}

DIGITAL_CODE_MODELS: dict[str, str] = {
    "NOT": "d_inverter (rise_delay=1n fall_delay=1n)",
    "INV": "d_inverter (rise_delay=1n fall_delay=1n)",
    "4069": "d_inverter (rise_delay=80n fall_delay=90n)",
    "4069B": "d_inverter (rise_delay=80n fall_delay=90n)",
    "7404": "d_inverter (rise_delay=15n fall_delay=15n)",
    "74HC04": "d_inverter (rise_delay=10n fall_delay=10n)",
    "AND": "d_and (rise_delay=1n fall_delay=1n)",
    "AND2": "d_and (rise_delay=1n fall_delay=1n)",
    "4081": "d_and (rise_delay=90n fall_delay=110n)",
    "4081B": "d_and (rise_delay=90n fall_delay=110n)",
    "OR": "d_or (rise_delay=1n fall_delay=1n)",
    "OR2": "d_or (rise_delay=1n fall_delay=1n)",
    "4071": "d_or (rise_delay=90n fall_delay=115n)",
    "4071B": "d_or (rise_delay=90n fall_delay=115n)",
    "NAND": "d_nand (rise_delay=1n fall_delay=1n)",
    "NAND2": "d_nand (rise_delay=1n fall_delay=1n)",
    "NOR": "d_nor (rise_delay=1n fall_delay=1n)",
    "NOR2": "d_nor (rise_delay=1n fall_delay=1n)",
    "XOR": "d_xor (rise_delay=1n fall_delay=1n)",
    "XOR2": "d_xor (rise_delay=1n fall_delay=1n)",
    "XNOR": "d_xnor (rise_delay=1n fall_delay=1n)",
    "XNOR2": "d_xnor (rise_delay=1n fall_delay=1n)",
    "JK": "d_jkff (clk_delay=1n set_delay=1n reset_delay=1n ic=0 rise_delay=1n fall_delay=1n)",
    "JKFF": "d_jkff (clk_delay=1n set_delay=1n reset_delay=1n ic=0 rise_delay=1n fall_delay=1n)",
}

XSUB16_TERMINAL_NAMES = (
    "R1A", "R2A", "R3A", "R4A", "R5A", "R6A", "R7A", "R8A",
    "R8B", "R7B", "R6B", "R5B", "R4B", "R3B", "R2B", "R1B",
)

NATIVE_MODEL_ALIASES: dict[str, frozenset[str]] = {
    "D": frozenset({"1N4001", "1N4001GP", "D1N4001GP"}),
    "QNPN": frozenset({"2N3904"}),
    "QPNP": frozenset({"2N3906"}),
    "MNMOS": frozenset({"NMOS"}),
    "MPMOS": frozenset({"PMOS"}),
    "OPAMP5": frozenset({"OPAMP5", "IDEALOPAMP"}),
}


@dataclass
class ParsedNetlist:
    components: list[ComponentSpec] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    grounded: bool = False
    subcircuits: dict[str, "SubcircuitDefinition"] = field(default_factory=dict)
    expanded_subcircuits: list[dict[str, Any]] = field(default_factory=list)
    subcircuit_expansion_failures: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class SubcircuitDefinition:
    """An inline SPICE subcircuit available for editable expansion."""

    name: str
    pins: tuple[str, ...]
    parameters: dict[str, str]
    text: str


def _logical_netlist_lines(text: str) -> list[str]:
    """Fold SPICE `+` continuation records into their preceding logical line."""
    logical: list[str] = []
    current: str | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("+") and current is not None:
            current += " " + stripped[1:].lstrip()
            continue
        if current is not None:
            logical.append(current)
        current = stripped
    if current is not None:
        logical.append(current)
    return logical


def _collect_subcircuits(
    logical_lines: list[str],
) -> tuple[dict[str, SubcircuitDefinition], str]:
    """Collect inline definitions and their command-engine dependency library."""
    definitions: dict[str, SubcircuitDefinition] = {}
    library_lines: list[str] = []
    index = 0
    while index < len(logical_lines):
        line = logical_lines[index].strip()
        lower = line.lower()
        if lower.startswith(".subckt"):
            header = line.split()
            if len(header) < 2:
                index += 1
                continue
            name = header[1]
            pins: list[str] = []
            parameter_tokens: list[str] = []
            reading_parameters = False
            for token in header[2:]:
                lowered = token.lower()
                if lowered in {"params:", "param:"} or "=" in token:
                    reading_parameters = True
                    if "=" not in token:
                        continue
                if reading_parameters:
                    parameter_tokens.append(token)
                else:
                    pins.append(token)
            block = [line]
            depth = 1
            index += 1
            while index < len(logical_lines) and depth:
                candidate = logical_lines[index].strip()
                candidate_lower = candidate.lower()
                if candidate_lower.startswith(".subckt"):
                    depth += 1
                elif candidate_lower.startswith(".ends"):
                    depth -= 1
                if candidate and not candidate.startswith(("*", ";", "#")):
                    block.append(candidate.split(";", 1)[0].rstrip())
                index += 1
            text = "\n".join(block)
            definitions[name.lower()] = SubcircuitDefinition(
                name=name,
                pins=tuple(pins),
                parameters=_parse_parameter_assignments(parameter_tokens),
                text=text,
            )
            library_lines.extend(block)
            continue
        if lower.startswith(
            (".model", ".param", ".global", ".options", ".temp", ".func")
        ):
            library_lines.append(line.split(";", 1)[0].rstrip())
        index += 1
    library = "\n".join(line for line in library_lines if line)
    return definitions, library


def _split_subcircuit_instance(
    parts: list[str], definitions: dict[str, SubcircuitDefinition]
) -> tuple[list[str], str, list[str]]:
    """Return nodes, model name, and instance parameters for an X record."""
    model_index: int | None = None
    for index in range(len(parts) - 1, 1, -1):
        if parts[index].lower() in definitions:
            model_index = index
            break
    if model_index is None:
        for index in range(2, len(parts)):
            token = parts[index]
            if token.lower() in {"params:", "param:"} or "=" in token:
                model_index = index - 1
                break
    if model_index is None:
        model_index = len(parts) - 1
    return parts[1:model_index], parts[model_index], parts[model_index + 1 :]


def _parse_parameter_assignments(tokens: list[str]) -> dict[str, str]:
    parameters: dict[str, str] = {}
    for token in tokens:
        if token.lower() in {"params:", "param:"} or "=" not in token:
            continue
        name, value = token.split("=", 1)
        if name:
            parameters[name.lower()] = value
    return parameters


def _substitute_parameters(text: str, parameters: dict[str, str]) -> str:
    rendered = text
    for _ in range(4):
        previous = rendered
        for name, value in parameters.items():
            rendered = re.sub(
                rf"\{{\s*{re.escape(name)}\s*\}}",
                value,
                rendered,
                flags=re.IGNORECASE,
            )
        def substitute_in_expression(match: re.Match[str]) -> str:
            expression = match.group(1)
            for name, value in parameters.items():
                expression = re.sub(
                    rf"(?<![A-Za-z0-9_.]){re.escape(name)}(?![A-Za-z0-9_.])",
                    f"({value})",
                    expression,
                    flags=re.IGNORECASE,
                )
            return "{" + expression + "}"

        rendered = re.sub(r"\{([^{}]+)\}", substitute_in_expression, rendered)
        if rendered == previous:
            break
    return rendered


def _expanded_refdes(refdes: str, prefix: str) -> str:
    raw_suffix = re.sub(r"[^A-Za-z0-9]", "", f"{prefix}{refdes[1:]}")
    ascii_letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    letters = "".join(char for char in raw_suffix if char in ascii_letters)[:14]
    digits = "".join(char for char in raw_suffix if char.isdigit())[:4]
    digest = str(
        int(uuid.uuid5(uuid.NAMESPACE_OID, raw_suffix).hex[:8], 16) % 1_000_000
    )
    # Multisim normalizes names such as RXU1__DOM or RXU1DOM back to RXU1.
    # Keeping all alphabetic characters before the final numeric suffix makes
    # the generated reference stable across XML encode/open/export round-trips.
    return f"{refdes[0]}{letters}{digits}{digest.zfill(6)}"


def _expanded_node(
    node: str,
    pins: dict[str, str],
    prefix: str,
    global_nodes: frozenset[str],
) -> str:
    normalized, _ = _normalize_net(node)
    if normalized == "0" or normalized in global_nodes:
        return normalized
    if normalized in pins:
        return pins[normalized]
    safe = re.sub(r"[^A-Za-z0-9_]", "_", normalized)
    return f"{prefix}__{safe}"


def _rewrite_expression_nodes(
    text: str,
    pins: dict[str, str],
    prefix: str,
    global_nodes: frozenset[str],
) -> str:
    def replace(match: re.Match[str]) -> str:
        function = match.group(1)
        arguments = [item.strip() for item in match.group(2).split(",")]
        if function.lower() == "v":
            mapped = [
                _expanded_node(item, pins, prefix, global_nodes)
                for item in arguments
            ]
        else:
            mapped = [_expanded_refdes(item, prefix) for item in arguments]
        return f"{function}({','.join(mapped)})"

    return re.sub(r"\b([VI])\(([^()]+)\)", replace, text, flags=re.IGNORECASE)


_EXPANDABLE_NODE_COUNTS: dict[str, int] = {
    "R": 2,
    "C": 2,
    "L": 2,
    "V": 2,
    "I": 2,
    "E": 4,
    "G": 4,
    "F": 2,
    "H": 2,
    "B": 2,
    "T": 4,
    "D": 2,
    "Q": 3,
    "M": 4,
    "S": 4,
    "J": 3,
    "Z": 3,
    "W": 2,
    "O": 4,
    "U": 3,
}


def _expand_subcircuit_instance(
    node_tokens: list[str],
    instance_parameters: list[str],
    definition: SubcircuitDefinition,
    definitions: dict[str, SubcircuitDefinition],
    *,
    prefix: str,
    global_parameters: dict[str, str],
    global_nodes: frozenset[str],
    function_names: frozenset[str],
    depth: int = 0,
) -> tuple[list[str] | None, str | None]:
    if depth > 16:
        return None, "subcircuit nesting exceeds 16 levels"
    if len(node_tokens) != len(definition.pins):
        return (
            None,
            f"{definition.name} expects {len(definition.pins)} pins, "
            f"received {len(node_tokens)}",
        )
    parameters = dict(global_parameters)
    parameters.update(definition.parameters)
    parameters.update(_parse_parameter_assignments(instance_parameters))
    pins = {
        pin.lower(): _normalize_net(node)[0]
        for pin, node in zip(definition.pins, node_tokens)
    }
    body = _logical_netlist_lines(definition.text)[1:-1]
    expanded: list[str] = []
    for raw_line in body:
        line = raw_line.strip()
        if not line or line.startswith(("*", ";", "#")):
            continue
        lowered = line.lower()
        if lowered.startswith(".param"):
            local = _parse_parameter_assignments(
                _substitute_parameters(line, parameters).split()[1:]
            )
            parameters.update(local)
            continue
        if lowered.startswith(".model"):
            continue
        if lowered.startswith((".if", ".elseif", ".else", ".endif")):
            return None, "conditional .if model blocks are not yet expandable"
        if lowered.startswith("."):
            return None, f"directive {line.split()[0]} is not expandable"

        substituted = _substitute_parameters(line, parameters)
        referenced_function = next(
            (
                name
                for name in function_names
                if re.search(
                    rf"(?<![A-Za-z0-9_.]){re.escape(name)}\s*\(",
                    substituted,
                    flags=re.IGNORECASE,
                )
            ),
            None,
        )
        if referenced_function:
            return None, f".func call {referenced_function!r} is not expandable"
        parts = substituted.split()
        if not parts:
            continue
        kind = parts[0][0].upper()
        if kind == "X":
            child_nodes, child_model, child_parameters = _split_subcircuit_instance(
                parts, definitions
            )
            child = definitions.get(child_model.lower())
            if child is None:
                return None, f"nested subcircuit {child_model!r} is not defined inline"
            mapped_nodes = [
                _expanded_node(node, pins, prefix, global_nodes)
                for node in child_nodes
            ]
            child_prefix = re.sub(
                r"[^A-Za-z0-9_]", "_", f"{prefix}__{parts[0]}"
            )
            child_lines, error = _expand_subcircuit_instance(
                mapped_nodes,
                child_parameters,
                child,
                definitions,
                prefix=child_prefix,
                global_parameters=global_parameters,
                global_nodes=global_nodes,
                function_names=function_names,
                depth=depth + 1,
            )
            if child_lines is None:
                return None, error
            expanded.extend(child_lines)
            continue
        if kind == "K":
            if len(parts) != 4:
                return None, f"unsupported coupled-inductor record: {line}"
            parts[0] = _expanded_refdes(parts[0], prefix)
            parts[1] = _expanded_refdes(parts[1], prefix)
            parts[2] = _expanded_refdes(parts[2], prefix)
            expanded.append(" ".join(parts))
            continue
        node_count = _EXPANDABLE_NODE_COUNTS.get(kind)
        if node_count is None or len(parts) < node_count + 2:
            return None, f"unsupported subcircuit device record: {line}"
        if kind != "B" and ("{" in substituted or "}" in substituted):
            return None, f"parameter expression is not expandable: {line}"
        parts[0] = _expanded_refdes(parts[0], prefix)
        for index in range(1, node_count + 1):
            parts[index] = _expanded_node(
                parts[index], pins, prefix, global_nodes
            )
        if kind in {"F", "H", "W"} and len(parts) > 3:
            parts[3] = _expanded_refdes(parts[3], prefix)
        remainder = " ".join(parts[node_count + 1 :])
        if remainder:
            remainder = _rewrite_expression_nodes(
                remainder, pins, prefix, global_nodes
            )
            parts = parts[: node_count + 1] + remainder.split()
        expanded.append(" ".join(parts))
    return expanded, None


def _expand_top_level_subcircuits(
    logical_lines: list[str], definitions: dict[str, SubcircuitDefinition]
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, str]]]:
    global_parameters: dict[str, str] = {}
    global_nodes: set[str] = set()
    function_names: set[str] = set()
    context_depth = 0
    for line in logical_lines:
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith(".subckt"):
            context_depth += 1
            continue
        if lowered.startswith(".ends"):
            context_depth = max(0, context_depth - 1)
            continue
        if context_depth:
            continue
        if lowered.startswith(".param"):
            global_parameters.update(
                _parse_parameter_assignments(stripped.split()[1:])
            )
        elif lowered.startswith(".global"):
            global_nodes.update(
                _normalize_net(item)[0] for item in stripped.split()[1:]
            )
        elif lowered.startswith(".func"):
            match = re.match(
                r"(?i)^\.func\s+([A-Za-z_][A-Za-z0-9_.]*)\s*\(",
                stripped,
            )
            if match:
                function_names.add(match.group(1).lower())

    rendered: list[str] = []
    expanded_records: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    in_subcircuit = 0
    for line in logical_lines:
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith(".subckt"):
            in_subcircuit += 1
            continue
        if lowered.startswith(".ends"):
            in_subcircuit = max(0, in_subcircuit - 1)
            continue
        if in_subcircuit:
            continue
        parts = stripped.split()
        if parts and parts[0][0].upper() == "X":
            nodes, model, parameters = _split_subcircuit_instance(parts, definitions)
            definition = definitions.get(model.lower())
            if definition is not None:
                prefix = re.sub(r"[^A-Za-z0-9_]", "_", parts[0])
                expanded, error = _expand_subcircuit_instance(
                    nodes,
                    parameters,
                    definition,
                    definitions,
                    prefix=prefix,
                    global_parameters=global_parameters,
                    global_nodes=frozenset(global_nodes),
                    function_names=frozenset(function_names),
                )
                if expanded is not None:
                    rendered.extend(expanded)
                    expanded_records.append(
                        {
                            "refdes": parts[0],
                            "model": definition.name,
                            "components": len(expanded),
                        }
                    )
                    continue
                failures.append(
                    {"refdes": parts[0], "model": definition.name, "reason": str(error)}
                )
        rendered.append(stripped)
    return rendered, expanded_records, failures


def _load_template(name: str) -> ET.Element:
    for root in template_search_paths():
        path = root / name
        if path.is_file():
            return ET.parse(str(path)).getroot()
    searched = ", ".join(str(path / name) for path in template_search_paths())
    raise FileNotFoundError(
        "Missing local schematic template. Generate a component pack with "
        "tools/bootstrap_local_component_pack.py and set "
        f"{TEMPLATE_PACK_ENV}. Searched: {searched}"
    )


def template_search_paths() -> list[Path]:
    """Return the trusted server-side local-pack overlay and package fallback."""
    paths: list[Path] = []
    override = os.environ.get(TEMPLATE_PACK_ENV)
    if override:
        paths.append(Path(override).expanduser().resolve())
    local_only = os.environ.get(TEMPLATE_ONLY_ENV, "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not (override and local_only):
        paths.append(TEMPLATE_DIR)
    return paths


def _deepcopy(element: ET.Element) -> ET.Element:
    return copy.deepcopy(element)


def _clear(element: ET.Element) -> None:
    for child in list(element):
        element.remove(child)


def _asc(value: str) -> str:
    return f"&ASC{value}"


def _normalize_net(name: str) -> tuple[str, str]:
    stripped = name.strip()
    if stripped.lower() in {"0", "gnd"}:
        return "0", "0"
    return stripped.lower(), stripped


def parse_spice_value(token: str) -> tuple[float, str]:
    """Return (ohms/volts, display token) from a SPICE value like 4.7k."""
    raw = token.strip()
    lowered = raw.lower()
    if lowered.endswith("v"):
        lowered = lowered[:-1]
    match = _VALUE_RE.match(lowered)
    if not match:
        raise ValueError(f"Unsupported SPICE value: {token!r}")
    number = float(match.group(1))
    suffix = match.group(2)
    if suffix == "m" and match.group(1) in {"1", "10", "100"} and len(match.group(1)) <= 3:
        pass
    scale = _SUFFIX_SCALE.get(suffix)
    if scale is None:
        raise ValueError(f"Unsupported SPICE value suffix: {suffix!r}")
    return number * scale, raw


def parse_netlist(text: str) -> ParsedNetlist:
    text = expand_component_adapters(text)
    parsed = ParsedNetlist()
    logical_lines = _logical_netlist_lines(text)
    subcircuits, subcircuit_library = _collect_subcircuits(logical_lines)
    parsed.subcircuits = subcircuits
    parse_lines, expanded_records, expansion_failures = _expand_top_level_subcircuits(
        logical_lines, subcircuits
    )
    parsed.expanded_subcircuits = expanded_records
    parsed.subcircuit_expansion_failures = expansion_failures
    model_types: dict[str, str] = {}
    model_definitions: dict[str, str] = {}
    for raw_line in logical_lines:
        parts = raw_line.strip().split()
        if len(parts) >= 3 and parts[0].lower() == ".model":
            model_types[parts[1].lower()] = parts[2].split("(", 1)[0].upper()
            model_definitions[parts[1].lower()] = " ".join(parts[2:])

    in_subcircuit = 0
    for raw_line in parse_lines:
        line = raw_line.strip()
        if not line or line.startswith(("*", ";")):
            continue
        lower = line.lower()
        if lower.startswith("."):
            if lower.startswith(".subckt"):
                in_subcircuit += 1
                continue
            if lower.startswith(".ends"):
                in_subcircuit = max(0, in_subcircuit - 1)
                continue
            if lower.startswith(".end"):
                break
            continue
        if in_subcircuit:
            continue
        parts = line.split()
        if not parts:
            continue
        refdes = parts[0]
        kind = refdes[0].upper()
        if kind in {"R", "C", "L"} and len(parts) >= 4:
            parsed.components.append(
                ComponentSpec(
                    kind=kind,
                    refdes=refdes,
                    nodes=[_normalize_net(parts[1])[0], _normalize_net(parts[2])[0]],
                    value=parts[3],
                )
            )
        elif kind == "K" and len(parts) == 4:
            parsed.components.append(
                ComponentSpec(
                    kind="K",
                    refdes=refdes,
                    nodes=[],
                    value=parts[3],
                    parameters=[parts[1], parts[2]],
                )
            )
        elif kind in {"V", "I"} and len(parts) >= 4:
            source_tokens = parts[3:]
            scalar_value: str | None = None
            if len(source_tokens) == 1:
                scalar_value = source_tokens[0]
            elif len(source_tokens) == 2 and source_tokens[0].upper() == "DC":
                scalar_value = source_tokens[1]
            source_spec = " ".join(source_tokens)
            parsed.components.append(
                ComponentSpec(
                    kind=kind,
                    refdes=refdes,
                    nodes=[_normalize_net(parts[1])[0], _normalize_net(parts[2])[0]],
                    value=scalar_value,
                    model=None if scalar_value is not None else source_spec,
                )
            )
        elif kind in {"E", "G"} and len(parts) == 6:
            # Linear voltage-controlled sources:
            # E/G name out+ out- control+ control- gain
            parsed.components.append(
                ComponentSpec(
                    kind=kind,
                    refdes=refdes,
                    nodes=[_normalize_net(item)[0] for item in parts[1:5]],
                    value=parts[5],
                )
            )
        elif kind in {"F", "H"} and len(parts) == 5:
            # Linear current-controlled sources. The controlling voltage-source
            # reference is not a physical terminal and is kept as a parameter.
            parsed.components.append(
                ComponentSpec(
                    kind=kind,
                    refdes=refdes,
                    nodes=[_normalize_net(parts[1])[0], _normalize_net(parts[2])[0]],
                    value=parts[4],
                    parameters=[parts[3]],
                )
            )
        elif kind == "B" and len(parts) >= 4:
            expression = " ".join(parts[3:]).strip()
            expression_match = re.match(r"(?i)^([VI])\s*=", expression)
            if expression_match:
                parsed.components.append(
                    ComponentSpec(
                        kind=f"B{expression_match.group(1).upper()}",
                        refdes=refdes,
                        nodes=[
                            _normalize_net(parts[1])[0],
                            _normalize_net(parts[2])[0],
                        ],
                        model=expression,
                    )
                )
            else:
                parsed.unsupported.append(line)
        elif kind == "T" and len(parts) >= 6:
            line_parameters = " ".join(parts[5:]).strip()
            parsed.components.append(
                ComponentSpec(
                    kind="T",
                    refdes=refdes,
                    nodes=[_normalize_net(item)[0] for item in parts[1:5]],
                    model=line_parameters,
                )
            )
        elif kind == "O" and len(parts) >= 6:
            model = parts[5]
            parsed.components.append(
                ComponentSpec(
                    kind="O",
                    refdes=refdes,
                    nodes=[_normalize_net(item)[0] for item in parts[1:5]],
                    model=model,
                    model_definition=model_definitions.get(model.lower()),
                    parameters=parts[6:],
                )
            )
        elif kind == "U" and len(parts) >= 5:
            model = parts[4]
            parsed.components.append(
                ComponentSpec(
                    kind="U",
                    refdes=refdes,
                    nodes=[_normalize_net(item)[0] for item in parts[1:4]],
                    model=model,
                    model_definition=model_definitions.get(model.lower()),
                    parameters=parts[5:],
                )
            )
        elif kind == "D" and len(parts) >= 4:
            parsed.components.append(
                ComponentSpec(
                    kind="D",
                    refdes=refdes,
                    nodes=[_normalize_net(parts[1])[0], _normalize_net(parts[2])[0]],
                    model=parts[3],
                    model_definition=model_definitions.get(parts[3].lower()),
                    parameters=parts[4:],
                )
            )
        elif kind == "Q" and len(parts) >= 5:
            model = parts[4]
            model_type = model_types.get(model.lower(), "")
            variant = (
                "QPNP"
                if model_type == "PNP" or "PNP" in model.upper() or model.upper() in {"2N3906"}
                else "QNPN"
            )
            parsed.components.append(
                ComponentSpec(
                    kind=variant,
                    refdes=refdes,
                    nodes=[_normalize_net(item)[0] for item in parts[1:4]],
                    model=model,
                    model_definition=model_definitions.get(model.lower()),
                    parameters=parts[5:],
                )
            )
        elif kind == "M" and len(parts) >= 6:
            model = parts[5]
            model_type = model_types.get(model.lower(), "")
            variant = (
                "MPMOS"
                if model_type in {"PMOS", "PMOS4"} or "PMOS" in model.upper()
                else "MNMOS"
            )
            parsed.components.append(
                ComponentSpec(
                    kind=variant,
                    refdes=refdes,
                    nodes=[_normalize_net(item)[0] for item in parts[1:5]],
                    model=model,
                    model_definition=model_definitions.get(model.lower()),
                    parameters=parts[6:],
                )
            )
        elif kind == "S" and len(parts) >= 6:
            model = parts[5]
            parsed.components.append(
                ComponentSpec(
                    kind="S",
                    refdes=refdes,
                    nodes=[_normalize_net(item)[0] for item in parts[1:5]],
                    model=model,
                    model_definition=model_definitions.get(model.lower()),
                    parameters=parts[6:],
                )
            )
        elif kind in {"J", "Z"} and len(parts) >= 5:
            model = parts[4]
            model_type = model_types.get(model.lower(), "")
            if kind == "J":
                variant = "JP" if model_type == "PJF" else "JN"
            else:
                variant = "ZP" if model_type in {"PMF", "PMES"} else "ZN"
            parsed.components.append(
                ComponentSpec(
                    kind=variant,
                    refdes=refdes,
                    nodes=[_normalize_net(item)[0] for item in parts[1:4]],
                    model=model,
                    model_definition=model_definitions.get(model.lower()),
                    parameters=parts[5:],
                )
            )
        elif kind == "W" and len(parts) >= 6:
            model = parts[4]
            parsed.components.append(
                ComponentSpec(
                    kind="W",
                    refdes=refdes,
                    nodes=[_normalize_net(parts[1])[0], _normalize_net(parts[2])[0]],
                    model=model,
                    model_definition=model_definitions.get(model.lower()),
                    parameters=[parts[3], *parts[5:]],
                )
            )
        elif kind == "A" and len(parts) >= 4:
            digital_kind = DIGITAL_MODEL_KINDS.get(parts[-1].upper())
            expected_ports = (
                len(COMPONENT_DEFINITIONS[digital_kind].port_templates)
                if digital_kind
                else 0
            )
            if digital_kind and len(parts[1:-1]) == expected_ports:
                parsed.components.append(
                    ComponentSpec(
                        kind=digital_kind,
                        refdes=refdes,
                        nodes=[_normalize_net(item)[0] for item in parts[1:-1]],
                        model=parts[-1],
                    )
                )
            else:
                parsed.unsupported.append(line)
        elif (
            kind == "X"
            and len(parts) >= 5
            and re.fullmatch(r"XFG[A-Za-z0-9_.-]*", refdes, re.IGNORECASE)
            and parts[4].upper() in {"FGEN", "FUNCTION_GENERATOR", "XFG"}
        ):
            parsed.components.append(
                ComponentSpec(
                    kind="XFG3",
                    refdes=refdes,
                    nodes=[_normalize_net(item)[0] for item in parts[1:4]],
                    model="FGEN",
                    parameters=parts[5:],
                )
            )
        elif (
            kind == "X"
            and len(parts) == 8
            and re.fullmatch(r"XSC[A-Za-z0-9_.-]*", refdes, re.IGNORECASE)
            and parts[-1].upper() in {"OSC", "OSCILLOSCOPE", "XSC"}
        ):
            parsed.components.append(
                ComponentSpec(
                    kind="OSC6",
                    refdes=refdes,
                    nodes=[_normalize_net(item)[0] for item in parts[1:-1]],
                    model="OSCILLOSCOPE",
                )
            )
        elif kind == "X" and 4 <= len(parts) <= 66:
            node_tokens, model, instance_parameters = _split_subcircuit_instance(
                parts, subcircuits
            )
            nodes = [_normalize_net(item)[0] for item in node_tokens]
            definition = subcircuits.get(model.lower())
            if definition and len(nodes) != len(definition.pins):
                parsed.unsupported.append(
                    f"{line} [subcircuit {definition.name} expects "
                    f"{len(definition.pins)} pins, received {len(nodes)}]"
                )
                continue
            if definition and 2 <= len(nodes) <= 16:
                parsed.components.append(
                    ComponentSpec(
                        kind="XSUBN",
                        refdes=refdes,
                        nodes=nodes,
                        model=model,
                        model_definition=subcircuit_library,
                        parameters=instance_parameters,
                    )
                )
            elif len(nodes) == 5 and model.upper() in {
                "OPAMP5",
                "IDEALOPAMP",
                "LM741",
                "LM358",
                "LM258",
            }:
                parsed.components.append(
                    ComponentSpec(
                        kind="OPAMP5",
                        refdes=refdes,
                        nodes=nodes,
                        model=model,
                        parameters=instance_parameters,
                    )
                )
            elif 2 <= len(nodes) <= 5:
                parsed.components.append(
                    ComponentSpec(
                        kind=f"XSUB{len(nodes)}",
                        refdes=refdes,
                        nodes=nodes,
                        model=model,
                        model_definition=(
                            subcircuit_library if definition else None
                        ),
                        parameters=instance_parameters,
                    )
                )
            elif 6 <= len(nodes) <= 16:
                parsed.components.append(
                    ComponentSpec(
                        kind="XSUBN",
                        refdes=refdes,
                        nodes=nodes,
                        model=model,
                        model_definition=(
                            subcircuit_library if definition else None
                        ),
                        parameters=instance_parameters,
                    )
                )
            else:
                parsed.unsupported.append(line)
        else:
            parsed.unsupported.append(line)
    parsed.grounded = any(
        net == "0" for comp in parsed.components for net in comp.nodes
    )
    return parsed


def prepare_simulation_netlist(text: str) -> str:
    """Translate the schematic A-device shorthand into executable XSPICE."""
    text = expand_component_adapters(text)
    logical_lines = _logical_netlist_lines(text)
    existing_models: set[str] = set()
    for line in logical_lines:
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower() == ".model":
            existing_models.add(parts[1].upper())

    rendered: list[str] = []
    required_models: dict[str, str] = {}
    for line in logical_lines:
        parts = line.split()
        if (
            len(parts) >= 5
            and not line.startswith(("*", ";"))
            and parts[0][0].upper() == "X"
            and re.fullmatch(r"XFG[A-Za-z0-9_.-]*", parts[0], re.IGNORECASE)
            and parts[4].upper() in {"FGEN", "FUNCTION_GENERATOR", "XFG"}
        ):
            values = _parse_virtual_instrument_parameters(parts[5:])
            positive, common, negative = parts[1:4]
            settings = _generator_settings(values)
            frequency = settings["frequency"]
            amplitude = settings["amplitude"]
            offset = settings["offset"]
            wave = settings["wave"]
            phase = f"2*pi*{frequency:g}*time"
            if wave == "SINE":
                expression = f"{offset:g}+{amplitude:g}*sin({phase})"
            elif wave == "SQUARE":
                period = 1.0 / frequency
                pulse_width = period * settings["duty"] / 100.0
                rise = settings["rise"]
                low = offset - amplitude
                high = offset + amplitude
                stem = re.sub(r"[^A-Za-z0-9_]", "_", parts[0])
                rendered.extend(
                    (
                        f"V__{stem}_P {positive} {common} "
                        f"PULSE({low:g} {high:g} 0 {rise:g} {rise:g} {pulse_width:g} {period:g})",
                        f"V__{stem}_N {negative} {common} "
                        f"PULSE({-low:g} {-high:g} 0 {rise:g} {rise:g} {pulse_width:g} {period:g})",
                    )
                )
                continue
            elif wave == "TRIANGLE":
                expression = (
                    f"{offset:g}+{amplitude:g}*(2/pi)*asin(sin({phase}))"
                )
            else:
                raise ValueError(
                    f"{parts[0]} WAVE must be SINE, SQUARE, or TRIANGLE"
                )
            stem = re.sub(r"[^A-Za-z0-9_]", "_", parts[0])
            rendered.extend(
                (
                    f"B__{stem}_P {positive} {common} V={{{expression}}}",
                    f"B__{stem}_N {negative} {common} V={{-({expression})}}",
                )
            )
            continue
        if (
            len(parts) == 8
            and not line.startswith(("*", ";"))
            and parts[0][0].upper() == "X"
            and re.fullmatch(r"XSC[A-Za-z0-9_.-]*", parts[0], re.IGNORECASE)
            and parts[-1].upper() in {"OSC", "OSCILLOSCOPE", "XSC"}
        ):
            # Virtual instruments load the observed nodes in the editable
            # Multisim design but are not electrical SPICE devices.
            continue
        if len(parts) >= 4 and not line.startswith(("*", ";")) and parts[0][0].upper() == "A":
            alias = parts[-1].upper()
            kind = DIGITAL_MODEL_KINDS.get(alias)
            expected_ports = (
                len(COMPONENT_DEFINITIONS[kind].port_templates) if kind else 0
            )
            nodes = parts[1:-1]
            if kind and len(nodes) == expected_ports:
                if kind in {
                    "DNOT4", "DAND5", "DOR5", "DNAND5", "DNOR5", "DXOR5", "DXNOR5"
                }:
                    if kind == "DNOT4":
                        input_a, output, high, low = nodes
                        condition = f"V({input_a})>((V({high})+V({low}))/2)"
                        expression = f"if({condition},V({low}),V({high}))"
                    else:
                        input_a, input_b, output, high, low = nodes
                        threshold = f"((V({high})+V({low}))/2)"
                        a_high = f"V({input_a})>{threshold}"
                        b_high = f"V({input_b})>{threshold}"
                        high_value = f"V({high})"
                        low_value = f"V({low})"
                        if kind == "DAND5":
                            expression = f"if({a_high},if({b_high},{high_value},{low_value}),{low_value})"
                        elif kind == "DNAND5":
                            expression = f"if({a_high},if({b_high},{low_value},{high_value}),{high_value})"
                        elif kind == "DOR5":
                            expression = f"if({a_high},{high_value},if({b_high},{high_value},{low_value}))"
                        elif kind == "DNOR5":
                            expression = f"if({a_high},{low_value},if({b_high},{low_value},{high_value}))"
                        elif kind == "DXOR5":
                            expression = f"if({a_high},if({b_high},{low_value},{high_value}),if({b_high},{high_value},{low_value}))"
                        else:
                            expression = f"if({a_high},if({b_high},{high_value},{low_value}),if({b_high},{low_value},{high_value}))"
                    line = f"B__{parts[0]} {output} {low} V={{{expression}}}"
                elif kind == "DJK7":
                    j, k, clk, set_node, reset, q, qbar = nodes
                    stem = re.sub(r"[^A-Za-z0-9_]", "_", parts[0])
                    digital_inputs = [
                        f"d_{stem}_j", f"d_{stem}_k", f"d_{stem}_clk",
                        f"d_{stem}_set", f"d_{stem}_reset",
                    ]
                    digital_outputs = [f"d_{stem}_q", f"d_{stem}_qbar"]
                    line = "\n".join(
                        (
                            f"A__{stem}_ADC [{j} {k} {clk} {set_node} {reset}] "
                            f"[{' '.join(digital_inputs)}] MCP_ADC",
                            f"{parts[0]} {' '.join(digital_inputs + digital_outputs)} {alias}",
                            f"A__{stem}_DAC [{' '.join(digital_outputs)}] [{q} {qbar}] MCP_DAC",
                        )
                    )
                    if "MCP_ADC" not in existing_models:
                        required_models["MCP_ADC"] = (
                            "adc_bridge (in_low=2.5 in_high=2.5)"
                        )
                    if "MCP_DAC" not in existing_models:
                        required_models["MCP_DAC"] = (
                            "dac_bridge (out_low=0 out_high=5 out_undef=2.5)"
                        )
                if kind == "DJK7" and alias not in existing_models:
                    required_models[alias] = DIGITAL_CODE_MODELS[alias]
        if line.lower().startswith(".end") and not line.lower().startswith(".ends"):
            rendered.extend(
                f".model {name} {definition}"
                for name, definition in sorted(required_models.items())
            )
            required_models.clear()
        rendered.append(line)
    if required_models:
        rendered.extend(
            f".model {name} {definition}"
            for name, definition in sorted(required_models.items())
        )
    return "\n".join(rendered).rstrip() + "\n"


def _find_main_diagram(root: ET.Element) -> tuple[ET.Element, ET.Element, ET.Element, ET.Element]:
    for diagram in root.iter():
        if diagram.tag.rsplit("}", 1)[-1] != "CIITDiagram":
            continue
        elements = diagram.find("./Elements")
        if elements is None:
            continue
        circuit_item = None
        for child in elements:
            if child.get("Class") == "CiCircuit":
                circuit_item = child
                break
        if circuit_item is None:
            continue
        composite = diagram.find("./Components/CODComposite")
        if composite is None:
            continue
        return diagram, composite, elements, circuit_item
    raise ValueError("Minimal template has no main CIITDiagram with a CiCircuit")


def _placeholder_for(item: ET.Element) -> ET.Element:
    return ET.Element("Item", {"ID": item.get("ID"), "Class": item.get("Class")})


def _symbol_pin_info(symbol_item: ET.Element) -> dict[str, dict[str, Any]]:
    info: dict[str, dict[str, Any]] = {}
    for pin_item in symbol_item.findall(
        "./CIITSymbolComp/Objects/Item[@Class='CIITPinSymbolComp']"
    ):
        pin = pin_item.find("./CIITPinSymbolComp")
        if pin is None:
            continue
        port_id = pin.get("PortID")
        connector_item = pin_item.find(
            "./CIITPinSymbolComp/Objects/Item[@Class='CIITPinConnectorComp']"
        )
        connector = connector_item.find("./CIITPinConnectorComp")
        info[port_id] = {
            "local_x": float(connector.get("ptCenterX")),
            "local_y": float(connector.get("ptCenterY")),
            "connector_id": connector_item.get("ID"),
        }
    return info


def _shift_pin_geometry(pin_item: ET.Element, dx: float, dy: float) -> None:
    """Translate the local drawing geometry inside a cloned symbol pin."""
    pin = pin_item.find("./CIITPinSymbolComp")
    if pin is None:
        return
    for attr, delta in (
        ("OriginalPinNamePositionInSEX", dx),
        ("OriginalPinNamePositionInSEY", dy),
    ):
        if pin.get(attr) is not None:
            pin.set(attr, f"{float(pin.get(attr)) + delta:g}")
    x_attrs = {"ptCenterX", "pt0X", "pt1X", "CenterX", "X"}
    y_attrs = {"ptCenterY", "pt0Y", "pt1Y", "CenterY", "Y"}
    for element in pin.iter():
        if element is pin:
            continue
        for attr in x_attrs:
            if element.get(attr) is not None:
                element.set(attr, f"{float(element.get(attr)) + dx:g}")
        for attr in y_attrs:
            if element.get(attr) is not None:
                element.set(attr, f"{float(element.get(attr)) + dy:g}")
        if element.get("Transformer-M20") is not None:
            element.set(
                "Transformer-M20",
                f"{float(element.get('Transformer-M20')) + dx:g}",
            )
        if element.get("Transformer-M21") is not None:
            element.set(
                "Transformer-M21",
                f"{float(element.get('Transformer-M21')) + dy:g}",
            )


def _make_variable_subcircuit_templates(
    pin_count: int,
) -> tuple[ET.Element, ET.Element, list[ET.Element]]:
    """Create a rectangular native X-model carrier with 2–16 real pins."""
    if not 2 <= pin_count <= 16:
        raise ValueError("Variable subcircuit symbols require 2 to 16 pins")
    # The resistor-network carrier has a genuine 16-terminal X-model interface.
    # Multisim rejects merely appending ports to a fixed five-terminal X model.
    element_item = _deepcopy(_load_template("xsub16_element.xml"))
    symbol_item = _deepcopy(_load_template("sym_djk7.xml"))
    objects = symbol_item.find("./CIITSymbolComp/Objects")
    pin_items = [
        item for item in list(objects) if item.get("Class") == "CIITPinSymbolComp"
    ]
    left_source = min(
        pin_items,
        key=lambda item: float(
            item.find("./CIITPinSymbolComp/Objects/Item/CIITPinConnectorComp").get(
                "ptCenterX"
            )
        ),
    )
    right_source = max(
        pin_items,
        key=lambda item: float(
            item.find("./CIITPinSymbolComp/Objects/Item/CIITPinConnectorComp").get(
                "ptCenterX"
            )
        ),
    )
    for item in pin_items:
        objects.remove(item)

    left_count = math.ceil(pin_count / 2)
    right_count = pin_count - left_count
    rows = max(left_count, right_count)
    start_y = 54.0
    step_y = 18.0
    body_bottom = max(135.0, start_y + (rows - 1) * step_y + 18.0)

    border = objects.find("./Item[@Class='CIITSymbolBorderRect']/CIITSymbolBorderRect")
    if border is not None:
        border.set("pt1Y", f"{body_bottom:g}")
    polygon = objects.find("./Item[@Class='CODPolygonComp']/CODPolygonComp/Points")
    if polygon is not None:
        points = polygon.findall("./Item")
        for point in points:
            if float(point.get("Y")) > 90:
                point.set("Y", f"{body_bottom:g}")

    available_ports: dict[str, ET.Element] = {}
    for port_index in range(1, 17):
        candidate = _load_template(f"xsub16_port{port_index}.xml")
        name = candidate.find("./CiPort").get("LocalName", "").removeprefix("&ASC")
        available_ports[name] = candidate
    dynamic_ports: list[ET.Element] = []
    # Every cloned pin must have a distinct internal object graph before the
    # whole symbol is remapped. Reusing a source pin's IDs makes Multisim
    # dereference the wrong connector and can crash while opening the design.
    clone_ids = IdAllocator(start=850_000_000)
    for index in range(pin_count):
        is_left = index < left_count
        side_index = index if is_left else index - left_count
        source = left_source if is_left else right_source
        pin_item = _deepcopy(source)
        _remap_subtree(pin_item, clone_ids)
        pin = pin_item.find("./CIITPinSymbolComp")
        connector = pin.find(
            "./Objects/Item[@Class='CIITPinConnectorComp']/CIITPinConnectorComp"
        )
        source_x = float(connector.get("ptCenterX"))
        source_y = float(connector.get("ptCenterY"))
        target_x = 27.0 if is_left else 135.0
        target_y = start_y + side_index * step_y
        _shift_pin_geometry(pin_item, target_x - source_x, target_y - source_y)
        pin_name = f"P{index + 1}"
        terminal_name = XSUB16_TERMINAL_NAMES[index]
        port_item = _deepcopy(available_ports[terminal_name])
        pin.set("PortID", port_item.get("CiID"))
        pin.set("PinName", _asc(pin_name))
        pin.set("PinNumber", _asc(str(index + 1)))
        for text_tag in ("CIITPinSymTextCompName", "CIITPinSymTextCompNumber"):
            text_item = pin.find(f"./Objects/Item[@Class='{text_tag}']/{text_tag}")
            if text_item is not None:
                text_item.set(
                    "Output", _asc(pin_name if text_tag.endswith("Name") else str(index + 1))
                )
        objects.append(pin_item)

        dynamic_ports.append(port_item)
    for terminal_name in XSUB16_TERMINAL_NAMES[pin_count:]:
        dynamic_ports.append(_deepcopy(available_ports[terminal_name]))
    return element_item, symbol_item, dynamic_ports


def _make_coupling_templates() -> tuple[ET.Element, ET.Element]:
    """Create a visible, non-terminal K-coupling annotation component."""
    element_item = _deepcopy(_load_template("r_element.xml"))
    symbol_item = _deepcopy(_load_template("sym_r.xml"))
    objects = symbol_item.find("./CIITSymbolComp/Objects")
    for item in list(objects):
        if item.get("Class") == "CIITPinSymbolComp":
            objects.remove(item)
    return element_item, symbol_item


def _link_symbol_port(
    symbol_item: ET.Element,
    old_port_id: str,
    new_port_id: str,
) -> None:
    """Point the symbol pin whose PortID is old_port_id at a new port CiID."""
    for pin_item in symbol_item.findall(
        "./CIITSymbolComp/Objects/Item[@Class='CIITPinSymbolComp']"
    ):
        pin = pin_item.find("./CIITPinSymbolComp")
        if pin is not None and pin.get("PortID") == old_port_id:
            pin.set("PortID", new_port_id)
            return


def _link_symbol_connector(
    symbol_item: ET.Element,
    connector_id: str,
    extpin_id: str,
) -> None:
    """Point the symbol pin connector at the external pin linked to it."""
    for pin_item in symbol_item.findall(
        "./CIITSymbolComp/Objects/Item[@Class='CIITPinSymbolComp']"
    ):
        connector_item = pin_item.find(
            "./CIITPinSymbolComp/Objects/Item[@Class='CIITPinConnectorComp']"
        )
        if connector_item is None or connector_item.get("ID") != connector_id:
            continue
        link = connector_item.find("./CIITPinConnectorComp/ConnectList/Item")
        if link is not None:
            link.set("ID", extpin_id)
        return


def _set_component_value(
    element_item: ET.Element,
    kind: str,
    display_value: str,
    numeric_value: float,
    parameters: list[str] | None = None,
) -> None:
    comp = element_item.find("./CiComponent")
    param_list = comp.find(".//CiaParamList")
    if param_list is not None:
        doubles = param_list.findall("./doubles/Item")
        parameter_items = param_list.findall("./parameters/Item")
        if len(doubles) > 1 and len(parameter_items) > 1:
            doubles[1].set("Value", f"{numeric_value:g}.")
            parameter_items[1].set("Value", _asc(display_value))
    items = comp.findall("./Attributes/Item")
    if kind in {"R", "C", "L"}:
        for index in (41, 42):
            if index < len(items):
                cstring = items[index].find("./CiaCString")
                if cstring is not None:
                    if kind == "R":
                        rendered = f"&UNI{display_value}_uc103a9"
                    elif kind == "L":
                        rendered = f"&UNI{display_value}_uc100b5H"
                    else:
                        rendered = f"&ASC{display_value}F"
                    cstring.set(
                        "String", _asc(display_value) if index == 41 else rendered
                    )
    if kind in {"E", "F", "G", "H"}:
        template = comp.find(".//CiaSpiceTmpltExprt")
        if template is None:
            raise ValueError(f"Native carrier for {kind} has no SPICE template")
        if kind in {"E", "G"}:
            # The four MOS-carrier terminal names are D, G, S, SUB. Here they
            # are deliberately reinterpreted as out+, out-, control+, control-.
            template.set(
                "String",
                _asc(f"{kind.lower()}%p %tD %tG %tS %tSUB {numeric_value:g}"),
            )
        else:
            controlling_source = (parameters or [""])[0]
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", controlling_source):
                raise ValueError(
                    f"{kind} controlled source requires a valid controlling "
                    "voltage-source reference"
                )
            terminal_expr = "%t2 %t1" if kind == "F" else "%t1 %t2"
            template.set(
                "String",
                _asc(
                    f"{kind.lower()}%p {terminal_expr} "
                    f"{controlling_source} {numeric_value:g}"
                ),
            )


def _set_symbol_labels(
    symbol_item: ET.Element,
    kind: str,
    refdes: str,
    display_value: str | None,
) -> None:
    sym = symbol_item.find("./CIITSymbolComp")
    name_item = sym.find(
        "./Objects/Item[@Class='CIITSymTextCompName']/CIITSymTextCompName"
    )
    if name_item is not None:
        name_item.set("Output", _asc(refdes))
    if display_value is not None:
        value_item = sym.find(
            "./Objects/Item[@Class='CIITSymTextCompValue']/CIITSymTextCompValue"
        )
        if value_item is not None:
            if kind == "R":
                value_item.set("Output", f"&UNI{display_value}_uc103a9")
            elif kind == "L":
                value_item.set("Output", f"&UNI{display_value}_uc100b5H")
            elif kind == "C":
                value_item.set("Output", f"&ASC{display_value}F")
            elif kind == "V":
                value_item.set("Output", f"&ASC{display_value}V ")
            elif kind == "I":
                value_item.set("Output", f"&ASC{display_value}A ")
            elif kind in {"E", "F", "G", "H", "BV", "BI", "T"} or kind.startswith("XSUB"):
                value_item.set("Output", _asc(display_value))
            elif kind.startswith("D"):
                value_item.set("Output", _asc(display_value))
            elif kind == "K":
                value_item.set("Output", _asc(f"k={display_value}"))


def _configure_component_semantics(
    element_item: ET.Element,
    spec: ComponentSpec,
) -> None:
    """Apply SPICE behavior which is not represented by a scalar value field."""
    if spec.kind not in {
        "V", "I", "BV", "BI", "T", "XSUB2", "XSUB3", "XSUB4", "XSUB5", "XSUBN",
        "D", "QNPN", "QPNP", "MNMOS", "MPMOS", "S", "JN", "JP", "ZN", "ZP", "W", "K", "O", "U",
        "DNAND5", "DNOR5", "DXOR5", "DXNOR5",
    }:
        return
    if spec.kind in {"DNAND5", "DNOR5", "DXOR5", "DXNOR5"}:
        alias = (spec.model or "").upper()
        code_model = DIGITAL_CODE_MODELS.get(alias)
        if not code_model:
            raise ValueError(f"{spec.refdes} has an unsupported digital model alias")
        model_name = f"MCP_{alias}%p"
        model_definition = _asc(f".MODEL {model_name} {code_model}")
        for collection in element_item.findall("./CiComponent//CiaCollString"):
            items = collection.findall("./strings/Item")
            definition_item = next(
                (
                    item
                    for item in items
                    if item.get("Value", "").upper().startswith("&ASC.MODEL ")
                ),
                None,
            )
            if definition_item is None:
                continue
            match = re.match(
                r"&ASC\.MODEL\s+(\S+)", definition_item.get("Value", ""), re.IGNORECASE
            )
            old_model = match.group(1) if match else ""
            definition_item.set("Value", model_definition)
            for item in items:
                if item.get("Value", "").removeprefix("&ASC") == old_model:
                    item.set("Value", _asc(model_name))
        return
    if spec.kind in {"V", "I"}:
        if not spec.model:
            return
        template = element_item.find("./CiComponent//CiaSpiceTmpltExprt")
        if template is None:
            raise ValueError(f"Native carrier for {spec.kind} has no SPICE template")
        terminals = "%t1 %t2" if spec.kind == "V" else "%t2 %t1"
        template.set(
            "String",
            _asc(f"{spec.kind.lower()}%p {terminals} {spec.model}"),
        )
        return
    if spec.kind == "K":
        if len(spec.parameters) != 2 or not all(
            re.fullmatch(r"L[A-Za-z0-9_.-]+", item, re.IGNORECASE)
            for item in spec.parameters
        ):
            raise ValueError(f"{spec.refdes} requires two valid inductor references")
        coupling, _ = parse_spice_value(spec.value or "")
        rendered = (
            f"k%p {spec.parameters[0]} {spec.parameters[1]} {coupling:g}"
        )
        template = element_item.find("./CiComponent//CiaSpiceTmpltExprt")
        if template is None:
            raise ValueError("Native carrier for K has no SPICE template")
        template.set("String", _asc(rendered))
        for item in element_item.findall("./CiComponent//CiaCollString/strings/Item"):
            value = item.get("Value", "")
            if "%t" in value and value.removeprefix("&ASC").lower().startswith("r%p"):
                item.set("Value", _asc(rendered))
        return
    if spec.kind == "T":
        parameters = (spec.model or "").strip()
        if not parameters or any(char in parameters for char in "\r\n"):
            raise ValueError(f"{spec.refdes} has invalid transmission-line parameters")
        template = element_item.find("./CiComponent//CiaSpiceTmpltExprt")
        if template is None:
            raise ValueError("Native carrier for T has no SPICE template")
        template.set(
            "String",
            _asc(f"t%p %tD %tG %tS %tSUB {parameters}"),
        )
        return
    if spec.kind.startswith("XSUB"):
        model = (spec.model or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", model):
            raise ValueError(f"{spec.refdes} has an invalid subcircuit model name")
        terminals_by_kind = {
            "XSUB2": "%t1 %t2",
            "XSUB3": "%tC %tB %tE",
            "XSUB4": "%tD %tG %tS %tSUB",
            "XSUB5": "%tIN+ %tIN- %tVS+ %tVS- %tOUT",
        }
        template = element_item.find("./CiComponent//CiaSpiceTmpltExprt")
        if template is None:
            raise ValueError(f"Native carrier for {spec.kind} has no SPICE template")
        rendered = (
            f"x%p "
            f"{terminals_by_kind.get(spec.kind, ' '.join(f'%t{name}' for name in XSUB16_TERMINAL_NAMES[:len(spec.nodes)]))} "
            f"{model}"
        )
        instance_parameters = " ".join(spec.parameters).strip()
        if instance_parameters:
            rendered += f" {instance_parameters}"
        template.set("String", _asc(rendered))
        # Native component records cache a second copy of the template in a
        # CiaCollString. Keeping it stale makes variable-pin X carriers enumerate
        # in Multisim but disappear from the exported native netlist.
        for item in element_item.findall("./CiComponent//CiaCollString/strings/Item"):
            value = item.get("Value", "")
            if "%t" in value and value.removeprefix("&ASC").lower().startswith("x%p"):
                item.set("Value", _asc(rendered))
        return
    modeled_terminals = {
        "D": ("d", "%tA %tK"),
        "QNPN": ("q", "%tC %tB %tE"),
        "QPNP": ("q", "%tC %tB %tE"),
        "MNMOS": ("m", "%tD %tG %tS %tSUB"),
        "MPMOS": ("m", "%tD %tG %tS %tSUB"),
        "S": ("s", "%tD %tG %tS %tSUB"),
        "JN": ("j", "%tC %tB %tE"),
        "JP": ("j", "%tC %tB %tE"),
        "ZN": ("z", "%tC %tB %tE"),
        "ZP": ("z", "%tC %tB %tE"),
        "W": ("w", "%t1 %t2"),
        "O": ("o", "%tD %tG %tS %tSUB"),
        "U": ("u", "%tC %tB %tE"),
    }
    if spec.kind in modeled_terminals:
        # Built-in D/Q/M templates remain untouched unless the source netlist
        # supplies an explicit .model. J/Z/S always need an instance template.
        if not spec.model_definition and spec.kind in {
            "D", "QNPN", "QPNP", "MNMOS", "MPMOS"
        }:
            return
        model = (spec.model or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", model):
            raise ValueError(f"{spec.refdes} has an invalid model name")
        prefix, terminals = modeled_terminals[spec.kind]
        instance_parameters = " ".join(spec.parameters).strip()
        template = element_item.find("./CiComponent//CiaSpiceTmpltExprt")
        if template is None:
            raise ValueError(f"Native carrier for {spec.kind} has no SPICE template")
        if spec.model_definition:
            private_model = f"{prefix}mdl%p"
            if spec.kind == "W":
                controlling_source, *switch_state = spec.parameters
                if not re.fullmatch(
                    r"[A-Za-z][A-Za-z0-9_.-]*", controlling_source
                ):
                    raise ValueError(
                        f"{spec.refdes} has an invalid controlling source reference"
                    )
                rendered = f"{prefix}%p {terminals} {controlling_source} {private_model}"
                instance_parameters = " ".join(switch_state)
            else:
                rendered = f"{prefix}%p {terminals} {private_model}"
            if instance_parameters:
                rendered += f" {instance_parameters}"
            rendered += f"  .model {private_model} {spec.model_definition}"
        else:
            if spec.kind == "W":
                controlling_source, *switch_state = spec.parameters
                if not re.fullmatch(
                    r"[A-Za-z][A-Za-z0-9_.-]*", controlling_source
                ):
                    raise ValueError(
                        f"{spec.refdes} has an invalid controlling source reference"
                    )
                rendered = f"{prefix}%p {terminals} {controlling_source} {model}"
                instance_parameters = " ".join(switch_state)
            else:
                rendered = f"{prefix}%p {terminals} {model}"
            if instance_parameters:
                rendered += f" {instance_parameters}"
        template.set("String", _asc(rendered))
        return
    expression = (spec.model or "").strip()
    if not re.match(r"(?i)^[VI]\s*=", expression):
        raise ValueError(f"{spec.refdes} has an invalid behavioral-source expression")
    template = element_item.find("./CiComponent//CiaSpiceTmpltExprt")
    if template is None:
        raise ValueError(f"Native carrier for {spec.kind} has no SPICE template")
    terminals = "%t1 %t2" if spec.kind == "BV" else "%t2 %t1"
    template.set("String", _asc(f"b%p {terminals} {expression}"))


def _make_external_pin(
    refs: ET.Element,
    connector_id: str,
    x: float,
    y: float,
    ids: IdAllocator,
) -> str:
    pin_item = _deepcopy(_load_template("extpin.xml"))
    _remap_subtree(pin_item, ids)
    pin = pin_item.find("./CODPinComp")
    pin.set("CenterX", f"{x:g}")
    pin.set("CenterY", f"{y:g}")
    connect = pin.find("./ConnectList/Item")
    connect.set("ID", connector_id)
    refs.append(pin_item)
    return pin_item.get("ID")


def _orthogonal_path(p1: tuple[float, float], p2: tuple[float, float]) -> list[tuple[float, float]]:
    if p1[0] == p2[0] or p1[1] == p2[1]:
        return [p1, p2]
    # Bend in the free channel between components instead of running the first
    # segment alongside/through the source symbol's bounding box.
    mid_x = (p1[0] + p2[0]) / 2.0
    return [p1, (mid_x, p1[1]), (mid_x, p2[1]), p2]


def _component_placement_order(specs: list[ComponentSpec]) -> list[int]:
    """Return a stable connectivity-first order for grid placement."""
    neighbors: dict[int, set[int]] = {index: set() for index in range(len(specs))}
    node_members: dict[str, list[int]] = {}
    refdes_to_index = {spec.refdes.lower(): index for index, spec in enumerate(specs)}
    for index, spec in enumerate(specs):
        for node in set(spec.nodes):
            if node != "0":
                node_members.setdefault(node, []).append(index)
        if spec.kind == "K":
            for refdes in spec.parameters:
                other = refdes_to_index.get(refdes.lower())
                if other is not None:
                    neighbors[index].add(other)
                    neighbors[other].add(index)
    for members in node_members.values():
        for left in members:
            neighbors[left].update(right for right in members if right != left)

    unvisited = set(range(len(specs)))
    ordered: list[int] = []
    source_kinds = {"V", "I", "BV", "BI", "XFG3"}
    while unvisited:
        source = min(
            unvisited,
            key=lambda index: (specs[index].kind not in source_kinds, index),
        )
        queue = [source]
        unvisited.remove(source)
        while queue:
            current = queue.pop(0)
            ordered.append(current)
            for neighbor in sorted(neighbors[current]):
                if neighbor in unvisited:
                    unvisited.remove(neighbor)
                    queue.append(neighbor)
    return ordered


def _add_placeholder(refs: ET.Element, item: ET.Element) -> None:
    refs.append(_placeholder_for(item))


def _wire_item(named: bool) -> ET.Element:
    return _deepcopy(_load_template("wire_named.xml" if named else "wire.xml"))


def _make_junction_pins(
    refs: ET.Element,
    x: float,
    y: float,
    count: int,
    ids: IdAllocator,
) -> tuple[ET.Element, list[str]]:
    """Create a junction owner plus count member pins at the same point."""
    owner_item = _deepcopy(_load_template("junction_owner.xml"))
    _remap_subtree(owner_item, ids)
    owner = owner_item.find("./CODPinComp")
    owner.set("Transformer-M20", f"{x:g}")
    owner.set("Transformer-M21", f"{y:g}")
    _clear(owner.find("./ConnectList"))
    member_ids: list[str] = []
    for _ in range(count):
        member_item = _deepcopy(_load_template("junction_member.xml"))
        _remap_subtree(member_item, ids)
        member = member_item.find("./CODPinComp")
        member.set("CenterX", f"{x:g}")
        member.set("CenterY", f"{y:g}")
        member.find("./ConnectList/Item").set("ID", owner_item.get("ID"))
        refs.append(member_item)
        owner.find("./ConnectList").append(
            ET.Element("Item", {"ID": member_item.get("ID"), "Class": "CODPinComp"})
        )
        member_ids.append(member_item.get("ID"))
    return owner_item, member_ids


def _remap_subtree(item: ET.Element, ids: IdAllocator) -> None:
    """Assign unique IDs to every element in a copied template subtree.

    References that point at elements inside the same subtree are rewritten to
    the new IDs. References to objects outside the subtree are left untouched
    and must be linked explicitly by the caller.
    """
    old_to_new: dict[str, str] = {}
    for el in item.iter():
        for key in ("ID", "CiID"):
            old = el.get(key)
            if old:
                old_to_new.setdefault(old, ids.next_id())
    for el in item.iter():
        for key, old in list(el.attrib.items()):
            if key in ID_ATTRS and old in old_to_new:
                el.set(key, old_to_new[old])
        for key in GUID_ATTRS:
            if el.get(key):
                el.set(key, ids.next_guid())


def _find_by_tag(root: ET.Element, tag: str) -> ET.Element | None:
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == tag:
            return el
    return None


def _refdes_info(
    refdes: str,
    circuit_name: str,
    file_path: str,
) -> ET.Element:
    """Build the CIRToInfoMap entry Multisim uses for probe RefDes records."""
    refdes_str = f"&ASC!0!0!0{refdes}!0{file_path}!01!0{circuit_name}!0"
    info_item = ET.Element(
        "CIRToInfoMapItem",
        {"CIRKey": _asc(refdes)},
    )
    refdes_info = ET.SubElement(
        info_item,
        "RefDesInfo",
        {
            "Class": "CIITHierRefDesInfo",
            "IRPrefix": _asc("OutProbe"),
            "IRNumber": "-1",
            "Locked": "0",
            "IRSection": "",
            "IRSectionID": "0",
            "SpiceTemplate": "",
        },
    )
    ET.SubElement(
        refdes_info,
        "RefDesInfoData",
        {
            "Class": "CIITHierRefDes",
            "RefDesBufSize": "260",
            "RefDesStrSize": str(len(refdes_str)),
            "RefDesCount": "2",
            "RefDes": refdes_str,
            "SectionBufSize": "0",
            "SectionStrSize": "0",
            "Section": "&ASC(null)",
            "Prefix": _asc("PR"),
            "Number": refdes.removeprefix("PR"),
        },
    )
    ET.SubElement(refdes_info, "RefDesData")
    ET.SubElement(refdes_info, "PinOrderCIR")
    ET.SubElement(refdes_info, "PinOrderIR")
    ET.SubElement(refdes_info, "PinNumbersCIR")
    ET.SubElement(refdes_info, "PinNumbersIR")
    ET.SubElement(refdes_info, "SharedPins")
    return info_item


def _refdes_prefix_usage() -> ET.Element:
    usage = ET.Element(
        "RefDesPrefixUsage",
        {
            "RefDes": _asc("OUTPROBE"),
            "Class": "CIITRefDesPrefixUsage",
            "Prefix": _asc("OutProbe"),
            "NextNumber": "1",
        },
    )
    inner = ET.SubElement(usage, "RefDesPrefixUsage")
    ET.SubElement(
        inner,
        "NumbersUsedVTwo",
        {"NumberUsed": "0"},
    )
    ET.SubElement(usage, "MultisectionUsage")
    return usage


def _probe_trigger(tree_instance: str, probe_id: str) -> ET.Element:
    trigger = ET.Element(
        "InstProbeTriggers",
        {"TreeInstance": tree_instance, "ProbeID": probe_id},
    )
    triggers = ET.SubElement(trigger, "Triggers")
    ET.SubElement(
        triggers,
        "ProbeTriggers",
        {"Class": _asc("CProbeTriggers"), "NumTriggers": "0"},
    ).append(ET.Element("Triggers"))
    return trigger


def _set_probe_comphandle(item: ET.Element, symbol_id: str) -> None:
    """Point every COMPHANDLE_EXT map entry at the probe symbol's new ID."""
    for map_data in item.iter():
        if map_data.get("Key") != "&ASCNI_EWB_COMPHANDLE_EXT":
            continue
        data = map_data.find("./CDataElement")
        if data is not None:
            data.set("Data", symbol_id)


def _pick_probe_point(
    wire_paths: list[list[tuple[float, float]]],
) -> tuple[float, float] | None:
    """Pick a point on an existing wire for a probe symbol center."""
    for points in wire_paths:
        for start, end in zip(points, points[1:]):
            mid_x = (start[0] + end[0]) / 2.0
            mid_y = (start[1] + end[1]) / 2.0
            if min(
                abs(mid_x - start[0]) + abs(mid_y - start[1]),
                abs(end[0] - mid_x) + abs(end[1] - mid_y),
            ) >= 12.0:
                return mid_x, mid_y
    if wire_paths and len(wire_paths[0]) > 1:
        points = wire_paths[0]
        return (
            (points[0][0] + points[-1][0]) / 2.0,
            (points[0][1] + points[-1][1]) / 2.0,
        )
    if wire_paths:
        return wire_paths[0][0]
    return None


def _add_probes(
    root: ET.Element,
    composite: ET.Element,
    elements: ET.Element,
    circuit_item: ET.Element,
    circuit_id: str,
    ids: IdAllocator,
    net_wires: dict[str, list[list[tuple[float, float]]]],
    probe_nets: list[str],
    output_ms14: str,
) -> list[dict[str, Any]]:
    """Insert voltage probes on named nets and register them with Multisim."""
    probes: list[dict[str, Any]] = []
    if not probe_nets:
        return probes

    objects = composite.find("./Objects")
    refs = composite.find("./ReferencedComponents")
    circuit = circuit_item.find("./CiCircuit")
    probe_exts = circuit.find("./ProbeExts")
    if objects is None or refs is None or probe_exts is None:
        return probes

    instruments_data = _find_by_tag(root, "InstrumentsData")
    refdes_container = _find_by_tag(root, "RefDesInfoContainer")
    prefix_map = _find_by_tag(root, "RefDesPrefixUsageMap")
    total_triggers = _find_by_tag(root, "TotalProbeTriggers")
    if (
        instruments_data is None
        or refdes_container is None
        or prefix_map is None
        or total_triggers is None
    ):
        return probes

    cir_to_info = refdes_container.find("./CIRToInfoMap")
    if cir_to_info is None:
        return probes

    circuit_name = str(circuit_item.get("LocalName") or "minimal")
    if circuit_name.startswith("&ASC"):
        circuit_name = circuit_name[4:]
    tree_instance = str(refdes_container.get("CIR") or f"&ASC#1/{circuit_name}:")

    trigger_set = total_triggers.find("./TriggerSet")
    next_probe_id = 1
    if trigger_set is None:
        trigger_set = ET.SubElement(total_triggers, "TriggerSet")
    else:
        try:
            next_probe_id = int(total_triggers.get("NextProbeID") or "1")
        except ValueError:
            next_probe_id = 1

    for index, net in enumerate(probe_nets, start=1):
        refdes = f"PR{index}"
        point = _pick_probe_point(net_wires.get(net, []))
        if point is None:
            continue

        element_item = _deepcopy(_load_template("probe_element.xml"))
        symbol_item = _deepcopy(_load_template("probe_symbol.xml"))
        instrument_item = _deepcopy(_load_template("probe_instrument.xml"))
        _remap_subtree(element_item, ids)
        _remap_subtree(symbol_item, ids)

        element = element_item.find("./CiProbeExtComp")
        symbol = symbol_item.find("./CIITProbeExtComponent")
        element.set("LocalName", _asc(refdes))
        element.set("SymCompID", symbol_item.get("ID"))
        element.set("Circuit", circuit_id)
        symbol.set("CiProbeExtComp", element_item.get("CiID"))
        symbol.set("Transformer-M20", f"{point[0]:g}")
        symbol.set("Transformer-M21", f"{point[1]:g}")

        objects.append(symbol_item)
        elements.append(element_item)
        probe_exts.append(
            ET.Element("Item", {"CiID": element_item.get("CiID")})
        )

        instruments_data.append(instrument_item)
        _set_probe_comphandle(symbol_item, symbol_item.get("ID"))
        _set_probe_comphandle(instrument_item, symbol_item.get("ID"))

        cir_to_info.append(_refdes_info(refdes, circuit_name, output_ms14))
        prefix_map.append(_refdes_prefix_usage())
        trigger_set.append(_probe_trigger(tree_instance, str(next_probe_id)))
        next_probe_id += 1

        probes.append(
            {
                "refdes": refdes,
                "net": net,
                "x": point[0],
                "y": point[1],
                "element_id": element_item.get("CiID"),
                "symbol_id": symbol_item.get("ID"),
            }
        )

    total_triggers.set("NextProbeID", str(next_probe_id))
    return probes


def _parse_virtual_instrument_parameters(parameters: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    allowed = {"WAVE", "FREQ", "AMPLITUDE", "OFFSET", "DUTY", "RISE"}
    for parameter in parameters:
        if "=" not in parameter:
            raise ValueError(
                f"Virtual instrument parameter must use NAME=VALUE: {parameter!r}"
            )
        key, value = parameter.split("=", 1)
        key = key.upper()
        if key not in allowed or not value:
            raise ValueError(f"Unsupported virtual instrument parameter: {parameter!r}")
        values[key] = value
    return values


def _instrument_number(values: dict[str, str], key: str, default: str) -> float:
    number, _ = parse_spice_value(values.get(key, default))
    return number


def _generator_settings(values: dict[str, str]) -> dict[str, Any]:
    wave = values.get("WAVE", "SINE").upper()
    if wave not in {"SINE", "SQUARE", "TRIANGLE"}:
        raise ValueError("WAVE must be SINE, SQUARE, or TRIANGLE")
    frequency = _instrument_number(values, "FREQ", "100")
    amplitude = _instrument_number(values, "AMPLITUDE", "1")
    offset = _instrument_number(values, "OFFSET", "0")
    duty = _instrument_number(values, "DUTY", "50")
    rise = _instrument_number(values, "RISE", "10n")
    if frequency <= 0:
        raise ValueError("FREQ must be greater than zero")
    if amplitude < 0:
        raise ValueError("AMPLITUDE must not be negative")
    if not 0 < duty < 100:
        raise ValueError("DUTY must be between 0 and 100")
    if rise <= 0:
        raise ValueError("RISE must be greater than zero")
    return {
        "wave": wave,
        "frequency": frequency,
        "amplitude": amplitude,
        "offset": offset,
        "duty": duty,
        "rise": rise,
    }


def _add_virtual_instrument_state(
    root: ET.Element,
    spec: ComponentSpec,
    circuit_item: ET.Element,
) -> None:
    """Register a native virtual instrument's front-panel state."""
    instruments_data = _find_by_tag(root, "InstrumentsData")
    if instruments_data is None:
        raise ValueError("The Multisim template has no InstrumentsData container")
    template_name = "xfg3_instrument.xml" if spec.kind == "XFG3" else "osc6_instrument.xml"
    state = _deepcopy(_load_template(template_name))
    circuit_name = str(circuit_item.get("LocalName") or "minimal")
    if circuit_name.startswith("&ASC"):
        circuit_name = circuit_name[4:]
    state.set("CompLongName", _asc(f"{spec.refdes}#1/{circuit_name}:"))
    if spec.kind == "XFG3":
        values = _parse_virtual_instrument_parameters(spec.parameters)
        settings_values = _generator_settings(values)
        wave = settings_values["wave"]
        wave_modes = {"SINE": "0", "SQUARE": "1", "TRIANGLE": "2"}
        if wave not in wave_modes:
            raise ValueError(f"{spec.refdes} WAVE must be SINE, SQUARE, or TRIANGLE")
        settings = {
            "&ASCNI_EWB_WAVE_MODE": wave_modes[wave],
            "&ASCNI_EWB_FREQUENCY_VALUE": f'{settings_values["frequency"]:g}',
            "&ASCNI_EWB_AMPLITUDE_VALUE": f'{settings_values["amplitude"]:g}',
            "&ASCNI_EWB_OFFSET_VALUE": f'{settings_values["offset"]:g}',
            "&ASCNI_EWB_DEPUTY_CYCLE_VALUE": f'{settings_values["duty"]:g}',
            "&ASCNI_EWB_RISETIMESETTING": f'{settings_values["rise"]:g}',
        }
        for element in state.findall(".//Element"):
            key = element.get("Key")
            data = element.find("./CDataElement")
            if key in settings and data is not None:
                data.set("Data", settings[key])
    instruments_data.append(state)


def build_schematic(
    netlist: str,
    output_path: str | Path,
    template_path: str | Path | None = None,
    probe_nets: list[str] | None = None,
) -> dict[str, Any]:
    """Build an editable Multisim XML design from a simple SPICE netlist.

    ``probe_nets`` names the nets that should get voltage probes. When omitted,
    the last non-ground net is probed automatically. Set it to an empty list to
    disable probes.
    """
    parsed = parse_netlist(netlist)
    inductor_refs = {
        spec.refdes.lower() for spec in parsed.components if spec.kind == "L"
    }
    for spec in parsed.components:
        if spec.kind == "K":
            missing = [
                ref for ref in spec.parameters if ref.lower() not in inductor_refs
            ]
            if missing:
                raise ValueError(
                    f"{spec.refdes} references missing inductors: {', '.join(missing)}"
                )
    if template_path is not None:
        root = ET.parse(str(Path(template_path))).getroot()
    else:
        root = _load_template("minimal.ms14.xml")
    # Do not propagate the workstation identity embedded by Multisim into
    # generated/open-source artifacts.
    for element in root.iter():
        if "User" in element.attrib:
            element.set("User", _asc("multisim-mcp"))
        if "OwnerName" in element.attrib:
            element.set("OwnerName", _asc("multisim-mcp"))
    diagram, composite, elements, circuit_item = _find_main_diagram(root)
    objects = composite.find("./Objects")
    refs = composite.find("./ReferencedComponents")
    circuit = circuit_item.find("./CiCircuit")

    _clear(objects)
    _clear(refs)
    for child in list(elements):
        if child is not circuit_item:
            elements.remove(child)
    elements.remove(circuit_item)
    _clear(circuit.find("./Nodes"))
    _clear(circuit.find("./Components"))

    ids = IdAllocator()
    _remap_subtree(circuit_item, ids)

    specs = list(parsed.components)
    if parsed.grounded:
        specs.append(ComponentSpec(kind="GND", refdes="0", nodes=["0"]))

    grid_columns = min(6, max(2, math.ceil(math.sqrt(max(1, len(specs))))))
    grid_origin_x = 36
    grid_origin_y = 117
    grid_step_x = 207
    grid_step_y = 153
    placement_rank = {
        component_index: rank
        for rank, component_index in enumerate(_component_placement_order(specs))
    }
    node_records: dict[str, dict[str, Any]] = {}
    connections: dict[str, list[dict[str, Any]]] = {}
    component_items: list[ET.Element] = []
    port_items: list[ET.Element] = []
    net_wires: dict[str, list[list[tuple[float, float]]]] = {}

    def get_node(name: str) -> dict[str, Any]:
        if name not in node_records:
            template = "node_v0.xml" if name == "0" else "node_named.xml"
            node_item = _deepcopy(_load_template(template))
            _remap_subtree(node_item, ids)
            node = node_item.find("./CiNode")
            node.set("LocalName", _asc(name))
            _clear(node.find("./Ports"))
            node_records[name] = {"item": node_item, "ports": node.find("./Ports")}
        return node_records[name]

    max_component_x = 0.0
    max_component_y = 0.0
    for component_index, spec in enumerate(specs):
        definition = COMPONENT_DEFINITIONS[spec.kind]
        if spec.kind == "XSUBN":
            element_item, symbol_item, component_port_templates = (
                _make_variable_subcircuit_templates(len(spec.nodes))
            )
        elif spec.kind == "K":
            element_item, symbol_item = _make_coupling_templates()
            component_port_templates = []
        else:
            element_item = _deepcopy(_load_template(definition.element_template))
            symbol_item = _deepcopy(_load_template(definition.symbol_template))
            component_port_templates = [
                _load_template(name) for name in definition.port_templates
            ]
        _remap_subtree(element_item, ids)
        _remap_subtree(symbol_item, ids)
        comp = element_item.find("./CiComponent")
        sym = symbol_item.find("./CIITSymbolComp")
        rank = placement_rank[component_index]
        x = grid_origin_x + (rank % grid_columns) * grid_step_x
        y = grid_origin_y + (rank // grid_columns) * grid_step_y
        max_component_x = max(max_component_x, x + 126)
        max_component_y = max(max_component_y, y + 108)
        sym.set("Transformer-M20", f"{x:g}")
        sym.set("Transformer-M21", f"{y:g}")
        comp.set("LocalName", _asc(spec.refdes))
        comp.set("SymCompID", symbol_item.get("ID"))
        sym.set("CiComponent", element_item.get("CiID"))
        component_ports = comp.find("./Ports")
        if component_ports is None:
            component_ports = ET.SubElement(comp, "Ports")
        else:
            _clear(component_ports)
        symbol_display = spec.value
        if (
            (
                spec.kind in {"V", "I", "BV", "BI", "T"}
                or spec.kind.startswith("XSUB")
            )
            and spec.model
        ):
            symbol_display = (
                spec.model if len(spec.model) <= 48 else spec.model[:45] + "..."
            )
        _set_symbol_labels(symbol_item, spec.kind, spec.refdes, symbol_display)

        display_value = spec.value
        numeric_value = None
        if spec.value is not None:
            numeric_value, display_value = parse_spice_value(spec.value)
            _set_component_value(
                element_item,
                spec.kind,
                display_value,
                numeric_value,
                spec.parameters,
            )
        _configure_component_semantics(element_item, spec)
        if spec.kind in {"OSC6", "XFG3"}:
            _add_virtual_instrument_state(root, spec, circuit_item)

        pin_info = _symbol_pin_info(symbol_item)
        objects.append(symbol_item)
        _add_placeholder(refs, symbol_item)
        component_items.append(element_item)

        terminal_ports = [
            (
                port_template,
                spec.nodes[index] if index < len(spec.nodes) else None,
            )
            for index, port_template in enumerate(component_port_templates)
        ]

        for port_template, net_name in terminal_ports:
            old_port_ciid = port_template.get("CiID")
            port_item = _deepcopy(port_template)
            _remap_subtree(port_item, ids)
            port = port_item.find("./CiPort")
            port_id = port_item.get("CiID")
            port.set("Component", element_item.get("CiID"))
            component_ports.append(ET.Element("Item", {"CiID": port_id}))
            _link_symbol_port(symbol_item, old_port_ciid, port_id)
            if net_name is None:
                _clear(port.find("./Nodes"))
                port_items.append(port_item)
                continue
            node = get_node(net_name)
            _clear(port.find("./Nodes"))
            port.find("./Nodes").append(ET.Element("Item", {"CiID": node["item"].get("CiID")}))
            node["ports"].append(ET.Element("Item", {"CiID": port_id}))
            pin = pin_info.get(old_port_ciid)
            # Some native digital devices expose VDD/VSS as logical ports but
            # intentionally have no drawable connector. Preserve their node
            # membership without inventing a visible wire endpoint.
            if pin is None:
                port_items.append(port_item)
                continue
            connections.setdefault(net_name, []).append(
                {
                    "x": x + pin["local_x"],
                    "y": y + pin["local_y"],
                    "connector_id": pin["connector_id"],
                    "port_id": port_id,
                    "symbol": symbol_item,
                }
            )
            port_items.append(port_item)

    node_items = [record["item"] for record in node_records.values()]
    for item in component_items + port_items + node_items:
        elements.append(item)
    elements.append(circuit_item)

    for name, record in node_records.items():
        circuit.find("./Nodes").append(
            ET.Element("Item", {"CiID": record["item"].get("CiID")})
        )
    for spec in specs:
        element_item = next(
            item
            for item in component_items
            if item.find("./CiComponent").get("LocalName") == _asc(spec.refdes)
        )
        circuit.find("./Components").append(
            ET.Element("Item", {"CiID": element_item.get("CiID")})
        )

    for name, conns in connections.items():
        if len(conns) < 2:
            continue
        node_record = node_records[name]
        for conn in conns:
            conn["extpin_id"] = _make_external_pin(
                refs, conn["connector_id"], conn["x"], conn["y"], ids
            )
            _link_symbol_connector(conn["symbol"], conn["connector_id"], conn["extpin_id"])

        node_text_item = _deepcopy(_load_template("nodetext.xml"))
        _remap_subtree(node_text_item, ids)
        node_text = node_text_item.find("./CODNodeTextComp")
        mid_x = sum(c["x"] for c in conns) / len(conns) + 3
        mid_y = sum(c["y"] for c in conns) / len(conns) - 6
        node_text.set("Transformer-M20", f"{mid_x:g}")
        node_text.set("Transformer-M21", f"{mid_y:g}")
        _clear(node_text.find("./Links"))

        def add_wire(start: dict[str, Any], end: dict[str, Any], end_id: str) -> None:
            wire_item = _wire_item(named=True)
            _remap_subtree(wire_item, ids)
            wire = wire_item.find("./CIITLinkComp")
            wire.set("Connect1", start["extpin_id"])
            wire.set("Connect2", end_id)
            wire.set("Node", node_record["item"].get("CiID"))
            wire.set("NodeText", node_text_item.get("ID"))
            points = wire.find("./Points")
            _clear(points)
            for px, py in _orthogonal_path((start["x"], start["y"]), (end["x"], end["y"])):
                points.append(ET.Element("Item", {"X": f"{px:g}", "Y": f"{py:g}"}))
            modifier = wire.find("./ElectricalObject/ModifierInfo/Element")
            value_item = modifier.find("./Item")
            if name == "0":
                modifier.set("NetModifier", "&ASCNI_EWB_NET_AUTONAMED")
                value_item.set("Value", "")
            else:
                modifier.set("NetModifier", "&ASCNI_EWB_NET_NAME")
                value_item.set("Value", _asc(name))
            objects.append(wire_item)
            _add_placeholder(refs, wire_item)
            wire_items.append(wire_item)
            net_wires.setdefault(name, []).append(
                [(px, py) for px, py in _orthogonal_path((start["x"], start["y"]), (end["x"], end["y"]))]
            )

        wire_items: list[ET.Element] = []
        if len(conns) == 2:
            first, second = conns
            add_wire(first, second, second["extpin_id"])
        else:
            jx = sum(c["x"] for c in conns) / len(conns)
            jy = sum(c["y"] for c in conns) / len(conns)
            owner_item, member_ids = _make_junction_pins(refs, jx, jy, len(conns), ids)
            objects.append(owner_item)
            for conn, member_id in zip(conns, member_ids):
                add_wire(conn, {"x": jx, "y": jy}, member_id)

        links = node_text.find("./Links")
        for wire_item in wire_items:
            links.append(ET.Element("Item", {"ID": wire_item.get("ID"), "Class": "CIITLinkComp"}))
        objects.append(node_text_item)
        _add_placeholder(refs, node_text_item)

    if probe_nets is None:
        probe_nets = [
            name for name in node_records if name != "0"
        ][-1:]
    else:
        probe_nets = [
            _normalize_net(name)[0]
            for name in probe_nets
            if _normalize_net(name)[0] != "0"
        ]
    probes = _add_probes(
        root,
        composite,
        elements,
        circuit_item,
        circuit_item.get("CiID"),
        ids,
        net_wires,
        probe_nets,
        str(Path(output_path).with_suffix(".ms14")),
    )

    model_warnings: list[str] = [
        f"{item['refdes']}: inline subcircuit {item['model']!r} was expanded into "
        f"{item['components']} editable primitive components"
        for item in parsed.expanded_subcircuits
    ]
    model_warnings.extend(
        f"{item['refdes']}: inline subcircuit {item['model']!r} could not be "
        f"expanded for editable simulation: {item['reason']}"
        for item in parsed.subcircuit_expansion_failures
    )
    for spec in parsed.components:
        aliases = NATIVE_MODEL_ALIASES.get(spec.kind)
        if (
            aliases
            and spec.model
            and not spec.model_definition
            and spec.model.upper() not in aliases
        ):
            model_warnings.append(
                f"{spec.refdes}: requested model {spec.model!r} is represented by "
                f"the native {spec.kind} template model"
            )
        if (
            spec.kind in {"MNMOS", "MPMOS"}
            and spec.parameters
            and not spec.model_definition
        ):
            model_warnings.append(
                f"{spec.refdes}: MOS instance parameters are used by command-engine "
                "simulation but are not yet written into the editable symbol"
            )
        if spec.kind in {"S", "JN", "JP", "ZN", "ZP", "W", "O", "U"}:
            model_note = (
                "the supplied .model is embedded per instance"
                if spec.model_definition
                else "the referenced model body is not present in the source netlist"
            )
            model_warnings.append(
                f"{spec.refdes}: {spec.kind} uses a verified generic carrier symbol; "
                f"{model_note}"
            )
        if spec.kind.startswith("XSUB"):
            if spec.model_definition:
                model_warnings.append(
                    f"{spec.refdes}: generic {len(spec.nodes)}-terminal subcircuit "
                    f"{spec.model!r} is shown as a carrier block; its model body is "
                    "retained only for command-engine simulation"
                )
            else:
                model_warnings.append(
                    f"{spec.refdes}: generic {len(spec.nodes)}-terminal subcircuit "
                    f"{spec.model!r} is shown as a carrier block; its model body is "
                    "not present in the source netlist"
                )
        if spec.kind in set(DIGITAL_MODEL_KINDS.values()):
            model_warnings.append(
                f"{spec.refdes}: {spec.kind} uses a native Multisim digital model; "
                "open/export and timing data are verified; symbol artwork remains preview maturity"
            )

    new_circuit_id = circuit_item.get("CiID")
    for el in root.iter():
        if "Circuit" in el.attrib:
            el.set("Circuit", new_circuit_id)

    # Keep larger generated circuits on the printable/exported page instead of
    # silently clipping later grid rows or rightmost component symbols.
    diagram.set(
        "PageWidth",
        f"{max(float(diagram.get('PageWidth') or 0), (max_component_x + 90) / 90):g}",
    )
    diagram.set(
        "PageHeight",
        f"{max(float(diagram.get('PageHeight') or 0), (max_component_y + 90) / 90):g}",
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    tree = ET.ElementTree(root)
    tree.write(str(output_path), encoding="utf-8", xml_declaration=True)

    if parsed.subcircuit_expansion_failures:
        editable_model_status = (
            "partial" if parsed.expanded_subcircuits else "carrier_only"
        )
    elif parsed.expanded_subcircuits:
        editable_model_status = "complete"
    else:
        editable_model_status = "not_applicable"

    return {
        "xml": str(output_path),
        "components": [
            {
                "refdes": spec.refdes,
                "kind": spec.kind,
                "nodes": spec.nodes,
                "value": spec.value,
                "model": spec.model,
                "parameters": spec.parameters,
            }
            for spec in specs
        ],
        "nets": sorted(node_records),
        "grounded": parsed.grounded,
        "subcircuits": [
            {"name": item.name, "pins": list(item.pins)}
            for item in parsed.subcircuits.values()
        ],
        "expanded_subcircuits": parsed.expanded_subcircuits,
        "subcircuit_expansion_failures": parsed.subcircuit_expansion_failures,
        "editable_model_coverage": {
            "status": editable_model_status,
            "expanded_instances": len(parsed.expanded_subcircuits),
            "carrier_only_instances": len(parsed.subcircuit_expansion_failures),
        },
        "unsupported": parsed.unsupported,
        "model_warnings": model_warnings,
        "probes": probes,
        "counts": {
            "components": len(component_items),
            "ports": len(port_items),
            "nodes": len(node_items),
            "symbols": len(objects.findall("./Item[@Class='CIITSymbolComp']")),
            "wires": len(objects.findall("./Item[@Class='CIITLinkComp']")),
            "probes": len(probes),
        },
    }


__all__ = [
    "COMPONENT_DEFINITIONS",
    "DIGITAL_MODEL_KINDS",
    "TEMPLATE_PACK_ENV",
    "TEMPLATE_ONLY_ENV",
    "NATIVE_MODEL_ALIASES",
    "ComponentDefinition",
    "ComponentSpec",
    "ParsedNetlist",
    "SubcircuitDefinition",
    "build_schematic",
    "parse_netlist",
    "parse_spice_value",
    "prepare_simulation_netlist",
    "template_search_paths",
]
