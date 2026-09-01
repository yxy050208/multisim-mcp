"""Evidence-backed, read-only evaluation of one explicit design patch.

The service runs the baseline and the in-memory patch candidate under the same
verification contract.  It publishes reproducible evidence and diagnoses, but
never writes either design back to a source document and never treats a passing
candidate as permission to adopt it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from .design_comparison import (
    DesignVariantComparisonService,
    read_design_comparison,
    validate_comparison_spec,
)
from .design_diagnosis import (
    DesignDiagnosisService,
    load_experiment_diagnosis_evidence,
)
from .design_patch_service import PreparedDesignPatch, prepare_design_patch
from .design_verification import validate_experiment_spec
from .eda_core import CircuitDesign, DesignPatch
from .experiment_service import ExperimentApplicationService
from .job_engine import output_lease
from .spice_adapter import circuit_design_to_spice
from .workspace_manifest import (
    DIRECTORY_MANIFEST_NAME,
    read_directory_manifest,
    write_directory_manifest,
)


PATCH_EVALUATION_SCHEMA_VERSION: Final = 1
MAX_PATCH_EVALUATION_SPEC_BYTES: Final = 1024 * 1024
PATCH_EVALUATION_STATE_NAME: Final = "evaluation.json"
PATCH_EVALUATION_PLAN_NAME: Final = "verification-plan.json"
SOURCE_DESIGN_NAME: Final = "source-design.json"
CANDIDATE_DESIGN_NAME: Final = "candidate-design.json"
PATCH_NAME: Final = "patch.json"
INVERSE_PATCH_NAME: Final = "inverse-patch.json"
BEFORE_DIAGNOSIS_NAME: Final = "diagnosis-before.json"
AFTER_DIAGNOSIS_NAME: Final = "diagnosis-after.json"

ProgressCallback = Callable[[str, int, str], None]
CancellationProbe = Callable[[], bool]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _plain_json(value: object) -> Any:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def read_patch_evaluation_spec(path: str) -> tuple[Path, dict[str, Any]]:
    """Read one bounded strict UTF-8 PatchEvaluationSpec document."""
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise ValueError("patch evaluation spec must not be a symbolic link")
    source = unresolved.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"patch evaluation spec does not exist: {source}")
    if source.stat().st_size > MAX_PATCH_EVALUATION_SPEC_BYTES:
        raise ValueError("patch evaluation spec exceeds the size limit")
    try:
        raw = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError("patch evaluation spec must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"patch evaluation spec is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("patch evaluation spec must contain one JSON object")
    return source, raw


def _normalize_plan(
    spec: Mapping[str, Any], baseline: CircuitDesign, candidate: CircuitDesign
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(spec, Mapping) or spec.get("schema_version") != 1:
        raise ValueError("PatchEvaluationSpec schema_version must be 1")
    allowed = {
        "schema_version",
        "title",
        "commands",
        "requirements",
        "theoretical_values",
    }
    unknown = set(spec) - allowed
    if unknown:
        raise ValueError(
            "PatchEvaluationSpec contains unknown fields: "
            + ", ".join(sorted(unknown))
        )
    baseline_netlist = circuit_design_to_spice(baseline)
    candidate_netlist = circuit_design_to_spice(candidate)
    if _canonical_bytes(baseline_netlist) == _canonical_bytes(candidate_netlist):
        raise ValueError("patch does not change the simulated electrical design")
    normalized = validate_experiment_spec(
        {
            "schema_version": 1,
            "title": spec.get("title", ""),
            "netlist": baseline_netlist,
            "commands": spec.get("commands", ""),
            "requirements": spec.get("requirements", []),
            "theoretical_values": spec.get("theoretical_values", {}),
        }
    )
    if not normalized["requirements"]:
        raise ValueError("PatchEvaluationSpec requires at least one hard requirement")
    validate_experiment_spec(
        {
            **normalized,
            "netlist": candidate_netlist,
        }
    )
    plan = {
        "schema_version": PATCH_EVALUATION_SCHEMA_VERSION,
        "title": normalized["title"],
        "commands": normalized["commands"],
        "requirements": normalized["requirements"],
        "theoretical_values": normalized["theoretical_values"],
    }
    # The comparison engine requires a finite ranking measurement.  Patch
    # eligibility below is based only on the hard requirements, never this
    # deterministic internal ordering objective.
    comparison_spec = {
        **plan,
        "objective": {
            "requirement_id": normalized["requirements"][0]["id"],
            "goal": "minimize",
        },
    }
    validate_comparison_spec(
        comparison_spec, {"baseline": baseline, "candidate": candidate}
    )
    return plan, comparison_spec


def _finding_key(finding: Mapping[str, Any]) -> str:
    evidence = finding.get("evidence")
    requirement_id = (
        evidence.get("requirement_id") if isinstance(evidence, Mapping) else None
    )
    return _digest(
        {
            "category": finding.get("category"),
            "code": finding.get("code"),
            "requirement_id": requirement_id,
            "affected_components": finding.get("affected_components", []),
            "affected_nets": finding.get("affected_nets", []),
        }
    )


def _diagnosis_delta(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    before_findings = [
        item for item in before.get("findings", []) if isinstance(item, Mapping)
    ]
    after_findings = [
        item for item in after.get("findings", []) if isinstance(item, Mapping)
    ]
    before_by_key = {_finding_key(item): item for item in before_findings}
    after_by_key = {_finding_key(item): item for item in after_findings}
    resolved = [
        _plain_json(before_by_key[key])
        for key in sorted(set(before_by_key) - set(after_by_key))
    ]
    introduced = [
        _plain_json(after_by_key[key])
        for key in sorted(set(after_by_key) - set(before_by_key))
    ]
    severity_delta = {
        severity: int(after.get("severity_counts", {}).get(severity, 0))
        - int(before.get("severity_counts", {}).get(severity, 0))
        for severity in ("error", "warning", "info")
    }
    return {
        "before_status": before.get("overall_status"),
        "after_status": after.get("overall_status"),
        "severity_delta": severity_delta,
        "resolved_finding_count": len(resolved),
        "introduced_finding_count": len(introduced),
        "resolved_findings": resolved,
        "introduced_findings": introduced,
    }


def _evaluation_status(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[str, bool, bool]:
    baseline_experiment = baseline.get("experiment")
    candidate_experiment = candidate.get("experiment")
    baseline_status = (
        baseline_experiment.get("overall_status")
        if isinstance(baseline_experiment, Mapping)
        else "error"
    )
    candidate_status = (
        candidate_experiment.get("overall_status")
        if isinstance(candidate_experiment, Mapping)
        else "error"
    )
    candidate_passed = (
        candidate_status == "pass" and candidate.get("status") == "feasible"
    )
    baseline_passed = (
        baseline_status == "pass" and baseline.get("status") == "feasible"
    )
    if candidate_passed and not baseline_passed:
        return "candidate-improved-and-passed", True, True
    if candidate_passed:
        return "candidate-passed", True, True
    if candidate_status == "fail":
        return "candidate-failed-requirements", False, False
    if candidate_status == "unverified":
        return "candidate-unverified", False, False
    return "inconclusive", False, False


def _artifact_allowlist(root: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    exact_roles = {
        PATCH_EVALUATION_STATE_NAME: "patch-evaluation-state",
        PATCH_EVALUATION_PLAN_NAME: "verification-plan",
        SOURCE_DESIGN_NAME: "source-design",
        CANDIDATE_DESIGN_NAME: "candidate-design",
        PATCH_NAME: "design-patch",
        INVERSE_PATCH_NAME: "inverse-patch",
        BEFORE_DIAGNOSIS_NAME: "diagnosis-before",
        AFTER_DIAGNOSIS_NAME: "diagnosis-after",
    }
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(
                f"patch evaluation artifacts must not contain symlinks: {path}"
            )
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == DIRECTORY_MANIFEST_NAME:
            continue
        artifacts[relative] = exact_roles.get(relative, "comparison-artifact")
    return artifacts


class DesignPatchEvaluationService:
    """Evaluate one explicit patch twice without adopting or persisting it."""

    def __init__(self, experiment_service: ExperimentApplicationService) -> None:
        if not isinstance(experiment_service, ExperimentApplicationService):
            raise ValueError("experiment_service must be ExperimentApplicationService")
        self._experiments = experiment_service

    def run(
        self,
        design: CircuitDesign,
        patch: DesignPatch | Mapping[str, Any],
        spec: Mapping[str, Any],
        output_directory: str,
        *,
        regenerate_source_netlist: bool = False,
        timeout_per_experiment: float = 120.0,
        max_points: int = 2000,
        checkpoint: ProgressCallback | None = None,
        cancel_requested: CancellationProbe | None = None,
    ) -> dict[str, Any]:
        if not isinstance(design, CircuitDesign):
            raise ValueError("design must be CircuitDesign")
        if not isinstance(regenerate_source_netlist, bool):
            raise ValueError("regenerate_source_netlist must be a boolean")
        if (
            isinstance(timeout_per_experiment, bool)
            or not isinstance(timeout_per_experiment, (int, float))
            or not math.isfinite(float(timeout_per_experiment))
            or not 0 < float(timeout_per_experiment) <= 3600
        ):
            raise ValueError("timeout_per_experiment must be between 0 and 3600")
        if (
            isinstance(max_points, bool)
            or not isinstance(max_points, int)
            or not 1 <= max_points <= 100_000
        ):
            raise ValueError("max_points must be between 1 and 100000")
        if checkpoint is not None and not callable(checkpoint):
            raise ValueError("checkpoint must be callable")
        if cancel_requested is not None and not callable(cancel_requested):
            raise ValueError("cancel_requested must be callable")
        if not isinstance(output_directory, str) or not output_directory.strip():
            raise ValueError("output_directory must not be empty")
        unresolved = Path(output_directory).expanduser()
        if unresolved.is_symlink():
            raise ValueError("output_directory must not be a symbolic link")
        root = unresolved.resolve()
        if root == Path(root.anchor):
            raise ValueError("output_directory must not be a filesystem root")

        prepared: PreparedDesignPatch = prepare_design_patch(
            design,
            patch,
            regenerate_source_netlist=regenerate_source_netlist,
        )
        if prepared.source_netlist_update_required:
            raise ValueError(
                "candidate source_netlist would be stale; explicitly set "
                "regenerate_source_netlist=true for in-memory evaluation"
            )
        plan, comparison_spec = _normalize_plan(spec, design, prepared.candidate)
        source_snapshot = design.to_dict()
        evaluation_id = f"patch-evaluation-{uuid.uuid4().hex}"

        def notify(stage: str, progress: int, message: str) -> None:
            if checkpoint is not None:
                checkpoint(stage, progress, message)

        with output_lease(str(root), evaluation_id):
            if root.exists() and not root.is_dir():
                raise ValueError("output_directory exists and is not a directory")
            if root.exists() and any(root.iterdir()):
                raise FileExistsError(
                    f"refusing to overwrite non-empty patch evaluation directory: {root}"
                )
            root.mkdir(parents=True, exist_ok=True)
            _atomic_json(root / SOURCE_DESIGN_NAME, source_snapshot)
            _atomic_json(root / CANDIDATE_DESIGN_NAME, prepared.candidate.to_dict())
            _atomic_json(root / PATCH_NAME, prepared.patch.to_dict())
            _atomic_json(root / INVERSE_PATCH_NAME, prepared.inverse_patch.to_dict())
            _atomic_json(root / PATCH_EVALUATION_PLAN_NAME, plan)
            notify("patch_evaluation_preflight", 3, "Validated patch and common plan")

            comparison_root = root / "comparison"
            comparison_result = DesignVariantComparisonService(self._experiments).run(
                {"baseline": design, "candidate": prepared.candidate},
                comparison_spec,
                str(comparison_root),
                timeout_per_experiment=float(timeout_per_experiment),
                max_points=max_points,
                checkpoint=(
                    None
                    if checkpoint is None
                    else lambda stage, progress, message: notify(
                        f"comparison_{stage}", 5 + int(progress * 0.75), message
                    )
                ),
                cancel_requested=cancel_requested,
            )
            comparison = read_design_comparison(str(comparison_root), verify=True)
            by_id = {
                str(item.get("variant_id")): item
                for item in comparison.get("evaluations", [])
                if isinstance(item, Mapping)
            }
            baseline_eval = by_id.get("baseline", {"status": "error"})
            candidate_eval = by_id.get("candidate", {"status": "error"})

            diagnoses: dict[str, dict[str, Any]] = {}
            for variant_id, variant_design, evaluation in (
                ("baseline", design, baseline_eval),
                ("candidate", prepared.candidate, candidate_eval),
            ):
                experiment = evaluation.get("experiment")
                failure = evaluation.get("error")
                evidence = None
                if isinstance(experiment, Mapping):
                    relative = experiment.get("output_directory")
                    if isinstance(relative, str):
                        evidence = load_experiment_diagnosis_evidence(
                            variant_design, str(comparison_root / relative)
                        )
                diagnoses[variant_id] = DesignDiagnosisService().run(
                    variant_design,
                    experiment_evidence=evidence,
                    simulation_failure=(failure if isinstance(failure, Mapping) else None),
                )
            _atomic_json(root / BEFORE_DIAGNOSIS_NAME, diagnoses["baseline"])
            _atomic_json(root / AFTER_DIAGNOSIS_NAME, diagnoses["candidate"])
            delta = _diagnosis_delta(
                diagnoses["baseline"], diagnoses["candidate"]
            )
            status, success, eligible = _evaluation_status(
                baseline_eval, candidate_eval
            )
            if design.to_dict() != source_snapshot:
                raise RuntimeError("source design changed during read-only evaluation")
            completed_at = _utc_now()
            state = {
                "schema_version": PATCH_EVALUATION_SCHEMA_VERSION,
                "kind": "multisim-mcp-design-patch-evaluation",
                "evaluation_id": evaluation_id,
                "state": "succeeded",
                "status": status,
                "success": success,
                "completed_at": completed_at,
                "design_id": design.design_id,
                "source_revision": design.revision,
                "candidate_revision": prepared.candidate.revision,
                "patch_id": prepared.patch.patch_id,
                "patch_digest": _digest(prepared.patch.to_dict()),
                "verification_plan_digest": _digest(plan),
                "source_design_modified": False,
                "candidate_persisted_as_source": False,
                "approval_required_before_apply": True,
                "adoption_eligible": eligible,
                "source_netlist_regenerated_in_memory": prepared.source_netlist_regenerated,
                "comparison": {
                    "comparison_id": comparison_result["comparison_id"],
                    "status": comparison_result["status"],
                    "baseline": _plain_json(baseline_eval),
                    "candidate": _plain_json(candidate_eval),
                },
                "diagnosis_delta": delta,
                "artifacts": {
                    "source_design": SOURCE_DESIGN_NAME,
                    "candidate_design": CANDIDATE_DESIGN_NAME,
                    "patch": PATCH_NAME,
                    "inverse_patch": INVERSE_PATCH_NAME,
                    "verification_plan": PATCH_EVALUATION_PLAN_NAME,
                    "diagnosis_before": BEFORE_DIAGNOSIS_NAME,
                    "diagnosis_after": AFTER_DIAGNOSIS_NAME,
                    "comparison": "comparison",
                },
                "limitations": [
                    "A passing candidate satisfies only the supplied requirements.",
                    "The candidate is not automatically better on unspecified behavior.",
                    "No patch or candidate is adopted without a separate approval workflow.",
                ],
            }
            _atomic_json(root / PATCH_EVALUATION_STATE_NAME, state)
            manifest = write_directory_manifest(
                root,
                directory_kind="patch-evaluation",
                entity_id=evaluation_id,
                state="succeeded",
                artifacts=_artifact_allowlist(root),
                metadata={
                    "operation": "evaluate-design-patch",
                    "status": status,
                    "patch_id": prepared.patch.patch_id,
                    "source_design_modified": False,
                    "candidate_persisted_as_source": False,
                    "adoption_eligible": eligible,
                },
            )
            notify("complete", 100, f"Patch evaluation finished: {status}")
            return {
                "schema_version": PATCH_EVALUATION_SCHEMA_VERSION,
                "success": success,
                "status": status,
                "evaluation_id": evaluation_id,
                "output_dir": str(root),
                "summary": str(root / PATCH_EVALUATION_STATE_NAME),
                "patch": str(root / PATCH_NAME),
                "inverse_patch": str(root / INVERSE_PATCH_NAME),
                "candidate_design": str(root / CANDIDATE_DESIGN_NAME),
                "diagnosis_before": str(root / BEFORE_DIAGNOSIS_NAME),
                "diagnosis_after": str(root / AFTER_DIAGNOSIS_NAME),
                "directory_manifest": str(root / DIRECTORY_MANIFEST_NAME),
                "directory_manifest_revision": manifest.revision,
                "source_design_modified": False,
                "candidate_persisted_as_source": False,
                "approval_required_before_apply": True,
                "adoption_eligible": eligible,
                "diagnosis_delta": delta,
            }


def read_design_patch_evaluation(
    output_directory: str, *, verify: bool = True
) -> dict[str, Any]:
    """Read one completed patch evaluation and optionally verify all artifacts."""
    unresolved = Path(output_directory).expanduser()
    if unresolved.is_symlink():
        raise ValueError("patch evaluation directory must not be a symbolic link")
    root = unresolved.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"patch evaluation directory does not exist: {root}")
    path = root / PATCH_EVALUATION_STATE_NAME
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"patch evaluation summary is missing: {path}")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("patch evaluation summary exceeds the size limit")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {constant}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError("patch evaluation summary must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"patch evaluation summary is not valid JSON: {exc}") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != PATCH_EVALUATION_SCHEMA_VERSION
        or value.get("kind") != "multisim-mcp-design-patch-evaluation"
    ):
        raise ValueError("patch evaluation summary contract is invalid")
    if verify:
        manifest = read_directory_manifest(root, verify=True)
        if (
            manifest.directory_kind != "patch-evaluation"
            or manifest.entity_id != value.get("evaluation_id")
            or manifest.state != value.get("state")
        ):
            raise ValueError("patch evaluation manifest does not match summary")
    return _plain_json(value)


__all__ = [
    "AFTER_DIAGNOSIS_NAME",
    "BEFORE_DIAGNOSIS_NAME",
    "CANDIDATE_DESIGN_NAME",
    "DesignPatchEvaluationService",
    "INVERSE_PATCH_NAME",
    "MAX_PATCH_EVALUATION_SPEC_BYTES",
    "PATCH_EVALUATION_PLAN_NAME",
    "PATCH_EVALUATION_SCHEMA_VERSION",
    "PATCH_EVALUATION_STATE_NAME",
    "PATCH_NAME",
    "SOURCE_DESIGN_NAME",
    "read_design_patch_evaluation",
    "read_patch_evaluation_spec",
]
