"""Versioned component mappings for Multisim template packs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from pathlib import Path
import hashlib
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
        for field in ("logical_family", "native_name", "model_source"):
            value = item[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"component manifest {field} must be a non-empty string")
        pins = item["pin_signature"]
        if not isinstance(pins, Sequence) or isinstance(pins, (str, bytes)) or not pins:
            raise ValueError("pin_signature must be a non-empty list")
        if any(not isinstance(pin, str) or not pin.strip() for pin in pins):
            raise ValueError("pin_signature entries must be non-empty strings")
        versions = item.get("supported_versions")
        if versions is not None and (not isinstance(versions, Sequence) or isinstance(versions, (str, bytes))):
            raise ValueError("supported_versions must be a list when provided")
        if versions is not None and any(not isinstance(version, str) or not version.strip() for version in versions):
            raise ValueError("supported_versions entries must be non-empty strings")
        if "verified" in item and not isinstance(item["verified"], bool):
            raise ValueError("verified must be a boolean when provided")
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
    candidates = [
        item for item in candidates
        if not item.get("supported_versions")
        or any(parse_multisim_version(str(v)) == target for v in item["supported_versions"])
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


def resolve_connector_mapping(
    manifest: Mapping[str, Any],
    connector: Mapping[str, Any],
    requested_version: str,
) -> dict[str, Any]:
    """Resolve a physical connector against an exact Multisim manifest.

    Connector contracts use the physical part number and pin numbers, while
    compatibility manifests use logical families and pin signatures.  This
    adapter deliberately matches both the part identifier and the complete
    pin sequence; a generic two-pin carrier is never silently accepted for a
    different connector.  Until a manifest contains a verified entry the
    result is ``unavailable`` or ``native-unverified``.
    """
    checked = validate_component_manifest(manifest)
    if not isinstance(connector, Mapping):
        raise ValueError("connector must be an object")
    part = str(connector.get("part", "")).strip()
    if not part:
        raise ValueError("connector.part is required")
    raw_pins = connector.get("pins")
    if not isinstance(raw_pins, Sequence) or isinstance(raw_pins, (str, bytes)) or not raw_pins:
        raise ValueError("connector.pins must be a non-empty list")
    numbers = [item.get("number") for item in raw_pins if isinstance(item, Mapping)]
    if len(numbers) != len(raw_pins) or any(isinstance(item, bool) or not isinstance(item, int) for item in numbers):
        raise ValueError("connector pins require integer numbers")
    expected_pins = [str(number) for number in sorted(numbers)]
    target = parse_multisim_version(requested_version)
    candidates: list[dict[str, Any]] = []
    for item in checked["components"]:
        family = str(item.get("logical_family", "")).casefold()
        native_name = str(item.get("native_name", "")).casefold()
        part_number = str(item.get("part_number", "")).casefold()
        if family not in {"connector", f"connector:{part.casefold()}"} and native_name != part.casefold() and part_number != part.casefold():
            continue
        versions = item.get("supported_versions") or [checked.get("multisim_version", "")]
        if not any(parse_multisim_version(str(version)) == target for version in versions):
            continue
        if [str(pin).casefold() for pin in item["pin_signature"]] != [pin.casefold() for pin in expected_pins]:
            continue
        candidates.append(item)
    if not candidates:
        return {
            "status": "unavailable",
            "part": part,
            "requested_version": requested_version,
            "expected_pins": expected_pins,
        }
    selected = sorted(candidates, key=lambda item: not bool(item.get("verified", False)))[0]
    return {
        "status": "native-verified" if bool(selected.get("verified", False)) else "native-unverified",
        "part": part,
        "requested_version": requested_version,
        "expected_pins": expected_pins,
        "mapping": selected,
    }


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


def build_component_mapping_matrix(
    directory: str | Path, target_versions: Sequence[str],
) -> dict[str, Any]:
    """Build an exact-version component mapping matrix without fallbacks.

    A matrix entry records the manifest and each declared logical mapping when
    an exact manifest exists.  A known version without its own manifest stays
    ``manifest-unavailable``; an unparseable version is ``unsupported``.  No
    neighboring manifest is copied into either case.
    """
    if (
        not isinstance(target_versions, Sequence)
        or isinstance(target_versions, (str, bytes))
    ):
        raise ValueError("target_versions must be a non-empty list")
    targets = [str(item).strip() for item in target_versions if str(item).strip()]
    if not targets:
        raise ValueError("target_versions must be a non-empty list")
    root = Path(directory).expanduser().resolve()
    manifest_files: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.glob("components-*.json")):
        try:
            manifest_files.append((path, load_component_manifest(path)))
        except ValueError:
            continue
    available_versions = sorted({
        str(manifest.get("multisim_version", ""))
        for _, manifest in manifest_files
        if str(manifest.get("multisim_version", "")).strip()
    })
    entries: list[dict[str, Any]] = []
    for requested in dict.fromkeys(targets):
        target_tuple = parse_multisim_version(requested)
        if target_tuple == (0, 0, 0):
            entries.append({
                "requested_version": requested,
                "requested_version_tuple": list(target_tuple),
                "status": "unsupported",
                "verification_scope": None,
                "reason": "version string is not recognized",
                "manifest": None,
                "mappings": [],
            })
            continue
        selected: tuple[Path, dict[str, Any]] | None = next(
            (
                (path, manifest)
                for path, manifest in manifest_files
                if parse_multisim_version(str(manifest.get("multisim_version", ""))) == target_tuple
            ),
            None,
        )
        if selected is None:
            entries.append({
                "requested_version": requested,
                "requested_version_tuple": list(target_tuple),
                "status": "manifest-unavailable",
                "verification_scope": None,
                "reason": "no exact component manifest is available",
                "manifest": None,
                "mappings": [],
            })
            continue
        path, manifest = selected
        raw_mappings = []
        for item in manifest["components"]:
            versions = list(item.get("supported_versions") or [manifest.get("multisim_version", "")])
            raw_mappings.append({
                "logical_family": item["logical_family"],
                "native_name": item["native_name"],
                "model_source": item["model_source"],
                "pin_signature": list(item["pin_signature"]),
                "supported_versions": versions,
                "verified": item.get("verified") is True,
                "status": "native-verified" if item.get("verified") is True else "native-unverified",
                "template": item.get("template"),
            })
        content = path.read_bytes()
        entries.append({
            "requested_version": requested,
            "requested_version_tuple": list(target_tuple),
            "status": "exact-manifest",
            "verification_scope": "component-manifest-only",
            "reason": "exact component manifest loaded; native execution remains separate",
            "manifest": {
                "file": path.name,
                "multisim_version": manifest.get("multisim_version"),
                "sha256": hashlib.sha256(content).hexdigest(),
                "component_count": len(raw_mappings),
                "verified_component_count": sum(item["verified"] for item in raw_mappings),
            },
            "mappings": raw_mappings,
        })
    return {
        "schema_version": 1,
        "kind": "multisim-component-mapping-matrix",
        "available_manifest_versions": available_versions,
        "entries": entries,
        "exact_manifest_versions": [
            item["requested_version"] for item in entries if item["status"] == "exact-manifest"
        ],
        "manifest_unavailable_versions": [
            item["requested_version"] for item in entries if item["status"] == "manifest-unavailable"
        ],
        "unsupported_versions": [
            item["requested_version"] for item in entries if item["status"] == "unsupported"
        ],
        "scope": "Exact versioned mappings only; no neighboring-version fallback or native execution claim.",
    }


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


__all__ = [
    "build_component_mapping_matrix",
    "resolve_component_mapping",
    "resolve_connector_mapping",
    "validate_component_manifest",
    "require_verified_mappings",
    "load_component_manifest",
    "load_manifest_for_version",
    "detect_multisim_version",
]
