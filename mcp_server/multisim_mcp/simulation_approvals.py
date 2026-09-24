"""Human approval gate for one bounded simulation plan.

The simulation-plan artifact binds an approved executable-netlist preview to a
validated ``ExperimentSpec``.  It is still a read-only transition record: it
does not create a schematic, write files, or start a simulator.  A later
verified-experiment call must present the same three artifacts again.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Final

from .design_verification import validate_experiment_spec
from .executable_approvals import validate_executable_netlist_approval
from .executable_netlists import _canonicalize_for_digest
from .safety import validate_analysis_commands


SIMULATION_APPROVAL_SCHEMA_VERSION: Final = 1
SIMULATION_APPROVAL_GENERATOR_VERSION: Final = "0.1.0"
EXPERIMENT_PROVENANCE_SCHEMA_VERSION: Final = 1
EXPERIMENT_PROVENANCE_KIND: Final = "multisim-mcp-approved-simulation-provenance"
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
    if not isinstance(value, Mapping):
        raise ValueError("approval input must be an object")
    return {
        str(key): item
        for key, item in value.items()
        if key not in {"service", "success", "read_only", "preview_only", "approval_only"}
    }


def _validated_inputs(
    executable_netlist: Mapping[str, Any],
    netlist_approval: Mapping[str, Any],
    experiment_spec: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    preview = _core(executable_netlist)
    approved_netlist = validate_executable_netlist_approval(
        preview, netlist_approval
    )
    for field in ("compiled_id", "compiled_digest", "design_id", "design_digest", "spice_sha256"):
        value = preview.get(field)
        if field == "design_id":
            # The compiled preview stores the design identity inside CircuitDesign.
            value = preview.get("circuit_design", {}).get("design_id")
        elif field == "design_digest":
            design_data = preview.get("circuit_design")
            value = _digest(design_data) if isinstance(design_data, Mapping) else None
        if field.endswith("_digest") or field == "spice_sha256":
            if not isinstance(value, str) or _HEX_DIGEST.fullmatch(value) is None:
                raise ValueError(f"executable_netlist.{field} is invalid")
        elif not isinstance(value, str) or _ID.fullmatch(value) is None:
            raise ValueError(f"executable_netlist.{field} is invalid")

    normalized = validate_experiment_spec(dict(experiment_spec))
    if normalized["netlist"] != preview.get("spice_netlist"):
        raise ValueError(
            "experiment_spec.netlist does not match the approved executable preview"
        )
    normalized["commands"] = "\n".join(
        validate_analysis_commands(normalized["commands"])
    )
    return preview, approved_netlist, normalized


def _review_payload(
    approval: Mapping[str, Any],
    approved_netlist: Mapping[str, Any],
    experiment_spec: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(approval, Mapping):
        raise ValueError("approval must be an object")
    allowed = {
        "approved",
        "netlist_approval_id",
        "netlist_approval_digest",
        "spec_digest",
        "confirm_netlist",
        "confirm_commands",
        "confirm_measurements",
        "confirm_limits",
        "review_note",
    }
    unknown = set(approval) - allowed
    if unknown:
        raise ValueError(f"approval contains unknown fields: {sorted(unknown)}")
    for field in (
        "approved",
        "confirm_netlist",
        "confirm_commands",
        "confirm_measurements",
        "confirm_limits",
    ):
        if approval.get(field) is not True:
            raise ValueError(f"approval.{field} must be true after explicit human review")
    if approval.get("netlist_approval_id") != approved_netlist["approval_id"]:
        raise ValueError("approval.netlist_approval_id does not match the netlist approval")
    if approval.get("netlist_approval_digest") != approved_netlist["approval_digest"]:
        raise ValueError(
            "approval.netlist_approval_digest does not match the netlist approval"
        )
    expected_spec_digest = _digest(experiment_spec)
    supplied_spec_digest = approval.get("spec_digest")
    if supplied_spec_digest is not None and supplied_spec_digest != expected_spec_digest:
        raise ValueError("approval.spec_digest does not match the experiment specification")
    note = approval.get("review_note", "")
    if not isinstance(note, str) or "\x00" in note or len(note) > 2048:
        raise ValueError("approval.review_note is invalid")
    return {
        "approved": True,
        "netlist_approval_id": approved_netlist["approval_id"],
        "netlist_approval_digest": approved_netlist["approval_digest"],
        "spec_digest": expected_spec_digest,
        "confirm_netlist": True,
        "confirm_commands": True,
        "confirm_measurements": True,
        "confirm_limits": True,
        "review_note": note.strip(),
    }


def _artifact(
    preview: Mapping[str, Any],
    approved_netlist: Mapping[str, Any],
    experiment_spec: Mapping[str, Any],
    reviewed: Mapping[str, Any],
) -> dict[str, Any]:
    review_digest = _digest(reviewed)
    spec_digest = _digest(experiment_spec)
    design = preview["circuit_design"]
    design_digest = _digest(design)
    payload = {
        "generator_version": SIMULATION_APPROVAL_GENERATOR_VERSION,
        "netlist_approval_digest": approved_netlist["approval_digest"],
        "compiled_digest": preview["compiled_digest"],
        "spec_digest": spec_digest,
        "review_digest": review_digest,
        "spice_sha256": preview["spice_sha256"],
        "design_digest": design_digest,
    }
    artifact_digest = _digest(payload)
    return {
        "schema_version": SIMULATION_APPROVAL_SCHEMA_VERSION,
        "generator_version": SIMULATION_APPROVAL_GENERATOR_VERSION,
        "kind": "multisim-mcp-simulation-plan-approval",
        "approval_id": f"simulation-approval-{artifact_digest[:32]}",
        "approval_digest": artifact_digest,
        "netlist_approval_id": approved_netlist["approval_id"],
        "netlist_approval_digest": approved_netlist["approval_digest"],
        "compiled_id": preview["compiled_id"],
        "compiled_digest": preview["compiled_digest"],
        "design_id": design["design_id"],
        "design_digest": design_digest,
        "spice_sha256": preview["spice_sha256"],
        "spec_digest": spec_digest,
        "experiment_spec": dict(experiment_spec),
        "state": "approved-for-simulation",
        "approval": {**reviewed, "review_digest": review_digest},
        "review_gates": [
            {"gate_id": "executable-netlist-approval", "status": "passed"},
            {"gate_id": "simulation-netlist-binding", "status": "passed"},
            {"gate_id": "analysis-command-safety", "status": "passed"},
            {"gate_id": "measurement-contract", "status": "human-reviewed"},
            {"gate_id": "simulation-plan-approval", "status": "passed"},
            {"gate_id": "schematic-generation", "status": "pending"},
            {"gate_id": "simulation-execution", "status": "pending"},
        ],
        "ready_for_schematic": True,
        "ready_for_simulation": True,
        "next_step": "run_verified_circuit_experiment_after_simulation_plan_approval",
        "execution_boundary": {
            "circuit_design_created": True,
            "spice_netlist_generated": True,
            "schematic_generated": False,
            "simulation_started": False,
            "files_written": False,
        },
        "artifacts_generated": [],
        "provenance_note": (
            "This approval binds the exact executable-netlist approval, safe analysis commands, "
            "measurement requirements, and SPICE digest; it does not approve a result or make "
            "the experiment production evidence until the later run succeeds."
        ),
    }


def approve_simulation_plan(
    executable_netlist: Mapping[str, Any],
    netlist_approval: Mapping[str, Any],
    experiment_spec: Mapping[str, Any],
    approval: Mapping[str, Any],
) -> dict[str, Any]:
    """Record explicit human approval for one immutable simulation plan."""
    preview, approved_netlist, normalized_spec = _validated_inputs(
        executable_netlist, netlist_approval, experiment_spec
    )
    reviewed = _review_payload(approval, approved_netlist, normalized_spec)
    return _artifact(preview, approved_netlist, normalized_spec, reviewed)


def validate_simulation_plan_approval(
    executable_netlist: Mapping[str, Any],
    netlist_approval: Mapping[str, Any],
    experiment_spec: Mapping[str, Any],
    approval_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild and verify a simulation-plan approval before execution."""
    preview, approved_netlist, normalized_spec = _validated_inputs(
        executable_netlist, netlist_approval, experiment_spec
    )
    if not isinstance(approval_artifact, Mapping):
        raise ValueError("simulation_plan_approval must be an object")
    core = _core(approval_artifact)
    if core.get("kind") != "multisim-mcp-simulation-plan-approval":
        raise ValueError("simulation_plan_approval.kind is invalid")
    raw_review = core.get("approval")
    if not isinstance(raw_review, Mapping):
        raise ValueError("simulation_plan_approval.approval is required")
    reviewed = _review_payload(
        {key: value for key, value in raw_review.items() if key != "review_digest"},
        approved_netlist,
        normalized_spec,
    )
    if raw_review.get("review_digest") != _digest(reviewed):
        raise ValueError("simulation_plan_approval review digest is invalid")
    expected = _artifact(preview, approved_netlist, normalized_spec, reviewed)
    if core != expected:
        raise ValueError("simulation_plan_approval does not match its bound plan")
    return expected


def build_experiment_approval_provenance(
    approval_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a validated approval into a small, safe manifest provenance record.

    The complete approval contains the experiment specification and review note;
    neither belongs in a directory manifest or a browser payload.  This record
    keeps only stable IDs and digests needed to prove which approved plan
    produced an experiment.
    """
    if not isinstance(approval_artifact, Mapping):
        raise ValueError("simulation_plan_approval must be an object")
    provenance = {
        "schema_version": EXPERIMENT_PROVENANCE_SCHEMA_VERSION,
        "kind": EXPERIMENT_PROVENANCE_KIND,
        "simulation_plan_approval_id": approval_artifact.get("approval_id"),
        "simulation_plan_approval_digest": approval_artifact.get("approval_digest"),
        "netlist_approval_id": approval_artifact.get("netlist_approval_id"),
        "netlist_approval_digest": approval_artifact.get("netlist_approval_digest"),
        "compiled_id": approval_artifact.get("compiled_id"),
        "compiled_digest": approval_artifact.get("compiled_digest"),
        "design_id": approval_artifact.get("design_id"),
        "design_digest": approval_artifact.get("design_digest"),
        "spice_sha256": approval_artifact.get("spice_sha256"),
        "spec_digest": approval_artifact.get("spec_digest"),
    }
    return validate_experiment_approval_provenance(provenance)


def validate_experiment_approval_provenance(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and normalize the manifest-safe approval provenance shape."""
    if not isinstance(value, Mapping):
        raise ValueError("approval_provenance must be an object")
    expected_keys = {
        "schema_version",
        "kind",
        "simulation_plan_approval_id",
        "simulation_plan_approval_digest",
        "netlist_approval_id",
        "netlist_approval_digest",
        "compiled_id",
        "compiled_digest",
        "design_id",
        "design_digest",
        "spice_sha256",
        "spec_digest",
    }
    if set(value) != expected_keys:
        raise ValueError("approval_provenance contains unknown or missing fields")
    if value.get("schema_version") != EXPERIMENT_PROVENANCE_SCHEMA_VERSION:
        raise ValueError("approval_provenance.schema_version is invalid")
    if value.get("kind") != EXPERIMENT_PROVENANCE_KIND:
        raise ValueError("approval_provenance.kind is invalid")
    id_fields = (
        "simulation_plan_approval_id",
        "netlist_approval_id",
        "compiled_id",
        "design_id",
    )
    digest_fields = (
        "simulation_plan_approval_digest",
        "netlist_approval_digest",
        "compiled_digest",
        "design_digest",
        "spice_sha256",
        "spec_digest",
    )
    for field in id_fields:
        candidate = value.get(field)
        if not isinstance(candidate, str) or _ID.fullmatch(candidate) is None:
            raise ValueError(f"approval_provenance.{field} is invalid")
    for field in digest_fields:
        candidate = value.get(field)
        if not isinstance(candidate, str) or _HEX_DIGEST.fullmatch(candidate) is None:
            raise ValueError(f"approval_provenance.{field} is invalid")
    return {key: value[key] for key in expected_keys}


__all__ = [
    "EXPERIMENT_PROVENANCE_KIND",
    "EXPERIMENT_PROVENANCE_SCHEMA_VERSION",
    "SIMULATION_APPROVAL_GENERATOR_VERSION",
    "SIMULATION_APPROVAL_SCHEMA_VERSION",
    "approve_simulation_plan",
    "build_experiment_approval_provenance",
    "validate_experiment_approval_provenance",
    "validate_simulation_plan_approval",
]
