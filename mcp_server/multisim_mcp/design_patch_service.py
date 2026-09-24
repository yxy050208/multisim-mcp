"""Transport-neutral validation and in-memory application of DesignPatch values."""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass
from typing import Any, Final, Mapping

from .eda_core import CircuitComponent, CircuitDesign, DesignPatch, PatchOperation
from .spice_adapter import circuit_design_to_spice


MAX_DESIGN_PATCH_OPERATIONS: Final = 64
_COMPONENT_VALUE_TARGET = re.compile(
    r"^(?P<refdes>[A-Za-z][A-Za-z0-9_.-]{0,63})\.value$"
)
_COMPONENT_NODES_TARGET = re.compile(
    r"^(?P<refdes>[A-Za-z][A-Za-z0-9_.-]{0,63})\.nodes$"
)
_COMPONENT_MODEL_TARGET = re.compile(
    r"^(?P<refdes>[A-Za-z][A-Za-z0-9_.-]{0,63})\.model$"
)
_COMPONENT_TARGET = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MISSING = object()


def _normalize_json(value: object, depth: int = 0) -> Any:
    if depth > 24:
        raise ValueError("patch value exceeds the JSON nesting limit")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("patch values must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("patch value keys must be non-empty strings")
            result[key] = _normalize_json(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item, depth + 1) for item in value]
    raise ValueError("patch values must contain finite JSON")


def _plain_json(value: object) -> Any:
    return json.loads(
        json.dumps(
            _normalize_json(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    )


def _same_json(left: object, right: object) -> bool:
    try:
        return _plain_json(left) == _plain_json(right)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("patch values must contain finite JSON") from exc


def _map_target(target: str, operation: str) -> str:
    if not target or len(target) > 255 or _CONTROL_RE.search(target):
        raise ValueError(f"{operation} target is invalid")
    return target


def _operation_key(operation: PatchOperation) -> tuple[str, str]:
    if operation.operation == "set_component_value":
        match = _COMPONENT_VALUE_TARGET.fullmatch(operation.target)
        if match is None:
            raise ValueError(
                "set_component_value target must use the form <refdes>.value"
            )
        return operation.operation, match.group("refdes").casefold()
    if operation.operation == "set_component_nodes":
        match = _COMPONENT_NODES_TARGET.fullmatch(operation.target)
        if match is None:
            raise ValueError(
                "set_component_nodes target must use the form <refdes>.nodes"
            )
        return operation.operation, match.group("refdes").casefold()
    if operation.operation == "set_component_model":
        match = _COMPONENT_MODEL_TARGET.fullmatch(operation.target)
        if match is None:
            raise ValueError(
                "set_component_model target must use the form <refdes>.model"
            )
        return operation.operation, match.group("refdes").casefold()
    if operation.operation in {
        "add_component",
        "remove_component",
        "replace_component",
    }:
        if _COMPONENT_TARGET.fullmatch(operation.target) is None:
            raise ValueError(f"{operation.operation} target must be a refdes")
        return "component", operation.target.casefold()
    if operation.operation in {"add_net", "remove_net"}:
        return "net", _map_target(operation.target, operation.operation).casefold()
    return operation.operation, _map_target(operation.target, operation.operation)


def _component_payload(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a CircuitComponent object")
    component = CircuitComponent.from_dict(value)
    return component.to_dict()


def _component_index(
    components: list[dict[str, Any]], refdes: str
) -> tuple[int, dict[str, Any]] | None:
    folded = refdes.casefold()
    for index, component in enumerate(components):
        if str(component["refdes"]).casefold() == folded:
            return index, component
    return None


def _validate_nodes(
    value: object,
    *,
    component_kind: str,
    known_nets: set[str],
    name: str,
) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an array")
    nodes = list(CircuitComponent("P1", component_kind, tuple(value)).nodes)
    missing = sorted(node for node in nodes if node.casefold() not in known_nets)
    if missing:
        raise ValueError(f"{name} references unknown nets: {missing}")
    return nodes


def _apply_operation_to_payload(
    payload: dict[str, Any], operation: PatchOperation
) -> bool:
    """Apply one compare-and-swap operation and return source relevance."""
    serialized = operation.to_dict()
    before = serialized["before"]
    after = serialized["after"]
    components: list[dict[str, Any]] = payload["components"]
    nets: list[str] = payload["nets"]
    known_nets = {net.casefold() for net in nets}

    if operation.operation == "set_component_value":
        match = _COMPONENT_VALUE_TARGET.fullmatch(operation.target)
        assert match is not None
        found = _component_index(components, match.group("refdes"))
        if found is None:
            raise ValueError("patch component target does not exist")
        _, component = found
        if not _same_json(before, component.get("value")):
            raise ValueError("patch before value does not match the fixed design")
        if after is not None and not isinstance(after, str):
            raise ValueError("component value after must be a string or null")
        component["value"] = after
        return True

    if operation.operation in {"set_component_nodes", "set_component_model"}:
        pattern = (
            _COMPONENT_NODES_TARGET
            if operation.operation == "set_component_nodes"
            else _COMPONENT_MODEL_TARGET
        )
        match = pattern.fullmatch(operation.target)
        assert match is not None
        found = _component_index(components, match.group("refdes"))
        if found is None:
            raise ValueError("patch component target does not exist")
        _, component = found
        field = "nodes" if operation.operation == "set_component_nodes" else "model"
        if not _same_json(before, component.get(field)):
            raise ValueError("patch before value does not match the fixed design")
        if field == "nodes":
            component[field] = _validate_nodes(
                after,
                component_kind=str(component["kind"]),
                known_nets=known_nets,
                name="component nodes after",
            )
        else:
            if after is not None and not isinstance(after, str):
                raise ValueError("component model after must be a string or null")
            probe = dict(component)
            probe[field] = after
            component[field] = CircuitComponent.from_dict(probe).model
        return True

    if operation.operation in {
        "add_component",
        "remove_component",
        "replace_component",
    }:
        found = _component_index(components, operation.target)
        if operation.operation == "add_component":
            if before is not None or found is not None:
                raise ValueError("add_component requires a missing component and null before")
            component = _component_payload(after, name="add_component after")
            if component["refdes"].casefold() != operation.target.casefold():
                raise ValueError("add_component target must match after.refdes")
            _validate_nodes(
                component["nodes"],
                component_kind=str(component["kind"]),
                known_nets=known_nets,
                name="add_component nodes",
            )
            components.append(component)
            return True
        if found is None:
            raise ValueError("patch component target does not exist")
        index, current = found
        if not _same_json(before, current):
            raise ValueError("patch before component does not match the fixed design")
        if operation.operation == "remove_component":
            if after is not None:
                raise ValueError("remove_component after must be null")
            components.pop(index)
            return True
        replacement = _component_payload(after, name="replace_component after")
        if replacement["refdes"].casefold() != operation.target.casefold():
            raise ValueError("replace_component target must match after.refdes")
        _validate_nodes(
            replacement["nodes"],
            component_kind=str(replacement["kind"]),
            known_nets=known_nets,
            name="replace_component nodes",
        )
        components[index] = replacement
        return True

    if operation.operation in {"add_net", "remove_net"}:
        matching_index = next(
            (index for index, net in enumerate(nets) if net.casefold() == operation.target.casefold()),
            None,
        )
        if operation.operation == "add_net":
            if before is not None or matching_index is not None:
                raise ValueError("add_net requires a missing net and null before")
            if not isinstance(after, str) or after != operation.target:
                raise ValueError("add_net after must exactly match the target net")
            CircuitComponent("P1", "R", (after, "0"), value="1")
            nets.append(after)
            return True
        if matching_index is None:
            raise ValueError("remove_net target does not exist")
        current = nets[matching_index]
        if not _same_json(before, current) or after is not None:
            raise ValueError("remove_net requires the exact current net and null after")
        users = [
            str(component["refdes"])
            for component in components
            if any(str(node).casefold() == current.casefold() for node in component["nodes"])
        ]
        if users:
            raise ValueError(f"remove_net target is still used by components: {users}")
        nets.pop(matching_index)
        return True

    values = (
        payload["parameters"]
        if operation.operation == "set_parameter"
        else payload["annotations"]
    )
    current = values.get(operation.target, _MISSING)
    if current is None and operation.target in values:
        raise ValueError("null-valued map targets cannot be patched reversibly")
    expected = None if current is _MISSING else current
    if not _same_json(before, expected):
        raise ValueError("patch before value does not match the fixed design")
    if after is None:
        values.pop(operation.target, None)
    else:
        values[operation.target] = after
    return operation.operation == "set_parameter"


def validate_design_patch(
    design: CircuitDesign,
    patch: DesignPatch | Mapping[str, Any],
) -> DesignPatch:
    """Validate patch identity, targets, and compare-and-swap before values."""
    if not isinstance(design, CircuitDesign):
        raise ValueError("design must be CircuitDesign")
    normalized = patch if isinstance(patch, DesignPatch) else DesignPatch.from_dict(patch)
    if normalized.design_id != design.design_id:
        raise ValueError("patch design_id does not match the fixed design")
    if normalized.base_revision != design.revision:
        raise ValueError("patch base_revision does not match the fixed design")
    if len(normalized.operations) > MAX_DESIGN_PATCH_OPERATIONS:
        raise ValueError(
            f"patch may contain at most {MAX_DESIGN_PATCH_OPERATIONS} operations"
        )
    payload = design.to_dict()
    seen: set[tuple[str, str]] = set()
    for operation in normalized.operations:
        key = _operation_key(operation)
        if key in seen:
            raise ValueError("patch contains duplicate targets")
        seen.add(key)
        _apply_operation_to_payload(payload, operation)
    payload["revision"] = design.revision + 1
    CircuitDesign.from_dict(payload)
    return normalized


@dataclass(frozen=True, slots=True)
class PreparedDesignPatch:
    """One validated in-memory candidate and its rollback contract."""

    patch: DesignPatch
    candidate: CircuitDesign
    inverse_patch: DesignPatch
    source_netlist_update_required: bool
    source_netlist_regenerated: bool


def prepare_design_patch(
    design: CircuitDesign,
    patch: DesignPatch | Mapping[str, Any],
    *,
    regenerate_source_netlist: bool = False,
) -> PreparedDesignPatch:
    """Apply a patch to an in-memory copy, optionally rebuilding safe SPICE source."""
    if not isinstance(regenerate_source_netlist, bool):
        raise ValueError("regenerate_source_netlist must be a boolean")
    normalized = validate_design_patch(design, patch)
    payload = design.to_dict()
    source_relevant_change = False
    for operation in normalized.operations:
        source_relevant_change = (
            _apply_operation_to_payload(payload, operation) or source_relevant_change
        )
    payload["revision"] = design.revision + 1
    candidate = CircuitDesign.from_dict(payload)
    update_required = candidate.source_netlist is not None and source_relevant_change
    if regenerate_source_netlist and not update_required:
        raise ValueError("source netlist regeneration is not required for this patch")
    regenerated = False
    if update_required and regenerate_source_netlist:
        compiled = circuit_design_to_spice(candidate, prefer_source=False)
        candidate_payload = candidate.to_dict()
        candidate_payload["source_netlist"] = compiled
        candidate = CircuitDesign.from_dict(candidate_payload)
        regenerated = True
        update_required = False
    return PreparedDesignPatch(
        patch=normalized,
        candidate=candidate,
        inverse_patch=normalized.inverse(),
        source_netlist_update_required=update_required,
        source_netlist_regenerated=regenerated,
    )


def create_design_diff_patch(
    before: CircuitDesign,
    after: CircuitDesign,
    *,
    patch_id: str | None = None,
    description: str = "Consolidated design changes",
    metadata: Mapping[str, Any] | None = None,
) -> DesignPatch:
    """Create one reversible patch that transforms ``before`` into ``after``.

    Identity and revision belong to ``before``; ``after`` may be the result of
    several autonomous in-memory rounds. Design title and model-reference edits
    are intentionally rejected until they have dedicated patch operations.
    """
    if not isinstance(before, CircuitDesign) or not isinstance(after, CircuitDesign):
        raise ValueError("before and after must be CircuitDesign")
    if before.design_id != after.design_id:
        raise ValueError("design diff requires matching design_id")
    if before.title != after.title or before.model_references != after.model_references:
        raise ValueError("design diff does not support title or model-reference changes")
    before_components = {item.refdes.casefold(): item for item in before.components}
    after_components = {item.refdes.casefold(): item for item in after.components}
    before_nets = {item.casefold(): item for item in before.nets}
    after_nets = {item.casefold(): item for item in after.nets}
    operations: list[PatchOperation] = []

    # Remove deleted components first so nets can become unused.
    for key, component in before_components.items():
        if key not in after_components:
            operations.append(
                PatchOperation(
                    "remove_component",
                    component.refdes,
                    component.to_dict(),
                    None,
                    "Component is absent from the consolidated candidate",
                )
            )
    # New nets must exist before a replacement or addition can reference them.
    for key, net in after_nets.items():
        if key not in before_nets:
            operations.append(
                PatchOperation(
                    "add_net",
                    net,
                    None,
                    net,
                    "Net is required by the consolidated candidate",
                )
            )
    for key, component in before_components.items():
        candidate = after_components.get(key)
        if candidate is not None and component.to_dict() != candidate.to_dict():
            operations.append(
                PatchOperation(
                    "replace_component",
                    component.refdes,
                    component.to_dict(),
                    candidate.to_dict(),
                    "Component differs in the consolidated candidate",
                )
            )
    for key, component in after_components.items():
        if key not in before_components:
            operations.append(
                PatchOperation(
                    "add_component",
                    component.refdes,
                    None,
                    component.to_dict(),
                    "Component is required by the consolidated candidate",
                )
            )
    for key, net in before_nets.items():
        if key not in after_nets:
            operations.append(
                PatchOperation(
                    "remove_net",
                    net,
                    net,
                    None,
                    "Net is absent from the consolidated candidate",
                )
            )
    for operation_name, before_map, after_map in (
        ("set_parameter", before.parameters, after.parameters),
        ("set_annotation", before.annotations, after.annotations),
    ):
        for key in sorted(set(before_map) | set(after_map)):
            left = before_map.get(key)
            right = after_map.get(key)
            if _same_json(left, right):
                continue
            if key in before_map and left is None:
                raise ValueError("null-valued map targets cannot be diffed reversibly")
            operations.append(
                PatchOperation(
                    operation_name,
                    key,
                    left,
                    right,
                    "Metadata differs in the consolidated candidate",
                )
            )
    if not operations:
        raise ValueError("design diff contains no changes")
    patch = DesignPatch(
        patch_id=patch_id or f"design-diff-{uuid.uuid4().hex}",
        design_id=before.design_id,
        base_revision=before.revision,
        operations=tuple(operations),
        description=description,
        metadata=dict(metadata or {}),
    )
    # This also proves the generated operation order and before values.
    prepared = prepare_design_patch(
        before,
        patch,
        regenerate_source_netlist=before.source_netlist is not None,
    )
    expected = after.to_dict()
    actual = prepared.candidate.to_dict()
    expected["revision"] = actual["revision"]
    if before.source_netlist is not None:
        expected["source_netlist"] = actual["source_netlist"]
    if actual != expected:
        raise RuntimeError("generated design diff does not reproduce the candidate")
    return patch


__all__ = [
    "MAX_DESIGN_PATCH_OPERATIONS",
    "PreparedDesignPatch",
    "create_design_diff_patch",
    "prepare_design_patch",
    "validate_design_patch",
]
