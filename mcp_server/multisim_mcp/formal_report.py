"""Self-contained bilingual HTML/PDF reports and reproducibility manifests."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import platform
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from multisim_mcp import __version__
from multisim_mcp.spice_raw import parse_raw, summarize_columns


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contained_file(root: Path, name: str, *, required: bool = True) -> Path | None:
    path = root / name
    if not path.exists():
        if required:
            raise FileNotFoundError(f"formal report input is missing: {name}")
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"formal report artifact must be a regular in-directory file: {name}")
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"formal report artifact escapes its directory: {name}") from exc
    return resolved


def _atomic_bytes(path: Path, value: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_text(path: Path, value: str) -> None:
    _atomic_bytes(path, value.encode("utf-8"))


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _backend_metadata(root: Path) -> dict[str, Any]:
    metadata = _load_json(root / "backend.json") or {}
    backend_id = metadata.get("backend_id")
    display_name = metadata.get("display_name")
    return {
        "backend_id": backend_id if isinstance(backend_id, str) else "multisim",
        "display_name": (
            display_name
            if isinstance(display_name, str) and display_name.strip()
            else "NI Multisim"
        ),
    }


def _schematic_artifact(root: Path) -> tuple[Path, str]:
    svg = _contained_file(root, "schematic.svg", required=False)
    if svg is not None:
        return svg, "image/svg+xml"
    png = _contained_file(root, "schematic.png")
    assert png is not None
    return png, "image/png"


def _html_report(root: Path, language: str, summaries: list[dict[str, Any]], verification: dict[str, Any] | None) -> str:
    zh = language == "zh-CN"
    backend = _backend_metadata(root)
    title = (
        "Multisim 电路实验报告"
        if zh and backend["backend_id"] == "multisim"
        else "电路实验报告"
        if zh
        else "Multisim Circuit Experiment Report"
        if backend["backend_id"] == "multisim"
        else "Circuit Experiment Report"
    )
    labels = {
        "overview": "实验概览" if zh else "Experiment overview",
        "schematic": "电路图" if zh else "Schematic",
        "plot": "实验曲线" if zh else "Experiment plot",
        "measurements": "数据摘要" if zh else "Data summary",
        "verification": "指标验证" if zh else "Requirement verification",
        "source": "可复现输入" if zh else "Reproducible inputs",
        "compatibility": "SPICE 兼容性与模型来源" if zh else "SPICE compatibility and model provenance",
        "digital_observation": "数字输出观测证据" if zh else "Digital output observation evidence",
        "artifact": "原始 Markdown 报告" if zh else "Original Markdown report",
    }
    rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(item.get(key, '')))}</td>" for key in ("column", "count", "first", "last", "min", "max", "mean")) + "</tr>"
        for item in summaries
    )
    verdict_rows = ""
    if verification:
        requirements = verification.get("requirements") or verification.get("results") or []
        for item in requirements if isinstance(requirements, list) else []:
            verdict_rows += "<tr>" + "".join(
                f"<td>{html.escape(str(item.get(key, '')))}</td>"
                for key in ("id", "status", "value", "target", "unit", "reason")
            ) + "</tr>"
    original = (root / "report.md").read_text(encoding="utf-8", errors="replace")
    netlist = (root / "circuit.cir").read_text(encoding="utf-8", errors="replace")
    commands = (root / "run.txt").read_text(encoding="utf-8", errors="replace")
    compatibility = _load_json(root / "spice-compatibility.json") or {}
    digital_observation = _load_json(root / "digital-observation.json") or {}
    compatibility_summary = compatibility.get("summary", {})
    compatibility_dialect = compatibility.get("dialect", {})
    compatibility_backend = compatibility.get("backend", {})
    compatibility_summary = compatibility_summary if isinstance(compatibility_summary, dict) else {}
    compatibility_dialect = compatibility_dialect if isinstance(compatibility_dialect, dict) else {}
    compatibility_backend = compatibility_backend if isinstance(compatibility_backend, dict) else {}
    compatibility_rows = "".join(
        f"<tr><td>{html.escape(label)}</td><td>{html.escape(str(value))}</td></tr>"
        for label, value in (
            ("Dialect", compatibility_dialect.get("name", "not recorded")),
            ("Dialect source", compatibility_dialect.get("source", "not recorded")),
            ("Static status", compatibility_backend.get("compatibility_status", "not recorded")),
            ("Solver version", compatibility_backend.get("solver_version") or "not captured"),
            ("Risk", compatibility_summary.get("risk_level", "not recorded")),
            ("Models", compatibility_summary.get("model_count", 0)),
            ("Unknown licenses", compatibility_summary.get("unknown_license_count", 0)),
            ("Provenance complete", compatibility_summary.get("provenance_complete", False)),
        )
    )
    observation_rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(item.get(key) or '—'))}</td>"
            for key in ("component", "pin", "net", "status", "claim", "raw_column")
        )
        + "</tr>"
        for item in digital_observation.get("signals", [])
        if isinstance(item, dict)
    )
    observation_routing = digital_observation.get("routing", {})
    if isinstance(observation_routing, dict) and observation_routing.get("mode") == "explicit-rerun":
        routing_note = (
            "Explicit fallback recommendation: rerun with "
            f"{observation_routing.get('recommended_backend', 'ngspice')}. "
            f"{observation_routing.get('reason', '')}"
        )
    else:
        routing_note = "Automatic backend switching is disabled."
    generated = datetime.now(timezone.utc).isoformat()
    schematic_path, schematic_mime = _schematic_artifact(root)
    schematic_uri = f"data:{schematic_mime};base64," + base64.b64encode(
        schematic_path.read_bytes()
    ).decode("ascii")
    plot_uri = "data:image/svg+xml;base64," + base64.b64encode(
        (root / "plot.svg").read_bytes()
    ).decode("ascii")
    return f"""<!doctype html>
<html lang="{language}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{title}</title><style>
body{{font-family:Segoe UI,Noto Sans CJK SC,Microsoft YaHei,sans-serif;max-width:1080px;margin:36px auto;padding:0 24px;color:#172033;line-height:1.55}}
h1{{border-bottom:3px solid #2563eb;padding-bottom:12px}} h2{{margin-top:32px;color:#1d4ed8}} img{{max-width:100%;border:1px solid #d7deea;border-radius:8px}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{border:1px solid #d7deea;padding:7px;text-align:left}} th{{background:#eff6ff}}
pre{{white-space:pre-wrap;background:#f6f8fb;padding:16px;border-radius:8px;overflow:auto}} .meta{{color:#526079}} footer{{margin-top:40px;border-top:1px solid #d7deea;padding-top:12px;color:#526079}}
</style></head><body><h1>{title}</h1>
<p class="meta">Multisim MCP {html.escape(__version__)} · {html.escape(str(backend['display_name']))} · {html.escape(generated)}</p>
<h2>{labels['overview']}</h2><pre>{html.escape(original[:12000])}</pre>
<h2>{labels['schematic']}</h2><img src="{schematic_uri}" alt="schematic">
<h2>{labels['plot']}</h2><img src="{plot_uri}" alt="plot">
<h2>{labels['measurements']}</h2><table><thead><tr><th>Signal</th><th>N</th><th>First</th><th>Last</th><th>Min</th><th>Max</th><th>Mean</th></tr></thead><tbody>{rows}</tbody></table>
<h2>{labels['verification']}</h2><table><thead><tr><th>ID</th><th>Status</th><th>Value</th><th>Target</th><th>Unit</th><th>Reason</th></tr></thead><tbody>{verdict_rows}</tbody></table>
<h2>{labels['compatibility']}</h2><table><tbody>{compatibility_rows}</tbody></table>
<h2>{labels['digital_observation']}</h2><p>{html.escape(str(digital_observation.get('overall_status', 'not-applicable')))}</p><p>{html.escape(routing_note)}</p><table><thead><tr><th>Component</th><th>Pin</th><th>Net</th><th>Status</th><th>Claim</th><th>Raw column</th></tr></thead><tbody>{observation_rows}</tbody></table>
<h2>{labels['source']}</h2><h3>SPICE</h3><pre>{html.escape(netlist)}</pre><h3>Commands</h3><pre>{html.escape(commands)}</pre>
<footer>{labels['artifact']} · manifest.json contains SHA-256 hashes for reproducibility.</footer></body></html>"""


def _wrap_text(text: str, width: int) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return [""]
    lines: list[str] = []
    current = ""
    for char in clean:
        units = 2 if ord(char) > 127 else 1
        current_units = sum(2 if ord(item) > 127 else 1 for item in current)
        if current and current_units + units > width:
            lines.append(current)
            current = char
        else:
            current += char
    if current:
        lines.append(current)
    return lines


def _pdf_string(text: str, cjk: bool) -> bytes:
    if cjk:
        return b"<FEFF" + text.encode("utf-16-be").hex().upper().encode("ascii") + b">"
    safe = text.encode("latin-1", errors="replace").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    return b"(" + safe + b")"


def _write_pdf(path: Path, title: str, lines: list[str], cjk: bool) -> None:
    per_page = 43
    pages = [lines[index:index + per_page] for index in range(0, len(lines), per_page)] or [[title]]
    # Objects: catalog, pages tree, font(s), then page/content pairs.
    objects: list[bytes] = [b"", b""]
    if cjk:
        font_id = 3
        objects.extend([
            b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H /DescendantFonts [4 0 R] >>",
            b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> /DW 1000 /FontDescriptor 5 0 R >>",
            b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 6 /FontBBox [-25 -254 1000 880] /ItalicAngle 0 /Ascent 880 /Descent -120 /CapHeight 880 /StemV 80 >>",
        ])
    else:
        font_id = 3
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    for page_lines in pages:
        page_id = len(objects) + 1
        content_id = page_id + 1
        page_ids.append(page_id)
        commands = [b"BT", b"/F1 11 Tf", b"50 800 Td", b"15 TL"]
        for index, line in enumerate(page_lines):
            if index:
                commands.append(b"T*")
            commands.append(_pdf_string(line, cjk) + b" Tj")
        commands.append(b"ET")
        stream = b"\n".join(commands)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode("ascii"))
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{item} 0 R' for item in page_ids)}] /Count {len(page_ids)} >>".encode("ascii")
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, value in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(value)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    _atomic_bytes(path, bytes(output))


def _pdf_lines(root: Path, language: str, summaries: list[dict[str, Any]], verification: dict[str, Any] | None) -> list[str]:
    zh = language == "zh-CN"
    backend = _backend_metadata(root)
    title = (
        "Multisim 电路实验报告"
        if zh and backend["backend_id"] == "multisim"
        else "电路实验报告"
        if zh
        else "Multisim Circuit Experiment Report"
        if backend["backend_id"] == "multisim"
        else "Circuit Experiment Report"
    )
    generated = (
        f"由 Multisim MCP 自动生成 · {backend['display_name']}"
        if zh
        else f"Generated automatically by Multisim MCP · {backend['display_name']}"
    )
    lines = [title, "", generated, ""]
    markdown = (root / "report.md").read_text(encoding="utf-8", errors="replace")
    overview = re.sub(r"(?m)^#{1,6}\s*", "", markdown[:3000])
    overview = re.sub(r"[`*_>|]", "", overview)
    lines.append("实验概览" if zh else "Experiment overview")
    lines.extend(_wrap_text(overview, 82 if zh else 92))
    lines.append("")
    lines.append("数据摘要" if zh else "Data summary")
    for item in summaries:
        lines.extend(_wrap_text(f"{item['column']}: N={item['count']}, min={item['min']:.8g}, max={item['max']:.8g}, mean={item['mean']:.8g}", 82 if zh else 92))
    lines.extend(["", "指标验证" if zh else "Requirement verification"])
    if verification:
        requirements = verification.get("requirements") or verification.get("results") or []
        for item in requirements if isinstance(requirements, list) else []:
            lines.extend(_wrap_text(f"{item.get('id', '')}: {item.get('status', '')} value={item.get('value', '')} target={item.get('target', '')} {item.get('unit', '')}", 82 if zh else 92))
    else:
        lines.append("未提供验证要求。" if zh else "No verification requirements were provided.")
    compatibility = _load_json(root / "spice-compatibility.json") or {}
    digital_observation = _load_json(root / "digital-observation.json") or {}
    compatibility_summary = compatibility.get("summary", {})
    compatibility_dialect = compatibility.get("dialect", {})
    compatibility_backend = compatibility.get("backend", {})
    compatibility_summary = compatibility_summary if isinstance(compatibility_summary, dict) else {}
    compatibility_dialect = compatibility_dialect if isinstance(compatibility_dialect, dict) else {}
    compatibility_backend = compatibility_backend if isinstance(compatibility_backend, dict) else {}
    lines.extend(["", "SPICE 兼容性与模型来源" if zh else "SPICE compatibility and model provenance"])
    lines.extend(
        _wrap_text(
            (
                f"dialect={compatibility_dialect.get('name', 'not recorded')}; "
                f"static_status={compatibility_backend.get('compatibility_status', 'not recorded')}; "
                f"solver_version={compatibility_backend.get('solver_version') or 'not captured'}; "
                f"models={compatibility_summary.get('model_count', 0)}; "
                f"unknown_licenses={compatibility_summary.get('unknown_license_count', 0)}; "
                f"provenance_complete={compatibility_summary.get('provenance_complete', False)}"
            ),
            82 if zh else 92,
        )
    )
    observation_routing = digital_observation.get("routing", {})
    if isinstance(observation_routing, dict) and observation_routing.get("mode") == "explicit-rerun":
        lines.extend(
            _wrap_text(
                (
                    f"explicit_fallback_backend={observation_routing.get('recommended_backend', 'ngspice')}; "
                    "automatic_switch=False; "
                    f"reason={observation_routing.get('reason', '')}"
                ),
                82 if zh else 92,
            )
        )
    lines.extend(["", "数字输出观测证据" if zh else "Digital output observation evidence"])
    lines.extend(
        _wrap_text(
            f"overall_status={digital_observation.get('overall_status', 'not-applicable')}; "
            f"observed={((digital_observation.get('counts') or {}).get('observed', 0))}; "
            f"unobserved={((digital_observation.get('counts') or {}).get('unobserved', 0))}",
            82 if zh else 92,
        )
    )
    for item in digital_observation.get("signals", []):
        if isinstance(item, dict):
            lines.extend(
                _wrap_text(
                    f"{item.get('component', '')}.{item.get('pin', '')} "
                    f"net={item.get('net', '')} status={item.get('status', '')} "
                    f"claim={item.get('claim', '')} raw={item.get('raw_column') or '—'}",
                    82 if zh else 92,
                )
            )
    lines.extend(["", "电路网表" if zh else "Circuit netlist"])
    for netlist_line in (root / "circuit.cir").read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        lines.extend(_wrap_text(netlist_line, 82 if zh else 92))
    lines.extend(["", "可复现性" if zh else "Reproducibility", "manifest.json 包含全部产物的 SHA-256。" if zh else "manifest.json contains SHA-256 hashes for all artifacts."])
    return lines


def export_formal_reports(root: Path, experiment_id: str) -> dict[str, Any]:
    """Write bilingual HTML/PDF reports plus a deterministic artifact manifest."""
    root = root.resolve()
    required = ("report.md", "plot.svg", "result.raw", "circuit.cir", "run.txt")
    for name in required:
        _contained_file(root, name)
    _schematic_artifact(root)
    _contained_file(root, "verification.json", required=False)
    summaries = summarize_columns(parse_raw(str(root / "result.raw")))
    verification = _load_json(root / "verification.json")
    compatibility = _load_json(root / "spice-compatibility.json")
    outputs = {
        "html_zh": root / "report.zh-CN.html",
        "html_en": root / "report.en.html",
        "pdf_zh": root / "report.zh-CN.pdf",
        "pdf_en": root / "report.en.pdf",
    }
    manifest_path = root / "manifest.json"
    for path in [*outputs.values(), manifest_path]:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"formal report output must be a regular file: {path.name}")
    _atomic_text(outputs["html_zh"], _html_report(root, "zh-CN", summaries, verification))
    _atomic_text(outputs["html_en"], _html_report(root, "en", summaries, verification))
    backend = _backend_metadata(root)
    _write_pdf(outputs["pdf_zh"], "电路实验报告", _pdf_lines(root, "zh-CN", summaries, verification), True)
    _write_pdf(outputs["pdf_en"], "Circuit Experiment Report", _pdf_lines(root, "en", summaries, verification), False)
    artifact_rows = []
    for path in sorted(
        item
        for item in root.iterdir()
        if item.is_file() and not item.is_symlink() and item.name != "manifest.json"
    ):
        artifact_rows.append({"filename": path.name, "size": path.stat().st_size, "sha256": _sha256(path)})
    compatibility_netlist = (
        compatibility.get("netlist", {})
        if isinstance(compatibility, dict)
        and isinstance(compatibility.get("netlist"), dict)
        else {}
    )
    compatibility_summary = (
        compatibility.get("summary", {})
        if isinstance(compatibility, dict)
        and isinstance(compatibility.get("summary"), dict)
        else {}
    )
    manifest = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "generator": {"name": "multisim-mcp", "version": __version__},
        "runtime": {"python": platform.python_version(), "platform": platform.platform()},
        "backend": backend,
        "spice_compatibility": (
            {
                "artifact": "spice-compatibility.json",
                "netlist_sha256": compatibility_netlist.get("sha256"),
                "model_fingerprint_sha256": compatibility.get(
                    "model_fingerprint_sha256"
                ),
                "risk_level": compatibility_summary.get("risk_level"),
                "provenance_complete": compatibility_summary.get("provenance_complete"),
            }
            if compatibility is not None
            else None
        ),
        "artifacts": artifact_rows,
        "reproduce": {
            "netlist": "circuit.cir",
            "commands": "run.txt",
            "raw_data": "result.raw",
            "spice_compatibility": (
                "spice-compatibility.json" if compatibility is not None else None
            ),
        },
    }
    _atomic_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {"schema_version": 1, "experiment_id": experiment_id, "reports": {name: str(path) for name, path in outputs.items()}, "manifest": str(manifest_path)}
