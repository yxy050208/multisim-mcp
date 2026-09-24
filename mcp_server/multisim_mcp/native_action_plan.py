"""Bounded native project actions; no arbitrary command files or implicit saves."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .preferred_values import parse_spice_scalar
from .safety import validate_analysis_commands

ALLOWED_ACTIONS = frozenset({
    "open_project", "set_component_value", "run_analysis", "measure", "export_artifacts",
})
_FIELDS = {
    "open_project": {"op", "path"},
    "set_component_value": {"op", "refdes", "value"},
    "run_analysis": {"op", "analysis", "commands", "outputs", "timeout", "max_points"},
    "measure": {"op", "signal"},
    "export_artifacts": {"op", "directory"},
}


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError(f"{name} must be non-empty text")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{name} contains control characters")
    return value.strip()


def _signals(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise ValueError("outputs must contain 1..32 signals")
    names = [_text(item, "output signal") for item in value]
    if len({item.casefold() for item in names}) != len(names):
        raise ValueError("outputs must be unique")
    return names


def validate_native_action_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the entire plan before any COM call, including action ordering."""
    if not isinstance(plan, Mapping) or type(plan.get("schema_version")) is not int or plan["schema_version"] != 1:
        raise ValueError("native action plan schema_version must be 1")
    if set(plan) - {"schema_version", "actions", "action_count"}:
        raise ValueError("unknown native action plan fields")
    actions = plan.get("actions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 128:
        raise ValueError("actions must contain 1..128 objects")
    normalized = []
    fresh_outputs: set[str] = set()
    for index, raw in enumerate(actions):
        if not isinstance(raw, Mapping) or raw.get("op") not in ALLOWED_ACTIONS:
            raise ValueError(f"action {index} is not supported")
        name = raw["op"]
        if set(raw) - _FIELDS[name]:
            raise ValueError(f"action {index} contains unknown fields")
        item = dict(raw)
        if index == 0 and name != "open_project":
            raise ValueError("first action must open_project")
        if name == "open_project":
            if index != 0:
                raise ValueError("only one open_project is allowed")
            path = Path(_text(item.get("path"), "path")).expanduser().resolve()
            if path.suffix.casefold() != ".ms14" or not path.is_file():
                raise ValueError("project must be an existing .ms14 file")
            item["path"] = str(path)
        elif name == "set_component_value":
            refdes = _text(item.get("refdes"), "refdes")
            if not re.fullmatch(r"[RCLrcl][A-Za-z0-9_]*", refdes):
                raise ValueError("only R/L/C component values can be changed")
            value = item.get("value")
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError("value must be a positive SPICE scalar")
            item["value"] = float(parse_spice_scalar(str(value)))
            if not math.isfinite(item["value"]):
                raise ValueError("value must be finite")
            item["refdes"] = refdes
            fresh_outputs.clear()
        elif name == "run_analysis":
            analysis = _text(item.get("analysis"), "analysis").casefold()
            if analysis not in {"op", "dc", "ac", "tran"}:
                raise ValueError("unsupported analysis")
            commands = item.get("commands", "op" if analysis == "op" else "")
            if not isinstance(commands, str) or len(commands) > 4096:
                raise ValueError("commands must be bounded text")
            accepted = validate_analysis_commands(commands)
            if len(accepted) != 1 or accepted[0].split()[0].casefold() != analysis:
                raise ValueError("exactly one command matching analysis is required")
            # Syntax allowlists alone do not reject zero steps or reversed ranges.
            tokens = accepted[0].split()
            if analysis in {"ac", "tran"}:
                numeric = tokens[3:] if analysis == "ac" else tokens[1:]
                for token in numeric:
                    if token.lower() != "uic":
                        if token == "0" and analysis == "tran":
                            continue  # optional start time
                        parse_spice_scalar(token)
                if analysis == "tran":
                    step, stop = (parse_spice_scalar(token) for token in tokens[1:3])
                    if step > stop:
                        raise ValueError("tran step must not exceed stop")
                elif parse_spice_scalar(tokens[3]) > parse_spice_scalar(tokens[4]):
                    raise ValueError("AC start must not exceed stop")
            timeout = item.get("timeout", 30.0)
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
                raise ValueError("timeout must be between 0 and 600")
            maximum = item.get("max_points", 2000)
            if type(maximum) is not int or not 1 <= maximum <= 10000:
                raise ValueError("max_points must be between 1 and 10000")
            item.update(analysis=analysis, commands=accepted[0], outputs=_signals(item.get("outputs")),
                        timeout=float(timeout), max_points=maximum)
            fresh_outputs = {value.casefold() for value in item["outputs"]}
        elif name == "measure":
            signal = _text(item.get("signal"), "signal")
            if signal.casefold() not in fresh_outputs:
                raise ValueError("measure needs a successful analysis after the last parameter change")
            item["signal"] = signal
        else:
            directory = Path(_text(item.get("directory"), "directory")).expanduser().resolve()
            if directory == Path(directory.anchor):
                raise ValueError("export directory must not be a filesystem root")
            for filename in ("schematic.png", "netlist.cir", "bom.txt"):
                if (directory / filename).exists():
                    raise FileExistsError(f"refusing to overwrite {directory / filename}")
            item["directory"] = str(directory)
        normalized.append(item)
    return {"schema_version": 1, "actions": normalized, "action_count": len(normalized)}


def execute_native_action_plan(
    client: Any, plan: Mapping[str, Any], *,
    analysis_runner: Callable[[Any, Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute one project serially and restore parameters on success and failure.

    The owner controls copy/save and evidence publication. Analysis is injected;
    no command file, stale source netlist, or arbitrary model callback is in JSON.
    """
    normalized = validate_native_action_plan(plan)
    if any(item["op"] == "run_analysis" for item in normalized["actions"]) and analysis_runner is None:
        raise ValueError("a controlled analysis_runner is required")
    originals: dict[str, float] = {}
    results = []
    measurements = []
    restore_errors = []
    failure = None
    latest: Mapping[str, Any] = {}
    try:
        client.open_circuit(normalized["actions"][0]["path"])
        # Read all original values before the first mutation.
        available = {str(ref).casefold(): str(ref) for ref in client.enum_components(0)}
        for action in normalized["actions"]:
            if action["op"] == "set_component_value":
                key = action["refdes"].casefold()
                if key not in available:
                    raise ValueError(f"component is not present: {action['refdes']}")
                action["refdes"] = available[key]
                if available[key] not in originals:
                    original = float(client.get_rlc_value(available[key])["value"])
                    if not math.isfinite(original):
                        raise ValueError("original component value is not finite")
                    originals[available[key]] = original
        for action in normalized["actions"][1:]:
            name = action["op"]
            if name == "set_component_value":
                result = client.set_rlc_value(action["refdes"], action["value"])
                actual = float(client.get_rlc_value(action["refdes"])["value"])
                if not math.isclose(actual, action["value"], rel_tol=1e-9, abs_tol=0.0):
                    raise RuntimeError(f"parameter readback mismatch: {action['refdes']}")
                latest = {}
            elif name == "run_analysis":
                result = dict(analysis_runner(client, action))
                if result.get("success") is not True or result.get("timed_out") or result.get("ready") is False:
                    raise RuntimeError("analysis failed or timed out")
                latest = result
            elif name == "measure":
                series = latest.get("signals", {}).get(action["signal"], [])
                if not series or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in series):
                    raise RuntimeError(f"no finite data for {action['signal']}")
                result = {"signal": action["signal"], "count": len(series),
                          "analysis": latest.get("analysis", "unknown"),
                          "quantity": latest.get("measurement_quantity", "value"),
                          "mean": sum(series) / len(series), "min": min(series),
                          "max": max(series), "final": series[-1]}
                measurements.append(result)
            else:
                root = Path(action["directory"])
                root.mkdir(parents=True, exist_ok=True)
                result = {"image": client.get_circuit_image(str(root / "schematic.png")),
                          "netlist": client.report_netlist(str(root / "netlist.cir")),
                          "bom": client.report_bom(str(root / "bom.txt"))}
            results.append({"op": name, "result": result})
    except Exception as exc:
        failure = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        for refdes, original in reversed(list(originals.items())):
            try:
                client.set_rlc_value(refdes, original)
                actual = float(client.get_rlc_value(refdes)["value"])
                if not math.isclose(actual, original, rel_tol=1e-9, abs_tol=0.0):
                    raise RuntimeError("restored value readback mismatch")
            except Exception as exc:
                restore_errors.append({"refdes": refdes, "error": str(exc)})
    return {"schema_version": 1, "success": failure is None and not restore_errors,
            "results": results, "measurements": measurements, "error": failure,
            "original_values": originals, "restore_errors": restore_errors,
            "restored_original_values": not restore_errors}


__all__ = ["ALLOWED_ACTIONS", "execute_native_action_plan", "validate_native_action_plan"]
