"""Approved, fixed two-board ideal signal experiments, without joint optimization.

The conditioner experiment includes a declared upstream circuit fixture. It
preserves RC phase and finite ideal-buffer gain; it is not a physical duplicate
board, an inferred equivalent source, or arbitrary coupled co-simulation.
"""
from __future__ import annotations

import csv
import cmath
import hashlib
import html
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .eda_core import CircuitDesign
from .engineering_planner import build_engineering_plan, select_multiboard_engineering_candidate
from .engineering_request import validate_engineering_request
from .executable_approvals import validate_executable_netlist_approval
from .executable_netlists import _digest
from .generated_analog_run import ideal_model_fingerprints
from .linear_reference import expected_native_pins, scalar, solve_linear, validated_components
from .multiboard_plan import materialize_circuit_design_partition
from .native_analysis_series import extract_native_analysis_series
from .native_multiboard_acceptance import _normalize_analysis_options, run_native_multiboard_acceptance
from .native_netlist_validation import validate_native_netlist
from .spice_adapter import circuit_design_to_spice


PROFILE = "approved-ideal-signal-two-board-v1"
SENSOR_REFS = frozenset({"VCC", "VINPUT", "RSOURCE", "RPROT", "CFLT", "XU1", "CDEC"})
LIMITATIONS = [
    "Fixed sensor/buffer and gain/bias partition; no joint parameter or topology optimization.",
    "Explicit upstream experiment fixture applies to this ideal zero-output-impedance buffer only.",
    "OPAMP5 is a finite-gain ideal VCVS, without vendor bandwidth, clipping, slew, noise or current limits.",
    "Physical board JSON/BOM excludes test fixtures; experiment .ms14 includes them and probes.",
    "No physical PCB, connector resistance, cable parasitics, thermal or safety verification.",
]
OBSERVATION_NETS = {
    "sensor": ("path_02", "rail_positive"),
    "conditioner": ("path_02", "rail_positive", "path_04"),
    "full-reference": ("path_02", "rail_positive", "path_04"),
}


def prepare_signal_multiboard_request(
    executable_netlist: Mapping[str, Any], netlist_approval: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive an exact bounded partition from a validated fixture approval."""
    validate_executable_netlist_approval(executable_netlist, netlist_approval)
    if executable_netlist.get("synthesis", {}).get("template") != "ideal-opamp-affine-low-pass-v1":
        raise ValueError("approved two-board signal profile requires signal-active")
    fixture = executable_netlist.get("experiment_fixture")
    if not isinstance(fixture, Mapping):
        raise ValueError("approved two-board signal profile requires an explicit experiment_fixture")
    if fixture["contract"]["target_version"] != "14.3":
        raise ValueError("approved two-board signal profile requires Multisim 14.3")
    design = CircuitDesign.from_dict(executable_netlist["circuit_design"])
    components = [dict(p.to_dict(), board="sensor" if p.refdes in SENSOR_REFS else "conditioner")
                  for p in design.components]
    upstream = [p.to_dict() for p in design.components if p.refdes in SENSOR_REFS]
    fixtures = [{
        "id": "conditioner-upstream-circuit", "board_id": "conditioner",
        "kind": "circuit_fragment", "net": "path_02",
        "ports": ["path_02", "rail_positive", "0"], "components": upstream,
        "source_compiled_digest": executable_netlist["compiled_digest"],
        "notes": "Explicit upstream test circuit; excluded from conditioner physical BOM.",
    }]
    for board, nets in (("sensor", ["path_02", "rail_positive"]),
                        ("conditioner", ["path_02", "rail_positive", "path_04"])):
        fixtures += [{"id": f"{board}-observe-{net}", "board_id": board,
                      "kind": "observation", "net": net} for net in nets]
        fixtures.append({"id": f"{board}-ground", "board_id": board,
                         "kind": "ground_reference", "net": "0"})
    return validate_engineering_request({
        "schema_version": 1, "title": f"{design.title} — two-board experiment",
        "application": PROFILE, "boards": [{"id": "sensor", "role": "sensor-filter-buffer"},
                                             {"id": "conditioner", "role": "gain-bias-output"}],
        "constraints": [{"text": "Keep the exact reviewed parts, values and four-pin interface."}],
        "components": components, "fixtures": fixtures,
        "experiments": [{"type": "op"}, {"type": "ac"}],
        "connectors": [{"id": "signal-link", "part": "HDR1X4", "boards": ["sensor", "conditioner"],
            "instances": [{"board": "sensor", "refdes": "J1"}, {"board": "conditioner", "refdes": "J1"}],
            "pins": [
                {"number": 1, "net": "path_02", "signal_type": "analog", "direction": "bidirectional",
                 "board_directions": {"sensor": "out", "conditioner": "in"}, "reference_net": "0"},
                {"number": 2, "net": "rail_positive", "signal_type": "power", "direction": "bidirectional",
                 "board_directions": {"sensor": "out", "conditioner": "in"}, "reference_net": "0"},
                {"number": 3, "net": "0", "signal_type": "ground", "direction": "passive"},
                {"number": 4, "net": "0", "signal_type": "ground", "direction": "passive"},
            ]}],
    })


def bind_signal_multiboard_request(request, executable_netlist, netlist_approval):
    if executable_netlist is None or netlist_approval is None:
        raise ValueError("executable_netlist and netlist_approval must be provided together")
    expected = prepare_signal_multiboard_request(executable_netlist, netlist_approval)
    if request is not None and validate_engineering_request(request) != expected:
        raise ValueError("multi-board request differs from the approved fixed signal partition")
    return expected


def linear_board_parts(board: Mapping[str, Any]) -> list[Any]:
    """Connectors are physical pin mappings, not active oracle elements."""
    data = dict(board["design"])
    data["components"] = [p for p in data["components"] if p["kind"].upper() != "HDR1X4"]
    # Do not let source_netlist restore the connector or the unpartitioned design.
    data["source_netlist"] = None
    return validated_components(circuit_design_to_spice(CircuitDesign.from_dict(data), prefer_source=False))


def linear_preflight(prepared: Mapping[str, Any], preview: Mapping[str, Any]) -> dict[str, Any]:
    full = validated_components(preview["spice_netlist"])
    cutoff = float(preview["synthesis"]["estimated_cutoff_hz"])
    checks = []
    for board in prepared["boards"]:
        parts = linear_board_parts(board)
        for frequency in (None, 10.0, cutoff, 10000.0):
            expected, actual = solve_linear(full, frequency), solve_linear(parts, frequency)
            for net in board["probe_nets"]:
                error = abs(expected[net] - actual[net])
                checks.append({"board_id": board["board_id"], "net": net, "frequency_hz": frequency,
                               "absolute_error_v": error, "passed": error <= 1e-9})
    return {"status": "pass" if checks and all(c["passed"] for c in checks) else "fail",
            "basis": "independent nodal solution of exact per-board fixture IR versus full approved netlist",
            "native_verified": False, "checks": checks}


def validate_reopened_signal_contract(board, client, board_root: Path) -> dict[str, Any]:
    """Strict final-file pin, SI value and ideal-model checks before simulation."""
    parts = linear_board_parts(board)
    expected = expected_native_pins(parts)
    for component in board["design"]["components"]:
        if component["kind"].upper() == "HDR1X4":
            expected[component["refdes"]] = {f"P{i}": n for i, n in enumerate(component["nodes"], 1)}
    pins = validate_native_netlist((board_root / "reopened-report-netlist.txt").read_text(encoding="utf-8", errors="replace"),
                                  expected, strict=True)
    values = {}
    for p in parts:
        if p.kind in {"R", "C", "L"}:
            raw = client.get_rlc_value(p.refdes).get("value")
            try:
                actual = float(raw) if not isinstance(raw, bool) else None
            except (TypeError, ValueError, OverflowError):
                actual = None
            finite = actual is not None and math.isfinite(actual)
            values[p.refdes] = {"expected_si": scalar(p.value), "actual_si": actual,
                "passed": finite and math.isclose(actual, scalar(p.value), rel_tol=1e-9, abs_tol=0)}
            if not finite:
                values[p.refdes].update(actual_si=None, readback_error="missing, non-numeric or non-finite RLC value")
    from .multisim_client import Ms14Codec
    Ms14Codec().decode(str(board_root / "circuit.ms14"), str(board_root / "reopened-model.xml"))
    try:
        fingerprints = ideal_model_fingerprints(board_root / "reopened-model.xml", parts)
        model_ok = True
        model_error = None
    except ValueError as exc:
        fingerprints, model_ok, model_error = {}, False, str(exc)
    return {"status": "pass" if pins["ok"] and all(v["passed"] for v in values.values()) and model_ok else "fail",
            "pins": pins, "rlc_values": values, "ideal_model_fingerprints": fingerprints,
            "ideal_models_pass": model_ok, "ideal_model_error": model_error}


def compare_native_to_linear(result: Mapping[str, Any], preview: Mapping[str, Any]) -> dict[str, Any]:
    """Complete board identities, observations and every native complex sample.

    Revalidate series rather than trusting an archived ``status: pass`` flag.
    Missing channels, duplicate boards and incompatible axes fail closed.
    """
    full = validated_components(preview["spice_netlist"])
    checks = []
    targets = []
    transfer = preview["synthesis"]["target_transfer"]
    fixture = preview["experiment_fixture"]["contract"]
    violations = []
    records = []
    boards = result.get("boards", [])
    if (not isinstance(boards, list) or len(boards) != 2
            or sorted(str(b.get("board_id")) for b in boards if isinstance(b, Mapping)) != ["conditioner", "sensor"]):
        violations.append("exactly one sensor and one conditioner record required")
    else:
        records = [(b["board_id"], b) for b in boards]
    root = Path(result["output_directory"])
    analysis = result.get("analysis")
    if analysis not in {"dc", "ac"}:
        violations.append("signal comparison supports dc/ac only")
    stem = "native-op" if analysis == "dc" else "native-ac"
    reference_path = root / "full-reference" / "build.json"
    try:
        build = json.loads(reference_path.read_text(encoding="utf-8"))
        series = json.loads((reference_path.parent / f"{stem}-series.json").read_text(encoding="utf-8"))
        probes = build["build"]["probes"]
        mapping = {p["net"]: p["voltage_output"] for p in probes}
        if len(mapping) != len(probes):
            violations.append("duplicate full-reference probe net")
        records.append(("full-reference", {"probe_map": mapping, "native_analysis_series": series}))
    except (OSError, ValueError, KeyError, TypeError):
        violations.append("complete readable full-reference build and series required")
    solutions = {}
    common_axis = None
    for board_id, record in records:
        mapping = record.get("probe_map")
        if (not isinstance(mapping, Mapping) or set(mapping) != set(OBSERVATION_NETS[board_id])
                or any(not isinstance(o, str) or not o.strip() for o in mapping.values())
                or len(set(mapping.values())) != len(mapping)):
            violations.append(f"{board_id}: exact distinct observation map required")
            continue
        archived = record.get("native_analysis_series")
        if not isinstance(archived, Mapping) or analysis not in {"dc", "ac"}:
            violations.append(f"{board_id}: native series required")
            continue
        for net, output in mapping.items():
            series = archived.get(output, {})
            if not isinstance(series, Mapping):
                series = {}
            rows = [series.get("axis", []), series.get("real", [])]
            if analysis == "ac":
                rows.append(series.get("imaginary", []))
            count = series.get("sample_count", len(rows[0]) if isinstance(rows[0], list) else 0)
            decoded = extract_native_analysis_series({"ready": True, "results": {output: {
                "rows": rows, "n_points": count, "sampled_points": count}}},
                [output], analysis, result.get("analysis_options"))[output]
            axes, real, imaginary = decoded["axis"], decoded["real"], decoded["imaginary"]
            ready = (series.get("status") == "pass" and decoded["status"] == "pass"
                     and series.get("analysis", analysis) == analysis and series.get("output", output) == output)
            if analysis == "dc" and series.get("imaginary") != imaginary:
                ready = False
            if not ready:
                violations.append(f"{board_id}/{net}: complete valid native series required")
            if ready:
                if common_axis is None:
                    common_axis = axes
                elif len(axes) != len(common_axis) or not all(
                        math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12) for a, b in zip(axes, common_axis)):
                    ready = False
                    violations.append(f"{board_id}/{net}: native axes differ")
            errors = []
            if ready:
                for frequency, re, im in zip(axes, real, imaginary):
                    key = float(frequency) if analysis == "ac" else None
                    if key not in solutions:
                        solutions[key] = solve_linear(full, key)
                    errors.append(abs(complex(re, im) - solutions[key][net]))
            checks.append({"board_id": board_id, "net": net, "samples": len(errors),
                           "max_complex_error_v": max(errors) if errors else None,
                           "passed": ready and all(math.isfinite(e) and e <= 1e-7 for e in errors)})
            if net == "path_04":
                target_errors = []
                for frequency, re, im in zip(axes, real, imaginary) if ready else []:
                    target = (transfer["gain_v_per_v"] * fixture["dc_voltage_v"] + transfer["offset_v"]
                              if analysis == "dc" else
                              transfer["gain_v_per_v"] * cmath.rect(fixture["ac_amplitude_v"], math.radians(fixture["ac_phase_deg"]))
                              / complex(1, frequency / preview["synthesis"]["estimated_cutoff_hz"]))
                    target_errors.append(abs(complex(re, im) - target))
                targets.append({"board_id": board_id, "samples": len(target_errors),
                    "max_transfer_target_error_v": max(target_errors) if target_errors else None,
                    "passed": ready and bool(target_errors) and all(math.isfinite(e) and e <= 1e-7 for e in target_errors)})
    complete = not violations and len(records) == 3 and len(checks) == 8
    return {"status": "pass" if complete and all(c["passed"] for c in checks) else "fail",
            "coverage_status": "complete" if complete else "incomplete", "coverage_violations": violations,
            "absolute_complex_tolerance_v": 1e-7, "checks": checks,
            "target_transfer_checks": targets,
            "target_transfer_status": "pass" if complete and len(targets) == 2 and all(c["passed"] for c in targets) else "fail",
            "basis": "all native sample axes, real and imaginary values versus independent full-circuit nodal solution"}


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def export_board_drawings(root, physical, *, schematic_executor=None, client=None):
    """Native board-only drawings; the isolated receiver is not simulated here."""
    if schematic_executor is None or client is None:
        from . import server
        schematic_executor = schematic_executor or server._create_schematic_impl
        client = client or server.client
    records = []
    try:
        client.connect()
        for board in physical["boards"]:
            local = root / "physical" / board["board_id"]
            local.mkdir(parents=True, exist_ok=False)
            ms14 = local / "circuit.ms14"
            build = dict(schematic_executor(board["spice_netlist"], str(ms14), probe_nets=[],
                include_experimental_probes=False, open_after_build=True, image_path=str(local / "schematic.png"),
                overwrite=False, verify=True, require_layout_pass=True))
            _write(local / "build.json", build)
            if build.get("success") is not True or build.get("layout_validation", {}).get("status") != "pass":
                raise RuntimeError(f"{board['board_id']}: board-only drawing failed")
            client.save_circuit(str(ms14))
            client.open_circuit(str(ms14))
            client.report_netlist(str(local / "reopened-report-netlist.txt"), False, 0)
            contract = validate_reopened_signal_contract(board, client, local)
            _write(local / "reopened-contract.json", contract)
            if contract["status"] != "pass":
                raise RuntimeError(f"{board['board_id']}: board-only saved drawing contract failed")
            records.append({"board_id": board["board_id"], "status": "pass", "simulation_performed": False,
                "project": str(ms14.relative_to(root)), "image": str((local / "schematic.png").relative_to(root)),
                "saved_sha256": hashlib.sha256(ms14.read_bytes()).hexdigest(), "layout": build["layout_validation"]})
        return records
    finally:
        client.disconnect()


def _persist(root, result, request, preview, approval, physical):
    _write(root / "approved-preview.json", preview)
    _write(root / "netlist-approval.json", approval)
    _write(root / "engineering-request.json", request)
    _write(root / "physical-board-artifacts.json", physical)
    for board in physical["boards"]:
        local = root / "physical" / board["board_id"]
        local.mkdir(parents=True, exist_ok=True)
        _write(local / "design.json", board["design"])
        (local / "physical.cir").write_text(board["spice_netlist"], encoding="utf-8")
        with (local / "bom.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["refdes", "kind", "value", "model", "scope"])
            for p in board["design"]["components"]:
                writer.writerow([p["refdes"], p["kind"], p.get("value"), p.get("model"), "physical-design"])
    _write(root / "signal-acceptance.json", result)
    lines = ["# 双板理想信号链实验报告", "", f"状态：`{result['status']}`；验证：`{result['verification_status']}`。",
             f"分析：`{result['analysis']}`；目标 Multisim：`14.3`。", "",
             "传感器/RC/缓冲板通过 J1 连接增益/偏置板。P1 为信号、P2 为供电、P3/P4 为地线。",
             "接收板实验文件包含明确的前级电路夹具；physical 目录提供不含前级夹具的独立板图和组件清单。",
             "板图中的理想源/运放是仿真设计对象，组件清单不等于可采购或投产 BOM。", "",
             "| 对象 | 网络 | 样本数 | 最大复数误差 (V) | 通过 |", "| --- | --- | ---: | ---: | --- |"]
    for c in result.get("linear_reference_comparison", {}).get("checks", []):
        lines.append(f"| {c['board_id']} | {c['net']} | {c['samples']} | {c['max_complex_error_v']} | {c['passed']} |")
    if result.get("error"):
        lines += ["", "执行失败：" + result["error"]]
    lines += ["", "适用范围："] + [f"- {x}" for x in LIMITATIONS]
    report = "\n".join(lines) + "\n"
    (root / "signal-report.md").write_text(report, encoding="utf-8")
    (root / "signal-report.html").write_text("<!doctype html><meta charset='utf-8'><title>双板实验报告</title>"
        "<style>body{max-width:1000px;margin:32px auto;font:16px sans-serif}pre{white-space:pre-wrap}</style>"
        "<pre>" + html.escape(report) + "</pre>", encoding="utf-8")
    _write(root / "sha256-manifest.json", {"schema_version": 1, "files": {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file() and p.name != "sha256-manifest.json"}})


def run_approved_signal_multiboard(
    request, executable_netlist, netlist_approval, output_directory, *, execute=False,
    candidate_index=None, target_multisim_version="14.3", analysis="dc", analysis_options=None,
    schematic_executor=None, client=None,
) -> dict[str, Any]:
    normalized = bind_signal_multiboard_request(request, executable_netlist, netlist_approval)
    kind, options = _normalize_analysis_options(analysis, analysis_options)
    if kind not in {"dc", "ac"}:
        raise ValueError("approved ideal two-board signal profile supports dc and ac only")
    if options["max_points"] > 2000 or options.get("num_points", 0) > 2000:
        raise ValueError("approved signal analysis is bounded to 2000 points")
    if options.get("series_alignment", "strict") != "strict":
        raise ValueError("approved signal profile requires strict native sample axes")
    if target_multisim_version != "14.3":
        raise ValueError("target version must match reviewed fixture version 14.3")
    if kind == "ac" and "ac_amplitude_v" not in executable_netlist["experiment_fixture"]["contract"]:
        raise ValueError("native AC requires a reviewed AC fixture")
    plan = build_engineering_plan(normalized)
    selected = select_multiboard_engineering_candidate(plan, candidate_index)
    design = CircuitDesign.from_dict(executable_netlist["circuit_design"])
    physical = materialize_circuit_design_partition(design, selected["candidate"], target_multisim_version="14.3")
    kwargs = dict(target_multisim_version="14.3", analysis=kind, analysis_options=options,
                  schematic_executor=schematic_executor, client=client,
                  reopened_contract_validator=validate_reopened_signal_contract)
    preview = run_native_multiboard_acceptance(design, selected["candidate"], normalized["fixtures"],
                                               output_directory, execute=False, **kwargs)
    preflight = linear_preflight(preview["prepared_artifacts"], executable_netlist)
    if preflight["status"] != "pass":
        raise ValueError("two-board fixture preflight differs from full approved circuit")
    provenance = {
        "compiled_digest": executable_netlist["compiled_digest"], "spice_sha256": executable_netlist["spice_sha256"],
        "netlist_approval_id": netlist_approval["approval_id"], "netlist_approval_digest": netlist_approval["approval_digest"],
        "fixture": netlist_approval["experiment_fixture"], "engineering_request_sha256": _digest(normalized),
        "analysis_authorization": "explicit-native-multiboard-execute", "simulation_plan_approval_present": False,
    }
    metadata = {"profile": PROFILE, "approval_provenance": provenance, "linear_preflight": preflight,
                "engineering_plan_digest": plan["plan_digest"], "selection_digest": selected["selection_digest"],
                "candidate_index": selected["candidate_index"], "limitations": LIMITATIONS}
    if not execute:
        return {**preview, **metadata, "request": normalized, "physical_artifacts": physical}
    created_directories = []
    try:
        result = run_native_multiboard_acceptance(design, selected["candidate"], normalized["fixtures"],
            output_directory, execute=True, output_directory_created=created_directories.append, **kwargs)
        independent = compare_native_to_linear(result, executable_netlist)
        if independent["status"] != "pass" or independent["target_transfer_status"] != "pass":
            result.update(status="failed", verification_status="native-failed")
        result["linear_reference_comparison"] = independent
        if result["status"] == "accepted":
            result["physical_native_drawings"] = export_board_drawings(Path(preview["output_directory"]), physical,
                schematic_executor=schematic_executor, client=client)
    except Exception as exc:
        if Path(preview["output_directory"]) not in created_directories:
            raise
        result = {k: v for k, v in preview.items() if k != "prepared_artifacts"}
        result.update(status="failed", verification_status="native-failed", execution_started=True,
                      error=f"{type(exc).__name__}: {exc}")
    result.update(metadata, report="signal-report.html")
    _persist(Path(preview["output_directory"]), result, normalized, executable_netlist, netlist_approval, physical)
    return result
