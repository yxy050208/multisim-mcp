"""Versioned component mappings for Multisim template packs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from pathlib import Path
import json

from .multisim_compat import parse_multisim_version


def validate_component_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if manifest.get("schema_version") != 1:
        raise ValueError("component manifest schema_version must be 1")
    components = manifest.get("components")
    if not isinstance(components, Sequence) or isinstance(components, (str, bytes)):
        raise ValueError("component manifest components must be a list")
    normalized: list[dict[str, Any]] = []
    for item in components:
        if not isinstance(item, Mapping):
            raise ValueError("component manifest entry must be an object")
        for field in ("logical_family", "native_name", "model_source", "pin_signature"):
            if field not in item:
                raise ValueError(f"component manifest entry requires {field}")
        pins = item["pin_signature"]
        if not isinstance(pins, Sequence) or isinstance(pins, (str, bytes)) or not pins:
            raise ValueError("pin_signature must be a non-empty list")
        versions = item.get("supported_versions")
        if versions is not None and (not isinstance(versions, Sequence) or isinstance(versions, (str, bytes))):
            raise ValueError("supported_versions must be a list when provided")
        normalized.append(dict(item))
    return {"schema_version": 1, "multisim_version": str(manifest.get("multisim_version", "")), "components": normalized}


def resolve_component_mapping(
    manifest: Mapping[str, Any],
    logical_family: str,
    requested_version: str,
    expected_pins: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Select a version-compatible mapping and report its confidence."""
    checked = validate_component_manifest(manifest)
    target = parse_multisim_version(requested_version)
    candidates = [
        item for item in checked["components"]
        if str(item["logical_family"]).casefold() == logical_family.casefold()
    ]
    target = parse_multisim_version(requested_version)
    candidates = [
        item for item in candidates
        if not item.get("supported_versions") or any(parse_multisim_version(str(v)) == target for v in item["supported_versions"])
    ]
    if expected_pins is not None:
        wanted = [str(pin).casefold() for pin in expected_pins]
        candidates = [item for item in candidates if [str(pin).casefold() for pin in item["pin_signature"]] == wanted]
    if not candidates:
        return {"status": "unavailable", "logical_family": logical_family, "requested_version": requested_version}
    # Prefer an explicitly versioned and verified mapping, then preserve
    # manifest order for deterministic fallback behavior.
    selected = sorted(candidates, key=lambda item: (
        not bool(item.get("verified", False)),
        0 if item.get("supported_versions") else 1,
    ))[0]
    mapped_versions = selected.get("supported_versions") or [checked.get("multisim_version", "")]
    status = "native-verified" if any(parse_multisim_version(str(v)) == target for v in mapped_versions) and bool(selected.get("verified", False)) else "native-unverified"
    return {"status": status, "logical_family": logical_family, "requested_version": requested_version, "mapping": selected}


def require_verified_mappings(
    manifest: Mapping[str, Any], requested_version: str,
    requirements: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """Resolve a set of logical families and fail closed on missing/unverified entries."""
    resolved: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []
    for family, pins in requirements.items():
        item = resolve_component_mapping(manifest, family, requested_version, pins)
        resolved[family] = item
        if item["status"] != "native-verified":
            failures.append({"logical_family": family, "status": item["status"]})
    if failures:
        raise ValueError(f"no verified component mapping for: {', '.join(item['logical_family'] for item in failures)}")
    return {"requested_version": requested_version, "mappings": resolved}


def load_component_manifest(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load component manifest: {source}") from exc
    return validate_component_manifest(value)


def load_manifest_for_version(directory: str | Path, version: str) -> dict[str, Any]:
    """Load the exact versioned manifest; never silently fall back."""
    target = parse_multisim_version(version)
    root = Path(directory).expanduser().resolve()
    for path in sorted(root.glob("components-*.json")):
        try:
            manifest = load_component_manifest(path)
        except ValueError:
            continue
        if parse_multisim_version(str(manifest.get("multisim_version", ""))) == target:
            return manifest
    raise ValueError(f"no component manifest is available for Multisim {version}")


def detect_multisim_version() -> str:
    """Read the installed Multisim version through a short-lived Worker."""
    from .com_worker_client import MultisimWorkerProcess, WorkerMultisimClient
    worker = MultisimWorkerProcess()
    try:
        result = WorkerMultisimClient(worker).connect()
        version = str(result.get("version", "")).strip()
        if not version:
            raise RuntimeError("Multisim did not report a version")
        return version.removeprefix("Multisim ").strip()
    finally:
        worker.close()


__all__ = ["resolve_component_mapping", "validate_component_manifest", "require_verified_mappings", "load_component_manifest", "load_manifest_for_version", "detect_multisim_version"]
