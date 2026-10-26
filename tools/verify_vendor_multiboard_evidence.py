"""Recheck local LM324AJ two-board evidence without starting Multisim."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from multisim_mcp.vendor_multiboard_evidence import verify_vendor_multiboard_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", type=Path, nargs="+")
    args = parser.parse_args()
    results = []
    for root in args.directories:
        try:
            result = verify_vendor_multiboard_evidence(root)
        except ValueError as exc:
            result = {"status": "fail", "error": str(exc), "current_machine_native_verified": False}
        results.append({"directory": str(root), **result})
    print(json.dumps(results, ensure_ascii=True, indent=2, allow_nan=False))
    return 0 if all(r["status"] == "pass" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
