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
