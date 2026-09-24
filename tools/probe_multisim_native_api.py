"""Read-only audit of the installed Multisim Automation type library.

Run with the 32-bit Python used by Multisim.  The default mode only inspects
the application interface; ``--blank`` creates an unsaved blank design so the
circuit interface can also be inspected.
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "mcp_server")
from multisim_mcp.multisim_client import MultisimClient, runtime_diagnostics  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blank", action="store_true", help="create an unsaved blank design")
    args = parser.parse_args()
    result: dict[str, object] = {"runtime": runtime_diagnostics()}
    if not result["runtime"]["runtime_compatible"]:  # type: ignore[index]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    client = MultisimClient()
    try:
        result["native_api"] = client.native_capability_probe(create_blank=args.blank)
    finally:
        client.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
