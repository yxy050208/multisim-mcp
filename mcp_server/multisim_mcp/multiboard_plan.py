"""Deterministic multi-board partition planning from a logical netlist."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence
from itertools import product


def plan_multiboard_partition(
    components: Sequence[Mapping[str, Any]],
    boards: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not boards:
        raise ValueError("boards must not be empty")
    board_ids = [str(board.get("id", "")).strip() for board in boards]
    if any(not item for item in board_ids) or len(set(board_ids)) != len(board_ids):
        raise ValueError("boards require unique non-empty ids")
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
            nets[str(node)].add(board)
    crossings = [
        {"net": net, "boards": sorted(owners), "connector_required": True}
        for net, owners in sorted(nets.items()) if len(owners) > 1
    ]
    connectors: list[dict[str, Any]] = []
    for index, crossing in enumerate(crossings, 1):
        connectors.append({
            "id": f"J{index}", "net": crossing["net"],
            "boards": crossing["boards"], "pin": index,
            "signal_type": "ground" if crossing["net"] in {"0", "gnd", "ground"} else "signal",
        })
    return {
        "schema_version": 1,
        "boards": [{"id": board_id, "component_count": sum(value == board_id for value in assignment.values())}
                   for board_id in board_ids],
        "component_assignment": assignment,
        "cross_board_nets": crossings,
        "connectors": connectors,
        "connector_count": len(connectors),
        "interface_constraints": [{"connector": item["id"], "pin": item["pin"],
                                   "net": item["net"], "boards": item["boards"]} for item in connectors],
        "status": "planned",
    }


def score_multiboard_partition(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Score a partition deterministically; lower cost is better."""
    connectors = list(plan.get("connectors", []))
    signal = sum(item.get("signal_type") == "signal" for item in connectors)
    ground = sum(item.get("signal_type") == "ground" for item in connectors)
    # Ground crossings are cheaper than signal crossings, but still consume a pin.
    cost = len(connectors) * 10 + signal * 6 + ground * 2
    return {"connector_count": len(connectors), "signal_crossings": signal,
            "ground_crossings": ground, "cost": cost,
            "objective": "minimize connector count and cross-board signal cost"}


def rank_partition_candidates(
    components: Sequence[Mapping[str, Any]], boards: Sequence[Mapping[str, Any]],
    *, max_candidates: int = 256,
) -> list[dict[str, Any]]:
    """Enumerate bounded assignments and return lowest-cost plans first."""
    if max_candidates <= 0:
        raise ValueError("max_candidates must be positive")
    board_ids = [str(item["id"]) for item in boards]
    refs = [str(item["refdes"]) for item in components]
    if len(refs) > 10:
        raise ValueError("enumeration is limited to 10 components; use a solver for larger designs")
    candidates: list[dict[str, Any]] = []
    for assignment in product(board_ids, repeat=len(refs)):
        assigned = [dict(item, board=assignment[index]) for index, item in enumerate(components)]
        plan = plan_multiboard_partition(assigned, boards)
        plan["score"] = score_multiboard_partition(plan)
        candidates.append(plan)
    candidates.sort(key=lambda item: (item["score"]["cost"], item["connector_count"], str(item["component_assignment"])))
    return candidates[:max_candidates]


__all__ = ["plan_multiboard_partition", "score_multiboard_partition", "rank_partition_candidates"]
