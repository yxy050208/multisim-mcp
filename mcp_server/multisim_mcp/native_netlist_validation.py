"""Validation of the tabular netlist exported by native Multisim."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Mapping


_ROW = re.compile(r"^\s*(\S+)\s+\S+\s+(\S+)\s+([A-Za-z0-9][A-Za-z0-9_+.-]*)\s*$")


def _pin(value: int | str) -> int | str:
    text = str(value)
    return int(text) if text.isdigit() else text.upper()


def parse_native_netlist(text: str) -> dict[str, dict[int | str, str]]:
    """Parse Multisim's whitespace table into ``refdes -> pin -> net``."""
    result: dict[str, dict[int | str, str]] = defaultdict(dict)
    for line in text.splitlines():
        match = _ROW.match(line)
        if not match:
            continue
        net, refdes, pin = match.group(1), match.group(2), _pin(match.group(3))
        if refdes.casefold() in {"component", "refdes", "reference"}:
            continue
        if pin in result[refdes]:
            raise ValueError(f"duplicate native pin row: {refdes}.{pin}")
        result[refdes][pin] = net
    return dict(result)


def validate_native_netlist(
    text: str,
    expected: Mapping[str, Mapping[int | str, str]] | None = None,
    *, strict: bool = False,
) -> dict[str, Any]:
    if strict and not expected:
        raise ValueError("strict topology verification requires expected pins")
    try:
        actual = parse_native_netlist(text)
    except ValueError as exc:
        return {"ok": False, "components": 0, "connections": 0, "netlist": {},
                "findings": [{"code": "duplicate-pin", "message": str(exc)}]}
    findings: list[dict[str, Any]] = []
    for refdes, pins in actual.items():
        for pin, net in pins.items():
            if net == "0" or net.lower() in {"gnd", "ground"}:
                continue
            if net.startswith("_uc"):
                findings.append({"code": "opaque-net-name", "refdes": refdes, "pin": pin, "net": net})
    if expected:
        for refdes, pins in expected.items():
            for raw_pin, expected_net in pins.items():
                pin = _pin(raw_pin)
                actual_net = actual.get(refdes, {}).get(pin)
                if actual_net != expected_net:
                    findings.append({"code": "pin-net-mismatch", "refdes": refdes, "pin": pin,
                                     "expected": expected_net, "actual": actual_net})
    if strict:
        for refdes, pins in actual.items():
            # Multisim localizes the generated ground symbol's refdes.
            if refdes.startswith("_uc") and pins == {1: "0"}:
                continue
            known = {_pin(pin) for pin in expected.get(refdes, {})}
            for pin, net in pins.items():
                if pin not in known:
                    findings.append({"code": "unexpected-pin", "refdes": refdes, "pin": pin, "net": net})
    mismatches = [item for item in findings if item["code"] in {"pin-net-mismatch", "unexpected-pin"}]
    return {
        "ok": not mismatches and bool(actual),
        "coverage": "all-declared-pins" if strict else "selected-pins" if expected else "parse-only",
        "components": len(actual),
        "connections": sum(len(pins) for pins in actual.values()),
        "netlist": actual,
        "findings": findings,
    }


def validate_native_project_netlist(project_dir: str, ms14_name: str,
                                    expected: Mapping[str, Mapping[int | str, str]] | None = None) -> dict[str, Any]:
    """Export and validate one already-simulated native project."""
    from pathlib import Path
    from .com_worker_client import MultisimWorkerProcess, WorkerMultisimClient
    root = Path(project_dir).expanduser().resolve()
    ms14 = root / ms14_name
    output = root / "native-netlist.txt"
    worker = MultisimWorkerProcess()
    client = WorkerMultisimClient(worker)
    try:
        client.open_circuit(str(ms14))
        client.report_netlist(str(output))
    finally:
        worker.close()
    result = validate_native_netlist(output.read_text(encoding="utf-8", errors="replace"), expected)
    result["path"] = str(output)
    return result
