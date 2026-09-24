"""Generate, natively test and rank bounded RLC candidates."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Callable

from .engineering_task_contract import normalize_task_result, finalize_task_result

from .natural_rlc import parse_natural_rlc_request
from .native_project_run import run_native_project
from .native_netlist_validation import validate_native_project_netlist
from .native_rlc_acceptance import evaluate_rlc
from .schematic_builder import build_schematic
from .component_compat import detect_multisim_version, load_manifest_for_version, require_verified_mappings


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def run_natural_rlc_engineering(text: str, output: str, *, execute: bool = False,
                                cancel_requested: Callable[[], bool] | None = None) -> dict[str, Any]:
    proposal = parse_natural_rlc_request(text)
    root = Path(output).expanduser().resolve()
    if root.exists() or root == Path(root.anchor):
        raise FileExistsError("output must be a new directory")
    if not execute:
        return normalize_task_result({"success": True, "mode": "preview", "output_dir": str(root), "proposal": proposal,
                "simulation_started": False, "verification_status": "unverified"})
    root.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {"success": False, "mode": "execute", "output_dir": str(root),
                              "verification_status": "failed", "proposal": proposal, "candidates": []}
    _write(root / "proposal.json", proposal)
    (root / "input.txt").write_text(text, encoding="utf-8")
    (root / "source.cir").write_text(proposal["netlist"], encoding="utf-8")
    try:
        multisim_version = detect_multisim_version()
        manifest = load_manifest_for_version(Path(__file__).resolve().parent / "compatibility", multisim_version)
        result["multisim_version"] = multisim_version
        result["component_mappings"] = require_verified_mappings(manifest, multisim_version, {
            "resistor": ["1", "2"], "capacitor": ["1", "2"], "inductor": ["1", "2"],
            "voltage-source": ["-", "+"]})
        from .multisim_client import Ms14Codec
        build = build_schematic(proposal["netlist"], root / "source.xml", probe_nets=["in", "out"])
        _write(root / "build.json", build)
        if build["unsupported"] or build["layout_validation"]["status"] != "pass" or len(build["probes"]) != 2:
            raise RuntimeError("generated RLC schematic failed layout/probe preflight")
        Ms14Codec().encode(str(root / "source.xml"), str(root / "source.ms14"))
        d = proposal["derived"]
        request = proposal["request"]
        for index, resistance in enumerate(proposal["candidate_resistances_ohm"], 1):
            if cancel_requested and cancel_requested():
                raise RuntimeError("engineering task cancelled before candidate execution")
            name = f"candidate-{index:03d}"
            execution = run_native_project(request, str(root / "source.ms14"), str(root / name),
                                           parameters={"R1": resistance}, execute=True)
            if not execution["success"] or not execution["simulation_completed"]:
                raise RuntimeError(f"{name}: native execution failed: {execution.get('error')}")
            topology = validate_native_project_netlist(
                str(root / name), "analysis-002.ms14",
                {"R1": {1: "in", 2: "n1"}, "L1": {1: "n1", 2: "out"}})
            _write(root / name / "topology-acceptance.json", topology)
            if not topology["ok"]:
                raise RuntimeError(f"{name}: native netlist topology failed")
            measurement = evaluate_rlc(root / name, resistance, d["inductance_h"], d["capacitance_f"],
                                       target_hz=d["target_frequency_hz"])
            measurement["topology_acceptance"] = topology
            measurement.update(directory=name, project=f"{name}/analysis-002.ms14")
            result["candidates"].append(measurement)
            if cancel_requested and cancel_requested():
                raise RuntimeError("engineering task cancelled after candidate execution")
            if not measurement["checks"]["ac_complex_response"]:
                raise RuntimeError(f"{name}: native RLC reference check failed")
        selected = min(result["candidates"], key=lambda item: item["target_error_fraction"])
        result["selected"] = selected
        result["success"] = bool(selected["passed"] and selected["target_error_fraction"] <= .02)
        result["verification_status"] = "passed-supported-rlc-contract" if result["success"] else "target-not-met"
    except Exception as exc:
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    result["report"] = str(root / "report.html")
    result["acceptance"] = str(root / "acceptance.json")
    _write(root / "acceptance.json", result)
    _report(root, result)
    return finalize_task_result(root, result)


def _report(root: Path, result: dict[str, Any]) -> None:
    proposal = result["proposal"]
    rows = "".join(
        f'<tr><td>{item["resistance_ohm"]:g} Ω</td><td>{item["peak_frequency_hz"]:.4f} Hz</td>'
        f'<td>{100 * item["target_error_fraction"]:.3f}%</td><td>{item["max_ac_complex_error"]:.3g}</td><td>PASS</td>'
        f'<td><a href="{item["directory"]}/report.html">原生实验</a></td></tr>'
        for item in result["candidates"])
    selected = result.get("selected")
    conclusion = (f'选中 R1={selected["resistance_ohm"]:g} Ω，峰值频率 {selected["peak_frequency_hz"]:.4f} Hz。'
                  if selected else html.escape(str(result.get("error", "验收未完成"))))
    text = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>RLC 原生工程报告</title>'
            '<style>body{font:16px/1.7 system-ui;max-width:1080px;margin:40px auto;padding:20px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:10px;text-align:left}</style>'
            '<h1>自然语言 RLC 二阶低通工程</h1>'
            f'<p>验收：{html.escape(result["verification_status"])}；需求：{html.escape(proposal["text"])}</p>'
            '<p>每个候选均由 Multisim 原生 OP/AC 分析验证，依据实测峰值频率选择；解析模型只用于独立验错。</p>'
            f'<p>{conclusion}</p><table><tr><th>R1</th><th>实测峰值</th><th>目标误差</th><th>最大复数误差</th><th>原生拓扑</th><th>证据</th></tr>{rows}</table>'
            '<p><a href="proposal.json">需求与网表</a> · <a href="acceptance.json">验收记录</a> · <a href="manifest.json">完整性清单</a></p></html>')
    (root / "report.html").write_text(text, encoding="utf-8")


__all__ = ["run_natural_rlc_engineering"]
