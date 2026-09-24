"""Deterministic engineering-plan scaffold from a validated request."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .engineering_request import validate_engineering_request
from .multiboard_plan import rank_partition_candidates


def build_engineering_plan(request: Mapping[str, Any]) -> dict[str, Any]:
    normalized = validate_engineering_request(request)
    unresolved = [
        item.get("text", "") for item in normalized["constraints"]
        if isinstance(item, Mapping) and item.get("requires_confirmation") is True
    ]
    plan = {
        "schema_version": 1,
        "kind": "multisim-mcp-engineering-plan",
        "request": normalized,
        "boards": [
            {"id": board["id"], "role": board.get("role", "secondary"), "status": "planned"}
            for board in normalized["boards"]
        ],
        "design_stages": [
            "requirements_review",
            "component_resolution",
            "topology_generation",
            "layout_and_routing",
            "erc_drc",
            "multisim_roundtrip",
            "simulation",
            "optimization",
            "report_export",
        ],
        "experiments": normalized["experiments"] or [{"type": "op", "status": "needs-definition"}],
        "objectives": normalized["objectives"],
        "unresolved_questions": unresolved,
    }
    if normalized["components"] and len(normalized["boards"]) > 1:
        plan["multiboard_candidates"] = rank_partition_candidates(
            normalized["components"], normalized["boards"], max_candidates=32)
    else:
        plan["multiboard_candidates"] = []
    unsigned = dict(plan)
    plan["plan_digest"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return plan


__all__ = ["build_engineering_plan"]
