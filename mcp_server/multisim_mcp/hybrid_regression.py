"""Executable regression cases for mixed digital/analog Multisim designs."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .schematic_builder import parse_netlist


@dataclass(frozen=True)
class HybridRegressionCase:
    """One mixed-signal acceptance case with both logic and analog outputs."""

    case_id: str
    description: str
    netlist: str
    commands: str
    output_nets: tuple[str, ...]
    required_components: tuple[str, ...]
    max_crossings_per_wire: float = 2.0
    max_points: int = 2000

    def validate(self) -> None:
        if not self.case_id or not self.case_id.replace("_", "").isalnum():
            raise ValueError(f"invalid hybrid regression case id: {self.case_id!r}")
        if not self.netlist.strip().lower().endswith(".end"):
            raise ValueError(f"{self.case_id}: netlist must end with .end")
        parse_netlist(self.netlist)
        refs = {
            line.split()[0].casefold()
            for line in self.netlist.splitlines()
            if line.strip() and not line.lstrip().startswith(("*", ";", "."))
        }
        missing = [item for item in self.required_components if item.casefold() not in refs]
        if missing:
            raise ValueError(f"{self.case_id}: required components missing: {missing}")
        if not self.output_nets:
            raise ValueError(f"{self.case_id}: at least one output net is required")
        if self.max_crossings_per_wire <= 0 or self.max_points <= 0:
            raise ValueError(f"{self.case_id}: invalid geometry or sampling limit")

    def manifest(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["output_nets"] = list(self.output_nets)
        payload["required_components"] = list(self.required_components)
        return payload


def hybrid_regression_matrix() -> tuple[HybridRegressionCase, ...]:
    """Return the initial mixed-signal acceptance matrix."""
    cases = (
        HybridRegressionCase(
            "not_rc_load",
            "Digital NOT output driving a first-order RC network and resistive load.",
            """VDD high 0 DC 5
VIN din 0 PULSE(0 5 0 1n 1n 10u 20u)
A1 din dout high 0 NOT
R1 dout filt 1k
C1 filt 0 1u
RLOAD filt 0 100k
.end
""",
            "tran 1u 100u",
            ("dout", "filt"),
            ("VDD", "VIN", "A1", "R1", "C1", "RLOAD"),
        ),
        HybridRegressionCase(
            "logic_chain_rc_load",
            "Three-stage digital logic chain driving a first-order RC network and load.",
            """VDD high 0 DC 5
VIN din 0 PULSE(0 5 0 1n 1n 10u 20u)
VEN enable 0 PULSE(0 5 0 1n 1n 20u 40u)
VBP bypass 0 PULSE(0 5 0 1n 1n 30u 60u)
A1 din n1 high 0 NOT
A2 n1 enable n2 high 0 AND2
A3 n2 bypass dout high 0 OR2
R1 dout filt 1k
C1 filt 0 1u
RLOAD filt 0 100k
.end
""",
            "tran 1u 100u",
            ("dout", "filt"),
            ("VDD", "VIN", "VEN", "VBP", "A1", "A2", "A3", "R1", "C1", "RLOAD"),
        ),
        HybridRegressionCase(
            "counter_q0_rc_load",
            "Four-bit counter least-significant output driving a first-order RC load.",
            """VDD high 0 DC 5
VCLK clk 0 PULSE(0 5 0 1n 1n 20u 40u)
VRESET reset 0 DC 0
XCNT clk reset q0 q1 q2 q3 high 0 @COUNTER4
R1 q0 filt 1k
C1 filt 0 1u
RLOAD filt 0 100k
.end
""",
            "tran 1u 100u",
            ("q0", "filt"),
            ("VDD", "VCLK", "VRESET", "XCNT", "R1", "C1", "RLOAD"),
        ),
        HybridRegressionCase(
            "shift_s0_rc_load",
            "Four-bit serial-in parallel-out shift-register output driving a first-order RC load.",
            """VDD high 0 DC 5
VDATA data 0 PULSE(0 5 0 1n 1n 40u 80u)
VCLK clk 0 PULSE(0 5 0 1n 1n 10u 20u)
VRESET reset 0 DC 0
XSR data clk reset s0 s1 s2 s3 high 0 @SHIFT_REGISTER4
R1 s0 filt 1k
C1 filt 0 1u
RLOAD filt 0 100k
.end
""",
            "tran 1u 160u",
            ("s0", "filt"),
            ("VDD", "VDATA", "VCLK", "VRESET", "XSR", "R1", "C1", "RLOAD"),
        ),
        HybridRegressionCase(
            "diode_rc_shaper",
            "1N4001GP diode pulse shaper driving a first-order RC load.",
            """VDD high 0 DC 5
VIN raw 0 PULSE(0 5 0 1n 1n 40u 80u)
D1 raw filt 1N4001GP
C1 filt 0 1u
RLOAD filt 0 100k
.end
""",
            "tran 1u 160u",
            ("raw", "filt"),
            ("VDD", "VIN", "D1", "C1", "RLOAD"),
        ),
        HybridRegressionCase(
            "counter_q0_q1_rc_load",
            "Four-bit counter with independent RC loads on two output bits.",
            """VDD high 0 DC 5
VCLK clk 0 PULSE(0 5 0 1n 1n 20u 40u)
VRESET reset 0 DC 0
XCNT clk reset q0 q1 q2 q3 high 0 @COUNTER4
R0 q0 filt0 1k
C0 filt0 0 1u
RLOAD0 filt0 0 100k
R1 q1 filt1 1k
C1 filt1 0 1u
RLOAD1 filt1 0 100k
.end
""",
            "tran 1u 160u",
            ("q0", "filt0", "q1", "filt1"),
            ("VDD", "VCLK", "VRESET", "XCNT", "R0", "C0", "RLOAD0", "R1", "C1", "RLOAD1"),
        ),
        HybridRegressionCase(
            "vcvs_rc_bridge",
            "Voltage-controlled voltage source driving a first-order RC load.",
            """VCTRL ctrl 0 PULSE(0 5 0 1n 1n 40u 80u)
E1 raw 0 ctrl 0 1
R1 raw filt 1k
C1 filt 0 1u
RLOAD filt 0 100k
.end
""",
            "tran 1u 160u",
            ("raw", "filt"),
            ("VCTRL", "E1", "R1", "C1", "RLOAD"),
        ),
        HybridRegressionCase(
            "vccs_rc_bridge",
            "Voltage-controlled current source driving a first-order RC load.",
            """VCTRL ctrl 0 PULSE(0 5 0 1n 1n 40u 80u)
G1 0 raw ctrl 0 1m
R1 raw filt 1k
C1 filt 0 1u
RLOAD filt 0 100k
.end
""",
            "tran 1u 160u",
            ("raw", "filt"),
            ("VCTRL", "G1", "R1", "C1", "RLOAD"),
        ),
        HybridRegressionCase(
            "dac_rc_bridge",
            "One-bit DAC behavioral bridge driving a first-order RC load.",
            """VDD high 0 DC 5
VDIN din 0 PULSE(0 5 0 1n 1n 40u 80u)
XDAC din raw high 0 @DAC1
R1 raw filt 1k
C1 filt 0 1u
RLOAD filt 0 100k
.end
""",
            "tran 1u 160u",
            ("raw", "filt"),
            ("VDD", "VDIN", "XDAC", "R1", "C1", "RLOAD"),
        ),
    )
    for case in cases:
        case.validate()
    return cases


def select_hybrid_regression_cases(case_id: str | None = None) -> tuple[HybridRegressionCase, ...]:
    cases = hybrid_regression_matrix()
    if case_id is None:
        return cases
    selected = tuple(item for item in cases if item.case_id == case_id)
    if not selected:
        raise ValueError(f"unknown hybrid regression case {case_id!r}")
    return selected


__all__ = [
    "HybridRegressionCase",
    "hybrid_regression_matrix",
    "select_hybrid_regression_cases",
]
