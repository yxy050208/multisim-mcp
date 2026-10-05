"""Version-gated native acceptance for an explicit multi-board fixture plan.

This is intentionally a narrow first native runner: it executes DC operating
point on each saved/reopened board and compares voltage observations.  It is a
reusable acceptance harness, rather than a claim that every multi-board
experiment is already supported.  Transient/AC contracts can build on the
same save/reopen and evidence manifest once their output semantics are fixed.
"""

from __future__ import annotations

import hashlib
import json
import math
import csv
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from .eda_core import CircuitDesign
from .multiboard_fixtures import (
    compare_multiboard_interface_observations,
    materialize_multiboard_fixture_artifacts,
)
from .multiboard_plan import materialize_circuit_design_partition
from .multisim_compat import parse_multisim_version
from .spice_adapter import circuit_design_to_spice
from .topology_validation import compare_roundtrip_topology


_ANALYSIS_KINDS = frozenset({"dc", "tran", "ac"})


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_native_op_csv(path: Path, values: Mapping[str, Any]) -> None:
    """Persist scalar native operating-point values in a portable CSV."""
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("output", "value"))
        for output in sorted(values):
            value = values[output]
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                writer.writerow((str(output), format(float(value), ".17g")))


def _normalize_analysis_options(
    analysis: str, options: Mapping[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Validate the deliberately small native analysis contract."""
    kind = str(analysis or "").strip().casefold()
    if kind not in _ANALYSIS_KINDS:
        raise ValueError("analysis must be one of: dc, tran, ac")
    if not isinstance(options, Mapping) and options is not None:
        raise ValueError("analysis_options must be an object")
    raw = {} if options is None else dict(options)
    allowed = {
        "dc": {"timeout", "max_points"},
        "tran": {"sample_rate", "num_samples", "duration", "timeout", "max_points"},
        "ac": {"sweep_type", "num_points", "start_frequency", "stop_frequency", "timeout", "max_points"},
    }[kind]
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"analysis_options contains unsupported fields: {sorted(unknown)}")
    defaults: dict[str, Any] = {
        "timeout": 60.0,
        "max_points": 2000,
    }
    if kind == "tran":
        defaults.update({"sample_rate": 1_000_000.0, "num_samples": 1000, "duration": 0.001})
    elif kind == "ac":
        defaults.update({"sweep_type": 0, "num_points": 10, "start_frequency": 100.0, "stop_frequency": 1_000_000.0})
    values = {**defaults, **raw}
    for name in ("timeout", "max_points"):
        value = values[name]
        if name == "max_points":
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100000:
                raise ValueError("analysis_options.max_points must be an integer from 1 to 100000")
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError(f"analysis_options.{name} must be finite and positive")
    if kind == "tran":
        if isinstance(values["num_samples"], bool) or not isinstance(values["num_samples"], int) or not 1 <= values["num_samples"] <= 100000:
            raise ValueError("analysis_options.num_samples must be an integer from 1 to 100000")
        for name in ("sample_rate", "duration"):
            value = values[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"analysis_options.{name} must be finite and positive")
    if kind == "ac":
        if isinstance(values["sweep_type"], bool) or not isinstance(values["sweep_type"], int) or not 0 <= values["sweep_type"] <= 2:
            raise ValueError("analysis_options.sweep_type must be an integer from 0 to 2")
        if isinstance(values["num_points"], bool) or not isinstance(values["num_points"], int) or not 1 <= values["num_points"] <= 100000:
            raise ValueError("analysis_options.num_points must be an integer from 1 to 100000")
        for name in ("start_frequency", "stop_frequency"):
            value = values[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"analysis_options.{name} must be finite and positive")
        if float(values["stop_frequency"]) <= float(values["start_frequency"]):
            raise ValueError("analysis_options.stop_frequency must exceed start_frequency")
    return kind, values


def _run_native_analysis(
    client: Any, analysis: str, outputs: Sequence[str], options: Mapping[str, Any],
) -> dict[str, Any]:
    if analysis == "dc":
        return dict(client.run_dc_operating_point(
            list(outputs), timeout=options["timeout"], max_points=options["max_points"]
        ))
    if analysis == "tran":
        return dict(client.run_transient_outputs(
            list(outputs), options["sample_rate"], options["num_samples"],
            options["duration"], timeout=options["timeout"], max_points=options["max_points"]
        ))
    return dict(client.run_ac_sweep(
        list(outputs), sweep_type=options["sweep_type"], num_points=options["num_points"],
        start_frequency=options["start_frequency"], stop_frequency=options["stop_frequency"],
        timeout=options["timeout"], max_points=options["max_points"],
    ))


def _analysis_values(
    result: Mapping[str, Any], outputs: Sequence[str], analysis: str,
) -> dict[str, float | None]:
    """Reduce native response rows to explicit scalar comparison observations."""
    nested = result.get("results")
    channels: Mapping[str, Any]
    if isinstance(nested, Mapping):
        channels = nested
    elif len(outputs) == 1:
        channels = {outputs[0]: result}
    else:
        channels = {}
    values: dict[str, float | None] = {}
    for output in outputs:
        payload = channels.get(output, {})
        rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
        value: float | None = None
        if isinstance(rows, list) and len(rows) >= 2 and all(isinstance(row, list) for row in rows):
            if analysis == "dc":
                candidate = rows[1][0] if rows[1] else None
            elif analysis == "tran":
                candidate = rows[1][-1] if rows[1] else None
            else:
                candidate = None
                if len(rows) >= 3 and rows[1] and rows[2]:
                    candidate = math.hypot(float(rows[1][-1]), float(rows[2][-1]))
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool) and math.isfinite(float(candidate)):
                value = float(candidate)
        values[output] = value
    return values


def _analysis_informative(analysis: str, values: Mapping[str, float | None]) -> bool:
    """Reject an all-zero AC response that proves no excitation was applied."""
    if analysis != "ac":
        return True
    return any(
        isinstance(value, (int, float)) and math.isfinite(float(value)) and abs(float(value)) > 1e-15
        for value in values.values()
    )


def _write_acceptance_report(path: Path, result: Mapping[str, Any]) -> None:
    """Write a concise human-readable report next to the machine evidence."""
    lines = [
        "# Multisim 多板原生验收报告",
        "",
        f"- 状态：`{result.get('status', 'unknown')}`",
        f"- 验证状态：`{result.get('verification_status', 'unknown')}`",
        f"- 目标版本：`{result.get('target_multisim_version', '')}`",
        f"- 检测版本：`{result.get('multisim_version', '未执行')}`",
        f"- 分析类型：`{result.get('analysis', 'dc')}`",
        "",
        "## 验收门禁",
        "",
        "| 门禁 | 结果 |",
        "| --- | --- |",
    ]
    acceptance = result.get("native_acceptance", {})
    if isinstance(acceptance, Mapping):
        for name, passed in acceptance.items():
            lines.append(f"| `{name}` | {'PASS' if passed is True else 'FAIL'} |")
    lines.extend(["", "## 板级读数", "", "| 板 | 观测网络 | 电压（V） |", "| --- | --- | ---: |"])
    observations = result.get("observations", {})
    if isinstance(observations, Mapping):
        for board_id in sorted(observations):
            values = observations[board_id]
            if isinstance(values, Mapping):
                for net in sorted(values):
                    lines.append(f"| `{board_id}` | `{net}` | {float(values[net]):.12g} |")
    comparison = result.get("interface_comparison", {})
    if isinstance(comparison, Mapping):
        lines.extend(["", "## 跨板接口比较", ""])
        lines.append(f"接口比较状态：`{comparison.get('status', 'unknown')}`。")
    reference = result.get("full_reference")
    if isinstance(reference, Mapping):
        lines.extend(["", "## 完整电路参考比较", ""])
        lines.append(f"完整参考状态：`{reference.get('status', 'unknown')}`。")
    lines.extend([
        "",
        "本报告只覆盖当前首版原生分析验收；它不替代 PCB、器件容差、"
        "热设计或安全认证。",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _probe_map(build_result: Mapping[str, Any]) -> dict[str, str]:
    build = build_result.get("build", {})
    probes = build.get("probes", []) if isinstance(build, Mapping) else []
    if not isinstance(probes, Sequence) or isinstance(probes, (str, bytes)):
        return {}
    return {
        str(item["net"]): str(item["voltage_output"])
        for item in probes
        if isinstance(item, Mapping) and item.get("net") and item.get("voltage_output")
    }


def _op_values(result: Mapping[str, Any], outputs: Sequence[str]) -> dict[str, float | None]:
    raw: Mapping[str, Any]
    nested = result.get("results")
    if isinstance(nested, Mapping):
        raw = nested
    elif len(outputs) == 1 and result.get("output") == outputs[0]:
        raw = {outputs[0]: result}
    else:
        raw = {}
    values: dict[str, float | None] = {}
    for output in outputs:
        payload = raw.get(output, {})
        rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
        value = rows[1][0] if (
            isinstance(rows, list)
            and len(rows) > 1
            and isinstance(rows[1], list)
            and rows[1]
        ) else None
        values[output] = float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None
    return values


def _observation_values(
    board_records: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for board in board_records:
        board_id = str(board.get("board_id", "")).strip()
        probe_map = board.get("probe_map", {})
        native_values = board.get("native_analysis_values", board.get("native_op_values", {}))
        if not isinstance(probe_map, Mapping) or not isinstance(native_values, Mapping):
            continue
        result[board_id] = {
            str(net): float(native_values[output])
            for net, output in probe_map.items()
            if output in native_values
            and isinstance(native_values[output], (int, float))
            and math.isfinite(float(native_values[output]))
        }
    return result


def _reopened_topology(
    board: Mapping[str, Any], report_text: str,
) -> dict[str, Any]:
    """Check stable component/net names in the final saved-and-reopened report."""
    design = board.get("design", {})
    raw_components = design.get("components", []) if isinstance(design, Mapping) else []
    components = [
        item for item in raw_components
        if isinstance(item, Mapping) and str(item.get("kind", "")).upper() != "GND"
    ]
    net_counts: dict[str, int] = {}
    for component in components:
        raw_nodes = component.get("nodes", [])
        if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
            continue
        for node in raw_nodes:
            node_name = str(node)
            if node_name != "0":
                net_counts[node_name] = net_counts.get(node_name, 0) + 1
    required_nets = [name for name, count in net_counts.items() if count >= 2]
    result = compare_roundtrip_topology(
        [str(item.get("refdes", "")) for item in components],
        required_nets,
        report_text,
    )
    result["evidence"] = (
        "final saved-and-reopened Multisim ReportNetlist text presence; "
        "singleton source nets remain informational"
    )
    return result


def _validate_output_directory(output_directory: str | Path) -> Path:
    root = Path(output_directory).expanduser().resolve()
    if root == Path(root.anchor) or root.exists():
        raise ValueError("output_directory must be a new non-root directory")
    return root


def run_native_multiboard_acceptance(
    design: CircuitDesign,
    partition: Mapping[str, Any],
    fixtures: Sequence[Mapping[str, Any]],
    output_directory: str | Path,
    *,
    execute: bool = False,
    target_multisim_version: str = "14.3",
    analysis: str = "dc",
    analysis_options: Mapping[str, Any] | None = None,
    schematic_executor: Callable[..., Mapping[str, Any]] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Preview or execute the first version-gated multi-board native gate.

    ``execute=False`` does not create files or activate COM.  For execution,
    the default adapter is the existing Multisim server's private native
    executor/client; tests and future backends may inject both explicitly.
    The injected schematic executor must open/verify the generated project in
    the same way as ``create_schematic_from_netlist``.
    """
    if not isinstance(design, CircuitDesign):
        raise ValueError("design must be CircuitDesign")
    analysis_kind, normalized_analysis_options = _normalize_analysis_options(
        analysis, analysis_options
    )
    root = _validate_output_directory(output_directory)
    logical = materialize_circuit_design_partition(design, partition)
    prepared = materialize_multiboard_fixture_artifacts(logical, fixtures)
    preview: dict[str, Any] = {
        "schema_version": 1,
        "kind": "multisim-mcp-native-multiboard-acceptance",
        "status": "logical-only",
        "verification_status": "unverified",
        "target_multisim_version": str(target_multisim_version).strip(),
        "analysis": analysis_kind,
        "analysis_options": normalized_analysis_options,
        "prepared_artifacts": prepared,
        "output_directory": str(root),
        "execution_started": False,
    }
    if not execute:
        return preview
    if not str(target_multisim_version).strip():
        raise ValueError("target_multisim_version must not be empty")

    if schematic_executor is None or client is None:
        # Lazy import keeps preview mode COM-free and reuses the tested server
        # pipeline for native XML encoding and round-trip checks.
        from . import server

        schematic_executor = schematic_executor or server._create_schematic_impl
        client = client or server.client
    root.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    full_reference: dict[str, Any] | None = None
    try:
        connection = client.connect()
        detected_version = str(connection.get("version", "")).strip()
        target_version_text = str(target_multisim_version).strip()
        target_version_tuple = parse_multisim_version(target_version_text)
        detected_version_tuple = parse_multisim_version(detected_version)
        if target_version_tuple == (0, 0, 0):
            raise ValueError(
                f"target_multisim_version is not a recognized version: {target_version_text!r}"
            )
        if detected_version_tuple == (0, 0, 0) or detected_version_tuple != target_version_tuple:
            raise RuntimeError(
                f"native acceptance requires Multisim {target_multisim_version}, detected {detected_version or 'unknown'}"
            )
        _write_json(root / "fixture-artifacts.json", prepared)
        for board in prepared["boards"]:
            board_id = str(board["board_id"])
            board_root = root / board_id
            board_root.mkdir()
            ms14 = board_root / "circuit.ms14"
            build = dict(schematic_executor(
                board["spice_netlist"],
                str(ms14),
                probe_nets=list(board.get("probe_nets", [])),
                include_experimental_probes=True,
                open_after_build=True,
                image_path=str(board_root / "schematic.png"),
                overwrite=False,
                verify=True,
                require_layout_pass=True,
            ))
            _write_json(board_root / "build.json", build)
            if build.get("success") is not True:
                raise RuntimeError(f"{board_id}: native schematic generation failed")
            save_result = client.save_circuit(str(ms14))
            saved_sha256 = _sha256(ms14)
            reopened = client.open_circuit(str(ms14))
            report_path = board_root / "reopened-report-netlist.txt"
            client.report_netlist(str(report_path), False, 0)
            reopened_report = report_path.read_bytes().decode("utf-8", errors="replace")
            reopened_topology = _reopened_topology(board, reopened_report)
            outputs = [str(item) for item in client.enum_outputs(0)]
            if not outputs:
                raise RuntimeError(f"{board_id}: saved/reopened project has no native probe outputs")
            native_analysis = _run_native_analysis(
                client, analysis_kind, outputs, normalized_analysis_options
            )
            values = _analysis_values(native_analysis, outputs, analysis_kind)
            if native_analysis.get("ready") is not True or any(value is None for value in values.values()):
                raise RuntimeError(
                    f"{board_id}: native {analysis_kind} did not produce complete probe values"
                )
            analysis_stem = "native-op" if analysis_kind == "dc" else f"native-{analysis_kind}"
            record = {
                "board_id": board_id,
                "build_success": True,
                "build_verification": build.get("verification", {}),
                "probe_map": _probe_map(build),
                "save_result": save_result,
                "reopened": reopened,
                "saved_sha256": saved_sha256,
                "reopened_components": [str(item) for item in client.enum_components(0)],
                "reopened_outputs": outputs,
                "analysis": analysis_kind,
                "native_analysis_values": values,
                "native_op_values": values if analysis_kind == "dc" else {},
                "native_analysis_ready": True,
                "native_analysis_informative": _analysis_informative(analysis_kind, values),
                "native_op_ready": True,
                "native_analysis_artifacts": {
                    "json": f"{analysis_stem}.json",
                    "csv": f"{analysis_stem}.csv",
                },
                "post_save_netlist_sha256": _sha256(report_path),
                "reopened_topology": reopened_topology,
                "topology": build.get("topology_diff", {}),
                "layout": build.get("layout_validation", {}),
            }
            _write_json(board_root / f"{analysis_stem}.json", native_analysis)
            _write_native_op_csv(board_root / f"{analysis_stem}.csv", values)
            reopen_name = "native-reopen-op.json" if analysis_kind == "dc" else f"native-reopen-{analysis_kind}.json"
            _write_json(board_root / reopen_name, {
                **record,
                "analysis_response": native_analysis,
            })
            records.append(record)

        observations = _observation_values(records)
        interface_nets = sorted({
            str(item["net"])
            for fixture in fixtures
            if isinstance(fixture, Mapping) and fixture.get("kind") == "observation"
            for item in [fixture]
            if item.get("net")
        })
        interface_comparison = compare_multiboard_interface_observations(
            prepared, observations, nets=interface_nets or None
        )
        # A complete-design reference uses the original source netlist and the
        # same explicitly observed nets; no fixture is inferred for it.
        reference_nets = [
            net for net in interface_nets
            if net in {node for component in design.components for node in component.nodes}
        ]
        if reference_nets:
            reference_root = root / "full-reference"
            reference_root.mkdir()
            reference_netlist = circuit_design_to_spice(design)
            reference_build = dict(schematic_executor(
                reference_netlist,
                str(reference_root / "circuit.ms14"),
                probe_nets=reference_nets,
                include_experimental_probes=True,
                open_after_build=True,
                image_path=str(reference_root / "schematic.png"),
                overwrite=False,
                verify=True,
                require_layout_pass=True,
            ))
            if reference_build.get("success") is not True:
                raise RuntimeError("full reference schematic generation failed")
            client.save_circuit(str(reference_root / "circuit.ms14"))
            client.open_circuit(str(reference_root / "circuit.ms14"))
            reference_outputs = [str(item) for item in client.enum_outputs(0)]
            if not reference_outputs:
                raise RuntimeError("full reference project has no native probe outputs")
            reference_analysis = _run_native_analysis(
                client, analysis_kind, reference_outputs, normalized_analysis_options
            )
            reference_values_by_output = _analysis_values(
                reference_analysis, reference_outputs, analysis_kind
            )
            reference_probe_map = _probe_map(reference_build)
            reference_values = {
                net: reference_values_by_output.get(output)
                for net, output in reference_probe_map.items()
            }
            reference_stem = "native-op" if analysis_kind == "dc" else f"native-{analysis_kind}"
            reference_reopen_name = "native-reopen-op.json" if analysis_kind == "dc" else f"native-reopen-{analysis_kind}.json"
            _write_json(reference_root / f"{reference_stem}.json", reference_analysis)
            _write_native_op_csv(reference_root / f"{reference_stem}.csv", reference_values)
            _write_json(reference_root / reference_reopen_name, {
                "outputs": reference_outputs,
                "analysis": reference_analysis,
                "values": reference_values,
                "artifacts": {"json": f"{reference_stem}.json", "csv": f"{reference_stem}.csv"},
            })
            reference_board = next(
                (
                    record for record in records
                    if all(
                        net in observations.get(record["board_id"], {})
                        for net in reference_nets
                    )
                ),
                None,
            )
            signal_values = (
                observations.get(reference_board["board_id"], {})
                if reference_board is not None else {}
            )
            differences = {
                net: abs(float(signal_values[net]) - float(reference_values[net]))
                for net in reference_nets
                if net in signal_values and isinstance(reference_values.get(net), (int, float))
            }
            full_reference = {
                "status": "pass" if len(differences) == len(reference_nets) and all(value <= 1e-9 for value in differences.values()) else "fail",
                "tolerance": 1e-9,
                "board": reference_board["board_id"] if reference_board is not None else None,
                "board_values": signal_values,
                "reference_values": reference_values,
                "differences": differences,
            }
        native_acceptance = {
            "all_boards_build": all(item["build_success"] for item in records),
            "all_boards_save_reopen": all(item["reopened_outputs"] for item in records),
            "all_probes_emitted": all(
                item["reopened_outputs"]
                and set(item["probe_map"].values()) <= set(item["reopened_outputs"])
                for item in records
            ),
            "all_topology_pass": all(item["topology"].get("status") == "pass" for item in records),
            "all_reopened_topology_pass": all(
                item["reopened_topology"].get("status") == "pass" for item in records
            ),
            "all_layout_pass": all(item["layout"].get("status") == "pass" for item in records),
            "all_native_analysis_ready": all(item["native_analysis_ready"] for item in records),
            "analysis_information_pass": all(item["native_analysis_informative"] for item in records),
            # Kept as a compatibility alias for consumers of the original DC-only gate.
            "all_native_op_ready": all(item["native_analysis_ready"] for item in records),
            "interface_values_match": interface_comparison["status"] == "pass",
            "matches_full_reference": full_reference is None or full_reference["status"] == "pass",
        }
        result = {
            **preview,
            "status": "accepted" if all(native_acceptance.values()) else "failed",
            "verification_status": "native-verified" if all(native_acceptance.values()) else "native-failed",
            "execution_started": True,
            "analysis": analysis_kind,
            "analysis_options": normalized_analysis_options,
            "multisim_version": detected_version,
            "multisim_version_tuple": list(detected_version_tuple),
            "target_multisim_version_tuple": list(target_version_tuple),
            "boards": records,
            "observations": observations,
            "interface_comparison": interface_comparison,
            "full_reference": full_reference,
            "native_acceptance": native_acceptance,
            "report": "acceptance-report.md",
        }
        result.pop("prepared_artifacts", None)
        _write_json(root / "acceptance.json", result)
        _write_acceptance_report(root / "acceptance-report.md", result)
        return result
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


__all__ = ["run_native_multiboard_acceptance"]
