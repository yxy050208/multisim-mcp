"""Recheck bounded LM324AJ archive consistency without COM or simulation.

Hashes detect file changes, not authenticity. Native measurements and RLC
readbacks remain archived observations, not fresh current-machine evidence.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .eda_core import CircuitDesign
from .generated_analog_run import vendor_model_fingerprints
from .linear_reference import expected_native_pins, scalar
from .multiboard_fixtures import materialize_multiboard_fixture_artifacts, compare_multiboard_interface_series
from .multiboard_plan import materialize_circuit_design_partition
from .multisim_compat import parse_multisim_version
from .native_analysis_series import extract_native_analysis_series, native_series_informative
from .native_multiboard_acceptance import _normalize_analysis_options, _compare_full_reference_series
from .native_netlist_validation import validate_native_netlist
from .vendor_multiboard import (
    BOARD_IDS, PROFILE, TARGET_MULTISIM_VERSION, NATIVE_COMPARISON_TOLERANCE_V,
    LIMITATIONS, _board_vendor_parts, validate_lm324aj_multiboard_contract, vendor_report_text, _report_html,
)


_GATES = {
    "all_boards_build", "all_boards_save_reopen", "all_probes_emitted", "all_topology_pass",
    "all_reopened_topology_pass", "all_layout_pass", "all_native_analysis_ready",
    "analysis_information_pass", "all_native_op_ready", "interface_values_match",
    "interface_series_match", "matches_full_reference", "full_reference_ready", "physical_boards_pass",
}


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"),
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite JSON: {value}")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"missing or invalid archive file: {path.name}") from exc


def _canonical(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False))


def _verify_manifest(root: Path) -> int:
    manifest = _read(root / "sha256-manifest.json")
    entries = manifest.get("files")
    paths = list(root.rglob("*"))
    _require(not any(path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) for path in paths),
             "archive links are not supported")
    names = {p.relative_to(root).as_posix() for p in paths if p.is_file() and p != root / "sha256-manifest.json"}
    _require(manifest.get("schema_version") == 1 and isinstance(entries, Mapping) and set(entries) == names,
             "manifest must cover every archive file exactly")
    for name, digest in entries.items():
        path = (root / name).resolve()
        _require(path.is_relative_to(root) and path.is_file(), "invalid archive path")
        _require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"artifact hash mismatch: {name}")
    return len(entries)


def _probe_mapping(build: Mapping[str, Any], expected: list[str]) -> dict[str, str]:
    probes = build.get("build", {}).get("probes", [])
    _require(isinstance(probes, list) and all(isinstance(p, Mapping) for p in probes), "invalid probe records")
    mapping = {p.get("net"): p.get("voltage_output") for p in probes}
    _require(len(mapping) == len(probes) and set(mapping) == set(expected)
             and all(isinstance(o, str) and o for o in mapping.values())
             and len(set(mapping.values())) == len(mapping), "incomplete or aliased native observations")
    return mapping


def _verify_project(local: Path, board: Mapping[str, Any]) -> dict[str, Any]:
    build = _read(local / "build.json")
    _require(build.get("success") is True and build.get("layout_validation", {}).get("status") == "pass",
             "archived build/layout gate failed")
    _require((local / "circuit.ms14").is_file() and (local / "circuit.ms14").stat().st_size > 0,
             "native project missing")
    _require((local / "schematic.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "schematic PNG missing or invalid")
    saved = _read(local / "reopened-contract.json")
    parts = _board_vendor_parts(board)
    expected = expected_native_pins(parts)
    # Carrier pin numbers are pack-specific. Compare all recorded terminals
    # to the declared node pair; do not guess a different local template's
    # numbering when rechecking an archive on a machine without its pack.
    for part in parts:
        if part.kind == "V":
            pins = saved.get("pins", {}).get("netlist", {}).get(part.refdes, {})
            _require(set(pins) == {"1", "2"} and sorted(pins.values()) == sorted(part.nodes),
                     "voltage carrier terminal contract differs")
            expected[part.refdes] = pins
    for component in board["design"]["components"]:
        if component["kind"] == "HDR1X4":
            expected[component["refdes"]] = {f"P{i}": net for i, net in enumerate(component["nodes"], 1)}
    pins = validate_native_netlist((local / "reopened-report-netlist.txt").read_text(encoding="utf-8"), expected, strict=True)
    _require(pins["ok"] and _canonical(pins) == saved.get("pins"), "archived native pin contract differs")
    passive = [part for part in parts if part.kind in {"R", "C"}]
    _require(set(saved.get("rlc_values", {})) == {part.refdes for part in passive}, "incomplete passive readback")
    for part in passive:
        value = saved["rlc_values"][part.refdes]
        actual = value.get("actual_si")
        _require(not isinstance(actual, bool) and isinstance(actual, (float, int)) and math.isfinite(actual)
                 and math.isclose(actual, scalar(part.value), rel_tol=1e-9, abs_tol=0)
                 and value.get("expected_si") == scalar(part.value) and value.get("passed") is True,
                 "archived native RLC readback differs")
    before = vendor_model_fingerprints(local / "circuit.ms14.xml", parts)
    after = vendor_model_fingerprints(local / "reopened-model.xml", parts)
    _require(before == after == saved.get("vendor_model_fingerprints") == saved.get("source_vendor_model_fingerprints"),
             "archived vendor model fingerprint differs")
    _require(saved.get("status") == "pass" and saved.get("vendor_models_pass") is True
             and saved.get("vendor_model_error") is None, "archived electrical contract did not pass")
    return build


def _verify_series(local: Path, stem: str, outputs: list[str], analysis: str, options: Mapping[str, Any]) -> dict:
    _require(isinstance(outputs, list) and outputs and all(isinstance(x, str) for x in outputs)
             and len(set(outputs)) == len(outputs), "native output identities missing or duplicated")
    raw = _read(local / f"{stem}.json")
    _require(raw.get("requested_outputs") == outputs, "raw native output list differs")
    derived = extract_native_analysis_series(raw, outputs, analysis, options)
    _require(native_series_informative(analysis, derived), "incomplete native samples or missing excitation")
    _require(derived == _read(local / f"{stem}-series.json"), "raw native matrix differs from stored series")
    with (local / f"{stem}-series.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    expected = [["output", "analysis", "axis", "real", "imaginary"]]
    for output in sorted(derived):
        item = derived[output]
        expected.extend([output, analysis, format(x, ".17g"), format(r, ".17g"), format(i, ".17g")]
                        for x, r, i in zip(item["axis"], item["real"], item["imaginary"]))
    _require(rows == expected, "native series CSV differs from raw samples")
    return derived


def verify_vendor_multiboard_evidence(output_directory: str | Path) -> dict[str, Any]:
    """Fail closed on changed contracts, readbacks, models or sampled responses."""
    root = Path(output_directory).expanduser().resolve()
    try:
        count = _verify_manifest(root)
        contract = validate_lm324aj_multiboard_contract(_read(root / "vendor-contract.json"))
        result = _read(root / "vendor-acceptance.json")
        _require(result == _read(root / "acceptance.json"), "final acceptance records differ")
        _require(result.get("status") == "accepted" and result.get("verification_status") == "native-verified"
                 and result.get("profile") == PROFILE, "archive is not an accepted vendor experiment")
        _require(result.get("contract_digest") == contract["contract_digest"]
                 and result.get("model_contract") == contract["model_contract"], "archive contract identity differs")
        _require(result.get("comparison_tolerance") == NATIVE_COMPARISON_TOLERANCE_V
                 and result.get("target_multisim_version") == TARGET_MULTISIM_VERSION
                 and parse_multisim_version(result.get("multisim_version", "")) == parse_multisim_version(TARGET_MULTISIM_VERSION),
                 "archive version or comparison tolerance differs")
        _require(set(result.get("native_acceptance", {})) == _GATES
                 and all(v is True for v in result["native_acceptance"].values()), "incomplete native acceptance gates")
        analysis, options = _normalize_analysis_options(result.get("analysis"), result.get("analysis_options"))
        _require(options == result["analysis_options"], "analysis options differ")
        design = CircuitDesign.from_dict(contract["design"])
        physical = materialize_circuit_design_partition(design, contract["partition"], target_multisim_version=TARGET_MULTISIM_VERSION)
        prepared = materialize_multiboard_fixture_artifacts(physical, contract["fixtures"])
        _require(physical == _read(root / "physical-board-artifacts.json"), "physical partition differs from contract")
        _require(prepared == _read(root / "fixture-artifacts.json"), "fixture archive differs from derived contract")
        records = result.get("boards", [])
        drawings = result.get("physical_board_drawings", [])
        _require(len(records) == len(drawings) == 2
                 and {b["board_id"] for b in records} == set(BOARD_IDS)
                 and {b["board_id"] for b in drawings} == set(BOARD_IDS), "board identities missing or duplicated")
        physical_by_id = {b["board_id"]: b for b in physical["boards"]}
        stem = "native-op" if analysis == "dc" else f"native-{analysis}"
        series_by_board: dict[str, dict] = {}
        samples = 0
        for board in prepared["boards"]:
            local = root / board["board_id"]
            build = _verify_project(local, board)
            mapping = _probe_mapping(build, board["probe_nets"])
            record = next(b for b in records if b["board_id"] == board["board_id"])
            _require(record.get("probe_map") == mapping, "acceptance observation mapping differs")
            derived = _verify_series(local, stem, record["reopened_outputs"], analysis, options)
            _require(derived == record.get("native_analysis_series"), "raw board matrix differs from acceptance series")
            _require(set(mapping.values()) <= set(derived), "requested probe output missing")
            series_by_board[board["board_id"]] = {net: derived[output] for net, output in mapping.items()}
            samples += sum(len(derived[o]["axis"]) for o in mapping.values())
        for drawing in drawings:
            board = physical_by_id[drawing["board_id"]]
            local = root / "physical" / board["board_id"]
            _verify_project(local, board)
            _require(drawing.get("status") == "pass" and drawing.get("simulation_performed") is False,
                     "physical drawing scope or state differs")
            _require(drawing.get("saved_sha256") == hashlib.sha256((local / "circuit.ms14").read_bytes()).hexdigest(),
                     "physical project hash differs from acceptance")
            _require(_read(local / "design.json") == board["design"]
                     and (local / "physical.cir").read_text(encoding="utf-8") == board["spice_netlist"],
                     "physical board contains changed components or experiment fixtures")
            with (local / "bom.csv").open(encoding="utf-8", newline="") as stream:
                rows = list(csv.reader(stream))
            expected = [["refdes", "kind", "value", "model", "scope"]] + [
                [p["refdes"], p["kind"], p.get("value") or "", p.get("model") or "", "physical-design"]
                for p in board["design"]["components"]]
            _require(rows == expected, "physical component CSV differs")
        nets = sorted({net for board in prepared["boards"] for net in board["probe_nets"]})
        local = root / "full-reference"
        build = _verify_project(local, {"design": design.to_dict()})
        mapping = _probe_mapping(build, nets)
        raw = _read(local / f"{stem}.json")
        derived = _verify_series(local, stem, raw["requested_outputs"], analysis, options)
        reference = {net: derived[output] for net, output in mapping.items()}
        samples += sum(len(derived[o]["axis"]) for o in mapping.values())
        allow_resampling = options.get("series_alignment") == "linear"
        interfaces = compare_multiboard_interface_series(prepared, series_by_board, nets=nets,
            absolute_tolerance=NATIVE_COMPARISON_TOLERANCE_V, allow_resampling=allow_resampling)
        comparison = _compare_full_reference_series(records, reference, nets,
            absolute_tolerance=NATIVE_COMPARISON_TOLERANCE_V, allow_resampling=allow_resampling)
        _require(interfaces["status"] == comparison["status"] == "pass", "archived boards differ from interfaces or full reference")
        _require(interfaces == result.get("series_interface_comparison")
                 and comparison["comparisons"] == result.get("full_reference", {}).get("comparisons"),
                 "stored comparison differs from recomputed samples")
        report = vendor_report_text(result)
        _require((root / "vendor-report.md").read_text(encoding="utf-8") == report
                 and (root / "vendor-report.html").read_text(encoding="utf-8") == _report_html("LM324AJ 双板实验", report),
                 "report differs from acceptance evidence")
        return {"status": "pass", "hash_checked_files": count, "sampled_voltage_values": samples,
                "analysis": analysis, "physical_board_count": 2,
                "max_reference_difference_v": max(c.get("max_absolute_difference", 0) for c in comparison["comparisons"]),
                "current_machine_native_verified": False, "basis": "archived consistency recheck; no COM; no authenticity claim"}
    except (KeyError, TypeError, AttributeError, OSError, IndexError) as exc:
        raise ValueError(f"incomplete or invalid vendor archive: {type(exc).__name__}: {exc}") from exc


__all__ = ["verify_vendor_multiboard_evidence"]
