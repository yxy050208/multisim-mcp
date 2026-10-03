"""Run the version-gated mixed digital/analog Multisim regression matrix."""

from __future__ import annotations

import argparse
import csv
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
from multisim_mcp.native_project_run import run_native_project  # noqa: E402
from multisim_mcp.server import (  # noqa: E402
    _create_schematic_impl,
    client,
)
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


def _native_observation(
    case: HybridRegressionCase,
    native_root: Path,
    probe_outputs: list[str],
) -> dict[str, Any]:
    """Read voltages produced by the saved/reopened native .ms14 project."""
    data_path = native_root / "analysis-001" / "data.csv"
    if not data_path.is_file():
        return {
            "required_outputs": list(case.output_nets),
            "observed_outputs": [],
            "missing_outputs": list(case.output_nets),
            "evidence": "native project CSV missing",
            "checks": {"digital_swing": False, "analog_response": False},
        }
    with data_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    observed: list[str] = []
    series: dict[str, list[float]] = {}
    for net, signal in zip(case.output_nets, probe_outputs):
        column = f"{signal}.value"
        try:
            values = [float(row[column]) for row in rows]
        except (KeyError, TypeError, ValueError):
            continue
        if values:
            observed.append(net)
            series[net] = values
    pairs = list(zip(case.output_nets[0::2], case.output_nets[1::2]))
    pair_checks: dict[str, dict[str, bool]] = {}
    for digital_net, analog_net in pairs:
        digital = series.get(digital_net, [])
        analog = series.get(analog_net, [])
        pair_checks[f"{digital_net}->{analog_net}"] = {
            "digital_swing": bool(digital) and min(digital) <= 0.1 and max(digital) >= 4.9,
            "analog_response": bool(analog) and max(analog) - min(analog) >= 0.1,
        }
    digital_swing = bool(pair_checks) and all(item["digital_swing"] for item in pair_checks.values())
    analog_response = bool(pair_checks) and all(item["analog_response"] for item in pair_checks.values())
    return {
        "required_outputs": list(case.output_nets),
        "observed_outputs": sorted(observed),
        "missing_outputs": sorted(set(case.output_nets) - set(observed)),
        "evidence": "saved native .ms14 reopened by Multisim; native COM transient CSV",
        "sample_count": len(rows),
        "signal_columns": {net: f"{signal}.value" for net, signal in zip(case.output_nets, probe_outputs)},
        "ranges": {
            net: {"min": min(values), "max": max(values)}
            for net, values in series.items()
        },
        "checks": {"digital_swing": digital_swing, "analog_response": analog_response},
        "pair_checks": pair_checks,
    }
def run_case(case: HybridRegressionCase, root: Path) -> dict[str, Any]:
    output_dir = root / case.case_id
    output_dir.mkdir(parents=True, exist_ok=False)
    schematic = _create_schematic_impl(
        case.netlist,
        str(output_dir / "circuit.ms14"),
        probe_nets=list(case.output_nets),
        include_experimental_probes=True,
        open_after_build=True,
        image_path=str(output_dir / "schematic.png"),
        overwrite=False,
        require_layout_pass=True,
    )
    layout = schematic.get("layout_validation", {})
    topology = schematic.get("topology_diff", {})
    probes = schematic.get("build", {}).get("probes", [])
    probe_outputs = [str(item.get("voltage_output")) for item in probes if item.get("voltage_output")]
    request = {
        "schema_version": 1,
        "title": f"Hybrid regression: {case.case_id}",
        "application": "native mixed-signal regression",
        "boards": [{"id": "main", "role": "primary"}],
        "components": [],
        "constraints": [],
        "objectives": [],
        "experiments": [{
            "type": "tran",
            "commands": case.commands,
            "outputs": probe_outputs,
            "max_points": case.max_points,
        }],
    }
    native = run_native_project(
        request,
        str(output_dir / "circuit.ms14"),
        str(output_dir / "native"),
        execute=True,
        client=client,
    )
    observed = _native_observation(case, output_dir / "native", probe_outputs)
    pin_evidence = _pin_evidence_summary(topology)
    checks = {
        "pipeline_success": schematic.get("success") is True and native.get("success") is True,
        "layout_pass": layout.get("status") == "pass"
        and float(layout.get("crossings_per_wire", 0)) <= case.max_crossings_per_wire,
        "topology_pass": topology.get("status") == "pass",
        "native_components_complete": schematic.get("verification", {}).get("native_netlist_complete") is True
        and native.get("simulation_completed") is True,
        "pin_evidence_complete": pin_evidence.get("fully_verified") is True,
        "native_simulation_success": native.get("success") is True,
        "required_outputs_observed": not observed["missing_outputs"],
        "digital_swing": observed["checks"]["digital_swing"],
        "analog_response": observed["checks"]["analog_response"],
    }
    return {
        "case_id": case.case_id,
        "checks": checks,
        "passed": all(checks.values()),
        "layout": layout,
        "topology": topology,
        "hybrid_observation": observed,
        "native_execution": native,
        "pin_evidence": pin_evidence,
        "output_dir": str(output_dir),
        "report": native.get("report"),
        "experiment_id": native.get("experiment_id"),
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
