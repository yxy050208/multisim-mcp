"""Deterministic preflight checks for generated schematic geometry.

The checks intentionally do not replace Multisim's importer.  They catch the
most common failure mode of generated schematics early: symbols or wires that
are geometrically inconsistent even though the logical netlist is valid.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any
from .orthogonal_routing import segment_relation


def _overlap(a: Mapping[str, float], b: Mapping[str, float], margin: float) -> bool:
    return not (
        a["x"] + a["width"] + margin <= b["x"]
        or b["x"] + b["width"] + margin <= a["x"]
        or a["y"] + a["height"] + margin <= b["y"]
        or b["y"] + b["height"] + margin <= a["y"]
    )


def validate_schematic_geometry(
    components: Sequence[Mapping[str, Any]],
    wires: Mapping[str, Sequence[Sequence[tuple[float, float]]]],
    *,
    pin_points: Mapping[str, Sequence[tuple[float, float]]] | None = None,
    pin_exits: Mapping[str, Sequence[tuple[tuple[float,float],tuple[float,float]]]] | None = None,
    component_width: float = 126.0,
    component_height: float = 108.0,
    clearance: float = 12.0,
    max_crossings_per_wire: float = 2.0,
) -> dict[str, Any]:
    """Return bounded, JSON-safe geometry findings.

    ``components`` contains ``refdes``, ``x`` and ``y``.  Wire paths are
    grouped by logical net.  This is deliberately a report-only preflight for
    the first iteration; callers can promote errors to a hard gate once all
    vendor symbol templates expose reliable footprints.
    """
    if not isinstance(max_crossings_per_wire, (int, float)) or isinstance(max_crossings_per_wire, bool):
        raise ValueError("max_crossings_per_wire must be a finite non-negative number")
    if max_crossings_per_wire < 0 or not math.isfinite(float(max_crossings_per_wire)):
        raise ValueError("max_crossings_per_wire must be a finite non-negative number")

    placements = []
    for item in components:
        refdes = str(item.get("refdes", ""))
        try:
            x = float(item["x"])
            y = float(item["y"])
        except (KeyError, TypeError, ValueError):
            placements.append({"refdes": refdes, "code": "invalid-placement"})
            continue
        placements.append({"refdes": refdes, "x": x, "y": y, "width": float(item.get("width", component_width)), "height": float(item.get("height", component_height))})

    findings: list[dict[str, Any]] = []
    for index, current in enumerate(placements):
        if "width" not in current:
            findings.append({"severity": "error", "code": "invalid-placement", "refdes": current["refdes"]})
            continue
        for other in placements[index + 1 :]:
            if "width" in other and _overlap(current, other, clearance):
                findings.append({
                    "severity": "error",
                    "code": "component-overlap",
                    "components": [current["refdes"], other["refdes"]],
                })

    pin_points = pin_points or {}
    for net, paths in wires.items():
        for path in paths:
            if len(path) < 2:
                findings.append({"severity": "error", "code": "degenerate-wire", "net": str(net)})
                continue
            if any(len(point) != 2 for point in path):
                findings.append({"severity": "error", "code": "invalid-wire-point", "net": str(net)})
                continue
            expected = pin_points.get(str(net), ())
            if expected:
                # A multi-drop net may terminate at a junction, so only flag
                # endpoints that are not near any known pin or existing path
                # junction.  The tolerance covers decimal rounding in XML.
                endpoint_counts: dict[tuple[float, float], int] = {}
                for item in paths:
                    for x, y in (item[0], item[-1]):
                        key = (round(float(x), 3), round(float(y), 3))
                        endpoint_counts[key] = endpoint_counts.get(key, 0) + 1
                junctions = {
                    (round(float(x), 3), round(float(y), 3))
                    for item in paths
                    for x, y in item[1:-1]
                }
                junctions.update(key for key, count in endpoint_counts.items() if count > 1)
                for endpoint in (path[0], path[-1]):
                    ex, ey = float(endpoint[0]), float(endpoint[1])
                    near_pin = any(abs(ex - px) <= 0.01 and abs(ey - py) <= 0.01 for px, py in expected)
                    near_junction = (round(ex, 3), round(ey, 3)) in junctions
                    if not near_pin and not near_junction:
                        findings.append({"severity": "error", "code": "wire-endpoint-off-pin", "net": str(net), "point": [ex, ey]})

            # A wire may touch a component only at a pin.  This catches the
            # common visual error where a route crosses a symbol body.
            for point_a, point_b in zip(path, path[1:]):
                if point_a[0] != point_b[0] and point_a[1] != point_b[1]:
                    findings.append({"severity": "error", "code": "non-orthogonal-wire", "net": str(net)})
                    continue
                for component in placements:
                    if "width" not in component:
                        continue
                    x0, y0 = component["x"], component["y"]
                    x1, y1 = x0 + component["width"], y0 + component["height"]
                    horizontal = abs(point_a[1] - point_b[1]) <= 0.01
                    vertical = abs(point_a[0] - point_b[0]) <= 0.01
                    endpoint_on_boundary = any(
                        abs(px - x0) <= 0.01 or abs(px - x1) <= 0.01
                        or abs(py - y0) <= 0.01 or abs(py - y1) <= 0.01
                        for px, py in (point_a, point_b)
                        if x0 - 0.01 <= px <= x1 + 0.01 and y0 - 0.01 <= py <= y1 + 0.01
                    )
                    endpoint_inside = any(
                        x0 < px < x1 and y0 < py < y1 for px, py in (point_a, point_b)
                    )
                    crosses = (
                        horizontal and y0 < point_a[1] < y1 and max(min(point_a[0], point_b[0]), x0) < min(max(point_a[0], point_b[0]), x1)
                    ) or (
                        vertical and x0 < point_a[0] < x1 and max(min(point_a[1], point_b[1]), y0) < min(max(point_a[1], point_b[1]), y1)
                    )
                    # A vendor template may place the logical pin endpoint a
                    # few units inside the symbol body. Treat that endpoint
                    # as an allowed pin anchor until per-template footprints
                    # are available.
                    allowed_pin_exit = False
                    for anchor, other in ((point_a, point_b), (point_b, point_a)):
                        px, py = anchor
                        if not any(abs(px-qx) <= .01 and abs(py-qy) <= .01 for qx,qy in expected):
                            continue
                        if not (x0 <= px <= x1 and y0 <= py <= y1):
                            continue
                        exits = [exit for point,exit in (pin_exits or {}).get(str(net),())
                                 if abs(point[0]-px)<=.01 and abs(point[1]-py)<=.01]
                        if exits:
                            allowed_pin_exit |= any(
                                (horizontal and abs(ey-py)<=.01 and (ex-px)*(other[0]-px)>0 and (other[0]<=x0 or other[0]>=x1)) or
                                (vertical and abs(ex-px)<=.01 and (ey-py)*(other[1]-py)>0 and (other[1]<=y0 or other[1]>=y1))
                                for ex,ey in exits)
                            continue
                        nearest = min(abs(px-x0), abs(px-x1), abs(py-y0), abs(py-y1))
                        allowed_pin_exit |= (
                            (horizontal and other[0] <= x0 and abs(px-x0) <= nearest+.01) or
                            (horizontal and other[0] >= x1 and abs(px-x1) <= nearest+.01) or
                            (vertical and other[1] <= y0 and abs(py-y0) <= nearest+.01) or
                            (vertical and other[1] >= y1 and abs(py-y1) <= nearest+.01)
                        )
                    # Legacy callers without pin geometry can only validate
                    # complete through-routes. Builders provide actual pins.
                    legacy_anchor = not pin_points and (endpoint_on_boundary or endpoint_inside)
                    if crosses and not allowed_pin_exit and not legacy_anchor:
                        findings.append({"severity": "error", "code": "wire-crosses-component", "net": str(net), "refdes": component["refdes"]})

    segments = [(str(net), a, b) for net, paths in wires.items() for path in paths
                for a,b in zip(path,path[1:]) if a[0] == b[0] or a[1] == b[1]]
    crossing_count = 0
    for index, (net, a, b) in enumerate(segments):
        for other, c, d in segments[index+1:]:
            if net == other:
                continue
            relation = segment_relation(tuple(a),tuple(b),tuple(c),tuple(d))
            if relation == "overlap":
                findings.append({"severity": "error", "code": "different-nets-overlap", "nets": [net, other]})
            elif relation:
                crossing_count += 1

    wire_count = sum(len(paths) for paths in wires.values())
    crossings_per_wire = crossing_count / wire_count if wire_count else 0.0
    if wire_count and crossings_per_wire > float(max_crossings_per_wire):
        findings.append({
            "severity": "error",
            "code": "excessive-wire-crossings",
            "crossings": crossing_count,
            "wires": wire_count,
            "crossings_per_wire": round(crossings_per_wire, 3),
            "limit": float(max_crossings_per_wire),
        })

    return {
        "schema_version": 1,
        "status": "fail" if any(item["severity"] == "error" for item in findings) else "pass",
        "component_count": len(placements),
        "wire_count": wire_count,
        "different_net_crossings": crossing_count,
        "crossings_per_wire": round(crossings_per_wire, 3),
        "crossings_limit_per_wire": float(max_crossings_per_wire),
        "findings": findings,
    }


__all__ = ["validate_schematic_geometry"]
