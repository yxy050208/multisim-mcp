"""Budgeted, deterministic parameter optimization for CircuitDesign values.

The optimizer is deliberately transport-neutral.  It generates only bounded
``set_component_value`` patches, evaluates each in-memory candidate through the
existing experiment application service, and never mutates the source design.
All verification requirements are hard constraints: a failed or unverified
candidate is never promoted as a feasible solution.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import os
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from .design_patch_service import PreparedDesignPatch, prepare_design_patch
from .design_verification import validate_experiment_spec
from .eda_core import CircuitDesign, DesignPatch, PatchOperation
from .experiment_service import ExperimentApplicationService, ExperimentRequest
from .job_engine import output_lease
from .preferred_values import (
    generate_preferred_values,
    spice_value_key,
)
from .ranked_evaluation import (
    finite_number,
    normalize_single_objective,
    validate_ranked_experiment_evidence,
)
from .safety import validate_analysis_commands
from .spice_adapter import circuit_design_to_spice
from .workspace_manifest import (
    DIRECTORY_MANIFEST_NAME,
    read_directory_manifest,
    write_directory_manifest,
)


OPTIMIZATION_SCHEMA_VERSION: Final = 1
MAX_OPTIMIZATION_EXPERIMENTS: Final = 32
MAX_OPTIMIZATION_VARIABLES: Final = 4
MAX_VALUES_PER_VARIABLE: Final = 32
MAX_INVENTORY_RECORDS: Final = 128
MAX_OPTIMIZATION_SPEC_BYTES: Final = 1024 * 1024
OPTIMIZATION_STATE_NAME: Final = "optimization.json"
OPTIMIZATION_SPEC_NAME: Final = "optimization-spec.json"
VERIFICATION_PLAN_NAME: Final = "verification-plan.json"
BASELINE_DESIGN_NAME: Final = "baseline-design.json"
BEST_PATCH_NAME: Final = "best-patch.json"
OPTIMIZATION_DATA_NAME: Final = "candidates.csv"

_REFDES_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SCALAR_VALUE_RE = re.compile(
    r"^[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))"
    r"(?:[eE][+-]?\d+|[A-Za-z\u00b5\u03bc][A-Za-z0-9\u00b5\u03bc]*)?$"
)
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9]{2,7}$")
_OPTIMIZATION_ID_RE = re.compile(r"^optimization-[0-9a-f]{32}$")
_PREFERRED_COMPONENT_KINDS: Final = frozenset(
    {"R", "C", "L", "RESISTOR", "CAPACITOR", "INDUCTOR"}
)
ProgressCallback = Callable[[str, int, str], None]
CancellationProbe = Callable[[], bool]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _plain_json(value: object) -> Any:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _value_key(value: str) -> str:
    try:
        return f"numeric:{spice_value_key(value)}"
    except ValueError:
        return f"literal:{value.casefold()}"


def _normalize_values(raw_values: object, refdes: str) -> list[str]:
    if not isinstance(raw_values, list) or not (
        1 <= len(raw_values) <= MAX_VALUES_PER_VARIABLE
    ):
        raise ValueError(
            f"{refdes}.values must contain between 1 and "
            f"{MAX_VALUES_PER_VARIABLE} entries"
        )
    values: list[str] = []
    value_keys: set[str] = set()
    for index, value in enumerate(raw_values):
        if not isinstance(value, str):
            raise ValueError(f"{refdes}.values[{index}] must be a string")
        normalized = value.strip()
        if not _SCALAR_VALUE_RE.fullmatch(normalized):
            raise ValueError(
                f"{refdes}.values[{index}] must be one scalar SPICE value"
            )
        key = _value_key(normalized)
        if key in value_keys:
            raise ValueError(f"{refdes}.values contains an equivalent duplicate")
        value_keys.add(key)
        values.append(normalized)
    return values


def _normalize_inventory(raw_inventory: object, refdes: str) -> list[dict[str, Any]]:
    if raw_inventory is None:
        return []
    if not isinstance(raw_inventory, list) or not (
        1 <= len(raw_inventory) <= MAX_INVENTORY_RECORDS
    ):
        raise ValueError(
            f"{refdes}.inventory must contain between 1 and "
            f"{MAX_INVENTORY_RECORDS} records"
        )
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_inventory):
        if not isinstance(raw, Mapping):
            raise ValueError(f"{refdes}.inventory[{index}] must be an object")
        allowed = {"value", "part_number", "supplier", "unit_cost", "stock"}
        if set(raw) - allowed or not {
            "value",
            "part_number",
            "unit_cost",
            "stock",
        } <= set(raw):
            raise ValueError(
                f"{refdes}.inventory[{index}] requires value, part_number, "
                "unit_cost, and stock"
            )
        raw_value = raw.get("value")
        if not isinstance(raw_value, str):
            raise ValueError(f"{refdes}.inventory[{index}].value must be a string")
        value = raw_value.strip()
        if not _SCALAR_VALUE_RE.fullmatch(value):
            raise ValueError(f"{refdes}.inventory[{index}].value is invalid")
        try:
            key = spice_value_key(value)
        except ValueError as exc:
            raise ValueError(
                f"{refdes}.inventory[{index}].value requires a standard suffix"
            ) from exc
        if key in seen:
            raise ValueError(f"{refdes}.inventory contains an equivalent duplicate")
        seen.add(key)
        raw_part_number = raw.get("part_number")
        if not isinstance(raw_part_number, str):
            raise ValueError(
                f"{refdes}.inventory[{index}].part_number must be a string"
            )
        part_number = raw_part_number.strip()
        if not part_number or "\x00" in part_number or len(part_number) > 256:
            raise ValueError(
                f"{refdes}.inventory[{index}].part_number is empty or invalid"
            )
        raw_supplier = raw.get("supplier", "")
        if not isinstance(raw_supplier, str):
            raise ValueError(f"{refdes}.inventory[{index}].supplier must be a string")
        supplier = raw_supplier.strip()
        if "\x00" in supplier or len(supplier) > 256:
            raise ValueError(f"{refdes}.inventory[{index}].supplier is invalid")
        unit_cost = finite_number(
            raw.get("unit_cost"), f"{refdes}.inventory[{index}].unit_cost"
        )
        if unit_cost < 0:
            raise ValueError(f"{refdes}.inventory[{index}].unit_cost must be >= 0")
        stock = raw.get("stock")
        if (
            isinstance(stock, bool)
            or not isinstance(stock, int)
            or not 0 <= stock <= 2_147_483_647
        ):
            raise ValueError(
                f"{refdes}.inventory[{index}].stock must be a non-negative integer"
            )
        normalized.append(
            {
                "value": value,
                "part_number": part_number,
                "supplier": supplier,
                "unit_cost": unit_cost,
                "stock": stock,
            }
        )
    return normalized


def _normalize_procurement(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("procurement must be an object")
    allowed = {
        "currency",
        "require_in_stock",
        "max_total_unit_cost",
        "prefer_lower_cost",
    }
    if set(raw) - allowed:
        raise ValueError("procurement contains unknown fields")
    currency = str(raw.get("currency", "")).strip().upper()
    if not _CURRENCY_RE.fullmatch(currency):
        raise ValueError("procurement.currency must be a 3-8 character code")
    require_in_stock = raw.get("require_in_stock", False)
    prefer_lower_cost = raw.get("prefer_lower_cost", False)
    if not isinstance(require_in_stock, bool):
        raise ValueError("procurement.require_in_stock must be a boolean")
    if not isinstance(prefer_lower_cost, bool):
        raise ValueError("procurement.prefer_lower_cost must be a boolean")
    normalized: dict[str, Any] = {
        "currency": currency,
        "require_in_stock": require_in_stock,
        "prefer_lower_cost": prefer_lower_cost,
    }
    if "max_total_unit_cost" in raw:
        maximum = finite_number(
            raw["max_total_unit_cost"], "procurement.max_total_unit_cost"
        )
        if maximum < 0:
            raise ValueError("procurement.max_total_unit_cost must be >= 0")
        normalized["max_total_unit_cost"] = maximum
    return normalized


def validate_optimization_spec(
    spec: Mapping[str, Any], design: CircuitDesign
) -> dict[str, Any]:
    """Validate and normalize the bounded OptimizationSpec v1 contract."""
    if not isinstance(design, CircuitDesign):
        raise ValueError("design must be CircuitDesign")
    if not isinstance(spec, Mapping) or spec.get("schema_version") != 1:
        raise ValueError("OptimizationSpec schema_version must be 1")
    allowed = {
        "schema_version",
        "title",
        "variables",
        "commands",
        "requirements",
        "theoretical_values",
        "objective",
        "max_experiments",
        "procurement",
    }
    unknown = set(spec) - allowed
    if unknown:
        raise ValueError(
            "OptimizationSpec contains unknown fields: " + ", ".join(sorted(unknown))
        )

    raw_variables = spec.get("variables")
    if not isinstance(raw_variables, list) or not (
        1 <= len(raw_variables) <= MAX_OPTIMIZATION_VARIABLES
    ):
        raise ValueError(
            f"variables must contain between 1 and {MAX_OPTIMIZATION_VARIABLES} entries"
        )
    components = {item.refdes.casefold(): item for item in design.components}
    variables: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_variables):
        if not isinstance(raw, Mapping):
            raise ValueError(f"variables[{index}] must be an object")
        allowed_variable = {"refdes", "values", "series", "inventory"}
        if set(raw) - allowed_variable:
            raise ValueError(f"variables[{index}] contains unknown fields")
        if ("values" in raw) == ("series" in raw):
            raise ValueError(
                f"variables[{index}] requires exactly one of values or series"
            )
        refdes = str(raw.get("refdes", "")).strip()
        if not _REFDES_RE.fullmatch(refdes):
            raise ValueError(f"variables[{index}].refdes is invalid")
        key = refdes.casefold()
        if key in seen:
            raise ValueError(f"duplicate optimization component: {refdes}")
        seen.add(key)
        component = components.get(key)
        if component is None:
            raise ValueError(f"optimization component does not exist: {refdes}")
        if component.value is None:
            raise ValueError(f"optimization component has no scalar value: {refdes}")
        value_source: dict[str, Any]
        if "values" in raw:
            values = _normalize_values(raw.get("values"), refdes)
            value_source = {"kind": "explicit"}
        else:
            if component.kind.strip().upper() not in _PREFERRED_COMPONENT_KINDS:
                raise ValueError(
                    f"preferred series is supported only for R, C, or L: {refdes}"
                )
            series = raw.get("series")
            if not isinstance(series, Mapping) or set(series) != {
                "name",
                "minimum",
                "maximum",
            }:
                raise ValueError(
                    f"{refdes}.series must contain exactly name, minimum, and maximum"
                )
            if any(
                not isinstance(series.get(name), str)
                for name in ("name", "minimum", "maximum")
            ):
                raise ValueError(f"{refdes}.series fields must be strings")
            series_name = series["name"].strip().upper()
            minimum = series["minimum"].strip()
            maximum = series["maximum"].strip()
            values = generate_preferred_values(series_name, minimum, maximum)
            if not 1 <= len(values) <= MAX_VALUES_PER_VARIABLE:
                raise ValueError(
                    f"{refdes}.series generates {len(values)} values; narrow it to "
                    f"between 1 and {MAX_VALUES_PER_VARIABLE}"
                )
            value_source = {
                "kind": "preferred_series",
                "name": series_name,
                "minimum": minimum,
                "maximum": maximum,
            }
        inventory = _normalize_inventory(raw.get("inventory"), refdes)
        variable = {
            "refdes": component.refdes,
            "before": component.value,
            "values": values,
            "value_source": value_source,
        }
        if inventory:
            variable["inventory"] = inventory
        variables.append(variable)

    max_experiments = spec.get("max_experiments")
    if (
        isinstance(max_experiments, bool)
        or not isinstance(max_experiments, int)
        or not 2 <= max_experiments <= MAX_OPTIMIZATION_EXPERIMENTS
    ):
        raise ValueError(
            f"max_experiments must be an integer between 2 and "
            f"{MAX_OPTIMIZATION_EXPERIMENTS}; the baseline consumes one experiment"
        )

    title = str(spec.get("title", "")).strip()
    if not title or "\x00" in title or len(title) > 4096:
        raise ValueError("OptimizationSpec title is empty, invalid, or too long")
    commands = "\n".join(validate_analysis_commands(str(spec.get("commands", ""))))
    normalized_experiment = validate_experiment_spec(
        {
            "schema_version": 1,
            "title": title,
            "netlist": circuit_design_to_spice(design),
            "commands": commands,
            "requirements": spec.get("requirements", []),
            "theoretical_values": spec.get("theoretical_values", {}),
        }
    )

    known_ids = {item["id"] for item in normalized_experiment["requirements"]}
    normalized_objective = normalize_single_objective(spec.get("objective"), known_ids)
    normalized_procurement = _normalize_procurement(spec.get("procurement"))

    return {
        "schema_version": OPTIMIZATION_SCHEMA_VERSION,
        "title": title,
        "variables": variables,
        "commands": commands,
        "requirements": normalized_experiment["requirements"],
        "theoretical_values": normalized_experiment["theoretical_values"],
        "objective": normalized_objective,
        "max_experiments": max_experiments,
        "procurement": normalized_procurement,
    }


def read_optimization_spec(
    path: str, design: CircuitDesign, *, normalize: bool = True
) -> tuple[Path, dict[str, Any]]:
    """Read and validate one strict UTF-8 OptimizationSpec document.

    The normalized form remains the default for callers that inspect the
    contract. Transport adapters can request the validated raw document when
    the application service will perform normalization itself.
    """
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise ValueError("optimization spec must not be a symbolic link")
    source = unresolved.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"optimization spec does not exist: {source}")
    if source.stat().st_size > MAX_OPTIMIZATION_SPEC_BYTES:
        raise ValueError("optimization spec exceeds the size limit")
    try:
        raw = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError("optimization spec must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"optimization spec is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("optimization spec must contain one JSON object")
    normalized = validate_optimization_spec(raw, design)
    return source, normalized if normalize else raw


def _candidate_space_size(spec: Mapping[str, Any]) -> int:
    combinations = math.prod(len(item["values"]) for item in spec["variables"])
    baseline_present = all(
        _value_key(str(item["before"]))
        in {_value_key(str(value)) for value in item["values"]}
        for item in spec["variables"]
    )
    return combinations - int(baseline_present)


def _prepare_candidates(
    design: CircuitDesign, spec: Mapping[str, Any]
) -> list[tuple[str, dict[str, str], PreparedDesignPatch]]:
    limit = int(spec["max_experiments"]) - 1
    variables = list(spec["variables"])
    value_sets = [item["values"] for item in variables]
    prepared: list[tuple[str, dict[str, str], PreparedDesignPatch]] = []
    spec_digest = _digest(spec)[:16]
    for combination in itertools.product(*value_sets):
        assignments = {
            str(variable["refdes"]): str(value)
            for variable, value in zip(variables, combination)
        }
        operations = []
        for variable, value in zip(variables, combination):
            before = str(variable["before"])
            after = str(value)
            if _value_key(before) == _value_key(after):
                continue
            operations.append(
                PatchOperation(
                    operation="set_component_value",
                    target=f"{variable['refdes']}.value",
                    before=before,
                    after=after,
                    reason="bounded value selected by OptimizationSpec v1",
                )
            )
        if not operations:
            continue
        candidate_id = f"candidate-{len(prepared) + 1:03d}"
        patch = DesignPatch(
            patch_id=f"opt-{spec_digest}-{len(prepared) + 1:03d}",
            design_id=design.design_id,
            base_revision=design.revision,
            operations=tuple(operations),
            description=f"{spec['title']}: {candidate_id}",
            metadata={
                "source": "optimize_design",
                "candidate_id": candidate_id,
                "optimization_spec_digest": _digest(spec),
            },
        )
        candidate = prepare_design_patch(
            design,
            patch,
            regenerate_source_netlist=design.source_netlist is not None,
        )
        # Compile now so every admitted value is proven representable before
        # any experiment output is created.
        circuit_design_to_spice(candidate.candidate)
        prepared.append((candidate_id, assignments, candidate))
        if len(prepared) >= limit:
            break
    if not prepared:
        raise ValueError("OptimizationSpec contains no value different from the baseline")
    return prepared


def _procurement_result(
    spec: Mapping[str, Any], assignments: Mapping[str, str]
) -> dict[str, Any]:
    procurement = spec.get("procurement")
    configured = isinstance(procurement, Mapping)
    require_record = bool(
        configured
        and (
            procurement.get("require_in_stock")
            or procurement.get("prefer_lower_cost")
            or "max_total_unit_cost" in procurement
        )
    )
    reasons: list[str] = []
    selections: list[dict[str, Any]] = []
    total = Decimal("0")
    complete_cost = True
    for variable in spec["variables"]:
        refdes = str(variable["refdes"])
        value = str(assignments.get(refdes, variable["before"]))
        try:
            key = spice_value_key(value)
        except ValueError:
            key = ""
        record = next(
            (
                item
                for item in variable.get("inventory", [])
                if spice_value_key(str(item.get("value", ""))) == key
            ),
            None,
        )
        if record is None:
            complete_cost = False
            selections.append(
                {
                    "refdes": refdes,
                    "value": value,
                    "inventory_matched": False,
                    "part_number": None,
                    "supplier": None,
                    "unit_cost": None,
                    "stock": None,
                }
            )
            if require_record:
                reasons.append(f"{refdes}:inventory_missing")
            continue
        cost = float(record["unit_cost"])
        total += Decimal(str(cost))
        stock = int(record["stock"])
        selections.append(
            {
                "refdes": refdes,
                "value": value,
                "inventory_matched": True,
                "part_number": record["part_number"],
                "supplier": record["supplier"],
                "unit_cost": cost,
                "stock": stock,
            }
        )
        if configured and procurement.get("require_in_stock") and stock <= 0:
            reasons.append(f"{refdes}:out_of_stock")
    total_cost = float(total) if complete_cost else None
    if configured and "max_total_unit_cost" in procurement:
        if total_cost is None:
            if not any(reason.endswith("inventory_missing") for reason in reasons):
                reasons.append("total_cost:unavailable")
        elif total > Decimal(str(procurement["max_total_unit_cost"])):
            reasons.append("total_cost:over_budget")
    return {
        "status": "fail" if reasons else "pass" if configured else "not_configured",
        "currency": None if not configured else procurement["currency"],
        "total_unit_cost": total_cost,
        "max_total_unit_cost": (
            None if not configured else procurement.get("max_total_unit_cost")
        ),
        "prefer_lower_cost": bool(
            configured and procurement.get("prefer_lower_cost")
        ),
        "selections": selections,
        "reasons": reasons,
    }


def _write_candidates_csv(root: Path, evaluations: list[dict[str, Any]]) -> None:
    variable_names = sorted(
        {name for item in evaluations for name in item.get("values", {})},
        key=str.casefold,
    )
    path = root / OPTIMIZATION_DATA_NAME
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "evaluation_id",
                "kind",
                "status",
                "hard_constraint_status",
                "objective_status",
                "objective_value",
                "objective_score",
                "procurement_status",
                "total_unit_cost",
                "currency",
                "part_numbers",
                *variable_names,
            ]
        )
        for item in evaluations:
            objective = item.get("objective") or {}
            evidence = item.get("experiment") or {}
            procurement = item.get("procurement") or {}
            part_numbers = ";".join(
                f"{selection['refdes']}={selection['part_number']}"
                for selection in procurement.get("selections", [])
                if selection.get("part_number")
            )
            writer.writerow(
                [
                    item["evaluation_id"],
                    item["kind"],
                    item["status"],
                    evidence.get("overall_status", "error"),
                    objective.get("status", "unverified"),
                    objective.get("value", ""),
                    objective.get("score", ""),
                    procurement.get("status", "not_configured"),
                    procurement.get("total_unit_cost", ""),
                    procurement.get("currency", ""),
                    part_numbers,
                    *(item.get("values", {}).get(name, "") for name in variable_names),
                ]
            )


def _artifact_allowlist(root: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"optimization artifacts must not contain symlinks: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == DIRECTORY_MANIFEST_NAME:
            continue
        role = (
            "optimization-state"
            if relative == OPTIMIZATION_STATE_NAME
            else "optimization-spec"
            if relative == OPTIMIZATION_SPEC_NAME
            else "verification-plan"
            if relative == VERIFICATION_PLAN_NAME
            else "baseline-design"
            if relative == BASELINE_DESIGN_NAME
            else "best-patch"
            if relative == BEST_PATCH_NAME
            else "optimization-data"
            if relative == OPTIMIZATION_DATA_NAME
            else "candidate-patch"
            if relative.startswith("patches/")
            else "experiment-artifact"
        )
        artifacts[relative] = role
    return artifacts


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError(f"{label} exceeds the size limit")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {constant}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError(f"{label} must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value


def _write_or_validate_json(path: Path, value: object, label: str) -> None:
    """Create an immutable run input or verify the copy left by an interrupted run."""
    if path.exists():
        stored = _read_json_object(path, label)
        if _canonical_bytes(stored) != _canonical_bytes(value):
            raise ValueError(f"{label} does not match the requested resume input")
        return
    _atomic_json(path, value)


def _expected_evaluation_status(
    evidence: Mapping[str, Any],
    objective: Mapping[str, Any],
    procurement: Mapping[str, Any],
) -> str:
    if evidence.get("overall_status") != "pass":
        return str(evidence.get("overall_status", "unverified"))
    if objective.get("status") != "measured":
        return "unrankable"
    if procurement.get("status") == "fail":
        return "procurement_fail"
    return "feasible"


def _validate_completed_evaluation(
    root: Path,
    evaluation: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> None:
    """Revalidate persisted evidence before a completed candidate is reused."""
    status = str(evaluation.get("status", ""))
    if status == "error":
        if not isinstance(evaluation.get("error"), Mapping):
            raise ValueError("resumable error evaluation lacks structured diagnostics")
        return
    evidence = evaluation.get("experiment")
    objective = evaluation.get("objective")
    procurement = evaluation.get("procurement")
    if not all(isinstance(item, Mapping) for item in (evidence, objective, procurement)):
        raise ValueError("resumable evaluation lacks ranked experiment evidence")
    relative_output = Path(str(evidence["output_directory"]))
    relative_verification = Path(str(evidence["verification_path"]))
    if relative_output.is_absolute() or relative_verification.is_absolute():
        raise ValueError("resumable evaluation contains an absolute artifact path")
    output = (root / relative_output).resolve()
    verification_path = (root / relative_verification).resolve()
    try:
        output.relative_to(root)
        verification_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("resumable evaluation artifact escapes the optimization root") from exc
    verification = _read_json_object(verification_path, "resumable verification")
    validated_evidence, validated_objective = validate_ranked_experiment_evidence(
        root,
        {
            "experiment_id": evidence["experiment_id"],
            "output_dir": str(output),
            "verification": verification,
            "verification_path": str(verification_path),
        },
        spec["objective"],
        workflow_name="optimization resume",
    )
    if _canonical_bytes(validated_evidence) != _canonical_bytes(evidence):
        raise ValueError("resumable evaluation evidence does not match its artifacts")
    if _canonical_bytes(validated_objective) != _canonical_bytes(objective):
        raise ValueError("resumable evaluation objective does not match its artifacts")
    expected_procurement = _procurement_result(spec, evaluation.get("values", {}))
    if _canonical_bytes(expected_procurement) != _canonical_bytes(procurement):
        raise ValueError("resumable evaluation procurement evidence is inconsistent")
    expected_status = _expected_evaluation_status(
        validated_evidence, validated_objective, expected_procurement
    )
    if status != expected_status:
        raise ValueError("resumable evaluation status is inconsistent with its evidence")


def _resume_evaluations(
    root: Path,
    state: Mapping[str, Any],
    planned: list[tuple[str, dict[str, str], PreparedDesignPatch | None]],
    spec: Mapping[str, Any],
) -> list[dict[str, Any]]:
    raw = state.get("evaluations")
    if not isinstance(raw, list) or len(raw) > len(planned):
        raise ValueError("optimization checkpoint contains an invalid evaluation list")
    evaluations: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError("optimization checkpoint evaluation must be an object")
        evaluation_id, values, prepared = planned[index]
        expected_kind = "baseline" if prepared is None else "candidate"
        expected_patch = None if prepared is None else f"patches/{evaluation_id}.json"
        if (
            item.get("evaluation_id") != evaluation_id
            or item.get("index") != index
            or item.get("kind") != expected_kind
            or _canonical_bytes(item.get("values")) != _canonical_bytes(values)
            or item.get("patch_path") != expected_patch
        ):
            raise ValueError("optimization checkpoint does not match deterministic candidates")
        evaluation = _plain_json(item)
        status = str(evaluation.get("status", ""))
        if status not in {
            "running",
            "interrupted",
            "error",
            "pass",
            "fail",
            "unverified",
            "unrankable",
            "procurement_fail",
            "feasible",
        }:
            raise ValueError("optimization checkpoint contains an invalid status")
        attempt = evaluation.get("attempt", 1)
        if (
            isinstance(attempt, bool)
            or not isinstance(attempt, int)
            or not 1 <= attempt <= 10_000
        ):
            raise ValueError("optimization checkpoint contains an invalid attempt")
        interrupted_attempts = evaluation.get("interrupted_attempts", [])
        if not isinstance(interrupted_attempts, list) or len(interrupted_attempts) > attempt:
            raise ValueError("optimization checkpoint attempt history is invalid")
        expected_procurement = _procurement_result(spec, values)
        if _canonical_bytes(evaluation.get("procurement")) != _canonical_bytes(
            expected_procurement
        ):
            raise ValueError("optimization checkpoint procurement data is inconsistent")
        if prepared is not None:
            _write_or_validate_json(
                root / str(expected_patch),
                prepared.patch.to_dict(),
                f"{evaluation_id} patch",
            )
        if status in {"running", "interrupted"}:
            evaluation["status"] = "interrupted"
            evaluation["experiment"] = None
            evaluation["objective"] = None
            evaluation["error"] = {
                "type": "InterruptedRun",
                "message": "Previous worker stopped before this evaluation committed",
            }
        else:
            _validate_completed_evaluation(root, evaluation, spec)
        evaluations.append(evaluation)
    return evaluations


class DesignOptimizationService:
    """Evaluate bounded value candidates without persisting source-design changes."""

    def __init__(self, experiment_service: ExperimentApplicationService) -> None:
        if not isinstance(experiment_service, ExperimentApplicationService):
            raise ValueError("experiment_service must be ExperimentApplicationService")
        self._experiments = experiment_service

    def run(
        self,
        design: CircuitDesign,
        spec: Mapping[str, Any],
        output_directory: str,
        *,
        timeout_per_experiment: float = 120.0,
        max_points: int = 2000,
        checkpoint: ProgressCallback | None = None,
        cancel_requested: CancellationProbe | None = None,
        resume: bool = False,
    ) -> dict[str, Any]:
        """Run baseline plus deterministic candidates and return only feasible ranks."""
        if not isinstance(design, CircuitDesign):
            raise ValueError("design must be CircuitDesign")
        if (
            isinstance(timeout_per_experiment, bool)
            or not isinstance(timeout_per_experiment, (int, float))
            or not math.isfinite(float(timeout_per_experiment))
            or not 0 < float(timeout_per_experiment) <= 3600
        ):
            raise ValueError("timeout_per_experiment must be between 0 and 3600")
        if (
            isinstance(max_points, bool)
            or not isinstance(max_points, int)
            or not 1 <= max_points <= 100_000
        ):
            raise ValueError("max_points must be between 1 and 100000")
        if checkpoint is not None and not callable(checkpoint):
            raise ValueError("checkpoint must be callable")
        if cancel_requested is not None and not callable(cancel_requested):
            raise ValueError("cancel_requested must be callable")
        if not isinstance(resume, bool):
            raise ValueError("resume must be a boolean")
        if not isinstance(output_directory, str) or not output_directory.strip():
            raise ValueError("output_directory must not be empty")
        unresolved = Path(output_directory).expanduser()
        if unresolved.is_symlink():
            raise ValueError("output_directory must not be a symbolic link")
        root = unresolved.resolve()
        if root == Path(root.anchor):
            raise ValueError("output_directory must not be a filesystem root")

        normalized = validate_optimization_spec(spec, design)
        candidates = _prepare_candidates(design, normalized)
        candidate_space_size = _candidate_space_size(normalized)
        budget_exhausted = candidate_space_size > len(candidates)
        optimization_id = f"optimization-{uuid.uuid4().hex}"
        baseline_values = {
            str(item["refdes"]): str(item["before"])
            for item in normalized["variables"]
        }
        planned: list[tuple[str, dict[str, str], PreparedDesignPatch | None]] = [
            ("baseline", baseline_values, None),
            *candidates,
        ]
        verification_plan = {
            "schema_version": 1,
            "title": normalized["title"],
            "commands": normalized["commands"],
            "requirements": normalized["requirements"],
            "theoretical_values": normalized["theoretical_values"],
        }
        runtime = {
            "timeout_per_experiment": float(timeout_per_experiment),
            "max_points": max_points,
        }

        def notify(stage: str, progress: int, message: str) -> None:
            if checkpoint is not None:
                checkpoint(stage, progress, message)

        with output_lease(str(root), optimization_id):
            if root.exists() and not root.is_dir():
                raise ValueError("output_directory exists and is not a directory")
            has_checkpoint = root.exists() and any(root.iterdir())
            if has_checkpoint and not resume:
                raise FileExistsError(
                    f"refusing to overwrite non-empty optimization directory: {root}"
                )
            root.mkdir(parents=True, exist_ok=True)
            patches_dir = root / "patches"
            if patches_dir.exists() and (patches_dir.is_symlink() or not patches_dir.is_dir()):
                raise ValueError("optimization patches path is not a safe directory")
            patches_dir.mkdir(exist_ok=True)
            if has_checkpoint:
                state = _read_json_object(
                    root / OPTIMIZATION_STATE_NAME, "optimization checkpoint"
                )
                if (
                    state.get("schema_version") != OPTIMIZATION_SCHEMA_VERSION
                    or state.get("kind") != "multisim-mcp-design-optimization"
                ):
                    raise ValueError("optimization checkpoint contract is invalid")
                if (
                    state.get("design_digest") != _digest(design.to_dict())
                    or state.get("optimization_spec_digest") != _digest(normalized)
                    or state.get("candidate_space_size") != candidate_space_size
                    or state.get("max_experiments") != normalized["max_experiments"]
                    or _canonical_bytes(state.get("runtime")) != _canonical_bytes(runtime)
                ):
                    raise ValueError(
                        "optimization checkpoint does not match design, spec, or runtime"
                    )
                stored_id = state.get("optimization_id")
                if not isinstance(stored_id, str) or not _OPTIMIZATION_ID_RE.fullmatch(
                    stored_id
                ):
                    raise ValueError("optimization checkpoint id is invalid")
                optimization_id = stored_id
                _write_or_validate_json(
                    root / BASELINE_DESIGN_NAME,
                    design.to_dict(),
                    "baseline design",
                )
                _write_or_validate_json(
                    root / OPTIMIZATION_SPEC_NAME,
                    normalized,
                    "optimization spec",
                )
                _write_or_validate_json(
                    root / VERIFICATION_PLAN_NAME,
                    verification_plan,
                    "verification plan",
                )
                evaluations = _resume_evaluations(root, state, planned, normalized)
                resumed_at = _utc_now()
                state.update(
                    state="running",
                    status="running",
                    stop_reason=None,
                    updated_at=resumed_at,
                    finished_at=None,
                    resume_count=int(state.get("resume_count", 0)) + 1,
                    last_resumed_at=resumed_at,
                    evaluations=evaluations,
                    experiments_attempted=len(evaluations),
                )
                _atomic_json(root / OPTIMIZATION_STATE_NAME, state)
                notify(
                    "optimization_recovered",
                    2,
                    f"Recovered {len(evaluations)} persisted evaluation checkpoints",
                )
            else:
                started_at = _utc_now()
                state = {
                    "schema_version": OPTIMIZATION_SCHEMA_VERSION,
                    "kind": "multisim-mcp-design-optimization",
                    "optimization_id": optimization_id,
                    "state": "running",
                    "status": "running",
                    "stop_reason": None,
                    "started_at": started_at,
                    "updated_at": started_at,
                    "finished_at": None,
                    "resume_count": 0,
                    "last_resumed_at": None,
                    "design_id": design.design_id,
                    "design_revision": design.revision,
                    "design_digest": _digest(design.to_dict()),
                    "optimization_spec_digest": _digest(normalized),
                    "source_design_modified": False,
                    "candidate_space_size": candidate_space_size,
                    "max_experiments": normalized["max_experiments"],
                    "experiments_attempted": 0,
                    "experiment_attempt_count": 0,
                    "feasible_solution_count": 0,
                    "procurement_rejected_count": 0,
                    "best_evaluation_id": None,
                    "evaluations": [],
                    "runtime": runtime,
                }
                evaluations = []
                _atomic_json(root / OPTIMIZATION_STATE_NAME, state)
                _write_or_validate_json(
                    root / BASELINE_DESIGN_NAME,
                    design.to_dict(),
                    "baseline design",
                )
                _write_or_validate_json(
                    root / OPTIMIZATION_SPEC_NAME,
                    normalized,
                    "optimization spec",
                )
                _write_or_validate_json(
                    root / VERIFICATION_PLAN_NAME,
                    verification_plan,
                    "verification plan",
                )
                notify("optimization_preflight", 2, "Validated bounded candidate space")

            cancelled = False
            total = len(planned)
            for index, (evaluation_id, values, prepared) in enumerate(planned):
                if cancel_requested is not None and cancel_requested():
                    cancelled = True
                    break
                if index < len(evaluations) and evaluations[index]["status"] != "interrupted":
                    notify(
                        "optimization_resume_skip",
                        5 + int((index + 1) / total * 85),
                        f"Reused completed {evaluation_id} ({index + 1}/{total})",
                    )
                    continue
                if prepared is not None:
                    _write_or_validate_json(
                        root / "patches" / f"{evaluation_id}.json",
                        prepared.patch.to_dict(),
                        f"{evaluation_id} patch",
                    )
                if index < len(evaluations):
                    evaluation = evaluations[index]
                    attempt = int(evaluation.get("attempt", 1)) + 1
                    interrupted_attempts = list(evaluation.get("interrupted_attempts", []))
                    interrupted_attempts.append(
                        {
                            "attempt": attempt - 1,
                            "experiment_output": evaluation.get("experiment_output"),
                            "error": evaluation.get("error"),
                        }
                    )
                    evaluation.update(
                        status="running",
                        objective=None,
                        experiment=None,
                        error=None,
                        attempt=attempt,
                        interrupted_attempts=interrupted_attempts,
                    )
                else:
                    attempt = 1
                    evaluation = {
                        "evaluation_id": evaluation_id,
                        "index": index,
                        "kind": "baseline" if prepared is None else "candidate",
                        "values": values,
                        "patch_path": (
                            None if prepared is None else f"patches/{evaluation_id}.json"
                        ),
                        "status": "running",
                        "attempt": attempt,
                        "interrupted_attempts": [],
                        "objective": None,
                        "procurement": _procurement_result(normalized, values),
                        "experiment": None,
                        "error": None,
                    }
                    evaluations.append(evaluation)
                experiment_name = (
                    evaluation_id
                    if attempt == 1
                    else f"{evaluation_id}-attempt-{attempt:03d}"
                )
                experiment_dir = root / "experiments" / experiment_name
                evaluation["experiment_output"] = (
                    Path("experiments") / experiment_name
                ).as_posix()
                state["evaluations"] = evaluations
                state["experiments_attempted"] = len(evaluations)
                state["experiment_attempt_count"] = sum(
                    int(item.get("attempt", 1)) for item in evaluations
                )
                state["updated_at"] = _utc_now()
                _atomic_json(root / OPTIMIZATION_STATE_NAME, state)
                progress = 5 + int(index / total * 85)
                notify(
                    "optimization_experiment",
                    progress,
                    f"Running {evaluation_id} ({index + 1}/{total})",
                )
                candidate_design = design if prepared is None else prepared.candidate
                try:
                    result = self._experiments.run(
                        ExperimentRequest(
                            design=candidate_design,
                            commands=normalized["commands"],
                            output_directory=str(experiment_dir),
                            title=f"{normalized['title']} - {evaluation_id}",
                            timeout_seconds=float(timeout_per_experiment),
                            max_points=max_points,
                            overwrite=False,
                            owner=optimization_id,
                            requirements=tuple(normalized["requirements"]),
                            theoretical_values=normalized["theoretical_values"],
                        ),
                        cancel_requested=cancel_requested,
                    )
                    if not result["success"]:
                        raise RuntimeError("experiment runner reported failure")
                    evidence, objective = validate_ranked_experiment_evidence(
                        root,
                        result,
                        normalized["objective"],
                        workflow_name="optimization",
                    )
                    evaluation["experiment"] = evidence
                    evaluation["objective"] = objective
                    evaluation["status"] = _expected_evaluation_status(
                        evidence, objective, evaluation["procurement"]
                    )
                except InterruptedError as exc:
                    evaluation["status"] = "interrupted"
                    evaluation["error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc)[:1000],
                    }
                    cancelled = True
                except Exception as exc:  # one failed candidate must not hide later evidence
                    evaluation["status"] = "error"
                    evaluation["error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc)[:1000],
                    }
                state["updated_at"] = _utc_now()
                _atomic_json(root / OPTIMIZATION_STATE_NAME, state)
                if cancelled:
                    break

            feasible = [item for item in evaluations if item["status"] == "feasible"]
            feasible.sort(
                key=lambda item: (
                    float(item["objective"]["score"]),
                    (
                        float(item["procurement"]["total_unit_cost"])
                        if item["procurement"]["prefer_lower_cost"]
                        else 0.0
                    ),
                    int(item["index"]),
                )
            )
            best = feasible[0] if feasible else None
            procurement_rejected_count = sum(
                item["procurement"]["status"] == "fail" for item in evaluations
            )
            if cancelled:
                status = "cancelled"
                stop_reason = "cancellation_requested"
                manifest_state = "cancelled"
            elif best is None:
                status = "no_feasible_candidate"
                stop_reason = (
                    "budget_exhausted" if budget_exhausted else "candidate_space_exhausted"
                )
                manifest_state = "succeeded"
            else:
                status = "baseline_best" if best["kind"] == "baseline" else "optimized"
                stop_reason = (
                    "budget_exhausted" if budget_exhausted else "candidate_space_exhausted"
                )
                manifest_state = "succeeded"

            if best is not None and best["patch_path"] is not None:
                best_patch = json.loads(
                    (root / str(best["patch_path"])).read_text(encoding="utf-8")
                )
                _atomic_json(root / BEST_PATCH_NAME, best_patch)
            state.update(
                {
                    "state": manifest_state,
                    "status": status,
                    "stop_reason": stop_reason,
                    "updated_at": _utc_now(),
                    "finished_at": _utc_now(),
                    "experiments_attempted": len(evaluations),
                    "experiment_attempt_count": sum(
                        int(item.get("attempt", 1)) for item in evaluations
                    ),
                    "feasible_solution_count": len(feasible),
                    "procurement_rejected_count": procurement_rejected_count,
                    "best_evaluation_id": (
                        None if best is None else best["evaluation_id"]
                    ),
                    "ranked_feasible_evaluation_ids": [
                        item["evaluation_id"] for item in feasible
                    ],
                }
            )
            _atomic_json(root / OPTIMIZATION_STATE_NAME, state)
            _write_candidates_csv(root, evaluations)
            manifest = write_directory_manifest(
                root,
                directory_kind="optimization",
                entity_id=optimization_id,
                state=manifest_state,
                artifacts=_artifact_allowlist(root),
                metadata={
                    "operation": "optimize-design",
                    "status": status,
                    "stop_reason": stop_reason,
                    "design_id": design.design_id,
                    "design_revision": design.revision,
                    "experiments_attempted": len(evaluations),
                    "feasible_solution_count": len(feasible),
                    "procurement_rejected_count": procurement_rejected_count,
                    "source_design_modified": False,
                },
            )
            notify("complete", 100, f"Optimization finished: {status}")
            best_patch_path = (
                str(root / BEST_PATCH_NAME)
                if best is not None and best["patch_path"] is not None
                else None
            )
            return {
                "schema_version": OPTIMIZATION_SCHEMA_VERSION,
                "success": status in {"optimized", "baseline_best"},
                "status": status,
                "stop_reason": stop_reason,
                "optimization_id": optimization_id,
                "output_dir": str(root),
                "summary": str(root / OPTIMIZATION_STATE_NAME),
                "data": str(root / OPTIMIZATION_DATA_NAME),
                "verification_plan": str(root / VERIFICATION_PLAN_NAME),
                "directory_manifest": str(root / DIRECTORY_MANIFEST_NAME),
                "directory_manifest_revision": manifest.revision,
                "source_design_modified": False,
                "experiments_attempted": len(evaluations),
                "experiment_attempt_count": sum(
                    int(item.get("attempt", 1)) for item in evaluations
                ),
                "resume_count": int(state.get("resume_count", 0)),
                "candidate_space_size": candidate_space_size,
                "feasible_solution_count": len(feasible),
                "procurement_rejected_count": procurement_rejected_count,
                "best_solution": (
                    None
                    if best is None
                    else {
                        "evaluation_id": best["evaluation_id"],
                        "kind": best["kind"],
                        "values": best["values"],
                        "objective": best["objective"],
                        "procurement": best["procurement"],
                        "patch_path": best_patch_path,
                        "verification_plan_path": str(root / VERIFICATION_PLAN_NAME),
                        "requires_approval_to_persist": best["kind"] == "candidate",
                        "regenerate_source_netlist": design.source_netlist is not None,
                    }
                ),
            }


def read_design_optimization(output_directory: str, *, verify: bool = True) -> dict[str, Any]:
    """Read a completed optimization summary and optionally verify every artifact."""
    unresolved_root = Path(output_directory).expanduser()
    if unresolved_root.is_symlink():
        raise ValueError("optimization directory must not be a symbolic link")
    root = unresolved_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"optimization directory does not exist: {root}")
    path = root / OPTIMIZATION_STATE_NAME
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"optimization summary is missing: {path}")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("optimization summary exceeds the size limit")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {constant}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError("optimization summary must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"optimization summary is not valid JSON: {exc}") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != OPTIMIZATION_SCHEMA_VERSION
        or value.get("kind") != "multisim-mcp-design-optimization"
    ):
        raise ValueError("optimization summary contract is invalid")
    if verify:
        manifest = read_directory_manifest(root, verify=True)
        if (
            manifest.directory_kind != "optimization"
            or manifest.entity_id != value.get("optimization_id")
            or manifest.state != value.get("state")
        ):
            raise ValueError("optimization directory manifest does not match summary")
    return _plain_json(value)


__all__ = [
    "BASELINE_DESIGN_NAME",
    "BEST_PATCH_NAME",
    "DesignOptimizationService",
    "MAX_OPTIMIZATION_EXPERIMENTS",
    "MAX_OPTIMIZATION_SPEC_BYTES",
    "OPTIMIZATION_DATA_NAME",
    "OPTIMIZATION_SCHEMA_VERSION",
    "OPTIMIZATION_SPEC_NAME",
    "OPTIMIZATION_STATE_NAME",
    "VERIFICATION_PLAN_NAME",
    "read_design_optimization",
    "read_optimization_spec",
    "validate_optimization_spec",
]
