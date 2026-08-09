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


def _html_report(root: Path, language: str, summaries: list[dict[str, Any]], verification: dict[str, Any] | None) -> str:
    zh = language == "zh-CN"
    title = "Multisim 电路实验报告" if zh else "Multisim Circuit Experiment Report"
    labels = {
        "overview": "实验概览" if zh else "Experiment overview",
        "schematic": "电路图" if zh else "Schematic",
        "plot": "实验曲线" if zh else "Experiment plot",
        "measurements": "数据摘要" if zh else "Data summary",
        "verification": "指标验证" if zh else "Requirement verification",
        "source": "可复现输入" if zh else "Reproducible inputs",
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
    generated = datetime.now(timezone.utc).isoformat()
    schematic_uri = "data:image/png;base64," + base64.b64encode(
        (root / "schematic.png").read_bytes()
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
<p class="meta">Multisim MCP {html.escape(__version__)} · {html.escape(generated)}</p>
<h2>{labels['overview']}</h2><pre>{html.escape(original[:12000])}</pre>
<h2>{labels['schematic']}</h2><img src="{schematic_uri}" alt="schematic">
<h2>{labels['plot']}</h2><img src="{plot_uri}" alt="plot">
<h2>{labels['measurements']}</h2><table><thead><tr><th>Signal</th><th>N</th><th>First</th><th>Last</th><th>Min</th><th>Max</th><th>Mean</th></tr></thead><tbody>{rows}</tbody></table>
<h2>{labels['verification']}</h2><table><thead><tr><th>ID</th><th>Status</th><th>Value</th><th>Target</th><th>Unit</th><th>Reason</th></tr></thead><tbody>{verdict_rows}</tbody></table>
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
    title = "Multisim 电路实验报告" if zh else "Multisim Circuit Experiment Report"
    lines = [title, "", ("由 Multisim MCP 自动生成" if zh else "Generated automatically by Multisim MCP"), ""]
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
    required = ("report.md", "schematic.png", "plot.svg", "result.raw", "circuit.cir", "run.txt")
    for name in required:
        _contained_file(root, name)
    _contained_file(root, "verification.json", required=False)
    summaries = summarize_columns(parse_raw(str(root / "result.raw")))
    verification = _load_json(root / "verification.json")
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
    _write_pdf(outputs["pdf_zh"], "Multisim 电路实验报告", _pdf_lines(root, "zh-CN", summaries, verification), True)
    _write_pdf(outputs["pdf_en"], "Multisim Circuit Experiment Report", _pdf_lines(root, "en", summaries, verification), False)
    artifact_rows = []
    for path in sorted(
        item
        for item in root.iterdir()
        if item.is_file() and not item.is_symlink() and item.name != "manifest.json"
    ):
        artifact_rows.append({"filename": path.name, "size": path.stat().st_size, "sha256": _sha256(path)})
    manifest = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "generator": {"name": "multisim-mcp", "version": __version__},
        "runtime": {"python": platform.python_version(), "platform": platform.platform()},
        "artifacts": artifact_rows,
        "reproduce": {"netlist": "circuit.cir", "commands": "run.txt", "raw_data": "result.raw"},
    }
    _atomic_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {"schema_version": 1, "experiment_id": experiment_id, "reports": {name: str(path) for name, path in outputs.items()}, "manifest": str(manifest_path)}
