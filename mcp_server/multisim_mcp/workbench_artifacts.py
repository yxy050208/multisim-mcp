"""Bounded read-only evidence views for the local visual workbench."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .experiment_resources import register_experiment, summarize_experiment
from .eda_core import DesignPatch
from .project_inspection import inspect_project
from .workbench_optimization import summarize_optimization_entry
from .workspace_manifest import read_directory_manifest


WORKBENCH_ENTRY_DETAILS_SCHEMA_VERSION = 1
ENTRY_HANDLE_PREFIX = "entry-"
_ENTRY_HANDLE = re.compile(r"^entry-[0-9a-f]{24}$")
_MAX_MEDIA_BYTES = 8 * 1024 * 1024
_MEDIA = {
    "plot": ("plot.svg", "plot", "image/svg+xml"),
    "schematic": ("schematic.png", "schematic-image", "image/png"),
}
_DETAIL_KINDS = frozenset({
    "experiment",
    "optimization",
    "global-optimization",
    "patch-evaluation",
    "autonomous-correction",
})
_PATCH_DETAIL_KINDS = frozenset({"patch-evaluation", "autonomous-correction"})
_MAX_PATCH_JSON_BYTES = 512 * 1024
_MAX_PATCH_OPERATIONS = 64
_PATCH_EVALUATION_STATE_NAME = "evaluation.json"
_PATCH_EVALUATION_PATCH_NAME = "patch.json"
_PATCH_EVALUATION_INVERSE_NAME = "inverse-patch.json"
_CORRECTION_STATE_NAME = "autonomous-correction.json"
_CORRECTION_FINAL_PATCH_NAME = "final-candidate-patch.json"
_TRANSACTION_ID = re.compile(r"^patch-txn-[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PATCH_DOWNLOADS = {
    "candidate": ("patch.json", frozenset({"patch-evaluation", "autonomous-correction"})),
    "inverse": (_PATCH_EVALUATION_INVERSE_NAME, frozenset({"patch-evaluation"})),
}


def _patch_transaction_status(
    state: dict[str, Any],
    *,
    patch_present: bool,
    adoption_eligible: bool,
) -> dict[str, Any]:
    """Project transaction state without exposing paths or approval secrets.

    Read-only evaluation artifacts intentionally do not contain a transaction
    receipt.  The projection therefore distinguishes a candidate waiting for
    the explicit CLI approval workflow from a committed transaction only when
    a complete workflow summary (transaction ID, consumed approval, receipt
    digest, and output digest) is present in the state object.  Unknown fields
    are discarded so a future workflow cannot accidentally leak local paths
    or bearer material into the browser payload.
    """
    raw = state.get("transaction")
    transaction = raw if isinstance(raw, dict) else None
    transaction_id = (
        transaction.get("transaction_id")
        if transaction is not None
        and isinstance(transaction.get("transaction_id"), str)
        and _TRANSACTION_ID.fullmatch(transaction["transaction_id"])
        else None
    )
    receipt_sha256 = (
        transaction.get("receipt_sha256")
        if transaction is not None
        and isinstance(transaction.get("receipt_sha256"), str)
        and _SHA256.fullmatch(transaction["receipt_sha256"])
        else None
    )
    output_design_digest = (
        transaction.get("output_design_digest")
        if transaction is not None
        and isinstance(transaction.get("output_design_digest"), str)
        and _SHA256.fullmatch(transaction["output_design_digest"])
        else None
    )
    operation = (
        transaction.get("operation")
        if transaction is not None and transaction.get("operation") in {"apply", "revert"}
        else "apply"
    )
    recovery_required = (
        bool(
            transaction.get(
                "patch_journal_recovery_required",
                transaction.get("recovery_required", False),
            )
        )
        if transaction is not None
        else False
    )
    approval_consumed = (
        bool(transaction.get("approval_consumed", False))
        if transaction is not None
        else False
    )

    committed_transaction = (
        transaction_id is not None
        and approval_consumed
        and receipt_sha256 is not None
        and output_design_digest is not None
    )
    if committed_transaction:
        status = "committed"
        summary = "A committed transaction receipt is recorded in the workflow evidence"
        source = "workflow-summary"
    elif not patch_present:
        status = "not_applicable"
        summary = "No candidate patch is available for adoption"
        source = "evaluation-state"
    elif adoption_eligible:
        status = "approval_pending"
        summary = "Candidate is ready for explicit CLI approval; no transaction has been committed"
        source = "evaluation-state"
    else:
        status = "not_committed"
        summary = "Candidate is not eligible for adoption and no transaction was committed"
        source = "evaluation-state"

    return {
        "status": status,
        "summary": summary,
        "operation": operation,
        "transaction_id": transaction_id,
        "approval_consumed": approval_consumed,
        "recovery_required": recovery_required,
        "receipt_sha256": receipt_sha256,
        "output_design_digest": output_design_digest,
        "source": source,
        "read_only": True,
    }


def entry_handle(relative_path: str) -> str:
    """Return an opaque stable handle without exposing a filesystem path."""
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("entry relative path must be a non-empty string")
    normalized = "." if relative_path == "." else PurePosixPath(relative_path).as_posix()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"{ENTRY_HANDLE_PREFIX}{digest}"


def add_entry_handles(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Add API-only opaque handles to an inspection snapshot."""
    enriched = dict(snapshot)
    entries: list[dict[str, Any]] = []
    for raw in snapshot.get("entries", []):
        item = dict(raw)
        if item.get("integrity_status") != "invalid":
            item["entry_handle"] = entry_handle(item["path"])
            item["details_available"] = item.get("directory_kind") in _DETAIL_KINDS
            item["detail_kind"] = (
                "experiment"
                if item.get("directory_kind") == "experiment"
                else "optimization"
                if item.get("directory_kind") in {"optimization", "global-optimization"}
                else "patch-review"
                if item.get("directory_kind") in _PATCH_DETAIL_KINDS
                else None
            )
        else:
            item["entry_handle"] = None
            item["details_available"] = False
            item["detail_kind"] = None
        entries.append(item)
    enriched["entries"] = entries
    root_entry = enriched.get("root_manifest")
    if isinstance(root_entry, dict):
        enriched["root_manifest"] = next(
            (item for item in entries if item.get("path") == "."),
            dict(root_entry),
        )
    return enriched


def _resolve_entry(
    project_root: str | Path,
    handle: str,
    *,
    allowed_kinds: frozenset[str],
    verify: bool,
    max_entries: int,
    max_depth: int,
) -> tuple[Path, dict[str, Any]]:
    if not isinstance(handle, str) or not _ENTRY_HANDLE.fullmatch(handle):
        raise KeyError("unknown workbench entry handle")
    snapshot = inspect_project(
        project_root,
        verify=verify,
        max_entries=max_entries,
        max_depth=max_depth,
    )
    entry = next(
        (
            item
            for item in snapshot["entries"]
            if item.get("integrity_status") != "invalid"
            and entry_handle(item["path"]) == handle
        ),
        None,
    )
    if entry is None:
        raise KeyError("unknown workbench entry handle")
    if entry.get("directory_kind") not in allowed_kinds:
        raise ValueError("workbench details are not available for this entry type")

    workspace = Path(snapshot["workspace_root"]).resolve()
    relative = entry["path"]
    directory = workspace
    if relative != ".":
        for part in PurePosixPath(relative).parts:
            directory = directory / part
            if directory.is_symlink():
                raise ValueError("workbench entry must not traverse symbolic links")
    directory = directory.resolve()
    try:
        directory.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("workbench entry escapes the project root") from exc
    if not directory.is_dir():
        raise FileNotFoundError("workbench entry directory is missing")
    read_directory_manifest(directory, verify=verify)
    return directory, entry


def _read_manifest_json(
    directory: Path,
    manifest: Any,
    relative_name: str,
    *,
    required: bool = False,
) -> dict[str, Any] | None:
    """Read one small JSON artifact already covered by a verified manifest."""
    artifact = next(
        (item for item in manifest.artifacts if item.path == relative_name),
        None,
    )
    if artifact is None:
        if required:
            raise FileNotFoundError(f"required patch artifact is missing: {relative_name}")
        return None
    if artifact.size > _MAX_PATCH_JSON_BYTES:
        raise ValueError(f"patch artifact exceeds the workbench size limit: {relative_name}")
    path = directory / relative_name
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"patch artifact is missing: {relative_name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeError as exc:
        raise ValueError(f"patch artifact must be UTF-8: {relative_name}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"patch artifact is not valid JSON: {relative_name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"patch artifact must contain one JSON object: {relative_name}")
    return value


def _patch_review_details(
    directory: Path,
    entry: dict[str, Any],
    manifest: Any,
    handle: str,
) -> dict[str, Any]:
    """Build a bounded, read-only review model for a persisted patch candidate."""
    kind = str(entry.get("directory_kind"))
    if kind == "patch-evaluation":
        state_name = _PATCH_EVALUATION_STATE_NAME
        patch_name = _PATCH_EVALUATION_PATCH_NAME
        inverse_name = _PATCH_EVALUATION_INVERSE_NAME
    else:
        state_name = _CORRECTION_STATE_NAME
        patch_name = _CORRECTION_FINAL_PATCH_NAME
        inverse_name = None
    state = _read_manifest_json(directory, manifest, state_name, required=True) or {}
    patch_payload = _read_manifest_json(directory, manifest, patch_name)
    patch = DesignPatch.from_dict(patch_payload) if patch_payload is not None else None
    operations: list[dict[str, Any]] = []
    if patch is not None:
        for operation in patch.operations[:_MAX_PATCH_OPERATIONS]:
            value = operation.to_dict()
            operations.append({
                "operation": value["operation"],
                "target": value["target"],
                "before": value["before"],
                "after": value["after"],
                "reason": value["reason"],
            })
    raw_comparison = state.get("comparison") if kind == "patch-evaluation" else None
    comparison = None
    if isinstance(raw_comparison, dict):
        baseline = raw_comparison.get("baseline")
        candidate = raw_comparison.get("candidate")
        comparison = {
            "status": raw_comparison.get("status"),
            "baseline_status": baseline.get("status") if isinstance(baseline, dict) else None,
            "candidate_status": candidate.get("status") if isinstance(candidate, dict) else None,
        }
    diagnosis_delta = state.get("diagnosis_delta") if isinstance(state.get("diagnosis_delta"), dict) else {}
    adoption_eligible = bool(state.get("adoption_eligible"))
    return {
        "schema_version": WORKBENCH_ENTRY_DETAILS_SCHEMA_VERSION,
        "success": True,
        "read_only": True,
        "detail_kind": "patch-review",
        "entry_handle": handle,
        "entry": {
            "entity_id": entry.get("entity_id"),
            "directory_kind": kind,
            "state": entry.get("state"),
            "revision": entry.get("revision"),
            "integrity_status": entry.get("integrity_status"),
        },
        "patch_review": {
            "workflow": kind,
            "status": state.get("status") or entry.get("state") or "unknown",
            "state": state.get("state") or entry.get("state") or "unknown",
            "patch_present": patch is not None,
            "patch_id": patch.patch_id if patch is not None else state.get("patch_id"),
            "design_id": patch.design_id if patch is not None else state.get("design_id"),
            "base_revision": patch.base_revision if patch is not None else state.get("source_revision"),
            "candidate_revision": state.get("candidate_revision") or state.get("final_revision"),
            "description": patch.description if patch is not None else "",
            "operation_count": len(patch.operations) if patch is not None else 0,
            "operations": operations,
            "inverse_patch_available": bool(inverse_name and _read_manifest_json(directory, manifest, inverse_name)),
            "adoption_eligible": adoption_eligible,
            "approval_required": bool(state.get("approval_required_before_apply", patch is not None)),
            "source_design_modified": bool(state.get("source_design_modified", False)),
            "candidate_persisted_as_source": bool(state.get("candidate_persisted_as_source", False)),
            "transaction": _patch_transaction_status(
                state,
                patch_present=patch is not None,
                adoption_eligible=adoption_eligible,
            ),
            "comparison": comparison if isinstance(comparison, dict) else None,
            "diagnosis_delta": {
                "before_status": diagnosis_delta.get("before_status"),
                "after_status": diagnosis_delta.get("after_status"),
                "resolved_finding_count": diagnosis_delta.get("resolved_finding_count", 0),
                "introduced_finding_count": diagnosis_delta.get("introduced_finding_count", 0),
                "severity_delta": diagnosis_delta.get("severity_delta", {}),
            },
            "preflight": [
                {"id": "manifest", "status": "pass", "summary": "Manifest and artifact hashes verified"},
                {"id": "candidate", "status": "pass" if bool(state.get("adoption_eligible")) else "required", "summary": "Candidate evidence is eligible for separate review" if bool(state.get("adoption_eligible")) else "Candidate is not eligible for adoption"},
                {"id": "source-boundary", "status": "pass" if not state.get("source_design_modified") else "blocked", "summary": "Source design was not modified by the evaluation" if not state.get("source_design_modified") else "Source design modification was reported"},
                {"id": "approval", "status": "required", "summary": "Explicit CLI approval is required before any apply operation"},
            ],
            "approval": {
                "status": "not_issued",
                "issuance": "cli-only",
                "execution_enabled": False,
                "token_exposed": False,
            },
            "limitations": [
                "This Workbench view is evidence-only and never applies a patch.",
                "Approval tokens are not issued to or stored in the browser.",
                "Apply and revert remain explicit CLI/MCP operations pending a future guarded endpoint.",
            ],
        },
    }


def load_entry_details(
    project_root: str | Path,
    handle: str,
    *,
    verify: bool = True,
    max_entries: int = 256,
    max_depth: int = 5,
) -> dict[str, Any]:
    """Load a compact report, measurement, verification, and media index."""
    directory, entry = _resolve_entry(
        project_root,
        handle,
        allowed_kinds=_DETAIL_KINDS,
        verify=verify,
        max_entries=max_entries,
        max_depth=max_depth,
    )
    manifest = read_directory_manifest(directory, verify=verify)
    if entry.get("directory_kind") in _PATCH_DETAIL_KINDS:
        return _patch_review_details(directory, entry, manifest, handle)
    if entry.get("directory_kind") in {"optimization", "global-optimization"}:
        return {
            "schema_version": WORKBENCH_ENTRY_DETAILS_SCHEMA_VERSION,
            "success": True,
            "read_only": True,
            "detail_kind": "optimization",
            "entry_handle": handle,
            "entry": {
                "entity_id": entry.get("entity_id"),
                "directory_kind": entry.get("directory_kind"),
                "state": entry.get("state"),
                "revision": entry.get("revision"),
                "integrity_status": entry.get("integrity_status"),
            },
            "optimization": summarize_optimization_entry(directory, manifest),
        }
    index = register_experiment(str(directory))
    summary = summarize_experiment(index["experiment_id"])
    paths = {item.path for item in manifest.artifacts}
    media = {
        name: {
            "available": filename in paths,
            "url": f"/api/entries/{handle}/media/{name}" if filename in paths else None,
            "mime_type": mime_type,
        }
        for name, (filename, _role, mime_type) in _MEDIA.items()
    }
    return {
        "schema_version": WORKBENCH_ENTRY_DETAILS_SCHEMA_VERSION,
        "success": True,
        "read_only": True,
        "detail_kind": "experiment",
        "entry_handle": handle,
        "entry": {
            "entity_id": entry.get("entity_id"),
            "directory_kind": entry.get("directory_kind"),
            "state": entry.get("state"),
            "revision": entry.get("revision"),
            "integrity_status": entry.get("integrity_status"),
            "approval_provenance": entry.get("approval_provenance"),
            "approval_provenance_status": entry.get(
                "approval_provenance_status", "absent"
            ),
        },
        "experiment": summary,
        "media": media,
    }


def read_entry_media(
    project_root: str | Path,
    handle: str,
    media_name: str,
    *,
    verify: bool = True,
    max_entries: int = 256,
    max_depth: int = 5,
) -> tuple[bytes, str, str]:
    """Read one fixed media artifact referenced by a verified manifest."""
    definition = _MEDIA.get(media_name)
    if definition is None:
        raise KeyError("unknown workbench media name")
    filename, required_role, mime_type = definition
    directory, _entry = _resolve_entry(
        project_root,
        handle,
        allowed_kinds=frozenset({"experiment"}),
        verify=verify,
        max_entries=max_entries,
        max_depth=max_depth,
    )
    manifest = read_directory_manifest(directory, verify=verify)
    artifact = next((item for item in manifest.artifacts if item.path == filename), None)
    if artifact is None or artifact.role != required_role:
        raise FileNotFoundError("requested media is not present in the experiment manifest")
    if artifact.size > _MAX_MEDIA_BYTES:
        raise ValueError("requested media exceeds the workbench size limit")
    path = directory / filename
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("requested media file is missing")
    content = path.read_bytes()
    if len(content) != artifact.size:
        raise ValueError("requested media changed after manifest verification")
    if hashlib.sha256(content).hexdigest() != artifact.sha256:
        raise ValueError("requested media changed after manifest verification")
    return content, mime_type, artifact.sha256


def read_entry_patch_artifact(
    project_root: str | Path,
    handle: str,
    artifact_name: str,
    *,
    verify: bool = True,
    max_entries: int = 256,
    max_depth: int = 5,
) -> tuple[bytes, str, str, str]:
    """Read one fixed candidate/inverse patch for manual CLI handoff."""
    definition = _PATCH_DOWNLOADS.get(artifact_name)
    if definition is None:
        raise KeyError("unknown workbench patch artifact name")
    relative_name, allowed_kinds = definition
    directory, _entry = _resolve_entry(
        project_root,
        handle,
        allowed_kinds=allowed_kinds,
        verify=verify,
        max_entries=max_entries,
        max_depth=max_depth,
    )
    manifest = read_directory_manifest(directory, verify=verify)
    artifact = next((item for item in manifest.artifacts if item.path == relative_name), None)
    if artifact is None:
        # Autonomous correction uses a different fixed candidate filename.
        if artifact_name == "candidate":
            relative_name = _CORRECTION_FINAL_PATCH_NAME
            artifact = next((item for item in manifest.artifacts if item.path == relative_name), None)
    if artifact is None or artifact.role not in {"design-patch", "inverse-patch", "final-candidate-patch"}:
        raise FileNotFoundError("requested patch artifact is not present in the verified manifest")
    if artifact.size > _MAX_PATCH_JSON_BYTES:
        raise ValueError("requested patch artifact exceeds the workbench size limit")
    path = directory / relative_name
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("requested patch artifact is missing")
    content = path.read_bytes()
    if len(content) != artifact.size or hashlib.sha256(content).hexdigest() != artifact.sha256:
        raise ValueError("requested patch artifact changed after manifest verification")
    return content, "application/json", artifact.sha256, Path(relative_name).name


__all__ = [
    "ENTRY_HANDLE_PREFIX",
    "WORKBENCH_ENTRY_DETAILS_SCHEMA_VERSION",
    "add_entry_handles",
    "entry_handle",
    "load_entry_details",
    "read_entry_media",
    "read_entry_patch_artifact",
]
