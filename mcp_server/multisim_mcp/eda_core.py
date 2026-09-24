"""Transport-neutral, versioned domain objects for the EDA application core.

This module deliberately imports neither MCP nor Multisim/COM code.  The same
objects can therefore be used by the MCP adapter, a local API, a visual
workbench, tests, and future simulation backends.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Mapping


EDA_SCHEMA_VERSION: Final = 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REFDES = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PATCH_OPERATIONS: Final = frozenset(
    {
        "set_component_value",
        "set_component_nodes",
        "set_component_model",
        "add_component",
        "remove_component",
        "replace_component",
        "add_net",
        "remove_net",
        "set_parameter",
        "set_annotation",
    }
)
_INVERSE_PATCH_OPERATION: Final = {
    "add_component": "remove_component",
    "remove_component": "add_component",
    "add_net": "remove_net",
    "remove_net": "add_net",
}

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


def _require_text(value: object, name: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    if "\x00" in normalized or len(normalized) > maximum:
        raise ValueError(f"{name} is invalid or too long")
    return normalized


def _require_identifier(value: object, name: str) -> str:
    normalized = _require_text(value, name, maximum=128)
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{name} must be a stable identifier")
    return normalized


def _derived_identifier(*parts: object) -> str:
    raw = ":".join(str(part) for part in parts)
    if len(raw) <= 128 and _IDENTIFIER.fullmatch(raw):
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    prefix = re.sub(r"[^A-Za-z0-9._:-]", "-", raw)[:110].rstrip(".:-")
    if not prefix or not prefix[0].isalnum():
        prefix = "derived"
    return f"{prefix}:{digest}"


def _freeze_json(value: Any, path: str = "value") -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or "\x00" in key:
                raise ValueError(f"{path} keys must be non-empty strings")
            frozen[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{path}[]") for item in value)
    raise ValueError(f"{path} is not JSON-compatible")


def _freeze_mapping(value: Mapping[str, Any], path: str) -> Mapping[str, JsonValue]:
    frozen = _freeze_json(value, path)
    assert isinstance(frozen, Mapping)
    return frozen


def _thaw_json(value: JsonValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _expect_object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _check_schema(
    value: Mapping[str, Any], name: str, allowed_fields: frozenset[str]
) -> None:
    if value.get("schema_version") != EDA_SCHEMA_VERSION:
        raise ValueError(f"{name} schema_version must be {EDA_SCHEMA_VERSION}")
    unknown = set(value) - allowed_fields
    if unknown:
        raise ValueError(f"{name} contains unknown fields: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class CircuitComponent:
    """One logical component in a backend-neutral circuit design."""

    refdes: str
    kind: str
    nodes: tuple[str, ...]
    value: str | None = None
    model: str | None = None
    parameters: Mapping[str, JsonValue] = field(default_factory=dict)
    annotations: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        refdes = _require_text(self.refdes, "component.refdes", maximum=64)
        if not _REFDES.fullmatch(refdes):
            raise ValueError("component.refdes is invalid")
        kind = _require_identifier(self.kind, "component.kind")
        nodes = tuple(
            _require_text(node, "component.node", maximum=255) for node in self.nodes
        )
        if not nodes and kind.upper() != "K":
            raise ValueError("component.nodes must not be empty except for K coupling")
        value = self.value
        if value is not None:
            value = _require_text(value, "component.value", maximum=1024)
        model = self.model
        if model is not None:
            model = _require_text(model, "component.model", maximum=255)
        object.__setattr__(self, "refdes", refdes)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "model", model)
        object.__setattr__(
            self, "parameters", _freeze_mapping(self.parameters, "component.parameters")
        )
        object.__setattr__(
            self, "annotations", _freeze_mapping(self.annotations, "component.annotations")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "refdes": self.refdes,
            "kind": self.kind,
            "nodes": list(self.nodes),
            "value": self.value,
            "model": self.model,
            "parameters": _thaw_json(self.parameters),
            "annotations": _thaw_json(self.annotations),
        }

    @classmethod
    def from_dict(cls, value: object) -> "CircuitComponent":
        data = _expect_object(value, "CircuitComponent")
        allowed = {
            "refdes",
            "kind",
            "nodes",
            "value",
            "model",
            "parameters",
            "annotations",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(
                f"CircuitComponent contains unknown fields: {sorted(unknown)}"
            )
        raw_nodes = data.get("nodes")
        if not isinstance(raw_nodes, (list, tuple)):
            raise ValueError("CircuitComponent.nodes must be an array")
        parameters = data.get("parameters", {})
        annotations = data.get("annotations", {})
        return cls(
            refdes=data.get("refdes", ""),
            kind=data.get("kind", ""),
            nodes=tuple(raw_nodes),
            value=data.get("value"),
            model=data.get("model"),
            parameters=_expect_object(parameters, "CircuitComponent.parameters"),
            annotations=_expect_object(annotations, "CircuitComponent.annotations"),
        )


@dataclass(frozen=True, slots=True)
class ModelReference:
    """A traceable external or inline device-model reference."""

    name: str
    source: str
    sha256: str | None = None
    license: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_identifier(self.name, "model.name"))
        object.__setattr__(
            self, "source", _require_text(self.source, "model.source", maximum=4096)
        )
        if self.sha256 is not None and not _SHA256.fullmatch(self.sha256):
            raise ValueError("model.sha256 must be a lowercase SHA-256 digest")
        if self.license is not None:
            object.__setattr__(
                self,
                "license",
                _require_text(self.license, "model.license", maximum=255),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "sha256": self.sha256,
            "license": self.license,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ModelReference":
        data = _expect_object(value, "ModelReference")
        unknown = set(data) - {"name", "source", "sha256", "license"}
        if unknown:
            raise ValueError(f"ModelReference contains unknown fields: {sorted(unknown)}")
        return cls(
            name=data.get("name", ""),
            source=data.get("source", ""),
            sha256=data.get("sha256"),
            license=data.get("license"),
        )


@dataclass(frozen=True, slots=True)
class CircuitDesign:
    """Versioned circuit input shared by every transport and EDA backend."""

    design_id: str
    title: str
    components: tuple[CircuitComponent, ...] = ()
    nets: tuple[str, ...] = ()
    parameters: Mapping[str, JsonValue] = field(default_factory=dict)
    model_references: tuple[ModelReference, ...] = ()
    annotations: Mapping[str, JsonValue] = field(default_factory=dict)
    source_netlist: str | None = None
    revision: int = 0
    schema_version: int = EDA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EDA_SCHEMA_VERSION:
            raise ValueError(
                f"CircuitDesign schema_version must be {EDA_SCHEMA_VERSION}"
            )
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise ValueError("CircuitDesign.revision must be an integer")
        if self.revision < 0:
            raise ValueError("CircuitDesign.revision must not be negative")
        object.__setattr__(
            self, "design_id", _require_identifier(self.design_id, "design_id")
        )
        object.__setattr__(self, "title", _require_text(self.title, "title"))
        components = tuple(self.components)
        if any(not isinstance(item, CircuitComponent) for item in components):
            raise ValueError("CircuitDesign.components must contain CircuitComponent")
        duplicate_refs = _duplicates(item.refdes.lower() for item in components)
        if duplicate_refs:
            raise ValueError(f"duplicate component refdes: {sorted(duplicate_refs)}")
        nets = tuple(_require_text(net, "net", maximum=255) for net in self.nets)
        if not nets and components:
            nets = tuple(
                dict.fromkeys(node for component in components for node in component.nodes)
            )
        duplicate_nets = _duplicates(net.lower() for net in nets)
        if duplicate_nets:
            raise ValueError(f"duplicate nets: {sorted(duplicate_nets)}")
        known_nets = {net.lower() for net in nets}
        missing_nets = sorted(
            {
                node
                for component in components
                for node in component.nodes
                if node.lower() not in known_nets
            }
        )
        if missing_nets:
            raise ValueError(f"component nodes missing from CircuitDesign.nets: {missing_nets}")
        models = tuple(self.model_references)
        if any(not isinstance(item, ModelReference) for item in models):
            raise ValueError("model_references must contain ModelReference")
        duplicate_models = _duplicates(item.name.lower() for item in models)
        if duplicate_models:
            raise ValueError(f"duplicate model references: {sorted(duplicate_models)}")
        source = self.source_netlist
        if source is not None:
            if not isinstance(source, str) or not source.strip():
                raise ValueError("source_netlist must be a non-empty string")
            if "\x00" in source or len(source) > 4_000_000:
                raise ValueError("source_netlist is invalid or too long")
        if not components and source is None:
            raise ValueError("CircuitDesign requires components or source_netlist")
        object.__setattr__(self, "components", components)
        object.__setattr__(self, "nets", nets)
        object.__setattr__(self, "model_references", models)
        object.__setattr__(self, "source_netlist", source)
        object.__setattr__(
            self, "parameters", _freeze_mapping(self.parameters, "design.parameters")
        )
        object.__setattr__(
            self, "annotations", _freeze_mapping(self.annotations, "design.annotations")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "design_id": self.design_id,
            "title": self.title,
            "revision": self.revision,
            "components": [item.to_dict() for item in self.components],
            "nets": list(self.nets),
            "parameters": _thaw_json(self.parameters),
            "model_references": [item.to_dict() for item in self.model_references],
            "annotations": _thaw_json(self.annotations),
            "source_netlist": self.source_netlist,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CircuitDesign":
        data = _expect_object(value, "CircuitDesign")
        _check_schema(
            data,
            "CircuitDesign",
            frozenset(
                {
                    "schema_version",
                    "design_id",
                    "title",
                    "revision",
                    "components",
                    "nets",
                    "parameters",
                    "model_references",
                    "annotations",
                    "source_netlist",
                }
            ),
        )
        raw_components = data.get("components", [])
        raw_nets = data.get("nets", [])
        raw_models = data.get("model_references", [])
        if not isinstance(raw_components, (list, tuple)):
            raise ValueError("CircuitDesign.components must be an array")
        if not isinstance(raw_nets, (list, tuple)):
            raise ValueError("CircuitDesign.nets must be an array")
        if not isinstance(raw_models, (list, tuple)):
            raise ValueError("CircuitDesign.model_references must be an array")
        return cls(
            schema_version=data["schema_version"],
            design_id=data.get("design_id", ""),
            title=data.get("title", ""),
            revision=data.get("revision", 0),
            components=tuple(CircuitComponent.from_dict(item) for item in raw_components),
            nets=tuple(raw_nets),
            parameters=_expect_object(data.get("parameters", {}), "parameters"),
            model_references=tuple(ModelReference.from_dict(item) for item in raw_models),
            annotations=_expect_object(data.get("annotations", {}), "annotations"),
            source_netlist=data.get("source_netlist"),
        )


def _duplicates(values: Any) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


@dataclass(frozen=True, slots=True)
class PatchOperation:
    """One bounded, reviewable and reversible design change."""

    operation: str
    target: str
    before: JsonValue
    after: JsonValue
    reason: str

    def __post_init__(self) -> None:
        if self.operation not in _PATCH_OPERATIONS:
            raise ValueError(f"unsupported patch operation: {self.operation!r}")
        object.__setattr__(
            self, "target", _require_text(self.target, "patch target", maximum=255)
        )
        object.__setattr__(self, "before", _freeze_json(self.before, "patch.before"))
        object.__setattr__(self, "after", _freeze_json(self.after, "patch.after"))
        object.__setattr__(
            self, "reason", _require_text(self.reason, "patch reason", maximum=4096)
        )
        if _thaw_json(self.before) == _thaw_json(self.after):
            raise ValueError("patch operation must change the target value")

    def inverse(self) -> "PatchOperation":
        return PatchOperation(
            operation=_INVERSE_PATCH_OPERATION.get(self.operation, self.operation),
            target=self.target,
            before=self.after,
            after=self.before,
            reason=f"Revert: {self.reason}"[:4096],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "target": self.target,
            "before": _thaw_json(self.before),
            "after": _thaw_json(self.after),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: object) -> "PatchOperation":
        data = _expect_object(value, "PatchOperation")
        unknown = set(data) - {"operation", "target", "before", "after", "reason"}
        if unknown:
            raise ValueError(f"PatchOperation contains unknown fields: {sorted(unknown)}")
        return cls(
            operation=data.get("operation", ""),
            target=data.get("target", ""),
            before=data.get("before"),
            after=data.get("after"),
            reason=data.get("reason", ""),
        )


@dataclass(frozen=True, slots=True)
class DesignPatch:
    """A versioned group of minimal changes with an explicit rollback form."""

    patch_id: str
    design_id: str
    base_revision: int
    operations: tuple[PatchOperation, ...]
    description: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    schema_version: int = EDA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EDA_SCHEMA_VERSION:
            raise ValueError(f"DesignPatch schema_version must be {EDA_SCHEMA_VERSION}")
        object.__setattr__(
            self, "patch_id", _require_identifier(self.patch_id, "patch_id")
        )
        object.__setattr__(
            self, "design_id", _require_identifier(self.design_id, "design_id")
        )
        if isinstance(self.base_revision, bool) or not isinstance(self.base_revision, int):
            raise ValueError("base_revision must be an integer")
        if self.base_revision < 0:
            raise ValueError("base_revision must not be negative")
        operations = tuple(self.operations)
        if not operations or any(not isinstance(item, PatchOperation) for item in operations):
            raise ValueError("DesignPatch.operations must contain at least one PatchOperation")
        object.__setattr__(self, "operations", operations)
        object.__setattr__(
            self,
            "description",
            _require_text(self.description, "patch description", maximum=4096),
        )
        object.__setattr__(
            self, "metadata", _freeze_mapping(self.metadata, "patch.metadata")
        )

    def inverse(self, patch_id: str | None = None) -> "DesignPatch":
        return DesignPatch(
            patch_id=patch_id or _derived_identifier(self.patch_id, "revert"),
            design_id=self.design_id,
            base_revision=self.base_revision + 1,
            operations=tuple(item.inverse() for item in reversed(self.operations)),
            description=f"Revert: {self.description}"[:4096],
            metadata={"reverts_patch_id": self.patch_id},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "patch_id": self.patch_id,
            "design_id": self.design_id,
            "base_revision": self.base_revision,
            "description": self.description,
            "operations": [item.to_dict() for item in self.operations],
            "metadata": _thaw_json(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: object) -> "DesignPatch":
        data = _expect_object(value, "DesignPatch")
        _check_schema(
            data,
            "DesignPatch",
            frozenset(
                {
                    "schema_version",
                    "patch_id",
                    "design_id",
                    "base_revision",
                    "description",
                    "operations",
                    "metadata",
                }
            ),
        )
        raw_operations = data.get("operations", [])
        if not isinstance(raw_operations, (list, tuple)):
            raise ValueError("DesignPatch.operations must be an array")
        return cls(
            schema_version=data["schema_version"],
            patch_id=data.get("patch_id", ""),
            design_id=data.get("design_id", ""),
            base_revision=data.get("base_revision", 0),
            description=data.get("description", ""),
            operations=tuple(PatchOperation.from_dict(item) for item in raw_operations),
            metadata=_expect_object(data.get("metadata", {}), "metadata"),
        )


@dataclass(frozen=True, slots=True)
class Artifact:
    """One traceable file or resource produced by an EDA operation."""

    artifact_id: str
    name: str
    kind: str
    location: str
    media_type: str
    size: int
    sha256: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "artifact_id", _require_identifier(self.artifact_id, "artifact_id")
        )
        name = _require_text(self.name, "artifact.name", maximum=255)
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError("artifact.name must be a plain file/resource name")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "kind", _require_identifier(self.kind, "artifact.kind"))
        object.__setattr__(
            self,
            "location",
            _require_text(self.location, "artifact.location", maximum=8192),
        )
        object.__setattr__(
            self,
            "media_type",
            _require_text(self.media_type, "artifact.media_type", maximum=255),
        )
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise ValueError("artifact.size must be a non-negative integer")
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("artifact.sha256 must be a lowercase SHA-256 digest")
        object.__setattr__(
            self, "metadata", _freeze_mapping(self.metadata, "artifact.metadata")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "kind": self.kind,
            "location": self.location,
            "media_type": self.media_type,
            "size": self.size,
            "sha256": self.sha256,
            "metadata": _thaw_json(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: object) -> "Artifact":
        data = _expect_object(value, "Artifact")
        allowed = {
            "artifact_id",
            "name",
            "kind",
            "location",
            "media_type",
            "size",
            "sha256",
            "metadata",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"Artifact contains unknown fields: {sorted(unknown)}")
        return cls(
            artifact_id=data.get("artifact_id", ""),
            name=data.get("name", ""),
            kind=data.get("kind", ""),
            location=data.get("location", ""),
            media_type=data.get("media_type", ""),
            size=data.get("size", -1),
            sha256=data.get("sha256", ""),
            metadata=_expect_object(data.get("metadata", {}), "artifact.metadata"),
        )


@dataclass(frozen=True, slots=True)
class ArtifactSet:
    """Versioned manifest of artifacts produced for one design operation."""

    artifact_set_id: str
    design_id: str
    producer: str
    artifacts: tuple[Artifact, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    schema_version: int = EDA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EDA_SCHEMA_VERSION:
            raise ValueError(f"ArtifactSet schema_version must be {EDA_SCHEMA_VERSION}")
        object.__setattr__(
            self,
            "artifact_set_id",
            _require_identifier(self.artifact_set_id, "artifact_set_id"),
        )
        object.__setattr__(
            self, "design_id", _require_identifier(self.design_id, "design_id")
        )
        object.__setattr__(self, "producer", _require_identifier(self.producer, "producer"))
        artifacts = tuple(self.artifacts)
        if any(not isinstance(item, Artifact) for item in artifacts):
            raise ValueError("ArtifactSet.artifacts must contain Artifact")
        duplicate_ids = _duplicates(item.artifact_id for item in artifacts)
        duplicate_names = _duplicates(item.name.lower() for item in artifacts)
        if duplicate_ids:
            raise ValueError(f"duplicate artifact ids: {sorted(duplicate_ids)}")
        if duplicate_names:
            raise ValueError(f"duplicate artifact names: {sorted(duplicate_names)}")
        object.__setattr__(self, "artifacts", artifacts)
        object.__setattr__(
            self, "metadata", _freeze_mapping(self.metadata, "artifact_set.metadata")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_set_id": self.artifact_set_id,
            "design_id": self.design_id,
            "producer": self.producer,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "metadata": _thaw_json(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ArtifactSet":
        data = _expect_object(value, "ArtifactSet")
        _check_schema(
            data,
            "ArtifactSet",
            frozenset(
                {
                    "schema_version",
                    "artifact_set_id",
                    "design_id",
                    "producer",
                    "artifacts",
                    "metadata",
                }
            ),
        )
        raw_artifacts = data.get("artifacts", [])
        if not isinstance(raw_artifacts, (list, tuple)):
            raise ValueError("ArtifactSet.artifacts must be an array")
        return cls(
            schema_version=data["schema_version"],
            artifact_set_id=data.get("artifact_set_id", ""),
            design_id=data.get("design_id", ""),
            producer=data.get("producer", ""),
            artifacts=tuple(Artifact.from_dict(item) for item in raw_artifacts),
            metadata=_expect_object(data.get("metadata", {}), "artifact_set.metadata"),
        )


__all__ = [
    "EDA_SCHEMA_VERSION",
    "Artifact",
    "ArtifactSet",
    "CircuitComponent",
    "CircuitDesign",
    "DesignPatch",
    "JsonScalar",
    "JsonValue",
    "ModelReference",
    "PatchOperation",
]
