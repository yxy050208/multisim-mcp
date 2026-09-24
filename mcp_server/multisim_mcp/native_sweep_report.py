"""Baseline comparison and auditable reports for native Multisim sweeps."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import csv
import io
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .native_sweep import _numeric_series, rank_native_sweep_results


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _validated(value: Mapping[str, Any], field: str, state: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("state") != state:
        raise ValueError(f"value must have state {state}")
    digest = value.get(field)
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError(f"value must include a valid {field}")
    unsigned = dict(value)
    unsigned.pop(field, None)
    if digest != _digest(unsigned):
        raise ValueError(f"{field} does not match value")
    return dict(value)


def _numeric_parameters(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("parameters must be a non-empty object")
    result: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("parameter names must be non-empty strings")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("parameter values must be finite numbers")
        numeric = float(raw)
        if not math.isfinite(numeric):
            raise ValueError("parameter values must be finite numbers")
        result[key.strip()] = numeric
    return result


def compare_native_sweep_baseline(ranking: Mapping[str, Any]) -> dict[str, Any]:
    """Compare the ranked best record with the original-value baseline record."""
    verified = _validated(ranking, "ranking_digest", "completed")
    if verified.get("quality_state") != "valid":
        raise ValueError("ranking quality must be valid before baseline comparison")
    ranked = verified.get("ranked_results")
    originals = _numeric_parameters(verified.get("original_values"))
    best = verified.get("best")
    if not isinstance(ranked, list) or not ranked or not isinstance(best, Mapping):
        raise ValueError("ranking must contain ranked_results and best")
    folded_originals = {key.casefold(): value for key, value in originals.items()}
    baseline: Mapping[str, Any] | None = None
    for item in ranked:
        if not isinstance(item, Mapping):
            continue
        parameters = _numeric_parameters(item.get("parameters"))
        if {key.casefold() for key in parameters} == set(folded_originals) and all(
            key.casefold() in folded_originals
            and math.isclose(value, folded_originals[key.casefold()], rel_tol=1e-9, abs_tol=1e-18)
            for key, value in parameters.items()
        ):
            baseline = item
            break
    if baseline is None:
        raise ValueError("the candidate grid does not contain the original-value baseline")
    objective = verified.get("objective")
    if not isinstance(objective, Mapping):
        raise ValueError("ranking objective is invalid")
    baseline_value = float(baseline["value"])
    best_value = float(best["value"])
    direction = objective.get("direction")
    if direction == "minimize":
        improvement = baseline_value - best_value
        denominator = abs(baseline_value)
    elif direction == "maximize":
        improvement = best_value - baseline_value
        denominator = abs(baseline_value)
    elif direction == "target":
        target = float(objective["target"])
        baseline_distance = abs(baseline_value - target)
        best_distance = abs(best_value - target)
        improvement = baseline_distance - best_distance
        denominator = baseline_distance
    else:
        raise ValueError("ranking objective direction is invalid")
    tolerance = max(1e-12, abs(float(baseline.get("score", 0.0))) * 1e-9)
    state = "improved" if improvement > tolerance else "regressed" if improvement < -tolerance else "unchanged"
    baseline_parameters = _numeric_parameters(baseline.get("parameters"))
    best_parameters = _numeric_parameters(best.get("parameters"))
    changes = [
        {
            "refdes": refdes,
            "before": folded_originals.get(refdes.casefold()),
            "after": value,
        }
        for refdes, value in best_parameters.items()
        if refdes.casefold() in folded_originals
        and not math.isclose(value, folded_originals[refdes.casefold()], rel_tol=1e-9, abs_tol=1e-18)
    ]
    payload: dict[str, Any] = {
        "state": state,
        "objective": dict(objective),
        "baseline": dict(baseline),
        "best": dict(best),
        "baseline_parameters": baseline_parameters,
        "best_parameters": best_parameters,
        "parameter_changes": changes,
        "metric_improvement": improvement,
        "relative_improvement_percent": (improvement / denominator * 100.0) if denominator > 0 else None,
        "ranking_digest": verified["ranking_digest"],
        "source_mutated": False,
    }
    if isinstance(verified.get("circuit"), Mapping):
        payload["circuit"] = dict(verified["circuit"])
    payload["comparison_digest"] = _digest(payload)
    return payload


def _report_markdown(
    comparison: Mapping[str, Any],
    optimized_copy_name: str | None = None,
    waveform_names: tuple[str, str] | None = None,
) -> str:
    objective = comparison["objective"]
    baseline = comparison["baseline"]
    best = comparison["best"]
    changes = comparison["parameter_changes"]
    relative = comparison.get("relative_improvement_percent")
    relative_text = "不可计算" if relative is None else f"{float(relative):.4g}%"
    rows = "\n".join(
        f"| {item['refdes']} | {item['before']:.12g} | {item['after']:.12g} |"
        for item in changes
    ) or "| 无变化 | - | - |"
    return (
        "# Multisim 原生参数优化报告\n\n"
        f"- 结论：**{comparison['state']}**\n"
        f"- 信号：`{objective['signal']}`\n"
        f"- 指标：`{objective['metric']}`\n"
        f"- 方向：`{objective['direction']}`\n"
        f"- 基线值：`{float(baseline['value']):.12g}`\n"
        f"- 最佳值：`{float(best['value']):.12g}`\n"
        f"- 指标改善量：`{float(comparison['metric_improvement']):.12g}`\n"
        f"- 相对改善：`{relative_text}`\n\n"
        "## 参数变化\n\n"
        "| 元件 | 原值 | 建议值 |\n|---|---:|---:|\n"
        f"{rows}\n\n"
        "## 证据与边界\n\n"
        f"- Ranking digest: `{comparison['ranking_digest']}`\n"
        f"- Comparison digest: `{comparison['comparison_digest']}`\n"
        "- 本报告只比较已验证的扫描结果；源工程未被修改。\n\n"
        "## English summary\n\n"
        f"The best candidate is **{comparison['state']}** relative to the original-value baseline. "
        f"The measured `{objective['metric']}` changed from `{float(baseline['value']):.12g}` "
        f"to `{float(best['value']):.12g}`. Source circuit mutation: `false`.\n"
        + (
            f"\nOptimized Multisim copy: [`{optimized_copy_name}`]({optimized_copy_name}).\n"
            if optimized_copy_name
            else ""
        )
        + (
            f"\nWaveform evidence: [`{waveform_names[0]}`]({waveform_names[0]}) · "
            f"[`{waveform_names[1]}`]({waveform_names[1]}).\n"
            if waveform_names
            else ""
        )
    )


def _same_parameters(left: object, right: Mapping[str, float]) -> bool:
    if not isinstance(left, Mapping):
        return False
    try:
        values = _numeric_parameters(left)
    except ValueError:
        return False
    folded = {key.casefold(): value for key, value in values.items()}
    return set(folded) == set(right) and all(
        math.isclose(value, right[key], rel_tol=1e-9, abs_tol=1e-18)
        for key, value in folded.items()
    )


def _waveform_series(
    sweep_result: Mapping[str, Any], comparison: Mapping[str, Any]
) -> tuple[list[float], list[float]]:
    if sweep_result.get("state") != "completed":
        raise ValueError("sweep_result must be completed for waveform evidence")
    ranking = rank_native_sweep_results(sweep_result, comparison["objective"])
    if ranking.get("ranking_digest") != comparison.get("ranking_digest"):
        raise ValueError("sweep_result does not match comparison ranking_digest")
    records = sweep_result.get("results")
    if not isinstance(records, list):
        raise ValueError("sweep_result.results must be an array")
    originals = _numeric_parameters(sweep_result.get("original_values"))
    folded_originals = {key.casefold(): value for key, value in originals.items()}
    baseline_record: Mapping[str, Any] | None = None
    best_record: Mapping[str, Any] | None = None
    best_parameters = _numeric_parameters(comparison["best"].get("parameters"))
    for record in records:
        if not isinstance(record, Mapping):
            continue
        parameters = record.get("parameters")
        if baseline_record is None and _same_parameters(parameters, folded_originals):
            baseline_record = record
        if best_record is None and _same_parameters(parameters, {
            key.casefold(): value for key, value in best_parameters.items()
        }):
            best_record = record
    if baseline_record is None or best_record is None:
        raise ValueError("sweep_result is missing baseline or best waveform")
    signal = str(comparison["objective"]["signal"])
    baseline = _numeric_series(baseline_record.get("analysis"), signal)
    best = _numeric_series(best_record.get("analysis"), signal)
    if len(baseline) < 2 or len(best) < 2:
        raise ValueError("baseline and best waveforms need at least two samples")
    return baseline, best


def _downsample(values: list[float], maximum: int = 1000) -> list[float]:
    if len(values) <= maximum:
        return values
    step = (len(values) - 1) / (maximum - 1)
    return [values[round(index * step)] for index in range(maximum)]


def _write_waveform_artifacts(
    root: Path, baseline: list[float], best: list[float]
) -> tuple[Path, Path]:
    baseline = _downsample(baseline)
    best = _downsample(best)
    csv_path = root / "waveform-comparison.csv"
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["sample_index", "baseline", "best"])
    for index in range(max(len(baseline), len(best))):
        writer.writerow([
            index,
            f"{baseline[index]:.17g}" if index < len(baseline) else "",
            f"{best[index]:.17g}" if index < len(best) else "",
        ])
    csv_path.write_text(stream.getvalue(), encoding="utf-8", newline="")

    svg_path = root / "waveform-comparison.svg"
    width, height = 960, 420
    left, top, right, bottom = 64, 24, 24, 48
    plot_width, plot_height = width - left - right, height - top - bottom
    values = baseline + best
    low, high = min(values), max(values)
    span = high - low or 1.0

    def points(series: list[float]) -> str:
        if len(series) == 1:
            x = left + plot_width / 2
            y = top + (high - series[0]) / span * plot_height
            return f"{x:.2f},{y:.2f}"
        return " ".join(
            f"{left + index / (len(series) - 1) * plot_width:.2f},"
            f"{top + (high - value) / span * plot_height:.2f}"
            for index, value in enumerate(series)
        )

    svg_path.write_text(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        f"<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 {width} {height}\" "
        f"role=\"img\" aria-label=\"Baseline and optimized waveform comparison\">"
        f"<rect width=\"100%\" height=\"100%\" fill=\"white\"/>"
        f"<line x1=\"{left}\" y1=\"{top + plot_height}\" x2=\"{left + plot_width}\" y2=\"{top + plot_height}\" stroke=\"#64748b\"/>"
        f"<line x1=\"{left}\" y1=\"{top}\" x2=\"{left}\" y2=\"{top + plot_height}\" stroke=\"#64748b\"/>"
        f"<polyline fill=\"none\" stroke=\"#64748b\" stroke-width=\"2\" points=\"{points(baseline)}\"/>"
        f"<polyline fill=\"none\" stroke=\"#2563eb\" stroke-width=\"2\" points=\"{points(best)}\"/>"
        f"<text x=\"{left}\" y=\"18\" font-family=\"sans-serif\" font-size=\"16\">Baseline vs optimized waveform</text>"
        f"<text x=\"{left}\" y=\"{height - 12}\" font-family=\"sans-serif\" font-size=\"12\">Sample index</text>"
        f"<text x=\"10\" y=\"{top + plot_height / 2}\" transform=\"rotate(-90 10 {top + plot_height / 2})\" font-family=\"sans-serif\" font-size=\"12\">Amplitude</text>"
        "<rect x=760" + f" y=\"28\" width=\"14\" height=\"4\" fill=\"#64748b\"/><text x=\"782\" y=\"34\" font-family=\"sans-serif\" font-size=\"12\">Baseline</text>"
        "<rect x=760" + f" y=\"48\" width=\"14\" height=\"4\" fill=\"#2563eb\"/><text x=\"782\" y=\"54\" font-family=\"sans-serif\" font-size=\"12\">Optimized</text></svg>\n",
        encoding="utf-8",
    )
    return csv_path, svg_path


def export_native_sweep_report(
    comparison: Mapping[str, Any],
    output_dir: str,
    optimized_copy_path: str | None = None,
    sweep_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a report package and optionally include an optimized .ms14 copy."""
    if not isinstance(comparison, Mapping) or comparison.get("state") not in {
        "improved",
        "unchanged",
        "regressed",
    }:
        raise ValueError("comparison state is invalid")
    verified = _validated(comparison, "comparison_digest", str(comparison["state"]))
    if not isinstance(output_dir, str) or not output_dir.strip() or "\x00" in output_dir:
        raise ValueError("output_dir must be a non-empty path")
    unresolved = Path(output_dir).expanduser()
    if unresolved.is_symlink():
        raise ValueError("output_dir must not be a symbolic link")
    root = unresolved.resolve()
    if root == Path(root.anchor):
        raise ValueError("output_dir must not be a filesystem root")
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise FileExistsError("report output directory must be empty")
    optimized_source: Path | None = None
    if optimized_copy_path is not None:
        if (
            not isinstance(optimized_copy_path, str)
            or not optimized_copy_path.strip()
            or "\x00" in optimized_copy_path
        ):
            raise ValueError("optimized_copy_path must be a non-empty path")
        optimized_source = Path(optimized_copy_path).expanduser().resolve()
        if optimized_source.suffix.casefold() != ".ms14":
            raise ValueError("optimized_copy_path must end with .ms14")
        if optimized_source.is_symlink() or not optimized_source.is_file():
            raise ValueError("optimized_copy_path must be an existing regular file")
    comparison_path = root / "native-optimization-comparison.json"
    report_path = root / "native-optimization-report.md"
    comparison_path.write_text(
        json.dumps(verified, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )
    optimized_copy: Path | None = None
    if optimized_source is not None:
        optimized_copy = root / "optimized-circuit.ms14"
        shutil.copy2(optimized_source, optimized_copy)
    waveform_paths: tuple[Path, Path] | None = None
    if sweep_result is not None:
        waveform_paths = _write_waveform_artifacts(
            root, *_waveform_series(sweep_result, verified)
        )
    report_path.write_text(
        _report_markdown(
            verified,
            optimized_copy.name if optimized_copy else None,
            tuple(path.name for path in waveform_paths) if waveform_paths else None,
        ),
        encoding="utf-8",
    )
    files = []
    artifact_paths = [comparison_path, report_path]
    if optimized_copy is not None:
        artifact_paths.append(optimized_copy)
    if waveform_paths is not None:
        artifact_paths.extend(waveform_paths)
    for path in artifact_paths:
        data = path.read_bytes()
        files.append(
            {
                "name": path.name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "multisim-mcp-native-sweep-report",
        "comparison_digest": verified["comparison_digest"],
        "files": files,
    }
    if optimized_copy is not None:
        manifest["optimized_copy"] = {
            "name": optimized_copy.name,
            "source_path": str(optimized_source),
            "sha256": next(item["sha256"] for item in files if item["name"] == optimized_copy.name),
        }
    if waveform_paths is not None:
        manifest["waveform_evidence"] = {
            "csv": waveform_paths[0].name,
            "svg": waveform_paths[1].name,
            "sample_limit": 1000,
        }
    manifest["manifest_digest"] = _digest(manifest)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "state": "completed",
        "output_dir": str(root),
        "comparison_path": str(comparison_path),
        "report_path": str(report_path),
        "manifest_path": str(manifest_path),
        "manifest_digest": manifest["manifest_digest"],
        "optimized_copy_path": str(optimized_copy) if optimized_copy else None,
        "waveform_csv_path": str(waveform_paths[0]) if waveform_paths else None,
        "waveform_svg_path": str(waveform_paths[1]) if waveform_paths else None,
        "source_mutated": False,
    }


__all__ = ["compare_native_sweep_baseline", "export_native_sweep_report"]
