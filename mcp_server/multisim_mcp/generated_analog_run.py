"""Composable native analog execution for MCP hosts that supply circuit plans."""
from __future__ import annotations

import csv
import html
import hashlib
import json
import math
import re
import shutil
from functools import partial
from pathlib import Path
from typing import Any, Mapping

from .engineering_task_contract import finalize_task_result, normalize_task_result
from .linear_reference import expected_native_pins, solve_linear, validated_components
from .native_project_analysis import analyze_current_project, analysis_parameters
from .native_project_run import run_native_project
from .schematic_builder import build_schematic

LIMITATIONS = [
    "Only ideal R/C/L, DC/AC voltage sources and OPAMP5/IDEALOPAMP are accepted.",
    "Opamp open-loop gain is 100000; supply limits, bandwidth, current limits and noise are not modeled.",
    "Checks apply to the requested sampled frequencies, not all frequencies, tolerances or operating conditions.",
    "Multisim 14.3 native execution is verified locally; other versions are not certified by this workflow.",
    "Geometry preflight checks pins and symbol bodies; wire crossings and label placement still need visual review.",
]
VENDOR_LIMITATIONS = [
    "Diodes require the licensed local 1N4001GP model; no replacement with another diode identity is permitted.",
    "LM324AJ uses a licensed local LM158_4 macromodel, section A of each separate package; other vendor models are not certified.",
    "2N3904 QNPN uses the licensed local BJT model; measured circuit acceptance does not certify arbitrary transistor circuits.",
    "Vendor results are checked against declared native measurements; no independent ideal-model equivalence is claimed.",
    "Transient checks apply only to raw solver samples within declared time windows; no worst-case, tolerance or physical-board validation is implied.",
    *LIMITATIONS[2:],
]


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")


def vendor_model_fingerprints(path: Path, parts: list[Any]) -> dict[str, Any]:
    """Compare saved native model identities and bodies without publishing them."""
    from .native_xml import parse_native_xml
    root = parse_native_xml(path).getroot()
    wanted = {p.refdes: p.kind for p in parts if p.kind in {"LM324AJ", "QNPN", "D"}}
    models = {item.get("CiID"): item.find("CiModel") for item in root.iter("Item") if item.find("CiModel") is not None}
    found = {}
    for component in root.iter("CiComponent"):
        ref = component.get("LocalName", "").removeprefix("&ASC")
        if ref not in wanted:
            continue
        primary = component.find("./Attributes/Item/CiaCollString/strings")
        values = [item.get("Value", "").removeprefix("&ASC") for item in primary] if primary is not None else []
        model = models.get(component.get("Model"))
        if wanted[ref] in {"QNPN", "D"}:
            identity = ["2N3904", "BJT_NPN", "2N3904"] if wanted[ref] == "QNPN" else ["1N4001GP", "DIODE", "D1N4001GP"]
            model_type = "NPN" if wanted[ref] == "QNPN" else "D"
            if ref in found or len(values) < 6 or values[1:4] != identity or model is None:
                raise ValueError("native semiconductor identity or linked model missing/changed")
            body = values[5]
            linked = [e.get("String", "").removeprefix("&ASC") for e in model.iter("CiaCString") if re.match(r"(?i)^&ASC\.model\s", e.get("String", ""))]
            expression = component.find("./Attributes/Item/CiaSpiceTmpltExprt")
            if not re.match(r"(?is)^\.model\s+" + identity[2] + r"\s+" + model_type + r"\s*\(.+\)\s*$", body) or not linked or expression is None:
                raise ValueError("native semiconductor model definition or expression missing")
            digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
            found[ref] = {"database_identity": values[1], "model_name": values[3], "definition_sha256": digest(body),
                          "linked_definition_sha256": [digest(b) for b in linked],
                          "spice_template_sha256": digest(expression.get("String", ""))}
            continue
        if ref in found or len(values) < 6 or values[1:4] != ["LM324AJ", "OPAMP", "LM158_4"] or model is None:
            raise ValueError("native vendor model identity or linked model is missing/changed")
        body = values[5]
        linked_name = model.get("LocalName", "").removeprefix("&ASC")
        linked_bodies = [v for item in model.iter() for v in item.attrib.values() if linked_name and re.search(r"(?im)^\s*\.subckt\s+" + re.escape(linked_name) + r"\s", v.removeprefix("&ASC"))]
        if not re.search(r"(?im)^\s*\.ends\b", body) or not linked_bodies:
            raise ValueError("vendor model body is missing or flattened")
        expression = component.find("./Attributes/Item/CiaSpiceTmpltExprt")
        if expression is None:
            raise ValueError("vendor SPICE expression missing")
        digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
        found[ref] = {"database_identity":values[1], "model_name":values[3],
                      "definition_sha256":digest(body), "linked_definition_sha256":[digest(v) for v in linked_bodies],
                      "spice_template_sha256":digest(expression.get("String", ""))}
    if set(found) != set(wanted):
        raise ValueError("native vendor component missing")
    return found


def validate_proposal(proposal: Mapping[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    allowed = {"title", "application", "netlist", "probe_nets", "experiments", "checks"}
    if not isinstance(proposal, Mapping) or set(proposal)-allowed:
        raise ValueError("unknown generated analog proposal fields")
    parts = validated_components(proposal.get("netlist"), allow_vendor=True)
    vendor = any(p.kind in {"LM324AJ", "QNPN", "D"} for p in parts)
    if not vendor:
        parts = validated_components(proposal.get("netlist"))
    for field in ("title", "application"):
        if not isinstance(proposal.get(field), str) or not 1 <= len(proposal[field]) <= 4000:
            raise ValueError(f"{field} is required (1..4000 characters)")
    nets = proposal.get("probe_nets")
    available = {n for p in parts for n in p.nodes}-{ "0" }
    if not isinstance(nets, list) or not 1 <= len(nets) <= 16 or any(not isinstance(n,str) or n not in available for n in nets) or len(set(nets)) != len(nets):
        raise ValueError("probe_nets must name 1..16 distinct non-ground circuit nets")
    outputs = [f"V(OutProbe{i if i else ''})" for i in range(len(nets))]
    experiments = proposal.get("experiments", [{"type":"op"}, {"type":"ac", "commands":"ac dec 40 10 100k"}])
    if not isinstance(experiments, list) or not 1 <= len(experiments) <= (3 if vendor else 2):
        raise ValueError("provide at most one of each supported analysis")
    normalized, kinds, ranges = [], set(), {}
    for experiment in experiments:
        if not isinstance(experiment, dict) or set(experiment)-{"type", "commands"}:
            raise ValueError("experiments only accept type and commands")
        kind = experiment.get("type")
        if kind not in ({"op", "ac", "tran"} if vendor else {"op", "ac"}) or kind in kinds:
            raise ValueError("provide unique supported analyses; transient requires an explicit native vendor model")
        kinds.add(kind)
        commands = experiment.get("commands", {"op":"op", "ac":"ac dec 40 10 100k", "tran":"tran 10u 5m"}[kind])
        parameters = analysis_parameters({"analysis":kind, "commands":commands})
        if kind == "ac":
            ranges[kind] = (parameters["start_frequency"],parameters["stop_frequency"])
            span = parameters["stop_frequency"]/parameters["start_frequency"]
            points = parameters["num_points"] if parameters["sweep_type"] == 2 else parameters["num_points"]*math.log(span,10 if parameters["sweep_type"] == 0 else 2)+1
            if points > 2000:
                raise ValueError("linear reference verification is bounded to 2000 AC points")
        if kind == "tran":
            ranges[kind] = (0, parameters["duration"])
        normalized.append({"type":kind,"commands":commands,"outputs":outputs,"timeout":60})
    checks = proposal.get("checks", [])
    if not isinstance(checks,list) or len(checks)>32:
        raise ValueError("checks must contain at most 32 sampled acceptance ranges")
    for check in checks:
        if not isinstance(check,dict) or set(check)-{"analysis","net","reference_net","subtract_net","quantity","min","max","frequency_min_hz","frequency_max_hz","time_min_s","time_max_s"}:
            raise ValueError("unsupported acceptance check fields")
        kind, quantity = check.get("analysis"), check.get("quantity")
        if kind not in kinds or check.get("net") not in nets:
            raise ValueError("check must refer to a requested analysis and probed net")
        if check.get("reference_net") is not None and check["reference_net"] not in nets:
            raise ValueError("reference_net must be a probed net")
        if check.get("subtract_net") is not None and (check["subtract_net"] not in nets or check.get("reference_net") is not None):
            raise ValueError("subtract_net must be probed and cannot combine with reference_net")
        if quantity not in ({"value", "mean", "ripple_vpp", "rms"} if kind == "tran" else {"value"} if kind == "op" else {"magnitude","phase_deg"}):
            raise ValueError("OP supports value; TRAN value/mean/ripple_vpp/rms; AC magnitude/phase_deg")
        if kind != "tran" and ("time_min_s" in check or "time_max_s" in check):
            raise ValueError("time constraints require a transient check")
        for key in ("min","max"):
            if isinstance(check.get(key),bool) or not isinstance(check.get(key),(int,float)) or not math.isfinite(check[key]):
                raise ValueError("checks require finite min and max")
        if check["min"]>check["max"]:
            raise ValueError("check min must not exceed max")
        if kind == "ac":
            for key in ("frequency_min_hz","frequency_max_hz"):
                if isinstance(check.get(key),bool) or not isinstance(check.get(key),(int,float)) or not math.isfinite(check[key]) or check[key]<=0:
                    raise ValueError("AC checks require finite positive frequency_min_hz and frequency_max_hz")
            if check["frequency_min_hz"]>check["frequency_max_hz"]:
                raise ValueError("AC check frequency range is reversed")
            start,stop = ranges[kind]
            if check["frequency_min_hz"] < start or check["frequency_max_hz"] > stop:
                raise ValueError("AC check frequency band must be inside the requested native sweep")
        elif "frequency_min_hz" in check or "frequency_max_hz" in check:
            raise ValueError("OP checks cannot contain frequency constraints")
        if kind == "tran":
            for key in ("time_min_s", "time_max_s"):
                if isinstance(check.get(key),bool) or not isinstance(check.get(key),(int,float)) or not math.isfinite(check[key]):
                    raise ValueError("transient checks require finite time_min_s and time_max_s")
            if not 0 <= check["time_min_s"] < check["time_max_s"] <= ranges[kind][1]:
                raise ValueError("transient window must have positive width inside the requested duration")
    required = {"op", "tran"} if any(p.kind == "D" for p in parts) else {"op", "ac"}
    if vendor and (not required.issubset(kinds) or {c["analysis"] for c in checks} != kinds):
        raise ValueError("vendor models require OP and AC (OP and TRAN for diodes) plus explicit checks for every requested analysis")
    # Structural singularities are rejected before a native worker is started.
    if not vendor:
        solve_linear(parts)
    result = dict(proposal, experiments=normalized, checks=checks)
    result["verification_method"] = "native-vendor-sampled" if vendor else "independent-linear-reference"
    result["limitations"] = list(VENDOR_LIMITATIONS if vendor else LIMITATIONS)
    return result,parts


def transient_statistic(points: list[tuple[float, float]], start: float, stop: float, quantity: str) -> dict[str, Any]:
    """Integrate raw samples with interpolated window boundaries; reject sparse/truncated evidence."""
    if len(points) < 3 or any(not math.isfinite(t) or not math.isfinite(v) for t,v in points):
        raise ValueError("transient statistic requires finite raw samples")
    if any(b[0] <= a[0] for a,b in zip(points, points[1:])):
        raise ValueError("transient time axis must increase strictly")
    if points[0][0] > start or points[-1][0] < stop - 1e-10:
        raise ValueError("transient samples do not cover the requested window")
    def boundary(time: float) -> tuple[float, float]:
        for a,b in zip(points, points[1:]):
            if a[0] <= time <= b[0] + 1e-10:
                weight = min(1., (time-a[0])/(b[0]-a[0]))
                return time, a[1] + weight*(b[1]-a[1])
        raise ValueError("missing transient boundary")
    window = [boundary(start), *[(t,v) for t,v in points if start < t < stop], boundary(stop)]
    if len(window) < 21 or max(b[0]-a[0] for a,b in zip(window,window[1:])) > (stop-start)/20 * (1+1e-9):
        raise ValueError("transient statistic has insufficient time resolution")
    lo,hi = min(v for _,v in window),max(v for _,v in window)
    mean = sum((b[0]-a[0])*(a[1]+b[1])/2 for a,b in zip(window,window[1:]))/(stop-start)
    square = sum((b[0]-a[0])*(a[1]**2+a[1]*b[1]+b[1]**2)/3 for a,b in zip(window,window[1:]))/(stop-start)
    measured = {"mean":mean,"ripple_vpp":hi-lo,"rms":math.sqrt(max(0.,square))}[quantity]
    return {"measured_value":measured,"waveform_min":lo,"waveform_max":hi,"window_start_s":start,"window_stop_s":stop,"samples":len(window)}


def evaluate_evidence(root: Path, proposal: dict[str, Any], parts: list[Any]) -> dict[str, Any]:
    channels = dict(zip(proposal["probe_nets"], proposal["experiments"][0]["outputs"]))
    measurements, comparisons = {}, []
    reference_applicable = not any(p.kind in {"LM324AJ", "QNPN", "D"} for p in parts)
    for index, experiment in enumerate(proposal["experiments"],1):
        kind = experiment["type"]
        with (root/f"analysis-{index:03d}"/"data.csv").open(encoding="utf-8",newline="") as stream:
            rows = list(csv.DictReader(stream))
        if not rows:
            raise ValueError("empty native measurement table")
        records = []
        worst = 0.0
        for row in rows:
            frequency = float(row["frequency_hz"]) if kind == "ac" else float(row["time_s"]) if kind == "tran" else None
            expected = solve_linear(parts,frequency) if reference_applicable else None
            voltages = {net:(complex(float(row[f"{signal}.real"]),float(row[f"{signal}.imaginary"])) if kind=="ac" else complex(float(row[f"{signal}.value"]))) for net,signal in channels.items()}
            for net,actual in voltages.items():
                if not math.isfinite(abs(actual)):
                    raise ValueError("nonfinite native voltage")
                if expected is not None:
                    normalized_error = abs(actual-expected[net])/(1e-7+1e-4*abs(expected[net]))
                    worst = max(worst,normalized_error)
            records.append((frequency,voltages))
        if reference_applicable:
            comparisons.append({"analysis":kind,"samples_per_channel":len(rows),"channels":len(channels),
                                "max_error_over_tolerance":worst,"passed":worst<=1})
        measurements[kind] = records
    checks = []
    for requirement in proposal["checks"]:
        values = []
        aggregate = requirement["analysis"] == "tran" and requirement["quantity"] in {"mean", "ripple_vpp", "rms"}
        points = []
        for frequency,voltages in measurements[requirement["analysis"]]:
            if frequency is not None:
                low,high = ("time_min_s","time_max_s") if requirement["analysis"] == "tran" else ("frequency_min_hz","frequency_max_hz")
                if not aggregate and not requirement[low]*(1-1e-9)<=frequency<=requirement[high]*(1+1e-9):
                    continue
            value = voltages[requirement["net"]]
            if requirement.get("subtract_net"):
                value -= voltages[requirement["subtract_net"]]
            if requirement.get("reference_net"):
                denominator = voltages[requirement["reference_net"]]
                if abs(denominator)<1e-15:
                    raise ValueError("acceptance ratio has zero reference voltage")
                value /= denominator
            if aggregate:
                points.append((frequency, value.real))
                continue
            measured = value.real if requirement["quantity"]=="value" else abs(value) if requirement["quantity"]=="magnitude" else math.degrees(math.atan2(value.imag,value.real))
            if requirement["quantity"] == "phase_deg":
                measured = (measured + 180) % 360 - 180
            values.append(measured)
        if aggregate:
            statistic = transient_statistic(points,requirement["time_min_s"],requirement["time_max_s"],requirement["quantity"])
            checks.append({"requirement":requirement,**statistic,"passed":requirement["min"]<=statistic["measured_value"]<=requirement["max"]})
            continue
        checks.append({"requirement":requirement,"samples":len(values),
                       "measured_min":min(values) if values else None,"measured_max":max(values) if values else None,
                       "passed":bool(values) and all(requirement["min"]<=v<=requirement["max"] for v in values)})
    return {"reference_applicable":reference_applicable,"reference_passed":all(c["passed"] for c in comparisons) if reference_applicable else None,"reference_comparisons":comparisons,
            "reference_tolerance":{"absolute_v":1e-7,"relative":1e-4} if reference_applicable else None,
            "requirements_verified":bool(checks) and all(c["passed"] for c in checks),"checks":checks}


def _analyze_saved_values(client: Any, action: Mapping[str, Any], output: Path, *, parts: list[Any]) -> dict:
    from .linear_reference import scalar
    values={p.refdes:float(client.get_rlc_value(p.refdes)['value']) for p in parts if p.kind in {'R','C','L'}}
    if any(not math.isclose(values[p.refdes],scalar(p.value),rel_tol=1e-9,abs_tol=1e-12) for p in parts if p.refdes in values):
        raise RuntimeError('native RLC readback differs from declared values')
    result=analyze_current_project(client,action,output,expected_pins=expected_native_pins(parts))
    _write(output/'native-parameters.json',values)
    return result


def run_generated_analog_project(proposal: Mapping[str, Any], output: str, *, execute: bool=False,
                                native_source: str | None = None,
                                native_parameters: Mapping[str, Any] | None = None) -> dict[str, Any]:
    plan,parts = validate_proposal(proposal)
    if native_parameters:
        from .linear_reference import scalar
        by_ref = {p.refdes:p for p in parts}
        if native_source is None or any(ref not in by_ref or by_ref[ref].kind not in {'R','C','L'}
                or not math.isclose(scalar(str(value)),scalar(by_ref[ref].value),rel_tol=1e-12)
                for ref,value in native_parameters.items()):
            raise ValueError('native parameter changes must match the declared passive component values')
    source = Path(native_source).expanduser().resolve() if native_source is not None else None
    if source is not None and (not source.is_file() or source.suffix.lower() != '.ms14'):
        raise ValueError('native_source must be an existing .ms14 file')
    root = Path(output).expanduser().resolve()
    if root.exists() or root==Path(root.anchor):
        raise FileExistsError("output must be a new directory")
    result = {"success":True,"mode":"preview","output_dir":str(root),"proposal":plan,
              "verification_status":"unverified","simulation_started":False}
    if not execute:
        return normalize_task_result(result)
    root.mkdir(parents=True,exist_ok=False)
    _write(root/"proposal.json",plan)
    (root/"input.cir").write_text(plan["netlist"],encoding="utf-8")
    result.update(success=False,mode="execute",stage="build",verification_status="failed")
    try:
        if source is None:
            build = build_schematic(plan["netlist"],root/"source.xml",probe_nets=plan["probe_nets"])
            _write(root/"build.json",build)
            if build["unsupported"] or build["layout_validation"]["status"]!="pass":
                raise RuntimeError("generated circuit failed geometry preflight")
            if len(build["probes"]) != len(plan["probe_nets"]):
                raise RuntimeError("not every requested net has a drawable probe")
        from .multisim_client import Ms14Codec
        from .component_compat import detect_multisim_version
        version = detect_multisim_version()
        result["multisim_version"] = version
        if not re.match(r"^14\.3(?:\.|$)", version):
            raise RuntimeError("this workflow's native pin/model acceptance is currently verified only on Multisim 14.3")
        if source is None:
            Ms14Codec().encode(str(root/"source.xml"),str(root/"source.ms14"))
        else:
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            shutil.copyfile(source,root/'source.ms14')
            if hashlib.sha256((root/'source.ms14').read_bytes()).hexdigest() != digest:
                raise RuntimeError('native source changed while copying')
            Ms14Codec().decode(str(root/'source.ms14'),str(root/'source.xml'))
            result['reopened_source'] = {'path':str(source),'sha256':digest,'parameter_changes':dict(native_parameters or {})}
        request = {"schema_version":1,"title":plan["title"],"application":plan["application"],
                   "boards":[{"id":"main","role":"primary"}],"experiments":plan["experiments"]}
        result["stage"] = "native-analysis"
        native = run_native_project(request,str(root/"source.ms14"),str(root/"native"),execute=True,
                    parameters=native_parameters,
                    analyzer=partial(_analyze_saved_values,parts=parts) if source is not None else partial(analyze_current_project,expected_pins=expected_native_pins(parts)))
        result["native_execution"] = native
        result["simulation_started"] = native.get("simulation_started")
        result["native_project"] = str(root/"native"/"circuit.ms14")
        if not native["success"]:
            raise RuntimeError(str(native.get("error")))
        if (root/"native"/"schematic.png").read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("native schematic export does not contain PNG data")
        vendor = any(p.kind in {"LM324AJ", "QNPN", "D"} for p in parts)
        if vendor:
            decoded = Ms14Codec().decode(str(root/"native"/"circuit.ms14"), str(root/"native-model.xml"))
            before = vendor_model_fingerprints(root/"source.xml", parts)
            after = vendor_model_fingerprints(Path(decoded["xml"]), parts)
            result["model_acceptance"] = {"ok":before == after,"source":before,"native":after}
            _write(root/"model-identity.json",result["model_acceptance"])
            if before != after:
                raise RuntimeError("native save changed vendor model identity or definition")
        result["stage"] = "acceptance"
        acceptance = evaluate_evidence(root/"native",plan,parts)
        topology = [json.loads((root/"native"/f"analysis-{i:03d}"/"topology.json").read_text(encoding="utf-8")) for i in range(1,len(plan["experiments"])+1)]
        result["topology_acceptance"] = {"ok":all(t["ok"] for t in topology),"analyses":topology}
        result["measurement_acceptance"] = acceptance
        result["success"] = result["topology_acceptance"]["ok"] and (acceptance["reference_passed"] if acceptance["reference_applicable"] else result["model_acceptance"]["ok"]) and (acceptance["requirements_verified"] or not plan["checks"])
        result["verification_status"] = ("passed-declared-sampled-requirements" if acceptance["requirements_verified"] else "passed-linear-reference-only") if result["success"] else "target-not-met"
        result["verification_method"] = plan["verification_method"]
        if source is not None:
            result['reopened_source']['unchanged'] = hashlib.sha256(source.read_bytes()).hexdigest() == digest
            if not result['reopened_source']['unchanged']:
                raise RuntimeError('native source changed during verification')
    except Exception as exc:
        result['success'] = False
        result['verification_status'] = 'failed'
        result["error"] = {"type":type(exc).__name__,"message":str(exc)}
    summary = html.escape(json.dumps({k:result.get(k) for k in ("success","verification_status","measurement_acceptance","error")},ensure_ascii=False,indent=2))
    (root/"report.html").write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>'+html.escape(plan["title"])+
        '</title><style>body{font:16px system-ui;max-width:1200px;margin:32px auto;padding:16px}pre{white-space:pre-wrap}img{max-width:100%}</style><h1>'+html.escape(plan["title"])+
        '</h1><p>'+('原生 Multisim 供应商模型仿真；核对模型身份、完整引脚连接和声明的采样指标。未进行独立模型方程核对。' if plan['verification_method']=='native-vendor-sampled' else '原生 Multisim 执行与独立节点方程核对；仅验收明确列出的理想模型和采样指标。')+'</p><pre>'+summary+
        '</pre><p><a href="native/report.html">完整原生实验记录、CSV 与原始矩阵</a> · <a href="native/circuit.ms14">原生工程</a></p>'+
        ('<img alt="Multisim native schematic" src="native/schematic.png">' if (root/"native"/"schematic.png").is_file() else '')+
        '<h2>适用范围</h2><ul>'+''.join('<li>'+html.escape(item)+'</li>' for item in plan['limitations'])+'</ul>',encoding="utf-8")
    result["report"] = str(root/"report.html")
    return finalize_task_result(root,result)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Native composed linear analog verification")
    parser.add_argument("--proposal", required=True, help="proposal JSON file")
    parser.add_argument("--output", required=True, help="new evidence directory")
    parser.add_argument("--execute", action="store_true", help="build and run native Multisim; default previews")
    args = parser.parse_args()
    proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
    result = run_generated_analog_project(proposal,args.output,execute=args.execute)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
