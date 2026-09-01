"""Explicit conversion between limited SPICE and the transport-neutral EDA IR."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Any

from .eda_core import CircuitComponent, CircuitDesign, ModelReference
from .safety import validate_spice_netlist
from .schematic_builder import (
    COMPONENT_DEFINITIONS,
    DIGITAL_MODEL_KINDS,
    ComponentSpec,
    ParsedNetlist,
    parse_netlist,
)


_SPICE_TOKEN = re.compile(r"^[^\s\x00]+$")
_MODEL_COMPONENT_KINDS = frozenset(
    {
        "D",
        "S",
        "QNPN",
        "QPNP",
        "MNMOS",
        "MPMOS",
        "JN",
        "JP",
        "ZN",
        "ZP",
        "W",
        "O",
        "U",
    }
) | frozenset(DIGITAL_MODEL_KINDS.values())

# Native vendor-backed carriers are emitted as portable X instances when a
# structured design is rebuilt.  They do not require an inline .model record;
# the user-local Multisim component pack supplies their native identity.
_NATIVE_CARRIER_KINDS = frozenset({"TIMER8", "DFF8"})


def _stable_design_id(netlist: str) -> str:
    digest = hashlib.sha256(netlist.encode("utf-8")).hexdigest()[:20]
    return f"spice:{digest}"


def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    current: str | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("+") and current is not None:
            current += " " + stripped[1:].lstrip()
            continue
        if current is not None:
            lines.append(current)
        current = stripped
    if current is not None:
        lines.append(current)
    return lines


def _top_level_parameters(lines: Sequence[str]) -> dict[str, str]:
    parameters: dict[str, str] = {}
    subcircuit_depth = 0
    for line in lines:
        lowered = line.lower()
        if lowered.startswith(".subckt"):
            subcircuit_depth += 1
            continue
        if lowered.startswith(".ends"):
            subcircuit_depth = max(0, subcircuit_depth - 1)
            continue
        if subcircuit_depth or not lowered.startswith(".param"):
            continue
        for token in line.split()[1:]:
            if "=" not in token:
                continue
            name, value = token.split("=", 1)
            if name and value:
                parameters[name] = value
    return parameters


def _inline_model_references(
    lines: Sequence[str], parsed: ParsedNetlist
) -> tuple[ModelReference, ...]:
    references: list[ModelReference] = []
    seen: set[str] = set()
    for definition in parsed.subcircuits.values():
        name = f"subckt:{definition.name}"
        normalized = name.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        references.append(
            ModelReference(
                name=name,
                source="inline-subcircuit",
                sha256=hashlib.sha256(definition.text.encode("utf-8")).hexdigest(),
            )
        )
    for line in lines:
        if not line.lower().startswith(".model"):
            continue
        parts = line.split(maxsplit=2)
        if len(parts) < 3:
            continue
        name = f"model:{parts[1]}"
        normalized = name.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        references.append(
            ModelReference(
                name=name,
                source="inline-model",
                sha256=hashlib.sha256(line.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(references)


def _component_from_spec(spec: ComponentSpec) -> CircuitComponent:
    annotations: dict[str, Any] = {}
    if spec.model_definition:
        annotations["has_inline_model_definition"] = True
    return CircuitComponent(
        refdes=spec.refdes,
        kind=spec.kind,
        nodes=tuple(spec.nodes),
        value=spec.value,
        model=spec.model,
        parameters={"tokens": list(spec.parameters)} if spec.parameters else {},
        annotations=annotations,
    )


def circuit_design_from_spice(
    netlist: str,
    *,
    design_id: str | None = None,
    title: str = "Imported SPICE design",
    allow_unsupported: bool = False,
) -> CircuitDesign:
    """Parse a safe SPICE netlist into a versioned :class:`CircuitDesign`.

    The original safe netlist remains authoritative.  Structured components are
    the editable representation produced by the current parser, so compatible
    inline subcircuits may appear as their expanded primitive components.
    """

    validate_spice_netlist(netlist)
    parsed = parse_netlist(netlist)
    if parsed.unsupported and not allow_unsupported:
        preview = "; ".join(parsed.unsupported[:8])
        raise ValueError(f"SPICE contains unsupported schematic records: {preview}")
    lines = _logical_lines(netlist)
    components = tuple(_component_from_spec(spec) for spec in parsed.components)
    annotations: dict[str, Any] = {
        "spice_import": {
            "unsupported": list(parsed.unsupported),
            "expanded_subcircuits": list(parsed.expanded_subcircuits),
            "subcircuit_expansion_failures": list(
                parsed.subcircuit_expansion_failures
            ),
            "grounded": parsed.grounded,
        }
    }
    return CircuitDesign(
        design_id=design_id or _stable_design_id(netlist),
        title=title,
        components=components,
        parameters=_top_level_parameters(lines),
        model_references=_inline_model_references(lines, parsed),
        annotations=annotations,
        source_netlist=netlist,
    )


def _token(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SPICE_TOKEN.fullmatch(value):
        raise ValueError(f"{name} must be one SPICE token")
    return value


def _tail(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    rendered = value.strip()
    if not rendered or "\x00" in rendered or "\n" in rendered or "\r" in rendered:
        raise ValueError(f"{name} must be a single non-empty SPICE record tail")
    return rendered


def _parameter_tokens(component: CircuitComponent) -> list[str]:
    raw = component.parameters.get("tokens", ())
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"{component.refdes}.parameters.tokens must be an array")
    return [_token(item, f"{component.refdes}.parameter") for item in raw]


def _nodes(component: CircuitComponent, count: int) -> list[str]:
    if len(component.nodes) != count:
        raise ValueError(
            f"{component.refdes} ({component.kind}) requires {count} nodes, "
            f"received {len(component.nodes)}"
        )
    return [_token(node, f"{component.refdes}.node") for node in component.nodes]


def _require_value(component: CircuitComponent) -> str:
    if component.value is None:
        raise ValueError(f"{component.refdes} requires a value")
    return _token(component.value, f"{component.refdes}.value")


def _require_model(component: CircuitComponent, *, tail: bool = False) -> str:
    if component.model is None:
        raise ValueError(f"{component.refdes} requires a model or source expression")
    return (
        _tail(component.model, f"{component.refdes}.model")
        if tail
        else _token(component.model, f"{component.refdes}.model")
    )


def _component_to_spice(component: CircuitComponent) -> str:
    refdes = _token(component.refdes, "component.refdes")
    kind = component.kind.upper()
    parameters = _parameter_tokens(component)
    if kind in {"R", "C", "L"}:
        return " ".join([refdes, *_nodes(component, 2), _require_value(component)])
    if kind in {"V", "I"}:
        source = (
            _require_value(component)
            if component.value is not None
            else _require_model(component, tail=True)
        )
        return " ".join([refdes, *_nodes(component, 2), source])
    if kind in {"E", "G"}:
        return " ".join([refdes, *_nodes(component, 4), _require_value(component)])
    if kind in {"F", "H"}:
        if len(parameters) != 1:
            raise ValueError(f"{refdes} requires one controlling source parameter")
        return " ".join(
            [refdes, *_nodes(component, 2), parameters[0], _require_value(component)]
        )
    if kind in {"BV", "BI"}:
        expression = _require_model(component, tail=True)
        expected = f"{kind[-1]}="
        if not expression.upper().startswith(expected):
            raise ValueError(f"{refdes} expression must start with {expected}")
        return " ".join([f"B{refdes[1:]}", *_nodes(component, 2), expression])
    if kind == "T":
        return " ".join(
            [refdes, *_nodes(component, 4), _require_model(component, tail=True)]
        )
    if kind == "K":
        if len(parameters) != 2:
            raise ValueError(f"{refdes} requires two inductor references")
        return " ".join([refdes, *parameters, _require_value(component)])
    if kind in {"D", "S"}:
        count = 2 if kind == "D" else 4
        return " ".join(
            [refdes, *_nodes(component, count), _require_model(component), *parameters]
        )
    if kind in {"QNPN", "QPNP"}:
        return " ".join(
            [
                f"Q{refdes[1:]}",
                *_nodes(component, 3),
                _require_model(component),
                *parameters,
            ]
        )
    if kind in {"MNMOS", "MPMOS"}:
        return " ".join(
            [
                f"M{refdes[1:]}",
                *_nodes(component, 4),
                _require_model(component),
                *parameters,
            ]
        )
    if kind in {"JN", "JP", "ZN", "ZP"}:
        prefix = "J" if kind.startswith("J") else "Z"
        return " ".join(
            [
                f"{prefix}{refdes[1:]}",
                *_nodes(component, 3),
                _require_model(component),
                *parameters,
            ]
        )
    if kind == "W":
        if not parameters:
            raise ValueError(f"{refdes} requires a controlling source parameter")
        return " ".join(
            [
                refdes,
                *_nodes(component, 2),
                parameters[0],
                _require_model(component),
                *parameters[1:],
            ]
        )
    if kind in {"O", "U"}:
        count = 4 if kind == "O" else 3
        return " ".join(
            [refdes, *_nodes(component, count), _require_model(component), *parameters]
        )
    if kind in _NATIVE_CARRIER_KINDS:
        if len(component.nodes) != 8:
            raise ValueError(f"{refdes} native carrier requires 8 nodes")
        return " ".join(
            [
                refdes if refdes[:1].upper() == "X" else f"X{refdes}",
                *(_token(node, f"{refdes}.node") for node in component.nodes),
                _require_model(component),
                *parameters,
            ]
        )
    if kind == "OPAMP5" or kind.startswith("XSUB"):
        if not 2 <= len(component.nodes) <= 16:
            raise ValueError(f"{refdes} subcircuit requires 2 to 16 nodes")
        return " ".join(
            [
                f"X{refdes[1:]}",
                *(_token(node, f"{refdes}.node") for node in component.nodes),
                _require_model(component),
                *parameters,
            ]
        )
    if kind in set(DIGITAL_MODEL_KINDS.values()):
        expected_nodes = len(COMPONENT_DEFINITIONS[kind].port_templates)
        return " ".join(
            [
                f"A{refdes[1:]}",
                *_nodes(component, expected_nodes),
                _require_model(component),
            ]
        )
    if kind == "XFG3":
        return " ".join(
            [f"XFG{refdes[3:]}", *_nodes(component, 3), "FGEN", *parameters]
        )
    if kind == "OSC6":
        return " ".join(
            [f"XSC{refdes[3:]}", *_nodes(component, 6), "OSCILLOSCOPE"]
        )
    raise ValueError(
        f"{refdes} uses unsupported structured SPICE component kind {component.kind!r}"
    )


def _parameter_lines(design: CircuitDesign) -> list[str]:
    lines: list[str] = []
    for name, value in design.parameters.items():
        parameter_name = _token(name, "design parameter name")
        if isinstance(value, bool):
            rendered = "1" if value else "0"
        elif isinstance(value, (str, int, float)):
            rendered = _token(str(value), f"design parameter {parameter_name}")
        else:
            raise ValueError(
                f"design parameter {parameter_name!r} is not a scalar SPICE value"
            )
        lines.append(f".param {parameter_name}={rendered}")
    return lines


def _referenced_inline_definitions(design: CircuitDesign) -> list[str]:
    """Recover safe inline model support when structured edits rebuild source.

    A stale authoritative source netlist cannot be reused after a component or
    topology edit.  Primitive ``.model`` records and referenced subcircuits are
    nevertheless part of the component semantics, so retain them from the
    already validated source rather than silently emitting an unbound device.
    """
    source = design.source_netlist
    if source is None:
        return []
    validate_spice_netlist(source)
    model_names = {
        str(component.model).casefold()
        for component in design.components
        if component.model is not None
        and component.kind.upper() in _MODEL_COMPONENT_KINDS
        and _SPICE_TOKEN.fullmatch(str(component.model))
    }
    uses_subcircuit = any(
        component.model is not None
        and (
            component.kind.upper() == "OPAMP5"
            or component.kind.upper().startswith("XSUB")
            or component.kind.upper() in {"XFG3", "OSC6"}
        )
        for component in design.components
    )
    if not model_names and not uses_subcircuit:
        return []

    lines = _logical_lines(source)
    retained: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        lowered = line.lower()
        if lowered.startswith(".model"):
            parts = line.split(maxsplit=2)
            if len(parts) >= 3 and parts[1].casefold() in model_names:
                retained.append(line)
            index += 1
            continue
        if uses_subcircuit and lowered.startswith(".func"):
            retained.append(line)
            index += 1
            continue
        if uses_subcircuit and lowered.startswith(".subckt"):
            depth = 0
            while index < len(lines):
                nested = lines[index]
                nested_lower = nested.lower()
                retained.append(nested)
                if nested_lower.startswith(".subckt"):
                    depth += 1
                elif nested_lower.startswith(".ends"):
                    depth -= 1
                    if depth == 0:
                        index += 1
                        break
                index += 1
            continue
        index += 1
    return retained


def circuit_design_to_spice(
    design: CircuitDesign,
    *,
    prefer_source: bool = True,
) -> str:
    """Return a safe SPICE netlist without silently changing design semantics."""

    if not isinstance(design, CircuitDesign):
        raise ValueError("design must be CircuitDesign")
    if prefer_source and design.source_netlist is not None:
        validate_spice_netlist(design.source_netlist)
        return design.source_netlist
    component_lines = [
        _component_to_spice(component) for component in design.components
    ]
    if not component_lines:
        raise ValueError("structured CircuitDesign contains no compilable components")
    lines = [
        *_parameter_lines(design),
        *_referenced_inline_definitions(design),
        *component_lines,
    ]
    lines.append(".end")
    netlist = "\n".join(lines) + "\n"
    validate_spice_netlist(netlist)
    return netlist


__all__ = ["circuit_design_from_spice", "circuit_design_to_spice"]
