"""Explicit human approval gate for resolved component requirements.

The approval artifact binds one logical draft, one deterministic component
resolution, the reviewed ratings, and model-provenance declarations.  It is a
read-only transition record: it does not emit a SPICE netlist, create a
``CircuitDesign``, write files, render a schematic, or start a simulator.

External model content is not fetched by this module.  A supplied SHA-256 and
license are therefore recorded as human-reviewed provenance, not represented
as proof that model behavior is correct.  A later compiler must verify the
actual bytes again before including an external model.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Final

from .component_resolution import resolve_component_requirements


COMPONENT_APPROVAL_SCHEMA_VERSION: Final = 1
COMPONENT_APPROVAL_GENERATOR_VERSION: Final = "0.1.0"
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_UNREVIEWED_LICENSES = frozenset({"", "pending", "unknown", "unreviewed", "none"})


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _core_resolution(resolution: Mapping[str, Any]) -> dict[str, Any]:
    """Remove transport-only envelope fields from a workbench response."""
    return {
        str(key): value
        for key, value in resolution.items()
        if key not in {"service", "success", "read_only", "approval_only"}
    }


def _validated_resolution(
    draft: Mapping[str, Any],
    resolution: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(resolution, Mapping):
        raise ValueError("resolution must be an object")
    core = _core_resolution(resolution)
    if core.get("kind") != "multisim-mcp-component-resolution":
        raise ValueError("resolution.kind is invalid")
    if core.get("schema_version") != 1:
        raise ValueError("resolution.schema_version must be 1")
    for field in ("resolution_id", "resolution_digest", "draft_id", "draft_digest"):
        value = core.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"resolution.{field} is invalid")
    if not _HEX_DIGEST.fullmatch(core["resolution_digest"]):
        raise ValueError("resolution.resolution_digest is invalid")
    if core.get("draft_id") != draft.get("draft_id") or core.get("draft_digest") != draft.get("draft_digest"):
        raise ValueError("resolution does not belong to the supplied draft")
    snapshot = core.get("selection_snapshot")
    if not isinstance(snapshot, Mapping):
        raise ValueError("resolution.selection_snapshot is required")
    rebuilt = resolve_component_requirements(draft, snapshot)
    if rebuilt["resolution_id"] != core["resolution_id"] or rebuilt["resolution_digest"] != core["resolution_digest"]:
        raise ValueError("resolution digest does not match the selected components")
    return rebuilt


def _approval_payload(approval: Mapping[str, Any], resolution: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(approval, Mapping):
        raise ValueError("approval must be an object")
    allowed = {
        "approved",
        "resolution_id",
        "resolution_digest",
        "confirm_topology",
        "confirm_ratings",
        "confirm_model_provenance",
        "review_note",
    }
    unknown = set(approval) - allowed
    if unknown:
        raise ValueError(f"approval contains unknown fields: {sorted(unknown)}")
    required_true = (
        "approved",
        "confirm_topology",
        "confirm_ratings",
        "confirm_model_provenance",
    )
    for field in required_true:
        if approval.get(field) is not True:
            raise ValueError(f"approval.{field} must be true after explicit human review")
    for field in ("resolution_id", "resolution_digest"):
        if approval.get(field) != resolution[field]:
            raise ValueError(f"approval.{field} does not match resolution")
    review_note = approval.get("review_note", "")
    if not isinstance(review_note, str) or "\x00" in review_note or len(review_note) > 1024:
        raise ValueError("approval.review_note is invalid")
    return {
        "approved": True,
        "resolution_id": resolution["resolution_id"],
        "resolution_digest": resolution["resolution_digest"],
        "confirm_topology": True,
        "confirm_ratings": True,
        "confirm_model_provenance": True,
        "review_note": review_note.strip(),
    }


def _model_provenance_status(candidate: Mapping[str, Any]) -> str:
    status = candidate.get("model_status")
    if status in {"not-applicable", "portable-adapter-available"}:
        return "not-required" if status == "not-applicable" else "portable-adapter-reviewed"
    if status != "provided-not-verified":
        return "missing"
    provenance = candidate.get("provenance")
    if not isinstance(provenance, Mapping):
        return "missing"
    sha256 = provenance.get("model_sha256")
    if not isinstance(sha256, str) or not _HEX_DIGEST.fullmatch(sha256):
        return "invalid-digest"
    source = provenance.get("source")
    if not isinstance(source, str) or not source.strip():
        return "missing-source"
    license_status = str(provenance.get("license_status", "")).strip()
    if license_status.casefold() in _UNREVIEWED_LICENSES:
        return "unreviewed-license"
    return "human-reviewed-provenance"


def _approved_requirements(resolution: Mapping[str, Any]) -> list[dict[str, Any]]:
    requirements = resolution.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("resolution requirements are missing")

    approved: list[dict[str, Any]] = []
    for item in requirements:
        if not isinstance(item, Mapping):
            raise ValueError("resolution requirement is invalid")
        requirement_id = item.get("requirement_id")
        candidate = item.get("selected_candidate")
        if item.get("selection_status") != "selected-awaiting-verification" or not isinstance(candidate, Mapping):
            raise ValueError(f"requirement {requirement_id} has no explicit component selection")
        rating_status = item.get("rating_status")
        if rating_status not in {"passed", "not-required"}:
            raise ValueError(f"requirement {requirement_id} ratings are not approved: {rating_status}")
        provenance_status = _model_provenance_status(candidate)
        if provenance_status in {"missing", "invalid-digest", "missing-source", "unreviewed-license"}:
            raise ValueError(
                f"requirement {requirement_id} model provenance is not approvable: {provenance_status}"
            )
        approved.append(
            {
                "requirement_id": requirement_id,
                "module_id": item.get("module_id"),
                "candidate_id": candidate.get("candidate_id"),
                "family": candidate.get("family"),
                "part_number": candidate.get("part_number"),
                "rating_status": rating_status,
                "model_provenance_status": provenance_status,
                "model_sha256": (
                    candidate.get("provenance", {}).get("model_sha256")
                    if isinstance(candidate.get("provenance"), Mapping)
                    else None
                ),
            }
        )
    return approved


def _approval_artifact(
    resolution: Mapping[str, Any],
    reviewed: Mapping[str, Any],
) -> dict[str, Any]:
    approved_requirements = _approved_requirements(resolution)
    review_digest = _digest(reviewed)
    artifact_payload = {
        "generator_version": COMPONENT_APPROVAL_GENERATOR_VERSION,
        "draft_digest": resolution["draft_digest"],
        "resolution_digest": resolution["resolution_digest"],
        "approval_digest": review_digest,
        "approved_requirements": approved_requirements,
    }
    artifact_digest = _digest(artifact_payload)
    return {
        "schema_version": COMPONENT_APPROVAL_SCHEMA_VERSION,
        "generator_version": COMPONENT_APPROVAL_GENERATOR_VERSION,
        "kind": "multisim-mcp-component-resolution-approval",
        "approval_id": f"component-approval-{artifact_digest[:32]}",
        "approval_digest": artifact_digest,
        "draft_id": resolution["draft_id"],
        "draft_digest": resolution["draft_digest"],
        "resolution_id": resolution["resolution_id"],
        "resolution_digest": resolution["resolution_digest"],
        "state": "approved-for-executable-netlist-compilation",
        "approval": {**reviewed, "review_digest": review_digest},
        "approved_requirements": approved_requirements,
        "selection_snapshot": resolution["selection_snapshot"],
        "review_gates": [
            {"gate_id": "logical-draft-integrity", "status": "passed"},
            {"gate_id": "component-family-selection", "status": "passed"},
            {"gate_id": "component-ratings", "status": "passed"},
            {"gate_id": "model-provenance", "status": "human-reviewed"},
            {"gate_id": "human-component-approval", "status": "passed"},
            {"gate_id": "executable-netlist", "status": "pending"},
        ],
        "ready_for_executable_netlist": True,
        "ready_for_schematic": False,
        "ready_for_simulation": False,
        "next_step": "compile_executable_netlist",
        "execution_boundary": {
            "circuit_design_created": False,
            "spice_netlist_generated": False,
            "schematic_generated": False,
            "simulation_started": False,
            "files_written": False,
        },
        "artifacts_generated": [],
        "provenance_note": (
            "External model digests and licenses were human-reviewed; actual model bytes "
            "must be re-hashed by the compiler before inclusion."
        ),
    }


def approve_component_resolution(
    draft: Mapping[str, Any],
    resolution: Mapping[str, Any],
    approval: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind an explicit review to a complete component resolution."""
    trusted = _validated_resolution(draft, resolution)
    reviewed = _approval_payload(approval, trusted)
    return _approval_artifact(trusted, reviewed)


def validate_component_approval(
    draft: Mapping[str, Any],
    approval_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild and verify the complete component-approval artifact.

    Later compilation stages must call this function instead of trusting the
    artifact's boolean readiness flags or copied component summary.
    """
    if not isinstance(approval_artifact, Mapping):
        raise ValueError("component_approval must be an object")
    core = _core_resolution(approval_artifact)
    if core.get("kind") != "multisim-mcp-component-resolution-approval":
        raise ValueError("component_approval.kind is invalid")
    snapshot = core.get("selection_snapshot")
    if not isinstance(snapshot, Mapping):
        raise ValueError("component_approval.selection_snapshot is required")
    rebuilt_resolution = resolve_component_requirements(draft, snapshot)
    raw_review = core.get("approval")
    if not isinstance(raw_review, Mapping):
        raise ValueError("component_approval.approval is required")
    supplied_review_digest = raw_review.get("review_digest")
    reviewed = _approval_payload(
        {key: value for key, value in raw_review.items() if key != "review_digest"},
        rebuilt_resolution,
    )
    if supplied_review_digest != _digest(reviewed):
        raise ValueError("component_approval review digest is invalid")
    expected = _approval_artifact(rebuilt_resolution, reviewed)
    if core != expected:
        raise ValueError("component_approval does not match its bound draft and selections")
    return expected


__all__ = [
    "COMPONENT_APPROVAL_GENERATOR_VERSION",
    "COMPONENT_APPROVAL_SCHEMA_VERSION",
    "approve_component_resolution",
    "validate_component_approval",
]
