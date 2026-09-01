"""Read-only, bounded project/workspace inspection for future UIs.

The inspector deliberately consumes only versioned ``directory.manifest.json``
files.  It never infers that an unmanifested directory is an experiment and it
continues past a corrupt child manifest so a UI can show the damaged entry and
the rest of the project at the same time.
"""

from __future__ import annotations

import os
from collections import Counter, deque
from pathlib import Path
from typing import Any

from .simulation_approvals import validate_experiment_approval_provenance
from .workspace_manifest import (
    DIRECTORY_MANIFEST_NAME,
    DirectoryManifest,
    read_directory_manifest,
)


PROJECT_INSPECTION_SCHEMA_VERSION = 1
DEFAULT_MAX_ENTRIES = 256
MAX_MAX_ENTRIES = 2_048
DEFAULT_MAX_DEPTH = 5
MAX_MAX_DEPTH = 8
MAX_SCANNED_DIRECTORIES = 16_384
MAX_ARTIFACT_PREVIEW = 256
MAX_ERROR_CHARS = 512
_SKIPPED_DIRECTORY_NAMES = frozenset({".git", ".hg", ".svn", "__pycache__"})


def _root_directory(root: str | Path) -> Path:
    candidate = Path(root).expanduser()
    if candidate.is_symlink():
        raise ValueError("project root must not be a symbolic link")
    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"project directory does not exist: {resolved}")
    return resolved


def _bounded_text(value: object, maximum: int = MAX_ERROR_CHARS) -> str:
    text = str(value)
    return text if len(text) <= maximum else text[: maximum - 1] + "…"


def _artifact_preview(manifest: DirectoryManifest) -> tuple[list[dict[str, Any]], bool]:
    items = [
        {
            "path": item.path,
            "role": item.role,
            "size": item.size,
            "sha256": item.sha256,
        }
        for item in manifest.artifacts[:MAX_ARTIFACT_PREVIEW]
    ]
    return items, len(manifest.artifacts) > len(items)


def _approval_provenance_view(
    manifest: DirectoryManifest,
) -> tuple[dict[str, Any] | None, str]:
    """Expose only a validated, manifest-safe approval identity."""
    raw = manifest.metadata.get("approval_provenance")
    if raw is None:
        return None, "absent"
    try:
        return validate_experiment_approval_provenance(raw), "verified"
    except (TypeError, ValueError):
        return None, "invalid"


def _manifest_entry(
    root: Path,
    directory: Path,
    *,
    verify: bool,
) -> dict[str, Any]:
    relative = directory.relative_to(root).as_posix() or "."
    try:
        manifest = read_directory_manifest(directory, verify=verify)
    except (OSError, ValueError) as exc:
        return {
            "path": relative,
            "integrity_status": "invalid",
            "error": {
                "type": type(exc).__name__,
                "message": _bounded_text(exc),
            },
        }

    artifacts, artifacts_truncated = _artifact_preview(manifest)
    roles = Counter(item.role for item in manifest.artifacts)
    approval_provenance, approval_provenance_status = _approval_provenance_view(
        manifest
    )
    return {
        "path": relative,
        "integrity_status": "verified" if verify else "loaded-without-verification",
        "manifest_id": manifest.manifest_id,
        "directory_kind": manifest.directory_kind,
        "entity_id": manifest.entity_id,
        "state": manifest.state,
        "revision": manifest.revision,
        "created_at": manifest.created_at,
        "updated_at": manifest.updated_at,
        "producer_version": manifest.producer_version,
        "artifact_count": len(manifest.artifacts),
        "artifact_bytes": sum(item.size for item in manifest.artifacts),
        "artifact_roles": dict(sorted(roles.items())),
        "artifacts": artifacts,
        "artifacts_truncated": artifacts_truncated,
        "metadata_keys": sorted(str(key) for key in manifest.metadata),
        "approval_provenance": approval_provenance,
        "approval_provenance_status": approval_provenance_status,
    }


def _validate_limits(max_entries: int, max_depth: int) -> tuple[int, int]:
    if isinstance(max_entries, bool) or not isinstance(max_entries, int):
        raise ValueError("max_entries must be an integer")
    if not 1 <= max_entries <= MAX_MAX_ENTRIES:
        raise ValueError(f"max_entries must be between 1 and {MAX_MAX_ENTRIES}")
    if isinstance(max_depth, bool) or not isinstance(max_depth, int):
        raise ValueError("max_depth must be an integer")
    if not 0 <= max_depth <= MAX_MAX_DEPTH:
        raise ValueError(f"max_depth must be between 0 and {MAX_MAX_DEPTH}")
    return max_entries, max_depth


def inspect_project(
    root: str | Path,
    *,
    verify: bool = True,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> dict[str, Any]:
    """Return a bounded, read-only snapshot of manifest-backed project folders.

    ``root`` itself is included when it contains a manifest.  Child folders are
    discovered without following symbolic links.  Invalid child manifests are
    reported as entries instead of aborting the whole snapshot.
    """

    max_entries, max_depth = _validate_limits(max_entries, max_depth)
    workspace = _root_directory(root)
    directories: list[Path] = []
    queue: deque[tuple[Path, int]] = deque([(workspace, 0)])
    truncated = False
    scanned_directory_count = 0
    visited: set[str] = set()

    while queue:
        directory, depth = queue.popleft()
        scanned_directory_count += 1
        if scanned_directory_count > MAX_SCANNED_DIRECTORIES:
            truncated = True
            break
        try:
            resolved = directory.resolve()
            key = os.path.normcase(str(resolved))
        except OSError:
            continue
        if key in visited:
            continue
        visited.add(key)
        if (directory / DIRECTORY_MANIFEST_NAME).is_file():
            directories.append(directory)
            if len(directories) >= max_entries:
                truncated = bool(queue)
                break
        if depth >= max_depth:
            continue
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue
            if child.name in _SKIPPED_DIRECTORY_NAMES:
                continue
            queue.append((child, depth + 1))

    entries = [
        _manifest_entry(workspace, directory, verify=verify)
        for directory in sorted(
            directories,
            key=lambda item: item.relative_to(workspace).as_posix().casefold(),
        )
    ]
    valid_entries = [item for item in entries if item["integrity_status"] != "invalid"]
    kind_counts = Counter(
        str(item.get("directory_kind")) for item in valid_entries if item.get("directory_kind")
    )
    state_counts = Counter(
        str(item.get("state")) for item in valid_entries if item.get("state")
    )
    integrity_counts = Counter(str(item["integrity_status"]) for item in entries)
    root_entry = next((item for item in entries if item["path"] == "."), None)
    result = {
        "schema_version": PROJECT_INSPECTION_SCHEMA_VERSION,
        "workspace_root": str(workspace),
        "root_manifest_present": root_entry is not None,
        "root_manifest": root_entry,
        "entries": entries,
        "summary": {
            "manifest_count": len(entries),
            "scanned_directory_count": scanned_directory_count,
            "verified_count": integrity_counts.get("verified", 0),
            "invalid_count": integrity_counts.get("invalid", 0),
            "kind_counts": dict(sorted(kind_counts.items())),
            "state_counts": dict(sorted(state_counts.items())),
            "integrity_counts": dict(sorted(integrity_counts.items())),
        },
        "limits": {
            "max_entries": max_entries,
            "max_depth": max_depth,
            "truncated": truncated,
            "verification_enabled": verify,
        },
    }
    result["success"] = result["summary"]["invalid_count"] == 0
    return result


__all__ = [
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_ENTRIES",
    "MAX_MAX_DEPTH",
    "MAX_MAX_ENTRIES",
    "MAX_SCANNED_DIRECTORIES",
    "PROJECT_INSPECTION_SCHEMA_VERSION",
    "inspect_project",
]
