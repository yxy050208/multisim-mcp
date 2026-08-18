"""Safe MCP resource handles for completed Multisim experiments."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Final

from typing_extensions import TypedDict

from multisim_mcp.spice_raw import parse_raw, summarize_columns


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


class VerifiedExperimentResult(ExperimentResult):
    """High-level result with persisted machine-readable requirement verdicts."""

    verification: dict[str, Any]
    verification_path: str


_RESOURCE_SCHEME: Final = "multisim://experiments"
_EXPERIMENT_ID_PATTERN: Final = re.compile(r"^exp-[0-9a-f]{24}$")
_DEFAULT_MAX_RESOURCE_BYTES: Final = 16 * 1024 * 1024
_MAX_TEXT_PAGE_CHARS: Final = 100_000
ARTIFACT_EXPORT_DIR_ENV: Final = "MULTISIM_MCP_ARTIFACT_EXPORT_DIR"

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
    "verification": ("verification.json", "application/json", False),
    "formal_html_zh": ("report.zh-CN.html", "text/html", False),
    "formal_html_en": ("report.en.html", "text/html", False),
    "formal_pdf_zh": ("report.zh-CN.pdf", "application/pdf", True),
    "formal_pdf_en": ("report.en.pdf", "application/pdf", True),
    "reproducibility_manifest": ("manifest.json", "application/json", False),
}
_RESOURCE_PATHS: Final[dict[str, str]] = {
    "formal_html_zh": "formal-html-zh",
    "formal_html_en": "formal-html-en",
    "formal_pdf_zh": "formal-pdf-zh",
    "formal_pdf_en": "formal-pdf-en",
    "reproducibility_manifest": "reproducibility-manifest",
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
    return f"{_RESOURCE_SCHEME}/{experiment_id}/{_RESOURCE_PATHS.get(name, name)}"


def _stable_experiment_id(root: Path) -> str:
    normalized = os.path.normcase(str(root.resolve()))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"exp-{digest}"


def experiment_id_for_output_dir(output_dir: str | Path) -> str:
    """Return the stable opaque ID without registering or reading artifacts."""
    return _stable_experiment_id(Path(output_dir).expanduser().resolve())


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
    available = {
        name: definition
        for name, definition in _ARTIFACTS.items()
        if (root / definition[0]).is_file()
    }
    return {
        "success": True,
        "experiment_id": experiment_id,
        "output_dir": str(root),
        "resources": {
            "manifest": _resource_uri(experiment_id, "manifest"),
            **{name: _resource_uri(experiment_id, name) for name in available},
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


def registered_experiment_root(experiment_id: str) -> Path:
    """Resolve an opaque handle for internal measurement tools."""
    return _registered_root(experiment_id)


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
    filenames = [*_REQUIRED_FILES]
    for filename, _, _ in _ARTIFACTS.values():
        if filename not in filenames and (root / filename).is_file():
            filenames.append(filename)
    for filename in filenames:
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


def list_artifacts(experiment_id: str) -> dict[str, Any]:
    """List registered artifacts with bounded-read and export capabilities."""
    manifest = experiment_manifest(experiment_id)
    artifacts: list[dict[str, Any]] = []
    for descriptor in manifest["artifacts"]:
        definition = _ARTIFACTS.get(descriptor["name"])
        artifacts.append(
            {
                **descriptor,
                "text_readable": bool(definition and not definition[2]),
                "exportable": definition is not None,
            }
        )
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "artifact_count": len(artifacts),
        "total_size": sum(int(item["size"]) for item in artifacts),
        "artifacts": artifacts,
    }


def read_artifact_page(
    experiment_id: str,
    name: str,
    offset: int = 0,
    max_chars: int = 20_000,
) -> dict[str, Any]:
    """Read a bounded character page from an allowlisted text artifact."""
    if offset < 0:
        raise ValueError("offset must not be negative")
    if max_chars < 1 or max_chars > _MAX_TEXT_PAGE_CHARS:
        raise ValueError(
            f"max_chars must be between 1 and {_MAX_TEXT_PAGE_CHARS}"
        )
    content = read_text_artifact(experiment_id, name)
    page = content[offset : offset + max_chars]
    next_offset = offset + len(page)
    truncated = next_offset < len(content)
    definition = _ARTIFACTS[name]
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "name": name,
        "mime_type": definition[1],
        "offset": offset,
        "next_offset": next_offset if truncated else None,
        "total_chars": len(content),
        "truncated": truncated,
        "content": page,
    }


def export_artifact(
    experiment_id: str,
    name: str,
    destination_subdir: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Copy one allowlisted artifact beneath an explicitly approved root."""
    definition = _ARTIFACTS.get(name)
    if definition is None:
        raise ValueError(f"Unknown experiment artifact: {name}")
    configured_root = os.environ.get(ARTIFACT_EXPORT_DIR_ENV, "").strip()
    if not configured_root:
        raise RuntimeError(
            f"Set {ARTIFACT_EXPORT_DIR_ENV} to an approved export directory"
        )
    export_root = Path(configured_root).expanduser().resolve()
    if export_root == Path(export_root.anchor):
        raise ValueError("artifact export directory must not be a filesystem root")

    relative = Path(destination_subdir or ".")
    if relative.is_absolute():
        raise ValueError("destination_subdir must be relative to the export directory")
    destination_dir = (export_root / relative).resolve()
    try:
        destination_dir.relative_to(export_root)
    except ValueError as exc:
        raise ValueError("destination_subdir escapes the export directory") from exc
    destination_dir.mkdir(parents=True, exist_ok=True)

    source = _safe_artifact(_registered_root(experiment_id), definition[0])
    destination = destination_dir / definition[0]
    if source == destination:
        raise ValueError("artifact source and export destination must differ")
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise ValueError("artifact export destination must be a regular file")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"artifact export destination exists: {destination}")

    temporary = destination_dir / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "schema_version": 1,
        "success": True,
        "experiment_id": experiment_id,
        "name": name,
        "mime_type": definition[1],
        "size": destination.stat().st_size,
        "sha256": _sha256_file(destination),
        "destination": str(destination),
    }


def summarize_experiment(experiment_id: str) -> dict[str, Any]:
    """Return a compact Tool-friendly summary without binary artifact content."""
    listing = list_artifacts(experiment_id)
    report = read_artifact_page(experiment_id, "report", max_chars=4_000)
    artifact_names = {item["name"] for item in listing["artifacts"]}
    root = _registered_root(experiment_id)
    raw_path = _safe_artifact(root, _ARTIFACTS["raw"][0])
    measurement_summary: dict[str, Any]
    if raw_path.stat().st_size > _resource_limit():
        measurement_summary = {
            "available": False,
            "error": "raw artifact exceeds the configured resource limit",
        }
    else:
        try:
            parsed_raw = parse_raw(str(raw_path))
            columns = summarize_columns(parsed_raw)
            measurement_summary = {
                "available": True,
                "plotname": parsed_raw.get("header", {}).get("plotname", ""),
                "point_count": parsed_raw.get("n_points", 0),
                "column_count": len(columns),
                "columns": columns[:64],
                "columns_truncated": len(columns) > 64,
            }
        except (OSError, ValueError) as exc:
            measurement_summary = {
                "available": False,
                "error": str(exc)[:500],
            }
    verification: dict[str, Any] | None = None
    if "verification" in artifact_names:
        text = read_text_artifact(experiment_id, "verification")
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("verification root must be an object")
            raw_requirements = parsed.get("requirements", [])
            requirements = raw_requirements if isinstance(raw_requirements, list) else []
            compact_requirements: list[dict[str, Any]] = []
            allowed_fields = (
                "id",
                "status",
                "value",
                "unit",
                "operator",
                "target",
                "lower",
                "upper",
                "reason",
            )
            for raw in requirements[:25]:
                if not isinstance(raw, dict):
                    continue
                item = {key: raw[key] for key in allowed_fields if key in raw}
                for key, value in list(item.items()):
                    if not isinstance(value, (str, int, float, bool, type(None))):
                        del item[key]
                    elif isinstance(value, str) and len(value) > 500:
                        item[key] = value[:500] + "..."
                compact_requirements.append(item)
            raw_counts = parsed.get("counts")
            compact_counts = (
                {
                    key: value
                    for key in ("pass", "fail", "unverified")
                    if isinstance((value := raw_counts.get(key)), int)
                }
                if isinstance(raw_counts, dict)
                else None
            )
            schema_version = parsed.get("schema_version")
            overall_status = parsed.get("overall_status")
            verification = {
                "available": True,
                "valid_json": True,
                "result": {
                    "schema_version": (
                        schema_version if isinstance(schema_version, int) else None
                    ),
                    "overall_status": (
                        str(overall_status)[:50]
                        if overall_status is not None
                        else None
                    ),
                    "counts": compact_counts,
                    "requirement_count": len(requirements),
                    "requirements": compact_requirements,
                    "requirements_truncated": len(requirements) > 25,
                },
            }
        except (json.JSONDecodeError, ValueError) as exc:
            verification = {
                "available": True,
                "valid_json": False,
                "error": str(exc),
            }
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "artifact_count": listing["artifact_count"],
        "total_size": listing["total_size"],
        "report_excerpt": report["content"],
        "report_truncated": report["truncated"],
        "measurements": measurement_summary,
        "verification": verification or {"available": False},
        "artifacts": [
            {
                "name": item["name"],
                "mime_type": item["mime_type"],
                "size": item["size"],
                "sha256": item["sha256"],
            }
            for item in listing["artifacts"]
        ],
    }


def clear_experiment_registry() -> None:
    """Clear process-local handles for isolated tests."""
    with _registry_lock:
        _registry.clear()
