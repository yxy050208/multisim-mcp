"""Native common-emitter execution with saved-file presentation checks."""
from pathlib import Path
import html
import csv
import json
import math
import re
from typing import Any

from .engineering_task_contract import finalize_task_result
from .generated_analog_run import run_generated_analog_project
from .native_xml import parse_native_xml
from .natural_common_emitter import parse_natural_common_emitter
from .preferred_values import parse_spice_scalar
from .schematic_builder import parse_netlist, voltage_source_stem


def _candidate_proposal(plan: dict[str, Any], resistance: float) -> dict[str, Any]:
    """Copy the bounded proposal and change only the declared RE value."""
    proposal = dict(plan['proposal'])
    netlist = re.sub(
        r'(?im)^RE\s+emitter\s+0\s+\S+',
        f"RE emitter 0 {format_resistance(resistance)}",
        plan['proposal']['netlist'],
        count=1,
    )
    if netlist == plan['proposal']['netlist']:
        raise ValueError('common-emitter proposal has no RE line')
    proposal['netlist'] = netlist
    return proposal


def _tolerance_proposal(proposal: dict[str, Any], values: dict[str, float]) -> dict[str, Any]:
    """Copy a proposal and apply deterministic resistor corner values."""
    updated = dict(proposal)
    netlist = proposal.get("netlist", "")
    for refdes, value in values.items():
        pattern = rf"(?im)^({re.escape(refdes)}\s+\S+\s+\S+\s+)\S+"
        replacement = rf"\g<1>{format_resistance(value)}"
        if re.search(pattern, netlist) is None:
            raise ValueError(f"tolerance proposal has no {refdes} resistor")
        netlist = re.sub(pattern, replacement, netlist, count=1)
    updated["netlist"] = netlist
    return updated


def _tolerance_corners(proposal: dict[str, Any], tolerance_percent: float) -> list[dict[str, Any]]:
    """Return nominal, one-at-a-time, and all-low/all-high resistor corners."""
    parts = parse_netlist(proposal["netlist"]).components
    refs = ("RBIAS1", "RBIAS2", "RC", "RE", "RLOAD")
    nominal = {
        part.refdes: float(parse_spice_scalar(part.value))
        for part in parts if part.refdes in refs
    }
    if set(nominal) != set(refs):
        missing = ", ".join(sorted(set(refs) - set(nominal)))
        raise ValueError(f"tolerance scan requires resistors: {missing}")
    fraction = tolerance_percent / 100.0
    corners: list[dict[str, Any]] = [{"id": "nominal", "values": nominal}]
    for refdes in refs:
        for suffix, scale in (("minus", 1.0 - fraction), ("plus", 1.0 + fraction)):
            values = dict(nominal)
            values[refdes] *= scale
            corners.append({"id": f"{refdes.lower()}-{suffix}", "values": values})
    corners.extend([
        {"id": "all-minus", "values": {ref: value * (1.0 - fraction) for ref, value in nominal.items()}},
        {"id": "all-plus", "values": {ref: value * (1.0 + fraction) for ref, value in nominal.items()}},
    ])
    return corners


def _sine_amplitude_proposal(proposal: dict[str, Any], amplitude: float) -> dict[str, Any]:
    """Copy a sine proposal and change only the input peak amplitude."""
    if not isinstance(amplitude, (int, float)) or not math.isfinite(amplitude) or amplitude <= 0:
        raise ValueError("sine amplitude must be a positive finite number")
    updated = dict(proposal)
    source = proposal.get("netlist", "")
    replacement = f"SIN(0 {format_resistance(amplitude)} 1k)"
    pattern = r"(?i)SIN\s*\(\s*0\s+\S+\s+1k\s*\)"
    if re.search(pattern, source) is None:
        raise ValueError("sine proposal has no 1 kHz SIN source")
    netlist = re.sub(pattern, replacement, source, count=1)
    updated["netlist"] = netlist
    checks = []
    for check in proposal.get("checks", []):
        item = dict(check)
        if item.get("analysis") == "tran":
            # The scan is diagnostic; use a deliberately broad output bound so
            # the native run can reveal clipping through THD rather than fail
            # early on the nominal small-signal check.
            scale = 1.1 if item.get("net") == "in" else 50.0
            item["min"] = -amplitude * scale
            item["max"] = amplitude * scale
        checks.append(item)
    updated["checks"] = checks
    return updated


def format_resistance(value: float) -> str:
    from decimal import Decimal
    from .preferred_values import format_spice_scalar
    return format_spice_scalar(Decimal(str(value)))


def _measured_gain(result: dict[str, Any], target: float) -> tuple[float | None, float | None]:
    acceptance = result.get('measurement_acceptance') or {}
    for check in acceptance.get('checks', []):
        requirement = check.get('requirement', {})
        if requirement.get('analysis') == 'ac' and requirement.get('quantity') == 'magnitude':
            measured = check.get('measured_max')
            if isinstance(measured, (int, float)) and measured == measured:
                return float(measured), abs(float(measured) - target) / max(target, 1e-12)
    return None, None


def _frequency_response_acceptance(candidate_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Measure the available native AC sweep and report honest -3 dB evidence.

    This is deliberately a derived report metric rather than a new hard gate:
    the current common-emitter contract requests 10 Hz..100 kHz, and some
    valid devices do not reach either -3 dB edge in that window.  In that case
    the result is ``unverified`` with the missing edge recorded explicitly.
    """
    experiments = plan["proposal"].get("experiments", [])
    ac_index = next((i for i, item in enumerate(experiments, 1) if item.get("type") == "ac"), None)
    if ac_index is None:
        return {"status": "unverified", "reason": "no native AC sweep requested"}
    csv_path = candidate_dir / "native" / f"analysis-{ac_index:03d}" / "data.csv"
    if not csv_path.is_file():
        return {"status": "unverified", "reason": "native AC data.csv is missing"}
    probe_nets = plan["proposal"].get("probe_nets", [])
    try:
        in_index = probe_nets.index("in")
        out_index = probe_nets.index("out")
    except ValueError:
        return {"status": "unverified", "reason": "in/out probes are not declared"}
    def signal(index: int) -> str:
        return f"V(OutProbe{index if index else ''})"
    in_signal, out_signal = signal(in_index), signal(out_index)
    rows = []
    try:
        with csv_path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                frequency = float(row["frequency_hz"])
                input_value = complex(float(row[f"{in_signal}.real"]), float(row[f"{in_signal}.imaginary"]))
                output_value = complex(float(row[f"{out_signal}.real"]), float(row[f"{out_signal}.imaginary"]))
                if frequency > 0 and math.isfinite(frequency) and abs(input_value) > 1e-15:
                    gain = abs(output_value / input_value)
                    if math.isfinite(gain):
                        rows.append((frequency, gain))
    except (KeyError, ValueError, TypeError) as exc:
        return {"status": "unverified", "reason": f"native AC data is invalid: {exc}"}
    if len(rows) < 3:
        return {"status": "unverified", "reason": "native AC sweep has fewer than three usable samples", "samples": len(rows)}
    rows.sort()
    peak_frequency, peak_gain = max(rows, key=lambda item: item[1])
    threshold = peak_gain / (10 ** (3 / 20))
    peak_index = next(index for index, item in enumerate(rows) if item == (peak_frequency, peak_gain))

    def crossing(left: tuple[float, float], right: tuple[float, float]) -> float | None:
        f1, g1 = left
        f2, g2 = right
        if (g1 - threshold) * (g2 - threshold) > 0 or g1 == g2 or f1 == f2:
            return None
        fraction = (threshold - g1) / (g2 - g1)
        # Frequency sweeps are logarithmic in the native contract.  Log-space
        # interpolation avoids overstating an edge between decades.
        return 10 ** (math.log10(f1) + fraction * (math.log10(f2) - math.log10(f1)))

    lower = next((crossing(rows[index - 1], rows[index]) for index in range(peak_index, 0, -1)
                  if crossing(rows[index - 1], rows[index]) is not None), None)
    upper = next((crossing(rows[index], rows[index + 1]) for index in range(peak_index, len(rows) - 1)
                  if crossing(rows[index], rows[index + 1]) is not None), None)
    result = {
        "status": "passed-sweep-minus3db" if lower is not None and upper is not None else "unverified",
        "criterion": "peak gain minus 3 dB",
        "samples": len(rows),
        "sweep_start_hz": rows[0][0],
        "sweep_stop_hz": rows[-1][0],
        "peak_frequency_hz": peak_frequency,
        "peak_gain": peak_gain,
        "threshold_gain": threshold,
        "lower_cutoff_hz": lower,
        "upper_cutoff_hz": upper,
        "bandwidth_hz": upper - lower if lower is not None and upper is not None else None,
    }
    if lower is None or upper is None:
        result["reason"] = "one or both -3 dB edges lie outside the requested native sweep"
    return result


def _distortion_acceptance(candidate_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Calculate bounded THD evidence for the optional native sine mode."""
    if plan.get("waveform") != "sine":
        return {
            "status": "unverified",
            "reason": "the default native transient experiment uses a pulse source; request 正弦 or THD to enable a VSIN transient run",
        }
    experiments = plan["proposal"].get("experiments", [])
    tran_index = next((i for i, item in enumerate(experiments, 1) if item.get("type") == "tran"), None)
    if tran_index is None:
        return {"status": "unverified", "reason": "no native sine transient was requested"}
    csv_path = candidate_dir / "native" / f"analysis-{tran_index:03d}" / "data.csv"
    if not csv_path.is_file():
        return {"status": "unverified", "reason": "native sine transient data.csv is missing"}
    try:
        output_index = plan["proposal"]["probe_nets"].index("out")
        signal = f"V(OutProbe{output_index if output_index else ''})"
        rows = []
        with csv_path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                time = float(row["time_s"])
                value = float(row[f"{signal}.value"])
                if math.isfinite(time) and math.isfinite(value) and .001 <= time <= .002:
                    rows.append((time, value))
    except (KeyError, ValueError, TypeError) as exc:
        return {"status": "unverified", "reason": f"native sine transient data is invalid: {exc}"}
    if len(rows) < 16:
        return {"status": "unverified", "reason": "steady-state sine window has fewer than 16 samples", "samples": len(rows)}
    rows.sort()
    frequency = 1000.0
    mean = sum(value for _, value in rows) / len(rows)
    amplitudes = []
    start = rows[0][0]
    for harmonic in range(1, 6):
        coefficient = sum(
            (value - mean) * complex(math.cos(-2 * math.pi * harmonic * frequency * (time - start)),
                                     math.sin(-2 * math.pi * harmonic * frequency * (time - start)))
            for time, value in rows
        ) / len(rows)
        amplitudes.append(2 * abs(coefficient))
    fundamental = amplitudes[0]
    if fundamental <= 1e-15:
        return {"status": "unverified", "reason": "fundamental amplitude is zero", "samples": len(rows)}
    thd_fraction = math.sqrt(sum(amplitude * amplitude for amplitude in amplitudes[1:])) / fundamental
    thd_percent = 100 * thd_fraction
    limit = float(plan.get("thd_limit_percent") or 1.0)
    return {
        "status": "passed-thd" if thd_percent <= limit else "target-not-met",
        "criterion": "H2..H5 RMS-equivalent amplitude divided by fundamental",
        "samples": len(rows), "window_start_s": rows[0][0], "window_stop_s": rows[-1][0],
        "peak_to_peak_v": max(value for _, value in rows) - min(value for _, value in rows),
        "fundamental_frequency_hz": frequency, "fundamental_amplitude_v": fundamental,
        "harmonic_amplitudes_v": amplitudes[1:], "thd_fraction": thd_fraction,
        "thd_percent": thd_percent, "limit_percent": limit,
    }


def _operating_margin_acceptance(
    candidate_dir: Path, plan: dict[str, Any], native_result: dict[str, Any]
) -> dict[str, Any]:
    """Check DC operating point and collector-to-rail headroom from native data.

    The output node is AC-coupled in this topology, so its voltage is not a
    useful rail-margin signal.  The collector probe is checked instead: every
    steady-state transient sample must leave a small margin to ground and VCC.
    This is a derived native measurement and is deliberately reported as
    ``unverified`` when the required probe or operating-point evidence is absent.
    """
    supply = float(plan.get("derived", {}).get("supply_v") or 0.0)
    if supply <= 0:
        return {"status": "unverified", "reason": "supply voltage is unavailable"}
    checks = (native_result.get("measurement_acceptance") or {}).get("checks", [])
    op_requirements = {
        ("op", "collector", None),
        ("op", "base", "emitter"),
        ("op", "collector", "base"),
    }
    op_checks = [
        check for check in checks
        if (check.get("requirement", {}).get("analysis"),
            check.get("requirement", {}).get("net"),
            check.get("requirement", {}).get("subtract_net")) in op_requirements
    ]
    if len(op_checks) != len(op_requirements):
        op_status = "unverified"
        op_reason = "native operating-point checks are incomplete"
    elif all(check.get("passed") is True for check in op_checks):
        op_status = "passed-operating-point"
        op_reason = None
    else:
        op_status = "target-not-met"
        op_reason = "one or more native operating-point checks failed"

    experiments = plan["proposal"].get("experiments", [])
    tran_index = next((i for i, item in enumerate(experiments, 1) if item.get("type") == "tran"), None)
    probe_nets = plan["proposal"].get("probe_nets", [])
    try:
        collector_index = probe_nets.index("collector")
    except ValueError:
        return {
            "status": "unverified",
            "operating_point_status": op_status,
            "reason": "collector probe is not declared",
        }
    if tran_index is None:
        return {
            "status": "unverified",
            "operating_point_status": op_status,
            "reason": "no native transient experiment was requested",
        }
    csv_path = candidate_dir / "native" / f"analysis-{tran_index:03d}" / "data.csv"
    if not csv_path.is_file():
        return {
            "status": "unverified",
            "operating_point_status": op_status,
            "reason": "native transient data.csv is missing",
        }
    signal = f"V(OutProbe{collector_index if collector_index else ''})"
    values = []
    try:
        with csv_path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                time = float(row["time_s"])
                value = float(row[f"{signal}.value"])
                if math.isfinite(time) and math.isfinite(value) and .001 <= time <= .002:
                    values.append(value)
    except (KeyError, ValueError, TypeError) as exc:
        return {
            "status": "unverified",
            "operating_point_status": op_status,
            "reason": f"native collector transient data is invalid: {exc}",
        }
    if len(values) < 16:
        return {
            "status": "unverified",
            "operating_point_status": op_status,
            "reason": "steady-state collector window has fewer than 16 samples",
            "samples": len(values),
        }
    collector_min = min(values)
    collector_max = max(values)
    low_headroom = collector_min
    high_headroom = supply - collector_max
    margin_limit = max(0.1, supply * 0.01)
    rail_status = "passed-rail-margin" if min(low_headroom, high_headroom) >= margin_limit else "target-not-met"
    if op_status == "passed-operating-point" and rail_status == "passed-rail-margin":
        status = "passed-operating-margin"
    elif op_status == "target-not-met" or rail_status == "target-not-met":
        status = "target-not-met"
    else:
        status = "unverified"
    result = {
        "status": status,
        "criterion": f"collector headroom >= {margin_limit:g} V and declared OP checks pass",
        "operating_point_status": op_status,
        "rail_status": rail_status,
        "samples": len(values),
        "window_start_s": .001,
        "window_stop_s": .002,
        "collector_min_v": collector_min,
        "collector_max_v": collector_max,
        "low_headroom_v": low_headroom,
        "high_headroom_v": high_headroom,
        "minimum_headroom_v": min(low_headroom, high_headroom),
        "headroom_limit_v": margin_limit,
    }
    if op_reason:
        result["operating_point_reason"] = op_reason
    return result


def _run_amplitude_scan(root: Path, plan: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any]:
    """Run an ascending native sine-amplitude scan for the selected candidate.

    The coarse scan finds a useful bracket cheaply.  When it finds both a
    passing point and a first THD failure, a few native midpoint runs narrow
    that bracket.  Every midpoint is kept as its own project so the reported
    boundary remains reproducible and auditable.
    """
    if plan.get("waveform") != "sine":
        return {"status": "unverified", "reason": "amplitude scan requires sine mode"}
    amplitudes = [0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5]
    refinement_iterations = 4
    base = _candidate_proposal(plan, float(selected["resistance_ohm"]))
    scan_root = root / "swing-scan"
    scan_root.mkdir(parents=True, exist_ok=False)
    points = []

    def run_point(amplitude: float, directory_name: str) -> dict[str, Any]:
        proposal = _sine_amplitude_proposal(base, amplitude)
        scan_plan = dict(plan)
        scan_plan["proposal"] = proposal
        point_dir = scan_root / directory_name
        try:
            native = run_generated_analog_project(proposal, str(point_dir), execute=True)
            distortion = _distortion_acceptance(point_dir, scan_plan)
            operating_margin = _operating_margin_acceptance(point_dir, scan_plan, native)
            return {
                "directory": point_dir.relative_to(root).as_posix(),
                "input_peak_v": amplitude,
                "success": bool(native.get("success")),
                "verification_status": native.get("verification_status"),
                "distortion": distortion,
                "operating_margin": operating_margin,
                "native_project": native.get("native_project"),
                "report": native.get("report"),
            }
        except Exception as exc:
            return {
                "directory": point_dir.relative_to(root).as_posix(),
                "input_peak_v": amplitude,
                "success": False,
                "verification_status": "failed",
                "distortion": {"status": "failed", "reason": str(exc)},
                "operating_margin": {"status": "unverified", "reason": "native run failed"},
            }

    for index, amplitude in enumerate(amplitudes, 1):
        point = run_point(amplitude, f"amplitude-{index:03d}")
        points.append(point)
        if (not point["success"]
                or point["distortion"].get("status") == "target-not-met"
                or point["operating_margin"].get("status") == "target-not-met"):
            break

    def is_passed(point: dict[str, Any]) -> bool:
        return (point["success"]
                and point["distortion"].get("status") == "passed-thd"
                and point["operating_margin"].get("status") == "passed-operating-margin")

    def is_limit_exceeded(point: dict[str, Any]) -> bool:
        return (point["success"]
                and (point["distortion"].get("status") == "target-not-met"
                     or point["operating_margin"].get("status") == "target-not-met"))

    first_failed_index = next((index for index, point in enumerate(points) if not is_passed(point)), None)
    refinement_points = []
    if (first_failed_index is not None and first_failed_index > 0
            and is_limit_exceeded(points[first_failed_index])):
        low = points[first_failed_index - 1]
        high = points[first_failed_index]
        for iteration in range(1, refinement_iterations + 1):
            midpoint = (float(low["input_peak_v"]) + float(high["input_peak_v"])) / 2.0
            point = run_point(midpoint, f"amplitude-refine-{iteration:03d}")
            points.append(point)
            refinement_points.append(point)
            if is_passed(point):
                low = point
            else:
                high = point

    passed = [point for point in points if point["success"] and point["distortion"].get("status") == "passed-thd"]
    failed = next((point for point in points if not is_passed(point)), None)
    result: dict[str, Any] = {
        "status": "passed-amplitude-scan" if passed else "failed",
        "criterion": f"THD <= {float(plan.get('thd_limit_percent') or 1.0):g}% plus native OP and collector rail-margin checks",
        "points": points,
        "scan_limit_input_peak_v": max((point["input_peak_v"] for point in points), default=None),
        "max_undistorted_input_peak_v": passed[-1]["input_peak_v"] if passed else None,
        "max_undistorted_output_peak_to_peak_v": passed[-1]["distortion"].get("peak_to_peak_v") if passed else None,
        "refinement_iterations": len(refinement_points),
    }
    if refinement_points and passed and failed is not None:
        refined_low = max((point for point in refinement_points if is_passed(point)),
                          key=lambda point: point["input_peak_v"], default=None)
        refined_high = min((point for point in refinement_points if not is_passed(point)),
                           key=lambda point: point["input_peak_v"], default=None)
        if refined_low is not None and refined_high is not None:
            result["refined_bracket_input_peak_v"] = {
                "passed": refined_low["input_peak_v"],
                "exceeded": refined_high["input_peak_v"],
                "width_v": refined_high["input_peak_v"] - refined_low["input_peak_v"],
            }
            result["max_undistorted_input_peak_v"] = refined_low["input_peak_v"]
            result["max_undistorted_output_peak_to_peak_v"] = refined_low["distortion"].get("peak_to_peak_v")
            result["first_limit_exceeding_input_peak_v"] = refined_high["input_peak_v"]
    if failed is None and passed:
        result["status"] = "unverified-scan-limit"
        result["reason"] = "all scanned amplitudes remained below the THD limit; extend the scan before claiming maximum swing"
    elif failed is not None and is_limit_exceeded(failed) and "first_limit_exceeding_input_peak_v" not in result:
        result["first_limit_exceeding_input_peak_v"] = failed["input_peak_v"]
    return result


def _run_tolerance_scan(root: Path, plan: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any]:
    """Run native resistor corners for the selected common-emitter candidate."""
    tolerance = plan.get("tolerance_percent")
    if tolerance is None:
        return {"status": "unverified", "reason": "component tolerance was not requested"}
    base = _candidate_proposal(plan, float(selected["resistance_ohm"]))
    scan_root = root / "tolerance-scan"
    scan_root.mkdir(parents=True, exist_ok=False)
    points = []
    target_gain = float(plan["derived"]["target_gain"])
    for index, corner in enumerate(_tolerance_corners(base, float(tolerance)), 1):
        proposal = _tolerance_proposal(base, corner["values"])
        scan_plan = dict(plan)
        scan_plan["proposal"] = proposal
        point_dir = scan_root / f"corner-{index:03d}-{corner['id']}"
        try:
            native = run_generated_analog_project(proposal, str(point_dir), execute=True)
            measured_gain, target_error = _measured_gain(native, target_gain)
            distortion = _distortion_acceptance(point_dir, scan_plan)
            operating_margin = _operating_margin_acceptance(point_dir, scan_plan, native)
            simulation_complete = bool((native.get("native_execution") or {}).get("simulation_completed"))
            gain_passed = target_error is not None and target_error <= 0.10
            distortion_passed = plan.get("waveform") != "sine" or distortion.get("status") == "passed-thd"
            margin_passed = operating_margin.get("status") == "passed-operating-margin"
            passed = simulation_complete and gain_passed and distortion_passed and margin_passed
            point = {
                "id": corner["id"],
                "values": corner["values"],
                "directory": point_dir.relative_to(root).as_posix(),
                "success": passed,
                "simulation_completed": simulation_complete,
                "measured_gain": measured_gain,
                "target_error_fraction": target_error,
                "gain_passed": gain_passed,
                "distortion": distortion,
                "operating_margin": operating_margin,
                "native_verification_status": native.get("verification_status"),
                "native_project": native.get("native_project"),
                "report": native.get("report"),
            }
        except Exception as exc:
            point = {
                "id": corner["id"],
                "values": corner["values"],
                "directory": point_dir.relative_to(root).as_posix(),
                "success": False,
                "simulation_completed": False,
                "gain_passed": False,
                "distortion": {"status": "unverified", "reason": "native run failed"},
                "operating_margin": {"status": "unverified", "reason": "native run failed"},
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        points.append(point)
    passed = [point for point in points if point["success"]]
    errors = [point["target_error_fraction"] for point in points if point.get("target_error_fraction") is not None]
    result: dict[str, Any] = {
        "status": "passed-tolerance-scan" if len(passed) == len(points) else "target-not-met",
        "criterion": f"resistor corners ±{float(tolerance):g}% with native gain, THD, OP and rail-margin checks",
        "tolerance_percent": float(tolerance),
        "points": points,
        "point_count": len(points),
        "passed_count": len(passed),
        "worst_target_error_fraction": max(errors) if errors else None,
    }
    if result["status"] != "passed-tolerance-scan":
        result["failed_points"] = [point["id"] for point in points if not point["success"]]
    return result


def verify_ce_presentation(path: Path, plan: dict) -> dict:
    """Inspect the file Multisim actually opened/saved, not a label-only patch."""
    root = parse_native_xml(path).getroot()
    components = {c.get('LocalName', '').removeprefix('&ASC'): c for c in root.iter('CiComponent')}
    checks = {}
    vin_sine = plan.get("waveform") == "sine"
    for ref, identity, parameters in (
        ('VCC', 'DC_POWER', {1: plan['derived']['supply_v']}),
        ('VIN', 'AC_VOLTAGE' if vin_sine else 'PULSE_VOLTAGE',
         ({1: .001, 3: 0., 5: 1000., 13: 1., 15: 0.} if vin_sine else
          {1: 0., 3: .001, 5: .001, 7: .000001, 9: .000001, 11: .001, 13: .002})),
    ):
        component = components.get(ref)
        if component is None:
            checks[ref] = False
            continue
        names = [e.get('Value', '').removeprefix('&ASC') for e in component.findall('./Attributes/Item/CiaCollString/strings/Item')]
        values = component.findall('.//CiaParamList/doubles/Item')
        checks[ref] = len(names) > 1 and names[1] == identity and all(
            index < len(values) and abs(float(values[index].get('Value'))-value) <= max(1e-12,abs(value)*1e-9)
            for index,value in parameters.items())
    probes = list(root.iter('CIITProbeExtComponent'))
    checks['probe_panels_hidden'] = len(probes) == len(plan['proposal']['probe_nets']) and all(
        p.get('Hidden') == '1' and p.get('ShowInfo') == '0' for p in probes)
    checks['no_ac_example_label'] = not any('5kHz' in e.get('Output','') for e in root.iter('CIITSymTextCompValue'))
    return {'ok': all(checks.values()), 'checks': checks,
            'scope': 'native source identities/parameters and probe visibility; human schematic review remains required'}


def run_natural_common_emitter(text: str, output: str, *, execute: bool = False) -> dict:
    plan = parse_natural_common_emitter(text)
    if execute:
        sources = {p.refdes: voltage_source_stem(p) for p in parse_netlist(plan['proposal']['netlist']).components if p.kind == 'V'}
        expected_sources = {'VCC': 'vdc', 'VIN': 'v' if plan.get('waveform') == 'sine' else 'vpulse'}
        if sources != expected_sources:
            raise ValueError('Rebuild the local component pack: native VDC and waveform carriers are required for this workflow')
    root = Path(output).expanduser().resolve()
    if root.exists() or root == Path(root.anchor):
        raise FileExistsError('output must be a new directory')
    if not execute:
        result = {
            'success': True, 'mode': 'preview', 'output_dir': str(root),
            'proposal': plan['proposal'], 'natural_language_plan': plan,
            'candidates': [{'resistance_ohm': value, 'status': 'planned'}
                           for value in plan['candidate_resistors_ohm']],
            'optimization': {'method': 'native-measured-gain-neighbourhood',
                             'candidate_count': len(plan['candidate_resistors_ohm'])},
            'verification_status': 'unverified', 'simulation_started': False,
        }
        from .engineering_task_contract import normalize_task_result
        return normalize_task_result(result)

    root.mkdir(parents=True, exist_ok=False)
    (root / 'input.txt').write_text(text, encoding='utf-8')
    (root / 'proposal.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    result: dict[str, Any] = {
        'success': False, 'mode': 'execute', 'output_dir': str(root),
        'proposal': plan['proposal'], 'natural_language_plan': plan, 'candidates': [],
        'verification_status': 'failed', 'simulation_started': False,
    }
    try:
        for index, resistance in enumerate(plan['candidate_resistors_ohm'], 1):
            proposal = _candidate_proposal(plan, resistance)
            candidate_dir = root / f'candidate-{index:03d}'
            candidate = run_generated_analog_project(proposal, str(candidate_dir), execute=True)
            measured_gain, target_error = _measured_gain(candidate, plan['derived']['target_gain'])
            candidate_record: dict[str, Any] = {
                'directory': candidate_dir.name,
                'project': f'{candidate_dir.name}/native/circuit.ms14',
                'resistance_ohm': resistance,
                'success': bool(candidate.get('success')),
                'measured_gain': measured_gain,
                'target_error_fraction': target_error,
                'verification_status': candidate.get('verification_status'),
                'result': candidate,
                'frequency_response': _frequency_response_acceptance(candidate_dir, plan),
                'distortion_acceptance': _distortion_acceptance(candidate_dir, plan),
            }
            if candidate_record['success']:
                model_xml = candidate_dir / 'native-model.xml'
                if not model_xml.is_file():
                    candidate_record['success'] = False
                    candidate_record['verification_status'] = 'native-presentation-missing'
                else:
                    presentation = verify_ce_presentation(model_xml, plan)
                    candidate_record['presentation_acceptance'] = presentation
                    candidate_record['success'] = presentation['ok']
                    if not presentation['ok']:
                        candidate_record['verification_status'] = 'native-presentation-mismatch'
            if candidate_record['success'] and candidate_record['distortion_acceptance']['status'] == 'target-not-met':
                candidate_record['success'] = False
                candidate_record['verification_status'] = 'target-not-met'
            result['candidates'].append(candidate_record)
        feasible = [item for item in result['candidates']
                    if item['success'] and item['target_error_fraction'] is not None]
        if feasible:
            selected = min(feasible, key=lambda item: (item['target_error_fraction'], item['resistance_ohm']))
            result['selected'] = selected
            result['success'] = True
            result['verification_status'] = 'passed-common-emitter-candidate-search'
            result['simulation_started'] = True
            result['delivery_status'] = 'requires-visual-review'
            result['amplitude_scan'] = _run_amplitude_scan(root, plan, selected)
            if plan.get("tolerance_percent") is not None:
                result['tolerance_scan'] = _run_tolerance_scan(root, plan, selected)
                if result['tolerance_scan'].get('status') != 'passed-tolerance-scan':
                    result['success'] = False
                    result['verification_status'] = 'target-not-met-tolerance'
                    result['delivery_status'] = 'not-ready'
                else:
                    result['verification_status'] = 'passed-common-emitter-candidate-search-with-tolerance'
        else:
            result['error'] = {'type': 'RuntimeError', 'message': 'no common-emitter candidate passed native acceptance'}
            result['delivery_status'] = 'not-ready'
    except Exception as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        result['delivery_status'] = 'not-ready'
    report_rows = ''.join(
        '<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
            item['resistance_ohm'], item.get('measured_gain') if item.get('measured_gain') is not None else '—',
            f"{100 * item['target_error_fraction']:.3f}%" if item.get('target_error_fraction') is not None else '—',
            html.escape(str(item.get('frequency_response', {}).get('status', 'unverified'))),
            html.escape(str(item.get('verification_status'))), '是' if item['success'] else '否')
        for item in result['candidates'])
    selected = result.get('selected')
    conclusion = (f"选中 RE={selected['resistance_ohm']:g} Ω，实测增益 {selected['measured_gain']:.6g}。"
                  if selected else '没有候选通过原生验收。')
    amplitude_scan = result.get('amplitude_scan')
    if isinstance(amplitude_scan, dict):
        scan_text = (
            f"幅值扫描：{amplitude_scan.get('status')}；最大已验证输入峰值 "
            f"{amplitude_scan.get('max_undistorted_input_peak_v', '—')} V，"
            f"首个综合验收超限点 {amplitude_scan.get('first_limit_exceeding_input_peak_v', '—')} V。"
        )
        bracket = amplitude_scan.get('refined_bracket_input_peak_v')
        if isinstance(bracket, dict):
            scan_text += (
                f" 二分细化后区间 [{bracket.get('passed', '—')}, "
                f"{bracket.get('exceeded', '—')}] V，宽度 "
                f"{bracket.get('width_v', '—')} V。"
            )
    else:
        scan_text = "幅值扫描：未启用（需要正弦模式）。"
    tolerance_scan = result.get('tolerance_scan')
    if isinstance(tolerance_scan, dict):
        scan_text += (
            f" 元件容差扫描：{tolerance_scan.get('status')}，通过 "
            f"{tolerance_scan.get('passed_count', 0)}/{tolerance_scan.get('point_count', 0)} 个角落。"
        )
    (root / 'report.html').write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>共射放大器候选搜索</title>'
        '<style>body{font:16px/1.7 system-ui;max-width:1100px;margin:40px auto;padding:20px}'
        'table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px}</style>'
        '<h1>2N3904 共射放大器候选搜索</h1>'
        f'<p>状态：{html.escape(result["verification_status"])}；{html.escape(conclusion)}</p>'
        f'<p>{html.escape(scan_text)}</p>'
        '<table><tr><th>RE</th><th>实测增益</th><th>目标误差</th><th>频带证据</th><th>状态</th><th>通过</th></tr>'
        + report_rows + '</table><p>每个候选均为独立原生工程副本，保留其 Multisim 工程、CSV、图纸和 manifest。</p>'
        '<p>频带指标从原生 AC 扫频派生；正弦模式会额外扫描输入幅值并以 THD 识别摆幅边界，普通脉冲模式的失真状态保持 unverified。</p>'
        '<p><a href="proposal.json">需求与候选</a> · <a href="acceptance.json">验收记录</a> · <a href="manifest.json">完整性清单</a></p></html>',
        encoding='utf-8')
    return finalize_task_result(root, result)
