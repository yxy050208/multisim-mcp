"""Compare digital threshold events across two native transient archives."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_server"))

from multisim_mcp.native_analysis_series import (  # noqa: E402
    DigitalEdgeComparisonContract,
    compare_digital_edge_series,
)


def _read_series(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read transient series JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"transient series JSON must be an object: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", type=Path, help="expected native-tran-series.json")
    parser.add_argument("actual", type=Path, help="actual native-tran-series.json")
    parser.add_argument("--expected-output", required=True)
    parser.add_argument("--actual-output", required=True)
    parser.add_argument("--threshold", type=float, default=2.5)
    parser.add_argument("--level-tolerance", type=float, default=0.05)
    parser.add_argument("--edge-time-tolerance", type=float, default=1e-9)
    parser.add_argument("--minimum-edge-separation", type=float, default=0.0)
    args = parser.parse_args()
    try:
        expected = _read_series(args.expected).get(args.expected_output)
        actual = _read_series(args.actual).get(args.actual_output)
        comparison = compare_digital_edge_series(
            expected,
            actual,
            contract=DigitalEdgeComparisonContract(
                threshold_voltage=args.threshold,
                level_tolerance=args.level_tolerance,
                edge_time_tolerance=args.edge_time_tolerance,
                minimum_edge_separation=args.minimum_edge_separation,
            ),
        )
    except (ValueError, TypeError) as exc:
        print(json.dumps({"status": "invalid", "passed": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(comparison, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if comparison.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
