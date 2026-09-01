"""Human approval gate for an in-memory executable-netlist preview."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Final

from .eda_core import CircuitDesign
from .executable_netlists import (
    COMPILER_SUPPORT_MATRIX,
    EXECUTABLE_NETLIST_GENERATOR_VERSION,
    _canonicalize_for_digest,
)
from .spice_adapter import circuit_design_from_spice, circuit_design_to_spice
from .safety import validate_spice_netlist


EXECUTABLE_APPROVAL_SCHEMA_VERSION: Final = 1
EXECUTABLE_APPROVAL_GENERATOR_VERSION: Final = "0.1.0"
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _digest(value: object) -> str:
    payload = json.dumps(
        _canonicalize_for_digest(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _core(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): item
        for key, item in value.items()
        if key not in {"service", "success", "read_only", "preview_only", "approval_only"}
    }


def _preview_payload(preview: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(preview, Mapping):
        raise ValueError("executable_netlist must be an object")
    core = _core(preview)
    if core.get("kind") != "multisim-mcp-executable-netlist-preview":
        raise ValueError("executable_netlist.kind is invalid")
    if core.get("schema_version") != 1:
        raise ValueError("executable_netlist.schema_version must be 1")
    if core.get("generator_version") != EXECUTABLE_NETLIST_GENERATOR_VERSION:
        raise ValueError("executable_netlist.generator_version is unsupported")
    for field in ("compiled_id", "draft_id", "component_approval_id", "selected_option_id", "state"):
        if not isinstance(core.get(field), str) or not core[field]:
            raise ValueError(f"executable_netlist.{field} is invalid")
    for field in (
        "compiled_digest",
        "draft_digest",
        "component_approval_digest",
        "spice_sha256",
    ):
        if not isinstance(core.get(field), str) or not _HEX_DIGEST.fullmatch(core[field]):
            raise ValueError(f"executable_netlist.{field} is invalid")
    if not _ID.fullmatch(core["compiled_id"]):
        raise ValueError("executable_netlist.compiled_id is invalid")
    if core.get("state") != "compiled-awaiting-human-netlist-approval":
        raise ValueError("executable_netlist is not awaiting human approval")
    if core.get("ready_for_netlist_approval") is not True:
        raise ValueError("executable_netlist is not ready for approval")
    if core.get("ready_for_schematic") is not False or core.get("ready_for_simulation") is not False:
        raise ValueError("executable_netlist readiness boundary is invalid")
    boundary = core.get("execution_boundary")
    if not isinstance(boundary, Mapping) or boundary != {
        "circuit_design_created": True,
        "spice_netlist_generated": True,
        "schematic_generated": False,
        "simulation_started": False,
        "files_written": False,
    }:
        raise ValueError("executable_netlist execution boundary is invalid")
    option_id = core.get("selected_option_id")
    if option_id not in COMPILER_SUPPORT_MATRIX:
        raise ValueError("executable_netlist option is not in the compiler support matrix")
    support = core.get("support")
    if support != COMPILER_SUPPORT_MATRIX[option_id]:
        raise ValueError("executable_netlist support metadata does not match the compiler")
    design_data = core.get("circuit_design")
    if not isinstance(design_data, Mapping):
        raise ValueError("executable_netlist.circuit_design is required")
    design = CircuitDesign.from_dict(design_data)
    if design.to_dict() != dict(design_data):
        raise ValueError("executable_netlist.circuit_design is not canonical")
    spice = core.get("spice_netlist")
    if not isinstance(spice, str) or not spice.strip() or len(spice) > 4_000_000:
        raise ValueError("executable_netlist.spice_netlist is invalid")
    validate_spice_netlist(spice)
    if hashlib.sha256(spice.encode("utf-8")).hexdigest() != core["spice_sha256"]:
        raise ValueError("executable_netlist.spice_sha256 does not match the netlist")
    if circuit_design_to_spice(design, prefer_source=False) != spice:
        raise ValueError("executable_netlist SPICE does not match CircuitDesign")
    parsed = circuit_design_from_spice(spice, design_id=design.design_id, title=design.title)
    if len(parsed.components) != len(design.components):
        raise ValueError("executable_netlist SPICE round-trip is incomplete")
    synthesis = core.get("synthesis")
    if not isinstance(synthesis, Mapping):
        raise ValueError("executable_netlist.synthesis is required")
    verified_models = core.get("verified_models")
    if not isinstance(verified_models, list):
        raise ValueError("executable_netlist.verified_models must be an array")
    payload = {
        "generator_version": EXECUTABLE_NETLIST_GENERATOR_VERSION,
        "draft_digest": core["draft_digest"],
        "component_approval_digest": core["component_approval_digest"],
        "design": design.to_dict(),
        "spice_sha256": core["spice_sha256"],
        "synthesis": synthesis,
        "verified_models": verified_models,
    }
    if core.get("compiled_digest") != _digest(payload):
        raise ValueError("executable_netlist.compiled_digest does not match its contents")
    return core


def _review_payload(
    approval: Mapping[str, Any], preview: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(approval, Mapping):
        raise ValueError("approval must be an object")
    allowed = {
        "approved",
        "compiled_id",
        "compiled_digest",
        "confirm_components",
        "confirm_topology",
        "confirm_calculated_values",
        "confirm_spice",
        "review_note",
    }
    unknown = set(approval) - allowed
    if unknown:
        raise ValueError(f"approval contains unknown fields: {sorted(unknown)}")
    for field in (
        "approved",
        "confirm_components",
        "confirm_topology",
        "confirm_calculated_values",
        "confirm_spice",
    ):
        if approval.get(field) is not True:
            raise ValueError(f"approval.{field} must be true after explicit human review")
    if approval.get("compiled_id") != preview["compiled_id"]:
        raise ValueError("approval.compiled_id does not match the preview")
    if approval.get("compiled_digest") != preview["compiled_digest"]:
        raise ValueError("approval.compiled_digest does not match the preview")
    note = approval.get("review_note", "")
    if not isinstance(note, str) or "\x00" in note or len(note) > 2048:
        raise ValueError("approval.review_note is invalid")
    return {
        "approved": True,
        "compiled_id": preview["compiled_id"],
        "compiled_digest": preview["compiled_digest"],
        "confirm_components": True,
        "confirm_topology": True,
        "confirm_calculated_values": True,
        "confirm_spice": True,
        "review_note": note.strip(),
    }


def _artifact(preview: Mapping[str, Any], reviewed: Mapping[str, Any]) -> dict[str, Any]:
    review_digest = _digest(reviewed)
    payload = {
        "generator_version": EXECUTABLE_APPROVAL_GENERATOR_VERSION,
        "compiled_digest": preview["compiled_digest"],
        "review_digest": review_digest,
        "spice_sha256": preview["spice_sha256"],
        "design_digest": _digest(preview["circuit_design"]),
    }
    artifact_digest = _digest(payload)
    return {
        "schema_version": EXECUTABLE_APPROVAL_SCHEMA_VERSION,
        "generator_version": EXECUTABLE_APPROVAL_GENERATOR_VERSION,
        "kind": "multisim-mcp-executable-netlist-approval",
        "approval_id": f"netlist-approval-{artifact_digest[:32]}",
        "approval_digest": artifact_digest,
        "compiled_id": preview["compiled_id"],
        "compiled_digest": preview["compiled_digest"],
        "draft_id": preview["draft_id"],
        "draft_digest": preview["draft_digest"],
        "component_approval_id": preview["component_approval_id"],
        "component_approval_digest": preview["component_approval_digest"],
        "selected_option_id": preview["selected_option_id"],
        "spice_sha256": preview["spice_sha256"],
        "design_id": preview["circuit_design"]["design_id"],
        "design_digest": _digest(preview["circuit_design"]),
        "state": "approved-for-schematic-and-simulation-planning",
        "approval": {**reviewed, "review_digest": review_digest},
        "review_gates": [
            {"gate_id": "compiled-preview-integrity", "status": "passed"},
            {"gate_id": "component-review", "status": "passed"},
            {"gate_id": "pin-level-topology", "status": "passed"},
            {"gate_id": "calculated-values", "status": "human-reviewed"},
            {"gate_id": "spice-preview", "status": "human-reviewed"},
            {"gate_id": "human-netlist-approval", "status": "passed"},
            {"gate_id": "schematic-generation", "status": "pending"},
            {"gate_id": "simulation-plan", "status": "pending"},
        ],
        "ready_for_schematic": True,
        "ready_for_simulation": False,
        "next_step": "create_schematic_after_netlist_approval",
        "execution_boundary": {
            "circuit_design_created": True,
            "spice_netlist_generated": True,
            "schematic_generated": False,
            "simulation_started": False,
            "files_written": False,
        },
        "artifacts_generated": [],
        "provenance_note": (
            "This approval binds the in-memory CircuitDesign and SPICE digest; it does not approve "
            "a file write, schematic export, stimulus, analysis command, or simulation result."
        ),
    }


def approve_executable_netlist(
    executable_netlist: Mapping[str, Any],
    approval: Mapping[str, Any],
) -> dict[str, Any]:
    """Record explicit human approval for one immutable compiled preview."""
    preview = _preview_payload(executable_netlist)
    reviewed = _review_payload(approval, preview)
    return _artifact(preview, reviewed)


def validate_executable_netlist_approval(
    executable_netlist: Mapping[str, Any],
    approval_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild and verify the approval before a later schematic/simulation stage."""
    preview = _preview_payload(executable_netlist)
    if not isinstance(approval_artifact, Mapping):
        raise ValueError("netlist_approval must be an object")
    core = _core(approval_artifact)
    if core.get("kind") != "multisim-mcp-executable-netlist-approval":
        raise ValueError("netlist_approval.kind is invalid")
    raw_review = core.get("approval")
    if not isinstance(raw_review, Mapping):
        raise ValueError("netlist_approval.approval is required")
    reviewed = _review_payload(
        {key: value for key, value in raw_review.items() if key != "review_digest"},
        preview,
    )
    if raw_review.get("review_digest") != _digest(reviewed):
        raise ValueError("netlist_approval review digest is invalid")
    expected = _artifact(preview, reviewed)
    if core != expected:
        raise ValueError("netlist_approval does not match the compiled preview")
    return expected


__all__ = [
    "EXECUTABLE_APPROVAL_GENERATOR_VERSION",
    "EXECUTABLE_APPROVAL_SCHEMA_VERSION",
    "approve_executable_netlist",
    "validate_executable_netlist_approval",
]
