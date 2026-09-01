"""Deterministic, read-only circuit diagnosis with optional experiment evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from .eda_agent_tools import run_readonly_structural_checks
from .eda_core import CircuitComponent, CircuitDesign
from .spice_adapter import circuit_design_to_spice
from .spice_raw import parse_raw
from .workspace_manifest import DIRECTORY_MANIFEST_NAME, read_directory_manifest


DIAGNOSIS_SCHEMA_VERSION: Final = 1
MAX_DIAGNOSIS_FINDINGS: Final = 256
MAX_VERIFICATION_BYTES: Final = 2 * 1024 * 1024
MAX_RAW_BYTES: Final = 32 * 1024 * 1024
MAX_LOG_BYTES: Final = 1024 * 1024

_FINDING_SEVERITIES = frozenset({"info", "warning", "error"})
_SOURCE_KINDS = frozenset({"V", "I", "VSOURCE", "ISOURCE"})
_CONVERGENCE_PATTERNS: Final[tuple[tuple[re.Pattern[str], str, str, str], ...]] = (
    (
        re.compile(r"singular\s+matrix", re.IGNORECASE),
        "singular-matrix",
        "The solver reported a singular matrix.",
        "Check the reference node, floating subcircuits, and DC paths to ground.",
    ),
    (
        re.compile(r"(?:failed\s+to\s+converge|no\s+convergence)", re.IGNORECASE),
        "solver-nonconvergence",
        "The solver did not converge.",
        "Inspect nonlinear bias, initial conditions, source ramps, and model parameters.",
    ),
    (
        re.compile(r"time\s*step\s+too\s+small", re.IGNORECASE),
        "timestep-too-small",
        "Transient analysis reduced the time step below the solver limit.",
        "Inspect discontinuities, ideal switching, initial conditions, and parasitic damping.",
    ),
    (
        re.compile(r"gmin\s+stepping.*fail", re.IGNORECASE),
        "gmin-stepping-failed",
        "GMIN stepping failed to establish a converged operating point.",
        "Check floating nonlinear nodes and unrealistic device or source values.",
    ),
    (
        re.compile(r"source\s+stepping.*fail", re.IGNORECASE),
        "source-stepping-failed",
        "Source stepping failed to establish a converged operating point.",
        "Check source magnitudes, feedback polarity, bias paths, and nonlinear models.",
    ),
    (
        re.compile(r"iteration\s+limit", re.IGNORECASE),
        "iteration-limit",
        "The solver reached its iteration limit.",
        "Inspect feedback, initial conditions, and strongly nonlinear model regions.",
    ),
)

_STRUCTURAL_ACTIONS: Final[dict[str, str]] = {
    "reference-net-absent": "Add or verify one explicit 0/GND reference net.",
    "declared-net-unused": "Remove the unused declaration or connect the intended pin.",
    "single-connection-net": "Inspect the named net for an unconnected or floating pin.",
    "component-pins-share-net": "Confirm that shorting every pin of this component is intentional.",
    "model-digest-absent": "Record the model source and SHA-256 before reproducible use.",
    "model-license-absent": "Record model license metadata before redistribution.",
    "source-only-design": "Import or reconstruct structured components before topology diagnosis.",
}
_MEASUREMENT_EVIDENCE_FIELDS: Final = (
    "id",
    "metric",
    "signal",
    "reference_signal",
    "x_signal",
    "status",
    "value",
    "unit",
    "reason",
)
_CRITERION_EVIDENCE_FIELDS: Final = (
    "operator",
    "target",
    "lower",
    "upper",
    "tolerance_abs",
    "tolerance_percent",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_spice(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _safe_file(root: Path, name: str, maximum: int, *, required: bool) -> Path | None:
    path = root / name
    if path.is_symlink():
        raise ValueError(f"diagnosis artifact must not be a symbolic link: {name}")
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"diagnosis artifact is missing: {name}")
        return None
    if path.stat().st_size > maximum:
        raise ValueError(f"diagnosis artifact exceeds its size limit: {name}")
    return path


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {constant}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError(f"{label} must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value


def _compact_scalar(value: Any, *, max_chars: int = 512) -> Any:
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:max_chars]
    return None


def _compact_fields(
    value: Any, fields: Sequence[str]
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for name in fields:
        if name in value:
            result[name] = _compact_scalar(value[name])
    return result


def load_experiment_diagnosis_evidence(
    design: CircuitDesign, output_directory: str
) -> dict[str, Any]:
    """Load integrity-checked, design-bound evidence from one completed experiment."""
    if not isinstance(design, CircuitDesign):
        raise ValueError("design must be CircuitDesign")
    if not isinstance(output_directory, str) or not output_directory.strip():
        raise ValueError("output_directory must not be empty")
    unresolved = Path(output_directory).expanduser()
    if unresolved.is_symlink():
        raise ValueError("experiment directory must not be a symbolic link")
    root = unresolved.resolve()
    manifest = read_directory_manifest(root, verify=True)
    if manifest.directory_kind != "experiment" or manifest.state != "succeeded":
        raise ValueError("diagnosis requires a completed experiment directory")
    netlist_path = _safe_file(root, "circuit.cir", 4 * 1024 * 1024, required=True)
    assert netlist_path is not None
    try:
        stored_netlist = netlist_path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ValueError("experiment netlist must be UTF-8") from exc
    expected_netlist = circuit_design_to_spice(design)
    if _canonical_spice(stored_netlist) != _canonical_spice(expected_netlist):
        raise ValueError("experiment netlist does not match the diagnosed design")

    verification_path = _safe_file(
        root, "verification.json", MAX_VERIFICATION_BYTES, required=False
    )
    verification = (
        None
        if verification_path is None
        else _read_json_object(verification_path, "verification evidence")
    )
    raw_path = _safe_file(root, "result.raw", MAX_RAW_BYTES, required=False)
    operating_point: dict[str, float] = {}
    analysis: dict[str, Any] = {"plotname": None, "point_count": 0}
    if raw_path is not None:
        parsed = parse_raw(str(raw_path))
        rows = parsed.get("rows")
        columns = parsed.get("columns")
        header = parsed.get("header") or {}
        point_count = int(parsed.get("n_points", 0))
        plotname = str(header.get("plotname", ""))
        analysis = {"plotname": plotname, "point_count": point_count}
        is_operating_point = point_count == 1 or "operating" in plotname.casefold()
        if is_operating_point and isinstance(rows, list) and rows and isinstance(columns, list):
            first = rows[0]
            if isinstance(first, list):
                for index, name in enumerate(columns):
                    if index >= len(first) or not isinstance(name, str):
                        continue
                    value = first[index]
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        continue
                    normalized = float(value)
                    if math.isfinite(normalized):
                        operating_point[name] = normalized
    log_path = _safe_file(root, "run.log", MAX_LOG_BYTES, required=False)
    if log_path is None:
        simulation_log = ""
    else:
        simulation_log = log_path.read_text(encoding="utf-8", errors="replace")
    manifest_path = root / DIRECTORY_MANIFEST_NAME
    return {
        "schema_version": DIAGNOSIS_SCHEMA_VERSION,
        "experiment_id": manifest.entity_id,
        "manifest_sha256": _sha256_file(manifest_path),
        "verification": verification,
        "operating_point": operating_point,
        "simulation_log": simulation_log,
        "analysis": analysis,
        "design_binding": "verified-netlist-match",
    }


def _normalize_failure(value: Mapping[str, Any] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("simulation_failure must be an object")
    allowed = {"code", "type", "stage", "message"}
    if set(value) - allowed:
        raise ValueError("simulation_failure contains unknown fields")
    result: dict[str, str] = {}
    for name in ("code", "type", "stage", "message"):
        if name not in value:
            continue
        raw = value[name]
        if not isinstance(raw, str):
            raise ValueError(f"simulation_failure.{name} must be a string")
        text = raw.strip()
        maximum = 4000 if name == "message" else 256
        if not text or len(text) > maximum or "\x00" in text:
            raise ValueError(f"simulation_failure.{name} is empty or too long")
        result[name] = text
    if not result:
        raise ValueError("simulation_failure must contain at least one field")
    return result


def _node_voltage(values: Mapping[str, float], node: str) -> float | None:
    if node.casefold() in {"0", "gnd", "ground"}:
        return 0.0
    lookup = {str(name).casefold(): float(value) for name, value in values.items()}
    for candidate in (f"v({node})", node):
        value = lookup.get(candidate.casefold())
        if value is not None and math.isfinite(value):
            return value
    return None


def _device_polarity(component: CircuitComponent) -> str | None:
    kind = component.kind.strip().upper()
    if "NPN" in kind:
        return "npn"
    if "PNP" in kind:
        return "pnp"
    return None


class DesignDiagnosisService:
    """Combine deterministic structural, experiment, and failure evidence."""

    def run(
        self,
        design: CircuitDesign,
        *,
        experiment_evidence: Mapping[str, Any] | None = None,
        simulation_failure: Mapping[str, Any] | None = None,
        backend_diagnostics: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        if not isinstance(design, CircuitDesign):
            raise ValueError("design must be CircuitDesign")
        if experiment_evidence is not None and not isinstance(
            experiment_evidence, Mapping
        ):
            raise ValueError("experiment_evidence must be an object")
        failure = _normalize_failure(simulation_failure)
        findings: list[dict[str, Any]] = []
        truncated = False

        def add(
            category: str,
            severity: str,
            code: str,
            summary: str,
            *,
            evidence: Mapping[str, Any] | None = None,
            affected_components: Sequence[str] = (),
            affected_nets: Sequence[str] = (),
            confidence: str = "high",
            actions: Sequence[str] = (),
        ) -> None:
            nonlocal truncated
            if severity not in _FINDING_SEVERITIES:
                raise ValueError("invalid diagnosis finding severity")
            if len(findings) >= MAX_DIAGNOSIS_FINDINGS:
                truncated = True
                return
            findings.append(
                {
                    "finding_id": f"finding-{len(findings) + 1:03d}",
                    "category": category,
                    "severity": severity,
                    "code": code,
                    "summary": summary,
                    "confidence": confidence,
                    "evidence": dict(evidence or {}),
                    "affected_components": list(affected_components),
                    "affected_nets": list(affected_nets),
                    "suggested_actions": list(actions),
                    "auto_fixable": False,
                }
            )

        structural = run_readonly_structural_checks(design)
        for item in structural["diagnostics"]:
            details = item.get("details") or {}
            code = str(item["code"])
            severity = str(item["severity"])
            if code in {"single-connection-net", "component-pins-share-net"}:
                severity = "warning"
            add(
                "topology" if not code.startswith("model-") else "model",
                severity,
                code,
                str(item["message"]),
                evidence=details,
                affected_components=(str(details["refdes"]),)
                if "refdes" in details
                else (),
                affected_nets=(str(details["net"]),) if "net" in details else (),
                actions=(_STRUCTURAL_ACTIONS[code],)
                if code in _STRUCTURAL_ACTIONS
                else (),
            )

        if design.components and not any(
            component.kind.strip().upper() in _SOURCE_KINDS
            for component in design.components
        ):
            add(
                "bias",
                "warning",
                "excitation-source-absent",
                "No structured independent voltage or current source is present.",
                actions=("Confirm that bias and excitation are supplied by an intentional subcircuit.",),
            )

        for diagnostic in backend_diagnostics:
            if not isinstance(diagnostic, Mapping):
                raise ValueError("backend_diagnostics must contain objects")
            severity = str(diagnostic.get("severity", "error"))
            add(
                "backend",
                severity if severity in _FINDING_SEVERITIES else "error",
                str(diagnostic.get("code", "backend-diagnostic")),
                str(diagnostic.get("message", "Backend validation reported a problem.")),
                evidence=diagnostic.get("details")
                if isinstance(diagnostic.get("details"), Mapping)
                else {},
            )

        verification: Mapping[str, Any] | None = None
        operating_point: Mapping[str, float] = {}
        simulation_log = ""
        experiment_summary: dict[str, Any] | None = None
        if experiment_evidence is not None:
            if experiment_evidence.get("schema_version") != DIAGNOSIS_SCHEMA_VERSION:
                raise ValueError("experiment diagnosis evidence schema is invalid")
            raw_verification = experiment_evidence.get("verification")
            if raw_verification is not None and not isinstance(raw_verification, Mapping):
                raise ValueError("verification evidence must be an object")
            verification = raw_verification
            raw_operating_point = experiment_evidence.get("operating_point", {})
            if not isinstance(raw_operating_point, Mapping):
                raise ValueError("operating_point evidence must be an object")
            operating_point = {
                str(name): float(value)
                for name, value in raw_operating_point.items()
                if not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(float(value))
            }
            simulation_log = str(experiment_evidence.get("simulation_log", ""))[
                :MAX_LOG_BYTES
            ]
            experiment_summary = {
                "experiment_id": experiment_evidence.get("experiment_id"),
                "manifest_sha256": experiment_evidence.get("manifest_sha256"),
                "design_binding": experiment_evidence.get("design_binding"),
                "analysis": experiment_evidence.get("analysis", {}),
                "operating_point_signal_count": len(operating_point),
                "verification_available": verification is not None,
            }

        if verification is not None:
            requirements = verification.get("requirements", [])
            if not isinstance(requirements, list):
                raise ValueError("verification requirements must be an array")
            for index, item in enumerate(requirements[:MAX_DIAGNOSIS_FINDINGS]):
                if not isinstance(item, Mapping):
                    continue
                raw_status = item.get("status", "unverified")
                status = (
                    raw_status.strip().lower()
                    if isinstance(raw_status, str)
                    else "unverified"
                )
                if status == "pass":
                    continue
                raw_requirement_id = item.get("id")
                requirement_id = (
                    raw_requirement_id[:256]
                    if isinstance(raw_requirement_id, str) and raw_requirement_id
                    else f"requirement-{index + 1}"
                )
                measurement = item.get("measurement")
                criterion = item.get("criterion")
                compact_evidence = {
                    "requirement_id": requirement_id,
                    "metric": _compact_scalar(item.get("metric")),
                    "signal": _compact_scalar(item.get("signal")),
                    "measurement": _compact_fields(
                        measurement, _MEASUREMENT_EVIDENCE_FIELDS
                    ),
                    "criterion": _compact_fields(
                        criterion, _CRITERION_EVIDENCE_FIELDS
                    ),
                    "reason": _compact_scalar(item.get("reason"), max_chars=1000),
                }
                if status == "fail":
                    add(
                        "requirements",
                        "error",
                        "requirement-failed",
                        f"Design requirement {requirement_id!r} failed.",
                        evidence=compact_evidence,
                        actions=("Trace the failed metric back to its bias, gain, timing, or loading assumptions.",),
                    )
                else:
                    add(
                        "evidence",
                        "warning",
                        "requirement-unverified",
                        f"Design requirement {requirement_id!r} could not be verified.",
                        evidence=compact_evidence,
                        actions=("Add the missing signal, analysis mode, or measurement evidence.",),
                    )

        convergence_text = "\n".join(
            part for part in (simulation_log, "" if failure is None else failure.get("message", "")) if part
        )
        convergence_codes: set[str] = set()
        for pattern, code, summary, action in _CONVERGENCE_PATTERNS:
            if pattern.search(convergence_text):
                convergence_codes.add(code)
                add(
                    "convergence",
                    "error",
                    code,
                    summary,
                    evidence={"failure": failure} if failure is not None else {},
                    actions=(action,),
                )
        if failure is not None and not convergence_codes:
            add(
                "simulation",
                "error",
                "simulation-failed",
                "The simulation failed without a recognized convergence signature.",
                evidence={"failure": failure},
                actions=("Inspect the bounded failure details and reproduce with the same simulator version.",),
            )

        analyzable_devices = 0
        for component in design.components:
            polarity = _device_polarity(component)
            if polarity is None or len(component.nodes) < 3:
                continue
            collector, base, emitter = component.nodes[:3]
            vc = _node_voltage(operating_point, collector)
            vb = _node_voltage(operating_point, base)
            ve = _node_voltage(operating_point, emitter)
            if vc is None or vb is None or ve is None:
                add(
                    "evidence",
                    "info",
                    "bjt-bias-evidence-incomplete",
                    "BJT operating-region diagnosis lacks one or more terminal voltages.",
                    affected_components=(component.refdes,),
                    affected_nets=(collector, base, emitter),
                    actions=("Run a DC operating-point experiment that records collector, base, and emitter voltages.",),
                )
                continue
            analyzable_devices += 1
            forward_be = vb - ve if polarity == "npn" else ve - vb
            forward_ce = vc - ve if polarity == "npn" else ve - vc
            device_evidence = {
                "polarity": polarity,
                "collector_voltage": vc,
                "base_voltage": vb,
                "emitter_voltage": ve,
                "forward_vbe": forward_be,
                "forward_vce": forward_ce,
                "heuristic_thresholds": {"on_vbe": 0.5, "saturation_vce": 0.25},
            }
            if forward_ce < -0.1:
                add(
                    "bias",
                    "warning",
                    "bjt-reverse-bias-likely",
                    "The BJT collector-emitter polarity is opposite the expected active direction.",
                    evidence=device_evidence,
                    affected_components=(component.refdes,),
                    confidence="medium",
                    actions=("Check transistor polarity, pin order, and supply orientation.",),
                )
            elif forward_be >= 0.5 and forward_ce <= 0.25:
                add(
                    "saturation",
                    "warning",
                    "bjt-saturation-likely",
                    "The BJT operating point is consistent with saturation.",
                    evidence=device_evidence,
                    affected_components=(component.refdes,),
                    affected_nets=(collector, base, emitter),
                    confidence="medium",
                    actions=("Review collector loading and base drive against the intended operating region.",),
                )
            elif forward_be < 0.4:
                add(
                    "bias",
                    "info",
                    "bjt-cutoff-likely",
                    "The BJT operating point is consistent with cutoff.",
                    evidence=device_evidence,
                    affected_components=(component.refdes,),
                    confidence="medium",
                    actions=("Confirm that cutoff is expected for this operating condition.",),
                )

        for component in design.components:
            if component.kind.strip().upper() != "OPAMP5" or len(component.nodes) != 5:
                continue
            noninv, inv, positive_rail, negative_rail, output = component.nodes
            voltages = [
                _node_voltage(operating_point, node)
                for node in (noninv, inv, positive_rail, negative_rail, output)
            ]
            if any(value is None for value in voltages):
                continue
            v_noninv, v_inv, v_pos, v_neg, v_out = (float(value) for value in voltages)
            high, low = max(v_pos, v_neg), min(v_pos, v_neg)
            span = high - low
            if span <= 0:
                continue
            analyzable_devices += 1
            margin = max(span * 0.02, 0.05)
            if v_out >= high - margin or v_out <= low + margin:
                add(
                    "saturation",
                    "warning",
                    "opamp-output-near-rail",
                    "The op-amp output is within the diagnostic margin of a supply rail.",
                    evidence={
                        "noninverting_voltage": v_noninv,
                        "inverting_voltage": v_inv,
                        "output_voltage": v_out,
                        "positive_rail_voltage": v_pos,
                        "negative_rail_voltage": v_neg,
                        "rail_margin": margin,
                    },
                    affected_components=(component.refdes,),
                    affected_nets=(output, positive_rail, negative_rail),
                    confidence="medium",
                    actions=("Check closed-loop gain, common-mode range, load, and supply headroom.",),
                )

        severity_counts = Counter(item["severity"] for item in findings)
        category_counts = Counter(item["category"] for item in findings)
        overall_status = (
            "error"
            if severity_counts["error"]
            else "warning"
            if severity_counts["warning"]
            else "no_problem_detected"
        )
        return {
            "schema_version": DIAGNOSIS_SCHEMA_VERSION,
            "success": True,
            "overall_status": overall_status,
            "design_id": design.design_id,
            "design_revision": design.revision,
            "read_only": True,
            "source_design_modified": False,
            "simulation_performed": False,
            "finding_count": len(findings),
            "findings_truncated": truncated,
            "severity_counts": {
                name: int(severity_counts[name]) for name in ("error", "warning", "info")
            },
            "category_counts": dict(sorted(category_counts.items())),
            "findings": findings,
            "evidence": {
                "structural_checks": True,
                "experiment": experiment_summary,
                "simulation_failure_supplied": failure is not None,
                "operating_point_device_count": analyzable_devices,
            },
            "limitations": [
                "Findings are deterministic diagnostics, not proof of electrical correctness.",
                "Device-region thresholds are conservative heuristics and model dependent.",
                "MOS operating regions are not inferred without explicit threshold/model evidence.",
                "No suggested action is applied automatically.",
            ],
        }


__all__ = [
    "DIAGNOSIS_SCHEMA_VERSION",
    "DesignDiagnosisService",
    "load_experiment_diagnosis_evidence",
]
