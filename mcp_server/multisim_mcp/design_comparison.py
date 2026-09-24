"""Deterministic, evidence-backed comparison of complete circuit designs."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from .design_verification import validate_experiment_spec
from .eda_core import CircuitDesign
from .experiment_service import ExperimentApplicationService, ExperimentRequest
from .job_engine import output_lease
from .ranked_evaluation import (
    normalize_single_objective,
    validate_ranked_experiment_evidence,
)
from .safety import validate_analysis_commands
from .spice_adapter import circuit_design_to_spice
from .workspace_manifest import (
    DIRECTORY_MANIFEST_NAME,
    read_directory_manifest,
    write_directory_manifest,
)


COMPARISON_SCHEMA_VERSION: Final = 1
MAX_COMPARISON_VARIANTS: Final = 16
MAX_COMPARISON_SPEC_BYTES: Final = 1024 * 1024
COMPARISON_STATE_NAME: Final = "comparison.json"
COMPARISON_SPEC_NAME: Final = "comparison-spec.json"
COMPARISON_DATA_NAME: Final = "variants.csv"
VERIFICATION_PLAN_NAME: Final = "verification-plan.json"

_VARIANT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")

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


def validate_design_variants(
    variants: Mapping[str, CircuitDesign],
) -> list[dict[str, Any]]:
    """Validate ordered, bounded variants and precompile every complete design."""
    if not isinstance(variants, Mapping) or not (
        2 <= len(variants) <= MAX_COMPARISON_VARIANTS
    ):
        raise ValueError(
            f"variants must contain between 2 and {MAX_COMPARISON_VARIANTS} designs"
        )
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_netlists: dict[str, str] = {}
    for index, (raw_id, design) in enumerate(variants.items()):
        if not isinstance(raw_id, str):
            raise ValueError(f"variant {index} id must be a string")
        variant_id = raw_id.strip()
        if not _VARIANT_ID_RE.fullmatch(variant_id):
            raise ValueError(f"variant id is invalid: {raw_id!r}")
        folded = variant_id.casefold()
        if folded in seen_ids:
            raise ValueError(f"duplicate variant id: {variant_id}")
        seen_ids.add(folded)
        if not isinstance(design, CircuitDesign):
            raise ValueError(f"variant {variant_id} must be CircuitDesign")
        netlist = circuit_design_to_spice(design)
        netlist_digest = hashlib.sha256(netlist.encode("utf-8")).hexdigest()
        duplicate = seen_netlists.get(netlist_digest)
        if duplicate is not None:
            raise ValueError(
                f"variant {variant_id} is electrically identical to {duplicate}"
            )
        seen_netlists[netlist_digest] = variant_id
        normalized.append(
            {
                "variant_id": variant_id,
                "index": index,
                "design": design,
                "design_digest": _digest(design.to_dict()),
                "netlist_digest": netlist_digest,
                "netlist": netlist,
            }
        )
    return normalized


def validate_comparison_spec(
    spec: Mapping[str, Any], variants: Mapping[str, CircuitDesign]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate one common verification and ranking contract for all variants."""
    normalized_variants = validate_design_variants(variants)
    if not isinstance(spec, Mapping) or spec.get("schema_version") != 1:
        raise ValueError("ComparisonSpec schema_version must be 1")
    allowed = {
        "schema_version",
        "title",
        "commands",
        "requirements",
        "theoretical_values",
        "objective",
    }
    unknown = set(spec) - allowed
    if unknown:
        raise ValueError(
            "ComparisonSpec contains unknown fields: " + ", ".join(sorted(unknown))
        )
    title = str(spec.get("title", "")).strip()
    if not title or "\x00" in title or len(title) > 4096:
        raise ValueError("ComparisonSpec title is empty, invalid, or too long")
    commands = "\n".join(validate_analysis_commands(str(spec.get("commands", ""))))
    first = validate_experiment_spec(
        {
            "schema_version": 1,
            "title": title,
            "netlist": normalized_variants[0]["netlist"],
            "commands": commands,
            "requirements": spec.get("requirements", []),
            "theoretical_values": spec.get("theoretical_values", {}),
        }
    )
    # Validate the same experiment contract against every compiled design before
    # creating an output directory or starting Multisim.
    for item in normalized_variants[1:]:
        validate_experiment_spec(
            {
                "schema_version": 1,
                "title": title,
                "netlist": item["netlist"],
                "commands": commands,
                "requirements": first["requirements"],
                "theoretical_values": first["theoretical_values"],
            }
        )
    known_ids = {item["id"] for item in first["requirements"]}
    objective = normalize_single_objective(spec.get("objective"), known_ids)
    return (
        {
            "schema_version": COMPARISON_SCHEMA_VERSION,
            "title": title,
            "commands": commands,
            "requirements": first["requirements"],
            "theoretical_values": first["theoretical_values"],
            "objective": objective,
        },
        normalized_variants,
    )


def read_comparison_spec(path: str) -> tuple[Path, dict[str, Any]]:
    """Read a strict UTF-8 ComparisonSpec; design-dependent checks occur at run."""
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise ValueError("comparison spec must not be a symbolic link")
    source = unresolved.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"comparison spec does not exist: {source}")
    if source.stat().st_size > MAX_COMPARISON_SPEC_BYTES:
        raise ValueError("comparison spec exceeds the size limit")
    try:
        raw = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError("comparison spec must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"comparison spec is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("comparison spec must contain one JSON object")
    return source, raw


def _write_csv(root: Path, evaluations: list[dict[str, Any]]) -> None:
    with (root / COMPARISON_DATA_NAME).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "rank",
                "variant_id",
                "design_id",
                "design_revision",
                "status",
                "hard_constraint_status",
                "objective_status",
                "objective_value",
                "objective_score",
            ]
        )
        for item in evaluations:
            objective = item.get("objective") or {}
            evidence = item.get("experiment") or {}
            writer.writerow(
                [
                    item.get("rank", ""),
                    item["variant_id"],
                    item["design_id"],
                    item["design_revision"],
                    item["status"],
                    evidence.get("overall_status", "error"),
                    objective.get("status", "unverified"),
                    objective.get("value", ""),
                    objective.get("score", ""),
                ]
            )


def _artifact_allowlist(root: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"comparison artifacts must not contain symlinks: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == DIRECTORY_MANIFEST_NAME:
            continue
        role = (
            "comparison-state"
            if relative == COMPARISON_STATE_NAME
            else "comparison-spec"
            if relative == COMPARISON_SPEC_NAME
            else "comparison-data"
            if relative == COMPARISON_DATA_NAME
            else "verification-plan"
            if relative == VERIFICATION_PLAN_NAME
            else "variant-design"
            if relative.startswith("variants/")
            else "experiment-artifact"
        )
        artifacts[relative] = role
    return artifacts


class DesignVariantComparisonService:
    """Run the same verified experiment over complete designs without mutation."""

    def __init__(self, experiment_service: ExperimentApplicationService) -> None:
        if not isinstance(experiment_service, ExperimentApplicationService):
            raise ValueError("experiment_service must be ExperimentApplicationService")
        self._experiments = experiment_service

    def run(
        self,
        variants: Mapping[str, CircuitDesign],
        spec: Mapping[str, Any],
        output_directory: str,
        *,
        timeout_per_experiment: float = 120.0,
        max_points: int = 2000,
        checkpoint: ProgressCallback | None = None,
        cancel_requested: CancellationProbe | None = None,
    ) -> dict[str, Any]:
        """Evaluate variants in input order and rank only fully verified passes."""
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

        normalized, prepared = validate_comparison_spec(spec, variants)
        comparison_id = f"comparison-{uuid.uuid4().hex}"

        def notify(stage: str, progress: int, message: str) -> None:
            if checkpoint is not None:
                checkpoint(stage, progress, message)

        with output_lease(str(root), comparison_id):
            if root.exists() and not root.is_dir():
                raise ValueError("output_directory exists and is not a directory")
            if root.exists() and any(root.iterdir()):
                raise FileExistsError(
                    f"refusing to overwrite non-empty comparison directory: {root}"
                )
            root.mkdir(parents=True, exist_ok=True)
            (root / "variants").mkdir()
            for item in prepared:
                _atomic_json(
                    root / "variants" / f"{item['variant_id']}.json",
                    item["design"].to_dict(),
                )
            _atomic_json(root / COMPARISON_SPEC_NAME, normalized)
            _atomic_json(
                root / VERIFICATION_PLAN_NAME,
                {
                    "schema_version": 1,
                    "title": normalized["title"],
                    "commands": normalized["commands"],
                    "requirements": normalized["requirements"],
                    "theoretical_values": normalized["theoretical_values"],
                },
            )
            started_at = _utc_now()
            state: dict[str, Any] = {
                "schema_version": COMPARISON_SCHEMA_VERSION,
                "kind": "multisim-mcp-design-comparison",
                "comparison_id": comparison_id,
                "state": "running",
                "status": "running",
                "stop_reason": None,
                "started_at": started_at,
                "updated_at": started_at,
                "comparison_spec_digest": _digest(normalized),
                "source_designs_modified": False,
                "variant_count": len(prepared),
                "experiments_attempted": 0,
                "feasible_variant_count": 0,
                "error_count": 0,
                "selected_variant_id": None,
                "variants": [
                    {
                        "variant_id": item["variant_id"],
                        "design_id": item["design"].design_id,
                        "design_revision": item["design"].revision,
                        "design_digest": item["design_digest"],
                        "netlist_digest": item["netlist_digest"],
                        "design_path": f"variants/{item['variant_id']}.json",
                    }
                    for item in prepared
                ],
                "evaluations": [],
                "runtime": {
                    "timeout_per_experiment": float(timeout_per_experiment),
                    "max_points": max_points,
                },
            }
            _atomic_json(root / COMPARISON_STATE_NAME, state)
            notify("comparison_preflight", 2, "Validated complete design variants")

            evaluations: list[dict[str, Any]] = []
            cancelled = False
            total = len(prepared)
            for index, item in enumerate(prepared):
                if cancel_requested is not None and cancel_requested():
                    cancelled = True
                    break
                variant_id = str(item["variant_id"])
                evaluation: dict[str, Any] = {
                    "variant_id": variant_id,
                    "index": index,
                    "design_id": item["design"].design_id,
                    "design_revision": item["design"].revision,
                    "design_path": f"variants/{variant_id}.json",
                    "status": "running",
                    "rank": None,
                    "objective": None,
                    "experiment": None,
                    "error": None,
                }
                evaluations.append(evaluation)
                state["evaluations"] = evaluations
                state["experiments_attempted"] = len(evaluations)
                state["updated_at"] = _utc_now()
                _atomic_json(root / COMPARISON_STATE_NAME, state)
                notify(
                    "comparison_experiment",
                    5 + int(index / total * 85),
                    f"Running {variant_id} ({index + 1}/{total})",
                )
                try:
                    result = self._experiments.run(
                        ExperimentRequest(
                            design=item["design"],
                            commands=normalized["commands"],
                            output_directory=str(root / "experiments" / variant_id),
                            title=f"{normalized['title']} - {variant_id}",
                            timeout_seconds=float(timeout_per_experiment),
                            max_points=max_points,
                            overwrite=False,
                            owner=comparison_id,
                            requirements=tuple(normalized["requirements"]),
                            theoretical_values=normalized["theoretical_values"],
                        ),
                        cancel_requested=cancel_requested,
                    )
                    if not result["success"]:
                        raise RuntimeError("experiment runner reported failure")
                    evidence, objective = validate_ranked_experiment_evidence(
                        root,
                        result,
                        normalized["objective"],
                        workflow_name="comparison",
                    )
                    evaluation["experiment"] = evidence
                    evaluation["objective"] = objective
                    evaluation["status"] = (
                        "feasible"
                        if evidence["overall_status"] == "pass"
                        and objective["status"] == "measured"
                        else evidence["overall_status"]
                        if evidence["overall_status"] != "pass"
                        else "unrankable"
                    )
                except Exception as exc:  # preserve later independent evidence
                    evaluation["status"] = "error"
                    evaluation["error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc)[:1000],
                    }
                state["updated_at"] = _utc_now()
                _atomic_json(root / COMPARISON_STATE_NAME, state)

            feasible = [item for item in evaluations if item["status"] == "feasible"]
            feasible.sort(
                key=lambda item: (
                    float(item["objective"]["score"]),
                    int(item["index"]),
                )
            )
            for rank, item in enumerate(feasible, start=1):
                item["rank"] = rank
            selected = feasible[0] if feasible else None
            error_count = sum(item["status"] == "error" for item in evaluations)
            if cancelled:
                status = "cancelled"
                stop_reason = "cancellation_requested"
                manifest_state = "cancelled"
            elif selected is None:
                status = "no_feasible_variant"
                stop_reason = "all_variants_evaluated"
                manifest_state = "succeeded"
            else:
                status = "ranked_with_errors" if error_count else "ranked"
                stop_reason = "all_variants_evaluated"
                manifest_state = "succeeded"
            state.update(
                {
                    "state": manifest_state,
                    "status": status,
                    "stop_reason": stop_reason,
                    "updated_at": _utc_now(),
                    "experiments_attempted": len(evaluations),
                    "feasible_variant_count": len(feasible),
                    "error_count": error_count,
                    "selected_variant_id": (
                        None if selected is None else selected["variant_id"]
                    ),
                    "ranked_feasible_variant_ids": [
                        item["variant_id"] for item in feasible
                    ],
                }
            )
            _atomic_json(root / COMPARISON_STATE_NAME, state)
            _write_csv(root, evaluations)
            manifest = write_directory_manifest(
                root,
                directory_kind="comparison",
                entity_id=comparison_id,
                state=manifest_state,
                artifacts=_artifact_allowlist(root),
                metadata={
                    "operation": "compare-design-variants",
                    "status": status,
                    "stop_reason": stop_reason,
                    "variant_count": len(prepared),
                    "experiments_attempted": len(evaluations),
                    "feasible_variant_count": len(feasible),
                    "error_count": error_count,
                    "source_designs_modified": False,
                },
            )
            notify("complete", 100, f"Design comparison finished: {status}")
            return {
                "schema_version": COMPARISON_SCHEMA_VERSION,
                "success": status in {"ranked", "ranked_with_errors"},
                "status": status,
                "stop_reason": stop_reason,
                "comparison_id": comparison_id,
                "output_dir": str(root),
                "summary": str(root / COMPARISON_STATE_NAME),
                "data": str(root / COMPARISON_DATA_NAME),
                "verification_plan": str(root / VERIFICATION_PLAN_NAME),
                "directory_manifest": str(root / DIRECTORY_MANIFEST_NAME),
                "directory_manifest_revision": manifest.revision,
                "source_designs_modified": False,
                "variant_count": len(prepared),
                "experiments_attempted": len(evaluations),
                "feasible_variant_count": len(feasible),
                "error_count": error_count,
                "selected_variant": (
                    None
                    if selected is None
                    else {
                        "variant_id": selected["variant_id"],
                        "design_id": selected["design_id"],
                        "design_revision": selected["design_revision"],
                        "objective": selected["objective"],
                        "design_path": str(root / selected["design_path"]),
                        "requires_manual_adoption": True,
                    }
                ),
            }


def read_design_comparison(
    output_directory: str, *, verify: bool = True
) -> dict[str, Any]:
    """Read a comparison summary and optionally verify every recursive artifact."""
    unresolved = Path(output_directory).expanduser()
    if unresolved.is_symlink():
        raise ValueError("comparison directory must not be a symbolic link")
    root = unresolved.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"comparison directory does not exist: {root}")
    path = root / COMPARISON_STATE_NAME
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"comparison summary is missing: {path}")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("comparison summary exceeds the size limit")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {constant}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError("comparison summary must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"comparison summary is not valid JSON: {exc}") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != COMPARISON_SCHEMA_VERSION
        or value.get("kind") != "multisim-mcp-design-comparison"
    ):
        raise ValueError("comparison summary contract is invalid")
    if verify:
        manifest = read_directory_manifest(root, verify=True)
        if (
            manifest.directory_kind != "comparison"
            or manifest.entity_id != value.get("comparison_id")
            or manifest.state != value.get("state")
        ):
            raise ValueError("comparison directory manifest does not match summary")
    return _plain_json(value)


__all__ = [
    "COMPARISON_DATA_NAME",
    "COMPARISON_SCHEMA_VERSION",
    "COMPARISON_SPEC_NAME",
    "COMPARISON_STATE_NAME",
    "DesignVariantComparisonService",
    "MAX_COMPARISON_SPEC_BYTES",
    "MAX_COMPARISON_VARIANTS",
    "VERIFICATION_PLAN_NAME",
    "read_comparison_spec",
    "read_design_comparison",
    "validate_comparison_spec",
    "validate_design_variants",
]
