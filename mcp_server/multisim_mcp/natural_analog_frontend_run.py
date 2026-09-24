"""Natural-language sensor front end routed through the verified analog runner."""
from typing import Any
from pathlib import Path
import json
from .natural_analog_frontend import parse_natural_analog_frontend, generate_analog_candidates
from .generated_analog_run import run_generated_analog_project
def run_natural_analog_frontend(text: str, output: str, *, execute: bool=False) -> dict[str, Any]:
    plan = parse_natural_analog_frontend(text)
    root = Path(output).expanduser().resolve()
    if root.exists() or root == Path(root.anchor): raise FileExistsError("output must be a new directory")
    if not execute:
        return {"success":True,"mode":"preview","output_dir":str(root),"verification_status":"unverified","natural_language_plan":plan}
    root.mkdir(parents=True)
    grid = generate_analog_candidates(plan)
    candidates = []
    target = plan["derived"]["input_v"] * plan["derived"]["gain"]

    def checkpoint(selected_name=None, selected_result=None, status="running"):
        summary = {
            "success": bool(selected_result and selected_result.get("success")),
            "mode": "execute", "output_dir": str(root),
            "verification_status": (selected_result or {}).get("verification_status", status),
            "natural_language_plan": plan,
            "optimization": {
                "strategy": "native-measurement-feedback-and-zero-offset-calibration",
                "candidate_grid": [{k: i[k] for k in ("rf_scale", "c_scale", "rf_ohm", "c_f")} for i in grid],
                "candidates": [{"name": i["name"], "success": i["result"].get("success"),
                                "verification_status": i["result"].get("verification_status")} for i in candidates],
                "selected": selected_name, "calibration": calibration,
            },
        }
        if selected_result is not None:
            summary["selected_result"] = selected_result
        (root / "acceptance.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    calibration = None
    for index, item in enumerate(grid, 1):
        name = "uncalibrated" if index == 1 else f"grid-{index:03d}"
        try:
            child = run_generated_analog_project(item["proposal"], str(root/f"candidate-{name}"), execute=True)
        except Exception as exc:
            child = {"success": False, "verification_status": "failed", "error": str(exc)}
        candidates.append({"name":name,"result":child,"parameters":item})
        checkpoint(status="partial")
    raw_best = min(candidates, key=lambda item: abs(next((c.get("measured_max", 1e9) for c in item["result"].get("measurement_acceptance",{}).get("checks",[]) if c.get("requirement",{}).get("analysis")=="op"),1e9)-plan["derived"]["input_v"]*plan["derived"]["gain"]))
    baseline = raw_best["result"]
    calibration = None
    acceptance = baseline.get("measurement_acceptance", {})
    checks = acceptance.get("checks", []) if isinstance(acceptance, dict) else []
    op = next((c.get("measured_max") for c in checks if c.get("requirement",{}).get("analysis")=="op"), None)
    ac = next((c.get("measured_max") for c in checks if c.get("requirement",{}).get("analysis")=="ac" and c.get("requirement",{}).get("frequency_max_hz",0) <= plan["derived"]["cutoff_hz"]), None)
    if isinstance(op,(int,float)) and isinstance(ac,(int,float)) and ac > 0 and abs(op-target) > target*.005:
        trim = (target - op) / ac
        calibrated = dict(raw_best["parameters"]["proposal"])
        calibrated["netlist"] = calibrated["netlist"].replace("R1 in lp1", f"VTRIM adjusted in DC {trim * 1000:.6g}m\nR1 adjusted lp1")
        calibrated["application"] += f"；根据首次原生测量加入零点校准候选（{trim * 1000:.6g}mV）。"
        calibration = {"trim_voltage_v":trim,"derived_from":{"measured_output_v":op,"measured_gain":ac}}
        try:
            child = run_generated_analog_project(calibrated, str(root/"candidate-calibrated"), execute=True)
        except Exception as exc:
            child = {"success": False, "verification_status": "failed", "error": str(exc)}
        candidates.append({"name":"calibrated","result":child,"parameters":{"rf_scale":raw_best["parameters"]["rf_scale"],"c_scale":raw_best["parameters"]["c_scale"],"trim_voltage_v":trim}})
        checkpoint(status="partial")
    selected = next((item for item in reversed(candidates) if item["result"].get("success")), candidates[-1])
    return checkpoint(selected["name"], selected["result"], "failed")
