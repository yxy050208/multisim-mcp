"""Explicit DC/AC fixture composition for the bounded ideal signal compiler.

No backend or file access. The base preview is retained so approval can rebuild
the derived circuit instead of trusting a hash of unverified fixture metadata.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any

from .eda_core import CircuitComponent, CircuitDesign
from .spice_adapter import circuit_design_to_spice


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"experiment_fixture.{name} must be a finite SI number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"experiment_fixture.{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"experiment_fixture.{name} must be finite")
    return number


def normalize_fixture(base: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    if base.get("selected_option_id") != "signal-active":
        raise ValueError("experiment_fixture currently requires signal-active")
    allowed = {"schema_version", "target_version", "dc_voltage_v", "source_impedance_ohm",
               "included_load_ohm", "ac_amplitude_v", "ac_phase_deg"}
    if not isinstance(raw, Mapping) or set(raw) - allowed:
        raise ValueError("experiment_fixture contains unknown fields")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ValueError("experiment_fixture.schema_version must be 1")
    if raw.get("target_version") != "14.3":
        raise ValueError("experiment_fixture target_version currently requires 14.3")
    interface = base["synthesis"]["interface_contract"]
    transfer = base["synthesis"]["target_transfer"]
    dc = _number(raw.get("dc_voltage_v"), "dc_voltage_v")
    if not transfer["input_min_v"] <= dc <= transfer["input_max_v"]:
        raise ValueError("experiment_fixture DC voltage is outside the reviewed input range")
    source = _number(raw.get("source_impedance_ohm"), "source_impedance_ohm")
    load = _number(raw.get("included_load_ohm"), "included_load_ohm")
    if source != interface["source_impedance_ohm"] or load != interface["included_load_ohm"]:
        raise ValueError("experiment_fixture source impedance or included load differs from the reviewed circuit")
    result = {"schema_version": 1, "target_version": "14.3", "dc_voltage_v": dc,
              "source_impedance_ohm": source, "included_load_ohm": load}
    if "ac_amplitude_v" in raw:
        amplitude = _number(raw["ac_amplitude_v"], "ac_amplitude_v")
        phase = _number(raw.get("ac_phase_deg", 0), "ac_phase_deg")
        if not 0 < amplitude <= 1000 or not -180 <= phase <= 180:
            raise ValueError("experiment_fixture AC amplitude (0..1000 V) or phase (-180..180 degrees) is invalid")
        result.update(ac_amplitude_v=amplitude, ac_phase_deg=phase)
    elif "ac_phase_deg" in raw:
        raise ValueError("experiment_fixture AC phase requires an AC amplitude")
    return result


def compose_experiment_fixture(base: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    """Compose a new review preview; the original approval never transfers."""
    from .executable_netlists import _digest, validate_compiled_round_trip
    from .spice_adapter import circuit_design_from_spice

    if "experiment_fixture" in base:
        raise ValueError("nested experiment fixtures are not supported")
    contract = normalize_fixture(base, raw)
    contract_sha = _digest(contract)
    design = CircuitDesign.from_dict(base["circuit_design"])
    aliases = {n: "0" if n == "gnd" else n.replace("-", "_") for n in design.nets}
    if len({n.casefold() for n in aliases.values()}) != len(aliases):
        raise ValueError("experiment_fixture native net aliases collide")
    reserved = {"VINPUT", "RSOURCE"}
    if reserved & {p.refdes.upper() for p in design.components} or "fixture_source" in aliases.values():
        raise ValueError("experiment_fixture reserved component or net name collides")
    interface = base["synthesis"]["interface_contract"]
    input_net = aliases[interface["input_net"]]
    source_net = "fixture_source" if contract["source_impedance_ohm"] else input_net
    expression = f"DC {contract['dc_voltage_v']:.12g}"
    if "ac_amplitude_v" in contract:
        expression += f" AC {contract['ac_amplitude_v']:.12g} {contract['ac_phase_deg']:.12g}"
    parts = [replace(p, nodes=tuple(aliases[n] for n in p.nodes)) for p in design.components]
    source_fields = {"model": expression} if "ac_amplitude_v" in contract else {"value": format(contract["dc_voltage_v"], ".12g")}
    parts.append(CircuitComponent("VINPUT", "V", (source_net, "0"), **source_fields,
                                  annotations={"role": "explicit reviewed experiment source", "fixture_sha256": contract_sha}))
    if contract["source_impedance_ohm"]:
        parts.append(CircuitComponent("RSOURCE", "R", (source_net, input_net),
                                      value=format(contract["source_impedance_ohm"], ".12g"),
                                      annotations={"role": "declared source impedance", "fixture_sha256": contract_sha}))
    # RLOAD is already in the compiled design; the fixture never adds a load.
    derived = replace(design, design_id=f"fixture:{_digest([base['compiled_digest'], contract])[:24]}",
                      components=tuple(parts), nets=tuple(aliases.values()) + ((source_net,) if source_net != input_net else ()),
                      annotations={**dict(design.annotations), "input_net": input_net,
                                   "output_net": aliases[interface["output_net"]], "fixture_sha256": contract_sha})
    spice = circuit_design_to_spice(derived, prefer_source=False)
    validate_compiled_round_trip(derived, circuit_design_from_spice(spice))
    # Rename only declared interface/port net values, preserving other metadata.
    def rename(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: rename(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rename(v) for v in value]
        return aliases.get(value, value) if isinstance(value, str) else value
    synthesis = rename(deepcopy(base["synthesis"]))
    synthesis["interface_contract"].update(input_source_included=True, source_net=source_net)
    synthesis["interface_conditions"] = [
        "VINPUT and RSOURCE implement the explicit reviewed fixture; no external source is required.",
        "RLOAD is the included load; do not add it again.",
        *synthesis["interface_conditions"][2:],
    ]
    fixture = {"contract": contract, "contract_sha256": contract_sha,
               "net_aliases": aliases, "base_preview": deepcopy(dict(base))}
    spice_sha = hashlib.sha256(spice.encode("utf-8")).hexdigest()
    payload = {"generator_version": base["generator_version"], "draft_digest": base["draft_digest"],
               "component_approval_digest": base["component_approval_digest"], "design": derived.to_dict(),
               "spice_sha256": spice_sha, "synthesis": synthesis, "verified_models": base["verified_models"],
               "experiment_fixture": fixture}
    digest = _digest(payload)
    result = deepcopy(dict(base))
    result.update(compiled_id=f"compiled-netlist-{digest[:32]}", compiled_digest=digest,
                  circuit_design=derived.to_dict(), spice_netlist=spice, spice_sha256=spice_sha,
                  synthesis=synthesis, experiment_fixture=fixture)
    result["review_gates"].insert(-2, {"gate_id": "experiment-fixture-review", "status": "pending"})
    result["artifacts_generated"] = [
        {"role": "circuit-design", "storage": "memory", "sha256": _digest(derived.to_dict())},
        {"role": "spice-netlist", "storage": "memory", "sha256": spice_sha},
        {"role": "experiment-fixture", "storage": "memory", "sha256": contract_sha},
    ]
    result["limitations"][1] = "Explicit DC/AC fixture is included; analysis commands require separate review."
    return result


def validate_fixture_preview(core: Mapping[str, Any]) -> dict[str, Any]:
    from .executable_approvals import _preview_payload
    fixture = core.get("experiment_fixture")
    if not isinstance(fixture, Mapping) or set(fixture) != {"contract", "contract_sha256", "net_aliases", "base_preview"}:
        raise ValueError("executable_netlist experiment_fixture is invalid")
    base = fixture["base_preview"]
    if not isinstance(base, Mapping) or "experiment_fixture" in base:
        raise ValueError("nested or invalid base fixture preview")
    trusted_base = _preview_payload(base)
    expected = compose_experiment_fixture(trusted_base, fixture["contract"])
    if dict(core) != expected:
        raise ValueError("executable_netlist fixture composition does not match its base and contract")
    return expected
