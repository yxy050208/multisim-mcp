"""Offline consistency verifier for the bounded hybrid multi-board archive."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .hybrid_multiboard_regression import select_hybrid_multiboard_regression_case


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"missing or invalid archive file: {path.name}") from exc


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _verify_manifest(root: Path) -> int:
    manifest = _read(root / "archive-manifest.json")
    entries = manifest.get("files")
    _require(manifest.get("schema_version") == 1 and isinstance(entries, Mapping), "invalid archive manifest")
    paths = [p for p in root.rglob("*") if p.is_file()]
    names = {p.relative_to(root).as_posix() for p in paths if p.name != "archive-manifest.json"}
    _require(set(entries) == names, "archive manifest coverage differs")
    for name, digest in entries.items():
        path = (root / name).resolve()
        _require(path.is_relative_to(root) and path.is_file(), f"invalid archive path: {name}")
        _require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"artifact hash mismatch: {name}")
    return len(entries)


def verify_hybrid_multiboard_evidence(output_directory: str | Path) -> dict[str, Any]:
    """Recheck an accepted archive without starting Multisim or COM."""
    root = Path(output_directory).expanduser().resolve()
    count = _verify_manifest(root)
    acceptance = _read(root / "acceptance.json")
    # Archives created before case identity was persisted are the original
    # DNOT4 baseline. New runners record the selected case explicitly so the
    # verifier cannot apply the wrong board/probe contract.
    case_id = str(acceptance.get("case_id") or "dnot4_rc_split")
    case = select_hybrid_multiboard_regression_case(case_id)
    fixtures = _read(root / "fixture-artifacts.json")
    _require(acceptance.get("status") == "accepted" and acceptance.get("verification_status") == "native-verified",
             "archive is not accepted/native-verified")
    _require(acceptance.get("target_multisim_version") == case.target_version, "archive target version differs")
    _require(acceptance.get("analysis") == case.analysis, "archive analysis differs")
    gates = acceptance.get("native_acceptance")
    _require(isinstance(gates, Mapping) and gates and all(value is True for value in gates.values()),
             "one or more native acceptance gates are false")
    _require(fixtures.get("fixture_contract", {}).get("status") == "valid"
             and fixtures.get("fixture_contract", {}).get("coverage_status") == "complete",
             "fixture contract is incomplete")
    archived_fixture_list = fixtures["fixture_contract"]["fixtures"]
    expected_fixture_list = case.fixtures()
    _require(
        {item.get("id") for item in archived_fixture_list}
        == {item.get("id") for item in expected_fixture_list},
        "fixture identities differ",
    )
    archived_by_id = {item["id"]: item for item in archived_fixture_list}
    for expected in expected_fixture_list:
        actual = archived_by_id[expected["id"]]
        _require(
            all(actual.get(field) == expected.get(field) for field in ("board_id", "kind", "net")),
            f"fixture definition differs: {expected['id']}",
        )
        if expected.get("kind") == "circuit_fragment":
            _require(
                actual.get("model_scope") == expected.get("model_scope")
                and actual.get("source_compiled_digest") == expected.get("source_compiled_digest"),
                "fixture source digest differs",
            )
        elif expected.get("kind") in {"voltage_source", "resistor_termination"}:
            for field in ("reference_net", "refdes", "value", "model"):
                if field in expected:
                    _require(
                        actual.get(field) == expected.get(field),
                        f"fixture definition differs: {expected['id']}.{field}",
                    )
    _require(fixtures.get("native_connector_status") == "native-verified"
             and fixtures.get("native_connector_ready") is True,
             "connector mapping is not native-verified")

    board_records = acceptance.get("boards", [])
    expected_board_ids = {
        str(item.get("id", "")).strip()
        for item in case.boards()
        if isinstance(item, Mapping) and str(item.get("id", "")).strip()
    }
    _require({item.get("board_id") for item in board_records} == expected_board_ids, "board identities differ")
    expected_probe_nets = {
        board_id: {
            str(item.get("net"))
            for item in case.fixtures()
            if item.get("board_id") == board_id and item.get("kind") == "observation"
        }
        for board_id in expected_board_ids
    }
    continuous_contracts = acceptance.get("continuous_response_contracts", {})
    continuous_nets = {
        str(net) for net in continuous_contracts
        if str(net).strip()
    } if isinstance(continuous_contracts, Mapping) else set()
    # Archives produced before the RC contract was added intentionally did
    # not probe ``filt``. Keep those historical records verifiable while
    # requiring the extra probe whenever the new contract is declared.
    if case_id in {"adc4_dac4_transfer_split", "adc4_dac4_edge_split"} and not continuous_nets:
        expected_probe_nets.get("dac", set()).discard("filt")
    case_manifest = case.manifest()
    raw_connectors = case_manifest.get("connectors")
    if not isinstance(raw_connectors, list):
        raw_connectors = [case_manifest.get("connector", {})]
    expected_named_pin_count = sum(
        len(item.get("pins", []))
        for item in raw_connectors
        if isinstance(item, Mapping) and isinstance(item.get("pins"), list)
    )
    samples = 0
    for record in board_records:
        board_id = record["board_id"]
        local = root / board_id
        build = _read(local / "build.json")
        _require(build.get("success") is True and build.get("layout_validation", {}).get("status") == "pass",
                 f"{board_id}: build/layout gate failed")
        reopened = record.get("reopened_topology", {})
        _require(reopened.get("status") == "pass", f"{board_id}: reopened topology failed")
        pin = reopened.get("connector_pin_connections", {})
        _require(
            pin.get("status") == "pass"
            and pin.get("named_pin_counts") == {
                "pass": expected_named_pin_count, "fail": 0, "unverified": 0
            },
                 f"{board_id}: connector pin evidence differs")
        probe_map = record.get("probe_map", {})
        _require(set(probe_map) == expected_probe_nets.get(board_id, set()),
                 f"{board_id}: probe map differs")
        _require(set(probe_map.values()) <= set(record.get("reopened_outputs", [])),
                 f"{board_id}: requested probe output missing")
        stored_series = _read(local / "native-tran-series.json")
        _require(stored_series == record.get("native_analysis_series"), f"{board_id}: stored series differs")
        samples += sum(len(stored_series[output]["axis"]) for output in probe_map.values())

    interface = acceptance.get("series_interface_comparison", {})
    reference = acceptance.get("full_reference", {})
    edge_nets = {
        str(net) for net in acceptance.get("digital_edge_contracts", {})
        if str(net).strip()
    }
    converter_contract = acceptance.get("converter_transfer", {})
    converter_nets = set()
    if isinstance(converter_contract, Mapping):
        declared = converter_contract.get("contract", {})
        if isinstance(declared, Mapping):
            converter_nets = {
                str(declared.get("analog_input", "")),
                str(declared.get("analog_output", "")),
                *(str(net) for net in declared.get("digital_bits", []) if str(net).strip()),
            }
            converter_nets.discard("")
    event_interface = acceptance.get("event_interface_comparison", {})
    _require(
        (
            interface.get("status") == "pass"
            or (edge_nets and event_interface.get("status") == "pass")
        )
        and reference.get("status") == "pass",
        "interface or full-reference comparison failed",
    )
    reference_comparisons = reference.get("comparisons", [])
    _require(
        isinstance(reference_comparisons, list)
        and reference_comparisons
        and all(
            item.get("status") == "pass"
            for item in reference_comparisons
            if isinstance(item, Mapping)
            and item.get("net") not in edge_nets
            and item.get("net") not in converter_nets
            and item.get("net") not in continuous_nets
        )
        and all(isinstance(item, Mapping) for item in reference_comparisons),
        "one or more full-reference net comparisons failed",
    )
    if edge_nets:
        event_comparisons = event_interface.get("comparisons", [])
        _require(
            event_interface.get("status") == "pass"
            and isinstance(event_comparisons, list)
            and all(isinstance(item, Mapping) for item in event_comparisons)
            and all(
                item.get("status") == "pass"
                and all(
                    endpoint.get("status") == "pass" and endpoint.get("passed") is True
                    for endpoint in item.get("endpoint_comparisons", {}).values()
                )
                for item in event_comparisons
                if isinstance(item, Mapping)
            )
            and reference.get("event_comparison", {}).get("status") == "pass",
            "digital edge reference comparison failed",
        )
    converter = acceptance.get("converter_transfer", {})
    if converter.get("status") not in {None, "not-applicable"}:
        transfer = converter.get("result", {})
        _require(
            converter.get("status") == "pass"
            and converter.get("passed") is True
            and transfer.get("passed") is True
            and transfer.get("observed_codes") == list(range(16))
            and isinstance(transfer.get("max_dac_error_voltage"), (int, float)),
            "converter transfer contract failed",
        )
    continuous = acceptance.get("continuous_response", {})
    if continuous_nets:
        comparisons = continuous.get("comparisons", []) if isinstance(continuous, Mapping) else []
        _require(
            continuous.get("status") == "pass"
            and continuous.get("passed") is True
            and isinstance(comparisons, list)
            and comparisons
            and all(isinstance(item, Mapping) for item in comparisons)
            and all(
                item.get("status") == "pass"
                and all(
                    endpoint.get("status") == "pass"
                    and endpoint.get("passed") is True
                    for endpoint in item.get("endpoint_comparisons", {}).values()
                )
                for item in comparisons
                if isinstance(item, Mapping)
            ),
            "continuous response contract failed",
        )
    _require(isinstance(reference.get("differences"), Mapping),
             "full-reference differences are missing")
    return {
        "status": "pass", "case_id": case_id, "hash_checked_files": count, "sampled_voltage_values": samples,
        "current_machine_native_verified": False,
        "basis": "offline archive consistency recheck; no COM and no authenticity claim",
    }


__all__ = ["verify_hybrid_multiboard_evidence"]
