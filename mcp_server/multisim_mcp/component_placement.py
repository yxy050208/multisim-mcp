"""Read and modify component placement inside a Multisim .ms14 design.

Placement model (as written by schematic_builder and by Multisim itself):

* Every component carries a symbol item (``<Item Class="CIITSymbolComp">``
  wrapper) whose ``CIITSymbolComp`` element stores the sheet position as the
  translation pair ``Transformer-M20`` (x) / ``Transformer-M21`` (y).  All
  drawing geometry below the symbol (labels, pin glyphs) is relative to that
  origin, so moving a component means moving this pair only.
* Wire endpoints are external pin items (``Item Class="CODPinComp"`` wrapping
  a ``CODPinComp`` with absolute ``CenterX``/``CenterY``).  A component's pin
  connector references its endpoint through ``CIITPinConnectorComp/
  ConnectList/Item/@ID``.
* Wires are ``CIITLinkComp`` polylines (``Points/Item @X @Y``) between two
  endpoint IDs (``Connect1``/``Connect2``).  Electrical connectivity is
  defined by those references alone, but the drawn geometry must follow a
  moved component, so every wire attached to a moved endpoint is re-routed
  with the same obstacle-aware router the builder uses
  (orthogonal_routing.route_pins), falling back to shifting the wire's end
  points if the router cannot find a clean corridor.

The module is file-level: it decodes the .ms14 with the packaged ewd codec,
edits the XML and encodes it back with ewe.  Multisim itself does not need to
be running, and the source file is only replaced when the caller asks for it.
"""
from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from .native_xml import parse_native_xml, write_native_xml
from .orthogonal_routing import route_pins
from .schematic_builder import _asc

__all__ = [
    "list_component_positions",
    "set_component_positions",
    "collect_placement",
    "set_sheet_size",
]

# Extra clearance around a component's pin hull so pin-escape segments and
# other symbols' bodies never touch the obstacle box edge.
_BOX_PADDING = 12.0


# ---------------------------------------------------------------------------
# decode / encode helpers
# ---------------------------------------------------------------------------


def _codec():
    """The ewd/ewe codec singleton; imported lazily to avoid a cycle."""
    from . import server

    return server.codec


def _decode(ms14_path: Path, work_dir: Path, codec: Any) -> tuple[ET.ElementTree, Path]:
    xml_path = work_dir / (ms14_path.stem + ".placement.xml")
    result = codec.decode(str(ms14_path), str(xml_path))
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(f"decode failed: {result['error']}")
    if not Path(xml_path).is_file():
        raise RuntimeError(f"decoder produced no XML at {xml_path}")
    return parse_native_xml(xml_path), Path(xml_path)


# ---------------------------------------------------------------------------
# design model
# ---------------------------------------------------------------------------


def _parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {child: node for node in root.iter() for child in node}


def collect_placement(root: ET.Element) -> dict[str, dict[str, Any]]:
    """Map refdes -> {item, symbol, x, y, endpoint_ids, box} for every component.

    ``box`` approximates the symbol body from the hull of its pin positions
    (transformed by the symbol's rotation, when it has one) inflated by
    ``_BOX_PADDING``.  It is what the wire router uses as an obstacle; the
    builder's default 126 x 108 body box is only a coarse stand-in that
    swallows neighbouring pins once components move to custom spots.
    """
    parent = _parent_map(root)

    refdes_by_symbol_id: dict[str, str] = {}
    for component in root.iter("CiComponent"):
        name = component.get("LocalName") or ""
        if name.startswith("&ASC"):
            refdes_by_symbol_id[component.get("SymCompID", "")] = name[4:]

    placement: dict[str, dict[str, Any]] = {}
    for item in root.iter("Item"):
        symbol = item.find("./CIITSymbolComp")
        if symbol is None:
            continue
        refdes = refdes_by_symbol_id.get(item.get("ID", ""))
        if not refdes:
            continue
        x = float(symbol.get("Transformer-M20", "0"))
        y = float(symbol.get("Transformer-M21", "0"))
        endpoint_ids: dict[str, str] = {}
        pin_points: list[tuple[float, float]] = []
        m00 = float(symbol.get("Transformer-M00", "1"))
        m01 = float(symbol.get("Transformer-M01", "0"))
        m10 = float(symbol.get("Transformer-M10", "0"))
        m11 = float(symbol.get("Transformer-M11", "1"))
        for connector in symbol.iter("CIITPinConnectorComp"):
            local_x = float(connector.get("ptCenterX", "0"))
            local_y = float(connector.get("ptCenterY", "0"))
            pin_points.append((x + m00 * local_x + m01 * local_y,
                               y + m10 * local_x + m11 * local_y))
            wrapper = parent.get(connector)
            if wrapper is None or not wrapper.get("ID"):
                continue
            link = connector.find("./ConnectList/Item")
            if link is not None and link.get("ID"):
                endpoint_ids[wrapper.get("ID")] = link.get("ID")
        pin_center = None
        if pin_points:
            xs = [p[0] for p in pin_points]
            ys = [p[1] for p in pin_points]
            pin_center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
            box = {
                "refdes": refdes,
                "x": min(xs) - _BOX_PADDING,
                "y": min(ys) - _BOX_PADDING,
                "width": (max(xs) - min(xs)) + 2 * _BOX_PADDING,
                "height": (max(ys) - min(ys)) + 2 * _BOX_PADDING,
            }
        else:
            box = {"refdes": refdes, "x": x, "y": y}
        placement[refdes] = {
            "item": item,
            "symbol": symbol,
            "x": x,
            "y": y,
            "endpoint_ids": endpoint_ids,
            "pin_points": pin_points,
            "pin_center": pin_center,
            "box": box,
        }
    return placement


def _endpoint_table(root: ET.Element) -> dict[str, ET.Element]:
    """endpoint ID -> its CODPinComp element (absolute CenterX/CenterY)."""
    table: dict[str, ET.Element] = {}
    for item in root.iter("Item"):
        pin = item.find("./CODPinComp")
        if pin is not None and pin.find("./ConnectList") is not None:
            table[item.get("ID", "")] = pin
    return table


def _wire_table(root: ET.Element) -> list[tuple[ET.Element, ET.Element, str, str, str]]:
    """All wires as (wrapper item, CIITLinkComp, connect1, connect2, node)."""
    wires = []
    for item in root.iter("Item"):
        link = item.find("./CIITLinkComp")
        if link is not None:
            wires.append((
                item, link, link.get("Connect1", ""), link.get("Connect2", ""),
                link.get("Node", ""),
            ))
    return wires


def _wire_segments(link: ET.Element) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    points = list(link.find("./Points"))
    return [
        ((float(a.get("X")), float(a.get("Y"))), (float(b.get("X")), float(b.get("Y"))))
        for a, b in zip(points[:-1], points[1:])
    ]


def _endpoint_position(endpoints: dict[str, ET.Element], endpoint_id: str) -> dict[str, Any]:
    pin = endpoints.get(endpoint_id)
    if pin is None:
        raise ValueError(f"wire endpoint {endpoint_id} has no CODPinComp position")
    return {"x": float(pin.get("CenterX", "0")), "y": float(pin.get("CenterY", "0"))}


def _endpoint_xy(endpoints: dict[str, ET.Element], endpoint_id: str) -> tuple[float, float]:
    pin = endpoints.get(endpoint_id)
    if pin is None:
        raise ValueError(f"wire endpoint {endpoint_id} has no CODPinComp position")
    return (float(pin.get("CenterX", "0")), float(pin.get("CenterY", "0")))


def _elbow_path(
    start: tuple[float, float],
    end: tuple[float, float],
) -> list[tuple[float, float]]:
    """Axis-aligned 3-segment path used when the strict router has no corridor."""
    (x1, y1), (x2, y2) = start, end
    if x1 == x2 or y1 == y2:
        return [start, end]
    mid_y = (y1 + y2) / 2
    path = [(x1, y1), (x1, mid_y), (x2, mid_y), (x2, y2)]
    return [point for index, point in enumerate(path)
            if index == 0 or point != path[index - 1]]


def _recenter_node_labels(
    root: ET.Element,
    wires: list[tuple[ET.Element, ET.Element, str, str, str]],
    affected_nodes: set[str],
) -> None:
    """Re-place a net's name label at the mean of its wire endpoints."""
    if not affected_nodes:
        return
    endpoints = _endpoint_table(root)
    points_by_node: dict[str, list[tuple[float, float]]] = {}
    for _item, link, c1, c2, node_id in wires:
        if node_id not in affected_nodes:
            continue
        for endpoint_id in (c1, c2):
            pin = endpoints.get(endpoint_id)
            if pin is not None:
                points_by_node.setdefault(node_id, []).append((
                    float(pin.get("CenterX", "0")), float(pin.get("CenterY", "0")),
                ))
    label_by_id: dict[str, ET.Element] = {}
    for item in root.iter("Item"):
        text = item.find("./CODNodeTextComp")
        if text is not None:
            label_by_id[item.get("ID", "")] = text
    for _item, link, _c1, _c2, node_id in wires:
        label_id = link.get("NodeText")
        points = points_by_node.get(node_id)
        if not label_id or not points:
            continue
        label = label_by_id.get(label_id)
        if label is None:
            continue
        mid_x = sum(p[0] for p in points) / len(points) + 3
        mid_y = sum(p[1] for p in points) / len(points) - 6
        label.set("Transformer-M20", f"{mid_x:g}")
        label.set("Transformer-M21", f"{mid_y:g}")


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def list_component_positions(
    ms14_path: str | Path,
    output_xml: str | Path | None = None,
    codec: Any = None,
) -> dict:
    """Return the sheet position of every component in an .ms14 file."""
    ms14_path = Path(ms14_path).expanduser().resolve()
    if not ms14_path.is_file():
        raise FileNotFoundError(f"no such .ms14 file: {ms14_path}")
    codec = codec or _codec()
    if output_xml:
        work_dir = Path(output_xml).expanduser().resolve().parent
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="mcp_placement_"))
    work_dir.mkdir(parents=True, exist_ok=True)
    tree, xml_path = _decode(ms14_path, work_dir, codec)
    placement = collect_placement(tree.getroot())
    components = {}
    for refdes, record in sorted(placement.items()):
        entry = {"x": record["x"], "y": record["y"]}
        if record.get("pin_center"):
            entry["pin_center"] = [
                round(record["pin_center"][0], 1), round(record["pin_center"][1], 1)
            ]
        components[refdes] = entry
    if output_xml:
        write_native_xml(tree, output_xml)
    return {
        "ms14": str(ms14_path),
        "component_count": len(components),
        "components": components,
        "decoded_xml": str(xml_path),
    }


def set_component_positions(
    ms14_path: str | Path,
    positions: dict[str, list[float]],
    output_ms14: str | Path | None = None,
    overwrite: bool = False,
    codec: Any = None,
    mode: str = "absolute",
    anchor: str = "origin",
    snap: float = 0,
) -> dict:
    """Move components to new sheet coordinates and re-route attached wires.

    ``positions`` maps refdes -> ``[x, y]`` in sheet units (the same scale the
    builder's layout uses; one auto-layout grid step is 270 x 216).  Only the
    listed components move.  ``mode="relative"`` treats the values as deltas
    from the current position (batch-friendly nudging).  ``anchor`` selects
    what the coordinates mean: ``"origin"`` (the symbol's transformer origin,
    default) or ``"pin_center"`` (the centre of the component's pin hull --
    the anchor the builder's ``component_positions`` uses, so both tools
    agree).  ``snap`` optionally rounds the final origin onto a grid (the
    builder normalises onto a 9 pt grid).

    Per Multisim's wiring model a wire's ENDPOINTS are fixed references --
    component ports, or points on already-connected wire segments (junctions).
    Rerouting only ever rewrites the drawn path (``Points``); the connection
    references (``Connect1``/``Connect2``) and every endpoint a moved pin
    owns follow the component and never change identity.

    When ``output_ms14`` is omitted the source file is rewritten in place and
    a one-time ``*.placement-backup.ms14`` copy of the original is kept next
    to it.
    """
    if mode not in {"absolute", "relative"}:
        raise ValueError("mode must be 'absolute' or 'relative'")
    if anchor not in {"origin", "pin_center"}:
        raise ValueError("anchor must be 'origin' or 'pin_center'")
    if not isinstance(positions, dict) or not positions:
        raise ValueError("positions must be a non-empty {refdes: [x, y]} mapping")
    normalized: dict[str, tuple[float, float]] = {}
    for refdes, xy in positions.items():
        if not isinstance(xy, (list, tuple)) or len(xy) != 2:
            raise ValueError(f"position for {refdes!r} must be [x, y]")
        normalized[refdes] = (float(xy[0]), float(xy[1]))

    ms14_path = Path(ms14_path).expanduser().resolve()
    if not ms14_path.is_file():
        raise FileNotFoundError(f"no such .ms14 file: {ms14_path}")
    out_path = Path(output_ms14).expanduser().resolve() if output_ms14 else ms14_path
    if out_path.suffix.lower() != ".ms14":
        raise ValueError("output path must end with .ms14")
    if out_path.exists() and not overwrite and out_path != ms14_path:
        raise FileExistsError(f"Refusing to overwrite existing file: {out_path}")
    codec = codec or _codec()

    with tempfile.TemporaryDirectory(prefix="mcp_placement_") as tmp:
        tree, xml_path = _decode(ms14_path, Path(tmp), codec)
        root = tree.getroot()
        placement = collect_placement(root)
        unknown = sorted(set(normalized) - set(placement))
        if unknown:
            raise ValueError(
                f"unknown component refdes {unknown}; known: {sorted(placement)}"
            )

        endpoints = _endpoint_table(root)
        wires = _wire_table(root)

        # --- move symbols and their wire endpoints -------------------------
        moved_endpoints: dict[str, tuple[float, float]] = {}
        moved_components: list[str] = []
        for refdes, (nx, ny) in normalized.items():
            record = placement[refdes]
            if mode == "relative":
                nx, ny = record["x"] + nx, record["y"] + ny
            elif anchor == "pin_center" and record.get("pin_center"):
                pcx, pcy = record["pin_center"]
                nx, ny = nx + (record["x"] - pcx), ny + (record["y"] - pcy)
            if snap:
                nx, ny = round(nx / snap) * snap, round(ny / snap) * snap
            dx, dy = nx - record["x"], ny - record["y"]
            if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                continue
            moved_components.append(refdes)
            record["symbol"].set("Transformer-M20", f"{nx:g}")
            record["symbol"].set("Transformer-M21", f"{ny:g}")
            if record.get("pin_center"):
                record["pin_center"] = (
                    record["pin_center"][0] + dx, record["pin_center"][1] + dy
                )
            record["x"], record["y"] = nx, ny
            record["box"]["x"] += dx
            record["box"]["y"] += dy
            for endpoint_id in record["endpoint_ids"].values():
                pin = endpoints.get(endpoint_id)
                if pin is None:
                    continue
                px = float(pin.get("CenterX", "0")) + dx
                py = float(pin.get("CenterY", "0")) + dy
                pin.set("CenterX", f"{px:g}")
                pin.set("CenterY", f"{py:g}")
                moved_endpoints[endpoint_id] = (px, py)

        # --- obstacles from the NEW symbol positions -----------------------
        obstacles = [record["box"] for record in placement.values()]
        endpoint_owner = {
            endpoint_id: refdes
            for refdes, record in placement.items()
            for endpoint_id in record["endpoint_ids"]
        }

        # --- re-route every wire that touches a moved endpoint -------------
        # `occupied` only covers wires that will NOT move, so the reroutes are
        # judged against the geometry that actually stays where it is.  When
        # the strict router cannot find a clean corridor (dense custom
        # layouts), the wire gets a plain orthogonal elbow instead -- always
        # connected, always axis-aligned, just possibly less elegant.
        affected_nodes: set[str] = set()
        rerouted = 0
        fallback = 0
        affected_wires = [
            (item, link, c1, c2, node_id)
            for item, link, c1, c2, node_id in wires
            if c1 in moved_endpoints or c2 in moved_endpoints
        ]
        static_occupied = [
            segment
            for item, link, c1, c2, _node in wires
            if c1 not in moved_endpoints and c2 not in moved_endpoints
            for segment in _wire_segments(link)
        ]
        for item, link, c1, c2, node_id in affected_wires:
            affected_nodes.add(node_id)
            start_xy = _endpoint_xy(endpoints, c1)
            end_xy = _endpoint_xy(endpoints, c2)
            path = None
            try:
                start = dict(_endpoint_position(endpoints, c1), refdes=endpoint_owner.get(c1))
                end = dict(_endpoint_position(endpoints, c2), refdes=endpoint_owner.get(c2))
                path = route_pins(start, end, obstacles, static_occupied)
            except Exception:  # noqa: BLE001 - cosmetic router, never fatal
                path = None
            if path:
                rerouted += 1
            else:
                fallback += 1
                path = _elbow_path(start_xy, end_xy)
            points = link.find("./Points")
            for child in list(points):
                points.remove(child)
            for px, py in path:
                points.append(ET.Element("Item", {"X": f"{px:g}", "Y": f"{py:g}"}))
        _recenter_node_labels(root, wires, affected_nodes)

        # overlap check with the NEW geometry (same hull model as the router)
        overlaps_after: list[list[str]] = []
        refs_after = sorted(placement)
        for i, a in enumerate(refs_after):
            box_a = placement[a]["box"]
            ax0, ay0 = box_a["x"], box_a["y"]
            ax1, ay1 = ax0 + box_a["width"], ay0 + box_a["height"]
            for b in refs_after[i + 1:]:
                box_b = placement[b]["box"]
                bx0, by0 = box_b["x"], box_b["y"]
                bx1, by1 = bx0 + box_b["width"], by0 + box_b["height"]
                if min(ax1, bx1) - max(ax0, bx0) > 0 and min(ay1, by1) - max(ay0, by0) > 0:
                    overlaps_after.append([a, b])

        write_native_xml(tree, xml_path)
        backup_note = None
        if out_path == ms14_path:
            backup = out_path.with_name(out_path.stem + ".placement-backup.ms14")
            if not backup.exists():
                shutil.copy2(ms14_path, backup)
                backup_note = str(backup)
        encode_result = codec.encode(str(xml_path), str(out_path))

    return {
        "success": True,
        "ms14": str(out_path),
        "moved_components": moved_components,
        "moved_wire_endpoints": len(moved_endpoints),
        "rerouted_wires": rerouted,
        "fallback_shifted_wires": fallback,
        "positions": {key: list(value) for key, value in normalized.items()},
        "overlaps_after": overlaps_after,
        "encode": encode_result,
        "backup": backup_note,
    }


def set_sheet_size(
    ms14_path: str | Path,
    width_in: float,
    height_in: float,
    output_ms14: str | Path | None = None,
    overwrite: bool = False,
    codec: Any = None,
) -> dict:
    """Enlarge (or shrink) an .ms14 design's page and GUI working area.

    Multisim stores the page in two places that must agree:

    * ``CircPrefs`` settings "Sheet Width"/"Sheet Height" (pixels at 96 dpi)
      plus their inch twins, and the diagram ``PageWidth``/``PageHeight``
      (inches) -- these drive the native image/export API;
    * the ``DesignSheet`` WorkArea rectangle (nanometres, centred on the
      origin) -- this is the white sheet area the GUI actually shows.  A
      schematic wider than the WorkArea is clipped on screen even when
      CircPrefs already describes a bigger page.

    ``width_in``/``height_in`` are inches at 96 dpi.  Existing components are
    never moved; the page just grows around them.  When ``output_ms14`` is
    omitted the source file is rewritten in place after a one-time
    ``*.sheet-backup.ms14`` copy is saved next to it.
    """
    width_in = float(width_in)
    height_in = float(height_in)
    if not (1.0 <= width_in <= 200.0 and 1.0 <= height_in <= 200.0):
        raise ValueError("sheet size must be within 1..200 inches")
    width_px = math.ceil(width_in * 96)
    height_px = math.ceil(height_in * 96)

    ms14_path = Path(ms14_path).expanduser().resolve()
    if not ms14_path.is_file():
        raise FileNotFoundError(f"no such .ms14 file: {ms14_path}")
    out_path = Path(output_ms14).expanduser().resolve() if output_ms14 else ms14_path
    if out_path.suffix.lower() != ".ms14":
        raise ValueError("output path must end with .ms14")
    if out_path.exists() and not overwrite and out_path != ms14_path:
        raise FileExistsError(f"Refusing to overwrite existing file: {out_path}")
    codec = codec or _codec()

    with tempfile.TemporaryDirectory(prefix="mcp_sheet_") as tmp:
        tree, xml_path = _decode(ms14_path, Path(tmp), codec)
        root = tree.getroot()

        diagram = root.find(".//CIITDiagramComp")
        sheet_settings = {
            "Sheet Width": width_px,
            "Sheet Height": height_px,
            "Sheet Width In Inch": width_px / 96,
            "Sheet Height In Inch": height_px / 96,
        }
        updated_keys: list[str] = []
        if diagram is not None:
            for setting in diagram.findall(
                "./CircPrefs/CIITCircuitPrefs/Settings/Element"
            ):
                key = setting.get("Key", "").removeprefix("&ASC")
                if key in sheet_settings and setting.find("Item") is not None:
                    setting.find("Item").set("Value", _asc(f"{sheet_settings[key]:g}"))
                    updated_keys.append(key)
            diagram.set("PageWidth", f"{width_px / 96:g}")
            diagram.set("PageHeight", f"{height_px / 96:g}")

        half_w_nm = round(width_px / 96 * 25.4 / 2 * 1_000_000)
        half_h_nm = round(height_px / 96 * 25.4 / 2 * 1_000_000)
        workarea_count = 0
        for sheet in root.iter("DesignSheet"):
            sheet.set("WorkAreaLeft", f"{-half_w_nm}")
            sheet.set("WorkAreaRight", f"{half_w_nm}")
            sheet.set("WorkAreaBottom", f"{-half_h_nm}")
            sheet.set("WorkAreaTop", f"{half_h_nm}")
            workarea_count += 1

        backup_note = None
        if out_path == ms14_path:
            backup = out_path.with_name(out_path.stem + ".sheet-backup.ms14")
            if not backup.exists():
                shutil.copy2(ms14_path, backup)
                backup_note = str(backup)
        write_native_xml(tree, xml_path)
        encode_result = codec.encode(str(xml_path), str(out_path))

    return {
        "success": True,
        "ms14": str(out_path),
        "width_inch": width_px / 96,
        "height_inch": height_px / 96,
        "width_px_96dpi": width_px,
        "height_px_96dpi": height_px,
        "workarea_nm": {
            "left": -half_w_nm, "right": half_w_nm,
            "bottom": -half_h_nm, "top": half_h_nm,
        },
        "circprefs_keys_updated": updated_keys,
        "designsheet_count": workarea_count,
        "encode": encode_result,
        "backup": backup_note,
    }


def check_component_overlap(
    ms14_path: str | Path,
    clearance: float = 12.0,
    codec: Any = None,
) -> dict:
    """Report components whose pin-hull boxes overlap, without any COM call.

    The box for each component is the hull of its pin positions (the same
    model the wire router uses) inflated by ``clearance``.  Two components
    "overlap" when their boxes intersect; those pairs are where wires are
    likely to be forced through symbol bodies.  Pure decode + geometry:
    Multisim itself does not need to run, so this is the fast pre-check to
    use after ``set_component_positions`` or before a rebuild.
    """
    ms14_path = Path(ms14_path).expanduser().resolve()
    if not ms14_path.is_file():
        raise FileNotFoundError(f"no such .ms14 file: {ms14_path}")
    codec = codec or _codec()
    with tempfile.TemporaryDirectory(prefix="mcp_overlap_") as tmp:
        tree, _xml = _decode(ms14_path, Path(tmp), codec)
    placement = collect_placement(tree.getroot())

    boxes = {
        refdes: (
            record["box"]["x"],
            record["box"]["y"],
            record["box"]["x"] + record["box"]["width"],
            record["box"]["y"] + record["box"]["height"],
        )
        for refdes, record in placement.items()
    }
    refs = sorted(boxes)
    overlapping: list[dict[str, Any]] = []
    for i, a in enumerate(refs):
        ax0, ay0, ax1, ay1 = boxes[a]
        for b in refs[i + 1:]:
            bx0, by0, bx1, by1 = boxes[b]
            ox = min(ax1, bx1) - max(ax0, bx0)
            oy = min(ay1, by1) - max(ay0, by0)
            if ox > 0 and oy > 0:
                overlapping.append({
                    "components": [a, b],
                    "overlap_px": {"x": round(ox, 1), "y": round(oy, 1)},
                })

    # wire-to-component proximity summary (informational)
    wires = _wire_table(root := tree.getroot())
    wire_through: dict[tuple[str, str], int] = {}
    endpoints = _endpoint_table(root)
    owner_of = {
        endpoint_id: refdes
        for refdes, record in placement.items()
        for endpoint_id in record["endpoint_ids"]
    }
    for _item, link, c1, c2, _node in wires:
        seen_owner = {owner_of.get(c1), owner_of.get(c2)}
        for px, py in _wire_interior_points(link):
            for refdes, (x0, y0, x1, y1) in boxes.items():
                if refdes in seen_owner:
                    continue
                if x0 < px < x1 and y0 < py < y1:
                    key = (refdes, "wire")
                    wire_through[key] = wire_through.get(key, 0) + 1

    return {
        "ms14": str(ms14_path),
        "component_count": len(placement),
        "clearance": clearance,
        "overlap_pairs": overlapping,
        "overlap_count": len(overlapping),
        "wire_through_component_points": [
            {"refdes": refdes, "points": n}
            for (refdes, _kind), n in sorted(wire_through.items(), key=lambda kv: -kv[1])
            if refdes
        ][:15],
        "ok": len(overlapping) == 0,
    }


def _wire_interior_points(link: ET.Element) -> list[tuple[float, float]]:
    """Sample the wire's interior vertices (bends), not its end points."""
    points = list(link.find("./Points"))
    if len(points) <= 2:
        return []
    return [
        (float(p.get("X")), float(p.get("Y")))
        for p in points[1:-1]
    ]


def validate_layout_positions(
    netlist: str,
    positions: dict[str, list[float]] | None = None,
    sheet_size: tuple[float, float] | None = None,
    probe_nets: list[str] | None = None,
) -> dict:
    """Dry-run a custom layout and report geometry findings -- no COM, no files.

    Runs the real builder (obstacle-aware routing included) into a scratch
    directory and returns the geometry preflight: placement coordinates,
    component-overlap pairs, wire-through-body points and the final sheet
    size.  Use it to iterate on a ``component_positions`` map in seconds
    before committing to the minute-scale build + verification cycle.
    """
    import tempfile

    from .schematic_builder import build_schematic

    positions = positions or {}
    if not isinstance(positions, dict):
        raise ValueError("positions must be a {refdes: [x, y]} mapping")

    with tempfile.TemporaryDirectory(prefix="mcp_layout_check_") as tmp:
        out = Path(tmp) / "design.xml"
        result = build_schematic(
            netlist,
            out,
            probe_nets=probe_nets,
            component_positions=positions,
            min_sheet_size=tuple(sheet_size) if sheet_size else None,
        )

    lv = result.get("layout_validation", {})
    geometry = result.get("geometry", {})
    placements = {
        p["refdes"]: {"x": p["x"], "y": p["y"]}
        for p in geometry.get("placements", [])
    }

    return {
        "netlist_bytes": len(netlist),
        "status": lv.get("status"),
        "component_count": lv.get("component_count"),
        "wire_count": lv.get("wire_count"),
        "different_net_crossings": lv.get("different_net_crossings"),
        "crossings_per_wire": lv.get("crossings_per_wire"),
        "crossings_limit_per_wire": lv.get("crossings_limit_per_wire"),
        "findings": lv.get("findings", []),
        "error_count": sum(
            1 for f in lv.get("findings", []) if f.get("severity") == "error"
        ),
        "placements": placements,
        "sheet": result.get("sheet"),
        "wire_fallbacks": len(result.get("wire_fallbacks", [])),
        "note": "dry-run: built into a scratch directory, nothing was kept; "
                "callers must require status=pass before delivery",
    }
