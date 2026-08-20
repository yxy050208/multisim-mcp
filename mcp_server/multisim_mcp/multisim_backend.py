"""Multisim implementation of the transport-neutral :mod:`eda_backend` API.

The adapter receives the current low-level executors as constructor arguments.
That keeps MCP decorators and COM initialization outside the EDA core, and lets
tests replace Multisim with deterministic in-process fakes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .eda_backend import (
    BackendCapabilities,
    BackendDiagnostic,
    BackendExecution,
    SchematicRequest,
    SimulationRequest,
)
from .eda_core import Artifact, ArtifactSet, CircuitDesign, _derived_identifier


Executor = Callable[..., Mapping[str, Any]]

_ARTIFACT_TYPES: dict[str, tuple[str, str]] = {
    ".cir": ("netlist", "text/plain"),
    ".csv": ("data", "text/csv"),
    ".html": ("report", "text/html"),
    ".json": ("manifest", "application/json"),
    ".log": ("log", "text/plain"),
    ".md": ("report", "text/markdown"),
    ".ms14": ("schematic", "application/octet-stream"),
    ".pdf": ("report", "application/pdf"),
    ".png": ("image", "image/png"),
    ".raw": ("simulation-data", "application/octet-stream"),
    ".svg": ("plot", "image/svg+xml"),
    ".txt": ("commands", "text/plain"),
    ".xml": ("schematic-source", "application/xml"),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_set(
    design: CircuitDesign,
    operation: str,
    output_directory: Path | None,
    paths: list[Path],
) -> ArtifactSet:
    unique: dict[str, Path] = {}
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved.is_file():
            unique[resolved.name.lower()] = resolved
    artifacts: list[Artifact] = []
    ordered_paths = sorted(unique.values(), key=lambda item: item.name.lower())
    for index, path in enumerate(ordered_paths):
        suffix = path.suffix.lower()
        kind, media_type = _ARTIFACT_TYPES.get(
            suffix, ("artifact", "application/octet-stream")
        )
        artifacts.append(
            Artifact(
                artifact_id=_derived_identifier(design.design_id, operation, index + 1),
                name=path.name,
                kind=kind,
                location=str(path),
                media_type=media_type,
                size=path.stat().st_size,
                sha256=_sha256(path),
            )
        )
    storage_location = (
        str(output_directory) if output_directory is not None else "backend-managed"
    )
    output_key = hashlib.sha256(storage_location.encode("utf-8")).hexdigest()[:12]
    return ArtifactSet(
        artifact_set_id=_derived_identifier(design.design_id, operation, output_key),
        design_id=design.design_id,
        producer="multisim",
        artifacts=tuple(artifacts),
        metadata={
            "output_directory": (
                str(output_directory) if output_directory is not None else None
            ),
            "storage_location": storage_location,
        },
    )


def _require_output_directory(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if root == Path(root.anchor):
        raise ValueError("output_directory must not be a filesystem root")
    if root.exists() and not root.is_dir():
        raise ValueError("output_directory must be a directory")
    root.mkdir(parents=True, exist_ok=True)
    return root


class MultisimBackend:
    """Adapter around existing Multisim schematic and simulation executors."""

    backend_id = "multisim"

    def __init__(
        self,
        schematic_executor: Executor,
        simulation_executor: Executor,
    ) -> None:
        if not callable(schematic_executor) or not callable(simulation_executor):
            raise ValueError("MultisimBackend executors must be callable")
        self._schematic_executor = schematic_executor
        self._simulation_executor = simulation_executor

    def discover_capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend_id=self.backend_id,
            display_name="NI Multisim Automation API",
            operations=("validate", "schematic", "simulate"),
            analyses=("op", "dc", "ac", "tran"),
            platforms=("windows",),
            requires_local_runtime=True,
            supports_editable_schematic=True,
            supports_vendor_models=True,
            supports_batch=True,
            metadata={
                "adapter_schema_version": 1,
                "authoritative_simulation_path": "command-engine",
                "schematic_maturity": "experimental",
            },
        )

    def validate_design(self, design: CircuitDesign) -> tuple[BackendDiagnostic, ...]:
        if not isinstance(design, CircuitDesign):
            raise ValueError("design must be CircuitDesign")
        diagnostics: list[BackendDiagnostic] = []
        if design.source_netlist is None:
            diagnostics.append(
                BackendDiagnostic(
                    severity="error",
                    code="source-netlist-required",
                    message=(
                        "The Multisim adapter requires source_netlist for "
                        "dialect-faithful execution."
                    ),
                )
            )
        if not design.components:
            diagnostics.append(
                BackendDiagnostic(
                    severity="info",
                    code="source-only-design",
                    message=(
                        "The design uses its source netlist as the authoritative "
                        "representation for this compatibility adapter."
                    ),
                )
            )
        return tuple(diagnostics)

    def _validated_netlist(self, design: CircuitDesign) -> str:
        diagnostics = self.validate_design(design)
        errors = [item.message for item in diagnostics if item.severity == "error"]
        if errors:
            raise ValueError("; ".join(errors))
        assert design.source_netlist is not None
        return design.source_netlist

    def create_schematic(self, request: SchematicRequest) -> BackendExecution:
        if not isinstance(request, SchematicRequest):
            raise ValueError("request must be SchematicRequest")
        netlist = self._validated_netlist(request.design)
        root = _require_output_directory(request.output_directory)
        ms14 = root / f"{request.file_stem}.ms14"
        image = (
            Path(request.image_path).expanduser().resolve()
            if request.image_path is not None
            else root / f"{request.file_stem}.png"
            if request.render_image
            else None
        )
        result = self._schematic_executor(
            netlist,
            str(ms14),
            probe_nets=list(request.probe_nets),
            include_experimental_probes=request.include_experimental_probes,
            open_after_build=request.open_after_build,
            image_path=str(image) if image else None,
            overwrite=request.overwrite,
        )
        success = result.get("success") is True
        paths = [ms14, Path(str(ms14) + ".xml")]
        if image is not None:
            paths.append(image)
        for key in ("ms14", "xml", "image"):
            value = result.get(key)
            if isinstance(value, str):
                paths.append(Path(value))
        build = result.get("build") if isinstance(result.get("build"), Mapping) else {}
        verification = (
            result.get("verification")
            if isinstance(result.get("verification"), Mapping)
            else {}
        )
        diagnostics = list(self.validate_design(request.design))
        if not success:
            diagnostics.append(
                BackendDiagnostic(
                    severity="error",
                    code="schematic-failed",
                    message="Multisim did not report a successful schematic build.",
                )
            )
        return BackendExecution(
            backend_id=self.backend_id,
            operation="schematic",
            success=success,
            artifacts=_artifact_set(request.design, "schematic", root, paths),
            diagnostics=tuple(diagnostics),
            payload={
                "compatibility_result": result,
                "editable_model_coverage": build.get("editable_model_coverage"),
                "native_netlist_complete": verification.get(
                    "native_netlist_complete"
                ),
                "experimental_probes": result.get("experimental_probes", False),
            },
        )

    def simulate(self, request: SimulationRequest) -> BackendExecution:
        if not isinstance(request, SimulationRequest):
            raise ValueError("request must be SimulationRequest")
        netlist = self._validated_netlist(request.design)
        root = (
            _require_output_directory(request.output_directory)
            if request.output_directory is not None
            else None
        )
        result = self._simulation_executor(
            netlist,
            request.commands,
            output_dir=str(root) if root is not None else None,
            timeout=float(request.timeout_seconds),
            max_points=request.max_points,
            unsafe_commands=request.unsafe_commands,
            overwrite=request.overwrite,
        )
        success = result.get("success") is True
        paths: list[Path] = []
        for key in ("raw", "csv", "netlist", "commands", "log"):
            value = result.get(key)
            if isinstance(value, str):
                paths.append(Path(value))
        result_artifacts = result.get("artifacts", [])
        if isinstance(result_artifacts, (list, tuple)):
            paths.extend(Path(item) for item in result_artifacts if isinstance(item, str))
        artifact_root = root
        if artifact_root is None:
            work_dir = result.get("work_dir")
            if isinstance(work_dir, str) and work_dir.strip():
                artifact_root = Path(work_dir).expanduser().resolve()
        diagnostics = list(self.validate_design(request.design))
        if not success:
            diagnostics.append(
                BackendDiagnostic(
                    severity="error",
                    code="simulation-failed",
                    message="Multisim did not report a successful simulation.",
                )
            )
        return BackendExecution(
            backend_id=self.backend_id,
            operation="simulate",
            success=success,
            artifacts=_artifact_set(
                request.design, "simulate", artifact_root, paths
            ),
            diagnostics=tuple(diagnostics),
            payload={
                "compatibility_result": result,
                "commands": request.commands,
                "max_points": request.max_points,
                "timeout_seconds": float(request.timeout_seconds),
                "unsafe_commands": request.unsafe_commands,
            },
        )


__all__ = ["MultisimBackend"]
