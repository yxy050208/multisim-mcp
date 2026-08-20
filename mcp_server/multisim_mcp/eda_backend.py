"""Backend contracts shared by Multisim and future EDA engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Mapping, Protocol, runtime_checkable

from .eda_core import (
    EDA_SCHEMA_VERSION,
    ArtifactSet,
    CircuitDesign,
    JsonValue,
    _freeze_mapping,
    _require_identifier,
    _require_text,
    _thaw_json,
)


_OPERATIONS: Final = frozenset({"validate", "schematic", "simulate"})
_ANALYSES: Final = frozenset({"op", "dc", "ac", "tran"})
_SEVERITIES: Final = frozenset({"info", "warning", "error"})


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    """Machine-readable backend capability discovery result."""

    backend_id: str
    display_name: str
    operations: tuple[str, ...]
    analyses: tuple[str, ...]
    platforms: tuple[str, ...]
    requires_local_runtime: bool
    supports_editable_schematic: bool
    supports_vendor_models: bool
    supports_batch: bool
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    schema_version: int = EDA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EDA_SCHEMA_VERSION:
            raise ValueError(
                f"BackendCapabilities schema_version must be {EDA_SCHEMA_VERSION}"
            )
        object.__setattr__(
            self, "backend_id", _require_identifier(self.backend_id, "backend_id")
        )
        object.__setattr__(
            self, "display_name", _require_text(self.display_name, "display_name")
        )
        operations = tuple(dict.fromkeys(self.operations))
        analyses = tuple(dict.fromkeys(self.analyses))
        if any(not isinstance(item, str) for item in operations):
            raise ValueError("backend operations must be strings")
        if any(not isinstance(item, str) for item in analyses):
            raise ValueError("backend analyses must be strings")
        platforms = tuple(
            _require_identifier(item, "platform") for item in self.platforms
        )
        unsupported_operations = set(operations) - _OPERATIONS
        unsupported_analyses = set(analyses) - _ANALYSES
        if unsupported_operations:
            raise ValueError(
                f"unsupported backend operations: {sorted(unsupported_operations)}"
            )
        if unsupported_analyses:
            raise ValueError(
                f"unsupported backend analyses: {sorted(unsupported_analyses)}"
            )
        if "simulate" in operations and not analyses:
            raise ValueError("simulation backends must declare at least one analysis")
        for name in (
            "requires_local_runtime",
            "supports_editable_schematic",
            "supports_vendor_models",
            "supports_batch",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        object.__setattr__(self, "operations", operations)
        object.__setattr__(self, "analyses", analyses)
        object.__setattr__(self, "platforms", platforms)
        object.__setattr__(
            self, "metadata", _freeze_mapping(self.metadata, "capabilities.metadata")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "backend_id": self.backend_id,
            "display_name": self.display_name,
            "operations": list(self.operations),
            "analyses": list(self.analyses),
            "platforms": list(self.platforms),
            "requires_local_runtime": self.requires_local_runtime,
            "supports_editable_schematic": self.supports_editable_schematic,
            "supports_vendor_models": self.supports_vendor_models,
            "supports_batch": self.supports_batch,
            "metadata": _thaw_json(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: object) -> "BackendCapabilities":
        if not isinstance(value, Mapping):
            raise ValueError("BackendCapabilities must be an object")
        allowed = {
            "schema_version",
            "backend_id",
            "display_name",
            "operations",
            "analyses",
            "platforms",
            "requires_local_runtime",
            "supports_editable_schematic",
            "supports_vendor_models",
            "supports_batch",
            "metadata",
        }
        if value.get("schema_version") != EDA_SCHEMA_VERSION:
            raise ValueError(
                f"BackendCapabilities schema_version must be {EDA_SCHEMA_VERSION}"
            )
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(
                f"BackendCapabilities contains unknown fields: {sorted(unknown)}"
            )
        arrays: dict[str, tuple[Any, ...]] = {}
        for name in ("operations", "analyses", "platforms"):
            raw = value.get(name, [])
            if not isinstance(raw, (list, tuple)):
                raise ValueError(f"BackendCapabilities.{name} must be an array")
            arrays[name] = tuple(raw)
        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("BackendCapabilities.metadata must be an object")
        return cls(
            schema_version=value["schema_version"],
            backend_id=value.get("backend_id", ""),
            display_name=value.get("display_name", ""),
            operations=arrays["operations"],
            analyses=arrays["analyses"],
            platforms=arrays["platforms"],
            requires_local_runtime=value.get("requires_local_runtime"),
            supports_editable_schematic=value.get("supports_editable_schematic"),
            supports_vendor_models=value.get("supports_vendor_models"),
            supports_batch=value.get("supports_batch"),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class BackendDiagnostic:
    """Structured validation or execution evidence from a backend."""

    severity: str
    code: str
    message: str
    details: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in _SEVERITIES:
            raise ValueError(f"unsupported diagnostic severity: {self.severity!r}")
        object.__setattr__(self, "code", _require_identifier(self.code, "code"))
        object.__setattr__(self, "message", _require_text(self.message, "message"))
        object.__setattr__(
            self, "details", _freeze_mapping(self.details, "diagnostic.details")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "details": _thaw_json(self.details),
        }


@dataclass(frozen=True, slots=True)
class SchematicRequest:
    """Backend-neutral request to render an editable schematic artifact."""

    design: CircuitDesign
    output_directory: str
    file_stem: str = "circuit"
    render_image: bool = True
    open_after_build: bool = False
    include_experimental_probes: bool = False
    probe_nets: tuple[str, ...] = ()
    overwrite: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.design, CircuitDesign):
            raise ValueError("SchematicRequest.design must be CircuitDesign")
        object.__setattr__(
            self,
            "output_directory",
            _require_text(self.output_directory, "output_directory", maximum=8192),
        )
        file_stem = _require_identifier(self.file_stem, "file_stem")
        if ":" in file_stem:
            raise ValueError("file_stem must not contain ':'")
        object.__setattr__(self, "file_stem", file_stem)
        object.__setattr__(
            self,
            "probe_nets",
            tuple(_require_text(net, "probe net", maximum=255) for net in self.probe_nets),
        )
        for name in (
            "render_image",
            "open_after_build",
            "include_experimental_probes",
            "overwrite",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    """Backend-neutral request for a validated SPICE-family analysis."""

    design: CircuitDesign
    commands: str
    output_directory: str
    timeout_seconds: float = 120.0
    max_points: int = 2000
    overwrite: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.design, CircuitDesign):
            raise ValueError("SimulationRequest.design must be CircuitDesign")
        object.__setattr__(
            self, "commands", _require_text(self.commands, "commands", maximum=64_000)
        )
        object.__setattr__(
            self,
            "output_directory",
            _require_text(self.output_directory, "output_directory", maximum=8192),
        )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < float(self.timeout_seconds) <= 86_400
        ):
            raise ValueError("timeout_seconds must be between 0 and 86400")
        if (
            isinstance(self.max_points, bool)
            or not isinstance(self.max_points, int)
            or not 1 <= self.max_points <= 10_000_000
        ):
            raise ValueError("max_points must be between 1 and 10000000")
        if not isinstance(self.overwrite, bool):
            raise ValueError("overwrite must be a boolean")


@dataclass(frozen=True, slots=True)
class BackendExecution:
    """Transport-neutral result of one backend operation."""

    backend_id: str
    operation: str
    success: bool
    artifacts: ArtifactSet
    diagnostics: tuple[BackendDiagnostic, ...] = ()
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    schema_version: int = EDA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EDA_SCHEMA_VERSION:
            raise ValueError(f"BackendExecution schema_version must be {EDA_SCHEMA_VERSION}")
        object.__setattr__(
            self, "backend_id", _require_identifier(self.backend_id, "backend_id")
        )
        if self.operation not in _OPERATIONS:
            raise ValueError(f"unsupported backend operation: {self.operation!r}")
        if not isinstance(self.success, bool):
            raise ValueError("success must be a boolean")
        if not isinstance(self.artifacts, ArtifactSet):
            raise ValueError("artifacts must be ArtifactSet")
        if self.artifacts.producer != self.backend_id:
            raise ValueError("ArtifactSet.producer must match backend_id")
        diagnostics = tuple(self.diagnostics)
        if any(not isinstance(item, BackendDiagnostic) for item in diagnostics):
            raise ValueError("diagnostics must contain BackendDiagnostic")
        if self.success and any(item.severity == "error" for item in diagnostics):
            raise ValueError("successful execution must not contain error diagnostics")
        object.__setattr__(self, "diagnostics", diagnostics)
        object.__setattr__(
            self, "payload", _freeze_mapping(self.payload, "execution.payload")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "backend_id": self.backend_id,
            "operation": self.operation,
            "success": self.success,
            "artifacts": self.artifacts.to_dict(),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "payload": _thaw_json(self.payload),
        }


@runtime_checkable
class EdaBackend(Protocol):
    """Capability-driven backend boundary used by the application service."""

    @property
    def backend_id(self) -> str: ...

    def discover_capabilities(self) -> BackendCapabilities: ...

    def validate_design(self, design: CircuitDesign) -> tuple[BackendDiagnostic, ...]: ...

    def create_schematic(self, request: SchematicRequest) -> BackendExecution: ...

    def simulate(self, request: SimulationRequest) -> BackendExecution: ...


__all__ = [
    "BackendCapabilities",
    "BackendDiagnostic",
    "BackendExecution",
    "EdaBackend",
    "SchematicRequest",
    "SimulationRequest",
]
