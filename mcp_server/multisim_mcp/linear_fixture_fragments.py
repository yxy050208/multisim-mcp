"""Bounded explicit circuit fragments for board-boundary experiments.

A fragment can reproduce frequency-dependent source behavior without guessing
a constant voltage. It is a declared test fixture, never a physical board part
or an automatically inferred equivalent circuit.

The default scopes are linear; ``explicit-digital-fixture`` is a separate,
narrow adapter scope for reproducing a reviewed DNOT4 boundary in a native
mixed-board experiment.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .eda_core import CircuitComponent, CircuitDesign
from .linear_reference import solve_linear, validated_components
from .spice_adapter import circuit_design_to_spice


def normalize_linear_fragment(raw: Mapping[str, Any], net: str) -> dict[str, Any]:
    model_scope = raw.get("model_scope", "explicit-ideal-linear-fixture")
    if model_scope not in {
        "explicit-ideal-linear-fixture",
        "explicit-native-vendor-fixture",
        "explicit-passive-load-fixture",
        "explicit-native-vendor-load-fixture",
        "explicit-digital-fixture",
    }:
        raise ValueError("circuit_fragment model_scope is unsupported")
    components = raw.get("components")
    ports = raw.get("ports")
    digest = raw.get("source_compiled_digest")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("circuit_fragment requires source_compiled_digest")
    if not isinstance(ports, list) or not 1 <= len(ports) <= 8 or any(
        not isinstance(n, str) or not re.fullmatch(r"[A-Za-z0-9_]+", n) for n in ports
    ) or len({n.casefold() for n in ports}) != len(ports) or net not in ports:
        raise ValueError("circuit_fragment requires distinct declared ports including net")
    if not isinstance(components, list) or not 1 <= len(components) <= 16:
        raise ValueError("circuit_fragment requires 1..16 explicit linear components")
    parts = tuple(CircuitComponent.from_dict(p) for p in components)
    design = CircuitDesign("linear-fixture-fragment", "Explicit linear fixture", components=parts)
    preview = circuit_design_to_spice(design, prefer_source=False)
    if model_scope == "explicit-digital-fixture":
        # A digital receiver-side fixture must use the same bounded adapter
        # contract as the source board (for example V + DNOT4).  It is not a
        # linear-oracle candidate, so validate the portable parser and the
        # deliberately small component family instead of pretending that the
        # XSPICE gate is an analog nodal model.
        from .schematic_builder import parse_netlist

        parsed = parse_netlist(preview)
        allowed_digital = {"V", "R", "C", "L", "BV", "DNOT4"}
        if any(item.kind.upper() not in allowed_digital for item in parsed.components):
            raise ValueError("digital fixture contains an unsupported component")
    else:
        parsed = validated_components(
            preview,
            allow_vendor=model_scope in {
                "explicit-native-vendor-fixture",
                "explicit-native-vendor-load-fixture",
            },
        )
    parsed_components = (
        list(parsed.components) if hasattr(parsed, "components") else list(parsed)
    )
    nodes = {n for p in parsed_components for n in p.nodes}
    if not set(ports) <= nodes:
        raise ValueError("circuit_fragment ports must exist in its components")
    source = next((p for p in parsed_components if p.kind == "V"), None)
    if model_scope == "explicit-passive-load-fixture":
        # A receiver-side loading network is a legitimate boundary condition
        # even though it has no source of its own.  Keep this scope strictly
        # passive so it cannot smuggle an unreviewed driver or active model
        # into an isolated board experiment.
        if source is not None or any(part.kind not in {"R", "C", "L"} for part in parsed_components):
            raise ValueError("passive load fixture may contain only R/C/L components")
    elif model_scope == "explicit-native-vendor-load-fixture":
        # A receiver-side vendor load may contain the reviewed LM324AJ input
        # stage, but must not introduce an independent source.  This is used
        # to reproduce input bias/startup loading at an isolated sender edge.
        if source is not None:
            raise ValueError("native vendor load fixture must not contain a source")
        if any(part.kind not in {"R", "C", "LM324AJ"} for part in parsed_components):
            raise ValueError("native vendor load fixture contains an unsupported component")
        if not any(part.kind == "LM324AJ" for part in parsed_components):
            raise ValueError("native vendor load fixture requires the reviewed LM324AJ model")
        if any(part.kind == "LM324AJ" and (part.model or "").upper() != "LM324AJ" for part in parsed_components):
            raise ValueError("native vendor load fixture requires the reviewed LM324AJ model")
    elif source is None:
        raise ValueError("circuit_fragment must contain an explicit DC/AC source")
    if model_scope == "explicit-digital-fixture":
        if not any(part.kind.upper() in {"DNOT4", "BV"} for part in parsed_components):
            raise ValueError("digital fixture requires a DNOT4 or BV digital adapter")
    elif model_scope == "explicit-ideal-linear-fixture":
        # This contract accepts only an independently solvable DC/AC source
        # circuit.  Resistive loads alone use the termination contract.
        solve_linear(parsed)
        solve_linear(parsed, 1000)
    elif model_scope == "explicit-native-vendor-fixture":
        # Native vendor fixtures are deliberately narrower than the general
        # fragment API: they may contain passives, voltage sources and the
        # reviewed LM324AJ only.  Their output is validated by Multisim, never
        # by the ideal linear oracle.
        if any(part.kind not in {"R", "C", "V", "LM324AJ"} for part in parsed):
            raise ValueError("native vendor fixture contains an unsupported component")
        if not any(part.kind == "LM324AJ" for part in parsed):
            raise ValueError("native vendor fixture requires an LM324AJ component")
        if any(part.kind == "LM324AJ" and (part.model or "").upper() != "LM324AJ" for part in parsed):
            raise ValueError("native vendor fixture requires the reviewed LM324AJ model")
    return {"components": [p.to_dict() for p in parts], "ports": list(ports),
            "source_compiled_digest": digest,
            "model_scope": model_scope}


def driven_nets(components: list[Mapping[str, Any]]) -> set[str]:
    """Direct ideal source outputs; no claim about arbitrary active devices."""
    result = set()
    for p in components:
        kind, nodes = p.get("kind", "").upper(), p.get("nodes", [])
        if kind == "V" and len(nodes) == 2:
            result.add(str(nodes[0]).casefold())
        elif kind in {"X", "OPAMP5"} and p.get("model") == "OPAMP5" and len(nodes) == 5:
            result.add(str(nodes[4]).casefold())
        elif kind == "LM324AJ" and len(nodes) == 5:
            result.add(str(nodes[4]).casefold())
    return result
