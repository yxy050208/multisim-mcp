"""Run fail-closed fault injections against a hybrid multi-board archive."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from multisim_mcp.hybrid_multiboard_evidence import verify_hybrid_multiboard_evidence


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _rewrite_manifest(root: Path) -> None:
    files = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "archive-manifest.json"
    }
    _write(root / "archive-manifest.json", {"schema_version": 1, "kind": "hybrid-multiboard-archive", "files": files})


def _mutate(root: Path, fault: str) -> None:
    if fault == "fixture_digest":
        payload = _read(root / "fixture-artifacts.json")
        fragment = next(
            (item for item in payload["fixture_contract"]["fixtures"] if item.get("kind") == "circuit_fragment"),
            None,
        )
        if fragment is not None:
            fragment["source_compiled_digest"] = "f" * 64
        else:
            source = next(
                item for item in payload["fixture_contract"]["fixtures"]
                if item.get("kind") == "voltage_source"
            )
            if source.get("model") is not None:
                source["model"] = "PULSE(0 4 0 1n 1n 40u 80u)"
            else:
                source["value"] = "4"
        _write(root / "fixture-artifacts.json", payload)
        _rewrite_manifest(root)
    elif fault == "connector_pin":
        payload = _read(root / "acceptance.json")
        payload["boards"][0]["reopened_topology"]["connector_pin_connections"]["named_pin_counts"]["fail"] = 1
        _write(root / "acceptance.json", payload)
        _rewrite_manifest(root)
    elif fault == "missing_probe":
        payload = _read(root / "acceptance.json")
        board = payload["boards"][0]
        probe_map = board.get("probe_map", {})
        if not probe_map:
            raise ValueError("fault injection requires a non-empty probe map")
        probe_map.pop(sorted(probe_map)[0])
        _write(root / "acceptance.json", payload)
        _rewrite_manifest(root)
    elif fault == "topology":
        payload = _read(root / "acceptance.json")
        payload["boards"][0]["reopened_topology"]["status"] = "fail"
        _write(root / "acceptance.json", payload)
        _rewrite_manifest(root)
    elif fault == "sampling_axis":
        acceptance = _read(root / "acceptance.json")
        board_id = str(acceptance["boards"][0]["board_id"])
        path = root / board_id / "native-tran-series.json"
        payload = _read(path)
        first = next(value for value in payload.values() if isinstance(value, dict) and value.get("axis"))
        first["axis"][0] = float(first["axis"][0]) + 1e-6
        _write(path, payload)
        _rewrite_manifest(root)
    elif fault == "full_reference":
        payload = _read(root / "acceptance.json")
        comparisons = payload["full_reference"].get("comparisons", [])
        if not comparisons:
            raise ValueError("fault injection requires full-reference comparisons")
        edge_nets = set(payload.get("digital_edge_contracts", {}))
        converter = payload.get("converter_transfer", {})
        converter_contract = converter.get("contract", {}) if isinstance(converter, dict) else {}
        converter_nets = {
            str(converter_contract.get("analog_input", "")),
            str(converter_contract.get("analog_output", "")),
            *(str(item) for item in converter_contract.get("digital_bits", [])),
        }
        continuous_nets = set(payload.get("continuous_response_contracts", {}))
        target = next(
            (item for item in comparisons
             if item.get("net") not in edge_nets
             and item.get("net") not in converter_nets
             and item.get("net") not in continuous_nets),
            None,
        )
        if target is None:
            raise ValueError("fault injection requires an independently gated full-reference net")
        target["status"] = "fail"
        _write(root / "acceptance.json", payload)
        _rewrite_manifest(root)
    elif fault == "digital_edge":
        payload = _read(root / "acceptance.json")
        event = payload.get("event_interface_comparison", {})
        comparisons = event.get("comparisons", [])
        if not comparisons:
            raise ValueError("fault injection requires a digital edge comparison")
        endpoint_comparisons = comparisons[0].get("endpoint_comparisons", {})
        if not endpoint_comparisons:
            raise ValueError("fault injection requires a digital edge endpoint")
        first = next(iter(endpoint_comparisons.values()))
        first["status"] = "fail"
        first["passed"] = False
        _write(root / "acceptance.json", payload)
        _rewrite_manifest(root)
    elif fault == "converter_transfer":
        payload = _read(root / "acceptance.json")
        converter = payload.get("converter_transfer", {})
        if converter.get("status") != "pass":
            raise ValueError("fault injection requires a passing converter transfer")
        converter["status"] = "fail"
        converter["passed"] = False
        converter.setdefault("result", {})["passed"] = False
        _write(root / "acceptance.json", payload)
        _rewrite_manifest(root)
    elif fault == "continuous_response":
        payload = _read(root / "acceptance.json")
        continuous = payload.get("continuous_response", {})
        if continuous.get("status") != "pass":
            raise ValueError("fault injection requires a passing continuous response contract")
        continuous["status"] = "fail"
        continuous["passed"] = False
        comparisons = continuous.get("comparisons", [])
        if comparisons:
            comparisons[0]["status"] = "fail"
            endpoints = comparisons[0].get("endpoint_comparisons", {})
            if endpoints:
                next(iter(endpoints.values()))["status"] = "fail"
                next(iter(endpoints.values()))["passed"] = False
        _write(root / "acceptance.json", payload)
        _rewrite_manifest(root)
    elif fault == "artifact_hash":
        path = root / "acceptance-report.md"
        path.write_text(path.read_text(encoding="utf-8") + "\ncorrupted\n", encoding="utf-8")
    else:
        raise ValueError(f"unknown fault: {fault}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    faults = ["fixture_digest", "connector_pin", "missing_probe", "topology", "sampling_axis", "full_reference"]
    acceptance_path = args.archive / "acceptance.json"
    if acceptance_path.is_file():
        edge_contracts = _read(acceptance_path).get("digital_edge_contracts", {})
        if isinstance(edge_contracts, dict) and edge_contracts:
            faults.append("digital_edge")
    if acceptance_path.is_file() and "converter_transfer" in _read(acceptance_path):
        converter_payload = _read(acceptance_path).get("converter_transfer", {})
        if converter_payload.get("status") not in {None, "not-applicable"}:
            faults.append("converter_transfer")
    if acceptance_path.is_file() and "continuous_response_contracts" in _read(acceptance_path):
        contracts = _read(acceptance_path).get("continuous_response_contracts", {})
        if contracts:
            faults.append("continuous_response")
    faults.append("artifact_hash")
    results = []
    with tempfile.TemporaryDirectory(prefix="hybrid-fault-") as temp:
        temp_root = Path(temp)
        for fault in faults:
            target = temp_root / fault
            shutil.copytree(args.archive, target)
            _mutate(target, fault)
            try:
                verify_hybrid_multiboard_evidence(target)
                result = {"fault": fault, "status": "accepted-unexpectedly"}
            except ValueError as exc:
                result = {"fault": fault, "status": "rejected", "reason": str(exc)}
            results.append(result)
    passed = all(item["status"] == "rejected" for item in results)
    payload = {"schema_version": 1, "kind": "hybrid-multiboard-fault-matrix", "source": str(args.archive),
               "faults": results, "passed": passed}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
