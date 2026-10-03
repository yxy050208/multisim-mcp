"""Probe whether a Multisim project carries a usable ``.TEMP`` corner.

The probe is deliberately copy-based.  It never saves over the source
project, and it retains the command logs, OP results, saved copies and a
machine-readable summary for later review.  A completed ``run_command_file``
call is not considered success when the log says that XSPICE rejected
``.temp``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "mcp_server"))

from multisim_mcp.com_worker_client import MultisimWorkerProcess, WorkerMultisimClient
from multisim_mcp.native_temperature import (
    classify_temperature_command_log,
    temperature_capability,
)


def _parse_temperatures(values: list[str]) -> list[float]:
    if not values:
        return [0.0, 25.0, 85.0]
    result = [float(value) for value in values]
    if any(value != value or value in (float("inf"), float("-inf")) for value in result):
        raise ValueError("temperatures must be finite")
    if len(result) != len(set(result)):
        raise ValueError("temperatures must be unique")
    return result


def probe(source: Path, output: Path, temperatures: list[float]) -> dict[str, Any]:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if source.suffix.lower() != ".ms14" or not source.is_file():
        raise ValueError("source must be an existing .ms14 file")
    if output.exists():
        raise FileExistsError("output must be a new directory")
    output.mkdir(parents=True)
    snapshot = output / "source.ms14"
    shutil.copy2(source, snapshot)
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    worker = MultisimWorkerProcess(startup_timeout=30)
    client = WorkerMultisimClient(worker)
    result: dict[str, Any] = {
        "schema_version": 1,
        "source": str(source),
        "source_sha256": source_digest,
        "snapshot": snapshot.name,
        "temperatures_c": temperatures,
        "results": [],
    }
    try:
        result["connect"] = client.connect()
        result["open"] = client.open_circuit(str(snapshot))
        outputs = client.enum_outputs()
        requested = [name for name in outputs if name.startswith("V(OutProbe")]
        if not requested:
            requested = outputs[:6]
        if not requested:
            raise RuntimeError("the project exposes no output channels for comparison")
        result["outputs"] = outputs
        result["requested_outputs"] = requested
        for temperature in temperatures:
            command = output / f"temp_{temperature:g}.cmd"
            log = output / f"temp_{temperature:g}.log"
            saved = output / f"temperature_{temperature:g}.ms14"
            command.write_text(f".temp {temperature:g}\nop\n", encoding="ascii")
            command_result = client.run_command_file(str(command), str(log), timeout=90)
            classification = classify_temperature_command_log(command_result.get("log", ""))
            before = client.run_dc_operating_point(requested, timeout=60, max_points=100)
            client.save_circuit(str(saved))
            reopened = client.open_circuit(str(saved))
            after = client.run_dc_operating_point(requested, timeout=60, max_points=100)
            result["results"].append({
                "temperature_c": temperature,
                "command": command.name,
                "log": log.name,
                "command_result": command_result,
                "command_probe": classification,
                "before_reopen_op": before,
                "saved_project": saved.name,
                "reopen": reopened,
                "after_reopen_op": after,
            })
    finally:
        worker.close()
    unsupported = [item["command_probe"] for item in result["results"] if item["command_probe"]["status"] == "unsupported"]
    result["temperature_capability"] = temperature_capability(
        native_writer=False,
        command_probe={
            "status": "unsupported" if unsupported else "unverified",
            "points": len(result["results"]),
            "unsupported_points": len(unsupported),
        },
    )
    (output / "probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            artifacts.append({
                "path": path.relative_to(output).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            })
    (output / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "kind": "native-temperature-probe", "artifacts": artifacts},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--temperature", dest="temperatures", action="append", default=[])
    args = parser.parse_args()
    result = probe(args.source, args.output, _parse_temperatures(args.temperatures))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
