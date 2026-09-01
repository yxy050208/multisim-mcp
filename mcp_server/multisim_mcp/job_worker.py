"""Isolated subprocess entry point for one persistent experiment job."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    request_path = Path(sys.argv[1]).resolve()
    with request_path.open("r", encoding="utf-8") as handle:
        request: dict[str, Any] = json.load(handle)
    progress_path = Path(request["progress_path"]).resolve()
    result_path = Path(request["result_path"]).resolve()
    cancel_path = Path(request["cancel_path"]).resolve()
    spec = dict(request["spec"])
    parent_pid = int(request.get("parent_pid", 0))
    heartbeat_state: dict[str, Any] = {"at": 0.0, "stage": None, "progress": None}

    if parent_pid > 0:
        from multisim_mcp.job_engine import _pid_alive

        def watch_parent() -> None:
            while True:
                time.sleep(0.5)
                if not _pid_alive(parent_pid):
                    os._exit(70)

        threading.Thread(
            target=watch_parent,
            name="multisim-job-parent-watch",
            daemon=True,
        ).start()

    def checkpoint(stage: str, progress: int, message: str) -> None:
        if cancel_path.exists():
            raise InterruptedError("Experiment cancellation requested")
        now = time.monotonic()
        changed = (
            stage != heartbeat_state["stage"]
            or progress != heartbeat_state["progress"]
        )
        if changed or now - float(heartbeat_state["at"]) >= 1.0:
            _atomic_json(
                progress_path,
                {"stage": stage, "progress": progress, "message": message},
            )
            heartbeat_state.update(at=now, stage=stage, progress=progress)

    try:
        # Importing here keeps protocol introspection independent from worker startup.
        from multisim_mcp.server import (
            _design_optimization_service,
            _experiment_application_service,
            _global_optimization_service,
            _run_circuit_experiment_impl,
            _run_experiment_sweep_impl,
        )
        from multisim_mcp.eda_core import CircuitDesign
        from multisim_mcp.simulation_approvals import (
            build_experiment_approval_provenance,
            validate_simulation_plan_approval,
        )

        if spec.get("job_kind") == "autonomous_correction":
            from multisim_mcp.autonomous_correction import (
                AutonomousDesignCorrectionService,
                ModelRepairPlanner,
            )
            from multisim_mcp.model_provider import ModelProviderRegistry

            registry = ModelProviderRegistry.from_config(dict(spec["provider_config"]))
            planner = ModelRepairPlanner(
                registry,
                provider_id=spec.get("provider"),
                fallback_provider_ids=tuple(spec.get("fallback_providers", [])),
                allow_failover=bool(spec.get("allow_failover", False)),
                timeout=float(spec.get("model_timeout", 60.0)),
            )
            result = AutonomousDesignCorrectionService(
                _experiment_application_service(), planner
            ).run(
                CircuitDesign.from_dict(spec["design"]),
                dict(spec["autonomous_correction_spec"]),
                str(spec["output_dir"]),
                timeout_per_experiment=float(
                    spec.get("timeout_per_experiment", 120.0)
                ),
                max_points=int(spec.get("max_points", 2000)),
                checkpoint=checkpoint,
                cancel_requested=cancel_path.exists,
                resume=True,
            )
            if result.get("status") == "cancelled":
                raise InterruptedError("Autonomous correction cancellation requested")
        elif spec.get("job_kind") == "global_optimization":
            result = _global_optimization_service().run(
                CircuitDesign.from_dict(spec["design"]),
                dict(spec["global_optimization_spec"]),
                str(spec["output_dir"]),
                timeout_per_experiment=float(
                    spec.get("timeout_per_experiment", 120.0)
                ),
                max_points=int(spec.get("max_points", 2000)),
                checkpoint=checkpoint,
                cancel_requested=cancel_path.exists,
                resume=True,
            )
            if result.get("status") == "cancelled":
                raise InterruptedError("Global optimization cancellation requested")
        elif spec.get("job_kind") == "optimization":
            result = _design_optimization_service().run(
                CircuitDesign.from_dict(spec["design"]),
                dict(spec["optimization_spec"]),
                str(spec["output_dir"]),
                timeout_per_experiment=float(
                    spec.get("timeout_per_experiment", 120.0)
                ),
                max_points=int(spec.get("max_points", 2000)),
                checkpoint=checkpoint,
                cancel_requested=cancel_path.exists,
                resume=True,
            )
            if result.get("status") == "cancelled":
                raise InterruptedError("Optimization cancellation requested")
        elif spec.get("job_kind") == "sweep":
            result = _run_experiment_sweep_impl(
                spec=dict(spec["sweep_spec"]),
                output_dir=str(spec["output_dir"]),
                timeout_per_run=float(spec.get("timeout_per_run", 120.0)),
                max_points=int(spec.get("max_points", 2000)),
                overwrite=bool(spec.get("overwrite", False)),
                checkpoint=checkpoint,
                cancel_requested=cancel_path.exists,
                owner=str(request["job_id"]),
            )
        else:
            approved_handoff = all(
                spec.get(name) is not None
                for name in (
                    "executable_netlist",
                    "netlist_approval",
                    "simulation_plan_approval",
                )
            )
            if any(
                spec.get(name) is not None
                for name in (
                    "executable_netlist",
                    "netlist_approval",
                    "simulation_plan_approval",
                )
            ) and not approved_handoff:
                raise ValueError(
                    "persisted experiment approval handoff is incomplete"
                )
            if approved_handoff:
                validated_approval = validate_simulation_plan_approval(
                    dict(spec["executable_netlist"]),
                    dict(spec["netlist_approval"]),
                    {
                        "schema_version": 1,
                        "title": str(spec.get("title", "Multisim experiment")),
                        "netlist": str(spec["netlist"]),
                        "commands": str(spec["commands"]),
                        "requirements": spec.get("requirements", []),
                        "theoretical_values": spec.get("theoretical_values", {}),
                    },
                    dict(spec["simulation_plan_approval"]),
                )
            result = _run_circuit_experiment_impl(
                netlist=str(spec["netlist"]),
                commands=str(spec["commands"]),
                output_dir=str(spec["output_dir"]),
                title=str(spec.get("title", "Multisim experiment")),
                timeout=float(spec.get("timeout", 120.0)),
                max_points=int(spec.get("max_points", 2000)),
                overwrite=bool(spec.get("overwrite", False)),
                checkpoint=checkpoint,
                cancel_requested=cancel_path.exists,
                owner=str(request["job_id"]),
                requirements=spec.get("requirements"),
                theoretical_values=spec.get("theoretical_values"),
                approval_provenance=(
                    build_experiment_approval_provenance(validated_approval)
                    if approved_handoff
                    else None
                ),
            )
            if approved_handoff:
                approval = dict(spec["simulation_plan_approval"])
                result["simulation_plan_approval"] = {
                    "approval_id": approval["approval_id"],
                    "approval_digest": approval["approval_digest"],
                    "netlist_approval_id": approval["netlist_approval_id"],
                    "netlist_approval_digest": approval[
                        "netlist_approval_digest"
                    ],
                    "state": approval["state"],
                    "simulation_started": bool(result.get("success")),
                }
        _atomic_json(result_path, {"success": True, "result": result})
        return 0
    except InterruptedError as exc:
        _atomic_json(
            result_path,
            {
                "success": False,
                "failure": {"code": "CANCELLED", "message": str(exc)},
            },
        )
        return 3
    except BaseException as exc:
        _atomic_json(
            result_path,
            {
                "success": False,
                "failure": {
                    "code": "EXPERIMENT_FAILED",
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(limit=20)[-12000:],
                },
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
