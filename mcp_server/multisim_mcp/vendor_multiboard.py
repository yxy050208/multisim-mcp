"""Bounded native two-board contract for the reviewed LM324AJ model.

This module deliberately does not generalise vendor multi-board generation.
It accepts one reviewed two-stage LM324AJ topology, uses the exact 14.3
component/connector mappings, and delegates save/reopen and analysis to the
existing native acceptance runner.  A receiver-board run gets an explicit
copy of the upstream stage as a test fixture; that copy is evidence for the
fixture, not an inferred replacement for the physical sensor board.
"""
from __future__ import annotations

import hashlib
import json
import math
import csv
import html
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from dataclasses import replace

from .eda_core import CircuitDesign
from .generated_analog_run import vendor_model_fingerprints
from .linear_reference import expected_native_pins, scalar, validate_native_source, validated_components
from .multiboard_plan import materialize_circuit_design_partition, plan_multiboard_partition
from .native_multiboard_acceptance import (
    _normalize_analysis_options,
    _write_acceptance_report,
    run_native_multiboard_acceptance,
)
from .native_netlist_validation import validate_native_netlist
from .spice_adapter import circuit_design_to_spice


PROFILE = "approved-lm324aj-two-board-v1"
TARGET_MULTISIM_VERSION = "14.3"
BOARD_IDS = ("sensor", "conditioner")
SENSOR_REFS = frozenset({"V1", "VCC", "VSS", "R1", "C1", "U1", "RF1", "RG1"})
CONDITIONER_REFS = frozenset({"R2", "C2", "U2", "RF2", "RG2", "RL"})
LM324_REFS = frozenset({"U1", "U2"})
INTERFACE_PINS = {
    1: ("stage1", "analog"),
    2: ("vcc", "power"),
    3: ("vss", "power"),
    4: ("0", "ground"),
}
LIMITATIONS = [
    "Only the reviewed Multisim 14.3 LM324AJ section-A model and this fixed two-board topology are accepted.",
    "Board experiments use explicit upstream and downstream LM324AJ fixtures; they are not coupled PCB or cable simulations.",
    "The result is native vendor-model sampled evidence, with no ideal-equation equivalence claim.",
    "Physical board drawings are exported without experiment fixtures, but no cross-board parameter/topology search, advanced wire optimisation, PCB fabrication, tolerance or thermal proof is included.",
]
NATIVE_COMPARISON_TOLERANCE_V = 1e-7


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _as_design(value: CircuitDesign | Mapping[str, Any]) -> CircuitDesign:
    if isinstance(value, CircuitDesign):
        return value
    return CircuitDesign.from_dict(value)


def _component_map(design: CircuitDesign) -> dict[str, Any]:
    return {item.refdes: item for item in design.components}


def validate_lm324aj_design(
    design: CircuitDesign | Mapping[str, Any], *, target_multisim_version: str = TARGET_MULTISIM_VERSION,
) -> CircuitDesign:
    """Validate the exact reviewed two-stage LM324AJ topology.

    Values are intentionally free within the bounded topology so the natural
    language planner can vary gain/cutoff/load.  Reference designators,
    terminal order, supplies and model identity remain fixed.
    """
    target = str(target_multisim_version).strip()
    if target != TARGET_MULTISIM_VERSION:
        raise ValueError("LM324AJ two-board contract is verified only for Multisim 14.3")
    normalized = _as_design(design)
    parts = _component_map(normalized)
    expected = SENSOR_REFS | CONDITIONER_REFS
    if set(parts) != expected:
        missing = sorted(expected - set(parts))
        extra = sorted(set(parts) - expected)
        raise ValueError(f"LM324AJ two-board topology requires exact reviewed parts; missing={missing}, extra={extra}")
    expected_terminals = {
        "V1": ("V", ("in", "0")), "VCC": ("V", ("vcc", "0")), "VSS": ("V", ("vss", "0")),
        "R1": ("R", ("in", "lp1")), "C1": ("C", ("lp1", "0")),
        "RF1": ("R", ("stage1", "fb1")), "RG1": ("R", ("fb1", "0")),
        "R2": ("R", ("stage1", "lp2")), "C2": ("C", ("lp2", "0")),
        "RF2": ("R", ("out", "fb2")), "RG2": ("R", ("fb2", "0")), "RL": ("R", ("out", "0")),
    }
    for ref, (kind, nodes) in expected_terminals.items():
        if parts[ref].kind != kind or parts[ref].nodes != nodes:
            raise ValueError(f"{ref} kind/pin order/nets differ from the reviewed topology")
    for ref in sorted(LM324_REFS):
        part = parts[ref]
        if part.kind != "LM324AJ" or (part.model or "").upper() != "LM324AJ":
            raise ValueError(f"{ref} must use the reviewed LM324AJ model identity")
        if len(part.nodes) != 5:
            raise ValueError(f"{ref} requires semantic pins 1IN+, 1IN-, VS+, VS-, 1OUT")
    if parts["U1"].nodes != ("lp1", "fb1", "vcc", "vss", "stage1"):
        raise ValueError("U1 pin order/nets do not match the reviewed sensor stage")
    if parts["U2"].nodes != ("lp2", "fb2", "vcc", "vss", "out"):
        raise ValueError("U2 pin order/nets do not match the reviewed conditioner stage")
    for ref in ("V1", "VCC", "VSS"):
        if parts[ref].kind != "V" or len(parts[ref].nodes) != 2 or parts[ref].nodes[1] != "0":
            raise ValueError(f"{ref} must be a grounded voltage source")
        validate_native_source(parts[ref])
    if scalar(parts["VCC"].value or "0") <= 0 or scalar(parts["VSS"].value or "0") >= 0:
        raise ValueError("VCC must be positive and VSS must be negative")
    for ref in sorted(expected - LM324_REFS - {"V1", "VCC", "VSS"}):
        part = parts[ref]
        if part.kind not in {"R", "C"} or len(part.nodes) != 2:
            raise ValueError(f"{ref} must be a two-terminal passive R/C component")
        if scalar(part.value or "0") <= 0:
            raise ValueError(f"{ref} must have a positive SI value")
    # Rebuild the executable source from the checked IR. A stale imported
    # source_netlist must never become a different complete-design reference.
    netlist = circuit_design_to_spice(normalized, prefer_source=False)
    validated_components(netlist, allow_vendor=True)
    return replace(normalized, source_netlist=netlist)


def _connector_contract() -> list[dict[str, Any]]:
    return [{
        "id": "signal-link",
        "part": "HDR1X4",
        "boards": list(BOARD_IDS),
        "instances": [
            {"board": "sensor", "refdes": "J1"},
            {"board": "conditioner", "refdes": "J1"},
        ],
        "pins": [
            {"number": number, "net": net, "signal_type": signal_type,
             "direction": "passive" if signal_type == "ground" else "bidirectional",
             "board_directions": ({"sensor": "out", "conditioner": "in"}
                                  if number == 1 else {}),
             "reference_net": "0"}
            for number, (net, signal_type) in INTERFACE_PINS.items()
        ],
        "notes": "P1 stage1, P2 positive supply, P3 negative supply, P4 ground.",
    }]


def _fixtures(design: CircuitDesign) -> list[dict[str, Any]]:
    source_stage = [
        item.to_dict() for item in design.components
        if item.refdes in SENSOR_REFS
    ]
    # An isolated sender-board experiment otherwise has an open-circuit
    # stage1.  The real conditioner board presents the complete second-stage
    # LM324AJ input/load at that connector, so reproduce that explicitly on
    # the sender run.  It is a test fixture only and is excluded from the
    # physical board export; without it, AC/TRAN comparisons would conflate
    # different edge conditions with a topology or model defect.
    receiver_load = [item.to_dict() for item in design.components if item.refdes in CONDITIONER_REFS]
    fixtures: list[dict[str, Any]] = [{
        "id": "sensor-downstream-input-load",
        "board_id": "sensor",
        "kind": "circuit_fragment",
        "net": "stage1",
        "ports": ["stage1", "vcc", "vss", "0"],
        "components": receiver_load,
        "source_compiled_digest": _digest(design.to_dict()),
        "model_scope": "explicit-native-vendor-load-fixture",
        "notes": "Explicit conditioner LM324AJ input/load for an isolated sensor-board AC/TRAN run; excluded from physical exports.",
    }, {
        "id": "conditioner-upstream-lm324aj",
        "board_id": "conditioner",
        "kind": "circuit_fragment",
        "net": "stage1",
        "ports": ["stage1", "vcc", "vss", "0"],
        "components": source_stage,
        "source_compiled_digest": _digest(design.to_dict()),
        "model_scope": "explicit-native-vendor-fixture",
        "notes": "Explicit sensor-board experiment fixture; excluded from the conditioner physical design.",
    }]
    for board, nets in (("sensor", ("stage1", "vcc", "vss")),
                        ("conditioner", ("stage1", "vcc", "vss", "out"))):
        fixtures.extend({"id": f"{board}-observe-{net}", "board_id": board,
                         "kind": "observation", "net": net} for net in nets)
        fixtures.append({"id": f"{board}-ground", "board_id": board,
                         "kind": "ground_reference", "net": "0"})
    return fixtures


def prepare_lm324aj_multiboard_contract(
    design: CircuitDesign | Mapping[str, Any], *, target_multisim_version: str = TARGET_MULTISIM_VERSION,
) -> dict[str, Any]:
    """Derive the fixed partition and explicit receiver fixture."""
    normalized = validate_lm324aj_design(design, target_multisim_version=target_multisim_version)
    assigned = [dict(part.to_dict(), board=("sensor" if part.refdes in SENSOR_REFS else "conditioner"))
                for part in normalized.components]
    partition = plan_multiboard_partition(
        assigned,
        [{"id": "sensor", "role": "sensor-filter-buffer"},
         {"id": "conditioner", "role": "gain-output"}],
        _connector_contract(),
    )
    if partition["status"] != "planned":
        raise ValueError(f"LM324AJ partition is infeasible: {partition['violations']}")
    fixtures = _fixtures(normalized)
    contract = {
        "schema_version": 1,
        "kind": "multisim-mcp-lm324aj-multiboard-contract",
        "profile": PROFILE,
        "target_multisim_version": TARGET_MULTISIM_VERSION,
        "design": normalized.to_dict(),
        "partition": partition,
        "fixtures": fixtures,
        "model_contract": {
            "logical_family": "lm324aj-section-a",
            "native_name": "LM324AJ",
            "model_name": "LM158_4",
            "pins": ["1IN+", "1IN-", "VS+", "VS-", "1OUT"],
            "verification_method": "native-vendor-sampled",
            "comparison_tolerance_v": NATIVE_COMPARISON_TOLERANCE_V,
        },
        "limitations": list(LIMITATIONS),
    }
    contract["contract_digest"] = _digest(contract)
    return contract


def validate_lm324aj_multiboard_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(contract, Mapping) or contract.get("kind") != "multisim-mcp-lm324aj-multiboard-contract":
        raise ValueError("invalid LM324AJ multi-board contract kind")
    if contract.get("profile") != PROFILE or contract.get("target_multisim_version") != TARGET_MULTISIM_VERSION:
        raise ValueError("LM324AJ multi-board contract version/profile mismatch")
    design = validate_lm324aj_design(contract.get("design", {}))
    expected = prepare_lm324aj_multiboard_contract(design)
    actual_digest = contract.get("contract_digest")
    supplied = dict(contract)
    supplied.pop("contract_digest", None)
    if actual_digest != _digest(supplied):
        raise ValueError("LM324AJ multi-board contract digest mismatch")
    if expected != contract:
        raise ValueError("LM324AJ multi-board contract was changed after derivation")
    return expected


def _board_vendor_parts(board: Mapping[str, Any]) -> list[Any]:
    raw = board.get("design")
    if not isinstance(raw, Mapping):
        raise ValueError("board design is required")
    design = CircuitDesign.from_dict(raw)
    # HDR1X4 is a physical interface, not a vendor analogue model and has no
    # place in the vendor-model fingerprint or passive value loop.
    data = dict(design.to_dict())
    data["components"] = [item for item in data["components"] if item.get("kind", "").upper() != "HDR1X4"]
    data["nets"] = list(dict.fromkeys(node for item in data["components"] for node in item["nodes"]))
    local = CircuitDesign.from_dict(data)
    return validated_components(circuit_design_to_spice(local, prefer_source=False), allow_vendor=True)


def validate_reopened_lm324aj_contract(board: Mapping[str, Any], client: Any, board_root: Path) -> dict[str, Any]:
    """Validate all LM324AJ pins, SI passives, and linked model fingerprints."""
    parts = _board_vendor_parts(board)
    expected = expected_native_pins(parts)
    raw = board.get("design", {})
    for item in raw.get("components", []) if isinstance(raw, Mapping) else []:
        if str(item.get("kind", "")).upper() == "HDR1X4":
            expected[str(item["refdes"])] = {f"P{i}": node for i, node in enumerate(item["nodes"], 1)}
    report_path = Path(board_root) / "reopened-report-netlist.txt"
    pins = validate_native_netlist(report_path.read_text(encoding="utf-8", errors="replace"), expected, strict=True)
    values: dict[str, Any] = {}
    for part in parts:
        if part.kind not in {"R", "C"}:
            continue
        raw_value = client.get_rlc_value(part.refdes).get("value")
        try:
            actual = float(raw_value) if not isinstance(raw_value, bool) else None
        except (TypeError, ValueError, OverflowError):
            actual = None
        finite = actual is not None and math.isfinite(actual)
        values[part.refdes] = {
            "expected_si": scalar(part.value or "0"), "actual_si": actual,
            "passed": bool(finite and math.isclose(actual, scalar(part.value or "0"), rel_tol=1e-9, abs_tol=0)),
        }
        if not finite:
            values[part.refdes]["actual_si"] = None
            values[part.refdes]["readback_error"] = "missing, non-numeric or non-finite RLC value"
    from .multisim_client import Ms14Codec
    try:
        decoded = Ms14Codec().decode(str(Path(board_root) / "circuit.ms14"), str(Path(board_root) / "reopened-model.xml"))
        model_path = Path(decoded.get("xml", Path(board_root) / "reopened-model.xml")) if isinstance(decoded, Mapping) else Path(board_root) / "reopened-model.xml"
        fingerprints = vendor_model_fingerprints(model_path, parts)
        source_fingerprints = vendor_model_fingerprints(Path(board_root) / "circuit.ms14.xml", parts)
        model_error = None if fingerprints == source_fingerprints else "native save changed vendor model identity or definition"
    except Exception as exc:
        fingerprints = {}
        source_fingerprints = {}
        model_error = f"{type(exc).__name__}: {exc}"
    ok = bool(pins.get("ok")) and all(item["passed"] for item in values.values()) and model_error is None
    return {
        "status": "pass" if ok else "fail",
        "pins": pins,
        "rlc_values": values,
        "vendor_model_fingerprints": fingerprints,
        "source_vendor_model_fingerprints": source_fingerprints,
        "vendor_models_pass": model_error is None,
        "vendor_model_error": model_error,
        "verification_method": "native-vendor-sampled",
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _export_physical_board_drawings(
    design: CircuitDesign, partition: Mapping[str, Any], root: Path,
    *, schematic_executor: Any | None = None, client: Any | None = None,
) -> list[dict[str, Any]]:
    """Export saved/reopened board-only projects without experiment fixtures."""
    if schematic_executor is None or client is None:
        from . import server
        schematic_executor = schematic_executor or server._create_schematic_impl
        client = client or server.client
    logical = materialize_circuit_design_partition(
        design, partition, target_multisim_version=TARGET_MULTISIM_VERSION
    )
    physical_root = root / "physical"
    physical_root.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    connected = False
    try:
        client.connect()
        connected = True
        for board in logical["boards"]:
            board_id = str(board["board_id"])
            board_root = physical_root / board_id
            board_root.mkdir()
            ms14 = board_root / "circuit.ms14"
            build = dict(schematic_executor(
                board["spice_netlist"], str(ms14), probe_nets=[],
                include_experimental_probes=False, open_after_build=True,
                image_path=str(board_root / "schematic.png"), overwrite=False,
                verify=True, require_layout_pass=True,
            ))
            _write_json(board_root / "build.json", build)
            if build.get("success") is not True or build.get("layout_validation", {}).get("status") != "pass":
                raise RuntimeError(f"{board_id}: physical board schematic generation failed")
            client.save_circuit(str(ms14))
            client.open_circuit(str(ms14))
            report_path = board_root / "reopened-report-netlist.txt"
            client.report_netlist(str(report_path), False, 0)
            reopened = validate_reopened_lm324aj_contract(board, client, board_root)
            _write_json(board_root / "reopened-contract.json", reopened)
            if reopened.get("status") != "pass":
                raise RuntimeError(f"{board_id}: physical board saved/reopened contract failed")
            physical_design = board["design"]
            _write_json(board_root / "design.json", physical_design)
            (board_root / "physical.cir").write_text(board["spice_netlist"], encoding="utf-8")
            with (board_root / "bom.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("refdes", "kind", "value", "model", "scope"))
                for part in physical_design.get("components", []):
                    writer.writerow((part["refdes"], part["kind"], part.get("value"), part.get("model"), "physical-design"))
            records.append({
                "board_id": board_id, "status": "pass", "simulation_performed": False,
                "project": str(ms14.relative_to(root)), "image": str((board_root / "schematic.png").relative_to(root)),
                "saved_sha256": hashlib.sha256(ms14.read_bytes()).hexdigest(),
                "layout": build.get("layout_validation", {}), "reopened_contract": reopened,
            })
    finally:
        if connected:
            client.disconnect()
    return records


def _write_physical_manifest(root: Path) -> None:
    _write_json(root / "sha256-manifest.json", {
        "schema_version": 1,
        "files": {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file() and path != root / "sha256-manifest.json"
        },
    })


def vendor_report_text(result: Mapping[str, Any]) -> str:
    """Deterministic summary shared with the archive verifier."""
    lines = ["# LM324AJ 双板原生实验报告", "",
             f"状态：`{result['status']}`；验证：`{result['verification_status']}`。",
             f"Multisim：`{result.get('multisim_version', '未完成')}`；分析：`{result['analysis']}`。",
             f"合同：`{result['contract_digest']}`；跨工程比较门槛：`{NATIVE_COMPARISON_TOLERANCE_V:g} V`。", "",
             "sensor/conditioner 目录为带明确远端电路夹具的实验工程；full-reference 为完整参考工程。",
             "physical 目录为不含远端夹具的板级设计图，仅检查保存重开、引脚、阻容和模型，未另行仿真。",
             "接口：J1 P1=stage1，P2=vcc，P3=vss，P4=0。", "",
             "本门禁验证逐板实验与完整参考的一致性，不评判未声明的目标增益、偏置或硬件性能。", "",
             "## 验收门禁", "", "| 项目 | 通过 |", "| --- | --- |"]
    for name, passed in result.get("native_acceptance", {}).items():
        lines.append(f"| {name} | {passed is True} |")
    lines += ["", "## 完整参考采样比较", "",
              "| 板 | 网络 | 样本数 | 最大差值 (V) | 状态 |", "| --- | --- | ---: | ---: | --- |"]
    for item in (result.get("full_reference") or {}).get("comparisons", []):
        lines.append(f"| {item['board_id']} | {item['net']} | {item.get('sample_count', 0)} | "
                     f"{item.get('max_absolute_difference', '未验证')} | {item['status']} |")
    lines += ["", f"采样轴对齐：`{result['analysis_options'].get('series_alignment', 'strict')}`。",
              "线性重采样只比较原生返回样本，不证明连续时间、实际线缆或 PCB 性能。", ""]
    if result.get("error"):
        lines += ["执行失败：" + str(result["error"]), ""]
    return "\n".join(lines + ["## 适用范围", "", *["- " + x for x in LIMITATIONS], ""])


def _report_html(title: str, report: str) -> str:
    return ("<!doctype html><html lang='zh'><meta charset='utf-8'><title>" + html.escape(title) +
            "</title><style>body{max-width:1100px;margin:32px auto;padding:16px;font:16px sans-serif}"
            "pre{white-space:pre-wrap}img{max-width:100%}</style><pre>" + html.escape(report) + "</pre></html>")


def _persist_vendor_evidence(root: Path, result: dict[str, Any], contract: Mapping[str, Any]) -> None:
    design = CircuitDesign.from_dict(contract["design"])
    physical = materialize_circuit_design_partition(design, contract["partition"], target_multisim_version=TARGET_MULTISIM_VERSION)
    _write_json(root / "vendor-contract.json", contract)
    _write_json(root / "physical-board-artifacts.json", physical)
    result["report"] = "vendor-report.html"
    _write_json(root / "vendor-acceptance.json", result)
    _write_json(root / "acceptance.json", result)
    _write_acceptance_report(root / "acceptance-report.md", result)
    report = vendor_report_text(result)
    (root / "vendor-report.md").write_text(report, encoding="utf-8")
    (root / "vendor-report.html").write_text(_report_html("LM324AJ 双板实验", report), encoding="utf-8")
    for scope in ("sensor", "conditioner", "full-reference", "physical/sensor", "physical/conditioner"):
        local = root / scope
        if not local.is_dir():
            continue
        experiment = not scope.startswith("physical/")
        stem = "native-op" if result["analysis"] == "dc" else f"native-{result['analysis']}"
        text = f"# {scope}\n\n原生工程：[circuit.ms14](circuit.ms14)\n\n电路图：![schematic](schematic.png)\n\n"
        text += (f"带明确夹具的原生实验。[完整采样 CSV]({stem}-series.csv)；[原始矩阵]({stem}.json)。\n"
                 if experiment else "不含远端实验夹具的板级设计视图；未另行仿真。[组件清单](bom.csv)；[SPICE](physical.cir)。\n")
        text += "\n[保存重开引脚](reopened-report-netlist.txt)；[值和模型指纹](reopened-contract.json)。\n"
        (local / "report.md").write_text(text, encoding="utf-8")
        rendered = _report_html(scope, text).replace("</pre></html>",
            "</pre><p><a href='circuit.ms14'>原生工程</a></p><img alt='schematic' src='schematic.png'></html>")
        (local / "report.html").write_text(rendered, encoding="utf-8")
    _write_physical_manifest(root)


def run_lm324aj_multiboard_acceptance(
    contract: Mapping[str, Any], output_directory: str | Path, *, execute: bool = False,
    analysis: str = "dc", analysis_options: Mapping[str, Any] | None = None,
    schematic_executor: Any | None = None, client: Any | None = None,
) -> dict[str, Any]:
    """Run preview or exact-version native acceptance for the vendor contract."""
    normalized = validate_lm324aj_multiboard_contract(contract)
    kind, options = _normalize_analysis_options(analysis, analysis_options)
    if kind not in {"dc", "tran", "ac"}:
        raise ValueError("LM324AJ multi-board acceptance supports dc, tran and ac")
    design = CircuitDesign.from_dict(normalized["design"])
    partition = normalized["partition"]
    kwargs = {
        "target_multisim_version": TARGET_MULTISIM_VERSION,
        "analysis": kind,
        "analysis_options": options,
        "schematic_executor": schematic_executor,
        "client": client,
        "reopened_contract_validator": validate_reopened_lm324aj_contract,
        "comparison_tolerance": NATIVE_COMPARISON_TOLERANCE_V,
    }
    preview = run_native_multiboard_acceptance(
        design, partition, normalized["fixtures"], output_directory, execute=False, **kwargs
    )
    created: list[Path] = []
    result = preview
    if execute:
        try:
            result = run_native_multiboard_acceptance(
                design, partition, normalized["fixtures"], output_directory, execute=True,
                output_directory_created=created.append, **kwargs
            )
        except Exception as exc:
            if Path(preview["output_directory"]) not in created:
                raise
            result = {key: value for key, value in preview.items() if key != "prepared_artifacts"}
            result.update(status="failed", verification_status="native-failed", execution_started=True,
                          error=f"{type(exc).__name__}: {exc}")
    result.update({
        "profile": PROFILE,
        "verification_method": "native-vendor-sampled" if execute else "native-vendor-sampled-planned",
        "model_contract": normalized["model_contract"],
        "limitations": list(LIMITATIONS),
        "contract_digest": normalized["contract_digest"],
    })
    if execute and result.get("status") == "accepted":
        root = Path(result["output_directory"])
        try:
            physical = _export_physical_board_drawings(
                design, partition, root, schematic_executor=schematic_executor, client=client
            )
            result["physical_board_drawings"] = physical
            result["native_acceptance"] = {
                **dict(result.get("native_acceptance", {})),
                "physical_boards_pass": len(physical) == len(BOARD_IDS) and all(
                    item.get("status") == "pass" for item in physical
                ),
            }
            if not result["native_acceptance"]["physical_boards_pass"]:
                result["status"] = "failed"
                result["verification_status"] = "native-failed"
        except Exception as exc:
            result["physical_board_drawings"] = []
            result["native_acceptance"] = {
                **dict(result.get("native_acceptance", {})), "physical_boards_pass": False,
            }
            result["status"] = "failed"
            result["verification_status"] = "native-failed"
            result["error"] = f"{type(exc).__name__}: {exc}"
    if execute:
        _persist_vendor_evidence(Path(preview["output_directory"]), result, normalized)
    return result


__all__ = [
    "PROFILE", "TARGET_MULTISIM_VERSION", "LIMITATIONS", "NATIVE_COMPARISON_TOLERANCE_V",
    "validate_lm324aj_design", "prepare_lm324aj_multiboard_contract",
    "validate_lm324aj_multiboard_contract", "validate_reopened_lm324aj_contract",
    "run_lm324aj_multiboard_acceptance",
]
