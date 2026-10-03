"""Run the version-gated mixed digital/analog Multisim regression matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp_server"))

from multisim_mcp.hybrid_regression import (  # noqa: E402
    HybridRegressionCase,
    select_hybrid_regression_cases,
)
from multisim_mcp.server import client, run_circuit_experiment  # noqa: E402
from tools.run_digital_regression import (  # noqa: E402
    _build_compatibility_evidence,
    _pin_evidence_summary,
)


def _observed_outputs(case: HybridRegressionCase, result: dict[str, Any]) -> dict[str, Any]:
    simulation = result.get("simulation")
    columns = simulation.get("columns", []) if isinstance(simulation, dict) else []
    available = {
        str(column)[2:-1]
        for column in columns
        if isinstance(column, str) and column.startswith("V(") and column.endswith(")")
    }
    observed = sorted(available.intersection(case.output_nets))
    return {
        "required_outputs": list(case.output_nets),
        "observed_outputs": observed,
        "missing_outputs": sorted(set(case.output_nets) - set(observed)),
        "evidence": "native transient CSV voltage columns",
    }


def run_case(case: HybridRegressionCase, root: Path) -> dict[str, Any]:
    output_dir = root / case.case_id
    output_dir.mkdir(parents=True, exist_ok=False)
    result = run_circuit_experiment(
        case.netlist,
        case.commands,
        str(output_dir),
        title=f"Hybrid regression: {case.case_id}",
        max_points=case.max_points,
        overwrite=True,
    )
    schematic = result.get("schematic", {})
    simulation = result.get("simulation", {})
    layout = schematic.get("layout_validation", {}) if isinstance(schematic, dict) else {}
    topology = schematic.get("topology_diff", {}) if isinstance(schematic, dict) else {}
    observed = _observed_outputs(case, result)
    pin_evidence = _pin_evidence_summary(topology)
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
        "hybrid_observation": observed,
        "pin_evidence": pin_evidence,
        "output_dir": str(output_dir),
        "report": result.get("report"),
        "experiment_id": result.get("experiment_id"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", help="run one case; omit to run the full matrix")
    parser.add_argument(
        "--target-version",
        help="expected installed Multisim version; a mismatch fails before any case runs",
    )
    args = parser.parse_args()
    cases = select_hybrid_regression_cases(args.case)
    args.output.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    compatibility: dict[str, Any] = {
        "schema_version": 1,
        "status": "unverified",
        "requested_version": args.target_version,
        "detected_version": None,
    }
    run_error: str | None = None
    try:
        connection = client.connect()
        compatibility = _build_compatibility_evidence(
            str(connection.get("version", "")).strip(),
            args.target_version,
            ROOT / "mcp_server" / "multisim_mcp" / "compatibility",
        )
        if compatibility["status"] != "manifest-verified":
            run_error = (
                "mixed-signal regression refused: exact verified component manifest "
                f"required ({compatibility['status']})"
            )
        else:
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
    except Exception as exc:
        run_error = str(exc)[:1000]
    finally:
        client.disconnect()
    summary = {
        "schema_version": 1,
        "compatibility": compatibility,
        "matrix": [case.manifest() for case in cases],
        "results": results,
        "passed": (
            compatibility.get("status") == "manifest-verified"
            and bool(results)
            and all(item.get("passed") is True for item in results)
        ),
        "pin_evidence": {
            "fully_verified_cases": sum(
                item.get("pin_evidence", {}).get("fully_verified") is True
                for item in results
            ),
            "mismatch_cases": sum(
                item.get("pin_evidence", {}).get("mismatch_count", 0) > 0
                for item in results
            ),
            "policy": (
                "unverified pin-to-net mapping remains explicit evidence; it is "
                "never promoted to a passing claim"
            ),
        },
    }
    if run_error:
        summary["error"] = run_error
    (args.output / "matrix.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
