"""Static SPICE dialect, model-provenance, and solver-evidence auditing.

The audit is deliberately conservative.  SPICE syntax is a family of related
dialects, so a static pass must not claim that a deck is portable merely because
it parses.  Unknown model licenses, missing model content, and solver versions
that were not captured remain explicit evidence gaps in the returned record.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .eda_core import ModelReference
from .safety import validate_spice_netlist


SPICE_COMPATIBILITY_SCHEMA_VERSION = 1
_EXECUTED_NETLIST_MAX_BYTES = 4_000_000
_BACKEND_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_DIALECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+ /-]{0,127}$")
_SOLVER_VERSION_PATTERNS = {
    "ngspice": (
        re.compile(r"(?i)\bngspice[-\s]+(?:revision\s+)?([0-9]+(?:\.[0-9]+)*)"),
        re.compile(r"(?i)\bversion\s+([0-9]+(?:\.[0-9]+)*)"),
    ),
    "multisim": (
        re.compile(r"(?i)\bmultisim(?:\s+(?:version|release))?\s+([0-9]+(?:\.[0-9]+)*)"),
    ),
}
_LICENSE_UNKNOWN = frozenset({"", "unknown", "noassertion", "none", "unspecified"})


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_executed_netlist(value: str) -> None:
    """Validate bounded backend output without applying source-input policy.

    Backends wrap the source in a solver deck containing directives such as
    ``.control``, ``.save``, and ``.endc``. That generated deck is audited and
    hashed, never executed by this function, so the user-input safety allowlist
    is intentionally not applied a second time here.
    """
    if not value.strip():
        raise ValueError("executed_netlist must not be empty")
    if len(value.encode("utf-8")) > _EXECUTED_NETLIST_MAX_BYTES:
        raise ValueError("executed_netlist exceeds the 4 MB safety limit")
    if "\x00" in value:
        raise ValueError("executed_netlist must not contain NUL bytes")


def _logical_records(text: str) -> list[tuple[int, str]]:
    records: list[tuple[int, str]] = []
    current_line: int | None = None
    current = ""
    for line_number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("+") and current_line is not None:
            current += " " + stripped[1:].lstrip()
            continue
        if current_line is not None:
            records.append((current_line, current))
        current_line = line_number
        current = stripped
    if current_line is not None:
        records.append((current_line, current))
    return records


def _diagnostic(
    diagnostics: list[dict[str, Any]],
    code: str,
    severity: str,
    category: str,
    message: str,
    *,
    line: int | None = None,
    model: str | None = None,
) -> None:
    item: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "category": category,
        "message": message,
    }
    if line is not None:
        item["line"] = line
    if model is not None:
        item["model"] = model
    diagnostics.append(item)


def _normalize_model_name(value: str) -> str:
    lowered = value.strip().casefold()
    for prefix in ("model:", "subckt:"):
        if lowered.startswith(prefix):
            return lowered[len(prefix) :]
    return lowered


def _model_references(
    values: Sequence[ModelReference | Mapping[str, Any]] | None,
) -> tuple[ModelReference, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("model_references must be an array")
    if len(values) > 1024:
        raise ValueError("model_references may contain at most 1024 entries")
    references: list[ModelReference] = []
    seen: set[str] = set()
    for value in values:
        reference = (
            value if isinstance(value, ModelReference) else ModelReference.from_dict(value)
        )
        normalized = reference.name.casefold()
        if normalized in seen:
            raise ValueError(f"duplicate model reference: {reference.name}")
        seen.add(normalized)
        references.append(reference)
    return tuple(references)


def _inline_definitions(records: list[tuple[int, str]]) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    for line_number, record in records:
        lowered = record.casefold()
        if lowered.startswith(".model"):
            parts = record.split(maxsplit=2)
            if len(parts) >= 3:
                model_type = parts[2].split("(", 1)[0].split()[0]
                definitions.append(
                    {
                        "name": f"model:{parts[1]}",
                        "model_name": parts[1],
                        "kind": "model",
                        "model_type": model_type,
                        "line": line_number,
                        "definition_sha256": _sha256_text(record),
                        "hash_scope": "logical-spice-definition-v1",
                        "definition_status": "embedded",
                    }
                )
    for start, (line_number, record) in enumerate(records):
        lowered = record.casefold()
        if lowered.startswith(".subckt"):
            parts = record.split()
            name = parts[1] if len(parts) >= 2 else "unknown"
            depth = 0
            block: list[str] = []
            index = start
            while index < len(records):
                _, nested = records[index]
                nested_lowered = nested.casefold()
                block.append(nested)
                if nested_lowered.startswith(".subckt"):
                    depth += 1
                elif nested_lowered.startswith(".ends"):
                    depth -= 1
                    if depth == 0:
                        index += 1
                        break
                index += 1
            text = "\n".join(block)
            definitions.append(
                {
                    "name": f"subckt:{name}",
                    "model_name": name,
                    "kind": "subcircuit",
                    "model_type": "subckt",
                    "line": line_number,
                    "definition_sha256": _sha256_text(text),
                    "hash_scope": "logical-spice-definition-v1",
                    "definition_status": "embedded",
                }
            )
    return definitions


def _reference_from_device(record: str) -> tuple[str, str] | None:
    parts = record.split()
    if len(parts) < 2 or not parts[0] or parts[0][0] in "*;#.\u0000":
        return None
    kind = parts[0][0].upper()
    positions = {"D": 3, "J": 4, "Z": 4, "M": 5, "S": 5, "W": 4}
    if kind in positions and len(parts) > positions[kind]:
        return "model", parts[positions[kind]]
    if kind == "Q" and len(parts) >= 5:
        # Q C B E MODEL or Q C B E SUBSTRATE MODEL.
        return "model", parts[4] if len(parts) == 5 else parts[5]
    if kind in {"A", "O", "U"} and len(parts) >= 3:
        return "model", parts[-1]
    if kind == "X" and len(parts) >= 3:
        candidates = [
            token
            for token in parts[1:]
            if "=" not in token and token.casefold() not in {"params:"}
        ]
        if candidates:
            return "subckt", candidates[-1]
    return None


def _feature_inventory(
    records: list[tuple[int, str]], backend_id: str, diagnostics: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    features: set[str] = set()
    markers: set[str] = set()
    device_kinds: set[str] = set()
    for line_number, record in records:
        if not record or record.startswith(("*", ";", "#")):
            continue
        lowered = record.casefold()
        if lowered.startswith("."):
            directive = lowered.split(maxsplit=1)[0]
            feature = {
                ".model": "inline-model",
                ".subckt": "subcircuit",
                ".func": "expression-function",
                ".if": "conditional-netlist",
                ".elseif": "conditional-netlist",
                ".else": "conditional-netlist",
                ".endif": "conditional-netlist",
                ".param": "parameters",
                ".options": "solver-options",
                ".temp": "temperature",
                ".global": "global-nodes",
                ".ic": "initial-condition",
                ".nodeset": "node-set",
                ".protect": "protected-model-block",
                ".unprotect": "protected-model-block",
            }.get(directive)
            if feature:
                features.add(feature)
            if directive in {".protect", ".unprotect"}:
                markers.add("vendor-protected-library")
                _diagnostic(
                    diagnostics,
                    "protected-model-block",
                    "warning",
                    "compatibility",
                    "Protected model-block semantics vary across SPICE engines.",
                    line=line_number,
                )
            if directive in {".if", ".elseif", ".else", ".endif"}:
                markers.add("conditional-spice")
            continue
        kind = record[0].upper()
        device_kinds.add(kind)
        if kind == "B":
            features.add("behavioral-source")
        elif kind in {"E", "F", "G", "H"}:
            features.add("controlled-source")
        elif kind == "A":
            features.add("xspice-code-model")
            markers.add("xspice")
            _diagnostic(
                diagnostics,
                "xspice-code-model",
                "warning",
                "compatibility",
                (
                    "XSPICE A-device availability depends on installed code models."
                    if backend_id == "ngspice"
                    else "A-device syntax is not portable across Multisim and XSPICE engines."
                ),
                line=line_number,
            )
        elif kind in {"T", "O", "U"}:
            features.add("transmission-line")
    full_text = "\n".join(record for _, record in records)
    if "{" in full_text or "}" in full_text:
        features.add("brace-expression")
        markers.add("expression-dialect")
    if re.search(r"(?<!\*)\^(?!\*)", full_text):
        markers.add("caret-expression-operator")
        _diagnostic(
            diagnostics,
            "caret-expression-operator",
            "warning",
            "compatibility",
            "The '^' expression operator has different meanings across SPICE dialects.",
        )
    if "conditional-netlist" in features:
        _diagnostic(
            diagnostics,
            "conditional-netlist-portability",
            "warning",
            "compatibility",
            "Conditional netlist evaluation is engine- and version-sensitive.",
        )
    if "expression-function" in features or "behavioral-source" in features:
        _diagnostic(
            diagnostics,
            "behavioral-expression-portability",
            "warning",
            "compatibility",
            "Behavioral expression functions and operators require runtime cross-backend verification.",
        )
    features.update(f"device:{kind}" for kind in device_kinds)
    return sorted(features), sorted(markers)


def _declared_dialect(
    netlist: str, declared_dialect: str | None
) -> tuple[str, str]:
    if declared_dialect is not None:
        if not isinstance(declared_dialect, str) or not _DIALECT.fullmatch(
            declared_dialect.strip()
        ):
            raise ValueError("declared_dialect is invalid or too long")
        return declared_dialect.strip(), "explicit-input"
    marker = re.search(
        r"(?im)^\s*[\*;#]\s*(?:multisim-mcp\s*:\s*)?spice-dialect\s*[:=]\s*([^\r\n]+)$",
        netlist,
    )
    if marker and _DIALECT.fullmatch(marker.group(1).strip()):
        return marker.group(1).strip(), "netlist-comment"
    return "spice-family", "not-declared"


def _solver_version(backend_id: str, solver_output: str | None) -> tuple[str | None, str]:
    if solver_output is None:
        return None, "not-captured"
    if not isinstance(solver_output, str) or len(solver_output) > 262_144:
        raise ValueError("solver_output must be a string no larger than 262144 characters")
    for pattern in _SOLVER_VERSION_PATTERNS.get(backend_id, ()):
        match = pattern.search(solver_output)
        if match:
            return match.group(1), "captured-from-log"
    return None, "log-present-version-not-detected"


def _definition_fingerprint(definitions: Sequence[Mapping[str, Any]]) -> str:
    return _sha256_text(
        json.dumps(
            [
                {
                    "name": item.get("name"),
                    "definition_sha256": item.get("definition_sha256"),
                }
                for item in sorted(
                    definitions,
                    key=lambda value: str(value.get("name", "")).casefold(),
                )
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def audit_spice_compatibility(
    netlist: str,
    *,
    backend_id: str = "multisim",
    model_references: Sequence[ModelReference | Mapping[str, Any]] | None = None,
    declared_dialect: str | None = None,
    solver_output: str | None = None,
    executed_netlist: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic, JSON-safe compatibility and provenance record."""
    if not isinstance(netlist, str):
        raise ValueError("netlist must be a string")
    validate_spice_netlist(netlist)
    if executed_netlist is not None:
        if not isinstance(executed_netlist, str):
            raise ValueError("executed_netlist must be a string")
        _validate_executed_netlist(executed_netlist)
    normalized_backend = backend_id.strip().casefold() if isinstance(backend_id, str) else ""
    if not _BACKEND_ID.fullmatch(normalized_backend):
        raise ValueError("backend_id must be a stable lowercase identifier")
    references = _model_references(model_references)
    records = _logical_records(netlist)
    diagnostics: list[dict[str, Any]] = []
    features, dialect_markers = _feature_inventory(
        records, normalized_backend, diagnostics
    )
    dialect, dialect_source = _declared_dialect(netlist, declared_dialect)
    solver_version, solver_status = _solver_version(normalized_backend, solver_output)
    if normalized_backend not in {"multisim", "ngspice"}:
        _diagnostic(
            diagnostics,
            "backend-profile-unknown",
            "warning",
            "compatibility",
            f"No static compatibility profile is registered for backend {normalized_backend!r}.",
        )
    if dialect_source == "not-declared":
        _diagnostic(
            diagnostics,
            "dialect-not-declared",
            "warning",
            "provenance",
            "The input identifies only the SPICE language family, not an exact dialect/version.",
        )
    if solver_version is None:
        _diagnostic(
            diagnostics,
            "solver-version-not-captured",
            "warning",
            "runtime",
            "The solver version could not be proven from the captured experiment evidence.",
        )

    explicit_by_exact = {reference.name.casefold(): reference for reference in references}
    explicit_by_model = {
        _normalize_model_name(reference.name): reference for reference in references
    }
    definitions = _inline_definitions(records)
    execution_features: list[str] = []
    execution_markers: list[str] = []
    execution_model_fingerprint: str | None = None
    if executed_netlist is not None:
        execution_records = _logical_records(executed_netlist)
        execution_diagnostics: list[dict[str, Any]] = []
        execution_features, execution_markers = _feature_inventory(
            execution_records, normalized_backend, execution_diagnostics
        )
        for item in execution_diagnostics:
            copied = dict(item)
            copied["code"] = f"executed-{copied['code']}"
            copied["message"] = f"Executed netlist: {copied['message']}"
            diagnostics.append(copied)
        execution_model_fingerprint = _definition_fingerprint(
            _inline_definitions(execution_records)
        )
    found_models = {_normalize_model_name(item["name"]): item for item in definitions}
    referenced_devices: dict[tuple[str, str], list[str]] = {}
    for _, record in records:
        reference = _reference_from_device(record)
        if reference is None:
            continue
        model_kind, model_name = reference
        referenced_devices.setdefault(
            (model_kind, _normalize_model_name(model_name)), []
        ).append(record.split()[0])
    for (kind, normalized_name), refdes_values in referenced_devices.items():
        if normalized_name in found_models:
            found_models[normalized_name]["referenced_by"] = sorted(set(refdes_values))
            continue
        explicit = explicit_by_model.get(normalized_name)
        name = f"{'subckt' if kind == 'subckt' else 'model'}:{normalized_name}"
        definitions.append(
            {
                "name": name,
                "model_name": normalized_name,
                "kind": "subcircuit" if kind == "subckt" else "model",
                "model_type": None,
                "line": None,
                "definition_sha256": None,
                "hash_scope": None,
                "definition_status": "not-embedded",
                "referenced_by": sorted(set(refdes_values)),
            }
        )
        found_models[normalized_name] = definitions[-1]
        if explicit is None:
            _diagnostic(
                diagnostics,
                "model-definition-not-embedded",
                "warning",
                "provenance",
                f"Model {model_name!r} depends on backend/library resolution and has no declared source.",
                model=name,
            )

    matched_explicit: set[str] = set()
    models: list[dict[str, Any]] = []
    for definition in definitions:
        explicit = explicit_by_exact.get(str(definition["name"]).casefold())
        if explicit is None:
            explicit = explicit_by_model.get(_normalize_model_name(definition["name"]))
        if explicit is not None:
            matched_explicit.add(explicit.name.casefold())
        computed = definition.get("definition_sha256")
        declared = explicit.sha256 if explicit is not None else None
        if computed and declared:
            hash_status = "verified" if computed == declared else "mismatch"
        elif computed:
            hash_status = "computed"
        elif declared:
            hash_status = "declared-content-not-embedded"
        else:
            hash_status = "missing"
        license_value = explicit.license if explicit is not None else None
        license_status = (
            "declared"
            if license_value is not None
            and license_value.strip().casefold() not in _LICENSE_UNKNOWN
            else "unknown"
        )
        source = explicit.source if explicit is not None else (
            "inline-netlist" if computed else "backend-or-library-resolution"
        )
        model = {
            **definition,
            "source": source,
            "declared_sha256": declared,
            "hash_status": hash_status,
            "license": license_value,
            "license_status": license_status,
        }
        models.append(model)
        if hash_status == "mismatch":
            _diagnostic(
                diagnostics,
                "model-sha256-mismatch",
                "error",
                "provenance",
                f"Declared SHA-256 does not match the embedded definition for {definition['name']}.",
                line=definition.get("line"),
                model=str(definition["name"]),
            )
        elif hash_status in {"missing", "declared-content-not-embedded"}:
            _diagnostic(
                diagnostics,
                "model-content-unverified",
                "warning",
                "provenance",
                f"Model content is not embedded and verifiable for {definition['name']}.",
                model=str(definition["name"]),
            )
        if license_status == "unknown":
            _diagnostic(
                diagnostics,
                "model-license-unknown",
                "warning",
                "provenance",
                f"No usable license declaration is recorded for {definition['name']}.",
                line=definition.get("line"),
                model=str(definition["name"]),
            )

    for explicit in references:
        if explicit.name.casefold() in matched_explicit:
            continue
        license_status = (
            "declared"
            if explicit.license is not None
            and explicit.license.strip().casefold() not in _LICENSE_UNKNOWN
            else "unknown"
        )
        models.append(
            {
                "name": explicit.name,
                "model_name": _normalize_model_name(explicit.name),
                "kind": "reference",
                "model_type": None,
                "line": None,
                "definition_sha256": None,
                "hash_scope": None,
                "definition_status": "not-embedded",
                "source": explicit.source,
                "declared_sha256": explicit.sha256,
                "hash_status": (
                    "declared-content-not-embedded" if explicit.sha256 else "missing"
                ),
                "license": explicit.license,
                "license_status": license_status,
                "referenced_by": [],
            }
        )
        _diagnostic(
            diagnostics,
            "declared-model-not-found-in-netlist",
            "warning",
            "provenance",
            f"Declared model reference {explicit.name!r} was not matched to an inline definition or device reference.",
            model=explicit.name,
        )

    models.sort(key=lambda item: (str(item["name"]).casefold(), str(item["kind"])))
    model_fingerprint = _sha256_text(
        json.dumps(
            [
                {
                    "name": item["name"],
                    "definition_sha256": item["definition_sha256"],
                    "declared_sha256": item["declared_sha256"],
                }
                for item in models
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    diagnostics.sort(
        key=lambda item: (
            {"error": 0, "warning": 1, "info": 2}.get(str(item["severity"]), 3),
            str(item["code"]),
            int(item.get("line") or 0),
        )
    )
    counts = {
        severity: sum(item["severity"] == severity for item in diagnostics)
        for severity in ("error", "warning", "info")
    }
    compatibility_warnings = sum(
        item["severity"] == "warning" and item["category"] == "compatibility"
        for item in diagnostics
    )
    if counts["error"]:
        risk_level = "high"
        compatibility_status = "static-errors"
    elif compatibility_warnings:
        risk_level = "medium"
        compatibility_status = "runtime-verification-required"
    else:
        risk_level = "low"
        compatibility_status = "portable-static-subset"
    provenance_complete = (
        dialect_source != "not-declared"
        and solver_version is not None
        and all(
            item["hash_status"] in {"verified", "computed"}
            and item["license_status"] == "declared"
            for item in models
        )
    )
    return {
        "schema_version": SPICE_COMPATIBILITY_SCHEMA_VERSION,
        "netlist": {
            "sha256": _sha256_text(netlist),
            "byte_count": len(netlist.encode("utf-8")),
            "line_count": len(netlist.splitlines()),
        },
        "executed_netlist": (
            {
                "sha256": _sha256_text(executed_netlist),
                "byte_count": len(executed_netlist.encode("utf-8")),
                "line_count": len(executed_netlist.splitlines()),
                "features": execution_features,
                "detected_markers": execution_markers,
                "model_fingerprint_sha256": execution_model_fingerprint,
            }
            if executed_netlist is not None
            else None
        ),
        "dialect": {
            "name": dialect,
            "source": dialect_source,
            "detected_markers": dialect_markers,
            "features": features,
        },
        "backend": {
            "backend_id": normalized_backend,
            "compatibility_status": compatibility_status,
            "solver_version": solver_version,
            "solver_version_status": solver_status,
        },
        "models": models,
        "model_fingerprint_sha256": model_fingerprint,
        "summary": {
            "risk_level": risk_level,
            "provenance_complete": provenance_complete,
            "model_count": len(models),
            "unknown_license_count": sum(
                item["license_status"] == "unknown" for item in models
            ),
            "unverified_model_content_count": sum(
                item["hash_status"]
                in {"missing", "declared-content-not-embedded", "mismatch"}
                for item in models
            ),
            "diagnostic_counts": counts,
        },
        "diagnostics": diagnostics,
    }


def compare_spice_compatibility_audits(
    reference: Mapping[str, Any] | None,
    candidate: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Classify whether a numerical backend comparison shares reproducible input."""
    if not isinstance(reference, Mapping) or not isinstance(candidate, Mapping):
        return {
            "status": "unverified",
            "comparison_scope": "unknown",
            "same_netlist": None,
            "same_models": None,
            "diagnostics": [
                {
                    "code": "compatibility-audit-missing",
                    "severity": "warning",
                    "message": "One or both experiments predate the SPICE compatibility audit artifact.",
                }
            ],
        }
    reference_netlist = reference.get("netlist")
    candidate_netlist = candidate.get("netlist")
    reference_executed = reference.get("executed_netlist")
    candidate_executed = candidate.get("executed_netlist")
    reference_hash = (
        reference_netlist.get("sha256") if isinstance(reference_netlist, Mapping) else None
    )
    candidate_hash = (
        candidate_netlist.get("sha256") if isinstance(candidate_netlist, Mapping) else None
    )
    reference_execution_hash = (
        reference_executed.get("sha256")
        if isinstance(reference_executed, Mapping)
        else reference_hash
    )
    candidate_execution_hash = (
        candidate_executed.get("sha256")
        if isinstance(candidate_executed, Mapping)
        else candidate_hash
    )
    same_netlist = bool(
        reference_hash
        and reference_hash == candidate_hash
        and reference_execution_hash
        and reference_execution_hash == candidate_execution_hash
    )
    reference_models = reference.get("model_fingerprint_sha256")
    candidate_models = candidate.get("model_fingerprint_sha256")
    reference_execution_models = (
        reference_executed.get("model_fingerprint_sha256")
        if isinstance(reference_executed, Mapping)
        else reference_models
    )
    candidate_execution_models = (
        candidate_executed.get("model_fingerprint_sha256")
        if isinstance(candidate_executed, Mapping)
        else candidate_models
    )
    same_models = bool(
        reference_models
        and reference_models == candidate_models
        and reference_execution_models
        and reference_execution_models == candidate_execution_models
    )
    diagnostics: list[dict[str, str]] = []
    if not same_netlist:
        diagnostics.append(
            {
                "code": "netlist-sha256-differs",
                "severity": "warning",
                "message": "The experiments do not use byte-identical SPICE netlists.",
            }
        )
    if reference_execution_hash != candidate_execution_hash:
        diagnostics.append(
            {
                "code": "executed-netlist-sha256-differs",
                "severity": "warning",
                "message": "Backend preparation produced different executed netlists.",
            }
        )
    if not same_models:
        diagnostics.append(
            {
                "code": "model-fingerprint-differs",
                "severity": "warning",
                "message": "The experiments do not have identical model fingerprints.",
            }
        )
    reference_backend = reference.get("backend")
    candidate_backend = candidate.get("backend")
    reference_version = (
        reference_backend.get("solver_version")
        if isinstance(reference_backend, Mapping)
        else None
    )
    candidate_version = (
        candidate_backend.get("solver_version")
        if isinstance(candidate_backend, Mapping)
        else None
    )
    if reference_version is None or candidate_version is None:
        diagnostics.append(
            {
                "code": "solver-version-evidence-incomplete",
                "severity": "warning",
                "message": "At least one solver version was not captured.",
            }
        )
    if same_netlist and same_models:
        scope = "same-input-cross-solver"
        status = "verified" if reference_version and candidate_version else "partial"
    else:
        scope = "different-input-or-model"
        status = "incomparable"
    return {
        "status": status,
        "comparison_scope": scope,
        "same_netlist": same_netlist,
        "same_models": same_models,
        "reference": {
            "netlist_sha256": reference_hash,
            "executed_netlist_sha256": reference_execution_hash,
            "model_fingerprint_sha256": reference_models,
            "executed_model_fingerprint_sha256": reference_execution_models,
            "solver_version": reference_version,
        },
        "candidate": {
            "netlist_sha256": candidate_hash,
            "executed_netlist_sha256": candidate_execution_hash,
            "model_fingerprint_sha256": candidate_models,
            "executed_model_fingerprint_sha256": candidate_execution_models,
            "solver_version": candidate_version,
        },
        "diagnostics": diagnostics,
    }


__all__ = [
    "SPICE_COMPATIBILITY_SCHEMA_VERSION",
    "audit_spice_compatibility",
    "compare_spice_compatibility_audits",
]
