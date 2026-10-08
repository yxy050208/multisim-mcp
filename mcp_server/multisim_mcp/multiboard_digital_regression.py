"""Structured regression cases for digital logic split across two boards.

The cases in this module are deliberately small enough for the bounded
engineering planner while still exercising the production boundary:

* two board-local designs;
* two digital signal crossings plus a shared supply and return;
* an explicit, version-resolved ``HDR1X4`` on both boards; and
* a load on the remote board's logic output.

This module does not open Multisim.  The companion runner in ``tools/`` calls
the existing native multi-board acceptance gate for the actual save/reopen,
ReportNetlist, layout and DC/TRAN/AC checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .engineering_request import validate_engineering_request
from .engineering_planner import build_engineering_plan


@dataclass(frozen=True)
class DigitalMultiboardRegressionCase:
    """One bounded multi-board digital acceptance request."""

    case_id: str
    description: str
    request: dict[str, Any]
    interface_nets: tuple[str, ...]
    output_nets: tuple[str, ...]

    def validate(self) -> None:
        if not self.case_id or not self.case_id.replace("_", "").isalnum():
            raise ValueError(f"invalid multi-board digital case id: {self.case_id!r}")
        normalized = validate_engineering_request(self.request)
        if len(normalized["boards"]) != 2:
            raise ValueError(f"{self.case_id}: exactly two boards are required")
        if not self.interface_nets:
            raise ValueError(f"{self.case_id}: at least one interface net is required")
        if not self.output_nets:
            raise ValueError(f"{self.case_id}: at least one output net is required")
        component_refs = {item["refdes"] for item in normalized["components"]}
        required_refs = {"VDD1", "VCLK", "VDATA", "A1", "A2", "A3", "A4", "RLOAD"}
        missing = sorted(required_refs - component_refs)
        if missing:
            raise ValueError(f"{self.case_id}: required components missing: {missing}")
        if not normalized.get("connectors"):
            raise ValueError(f"{self.case_id}: an explicit physical connector is required")
        connector = normalized["connectors"][0]
        pins = connector.get("pins", [])
        if str(connector.get("part", "")).upper() != "HDR1X4" or len(pins) != 4:
            raise ValueError(f"{self.case_id}: the case must use a four-pin HDR1X4")
        pin_nets = tuple(str(item.get("net", "")) for item in sorted(pins, key=lambda item: item.get("number", 0)))
        if pin_nets != ("clk", "data", "high", "0"):
            raise ValueError(f"{self.case_id}: connector pin order is not deterministic")
        plan = build_engineering_plan(normalized)
        candidates = plan.get("multiboard_candidates", [])
        if not candidates or not any(item.get("structurally_ready") is True for item in candidates):
            raise ValueError(f"{self.case_id}: request has no structurally ready partition")

    def manifest(self) -> dict[str, Any]:
        self.validate()
        return {
            "case_id": self.case_id,
            "description": self.description,
            "request": self.request,
            "interface_nets": list(self.interface_nets),
            "output_nets": list(self.output_nets),
        }


def _split_logic_load_request() -> dict[str, Any]:
    """Return a two-board logic chain with a remote output load.

    Board ``logic`` owns the pulse sources and the first two gates.  Board
    ``io`` receives the two digital signals through J1; explicit voltage
    fixtures reproduce those source waveforms for the board-local simulation.
    The second board performs another two-gate stage and drives a real load.
    The remaining two J1 pins carry the shared supply and return.
    """

    return {
        "schema_version": 1,
        "title": "Two-board digital chain with remote load",
        "application": "complex digital multi-board regression",
        "boards": [
            {"id": "logic", "role": "primary"},
            {"id": "io", "role": "secondary"},
        ],
        "constraints": [
            {"text": "all cross-board nets must use the declared HDR1X4 pins"},
            {"text": "remote logic output must retain its resistive load after reopen"},
        ],
        "objectives": [
            {"metric": "topology_integrity", "direction": "maximize"},
            {"metric": "layout_crossings", "direction": "minimize"},
        ],
        "experiments": [
            {"type": "dc", "outputs": ["clk", "data", "io_out"]},
            {"type": "tran", "outputs": ["clk", "data", "io_out"]},
            {"type": "ac", "outputs": ["clk", "data", "io_out"]},
        ],
        "components": [
            {"refdes": "VDD1", "kind": "V", "nodes": ["high", "0"], "value": "5", "board": "logic"},
            # ``AC 1`` keeps the same pulse sources useful for the optional
            # AC analysis; the native pulse carrier preserves both clauses.
            {"refdes": "VCLK", "kind": "V", "nodes": ["clk", "0"], "model": "PULSE(0 5 0 1n 1n 10u 20u) AC 1", "board": "logic"},
            {"refdes": "VDATA", "kind": "V", "nodes": ["data", "0"], "model": "PULSE(0 5 0 1n 1n 20u 40u) AC 1", "board": "logic"},
            {"refdes": "A1", "kind": "DNOT4", "nodes": ["data", "n1", "high", "0"], "model": "NOT", "board": "logic"},
            {"refdes": "A2", "kind": "DAND5", "nodes": ["n1", "clk", "logic_out", "high", "0"], "model": "AND2", "board": "logic"},
            {"refdes": "A3", "kind": "DNOT4", "nodes": ["data", "n2", "high", "0"], "model": "NOT", "board": "io"},
            {"refdes": "A4", "kind": "DAND5", "nodes": ["n2", "clk", "io_out", "high", "0"], "model": "AND2", "board": "io"},
            {"refdes": "RLOAD", "kind": "R", "nodes": ["io_out", "0"], "value": "1k", "board": "io"},
        ],
        "connectors": [{
            "id": "J1",
            "part": "HDR1X4",
            "boards": ["logic", "io"],
            "instances": [
                {"board": "logic", "refdes": "J1"},
                {"board": "io", "refdes": "J1"},
            ],
            "pins": [
                {"number": 1, "net": "clk", "signal_type": "clock", "direction": "bidirectional"},
                {"number": 2, "net": "data", "signal_type": "digital", "direction": "bidirectional"},
                {"number": 3, "net": "high", "signal_type": "power", "direction": "power"},
                {"number": 4, "net": "0", "signal_type": "ground", "direction": "passive"},
            ],
        }],
        "fixtures": [
            {"id": "logic-clk", "board_id": "logic", "kind": "observation", "net": "clk"},
            {"id": "logic-clk-anchor", "board_id": "logic", "kind": "resistor_termination", "net": "clk", "reference_net": "0", "refdes": "RFLC", "value": "1G"},
            {"id": "logic-data", "board_id": "logic", "kind": "observation", "net": "data"},
            {"id": "logic-data-anchor", "board_id": "logic", "kind": "resistor_termination", "net": "data", "reference_net": "0", "refdes": "RFLD", "value": "1G"},
            {"id": "logic-ground", "board_id": "logic", "kind": "ground_reference", "net": "0"},
            {"id": "logic-high-anchor", "board_id": "logic", "kind": "resistor_termination", "net": "high", "reference_net": "0", "refdes": "RFLH", "value": "1G"},
            {"id": "io-clk-source", "board_id": "io", "kind": "voltage_source", "net": "clk", "reference_net": "0", "refdes": "VFIXCLK", "model": "PULSE(0 5 0 1n 1n 10u 20u) AC 1"},
            {"id": "io-clk", "board_id": "io", "kind": "observation", "net": "clk"},
            {"id": "io-data-source", "board_id": "io", "kind": "voltage_source", "net": "data", "reference_net": "0", "refdes": "VFIXDATA", "model": "PULSE(0 5 0 1n 1n 20u 40u) AC 1"},
            {"id": "io-data", "board_id": "io", "kind": "observation", "net": "data"},
            {"id": "io-out", "board_id": "io", "kind": "observation", "net": "io_out"},
            {"id": "io-ground", "board_id": "io", "kind": "ground_reference", "net": "0"},
            {"id": "io-high-anchor", "board_id": "io", "kind": "resistor_termination", "net": "high", "reference_net": "0", "refdes": "RFIH", "value": "1G"},
        ],
    }


def _split_mixed_logic_request() -> dict[str, Any]:
    """Return a second two-board case covering the remaining gate families.

    The physical boundary is the same as ``split_logic_load`` so that a
    failure can be attributed to the gate model rather than to a new
    connector or fixture arrangement.  The board-local logic intentionally
    uses OR/XOR/NOR/XNOR models and keeps the remote 1 kOhm load.
    """
    request = _split_logic_load_request()
    request.update({
        "title": "Two-board mixed-gate chain with remote load",
        "application": "mixed digital gate multi-board regression",
        "constraints": [
            {"text": "all cross-board nets must use the declared HDR1X4 pins"},
            {"text": "OR, XOR, NOR and XNOR model objects must survive reopen"},
            {"text": "remote mixed-gate output must retain its resistive load after reopen"},
        ],
    })
    replacements = {
        "A1": ("DOR5", ["data", "clk", "logic_mid", "high", "0"], "OR2"),
        "A2": ("DXOR5", ["logic_mid", "data", "logic_out", "high", "0"], "XOR2"),
        "A3": ("DNOR5", ["data", "clk", "io_mid", "high", "0"], "NOR2"),
        "A4": ("DXNOR5", ["io_mid", "clk", "io_out", "high", "0"], "XNOR2"),
    }
    for component in request["components"]:
        replacement = replacements.get(component["refdes"])
        if replacement:
            component["kind"], component["nodes"], component["model"] = replacement
    request["experiments"] = [
        {"type": "dc", "outputs": ["clk", "data", "io_out"]},
        {"type": "tran", "outputs": ["clk", "data", "io_out"]},
        {"type": "ac", "outputs": ["clk", "data", "io_out"]},
    ]
    return request


def multiboard_digital_regression_matrix() -> tuple[DigitalMultiboardRegressionCase, ...]:
    cases = (
        DigitalMultiboardRegressionCase(
            case_id="split_logic_load",
            description="Two-board NOT/AND/NOT/AND chain with two digital crossings, shared supply and return, explicit remote input fixtures, and a 1 kOhm load.",
            request=_split_logic_load_request(),
            interface_nets=("clk", "data"),
            output_nets=("io_out",),
        ),
        DigitalMultiboardRegressionCase(
            case_id="split_mixed_logic",
            description="Two-board OR/XOR/NOR/XNOR chain with two digital crossings, shared supply and return, explicit remote input fixtures, and a 1 kOhm load.",
            request=_split_mixed_logic_request(),
            interface_nets=("clk", "data"),
            output_nets=("io_out",),
        ),
    )
    for case in cases:
        case.validate()
    return cases


def select_multiboard_digital_regression_cases(case_id: str | None = None) -> tuple[DigitalMultiboardRegressionCase, ...]:
    cases = multiboard_digital_regression_matrix()
    if case_id is None:
        return cases
    selected = tuple(item for item in cases if item.case_id == case_id)
    if not selected:
        known = ", ".join(item.case_id for item in cases)
        raise ValueError(f"unknown multi-board digital case {case_id!r}; choose from {known}")
    return selected


__all__ = [
    "DigitalMultiboardRegressionCase",
    "multiboard_digital_regression_matrix",
    "select_multiboard_digital_regression_cases",
]
