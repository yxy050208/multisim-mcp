"""Dependency-free topology diagrams for non-schematic SPICE backends."""

from __future__ import annotations

import hashlib
import html
import json
import struct
import zlib
from pathlib import Path
from typing import Any

from .component_adapters import expand_component_adapters
from .spice_adapter import circuit_design_from_spice


def _color(value: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(value.casefold().encode("utf-8")).digest()
    return (55 + digest[0] % 150, 55 + digest[1] % 150, 55 + digest[2] % 150)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _write_png(
    path: Path,
    width: int,
    height: int,
    edges: list[tuple[tuple[int, int], tuple[int, int], tuple[int, int, int]]],
    net_points: list[tuple[int, int, tuple[int, int, int]]],
    component_boxes: list[tuple[int, int, int, int]],
) -> None:
    pixels = bytearray([255] * (width * height * 3))

    def point(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 3
            pixels[offset : offset + 3] = bytes(color)

    def line(a: tuple[int, int], b: tuple[int, int], color: tuple[int, int, int]) -> None:
        x0, y0 = a
        x1, y1 = b
        dx = abs(x1 - x0)
        sx = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0)
        sy = 1 if y0 < y1 else -1
        error = dx + dy
        while True:
            for thickness in (-1, 0, 1):
                point(x0, y0 + thickness, color)
            if x0 == x1 and y0 == y1:
                break
            doubled = 2 * error
            if doubled >= dy:
                error += dy
                x0 += sx
            if doubled <= dx:
                error += dx
                y0 += sy

    for start, end, color in edges:
        line(start, end, color)
    for x, y, color in net_points:
        for dx in range(-6, 7):
            for dy in range(-6, 7):
                if dx * dx + dy * dy <= 36:
                    point(x + dx, y + dy, color)
    for x, y, box_width, box_height in component_boxes:
        line((x, y), (x + box_width, y), (23, 32, 51))
        line((x, y + box_height), (x + box_width, y + box_height), (23, 32, 51))
        line((x, y), (x, y + box_height), (23, 32, 51))
        line((x + box_width, y), (x + box_width, y + box_height), (23, 32, 51))

    raw = b"".join(b"\x00" + bytes(pixels[row * width * 3 : (row + 1) * width * 3]) for row in range(height))
    document = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )
    path.write_bytes(document)


def render_portable_schematic(
    netlist: str,
    output_design: str,
    *,
    probe_nets: list[str] | None = None,
    include_experimental_probes: bool = False,
    open_after_build: bool = False,
    image_path: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Render an accurate labeled connectivity graph as SVG plus PNG preview."""
    del probe_nets, include_experimental_probes, open_after_build
    svg_path = Path(output_design).expanduser().resolve()
    if svg_path.suffix.casefold() != ".svg":
        raise ValueError("portable schematic output must end with .svg")
    png_path = (
        Path(image_path).expanduser().resolve()
        if image_path is not None
        else svg_path.with_suffix(".png")
    )
    if png_path.suffix.casefold() != ".png":
        raise ValueError("portable schematic image must end with .png")
    if png_path.parent != svg_path.parent:
        raise ValueError("portable schematic artifacts must share one output directory")
    backend_path = svg_path.parent / "backend.json"
    for path in (svg_path, png_path, backend_path):
        if path.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite portable schematic artifact: {path}")
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"portable schematic artifact must be a regular file: {path}")
    svg_path.parent.mkdir(parents=True, exist_ok=True)

    expanded = expand_component_adapters(netlist)
    design = circuit_design_from_spice(
        expanded,
        title=svg_path.stem,
        allow_unsupported=True,
    )
    components = list(design.components)
    nets = list(design.nets)
    if len(components) > 500 or len(nets) > 500:
        raise ValueError("portable topology renderer supports at most 500 components and 500 nets")
    row_height = 52
    height = max(420, 120 + max(len(nets), len(components)) * row_height)
    width = 1200
    net_x = 180
    component_x = 710
    box_width = 350
    box_height = 34
    net_positions = {
        net: (net_x, 100 + index * row_height) for index, net in enumerate(nets)
    }
    component_positions = {
        component.refdes: (component_x, 92 + index * row_height)
        for index, component in enumerate(components)
    }
    svg: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>text{font-family:Segoe UI,Arial,sans-serif;fill:#172033}.heading{font-size:22px;font-weight:700}.label{font-size:13px}.small{font-size:11px;fill:#526079}.component{fill:#eff6ff;stroke:#172033;stroke-width:1.5}</style>",
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="40" y="40" class="heading">Portable SPICE connectivity diagram</text>',
        '<text x="40" y="66" class="small">Labeled topology graph; not an editable vendor schematic</text>',
        '<text x="80" y="88" class="label">Nets</text>',
        '<text x="710" y="88" class="label">Components</text>',
    ]
    png_edges: list[tuple[tuple[int, int], tuple[int, int], tuple[int, int, int]]] = []
    png_nets: list[tuple[int, int, tuple[int, int, int]]] = []
    png_boxes: list[tuple[int, int, int, int]] = []
    for net, (x, y) in net_positions.items():
        color = _color(net)
        svg.append(f'<circle cx="{x}" cy="{y}" r="7" fill="rgb{color}"/>')
        svg.append(f'<text x="{x - 14}" y="{y + 5}" text-anchor="end" class="label">{html.escape(net)}</text>')
        png_nets.append((x, y, color))
    for component in components:
        x, y = component_positions[component.refdes]
        svg.append(f'<rect x="{x}" y="{y}" width="{box_width}" height="{box_height}" rx="5" class="component"/>')
        descriptor = " · ".join(value for value in (component.refdes, component.kind, component.value or component.model) if value)
        svg.append(f'<text x="{x + 12}" y="{y + 22}" class="label">{html.escape(descriptor)}</text>')
        png_boxes.append((x, y, box_width, box_height))
        for pin_index, node in enumerate(component.nodes, start=1):
            start = net_positions.get(node)
            if start is None:
                continue
            end = (x, y + min(box_height - 5, 5 + pin_index * 6))
            color = _color(node)
            svg.append(
                f'<line x1="{start[0] + 8}" y1="{start[1]}" x2="{end[0]}" y2="{end[1]}" stroke="rgb{color}" stroke-width="1.5" opacity="0.72"/>'
            )
            png_edges.append(((start[0] + 8, start[1]), end, color))
    if not components:
        svg.append('<text x="40" y="130" class="label">No structured component topology was available.</text>')
    svg.append("</svg>")
    svg_path.write_text("\n".join(svg) + "\n", encoding="utf-8", newline="\n")
    png_height = min(height, 4000)
    clipped_edges = [item for item in png_edges if item[0][1] < png_height and item[1][1] < png_height]
    clipped_nets = [item for item in png_nets if item[1] < png_height]
    clipped_boxes = [item for item in png_boxes if item[1] + item[3] < png_height]
    _write_png(png_path, width, png_height, clipped_edges, clipped_nets, clipped_boxes)
    backend = {
        "schema_version": 1,
        "backend_id": "ngspice",
        "display_name": "ngspice open-source simulator",
        "schematic_kind": "connectivity-graph",
        "editable_schematic": False,
        "diagram": svg_path.name,
        "preview": png_path.name,
        "component_count": len(components),
        "net_count": len(nets),
    }
    backend_path.write_text(json.dumps(backend, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "success": True,
        "backend_id": "ngspice",
        "diagram": str(svg_path),
        "image": str(png_path),
        "backend_manifest": str(backend_path),
        "editable": False,
        "build": {
            "representation": "connectivity-graph",
            "component_count": len(components),
            "net_count": len(nets),
            "model_warnings": [],
        },
    }


__all__ = ["render_portable_schematic"]
