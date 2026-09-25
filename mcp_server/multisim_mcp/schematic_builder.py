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

from multisim_mcp.layout_validation import validate_schematic_geometry
from multisim_mcp.orthogonal_routing import route_pins, junction_point, pin_escape

from multisim_mcp.component_adapters import expand_component_adapters
from multisim_mcp.native_xml import parse_native_xml, write_native_xml


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_PACK_ENV = "MULTISIM_MCP_TEMPLATE_DIR"
TEMPLATE_ONLY_ENV = "MULTISIM_MCP_TEMPLATE_ONLY"
NATIVE_OPAMP_MODELS = {"LM324AJ": "LM158_4"}

# Native carriers whose SPICE template ends in the model placeholder `%m`.
# Each one needs its CiModel object embedded in the generated .ms14, otherwise
# Multisim exports an empty `%m` (see the long note in build_schematic_xml).
NATIVE_MODEL_CARRIERS = frozenset(
    {"TIMER8", "CD4017", "DFF8", "OPAMP5", "LM324AJ", "XFG3", "OSC6"}
)

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
    # SWB = a voltage-controlled SPST switch used by the v7 LED boards.
    #
    # HISTORY / WHY IT IS NOT THE NATIVE NI SYMBOL ANY MORE
    # ----------------------------------------------------
    # The first attempt carried the extracted native
    # VOLTAGE_CONTROLLED_SPST_BOUNCE part (swb_element/sym_swb/swb_port*, whose
    # SPICE template is `x%p ... SWBOUNCE_%p` + a .subckt with an XSPICE
    # triggered_pwl A-device).  Multisim deleted that whole component on
    # re-open, which surfaced as
    #   RuntimeError: Multisim round-trip topology mismatch: S1, S2, S3, ...
    # and, one step earlier, as
    #   Error: RefDes 's1', element 'ss1': Unable to parse parameter name
    # (the MOSFET carrier emitted `s%p %tD %tG %tS %tSUB SWB` plus MOSFET
    # geometry params L/W/AD/AS/PD/PS/NRD/NRS, which a switch card rejects).
    #
    # So the carrier is now built on the mnmos skeleton that v6 proved Multisim
    # accepts, with the SPICE card swapped to a plain voltage-controlled
    # switch:   s%p %tD %tG %tS %tSUB smdl%p
    #           .model smdl%p SW(RON=0.01 ROFF=1e9 VT=3 VH=0.5)
    # Trade-off: it draws as the generic 4-terminal symbol rather than a
    # switch glyph.  Correct simulation beats correct glyph; the glyph can be
    # swapped by hand in the GUI later.
    "SWB": ComponentDefinition(
        "SWB", "swb_element.xml", "sym_mnmos.xml",
        (
            "mnmos_port1.xml",
            "mnmos_port2.xml",
            "mnmos_port3.xml",
            "mnmos_port4.xml",
        ),
        900, 1017, 180, "switch",
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
    # Local-only LM324AJ section A. A separate kind prevents a real vendor
    # device from inheriting ideal OPAMP5 semantics.
    #
    # Port order follows the part's own pin table, NOT the SPICE template.
    # The extracted LM324AJ carrier carries the full quad pin table in
    # CiaCollString[39]/[40]:
    #     names : 1IN+ 1IN- VS- VS+ 1OUT 2IN+ 2IN- VS- VS+ 2OUT 3IN+ ...
    #     pins  : 3    2    11   4   1    5    6    11   4    7    10  ...
    # so section A is (3,2,11,4,1) == (1IN+, 1IN-, VS-, VS+, 1OUT).
    # Multisim's netlister walks that table, and its own SaveAs rewrites our
    # <Ports> list into this order, so the builder must emit it directly.
    # Getting this wrong is what makes the exporter reject the instance with
    #   Error: RefDes 'u41', element 'xu41_a':
    #          Invalid subckt definition name 'lm158_4__opamp__1'
    # even though the CiModel object and its card are byte-identical to NI's
    # own working sample (samples/Non-InvertingOpAmp.ms14).
    "LM324AJ": ComponentDefinition(
        "LM324AJ", "lm324aj_element.xml", "sym_lm324aj.xml",
        ("lm324aj_port1.xml", "lm324aj_port2.xml", "lm324aj_port5.xml",
         "lm324aj_port4.xml", "lm324aj_port3.xml"), 432, 657, 180,
    ),
    # Verified native LM555CN carrier. The files are intentionally supplied
    # by the user-local component pack, not bundled with this open-source
    # project. The order below is the terminal order in the extracted
    # Multisim SPICE template: GND, TRI, OUT, RST, CON, THR, DIS, VCC.
    "TIMER8": ComponentDefinition(
        "TIMER8", "timer8_element.xml", "sym_timer8.xml",
        (
            "timer8_port3.xml", "timer8_port7.xml", "timer8_port4.xml",
            "timer8_port5.xml", "timer8_port1.xml", "timer8_port6.xml",
            "timer8_port2.xml", "timer8_port8.xml",
        ),
        432, 837, 180, "native-timer",
    ),
    # Verified native CD4017BD_5V carrier (CMOS_5V decade Johnson counter).
    # Supplied by the user-local pack, never bundled.  Terminal order in the
    # netlist is: CP0 ~CP1 MR VDD VSS O0 O1 O2 O3 O4 O5 O6 O7 O8 O9 ~O5-9
    "CD4017": ComponentDefinition(
        "CD4017", "cd4017_element.xml", "sym_cd4017.xml",
        (
            "cd4017_port1.xml", "cd4017_port4.xml", "cd4017_port14.xml",
            "cd4017_port15.xml", "cd4017_port16.xml", "cd4017_port2.xml",
            "cd4017_port3.xml", "cd4017_port5.xml", "cd4017_port6.xml",
            "cd4017_port7.xml", "cd4017_port8.xml", "cd4017_port10.xml",
            "cd4017_port11.xml", "cd4017_port12.xml", "cd4017_port13.xml",
            "cd4017_port9.xml",
        ),
        432, 837, 180, "native-counter",
    ),
    # Verified native 7474N single-section carrier. The extracted component
    # exposes the A-section's eight logical pins; the local pack must be
    # derived from the user's licensed QuizShowVariants sample.
    "DFF8": ComponentDefinition(
        "DFF8", "dff8_element.xml", "sym_dff8.xml",
        (
            "dff8_port2.xml", "dff8_port5.xml", "dff8_port4.xml",
            "dff8_port1.xml", "dff8_port3.xml", "dff8_port6.xml",
            "dff8_port7.xml", "dff8_port8.xml",
        ),
        846, 1017, 180, "native-d-flip-flop",
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
    "TIMER8": frozenset({"TIMER8", "LM555CN", "LM555", "NE555", "HE555"}),
    "DFF8": frozenset({"DFF8", "7474N", "7474", "74LS74N", "74LS74D"}),
    "CD4017": frozenset({"CD4017", "CD4017B", "4017B_5", "4017BD_5V", "4017BD_5V"}),
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


def _validate_template(name: str, root: ET.Element) -> None:
    """Fail loudly on a structurally broken template instead of losing parts.

    Two failure modes were hit in practice and both are silent killers:

    1. The extracted SWB pack stored the bare ``<CiComponent>``/``<CiPort>``
       without the surrounding ``<Item>`` wrapper every other pack uses, so
       ``element_item.find("./CiComponent")`` returned None and the build died
       with ``AttributeError: 'NoneType' object has no attribute 'set'``.
    2. ``sym_swb.xml`` lost its closing ``</Item>``, which only showed up as
       ``ParseError: no element found``.
    """
    if root.tag != "Item":
        raise ValueError(
            f"Template {name} is not wrapped in <Item> (root is <{root.tag}>). "
            "Multisim element/port/symbol templates must be "
            "<Item Class=\"...\"><CiComponent|CiPort .../></Item>."
        )
    inner = list(root)
    if not inner:
        raise ValueError(f"Template {name} has an empty <Item> wrapper.")


def _normalize_inline_model_cards(root: ET.Element, name: str) -> None:
    """Put template-embedded ``.model``/``.subckt`` cards on their own line.

    NI's template convention embeds per-instance model cards inside the SPICE
    template string separated by a NEWLINE (``r%p ... \\n.model r%p r(...)``).
    A card glued onto the device line with spaces parses fine in the MCP
    command engine but Multisim's own "Check SPICE Netlist" reads the glued
    ``SW(...)``/``r(...)`` as a function call on the instance card:

        Error: RefDes 's4', element 'ss4': Expected ')' in function sw
        Error: RefDes 's4', element 'ss4': Unexpected ')' found in function ''
        Error: RefDes 's4', element 'ss4': Due to errors, the component 'ss4'
                                           will be omitted from the simulation

    Hit on the v7 boards: swb_element.xml carried ``s%p ... smdl%p .model
    smdl%p SW(...)`` on one line.  Repair it here so any pack (current or
    regenerated) yields GUI-checkable schematics.
    """
    pattern = re.compile(r"(?<=\S)[ \t]+(\.(?:model|subckt)\b)")
    for template in root.iter("CiaSpiceTmpltExprt"):
        text = template.get("String")
        if text and pattern.search(text):
            template.set("String", pattern.sub(r"\n\1", text))


def _load_template(name: str) -> ET.Element:
    for root in template_search_paths():
        path = root / name
        if path.is_file():
            parsed = parse_native_xml(path).getroot()
            if name.startswith(("sym_", "swb_", "mnmos", "sw_")):
                _validate_template(name, parsed)
            _normalize_inline_model_cards(parsed, name)
            return parsed
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
            # A netlist line "S1 ctrlp ctrln sw1 sw2 SWB" asks for the native
            # NI SPST-bounce switch.  The refdes prefix is still "S", so the
            # generic branch above would resolve it to kind "S" -- which is
            # mapped to mnmos_element.xml (an N-MOS symbol/template) and would
            # emit  s%p %tD %tG %tS %tSUB SWB  plus the MOSFET geometry params
            # (L/W/AD/AS/PD/PS/NRD/NRS).  SPICE reads a leading lowercase "s"
            # as a *voltage-controlled switch* card, which does not accept
            # those params, hence:
            #   Error: RefDes 's1', element 'ss1': Unable to parse parameter name
            # Detect the carrier from the model token and switch to SWB.
            variant = "SWB" if model.upper() == "SWB" else "S"
            parsed.components.append(
                ComponentSpec(
                    kind=variant,
                    refdes=refdes,
                    nodes=[_normalize_net(item)[0] for item in parts[1:5]],
                    model=model,
                    model_definition=model_definitions.get(model.lower()),
                    # SWB carries its own vendor body in the template; the
                    # trailing tokens after the model name are MOSFET geometry
                    # and must NOT be forwarded.
                    parameters=[] if variant == "SWB" else parts[6:],
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
            elif len(nodes) == 8 and model.upper() in {
                "TIMER8", "LM555CN", "LM555", "NE555", "HE555",
            }:
                parsed.components.append(
                    ComponentSpec(
                        kind="TIMER8",
                        # Multisim's native timer carrier is a U-device even
                        # though the portable SPICE instance uses the X
                        # prefix. Normalize XU1 -> U1 so the exported native
                        # netlist retains the vendor macro instead of treating
                        # it as an unresolved generic subcircuit.
                        refdes=(
                            refdes[1:]
                            if refdes[:1].upper() == "X"
                            and len(refdes) > 1
                            else refdes
                        ),
                        nodes=nodes,
                        model=model,
                        parameters=instance_parameters,
                    )
                )
            elif len(nodes) == 8 and model.upper() in {
                "DFF8", "7474N", "7474", "74LS74N", "74LS74D",
            }:
                parsed.components.append(
                    ComponentSpec(
                        kind="DFF8",
                        # Multisim's extracted 7474 section is a U-device;
                        # normalize portable XU1 notation to native U1.
                        refdes=(
                            refdes[1:]
                            if refdes[:1].upper() == "X"
                            and len(refdes) > 1
                            else refdes
                        ),
                        nodes=nodes,
                        model=model,
                        parameters=instance_parameters,
                    )
                )
            elif len(nodes) == 16 and model.upper() in {
                "CD4017", "CD4017B", "4017B", "4017B_5", "4017BD_5V",
            }:
                parsed.components.append(
                    ComponentSpec(
                        kind="CD4017",
                        # Native CMOS carrier is a U-device even though the
                        # portable SPICE instance carries the X prefix.
                        refdes=(
                            refdes[1:]
                            if refdes[:1].upper() == "X"
                            and len(refdes) > 1
                            else refdes
                        ),
                        nodes=nodes,
                        model=model,
                        parameters=instance_parameters,
                    )
                )
            elif len(nodes) == 5 and model.upper() in NATIVE_OPAMP_MODELS:
                parsed.components.append(ComponentSpec(
                    kind=model.upper(), refdes=refdes[1:] if refdes.upper().startswith("XU") else refdes,
                    nodes=nodes, model=model, parameters=instance_parameters,
                ))
            elif len(nodes) == 5 and model.upper() in {
                "OPAMP5",
                "IDEALOPAMP",
                "LM741",
                "LM358",
                "LM258",
                "LM324M",
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


def _split_expression_arguments(value: str) -> list[str]:
    """Split a function-style expression at top-level commas."""
    arguments: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(value):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("unbalanced expression parentheses")
        elif character == "," and depth == 0:
            arguments.append(value[start:index].strip())
            start = index + 1
    if depth != 0:
        raise ValueError("unbalanced expression parentheses")
    arguments.append(value[start:].strip())
    return arguments


def _rewrite_ngspice_conditionals(text: str) -> str:
    """Rewrite legacy ``if(condition,yes,no)`` expressions to ngspice ternaries."""

    def rewrite(value: str) -> str:
        rendered: list[str] = []
        index = 0
        while index < len(value):
            candidate = value[index : index + 3].casefold()
            previous = value[index - 1] if index else ""
            if candidate == "if(" and not (
                previous.isalnum() or previous in "_."
            ):
                open_index = index + 2
                depth = 1
                close_index = open_index + 1
                while close_index < len(value) and depth:
                    if value[close_index] == "(":
                        depth += 1
                    elif value[close_index] == ")":
                        depth -= 1
                    close_index += 1
                if depth:
                    raise ValueError("unbalanced conditional expression")
                arguments = _split_expression_arguments(
                    value[open_index + 1 : close_index - 1]
                )
                if len(arguments) != 3 or any(not argument for argument in arguments):
                    raise ValueError(
                        "ngspice conditional expressions require three arguments"
                    )
                condition, when_true, when_false = (
                    rewrite(argument) for argument in arguments
                )
                rendered.append(
                    f"({condition} ? {when_true} : {when_false})"
                )
                index = close_index
                continue
            rendered.append(value[index])
            index += 1
        return "".join(rendered)

    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("*", ";", "#")):
            lines.append(line)
        else:
            lines.append(rewrite(line))
    return "\n".join(lines)


def _conditional_expression(
    condition: str,
    when_true: str,
    when_false: str,
    *,
    ngspice_compatible: bool,
) -> str:
    if ngspice_compatible:
        return f"({condition} ? {when_true} : {when_false})"
    return f"if({condition},{when_true},{when_false})"


_GROUND_ALIAS = re.compile(
    r"(?<![A-Za-z0-9_])(?:gnd|ground)(?![A-Za-z0-9_])", re.IGNORECASE
)


def _canonicalize_ground_aliases(lines: Sequence[str]) -> list[str]:
    """Map common ground aliases to SPICE node ``0`` for execution decks.

    Multisim's command engine is stricter than the schematic importer: it
    accepts a ground element in the generated diagram, but the sourced SPICE
    deck must expose node ``0``. Keep subcircuit declarations and model
    definitions untouched so an intentional local port/model named ``gnd`` is
    not rewritten. The source netlist remains unchanged; this only affects the
    backend execution copy and its audit evidence.
    """
    rendered: list[str] = []
    subcircuit_depth = 0
    for line in lines:
        stripped = line.lstrip()
        lowered = stripped.casefold()
        if lowered.startswith(".subckt"):
            subcircuit_depth += 1
            rendered.append(line)
            continue
        if lowered.startswith(".ends"):
            subcircuit_depth = max(0, subcircuit_depth - 1)
            rendered.append(line)
            continue
        if (
            not stripped
            or stripped.startswith(("*", ";", "#"))
            or lowered.startswith((".model", ".func"))
            or subcircuit_depth
        ):
            rendered.append(line)
            continue
        rendered.append(_GROUND_ALIAS.sub("0", line))
    return rendered


def prepare_simulation_netlist(
    text: str, *, ngspice_compatible: bool = False
) -> str:
    """Translate schematic A-device shorthand into executable XSPICE.

    Multisim's historical expression dialect uses ``if(condition,yes,no)``;
    ngspice's behavioral-source parser uses the equivalent C-style ternary
    expression. Keep the Multisim spelling as the default and opt into the
    ngspice form only from the ngspice backend.
    """
    text = expand_component_adapters(text)
    if ngspice_compatible:
        text = _rewrite_ngspice_conditionals(text)
    logical_lines = _canonicalize_ground_aliases(_logical_netlist_lines(text))
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
                        expression = _conditional_expression(
                            condition,
                            f"V({low})",
                            f"V({high})",
                            ngspice_compatible=ngspice_compatible,
                        )
                    else:
                        input_a, input_b, output, high, low = nodes
                        threshold = f"((V({high})+V({low}))/2)"
                        a_high = f"V({input_a})>{threshold}"
                        b_high = f"V({input_b})>{threshold}"
                        high_value = f"V({high})"
                        low_value = f"V({low})"
                        if kind == "DAND5":
                            expression = _conditional_expression(
                                a_high,
                                _conditional_expression(
                                    b_high,
                                    high_value,
                                    low_value,
                                    ngspice_compatible=ngspice_compatible,
                                ),
                                low_value,
                                ngspice_compatible=ngspice_compatible,
                            )
                        elif kind == "DNAND5":
                            expression = _conditional_expression(
                                a_high,
                                _conditional_expression(
                                    b_high,
                                    low_value,
                                    high_value,
                                    ngspice_compatible=ngspice_compatible,
                                ),
                                high_value,
                                ngspice_compatible=ngspice_compatible,
                            )
                        elif kind == "DOR5":
                            expression = _conditional_expression(
                                a_high,
                                high_value,
                                _conditional_expression(
                                    b_high,
                                    high_value,
                                    low_value,
                                    ngspice_compatible=ngspice_compatible,
                                ),
                                ngspice_compatible=ngspice_compatible,
                            )
                        elif kind == "DNOR5":
                            expression = _conditional_expression(
                                a_high,
                                low_value,
                                _conditional_expression(
                                    b_high,
                                    low_value,
                                    high_value,
                                    ngspice_compatible=ngspice_compatible,
                                ),
                                ngspice_compatible=ngspice_compatible,
                            )
                        elif kind == "DXOR5":
                            expression = _conditional_expression(
                                a_high,
                                _conditional_expression(
                                    b_high,
                                    low_value,
                                    high_value,
                                    ngspice_compatible=ngspice_compatible,
                                ),
                                _conditional_expression(
                                    b_high,
                                    high_value,
                                    low_value,
                                    ngspice_compatible=ngspice_compatible,
                                ),
                                ngspice_compatible=ngspice_compatible,
                            )
                        else:
                            expression = _conditional_expression(
                                a_high,
                                _conditional_expression(
                                    b_high,
                                    high_value,
                                    low_value,
                                    ngspice_compatible=ngspice_compatible,
                                ),
                                _conditional_expression(
                                    b_high,
                                    low_value,
                                    high_value,
                                    ngspice_compatible=ngspice_compatible,
                                ),
                                ngspice_compatible=ngspice_compatible,
                            )
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


def _transform_point(element: ET.Element, x: float, y: float, *, translation: bool = True) -> tuple[float, float]:
    value = lambda key, default: float(element.get('Transformer-' + key, default))
    return (x * value('M00', '1') + y * value('M10', '0') + (value('M20', '0') if translation else 0),
            x * value('M01', '0') + y * value('M11', '1') + (value('M21', '0') if translation else 0))


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
        px, py = _transform_point(connector, float(connector.get('ptCenterX')), float(connector.get('ptCenterY')))
        px, py = _transform_point(pin, px, py)
        px, py = _transform_point(symbol_item.find('./CIITSymbolComp'), px, py, translation=False)
        # Remove tiny float rotation noise from vendor templates, not real offsets.
        px = round(px / 9) * 9 if abs(px - round(px / 9) * 9) < .001 else px
        py = round(py / 9) * 9 if abs(py - round(py / 9) * 9) < .001 else py
        direction = None
        for lead in pin.findall('./Objects/Item/CODLineComp'):
            ends = []
            for number in (0, 1):
                lx, ly = _transform_point(lead, float(lead.get(f'pt{number}X')), float(lead.get(f'pt{number}Y')))
                lx, ly = _transform_point(pin, lx, ly)
                ends.append(_transform_point(symbol_item.find('./CIITSymbolComp'), lx, ly, translation=False))
            for anchor, inside in (ends, list(reversed(ends))):
                if abs(anchor[0]-px) < .01 and abs(anchor[1]-py) < .01:
                    vx, vy = px-inside[0], py-inside[1]
                    if abs(vx) > .01 and abs(vy) < .01:
                        direction = (1 if vx > 0 else -1, 0)
                    elif abs(vy) > .01 and abs(vx) < .01:
                        direction = (0, 1 if vy > 0 else -1)
        info[port_id] = {
            "local_x": px,
            "local_y": py,
            "direction": direction,
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
        connector = connector_item.find("./CIITPinConnectorComp")
        if connector is None:
            return
        link = connector.find("./ConnectList/Item")
        if link is None:
            # Extracted native carriers can ship with an empty ConnectList when
            # the pin happened to be unconnected in the probe circuit. Leaving
            # it empty makes Multisim drop that terminal on the next open, so
            # create the placeholder instead of silently no-oping.
            connect_list = connector.find("./ConnectList")
            if connect_list is None:
                connect_list = ET.SubElement(connector, "ConnectList")
            connect_list.append(ET.Element("Item", {"ID": extpin_id}))
            return
        link.set("ID", extpin_id)
        return


def voltage_source_stem(spec: ComponentSpec) -> str:
    """Prefer waveform-specific native carriers when the local pack has them."""
    pulse = bool(re.search(r"(?i)\bPULSE\s*\(", spec.model or ""))
    dc_only = spec.value is not None or bool(re.fullmatch(r"(?i)\s*DC\s+\S+\s*", spec.model or ""))
    stem = "vpulse" if pulse else "vdc" if dc_only else "v"
    if any((path / (stem + "_element.xml")).is_file() for path in template_search_paths()):
        return stem
    return "v"


def voltage_pin_order(spec: ComponentSpec) -> list[int]:
    if re.fullmatch(r"(?i)DC\s+\S+\s+SIN\s*\([^()]*\)", spec.model or ""):
        return [1, 2]  # Native AC_VOLTAGE: top terminal is positive.
    return [1, 2] if voltage_source_stem(spec) in {"vdc", "vpulse"} else [2, 1]


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
            # A trailing decimal point is valid only for integer notation.
            # Appending it to 0.1 or 1e-07 corrupts the native numeric value.
            doubles[1].set("Value", format(numeric_value, ".17g"))
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
            elif kind in {"E", "F", "G", "H", "BV", "BI", "T", "TIMER8", "DFF8", "CD4017"} or kind.startswith("XSUB") or kind in NATIVE_OPAMP_MODELS:
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
    if spec.kind in NATIVE_OPAMP_MODELS:
        values = [item.get("Value", "").removeprefix("&ASC") for item in element_item.iter("Item")]
        if spec.kind not in values or not any(
            re.search(r"(?im)^\s*\.subckt\s+" + re.escape(NATIVE_OPAMP_MODELS[spec.kind]) + r"\s", value) and
            re.search(r"(?im)^\s*\.ends\b", value) for value in values
        ):
            raise ValueError(f"{spec.kind} requires an intact local multiline model; re-extract the licensed template")
        if spec.parameters:
            raise ValueError(f"{spec.kind} instance parameters are not supported")
        return
    if spec.kind not in {
        "R", "V", "I", "BV", "BI", "T", "XSUB2", "XSUB3", "XSUB4", "XSUB5", "XSUBN",
        "D", "QNPN", "QPNP", "MNMOS", "MPMOS", "S", "JN", "JP", "ZN", "ZP", "W", "K", "O", "U",
        "DNAND5", "DNOR5", "DXOR5", "DXNOR5", "TIMER8", "DFF8", "CD4017", "OPAMP5",
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
    if spec.kind == "R":
        # The supported R syntax describes an ideal resistor. The extracted
        # vendor carrier's multiline temperature model is not a safe default:
        # flattened XML attributes can omit the resistor from native analysis.
        template = element_item.find("./CiComponent//CiaSpiceTmpltExprt")
        if template is None:
            raise ValueError("Native resistor carrier has no SPICE template")
        template.set("String", _asc("r%p %t1 %t2 #1"))
        return
    if spec.kind == "OPAMP5":
        if (spec.model or "OPAMP5").upper() not in {"OPAMP5", "IDEALOPAMP"}:
            raise ValueError(f"{spec.refdes}: native model {spec.model!r} is not verified; request OPAMP5 explicitly for an ideal model")
        # The vendor 5T_Virtual carrier is not stable when emitted through
        # the generated XML (it enumerates correctly but produces zero gain).
        # Use a deterministic ideal VCVS for the bounded OPAMP5 contract.
        template = element_item.find("./CiComponent//CiaSpiceTmpltExprt")
        if template is None:
            raise ValueError("Native OPAMP5 carrier has no SPICE template")
        template.set("String", _asc("e%p %tOUT 0 %tIN+ %tIN- 1e5"))
        return
    if spec.kind in {"V", "I"}:
        if not spec.model and spec.kind != "V":
            return
        template = element_item.find("./CiComponent//CiaSpiceTmpltExprt")
        if template is None:
            raise ValueError(f"Native carrier for {spec.kind} has no SPICE template")
        carrier_values = element_item.findall("./CiComponent/Attributes/Item/CiaCollString/strings/Item")
        carrier_identity = carrier_values[1].get("Value", "").removeprefix("&ASC") if len(carrier_values) > 1 else None
        sine = re.fullmatch(r"(?i)DC\s+(\S+)\s+SIN\s*\(([^()]*)\)", spec.model or "")
        if spec.kind == "V" and sine:
            from .linear_reference import validate_native_source
            validate_native_source(spec)
            if carrier_identity != "AC_VOLTAGE":
                raise ValueError("SIN requires the local native AC_VOLTAGE carrier")
            param_list = element_item.find("./CiComponent//CiaParamList")
            doubles, params = param_list.findall("./doubles/Item"), param_list.findall("./parameters/Item")
            offset, amplitude, frequency = sine[2].split()
            for index, token in {1:amplitude, 3:offset, 5:frequency, 7:"0", 9:"0", 11:"0", 13:"0", 15:"0", 17:"0", 19:"0", 21:"0", 23:"0"}.items():
                if index >= min(len(doubles), len(params)):
                    raise ValueError("native sine carrier has an incomplete parameter table")
                value, display = parse_spice_value(token)
                doubles[index].set("Value", format(value, ".17g"))
                params[index].set("Value", _asc(display))
            template.set("String", _asc("v%p %t1 %t2 dc #3 sin(#3 #1 #5 #7 #9 #11)"))
            return
        if spec.kind == "V" and voltage_source_stem(spec) in {"vdc", "vpulse"} and carrier_identity in {"DC_POWER", "PULSE_VOLTAGE"}:
            param_list = element_item.find("./CiComponent//CiaParamList")
            doubles, params = param_list.findall("./doubles/Item"), param_list.findall("./parameters/Item")
            def set_parameter(index: int, token: str) -> None:
                if index >= len(doubles) or index >= len(params):
                    raise ValueError("native source carrier has an incomplete parameter table")
                value, display = parse_spice_value(token)
                doubles[index].set("Value", format(value, ".17g"))
                params[index].set("Value", _asc(display))
            expression = spec.model or f"DC {spec.value}"
            if voltage_source_stem(spec) == "vpulse":
                pulse = re.search(r"(?i)PULSE\s*\(([^()]*)\)", expression)
                tokens = pulse[1].split()
                if len(tokens) != 7:
                    raise ValueError("native pulse carrier requires seven pulse parameters")
                for index, token in zip(range(1, 14, 2), tokens):
                    set_parameter(index, token)
                ac = re.search(r"(?i)\bAC\s+(\S+)(?:\s+([+-]?(?:\d|\.)\S*))?", expression)
                set_parameter(15, ac[1] if ac else "0")
                set_parameter(17, ac[2] if ac and ac[2] else "0")
            else:
                set_parameter(1, spec.value or expression.split()[1])
                set_parameter(3, "0")
                set_parameter(5, "0")
            template.set("String", _asc(f"v%p %t1 %t2 {expression}"))
            return
        # In the legacy carrier port 2 is assigned the positive terminal and
        # the first SPICE node; port 1 is negative. Verified by native OP.
        terminals = "%t2 %t1"
        # A scalar source value comes from the compact ``V1 ... DC 10``
        # syntax.  Preserve it as a DC-only source; falling back to the
        # carrier's AC waveform makes the editable schematic contradict the
        # validated netlist (for example, displaying 5 kHz on a DC divider).
        expression = spec.model or ("dc #1" if spec.value is not None else "dc #1 ac #3 #5")
        if spec.model:
            # Synchronize the editable carrier properties with explicit DC/AC
            # clauses; otherwise a 1 V waveform still displays the vendor's 15 V.
            param_list = element_item.find("./CiComponent//CiaParamList")
            for clause, parameter_index in (("dc", 1), ("ac", 3)):
                match = re.search(rf"\b{clause}\s+([^\s()]+)", expression, re.IGNORECASE)
                if match and param_list is not None:
                    value, display = parse_spice_value(match.group(1))
                    param_list.findall("./doubles/Item")[parameter_index].set("Value", format(value, ".17g"))
                    param_list.findall("./parameters/Item")[parameter_index].set("Value", _asc(display))
                    expression = expression[:match.start(1)] + f"#{parameter_index}" + expression[match.end(1):]
        elif spec.value is not None:
            param_list = element_item.find("./CiComponent//CiaParamList")
            if param_list is not None:
                doubles = param_list.findall("./doubles/Item")
                params = param_list.findall("./parameters/Item")
                if len(doubles) > 1:
                    value, display = parse_spice_value(spec.value)
                    doubles[1].set("Value", format(value, ".17g"))
                    if len(params) > 1:
                        params[1].set("Value", _asc(display))
        template.set(
            "String",
            _asc(f"{spec.kind.lower()}%p {terminals} {expression}"),
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
    if spec.kind == "CD4017":
        # CD4017 is a verified native CMOS decade counter. Its extracted
        # carrier already embeds the vendor digital macro and must not be
        # rewritten as a generic X subcircuit.
        return
    if spec.kind == "TIMER8":
        # TIMER8 is a verified native vendor macro. Its extracted carrier
        # already contains the exact named-terminal SPICE template; rewriting
        # it as a generic XSUB would lose the vendor model identity.
        return
    if spec.kind == "DFF8":
        # DFF8 is a verified native 7474N A-section carrier. Preserve its
        # named-terminal digital model rather than rewriting it as a generic
        # subcircuit; exact 74LS74 equivalence is deliberately reported as a
        # substitution in the build warnings.
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
            # The private model card must start on its OWN LINE.  Multisim's
            # SPICE reader only recognises `.model` at the start of a line; if it
            # is glued onto the device card with spaces the reader rejects the
            # whole component:
            #   Error: Element 'd1:':  Expected ')' in function d
            #   Error: Element 'd1:':  Unexpected ')' found in function ''
            #   Error: Element 'd1:':  Due to errors, the component 'd1' will
            #                          be omitted from the simulation
            # (verified 2026-09-23 against Multisim 14.3's own engine; the
            # newline form produces a clean log).  NI's own templates do the
            # same: "&ASCr%p %t1 %t2 #1 vres%p ... \n.model vres%p r(...)\n"
            # in samples/Analog/LowPassFilter.ms14.xml.
            rendered += f"\n.model {private_model} {spec.model_definition}\n"
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


def _simple_analog_profile(specs: list[ComponentSpec]) -> dict[str, Any] | None:
    """A planar layout for a source, series resistor and shunt R/C.

    Match connectivity exactly; unsupported circuits keep the general layout.
    """
    parts = [s for s in specs if s.kind != "GND"]
    if len(parts) != 3:
        return None
    sources = [s for s in parts if s.kind == "V" and len(s.nodes) == 2 and s.nodes[1] == "0"]
    if len(sources) != 1:
        return None
    source = sources[0]
    series = [s for s in parts if s.kind == "R" and s.nodes[0] == source.nodes[0] and s.nodes[1] not in {"0", source.nodes[0]}]
    if len(series) != 1:
        return None
    resistor = series[0]
    shunt = [s for s in parts if s is not resistor and s.kind in {"R", "C"} and s.nodes == [resistor.nodes[1], "0"]]
    if len(shunt) != 1:
        return None
    return {"source": source.refdes, "series": resistor.refdes, "shunt": shunt[0].refdes,
            "shunt_kind": shunt[0].kind, "input": source.nodes[0], "output": resistor.nodes[1],
            "positions": {source.refdes: (36, 117), resistor.refdes: (243, 117),
                          shunt[0].refdes: (450, 252), "0": (45, 414)}}


def _opamp_profile(specs: list[ComponentSpec]) -> dict[str, Any] | None:
    """Planar placement for the bounded five-pin non-inverting OPAMP5 stage."""
    kinds = {item.refdes: item.kind for item in specs}
    opamp_ref = "U1" if kinds.get("U1") == "OPAMP5" else "XU1"
    required = {opamp_ref: "OPAMP5", "V1": "V", "VCC": "V", "VSS": "V", "R1": "R", "RF": "R", "RG": "R"}
    if any(kinds.get(ref) != kind for ref, kind in required.items()):
        return None
    positions = {"V1": (90, 270), "VCC": (90, 90), "VSS": (900, 900),
                 opamp_ref: (360, 270), "RF": (630, 270), "R1": (90, 630),
                 "RG": (360, 450), "0": (18, 1080)}
    if any(item.refdes not in positions for item in specs):
        return None
    return {"positions": positions}


def _rectifier_profile(specs: list[ComponentSpec]) -> dict[str, Any] | None:
    expected = {'V1':('V',['ac_p','ac_n']), 'D1':('D',['ac_p','vraw']), 'D2':('D',['ac_n','vraw']),
                'D3':('D',['0','ac_p']), 'D4':('D',['0','ac_n']), 'C1':('C',['vraw','0']), 'RLOAD':('R',['vraw','0'])}
    if {s.refdes:(s.kind,s.nodes) for s in specs if s.kind != 'GND'} != expected:
        return None
    return {'positions':{'V1':(63,216), 'D1':(297,126), 'D3':(297,306),
                         'D2':(531,126), 'D4':(531,306), 'C1':(729,216),
                         'RLOAD':(909,216), '0':(909,486)}}


def _common_emitter_profile(specs: list[ComponentSpec]) -> dict[str, Any] | None:
    expected = {"VCC": ("V", ["vcc", "0"]), "VIN": ("V", ["in", "0"]),
                "RBIAS1": ("R", ["vcc", "base"]), "RBIAS2": ("R", ["base", "0"]),
                "RC": ("R", ["vcc", "collector"]), "RE": ("R", ["emitter", "0"]),
                "Q1": ("QNPN", ["collector", "base", "emitter"]),
                "CIN": ("C", ["in", "base"]), "COUT": ("C", ["collector", "out"]),
                "RLOAD": ("R", ["out", "0"])}
    parts = {s.refdes: (s.kind, s.nodes) for s in specs if s.kind != "GND"}
    if parts != expected:
        return None
    # Source at left, bias column, transistor column, output/load at right.
    return {"positions": {"VCC": (27, 63), "VIN": (27, 270), "CIN": (225, 243),
                          "RBIAS1": (369, 108), "RBIAS2": (369, 378),
                          "RC": (567, 108), "RE": (567, 378), "Q1": (540, 243),
                          "COUT": (747, 207), "RLOAD": (927, 378), "0": (927, 540)},
            "vertical": {"RBIAS1", "RBIAS2", "RC", "RE", "RLOAD"}}


# ---------------------------------------------------------------------------
# v7 photo-driven profiles (照原图重建：流水灯 + 呼吸灯)
# ---------------------------------------------------------------------------
# 原始来源:剪贴板图片 `clipboard-2026-09-23T16-55-09-631Z-288e6a4a.jpg`,
#          1440 x 1920 像素; schematic 块大致位于 ( 80, 220 ) ... (1380, 1180)。
# 坐标单位:与 grid_origin_x/y (36, 117), grid_step_x/y (270, 216) 同一坐标系。
# 设计原则:
#   * 仅按 refdes 集合识别(写真型),不依赖通联匹配,跟 simple/opamp/ce profile
#     的"严格类型匹配"逻辑区分开。
#   * 当 6 个以上关键 refdes 全部命中时启用,其余仍走网格布局。
#   * 坐标只把"分区 + 顺序"映射到位,精度不高(± 一两个网距都可),用以改善
#     "不像原图",不是像素级重构。
# ---------------------------------------------------------------------------
_V7_FLOW_FINGERPRINT = {
    "VUSB", "VCC", "C1", "C3", "C5",
    "R1", "R2", "R3", "R4", "R5", "R6", "R7",
    "DL1", "DL2", "DL3", "DL4", "DL5", "DL6", "DL7",
    "D8", "D9", "D10", "D11", "D12", "D13", "D14",
    "D1", "D2", "D3", "D4", "D5", "D6", "D7",
    "XU1", "XU2", "M3", "S1", "S2", "S3",
    "R8", "R9", "R10", "RP1",
}
_V7_BREATH_FINGERPRINT = {
    "VU3", "VCC", "V33",
    "R11", "R12", "R13", "R14", "RP2",
    "C6", "C7", "C8", "C9", "C10",
    "XU41", "XU42",
    "Q4", "M5", "S4",
    "R15", "R16",
    "D22",
    "DL8", "DL9", "DL10", "DL11", "DL12", "DL13", "DL14",
    "DL15", "DL16", "DL17", "DL18", "DL19",
    "DL20", "DL21", "DL22", "DL23", "DL24", "DL25",
    "DL26", "DL27", "DL28", "DL29", "DL30", "DL31",
}


def _v7_flow_positions() -> dict[str, tuple[int, int]]:
    """流水灯 v7:NE555 + CD4017 + SW2 8位 DIP + 7 颗 LED 共阴极。

    GND 节点 ("0") 不写在这里 — 让它回退到网格布局末尾,远离主电路,
    避免被任意 pin escape 当作 obstacle 阻挡。
    """
    return {
        # 顶部一行:USB1 / VCC / C1..C4 / SW1(VUSB->VCC)
        "VCC": (630,  90),
        "VUSB": (360,  90),   # USB 5 V 入
        "VK1": (90,  90),   # SW1 控制器
        "C1":  (450, 198),
        "C2":  (594, 198),
        "C3":  (738, 198),
        "C4":  (882, 198),
        # U1 NE555 居中上方,占 U1=NE555 区域
        "R8":  (90, 306),
        "S3":  (270, 306),
        "VK3": (450, 306),
        "R9":  (90, 414),
        "R10": (90, 558),
        "RP1": (90, 666),
        "C5":  (270, 666),
        "C20": (450, 666),
        "XU1": (270, 522),     # NE555
        "M3":  (450, 234),
        "RK4": (450, 162),
        # U2 CD4017 在 U1 右侧
        "D8":  (810, 306),
        "D9":  (810, 378),
        "D10": (810, 450),
        "D11": (810, 522),
        "D12": (810, 594),
        "D13": (810, 666),
        "D14": (810, 738),
        "XU2": (594, 486),
        # SW2 8 位 DIP 在中部,上方标注 VCC_IN1,VCC_IN2, VCC_IN3
        "VK2": (90, 918),
        "S2":  (90, 990),
        "D1":  (450, 882),
        "D2":  (450, 918),
        "D3":  (450, 954),
        "D4":  (450, 990),
        "D5":  (450, 1026),
        "D6":  (450, 1062),
        "D7":  (450, 1098),
        "RK3": (306, 1122),
        "D23": (450, 1122),
        # 7 路 LED 右列
        "R1":  (810, 198),
        "R2":  (810, 270),
        "R3":  (810, 342),
        "R4":  (810, 414),
        "R5":  (810, 486),
        "R6":  (810, 558),
        "R7":  (810, 630),
        "DL1": (1170, 198),
        "DL2": (1170, 270),
        "DL3": (1170, 342),
        "DL4": (1170, 414),
        "DL5": (1170, 486),
        "DL6": (1170, 558),
        "DL7": (1170, 630),
        # unconnected CD4017 outputs -> pull-downs
        "R30": (594, 270),
        "R31": (594, 306),
        "R32": (594, 342),
    }


def _v7_breath_positions() -> dict[str, tuple[int, int]]:
    """呼吸灯 v7:LM358 双运放 + Q4/Q5 + SW4 + 24 颗 LED 矩阵。

    GND 节点 ("0") 不写在 profile — 让它回退网格末位。
    """
    return {
        "VCC": (270,  90),     # 5V rail
        "VU3": (450,  90),     # internal 3V3 source (sim only)
        "V33": (594,  90),
        "C6":  (450, 198),
        "C7":  (594, 198),
        "C8":  (738, 198),
        "C9":  (882, 198),
        # 中分点 vb = 2.5 V mid-rail
        "R11": (306, 306),
        "R12": (306, 414),
        # U41 积分器 (左下)
        "R13": (450, 414),
        "R910": (594, 522),
        "C10": (306, 558),
        "XU41": (198, 522),
        # U42 比较器 (右下)
        "R14": (594, 414),
        "RP2": (594, 522),
        "XU42": (198, 666),
        # Q4 -> SW4 -> D22 -> LED 链 (中右)
        "Q4":  (450, 810),
        "VK4": (594, 810),
        "S4":  (594, 738),
        "R15": (738, 810),
        "D22": (738, 666),
        "R16": (450, 666),
        "M5":  (738, 522),
        "RK2": (738, 594),
        # 24 颗 LED 排成两排,上方 12 (DL8..DL19),下方 12 (DL20..DL31)
        "DL8":  (270, 990),
        "DL9":  (378, 990),
        "DL10": (486, 990),
        "DL11": (594, 990),
        "DL12": (702, 990),
        "DL13": (810, 990),
        "DL14": (918, 990),
        "DL15": (1026, 990),
        "DL16": (1134, 990),
        "DL17": (1242, 990),
        "DL18": (1350, 990),
        "DL19": (1458, 990),
        "DL20": (270, 1188),
        "DL21": (378, 1188),
        "DL22": (486, 1188),
        "DL23": (594, 1188),
        "DL24": (702, 1188),
        "DL25": (810, 1188),
        "DL26": (918, 1188),
        "DL27": (1026, 1188),
        "DL28": (1134, 1188),
        "DL29": (1242, 1188),
        "DL30": (1350, 1188),
        "DL31": (1458, 1188),
        # sim aid
        "IKICK": (450, 1188),
    }


def _v7_photo_profile(specs: list[ComponentSpec]) -> dict[str, Any] | None:
    refdes = {s.refdes for s in specs if s.kind != "GND"}
    flow_hit = len(refdes & _V7_FLOW_FINGERPRINT)
    breath_hit = len(refdes & _V7_BREATH_FINGERPRINT)
    if breath_hit >= flow_hit and breath_hit >= 10:
        return {"positions": _v7_breath_positions()}
    if flow_hit >= breath_hit and flow_hit >= 10:
        return {"positions": _v7_flow_positions()}
    return None


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


def _template_available(name: str) -> bool:
    """True when a user-local component-pack template file exists."""
    return any((root / name).is_file() for root in template_search_paths())


def _port_native_model_spec(port: ET.Element) -> tuple[str, str, str] | None:
    """Return (base, family, card) for a native port that owns a SPICE model.

    Digital native ports (a 4017's CP0 / O0 / ...) carry their analog/digital
    bridge as a sub-circuit: receiver for inputs, driver for outputs.  Multisim
    does NOT take that card from the port's cached CiaCollString; it looks the
    CiModel object up by the port's `Model` attribute.  When that object is
    missing from the .ms14 the port's `%m` comes out empty and the exporter
    eats the VSS terminal as if it were the sub-circuit name:

        Error: Element 'xu2.cp0': Invalid subckt definition name 'u2_open_vss'

    The card itself is still cached in the port, so it can be recovered here.
    """
    for coll in port.iter("CiaCollString"):
        values = [item.get("Value", "") for item in coll.findall("./strings/Item")]
        if len(values) < 6:
            continue
        card = values[5].removeprefix("&ASC")
        if not card.lstrip().upper().startswith(".SUBCKT"):
            continue
        base = values[3].removeprefix("&ASC").strip()
        family = values[2].removeprefix("&ASC").strip()
        if base and family:
            return base, family, card
    return None


def _component_native_model_spec(comp: ET.Element) -> tuple[str, str, str] | None:
    """Return (base, family, card) for a component that owns a SPICE model.

    Mirrors _port_native_model_spec() for whole components.  A native part
    whose SPICE template ends in ``%m`` (``d%p %tA %tK %m``, ``x%p_a ... %m``,
    ``m%p ... %m``, ``s%p ... %m``, ...) needs the CiModel object that ``%m``
    expands to.  Multisim does NOT fall back to the card cached on the
    element; a missing CiModel makes ``%m`` expand to nothing and the exporter
    then eats the last terminal as the sub-circuit name.

    The card is still cached on the component itself, as
    ``CiaCollString[5]``, exactly like a native port -- so it can be rebuilt.
    ``CiaCollString[3]`` / ``[2]`` give the base and family names Multisim uses
    when it names a copied model ``<base>__<family>__<n>``.

    Returns None when the component carries no usable cached card (a pure
    built-in part such as a resistor, whose model is inlined in the template).
    """
    for coll in comp.iter("CiaCollString"):
        values = [item.get("Value", "") for item in coll.findall("./strings/Item")]
        if len(values) < 6:
            continue
        card = values[5].removeprefix("&ASC")
        head = card.lstrip().upper()
        if not (head.startswith(".SUBCKT") or head.startswith(".MODEL")):
            continue
        base = values[3].removeprefix("&ASC").strip()
        family = values[2].removeprefix("&ASC").strip()
        if base and family:
            return base, family, card
    return None


def _bind_component_native_model(
    comp: ET.Element,
    cache: dict[str, ET.Element],
    sequence: dict[tuple[str, str], int],
    ids: "IdAllocator",
    circuit_item: ET.Element,
    elements: ET.Element,
    model_refs: set[str],
) -> str | None:
    """Embed (once per design) the CiModel a native component refers to.

    Returns the CiID the component should point at, or None when the part has
    no reusable cached card (in which case the caller keeps the original
    reference, which for built-in parts points at the master database and is
    resolved by Multisim itself rather than by this builder).

    Numbering follows Multisim's own convention ``<base>__<family>__<n>`` and
    the ``%m``-visible name inside the card is rewritten to match, so that a
    re-save of the delivered .ms14 is a no-op -- the same contract
    _bind_port_native_model() implements for ports.
    """
    reference = comp.get("Model")
    if not reference:
        return None
    if reference in cache:
        return cache[reference].get("CiID")

    spec = _component_native_model_spec(comp)
    if spec is None:
        # Built-in part (resistor / capacitor / source): its model is inlined
        # in the template and the Model attribute legitimately points outside
        # this design.  Leave it alone; Multisim resolves it from its own
        # database on load.  Registering the CiID in <Models> without an
        # object is what used to happen and is harmless for these parts
        # because their templates never expand %m.
        return reference

    base, family, card = spec
    key = (base, family)
    sequence[key] = sequence.get(key, 0) + 1
    qualified = f"{base}__{family}__{sequence[key]}"
    card = re.sub(
        r"^(\s*\.(?:SUBCKT|MODEL)\s+)\S+",
        lambda match: match.group(1) + qualified,
        card,
        count=1,
        flags=re.IGNORECASE,
    )
    item = ET.Element("Item", {"CiID": ids.next_id(), "Class": "CiModel"})
    model = ET.SubElement(
        item,
        "CiModel",
        {
            "Class": "CiModel",
            "LocalName": _asc(qualified),
            "ChangedByUser": "0",
            "Scope": circuit_item.get("CiID"),
        },
    )
    attributes = ET.SubElement(model, "Attributes")
    ET.SubElement(attributes, "Item")
    holder = ET.SubElement(attributes, "Item")
    ET.SubElement(
        holder, "CiaCString", {"Class": "CiaCString", "String": _asc(card)}
    )
    ET.SubElement(attributes, "Item")
    ET.SubElement(attributes, "Item")
    refcount = ET.SubElement(attributes, "Item")
    ET.SubElement(
        refcount,
        "CiaModelDataRefCount",
        {"Class": "CiaModelDataRefCount", "RefCnt": "1"},
    )
    elements.append(item)
    model_refs.add(item.get("CiID"))
    cache[reference] = item
    return item.get("CiID")


def _bind_port_native_model(
    port: ET.Element,
    cache: dict[str, ET.Element],
    sequence: dict[tuple[str, str], int],
    ids: "IdAllocator",
    circuit_item: ET.Element,
    elements: ET.Element,
    model_refs: set[str],
) -> None:
    """Embed (once per design) the CiModel a native carrier port refers to.

    Multisim names copied models ``<base>__<family>__<n>`` and rewrites both
    the model's own ``.SUBCKT`` line and every ``%m`` expansion to that name
    (verified against samples/Digital/DecadeCounterUserLoad.ms14.xml, where
    port term TTL_LSRCV is stored as TTL_LSRCV__NON__2).
    """
    reference = port.get("Model")
    if not reference:
        return
    if reference in cache:
        port.set("Model", cache[reference].get("CiID"))
        return
    spec = _port_native_model_spec(port)
    if spec is None:
        # Nothing usable to rebuild from: drop the dangling reference so the
        # port falls back to its own cached card instead of exporting blank.
        port.attrib.pop("Model", None)
        return
    base, family, card = spec
    key = (base, family)
    sequence[key] = sequence.get(key, 0) + 1
    qualified = f"{base}__{family}__{sequence[key]}"
    card = re.sub(
        r"^(\s*\.SUBCKT\s+)\S+",
        lambda match: match.group(1) + qualified,
        card,
        count=1,
        flags=re.IGNORECASE,
    )
    item = ET.Element("Item", {"CiID": ids.next_id(), "Class": "CiModel"})
    model = ET.SubElement(
        item,
        "CiModel",
        {
            "Class": "CiModel",
            "LocalName": _asc(qualified),
            "ChangedByUser": "0",
            "Scope": circuit_item.get("CiID"),
        },
    )
    attributes = ET.SubElement(model, "Attributes")
    ET.SubElement(attributes, "Item")
    holder = ET.SubElement(attributes, "Item")
    ET.SubElement(
        holder, "CiaCString", {"Class": "CiaCString", "String": _asc(card)}
    )
    # NI's own port-bridge CiModels carry a trailing CiaParamList:
    #   <Item/> <Item><CiaCString/></Item> <Item><CiaParamList/></Item>
    # (see Up-DownCounter.ms14 -> CMOS_RCV__NON__1 / CMOS_DRV__NON__1 and
    #  SwitchDebounce.ms14 -> the same two models).  Omitting it made Multisim's
    # GUI netlister unable to bind the port's %m to this model, so every
    # CD4017 bridge port exported as
    #   Error: Element 'xu2.cp0': Invalid subckt definition name 'cmos_rcv__non__1'
    paramlist = ET.SubElement(attributes, "Item")
    ET.SubElement(
        paramlist, "CiaParamList", {"Class": "CiaParamList"}
    ).extend(
        ET.Element(tag)
        for tag in ("doubles", "strings", "parameters", "paramindicators")
    )
    elements.append(item)
    model_refs.add(item.get("CiID"))
    cache[reference] = item
    port.set("Model", item.get("CiID"))


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
    output_number: int = -1,
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
            "IRNumber": str(output_number),
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
    pin_points: list[tuple[float, float]] | None = None,
) -> tuple[float, float] | None:
    """Pick a native-stable terminal point on an existing wire for a probe."""
    if pin_points:
        terminals = {point for path in wire_paths for point in (path[0], path[-1])}
        candidates = [point for point in pin_points if point in terminals]
        if candidates:
            return max(candidates, key=lambda point: (point[0], -point[1]))
    # Multisim 14.x can omit a voltage probe placed in the middle of a wire
    # when that wire terminates at an inductor/capacitor pin.  A terminal point
    # is electrically equivalent and survives native output enumeration.
    if wire_paths:
        # Prefer the shortest direct segment and its rightmost terminal. This
        # avoids placing a probe on a junction branch in series RLC layouts.
        direct = min((path for path in wire_paths if path), key=lambda path: (len(path), -max(point[0] for point in path)), default=None)
        if direct:
            return max(direct, key=lambda point: point[0])
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
    net_pin_points: dict[str, list[tuple[float, float]]] | None = None,
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
        point = _pick_probe_point(net_wires.get(net, []), (net_pin_points or {}).get(net))
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
        symbol.set("UniqueID", str(next_probe_id))
        symbol.set("ShowInfo", "0")
        package_id = _asc(f"X_MCP_{symbol_item.get('ID')}")
        symbol.set("FileDataPackageID", package_id)
        instrument_item.set("CompLongName", package_id)

        objects.append(symbol_item)
        elements.append(element_item)
        probe_exts.append(
            ET.Element("Item", {"CiID": element_item.get("CiID")})
        )

        instruments_data.append(instrument_item)
        _set_probe_comphandle(symbol_item, symbol_item.get("ID"))
        _set_probe_comphandle(instrument_item, symbol_item.get("ID"))

        output_number = -1 if index == 1 else index - 1
        cir_to_info.append(_refdes_info(refdes, circuit_name, output_ms14, output_number))
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
                "voltage_output": f"V(OutProbe{index - 1 if index > 1 else ''})",
                "current_output": f"I(OutProbe{index - 1 if index > 1 else ''})",
                "attachment": {
                    "wire_segments_available": len(net_wires.get(net, [])),
                    "point_selected_on_wire": True,
                    "native_output_requires_enumeration": True,
                },
            }
        )

    total_triggers.set("NextProbeID", str(next_probe_id))
    if probes:
        usage = _refdes_prefix_usage()
        usage.set("NextNumber", str(len(probes)))
        numbers = usage.find("./RefDesPrefixUsage")
        _clear(numbers)
        for number in range(len(probes)):
            ET.SubElement(numbers, "NumbersUsedVTwo", {"NumberUsed": str(number)})
        prefix_map.append(usage)
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
    component_positions: dict[str, Any] | None = None,
    min_sheet_size: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Build an editable Multisim XML design from a simple SPICE netlist.

    ``probe_nets`` names the nets that should get voltage probes. When omitted,
    the last non-ground net is probed automatically. Set it to an empty list to
    disable probes.

    ``component_positions`` optionally maps refdes -> ``[x, y]`` sheet
    coordinates for components that must land at a fixed spot (for example
    matching an original board photograph).  Components without an entry keep
    the deterministic auto-layout.  Overrides are applied BEFORE the wire
    router runs, so all wiring follows the custom positions.

    ``min_sheet_size`` optionally enforces a minimum page in inches
    (width, height) at 96 dpi; the page still grows further when the content
    needs more room.  The GUI working area (DesignSheet WorkArea) always
    follows the final page size.
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
        root = parse_native_xml(template_path).getroot()
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
    models = next(root.iter("Models"), None)
    model_refs: set[str] = set()
    native_models: dict[str, ET.Element] = {}
    # Per-reference cache/sequence for component-owned models.  Keyed by the
    # template's original CiID so every instance of the same library part
    # shares ONE embedded CiModel (matching NI's own files, where a linear
    # resistor chain references a single model object).
    native_model_cache: dict[str, ET.Element] = {}
    model_sequence: dict[tuple[str, str], int] = {}

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
    grid_step_x = 270
    grid_step_y = 216
    placement_rank = {
        component_index: rank
        for rank, component_index in enumerate(_component_placement_order(specs))
    }
    simple_profile = _simple_analog_profile(specs)
    opamp_profile = _opamp_profile(specs)
    ce_profile = _common_emitter_profile(specs)
    rectifier_profile = _rectifier_profile(specs)
    node_records: dict[str, dict[str, Any]] = {}
    connections: dict[str, list[dict[str, Any]]] = {}
    component_items: list[ET.Element] = []
    port_items: list[ET.Element] = []
    port_model_sequence: dict[tuple[str, str], int] = {}
    port_model_cache: dict[str, ET.Element] = {}
    net_wires: dict[str, list[list[tuple[float, float]]]] = {}
    net_pin_points: dict[str, list[tuple[float, float]]] = {}
    placements: list[dict[str, Any]] = []

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
        # One native model object per instance, exactly as Multisim itself
        # writes them (a two-instance design gets __1 and __2 copies).
        port_model_cache = {}
        if spec.kind == "XSUBN":
            element_item, symbol_item, component_port_templates = (
                _make_variable_subcircuit_templates(len(spec.nodes))
            )
        elif spec.kind == "K":
            element_item, symbol_item = _make_coupling_templates()
            component_port_templates = []
        else:
            stem = voltage_source_stem(spec) if spec.kind == "V" else None
            element_item = _deepcopy(_load_template(stem + "_element.xml" if stem else definition.element_template))
            symbol_item = _deepcopy(_load_template("sym_" + stem + ".xml" if stem else definition.symbol_template))
            component_port_templates = [
                _load_template(name) for name in definition.port_templates
            ] if stem is None else [_load_template(stem + f"_port{i}.xml") for i in (1, 2)]
        if spec.kind == "V":
            # Old packs stored the positive port in v_port1.xml; freshly
            # extracted packs sort by native pin number. Filenames are not
            # electrical semantics: the DC_POWER carrier uses 2=+, 1=-.
            by_name = {item.find("CiPort").get("LocalName", "").removeprefix("&ASC"): item for item in component_port_templates}
            if set(by_name) != {"1", "2"}:
                raise ValueError("DC_POWER carrier must expose native pins 2 (+) and 1 (-)")
            component_port_templates = [by_name[str(pin)] for pin in voltage_pin_order(spec)]
        _remap_subtree(element_item, ids)
        _remap_subtree(symbol_item, ids)
        comp = element_item.find("./CiComponent")
        sym = symbol_item.find("./CIITSymbolComp")
        # ------------------------------------------------------------------
        # A native part whose SPICE template ends in the model placeholder `%m`
        # only works when the CiModel object it names exists INSIDE this .ms14.
        # Multisim resolves `%m` from the model referenced by the component's
        # `Model` attribute; the element's own cached CiaCollString card is a
        # stale copy and is NOT used.  A dangling reference therefore exports an
        # empty `%m`, and the SPICE reader then swallows the last terminal of
        # the instance card as if it were the sub-circuit name:
        #   Error: RefDes 'u1', element 'xu1': Invalid subckt definition name 'vsel'
        #   Error: RefDes 'u2', element 'au2': Unable to identify XSPICE code
        #          model for simulation in netlist element 'au2'
        #   Error: Element 'xu2.cp0': Invalid subckt definition name 'u2_open_vss'
        # The same is true for the ports of a native digital carrier - see
        # _bind_port_native_model().  NI's own samples embed one CiModel per
        # instance, named <CiaCollString[3]>__<CiaCollString[2]>__<n>
        # (samples/Digital/DecadeCounterUserLoad.ms14.xml).
        # ------------------------------------------------------------------
        native_model_file = spec.kind.lower() + "_model.xml"
        carrier_model_bound = False
        if (
            spec.kind in NATIVE_OPAMP_MODELS
            or spec.kind in NATIVE_MODEL_CARRIERS
            or (spec.kind == "QNPN" and not spec.model_definition)
        ) and _template_available(native_model_file):
            if spec.kind not in native_models:
                model_item = _deepcopy(_load_template(native_model_file))
                _remap_subtree(model_item, ids)
                model_item.find("CiModel").set("Scope", circuit_item.get("CiID"))
                native_models[spec.kind] = model_item
                elements.append(model_item)
            comp.set("Model", native_models[spec.kind].get("CiID"))
            # This path already materialised a real CiModel object, so the
            # generic binding below must not create a second one.
            carrier_model_bound = True
        if comp is not None and comp.get("Model") and not carrier_model_bound:
            # Native components extracted from a licensed Multisim database
            # may point at a CiModel object outside the component subtree.
            # Keep a project-local model placeholder so Multisim does not
            # silently omit the component when exporting its native netlist.
            #
            # ★★ A bare CiID registration in <Models> is NOT enough.  Multisim
            # resolves the `%m` placeholder of the component's SPICE template
            # from the CiModel OBJECT, not from the <Models> membership list.
            # If only the CiID is registered, `%m` expands to an empty string
            # and the exporter eats the last terminal as if it were the
            # sub-circuit name -- which surfaces as, depending on the part:
            #   Error: RefDes 'd8', element 'dD8': Invalid subckt definition
            #          name '<node>'            (discrete diode / mosfet / switch)
            #   Error: RefDes 'u41', element 'xu41_a': Invalid subckt definition
            #          name 'lm158_4__opamp__1'  (native op-amp carrier)
            # The card is still cached on the component as CiaCollString[5], so
            # recover it exactly the way _bind_port_native_model() does for
            # native carrier ports.
            comp.set(
                "Model",
                _bind_component_native_model(
                    comp, native_model_cache, model_sequence, ids,
                    circuit_item, elements, model_refs,
                ),
            )
        if comp is not None and comp.get("Model"):
            model_refs.add(comp.get("Model"))
        rank = placement_rank[component_index]
        x = grid_origin_x + (rank % grid_columns) * grid_step_x
        y = grid_origin_y + (rank // grid_columns) * grid_step_y
        if simple_profile:
            x, y = simple_profile['positions'][spec.refdes]
        elif opamp_profile:
            x, y = opamp_profile['positions'][spec.refdes]
        elif ce_profile:
            x, y = ce_profile['positions'][spec.refdes]
        elif rectifier_profile:
            x, y = rectifier_profile['positions'][spec.refdes]
        position_override = (component_positions or {}).get(spec.refdes)
        if position_override:
            x, y = float(position_override[0]), float(position_override[1])
        max_component_x = max(max_component_x, x + 126)
        max_component_y = max(max_component_y, y + 108)
        placements.append({"refdes": spec.refdes, "kind": spec.kind, "x": x, "y": y})
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
        if spec.kind == "V" and spec.value is not None and not spec.model:
            # Make the editable symbol disclose its waveform family.  The
            # carrier template ships with an AC example label (10Vpk/5kHz),
            # which is misleading for compact DC source syntax.
            symbol_display = f"DC {spec.value}"
        if spec.kind in NATIVE_OPAMP_MODELS:
            symbol_display = spec.model
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
        _set_symbol_labels(symbol_item, spec.kind, spec.refdes + ("A" if spec.kind in NATIVE_OPAMP_MODELS else ""), symbol_display)

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

        if ce_profile and spec.refdes in ce_profile["vertical"]:
            for key, value in {"M00":"0", "M01":"1", "M10":"-1", "M11":"0"}.items():
                sym.set("Transformer-" + key, value)
        if ce_profile and spec.kind == "C":
            for key, value in {"M00":"-1", "M01":"0", "M10":"0", "M11":"-1"}.items():
                sym.set("Transformer-" + key, value)
        if rectifier_profile and spec.kind in {'D','C','R'}:
            rotation = {'M00':'-1','M01':'0','M10':'0','M11':'-1'} if spec.kind == 'D' else {'M00':'0','M01':'-1','M10':'1','M11':'0'} if spec.kind == 'C' else {'M00':'0','M01':'1','M10':'-1','M11':'0'}
            for key,value in rotation.items():
                sym.set('Transformer-'+key,value)
        pin_info = _symbol_pin_info(symbol_item)
        if pin_info:
            center_x = (min(p['local_x'] for p in pin_info.values()) + max(p['local_x'] for p in pin_info.values())) / 2
            center_y = (min(p['local_y'] for p in pin_info.values()) + max(p['local_y'] for p in pin_info.values())) / 2
            if (ce_profile and (spec.kind == "C" or spec.refdes in ce_profile["vertical"])) or (rectifier_profile and spec.kind in {'D','C','R'}):
                # Counter-rotate only text; geometry and electrical pins keep
                # the native rotation. Values remain readable horizontally.
                a, b, c, d = [float(sym.get("Transformer-"+k)) for k in ("M00","M01","M10","M11")]
                for tag, offset in (("CIITSymTextCompName", -9), ("CIITSymTextCompValue", 9)):
                    label = sym.find("./Objects/Item/" + tag)
                    if label is not None:
                        px = center_x + (18 if spec.kind == "R" or rectifier_profile else 0)
                        py = center_y + (offset if spec.kind == "R" or rectifier_profile else -27 if offset < 0 else 27)
                        for key, value in {"M00":a,"M01":c,"M10":b,"M11":d,
                                           "M20":a*px+b*py,"M21":c*px+d*py}.items():
                            label.set("Transformer-"+key, f"{value:g}")
                        label.set("HorizontalAlign", "0" if spec.kind == "R" or rectifier_profile else "1")
            # Normalize vendor artwork saved with internal drawing offsets.
            dx, dy = round((center_x - 63) / 9) * 9, round((center_y - 54) / 9) * 9
            sym.set('Transformer-M20', f'{x-dx:g}')
            sym.set('Transformer-M21', f'{y-dy:g}')
            for pin in pin_info.values():
                pin['right_facing'] = pin['local_x'] > center_x
                pin['local_x'] -= dx
                pin['local_y'] -= dy
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
            _bind_port_native_model(
                port,
                port_model_cache,
                port_model_sequence,
                ids,
                circuit_item,
                elements,
                model_refs,
            )
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
                    "refdes": spec.refdes,
                    "right_facing": pin.get('right_facing', False),
                    "direction": pin.get('direction'),
                }
            )
            net_pin_points.setdefault(net_name, []).append((x + pin["local_x"], y + pin["local_y"]))
            port_items.append(port_item)

    node_items = [record["item"] for record in node_records.values()]
    for item in component_items + port_items + node_items:
        elements.append(item)
    elements.append(circuit_item)

    # Multisim writes <CiModel> objects LAST inside <Elements> -- after every
    # component, port, node and the CiCircuit itself -- and its own SaveAs
    # relocates ours to the end when we emit them first.  Match that layout so
    # a delivered .ms14 is already byte-comparable with a native re-save
    # (verified against samples/Non-InvertingOpAmp.ms14 and
    # samples/Mixed-signal/PulseWidthModulator.ms14).
    model_items = [el for el in list(elements) if el.get("Class") == "CiModel"]
    if model_items:
        for el in model_items:
            elements.remove(el)
        for el in model_items:
            elements.append(el)

    if models is not None:
        existing_model_refs = {
            item.get("CiID") for item in models.findall("./Item") if item.get("CiID")
        }
        for model_ref in sorted(model_refs - existing_model_refs):
            models.append(ET.Element("Item", {"CiID": model_ref}))

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

    # Wire-routing obstacles: a box around each component's PIN HULL (+12
    # clearance) instead of the coarse origin+126x108 body box.  The default
    # box swallowed neighbouring components whenever a custom
    # component_positions layout placed parts closer than ~130 pt apart and
    # made every pin escape "cross" an imaginary neighbour.  The hull hugs
    # the real pin span, so dense reference layouts route cleanly.
    pin_points_by_refdes: dict[str, list[tuple[float, float]]] = {}
    for pins in connections.values():
        for conn in pins:
            pin_points_by_refdes.setdefault(conn["refdes"], []).append(
                (float(conn["x"]), float(conn["y"]))
            )
    placement_boxes: list[dict[str, Any]] = []
    wire_fallbacks: list[tuple[str, str | None, str]] = []
    for placement in placements:
        hull = pin_points_by_refdes.get(placement["refdes"])
        if hull:
            xs = [p[0] for p in hull]
            ys = [p[1] for p in hull]
            placement_boxes.append({
                "refdes": placement["refdes"],
                "kind": placement.get("kind"),
                "x": min(xs) - 12,
                "y": min(ys) - 12,
                "width": (max(xs) - min(xs)) + 24,
                "height": (max(ys) - min(ys)) + 24,
            })
            placement["hull_box"] = [
                min(xs) - 12, min(ys) - 12,
                (max(xs) - min(xs)) + 24, (max(ys) - min(ys)) + 24,
            ]
        else:
            placement_boxes.append(dict(placement))

    for name, conns in connections.items():
        if len(conns) < 2:
            continue
        routing_obstacles = list(placement_boxes)
        # Reserve every other net's pin escape before routing the first net.
        # Otherwise an early supply wire can occupy a later signal's only exit.
        for other, pins in connections.items():
            if other == name:
                continue
            for pin in pins:
                ex, ey = pin_escape(pin, placement_boxes)
                px, py = float(pin["x"]), float(pin["y"])
                routing_obstacles.append({"refdes": "__pin__", "x": min(px, ex)-3,
                    "y": min(py, ey)-3, "width": abs(px-ex)+6, "height": abs(py-ey)+6})
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
            occupied = [segment for other, paths in net_wires.items() if other != name
                        for path in paths for segment in zip(path, path[1:])]
            try:
                path = route_pins(start, end, routing_obstacles, occupied)
            except ValueError:
                # Dense custom layouts (component_positions) can leave no
                # corridor that satisfies every pin-escape reservation.  Keep
                # the build alive with a plain orthogonal elbow -- always
                # connected, just possibly less elegant.  Connectivity comes
                # from the Connect references, not from the geometry.
                sx, sy = float(start["x"]), float(start["y"])
                ex, ey = float(end["x"]), float(end["y"])
                if abs(sx - ex) < 1e-9 or abs(sy - ey) < 1e-9:
                    path = [(sx, sy), (ex, ey)]
                else:
                    mid_y = (sy + ey) / 2
                    path = [(sx, sy), (sx, mid_y), (ex, mid_y), (ex, ey)]
                wire_fallbacks.append((name, start.get("refdes"), end_id))
            for px, py in path:
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
                path
            )

        wire_items: list[ET.Element] = []
        if len(conns) == 2:
            first, second = conns
            add_wire(first, second, second["extpin_id"])
        else:
            jx = sum(c["x"] for c in conns) / len(conns)
            jy = sum(c["y"] for c in conns) / len(conns)
            occupied = [segment for paths in net_wires.values() for path in paths
                        for segment in zip(path, path[1:])]
            jx, jy = junction_point(conns, routing_obstacles, occupied)
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
        net_pin_points,
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
        if spec.kind in NATIVE_OPAMP_MODELS:
            model_warnings.append(f"{spec.refdes}: local {spec.kind} / {NATIVE_OPAMP_MODELS[spec.kind]} macromodel, section A of a separate package; native topology and electrical acceptance are still required")
        if spec.kind == "OPAMP5":
            model_warnings.append(f"{spec.refdes}: ideal VCVS opamp, open-loop gain 100000; no supply clipping, bandwidth, current limit or noise model")
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
        if spec.kind == "TIMER8":
            model_warnings.append(
                f"{spec.refdes}: TIMER8 uses the user-local verified LM555CN native "
                "macro carrier; Multisim ReportNetlist may omit its internal body"
            )
            if (spec.model or "").upper() != "LM555CN":
                model_warnings.append(
                    f"{spec.refdes}: requested timer model {spec.model!r} is represented "
                    "by the local LM555CN carrier and requires explicit compatibility review"
                )
        if spec.kind == "DFF8":
            model_warnings.append(
                f"{spec.refdes}: DFF8 uses the user-local verified 7474N native "
                "A-section carrier; the second internal section is not instantiated"
            )
            if (spec.model or "").upper() != "7474N":
                model_warnings.append(
                    f"{spec.refdes}: requested flip-flop model {spec.model!r} is represented "
                    "by the local 7474N carrier and requires explicit compatibility review"
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

    # The native image API uses CircPrefs Sheet Width/Height, not just the
    # print-page dimensions. Include routes, labels and probe information boxes.
    extent_x = [max_component_x] + [p[0] for paths in net_wires.values() for path in paths for p in path]
    extent_y = [max_component_y] + [p[1] for paths in net_wires.values() for path in paths for p in path]
    sheet_width = max(960, math.ceil((max(extent_x) + 240) / 96) * 96)
    sheet_height = max(720, math.ceil((max(extent_y) + 144) / 96) * 96)
    # An explicit minimum sheet size (inches) wins over the auto-fit when it
    # is larger, so callers can reserve room for instruments or later edits.
    if min_sheet_size:
        min_w = math.ceil(float(min_sheet_size[0]) * 96)
        min_h = math.ceil(float(min_sheet_size[1]) * 96)
        sheet_width = max(sheet_width, min_w)
        sheet_height = max(sheet_height, min_h)
    sheet_settings = {"Sheet Width": sheet_width, "Sheet Height": sheet_height,
                      "Sheet Width In Inch": sheet_width / 96, "Sheet Height In Inch": sheet_height / 96}
    for setting in diagram.findall("./CircPrefs/CIITCircuitPrefs/Settings/Element"):
        key = setting.get("Key", "").removeprefix("&ASC")
        if key in sheet_settings and setting.find("Item") is not None:
            setting.find("Item").set("Value", _asc(f"{sheet_settings[key]:g}"))
    diagram.set(
        "PageWidth",
        f"{sheet_width / 96:g}",
    )
    diagram.set(
        "PageHeight",
        f"{sheet_height / 96:g}",
    )
    # Keep the GUI working area (DesignSheet WorkArea, nanometres, centred on
    # the origin) in sync with the grown page.  The minimal template ships a
    # fixed A4 work area, so a schematic wider than 297 mm was clipped on
    # screen even though CircPrefs already described the larger page.
    half_w_nm = round(sheet_width / 96 * 25.4 / 2 * 1_000_000)
    half_h_nm = round(sheet_height / 96 * 25.4 / 2 * 1_000_000)
    for sheet in root.iter("DesignSheet"):
        sheet.set("WorkAreaLeft", f"{-half_w_nm}")
        sheet.set("WorkAreaRight", f"{half_w_nm}")
        sheet.set("WorkAreaBottom", f"{-half_h_nm}")
        sheet.set("WorkAreaTop", f"{half_h_nm}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    tree = ET.ElementTree(root)
    if ce_profile or rectifier_profile:
        for probe in root.iter("CIITProbeExtComponent"):
            probe.set("Hidden", "1")
    write_native_xml(tree, output_path)

    if parsed.subcircuit_expansion_failures:
        editable_model_status = (
            "partial" if parsed.expanded_subcircuits else "carrier_only"
        )
    elif parsed.expanded_subcircuits:
        editable_model_status = "complete"
    else:
        editable_model_status = "not_applicable"

    layout_validation = validate_schematic_geometry(
        placement_boxes,
        net_wires,
        pin_points=net_pin_points,
        pin_exits={net: [((c['x'],c['y']), pin_escape(c,placement_boxes)) for c in pins]
                   for net,pins in connections.items()},
    )

    return {
        "xml": str(output_path),
        "layout_profile": "bridge_rectifier" if rectifier_profile else "common_emitter" if ce_profile else "source_series_shunt" if simple_profile else "pin_escape_obstacle_routing",
        "geometry": {"placements": placements, "wires": net_wires, "pins": net_pin_points},
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
        "layout_validation": layout_validation,
        "sheet": {
            "width_px_96dpi": sheet_width,
            "height_px_96dpi": sheet_height,
            "width_inch": sheet_width / 96,
            "height_inch": sheet_height / 96,
        },
        "wire_fallbacks": [
            {"net": net, "from": refdes, "to_endpoint": endpoint}
            for net, refdes, endpoint in wire_fallbacks
        ],
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
