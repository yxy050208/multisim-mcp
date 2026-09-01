"""Transport-neutral application service for complete circuit experiments."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .design_verification import DesignRequirement, validate_experiment_spec
from .eda_core import CircuitDesign
from .safety import validate_analysis_commands
from .spice_adapter import circuit_design_to_spice


Checkpoint = Callable[[str, int, str], None]
CancellationProbe = Callable[[], bool]
ExperimentRunner = Callable[..., Mapping[str, Any]]


def _freeze_json(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or "\x00" in key:
                raise ValueError(f"{path} keys must be non-empty strings")
            frozen[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{path}[]") for item in value)
    raise ValueError(f"{path} is not JSON-compatible")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ExperimentRequest:
    """Validated request for one transactional schematic/simulation workflow."""

    design: CircuitDesign
    commands: str
    output_directory: str
    title: str = "Multisim experiment"
    timeout_seconds: float = 120.0
    max_points: int = 2000
    overwrite: bool = False
    owner: str | None = None
    requirements: tuple[DesignRequirement, ...] | None = None
    theoretical_values: Mapping[str, float] = field(default_factory=dict)
    approval_provenance: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.design, CircuitDesign):
            raise ValueError("ExperimentRequest.design must be CircuitDesign")
        if not isinstance(self.commands, str) or len(self.commands) > 64_000:
            raise ValueError("commands must be a bounded string")
        accepted = validate_analysis_commands(self.commands)
        object.__setattr__(self, "commands", "\n".join(accepted))

        if not isinstance(self.output_directory, str) or not self.output_directory.strip():
            raise ValueError("output_directory must not be empty")
        output_path = Path(self.output_directory).expanduser().resolve()
        if output_path == Path(output_path.anchor):
            raise ValueError("output_directory must not be a filesystem root")
        object.__setattr__(self, "output_directory", str(output_path))

        if not isinstance(self.title, str) or "\x00" in self.title:
            raise ValueError("title must be a string without NUL characters")
        if len(self.title) > 4096:
            raise ValueError("title is too long")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or not 0 < float(self.timeout_seconds) <= 3600
        ):
            raise ValueError("timeout_seconds must be between 0 and 3600")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        if (
            isinstance(self.max_points, bool)
            or not isinstance(self.max_points, int)
            or not 1 <= self.max_points <= 100_000
        ):
            raise ValueError("max_points must be between 1 and 100000")
        if not isinstance(self.overwrite, bool):
            raise ValueError("overwrite must be a boolean")
        if self.approval_provenance is not None:
            if not isinstance(self.approval_provenance, Mapping):
                raise ValueError("approval_provenance must be an object")
            frozen_provenance = _freeze_json(
                self.approval_provenance, "approval_provenance"
            )
            assert isinstance(frozen_provenance, Mapping)
            object.__setattr__(self, "approval_provenance", frozen_provenance)
        if self.owner is not None:
            if (
                not isinstance(self.owner, str)
                or not self.owner.strip()
                or "\x00" in self.owner
                or len(self.owner) > 256
            ):
                raise ValueError("owner must be a non-empty bounded string")

        raw_theories = self.theoretical_values
        if not isinstance(raw_theories, Mapping):
            raise ValueError("theoretical_values must be an object")
        if self.requirements is None:
            if raw_theories:
                raise ValueError("theoretical_values requires requirements")
            object.__setattr__(self, "requirements", None)
            object.__setattr__(
                self, "theoretical_values", MappingProxyType({})
            )
            return

        if not isinstance(self.requirements, (list, tuple)):
            raise ValueError("requirements must be an array")
        normalized = validate_experiment_spec(
            {
                "schema_version": 1,
                "title": self.title,
                "netlist": circuit_design_to_spice(self.design),
                "commands": self.commands,
                "requirements": list(self.requirements),
                "theoretical_values": dict(raw_theories),
            }
        )
        object.__setattr__(self, "title", normalized["title"])
        frozen_requirements = _freeze_json(
            normalized["requirements"], "requirements"
        )
        assert isinstance(frozen_requirements, tuple)
        object.__setattr__(self, "requirements", frozen_requirements)
        frozen_theories = _freeze_json(
            normalized["theoretical_values"], "theoretical_values"
        )
        assert isinstance(frozen_theories, Mapping)
        object.__setattr__(self, "theoretical_values", frozen_theories)

    def runner_requirements(self) -> list[DesignRequirement] | None:
        if self.requirements is None:
            return None
        thawed = _thaw_json(self.requirements)
        assert isinstance(thawed, list)
        return thawed

    def runner_theoretical_values(self) -> dict[str, float] | None:
        if self.requirements is None:
            return None
        thawed = _thaw_json(self.theoretical_values)
        assert isinstance(thawed, dict)
        return thawed


class ExperimentApplicationService:
    """Dispatch complete experiments without depending on MCP or COM state."""

    def __init__(self, runner: ExperimentRunner) -> None:
        if not callable(runner):
            raise ValueError("runner must be callable")
        self._runner = runner

    def run(
        self,
        request: ExperimentRequest,
        *,
        checkpoint: Checkpoint | None = None,
        cancel_requested: CancellationProbe | None = None,
    ) -> dict[str, Any]:
        if not isinstance(request, ExperimentRequest):
            raise ValueError("request must be ExperimentRequest")
        if checkpoint is not None and not callable(checkpoint):
            raise ValueError("checkpoint must be callable")
        if cancel_requested is not None and not callable(cancel_requested):
            raise ValueError("cancel_requested must be callable")

        result = self._runner(
            netlist=circuit_design_to_spice(request.design),
            model_references=[
                item.to_dict() for item in request.design.model_references
            ],
            declared_dialect=(
                request.design.annotations.get("spice_dialect")
                if isinstance(
                    request.design.annotations.get("spice_dialect"), str
                )
                else None
            ),
            commands=request.commands,
            output_dir=request.output_directory,
            title=request.title,
            timeout=request.timeout_seconds,
            max_points=request.max_points,
            overwrite=request.overwrite,
            checkpoint=checkpoint,
            cancel_requested=cancel_requested,
            owner=request.owner,
            requirements=request.runner_requirements(),
            theoretical_values=request.runner_theoretical_values(),
            approval_provenance=(
                _thaw_json(request.approval_provenance)
                if request.approval_provenance is not None
                else None
            ),
        )
        if not isinstance(result, Mapping):
            raise RuntimeError("experiment runner returned a non-object result")
        if not isinstance(result.get("success"), bool):
            raise RuntimeError("experiment runner result requires a boolean success")
        if result["success"]:
            self._validate_success(request, result)
        return dict(result)

    @staticmethod
    def _validate_success(
        request: ExperimentRequest, result: Mapping[str, Any]
    ) -> None:
        for name in ("experiment_id", "report", "plot", "output_dir"):
            if not isinstance(result.get(name), str) or not str(result[name]).strip():
                raise RuntimeError(f"successful experiment result requires {name}")
        for name in ("resources", "schematic", "simulation"):
            if not isinstance(result.get(name), Mapping):
                raise RuntimeError(f"successful experiment result requires {name}")
        returned_root = Path(str(result["output_dir"])).expanduser().resolve()
        if returned_root != Path(request.output_directory):
            raise RuntimeError("experiment result output_dir does not match the request")
        if request.requirements is None:
            return
        verification = result.get("verification")
        if not isinstance(verification, Mapping):
            raise RuntimeError(
                "successful verified experiment requires verification results"
            )
        if verification.get("schema_version") != 1:
            raise RuntimeError("verified experiment schema_version is invalid")
        if verification.get("overall_status") not in {
            "pass",
            "fail",
            "unverified",
        }:
            raise RuntimeError("verified experiment overall_status is invalid")
        counts = verification.get("counts")
        requirements = verification.get("requirements")
        if not isinstance(counts, Mapping) or set(counts) != {
            "pass",
            "fail",
            "unverified",
        }:
            raise RuntimeError("verified experiment counts are invalid")
        if any(
            isinstance(counts[name], bool)
            or not isinstance(counts[name], int)
            or counts[name] < 0
            for name in ("pass", "fail", "unverified")
        ):
            raise RuntimeError("verified experiment counts are invalid")
        if (
            not isinstance(requirements, list)
            or len(requirements) != len(request.requirements)
            or sum(int(counts[name]) for name in counts) != len(requirements)
        ):
            raise RuntimeError("verified experiment requirement count is invalid")
        expected_ids = [str(item["id"]) for item in request.requirements]
        received_ids: list[str] = []
        actual_counts = {"pass": 0, "fail": 0, "unverified": 0}
        for item in requirements:
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                raise RuntimeError("verified experiment requirement is invalid")
            status = item.get("status")
            if status not in actual_counts:
                raise RuntimeError("verified experiment requirement status is invalid")
            received_ids.append(item["id"])
            actual_counts[status] += 1
        if received_ids != expected_ids:
            raise RuntimeError("verified experiment requirement ids do not match request")
        if any(int(counts[name]) != actual_counts[name] for name in actual_counts):
            raise RuntimeError("verified experiment counts do not match requirements")
        derived_status = (
            "fail"
            if actual_counts["fail"]
            else "unverified"
            if actual_counts["unverified"]
            else "pass"
        )
        if verification["overall_status"] != derived_status:
            raise RuntimeError("verified experiment overall_status is inconsistent")
        verification_path = result.get("verification_path")
        if not isinstance(verification_path, str) or not verification_path.strip():
            raise RuntimeError(
                "successful verified experiment requires verification_path"
            )
        resolved_verification = Path(verification_path).expanduser().resolve()
        try:
            resolved_verification.relative_to(returned_root)
        except ValueError as exc:
            raise RuntimeError(
                "verified experiment verification_path escapes output_dir"
            ) from exc


__all__ = ["ExperimentApplicationService", "ExperimentRequest"]
