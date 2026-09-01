"""Auditable mixed topology/value global optimization for CircuitDesign.

The service explores a finite declared design domain. Small domains are
exhaustive; larger or mixed continuous domains use a deterministic Halton
space-filling plan. Every admitted candidate is compiled and simulated, hard
requirements remain hard, and feasible designs are ranked by epsilon-aware
Pareto dominance. No source design is ever modified automatically.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import os
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from .design_patch_service import PreparedDesignPatch, prepare_design_patch
from .design_verification import validate_experiment_spec
from .eda_core import CircuitDesign, DesignPatch, PatchOperation
from .experiment_service import ExperimentApplicationService, ExperimentRequest
from .job_engine import output_lease
from .preferred_values import generate_preferred_values, spice_value_key
from .ranked_evaluation import (
    normalize_objectives,
    objective_vector,
    pareto_fronts,
    validate_ranked_experiment_evidence,
    weighted_compromise,
)
from .safety import validate_analysis_commands
from .spice_adapter import circuit_design_to_spice
from .workspace_manifest import (
    DIRECTORY_MANIFEST_NAME,
    read_directory_manifest,
    write_directory_manifest,
)


GLOBAL_OPTIMIZATION_SCHEMA_VERSION: Final = 1
MAX_GLOBAL_EXPERIMENTS: Final = 512
MAX_GLOBAL_DIMENSIONS: Final = 16
MAX_DIMENSION_OPTIONS: Final = 256
MAX_TOPOLOGY_CHOICES: Final = 64
MAX_GLOBAL_SPEC_BYTES: Final = 4 * 1024 * 1024
GLOBAL_STATE_NAME: Final = "global-optimization.json"
GLOBAL_SPEC_NAME: Final = "global-optimization-spec.json"
PARETO_NAME: Final = "pareto-front.json"
GLOBAL_CSV_NAME: Final = "global-candidates.csv"
BASELINE_NAME: Final = "baseline-design.json"
VERIFICATION_PLAN_NAME: Final = "verification-plan.json"

_DIMENSION_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_REFDES_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SPICE_TOKEN_RE = re.compile(r"^[^\s\x00]+$")
_GLOBAL_ID_RE = re.compile(r"^global-optimization-[0-9a-f]{32}$")
_PRIMES: Final = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53)

ProgressCallback = Callable[[str, int, str], None]
CancellationProbe = Callable[[], bool]


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _same_spice_value(left: str, right: str) -> bool:
    try:
        return spice_value_key(left) == spice_value_key(right)
    except ValueError:
        return left.casefold() == right.casefold()


def _normalize_value_options(raw: object, name: str) -> list[str]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_DIMENSION_OPTIONS:
        raise ValueError(
            f"{name} must contain between 1 and {MAX_DIMENSION_OPTIONS} values"
        )
    result: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(raw):
        if not isinstance(value, str):
            raise ValueError(f"{name}[{index}] must be a string")
        normalized = value.strip()
        if not normalized or not _SPICE_TOKEN_RE.fullmatch(normalized):
            raise ValueError(f"{name}[{index}] must be one safe SPICE token")
        try:
            key = f"numeric:{spice_value_key(normalized)}"
        except ValueError:
            key = f"literal:{normalized.casefold()}"
        if key in seen:
            raise ValueError(f"{name} contains an equivalent duplicate")
        seen.add(key)
        result.append(normalized)
    return result


def _continuous_values(raw: Mapping[str, Any], name: str) -> list[str]:
    allowed = {"minimum", "maximum", "samples", "scale"}
    if set(raw) - allowed or not {"minimum", "maximum", "samples"} <= set(raw):
        raise ValueError(
            f"{name}.range requires minimum, maximum, samples, and optional scale"
        )
    minimum = raw["minimum"]
    maximum = raw["maximum"]
    samples = raw["samples"]
    if (
        isinstance(minimum, bool)
        or not isinstance(minimum, (int, float))
        or not math.isfinite(float(minimum))
        or isinstance(maximum, bool)
        or not isinstance(maximum, (int, float))
        or not math.isfinite(float(maximum))
    ):
        raise ValueError(f"{name}.range bounds must be finite numbers")
    if float(minimum) >= float(maximum):
        raise ValueError(f"{name}.range minimum must be less than maximum")
    if isinstance(samples, bool) or not isinstance(samples, int) or not 2 <= samples <= 256:
        raise ValueError(f"{name}.range samples must be between 2 and 256")
    scale = str(raw.get("scale", "linear")).strip().lower()
    if scale not in {"linear", "log"}:
        raise ValueError(f"{name}.range scale must be linear or log")
    low = float(minimum)
    high = float(maximum)
    if scale == "log" and low <= 0:
        raise ValueError(f"{name}.range logarithmic bounds must be positive")
    values: list[str] = []
    for index in range(samples):
        ratio = index / (samples - 1)
        value = (
            math.exp(math.log(low) + ratio * (math.log(high) - math.log(low)))
            if scale == "log"
            else low + ratio * (high - low)
        )
        values.append(format(value, ".12g"))
    return _normalize_value_options(values, f"{name}.generated_values")


def _normalize_dimension(
    raw: object,
    index: int,
    components: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"dimensions[{index}] must be an object")
    dimension_id = str(raw.get("id", "")).strip()
    if not _DIMENSION_ID_RE.fullmatch(dimension_id):
        raise ValueError(f"dimensions[{index}].id is invalid")
    kind = str(raw.get("kind", "")).strip().lower()
    if kind == "component_value":
        allowed = {"id", "kind", "refdes", "values", "series", "range"}
        if set(raw) - allowed:
            raise ValueError(f"dimensions[{index}] contains unknown fields")
        modes = [name for name in ("values", "series", "range") if name in raw]
        if len(modes) != 1:
            raise ValueError(
                f"dimensions[{index}] requires exactly one of values, series, or range"
            )
        refdes = str(raw.get("refdes", "")).strip()
        if not _REFDES_RE.fullmatch(refdes):
            raise ValueError(f"dimensions[{index}].refdes is invalid")
        component = components.get(refdes.casefold())
        if component is None:
            raise ValueError(f"global optimization component does not exist: {refdes}")
        if component.value is None:
            raise ValueError(f"global optimization component has no value: {refdes}")
        mode = modes[0]
        if mode == "values":
            options = _normalize_value_options(raw["values"], f"dimensions[{index}].values")
            source = {"kind": "explicit"}
        elif mode == "range":
            range_value = raw["range"]
            if not isinstance(range_value, Mapping):
                raise ValueError(f"dimensions[{index}].range must be an object")
            options = _continuous_values(range_value, f"dimensions[{index}]")
            source = {"kind": "continuous_grid", **dict(range_value)}
        else:
            series = raw["series"]
            if not isinstance(series, Mapping) or set(series) != {"name", "minimum", "maximum"}:
                raise ValueError(
                    f"dimensions[{index}].series requires name, minimum, and maximum"
                )
            options = generate_preferred_values(
                str(series["name"]), str(series["minimum"]), str(series["maximum"])
            )
            if not 1 <= len(options) <= MAX_DIMENSION_OPTIONS:
                raise ValueError(f"dimensions[{index}].series generated too many values")
            source = {"kind": "preferred_series", **dict(series)}
        return {
            "id": dimension_id,
            "kind": kind,
            "refdes": component.refdes,
            "before": component.value,
            "options": options,
            "source": source,
        }
    if kind == "topology_choice":
        allowed = {"id", "kind", "choices", "include_baseline"}
        if set(raw) - allowed:
            raise ValueError(f"dimensions[{index}] contains unknown fields")
        include_baseline = raw.get("include_baseline", True)
        if not isinstance(include_baseline, bool):
            raise ValueError(f"dimensions[{index}].include_baseline must be a boolean")
        choices = raw.get("choices")
        if not isinstance(choices, list) or not 1 <= len(choices) <= MAX_TOPOLOGY_CHOICES:
            raise ValueError(
                f"dimensions[{index}].choices must contain between 1 and {MAX_TOPOLOGY_CHOICES} items"
            )
        normalized_choices: list[dict[str, Any]] = []
        seen_choices: set[str] = set()
        if include_baseline:
            normalized_choices.append({"choice_id": "baseline", "operations": []})
            seen_choices.add("baseline")
        for choice_index, choice in enumerate(choices):
            if not isinstance(choice, Mapping) or set(choice) != {"choice_id", "operations"}:
                raise ValueError(
                    f"dimensions[{index}].choices[{choice_index}] requires choice_id and operations"
                )
            choice_id = str(choice["choice_id"]).strip()
            if not _DIMENSION_ID_RE.fullmatch(choice_id) or choice_id in seen_choices:
                raise ValueError(f"dimensions[{index}] has an invalid or duplicate choice_id")
            operations = choice["operations"]
            if not isinstance(operations, list) or not operations:
                raise ValueError(
                    f"dimensions[{index}].choices[{choice_index}].operations must not be empty"
                )
            normalized_operations = [PatchOperation.from_dict(item).to_dict() for item in operations]
            normalized_choices.append(
                {"choice_id": choice_id, "operations": normalized_operations}
            )
            seen_choices.add(choice_id)
        return {
            "id": dimension_id,
            "kind": kind,
            "options": normalized_choices,
            "source": {"kind": "declared_topology_alternatives"},
        }
    raise ValueError(
        f"dimensions[{index}].kind must be component_value or topology_choice"
    )


def validate_global_optimization_spec(
    spec: Mapping[str, Any], design: CircuitDesign
) -> dict[str, Any]:
    """Validate and normalize GlobalOptimizationSpec v1 before any experiment."""
    if not isinstance(design, CircuitDesign):
        raise ValueError("design must be CircuitDesign")
    if not isinstance(spec, Mapping) or spec.get("schema_version") != 1:
        raise ValueError("GlobalOptimizationSpec schema_version must be 1")
    allowed = {
        "schema_version",
        "title",
        "dimensions",
        "commands",
        "requirements",
        "theoretical_values",
        "objectives",
        "max_experiments",
        "search_strategy",
        "sequence_seed",
        "selection_policy",
    }
    if set(spec) - allowed:
        raise ValueError("GlobalOptimizationSpec contains unknown fields")
    title = str(spec.get("title", "")).strip()
    if not title or "\x00" in title or len(title) > 4096:
        raise ValueError("GlobalOptimizationSpec title is empty, invalid, or too long")
    dimensions = spec.get("dimensions")
    if not isinstance(dimensions, list) or not 1 <= len(dimensions) <= MAX_GLOBAL_DIMENSIONS:
        raise ValueError(
            f"dimensions must contain between 1 and {MAX_GLOBAL_DIMENSIONS} items"
        )
    components = {item.refdes.casefold(): item for item in design.components}
    normalized_dimensions = [
        _normalize_dimension(item, index, components)
        for index, item in enumerate(dimensions)
    ]
    ids = [item["id"] for item in normalized_dimensions]
    if len(ids) != len(set(ids)):
        raise ValueError("dimension ids must be unique")
    value_targets = [
        item["refdes"].casefold()
        for item in normalized_dimensions
        if item["kind"] == "component_value"
    ]
    if len(value_targets) != len(set(value_targets)):
        raise ValueError("component value dimensions must target distinct components")
    max_experiments = spec.get("max_experiments")
    if (
        isinstance(max_experiments, bool)
        or not isinstance(max_experiments, int)
        or not 2 <= max_experiments <= MAX_GLOBAL_EXPERIMENTS
    ):
        raise ValueError(
            f"max_experiments must be between 2 and {MAX_GLOBAL_EXPERIMENTS}"
        )
    strategy = str(spec.get("search_strategy", "auto")).strip().lower()
    if strategy not in {"auto", "exhaustive", "halton"}:
        raise ValueError("search_strategy must be auto, exhaustive, or halton")
    sequence_seed = spec.get("sequence_seed", 0)
    if (
        isinstance(sequence_seed, bool)
        or not isinstance(sequence_seed, int)
        or not 0 <= sequence_seed <= 1_000_000
    ):
        raise ValueError("sequence_seed must be between 0 and 1000000")
    selection = str(spec.get("selection_policy", "weighted_compromise")).strip().lower()
    if selection not in {"none", "weighted_compromise"}:
        raise ValueError("selection_policy must be none or weighted_compromise")
    commands = "\n".join(validate_analysis_commands(str(spec.get("commands", ""))))
    normalized_experiment = validate_experiment_spec(
        {
            "schema_version": 1,
            "title": title,
            "netlist": circuit_design_to_spice(design),
            "commands": commands,
            "requirements": spec.get("requirements", []),
            "theoretical_values": spec.get("theoretical_values", {}),
        }
    )
    requirement_ids = {item["id"] for item in normalized_experiment["requirements"]}
    objectives = normalize_objectives(spec.get("objectives"), requirement_ids)
    return {
        "schema_version": GLOBAL_OPTIMIZATION_SCHEMA_VERSION,
        "title": title,
        "dimensions": normalized_dimensions,
        "commands": commands,
        "requirements": normalized_experiment["requirements"],
        "theoretical_values": normalized_experiment["theoretical_values"],
        "objectives": objectives,
        "max_experiments": max_experiments,
        "search_strategy": strategy,
        "sequence_seed": sequence_seed,
        "selection_policy": selection,
    }


def read_global_optimization_spec(
    path: str, design: CircuitDesign, *, normalize: bool = True
) -> tuple[Path, dict[str, Any]]:
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise ValueError("global optimization spec must not be a symbolic link")
    source = unresolved.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"global optimization spec does not exist: {source}")
    if source.stat().st_size > MAX_GLOBAL_SPEC_BYTES:
        raise ValueError("global optimization spec exceeds the size limit")
    try:
        raw = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError("global optimization spec must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"global optimization spec is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("global optimization spec must contain one JSON object")
    normalized = validate_global_optimization_spec(raw, design)
    return source, normalized if normalize else raw


def _radical_inverse(index: int, base: int) -> float:
    result = 0.0
    factor = 1.0 / base
    while index:
        result += factor * (index % base)
        index //= base
        factor /= base
    return result


def _baseline_option_index(dimension: Mapping[str, Any]) -> int:
    if dimension["kind"] == "topology_choice":
        for index, option in enumerate(dimension["options"]):
            if option["choice_id"] == "baseline":
                return index
        return 0
    before = str(dimension["before"])
    for index, option in enumerate(dimension["options"]):
        if _same_spice_value(before, str(option)):
            return index
    return 0


def _candidate_index_plan(
    dimensions: Sequence[Mapping[str, Any]],
    strategy: str,
    seed: int,
    limit: int,
) -> tuple[list[tuple[int, ...]], int, str]:
    sizes = [len(item["options"]) for item in dimensions]
    space_size = math.prod(sizes)
    baseline = tuple(_baseline_option_index(item) for item in dimensions)
    effective = "exhaustive" if strategy == "auto" and space_size <= limit + 1 else strategy
    if effective == "auto":
        effective = "halton"
    if effective == "exhaustive":
        plan = [item for item in itertools.product(*(range(size) for size in sizes)) if item != baseline]
        return plan[:limit], max(0, space_size - 1), effective

    plan: list[tuple[int, ...]] = []
    seen = {baseline}

    def add(candidate: tuple[int, ...]) -> None:
        if candidate not in seen and len(plan) < limit:
            seen.add(candidate)
            plan.append(candidate)

    # Axis anchors make endpoints visible before the low-discrepancy sequence.
    for dimension_index, size in enumerate(sizes):
        for option_index in dict.fromkeys((0, size - 1)):
            candidate = list(baseline)
            candidate[dimension_index] = option_index
            add(tuple(candidate))
    sequence_index = seed + 1
    maximum_attempts = max(10_000, limit * 100)
    attempts = 0
    while len(plan) < min(limit, max(0, space_size - 1)) and attempts < maximum_attempts:
        candidate = tuple(
            min(size - 1, int(_radical_inverse(sequence_index, _PRIMES[index]) * size))
            for index, size in enumerate(sizes)
        )
        add(candidate)
        sequence_index += 1
        attempts += 1
    return plan, max(0, space_size - 1), effective


def _build_candidate_patch(
    design: CircuitDesign,
    spec: Mapping[str, Any],
    option_indexes: Sequence[int],
    candidate_number: int,
) -> tuple[dict[str, Any], PreparedDesignPatch]:
    operations: list[PatchOperation] = []
    assignments: dict[str, Any] = {}
    for dimension, option_index in zip(spec["dimensions"], option_indexes):
        option = dimension["options"][option_index]
        if dimension["kind"] == "component_value":
            value = str(option)
            assignments[dimension["id"]] = value
            if _same_spice_value(str(dimension["before"]), value):
                continue
            operations.append(
                PatchOperation(
                    operation="set_component_value",
                    target=f"{dimension['refdes']}.value",
                    before=str(dimension["before"]),
                    after=value,
                    reason=f"global search selected dimension {dimension['id']}",
                )
            )
        else:
            assignments[dimension["id"]] = option["choice_id"]
            operations.extend(PatchOperation.from_dict(item) for item in option["operations"])
    if not operations:
        raise ValueError("candidate is identical to the baseline")
    candidate_id = f"candidate-{candidate_number:04d}"
    patch = DesignPatch(
        patch_id=f"global-{_digest(spec)[:16]}-{candidate_number:04d}",
        design_id=design.design_id,
        base_revision=design.revision,
        operations=tuple(operations),
        description=f"{spec['title']}: {candidate_id}",
        metadata={
            "source": "global_optimize_design",
            "candidate_id": candidate_id,
            "global_optimization_spec_digest": _digest(spec),
        },
    )
    prepared = prepare_design_patch(
        design,
        patch,
        regenerate_source_netlist=design.source_netlist is not None,
    )
    circuit_design_to_spice(prepared.candidate)
    return assignments, prepared


def _write_csv(root: Path, evaluations: Sequence[Mapping[str, Any]]) -> None:
    with (root / GLOBAL_CSV_NAME).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "evaluation_id",
                "kind",
                "status",
                "pareto_rank",
                "assignments_json",
                "objective_values_json",
                "error_type",
            ]
        )
        for item in evaluations:
            objectives = item.get("objectives") or []
            writer.writerow(
                [
                    item.get("evaluation_id"),
                    item.get("kind"),
                    item.get("status"),
                    item.get("pareto_rank"),
                    json.dumps(item.get("assignments", {}), sort_keys=True),
                    json.dumps(
                        {
                            objective.get("requirement_id"): objective.get("value")
                            for objective in objectives
                        },
                        sort_keys=True,
                    ),
                    (item.get("error") or {}).get("type"),
                ]
            )


def _artifact_allowlist(root: Path) -> dict[str, str]:
    roles = {
        GLOBAL_STATE_NAME: "global-optimization-state",
        GLOBAL_SPEC_NAME: "global-optimization-spec",
        PARETO_NAME: "pareto-front",
        GLOBAL_CSV_NAME: "optimization-data",
        BASELINE_NAME: "baseline-design",
        VERIFICATION_PLAN_NAME: "verification-plan",
    }
    artifacts: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"global optimization artifacts contain a symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == DIRECTORY_MANIFEST_NAME:
            continue
        role = roles.get(relative)
        if role is None:
            role = (
                "candidate-patch"
                if relative.startswith("patches/")
                else "experiment-artifact"
            )
        artifacts[relative] = role
    return artifacts


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError(f"{label} exceeds the size limit")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {constant}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError(f"{label} must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value


def _write_or_validate_json(path: Path, value: object, label: str) -> None:
    if path.exists():
        stored = _read_json_object(path, label)
        if _canonical_bytes(stored) != _canonical_bytes(value):
            raise ValueError(f"{label} does not match the requested resume input")
        return
    _atomic_json(path, value)


def _validate_completed_global_evaluation(
    root: Path,
    evaluation: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> None:
    status = str(evaluation.get("status", ""))
    if status == "error":
        if not isinstance(evaluation.get("error"), Mapping):
            raise ValueError("resumable global error lacks structured diagnostics")
        return
    evidence = evaluation.get("experiment")
    if not isinstance(evidence, Mapping):
        raise ValueError("resumable global evaluation lacks experiment evidence")
    relative_output = Path(str(evidence.get("output_directory", "")))
    relative_verification = Path(str(evidence.get("verification_path", "")))
    if relative_output.is_absolute() or relative_verification.is_absolute():
        raise ValueError("resumable global evaluation contains an absolute artifact path")
    output = (root / relative_output).resolve()
    verification_path = (root / relative_verification).resolve()
    try:
        output.relative_to(root)
        verification_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("resumable global artifact escapes the run root") from exc
    verification = _read_json_object(verification_path, "resumable verification")
    validated_evidence, _ = validate_ranked_experiment_evidence(
        root,
        {
            "experiment_id": evidence.get("experiment_id"),
            "output_dir": str(output),
            "verification": verification,
            "verification_path": str(verification_path),
        },
        spec["objectives"][0],
        workflow_name="global optimization resume",
    )
    if _canonical_bytes(validated_evidence) != _canonical_bytes(evidence):
        raise ValueError("resumable global evidence does not match its artifacts")
    if status == "constraint_fail":
        if evidence.get("overall_status") == "pass" or evaluation.get("objectives") is not None:
            raise ValueError("resumable constraint failure is inconsistent")
        return
    if status != "feasible" or evidence.get("overall_status") != "pass":
        raise ValueError("resumable global evaluation status is inconsistent")
    vector = objective_vector(verification, spec["objectives"])
    if any(item["status"] != "measured" for item in vector):
        raise ValueError("resumable global objective lacks measured evidence")
    if _canonical_bytes(vector) != _canonical_bytes(evaluation.get("objectives")):
        raise ValueError("resumable global objectives do not match verification")


def _resume_global_evaluations(
    root: Path,
    state: Mapping[str, Any],
    planned: Sequence[
        tuple[str, dict[str, Any], PreparedDesignPatch | None, dict[str, Any] | None]
    ],
    spec: Mapping[str, Any],
) -> list[dict[str, Any]]:
    raw = state.get("evaluations")
    if not isinstance(raw, list) or len(raw) > len(planned):
        raise ValueError("global optimization checkpoint has an invalid evaluation list")
    evaluations: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError("global optimization checkpoint evaluation must be an object")
        evaluation_id, assignments, prepared, preflight_error = planned[index]
        expected_kind = "baseline" if evaluation_id == "baseline" else "candidate"
        expected_patch = None if prepared is None else f"patches/{evaluation_id}.json"
        if (
            item.get("evaluation_id") != evaluation_id
            or item.get("index") != index
            or item.get("kind") != expected_kind
            or _canonical_bytes(item.get("assignments")) != _canonical_bytes(assignments)
            or item.get("patch_path") != expected_patch
        ):
            raise ValueError("global checkpoint does not match deterministic candidates")
        evaluation = json.loads(_canonical_bytes(item))
        attempt = evaluation.get("attempt", 1)
        if isinstance(attempt, bool) or not isinstance(attempt, int) or not 0 <= attempt <= 10_000:
            raise ValueError("global checkpoint contains an invalid attempt")
        interrupted = evaluation.get("interrupted_attempts", [])
        if not isinstance(interrupted, list) or len(interrupted) > max(1, attempt):
            raise ValueError("global checkpoint attempt history is invalid")
        status = str(evaluation.get("status", ""))
        if preflight_error is not None:
            if (
                status != "invalid_candidate"
                or attempt != 0
                or _canonical_bytes(evaluation.get("error")) != _canonical_bytes(preflight_error)
            ):
                raise ValueError("global invalid-candidate checkpoint is inconsistent")
        elif status in {"running", "interrupted"}:
            evaluation.update(
                status="interrupted",
                objectives=None,
                pareto_rank=None,
                experiment=None,
                error={
                    "type": "InterruptedRun",
                    "message": "Previous worker stopped before this evaluation committed",
                },
            )
        elif status in {"error", "constraint_fail", "feasible"}:
            _validate_completed_global_evaluation(root, evaluation, spec)
        else:
            raise ValueError("global checkpoint contains an invalid evaluation status")
        if prepared is not None:
            _write_or_validate_json(
                root / str(expected_patch),
                prepared.patch.to_dict(),
                f"{evaluation_id} patch",
            )
        evaluations.append(evaluation)
    return evaluations


class GlobalDesignOptimizationService:
    """Explore bounded topology/value domains and return measured Pareto designs."""

    def __init__(self, experiment_service: ExperimentApplicationService) -> None:
        if not isinstance(experiment_service, ExperimentApplicationService):
            raise ValueError("experiment_service must be ExperimentApplicationService")
        self._experiments = experiment_service

    def run(
        self,
        design: CircuitDesign,
        spec: Mapping[str, Any],
        output_directory: str,
        *,
        timeout_per_experiment: float = 120.0,
        max_points: int = 2000,
        checkpoint: ProgressCallback | None = None,
        cancel_requested: CancellationProbe | None = None,
        resume: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(design, CircuitDesign):
            raise ValueError("design must be CircuitDesign")
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
        if not isinstance(resume, bool):
            raise ValueError("resume must be a boolean")
        if not isinstance(output_directory, str) or not output_directory.strip():
            raise ValueError("output_directory must not be empty")
        normalized = validate_global_optimization_spec(spec, design)
        unresolved = Path(output_directory).expanduser()
        if unresolved.is_symlink():
            raise ValueError("global optimization directory must not be a symbolic link")
        root = unresolved.resolve()
        if root == Path(root.anchor):
            raise ValueError("global optimization directory must not be a filesystem root")
        option_plan, candidate_space_size, effective_strategy = _candidate_index_plan(
            normalized["dimensions"],
            str(normalized["search_strategy"]),
            int(normalized["sequence_seed"]),
            int(normalized["max_experiments"]) - 1,
        )
        global_id = f"global-optimization-{uuid.uuid4().hex}"
        verification_plan = {
            "schema_version": 1,
            "title": normalized["title"],
            "commands": normalized["commands"],
            "requirements": normalized["requirements"],
            "theoretical_values": normalized["theoretical_values"],
            "timeout_per_experiment": float(timeout_per_experiment),
            "max_points": int(max_points),
        }
        runtime = {
            "timeout_per_experiment": float(timeout_per_experiment),
            "max_points": int(max_points),
        }
        planned: list[
            tuple[
                str,
                dict[str, Any],
                PreparedDesignPatch | None,
                dict[str, Any] | None,
            ]
        ] = [("baseline", {}, None, None)]
        for number, indexes in enumerate(option_plan, 1):
            candidate_id = f"candidate-{number:04d}"
            try:
                assignments, prepared = _build_candidate_patch(
                    design, normalized, indexes, number
                )
                planned.append((candidate_id, assignments, prepared, None))
            except Exception as exc:
                planned.append(
                    (
                        candidate_id,
                        {
                            dimension["id"]: (
                                dimension["options"][option_index]
                                if dimension["kind"] == "component_value"
                                else dimension["options"][option_index]["choice_id"]
                            )
                            for dimension, option_index in zip(
                                normalized["dimensions"], indexes
                            )
                        },
                        None,
                        {"type": type(exc).__name__, "message": str(exc)[:1000]},
                    )
                )

        def notify(stage: str, progress: int, message: str) -> None:
            if checkpoint is not None:
                checkpoint(stage, progress, message)

        with output_lease(root, global_id):
            if root.exists() and not root.is_dir():
                raise ValueError("global optimization directory exists and is not a directory")
            has_checkpoint = root.exists() and any(root.iterdir())
            if has_checkpoint and not resume:
                raise FileExistsError(
                    f"refusing to overwrite non-empty global optimization directory: {root}"
                )
            root.mkdir(parents=True, exist_ok=True)
            patches_dir = root / "patches"
            if patches_dir.exists() and (
                patches_dir.is_symlink() or not patches_dir.is_dir()
            ):
                raise ValueError("global optimization patches path is not a safe directory")
            patches_dir.mkdir(exist_ok=True)
            if has_checkpoint:
                state = _read_json_object(
                    root / GLOBAL_STATE_NAME, "global optimization checkpoint"
                )
                if (
                    state.get("schema_version") != GLOBAL_OPTIMIZATION_SCHEMA_VERSION
                    or state.get("kind") != "multisim-mcp-global-optimization"
                ):
                    raise ValueError("global optimization checkpoint contract is invalid")
                if (
                    state.get("design_digest") != _digest(design.to_dict())
                    or state.get("spec_digest") != _digest(normalized)
                    or state.get("search_strategy") != effective_strategy
                    or state.get("candidate_space_size") != candidate_space_size
                    or state.get("max_experiments") != normalized["max_experiments"]
                    or _canonical_bytes(state.get("runtime"))
                    != _canonical_bytes(runtime)
                ):
                    raise ValueError(
                        "global checkpoint does not match design, spec, search, or runtime"
                    )
                stored_id = state.get("global_optimization_id")
                if not isinstance(stored_id, str) or not _GLOBAL_ID_RE.fullmatch(stored_id):
                    raise ValueError("global optimization checkpoint id is invalid")
                global_id = stored_id
                _write_or_validate_json(
                    root / BASELINE_NAME, design.to_dict(), "global baseline design"
                )
                _write_or_validate_json(
                    root / GLOBAL_SPEC_NAME, normalized, "global optimization spec"
                )
                _write_or_validate_json(
                    root / VERIFICATION_PLAN_NAME,
                    verification_plan,
                    "global verification plan",
                )
                evaluations = _resume_global_evaluations(
                    root, state, planned, normalized
                )
                state.update(
                    state="running",
                    status="running",
                    stop_reason=None,
                    updated_at=_utc_now(),
                    finished_at=None,
                    resume_count=int(state.get("resume_count", 0)) + 1,
                    evaluations=evaluations,
                )
                _atomic_json(root / GLOBAL_STATE_NAME, state)
                notify(
                    "global_recovered",
                    2,
                    f"Recovered {len(evaluations)} global evaluation checkpoints",
                )
            else:
                started_at = _utc_now()
                state = {
                    "schema_version": GLOBAL_OPTIMIZATION_SCHEMA_VERSION,
                    "kind": "multisim-mcp-global-optimization",
                    "global_optimization_id": global_id,
                    "state": "running",
                    "status": "running",
                    "stop_reason": None,
                    "started_at": started_at,
                    "updated_at": started_at,
                    "finished_at": None,
                    "resume_count": 0,
                    "design_id": design.design_id,
                    "design_revision": design.revision,
                    "design_digest": _digest(design.to_dict()),
                    "spec_digest": _digest(normalized),
                    "search_strategy": effective_strategy,
                    "candidate_space_size": candidate_space_size,
                    "max_experiments": normalized["max_experiments"],
                    "experiments_attempted": 0,
                    "experiment_attempt_count": 0,
                    "evaluations": [],
                    "runtime": runtime,
                    "source_design_modified": False,
                    "candidate_persisted_as_source": False,
                }
                evaluations = []
                _atomic_json(root / GLOBAL_STATE_NAME, state)
                _write_or_validate_json(
                    root / BASELINE_NAME, design.to_dict(), "global baseline design"
                )
                _write_or_validate_json(
                    root / GLOBAL_SPEC_NAME, normalized, "global optimization spec"
                )
                _write_or_validate_json(
                    root / VERIFICATION_PLAN_NAME,
                    verification_plan,
                    "global verification plan",
                )
                notify("global_preflight", 2, "Validated global search domain")
            cancelled = False
            for index, (
                evaluation_id,
                assignments,
                prepared,
                preflight_error,
            ) in enumerate(planned):
                if cancel_requested is not None and cancel_requested():
                    cancelled = True
                    break
                if index < len(evaluations) and evaluations[index]["status"] != "interrupted":
                    notify(
                        "global_resume_skip",
                        5 + int((index + 1) / max(1, len(planned)) * 85),
                        f"Reused completed {evaluation_id}",
                    )
                    continue
                if preflight_error is not None:
                    evaluation = {
                        "evaluation_id": evaluation_id,
                        "index": index,
                        "kind": "candidate",
                        "assignments": assignments,
                        "patch_path": None,
                        "status": "invalid_candidate",
                        "attempt": 0,
                        "interrupted_attempts": [],
                        "objectives": None,
                        "pareto_rank": None,
                        "experiment": None,
                        "error": preflight_error,
                    }
                    evaluations.append(evaluation)
                    state["evaluations"] = evaluations
                    _atomic_json(root / GLOBAL_STATE_NAME, state)
                    continue
                if prepared is not None:
                    patch_path = root / "patches" / f"{evaluation_id}.json"
                    _write_or_validate_json(
                        patch_path, prepared.patch.to_dict(), f"{evaluation_id} patch"
                    )
                    candidate_design = prepared.candidate
                else:
                    candidate_design = design
                if index < len(evaluations):
                    evaluation = evaluations[index]
                    attempt = int(evaluation.get("attempt", 1)) + 1
                    interrupted_attempts = list(
                        evaluation.get("interrupted_attempts", [])
                    )
                    interrupted_attempts.append(
                        {
                            "attempt": attempt - 1,
                            "experiment_output": evaluation.get("experiment_output"),
                            "error": evaluation.get("error"),
                        }
                    )
                    evaluation.update(
                        status="running",
                        attempt=attempt,
                        interrupted_attempts=interrupted_attempts,
                        objectives=None,
                        pareto_rank=None,
                        experiment=None,
                        error=None,
                    )
                else:
                    attempt = 1
                    evaluation = {
                        "evaluation_id": evaluation_id,
                        "index": index,
                        "kind": "baseline" if evaluation_id == "baseline" else "candidate",
                        "assignments": assignments,
                        "patch_path": (
                            None if prepared is None else f"patches/{evaluation_id}.json"
                        ),
                        "status": "running",
                        "attempt": attempt,
                        "interrupted_attempts": [],
                        "objectives": None,
                        "pareto_rank": None,
                        "experiment": None,
                        "error": None,
                    }
                    evaluations.append(evaluation)
                experiment_name = (
                    evaluation_id
                    if attempt == 1
                    else f"{evaluation_id}-attempt-{attempt:03d}"
                )
                experiment_dir = root / "experiments" / experiment_name
                evaluation["experiment_output"] = (
                    Path("experiments") / experiment_name
                ).as_posix()
                state["evaluations"] = evaluations
                state["experiments_attempted"] = sum(
                    item.get("attempt", 0) > 0 for item in evaluations
                )
                state["experiment_attempt_count"] = sum(
                    int(item.get("attempt", 0)) for item in evaluations
                )
                state["updated_at"] = _utc_now()
                _atomic_json(root / GLOBAL_STATE_NAME, state)
                try:
                    result = self._experiments.run(
                        ExperimentRequest(
                            design=candidate_design,
                            commands=normalized["commands"],
                            output_directory=str(experiment_dir),
                            title=f"{normalized['title']} - {evaluation_id}",
                            timeout_seconds=float(timeout_per_experiment),
                            max_points=int(max_points),
                            owner=global_id,
                            requirements=tuple(normalized["requirements"]),
                            theoretical_values=normalized["theoretical_values"],
                        ),
                        cancel_requested=cancel_requested,
                    )
                    evidence, _ = validate_ranked_experiment_evidence(
                        root,
                        result,
                        normalized["objectives"][0],
                        workflow_name="global optimization",
                    )
                    evaluation["experiment"] = evidence
                    if evidence["overall_status"] == "pass":
                        vector = objective_vector(result["verification"], normalized["objectives"])
                        if any(item["status"] != "measured" for item in vector):
                            raise RuntimeError("global objective lacks measured evidence")
                        evaluation["objectives"] = vector
                        evaluation["status"] = "feasible"
                    else:
                        evaluation["status"] = "constraint_fail"
                except InterruptedError as exc:
                    evaluation["status"] = "interrupted"
                    evaluation["error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
                    cancelled = True
                except Exception as exc:
                    evaluation["status"] = "error"
                    evaluation["error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
                state["updated_at"] = _utc_now()
                _atomic_json(root / GLOBAL_STATE_NAME, state)
                progress = min(94, 5 + int(85 * (index + 1) / max(1, len(planned))))
                notify("global_candidate", progress, f"Evaluated {evaluation_id}")
                if cancelled:
                    break

            for item in evaluations:
                item["pareto_rank"] = None
            feasible = [item for item in evaluations if item["status"] == "feasible"]
            fronts = pareto_fronts(feasible) if feasible else []
            for rank, front in enumerate(fronts):
                for feasible_index in front:
                    feasible[feasible_index]["pareto_rank"] = rank
            pareto = [feasible[index] for index in fronts[0]] if fronts else []
            recommended: Mapping[str, Any] | None = None
            if pareto and normalized["selection_policy"] == "weighted_compromise":
                selected_index = weighted_compromise(pareto)
                assert selected_index is not None
                recommended = pareto[selected_index]
            state["status"] = (
                "cancelled"
                if cancelled
                else "completed"
                if feasible
                else "no_feasible_candidate"
            )
            state["stop_reason"] = (
                "cancellation_requested"
                if cancelled
                else "budget_exhausted"
                if candidate_space_size > len(option_plan)
                else "candidate_space_exhausted"
            )
            manifest_state = "cancelled" if cancelled else "succeeded"
            state.update(
                state=manifest_state,
                updated_at=_utc_now(),
                finished_at=_utc_now(),
                experiments_attempted=sum(
                    item.get("attempt", 0) > 0 for item in evaluations
                ),
                experiment_attempt_count=sum(
                    int(item.get("attempt", 0)) for item in evaluations
                ),
                feasible_solution_count=len(feasible),
                pareto_evaluation_ids=[item["evaluation_id"] for item in pareto],
                recommended_evaluation_id=(
                    None if recommended is None else recommended["evaluation_id"]
                ),
            )
            _atomic_json(root / GLOBAL_STATE_NAME, state)
            _atomic_json(
                root / PARETO_NAME,
                {
                    "schema_version": GLOBAL_OPTIMIZATION_SCHEMA_VERSION,
                    "global_optimization_id": global_id,
                    "objectives": normalized["objectives"],
                    "pareto_evaluation_ids": state["pareto_evaluation_ids"],
                    "recommended_evaluation_id": state["recommended_evaluation_id"],
                    "selection_policy": normalized["selection_policy"],
                },
            )
            _write_csv(root, evaluations)
            manifest = write_directory_manifest(
                root,
                directory_kind="global-optimization",
                entity_id=global_id,
                state=manifest_state,
                artifacts=_artifact_allowlist(root),
                metadata={
                    "operation": "global-optimize-design",
                    "status": state["status"],
                    "stop_reason": state["stop_reason"],
                    "design_id": design.design_id,
                },
            )
            notify("global_complete", 100, state["status"])
            recommendation = None
            if recommended is not None:
                recommendation = {
                    "evaluation_id": recommended["evaluation_id"],
                    "kind": recommended["kind"],
                    "assignments": recommended["assignments"],
                    "objectives": recommended["objectives"],
                    "patch_path": (
                        None
                        if recommended["patch_path"] is None
                        else str(root / recommended["patch_path"])
                    ),
                    "requires_approval_to_persist": recommended["kind"] == "candidate",
                }
            return {
                "schema_version": GLOBAL_OPTIMIZATION_SCHEMA_VERSION,
                "success": bool(feasible) and not cancelled,
                "status": state["status"],
                "global_optimization_id": global_id,
                "output_dir": str(root),
                "search_strategy": effective_strategy,
                "stop_reason": state["stop_reason"],
                "candidate_space_size": candidate_space_size,
                "experiments_attempted": state["experiments_attempted"],
                "experiment_attempt_count": state["experiment_attempt_count"],
                "resume_count": state["resume_count"],
                "feasible_solution_count": len(feasible),
                "pareto_solution_count": len(pareto),
                "pareto_evaluation_ids": state["pareto_evaluation_ids"],
                "recommended_solution": recommendation,
                "manifest": manifest.to_dict(),
                "source_design_modified": False,
                "candidate_persisted_as_source": False,
            }


def read_global_optimization(output_directory: str, *, verify: bool = True) -> dict[str, Any]:
    root = Path(output_directory).expanduser().resolve()
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"global optimization directory does not exist: {root}")
    if verify:
        manifest = read_directory_manifest(root, verify=True)
        if manifest.directory_kind != "global-optimization":
            raise ValueError("directory is not a global optimization result")
    path = root / GLOBAL_STATE_NAME
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("global optimization result is unreadable") from exc
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise ValueError("global optimization result schema is invalid")
    return result


__all__ = [
    "BASELINE_NAME",
    "GLOBAL_CSV_NAME",
    "GLOBAL_OPTIMIZATION_SCHEMA_VERSION",
    "GLOBAL_SPEC_NAME",
    "GLOBAL_STATE_NAME",
    "GlobalDesignOptimizationService",
    "MAX_GLOBAL_EXPERIMENTS",
    "PARETO_NAME",
    "VERIFICATION_PLAN_NAME",
    "read_global_optimization",
    "read_global_optimization_spec",
    "validate_global_optimization_spec",
]
