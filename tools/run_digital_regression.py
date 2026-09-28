"""Run the complex digital Multisim regression matrix on a licensed host.

This is a manual acceptance runner.  It writes only local experiment artifacts
and a compact ``matrix.json`` summary; do not publish outputs containing local
Multisim model data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_server"))

from multisim_mcp.digital_regression import (  # noqa: E402
    DigitalRegressionCase,
    select_digital_regression_cases,
)
from multisim_mcp.server import client, run_circuit_experiment  # noqa: E402


def _observed_outputs(case: DigitalRegressionCase, result: dict[str, Any]) -> dict[str, Any]:
    evidence = result.get("digital_observation")
    if not isinstance(evidence, dict):
        simulation = result.get("simulation")
        evidence = simulation.get("digital_observation") if isinstance(simulation, dict) else None
    signals = evidence.get("signals", []) if isinstance(evidence, dict) else []
    observed = {
        str(item.get("net"))
        for item in signals
        if isinstance(item, dict) and item.get("status") == "observed"
    }
    return {
        "overall_status": evidence.get("overall_status") if isinstance(evidence, dict) else "unverified",
        "required_outputs": list(case.output_nets),
        "observed_outputs": sorted(observed.intersection(case.output_nets)),
        "missing_outputs": sorted(set(case.output_nets) - observed),
    }


def run_case(case: DigitalRegressionCase, root: Path) -> dict[str, Any]:
    output_dir = root / case.case_id
    output_dir.mkdir(parents=True, exist_ok=False)
    result = run_circuit_experiment(
        case.netlist,
        case.commands,
        str(output_dir),
        title=f"Digital regression: {case.case_id}",
        max_points=case.max_points,
        overwrite=True,
    )
    schematic = result.get("schematic", {})
    simulation = result.get("simulation", {})
    layout = schematic.get("layout_validation", {}) if isinstance(schematic, dict) else {}
    topology = schematic.get("topology_diff", {}) if isinstance(schematic, dict) else {}
    observed = _observed_outputs(case, result)
    checks = {
        "pipeline_success": result.get("success") is True,
        "layout_pass": layout.get("status") == "pass"
        and float(layout.get("crossings_per_wire", 0)) <= case.max_crossings_per_wire,
        "topology_pass": topology.get("status") == "pass",
        "native_components_complete": schematic.get("verification", {}).get("native_netlist_complete") is True
        if isinstance(schematic, dict)
        else False,
        "simulation_success": simulation.get("success") is True if isinstance(simulation, dict) else False,
        "required_outputs_observed": not observed["missing_outputs"],
    }
    return {
        "case_id": case.case_id,
        "checks": checks,
        "passed": all(checks.values()),
        "layout": layout,
        "topology": topology,
        "digital_observation": observed,
        "output_dir": str(output_dir),
        "report": result.get("report"),
        "experiment_id": result.get("experiment_id"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", help="run one case; omit to run the full matrix")
    args = parser.parse_args()
    cases = select_digital_regression_cases(args.case)
    args.output.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    try:
        for case in cases:
            print(f"Running {case.case_id}...", file=sys.stderr, flush=True)
            try:
                results.append(run_case(case, args.output))
            except Exception as exc:
                results.append({
                    "case_id": case.case_id,
                    "passed": False,
                    "checks": {"exception_free": False},
                    "error": str(exc)[:1000],
                })
    finally:
        client.disconnect()
    summary = {
        "schema_version": 1,
        "matrix": [case.manifest() for case in cases],
        "results": results,
        "passed": bool(results) and all(item.get("passed") is True for item in results),
    }
    (args.output / "matrix.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
