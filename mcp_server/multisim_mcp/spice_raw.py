"""Parse SPICE3 ASCII raw files and emit CSV / SVG chart artifacts.

Multisim's DoCommandLine writes standard SPICE3 raw files. This module is
kept dependency-free so the 32-bit MCP runtime can parse and export results
without numpy or matplotlib.
"""

from __future__ import annotations

import csv
import html
import math
import os
import re
from typing import Any


_NUMBER_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _raw_value(token: str, complex_mode: bool) -> float | complex | None:
    if _NUMBER_RE.match(token):
        return float(token)
    if complex_mode and token.count(",") == 1:
        real, imaginary = token.split(",", 1)
        if _NUMBER_RE.match(real) and _NUMBER_RE.match(imaginary):
            return complex(float(real), float(imaginary))
    return None


def parse_raw(path: str) -> dict[str, Any]:
    """Parse and strictly validate an ASCII SPICE3 raw file."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    header: dict[str, Any] = {}
    variables: list[dict[str, Any]] = []
    rows: list[list[float]] = []
    real_rows: list[list[float]] = []
    imaginary_rows: list[list[float]] = []
    phase_rows: list[list[float]] = []
    current: list[float | complex] | None = None
    current_index: int | None = None
    in_variables = False
    in_values = False

    for line in lines:
        stripped = line.strip()
        if not in_values:
            if not stripped:
                continue
            if stripped.startswith("No. Variables:"):
                header["n_variables"] = int(stripped.split(":", 1)[1].strip())
            elif stripped.startswith("No. Points:"):
                header["n_points"] = int(stripped.split(":", 1)[1].strip())
            elif stripped.startswith("Plotname:"):
                header["plotname"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Title:"):
                header["title"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Date:"):
                header["date"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Flags:"):
                header["flags"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Params:"):
                header["params"] = stripped.split(":", 1)[1].strip()
            elif stripped == "Variables:":
                in_variables = True
            elif stripped == "Values:":
                in_variables = False
                in_values = True
            elif in_variables and re.match(r"^\d+\s", stripped):
                # variables section rows: index, name, type, plotname
                parts = stripped.split()
                if len(parts) >= 4:
                    variables.append(
                        {
                            "index": int(parts[0]),
                            "name": parts[1],
                            "type": parts[2],
                            "plot": " ".join(parts[3:]),
                        }
                    )
            continue

        if not stripped:
            continue
        tokens = stripped.split()
        if len(tokens) >= 2 and tokens[0].isdigit() and current is None:
            current_index = int(tokens[0])
            current = []
            complex_mode = "complex" in str(header.get("flags", "")).casefold()
            for token in tokens[1:]:
                value = _raw_value(token, complex_mode)
                if value is not None:
                    current.append(value)
                else:
                    current = None
                    current_index = None
                    break
        elif current is not None and len(tokens) == 1:
            complex_mode = "complex" in str(header.get("flags", "")).casefold()
            value = _raw_value(tokens[0], complex_mode)
            if value is not None:
                current.append(value)
            else:
                current = None
                current_index = None
        else:
            current = None
            current_index = None

        expected_width = header.get("n_variables")
        if current is not None and expected_width and len(current) == expected_width:
            if current_index != len(rows):
                raise ValueError(
                    f"Raw point index {current_index} is not the expected index {len(rows)}"
                )
            if any(isinstance(value, complex) for value in current):
                values = [complex(value) for value in current]
                real_rows.append([value.real for value in values])
                imaginary_rows.append([value.imag for value in values])
                phase_rows.append([math.degrees(math.atan2(value.imag, value.real)) for value in values])
                rows.append([abs(value) for value in values])
            else:
                rows.append([float(value) for value in current])
            current = None
            current_index = None
        elif current is not None and expected_width and len(current) > expected_width:
            raise ValueError("Raw data row contains more values than declared variables")

    required = {"n_variables", "n_points", "plotname"}
    missing = sorted(required - header.keys())
    if missing:
        raise ValueError("Invalid SPICE raw file; missing headers: " + ", ".join(missing))
    n_variables = header["n_variables"]
    n_points = header["n_points"]
    if n_variables <= 0 or n_points <= 0:
        raise ValueError("SPICE raw file must declare at least one variable and one point")
    if not in_values:
        raise ValueError("Invalid SPICE raw file; Values section is missing")
    if current is not None:
        raise ValueError("SPICE raw file ends with an incomplete data row")
    if len(variables) != n_variables:
        raise ValueError(
            f"SPICE raw variable count mismatch: declared {n_variables}, parsed {len(variables)}"
        )
    if [item["index"] for item in variables] != list(range(n_variables)):
        raise ValueError("SPICE raw variable indices must be contiguous from zero")
    if len(rows) != n_points:
        raise ValueError(
            f"SPICE raw point count mismatch: declared {n_points}, parsed {len(rows)}"
        )
    if any(len(row) != n_variables for row in rows):
        raise ValueError("SPICE raw row width does not match the declared variable count")

    result = {
        "header": header,
        "variables": variables,
        "columns": [v["plot"] for v in variables],
        "n_points": len(rows),
        "rows": rows,
        "value_representation": "magnitude" if real_rows else "real",
    }
    if real_rows:
        if len(real_rows) != len(rows):
            raise ValueError("Complex SPICE raw data contains mixed row encodings")
        result["real_rows"] = real_rows
        result["imaginary_rows"] = imaginary_rows
        result["phase_rows"] = phase_rows
    return result


def write_csv(path: str, parsed: dict[str, Any]) -> str:
    """Write parsed raw data to CSV, one column per raw variable."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(parsed["columns"])
        writer.writerows(parsed["rows"])
    return path


def summarize_columns(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Compute deterministic first/last/min/max/mean measurements per column."""
    columns = parsed.get("columns") or []
    rows = parsed.get("rows") or []
    summaries: list[dict[str, Any]] = []
    for index, name in enumerate(columns):
        values = [
            float(row[index])
            for row in rows
            if len(row) > index and isinstance(row[index], (int, float))
            and math.isfinite(float(row[index]))
        ]
        if not values:
            continue
        summaries.append(
            {
                "column": name,
                "count": len(values),
                "first": values[0],
                "last": values[-1],
                "min": min(values),
                "max": max(values),
                "mean": sum(values) / len(values),
            }
        )
    return summaries


def _auto_scale(values: list[float]) -> tuple[float, float, float]:
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return (0.0, 1.0, 0.5)
    low, high = min(finite), max(finite)
    if math.isclose(low, high):
        pad = max(abs(low) * 0.05, 0.5)
        low, high = low - pad, high + pad
    else:
        pad = (high - low) * 0.05
        low, high = low - pad, high + pad
    return low, high, (low + high) / 2.0


def _format_tick(value: float) -> str:
    if abs(value) >= 1000 or (abs(value) > 0 and abs(value) < 0.001):
        return f"{value:.3g}"
    if abs(value) >= 10:
        return f"{value:.4g}"
    return f"{value:.4g}"


def plot_svg(
    path: str,
    series: list[dict[str, Any]],
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    markers: list[dict[str, Any]] | None = None,
    width: int = 960,
    height: int = 560,
) -> str:
    """Render a simple XY line chart to SVG without third-party packages."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    margin_l, margin_r, margin_t, margin_b = 76, 24, 46, 56
    plot_w = width - margin_l - margin_r
    plot_h = height - margin_t - margin_b

    xs: list[float] = []
    ys: list[float] = []
    for item in series:
        xs.extend(item.get("x", []))
        ys.extend(item.get("y", []))
    x0, x1, _ = _auto_scale(xs)
    y0, y1, _ = _auto_scale(ys)

    def px(x: float) -> float:
        return margin_l + (x - x0) / (x1 - x0) * plot_w if x1 != x0 else margin_l + plot_w / 2

    def py(y: float) -> float:
        return margin_t + (y1 - y) / (y1 - y0) * plot_h if y1 != y0 else margin_t + plot_h / 2

    palette = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0f766e"]
    escaped_title = html.escape(str(title), quote=True)
    escaped_x_label = html.escape(str(x_label), quote=True)
    escaped_y_label = html.escape(str(y_label), quote=True)
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Segoe UI, Arial, sans-serif">'
    )
    parts.append(f'<text x="{width/2}" y="28" text-anchor="middle" font-size="19" font-weight="600">{escaped_title}</text>')

    # grid
    for i in range(6):
        gx = margin_l + plot_w * i / 5
        parts.append(f'<line x1="{gx:.1f}" y1="{margin_t}" x2="{gx:.1f}" y2="{margin_t+plot_h}" stroke="#e5e7eb" stroke-width="1"/>')
        parts.append(f'<text x="{gx:.1f}" y="{margin_t+plot_h+18}" text-anchor="middle" font-size="12" fill="#6b7280">{_format_tick(x0 + (x1-x0)*i/5)}</text>')
    for i in range(6):
        gy = margin_t + plot_h * i / 5
        parts.append(f'<line x1="{margin_l}" y1="{gy:.1f}" x2="{margin_l+plot_w}" y2="{gy:.1f}" stroke="#e5e7eb" stroke-width="1"/>')
        parts.append(f'<text x="{margin_l-8}" y="{gy+4:.1f}" text-anchor="end" font-size="12" fill="#6b7280">{_format_tick(y1 - (y1-y0)*i/5)}</text>')

    parts.append(f'<rect x="{margin_l}" y="{margin_t}" width="{plot_w}" height="{plot_h}" fill="none" stroke="#9ca3af" stroke-width="1"/>')
    parts.append(f'<text x="{margin_l}" y="{height-14}" font-size="13" fill="#374151">{escaped_x_label}</text>')
    parts.append(f'<text x="14" y="{height/2}" font-size="13" fill="#374151" transform="rotate(-90 14 {height/2})" text-anchor="middle">{escaped_y_label}</text>')

    for idx, item in enumerate(series):
        color = html.escape(str(item.get("color") or palette[idx % len(palette)]), quote=True)
        pts = []
        for x, y in zip(item.get("x", []), item.get("y", [])):
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            pts.append(f"{px(x):.1f},{py(y):.1f}")
        if pts:
            parts.append(
                f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" '
                f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
            )
            parts.append(
                f'<circle cx="{px(series[idx]["x"][0]):.1f}" cy="{py(series[idx]["y"][0]):.1f}" '
                f'r="3" fill="{color}"/>'
            )

    for marker in markers or []:
        mx, my = marker.get("x"), marker.get("y")
        if mx is None or my is None:
            continue
        color = html.escape(str(marker.get("color", "#111827")), quote=True)
        label = html.escape(str(marker.get("label", "")), quote=True)
        parts.append(
            f'<circle cx="{px(mx):.1f}" cy="{py(my):.1f}" r="5" fill="none" stroke="{color}" stroke-width="2"/>'
        )
        parts.append(
            f'<line x1="{px(mx):.1f}" y1="{py(my):.1f}" x2="{px(mx)+18:.1f}" y2="{py(my)-18:.1f}" '
            f'stroke="{color}" stroke-width="1.5"/>'
        )
        parts.append(
            f'<text x="{px(mx)+24:.1f}" y="{py(my)-20:.1f}" font-size="13" fill="{color}">{label}</text>'
        )

    legend_x = margin_l + 12
    legend_y = margin_t + 18
    for idx, item in enumerate(series):
        color = html.escape(str(item.get("color") or palette[idx % len(palette)]), quote=True)
        series_name = html.escape(str(item.get("name", f"series {idx+1}")), quote=True)
        parts.append(f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x+26}" y2="{legend_y}" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="{legend_x+34}" y="{legend_y+4}" font-size="13" fill="#374151">{series_name}</text>')
        legend_y += 22

    parts.append("</svg>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return path


def try_plot_png(
    path: str,
    series: list[dict[str, Any]],
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    markers: list[dict[str, Any]] | None = None,
) -> str | None:
    """Render PNG with matplotlib when available; otherwise return None."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None

    palette = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0f766e"]
    fig, ax = plt.subplots(figsize=(9.6, 5.6), dpi=120)
    for idx, item in enumerate(series):
        ax.plot(
            item.get("x", []),
            item.get("y", []),
            label=item.get("name", f"series {idx + 1}"),
            color=item.get("color") or palette[idx % len(palette)],
            linewidth=2,
        )
    for marker in markers or []:
        if marker.get("x") is None or marker.get("y") is None:
            continue
        ax.plot(
            marker["x"],
            marker["y"],
            "o",
            mfc="none",
            mec=marker.get("color", "#111827"),
            mew=2,
            ms=9,
        )
        ax.annotate(
            marker.get("label", ""),
            (marker["x"], marker["y"]),
            textcoords="offset points",
            xytext=(12, -10),
            fontsize=10,
            color=marker.get("color", "#111827"),
        )
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.grid(True, linestyle="--", alpha=0.35)
    ax.legend(fontsize=10)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path
