"""Copy-based execution of an engineering request against a native .ms14 file."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from .engineering_planner import build_engineering_plan
from .engineering_run import build_engineering_run
from .native_action_plan import execute_native_action_plan
from .native_project_analysis import analyze_current_project, validate_native_analysis
from .plan_actions import build_action_plan


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_native_project(
    request: Mapping[str, Any], source: str, output: str, *,
    parameters: Mapping[str, Any] | None = None, execute: bool = False,
    client: Any = None, analyzer: Any = analyze_current_project,
) -> dict[str, Any]:
    """Preview without COM; execute only in a new output copy with explicit opt-in.

    Single-board native R/L/C experiments only. A completed run denotes execution,
    not electrical acceptance or layout correctness.
    """
    source_path = Path(source).expanduser().resolve()
    root = Path(output).expanduser().resolve()
    if root == Path(root.anchor) or root.exists():
        raise FileExistsError("output must be a new directory")
    plan = build_engineering_plan(request)
    action_plan = build_action_plan(
        plan, project_path=str(source_path), output_directory=str(root),
        parameters=parameters,
    )
    for action in action_plan["actions"]:
        if action["op"] == "run_analysis":
            validate_native_analysis(action)
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    preview = {"schema_version": 1, "success": True, "mode": "preview",
               "source": str(source_path), "source_sha256": digest, "output_dir": str(root),
               "plan": plan, "action_plan": action_plan, "simulation_started": False}
    if not execute:
        return preview
    root.mkdir(parents=True, exist_ok=False)
    # Only import/create the COM worker after all source/plan/path validation.
    worker = None
    if client is None:
        from .com_worker_client import MultisimWorkerProcess, WorkerMultisimClient
        worker = MultisimWorkerProcess()
        client = WorkerMultisimClient(worker)
    runtime: dict[str, Any] = {}
    execution: dict[str, Any] = {"success": False}
    sequence = 0
    snapshot = root / "circuit.ms14"
    try:
        shutil.copyfile(source_path, snapshot)
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != digest:
            raise RuntimeError("source changed while creating execution copy")
        action_plan["actions"][0]["path"] = str(snapshot)
        _write_json(root / "plan.json", plan)
        _write_json(root / "actions.json", action_plan)
        runtime = client.connect()

        def run_analysis(active_client: Any, action: Mapping[str, Any]) -> Mapping[str, Any]:
            nonlocal sequence
            sequence += 1
            # Save the modified copy and export its current topology for every run.
            active_client.save_circuit(str(snapshot))
            analysis_project = root / f"analysis-{sequence:03d}.ms14"
            shutil.copyfile(snapshot, analysis_project)
            result = dict(analyzer(active_client, action, root / f"analysis-{sequence:03d}"))
            result["circuit_sha256"] = hashlib.sha256(analysis_project.read_bytes()).hexdigest()
            result["circuit_artifact"] = analysis_project.name
            return result

        execution = execute_native_action_plan(client, action_plan, analysis_runner=run_analysis)
    except Exception as exc:
        execution = {"success": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
    finally:
        if worker is not None:
            worker.close()
    try:
        source_unchanged = hashlib.sha256(source_path.read_bytes()).hexdigest() == digest
    except OSError:
        source_unchanged = False
    if not source_unchanged:
        execution["success"] = False
        execution["source_error"] = "source file changed or became unreadable during execution"
    execution["source_unchanged"] = source_unchanged
    record = build_engineering_run(
        request=plan["request"], plan=plan, action_plan=action_plan,
        runtime=runtime, results=execution,
    )
    record["source"] = {"path": str(source_path), "sha256": digest}
    record["execution_scope"] = "native-project-copy; direct native COM analysis"
    # Additional fields are included in the final digest.
    record.pop("run_digest", None)
    record["run_digest"] = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    _write_json(root / "run.json", record)
    lines = [
        f"# {plan['request']['title']}", "",
        f"执行状态：{record['status']}", "",
        f"Multisim：{runtime.get('version', '未连接')}", "",
        "仿真来源：参数修改后的原生工程副本，由 Multisim COM 直接执行请求中的分析。", "",
        "验收状态：未验证。本报告记录实验执行和测量，不代表 ERC/DRC 或全部工程要求通过。", "",
        f"源文件保持不变：{source_unchanged}", "",
        "[工程副本](circuit.ms14) · [执行记录](run.json)", "",
        "AC 统计使用幅值，CSV 同时保留实部、虚部和相位。瞬态均值为原始求解器样本的算术均值，不是时间加权均值。", "",
        "| 分析 | 信号 | 统计量 | 样本数 | 样本均值 | 最小值 | 最大值 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for measurement in execution.get("measurements", []):
        signal = measurement["signal"].replace("|", "\\|")
        lines.append(f"| {measurement.get('analysis', 'unknown')} | {signal} | {measurement.get('quantity', 'value')} | {measurement['count']} | {measurement['mean']:.9g} | {measurement['min']:.9g} | {measurement['max']:.9g} |")
    if execution.get("error"):
        lines.extend(["", "执行错误：" + json.dumps(execution["error"], ensure_ascii=False)])
    if execution.get("restore_errors"):
        lines.extend(["", "恢复错误：" + json.dumps(execution["restore_errors"], ensure_ascii=False)])
    if execution.get("source_error"):
        lines.extend(["", "源文件检查错误：" + execution["source_error"]])
    for index in range(1, sequence + 1):
        folder = root / f"analysis-{index:03d}"
        for filename, label in (("data.csv", "CSV"), ("native-result.json", "COM 原始数据"), ("available-outputs.json", "可用输出通道")):
            if (folder / filename).is_file():
                lines.extend(["", f"[实验 {index} {label}](analysis-{index:03d}/{filename})"])
    if (root / "schematic.png").is_file():
        lines.extend(["", "![Multisim 原理图](schematic.png)"])
    report = "\n".join(lines) + "\n"
    (root / "report.md").write_text(report, encoding="utf-8")
    image = ""
    if (root / "schematic.png").is_file():
        image = '<img alt="Multisim schematic" style="max-width:100%" src="data:image/png;base64,' + base64.b64encode((root / "schematic.png").read_bytes()).decode("ascii") + '">'
    table_rows = []
    for item in execution.get("measurements", []):
        cells = [item.get("analysis", "unknown"), item["signal"], item.get("quantity", "value"),
                 item["count"], f"{item['mean']:.9g}", f"{item['min']:.9g}", f"{item['max']:.9g}"]
        table_rows.append("<tr>" + "".join("<td>" + html.escape(str(value)) + "</td>" for value in cells) + "</tr>")
    experiment_links = []
    for index in range(1, sequence + 1):
        name = f"analysis-{index:03d}"
        for filename, label in (("data.csv", "CSV"), ("native-result.json", "COM 原始数据"), ("available-outputs.json", "输出通道")):
            if (root / name / filename).is_file():
                experiment_links.append(f'<li><a href="{name}/{filename}">实验 {index} {label}</a></li>')
    error_html = html.escape(json.dumps({key: execution[key] for key in ("error", "restore_errors", "source_error") if execution.get(key)}, ensure_ascii=False))
    (root / "report.html").write_text(
        '<!doctype html><html lang="zh"><meta charset="utf-8"><title>Multisim 实验记录</title>'
        '<style>body{font-family:system-ui;max-width:1050px;margin:32px auto;padding:16px;line-height:1.6}'
        'table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:8px;text-align:left}'
        'th{background:#edf2f7}a{color:#1461a0}</style><body>'
        + '<h1>' + html.escape(plan['request']['title']) + '</h1>'
        + '<p>执行状态：' + html.escape(record['status']) + '；Multisim：' + html.escape(str(runtime.get('version', '未连接'))) + '</p>'
        + '<p>仿真来源：原生工程副本，直接调用 Multisim COM。源文件保持不变：' + str(source_unchanged) + '</p>'
        + '<p>验收状态：未验证。实验执行成功不代表电气设计、布局或全部需求通过验收。</p>'
        + '<p>AC 统计使用幅值；CSV 保留频率、实部、虚部和相位。瞬态 CSV 保留求解器时间点；下表为样本算术均值。</p>'
        + '<table><thead><tr><th>分析</th><th>信号</th><th>统计量</th><th>样本数</th><th>样本均值</th><th>最小值</th><th>最大值</th></tr></thead><tbody>'
        + ''.join(table_rows) + '</tbody></table>'
        + ('<p>执行错误：' + error_html + '</p>' if error_html != '{}' else '')
        + '<h2>实验文件</h2><p><a href="circuit.ms14">工程副本</a> · <a href="run.json">执行记录</a> · <a href="manifest.json">文件校验清单</a></p><ul>'
        + ''.join(experiment_links) + '</ul>' + image + '</body></html>', encoding='utf-8')
    artifacts = [
        {"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
        for path in sorted(root.rglob("*")) if path.is_file()
    ]
    _write_json(root / "manifest.json", {"schema_version": 1, "kind": "native-project-run-evidence", "artifacts": artifacts})
    analysis_count = sum(item["op"] == "run_analysis" for item in execution.get("results", []))
    expected_count = sum(item["op"] == "run_analysis" for item in action_plan["actions"])
    analyzed = analysis_count > 0
    return {"schema_version": 1, "success": execution["success"], "mode": "execute",
            "status": record["status"], "verification_status": "unverified",
            "analysis_attempted": sequence > 0, "analyses_completed": analysis_count,
            "analyses_requested": expected_count, "simulation_completed": analysis_count == expected_count,
            "simulation_started": True if analyzed else (None if sequence else False),
            "source_unchanged": source_unchanged,
            "output_dir": str(root), "record": str(root / "run.json"), "report": str(root / "report.html"),
            "measurements": execution.get("measurements", []), "error": execution.get("error"),
            "restore_errors": execution.get("restore_errors", [])}
