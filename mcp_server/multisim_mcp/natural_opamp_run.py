"""Execute the bounded OPAMP5 natural-language contract in native Multisim."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .engineering_task_contract import normalize_task_result, finalize_task_result

from .multisim_client import Ms14Codec
from .com_worker_client import MultisimWorkerProcess, WorkerMultisimClient
from .native_netlist_validation import validate_native_netlist
from .native_opamp_acceptance import evaluate_opamp_ac
from .natural_opamp import parse_natural_opamp_request
from .schematic_builder import build_schematic
from .component_compat import detect_multisim_version, load_manifest_for_version, require_verified_mappings


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run_natural_opamp_engineering(text: str, output: str, *, execute: bool = False,
                                  cancel_requested: Callable[[], bool] | None = None) -> dict[str, Any]:
    proposal = parse_natural_opamp_request(text)
    root = Path(output).expanduser().resolve()
    if root.exists() or root == Path(root.anchor):
        raise FileExistsError("output must be a new directory")
    if not execute:
        return normalize_task_result({"success": True, "mode": "preview", "output_dir": str(root), "proposal": proposal,
                "simulation_started": False, "verification_status": "unverified"})
    root.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {"success": False, "mode": "execute", "output_dir": str(root),
                              "proposal": proposal, "verification_status": "failed"}
    def stage(name: str) -> None:
        result["stage"] = name
        _write(root / "checkpoint.json", {"stage": name, "verification_status": result["verification_status"]})
    _write(root / "proposal.json", proposal)
    (root / "input.txt").write_text(text, encoding="utf-8")
    try:
        multisim_version = detect_multisim_version()
        manifest = load_manifest_for_version(Path(__file__).resolve().parent / "compatibility", multisim_version)
        result["multisim_version"] = multisim_version
        result["component_mappings"] = require_verified_mappings(
            manifest, multisim_version, {"ideal-opamp5": ["in+", "in-", "v+", "v-", "out"]})
        stage("build")
        build = build_schematic(proposal["netlist"], root / "source.xml", probe_nets=["in", "out"])
        _write(root / "build.json", build)
        if build["unsupported"] or build["layout_validation"]["status"] != "pass":
            raise RuntimeError("generated OPAMP schematic failed layout preflight")
        Ms14Codec().encode(str(root / "source.xml"), str(root / "source.ms14"))
        result["native_project"] = str(root / "source.ms14")
        stage("native-open")
        worker = MultisimWorkerProcess()
        client = WorkerMultisimClient(worker)
        try:
            client.call_controlled("open_circuit", str(root / "source.ms14"), rpc_timeout=60,
                                   cancel_requested=cancel_requested)
            stage("native-op")
            op = client.call_controlled("run_dc_operating_point", ["V(OutProbe)", "V(OutProbe1)"],
                                        timeout=60, rpc_timeout=90, cancel_requested=cancel_requested)
            stage("native-ac")
            ac = client.call_controlled("run_ac_sweep", ["V(OutProbe)", "V(OutProbe1)"],
                                        num_points=20, start_frequency=1, stop_frequency=1e5,
                                        timeout=60, rpc_timeout=90, cancel_requested=cancel_requested)
            stage("native-netlist")
            client.call_controlled("report_netlist", str(root / "native-netlist.txt"),
                                   rpc_timeout=60, cancel_requested=cancel_requested)
        finally:
            worker.close()
        topology = validate_native_netlist(
            (root / "native-netlist.txt").read_text(encoding="utf-8", errors="replace"),
            {"RF": {1: "out", 2: "nfb"}, "RG": {1: "nfb", 2: "0"}})
        acceptance = evaluate_opamp_ac(ac, proposal["derived"]["gain"])
        result.update({"operating_point": op, "ac_sweep": ac, "topology_acceptance": topology,
                       "acceptance": acceptance,
                       "success": bool(topology["ok"] and acceptance["passed"]),
                       "verification_status": "passed-supported-opamp-contract"
                       if topology["ok"] and acceptance["passed"] else "target-not-met"})
    except Exception as exc:
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    _write(root / "acceptance.json", result)
    return finalize_task_result(root, result)


__all__ = ["run_natural_opamp_engineering"]
