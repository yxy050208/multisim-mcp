"""Versioned, transport-neutral manifests for persistent workspace directories."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Final, Iterable, Mapping

from multisim_mcp import __version__


DIRECTORY_MANIFEST_SCHEMA_VERSION: Final = 1
DIRECTORY_MANIFEST_NAME: Final = "directory.manifest.json"
MAX_DIRECTORY_ARTIFACTS: Final = 20_000
MAX_MANIFEST_BYTES: Final = 8 * 1024 * 1024
MAX_METADATA_BYTES: Final = 1024 * 1024

_MANIFEST_TYPE: Final = "multisim-mcp-directory"
_DIRECTORY_KINDS: Final = frozenset({"project", "experiment", "optimization"})
_STATES: Final = frozenset(
    {"planned", "active", "running", "succeeded", "failed", "cancelled", "archived"}
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ROLE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

JsonScalar = str | int | float | bool | None
FrozenJson = JsonScalar | tuple["FrozenJson", ...] | Mapping[str, "FrozenJson"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: object, name: str, maximum: int = 4096) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized or "\x00" in normalized or len(normalized) > maximum:
        raise ValueError(f"{name} is empty, invalid, or too long")
    return normalized


def _identifier(value: object, name: str) -> str:
    normalized = _text(value, name, 128)
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{name} must be a stable identifier")
    return normalized


def _timestamp(value: object, name: str) -> str:
    normalized = _text(value, name, 64)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return normalized


def _freeze_json(value: Any, path: str = "metadata") -> FrozenJson:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJson] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or "\x00" in key:
                raise ValueError(f"{path} keys must be non-empty strings")
            frozen[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{path}[]") for item in value)
    raise ValueError(f"{path} is not JSON-compatible")


def _thaw_json(value: FrozenJson) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _relative_path(value: object) -> str:
    normalized = _text(value, "artifact.path", 4096)
    if "\\" in normalized:
        raise ValueError("artifact.path must use portable forward slashes")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("artifact.path must be a contained relative path")
    portable = path.as_posix()
    if portable == DIRECTORY_MANIFEST_NAME:
        raise ValueError("the directory manifest cannot hash itself")
    return portable


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _root_directory(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink():
        raise ValueError("workspace root must not be a symbolic link")
    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"workspace directory does not exist: {resolved}")
    return resolved


def _contained_file(root: Path, relative: str) -> Path:
    portable = _relative_path(relative)
    candidate = root
    for part in PurePosixPath(portable).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError(f"manifest artifacts must not traverse symlinks: {portable}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"manifest artifact escapes its workspace: {portable}") from exc
    if not resolved.is_file():
        raise FileNotFoundError(f"manifest artifact is missing: {portable}")
    return resolved


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        delay = 0.005
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except OSError as exc:
                transient = isinstance(exc, PermissionError) or getattr(
                    exc, "winerror", None
                ) in {5, 32, 33}
                if not transient or attempt == 7:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.08)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class DirectoryArtifact:
    """One immutable file reference stored in a directory manifest."""

    path: str
    role: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _relative_path(self.path))
        role = _text(self.role, "artifact.role", 64)
        if not _ROLE.fullmatch(role):
            raise ValueError("artifact.role must be a lowercase stable name")
        object.__setattr__(self, "role", role)
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise ValueError("artifact.size must be a non-negative integer")
        if not isinstance(self.sha256, str) or not _SHA256.fullmatch(self.sha256):
            raise ValueError("artifact.sha256 must be a lowercase SHA-256 digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "role": self.role,
            "size": self.size,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "DirectoryArtifact":
        if not isinstance(value, Mapping):
            raise ValueError("DirectoryArtifact must be an object")
        unknown = set(value) - {"path", "role", "size", "sha256"}
        if unknown:
            raise ValueError(f"DirectoryArtifact contains unknown fields: {sorted(unknown)}")
        return cls(
            path=value.get("path", ""),
            role=value.get("role", ""),
            size=value.get("size", -1),
            sha256=value.get("sha256", ""),
        )


@dataclass(frozen=True, slots=True)
class DirectoryManifest:
    """Strict schema shared by project, experiment, and optimization folders."""

    manifest_id: str
    directory_kind: str
    entity_id: str
    state: str
    revision: int
    created_at: str
    updated_at: str
    producer_version: str
    artifacts: tuple[DirectoryArtifact, ...]
    metadata: Mapping[str, FrozenJson] = field(default_factory=dict)
    schema_version: int = DIRECTORY_MANIFEST_SCHEMA_VERSION
    manifest_type: str = _MANIFEST_TYPE

    def __post_init__(self) -> None:
        if self.schema_version != DIRECTORY_MANIFEST_SCHEMA_VERSION:
            raise ValueError(
                f"DirectoryManifest schema_version must be {DIRECTORY_MANIFEST_SCHEMA_VERSION}"
            )
        if self.manifest_type != _MANIFEST_TYPE:
            raise ValueError(f"DirectoryManifest manifest_type must be {_MANIFEST_TYPE}")
        object.__setattr__(self, "manifest_id", _identifier(self.manifest_id, "manifest_id"))
        if self.directory_kind not in _DIRECTORY_KINDS:
            raise ValueError("directory_kind must be project, experiment, or optimization")
        object.__setattr__(self, "entity_id", _identifier(self.entity_id, "entity_id"))
        if self.state not in _STATES:
            raise ValueError(f"unsupported directory manifest state: {self.state!r}")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))
        object.__setattr__(
            self, "producer_version", _text(self.producer_version, "producer_version", 64)
        )
        artifacts = tuple(self.artifacts)
        if not artifacts or len(artifacts) > MAX_DIRECTORY_ARTIFACTS:
            raise ValueError(
                f"artifacts must contain between 1 and {MAX_DIRECTORY_ARTIFACTS} entries"
            )
        if any(not isinstance(item, DirectoryArtifact) for item in artifacts):
            raise ValueError("artifacts must contain DirectoryArtifact values")
        paths = [item.path.casefold() for item in artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("directory manifest contains duplicate artifact paths")
        object.__setattr__(self, "artifacts", artifacts)
        frozen = _freeze_json(self.metadata)
        assert isinstance(frozen, Mapping)
        encoded_metadata = json.dumps(
            _thaw_json(frozen), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded_metadata) > MAX_METADATA_BYTES:
            raise ValueError("metadata exceeds the size limit")
        object.__setattr__(self, "metadata", frozen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_type": self.manifest_type,
            "manifest_id": self.manifest_id,
            "directory_kind": self.directory_kind,
            "entity_id": self.entity_id,
            "state": self.state,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "producer": {"name": "multisim-mcp", "version": self.producer_version},
            "artifacts": [item.to_dict() for item in self.artifacts],
            "metadata": _thaw_json(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: object) -> "DirectoryManifest":
        if not isinstance(value, Mapping):
            raise ValueError("DirectoryManifest must be an object")
        allowed = {
            "schema_version",
            "manifest_type",
            "manifest_id",
            "directory_kind",
            "entity_id",
            "state",
            "revision",
            "created_at",
            "updated_at",
            "producer",
            "artifacts",
            "metadata",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"DirectoryManifest contains unknown fields: {sorted(unknown)}")
        producer = value.get("producer")
        if not isinstance(producer, Mapping) or set(producer) != {"name", "version"}:
            raise ValueError("producer must contain exactly name and version")
        if producer.get("name") != "multisim-mcp":
            raise ValueError("producer.name must be multisim-mcp")
        raw_artifacts = value.get("artifacts")
        if not isinstance(raw_artifacts, (list, tuple)):
            raise ValueError("artifacts must be an array")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be an object")
        return cls(
            schema_version=value.get("schema_version", -1),
            manifest_type=value.get("manifest_type", ""),
            manifest_id=value.get("manifest_id", ""),
            directory_kind=value.get("directory_kind", ""),
            entity_id=value.get("entity_id", ""),
            state=value.get("state", ""),
            revision=value.get("revision", -1),
            created_at=value.get("created_at", ""),
            updated_at=value.get("updated_at", ""),
            producer_version=producer.get("version", ""),
            artifacts=tuple(DirectoryArtifact.from_dict(item) for item in raw_artifacts),
            metadata=metadata,
        )


def _manifest_id(directory_kind: str, entity_id: str) -> str:
    digest = hashlib.sha256(f"{directory_kind}:{entity_id}".encode("utf-8")).hexdigest()[:24]
    return f"manifest-{digest}"


def write_directory_manifest(
    root: str | Path,
    *,
    directory_kind: str,
    entity_id: str,
    state: str,
    artifacts: Mapping[str, str] | Iterable[str],
    metadata: Mapping[str, Any] | None = None,
) -> DirectoryManifest:
    """Atomically create or revise a manifest from an explicit artifact allowlist."""
    workspace = _root_directory(root)
    normalized_id = _identifier(entity_id, "entity_id")
    if directory_kind not in _DIRECTORY_KINDS:
        raise ValueError("directory_kind must be project, experiment, or optimization")
    if isinstance(artifacts, Mapping):
        requested = list(artifacts.items())
    else:
        requested = [(path, "artifact") for path in artifacts]
    if not requested or len(requested) > MAX_DIRECTORY_ARTIFACTS:
        raise ValueError(
            f"artifacts must contain between 1 and {MAX_DIRECTORY_ARTIFACTS} entries"
        )
    entries: list[DirectoryArtifact] = []
    for relative, role in requested:
        portable = _relative_path(relative)
        path = _contained_file(workspace, portable)
        entries.append(
            DirectoryArtifact(
                path=portable,
                role=role,
                size=path.stat().st_size,
                sha256=_sha256_file(path),
            )
        )
    entries.sort(key=lambda item: item.path.casefold())

    path = workspace / DIRECTORY_MANIFEST_NAME
    now = _utc_now()
    revision = 0
    created_at = now
    if path.exists():
        existing = read_directory_manifest(workspace, verify=False)
        if existing.directory_kind != directory_kind or existing.entity_id != normalized_id:
            raise ValueError("existing directory manifest belongs to a different entity")
        revision = existing.revision + 1
        created_at = existing.created_at
    manifest = DirectoryManifest(
        manifest_id=_manifest_id(directory_kind, normalized_id),
        directory_kind=directory_kind,
        entity_id=normalized_id,
        state=state,
        revision=revision,
        created_at=created_at,
        updated_at=now,
        producer_version=__version__,
        artifacts=tuple(entries),
        metadata=metadata or {},
    )
    _atomic_json(path, manifest.to_dict())
    return manifest


def read_directory_manifest(
    root: str | Path, *, verify: bool = True
) -> DirectoryManifest:
    """Read a strict manifest and optionally verify every referenced artifact."""
    workspace = _root_directory(root)
    path = workspace / DIRECTORY_MANIFEST_NAME
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"directory manifest is missing: {path}")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("directory manifest exceeds the size limit")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"directory manifest is not valid JSON: {exc}") from exc
    manifest = DirectoryManifest.from_dict(payload)
    if verify:
        verify_directory_manifest(workspace, manifest)
    return manifest


def verify_directory_manifest(
    root: str | Path, manifest: DirectoryManifest | None = None
) -> None:
    """Fail closed when a manifest artifact is missing, replaced, or modified."""
    workspace = _root_directory(root)
    checked = manifest or read_directory_manifest(workspace, verify=False)
    for artifact in checked.artifacts:
        path = _contained_file(workspace, artifact.path)
        if path.stat().st_size != artifact.size:
            raise ValueError(f"manifest artifact size mismatch: {artifact.path}")
        if _sha256_file(path) != artifact.sha256:
            raise ValueError(f"manifest artifact SHA-256 mismatch: {artifact.path}")


__all__ = [
    "DIRECTORY_MANIFEST_NAME",
    "DIRECTORY_MANIFEST_SCHEMA_VERSION",
    "DirectoryArtifact",
    "DirectoryManifest",
    "read_directory_manifest",
    "verify_directory_manifest",
    "write_directory_manifest",
]
