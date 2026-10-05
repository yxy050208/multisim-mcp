"""Explicit boundary fixtures for isolated multi-board verification.

Partitioning a design gives each board a logical view, but it does not define
what is connected at a board edge during a simulation.  This module keeps
those boundary conditions explicit.  It deliberately does not infer an input
source, a termination, or an observation point from a net name or component
kind.

The contract is backend-neutral.  ``materialize_multiboard_fixture_artifacts``
adds only portable voltage/resistor fixture components to a board's structured
EDA design and returns probe nets for a later native build.  It does not create
an ``.ms14`` file and remains ``logical-only`` until Multisim opens, saves,
re-reads, and simulates the generated project.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .eda_core import CircuitComponent, CircuitDesign
from .spice_adapter import circuit_design_to_spice


FIXTURE_SCHEMA_VERSION = 1
FIXTURE_KINDS = frozenset(
    {"voltage_source", "resistor_termination", "ground_reference", "observation"}
)
GROUND_NET_ALIASES = frozenset({"0", "gnd", "ground"})
_TOKEN = re.compile(r"^[^\s\x00]+$")
_REFDES = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def _text(value: object, field: str, *, required: bool = True) -> str:
    if not isinstance(value, str):
        if required:
            raise ValueError(f"{field} must be a string")
        return ""
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} must not be empty")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{field} contains an invalid character")
    return value


def _token(value: object, field: str) -> str:
    value = _text(value, field)
    if not _TOKEN.fullmatch(value):
        raise ValueError(f"{field} must be one SPICE token")
    return value


def _fixture_object(raw: object, index: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"fixtures[{index}] must be an object")
    allowed = {
        "schema_version",
        "id",
        "board_id",
        "kind",
        "net",
        "reference_net",
        "refdes",
        "value",
        "model",
        "quantity",
        "label",
        "notes",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"fixtures[{index}] contains unknown fields: {sorted(unknown)}")
    schema_version = raw.get("schema_version", FIXTURE_SCHEMA_VERSION)
    if schema_version != FIXTURE_SCHEMA_VERSION:
        raise ValueError(f"fixtures[{index}].schema_version must be {FIXTURE_SCHEMA_VERSION}")
    fixture_id = _text(raw.get("id"), f"fixtures[{index}].id")
    board_id = _text(raw.get("board_id"), f"fixtures[{index}].board_id")
    kind = _text(raw.get("kind"), f"fixtures[{index}].kind").casefold()
    if kind not in FIXTURE_KINDS:
        raise ValueError(f"fixtures[{index}].kind must be one of {sorted(FIXTURE_KINDS)}")
    net = _token(raw.get("net"), f"fixtures[{index}].net")
    normalized: dict[str, Any] = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "id": fixture_id,
        "board_id": board_id,
        "kind": kind,
        "net": net,
    }
    if kind in {"voltage_source", "resistor_termination"}:
        reference_net = _token(raw.get("reference_net"), f"fixtures[{index}].reference_net")
        refdes = _text(raw.get("refdes"), f"fixtures[{index}].refdes")
        if not _REFDES.fullmatch(refdes):
            raise ValueError(f"fixtures[{index}].refdes is invalid")
        expected_prefix = "V" if kind == "voltage_source" else "R"
        if refdes[:1].upper() != expected_prefix:
            raise ValueError(
                f"fixtures[{index}].refdes must start with {expected_prefix!r} for {kind}"
            )
        model = raw.get("model")
        if kind == "voltage_source" and model is not None:
            model = _text(model, f"fixtures[{index}].model")
        if kind == "resistor_termination" and model is not None:
            raise ValueError(f"fixtures[{index}] resistor_termination does not accept model")
        if kind == "voltage_source" and model is not None:
            if raw.get("value") is not None:
                raise ValueError(f"fixtures[{index}] voltage_source must use value or model, not both")
            normalized.update({"reference_net": reference_net, "refdes": refdes, "model": model})
        else:
            value = _token(raw.get("value"), f"fixtures[{index}].value")
            normalized.update({"reference_net": reference_net, "refdes": refdes, "value": value})
    elif kind == "ground_reference":
        if net.casefold() not in GROUND_NET_ALIASES:
            raise ValueError(
                f"fixtures[{index}].net must be a ground alias for ground_reference"
            )
    else:
        quantity = _text(raw.get("quantity", "voltage"), f"fixtures[{index}].quantity")
        if quantity.casefold() != "voltage":
            raise ValueError("only voltage observation fixtures are currently supported")
        normalized["quantity"] = "voltage"
        normalized["label"] = _text(raw.get("label", fixture_id), f"fixtures[{index}].label")
    if "notes" in raw:
        normalized["notes"] = _text(raw.get("notes"), f"fixtures[{index}].notes", required=False)
    return normalized


def _board_nets(board: Mapping[str, Any]) -> set[str]:
    raw_nets = board.get("nets", [])
    if not raw_nets and isinstance(board.get("design"), Mapping):
        raw_nets = board["design"].get("nets", [])
    if not isinstance(raw_nets, Sequence) or isinstance(raw_nets, (str, bytes)):
        return set()
    result: set[str] = set()
    for item in raw_nets:
        if isinstance(item, Mapping):
            name = str(item.get("name", "")).strip()
        else:
            name = str(item).strip()
        if name:
            result.add(name)
    return result


def _board_components(board: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = board.get("components", [])
    if not raw and isinstance(board.get("design"), Mapping):
        raw = board["design"].get("components", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def validate_multiboard_fixture_contract(
    artifacts: Mapping[str, Any],
    fixtures: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate explicit fixtures against board-local logical artifacts.

    ``status=valid`` means every declared fixture is structurally safe.  The
    returned ``uncovered_interfaces`` list is intentionally separate: an
    uncovered edge is not silently filled with a guessed source or load.  A
    caller that needs an isolated native run must either declare a fixture for
    each edge or explicitly opt into an uncovered preview.
    """
    if not isinstance(artifacts, Mapping):
        raise ValueError("artifacts must be an object")
    raw_boards = artifacts.get("boards")
    if not isinstance(raw_boards, Sequence) or isinstance(raw_boards, (str, bytes)):
        raise ValueError("artifacts.boards must be a list")
    board_by_id: dict[str, Mapping[str, Any]] = {}
    violations: list[dict[str, Any]] = []
    for board in raw_boards:
        if not isinstance(board, Mapping):
            violations.append({"constraint": "board_object", "message": "board must be an object"})
            continue
        board_id = str(board.get("board_id", "")).strip()
        if not board_id or board_id in board_by_id:
            violations.append({"constraint": "board_ids", "message": "board ids must be unique and non-empty"})
            continue
        board_by_id[board_id] = board
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_refs: dict[str, set[str]] = {
        board_id: {
            str(item.get("refdes", "")).casefold()
            for item in _board_components(board)
            if str(item.get("refdes", "")).strip()
        }
        for board_id, board in board_by_id.items()
    }
    seen_driver_nets: set[tuple[str, str]] = set()
    seen_observation_nets: set[tuple[str, str]] = set()
    declared_terminal_counts: dict[tuple[str, str], int] = defaultdict(int)
    for board_id, board in board_by_id.items():
        for component in _board_components(board):
            raw_nodes = component.get("nodes", [])
            if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
                continue
            for node in raw_nodes:
                node_name = str(node).strip()
                if node_name:
                    declared_terminal_counts[(board_id, node_name.casefold())] += 1
    for index, raw_fixture in enumerate(fixtures):
        try:
            fixture = _fixture_object(raw_fixture, index)
        except ValueError as exc:
            violations.append({"fixture_index": index, "constraint": "fixture_fields", "message": str(exc)})
            continue
        fixture_id = fixture["id"]
        board_id = fixture["board_id"]
        net = fixture["net"]
        if fixture_id in seen_ids:
            violations.append({"fixture": fixture_id, "constraint": "fixture_ids", "message": "fixture ids must be unique"})
        seen_ids.add(fixture_id)
        board = board_by_id.get(board_id)
        if board is None:
            violations.append({"fixture": fixture_id, "constraint": "board_id", "message": f"unknown board {board_id!r}"})
            continue
        nets = _board_nets(board)
        if net not in nets:
            violations.append({"fixture": fixture_id, "constraint": "net", "message": f"net {net!r} is absent from board {board_id!r}"})
        if fixture["kind"] in {"voltage_source", "resistor_termination"}:
            reference_net = fixture["reference_net"]
            if reference_net not in nets:
                violations.append({"fixture": fixture_id, "constraint": "reference_net", "message": f"reference net {reference_net!r} is absent from board {board_id!r}"})
            refdes_key = fixture["refdes"].casefold()
            if refdes_key in seen_refs.setdefault(board_id, set()):
                violations.append({"fixture": fixture_id, "constraint": "refdes", "message": f"refdes {fixture['refdes']!r} collides on board {board_id!r}"})
            seen_refs[board_id].add(refdes_key)
            declared_terminal_counts[(board_id, net.casefold())] += 1
            declared_terminal_counts[(board_id, reference_net.casefold())] += 1
            driver_key = (board_id, net.casefold())
            if fixture["kind"] == "voltage_source" and driver_key in seen_driver_nets:
                violations.append({"fixture": fixture_id, "constraint": "multiple_drivers", "message": f"multiple voltage sources drive {board_id}:{net}"})
            if fixture["kind"] == "voltage_source":
                seen_driver_nets.add(driver_key)
        elif fixture["kind"] == "observation":
            observation_key = (board_id, net.casefold())
            if observation_key in seen_observation_nets:
                violations.append({"fixture": fixture_id, "constraint": "duplicate_observation", "message": f"duplicate voltage observation on {board_id}:{net}"})
            seen_observation_nets.add(observation_key)
        normalized.append(fixture)

    for fixture in normalized:
        if fixture["kind"] != "observation":
            continue
        terminal_count = declared_terminal_counts.get(
            (fixture["board_id"], fixture["net"].casefold()), 0
        )
        if terminal_count < 2:
            violations.append({
                "fixture": fixture["id"],
                "constraint": "observation_anchor",
                "message": (
                    f"observation net {fixture['net']!r} on board {fixture['board_id']!r} "
                    "has no physical wire anchor; add an explicit termination or connector"
                ),
                "terminal_count": terminal_count,
            })

    interface_keys: set[tuple[str, str]] = set()
    for board_id, board in board_by_id.items():
        interfaces = board.get("interfaces", [])
        if not isinstance(interfaces, Sequence) or isinstance(interfaces, (str, bytes)):
            continue
        for interface in interfaces:
            if isinstance(interface, Mapping):
                net = str(interface.get("net", "")).strip()
                if net:
                    interface_keys.add((board_id, net.casefold()))
    covered = {(item["board_id"], item["net"].casefold()) for item in normalized}
    uncovered = [
        {"board_id": board_id, "net": net}
        for board_id, net in sorted(interface_keys)
        if (board_id, net) not in covered
    ]
    by_board: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in normalized:
        by_board[item["board_id"]].append(item)
    return {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "status": "valid" if not violations else "invalid",
        "native_status": "unverified",
        "fixtures": normalized,
        "fixture_count": len(normalized),
        "fixtures_by_board": {board_id: by_board.get(board_id, []) for board_id in board_by_id},
        "covered_interfaces": [
            {"board_id": board_id, "net": net}
            for board_id, net in sorted(interface_keys & covered)
        ],
        "uncovered_interfaces": uncovered,
        "coverage_status": "complete" if not uncovered else "incomplete",
        "violations": violations,
    }


def materialize_multiboard_fixture_artifacts(
    artifacts: Mapping[str, Any],
    fixtures: Sequence[Mapping[str, Any]],
    *,
    allow_uncovered: bool = False,
) -> dict[str, Any]:
    """Add declared fixture components and observations to board designs.

    The default rejects an incomplete cross-board boundary contract.  Setting
    ``allow_uncovered=True`` is useful for a review-only preview, but the
    resulting payload remains ``unverified`` and cannot claim native acceptance.
    """
    contract = validate_multiboard_fixture_contract(artifacts, fixtures)
    if contract["status"] != "valid":
        raise ValueError("multiboard fixture contract is structurally invalid")
    if contract["uncovered_interfaces"] and not allow_uncovered:
        raise ValueError(
            "multiboard fixture contract leaves interfaces uncovered; declare explicit fixtures or set allow_uncovered=True"
        )
    board_results: list[dict[str, Any]] = []
    for board in artifacts["boards"]:
        board_id = str(board.get("board_id", "")).strip()
        raw_design = board.get("design")
        if not isinstance(raw_design, Mapping):
            raise ValueError(f"board {board_id!r} has no structured design for fixture materialization")
        design = CircuitDesign.from_dict(raw_design)
        board_fixtures = contract["fixtures_by_board"].get(board_id, [])
        fixture_components: list[CircuitComponent] = []
        probe_nets: list[str] = []
        comments: list[str] = []
        for fixture in board_fixtures:
            kind = fixture["kind"]
            if kind == "voltage_source":
                fixture_components.append(CircuitComponent(
                    refdes=fixture["refdes"], kind="V",
                    nodes=(fixture["net"], fixture["reference_net"]),
                    value=fixture.get("value"), model=fixture.get("model"),
                    annotations={"role": "explicit multiboard test fixture", "fixture_id": fixture["id"]},
                ))
            elif kind == "resistor_termination":
                fixture_components.append(CircuitComponent(
                    refdes=fixture["refdes"], kind="R",
                    nodes=(fixture["net"], fixture["reference_net"]), value=fixture["value"],
                    annotations={"role": "explicit multiboard test fixture", "fixture_id": fixture["id"]},
                ))
            elif kind == "observation":
                probe_nets.append(fixture["net"])
            comments.append(
                f"* multisim-mcp fixture {fixture['id']} kind={kind} net={fixture['net']}"
            )
        all_components = tuple((*design.components, *fixture_components))
        enriched = CircuitDesign(
            design_id=f"{design.design_id}.fixture-{hashlib.sha256(board_id.encode('utf-8')).hexdigest()[:10]}",
            title=f"{design.title} [explicit test fixtures]",
            components=all_components,
            nets=design.nets,
            parameters=design.parameters,
            model_references=design.model_references,
            annotations={
                **dict(design.annotations),
                "multiboard": {
                    **dict(design.annotations.get("multiboard", {})),
                    "test_fixture_status": "logical-only",
                    "fixture_ids": [item["id"] for item in board_fixtures],
                    "probe_nets": list(dict.fromkeys(probe_nets)),
                },
            },
            # Keep the board preview available while rebuilding so inline
            # model/subcircuit definitions survive the fixture edit.
            source_netlist=design.source_netlist,
            revision=design.revision,
        )
        preview = circuit_design_to_spice(enriched, prefer_source=False)
        if comments:
            lines = preview.rstrip().splitlines()
            end_index = next((index for index, line in enumerate(lines) if line.strip().casefold() == ".end"), len(lines))
            lines[end_index:end_index] = comments
            preview = "\n".join(lines) + "\n"
        enriched = CircuitDesign(
            design_id=enriched.design_id,
            title=enriched.title,
            components=enriched.components,
            nets=enriched.nets,
            parameters=enriched.parameters,
            model_references=enriched.model_references,
            annotations=enriched.annotations,
            source_netlist=preview,
            revision=enriched.revision,
        )
        board_results.append({
            "board_id": board_id,
            "status": "logical-only",
            "verification_status": "unverified",
            "design": enriched.to_dict(),
            "spice_netlist": preview,
            "fixture_ids": [item["id"] for item in board_fixtures],
            "fixture_components": [item.to_dict() for item in fixture_components],
            "probe_nets": list(dict.fromkeys(probe_nets)),
            "interfaces": board.get("interfaces", []),
            "native_project": None,
        })
    payload: dict[str, Any] = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "kind": "multisim-mcp-multiboard-fixture-artifacts",
        "status": "logical-only",
        "verification_status": "unverified",
        "parent_artifact_digest": artifacts.get("artifact_digest"),
        "interface_validation": artifacts.get("interface_validation"),
        "fixture_contract": contract,
        "boards": board_results,
        "native_projects": "pending-per-board-generation-and-acceptance",
    }
    payload["artifact_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def compare_multiboard_interface_observations(
    artifacts: Mapping[str, Any],
    observations: Mapping[str, Mapping[str, float]],
    *,
    nets: Sequence[str] | None = None,
    absolute_tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Compare measured values at both ends of selected board interfaces.

    The values are supplied by the caller from an authoritative backend result
    (for example, Multisim's exported CSV).  This function never treats a
    missing value as zero and never substitutes a command-engine result for a
    native result.  It only performs the explicitly requested numerical
    comparison and returns ``unverified`` when an endpoint is absent.
    """
    if isinstance(absolute_tolerance, bool) or not isinstance(absolute_tolerance, (int, float)) or absolute_tolerance < 0:
        raise ValueError("absolute_tolerance must be a non-negative number")
    contract = validate_multiboard_fixture_contract(artifacts, [])
    if contract["status"] != "valid":
        raise ValueError("artifacts contain an invalid board interface contract")
    interface_validation = artifacts.get("interface_validation")
    if isinstance(interface_validation, Mapping) and interface_validation.get("status") != "valid":
        raise ValueError("artifacts contain an invalid cross-board interface contract")
    if not isinstance(observations, Mapping):
        raise ValueError("observations must be an object keyed by board id")
    selected = None if nets is None else {str(item).strip() for item in nets if str(item).strip()}
    if selected is not None and not selected:
        raise ValueError("nets must contain at least one non-empty net")
    board_by_id = {
        str(board.get("board_id", "")).strip(): board
        for board in artifacts.get("boards", [])
        if isinstance(board, Mapping)
    }
    interface_boards: dict[str, set[str]] = defaultdict(set)
    for board_id, board in board_by_id.items():
        raw_interfaces = board.get("interfaces", [])
        if not isinstance(raw_interfaces, Sequence) or isinstance(raw_interfaces, (str, bytes)):
            continue
        for interface in raw_interfaces:
            if not isinstance(interface, Mapping):
                continue
            net = str(interface.get("net", "")).strip()
            if net and (selected is None or net in selected):
                interface_boards[net].add(board_id)
    comparisons: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    invalid_values: list[dict[str, str]] = []
    for net, board_ids in sorted(interface_boards.items()):
        values: dict[str, float] = {}
        for board_id in sorted(board_ids):
            raw_board = observations.get(board_id, {})
            if not isinstance(raw_board, Mapping) or net not in raw_board:
                missing.append({"board_id": board_id, "net": net})
                continue
            value = raw_board[net]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                invalid_values.append({"board_id": board_id, "net": net})
                continue
            values[board_id] = float(value)
        if len(values) < len(board_ids):
            continue
        baseline_board = sorted(values)[0]
        baseline = values[baseline_board]
        differences = {
            board_id: abs(value - baseline)
            for board_id, value in values.items()
            if board_id != baseline_board
        }
        maximum_difference = max(differences.values(), default=0.0)
        comparisons.append({
            "net": net,
            "boards": sorted(values),
            "values": values,
            "baseline_board": baseline_board,
            "max_absolute_difference": maximum_difference,
            "absolute_tolerance": float(absolute_tolerance),
            "passed": maximum_difference <= float(absolute_tolerance),
        })
    if invalid_values:
        status = "invalid"
    elif missing:
        status = "unverified"
    elif not comparisons:
        status = "unverified"
    else:
        status = "pass" if all(item["passed"] for item in comparisons) else "fail"
    return {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "kind": "multisim-mcp-multiboard-interface-comparison",
        "status": status,
        "verification_status": "unverified" if status in {"unverified", "invalid"} else "comparison-only",
        "comparisons": comparisons,
        "missing": missing,
        "invalid_values": invalid_values,
        "absolute_tolerance": float(absolute_tolerance),
    }


__all__ = [
    "FIXTURE_KINDS",
    "FIXTURE_SCHEMA_VERSION",
    "GROUND_NET_ALIASES",
    "compare_multiboard_interface_observations",
    "materialize_multiboard_fixture_artifacts",
    "validate_multiboard_fixture_contract",
]
