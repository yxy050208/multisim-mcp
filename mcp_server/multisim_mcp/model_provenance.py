"""Shared metadata checks; these declarations do not grant redistribution rights."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .eda_core import ModelReference


MODEL_PROVENANCE_SCHEMA_VERSION = 1
MODEL_PROVENANCE_KIND = "multisim-mcp-model-provenance"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


UNREVIEWED_MODEL_LICENSES = frozenset(
    {"", "pending", "unknown", "unreviewed", "none", "null", "unspecified", "noassertion",
     "n/a", "not-applicable", "待核对", "待确认", "未知", "未审核"}
)


def has_declared_model_license(value: object) -> bool:
    """Accept a bounded reviewed label, including local proprietary-use labels.

    This only distinguishes an actual declaration from a placeholder. It does
    not interpret legal terms or authorize publishing the associated model.
    """
    return (
        isinstance(value, str)
        and len(value) <= 255
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
        and value.strip().casefold() not in UNREVIEWED_MODEL_LICENSES
    )


def validated_model_source(source: Mapping[str, Any]) -> dict[str, str]:
    """Validate reference metadata without truncation, file reads, or downloads."""
    unknown = set(source) - {"name", "uri", "sha256", "license"}
    if unknown:
        raise ValueError(f"model_source contains unknown fields: {sorted(unknown)}")
    name = source.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("model_source.name is required")
    digest = source.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise ValueError("model_source.sha256 must be a SHA-256 digest")
    result = {"sha256": digest.lower()}
    for field, default, maximum in (("name", "", 160), ("uri", "", 300), ("license", "pending", 80)):
        value = source.get(field, default)
        if (
            not isinstance(value, str)
            or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError(f"model_source.{field} must be bounded single-line text")
        # Keep URI/license bytes as supplied so existing approval digests remain
        # stable. The source name already used strip() in the old resolver.
        result[field] = value.strip() if field == "name" else value
    return result


def _canonical_reference(value: ModelReference | Mapping[str, Any]) -> ModelReference:
    """Accept both public model-reference shapes and normalize them.

    ``CircuitDesign`` uses ``source`` while component selection requests use
    ``uri``.  They describe the same provenance fact, so this adapter keeps
    the two public contracts compatible without copying unbounded input into
    evidence artifacts.
    """
    if isinstance(value, ModelReference):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("model reference must be an object")
    if "uri" in value:
        unknown = set(value) - {"name", "uri", "sha256", "license"}
        if unknown:
            raise ValueError(f"model reference contains unknown fields: {sorted(unknown)}")
        source = dict(value)
        source["source"] = source.pop("uri")
    else:
        unknown = set(value) - {"name", "source", "sha256", "license"}
        if unknown:
            raise ValueError(f"model reference contains unknown fields: {sorted(unknown)}")
        source = dict(value)
    try:
        return ModelReference.from_dict(source)
    except ValueError as exc:
        raise ValueError(f"invalid model reference: {exc}") from exc


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_model_provenance(
    references: Sequence[ModelReference | Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Build one deterministic, manifest-safe model provenance record.

    The record contains declarations supplied by the caller only.  It does
    not read model files, download URLs, or grant redistribution rights.  A
    missing digest or license remains explicit as ``unverified``/``unknown``.
    """
    if references is None:
        values: Sequence[ModelReference | Mapping[str, Any]] = ()
    elif isinstance(references, (str, bytes)) or not isinstance(references, Sequence):
        raise ValueError("model_references must be an array")
    else:
        values = references
    if len(values) > 1024:
        raise ValueError("model_references may contain at most 1024 entries")
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values:
        reference = _canonical_reference(raw)
        key = reference.name.casefold()
        if key in seen:
            raise ValueError(f"duplicate model reference: {reference.name}")
        seen.add(key)
        digest = reference.sha256
        license_value = reference.license
        models.append(
            {
                "name": reference.name,
                "source": reference.source,
                "sha256": digest,
                "license": license_value,
                "sha256_status": "declared" if digest else "missing",
                "license_status": (
                    "declared"
                    if has_declared_model_license(license_value)
                    else "unknown"
                ),
            }
        )
    models.sort(key=lambda item: item["name"].casefold())
    complete = all(
        item["sha256_status"] == "declared"
        and item["license_status"] == "declared"
        for item in models
    )
    canonical = [
        {
            "name": item["name"],
            "source": item["source"],
            "sha256": item["sha256"],
            "license": item["license"],
        }
        for item in models
    ]
    return {
        "schema_version": MODEL_PROVENANCE_SCHEMA_VERSION,
        "kind": MODEL_PROVENANCE_KIND,
        "models": models,
        "model_count": len(models),
        "provenance_complete": complete,
        "provenance_sha256": _digest(canonical),
    }


def validate_model_provenance(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a previously persisted model provenance record."""
    if not isinstance(value, Mapping):
        raise ValueError("model_provenance must be an object")
    expected = {
        "schema_version",
        "kind",
        "models",
        "model_count",
        "provenance_complete",
        "provenance_sha256",
    }
    if set(value) != expected:
        raise ValueError("model_provenance contains unknown or missing fields")
    if value.get("schema_version") != MODEL_PROVENANCE_SCHEMA_VERSION:
        raise ValueError("model_provenance.schema_version is invalid")
    if value.get("kind") != MODEL_PROVENANCE_KIND:
        raise ValueError("model_provenance.kind is invalid")
    models = value.get("models")
    if not isinstance(models, list):
        raise ValueError("model_provenance.models must be an array")
    declarations: list[dict[str, Any]] = []
    for item in models:
        if not isinstance(item, Mapping):
            raise ValueError("model_provenance.models must contain objects")
        expected_model = {
            "name",
            "source",
            "sha256",
            "license",
            "sha256_status",
            "license_status",
        }
        if set(item) != expected_model:
            raise ValueError("model_provenance model contains unknown or missing fields")
        declarations.append(
            {
                "name": item.get("name"),
                "source": item.get("source"),
                "sha256": item.get("sha256"),
                "license": item.get("license"),
            }
        )
    normalized = build_model_provenance(declarations)
    if models != normalized["models"]:
        raise ValueError("model_provenance.models are not normalized")
    if value.get("model_count") != normalized["model_count"]:
        raise ValueError("model_provenance.model_count is invalid")
    if value.get("provenance_complete") != normalized["provenance_complete"]:
        raise ValueError("model_provenance.provenance_complete is invalid")
    digest = value.get("provenance_sha256")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise ValueError("model_provenance.provenance_sha256 is invalid")
    if digest != normalized["provenance_sha256"]:
        raise ValueError("model_provenance.provenance_sha256 does not match models")
    return normalized


__all__ = [
    "MODEL_PROVENANCE_KIND",
    "MODEL_PROVENANCE_SCHEMA_VERSION",
    "build_model_provenance",
    "has_declared_model_license",
    "validated_model_source",
    "validate_model_provenance",
]
