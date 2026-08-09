"""Validated, deterministic planning for Multisim experiment sweeps."""

from __future__ import annotations

import itertools
import math
import random
import re
from typing import Any, Final

from multisim_mcp.design_verification import validate_measurement_requests
from multisim_mcp.safety import validate_analysis_commands, validate_spice_netlist


MAX_SWEEP_RUNS: Final = 100
MAX_TEMPLATE_CHARS: Final = 64 * 1024
_NAME_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
_PLACEHOLDER_RE: Final = re.compile(r"\{\{([A-Za-z][A-Za-z0-9_]{0,31})\}\}")


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _render_number(value: float) -> str:
    return format(value, ".12g")


def _replace_placeholders(template: str, values: dict[str, float]) -> str:
    rendered = template
    for name, value in values.items():
        rendered = rendered.replace("{{" + name + "}}", _render_number(value))
    unresolved = sorted(set(_PLACEHOLDER_RE.findall(rendered)))
    if unresolved:
        raise ValueError("unresolved sweep placeholders: " + ", ".join(unresolved))
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("invalid sweep placeholder syntax")
    return rendered


def _inject_temperature(netlist: str, temperature: float) -> str:
    if "{{temperature}}" in netlist:
        return netlist.replace("{{temperature}}", _render_number(temperature))
    lines = netlist.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() == ".end":
            lines.insert(index, f".temp {_render_number(temperature)}")
            return "\n".join(lines) + ("\n" if netlist.endswith("\n") else "")
    raise ValueError("temperature sweep netlist must contain a top-level .end")


def _normalize_parameters(raw: object, mode: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("parameters must be an array")
    parameters: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, candidate in enumerate(raw):
        if not isinstance(candidate, dict):
            raise ValueError(f"parameter {index} must be an object")
        item = dict(candidate)
        allowed_keys = (
            {"name", "values"}
            if mode == "parameter"
            else {"name", "nominal", "tolerance_percent"}
            if mode == "tolerance"
            else {
                "name",
                "nominal",
                "distribution",
                "tolerance_percent",
                "sigma_percent",
                "minimum",
                "maximum",
            }
            if mode == "monte_carlo"
            else {"name"}
        )
        unknown_keys = set(item) - allowed_keys
        if unknown_keys:
            raise ValueError(
                f"unknown fields for parameter {index}: "
                + ", ".join(sorted(unknown_keys))
            )
        name = str(item.get("name", ""))
        if not _NAME_RE.fullmatch(name) or name == "temperature":
            raise ValueError(f"invalid sweep parameter name: {name!r}")
        if name in seen:
            raise ValueError(f"duplicate sweep parameter: {name}")
        seen.add(name)
        item["name"] = name
        if mode == "parameter":
            values = item.get("values")
            if not isinstance(values, list) or not values:
                raise ValueError(f"parameter {name} requires a non-empty values array")
            item["values"] = [_finite(value, f"{name}.values") for value in values]
        else:
            item["nominal"] = _finite(item.get("nominal"), f"{name}.nominal")
            if mode == "tolerance":
                tolerance = _finite(item.get("tolerance_percent"), f"{name}.tolerance_percent")
                if tolerance < 0 or tolerance > 100:
                    raise ValueError(f"{name}.tolerance_percent must be between 0 and 100")
                item["tolerance_percent"] = tolerance
            elif mode == "monte_carlo":
                distribution = str(item.get("distribution", "uniform")).lower()
                if distribution not in {"uniform", "normal"}:
                    raise ValueError(f"unsupported distribution for {name}: {distribution}")
                item["distribution"] = distribution
                field = "tolerance_percent" if distribution == "uniform" else "sigma_percent"
                conflicting = "sigma_percent" if distribution == "uniform" else "tolerance_percent"
                if conflicting in item:
                    raise ValueError(f"{name}.{conflicting} does not apply to {distribution}")
                spread = _finite(item.get(field), f"{name}.{field}")
                if spread < 0 or spread > 100:
                    raise ValueError(f"{name}.{field} must be between 0 and 100")
                item[field] = spread
                for bound in ("minimum", "maximum"):
                    if bound in item:
                        item[bound] = _finite(item[bound], f"{name}.{bound}")
                if item.get("minimum", -math.inf) > item.get("maximum", math.inf):
                    raise ValueError(f"{name}.minimum exceeds maximum")
        parameters.append(item)
    if mode != "temperature" and not parameters:
        raise ValueError(f"{mode} sweep requires at least one parameter")
    return parameters


def _value_sets(spec: dict[str, Any], parameters: list[dict[str, Any]]) -> list[dict[str, float]]:
    mode = spec["mode"]
    if mode == "parameter":
        products = itertools.product(*(item["values"] for item in parameters))
        return [dict(zip((item["name"] for item in parameters), values)) for values in products]
    if mode == "tolerance":
        names = [item["name"] for item in parameters]
        edges = []
        nominal = {}
        for item in parameters:
            center = item["nominal"]
            delta = abs(center) * item["tolerance_percent"] / 100.0
            edges.append((center - delta, center + delta))
            nominal[item["name"]] = center
        corners = [dict(zip(names, values)) for values in itertools.product(*edges)]
        return [nominal, *[corner for corner in corners if corner != nominal]]
    if mode == "temperature":
        temperatures = spec.get("temperatures")
        if not isinstance(temperatures, list) or not temperatures:
            raise ValueError("temperature sweep requires temperatures")
        return [{"temperature": _finite(value, "temperatures")} for value in temperatures]
    runs = spec.get("runs", 20)
    if isinstance(runs, bool) or not isinstance(runs, int) or not 1 <= runs <= MAX_SWEEP_RUNS:
        raise ValueError(f"runs must be an integer between 1 and {MAX_SWEEP_RUNS}")
    seed = spec.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    rng = random.Random(seed)
    samples: list[dict[str, float]] = []
    for _ in range(runs):
        sample = {}
        for item in parameters:
            nominal = item["nominal"]
            if item["distribution"] == "uniform":
                delta = abs(nominal) * item["tolerance_percent"] / 100.0
                value = rng.uniform(nominal - delta, nominal + delta)
            else:
                sigma = abs(nominal) * item["sigma_percent"] / 100.0
                value = rng.gauss(nominal, sigma)
            value = max(item.get("minimum", -math.inf), value)
            value = min(item.get("maximum", math.inf), value)
            sample[item["name"]] = value
        samples.append(sample)
    return samples


def plan_experiment_sweep(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate a sweep contract and expand it into deterministic safe runs."""
    if not isinstance(spec, dict) or spec.get("schema_version") != 1:
        raise ValueError("SweepSpec schema_version must be 1")
    unknown_keys = set(spec) - {
        "schema_version",
        "mode",
        "title",
        "netlist_template",
        "commands",
        "parameters",
        "measurements",
        "temperatures",
        "runs",
        "seed",
    }
    if unknown_keys:
        raise ValueError("unknown SweepSpec fields: " + ", ".join(sorted(unknown_keys)))
    mode = str(spec.get("mode", "")).lower()
    if mode not in {"parameter", "tolerance", "temperature", "monte_carlo"}:
        raise ValueError("mode must be parameter, tolerance, temperature, or monte_carlo")
    irrelevant = (
        ({"temperatures", "runs", "seed"} & set(spec))
        if mode in {"parameter", "tolerance"}
        else ({"runs", "seed"} & set(spec))
        if mode == "temperature"
        else ({"temperatures"} & set(spec))
    )
    if irrelevant:
        raise ValueError(
            f"fields do not apply to {mode} mode: " + ", ".join(sorted(irrelevant))
        )
    netlist_template = str(spec.get("netlist_template", ""))
    commands = str(spec.get("commands", ""))
    if not netlist_template.strip() or len(netlist_template) > MAX_TEMPLATE_CHARS:
        raise ValueError("netlist_template must contain 1 to 65536 characters")
    accepted = "\n".join(validate_analysis_commands(commands))
    parameters = _normalize_parameters(spec.get("parameters", []), mode)
    declared = {item["name"] for item in parameters}
    placeholders = set(_PLACEHOLDER_RE.findall(netlist_template))
    allowed = declared | ({"temperature"} if mode == "temperature" else set())
    unknown = placeholders - allowed
    if unknown:
        raise ValueError("unknown sweep placeholders: " + ", ".join(sorted(unknown)))
    missing = declared - placeholders
    if missing:
        raise ValueError("parameters missing from netlist_template: " + ", ".join(sorted(missing)))
    measurements = validate_measurement_requests(spec.get("measurements", []))
    value_sets = _value_sets({**spec, "mode": mode}, parameters)
    if not value_sets or len(value_sets) > MAX_SWEEP_RUNS:
        raise ValueError(f"sweep expands to {len(value_sets)} runs; maximum is {MAX_SWEEP_RUNS}")
    expanded = []
    for index, values in enumerate(value_sets, start=1):
        rendered = netlist_template
        if mode == "temperature":
            rendered = _inject_temperature(rendered, values["temperature"])
        rendered = _replace_placeholders(rendered, values)
        validate_spice_netlist(rendered)
        expanded.append(
            {
                "run_id": f"run-{index:04d}",
                "index": index,
                "variables": values,
                "netlist": rendered,
                "commands": accepted,
            }
        )
    return {
        "schema_version": 1,
        "mode": mode,
        "title": str(spec.get("title", "Multisim experiment sweep")).strip() or "Multisim experiment sweep",
        "run_count": len(expanded),
        "measurements": measurements,
        "runs": expanded,
        "seed": spec.get("seed", 0) if mode == "monte_carlo" else None,
    }
