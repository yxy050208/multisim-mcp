"""Executable regression cases for mixed digital/analog Multisim designs."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from typing import Any

from .schematic_builder import parse_netlist


@dataclass(frozen=True)
class ConverterTransferContract:
    """Sampled ADC-to-DAC transfer contract; bit nets are ordered LSB first."""

    analog_input: str
    digital_bits: tuple[str, ...]
    analog_output: str
    low_voltage: float = 0.0
    high_voltage: float = 5.0
    voltage_tolerance: float = 0.02
    boundary_guard_voltage: float = 0.01

    def validate(self) -> None:
        nets = (self.analog_input, *self.digital_bits, self.analog_output)
        if any(not isinstance(net, str) or not net for net in nets):
            raise ValueError("converter contract requires non-empty net names")
        if not 1 <= len(self.digital_bits) <= 8 or len(set(nets)) != len(nets):
            raise ValueError("converter contract requires distinct input, bit and output nets")
        values = (
            self.low_voltage, self.high_voltage,
            self.voltage_tolerance, self.boundary_guard_voltage,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("converter contract voltages must be finite")
        step = (self.high_voltage - self.low_voltage) / (1 << len(self.digital_bits))
        if step <= 0 or not 0 < self.voltage_tolerance < step / 2:
            raise ValueError("converter contract requires ordered rails and bounded tolerance")
        if not 0 <= self.boundary_guard_voltage < step / 2:
            raise ValueError("converter boundary guard must be smaller than half a step")


def evaluate_converter_transfer(
    contract: ConverterTransferContract,
    series: dict[str, list[float]],
) -> dict[str, Any]:
    """Check all codes against independent quantizer and reconstruction equations."""
    contract.validate()
    nets = (contract.analog_input, *contract.digital_bits, contract.analog_output)
    values = [series.get(net, []) for net in nets]
    checks = {
        "finite_samples": bool(values[0]) and all(
            len(column) == len(values[0]) and all(math.isfinite(value) for value in column)
            for column in values
        ),
        "all_codes_observed": False,
        "digital_levels": False,
        "adc_code_matches": False,
        "dac_weighting_matches": False,
    }
    result: dict[str, Any] = {"passed": False, "checks": checks}
    if not checks["finite_samples"]:
        result["reason"] = "missing, non-finite or misaligned converter samples"
        return result
    levels = 1 << len(contract.digital_bits)
    low, high = contract.low_voltage, contract.high_voltage
    span = high - low
    thresholds = [low + span * code / levels for code in range(1, levels)]
    counts = [0] * levels
    expected_counts = [0] * levels
    mismatches: list[dict[str, Any]] = []
    digital_levels = adc_matches = dac_matches = True
    max_dac_error = 0.0
    excluded = 0
    for index, analog in enumerate(values[0]):
        # Solver samples at a discontinuity do not establish static transfer
        # accuracy. The exclusion window is explicit and narrower than a bin.
        if any(abs(analog - threshold) <= contract.boundary_guard_voltage for threshold in thresholds):
            excluded += 1
            continue
        expected_code = sum(analog > threshold for threshold in thresholds)
        bits = [column[index] for column in values[1:-1]]
        valid_bits = all(
            min(abs(value - low), abs(value - high)) <= contract.voltage_tolerance
            for value in bits
        )
        code = sum((value > (low + high) / 2) << bit for bit, value in enumerate(bits))
        expected_output = low + span * code / (levels - 1)
        error = abs(values[-1][index] - expected_output)
        counts[code] += 1
        expected_counts[expected_code] += 1
        digital_levels &= valid_bits
        adc_matches &= code == expected_code
        dac_matches &= error <= contract.voltage_tolerance
        max_dac_error = max(max_dac_error, error)
        if len(mismatches) < 8 and (
            not valid_bits or code != expected_code or error > contract.voltage_tolerance
        ):
            mismatches.append({
                "sample": index, "input_voltage": analog, "expected_code": expected_code,
                "observed_code": code, "dac_error_voltage": error,
            })
    checks.update({
        "all_codes_observed": all(counts) and all(expected_counts),
        "digital_levels": digital_levels,
        "adc_code_matches": adc_matches,
        "dac_weighting_matches": dac_matches,
    })
    result.update({
        "passed": all(checks.values()),
        "sample_count": len(values[0]),
        "checked_samples": sum(counts),
        "boundary_samples_excluded": excluded,
        "observed_codes": [code for code, count in enumerate(counts) if count],
        "samples_per_code": counts,
        "expected_samples_per_code": expected_counts,
        "max_dac_error_voltage": max_dac_error,
        "mismatch_examples": mismatches,
        "evidence": "sampled ADC thresholds and DAC full-scale binary reconstruction; boundary guard applied",
    })
    return result


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
    output_pairs: tuple[tuple[str, str], ...] = ()
    converter_contract: ConverterTransferContract | None = None

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
        if len(set(self.output_nets)) != len(self.output_nets):
            raise ValueError(f"{self.case_id}: output nets must be unique")
        if not self.output_pairs and len(self.output_nets) % 2:
            raise ValueError(f"{self.case_id}: unpaired outputs require explicit output pairs")
        if self.max_crossings_per_wire <= 0 or self.max_points <= 0:
            raise ValueError(f"{self.case_id}: invalid geometry or sampling limit")
        if any(
            len(pair) != 2 or any(net not in self.output_nets for net in pair)
            for pair in self.output_pairs
        ):
            raise ValueError(f"{self.case_id}: output pairs must refer to observed nets")
        if self.converter_contract is not None:
            self.converter_contract.validate()
            contract = self.converter_contract
            contract_nets = (contract.analog_input, *contract.digital_bits, contract.analog_output)
            if any(net not in self.output_nets for net in contract_nets):
                raise ValueError(f"{self.case_id}: converter contract requires observed nets")

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
        HybridRegressionCase(
            "adc_rc_bridge",
            "One-bit ADC behavioral bridge driving a first-order RC load.",
            """VDD high 0 DC 5
VAN analog 0 PULSE(0 5 0 1n 1n 40u 80u)
XADC analog digital high 0 @ADC1 THRESHOLD=.5
R1 digital filt 1k
C1 filt 0 1u
RLOAD filt 0 100k
.end
""",
            "tran 1u 160u",
            ("digital", "filt"),
            ("VDD", "VAN", "XADC", "R1", "C1", "RLOAD"),
        ),
        HybridRegressionCase(
            "adc4_rc_bridge",
            "Four-bit ADC behavioral bridge with independent RC loads on all output bits.",
            """VDD high 0 DC 5
VAN analog 0 PULSE(0 5 0 1n 1n 40u 80u)
XADC analog d0 d1 d2 d3 high 0 @ADC4
R3 d3 f3 1k
C3 f3 0 1u
RL3 f3 0 100k
R2 d2 f2 1k
C2 f2 0 1u
RL2 f2 0 100k
R1 d1 f1 1k
C1 f1 0 1u
RL1 f1 0 100k
R0 d0 f0 1k
C0 f0 0 1u
RL0 f0 0 100k
.end
""",
            "tran 1u 160u",
            ("d3", "f3", "d2", "f2", "d1", "f1", "d0", "f0"),
            (
                "VDD", "VAN", "XADC", "R3", "C3", "RL3", "R2", "C2",
                "RL2", "R1", "C1", "RL1", "R0", "C0", "RL0",
            ),
        ),
        HybridRegressionCase(
            "dac4_rc_bridge",
            "Four-bit DAC behavioral bridge with binary-weighted native output.",
            """VDD high 0 DC 5
V3 d3 0 PULSE(0 5 0 1n 1n 80u 160u)
V2 d2 0 PULSE(0 5 0 1n 1n 40u 80u)
V1 d1 0 PULSE(0 5 0 1n 1n 20u 40u)
V0 d0 0 PULSE(0 5 0 1n 1n 10u 20u)
XDAC d0 d1 d2 d3 analog_out high 0 @DAC4
R3 d3 f3 1k
C3 f3 0 1u
RL3 f3 0 100k
R2 d2 f2 1k
C2 f2 0 1u
RL2 f2 0 100k
R1 d1 f1 1k
C1 f1 0 1u
RL1 f1 0 100k
R0 d0 f0 1k
C0 f0 0 1u
RL0 f0 0 100k
RO analog_out filt 1k
CO filt 0 1u
RLO filt 0 100k
.end
""",
            "tran 1u 160u",
            ("d3", "f3", "d2", "f2", "d1", "f1", "d0", "f0", "analog_out", "filt"),
            (
                "VDD", "V3", "V2", "V1", "V0", "XDAC", "R3", "C3", "RL3",
                "R2", "C2", "RL2", "R1", "C1", "RL1", "R0", "C0", "RL0",
                "RO", "CO", "RLO",
            ),
        ),
        HybridRegressionCase(
            "adc4_dac4_transfer",
            "All sixteen ADC codes, DAC reconstruction and RC output under a triangular input sweep.",
            """VDD high 0 DC 5
VAN analog 0 PULSE(0 5 0 160u 160u 10u 340u)
XADC analog d0 d1 d2 d3 high 0 @ADC4
XDAC d0 d1 d2 d3 analog_out high 0 @DAC4
RO analog_out filt 1k
CO filt 0 10n
RLO filt 0 100k
.end
""",
            "tran 1u 340u",
            ("d0", "d1", "d2", "d3", "analog", "analog_out", "filt"),
            ("VDD", "VAN", "XADC", "XDAC", "RO", "CO", "RLO"),
            output_pairs=(("d0", "filt"), ("d1", "filt"), ("d2", "filt"), ("d3", "filt")),
            converter_contract=ConverterTransferContract("analog", ("d0", "d1", "d2", "d3"), "analog_out"),
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
    "ConverterTransferContract",
    "evaluate_converter_transfer",
    "HybridRegressionCase",
    "hybrid_regression_matrix",
    "select_hybrid_regression_cases",
]
