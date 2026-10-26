"""Execute a reviewed ideal signal preview through the fixed two-board gate.

Supply approval files produced by the public review workflow. This runner
does not grant approval, infer fixtures or optimize the partition. Native
template assets remain in the user's licensed local component pack.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from multisim_mcp.signal_multiboard import run_approved_signal_multiboard


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory; existing projects are never overwritten")
    parser.add_argument("--analysis", choices=("dc", "ac"), default="dc")
    parser.add_argument("--start-hz", type=float, default=10.0)
    parser.add_argument("--stop-hz", type=float, default=10000.0)
    parser.add_argument("--points-per-decade", type=int, default=10)
    parser.add_argument("--execute", action="store_true", help="Generate and measure in the installed Multisim 14.3")
    args = parser.parse_args()
    preview = json.loads(args.preview.read_text(encoding="utf-8"))
    approval = json.loads(args.approval.read_text(encoding="utf-8"))
    options = {"sweep_type": 0, "num_points": args.points_per_decade,
               "start_frequency": args.start_hz, "stop_frequency": args.stop_hz} if args.analysis == "ac" else None
    result = run_approved_signal_multiboard(None, preview, approval, args.output,
        execute=args.execute, analysis=args.analysis, analysis_options=options)
    print(json.dumps({k: result.get(k) for k in ("status", "verification_status", "analysis", "output_directory",
        "report", "error", "linear_reference_comparison")}, ensure_ascii=True, indent=2))
    return 0 if result["status"] in {"accepted", "logical-only"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
