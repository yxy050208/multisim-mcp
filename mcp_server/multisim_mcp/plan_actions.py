"""Convert an approved engineering plan into a safe native action skeleton."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .engineering_planner import build_engineering_plan
from .native_action_plan import validate_native_action_plan


def build_action_plan(
    plan: Mapping[str, Any],
    *,
    project_path: str,
    output_directory: str,
    outputs: Sequence[str] | None = None,
    parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an execution skeleton; component selection remains explicit."""
    normalized = build_engineering_plan(plan["request"] if "request" in plan else plan)
    if normalized != dict(plan):
        raise ValueError("engineering plan or digest does not match request")
    if normalized["unresolved_questions"]:
        raise ValueError("engineering plan has unresolved requirements")
    if len(normalized["boards"]) != 1:
        raise ValueError("execution currently supports exactly one board")
    if not normalized["request"]["experiments"]:
        raise ValueError("define experiments before executing a plan")
    actions: list[dict[str, Any]] = [{"op": "open_project", "path": project_path}]
    for refdes, value in (parameters or {}).items():
        actions.append({"op": "set_component_value", "refdes": refdes, "value": value})
    for experiment in normalized["experiments"]:
        if set(experiment) - {"type", "commands", "outputs", "timeout", "max_points"}:
            raise ValueError("experiment contains unsupported fields")
        analysis = experiment.get("type")
        signals = experiment.get("outputs", outputs)
        action = {"op": "run_analysis", "analysis": analysis, "outputs": signals,
                  "commands": experiment.get("commands", "op" if analysis == "op" else "")}
        for field in ("timeout", "max_points"):
            if field in experiment:
                action[field] = experiment[field]
        actions.append(action)
        if isinstance(signals, list):
            actions.extend({"op": "measure", "signal": signal} for signal in signals)
    actions.append({"op": "export_artifacts", "directory": output_directory})
    return validate_native_action_plan({"schema_version": 1, "actions": actions})


__all__ = ["build_action_plan"]
