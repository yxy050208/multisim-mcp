"""Executable regression cases for generated digital Multisim designs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .schematic_builder import parse_netlist


@dataclass(frozen=True)
class DigitalRegressionCase:
    """One end-to-end digital design acceptance case."""

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
            raise ValueError(f"invalid digital regression case id: {self.case_id!r}")
        if not self.netlist.strip().lower().endswith(".end"):
            raise ValueError(f"{self.case_id}: netlist must end with .end")
        parsed = parse_netlist(self.netlist)
        # Adapter invocations (XDFF/XCNT/XSR) are intentionally expanded by
        # ``parse_netlist`` into generated A-device references.  The matrix
        # records the source-level references, so validate those against the
        # original lines while still parsing the complete netlist above.
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


def digital_regression_matrix() -> tuple[DigitalRegressionCase, ...]:
    """Return the digital acceptance matrix in execution order."""
    cases = (
        DigitalRegressionCase(
            "logic_chain_load",
            "Three-stage combinational chain with a resistive output load.",
            """VDD high 0 DC 5
VIN din 0 PULSE(0 5 0 1n 1n 10u 20u)
VEN enable 0 PULSE(0 5 0 1n 1n 20u 40u)
VBP bypass 0 PULSE(0 5 0 1n 1n 30u 60u)
A1 din n1 high 0 NOT
A2 n1 enable n2 high 0 AND2
A3 n2 bypass dout high 0 OR2
RLOAD dout 0 1k
.end
""",
            "tran 1u 100u",
            ("dout", "n2"),
            ("VDD", "VIN", "VEN", "VBP", "A1", "A2", "A3", "RLOAD"),
        ),
        DigitalRegressionCase(
            "dff_load",
            "D flip-flop adapter with a resistive Q load.",
            """VDD high 0 DC 5
VDATA data 0 PULSE(0 5 0 10n 10n 40u 80u)
VCLK clk 0 PULSE(0 5 0 1n 1n 10u 20u)
XDFF data clk high 0 q qb high 0 @DFF
RLOAD q 0 1k
.end
""",
            "tran 1u 100u",
            ("q", "qb"),
            ("VDD", "VDATA", "VCLK", "XDFF", "RLOAD"),
        ),
        DigitalRegressionCase(
            "counter4_load",
            "Four-bit asynchronous counter adapter with four output loads.",
            """VDD high 0 DC 5
VCLK clk 0 PULSE(0 5 0 1n 1n 20u 40u)
VRESET reset 0 DC 0
XCNT clk reset q0 q1 q2 q3 high 0 @COUNTER4
RLOAD0 q0 0 1k
RLOAD1 q1 0 1k
RLOAD2 q2 0 1k
RLOAD3 q3 0 1k
.end
""",
            "tran 1u 100u",
            ("q0", "q1", "q2", "q3"),
            ("VDD", "VCLK", "VRESET", "XCNT", "RLOAD0", "RLOAD1", "RLOAD2", "RLOAD3"),
        ),
        DigitalRegressionCase(
            "shift4_load",
            "Four-bit serial-in parallel-out shift register with four output loads.",
            """VDD high 0 DC 5
VDATA data 0 PULSE(0 5 0 1n 1n 40u 80u)
VCLK clk 0 PULSE(0 5 0 1n 1n 10u 20u)
VRESET reset 0 DC 0
XSR data clk reset s0 s1 s2 s3 high 0 @SHIFT_REGISTER4
RLOAD0 s0 0 1k
RLOAD1 s1 0 1k
RLOAD2 s2 0 1k
RLOAD3 s3 0 1k
.end
""",
            "tran 1u 100u",
            ("s0", "s1", "s2", "s3"),
            ("VDD", "VDATA", "VCLK", "VRESET", "XSR", "RLOAD0", "RLOAD1", "RLOAD2", "RLOAD3"),
        ),
        DigitalRegressionCase(
            "counter4_decode_load",
            "Counter outputs fanned into four inverters and four output loads.",
            """VDD high 0 DC 5
VCLK clk 0 PULSE(0 5 0 1n 1n 20u 40u)
VRESET reset 0 DC 0
XCNT clk reset q0 q1 q2 q3 high 0 @COUNTER4
A5 q0 d0 high 0 NOT
A6 q1 d1 high 0 NOT
A7 q2 d2 high 0 NOT
A8 q3 d3 high 0 NOT
RLOAD0 d0 0 1k
RLOAD1 d1 0 1k
RLOAD2 d2 0 1k
RLOAD3 d3 0 1k
.end
""",
            "tran 1u 100u",
            ("d0", "d1", "d2", "d3"),
            ("VDD", "VCLK", "VRESET", "XCNT", "A5", "A6", "A7", "A8", "RLOAD0", "RLOAD1", "RLOAD2", "RLOAD3"),
        ),
    )
    for case in cases:
        case.validate()
    return cases


def select_digital_regression_cases(case_id: str | None = None) -> tuple[DigitalRegressionCase, ...]:
    cases = digital_regression_matrix()
    if case_id is None:
        return cases
    selected = tuple(item for item in cases if item.case_id == case_id)
    if not selected:
        known = ", ".join(item.case_id for item in cases)
        raise ValueError(f"unknown digital regression case {case_id!r}; choose from {known}")
    return selected


__all__ = [
    "DigitalRegressionCase",
    "digital_regression_matrix",
    "select_digital_regression_cases",
]
