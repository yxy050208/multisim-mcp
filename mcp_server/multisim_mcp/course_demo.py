"""Course-design waveform demo contract and reproducible reference fixture.

The fixture is intentionally a behavioral reference model.  It exercises the
same verified-experiment/reporting path that a native Multisim 555/74LS74/
LM324 design will use, but it must not be presented as proof of the component
level implementation.  The manifest keeps the course requirements, nominal
targets, load assumptions, and the evidence scope in one auditable object.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .design_verification import ExperimentSpec, validate_experiment_spec


COURSE_DEMO_SCHEMA_VERSION = 2
COURSE_DEMO_ID = "multi-waveform-generator-course-demo"
COURSE_DEMO_TITLE = "多种波形产生电路 / Multi-waveform generator"
COURSE_DEMO_LOAD_OHMS = 600.0
COURSE_DEMO_SUPPLY_VOLTAGE = 10.0
COURSE_DEMO_COMMANDS = "tran 50n 400u"


@dataclass(frozen=True, slots=True)
class CourseWaveformChannel:
    """One externally observable waveform channel in the course brief."""

    channel_id: str
    label_zh: str
    label_en: str
    signal: str
    source_family: str
    frequency_lower_hz: float
    frequency_upper_hz: float
    nominal_frequency_hz: float
    peak_to_peak_volts: float
    component_path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.channel_id,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "signal": self.signal,
            "source_family": self.source_family,
            "frequency_hz": {
                "lower": self.frequency_lower_hz,
                "upper": self.frequency_upper_hz,
                "nominal": self.nominal_frequency_hz,
            },
            "peak_to_peak_volts": self.peak_to_peak_volts,
            "load_ohms": COURSE_DEMO_LOAD_OHMS,
            "component_path": self.component_path,
        }


@dataclass(frozen=True, slots=True)
class CourseBomItem:
    """One row transcribed from the supplied course-design BOM image."""

    item_id: str
    section: str
    name_zh: str
    name_en: str
    category: str
    value_or_part: str
    quantity: int
    model_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "section": self.section,
            "name_zh": self.name_zh,
            "name_en": self.name_en,
            "category": self.category,
            "value_or_part": self.value_or_part,
            "quantity": self.quantity,
            "model_key": self.model_key,
        }


COURSE_WAVEFORM_CHANNELS: tuple[CourseWaveformChannel, ...] = (
    CourseWaveformChannel(
        "square_i",
        "方波 I",
        "Square I",
        "V(sq1)",
        "555 astable",
        20_000.0,
        50_000.0,
        25_000.0,
        1.0,
        "555 timer stage; frequency trimmer",
    ),
    CourseWaveformChannel(
        "square_ii",
        "方波 II",
        "Square II",
        "V(sq2)",
        "74LS74 divider",
        5_000.0,
        10_000.0,
        7_500.0,
        1.0,
        "74LS74 divider stage; frequency trimmer",
    ),
    CourseWaveformChannel(
        "triangle",
        "三角波",
        "Triangle",
        "V(tri)",
        "74LS74 + integrator",
        5_000.0,
        10_000.0,
        7_500.0,
        3.0,
        "74LS74 divider and LM324 integrator",
    ),
    CourseWaveformChannel(
        "sine_i",
        "正弦波 I",
        "Sine I",
        "V(sin1)",
        "LM324 shaping filter",
        20_000.0,
        30_000.0,
        25_000.0,
        3.0,
        "LM324 sine shaper; frequency trimmer",
    ),
    CourseWaveformChannel(
        "sine_ii",
        "正弦波 II",
        "Sine II",
        "V(sin2)",
        "LM324 fixed-frequency stage",
        250_000.0,
        250_000.0,
        250_000.0,
        8.0,
        "LM324 fixed 250 kHz stage",
    ),
)


COURSE_BOM: tuple[CourseBomItem, ...] = (
    CourseBomItem("board", "shared", "双面面包板", "double-sided breadboard", "mechanical", "120x200mm", 1),
    CourseBomItem("square-he555", "square_i", "时基芯片", "timer IC", "integrated-circuit", "HE555", 1, "he555"),
    CourseBomItem("square-c-100n", "square_i", "电容", "capacitor", "capacitor", "0.1uF", 1),
    CourseBomItem("square-c-10n", "square_i", "电容", "capacitor", "capacitor", "0.01uF", 1),
    CourseBomItem("square-c-2n2", "square_i", "电容", "capacitor", "capacitor", "2.2nF", 1),
    CourseBomItem("square-pot-10k", "square_i", "电位器", "potentiometer", "potentiometer", "10K", 2),
    CourseBomItem("square-d-1n4007", "square_i", "二极管", "diode", "diode", "1N4007", 2, "1n4007"),
    CourseBomItem("square-r-330", "square_i", "电阻", "resistor", "resistor", "330R", 2),
    CourseBomItem("square-r-600", "square_i", "电阻", "resistor", "resistor", "600R", 1),
    CourseBomItem("divider-74ls74", "square_ii", "双D触发器", "dual D flip-flop", "integrated-circuit", "74LS74", 1, "74ls74"),
    CourseBomItem("divider-r-10k", "square_ii", "电阻", "resistor", "resistor", "10K", 1),
    CourseBomItem("divider-pot-20k", "square_ii", "电位器", "potentiometer", "potentiometer", "20K", 1),
    CourseBomItem("divider-c-1u", "square_ii", "电容", "capacitor", "capacitor", "1uF", 1),
    CourseBomItem("divider-r-600", "square_ii", "电阻", "resistor", "resistor", "600R", 1),
    CourseBomItem("triangle-lm324", "triangle", "四运算放大器", "quad operational amplifier", "integrated-circuit", "LM324", 1, "lm324"),
    CourseBomItem("triangle-pot-500k", "triangle", "电位器", "potentiometer", "potentiometer", "500K", 1),
    CourseBomItem("triangle-c-1n", "triangle", "电容", "capacitor", "capacitor", "1nF", 1),
    CourseBomItem("triangle-r-30k", "triangle", "电阻", "resistor", "resistor", "30K", 1),
    CourseBomItem("triangle-r-200k", "triangle", "电阻", "resistor", "resistor", "200K", 1),
    CourseBomItem("triangle-r-600", "triangle", "电阻", "resistor", "resistor", "600R", 1),
    CourseBomItem("sine-i-lm324", "sine_i", "四运算放大器", "quad operational amplifier", "integrated-circuit", "LM324", 1, "lm324"),
    CourseBomItem("sine-i-c-10n", "sine_i", "电容", "capacitor", "capacitor", "10nF", 8),
    CourseBomItem("sine-i-pot-100k", "sine_i", "电位器", "potentiometer", "potentiometer", "100K", 2),
    CourseBomItem("sine-i-pot-200k", "sine_i", "电位器", "potentiometer", "potentiometer", "200K", 2),
    CourseBomItem("sine-i-r-250", "sine_i", "电阻", "resistor", "resistor", "250R", 2),
    CourseBomItem("sine-i-r-1k", "sine_i", "电阻", "resistor", "resistor", "1K", 2),
    CourseBomItem("sine-i-r-470", "sine_i", "电阻", "resistor", "resistor", "470R", 2),
    CourseBomItem("sine-i-r-600", "sine_i", "电阻", "resistor", "resistor", "600R", 1),
    CourseBomItem("sine-ii-lm324", "sine_ii", "四运算放大器", "quad operational amplifier", "integrated-circuit", "LM324", 1, "lm324"),
    CourseBomItem("sine-ii-c-100p", "sine_ii", "电容", "capacitor", "capacitor", "100pF", 4),
    CourseBomItem("sine-ii-r-700", "sine_ii", "电阻", "resistor", "resistor", "700R", 1),
    CourseBomItem("sine-ii-pot-10k", "sine_ii", "电位器", "potentiometer", "potentiometer", "10K", 2),
    CourseBomItem("sine-ii-pot-100k", "sine_ii", "电位器", "potentiometer", "potentiometer", "100K", 1),
    CourseBomItem("sine-ii-pot-20k", "sine_ii", "电位器", "potentiometer", "potentiometer", "20K", 1),
    CourseBomItem("sine-ii-r-6k4", "sine_ii", "电阻", "resistor", "resistor", "6.4K", 2),
)


COURSE_COMPONENT_MODEL_POLICY: tuple[dict[str, Any], ...] = (
    {
        "model_key": "he555",
        "required_identity": "HE555",
        "accepted_identities": ["HE555", "NE555", "LM555"],
        "accepted_implementations": ["native-library", "inline-licensed-macro-model"],
        "multisim_database_candidates": [
            {"group": "Mixed", "family": "TIMER", "source_name": "LM555CN", "identity": "LM555"}
        ],
        "required_for_component_claim": True,
        "behavioral_fallback": "explicit PULSE source",
        "fallback_proves_component": False,
        "notes_zh": "优先使用 HE555；NE555/LM555 替代必须记录课程允许和电气兼容依据。",
        "notes_en": "Prefer HE555; NE555/LM555 substitutions need course approval and an electrical-compatibility rationale.",
    },
    {
        "model_key": "74ls74",
        "required_identity": "74LS74",
        "accepted_identities": ["74LS74"],
        "accepted_implementations": ["native-library", "inline-licensed-macro-model"],
        "multisim_database_candidates": [
            {"group": "TTL", "family": "74LS", "source_name": "74LS74N", "identity": "74LS74"},
            {"group": "TTL", "family": "74LS", "source_name": "74LS74D", "identity": "74LS74"},
        ],
        "required_for_component_claim": True,
        "behavioral_fallback": "portable DFF/TFF adapter",
        "fallback_proves_component": False,
        "notes_zh": "便携 DFF/TFF 可验证逻辑关系，但不能证明 74LS74 时序与电气特性。",
        "notes_en": "Portable DFF/TFF adapters prove logic only, not 74LS74 timing or electrical behavior.",
    },
    {
        "model_key": "lm324",
        "required_identity": "LM324",
        "accepted_identities": ["LM324"],
        "accepted_implementations": ["native-library", "inline-licensed-macro-model"],
        "multisim_database_candidates": [
            {"group": "Analog", "family": "OPAMP", "source_name": "LM324M", "identity": "LM324"}
        ],
        "required_for_component_claim": True,
        "behavioral_fallback": "finite-bandwidth generic op-amp",
        "fallback_proves_component": False,
        "notes_zh": "三个 LM324 封装实例必须使用同一已记录模型身份，且检查 250 kHz 裕量。",
        "notes_en": "All three LM324 package instances need one recorded model identity and a 250 kHz margin check.",
    },
    {
        "model_key": "1n4007",
        "required_identity": "1N4007",
        "accepted_identities": ["1N4007"],
        "accepted_implementations": ["native-library", "inline-licensed-macro-model"],
        "multisim_database_candidates": [
            {"group": "Diodes", "family": "DIODE", "source_name": "1N4007", "identity": "1N4007"}
        ],
        "required_for_component_claim": True,
        "behavioral_fallback": "generic power-diode adapter",
        "fallback_proves_component": False,
        "notes_zh": "通用功率二极管适配器不是 1N4007 型号证据。",
        "notes_en": "The generic power-diode adapter is not evidence of a 1N4007 model.",
    },
)


_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_REQUIRED_EXPERIMENT_COUNT = 12


def _required_experiment_ids() -> list[str]:
    return [str(item["id"]) for item in _requirements()]


def _model_policy_by_key() -> dict[str, dict[str, Any]]:
    return {str(item["model_key"]): item for item in COURSE_COMPONENT_MODEL_POLICY}


def course_component_evidence_template() -> dict[str, Any]:
    """Return a fill-in template without pretending any model was verified."""

    return {
        key: {
            "status": "planned",
            "implementation": "native-library",
            "model_identity": str(policy["required_identity"]),
            "source": "",
            "license": "",
            "backend": "multisim",
            "artifact_sha256": "",
            "substitution_justification": "",
        }
        for key, policy in _model_policy_by_key().items()
    }


def assess_course_component_evidence(
    component_evidence: dict[str, Any] | None = None,
    experiment_evidence: dict[str, Any] | None = None,
    *,
    netlist_kind: str = "behavioral-reference",
) -> dict[str, Any]:
    """Evaluate whether a native component-level claim is reproducible.

    This is deliberately stricter than checking that Multisim produced data.
    Each named device needs identity/provenance evidence and the exact five-wave
    experiment must pass all twelve gates on the Multisim backend.
    """

    supplied = component_evidence or {}
    if not isinstance(supplied, dict):
        raise ValueError("component_evidence must be an object")
    experiment = experiment_evidence or {}
    if not isinstance(experiment, dict):
        raise ValueError("experiment_evidence must be an object")
    model_results: list[dict[str, Any]] = []
    for model_key, policy in _model_policy_by_key().items():
        raw = supplied.get(model_key, {})
        problems: list[str] = []
        if not isinstance(raw, dict):
            raw = {}
            problems.append("evidence must be an object")
        status = str(raw.get("status", "missing")).strip().lower()
        implementation = str(raw.get("implementation", "")).strip()
        identity = str(raw.get("model_identity", "")).strip()
        substitution_justification = str(
            raw.get("substitution_justification", "")
        ).strip()
        source = str(raw.get("source", "")).strip()
        license_note = str(raw.get("license", "")).strip()
        backend = str(raw.get("backend", "")).strip().lower()
        artifact_sha256 = str(raw.get("artifact_sha256", "")).strip()
        if status != "verified":
            problems.append("status must be verified")
        if implementation not in policy["accepted_implementations"]:
            problems.append("implementation is not accepted")
        accepted_identities = {
            str(item).casefold()
            for item in policy.get("accepted_identities", [policy["required_identity"]])
        }
        if identity.casefold() not in accepted_identities:
            problems.append(
                "model_identity must be one of "
                + ", ".join(str(item) for item in policy.get("accepted_identities", []))
            )
        elif identity.casefold() != str(policy["required_identity"]).casefold() and not substitution_justification:
            problems.append("substitution_justification is required for an alternate identity")
        if not source:
            problems.append("source is required")
        if not license_note:
            problems.append("license is required")
        if backend != "multisim":
            problems.append("backend must be multisim")
        if not _SHA256_PATTERN.fullmatch(artifact_sha256):
            problems.append("artifact_sha256 must be a 64-digit hexadecimal digest")
        model_results.append(
            {
                "model_key": model_key,
                "required_identity": policy["required_identity"],
                "model_identity": identity or None,
                "status": "verified" if not problems else "unverified",
                "implementation": implementation or None,
                "source": source or None,
                "license": license_note or None,
                "backend": backend or None,
                "artifact_sha256": artifact_sha256 or None,
                "substitution_justification": substitution_justification or None,
                "problems": problems,
            }
        )

    experiment_problems: list[str] = []
    experiment_status = str(experiment.get("status", "missing")).strip().lower()
    experiment_backend = str(experiment.get("backend_id", "")).strip().lower()
    overall_status = str(experiment.get("overall_status", "")).strip().lower()
    passed = experiment.get("passed")
    failed = experiment.get("failed")
    unverified = experiment.get("unverified")
    experiment_sha256 = str(experiment.get("artifact_sha256", "")).strip()
    requirement_ids = experiment.get("requirement_ids")
    if experiment_status != "verified":
        experiment_problems.append("status must be verified")
    if experiment_backend != "multisim":
        experiment_problems.append("backend_id must be multisim")
    if overall_status != "pass":
        experiment_problems.append("overall_status must be pass")
    if passed != _REQUIRED_EXPERIMENT_COUNT or failed != 0 or unverified != 0:
        experiment_problems.append("all 12 requirements must pass with no failed or unverified result")
    if not _SHA256_PATTERN.fullmatch(experiment_sha256):
        experiment_problems.append("artifact_sha256 must be a 64-digit hexadecimal digest")
    if not isinstance(requirement_ids, list) or {
        str(item) for item in requirement_ids
    } != set(_required_experiment_ids()):
        experiment_problems.append("requirement_ids must identify the exact 12 course gates")
    experiment_result = {
        "status": "verified" if not experiment_problems else "unverified",
        "backend_id": experiment_backend or None,
        "overall_status": overall_status or None,
        "passed": passed,
        "failed": failed,
        "unverified": unverified,
        "artifact_sha256": experiment_sha256 or None,
        "requirement_ids": requirement_ids if isinstance(requirement_ids, list) else [],
        "problems": experiment_problems,
    }
    missing_models = [
        item["model_key"] for item in model_results if item["status"] != "verified"
    ]
    native_netlist = str(netlist_kind).strip() == "native-multisim"
    claim_ready = native_netlist and not missing_models and not experiment_problems
    blockers = []
    if not native_netlist:
        blockers.append("netlist_kind must be native-multisim")
    blockers.extend(f"model evidence incomplete: {key}" for key in missing_models)
    blockers.extend(f"experiment evidence: {item}" for item in experiment_problems)
    return {
        "schema_version": 1,
        "claim_ready": claim_ready,
        "component_level_claim": claim_ready,
        "verified_model_count": len(model_results) - len(missing_models),
        "required_model_count": len(model_results),
        "missing_models": missing_models,
        "models": model_results,
        "experiment": experiment_result,
        "blockers": blockers,
    }


def load_course_experiment_evidence(output_dir: str | Path) -> dict[str, Any]:
    """Load and integrity-check one completed course experiment directory."""

    root = Path(output_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("course experiment output directory does not exist")
    manifest_path = root / "manifest.json"
    verification_path = root / "verification.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
        verification_bytes = verification_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read course experiment evidence: {exc}") from exc
    if len(manifest_bytes) > 2_000_000 or len(verification_bytes) > 4_000_000:
        raise ValueError("course experiment evidence exceeds the size limit")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        verification = json.loads(verification_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"invalid course experiment evidence JSON: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(verification, dict):
        raise ValueError("course experiment manifest and verification must be objects")
    backend = manifest.get("backend")
    backend_id = (
        str(backend.get("backend_id", "")) if isinstance(backend, dict) else ""
    )
    counts = verification.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    requirements = verification.get("requirements")
    requirement_ids = (
        [str(item.get("id", "")) for item in requirements if isinstance(item, dict)]
        if isinstance(requirements, list)
        else []
    )
    digest = hashlib.sha256(verification_bytes).hexdigest()
    recorded_digest = ""
    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, list):
        for item in artifacts:
            if isinstance(item, dict) and item.get("filename") == "verification.json":
                recorded_digest = str(item.get("sha256", ""))
                break
    integrity_ok = recorded_digest == digest
    return {
        "status": "verified" if integrity_ok else "unverified",
        "backend_id": backend_id,
        "overall_status": str(verification.get("overall_status", "")),
        "passed": counts.get("pass"),
        "failed": counts.get("fail"),
        "unverified": counts.get("unverified"),
        "artifact_sha256": digest,
        "requirement_ids": requirement_ids,
        "integrity": {
            "recorded_sha256": recorded_digest or None,
            "matches_manifest": integrity_ok,
        },
        "experiment_id": manifest.get("experiment_id"),
    }
def _channel_by_id() -> dict[str, CourseWaveformChannel]:
    return {channel.channel_id: channel for channel in COURSE_WAVEFORM_CHANNELS}


def _requirements() -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for channel in COURSE_WAVEFORM_CHANNELS:
        frequency_parameters: dict[str, Any] = {
            # The behavioral square sources swing 0..1 V; centered triangle
            # and sine sources cross 0 V.  Keep the threshold inside the
            # measured range so a missing/incorrect source is unverified.
            "threshold": 0.5 if channel.channel_id.startswith("square") else 0.0,
            "edge": "rising",
            "hysteresis": 0.05,
            "min_cycles": 2,
        }
        requirements.append(
            {
                "id": f"{channel.channel_id}_frequency",
                "metric": "frequency",
                "signal": channel.signal,
                "unit": "Hz",
                "parameters": frequency_parameters,
                "operator": "between"
                if channel.frequency_lower_hz != channel.frequency_upper_hz
                else "approximately",
                "lower": channel.frequency_lower_hz,
                "upper": channel.frequency_upper_hz,
                "target": channel.nominal_frequency_hz,
                "tolerance_percent": 5.0,
                "theoretical_value": channel.nominal_frequency_hz,
            }
        )
        requirements.append(
            {
                "id": f"{channel.channel_id}_amplitude",
                "metric": "peak_to_peak",
                "signal": channel.signal,
                "unit": "Vpp",
                "parameters": {},
                "operator": "approximately",
                "target": channel.peak_to_peak_volts,
                "tolerance_percent": 5.0,
                "theoretical_value": channel.peak_to_peak_volts,
            }
        )
        if channel.channel_id in {"sine_i", "sine_ii"}:
            requirements.append(
                {
                    "id": f"{channel.channel_id}_thd",
                    "metric": "thd",
                    "signal": channel.signal,
                    "unit": "%",
                    "parameters": {
                        "fundamental_frequency": channel.nominal_frequency_hz,
                        "harmonics": 10,
                        "threshold": 0.0,
                        "edge": "rising",
                        "hysteresis": 0.05,
                        "min_cycles": 2,
                    },
                    "operator": "at_most",
                    "target": 5.0,
                    "theoretical_value": 0.0,
                }
            )
    return requirements


def behavioral_reference_netlist() -> str:
    """Return a safe, dependency-free behavioral reference netlist.

    Every output is loaded by 600 ohms and the supply/test node is +10 V.  The
    source waveforms are deliberately explicit so the same contract can be
    run with ngspice in CI or through the local Multisim backend.  This is not a
    claim that the source elements replace the required ICs.
    """

    triangle_points = " ".join(
        f"{time}u {value}"
        for time, value in (
            (0, -1.5),
            (66.6667, 1.5),
            (133.3333, -1.5),
            (200.0, 1.5),
            (266.6667, -1.5),
            (333.3333, 1.5),
            (400.0, -1.5),
        )
    )
    return f"""* {COURSE_DEMO_ID}
* Behavioral reference only; replace V sources with the native IC design.
VDD vdd 0 {COURSE_DEMO_SUPPLY_VOLTAGE:g}
V_SQ1 sq1 0 PULSE(0 1 0 1n 1n 20u 40u)
V_SQ2 sq2 0 PULSE(0 1 0 1n 1n 66.6667u 133.3333u)
V_TRI tri 0 PWL({triangle_points})
V_SIN1 sin1 0 SIN(0 1.5 25k)
V_SIN2 sin2 0 SIN(0 4 250k)
RLOAD_SQ1 sq1 0 {COURSE_DEMO_LOAD_OHMS:g}
RLOAD_SQ2 sq2 0 {COURSE_DEMO_LOAD_OHMS:g}
RLOAD_TRI tri 0 {COURSE_DEMO_LOAD_OHMS:g}
RLOAD_SIN1 sin1 0 {COURSE_DEMO_LOAD_OHMS:g}
RLOAD_SIN2 sin2 0 {COURSE_DEMO_LOAD_OHMS:g}
.end
"""


def behavioral_reference_commands() -> str:
    return COURSE_DEMO_COMMANDS


def build_course_demo_spec(
    netlist: str | None = None,
    commands: str | None = None,
) -> ExperimentSpec:
    """Build and validate an ExperimentSpec for the five-channel brief."""

    chosen_netlist = behavioral_reference_netlist() if netlist is None else netlist
    chosen_commands = (
        behavioral_reference_commands() if commands is None else commands
    )
    spec: ExperimentSpec = {
        "schema_version": 1,
        "title": COURSE_DEMO_TITLE,
        "netlist": chosen_netlist,
        "commands": chosen_commands,
        "requirements": _requirements(),
        "theoretical_values": {
            item["id"]: float(item["theoretical_value"])
            for item in _requirements()
            if "theoretical_value" in item
        },
    }
    return validate_experiment_spec(spec)  # type: ignore[return-value]


def build_course_demo_manifest(
    *,
    netlist_kind: str = "behavioral-reference",
    backend_note: str = "Requires a real local simulator for measured evidence.",
    component_evidence: dict[str, Any] | None = None,
    experiment_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the bilingual, auditable requirement manifest."""

    normalized_kind = str(netlist_kind).strip()
    if not normalized_kind:
        raise ValueError("netlist_kind must not be empty")
    note = str(backend_note).strip()
    if not note:
        raise ValueError("backend_note must not be empty")
    readiness = assess_course_component_evidence(
        component_evidence,
        experiment_evidence,
        netlist_kind=normalized_kind,
    )
    return {
        "schema_version": COURSE_DEMO_SCHEMA_VERSION,
        "demo_id": COURSE_DEMO_ID,
        "title_zh": COURSE_DEMO_TITLE.split(" / ", 1)[0],
        "title_en": COURSE_DEMO_TITLE.split(" / ", 1)[1],
        "evidence_scope": {
            "netlist_kind": normalized_kind,
            "component_level_claim": readiness["claim_ready"],
            "backend_note": note,
        },
        "supply": {"voltage_v": COURSE_DEMO_SUPPLY_VOLTAGE, "regulated": True},
        "default_load_ohms": COURSE_DEMO_LOAD_OHMS,
        "test_terminals": [
            "VDD_10V",
            "GND",
            *(channel.channel_id.upper() for channel in COURSE_WAVEFORM_CHANNELS),
        ],
        "channels": [channel.as_dict() for channel in COURSE_WAVEFORM_CHANNELS],
        "bom_source": {
            "kind": "user-supplied-image-transcription",
            "language": "zh-CN",
            "human_confirmation_required": True,
        },
        "bom": [item.as_dict() for item in COURSE_BOM],
        "component_model_policy": [dict(item) for item in COURSE_COMPONENT_MODEL_POLICY],
        "component_evidence": component_evidence or {},
        "experiment_evidence": experiment_evidence or {},
        "component_readiness": readiness,
    }


def validate_course_demo_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate a manifest without accepting unknown channels or bad targets."""

    if not isinstance(manifest, dict):
        raise ValueError("course demo manifest must be an object")
    required = {
        "schema_version",
        "demo_id",
        "evidence_scope",
        "supply",
        "channels",
        "bom",
        "component_model_policy",
        "component_readiness",
    }
    missing = required - set(manifest)
    if missing:
        raise ValueError("course demo manifest is missing: " + ", ".join(sorted(missing)))
    if manifest["schema_version"] != COURSE_DEMO_SCHEMA_VERSION:
        raise ValueError("unsupported course demo manifest schema_version")
    if manifest["demo_id"] != COURSE_DEMO_ID:
        raise ValueError("unexpected course demo id")
    channels = manifest["channels"]
    if not isinstance(channels, list) or len(channels) != len(COURSE_WAVEFORM_CHANNELS):
        raise ValueError("course demo manifest must contain exactly five channels")
    expected = _channel_by_id()
    seen: set[str] = set()
    for item in channels:
        if not isinstance(item, dict):
            raise ValueError("course demo channel must be an object")
        channel_id = str(item.get("id", ""))
        if channel_id in seen or channel_id not in expected:
            raise ValueError(f"unknown or duplicate course demo channel: {channel_id!r}")
        seen.add(channel_id)
        channel = expected[channel_id]
        frequency = item.get("frequency_hz")
        if not isinstance(frequency, dict):
            raise ValueError(f"{channel_id}.frequency_hz must be an object")
        lower = float(frequency.get("lower", math.nan))
        upper = float(frequency.get("upper", math.nan))
        nominal = float(frequency.get("nominal", math.nan))
        if not all(math.isfinite(value) for value in (lower, upper, nominal)):
            raise ValueError(f"{channel_id}.frequency_hz must be finite")
        if lower <= 0 or upper < lower or not lower <= nominal <= upper:
            raise ValueError(f"{channel_id}.frequency_hz range is invalid")
        amplitude = float(item.get("peak_to_peak_volts", math.nan))
        if not math.isfinite(amplitude) or amplitude <= 0:
            raise ValueError(f"{channel_id}.peak_to_peak_volts must be positive")
        load = float(item.get("load_ohms", math.nan))
        if not math.isfinite(load) or load <= 0:
            raise ValueError(f"{channel_id}.load_ohms must be positive")
    if seen != set(expected):
        raise ValueError("course demo manifest channel set is incomplete")
    bom = manifest["bom"]
    if not isinstance(bom, list) or len(bom) != len(COURSE_BOM):
        raise ValueError(f"course demo manifest must contain exactly {len(COURSE_BOM)} BOM rows")
    expected_bom = {item.item_id: item for item in COURSE_BOM}
    seen_bom: set[str] = set()
    for item in bom:
        if not isinstance(item, dict):
            raise ValueError("course demo BOM row must be an object")
        item_id = str(item.get("id", ""))
        if item_id in seen_bom or item_id not in expected_bom:
            raise ValueError(f"unknown or duplicate course demo BOM row: {item_id!r}")
        seen_bom.add(item_id)
        expected_item = expected_bom[item_id]
        if str(item.get("value_or_part", "")) != expected_item.value_or_part:
            raise ValueError(f"{item_id}.value_or_part does not match the course BOM")
        if item.get("quantity") != expected_item.quantity:
            raise ValueError(f"{item_id}.quantity does not match the course BOM")
    policy = manifest["component_model_policy"]
    if not isinstance(policy, list) or {
        str(item.get("model_key", "")) for item in policy if isinstance(item, dict)
    } != set(_model_policy_by_key()):
        raise ValueError("course demo component model policy is incomplete")
    scope = manifest["evidence_scope"]
    if not isinstance(scope, dict):
        raise ValueError("course demo evidence_scope must be an object")
    recalculated = assess_course_component_evidence(
        manifest.get("component_evidence"),
        manifest.get("experiment_evidence"),
        netlist_kind=str(scope.get("netlist_kind", "")),
    )
    if manifest["component_readiness"] != recalculated:
        raise ValueError("course demo component_readiness does not match its evidence")
    if bool(scope.get("component_level_claim")) != recalculated["claim_ready"]:
        raise ValueError("component_level_claim is inconsistent with the evidence gate")
    return manifest


def write_course_demo_bundle(
    output_dir: str | Path,
    *,
    netlist: str | None = None,
    commands: str | None = None,
    netlist_kind: str = "behavioral-reference",
    backend_note: str = "Requires a real local simulator for measured evidence.",
    component_evidence: dict[str, Any] | None = None,
    experiment_evidence: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write a small, deterministic bundle for demos and competition review."""

    root = Path(output_dir).expanduser().resolve()
    if root == Path(root.anchor):
        raise ValueError("output_dir must not be a filesystem root")
    if root.exists() and not root.is_dir():
        raise ValueError("output_dir must be a directory")
    if root.exists() and any(root.iterdir()) and not overwrite:
        raise FileExistsError("output_dir is not empty; pass overwrite=True")
    root.mkdir(parents=True, exist_ok=True)
    chosen_netlist = behavioral_reference_netlist() if netlist is None else netlist
    chosen_commands = behavioral_reference_commands() if commands is None else commands
    spec = build_course_demo_spec(chosen_netlist, chosen_commands)
    manifest = validate_course_demo_manifest(
        build_course_demo_manifest(
            netlist_kind=netlist_kind,
            backend_note=backend_note,
            component_evidence=component_evidence,
            experiment_evidence=experiment_evidence,
        )
    )
    files = {
        "course-demo-manifest.json": manifest,
        "course-demo-spec.json": spec,
    }
    for filename, payload in files.items():
        (root / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (root / "behavioral-reference.cir").write_text(chosen_netlist, encoding="utf-8")
    (root / "analysis-commands.txt").write_text(chosen_commands + "\n", encoding="utf-8")
    bom_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        bom_buffer,
        fieldnames=(
            "id",
            "section",
            "name_zh",
            "name_en",
            "category",
            "value_or_part",
            "quantity",
            "model_key",
        ),
    )
    writer.writeheader()
    writer.writerows(item.as_dict() for item in COURSE_BOM)
    (root / "course-bom.csv").write_text(bom_buffer.getvalue(), encoding="utf-8")
    (root / "component-readiness.json").write_text(
        json.dumps(
            manifest["component_readiness"],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "course-component-evidence.template.json").write_text(
        json.dumps(
            course_component_evidence_template(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "course-experiment-evidence.json").write_text(
        json.dumps(
            manifest["experiment_evidence"],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    missing_models = manifest["component_readiness"]["missing_models"]
    missing_text = ", ".join(missing_models) if missing_models else "none / 无"
    (root / "native-implementation-plan.md").write_text(
        "# 原生元器件实现计划 / Native component implementation plan\n\n"
        f"- Component claim ready: `{manifest['component_readiness']['claim_ready']}`\n"
        f"- Missing model evidence: `{missing_text}`\n"
        "- Required identities: `HE555`, `74LS74`, `LM324`, `1N4007`.\n"
        "- Database paths in the model policy are version-specific candidates, not proof; probe them in an isolated local worker.\n"
        "- All model records need source, license, Multisim backend identity, and a SHA-256 artifact fingerprint.\n"
        "- The same native netlist must pass all 12 frequency/amplitude/THD gates.\n"
        "- Two fixed 74LS74 divide-by-two stages map 20-50 kHz to 5-12.5 kHz; "
        "the common linked range is therefore 20-40 kHz unless a selectable or independent divider is used.\n"
        "- 中文：BOM 来自用户图片转录，制作或采购前仍需人工逐项核对。\n",
        encoding="utf-8",
    )
    (root / "evidence-scope.md").write_text(
        "# 证据边界 / Evidence scope\n\n"
        f"- Netlist kind: `{manifest['evidence_scope']['netlist_kind']}`\n"
        f"- Component-level claim: `{manifest['evidence_scope']['component_level_claim']}`\n"
        f"- 中文说明：{manifest['evidence_scope']['backend_note']}\n"
        "- English: Behavioral reference PASS does not prove native IC, vendor "
        "macro-model, tolerance, parasitic, or bench-measurement performance.\n",
        encoding="utf-8",
    )
    return {
        "schema_version": COURSE_DEMO_SCHEMA_VERSION,
        "demo_id": COURSE_DEMO_ID,
        "output_dir": str(root),
        "files": {
            name: str(root / name)
            for name in (
                *files,
                "behavioral-reference.cir",
                "analysis-commands.txt",
                "course-bom.csv",
                "course-component-evidence.template.json",
                "course-experiment-evidence.json",
                "component-readiness.json",
                "native-implementation-plan.md",
                "evidence-scope.md",
            )
        },
        "bom_row_count": len(COURSE_BOM),
        "bom_total_quantity": sum(item.quantity for item in COURSE_BOM),
        "channel_count": len(COURSE_WAVEFORM_CHANNELS),
        "requirement_count": len(spec["requirements"]),
        "evidence_scope": manifest["evidence_scope"],
    }


__all__ = [
    "COURSE_BOM",
    "COURSE_COMPONENT_MODEL_POLICY",
    "COURSE_DEMO_COMMANDS",
    "COURSE_DEMO_ID",
    "COURSE_DEMO_LOAD_OHMS",
    "COURSE_DEMO_SCHEMA_VERSION",
    "COURSE_DEMO_SUPPLY_VOLTAGE",
    "COURSE_DEMO_TITLE",
    "COURSE_WAVEFORM_CHANNELS",
    "CourseBomItem",
    "CourseWaveformChannel",
    "assess_course_component_evidence",
    "behavioral_reference_commands",
    "behavioral_reference_netlist",
    "build_course_demo_manifest",
    "build_course_demo_spec",
    "course_component_evidence_template",
    "load_course_experiment_evidence",
    "validate_course_demo_manifest",
    "write_course_demo_bundle",
]
