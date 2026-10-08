"""Run the two-board digital acceptance case on a licensed Multisim host.

The runner is intentionally separate from the MCP tool.  It provides a
repeatable local evidence directory for DC, TRAN and AC, while keeping vendor
projects, model XML and screenshots outside the repository.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_server"))

from multisim_mcp.multiboard_digital_regression import (  # noqa: E402
    DigitalMultiboardRegressionCase,
    select_multiboard_digital_regression_cases,
)
from multisim_mcp.server import run_native_multiboard_acceptance  # noqa: E402


def _analysis_options(analysis: str) -> dict[str, Any]:
    if analysis == "dc":
        return {"timeout": 120.0, "max_points": 2000}
    if analysis == "tran":
        return {
            "sample_rate": 100_000.0,
            "num_samples": 100,
            "duration": 0.0001,
            "timeout": 120.0,
            "max_points": 2000,
            "series_alignment": "linear",
        }
    return {
        "sweep_type": 0,
        "num_points": 10,
        "start_frequency": 100.0,
        "stop_frequency": 1_000_000.0,
        "timeout": 120.0,
        "max_points": 2000,
        "series_alignment": "linear",
    }


def _preview_checks(case: DigitalMultiboardRegressionCase, result: dict[str, Any]) -> dict[str, bool]:
    prepared = result.get("prepared_artifacts", {})
    boards = prepared.get("boards", []) if isinstance(prepared, dict) else []
    return {
        "fixture_contract_complete": prepared.get("fixture_contract", {}).get("coverage_status") == "complete"
        if isinstance(prepared, dict) else False,
        "native_connector_verified": prepared.get("native_connector_status") == "native-verified"
        if isinstance(prepared, dict) else False,
        "two_boards_materialized": len(boards) == 2,
        "declared_outputs_present": bool(case.output_nets),
    }


def _native_checks(result: dict[str, Any]) -> dict[str, bool]:
    acceptance = result.get("native_acceptance", {})
    if not isinstance(acceptance, dict):
        return {"native_acceptance": False}
    return {str(name): value is True for name, value in acceptance.items()}


def run_case(
    case: DigitalMultiboardRegressionCase,
    output_root: Path,
    analysis: str,
    *,
    execute: bool,
    target_version: str,
) -> dict[str, Any]:
    output_directory = output_root / case.case_id / analysis
    result = run_native_multiboard_acceptance(
        case.request,
        str(output_directory),
        execute=execute,
        target_multisim_version=target_version,
        analysis=analysis,
        analysis_options=_analysis_options(analysis),
    )
    checks = _native_checks(result) if execute else _preview_checks(case, result)
    return {
        "case_id": case.case_id,
        "analysis": analysis,
        "execute": execute,
        "checks": checks,
        "passed": all(checks.values()),
        "status": result.get("status"),
        "verification_status": result.get("verification_status"),
        "native_connector_status": result.get("native_connector_status"),
        "output_directory": str(output_directory),
        "acceptance": result.get("native_acceptance") if execute else None,
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", help="run one case; omit to run the matrix")
    parser.add_argument("--analysis", choices=("dc", "tran", "ac", "all"), default="dc")
    parser.add_argument("--target-version", default="Multisim 14.3")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="open Multisim and run native acceptance; without this flag only a COM-free preview is produced",
    )
    args = parser.parse_args()
    cases = select_multiboard_digital_regression_cases(args.case)
    analyses = ("dc", "tran", "ac") if args.analysis == "all" else (args.analysis,)
    args.output.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    for case in cases:
        for analysis in analyses:
            print(f"Running {case.case_id}/{analysis} ({'native' if args.execute else 'preview'})...", file=sys.stderr, flush=True)
            try:
                results.append(run_case(
                    case,
                    args.output,
                    analysis,
                    execute=args.execute,
                    target_version=args.target_version,
                ))
            except Exception as exc:
                results.append({
                    "case_id": case.case_id,
                    "analysis": analysis,
                    "execute": args.execute,
                    "checks": {"exception_free": False},
                    "passed": False,
                    "status": "failed",
                    "verification_status": "unverified",
                    "error": str(exc)[:2000],
                })
    summary = {
        "schema_version": 1,
        "kind": "multisim-mcp-multiboard-digital-regression",
        "target_version": args.target_version,
        "execute": args.execute,
        "analyses": list(analyses),
        "results": results,
        "passed": bool(results) and all(item.get("passed") is True for item in results),
        "policy": "native acceptance is claimed only when every selected gate is true; preview remains unverified",
    }
    (args.output / "matrix.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
