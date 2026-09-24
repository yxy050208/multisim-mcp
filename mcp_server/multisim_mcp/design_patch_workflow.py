"""Approval-bound DesignPatch verification and pre-commit persistence workflow."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Mapping

from .design_patch_service import PreparedDesignPatch, prepare_design_patch
from .design_patch_transactions import (
    DEFAULT_APPROVAL_TTL_SECONDS,
    apply_patch_transaction,
    approve_patch_apply,
    read_design_document,
    read_patch_document,
    read_transaction_receipt,
    validate_patch_apply_approval,
)
from .design_verification import validate_experiment_spec
from .experiment_service import ExperimentApplicationService, ExperimentRequest
from .eda_core import CircuitDesign
from .spice_adapter import circuit_design_to_spice
from .workspace_manifest import DIRECTORY_MANIFEST_NAME, read_directory_manifest


PATCH_WORKFLOW_SCHEMA_VERSION: Final = 1
MAX_VERIFICATION_PLAN_BYTES: Final = 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_APPROVAL_ID_RE = re.compile(r"^approval-[0-9a-f]{32}$")
_WORKFLOW_ID_RE = re.compile(r"^patch-workflow-[0-9a-f]{32}$")
_WORKFLOW_STATES = frozenset(
    {
        "running",
        "verification_passed",
        "rejected",
        "experiment_error",
        "commit_failed",
        "committed",
        "aborted",
    }
)
_PLAN_REQUIRED_FIELDS = {
    "schema_version",
    "title",
    "commands",
    "requirements",
}
_PLAN_FIELDS = _PLAN_REQUIRED_FIELDS | {"theoretical_values"}


def _workflow_crash_point(stage: str) -> None:
    """Test seam for hard-crash coverage; production is a no-op."""
    del stage


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field in JSON document: {key}")
        result[key] = value
    return result


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_fingerprint(path: Path) -> str:
    normalized = os.path.normcase(str(path.resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _fsync_directory(directory: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(directory, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)


def _read_json(path: Path, name: str, maximum: int) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"{name} must be an existing regular file")
    if path.stat().st_size > maximum:
        raise ValueError(f"{name} exceeds its size limit")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError(f"{name} must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain one JSON object")
    return value


def _new_file(path: str, name: str) -> Path:
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink() or unresolved.parent.is_symlink():
        raise ValueError(f"{name} must not be a symbolic link")
    resolved = unresolved.resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError(f"{name} must not be a filesystem root")
    if not resolved.parent.is_dir() or resolved.parent.is_symlink():
        raise FileNotFoundError(f"{name} parent must be an existing directory")
    if resolved.exists():
        raise FileExistsError(f"{name} already exists: {resolved}")
    return resolved


def _new_directory(path: str, name: str) -> Path:
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink() or unresolved.parent.is_symlink():
        raise ValueError(f"{name} must not be a symbolic link")
    resolved = unresolved.resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError(f"{name} must not be a filesystem root")
    if not resolved.parent.is_dir() or resolved.parent.is_symlink():
        raise FileNotFoundError(f"{name} parent must be an existing directory")
    if resolved.exists():
        raise FileExistsError(f"{name} already exists: {resolved}")
    return resolved


def _atomic_json(path: Path, value: Mapping[str, Any], *, create: bool) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    content = (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if create:
            os.link(temporary, path)
        else:
            if not path.is_file() or path.is_symlink():
                raise ValueError("workflow manifest was removed or replaced")
            os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def read_patch_verification_plan(
    path: str, candidate: CircuitDesign
) -> tuple[Path, dict[str, Any]]:
    """Read and normalize a netlist-free verification plan for one candidate."""
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise ValueError("verification plan must not be a symbolic link")
    source = unresolved.resolve()
    raw = _read_json(source, "verification plan", MAX_VERIFICATION_PLAN_BYTES)
    if not _PLAN_REQUIRED_FIELDS <= set(raw) <= _PLAN_FIELDS:
        raise ValueError("verification plan fields are invalid")
    if not isinstance(candidate, CircuitDesign):
        raise ValueError("candidate must be a CircuitDesign")
    normalized = validate_experiment_spec(
        {
            **raw,
            "theoretical_values": raw.get("theoretical_values", {}),
            "netlist": circuit_design_to_spice(candidate),
        }
    )
    if not normalized["requirements"]:
        raise ValueError("verification plan requires at least one requirement")
    normalized.pop("netlist")
    return source, normalized


def _validate_runtime_limits(timeout_seconds: float, max_points: int) -> None:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or not 0 < float(timeout_seconds) <= 3600
    ):
        raise ValueError("timeout_seconds must be between 0 and 3600")
    if (
        isinstance(max_points, bool)
        or not isinstance(max_points, int)
        or not 1 <= max_points <= 100_000
    ):
        raise ValueError("max_points must be between 1 and 100000")


@dataclass(frozen=True, slots=True)
class _WorkflowContract:
    design_source: Path
    patch_source: Path
    prepared: PreparedDesignPatch
    target: Path
    receipt: Path
    plan_source: Path
    plan: dict[str, Any]
    experiment_output: Path
    manifest: Path
    timeout_seconds: float
    max_points: int
    authorization_context_digest: str


def _workflow_contract(
    *,
    design_path: str,
    patch_path: str,
    output_path: str | None,
    in_place: bool,
    receipt_path: str,
    regenerate_source_netlist: bool,
    verification_plan_path: str,
    experiment_output: str,
    workflow_manifest: str,
    timeout_seconds: float,
    max_points: int,
) -> _WorkflowContract:
    _validate_runtime_limits(timeout_seconds, max_points)
    design_source, design = read_design_document(design_path)
    patch_source, patch = read_patch_document(patch_path)
    prepared = prepare_design_patch(
        design, patch, regenerate_source_netlist=regenerate_source_netlist
    )
    if prepared.source_netlist_update_required:
        raise ValueError(
            "patch would stale the authoritative source netlist; explicitly "
            "authorize source regeneration"
        )
    if not isinstance(in_place, bool) or in_place == (output_path is not None):
        raise ValueError("select exactly one of in_place or output_path")
    target = design_source if in_place else Path(str(output_path)).expanduser().resolve()
    receipt = Path(receipt_path).expanduser().resolve()
    plan_source, plan = read_patch_verification_plan(
        verification_plan_path, prepared.candidate
    )
    experiment_root = _new_directory(experiment_output, "experiment output")
    manifest = _new_file(workflow_manifest, "workflow manifest")
    compared_files = {
        design_source,
        patch_source,
        plan_source,
        receipt,
        manifest,
    }
    if len(compared_files) != 5:
        raise ValueError("workflow files must use distinct paths")
    if not in_place:
        if target in compared_files:
            raise ValueError("workflow files must use distinct paths")
        compared_files.add(target)
    if experiment_root in compared_files:
        raise ValueError("experiment output must be distinct from workflow files")
    contract_value = {
        "schema_version": PATCH_WORKFLOW_SCHEMA_VERSION,
        "kind": "multisim-mcp-verified-patch-authorization",
        "verification_plan_digest": _digest(plan),
        "verification_plan_path_sha256": _path_fingerprint(plan_source),
        "experiment_output_path_sha256": _path_fingerprint(experiment_root),
        "workflow_manifest_path_sha256": _path_fingerprint(manifest),
        "timeout_seconds": float(timeout_seconds),
        "max_points": max_points,
        "commit_policy": "all-requirements-pass",
        "failure_policy": "discard-uncommitted-candidate",
    }
    return _WorkflowContract(
        design_source=design_source,
        patch_source=patch_source,
        prepared=prepared,
        target=target,
        receipt=receipt,
        plan_source=plan_source,
        plan=plan,
        experiment_output=experiment_root,
        manifest=manifest,
        timeout_seconds=float(timeout_seconds),
        max_points=max_points,
        authorization_context_digest=_digest(contract_value),
    )


def approve_verified_patch_application(
    design_path: str,
    patch_path: str,
    *,
    output_path: str | None,
    in_place: bool,
    receipt_path: str,
    regenerate_source_netlist: bool,
    verification_plan_path: str,
    experiment_output: str,
    workflow_manifest: str,
    timeout_seconds: float = 120.0,
    max_points: int = 2000,
    approval_store: str | None = None,
    ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
) -> dict[str, Any]:
    """Approve one exact candidate, verification plan, and output contract."""
    _validate_runtime_limits(timeout_seconds, max_points)
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or ttl_seconds < math.ceil(float(timeout_seconds)) + 60
    ):
        raise ValueError(
            "ttl_seconds must cover timeout_seconds plus a 60-second commit margin"
        )
    contract = _workflow_contract(
        design_path=design_path,
        patch_path=patch_path,
        output_path=output_path,
        in_place=in_place,
        receipt_path=receipt_path,
        regenerate_source_netlist=regenerate_source_netlist,
        verification_plan_path=verification_plan_path,
        experiment_output=experiment_output,
        workflow_manifest=workflow_manifest,
        timeout_seconds=timeout_seconds,
        max_points=max_points,
    )
    result = approve_patch_apply(
        design_path,
        patch_path,
        output_path=output_path,
        in_place=in_place,
        receipt_path=receipt_path,
        regenerate_source_netlist=regenerate_source_netlist,
        approval_store=approval_store,
        ttl_seconds=ttl_seconds,
        authorization_context_digest=contract.authorization_context_digest,
    )
    result.update(
        {
            "command": "patch-verify-approve",
            "verification_plan": str(contract.plan_source),
            "verification_plan_digest": _digest(contract.plan),
            "requirement_count": len(contract.plan["requirements"]),
            "experiment_output": str(contract.experiment_output),
            "workflow_manifest": str(contract.manifest),
            "timeout_seconds": contract.timeout_seconds,
            "max_points": contract.max_points,
            "commit_policy": "all-requirements-pass",
            "failure_policy": "discard-uncommitted-candidate",
        }
    )
    return result


def _initial_manifest(
    contract: _WorkflowContract, approval_id: str
) -> dict[str, Any]:
    now = _timestamp()
    return {
        "schema_version": PATCH_WORKFLOW_SCHEMA_VERSION,
        "kind": "multisim-mcp-verified-patch-workflow",
        "workflow_id": f"patch-workflow-{uuid.uuid4().hex}",
        "state": "running",
        "created_at": now,
        "updated_at": now,
        "approval_id": approval_id,
        "authorization_context_digest": contract.authorization_context_digest,
        "design_path": str(contract.design_source),
        "patch_path": str(contract.patch_source),
        "target_path": str(contract.target),
        "receipt_path": str(contract.receipt),
        "input_design_digest": _digest(
            read_design_document(str(contract.design_source))[1].to_dict()
        ),
        "candidate_design_digest": _digest(contract.prepared.candidate.to_dict()),
        "patch_digest": _digest(contract.prepared.patch.to_dict()),
        "verification_plan_path": str(contract.plan_source),
        "verification_plan_digest": _digest(contract.plan),
        "experiment_output_path": str(contract.experiment_output),
        "timeout_seconds": contract.timeout_seconds,
        "max_points": contract.max_points,
        "commit_policy": "all-requirements-pass",
        "failure_policy": "discard-uncommitted-candidate",
        "experiment": None,
        "transaction": None,
        "failure": None,
    }


def _updated(
    manifest: Mapping[str, Any],
    *,
    state: str,
    experiment: Mapping[str, Any] | None = None,
    transaction: Mapping[str, Any] | None = None,
    failure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    value = dict(manifest)
    value["state"] = state
    value["updated_at"] = _timestamp()
    if experiment is not None:
        value["experiment"] = dict(experiment)
    if transaction is not None:
        value["transaction"] = dict(transaction)
    value["failure"] = dict(failure) if failure is not None else None
    _validate_workflow_manifest(value)
    return value


def _validate_workflow_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema_version",
        "kind",
        "workflow_id",
        "state",
        "created_at",
        "updated_at",
        "approval_id",
        "authorization_context_digest",
        "design_path",
        "patch_path",
        "target_path",
        "receipt_path",
        "input_design_digest",
        "candidate_design_digest",
        "patch_digest",
        "verification_plan_path",
        "verification_plan_digest",
        "experiment_output_path",
        "timeout_seconds",
        "max_points",
        "commit_policy",
        "failure_policy",
        "experiment",
        "transaction",
        "failure",
    }
    if set(value) != allowed:
        raise ValueError("workflow manifest fields are invalid")
    if value.get("schema_version") != PATCH_WORKFLOW_SCHEMA_VERSION:
        raise ValueError("workflow manifest schema_version is invalid")
    if value.get("kind") != "multisim-mcp-verified-patch-workflow":
        raise ValueError("workflow manifest kind is invalid")
    if not isinstance(value.get("workflow_id"), str) or not _WORKFLOW_ID_RE.fullmatch(
        value["workflow_id"]
    ):
        raise ValueError("workflow manifest workflow_id is invalid")
    if value.get("state") not in _WORKFLOW_STATES:
        raise ValueError("workflow manifest state is invalid")
    if not isinstance(value.get("approval_id"), str) or not _APPROVAL_ID_RE.fullmatch(
        value["approval_id"]
    ):
        raise ValueError("workflow manifest approval_id is invalid")
    for key in (
        "authorization_context_digest",
        "input_design_digest",
        "candidate_design_digest",
        "patch_digest",
        "verification_plan_digest",
    ):
        if not isinstance(value.get(key), str) or not _SHA256_RE.fullmatch(value[key]):
            raise ValueError(f"workflow manifest {key} is invalid")
    for key in (
        "created_at",
        "updated_at",
        "design_path",
        "patch_path",
        "target_path",
        "receipt_path",
        "verification_plan_path",
        "experiment_output_path",
    ):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ValueError(f"workflow manifest {key} is invalid")
    _validate_runtime_limits(value.get("timeout_seconds"), value.get("max_points"))
    if value.get("commit_policy") != "all-requirements-pass":
        raise ValueError("workflow manifest commit policy is invalid")
    if value.get("failure_policy") != "discard-uncommitted-candidate":
        raise ValueError("workflow manifest failure policy is invalid")
    for key in ("experiment", "transaction", "failure"):
        if value.get(key) is not None and not isinstance(value[key], Mapping):
            raise ValueError(f"workflow manifest {key} is invalid")
    return dict(value)


def read_verified_patch_workflow(path: str) -> tuple[Path, dict[str, Any]]:
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise ValueError("workflow manifest must not be a symbolic link")
    source = unresolved.resolve()
    value = _read_json(source, "workflow manifest", MAX_VERIFICATION_PLAN_BYTES)
    return source, _validate_workflow_manifest(value)


def _experiment_evidence(
    contract: _WorkflowContract, result: Mapping[str, Any]
) -> dict[str, Any]:
    verification = result.get("verification")
    if not isinstance(verification, Mapping):
        raise RuntimeError("verified experiment returned no verification result")
    verification_path = Path(str(result.get("verification_path", ""))).resolve()
    try:
        verification_path.relative_to(contract.experiment_output)
    except ValueError as exc:
        raise RuntimeError("verification artifact escapes experiment output") from exc
    stored = _read_json(
        verification_path, "verification artifact", MAX_VERIFICATION_PLAN_BYTES
    )
    if _canonical_bytes(stored) != _canonical_bytes(verification):
        raise RuntimeError("verification artifact does not match runner result")
    directory_manifest = contract.experiment_output / DIRECTORY_MANIFEST_NAME
    if not directory_manifest.is_file() or directory_manifest.is_symlink():
        raise RuntimeError("experiment directory manifest is missing")
    directory_record = read_directory_manifest(contract.experiment_output, verify=True)
    if (
        directory_record.directory_kind != "experiment"
        or directory_record.state != "succeeded"
        or directory_record.entity_id != result["experiment_id"]
        or verification_path.name
        not in {artifact.path for artifact in directory_record.artifacts}
    ):
        raise RuntimeError("experiment directory manifest does not bind verification")
    counts = verification.get("counts")
    assert isinstance(counts, Mapping)
    return {
        "experiment_id": result["experiment_id"],
        "output_dir": str(contract.experiment_output),
        "verification_path": str(verification_path),
        "verification_sha256": _sha256_file(verification_path),
        "directory_manifest_path": str(directory_manifest),
        "directory_manifest_sha256": _sha256_file(directory_manifest),
        "overall_status": verification["overall_status"],
        "counts": {
            key: int(counts[key]) for key in ("pass", "fail", "unverified")
        },
        "requirement_count": len(verification["requirements"]),
    }


def _failure(exc: Exception) -> dict[str, str]:
    return {
        "type": type(exc).__name__,
        "message": str(exc)[:1000],
    }


def _input_is_preserved(manifest: Mapping[str, Any]) -> bool:
    design_path = Path(str(manifest["design_path"])).resolve()
    target = Path(str(manifest["target_path"])).resolve()
    receipt = Path(str(manifest["receipt_path"])).resolve()
    if receipt.exists():
        return False
    if target == design_path:
        try:
            _, current = read_design_document(str(design_path))
        except (OSError, ValueError):
            return False
        return _digest(current.to_dict()) == manifest["input_design_digest"]
    return not target.exists()


def _validate_recorded_experiment(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    experiment = manifest.get("experiment")
    if not isinstance(experiment, Mapping) or experiment.get("overall_status") != "pass":
        raise ValueError("workflow cannot commit without a recorded passing verdict")
    root = Path(str(manifest["experiment_output_path"])).resolve()
    verification_path = Path(str(experiment.get("verification_path", ""))).resolve()
    directory_path = Path(
        str(experiment.get("directory_manifest_path", ""))
    ).resolve()
    try:
        verification_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("recorded verification path escapes experiment output") from exc
    if directory_path != root / DIRECTORY_MANIFEST_NAME:
        raise ValueError("recorded experiment manifest path is invalid")
    for path, key in (
        (verification_path, "verification_sha256"),
        (directory_path, "directory_manifest_sha256"),
    ):
        expected = experiment.get(key)
        if (
            not isinstance(expected, str)
            or not _SHA256_RE.fullmatch(expected)
            or not path.is_file()
            or path.is_symlink()
            or _sha256_file(path) != expected
        ):
            raise ValueError(f"recorded experiment {key} mismatch")
    directory_record = read_directory_manifest(root, verify=True)
    if (
        directory_record.directory_kind != "experiment"
        or directory_record.state != "succeeded"
        or directory_record.entity_id != experiment.get("experiment_id")
        or verification_path.name
        not in {artifact.path for artifact in directory_record.artifacts}
    ):
        raise ValueError("recorded experiment manifest does not bind verification")
    return experiment


def execute_verified_patch_application(
    experiment_service: ExperimentApplicationService,
    design_path: str,
    patch_path: str,
    *,
    output_path: str | None,
    in_place: bool,
    receipt_path: str,
    regenerate_source_netlist: bool,
    verification_plan_path: str,
    experiment_output: str,
    workflow_manifest: str,
    approval_token: str,
    timeout_seconds: float = 120.0,
    max_points: int = 2000,
    approval_store: str | None = None,
) -> dict[str, Any]:
    """Simulate an in-memory candidate and persist it only after a strict pass."""
    if not isinstance(experiment_service, ExperimentApplicationService):
        raise ValueError("experiment_service must be ExperimentApplicationService")
    contract = _workflow_contract(
        design_path=design_path,
        patch_path=patch_path,
        output_path=output_path,
        in_place=in_place,
        receipt_path=receipt_path,
        regenerate_source_netlist=regenerate_source_netlist,
        verification_plan_path=verification_plan_path,
        experiment_output=experiment_output,
        workflow_manifest=workflow_manifest,
        timeout_seconds=timeout_seconds,
        max_points=max_points,
    )
    approval = validate_patch_apply_approval(
        design_path,
        patch_path,
        output_path=output_path,
        in_place=in_place,
        receipt_path=receipt_path,
        regenerate_source_netlist=regenerate_source_netlist,
        approval_token=approval_token,
        approval_store=approval_store,
        authorization_context_digest=contract.authorization_context_digest,
    )
    manifest = _initial_manifest(contract, approval["approval_id"])
    _validate_workflow_manifest(manifest)
    _atomic_json(contract.manifest, manifest, create=True)
    _workflow_crash_point("manifest_created")
    request = ExperimentRequest(
        design=contract.prepared.candidate,
        commands=contract.plan["commands"],
        output_directory=str(contract.experiment_output),
        title=contract.plan["title"],
        timeout_seconds=contract.timeout_seconds,
        max_points=contract.max_points,
        overwrite=False,
        owner=manifest["workflow_id"],
        requirements=tuple(contract.plan["requirements"]),
        theoretical_values=contract.plan["theoretical_values"],
    )
    try:
        experiment_result = experiment_service.run(request)
        evidence = _experiment_evidence(contract, experiment_result)
    except Exception as exc:
        failed = _updated(manifest, state="experiment_error", failure=_failure(exc))
        _atomic_json(contract.manifest, failed, create=False)
        preserved = _input_is_preserved(failed)
        return {
            "schema_version": PATCH_WORKFLOW_SCHEMA_VERSION,
            "command": "patch-verify-apply",
            "success": False,
            "state": "experiment_error",
            "design_committed": False,
            "input_design_preserved": preserved,
            "approval_consumed": False,
            "workflow_manifest": str(contract.manifest),
            "experiment_output": str(contract.experiment_output),
            "error": _failure(exc),
        }
    verdict = str(evidence["overall_status"])
    state = "verification_passed" if verdict == "pass" else "rejected"
    manifest = _updated(manifest, state=state, experiment=evidence)
    _atomic_json(contract.manifest, manifest, create=False)
    _workflow_crash_point("verification_recorded")
    if verdict != "pass":
        preserved = _input_is_preserved(manifest)
        if not preserved:
            raise RuntimeError(
                "verification rejected the candidate but the approved input/target changed"
            )
        return {
            "schema_version": PATCH_WORKFLOW_SCHEMA_VERSION,
            "command": "patch-verify-apply",
            "success": False,
            "state": "rejected",
            "verification_status": verdict,
            "verification_counts": evidence["counts"],
            "design_committed": False,
            "input_design_preserved": True,
            "automatic_rollback": "discarded-uncommitted-candidate",
            "approval_consumed": False,
            "workflow_manifest": str(contract.manifest),
            "experiment_output": str(contract.experiment_output),
        }
    _validate_recorded_experiment(manifest)
    try:
        transaction = apply_patch_transaction(
            design_path,
            patch_path,
            output_path=output_path,
            in_place=in_place,
            receipt_path=receipt_path,
            regenerate_source_netlist=regenerate_source_netlist,
            approval_token=approval_token,
            approval_store=approval_store,
            authorization_context_digest=contract.authorization_context_digest,
        )
    except Exception as exc:
        failed = _updated(
            manifest,
            state="commit_failed",
            experiment=evidence,
            failure=_failure(exc),
        )
        _atomic_json(contract.manifest, failed, create=False)
        return {
            "schema_version": PATCH_WORKFLOW_SCHEMA_VERSION,
            "command": "patch-verify-apply",
            "success": False,
            "state": "commit_failed",
            "verification_status": "pass",
            "design_committed": False,
            "input_design_preserved": _input_is_preserved(failed),
            "approval_consumed": False,
            "workflow_manifest": str(contract.manifest),
            "experiment_output": str(contract.experiment_output),
            "error": _failure(exc),
        }
    _workflow_crash_point("patch_committed")
    transaction_summary = {
        "transaction_id": transaction["transaction_id"],
        "receipt_path": transaction["receipt"],
        "receipt_sha256": _sha256_file(Path(transaction["receipt"])),
        "output_path": transaction["output"],
        "output_design_digest": transaction["output_design_digest"],
        "approval_consumed": transaction["approval_consumed"],
        "patch_journal_recovery_required": transaction["journal"][
            "recovery_required"
        ],
    }
    committed = _updated(
        manifest,
        state="committed",
        experiment=evidence,
        transaction=transaction_summary,
    )
    manifest_recovery_required = False
    try:
        _atomic_json(contract.manifest, committed, create=False)
    except Exception:
        manifest_recovery_required = True
    return {
        "schema_version": PATCH_WORKFLOW_SCHEMA_VERSION,
        "command": "patch-verify-apply",
        "success": True,
        "state": "committed",
        "verification_status": "pass",
        "verification_counts": evidence["counts"],
        "design_committed": True,
        "input_design_preserved": not in_place,
        "approval_consumed": True,
        "transaction": transaction,
        "workflow_manifest": str(contract.manifest),
        "workflow_manifest_recovery_required": manifest_recovery_required,
        "experiment_output": str(contract.experiment_output),
        "experiment_id": evidence["experiment_id"],
    }


def recover_verified_patch_workflow(workflow_manifest: str) -> dict[str, Any]:
    """Finalize audit state or safely abort a crash-interrupted workflow."""
    path, manifest = read_verified_patch_workflow(workflow_manifest)
    receipt_path = Path(manifest["receipt_path"]).resolve()
    target_path = Path(manifest["target_path"]).resolve()
    design_path = Path(manifest["design_path"]).resolve()
    if receipt_path.is_file():
        _, receipt = read_transaction_receipt(str(receipt_path))
        if receipt["approval_id"] != manifest["approval_id"]:
            raise ValueError("workflow receipt approval_id mismatch")
        if receipt["output_design_digest"] != manifest["candidate_design_digest"]:
            raise ValueError("workflow receipt candidate digest mismatch")
        _, target = read_design_document(str(target_path))
        if _digest(target.to_dict()) != manifest["candidate_design_digest"]:
            raise ValueError("workflow target does not match committed receipt")
        experiment = _validate_recorded_experiment(manifest)
        summary = {
            "transaction_id": receipt["transaction_id"],
            "receipt_path": str(receipt_path),
            "receipt_sha256": _sha256_file(receipt_path),
            "output_path": str(target_path),
            "output_design_digest": receipt["output_design_digest"],
            "approval_consumed": True,
            "patch_journal_recovery_required": False,
        }
        recovered = _updated(
            manifest,
            state="committed",
            experiment=experiment,
            transaction=summary,
        )
        _atomic_json(path, recovered, create=False)
        return {
            "schema_version": PATCH_WORKFLOW_SCHEMA_VERSION,
            "command": "patch-verify-recover",
            "success": True,
            "action": "finalized-committed",
            "state": "committed",
            "target": str(target_path),
            "receipt": str(receipt_path),
            "workflow_manifest": str(path),
        }
    if target_path == design_path:
        _, current = read_design_document(str(design_path))
        safe = _digest(current.to_dict()) == manifest["input_design_digest"]
    else:
        safe = not target_path.exists()
    if not safe:
        raise RuntimeError(
            "workflow has no receipt but target state is ambiguous; run patch-recover "
            "for any adjacent transaction journal and retry"
        )
    recovered = _updated(
        manifest,
        state=(manifest["state"] if manifest["state"] in {"rejected", "experiment_error"} else "aborted"),
        experiment=(
            manifest["experiment"]
            if isinstance(manifest.get("experiment"), Mapping)
            else None
        ),
        failure=(
            manifest["failure"]
            if isinstance(manifest.get("failure"), Mapping)
            else None
        ),
    )
    _atomic_json(path, recovered, create=False)
    return {
        "schema_version": PATCH_WORKFLOW_SCHEMA_VERSION,
        "command": "patch-verify-recover",
        "success": True,
        "action": "confirmed-no-commit",
        "state": recovered["state"],
        "target": str(target_path),
        "receipt": str(receipt_path),
        "workflow_manifest": str(path),
        "input_design_preserved": True,
    }


__all__ = [
    "PATCH_WORKFLOW_SCHEMA_VERSION",
    "approve_verified_patch_application",
    "execute_verified_patch_application",
    "read_patch_verification_plan",
    "read_verified_patch_workflow",
    "recover_verified_patch_workflow",
]
