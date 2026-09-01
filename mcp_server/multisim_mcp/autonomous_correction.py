"""Bounded autonomous diagnose-propose-simulate-select correction loop.

The loop accepts a pluggable repair planner. A planner may be deterministic,
model-backed, or supplied by an MCP host, but every proposal crosses the same
strict DesignPatch boundary and real experiment gate. The source design is
never persisted automatically; a passing final candidate is consolidated into
one reversible patch against the original revision for explicit approval.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Protocol

from .agent_runtime import BoundedToolLoop
from .design_diagnosis import DesignDiagnosisService
from .design_patch_service import create_design_diff_patch, prepare_design_patch
from .design_patch_tools import ReadOnlyDesignPatchPreview
from .design_verification import validate_experiment_spec
from .eda_agent_tools import create_readonly_eda_bindings
from .eda_core import CircuitDesign, DesignPatch
from .experiment_service import ExperimentApplicationService, ExperimentRequest
from .job_engine import output_lease
from .model_provider import ModelMessage, ModelProviderRegistry
from .ranked_evaluation import (
    normalize_objectives,
    objective_vector,
    pareto_fronts,
    validate_ranked_experiment_evidence,
    weighted_compromise,
)
from .safety import validate_analysis_commands
from .spice_adapter import circuit_design_to_spice
from .workspace_manifest import DIRECTORY_MANIFEST_NAME, write_directory_manifest


AUTONOMOUS_CORRECTION_SCHEMA_VERSION: Final = 1
MAX_CORRECTION_ROUNDS: Final = 8
MAX_CANDIDATES_PER_ROUND: Final = 8
MAX_CORRECTION_EXPERIMENTS: Final = 65
MAX_CORRECTION_SPEC_BYTES: Final = 4 * 1024 * 1024
CORRECTION_STATE_NAME: Final = "autonomous-correction.json"
CORRECTION_SPEC_NAME: Final = "autonomous-correction-spec.json"
ORIGINAL_DESIGN_NAME: Final = "original-design.json"
FINAL_DESIGN_NAME: Final = "final-candidate-design.json"
FINAL_PATCH_NAME: Final = "final-candidate-patch.json"
_CORRECTION_ID_RE = re.compile(r"^autonomous-correction-[0-9a-f]{32}$")

ProgressCallback = Callable[[str, int, str], None]
CancellationProbe = Callable[[], bool]


class RepairPlanner(Protocol):
    def __call__(
        self,
        design: CircuitDesign,
        diagnosis: Mapping[str, Any],
        spec: Mapping[str, Any],
        history: Sequence[Mapping[str, Any]],
        round_number: int,
    ) -> Sequence[DesignPatch | Mapping[str, Any]]: ...


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


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


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


def _contained_json(root: Path, relative: object, label: str) -> dict[str, Any]:
    path_value = Path(str(relative))
    if path_value.is_absolute():
        raise ValueError(f"{label} path must be relative")
    path = (root / path_value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} path escapes the correction root") from exc
    return _read_json_object(path, label)


def _primary_objective(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    objectives = spec.get("objectives")
    if isinstance(objectives, Sequence) and objectives:
        item = objectives[0]
        if isinstance(item, Mapping):
            return item
    return {
        "requirement_id": spec["requirements"][0]["id"],
        "goal": "minimize",
    }


def _load_verified_result(
    root: Path,
    evidence: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    workflow_name: str,
) -> dict[str, Any]:
    relative_output = Path(str(evidence.get("output_directory", "")))
    relative_verification = Path(str(evidence.get("verification_path", "")))
    if relative_output.is_absolute() or relative_verification.is_absolute():
        raise ValueError("correction checkpoint contains an absolute artifact path")
    output = (root / relative_output).resolve()
    verification_path = (root / relative_verification).resolve()
    try:
        output.relative_to(root)
        verification_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("correction checkpoint artifact escapes the run root") from exc
    verification = _read_json_object(verification_path, "correction verification")
    result = {
        "success": True,
        "experiment_id": evidence.get("experiment_id"),
        "output_dir": str(output),
        "verification": verification,
        "verification_path": str(verification_path),
    }
    validated, _ = validate_ranked_experiment_evidence(
        root,
        result,
        _primary_objective(spec),
        workflow_name=workflow_name,
    )
    if _canonical_bytes(validated) != _canonical_bytes(evidence):
        raise ValueError("correction checkpoint evidence does not match artifacts")
    return result


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


def validate_autonomous_correction_spec(
    spec: Mapping[str, Any], design: CircuitDesign
) -> dict[str, Any]:
    if not isinstance(design, CircuitDesign):
        raise ValueError("design must be CircuitDesign")
    if not isinstance(spec, Mapping) or spec.get("schema_version") != 1:
        raise ValueError("AutonomousCorrectionSpec schema_version must be 1")
    allowed = {
        "schema_version",
        "title",
        "commands",
        "requirements",
        "theoretical_values",
        "objectives",
        "max_rounds",
        "max_candidates_per_round",
        "require_strict_improvement",
        "stop_on_first_pass",
    }
    if set(spec) - allowed:
        raise ValueError("AutonomousCorrectionSpec contains unknown fields")
    title = str(spec.get("title", "")).strip()
    if not title or "\x00" in title or len(title) > 4096:
        raise ValueError("AutonomousCorrectionSpec title is empty, invalid, or too long")
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
    max_rounds = spec.get("max_rounds", 4)
    if (
        isinstance(max_rounds, bool)
        or not isinstance(max_rounds, int)
        or not 1 <= max_rounds <= MAX_CORRECTION_ROUNDS
    ):
        raise ValueError(f"max_rounds must be between 1 and {MAX_CORRECTION_ROUNDS}")
    max_candidates = spec.get("max_candidates_per_round", 4)
    if (
        isinstance(max_candidates, bool)
        or not isinstance(max_candidates, int)
        or not 1 <= max_candidates <= MAX_CANDIDATES_PER_ROUND
    ):
        raise ValueError(
            f"max_candidates_per_round must be between 1 and {MAX_CANDIDATES_PER_ROUND}"
        )
    strict = spec.get("require_strict_improvement", True)
    stop_first = spec.get("stop_on_first_pass", False)
    if not isinstance(strict, bool) or not isinstance(stop_first, bool):
        raise ValueError("correction policy flags must be booleans")
    objectives: list[dict[str, Any]] = []
    if spec.get("objectives") is not None:
        ids = {item["id"] for item in normalized_experiment["requirements"]}
        objectives = normalize_objectives(spec["objectives"], ids)
    return {
        "schema_version": AUTONOMOUS_CORRECTION_SCHEMA_VERSION,
        "title": title,
        "commands": commands,
        "requirements": normalized_experiment["requirements"],
        "theoretical_values": normalized_experiment["theoretical_values"],
        "objectives": objectives,
        "max_rounds": max_rounds,
        "max_candidates_per_round": max_candidates,
        "max_total_experiments": min(
            MAX_CORRECTION_EXPERIMENTS, 1 + max_rounds * max_candidates
        ),
        "require_strict_improvement": strict,
        "stop_on_first_pass": stop_first,
    }


def read_autonomous_correction_spec(
    path: str, design: CircuitDesign, *, normalize: bool = True
) -> tuple[Path, dict[str, Any]]:
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise ValueError("autonomous correction spec must not be a symbolic link")
    source = unresolved.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"autonomous correction spec does not exist: {source}")
    if source.stat().st_size > MAX_CORRECTION_SPEC_BYTES:
        raise ValueError("autonomous correction spec exceeds the size limit")

    def strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key}")
            result[key] = value
        return result

    try:
        raw = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=strict_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError("autonomous correction spec must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"autonomous correction spec is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("autonomous correction spec must contain one JSON object")
    normalized = validate_autonomous_correction_spec(raw, design)
    return source, normalized if normalize else raw


def _diagnosis_evidence(result: Mapping[str, Any]) -> dict[str, Any]:
    verification = result.get("verification")
    if not isinstance(verification, Mapping):
        raise RuntimeError("correction experiment returned no verification")
    return {
        "schema_version": 1,
        "experiment_id": result.get("experiment_id"),
        "manifest_sha256": None,
        "design_binding": None,
        "analysis": {},
        "verification": verification,
        "operating_point": {},
        "simulation_log": "",
    }


def _verification_merit(verification: Mapping[str, Any]) -> tuple[int, int, int]:
    counts = verification.get("counts")
    if not isinstance(counts, Mapping):
        raise RuntimeError("verification counts are missing")
    values: list[int] = []
    for name in ("fail", "unverified", "pass"):
        value = counts.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError("verification counts are invalid")
        values.append(value)
    return values[0], values[1], -values[2]


def _artifact_allowlist(root: Path) -> dict[str, str]:
    root_roles = {
        CORRECTION_STATE_NAME: "autonomous-correction-state",
        CORRECTION_SPEC_NAME: "autonomous-correction-spec",
        ORIGINAL_DESIGN_NAME: "original-design",
        FINAL_DESIGN_NAME: "final-candidate-design",
        FINAL_PATCH_NAME: "final-candidate-patch",
    }
    artifacts: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"correction artifacts contain a symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == DIRECTORY_MANIFEST_NAME:
            continue
        role = root_roles.get(relative)
        if role is None:
            if relative.endswith("/patch.json"):
                role = "candidate-patch"
            elif relative.endswith("/design.json"):
                role = "candidate-design"
            elif relative.endswith("/diagnosis.json"):
                role = "design-diagnosis"
            else:
                role = "experiment-artifact"
        artifacts[relative] = role
    return artifacts


class ModelRepairPlanner:
    """Ask a configured model for bounded topology/value DesignPatch previews."""

    def __init__(
        self,
        registry: ModelProviderRegistry,
        *,
        provider_id: str | None = None,
        fallback_provider_ids: Sequence[str] = (),
        allow_failover: bool = False,
        timeout: float = 60.0,
        max_tokens: int | None = None,
        temperature: float = 0.1,
    ) -> None:
        if not isinstance(registry, ModelProviderRegistry):
            raise ValueError("registry must be ModelProviderRegistry")
        self.registry = registry
        self.provider_id = provider_id
        self.fallback_provider_ids = tuple(fallback_provider_ids)
        self.allow_failover = allow_failover
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._last_metadata: dict[str, Any] | None = None

    def last_run_metadata(self) -> dict[str, Any] | None:
        """Return a secret-free summary of the most recent planning turn."""
        return None if self._last_metadata is None else dict(self._last_metadata)

    def resume_identity(self) -> dict[str, Any]:
        """Return the secret-free planner contract bound to durable checkpoints."""
        return {
            "kind": "model-repair-planner",
            "provider_id": self.provider_id,
            "fallback_provider_ids": list(self.fallback_provider_ids),
            "allow_failover": self.allow_failover,
            "timeout": self.timeout,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "registered_provider_ids": list(self.registry.provider_ids()),
        }

    def __call__(
        self,
        design: CircuitDesign,
        diagnosis: Mapping[str, Any],
        spec: Mapping[str, Any],
        history: Sequence[Mapping[str, Any]],
        round_number: int,
    ) -> Sequence[DesignPatch]:
        preview = ReadOnlyDesignPatchPreview(design)
        system = (
            "You are an EDA repair planner. Inspect the fixed CircuitDesign with the "
            "read-only tools, then call eda_preview_design_patch for one or more minimal "
            "independent repair candidates. You may change values, models, pins, nets, or "
            "add/remove/replace components. Every before value must exactly match the "
            "current design. Do not claim a repair works until the host simulates it. "
            "Never request persistence or hide uncertainty."
        )
        request = {
            "round": round_number,
            "diagnosis": diagnosis,
            "requirements": spec["requirements"],
            "objectives": spec.get("objectives", []),
            "prior_rounds": list(history)[-3:],
            "requested_candidates": spec["max_candidates_per_round"],
        }
        loop = BoundedToolLoop(
            self.registry,
            create_readonly_eda_bindings(design) + preview.bindings(),
            max_rounds=8,
            max_tool_calls=min(16, int(spec["max_candidates_per_round"]) + 8),
        )
        run = loop.run(
            [
                ModelMessage("system", system),
                ModelMessage("user", json.dumps(request, ensure_ascii=False)),
            ],
            provider_id=self.provider_id,
            fallback_provider_ids=self.fallback_provider_ids,
            allow_failover=self.allow_failover,
            timeout=self.timeout,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        self._last_metadata = {
            "rounds": run.rounds,
            "tool_call_count": run.tool_call_count,
            "provider_ids": list(run.provider_ids),
            "usage": run.usage.to_dict() if run.usage is not None else None,
            "usage_complete": run.usage_complete,
            "captured_candidate_count": len(preview.captured_previews()),
            "transcript_persisted": False,
            "credential_values_persisted": False,
        }
        return tuple(
            DesignPatch.from_dict(item["patch"])
            for item in preview.captured_previews()[: int(spec["max_candidates_per_round"])]
        )


class AutonomousDesignCorrectionService:
    """Run a bounded autonomous correction loop without persisting a candidate."""

    def __init__(
        self,
        experiment_service: ExperimentApplicationService,
        planner: RepairPlanner,
    ) -> None:
        if not isinstance(experiment_service, ExperimentApplicationService):
            raise ValueError("experiment_service must be ExperimentApplicationService")
        if not callable(planner):
            raise ValueError("planner must be callable")
        self._experiments = experiment_service
        self._planner = planner
        self._diagnosis = DesignDiagnosisService()

    def _planner_resume_identity(self) -> dict[str, Any]:
        if hasattr(self._planner, "resume_identity"):
            value = self._planner.resume_identity()
            if not isinstance(value, Mapping):
                raise ValueError("repair planner resume identity must be an object")
            return json.loads(_canonical_bytes(value))
        planner_type = type(self._planner)
        return {
            "kind": "callable-repair-planner",
            "type": f"{planner_type.__module__}.{planner_type.__qualname__}",
        }

    def _run_experiment(
        self,
        design: CircuitDesign,
        spec: Mapping[str, Any],
        output: Path,
        owner: str,
        timeout: float,
        max_points: int,
        cancel_requested: CancellationProbe | None,
    ) -> dict[str, Any]:
        return self._experiments.run(
            ExperimentRequest(
                design=design,
                commands=spec["commands"],
                output_directory=str(output),
                title=spec["title"],
                timeout_seconds=timeout,
                max_points=max_points,
                owner=owner,
                requirements=tuple(spec["requirements"]),
                theoretical_values=spec["theoretical_values"],
            ),
            cancel_requested=cancel_requested,
        )

    def _restore_committed_progress(
        self,
        root: Path,
        state: dict[str, Any],
        original: CircuitDesign,
        spec: Mapping[str, Any],
    ) -> tuple[
        CircuitDesign,
        dict[str, Any] | None,
        dict[str, Any] | None,
        tuple[int, int, int] | None,
        list[Mapping[str, Any]],
    ]:
        baseline = state.get("baseline")
        if not isinstance(baseline, Mapping):
            raise ValueError("correction checkpoint baseline is invalid")
        baseline_status = str(baseline.get("status", ""))
        if baseline_status != "completed":
            if baseline_status not in {"running", "interrupted"}:
                raise ValueError("correction checkpoint baseline status is invalid")
            interrupted = list(state.get("interrupted_rounds", []))
            raw_rounds = state.get("rounds", [])
            if not isinstance(raw_rounds, list):
                raise ValueError("correction checkpoint rounds are invalid")
            interrupted.extend(raw_rounds)
            state["interrupted_rounds"] = interrupted
            state["rounds"] = []
            return original, None, None, None, []

        evidence = baseline.get("experiment")
        if not isinstance(evidence, Mapping):
            raise ValueError("completed correction baseline lacks experiment evidence")
        current_result = _load_verified_result(
            root,
            evidence,
            spec,
            workflow_name="autonomous correction baseline resume",
        )
        diagnosis_payload = _contained_json(
            root, baseline.get("diagnosis_path"), "baseline diagnosis"
        )
        expected_diagnosis = self._diagnosis.run(
            original,
            experiment_evidence=_diagnosis_evidence(current_result),
        )
        if _canonical_bytes(diagnosis_payload) != _canonical_bytes(expected_diagnosis):
            raise ValueError("correction baseline diagnosis does not match evidence")
        current_merit = _verification_merit(current_result["verification"])
        if list(current_merit) != baseline.get("merit"):
            raise ValueError("correction baseline merit is inconsistent")
        current_design = original
        current_diagnosis = diagnosis_payload
        history: list[Mapping[str, Any]] = []
        raw_rounds = state.get("rounds")
        if not isinstance(raw_rounds, list):
            raise ValueError("correction checkpoint rounds are invalid")
        completed: list[dict[str, Any]] = []
        for expected_round, raw_round in enumerate(raw_rounds, 1):
            if not isinstance(raw_round, Mapping):
                raise ValueError("correction checkpoint round must be an object")
            if raw_round.get("status") != "selected":
                break
            if (
                raw_round.get("round") != expected_round
                or raw_round.get("base_revision") != current_design.revision
                or raw_round.get("base_merit") != list(current_merit)
            ):
                raise ValueError("correction checkpoint round base is inconsistent")
            selected_id = raw_round.get("selected_candidate_id")
            candidates = raw_round.get("candidates")
            if not isinstance(selected_id, str) or not isinstance(candidates, list):
                raise ValueError("selected correction round is incomplete")
            selected_record = next(
                (
                    item
                    for item in candidates
                    if isinstance(item, Mapping)
                    and item.get("candidate_id") == selected_id
                ),
                None,
            )
            if selected_record is None:
                raise ValueError("selected correction candidate is missing")
            patch_payload = _contained_json(
                root, selected_record.get("patch_path"), "selected correction patch"
            )
            candidate_payload = _contained_json(
                root, selected_record.get("design_path"), "selected correction design"
            )
            prepared = prepare_design_patch(
                current_design,
                DesignPatch.from_dict(patch_payload),
                regenerate_source_netlist=current_design.source_netlist is not None,
            )
            if _canonical_bytes(prepared.candidate.to_dict()) != _canonical_bytes(
                candidate_payload
            ):
                raise ValueError("selected correction design does not match its patch")
            candidate_result = _load_verified_result(
                root,
                selected_record.get("experiment", {}),
                spec,
                workflow_name="autonomous correction candidate resume",
            )
            candidate_merit = _verification_merit(candidate_result["verification"])
            expected_status = (
                "passed"
                if candidate_result["verification"]["overall_status"] == "pass"
                else "failed_requirements"
            )
            if (
                selected_record.get("status") != expected_status
                or selected_record.get("merit") != list(candidate_merit)
            ):
                raise ValueError("selected correction candidate status is inconsistent")
            expected_objectives = (
                objective_vector(candidate_result["verification"], spec["objectives"])
                if spec["objectives"]
                else None
            )
            if _canonical_bytes(selected_record.get("objectives")) != _canonical_bytes(
                expected_objectives
            ):
                raise ValueError("selected correction objectives are inconsistent")
            diagnosis = _contained_json(
                root,
                selected_record.get("diagnosis_path"),
                "selected correction diagnosis",
            )
            expected_candidate_diagnosis = self._diagnosis.run(
                prepared.candidate,
                experiment_evidence=_diagnosis_evidence(candidate_result),
            )
            if _canonical_bytes(diagnosis) != _canonical_bytes(
                expected_candidate_diagnosis
            ):
                raise ValueError("selected correction diagnosis is inconsistent")
            if spec["require_strict_improvement"] and candidate_merit >= current_merit:
                raise ValueError("selected correction candidate is not a strict improvement")
            current_design = prepared.candidate
            current_result = candidate_result
            current_diagnosis = diagnosis
            current_merit = candidate_merit
            history.append(
                {
                    "round": expected_round,
                    "selected_candidate_id": selected_id,
                    "merit": list(current_merit),
                    "verification_status": current_result["verification"][
                        "overall_status"
                    ],
                }
            )
            completed.append(dict(raw_round))
            if current_result["verification"]["overall_status"] == "pass":
                if expected_round != len(raw_rounds):
                    raise ValueError("correction checkpoint continues after a passing round")
                break
        remainder = raw_rounds[len(completed) :]
        if remainder:
            interrupted = list(state.get("interrupted_rounds", []))
            interrupted.extend(json.loads(_canonical_bytes(remainder)))
            state["interrupted_rounds"] = interrupted
            state["rounds"] = completed
        return (
            current_design,
            current_result,
            current_diagnosis,
            current_merit,
            history,
        )

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
        normalized = validate_autonomous_correction_spec(spec, design)
        unresolved = Path(output_directory).expanduser()
        if unresolved.is_symlink():
            raise ValueError("correction directory must not be a symbolic link")
        root = unresolved.resolve()
        if root == Path(root.anchor):
            raise ValueError("correction directory must not be a filesystem root")
        correction_id = f"autonomous-correction-{uuid.uuid4().hex}"
        runtime = {
            "timeout_per_experiment": float(timeout_per_experiment),
            "max_points": int(max_points),
            "planner": self._planner_resume_identity(),
        }

        def notify(stage: str, progress: int, message: str) -> None:
            if checkpoint is not None:
                checkpoint(stage, progress, message)

        with output_lease(root, correction_id):
            if root.exists() and not root.is_dir():
                raise ValueError("correction directory exists and is not a directory")
            has_checkpoint = root.exists() and any(root.iterdir())
            if has_checkpoint and not resume:
                raise FileExistsError(
                    f"refusing to overwrite non-empty correction directory: {root}"
                )
            root.mkdir(parents=True, exist_ok=True)
            if has_checkpoint:
                state = _read_json_object(
                    root / CORRECTION_STATE_NAME, "correction checkpoint"
                )
                if (
                    state.get("schema_version") != AUTONOMOUS_CORRECTION_SCHEMA_VERSION
                    or state.get("kind") != "multisim-mcp-autonomous-correction"
                ):
                    raise ValueError("correction checkpoint contract is invalid")
                if (
                    state.get("original_digest") != _digest(design.to_dict())
                    or state.get("spec_digest") != _digest(normalized)
                    or _canonical_bytes(state.get("runtime"))
                    != _canonical_bytes(runtime)
                ):
                    raise ValueError(
                        "correction checkpoint does not match design, spec, or runtime"
                    )
                stored_id = state.get("correction_id")
                if not isinstance(stored_id, str) or not _CORRECTION_ID_RE.fullmatch(
                    stored_id
                ):
                    raise ValueError("correction checkpoint id is invalid")
                correction_id = stored_id
                _write_or_validate_json(
                    root / ORIGINAL_DESIGN_NAME,
                    design.to_dict(),
                    "original correction design",
                )
                _write_or_validate_json(
                    root / CORRECTION_SPEC_NAME,
                    normalized,
                    "autonomous correction spec",
                )
                (
                    current_design,
                    current_result,
                    current_diagnosis,
                    current_merit,
                    history,
                ) = self._restore_committed_progress(
                    root, state, design, normalized
                )
                state.update(
                    state="running",
                    status="running",
                    stop_reason=None,
                    updated_at=_utc_now(),
                    finished_at=None,
                    resume_count=int(state.get("resume_count", 0)) + 1,
                )
                _atomic_json(root / CORRECTION_STATE_NAME, state)
                notify(
                    "correction_recovered",
                    2,
                    f"Recovered {len(history)} committed correction rounds",
                )
            else:
                started_at = _utc_now()
                state = {
                    "schema_version": AUTONOMOUS_CORRECTION_SCHEMA_VERSION,
                    "kind": "multisim-mcp-autonomous-correction",
                    "correction_id": correction_id,
                    "state": "running",
                    "status": "running",
                    "stop_reason": None,
                    "started_at": started_at,
                    "updated_at": started_at,
                    "finished_at": None,
                    "resume_count": 0,
                    "design_id": design.design_id,
                    "original_revision": design.revision,
                    "original_digest": _digest(design.to_dict()),
                    "spec_digest": _digest(normalized),
                    "runtime": runtime,
                    "baseline": {
                        "status": "running",
                        "attempt": 0,
                        "interrupted_attempts": [],
                        "experiment_output": None,
                        "experiment": None,
                        "diagnosis_path": None,
                        "merit": None,
                    },
                    "rounds": [],
                    "interrupted_rounds": [],
                    "round_attempt_counts": {},
                    "experiments_attempted": 0,
                    "experiment_attempt_count": 0,
                    "source_design_modified": False,
                    "candidate_persisted_as_source": False,
                }
                current_design = design
                current_result = None
                current_diagnosis = None
                current_merit = None
                history = []
                _atomic_json(root / CORRECTION_STATE_NAME, state)
                _write_or_validate_json(
                    root / ORIGINAL_DESIGN_NAME,
                    design.to_dict(),
                    "original correction design",
                )
                _write_or_validate_json(
                    root / CORRECTION_SPEC_NAME,
                    normalized,
                    "autonomous correction spec",
                )

            if current_result is None:
                baseline = state["baseline"]
                attempt = int(baseline.get("attempt", 0)) + 1
                if attempt > 1:
                    interrupted = list(baseline.get("interrupted_attempts", []))
                    interrupted.append(
                        {
                            "attempt": attempt - 1,
                            "experiment_output": baseline.get("experiment_output"),
                        }
                    )
                    baseline["interrupted_attempts"] = interrupted
                experiment_name = "baseline" if attempt == 1 else f"baseline-attempt-{attempt:03d}"
                experiment_output = Path(experiment_name) / "experiment"
                baseline.update(
                    status="running",
                    attempt=attempt,
                    experiment_output=experiment_output.as_posix(),
                    experiment=None,
                    diagnosis_path=None,
                    merit=None,
                )
                state["experiment_attempt_count"] = int(
                    state.get("experiment_attempt_count", 0)
                ) + 1
                state["experiments_attempted"] = state["experiment_attempt_count"]
                state["updated_at"] = _utc_now()
                _atomic_json(root / CORRECTION_STATE_NAME, state)
                notify("correction_baseline", 2, "Running baseline experiment")
                try:
                    current_result = self._run_experiment(
                        design,
                        normalized,
                        root / experiment_output,
                        correction_id,
                        float(timeout_per_experiment),
                        int(max_points),
                        cancel_requested,
                    )
                except InterruptedError:
                    baseline["status"] = "interrupted"
                    state.update(
                        state="cancelled",
                        status="cancelled",
                        stop_reason="cancellation_requested",
                        updated_at=_utc_now(),
                    )
                    _atomic_json(root / CORRECTION_STATE_NAME, state)
                    raise
                evidence, _ = validate_ranked_experiment_evidence(
                    root,
                    current_result,
                    _primary_objective(normalized),
                    workflow_name="autonomous correction baseline",
                )
                current_diagnosis = self._diagnosis.run(
                    design,
                    experiment_evidence=_diagnosis_evidence(current_result),
                )
                diagnosis_path = root / experiment_name / "diagnosis.json"
                _atomic_json(diagnosis_path, current_diagnosis)
                current_merit = _verification_merit(current_result["verification"])
                baseline.update(
                    status="completed",
                    experiment=evidence,
                    diagnosis_path=diagnosis_path.relative_to(root).as_posix(),
                    merit=list(current_merit),
                )
                state["updated_at"] = _utc_now()
                _atomic_json(root / CORRECTION_STATE_NAME, state)
            assert current_result is not None
            assert current_diagnosis is not None
            assert current_merit is not None
            current_verification = current_result["verification"]
            stop_reason = "requirements_already_pass"
            cancelled = False
            if current_verification["overall_status"] != "pass":
                stop_reason = "round_budget_exhausted"
                for round_number in range(
                    len(history) + 1, int(normalized["max_rounds"]) + 1
                ):
                    if cancel_requested is not None and cancel_requested():
                        stop_reason = "cancellation_requested"
                        cancelled = True
                        break
                    notify(
                        "correction_planning",
                        min(85, 5 + int(75 * (round_number - 1) / normalized["max_rounds"])),
                        f"Planning correction round {round_number}",
                    )
                    planner_error: dict[str, str] | None = None
                    try:
                        raw_proposals = self._planner(
                            current_design,
                            current_diagnosis,
                            normalized,
                            history,
                            round_number,
                        )
                    except Exception as exc:
                        raw_proposals = ()
                        planner_error = {
                            "type": type(exc).__name__,
                            "message": str(exc)[:1000],
                        }
                    if not isinstance(raw_proposals, Sequence) or isinstance(raw_proposals, (str, bytes)):
                        raise ValueError("repair planner must return a sequence of patches")
                    proposals = list(raw_proposals)[: int(normalized["max_candidates_per_round"])]
                    attempt_counts = state.get("round_attempt_counts")
                    if not isinstance(attempt_counts, dict):
                        raise ValueError("correction round attempt counters are invalid")
                    round_key = str(round_number)
                    round_attempt = int(attempt_counts.get(round_key, 0)) + 1
                    attempt_counts[round_key] = round_attempt
                    round_directory = (
                        f"round-{round_number:03d}"
                        if round_attempt == 1
                        else f"round-{round_number:03d}-attempt-{round_attempt:03d}"
                    )
                    round_record: dict[str, Any] = {
                        "round": round_number,
                        "attempt": round_attempt,
                        "status": "running",
                        "base_revision": current_design.revision,
                        "base_merit": list(current_merit),
                        "candidate_count": len(proposals),
                        "candidates": [],
                        "selected_candidate_id": None,
                        "planner": (
                            self._planner.last_run_metadata()
                            if hasattr(self._planner, "last_run_metadata")
                            else None
                        ),
                        "planner_error": planner_error,
                    }
                    state["rounds"].append(round_record)
                    state["updated_at"] = _utc_now()
                    _atomic_json(root / CORRECTION_STATE_NAME, state)
                    if not proposals:
                        stop_reason = (
                            "planner_failed"
                            if planner_error is not None
                            else "planner_returned_no_candidates"
                        )
                        round_record["status"] = "terminal"
                        _atomic_json(root / CORRECTION_STATE_NAME, state)
                        break
                    candidate_records: list[dict[str, Any]] = []
                    for candidate_number, raw_patch in enumerate(proposals, 1):
                        if int(state.get("experiment_attempt_count", 0)) >= int(
                            normalized["max_total_experiments"]
                        ):
                            stop_reason = "experiment_budget_exhausted"
                            break
                        candidate_id = f"round-{round_number:03d}-candidate-{candidate_number:03d}"
                        candidate_root = (
                            root
                            / "rounds"
                            / round_directory
                            / f"candidate-{candidate_number:03d}"
                        )
                        record: dict[str, Any] = {
                            "candidate_id": candidate_id,
                            "status": "running",
                            "merit": None,
                            "objectives": None,
                            "patch_path": None,
                            "design_path": None,
                            "diagnosis_path": None,
                            "experiment_output": (
                                candidate_root / "experiment"
                            ).relative_to(root).as_posix(),
                            "experiment": None,
                            "error": None,
                        }
                        round_record["candidates"].append(record)
                        state["experiment_attempt_count"] = int(
                            state.get("experiment_attempt_count", 0)
                        ) + 1
                        state["experiments_attempted"] = state[
                            "experiment_attempt_count"
                        ]
                        state["updated_at"] = _utc_now()
                        _atomic_json(root / CORRECTION_STATE_NAME, state)
                        try:
                            prepared = prepare_design_patch(
                                current_design,
                                raw_patch,
                                regenerate_source_netlist=current_design.source_netlist is not None,
                            )
                            _atomic_json(candidate_root / "patch.json", prepared.patch.to_dict())
                            _atomic_json(candidate_root / "design.json", prepared.candidate.to_dict())
                            record["patch_path"] = (candidate_root / "patch.json").relative_to(root).as_posix()
                            record["design_path"] = (candidate_root / "design.json").relative_to(root).as_posix()
                            result = self._run_experiment(
                                prepared.candidate,
                                normalized,
                                candidate_root / "experiment",
                                correction_id,
                                float(timeout_per_experiment),
                                int(max_points),
                                cancel_requested,
                            )
                            evidence, _ = validate_ranked_experiment_evidence(
                                root,
                                result,
                                _primary_objective(normalized),
                                workflow_name="autonomous correction",
                            )
                            verification = result["verification"]
                            merit = _verification_merit(verification)
                            diagnosis = self._diagnosis.run(
                                prepared.candidate,
                                experiment_evidence=_diagnosis_evidence(result),
                            )
                            diagnosis_path = candidate_root / "diagnosis.json"
                            _atomic_json(diagnosis_path, diagnosis)
                            record["status"] = (
                                "passed" if evidence["overall_status"] == "pass" else "failed_requirements"
                            )
                            record["merit"] = list(merit)
                            record["objectives"] = (
                                objective_vector(verification, normalized["objectives"])
                                if normalized["objectives"]
                                else None
                            )
                            record["experiment"] = evidence
                            record["diagnosis_path"] = diagnosis_path.relative_to(
                                root
                            ).as_posix()
                            candidate_records.append(
                                {
                                    "record": record,
                                    "prepared": prepared,
                                    "result": result,
                                    "diagnosis": diagnosis,
                                    "merit": merit,
                                }
                            )
                        except InterruptedError as exc:
                            record["status"] = "interrupted"
                            record["error"] = {
                                "type": type(exc).__name__,
                                "message": str(exc)[:1000],
                            }
                            stop_reason = "cancellation_requested"
                            cancelled = True
                        except Exception as exc:
                            record["status"] = "error"
                            record["error"] = {
                                "type": type(exc).__name__,
                                "message": str(exc)[:1000],
                            }
                        state["updated_at"] = _utc_now()
                        _atomic_json(root / CORRECTION_STATE_NAME, state)
                        if cancelled:
                            break
                        if (
                            normalized["stop_on_first_pass"]
                            and candidate_records
                            and candidate_records[-1]["record"]["status"] == "passed"
                        ):
                            break
                    if cancelled:
                        break
                    if stop_reason == "experiment_budget_exhausted":
                        round_record["status"] = "terminal"
                        _atomic_json(root / CORRECTION_STATE_NAME, state)
                        break
                    if not candidate_records:
                        stop_reason = "all_candidates_invalid_or_failed"
                        round_record["status"] = "terminal"
                        _atomic_json(root / CORRECTION_STATE_NAME, state)
                        break
                    passing = [item for item in candidate_records if item["record"]["status"] == "passed"]
                    if passing and normalized["objectives"]:
                        fronts = pareto_fronts(
                            [
                                {"objectives": item["record"]["objectives"]}
                                for item in passing
                            ]
                        )
                        front = [passing[index] for index in fronts[0]]
                        selected_index = weighted_compromise(
                            [{"objectives": item["record"]["objectives"]} for item in front]
                        )
                        assert selected_index is not None
                        selected = front[selected_index]
                    elif passing:
                        selected = min(passing, key=lambda item: item["merit"])
                    else:
                        selected = min(candidate_records, key=lambda item: item["merit"])
                    if (
                        normalized["require_strict_improvement"]
                        and selected["merit"] >= current_merit
                    ):
                        stop_reason = "no_strict_improvement"
                        round_record["status"] = "terminal"
                        _atomic_json(root / CORRECTION_STATE_NAME, state)
                        break
                    round_record["selected_candidate_id"] = selected["record"]["candidate_id"]
                    round_record["status"] = "selected"
                    current_design = selected["prepared"].candidate
                    current_result = selected["result"]
                    current_verification = current_result["verification"]
                    current_diagnosis = selected["diagnosis"]
                    current_merit = selected["merit"]
                    history.append(
                        {
                            "round": round_number,
                            "selected_candidate_id": round_record["selected_candidate_id"],
                            "merit": list(current_merit),
                            "verification_status": current_verification["overall_status"],
                        }
                    )
                    state["updated_at"] = _utc_now()
                    _atomic_json(root / CORRECTION_STATE_NAME, state)
                    if current_verification["overall_status"] == "pass":
                        stop_reason = "requirements_passed"
                        break
            final_passed = current_verification["overall_status"] == "pass"
            final_patch: DesignPatch | None = None
            if current_design.to_dict() != design.to_dict():
                final_patch = create_design_diff_patch(
                    design,
                    current_design,
                    patch_id=f"autonomous-repair-{uuid.uuid4().hex}",
                    description=f"Autonomous correction candidate: {normalized['title']}",
                    metadata={
                        "source": "autonomous_correct_design",
                        "correction_id": correction_id,
                        "requirements_passed": final_passed,
                    },
                )
                _atomic_json(root / FINAL_DESIGN_NAME, current_design.to_dict())
                _atomic_json(root / FINAL_PATCH_NAME, final_patch.to_dict())
            manifest_state = "cancelled" if cancelled else "succeeded"
            final_status = (
                "cancelled"
                if cancelled
                else "corrected"
                if final_passed and final_patch
                else "already_passed"
                if final_passed
                else "not_corrected"
            )
            state.update(
                {
                    "state": manifest_state,
                    "status": final_status,
                    "stop_reason": stop_reason,
                    "updated_at": _utc_now(),
                    "finished_at": _utc_now(),
                    "final_revision": current_design.revision,
                    "final_verification_status": current_verification["overall_status"],
                    "final_merit": list(current_merit),
                    "adoption_eligible": bool(final_passed and final_patch),
                    "approval_required_before_apply": bool(final_patch),
                    "final_patch_path": FINAL_PATCH_NAME if final_patch else None,
                }
            )
            _atomic_json(root / CORRECTION_STATE_NAME, state)
            manifest = write_directory_manifest(
                root,
                directory_kind="autonomous-correction",
                entity_id=correction_id,
                state=manifest_state,
                artifacts=_artifact_allowlist(root),
                metadata={
                    "operation": "autonomous-correct-design",
                    "status": state["status"],
                    "stop_reason": stop_reason,
                    "design_id": design.design_id,
                },
            )
            notify("correction_complete", 100, state["status"])
            return {
                "schema_version": AUTONOMOUS_CORRECTION_SCHEMA_VERSION,
                "success": final_passed and not cancelled,
                "status": state["status"],
                "stop_reason": stop_reason,
                "correction_id": correction_id,
                "output_dir": str(root),
                "rounds_completed": sum(
                    item.get("status") == "selected" for item in state["rounds"]
                ),
                "experiments_attempted": state["experiments_attempted"],
                "experiment_attempt_count": state["experiment_attempt_count"],
                "resume_count": state["resume_count"],
                "final_verification_status": state["final_verification_status"],
                "adoption_eligible": state["adoption_eligible"],
                "final_patch_path": (
                    str(root / FINAL_PATCH_NAME) if final_patch is not None else None
                ),
                "approval_required_before_apply": bool(final_patch),
                "source_design_modified": False,
                "candidate_persisted_as_source": False,
                "manifest": manifest.to_dict(),
            }


__all__ = [
    "AUTONOMOUS_CORRECTION_SCHEMA_VERSION",
    "AutonomousDesignCorrectionService",
    "MAX_CANDIDATES_PER_ROUND",
    "MAX_CORRECTION_ROUNDS",
    "MAX_CORRECTION_SPEC_BYTES",
    "ModelRepairPlanner",
    "RepairPlanner",
    "read_autonomous_correction_spec",
    "validate_autonomous_correction_spec",
]
