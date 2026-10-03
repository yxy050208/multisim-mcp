"""Capture a version-scoped Multisim Automation compatibility matrix.

The tool probes one installed Multisim instance through the 32-bit worker and
records requested versions that were not installed as ``unverified``.  It
does not save or modify a user circuit; the probe only creates an unsaved blank
design.  A native API result is evidence for the API surface, not a complete
component or circuit-family certification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_server"))

from multisim_mcp.multisim_client import (  # noqa: E402
    MultisimClient,
    runtime_diagnostics,
)
from multisim_mcp.multisim_compat import build_compatibility_matrix  # noqa: E402


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run(target_versions: list[str], output: Path) -> dict[str, Any]:
    output = output.expanduser().resolve()
    if output.exists():
        raise FileExistsError("output must be a new directory")
    output.mkdir(parents=True)
    runtime = runtime_diagnostics()
    _write_json(output / "runtime.json", runtime)
    if not runtime.get("runtime_compatible"):
        matrix = build_compatibility_matrix(target_versions, detected_version="")
        matrix["runtime_error"] = runtime
        _write_json(output / "matrix.json", matrix)
        return matrix

    client = MultisimClient()
    probe: dict[str, Any] = {}
    try:
        probe = client.native_capability_probe(create_blank=True)
    finally:
        client.close()
    _write_json(output / "native-api.json", probe)
    detected = str(probe.get("version", ""))
    matrix = build_compatibility_matrix(
        target_versions or [detected],
        detected_version=detected,
        native_probe=probe,
        evidence_path="native-api.json",
    )
    matrix["runtime"] = {
        "python_bits": runtime.get("python_bits"),
        "multisim_version": detected,
    }
    _write_json(output / "matrix.json", matrix)
    artifacts = []
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            artifacts.append({
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            })
    _write_json(output / "manifest.json", {
        "schema_version": 1,
        "kind": "multisim-compatibility-matrix",
        "artifacts": artifacts,
    })
    return matrix


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--target-version",
        action="append",
        default=[],
        help="requested version; repeat for more than one (default: installed version)",
    )
    args = parser.parse_args()
    result = run(args.target_version, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
