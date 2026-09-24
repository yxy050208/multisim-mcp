"""Structured engineering request contract for natural-language workflows."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def validate_engineering_request(request: Mapping[str, Any]) -> dict[str, Any]:
    if request.get("schema_version") != 1:
        raise ValueError("engineering request schema_version must be 1")
    title = request.get("title")
    application = request.get("application")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("engineering request title is required")
    if not isinstance(application, str) or not application.strip():
        raise ValueError("engineering request application is required")
    constraints = request.get("constraints", [])
    objectives = request.get("objectives", [])
    boards = request.get("boards", [{"id": "main", "role": "primary"}])
    experiments = request.get("experiments", [])
    components = request.get("components", [])
    for name, value in (("constraints", constraints), ("objectives", objectives), ("boards", boards), ("experiments", experiments), ("components", components)):
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise ValueError(f"{name} must be a list")
    normalized_boards = []
    seen: set[str] = set()
    for board in boards:
        if not isinstance(board, Mapping) or not isinstance(board.get("id"), str) or not board["id"].strip():
            raise ValueError("each board requires a non-empty id")
        board_id = board["id"].strip()
        if board_id in seen:
            raise ValueError(f"duplicate board id: {board_id}")
        seen.add(board_id)
        normalized_boards.append(dict(board, id=board_id))
    normalized_objectives = []
    for item in objectives:
        if not isinstance(item, Mapping) or not isinstance(item.get("metric"), str):
            raise ValueError("each objective requires a metric")
        direction = str(item.get("direction", "target")).lower()
        if direction not in {"minimize", "maximize", "target"}:
            raise ValueError("objective direction must be minimize, maximize, or target")
        if direction == "target" and (isinstance(item.get("target"), bool) or not isinstance(item.get("target"), (int, float)) or not math.isfinite(float(item["target"]))):
            raise ValueError("target objectives require a finite numeric target")
        normalized_objectives.append(dict(item, direction=direction))
    normalized_components = []
    for item in components:
        if not isinstance(item, Mapping) or not isinstance(item.get("refdes"), str) or not item["refdes"].strip():
            raise ValueError("each component requires a non-empty refdes")
        nodes = item.get("nodes", [])
        if not isinstance(nodes, Sequence) or isinstance(nodes, (str, bytes)):
            raise ValueError("each component nodes must be a list")
        normalized_components.append(dict(item, refdes=item["refdes"].strip(), nodes=[str(node) for node in nodes]))
    return {
        "schema_version": 1,
        "title": title.strip(),
        "application": application.strip(),
        "constraints": [dict(item) if isinstance(item, Mapping) else {"text": str(item)} for item in constraints],
        "objectives": normalized_objectives,
        "boards": normalized_boards,
        "experiments": [dict(item) if isinstance(item, Mapping) else {"description": str(item)} for item in experiments],
        "components": normalized_components,
    }


__all__ = ["validate_engineering_request"]
