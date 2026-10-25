"""Run reviewed signal requirement variants sequentially on Multisim 14.3.

--list prints requirements only. --inputs takes a JSON list of objects with
case_id, preview and approval paths (relative to that JSON file). No approvals
are created. All inputs are validated before execution. Source/installed usage
is controlled by Python's import path; this tool does not inject source paths.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from multisim_mcp.signal_multiboard import run_approved_signal_multiboard
from multisim_mcp.signal_multiboard_regression import select_signal_multiboard_regression_cases


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--analyses", choices=("dc", "ac"), nargs="+", default=["dc", "ac"])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    cases = select_signal_multiboard_regression_cases(args.cases)
    if args.list:
        print(json.dumps([case.manifest() for case in cases], indent=2))
        return 0
    if args.inputs is None or args.output is None:
        parser.error("--inputs and --output are required unless --list is used")
    if len(set(args.analyses)) != len(args.analyses):
        parser.error("analyses must be unique")
    entries = json.loads(args.inputs.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        raise ValueError("inputs must be a list of case/preview/approval objects")
    by_id = {e.get("case_id"): e for e in entries}
    selected_ids = {case.case_id for case in cases}
    if len(by_id) != len(entries) or set(by_id) != selected_ids:
        raise ValueError("inputs must contain exactly one entry per selected case")
    loaded = []
    root = args.output.resolve()
    options = {"sweep_type": 0, "num_points": 10, "start_frequency": 10, "stop_frequency": 10000}
    for case in cases:
        entry = by_id[case.case_id]
        preview, approval = [json.loads((args.inputs.parent / entry[k]).read_text(encoding="utf-8"))
                             for k in ("preview", "approval")]
        case.validate_preview(preview, approval)
        for analysis in args.analyses:
            run_approved_signal_multiboard(None, preview, approval, root / f"{case.case_id}-{analysis}",
                analysis=analysis, analysis_options=options if analysis == "ac" else None)
        loaded.append((case, preview, approval))
    if args.execute:
        root.mkdir(parents=True, exist_ok=False)
    records = []
    for case, preview, approval in loaded:
        for analysis in args.analyses:
            destination = root / f"{case.case_id}-{analysis}"
            try:
                result = run_approved_signal_multiboard(None, preview, approval, destination,
                    execute=args.execute, analysis=analysis, analysis_options=options if analysis == "ac" else None)
            except Exception as exc:
                # The matrix root belongs to this invocation; never write into
                # a conflicting child directory. Retain other case results.
                result = {"status": "failed", "verification_status": "unverified",
                          "error": f"{type(exc).__name__}: {exc}"}
            record = {"case_id": case.case_id, "analysis": analysis, "directory": str(destination),
                      "status": result["status"], "verification_status": result["verification_status"],
                      "compiled_digest": preview["compiled_digest"], "error": result.get("error"),
                      "linear_reference_comparison": result.get("linear_reference_comparison")}
            records.append(record)
            print(json.dumps({k: v for k, v in record.items() if k != "linear_reference_comparison"},
                             ensure_ascii=True), flush=True)
            if args.execute:
                _write(root / "matrix.json", {"schema_version": 1, "execution_requested": True,
                    "completed": len(records) == len(cases) * len(args.analyses), "records": records})
    return 0 if all(r["status"] == ("accepted" if args.execute else "logical-only") for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
