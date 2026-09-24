"""Auditable record for one AI-orchestrated engineering run."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def build_engineering_run(
    *,
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
    action_plan: Mapping[str, Any],
    runtime: Mapping[str, Any] | None = None,
    results: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "kind": "multisim-mcp-engineering-run",
        "request": dict(request),
        "plan_digest": plan.get("plan_digest"),
        "action_plan": dict(action_plan),
        "runtime": dict(runtime or {}),
        "results": dict(results or {}),
        "status": ("planned" if results is None else
                   "completed" if results.get("success") is True and not results.get("restore_errors") else "failed"),
        "verification_status": "unverified",
    }
    payload["run_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


__all__ = ["build_engineering_run"]
