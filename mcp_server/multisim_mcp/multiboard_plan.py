"""Deterministic multi-board partition planning from a logical netlist."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Mapping, Sequence
from itertools import product


def _positive_limit(board: Mapping[str, Any], field: str, board_id: str) -> int | None:
    value = board.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"board {board_id!r} {field} must be a positive integer")
    return value


def plan_multiboard_partition(
    components: Sequence[Mapping[str, Any]],
    boards: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not boards:
        raise ValueError("boards must not be empty")
    if any(not isinstance(board, Mapping) for board in boards):
        raise ValueError("each board must be an object")
    board_ids = [str(board.get("id", "")).strip() for board in boards]
    if any(not item for item in board_ids) or len(set(board_ids)) != len(board_ids):
        raise ValueError("boards require unique non-empty ids")
    limits = {
        board_id: {
            "max_components": _positive_limit(board, "max_components", board_id),
            "max_connector_pins": _positive_limit(board, "max_connector_pins", board_id),
        }
        for board_id, board in zip(board_ids, boards)
    }
    assignment: dict[str, str] = {}
    nets: dict[str, set[str]] = defaultdict(set)
    for component in components:
        refdes = str(component.get("refdes", "")).strip()
        board = str(component.get("board", board_ids[0])).strip()
        nodes = component.get("nodes", [])
        if not refdes or board not in board_ids or not isinstance(nodes, Sequence) or isinstance(nodes, (str, bytes)):
            raise ValueError("each component requires refdes, valid board and node list")
        if refdes in assignment:
            raise ValueError(f"duplicate component refdes: {refdes}")
        assignment[refdes] = board
        for node in nodes:
            node_name = str(node).strip()
            if not node_name:
                raise ValueError(f"component {refdes!r} contains an empty node")
            nets[node_name].add(board)
    crossings = [
        {"net": net, "boards": sorted(owners), "connector_required": True}
        for net, owners in sorted(nets.items()) if len(owners) > 1
    ]
    connectors: list[dict[str, Any]] = []
    for index, crossing in enumerate(crossings, 1):
        connectors.append({
            "id": f"J{index}", "net": crossing["net"],
            "boards": crossing["boards"], "pin": index,
            "signal_type": "ground" if crossing["net"].casefold() in {"0", "gnd", "ground"} else "signal",
        })
    connector_pins_by_board = {board_id: 0 for board_id in board_ids}
    interfaces_by_board = {board_id: [] for board_id in board_ids}
    for connector in connectors:
        for board_id in connector["boards"]:
            connector_pins_by_board[board_id] += 1
            interfaces_by_board[board_id].append({
                "connector": connector["id"],
                "pin": connector["pin"],
                "net": connector["net"],
                "signal_type": connector["signal_type"],
                "peer_boards": [peer for peer in connector["boards"] if peer != board_id],
            })
    component_counts = {
        board_id: sum(value == board_id for value in assignment.values())
        for board_id in board_ids
    }
    violations: list[dict[str, Any]] = []
    for board_id in board_ids:
        board_limit = limits[board_id]
        if board_limit["max_components"] is not None and component_counts[board_id] > board_limit["max_components"]:
            violations.append({
                "board": board_id,
                "constraint": "max_components",
                "actual": component_counts[board_id],
                "limit": board_limit["max_components"],
            })
        if board_limit["max_connector_pins"] is not None and connector_pins_by_board[board_id] > board_limit["max_connector_pins"]:
            violations.append({
                "board": board_id,
                "constraint": "max_connector_pins",
                "actual": connector_pins_by_board[board_id],
                "limit": board_limit["max_connector_pins"],
            })
    return {
        "schema_version": 1,
        "boards": [{
            "id": board_id,
            "component_count": component_counts[board_id],
            "connector_pin_count": connector_pins_by_board[board_id],
            "limits": limits[board_id],
        }
                   for board_id in board_ids],
        "component_assignment": assignment,
        "cross_board_nets": crossings,
        "connectors": connectors,
        "connector_count": len(connectors),
        "connector_pins_by_board": connector_pins_by_board,
        "board_interfaces": interfaces_by_board,
        "interface_constraints": [{"connector": item["id"], "pin": item["pin"],
                                   "net": item["net"], "boards": item["boards"]} for item in connectors],
        "feasible": not violations,
        "violations": violations,
        "status": "planned" if not violations else "infeasible",
    }


def score_multiboard_partition(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Score a partition deterministically; lower cost is better."""
    connectors = list(plan.get("connectors", []))
    signal = sum(item.get("signal_type") == "signal" for item in connectors)
    ground = sum(item.get("signal_type") == "ground" for item in connectors)
    # Ground crossings are cheaper than signal crossings, but still consume a pin.
    constraint_penalty = len(list(plan.get("violations", []))) * 1_000_000
    cost = len(connectors) * 10 + signal * 6 + ground * 2 + constraint_penalty
    return {"connector_count": len(connectors), "signal_crossings": signal,
            "ground_crossings": ground, "constraint_violations": len(list(plan.get("violations", []))),
            "feasible": plan.get("feasible") is not False and not plan.get("violations"),
            "cost": cost,
            "objective": "minimize connector count and cross-board signal cost"}


def materialize_multiboard_partition(
    components: Sequence[Mapping[str, Any]],
    partition: Mapping[str, Any],
) -> dict[str, Any]:
    """Materialize a feasible partition into deterministic per-board logical artifacts.

    The returned artifacts are a board-local view of the source components and
    nets.  They deliberately do not contain ``.ms14`` files or claim native
    verification; each board must still be built, reopened, and simulated by
    the Multisim backend in a later stage.
    """
    if not isinstance(partition, Mapping):
        raise ValueError("partition must be an object")
    if partition.get("feasible") is not True or partition.get("violations"):
        raise ValueError("only feasible partitions can be materialized")
    raw_boards = partition.get("boards")
    assignment = partition.get("component_assignment")
    interfaces = partition.get("board_interfaces")
    connectors = partition.get("connectors")
    if not isinstance(raw_boards, Sequence) or isinstance(raw_boards, (str, bytes)):
        raise ValueError("partition boards must be a list")
    if not isinstance(assignment, Mapping) or not isinstance(interfaces, Mapping):
        raise ValueError("partition assignment and interfaces are required")
    if not isinstance(connectors, Sequence) or isinstance(connectors, (str, bytes)):
        raise ValueError("partition connectors must be a list")
    board_ids = [str(item.get("id", "")).strip() for item in raw_boards if isinstance(item, Mapping)]
    if len(board_ids) != len(raw_boards) or any(not item for item in board_ids):
        raise ValueError("partition boards require non-empty ids")
    if len(set(board_ids)) != len(board_ids):
        raise ValueError("partition boards require unique ids")

    source_by_ref: dict[str, Mapping[str, Any]] = {}
    for component in components:
        if not isinstance(component, Mapping):
            raise ValueError("each component must be an object")
        refdes = str(component.get("refdes", "")).strip()
        if not refdes or refdes in source_by_ref:
            raise ValueError("components require unique non-empty refdes")
        if refdes not in assignment:
            raise ValueError(f"partition is missing component assignment for {refdes!r}")
        assigned_board = str(assignment[refdes]).strip()
        if assigned_board not in board_ids:
            raise ValueError(f"component {refdes!r} has invalid partition board")
        nodes = component.get("nodes", [])
        if not isinstance(nodes, Sequence) or isinstance(nodes, (str, bytes)):
            raise ValueError(f"component {refdes!r} nodes must be a list")
        source_by_ref[refdes] = component
    if set(assignment) != set(source_by_ref):
        raise ValueError("partition assignment does not match components")

    connector_by_net = {
        str(item.get("net", "")): dict(item)
        for item in connectors
        if isinstance(item, Mapping) and str(item.get("net", "")).strip()
    }
    board_artifacts: list[dict[str, Any]] = []
    for board_id in board_ids:
        board_components: list[dict[str, Any]] = []
        board_nets: set[str] = set()
        for refdes, component in source_by_ref.items():
            if str(assignment[refdes]).strip() != board_id:
                continue
            nodes = [str(node).strip() for node in component.get("nodes", [])]
            board_components.append(dict(component, refdes=refdes, board=board_id, nodes=nodes))
            board_nets.update(nodes)
        board_net_records = []
        for net in sorted(board_nets):
            connector = connector_by_net.get(net)
            board_net_records.append({
                "name": net,
                "scope": "cross-board" if connector else "board-local",
                "connector": connector.get("id") if connector else None,
                "pin": connector.get("pin") if connector else None,
                "peer_boards": [
                    peer for peer in connector.get("boards", []) if peer != board_id
                ] if connector else [],
            })
        board_interfaces = [dict(item) for item in interfaces.get(board_id, [])]
        board_artifacts.append({
            "schema_version": 1,
            "kind": "multisim-mcp-board-logical-artifact",
            "board_id": board_id,
            "status": "logical-only",
            "verification_status": "unverified",
            "components": board_components,
            "nets": board_net_records,
            "interfaces": board_interfaces,
            "native_project": None,
        })

    payload = {
        "schema_version": 1,
        "kind": "multisim-mcp-multiboard-logical-artifacts",
        "status": "logical-only",
        "verification_status": "unverified",
        "source": "feasible-multiboard-partition",
        "boards": board_artifacts,
        "native_projects": "pending-per-board-generation-and-acceptance",
    }
    payload["interface_validation"] = validate_multiboard_logical_artifacts(payload)
    payload["artifact_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def validate_multiboard_logical_artifacts(
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate board-local interface symmetry without claiming native validity."""
    violations: list[dict[str, Any]] = []
    raw_boards = artifacts.get("boards")
    if not isinstance(raw_boards, Sequence) or isinstance(raw_boards, (str, bytes)):
        return {"status": "invalid", "native_status": "unverified", "violations": [
            {"constraint": "boards", "message": "boards must be a list"},
        ]}
    board_ids = [
        str(item.get("board_id", "")).strip()
        for item in raw_boards
        if isinstance(item, Mapping)
    ]
    if len(board_ids) != len(raw_boards) or len(set(board_ids)) != len(board_ids):
        violations.append({"constraint": "board_ids", "message": "board ids must be unique and non-empty"})
    board_by_id = {
        str(item.get("board_id", "")).strip(): item
        for item in raw_boards
        if isinstance(item, Mapping)
    }
    interface_index: dict[tuple[str, str], Mapping[str, Any]] = {}
    for board_id, board in board_by_id.items():
        components = board.get("components", [])
        nets = board.get("nets", [])
        interfaces = board.get("interfaces", [])
        if not isinstance(components, Sequence) or isinstance(components, (str, bytes)):
            violations.append({"board": board_id, "constraint": "components", "message": "components must be a list"})
        elif not components:
            violations.append({"board": board_id, "constraint": "non_empty_board", "message": "board has no components"})
        net_names = {
            str(item.get("name", "")).strip()
            for item in nets
            if isinstance(item, Mapping)
        } if isinstance(nets, Sequence) and not isinstance(nets, (str, bytes)) else set()
        if not isinstance(interfaces, Sequence) or isinstance(interfaces, (str, bytes)):
            violations.append({"board": board_id, "constraint": "interfaces", "message": "interfaces must be a list"})
            continue
        for interface in interfaces:
            if not isinstance(interface, Mapping):
                violations.append({"board": board_id, "constraint": "interface_object", "message": "interface must be an object"})
                continue
            connector = str(interface.get("connector", "")).strip()
            net = str(interface.get("net", "")).strip()
            pin = interface.get("pin")
            peers = interface.get("peer_boards", [])
            key = (connector, str(pin))
            if not connector or not net or not isinstance(pin, int) or isinstance(pin, bool):
                violations.append({"board": board_id, "constraint": "interface_fields", "message": "connector, net and integer pin are required"})
                continue
            if net not in net_names:
                violations.append({"board": board_id, "connector": connector, "constraint": "interface_net", "message": f"interface net {net!r} is absent from board nets"})
            if not isinstance(peers, Sequence) or isinstance(peers, (str, bytes)) or any(str(peer) not in board_by_id for peer in peers):
                violations.append({"board": board_id, "connector": connector, "constraint": "peer_boards", "message": "interface peers must reference declared boards"})
            interface_index[(board_id, connector)] = interface
    for (board_id, connector), interface in interface_index.items():
        for peer in interface.get("peer_boards", []):
            peer_id = str(peer)
            counterpart = interface_index.get((peer_id, connector))
            if counterpart is None:
                violations.append({"board": board_id, "connector": connector, "constraint": "interface_symmetry", "message": f"missing counterpart on {peer_id!r}"})
                continue
            if counterpart.get("net") != interface.get("net") or counterpart.get("pin") != interface.get("pin"):
                violations.append({"board": board_id, "connector": connector, "constraint": "interface_symmetry", "message": f"counterpart on {peer_id!r} disagrees"})
            if board_id not in [str(item) for item in counterpart.get("peer_boards", [])]:
                violations.append({"board": board_id, "connector": connector, "constraint": "interface_symmetry", "message": f"counterpart on {peer_id!r} does not point back"})
    return {
        "status": "valid" if not violations else "invalid",
        "native_status": "unverified",
        "violations": violations,
    }


def materialize_circuit_design_partition(
    design: Any,
    partition: Mapping[str, Any],
) -> dict[str, Any]:
    """Create board-local :class:`CircuitDesign` and SPICE preview artifacts.

    This is the bridge between the generic engineering planner and the EDA
    core.  It keeps only components assigned to each board, retains inline
    model definitions from the source design, and annotates cross-board nets.
    The previews are not native Multisim projects and remain unverified.
    """
    from .eda_core import CircuitDesign
    from .spice_adapter import circuit_design_to_spice

    if not isinstance(design, CircuitDesign):
        raise ValueError("design must be CircuitDesign")
    component_payload = [component.to_dict() for component in design.components]
    logical = materialize_multiboard_partition(component_payload, partition)
    if logical["interface_validation"].get("status") != "valid":
        raise ValueError("multiboard interface contract is structurally invalid")
    assignment = partition["component_assignment"]
    board_results: list[dict[str, Any]] = []
    for board_artifact in logical["boards"]:
        board_id = board_artifact["board_id"]
        selected = tuple(
            component
            for component in design.components
            if str(assignment[component.refdes]).strip() == board_id
        )
        if not selected:
            raise ValueError(f"board {board_id!r} has no assigned components")
        selected_nets = tuple(dict.fromkeys(
            node for component in selected for node in component.nodes
        ))
        board_digest = hashlib.sha256(board_id.encode("utf-8")).hexdigest()[:12]
        board_design = CircuitDesign(
            design_id=f"{design.design_id}.board-{board_digest}",
            title=f"{design.title} [{board_id}]",
            components=selected,
            nets=selected_nets,
            parameters=design.parameters,
            model_references=design.model_references,
            annotations={
                **dict(design.annotations),
                "multiboard": {
                    "parent_design_id": design.design_id,
                    "board_id": board_id,
                    "status": "logical-only",
                    "verification_status": "unverified",
                    "interfaces": board_artifact["interfaces"],
                },
            },
            source_netlist=design.source_netlist,
            revision=design.revision,
        )
        preview = circuit_design_to_spice(board_design, prefer_source=False)
        interface_comments = [
            "* multisim-mcp external interface "
            f"{item['connector']} pin={item['pin']} net={item['net']} "
            f"peers={','.join(item['peer_boards'])}"
            for item in board_artifact["interfaces"]
        ]
        if interface_comments:
            lines = preview.rstrip().splitlines()
            end_index = next(
                (index for index, line in enumerate(lines) if line.strip().lower() == ".end"),
                len(lines),
            )
            lines[end_index:end_index] = interface_comments
            preview = "\n".join(lines) + "\n"
        # The parent source is only used above to recover inline model
        # definitions.  Persist the partitioned preview so a later default
        # ``prefer_source=True`` serialization cannot restore the full design.
        board_design = CircuitDesign(
            design_id=board_design.design_id,
            title=board_design.title,
            components=board_design.components,
            nets=board_design.nets,
            parameters=board_design.parameters,
            model_references=board_design.model_references,
            annotations=board_design.annotations,
            source_netlist=preview,
            revision=board_design.revision,
        )
        board_results.append({
            "board_id": board_id,
            "status": "logical-only",
            "verification_status": "unverified",
            "design": board_design.to_dict(),
            "spice_netlist": preview,
            "interfaces": board_artifact["interfaces"],
            "native_project": None,
        })
    payload = {
        "schema_version": 1,
        "kind": "multisim-mcp-multiboard-design-artifacts",
        "status": "logical-only",
        "verification_status": "unverified",
        "parent_design_id": design.design_id,
        "boards": board_results,
        "native_projects": "pending-per-board-generation-and-acceptance",
        "interface_validation": logical["interface_validation"],
    }
    payload["artifact_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def rank_partition_candidates(
    components: Sequence[Mapping[str, Any]], boards: Sequence[Mapping[str, Any]],
    *, max_candidates: int = 256,
) -> list[dict[str, Any]]:
    """Enumerate bounded assignments and return lowest-cost plans first."""
    if max_candidates <= 0:
        raise ValueError("max_candidates must be positive")
    if any(not isinstance(item, Mapping) for item in boards):
        raise ValueError("each board must be an object")
    board_ids = [str(item.get("id", "")).strip() for item in boards]
    if any(not board_id for board_id in board_ids) or len(set(board_ids)) != len(board_ids):
        raise ValueError("boards require unique non-empty ids")
    refs = [str(item["refdes"]) for item in components]
    if len(refs) > 10:
        raise ValueError("enumeration is limited to 10 components; use a solver for larger designs")
    fixed_boards: dict[int, str] = {}
    for index, component in enumerate(components):
        if not isinstance(component, Mapping):
            raise ValueError("each component must be an object")
        if "board" in component:
            board_id = str(component.get("board", "")).strip()
            if board_id not in board_ids:
                raise ValueError(f"component {refs[index]!r} has invalid fixed board {board_id!r}")
            fixed_boards[index] = board_id
    variable_indices = [index for index in range(len(refs)) if index not in fixed_boards]
    candidates: list[dict[str, Any]] = []
    for variable_assignment in product(board_ids, repeat=len(variable_indices)):
        selected = dict(zip(variable_indices, variable_assignment))
        selected.update(fixed_boards)
        assigned = [dict(item, board=selected[index]) for index, item in enumerate(components)]
        plan = plan_multiboard_partition(assigned, boards)
        plan["score"] = score_multiboard_partition(plan)
        candidates.append(plan)
    candidates.sort(key=lambda item: (
        not item["score"]["feasible"], item["score"]["cost"],
        item["connector_count"], str(item["component_assignment"]),
    ))
    return candidates[:max_candidates]


__all__ = [
    "materialize_circuit_design_partition",
    "materialize_multiboard_partition",
    "plan_multiboard_partition",
    "score_multiboard_partition",
    "rank_partition_candidates",
    "validate_multiboard_logical_artifacts",
]
