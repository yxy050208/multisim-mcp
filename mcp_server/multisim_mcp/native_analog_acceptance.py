"""Repeatable native acceptance benchmark for generated divider/RC projects.

Run with python -m multisim_mcp.native_analog_acceptance --output NEW_DIRECTORY.
Requires a licensed local template pack, codec and Multisim COM installation.
Only the exported .ms14 projects are simulated; no command-engine fallback.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
from pathlib import Path
from typing import Any

from .native_project_run import run_native_project
from .schematic_builder import build_schematic

VIN = "V(OutProbe)"
VOUT = "V(OutProbe1)"
TARGET_HZ = 1000.0
CAPACITANCE = 100e-9


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def _csv(path: Path) -> list[dict[str, float]]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = [{key: float(value) for key, value in row.items()} for row in csv.DictReader(stream)]
    if not rows or any(not math.isfinite(v) for row in rows for v in row.values()):
        raise ValueError(f"Missing or nonfinite native measurements: {path}")
    return rows


def cutoff_frequency(frequencies: list[float], gains: list[float]) -> float:
    """Interpolate the -3.0103 dB crossing in log-frequency / dB space."""
    if len(frequencies) != len(gains) or len(gains) < 2:
        raise ValueError("cutoff requires aligned frequency and gain samples")
    if any(not math.isfinite(v) or v <= 0 for v in frequencies + gains):
        raise ValueError("frequency and gain must be positive finite numbers")
    if any(b <= a for a, b in zip(frequencies, frequencies[1:])):
        raise ValueError("frequency must increase")
    threshold = -10 * math.log10(2)
    levels = [20 * math.log10(g) for g in gains]
    for i in range(1, len(levels)):
        if levels[i - 1] >= threshold >= levels[i] and levels[i - 1] != levels[i]:
            weight = (threshold - levels[i - 1]) / (levels[i] - levels[i - 1])
            return math.exp(math.log(frequencies[i - 1]) + weight * math.log(frequencies[i] / frequencies[i - 1]))
    raise ValueError("native sweep does not bracket the half-power cutoff")


def rc_step_response(times: list[float], resistance: float, *, capacitance: float = CAPACITANCE,
                     amplitude: float = 1.0, delay: float = 100e-6, rise: float = 1e-6) -> list[float]:
    """Exact RC response to this benchmark's delayed 1 us linear 0->1 V ramp."""
    tau = resistance * capacitance
    result = []
    at_end = 1 - tau / rise * (-math.expm1(-rise / tau))
    for t in times:
        u = t - delay
        if u <= 0:
            result.append(0.0)
        elif u < rise:
            result.append((u - tau * (-math.expm1(-u / tau))) / rise)
        else:
            result.append(1 - (1 - at_end) * math.exp(-(u - rise) / tau))
    return [amplitude * value for value in result]


def evaluate_rc(directory: Path, resistance: float, *, capacitance: float = CAPACITANCE,
                amplitude: float = 1.0, target_hz: float = TARGET_HZ,
                delay: float = 100e-6, rise: float = 1e-6) -> dict[str, Any]:
    op = _csv(directory / "analysis-001/data.csv")
    ac = _csv(directory / "analysis-002/data.csv")
    tran = _csv(directory / "analysis-003/data.csv")
    dc_error = max(abs(op[0][f"{signal}.value"] - amplitude) for signal in (VIN, VOUT))
    frequencies, gains, complex_errors = [], [], []
    for row in ac:
        frequency = row["frequency_hz"]
        input_value = complex(row[f"{VIN}.real"], row[f"{VIN}.imaginary"])
        output_value = complex(row[f"{VOUT}.real"], row[f"{VOUT}.imaginary"])
        if abs(input_value - 1) > 1e-6:
            raise ValueError("native AC input does not match 1 V excitation")
        gain = output_value / input_value
        reference = 1 / complex(1, 2 * math.pi * frequency * resistance * capacitance)
        frequencies.append(frequency)
        gains.append(abs(gain))
        complex_errors.append(abs(gain - reference))
    times = [r["time_s"] for r in tran]
    expected = rc_step_response(times, resistance, capacitance=capacitance, amplitude=amplitude, delay=delay, rise=rise)
    transient_error = max(abs(row[f"{VOUT}.value"] - value) for row, value in zip(tran, expected))
    input_error = max(abs(row[f"{VIN}.value"] - amplitude * min(1, max(0, (row["time_s"] - delay) / rise))) for row in tran)
    cutoff = cutoff_frequency(frequencies, gains)
    checks = {"dc": dc_error < 1e-6, "ac_complex_response": max(complex_errors) < 1e-3,
              "transient_input": input_error < 1e-6, "transient_response": transient_error < 0.01 * amplitude}
    return {"resistance_ohm": resistance, "cutoff_hz": cutoff,
            "theoretical_cutoff_hz": 1 / (2 * math.pi * resistance * capacitance),
            "target_error_fraction": abs(cutoff / target_hz - 1),
            "max_dc_error_v": dc_error, "max_ac_complex_error": max(complex_errors),
            "max_transient_error_v": transient_error, "max_input_error_v": input_error,
            "ac_points": len(ac), "transient_points": len(tran),
            "checks": checks, "passed": all(checks.values())}


def _request(title: str, divider: bool = False) -> dict[str, Any]:
    experiments = [{"type": "op", "outputs": [VIN, VOUT, "I(OutProbe1)"] if divider else [VIN, VOUT]}]
    if not divider:
        experiments.extend([
            {"type": "ac", "commands": "ac dec 40 10 100k", "outputs": [VIN, VOUT]},
            {"type": "tran", "commands": "tran 2u 1m", "outputs": [VIN, VOUT]},
        ])
    return {"schema_version": 1, "title": title, "application": "生成工程的原生仿真验收",
            "boards": [{"id": "main", "role": "primary"}], "experiments": experiments,
            "objectives": [], "constraints": []}


def run_benchmark(output: str | Path) -> dict[str, Any]:
    root = Path(output).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    evidence: dict[str, Any] = {"schema_version": 1, "passed": False, "target_hz": TARGET_HZ,
                              "scope": "generated ideal divider and RC low-pass; native Multisim COM", "candidates": []}
    try:
        from .multisim_client import Ms14Codec
        for name, netlist in (
            ("divider", "* Divider\nV1 in 0 DC 10\nR1 in out 1k\nR2 out 0 1k\n.end\n"),
            ("rc", "* RC low-pass\nV1 in 0 DC 1 AC 1 PULSE(0 1 100u 1u 1u 2m 4m)\nR1 in out 1k\nC1 out 0 100n\n.end\n"),
        ):
            (root / f"{name}.cir").write_text(netlist, encoding="utf-8")
            build = build_schematic(netlist, root / f"{name}.xml", probe_nets=["in", "out"])
            _write(root / f"{name}-build.json", build)
            if build["unsupported"] or build["layout_validation"]["status"] != "pass" or len(build["probes"]) != 2:
                raise RuntimeError(f"{name}: generated layout/probe preflight failed")
            Ms14Codec().encode(str(root / f"{name}.xml"), str(root / f"{name}.ms14"))

        request = _request("分压电路原生验收", divider=True)
        _write(root / "divider-request.json", request)
        result = run_native_project(request, str(root / "divider.ms14"), str(root / "divider-run"), execute=True)
        if not result["success"] or not result["simulation_completed"]:
            raise RuntimeError(f"divider native execution failed: {result['error']}")
        row = _csv(root / "divider-run/analysis-001/data.csv")[0]
        checks = {"input_10v": abs(row[f"{VIN}.value"] - 10) < 1e-6,
                  "output_5v": abs(row[f"{VOUT}.value"] - 5) < 1e-6,
                  "current_5ma": abs(abs(row["I(OutProbe1).value"]) - .005) < 1e-8}
        evidence["divider"] = {"measurements": row, "checks": checks, "passed": all(checks.values())}
        if not all(checks.values()):
            raise RuntimeError("divider electrical acceptance failed")

        request = _request("RC 低通电路原生验收及参数优化")
        _write(root / "rc-request.json", request)
        # Select the best of these bounded E24 candidates from measured cutoff.
        # Every candidate receives OP, AC and transient verification.
        for resistance in (1000, 1500, 1600, 1800):
            name = f"rc-{resistance}"
            result = run_native_project(request, str(root / "rc.ms14"), str(root / name),
                                        parameters={"R1": resistance}, execute=True)
            if not result["success"] or not result["simulation_completed"]:
                raise RuntimeError(f"{name}: native execution failed: {result['error']}")
            metrics = evaluate_rc(root / name, resistance)
            metrics["directory"] = name
            metrics["project"] = f"{name}/analysis-003.ms14"
            evidence["candidates"].append(metrics)
            if not metrics["passed"]:
                raise RuntimeError(f"{name}: analytical reference checks failed")
        baseline, *candidates = evidence["candidates"]
        selected = min(candidates, key=lambda item: item["target_error_fraction"])
        evidence["selected"] = selected
        evidence["passed"] = selected["target_error_fraction"] < .02 and selected["target_error_fraction"] < baseline["target_error_fraction"]
        evidence["acceptance_limits"] = {"dc_error_v": 1e-6, "ac_complex_error": 1e-3,
                                         "transient_error_v": .01, "target_error_fraction": .02}
    except Exception as exc:
        evidence["error"] = {"type": type(exc).__name__, "message": str(exc)}
    _write(root / "acceptance.json", evidence)
    _report(root, evidence)
    _write(root / "manifest.json", {"artifacts": [
        {"path": p.relative_to(root).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(root.rglob("*")) if p.is_file() and p != root / "manifest.json"
    ]})
    return evidence


def _report(root: Path, evidence: dict[str, Any]) -> None:
    rows = "".join(
        f'<tr><td>{v["resistance_ohm"]:g} Ω</td><td>{v["cutoff_hz"]:.3f} Hz</td>'
        f'<td>{100 * v["target_error_fraction"]:.3f}%</td><td>{v["max_ac_complex_error"]:.3g}</td>'
        f'<td>{v["max_transient_error_v"]:.6f} V</td><td><a href="{v["directory"]}/report.html">原始实验</a></td></tr>'
        for v in evidence["candidates"])
    selected = evidence.get("selected")
    conclusion = (f'选中 R1 = {selected["resistance_ohm"]:g} Ω，C1 = 100 nF；截止频率 {selected["cutoff_hz"]:.3f} Hz。'
                  if selected else html.escape(str(evidence.get("error", "验收未完成"))))
    image = (f'<p><a href="{selected["project"]}">下载优化后原生工程</a></p>'
             f'<img alt="Multisim 原生 RC 电路图" src="{selected["directory"]}/schematic.png" style="max-width:100%">' if selected else "")
    (root / "report.html").write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>自动生成电路原生验收</title>'
        '<style>body{font:16px/1.7 system-ui;max-width:1080px;margin:40px auto;padding:20px}table{border-collapse:collapse;width:100%}'
        'td,th{border:1px solid #ddd;padding:10px;text-align:left}</style><h1>自动生成电路原生验收</h1>'
        f'<p>验收结果：{"通过" if evidence["passed"] else "失败"}。{conclusion}</p>'
        '<p>目标：1 kHz 无负载理想 RC 低通。先生成可编辑 .ms14，再由 Multisim COM 执行原生分析；全部候选均经直流、交流和瞬态测试。'
        '两个 1 kΩ 电阻的 10 V 分压电路另行核对 5 V 输出和 5 mA 电流。</p>'
        '<table><tr><th>R1</th><th>实测截止频率</th><th>目标误差</th><th>最大复数增益误差</th><th>最大瞬态误差</th><th>证据</th></tr>'
        + rows + '</table><p>交流：10 Hz–100 kHz，每十倍频程 40 点；截止频率按实测增益的半功率点插值。'
        '瞬态：100 μs 延迟、1 μs 上升沿的 0→1 V 输入，仿真至 1 ms；按原生求解器时间点与有限上升沿的解析解比较。'
        '直流/交流源为 1 V，瞬态源为上述脉冲；图中电源数值显示 DC 属性。</p>'
        '<p>本验收覆盖这两个理想电路在本机版本上的结果，不覆盖元件容差、实际负载、PCB 制造规则、其它电路或其它 Multisim 版本。'
        '选择依据是测得的截止频率；解析解用于独立验错。</p>' + image
        + '<p><a href="divider-run/report.html">分压实验报告</a> · <a href="acceptance.json">验收数据</a> · '
        '<a href="manifest.json">SHA-256 文件清单</a></p></html>', encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new evidence directory")
    args = parser.parse_args()
    evidence = run_benchmark(args.output)
    print(json.dumps(evidence, ensure_ascii=True, allow_nan=False, indent=2))
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
