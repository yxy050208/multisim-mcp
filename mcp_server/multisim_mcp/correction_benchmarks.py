"""Repeatable cross-family benchmarks for correction and global optimization."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping, Sequence

from .eda_core import CircuitComponent, CircuitDesign
from .global_optimization import (
    GlobalDesignOptimizationService,
    validate_global_optimization_spec,
)
from .spice_adapter import circuit_design_to_spice
from .workspace_manifest import (
    DIRECTORY_MANIFEST_NAME,
    read_directory_manifest,
    write_directory_manifest,
)

BENCHMARK_SCHEMA_VERSION: Final = 1
BENCHMARK_SUMMARY_NAME: Final = "benchmark-suite.json"
_CASE_ID_LIMIT: Final = 32


@dataclass(frozen=True)
class CorrectionBenchmark:
    case_id: str
    family: str
    title_zh: str
    title_en: str
    defect: str
    design: CircuitDesign
    optimization_spec: Mapping[str, Any]
    expected_assignment: Mapping[str, str]

    def summary(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "family": self.family,
            "title_zh": self.title_zh,
            "title_en": self.title_en,
            "defect": self.defect,
            "design_id": self.design.design_id,
            "expected_assignment": dict(self.expected_assignment),
        }


def _objective(requirement_id: str, target: float) -> list[dict[str, Any]]:
    return [
        {
            "requirement_id": requirement_id,
            "goal": "target",
            "target": target,
            "weight": 1.0,
        }
    ]


def _search_spec(
    *,
    title: str,
    dimension_id: str,
    refdes: str,
    values: Sequence[str],
    commands: str,
    requirement: Mapping[str, Any],
    target: float,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "title": title,
        "dimensions": [
            {
                "id": dimension_id,
                "kind": "component_value",
                "refdes": refdes,
                "values": list(values),
            }
        ],
        "commands": commands,
        "requirements": [dict(requirement)],
        "theoretical_values": {str(requirement["id"]): target},
        "objectives": _objective(str(requirement["id"]), target),
        "max_experiments": len(values),
        "search_strategy": "exhaustive",
        "selection_policy": "weighted_compromise",
    }


def standard_benchmark_catalog() -> tuple[CorrectionBenchmark, ...]:
    """Return fresh immutable benchmark cases spanning five circuit families."""
    rc_design = CircuitDesign(
        design_id="benchmark-rc-lowpass",
        title="Faulty RC low-pass",
        components=(
            CircuitComponent("V1", "V", ("in", "0"), model="AC 1"),
            CircuitComponent("R1", "R", ("in", "out"), value="1k"),
            CircuitComponent("C1", "C", ("out", "0"), value="1u"),
        ),
        source_netlist="V1 in 0 AC 1\nR1 in out 1k\nC1 out 0 1u\n.end\n",
    )
    rc_target = 1591.5494309
    rc_spec = _search_spec(
        title="RC low-pass cutoff correction",
        dimension_id="capacitance",
        refdes="C1",
        values=("220n", "100n", "47n"),
        commands="ac dec 50 10 100k",
        requirement={
            "id": "cutoff",
            "metric": "cutoff_frequency",
            "signal": "V(out)",
            "reference_signal": "V(in)",
            "operator": "approximately",
            "target": rc_target,
            "tolerance_percent": 8.0,
            "unit": "Hz",
        },
        target=rc_target,
    )

    rlc_design = CircuitDesign(
        design_id="benchmark-rlc-bandpass",
        title="Faulty series RLC band-pass",
        components=(
            CircuitComponent("V1", "V", ("in", "0"), model="AC 1"),
            CircuitComponent("L1", "L", ("in", "tank"), value="10m"),
            CircuitComponent("C1", "C", ("tank", "out"), value="1u"),
            CircuitComponent("R1", "R", ("out", "0"), value="1k"),
        ),
        source_netlist=(
            "V1 in 0 AC 1\nL1 in tank 10m\nC1 tank out 1u\n"
            "R1 out 0 1k\n.end\n"
        ),
    )
    rlc_target = 1591.5494309
    rlc_spec = _search_spec(
        title="RLC band-pass bandwidth correction",
        dimension_id="series-resistance",
        refdes="R1",
        values=("330", "100", "47"),
        commands="ac dec 80 10 100k",
        requirement={
            "id": "bandwidth",
            "metric": "bandwidth",
            "signal": "V(out)",
            "reference_signal": "V(in)",
            "operator": "approximately",
            "target": rlc_target,
            "tolerance_percent": 10.0,
            "unit": "Hz",
            "parameters": {"bandwidth_mode": "bandpass"},
        },
        target=rlc_target,
    )

    opamp_source = """\
V1 inp 0 1
VCC vp 0 15
VEE vn 0 -15
XU1 inp inn vp vn out IDEAL_OP
RG inn 0 1k
RF out inn 2k
.subckt IDEAL_OP inp inn vp vn out
EGAIN raw 0 inp inn 100k
ROUT raw out 10
.ends IDEAL_OP
.end
"""
    opamp_design = CircuitDesign(
        design_id="benchmark-opamp-feedback",
        title="Faulty non-inverting amplifier",
        components=(
            CircuitComponent("V1", "V", ("inp", "0"), value="1"),
            CircuitComponent("VCC", "V", ("vp", "0"), value="15"),
            CircuitComponent("VEE", "V", ("vn", "0"), value="-15"),
            CircuitComponent(
                "XU1",
                "OPAMP5",
                ("inp", "inn", "vp", "vn", "out"),
                model="IDEAL_OP",
            ),
            CircuitComponent("RG", "R", ("inn", "0"), value="1k"),
            CircuitComponent("RF", "R", ("out", "inn"), value="2k"),
        ),
        source_netlist=opamp_source,
    )
    opamp_target = 11.0
    opamp_spec = _search_spec(
        title="Op-amp feedback correction",
        dimension_id="feedback-resistance",
        refdes="RF",
        values=("4.7k", "10k", "22k"),
        commands="op",
        requirement={
            "id": "closed-loop-output",
            "metric": "mean",
            "signal": "V(out)",
            "operator": "approximately",
            "target": opamp_target,
            "tolerance_percent": 3.0,
            "unit": "V",
        },
        target=opamp_target,
    )

    bjt_source = """\
VCC vcc 0 10
RC vcc c 1k
RB vcc b 47k
Q1 c b 0 QTEST
.model QTEST NPN(IS=1e-14 BF=100 VAF=100)
.end
"""
    bjt_design = CircuitDesign(
        design_id="benchmark-bjt-bias",
        title="Faulty NPN common-emitter bias",
        components=(
            CircuitComponent("VCC", "V", ("vcc", "0"), value="10"),
            CircuitComponent("RC", "R", ("vcc", "c"), value="1k"),
            CircuitComponent("RB", "R", ("vcc", "b"), value="47k"),
            CircuitComponent("Q1", "QNPN", ("c", "b", "0"), model="QTEST"),
        ),
        source_netlist=bjt_source,
    )
    bjt_target = 5.0
    bjt_spec = _search_spec(
        title="BJT quiescent-point correction",
        dimension_id="base-resistance",
        refdes="RB",
        values=("100k", "180k", "330k"),
        commands="op",
        requirement={
            "id": "collector-voltage",
            "metric": "mean",
            "signal": "V(c)",
            "operator": "approximately",
            "target": bjt_target,
            "tolerance_percent": 12.0,
            "unit": "V",
        },
        target=bjt_target,
    )

    power_source = """\
V1 raw 0 12
RS raw out 2k
RL out 0 1k
D1 0 out DZ5V1
.model DZ5V1 D(IS=1n BV=5.1 IBV=1m)
.end
"""
    power_design = CircuitDesign(
        design_id="benchmark-zener-supply",
        title="Faulty shunt-regulated supply",
        components=(
            CircuitComponent("V1", "V", ("raw", "0"), value="12"),
            CircuitComponent("RS", "R", ("raw", "out"), value="2k"),
            CircuitComponent("RL", "R", ("out", "0"), value="1k"),
            CircuitComponent("D1", "D", ("0", "out"), model="DZ5V1"),
        ),
        source_netlist=power_source,
    )
    power_target = 5.1
    power_spec = _search_spec(
        title="Zener supply regulation correction",
        dimension_id="series-resistance",
        refdes="RS",
        values=("680", "470", "330"),
        commands="op",
        requirement={
            "id": "regulated-output",
            "metric": "mean",
            "signal": "V(out)",
            "operator": "approximately",
            "target": power_target,
            "tolerance_percent": 6.0,
            "unit": "V",
        },
        target=power_target,
    )

    return (
        CorrectionBenchmark(
            "rc-lowpass",
            "rc",
            "RC 低通截止频率纠错",
            "RC low-pass cutoff correction",
            "C1 is one decade too large",
            rc_design,
            rc_spec,
            {"capacitance": "100n"},
        ),
        CorrectionBenchmark(
            "rlc-bandpass",
            "rlc",
            "RLC 带通带宽纠错",
            "RLC band-pass bandwidth correction",
            "R1 makes the passband too wide",
            rlc_design,
            rlc_spec,
            {"series-resistance": "100"},
        ),
        CorrectionBenchmark(
            "opamp-feedback",
            "opamp",
            "运放反馈增益纠错",
            "Op-amp feedback gain correction",
            "RF sets the closed-loop gain too low",
            opamp_design,
            opamp_spec,
            {"feedback-resistance": "10k"},
        ),
        CorrectionBenchmark(
            "bjt-bias",
            "bjt",
            "BJT 静态工作点纠错",
            "BJT quiescent-point correction",
            "RB over-biases the transistor",
            bjt_design,
            bjt_spec,
            {"base-resistance": "180k"},
        ),
        CorrectionBenchmark(
            "zener-supply",
            "power",
            "稳压电源工作点纠错",
            "Regulated power-supply correction",
            "RS starves the Zener regulator",
            power_design,
            power_spec,
            {"series-resistance": "470"},
        ),
    )


def _select_cases(case_ids: Sequence[str] | None) -> tuple[CorrectionBenchmark, ...]:
    catalog = standard_benchmark_catalog()
    if case_ids is None or len(case_ids) == 0:
        return catalog
    if len(case_ids) > _CASE_ID_LIMIT:
        raise ValueError(f"at most {_CASE_ID_LIMIT} benchmark case IDs are allowed")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("benchmark case IDs must not contain duplicates")
    by_id = {case.case_id: case for case in catalog}
    unknown = sorted(set(case_ids) - set(by_id))
    if unknown:
        raise ValueError(f"unknown benchmark cases: {unknown}")
    return tuple(by_id[case_id] for case_id in case_ids)


def validate_standard_benchmarks(
    case_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Validate catalog structure, safe compilation, models, and search domains."""
    validated: list[dict[str, Any]] = []
    for case in _select_cases(case_ids):
        source = circuit_design_to_spice(case.design, prefer_source=False)
        normalized = validate_global_optimization_spec(
            dict(case.optimization_spec), case.design
        )
        dimensions = {item["id"]: item for item in normalized["dimensions"]}
        for dimension_id, expected in case.expected_assignment.items():
            if dimension_id not in dimensions:
                raise ValueError(
                    f"benchmark {case.case_id} expected dimension is missing: "
                    f"{dimension_id}"
                )
            if expected not in dimensions[dimension_id]["options"]:
                raise ValueError(
                    f"benchmark {case.case_id} expected assignment is outside domain"
                )
        if case.family in {"bjt", "power"} and ".model" not in source.lower():
            raise ValueError(f"benchmark {case.case_id} lost its inline model")
        if case.family == "opamp" and ".subckt" not in source.lower():
            raise ValueError(f"benchmark {case.case_id} lost its inline subcircuit")
        item = case.summary()
        item.update(
            validation_status="valid",
            search_space_size=math.prod(
                len(dimension["options"]) for dimension in normalized["dimensions"]
            ),
            compiled_netlist_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        )
        validated.append(item)
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "mode": "validate",
        "success": True,
        "case_count": len(validated),
        "cases": validated,
        "simulation_performed": False,
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_standard_benchmarks(
    service: GlobalDesignOptimizationService,
    output_directory: str,
    *,
    case_ids: Sequence[str] | None = None,
    timeout_per_experiment: float = 120.0,
    max_points: int = 2000,
) -> dict[str, Any]:
    """Run selected benchmark cases through real experiment-backed optimization."""
    if not isinstance(service, GlobalDesignOptimizationService):
        raise ValueError("service must be GlobalDesignOptimizationService")
    if (
        isinstance(timeout_per_experiment, bool)
        or not isinstance(timeout_per_experiment, (int, float))
        or not math.isfinite(float(timeout_per_experiment))
        or not 0 < float(timeout_per_experiment) <= 3600
    ):
        raise ValueError("timeout_per_experiment must be between 0 and 3600")
    if (
        isinstance(max_points, bool)
        or not isinstance(max_points, int)
        or not 1 <= max_points <= 100_000
    ):
        raise ValueError("max_points must be between 1 and 100000")
    root = Path(output_directory).expanduser()
    if root.is_symlink():
        raise ValueError("benchmark output must not be a symbolic link")
    root = root.resolve()
    if root == Path(root.anchor):
        raise ValueError("benchmark output must not be a filesystem root")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise FileExistsError("benchmark output directory must be new or empty")
    root.mkdir(parents=True, exist_ok=True)

    selected = _select_cases(case_ids)
    validation = validate_standard_benchmarks([case.case_id for case in selected])
    results: list[dict[str, Any]] = []
    for case in selected:
        try:
            result = service.run(
                case.design,
                dict(case.optimization_spec),
                str(root / case.case_id),
                timeout_per_experiment=float(timeout_per_experiment),
                max_points=max_points,
            )
            recommendation = result.get("recommended_solution")
            assignments = (
                recommendation.get("assignments", {})
                if isinstance(recommendation, Mapping)
                else {}
            )
            expected_selected = all(
                assignments.get(key) == value
                for key, value in case.expected_assignment.items()
            )
            passed = bool(result.get("success")) and expected_selected
            results.append(
                {
                    **case.summary(),
                    "passed": passed,
                    "status": result.get("status"),
                    "experiments_attempted": result.get("experiments_attempted"),
                    "feasible_solution_count": result.get("feasible_solution_count"),
                    "recommended_assignment": dict(assignments),
                    "expected_assignment_selected": expected_selected,
                    "output_dir": str(root / case.case_id),
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    **case.summary(),
                    "passed": False,
                    "status": "error",
                    "experiments_attempted": None,
                    "feasible_solution_count": 0,
                    "recommended_assignment": {},
                    "expected_assignment_selected": False,
                    "output_dir": str(root / case.case_id),
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            )
    passed_count = sum(bool(item["passed"]) for item in results)
    summary: dict[str, Any] = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "mode": "real-multisim",
        "success": passed_count == len(results),
        "status": "passed" if passed_count == len(results) else "failed",
        "case_count": len(results),
        "passed_count": passed_count,
        "failed_count": len(results) - passed_count,
        "validation": validation,
        "cases": results,
        "output_dir": str(root),
        "simulation_performed": True,
    }
    _atomic_json(root / BENCHMARK_SUMMARY_NAME, summary)
    artifacts = {
        path.relative_to(root).as_posix(): (
            "summary" if path.name == BENCHMARK_SUMMARY_NAME else "case-artifact"
        )
        for path in root.rglob("*")
        if path.is_file() and path != root / DIRECTORY_MANIFEST_NAME
    }
    manifest = write_directory_manifest(
        root,
        directory_kind="benchmark-suite",
        entity_id="standard-correction-benchmark-v1",
        state="succeeded" if summary["success"] else "failed",
        artifacts=artifacts,
        metadata={
            "operation": "benchmark-suite",
            "case_count": len(results),
            "passed_count": passed_count,
        },
    )
    summary["manifest"] = manifest.to_dict()
    return summary


def read_benchmark_suite(output_directory: str, *, verify: bool = True) -> dict[str, Any]:
    root = Path(output_directory).expanduser().resolve()
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"benchmark directory does not exist: {root}")
    if verify:
        manifest = read_directory_manifest(root, verify=True)
        if manifest.directory_kind != "benchmark-suite":
            raise ValueError("directory is not a benchmark suite")
    try:
        payload = json.loads((root / BENCHMARK_SUMMARY_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("benchmark summary is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("benchmark summary schema is invalid")
    return payload


__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "BENCHMARK_SUMMARY_NAME",
    "CorrectionBenchmark",
    "read_benchmark_suite",
    "run_standard_benchmarks",
    "standard_benchmark_catalog",
    "validate_standard_benchmarks",
]
