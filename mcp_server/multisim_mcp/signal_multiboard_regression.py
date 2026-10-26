"""Public requirement matrix for the bounded ideal two-board signal profile.

Cases describe requirements and test stimuli. They neither grant approval nor
claim native evidence. The runner consumes separately reviewed preview/approval
files and uses the existing native gate, without a new MCP tool.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .executable_approvals import validate_executable_netlist_approval
from .linear_reference import scalar, validated_components


@dataclass(frozen=True)
class SignalMultiboardRegressionCase:
    case_id: str
    description: str
    specification_inputs: dict[str, float]
    experiment_fixture: dict[str, Any]

    def manifest(self) -> dict[str, Any]:
        return copy.deepcopy({
            "case_id": self.case_id, "description": self.description,
            "specification_inputs": self.specification_inputs,
            "experiment_fixture": self.experiment_fixture,
            "analyses": ["dc", "ac"], "native_verified": False,
        })

    def validate_preview(self, preview: Mapping[str, Any], approval: Mapping[str, Any]) -> None:
        """Reject a valid approval for the wrong case, before any native work."""
        validate_executable_netlist_approval(preview, approval)
        inputs = self.specification_inputs
        lo, hi = (Decimal(str(inputs[k])) for k in ("input_min_v", "input_max_v"))
        out_lo, out_hi = (Decimal(str(inputs[k])) for k in ("output_min_v", "output_max_v"))
        gain = (out_hi - out_lo) / (hi - lo)
        expected = {
            "gain_v_per_v": float(gain), "offset_v": float(out_lo - gain * lo),
            "input_min_v": float(lo), "input_max_v": float(hi),
            "estimated_output_min_v": float(out_lo), "estimated_output_max_v": float(out_hi),
        }
        synthesis = preview.get("synthesis", {})
        target = synthesis.get("target_transfer", {})
        if (synthesis.get("template") != "ideal-opamp-affine-low-pass-v1"
                or preview.get("experiment_fixture", {}).get("contract") != self.experiment_fixture
                or any(target.get(k) != v for k, v in expected.items())
                or synthesis.get("estimated_cutoff_hz") != inputs["cutoff_frequency_hz"]):
            raise ValueError(f"{self.case_id}: approved preview differs from regression requirements")
        supplies = [p for p in validated_components(preview["spice_netlist"]) if p.refdes == "VCC"]
        if len(supplies) != 1 or not math.isclose(scalar(supplies[0].value), inputs["supply_voltage_v"], rel_tol=1e-12):
            raise ValueError(f"{self.case_id}: supply differs from regression requirements")


def signal_multiboard_regression_cases() -> tuple[SignalMultiboardRegressionCase, ...]:
    base = {
        "supply_voltage_v": 5, "input_min_v": 0, "input_max_v": 5,
        "output_min_v": 0.5, "output_max_v": 2.5, "source_impedance_ohm": 100,
        "load_impedance_ohm": 100000, "cutoff_frequency_hz": 1000,
    }
    definitions = [
        ("nominal", "Gain 0.4, positive bias, nominal source impedance", {}, 2.5, 1, 0),
        ("zero_source_phase90", "Direct ideal source with +90 degree AC phase",
         {"source_impedance_ohm": 0}, 2.5, 1, 90),
        ("gain3_phase_minus45", "Gain 3 with a 2 kHz cutoff and -45 degree input phase",
         {"input_max_v": 1, "output_max_v": 3.5, "cutoff_frequency_hz": 2000}, 0.5, 0.2, -45),
        ("negative_bias", "Gain 2, -2 V bias, 1 kohm source and 500 Hz cutoff",
         {"input_min_v": 1, "input_max_v": 3, "output_min_v": 0, "output_max_v": 4,
          "source_impedance_ohm": 1000, "cutoff_frequency_hz": 500}, 2, 0.5, 30),
        ("unity_zero_bias", "Unity gain, zero bias, 2.2 kohm source and +90 degree phase",
         {"output_min_v": 0, "output_max_v": 5, "source_impedance_ohm": 2200,
          "cutoff_frequency_hz": 800}, 2.5, 0.2, 90),
        ("small_gain_phase_minus90", "Gain 0.001 with 1 V bias and -90 degree input phase",
         {"output_min_v": 1, "output_max_v": 1.005, "source_impedance_ohm": 47,
          "cutoff_frequency_hz": 3000}, 2.5, 0.3, -90),
    ]
    cases = []
    for case_id, description, overrides, dc, amplitude, phase in definitions:
        inputs = {**base, **overrides}
        fixture = {"schema_version": 1, "target_version": "14.3", "dc_voltage_v": dc,
                   "source_impedance_ohm": inputs["source_impedance_ohm"],
                   "included_load_ohm": inputs["load_impedance_ohm"],
                   "ac_amplitude_v": amplitude, "ac_phase_deg": phase}
        cases.append(SignalMultiboardRegressionCase(case_id, description, inputs, fixture))
    return tuple(cases)


def select_signal_multiboard_regression_cases(case_ids: Sequence[str] | None = None):
    cases = signal_multiboard_regression_cases()
    if case_ids is None:
        return cases
    if not case_ids or len(set(case_ids)) != len(case_ids):
        raise ValueError("case ids must be nonempty and unique")
    by_id = {case.case_id: case for case in cases}
    unknown = set(case_ids) - set(by_id)
    if unknown:
        raise ValueError(f"unknown signal regression cases: {sorted(unknown)}")
    return tuple(by_id[k] for k in case_ids)
