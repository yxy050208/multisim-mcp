"""Recheck archived ideal two-board native evidence without starting Multisim.

Hash checks detect archive changes; recorded native observations are not fresh
current-machine validation. NI-derived XML/project assets stay local.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from multisim_mcp.generated_analog_run import ideal_model_fingerprints
from multisim_mcp.linear_reference import expected_native_pins, scalar
from multisim_mcp.native_analysis_series import extract_native_analysis_series
from multisim_mcp.native_multiboard_acceptance import _compare_full_reference_series
from multisim_mcp.native_netlist_validation import validate_native_netlist
from multisim_mcp.signal_multiboard import (
    compare_native_to_linear, linear_board_parts, run_approved_signal_multiboard,
)


def verify(root: Path) -> dict:
    root = root.resolve()
    read = lambda p: json.loads((root / p).read_text(encoding="utf-8"))
    manifest = read("sha256-manifest.json")
    actual_files = {p.relative_to(root).as_posix() for p in root.rglob("*")
                    if p.is_file() and p.name != "sha256-manifest.json"}
    if manifest.get("schema_version") != 1 or set(manifest.get("files", {})) != actual_files:
        raise ValueError("manifest must cover every archive file exactly")
    for relative, digest in manifest["files"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"invalid manifest path: {relative}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"artifact hash mismatch: {relative}")
    result = read("signal-acceptance.json")
    if result["status"] != "accepted" or result["verification_status"] != "native-verified":
        raise ValueError("archive is not a passed signal experiment")
    result["output_directory"] = str(root)
    preview, approval = read("approved-preview.json"), read("netlist-approval.json")
    prepared = run_approved_signal_multiboard(read("engineering-request.json"), preview, approval,
        root / "unused-offline-preview", analysis=result["analysis"], analysis_options=result["analysis_options"])
    if read("physical-board-artifacts.json") != prepared["physical_artifacts"]:
        raise ValueError("physical partition differs from the approved contract")
    layouts = []
    for board in [*prepared["prepared_artifacts"]["boards"], *prepared["physical_artifacts"]["boards"],
                  {"board_id": "full-reference", "design": preview["circuit_design"]}]:
        physical = board in prepared["physical_artifacts"]["boards"]
        local = root / ("physical" if physical else "") / board["board_id"]
        parts = linear_board_parts(board)
        expected = expected_native_pins(parts)
        contract = json.loads((local / "reopened-contract.json").read_text(encoding="utf-8"))
        # DC/AC carriers have different native pin numbering. Offline checking
        # uses the recorded native mapping, rather than today's optional pack.
        # Source polarity/phase is independently checked against measured OP/AC.
        for p in parts:
            if p.kind == "V":
                pins = contract["pins"]["netlist"][p.refdes]
                if set(pins) != {"1", "2"} or sorted(pins.values()) != sorted(p.nodes):
                    raise ValueError("archived voltage carrier terminals differ from the approved source")
                expected[p.refdes] = pins
        for p in board["design"]["components"]:
            if p["kind"] == "HDR1X4":
                expected[p["refdes"]] = {f"P{i}": n for i, n in enumerate(p["nodes"], 1)}
        if not validate_native_netlist((local / "reopened-report-netlist.txt").read_text(encoding="utf-8"), expected, strict=True)["ok"]:
            raise ValueError("archived native pin contract differs")
        for p in parts:
            if p.kind in {"R", "C", "L"}:
                actual = contract["rlc_values"][p.refdes]["actual_si"]
                if (isinstance(actual, bool) or not isinstance(actual, (int, float))
                        or not math.isfinite(actual) or not math.isclose(actual, scalar(p.value), rel_tol=1e-9, abs_tol=0)):
                    raise ValueError("archived native RLC readback differs")
        if ideal_model_fingerprints(local / "reopened-model.xml", parts) != contract["ideal_model_fingerprints"]:
            raise ValueError("archived ideal model differs")
        build = json.loads((local / "build.json").read_text(encoding="utf-8"))
        if not build["success"] or build["layout_validation"]["status"] != "pass":
            raise ValueError("archived build/layout gate failed")
        layouts.append({"object": str(local.relative_to(root)), **build["layout_validation"]})
    stem = "native-op" if result["analysis"] == "dc" else "native-ac"
    for record in result["boards"]:
        local = root / record["board_id"]
        raw = json.loads((local / f"{stem}.json").read_text(encoding="utf-8"))
        derived = extract_native_analysis_series(raw, record["reopened_outputs"], result["analysis"], result["analysis_options"])
        if derived != record["native_analysis_series"]:
            raise ValueError("raw board matrix differs from acceptance record")
    local = root / "full-reference"
    build = json.loads((local / "build.json").read_text(encoding="utf-8"))
    mapping = {p["net"]: p["voltage_output"] for p in build["build"]["probes"]}
    raw = json.loads((local / f"{stem}.json").read_text(encoding="utf-8"))
    derived = extract_native_analysis_series(raw, raw["requested_outputs"], result["analysis"], result["analysis_options"])
    if derived != json.loads((local / f"{stem}-series.json").read_text(encoding="utf-8")):
        raise ValueError("raw full-reference matrix differs from series archive")
    if _compare_full_reference_series(result["boards"], {n: derived[o] for n, o in mapping.items()}, list(mapping))["status"] != "pass":
        raise ValueError("archived boards differ from complete native reference")
    independent = compare_native_to_linear(result, preview)
    if independent["status"] != "pass" or independent["target_transfer_status"] != "pass":
        raise ValueError("archived native values differ from ideal nodal or transfer target")
    return {"status": "pass", "root": str(root), "hash_checked_files": len(manifest["files"]),
            "sampled_channel_values": sum(c["samples"] for c in independent["checks"]),
            "max_complex_error_v": max(c["max_complex_error_v"] for c in independent["checks"]),
            "target_transfer_checks": independent["target_transfer_checks"], "layouts": layouts,
            "current_machine_native_verified": False, "basis": "archived evidence recheck; no COM"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = [verify(root) for root in args.directories]
    text = json.dumps(results, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
