"""Deterministic engineering-plan scaffold from a validated request."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .engineering_request import validate_engineering_request
from .multiboard_fixtures import validate_multiboard_fixture_contract
from .multiboard_plan import materialize_multiboard_partition, rank_partition_candidates


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
        for candidate in plan["multiboard_candidates"]:
            if candidate.get("feasible") is True:
                candidate["logical_artifacts"] = materialize_multiboard_partition(
                    normalized["components"], candidate)
                structural_status = candidate["logical_artifacts"]["interface_validation"]["status"]
                fixtures = normalized.get("fixtures", [])
                if fixtures:
                    fixture_contract = validate_multiboard_fixture_contract(
                        candidate["logical_artifacts"], fixtures
                    )
                    candidate["fixture_contract"] = fixture_contract
                    if fixture_contract["status"] != "valid":
                        structural_status = "invalid-fixture-contract"
                    elif fixture_contract["coverage_status"] != "complete":
                        structural_status = "incomplete-fixture-coverage"
                candidate["structural_status"] = structural_status
                candidate["structurally_ready"] = structural_status == "valid"
            else:
                candidate["structural_status"] = "blocked-by-constraints"
                candidate["structurally_ready"] = False
        plan["recommended_multiboard_candidate"] = next(
            (
                index
                for index, candidate in enumerate(plan["multiboard_candidates"])
                if candidate.get("structurally_ready") is True
            ),
            None,
        )
    else:
        plan["multiboard_candidates"] = []
        plan["recommended_multiboard_candidate"] = None
    unsigned = dict(plan)
    plan["plan_digest"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return plan


def select_multiboard_engineering_candidate(
    plan: Mapping[str, Any],
    candidate_index: int | None = None,
) -> dict[str, Any]:
    """Lock one structurally ready candidate without starting native execution."""
    if not isinstance(plan, Mapping):
        raise ValueError("plan must be an object")
    request = plan.get("request")
    if not isinstance(request, Mapping):
        raise ValueError("plan.request is required")
    rebuilt = build_engineering_plan(request)
    if rebuilt != dict(plan):
        raise ValueError("engineering plan or digest does not match request")
    candidates = rebuilt.get("multiboard_candidates", [])
    if not candidates:
        raise ValueError("plan has no multiboard candidates")
    if candidate_index is None:
        candidate_index = rebuilt.get("recommended_multiboard_candidate")
    if isinstance(candidate_index, bool) or not isinstance(candidate_index, int):
        raise ValueError("candidate_index must be an integer")
    if not 0 <= candidate_index < len(candidates):
        raise ValueError("candidate_index is outside the candidate list")
    candidate = candidates[candidate_index]
    if candidate.get("structurally_ready") is not True:
        raise ValueError("candidate is not structurally ready for native generation")
    selected = {
        "schema_version": 1,
        "kind": "multisim-mcp-selected-multiboard-candidate",
        "state": "selected",
        "source_plan_digest": rebuilt["plan_digest"],
        "candidate_index": candidate_index,
        "candidate": candidate,
    }
    selected["selected_plan_digest"] = hashlib.sha256(
        json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    selected["selection_digest"] = hashlib.sha256(
        json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return selected


__all__ = ["build_engineering_plan", "select_multiboard_engineering_candidate"]
