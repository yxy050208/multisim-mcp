"""Validate and optionally execute a workbench controlled-execution handoff.

The visual workbench deliberately stays read-only.  It exports a small JSON
handoff that an operator can run through this module from a trusted local
shell.  Validation is strict and path-safe; execution is always opt-in and
keeps the schematic-first, simulation-second ordering from the UI contract.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from .design_verification import validate_experiment_spec
from .safety import validate_analysis_commands, validate_spice_netlist


HANDOFF_SCHEMA_VERSION = 1
HANDOFF_KIND = "multisim-mcp-controlled-execution-handoff"
MAX_HANDOFF_BYTES = 512 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _text(value: object, name: str, *, maximum: int = 4_000_000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > maximum or "\x00" in value:
        raise ValueError(f"{name} is empty, too long, or contains NUL bytes")
    return value


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _bounded_number(
    value: object,
    name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    """Convert an untrusted JSON number without leaking OverflowError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    try:
        candidate = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(candidate) or not minimum <= candidate <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return candidate


def _relative_path(root: Path, value: object, name: str, *, suffix: str | None = None) -> Path:
    raw = _text(value, name, maximum=1_024).replace("\\", "/")
    candidate = Path(raw)
    if (
        candidate.is_absolute()
        or PureWindowsPath(raw).is_absolute()
        or PureWindowsPath(raw).drive
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError(f"{name} must be a relative path without traversal")
    if suffix is not None and candidate.suffix.casefold() != suffix.casefold():
        raise ValueError(f"{name} must end with {suffix}")
    resolved = (root / candidate).resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"{name} must remain below the project root")
    cursor = root
    for part in candidate.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"{name} crosses a symbolic link")
    return resolved


def _same_json(left: object, right: object, name: str) -> None:
    if json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":")) != json.dumps(
        right, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ):
        raise ValueError(f"{name} must be identical in both handoff steps")


@dataclass(frozen=True)
class ValidatedHandoff:
    """Normalized, path-resolved execution arguments."""

    project_root: Path
    output_dir: Path
    schematic: dict[str, Any]
    simulation: dict[str, Any]
    approval_identity: dict[str, Any]


def load_handoff(path: str | Path) -> dict[str, Any]:
    """Read one bounded UTF-8 handoff and reject duplicate JSON keys."""
    candidate = Path(path).expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("handoff must be a regular, non-symbolic-link file")
    if candidate.stat().st_size > MAX_HANDOFF_BYTES:
        raise ValueError("handoff exceeds the 512 KiB safety limit")

    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"handoff contains duplicate field: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            candidate.read_text(encoding="utf-8"),
            object_pairs_hook=strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite number: {value}")
            ),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid handoff JSON: {exc}") from exc
    return _object(payload, "handoff")


def validate_handoff(payload: dict[str, Any], project_root: str | Path) -> ValidatedHandoff:
    """Strictly validate a workbench handoff and resolve all local paths."""
    if payload.get("schema_version") != HANDOFF_SCHEMA_VERSION:
        raise ValueError("handoff.schema_version is unsupported")
    if payload.get("kind") != HANDOFF_KIND:
        raise ValueError("handoff.kind is unsupported")
    if payload.get("execution_started") is not False:
        raise ValueError("handoff.execution_started must be false")

    raw_root = Path(project_root).expanduser().absolute()
    if raw_root == Path(raw_root.anchor) or not raw_root.is_dir():
        raise ValueError("project root must be an existing non-symbolic-link directory")
    cursor = Path(raw_root.anchor)
    for part in raw_root.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("project root must not cross a symbolic link")
    root = raw_root.resolve()
    output_dir = _relative_path(root, payload.get("output_dir"), "output_dir")

    steps = payload.get("steps")
    if not isinstance(steps, list) or len(steps) != 2:
        raise ValueError("handoff.steps must contain exactly two steps")
    first = _object(steps[0], "steps[0]")
    second = _object(steps[1], "steps[1]")
    if first.get("step_id") != "schematic" or first.get("order") != 1:
        raise ValueError("steps[0] must be the schematic step")
    if second.get("step_id") != "simulation" or second.get("order") != 2:
        raise ValueError("steps[1] must be the simulation step")
    if first.get("tool") != "create_schematic_from_netlist":
        raise ValueError("steps[0] must call create_schematic_from_netlist")
    if second.get("tool") != "run_verified_circuit_experiment":
        raise ValueError("steps[1] must call run_verified_circuit_experiment")

    schematic_raw = _object(first.get("arguments"), "steps[0].arguments")
    simulation_raw = _object(second.get("arguments"), "steps[1].arguments")
    required_schematic = {
        "netlist",
        "output_ms14",
        "image_path",
        "probe_nets",
        "include_experimental_probes",
        "open_after_build",
        "overwrite",
        "executable_netlist",
        "netlist_approval",
    }
    required_simulation = {
        "spec",
        "output_dir",
        "timeout",
        "max_points",
        "overwrite",
        "executable_netlist",
        "netlist_approval",
        "simulation_plan_approval",
    }
    if set(schematic_raw) != required_schematic:
        raise ValueError("steps[0].arguments has unknown or missing fields")
    if set(simulation_raw) != required_simulation:
        raise ValueError("steps[1].arguments has unknown or missing fields")

    if not isinstance(schematic_raw["probe_nets"], list) or any(
        not isinstance(item, str) or not item.strip()
        for item in schematic_raw["probe_nets"]
    ):
        raise ValueError("steps[0].arguments.probe_nets must be a string array")
    for field in ("include_experimental_probes", "open_after_build", "overwrite"):
        _bool(schematic_raw[field], f"steps[0].arguments.{field}")
    _bool(simulation_raw["overwrite"], "steps[1].arguments.overwrite")
    timeout = _bounded_number(
        simulation_raw["timeout"],
        "steps[1].arguments.timeout",
        minimum=0,
        maximum=3600,
    )
    if timeout <= 0:
        raise ValueError(
            "steps[1].arguments.timeout must be between 0 and 3600 seconds"
        )
    max_points = simulation_raw["max_points"]
    if (
        isinstance(max_points, bool)
        or not isinstance(max_points, int)
        or not 1 <= max_points <= 100_000
    ):
        raise ValueError(
            "steps[1].arguments.max_points must be between 1 and 100000"
        )

    schematic_output = _relative_path(
        root, schematic_raw["output_ms14"], "steps[0].arguments.output_ms14", suffix=".ms14"
    )
    image_path = _relative_path(
        root, schematic_raw["image_path"], "steps[0].arguments.image_path", suffix=".png"
    )
    if schematic_output.parent != output_dir or image_path.parent != output_dir:
        raise ValueError("schematic artifacts must be inside output_dir")
    simulation_output = _relative_path(
        root, simulation_raw["output_dir"], "steps[1].arguments.output_dir"
    )
    if simulation_output != output_dir:
        raise ValueError("simulation output_dir must match handoff.output_dir")

    netlist = _text(schematic_raw["netlist"], "steps[0].arguments.netlist")
    validate_spice_netlist(netlist)
    compiled = _object(schematic_raw["executable_netlist"], "executable_netlist")
    compiled_spice = _text(compiled.get("spice_netlist"), "executable_netlist.spice_netlist")
    if netlist != compiled_spice:
        raise ValueError("schematic netlist does not match executable preview")
    spec = _object(simulation_raw["spec"], "simulation spec")
    if spec.get("netlist") != netlist:
        raise ValueError("simulation spec netlist does not match schematic netlist")
    # Run the same semantic and command-safety checks used by the execution
    # service while the handoff is still a dry-run.  This prevents --submit
    # from creating a schematic and only then discovering an invalid queue
    # payload (for example a missing requirements array or an unsafe command).
    normalized_spec = validate_experiment_spec(spec)
    if normalized_spec["netlist"] != netlist:
        raise ValueError("simulation spec netlist does not match schematic netlist")
    validate_analysis_commands(normalized_spec["commands"])
    _same_json(schematic_raw["executable_netlist"], simulation_raw["executable_netlist"], "executable_netlist")
    _same_json(schematic_raw["netlist_approval"], simulation_raw["netlist_approval"], "netlist_approval")
    approval = _object(simulation_raw["simulation_plan_approval"], "simulation_plan_approval")
    approval_identity = {
        "simulation_plan_approval_id": approval.get("approval_id"),
        "simulation_plan_approval_digest": approval.get("approval_digest"),
        "netlist_approval_id": approval.get("netlist_approval_id"),
        "netlist_approval_digest": approval.get("netlist_approval_digest"),
        "compiled_id": approval.get("compiled_id"),
        "compiled_digest": approval.get("compiled_digest"),
        "spec_digest": approval.get("spec_digest"),
    }
    for field, value in approval_identity.items():
        pattern = _DIGEST_RE if field.endswith("digest") else _ID_RE
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise ValueError(f"simulation_plan_approval.{field} is invalid")

    contract = _object(payload.get("result_contract"), "result_contract")
    if contract.get("expected_entry_kind") != "experiment" or contract.get("expected_path") != payload.get("output_dir"):
        raise ValueError("result_contract does not bind the expected experiment directory")
    if contract.get("identity_check") != "path_manifest_and_approval_provenance":
        raise ValueError("result_contract.identity_check is unsupported")

    schematic = dict(schematic_raw)
    schematic["output_ms14"] = str(schematic_output)
    schematic["image_path"] = str(image_path)
    simulation = dict(simulation_raw)
    simulation["output_dir"] = str(output_dir)
    return ValidatedHandoff(root, output_dir, schematic, simulation, approval_identity)


def execute_handoff(handoff: ValidatedHandoff, *, allow_overwrite: bool = False) -> dict[str, Any]:
    """Execute schematic generation, then verified simulation, in that order."""
    from multisim_mcp import server

    schematic_args = dict(handoff.schematic)
    simulation_args = dict(handoff.simulation)
    if not allow_overwrite:
        schematic_args["overwrite"] = False
        simulation_args["overwrite"] = False
    schematic_result = dict(server.create_schematic_from_netlist(**schematic_args))
    if not schematic_result.get("success"):
        return {
            "success": False,
            "stage": "schematic",
            "output_dir": str(handoff.output_dir),
            "schematic": schematic_result,
            "simulation_started": False,
        }
    simulation_result = dict(server.run_verified_circuit_experiment(**simulation_args))
    approval_result = simulation_result.get("simulation_plan_approval")
    simulation_started = (
        bool(approval_result.get("simulation_started"))
        if isinstance(approval_result, dict)
        else bool(simulation_result.get("success"))
    )
    return {
        "success": bool(simulation_result.get("success")),
        "stage": "simulation",
        "output_dir": str(handoff.output_dir),
        "schematic": schematic_result,
        "simulation": simulation_result,
        "simulation_started": simulation_started,
    }


def submit_handoff(
    handoff: ValidatedHandoff,
    *,
    allow_overwrite: bool = False,
    job_timeout: float = 600.0,
    heartbeat_timeout: float = 180.0,
) -> dict[str, Any]:
    """Create the approved schematic, then enqueue the verified experiment."""
    from multisim_mcp import server

    try:
        job_timeout_value = float(job_timeout)
        heartbeat_timeout_value = float(heartbeat_timeout)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("job_timeout and heartbeat_timeout must be finite numbers") from exc
    if not math.isfinite(job_timeout_value) or not 1 <= job_timeout_value <= 7200:
        raise ValueError("job_timeout must be between 1 and 7200 seconds")
    if not math.isfinite(heartbeat_timeout_value) or not 10 <= heartbeat_timeout_value <= 900:
        raise ValueError("heartbeat_timeout must be between 10 and 900 seconds")
    if job_timeout_value <= float(handoff.simulation["timeout"]):
        raise ValueError("job_timeout must be greater than the simulation timeout")

    schematic_args = dict(handoff.schematic)
    simulation_args = dict(handoff.simulation)
    if not allow_overwrite:
        schematic_args["overwrite"] = False
        simulation_args["overwrite"] = False
    schematic_result = dict(server.create_schematic_from_netlist(**schematic_args))
    if not schematic_result.get("success"):
        return {
            "success": False,
            "stage": "schematic",
            "output_dir": str(handoff.output_dir),
            "schematic": schematic_result,
            "simulation_started": False,
        }

    spec = _object(simulation_args.pop("spec"), "simulation spec")
    submission = dict(
        server.submit_circuit_experiment(
            netlist=_text(spec.get("netlist"), "simulation spec.netlist"),
            commands=_text(spec.get("commands"), "simulation spec.commands"),
            output_dir=str(handoff.output_dir),
            title=str(spec.get("title", "Multisim experiment")),
            timeout=float(simulation_args["timeout"]),
            max_points=int(simulation_args["max_points"]),
            overwrite=bool(simulation_args["overwrite"]),
            job_timeout=job_timeout,
            heartbeat_timeout=heartbeat_timeout,
            requirements=spec.get("requirements"),
            theoretical_values=spec.get("theoretical_values"),
            executable_netlist=simulation_args["executable_netlist"],
            netlist_approval=simulation_args["netlist_approval"],
            simulation_plan_approval=simulation_args["simulation_plan_approval"],
        )
    )
    return {
        "success": bool(submission.get("success")),
        "stage": "queue",
        "output_dir": str(handoff.output_dir),
        "schematic": schematic_result,
        "job": submission,
        "simulation_started": False,
    }


__all__ = [
    "HANDOFF_KIND",
    "HANDOFF_SCHEMA_VERSION",
    "MAX_HANDOFF_BYTES",
    "ValidatedHandoff",
    "execute_handoff",
    "load_handoff",
    "submit_handoff",
    "validate_handoff",
]
