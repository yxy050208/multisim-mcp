"""Safe MCP resource handles for completed Multisim experiments."""

from __future__ import annotations

import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Any, Final

from typing_extensions import TypedDict


class ArtifactDescriptor(TypedDict):
    """Metadata for one exported experiment artifact."""

    name: str
    filename: str
    mime_type: str
    size: int
    sha256: str
    resource_uri: str | None


class ExperimentResourceIndex(TypedDict):
    """Stable structured result returned when resources are registered."""

    success: bool
    experiment_id: str
    output_dir: str
    resources: dict[str, str]


class ExperimentResult(TypedDict):
    """Structured result of the high-level circuit experiment workflow."""

    success: bool
    experiment_id: str
    resources: dict[str, str]
    schematic: dict[str, Any]
    simulation: dict[str, Any]
    report: str
    plot: str
    output_dir: str


_RESOURCE_SCHEME: Final = "multisim://experiments"
_EXPERIMENT_ID_PATTERN: Final = re.compile(r"^exp-[0-9a-f]{24}$")
_DEFAULT_MAX_RESOURCE_BYTES: Final = 16 * 1024 * 1024

# Logical names are intentionally fixed. A model cannot turn a resource URI into
# an arbitrary local-file read by supplying path separators or a different name.
_ARTIFACTS: Final[dict[str, tuple[str, str, bool]]] = {
    "report": ("report.md", "text/markdown", False),
    "schematic": ("schematic.png", "image/png", True),
    "data": ("data.csv", "text/csv", False),
    "plot": ("plot.svg", "image/svg+xml", False),
    "netlist": ("circuit.cir", "text/x-spice", False),
    "circuit": ("circuit.ms14", "application/octet-stream", True),
    "raw": ("result.raw", "application/octet-stream", True),
    "commands": ("run.txt", "text/plain", False),
    "log": ("run.log", "text/plain", False),
}
_REQUIRED_FILES: Final = (
    "circuit.ms14",
    "circuit.ms14.xml",
    "schematic.png",
    "data.csv",
    "result.raw",
    "run.log",
    "run.txt",
    "circuit.cir",
    "plot.svg",
    "report.md",
)

_registry: dict[str, Path] = {}
_registry_lock = threading.RLock()


def _resource_limit() -> int:
    value = os.environ.get("MULTISIM_MCP_RESOURCE_MAX_BYTES", "").strip()
    if not value:
        return _DEFAULT_MAX_RESOURCE_BYTES
    try:
        limit = int(value)
    except ValueError as exc:
        raise ValueError("MULTISIM_MCP_RESOURCE_MAX_BYTES must be an integer") from exc
    if limit < 1:
        raise ValueError("MULTISIM_MCP_RESOURCE_MAX_BYTES must be positive")
    return limit


def _resource_uri(experiment_id: str, name: str) -> str:
    return f"{_RESOURCE_SCHEME}/{experiment_id}/{name}"


def _stable_experiment_id(root: Path) -> str:
    normalized = os.path.normcase(str(root.resolve()))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"exp-{digest}"


def _safe_artifact(root: Path, filename: str) -> Path:
    candidate = root / filename
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Experiment artifact escapes its output directory: {filename}"
        ) from exc
    if not resolved.is_file():
        raise FileNotFoundError(f"Experiment artifact is missing: {filename}")
    return resolved


def register_experiment(output_dir: str) -> ExperimentResourceIndex:
    """Register a complete high-level experiment and return opaque resource URIs."""
    root = Path(output_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Experiment output directory does not exist: {root}")
    for filename in _REQUIRED_FILES:
        path = _safe_artifact(root, filename)
        if path.stat().st_size <= 0:
            raise ValueError(f"Experiment artifact is empty: {filename}")

    experiment_id = _stable_experiment_id(root)
    with _registry_lock:
        _registry[experiment_id] = root
    return {
        "success": True,
        "experiment_id": experiment_id,
        "output_dir": str(root),
        "resources": {
            "manifest": _resource_uri(experiment_id, "manifest"),
            **{name: _resource_uri(experiment_id, name) for name in _ARTIFACTS},
        },
    }


def _registered_root(experiment_id: str) -> Path:
    if not _EXPERIMENT_ID_PATTERN.fullmatch(experiment_id):
        raise ValueError("Invalid experiment resource handle")
    with _registry_lock:
        root = _registry.get(experiment_id)
    if root is None:
        raise KeyError(
            "Unknown experiment resource handle; run the experiment or call "
            "register_experiment_artifacts first"
        )
    return root


def _read_bytes(experiment_id: str, name: str) -> bytes:
    definition = _ARTIFACTS.get(name)
    if definition is None:
        raise ValueError(f"Unknown experiment artifact: {name}")
    filename, _, _ = definition
    path = _safe_artifact(_registered_root(experiment_id), filename)
    size = path.stat().st_size
    limit = _resource_limit()
    if size > limit:
        raise ValueError(
            f"Experiment artifact exceeds the resource limit ({size} > {limit} bytes)"
        )
    return path.read_bytes()


def read_text_artifact(experiment_id: str, name: str) -> str:
    """Read one allowlisted text artifact through a registered handle."""
    definition = _ARTIFACTS.get(name)
    if definition is None or definition[2]:
        raise ValueError(f"Artifact is not an allowlisted text resource: {name}")
    return _read_bytes(experiment_id, name).decode("utf-8", errors="replace")


def read_binary_artifact(experiment_id: str, name: str) -> bytes:
    """Read one allowlisted binary artifact through a registered handle."""
    definition = _ARTIFACTS.get(name)
    if definition is None or not definition[2]:
        raise ValueError(f"Artifact is not an allowlisted binary resource: {name}")
    return _read_bytes(experiment_id, name)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def experiment_manifest(experiment_id: str) -> dict[str, Any]:
    """Return hashes and resource links for a registered experiment."""
    root = _registered_root(experiment_id)
    artifacts: list[ArtifactDescriptor] = []
    logical_by_filename = {
        filename: (name, mime_type)
        for name, (filename, mime_type, _) in _ARTIFACTS.items()
    }
    for filename in _REQUIRED_FILES:
        path = _safe_artifact(root, filename)
        logical = logical_by_filename.get(filename)
        artifacts.append(
            {
                "name": logical[0] if logical else filename,
                "filename": filename,
                "mime_type": logical[1] if logical else "application/xml",
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
                "resource_uri": (
                    _resource_uri(experiment_id, logical[0]) if logical else None
                ),
            }
        )
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "artifacts": artifacts,
    }


def clear_experiment_registry() -> None:
    """Clear process-local handles for isolated tests."""
    with _registry_lock:
        _registry.clear()
