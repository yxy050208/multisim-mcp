"""Bounded compilation from an approved logical draft to a pin-level preview.

This module deliberately compiles only templates whose physical topology is
implemented and tested here.  It never treats a block diagram as a complete
circuit, never writes files, and never starts an EDA backend.  The returned
SPICE and :class:`CircuitDesign` objects remain in-memory review artifacts
until a separate human netlist approval is added.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from .component_approvals import validate_component_approval
from .eda_core import CircuitComponent, CircuitDesign
from .spice_adapter import circuit_design_from_spice, circuit_design_to_spice


EXECUTABLE_NETLIST_SCHEMA_VERSION: Final = 1
EXECUTABLE_NETLIST_GENERATOR_VERSION: Final = "0.1.0"
MAX_MODEL_FILE_BYTES: Final = 4 * 1024 * 1024
MODEL_ROOT_ENV: Final = "MULTISIM_MCP_MODEL_ROOT"

COMPILER_SUPPORT_MATRIX: Final[dict[str, dict[str, Any]]] = {
    "signal-passive": {
        "template": "passive-affine-low-pass-v1",
        "required_families": {
            "cr-01": "series-resistor",
            "cr-02": "capacitor",
            "cr-03": "resistor-divider",
        },
        "status": "pin-level-preview-supported",
        "simulation_status": "requires-input-source-and-analysis-approval",
    }
}


def _canonicalize_for_digest(value: object) -> object:
    """Normalize JSON numbers so Python/JavaScript transports hash alike."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("digest input must contain finite numbers")
        if value == 0 or value.is_integer():
            return int(value)
        return value
    if isinstance(value, Mapping):
        return {str(key): _canonicalize_for_digest(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonicalize_for_digest(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize_for_digest(item) for item in value]
    return value


def _digest(value: object) -> str:
    payload = json.dumps(
        _canonicalize_for_digest(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _finite_positive(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return number


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _spice_number(value: float) -> str:
    rendered = format(value, ".12g")
    if rendered == "-0":
        return "0"
    return rendered


def _model_relative_path(uri: object) -> Path:
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError("approved external model URI must be a relative file path")
    normalized = uri.strip().replace("\\", "/")
    if "://" in normalized or normalized.startswith(("/", "//")):
        raise ValueError("approved external model URI must stay inside the configured model root")
    candidate = Path(normalized)
    if candidate.is_absolute() or candidate.drive or any(part == ".." for part in candidate.parts):
        raise ValueError("approved external model URI must stay inside the configured model root")
    if any(part in {"", "."} for part in candidate.parts):
        raise ValueError("approved external model URI is invalid")
    return candidate


def verify_approved_model_files(
    selection_snapshot: Mapping[str, Any],
    model_root: str | Path | None,
) -> list[dict[str, Any]]:
    """Re-hash every approved external model beneath one restricted root."""
    if not isinstance(selection_snapshot, Mapping):
        raise ValueError("selection_snapshot must be an object")
    model_items: list[tuple[str, Mapping[str, Any]]] = []
    for requirement_id, selection in selection_snapshot.items():
        if not isinstance(selection, Mapping):
            raise ValueError(f"selection_snapshot.{requirement_id} must be an object")
        source = selection.get("model_source")
        if source is not None:
            if not isinstance(source, Mapping):
                raise ValueError(f"selection_snapshot.{requirement_id}.model_source must be an object")
            model_items.append((str(requirement_id), source))
    if not model_items:
        return []
    if model_root is None:
        raise ValueError("model_root is required to verify approved external model bytes")
    try:
        root = Path(model_root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError("model_root must be an existing readable directory") from exc
    if not root.is_dir():
        raise ValueError("model_root must be an existing directory")

    verified: list[dict[str, Any]] = []
    for requirement_id, source in model_items:
        relative = _model_relative_path(source.get("uri"))
        try:
            path = (root / relative).resolve(strict=True)
        except OSError as exc:
            raise ValueError(
                f"approved external model cannot be read: {relative.as_posix()}"
            ) from exc
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("approved external model resolves outside model_root") from exc
        if not path.is_file():
            raise ValueError(f"approved external model is not a file: {relative.as_posix()}")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ValueError(
                f"approved external model cannot be read: {relative.as_posix()}"
            ) from exc
        if size > MAX_MODEL_FILE_BYTES:
            raise ValueError(
                f"approved external model exceeds {MAX_MODEL_FILE_BYTES} bytes: {relative.as_posix()}"
            )
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ValueError(
                f"approved external model cannot be read: {relative.as_posix()}"
            ) from exc
        if len(content) > MAX_MODEL_FILE_BYTES:
            raise ValueError(
                f"approved external model exceeds {MAX_MODEL_FILE_BYTES} bytes: {relative.as_posix()}"
            )
        actual = hashlib.sha256(content).hexdigest()
        expected = source.get("sha256")
        if not isinstance(expected, str) or actual != expected.lower():
            raise ValueError(f"approved external model SHA-256 mismatch: {relative.as_posix()}")
        if b"\x00" in content:
            raise ValueError(f"approved external model contains NUL bytes: {relative.as_posix()}")
        verified.append(
            {
                "requirement_id": requirement_id,
                "name": str(source.get("name", "")),
                "relative_path": relative.as_posix(),
                "sha256": actual,
                "size_bytes": size,
                "license": str(source.get("license", "")),
                "status": "bytes-rehashed",
            }
        )
    return verified


def _required_inputs(draft: Mapping[str, Any]) -> dict[str, float]:
    raw = draft.get("design_inputs")
    if not isinstance(raw, Mapping):
        raise ValueError("draft.design_inputs must be an object")
    values = {
        "supply_voltage_v": _finite_positive(raw.get("supply_voltage_v"), "supply_voltage_v"),
        "input_min_v": _finite(raw.get("input_min_v"), "input_min_v"),
        "input_max_v": _finite(raw.get("input_max_v"), "input_max_v"),
        "output_min_v": _finite(raw.get("output_min_v"), "output_min_v"),
        "output_max_v": _finite(raw.get("output_max_v"), "output_max_v"),
        "source_impedance_ohm": _finite_positive(
            raw.get("source_impedance_ohm"), "source_impedance_ohm"
        ),
        "load_impedance_ohm": _finite_positive(
            raw.get("load_impedance_ohm"), "load_impedance_ohm"
        ),
        "cutoff_frequency_hz": _finite_positive(
            raw.get("cutoff_frequency_hz"), "cutoff_frequency_hz"
        ),
    }
    if values["input_max_v"] <= values["input_min_v"]:
        raise ValueError("input_max_v must be greater than input_min_v")
    if values["output_max_v"] <= values["output_min_v"]:
        raise ValueError("output_max_v must be greater than output_min_v")
    if values["input_min_v"] < 0 or values["output_min_v"] < 0:
        raise ValueError("passive-affine-low-pass-v1 requires non-negative input and output ranges")
    if values["output_max_v"] > values["supply_voltage_v"]:
        raise ValueError("requested output range exceeds the positive supply rail")
    return values


def _selected_families(approval: Mapping[str, Any]) -> dict[str, str]:
    items = approval.get("approved_requirements")
    if not isinstance(items, list):
        raise ValueError("component approval requirements are missing")
    result: dict[str, str] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("component approval requirement is invalid")
        requirement_id = item.get("requirement_id")
        family = item.get("family")
        if isinstance(requirement_id, str) and isinstance(family, str):
            result[requirement_id] = family
    return result


def _synthesize_passive_affine_low_pass(
    draft: Mapping[str, Any],
    approval: Mapping[str, Any],
) -> tuple[CircuitDesign, dict[str, Any]]:
    expected = COMPILER_SUPPORT_MATRIX["signal-passive"]["required_families"]
    selected = _selected_families(approval)
    mismatches = {
        requirement_id: {"required": family, "selected": selected.get(requirement_id)}
        for requirement_id, family in expected.items()
        if selected.get(requirement_id) != family
    }
    if mismatches:
        raise ValueError(
            "signal-passive compiler requires exact physical families: "
            + json.dumps(mismatches, ensure_ascii=False, sort_keys=True)
        )

    values = _required_inputs(draft)
    input_span = values["input_max_v"] - values["input_min_v"]
    output_span = values["output_max_v"] - values["output_min_v"]
    gain = output_span / input_span
    offset = values["output_min_v"] - gain * values["input_min_v"]
    supply = values["supply_voltage_v"]
    if not 0 < gain < 1:
        raise ValueError(
            "passive-affine-low-pass-v1 cannot provide voltage gain; choose an active signal option"
        )
    if not 0 < offset < supply:
        raise ValueError(
            "passive-affine-low-pass-v1 requires a positive supply-derived output offset"
        )
    residual_fraction = 1.0 - gain - offset / supply
    if residual_fraction <= 0:
        raise ValueError(
            "requested passive gain and offset cannot be realized below the positive rail"
        )

    source = values["source_impedance_ohm"]
    load = values["load_impedance_ohm"]
    load_conductance = 1.0 / load
    minimum_total_conductance = load_conductance / residual_fraction
    maximum_total_conductance = 1.0 / (gain * source)
    if minimum_total_conductance * 1.05 >= maximum_total_conductance:
        raise ValueError(
            "source and load impedances leave no passive resistor solution with positive values"
        )
    preferred_source_path = max(1_000.0, source * 10.0)
    preferred_total_conductance = 1.0 / (gain * preferred_source_path)
    total_conductance = min(
        max(preferred_total_conductance, minimum_total_conductance * 2.0),
        maximum_total_conductance * 0.8,
    )
    source_path_resistance = 1.0 / (gain * total_conductance)
    protection_resistance = source_path_resistance - source
    top_conductance = offset / supply * total_conductance
    bottom_conductance = residual_fraction * total_conductance - load_conductance
    if min(protection_resistance, top_conductance, bottom_conductance) <= 0:
        raise ValueError("calculated passive network contains a non-positive component value")
    top_resistance = 1.0 / top_conductance
    bottom_resistance = 1.0 / bottom_conductance
    thevenin_resistance = 1.0 / total_conductance
    capacitance = 1.0 / (
        2.0 * math.pi * thevenin_resistance * values["cutoff_frequency_hz"]
    )

    synthesis = {
        "template": "passive-affine-low-pass-v1",
        "calculation_status": "deterministic-first-order-not-simulated",
        "target_transfer": {
            "gain_v_per_v": gain,
            "offset_v": offset,
            "input_min_v": values["input_min_v"],
            "input_max_v": values["input_max_v"],
            "estimated_output_min_v": gain * values["input_min_v"] + offset,
            "estimated_output_max_v": gain * values["input_max_v"] + offset,
        },
        "calculated_values": {
            "RPROT_ohm": protection_resistance,
            "RTOP_ohm": top_resistance,
            "RBOT_ohm": bottom_resistance,
            "RLOAD_ohm": load,
            "CFLT_f": capacitance,
        },
        "estimated_cutoff_hz": 1.0 / (2.0 * math.pi * thevenin_resistance * capacitance),
        "interface_conditions": [
            f"External source impedance must be {source:g} ohm at net path-00.",
            "External stimulus and analysis commands are intentionally absent.",
            "Calculated values are exact numbers, not preferred-series or tolerance-selected parts.",
        ],
    }
    synthesis_digest = _digest(
        {
            "draft_digest": draft["draft_digest"],
            "approval_digest": approval["approval_digest"],
            "synthesis": synthesis,
        }
    )
    components = (
        CircuitComponent(
            refdes="VCC",
            kind="V",
            nodes=("rail-positive", "gnd"),
            value=_spice_number(supply),
            annotations={"role": "approved positive supply"},
        ),
        CircuitComponent(
            refdes="RPROT",
            kind="R",
            nodes=("path-00", "path-03"),
            value=_spice_number(protection_resistance),
            annotations={"requirement_id": "cr-01", "role": "input protection and gain setting"},
        ),
        CircuitComponent(
            refdes="RTOP",
            kind="R",
            nodes=("rail-positive", "path-03"),
            value=_spice_number(top_resistance),
            annotations={"requirement_id": "cr-03", "role": "positive-rail bias divider"},
        ),
        CircuitComponent(
            refdes="RBOT",
            kind="R",
            nodes=("path-03", "gnd"),
            value=_spice_number(bottom_resistance),
            annotations={"requirement_id": "cr-03", "role": "ground bias divider"},
        ),
        CircuitComponent(
            refdes="RLOAD",
            kind="R",
            nodes=("path-03", "gnd"),
            value=_spice_number(load),
            annotations={"role": "declared output load"},
        ),
        CircuitComponent(
            refdes="CFLT",
            kind="C",
            nodes=("path-03", "gnd"),
            value=_spice_number(capacitance),
            annotations={"requirement_id": "cr-02", "role": "first-order low-pass"},
        ),
    )
    design = CircuitDesign(
        design_id=f"compiled:{synthesis_digest[:24]}",
        title=f"{draft.get('title', 'Signal design')} · pin-level preview",
        components=components,
        nets=("gnd", "rail-positive", "path-00", "path-03"),
        annotations={
            "compiler_template": synthesis["template"],
            "draft_id": draft["draft_id"],
            "component_approval_id": approval["approval_id"],
            "input_net": "path-00",
            "output_net": "path-03",
            "review_status": "awaiting-human-netlist-approval",
        },
    )
    return design, synthesis


def compile_executable_netlist(
    draft: Mapping[str, Any],
    component_approval: Mapping[str, Any],
    *,
    model_root: str | Path | None = None,
) -> dict[str, Any]:
    """Compile one approved, supported topology into in-memory review artifacts."""
    trusted_approval = validate_component_approval(draft, component_approval)
    option_id = draft.get("selected_option_id")
    if option_id not in COMPILER_SUPPORT_MATRIX:
        supported = ", ".join(sorted(COMPILER_SUPPORT_MATRIX))
        raise ValueError(
            f"selected option {option_id!r} has no pin-level compiler; supported options: {supported}"
        )
    verified_models = verify_approved_model_files(
        trusted_approval["selection_snapshot"], model_root
    )
    if option_id == "signal-passive":
        design, synthesis = _synthesize_passive_affine_low_pass(draft, trusted_approval)
    else:  # pragma: no cover - support matrix and dispatch must change together
        raise ValueError(f"compiler dispatch is missing for {option_id}")

    spice = circuit_design_to_spice(design, prefer_source=False)
    parsed = circuit_design_from_spice(
        spice,
        design_id=design.design_id,
        title=design.title,
    )
    if len(parsed.components) != len(design.components):
        raise ValueError("compiled SPICE did not round-trip to the expected component count")
    spice_sha256 = hashlib.sha256(spice.encode("utf-8")).hexdigest()
    compiled_payload = {
        "generator_version": EXECUTABLE_NETLIST_GENERATOR_VERSION,
        "draft_digest": draft["draft_digest"],
        "component_approval_digest": trusted_approval["approval_digest"],
        "design": design.to_dict(),
        "spice_sha256": spice_sha256,
        "synthesis": synthesis,
        "verified_models": verified_models,
    }
    compiled_digest = _digest(compiled_payload)
    return {
        "schema_version": EXECUTABLE_NETLIST_SCHEMA_VERSION,
        "generator_version": EXECUTABLE_NETLIST_GENERATOR_VERSION,
        "kind": "multisim-mcp-executable-netlist-preview",
        "compiled_id": f"compiled-netlist-{compiled_digest[:32]}",
        "compiled_digest": compiled_digest,
        "draft_id": draft["draft_id"],
        "draft_digest": draft["draft_digest"],
        "component_approval_id": trusted_approval["approval_id"],
        "component_approval_digest": trusted_approval["approval_digest"],
        "selected_option_id": option_id,
        "state": "compiled-awaiting-human-netlist-approval",
        "support": dict(COMPILER_SUPPORT_MATRIX[option_id]),
        "circuit_design": design.to_dict(),
        "spice_netlist": spice,
        "spice_sha256": spice_sha256,
        "synthesis": synthesis,
        "verified_models": verified_models,
        "review_gates": [
            {"gate_id": "component-approval-integrity", "status": "passed"},
            {"gate_id": "pin-level-template", "status": "passed"},
            {"gate_id": "external-model-bytes", "status": "passed"},
            {"gate_id": "spice-safe-syntax", "status": "passed"},
            {"gate_id": "spice-structured-round-trip", "status": "passed"},
            {"gate_id": "human-netlist-approval", "status": "pending"},
            {"gate_id": "simulation-evidence", "status": "pending"},
        ],
        "ready_for_netlist_approval": True,
        "ready_for_schematic": False,
        "ready_for_simulation": False,
        "next_step": "approve_executable_netlist",
        "execution_boundary": {
            "circuit_design_created": True,
            "spice_netlist_generated": True,
            "schematic_generated": False,
            "simulation_started": False,
            "files_written": False,
        },
        "artifacts_generated": [
            {"role": "circuit-design", "storage": "memory", "sha256": _digest(design.to_dict())},
            {"role": "spice-netlist", "storage": "memory", "sha256": spice_sha256},
        ],
        "limitations": [
            "Only the listed compiler support matrix is implemented.",
            "No input source or analysis command has been added.",
            "Calculated values have not been rounded to purchasable tolerance series.",
            "No Multisim schematic or simulation evidence exists yet.",
        ],
    }


__all__ = [
    "COMPILER_SUPPORT_MATRIX",
    "EXECUTABLE_NETLIST_GENERATOR_VERSION",
    "EXECUTABLE_NETLIST_SCHEMA_VERSION",
    "MAX_MODEL_FILE_BYTES",
    "MODEL_ROOT_ENV",
    "compile_executable_netlist",
    "verify_approved_model_files",
]
