"""Explicit read-only EDA bindings for the bounded model tool loop.

The bindings capture one already validated :class:`CircuitDesign`.  They do
not accept paths, initialize a backend, expose the source netlist, or perform
simulation and mutation.  Circuit values returned by these tools are still
untrusted model input and must never be interpreted as instructions.
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from collections.abc import Mapping
from typing import Any, Final

from .agent_runtime import ToolBinding
from .eda_core import CircuitComponent, CircuitDesign
from .model_provider import ModelCancelled, ToolDefinition


READ_ONLY_EDA_TOOL_SCHEMA_VERSION: Final = 1
MAX_COMPONENT_PAGE: Final = 20
MAX_COMPONENT_NODES_RETURNED: Final = 32
MAX_NET_CONNECTIONS_RETURNED: Final = 100
MAX_DIAGNOSTICS_RETURNED: Final = 100
MAX_KIND_COUNTS_RETURNED: Final = 64

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_REFERENCE_NETS = frozenset({"0", "gnd", "ground"})


def _check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ModelCancelled("read-only EDA inspection was cancelled")


def _require_fields(
    arguments: Mapping[str, Any], allowed: frozenset[str]
) -> dict[str, Any]:
    unknown = set(arguments) - allowed
    if unknown:
        raise ValueError(f"unknown arguments: {sorted(unknown)}")
    return dict(arguments)


def _validate_empty(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    return _require_fields(arguments, frozenset())


def _bounded_integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or _CONTROL_RE.search(normalized):
        raise ValueError(f"{name} is empty, too long, or contains control characters")
    return normalized


def _validate_component_page(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    values = _require_fields(arguments, frozenset({"offset", "limit", "kind"}))
    offset = _bounded_integer(values.get("offset", 0), "offset", 0, 1_000_000)
    limit = _bounded_integer(
        values.get("limit", MAX_COMPONENT_PAGE),
        "limit",
        1,
        MAX_COMPONENT_PAGE,
    )
    result: dict[str, Any] = {"offset": offset, "limit": limit}
    if "kind" in values:
        result["kind"] = _bounded_text(values["kind"], "kind", 128)
    return result


def _validate_net(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    values = _require_fields(arguments, frozenset({"net"}))
    if "net" not in values:
        raise ValueError("net is required")
    return {"net": _bounded_text(values["net"], "net", 255)}


def _component_view(component: CircuitComponent) -> dict[str, Any]:
    nodes = list(component.nodes[:MAX_COMPONENT_NODES_RETURNED])
    return {
        "refdes": component.refdes,
        "kind": component.kind,
        "value": component.value,
        "model": component.model,
        "node_count": len(component.nodes),
        "nodes": nodes,
        "nodes_truncated": len(nodes) < len(component.nodes),
        "parameter_count": len(component.parameters),
        "annotation_count": len(component.annotations),
    }


class ReadOnlyEdaTools:
    """Build a fixed, side-effect-free inspection surface for one design."""

    def __init__(self, design: CircuitDesign) -> None:
        if not isinstance(design, CircuitDesign):
            raise ValueError("design must be CircuitDesign")
        self.design = design
        self._net_names = {net.casefold(): net for net in design.nets}

    def bindings(self) -> tuple[ToolBinding, ...]:
        return (
            ToolBinding(
                ToolDefinition(
                    "eda_get_design_summary",
                    (
                        "Return bounded metadata and topology counts for the fixed "
                        "read-only circuit design. It never returns source netlist text."
                    ),
                    {"type": "object", "properties": {}, "additionalProperties": False},
                ),
                _validate_empty,
                self._design_summary,
            ),
            ToolBinding(
                ToolDefinition(
                    "eda_list_components",
                    (
                        "List a bounded page of components in the fixed read-only "
                        "design, optionally filtered by exact component kind."
                    ),
                    {
                        "type": "object",
                        "properties": {
                            "offset": {"type": "integer", "minimum": 0},
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": MAX_COMPONENT_PAGE,
                            },
                            "kind": {"type": "string", "minLength": 1, "maxLength": 128},
                        },
                        "additionalProperties": False,
                    },
                ),
                _validate_component_page,
                self._list_components,
            ),
            ToolBinding(
                ToolDefinition(
                    "eda_inspect_net",
                    (
                        "Inspect component pin connections for one exact net name in "
                        "the fixed read-only design."
                    ),
                    {
                        "type": "object",
                        "properties": {
                            "net": {"type": "string", "minLength": 1, "maxLength": 255}
                        },
                        "required": ["net"],
                        "additionalProperties": False,
                    },
                ),
                _validate_net,
                self._inspect_net,
            ),
            ToolBinding(
                ToolDefinition(
                    "eda_run_structural_checks",
                    (
                        "Run deterministic structural checks on the fixed circuit. "
                        "These checks are not simulation, ERC, or proof of correctness."
                    ),
                    {"type": "object", "properties": {}, "additionalProperties": False},
                ),
                _validate_empty,
                self._structural_checks,
            ),
        )

    def _base_result(self) -> dict[str, Any]:
        return {
            "schema_version": READ_ONLY_EDA_TOOL_SCHEMA_VERSION,
            "design_id": self.design.design_id,
            "revision": self.design.revision,
            "read_only": True,
        }

    def _connection_counts(
        self, cancel_event: threading.Event | None
    ) -> dict[str, int]:
        counts = {net.casefold(): 0 for net in self.design.nets}
        for index, component in enumerate(self.design.components):
            if index % 128 == 0:
                _check_cancelled(cancel_event)
            for node_index, node in enumerate(component.nodes):
                if node_index % 128 == 0:
                    _check_cancelled(cancel_event)
                counts[node.casefold()] += 1
        return counts

    def _design_summary(
        self,
        arguments: Mapping[str, Any],
        cancel_event: threading.Event | None,
    ) -> Any:
        del arguments
        _check_cancelled(cancel_event)
        kind_counts = Counter(component.kind for component in self.design.components)
        ordered_kinds = sorted(kind_counts.items(), key=lambda item: (-item[1], item[0]))
        shown_kinds = ordered_kinds[:MAX_KIND_COUNTS_RETURNED]
        connections = self._connection_counts(cancel_event)
        result = self._base_result()
        result.update(
            {
                "title": self.design.title,
                "component_count": len(self.design.components),
                "net_count": len(self.design.nets),
                "connection_count": sum(connections.values()),
                "model_reference_count": len(self.design.model_references),
                "parameter_count": len(self.design.parameters),
                "annotation_count": len(self.design.annotations),
                "source_netlist_present": self.design.source_netlist is not None,
                "source_netlist_exposed": False,
                "unused_net_count": sum(value == 0 for value in connections.values()),
                "single_connection_net_count": sum(
                    value == 1 for value in connections.values()
                ),
                "component_kind_counts": [
                    {"kind": kind, "count": count} for kind, count in shown_kinds
                ],
                "component_kind_counts_truncated": len(shown_kinds) < len(ordered_kinds),
            }
        )
        return result

    def _list_components(
        self,
        arguments: Mapping[str, Any],
        cancel_event: threading.Event | None,
    ) -> Any:
        offset = arguments["offset"]
        limit = arguments["limit"]
        kind = arguments.get("kind")
        page: list[CircuitComponent] = []
        matching_count = 0
        for index, component in enumerate(self.design.components):
            if index % 128 == 0:
                _check_cancelled(cancel_event)
            if kind is None or component.kind.casefold() == kind.casefold():
                if offset <= matching_count < offset + limit:
                    page.append(component)
                matching_count += 1
        next_offset = offset + len(page)
        result = self._base_result()
        result.update(
            {
                "filter_kind": kind,
                "offset": offset,
                "limit": limit,
                "matching_count": matching_count,
                "returned_count": len(page),
                "components": [_component_view(component) for component in page],
                "has_more": next_offset < matching_count,
                "next_offset": next_offset if next_offset < matching_count else None,
                "source_netlist_exposed": False,
            }
        )
        return result

    def _inspect_net(
        self,
        arguments: Mapping[str, Any],
        cancel_event: threading.Event | None,
    ) -> Any:
        requested = arguments["net"]
        canonical = self._net_names.get(requested.casefold())
        result = self._base_result()
        if canonical is None:
            result.update(
                {
                    "requested_net": requested,
                    "found": False,
                    "known_net_count": len(self.design.nets),
                    "connections": [],
                }
            )
            return result
        connections: list[dict[str, Any]] = []
        connection_count = 0
        for component_index, component in enumerate(self.design.components):
            if component_index % 128 == 0:
                _check_cancelled(cancel_event)
            for pin_index, node in enumerate(component.nodes, start=1):
                if pin_index % 128 == 0:
                    _check_cancelled(cancel_event)
                if node.casefold() != canonical.casefold():
                    continue
                connection_count += 1
                if len(connections) < MAX_NET_CONNECTIONS_RETURNED:
                    connections.append(
                        {
                            "refdes": component.refdes,
                            "kind": component.kind,
                            "pin_index": pin_index,
                        }
                    )
        result.update(
            {
                "requested_net": requested,
                "found": True,
                "net": canonical,
                "is_reference_net": canonical.casefold() in _REFERENCE_NETS,
                "connection_count": connection_count,
                "connections": connections,
                "connections_truncated": connection_count > len(connections),
            }
        )
        return result

    def _structural_checks(
        self,
        arguments: Mapping[str, Any],
        cancel_event: threading.Event | None,
    ) -> Any:
        del arguments
        diagnostics: list[dict[str, Any]] = []
        code_counts: Counter[str] = Counter()
        severity_counts: Counter[str] = Counter()
        total = 0

        def add(
            severity: str,
            code: str,
            message: str,
            details: Mapping[str, Any] | None = None,
        ) -> None:
            nonlocal total
            total += 1
            code_counts[code] += 1
            severity_counts[severity] += 1
            if len(diagnostics) < MAX_DIAGNOSTICS_RETURNED:
                diagnostics.append(
                    {
                        "severity": severity,
                        "code": code,
                        "message": message,
                        "details": dict(details or {}),
                    }
                )

        _check_cancelled(cancel_event)
        connections = self._connection_counts(cancel_event)
        if not self.design.components:
            add(
                "info",
                "source-only-design",
                "No structured components are available; only source text is retained.",
            )
        if self.design.nets and not any(
            net.casefold() in _REFERENCE_NETS for net in self.design.nets
        ):
            add(
                "warning",
                "reference-net-absent",
                "No conventional 0/GND reference net is declared.",
            )
        for index, net in enumerate(self.design.nets):
            if index % 128 == 0:
                _check_cancelled(cancel_event)
            count = connections[net.casefold()]
            if count == 0:
                add(
                    "warning",
                    "declared-net-unused",
                    "A declared net has no structured component connections.",
                    {"net": net},
                )
            elif count == 1:
                add(
                    "info",
                    "single-connection-net",
                    "A net has only one structured component-pin connection.",
                    {"net": net},
                )
        for index, component in enumerate(self.design.components):
            if index % 128 == 0:
                _check_cancelled(cancel_event)
            all_pins_share_net = len(component.nodes) > 1
            first_net = component.nodes[0].casefold() if component.nodes else ""
            for node_index, node in enumerate(component.nodes[1:], start=1):
                if node_index % 128 == 0:
                    _check_cancelled(cancel_event)
                if node.casefold() != first_net:
                    all_pins_share_net = False
                    break
            if all_pins_share_net:
                add(
                    "info",
                    "component-pins-share-net",
                    "All structured pins of a component share the same net.",
                    {"refdes": component.refdes, "net": component.nodes[0]},
                )
        for index, model in enumerate(self.design.model_references):
            if index % 128 == 0:
                _check_cancelled(cancel_event)
            if model.sha256 is None:
                add(
                    "info",
                    "model-digest-absent",
                    "A model reference has no SHA-256 traceability digest.",
                    {"model": model.name},
                )
            if model.license is None:
                add(
                    "info",
                    "model-license-absent",
                    "A model reference has no recorded license metadata.",
                    {"model": model.name},
                )
        result = self._base_result()
        result.update(
            {
                "scope": "structural-only",
                "simulation_performed": False,
                "electrical_correctness_proven": False,
                "diagnostic_count": total,
                "returned_count": len(diagnostics),
                "diagnostics_truncated": total > len(diagnostics),
                "severity_counts": dict(sorted(severity_counts.items())),
                "code_counts": dict(sorted(code_counts.items())),
                "diagnostics": diagnostics,
            }
        )
        return result


def create_readonly_eda_bindings(design: CircuitDesign) -> tuple[ToolBinding, ...]:
    """Return the fixed four-tool allowlist for one validated design."""
    return ReadOnlyEdaTools(design).bindings()


def run_readonly_structural_checks(
    design: CircuitDesign,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Run the same deterministic structural checks without a model tool loop."""
    result = ReadOnlyEdaTools(design)._structural_checks({}, cancel_event)
    assert isinstance(result, dict)
    return result


__all__ = [
    "READ_ONLY_EDA_TOOL_SCHEMA_VERSION",
    "ReadOnlyEdaTools",
    "create_readonly_eda_bindings",
    "run_readonly_structural_checks",
]
