"""Read-only engineering discovery, separate from execution or certification.

Catalog labels, implemented entrypoints, compiler options, versioned mappings,
and documented native cases are different claims. None activates an EDA backend
or proves that an arbitrary new circuit will pass on the current machine.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .circuit_template_registry import list_template_families
from .component_compat import validate_component_manifest
from .executable_netlists import COMPILER_SUPPORT_MATRIX
from .multisim_compat import parse_multisim_version
from .tool_profiles import PROFILE_TOOL_NAMES, normalize_tool_profile


ENGINEERING_CAPABILITIES_SCHEMA_VERSION = 1
_MANIFEST_ROOT = Path(__file__).parent / "compatibility"
_TARGET_VERSION = re.compile(r"(?:NI\s+)?(?:Multisim\s+)?[1-9]\d*(?:\.\d+){0,2}", re.I)

# These are public documentation records, not freshly checked experiment files.
# Keep sampled scopes explicit. New cases require new native evidence first.
_NATIVE_RECORDS = {
    "approved-signal-two-board": {
        "document": "docs/SIGNAL_MULTIBOARD_NATIVE_ACCEPTANCE_20261011.md",
        "version": "14.3", "analyses": ["dc", "ac"],
        "cases": ["nominal-dc", "nominal-ac", "gain-positive-ac", "zero-source-dc"],
        "scope": "Fixed ideal sensor/buffer and gain/bias boards, HDR1X4 with two ground pins, explicit upstream RC fixture, strict saved-file pin/RLC/model checks, per-board/full-reference sampled measurements and separate board-only drawings. Synthetic regression approval flags; no vendor or PCB certification.",
    },
    "ideal-signal-fixture": {
        "document": "docs/ACTIVE_SIGNAL_NATIVE_ACCEPTANCE_20261011.md",
        "version": "14.3",
        "analyses": ["op", "ac"],
        "cases": ["nominal", "gain-positive", "negative-bias", "zero-source"],
        "scope": "Four compiled ideal signal designs with explicit reviewed DC/AC fixtures, native saved-project pin/RLC/model readback and sampled measurements. Synthetic approval flags are test fixtures; no vendor or physical-board certification.",
    },
    "generated-analog": {
        "document": "docs/GENERATED_ANALOG_ACCEPTANCE.md",
        "version": "14.3",
        "analyses": ["op", "ac", "tran"],
        "scope": "Documented divider and unloaded RC low-pass baseline only; separate native acceptance runner, not a natural-language entrypoint test.",
    },
    "natural-rc": {
        "document": "docs/NATURAL_ENGINEERING.md",
        "version": "14.3",
        "analyses": ["op", "ac", "tran"],
        "scope": "Listed 1 kHz/800 Hz unloaded RC requests and a correctly rejected fixed-value request.",
    },
    "natural-rlc": {
        "document": "docs/NATURAL_ENGINEERING.md",
        "version": "14.3",
        "analyses": ["op", "ac"],
        "scope": "Listed 10 mH/2.5 uF second-order low-pass and bounded E24 damping candidates.",
    },
    "model-rc": {
        "document": "docs/NATURAL_ENGINEERING.md",
        "version": "14.3",
        "analyses": ["op", "ac", "tran"],
        "scope": "Documented model requirement restatement followed by local RC native execution; not autonomous circuit invention.",
    },
    "composed-analog": {
        "document": "docs/COMPOSED_ANALOG_ACCEPTANCE_20260909.md",
        "version": "14.3",
        "analyses": ["op", "ac"],
        "scope": "Two/four-stage ideal-opamp RC chains and a loaded RLC bridge; includes a correctly rejected variant.",
    },
    "lm324-frontend": {
        "document": "docs/THESIS_PLUGIN_ACCEPTANCE_20260909.md",
        "version": "14.3",
        "analyses": ["op", "ac", "tran"],
        "scope": "Sampled LM324AJ section-A frontend with local licensed models.",
    },
    "lm324-vendor-two-board": {
        "document": "docs/VENDOR_MULTIBOARD_ACCEPTANCE_20261011.md",
        "version": "14.3",
        "analyses": ["dc", "ac", "tran"],
        "scope": "Fixed sensor/conditioner two-board LM324AJ section-A contract with HDR1X4 P1 stage1, P2 VCC, P3 VSS, P4 ground; native OP/AC/TRAN, save/reopen model and pin checks, plus fixture-free physical board drawings. No arbitrary vendor multi-board or PCB certification.",
    },
    "rectifier": {
        "document": "docs/RECTIFIER_NATIVE_ACCEPTANCE.md",
        "version": "14.3",
        "analyses": ["op", "tran"],
        "scope": "Listed low-voltage 1N4001GP bridge cases with resistive loads; excludes thermal and safety certification.",
    },
    "common-emitter": {
        "document": "docs/COMMON_EMITTER_NATIVE_ACCEPTANCE.md",
        "version": "14.3",
        "analyses": ["op", "ac", "tran"],
        "scope": "Listed 2N3904 bias, gain and bounded sine/candidate experiments; excludes arbitrary transistor designs.",
    },
    "digital-matrix": {
        "document": "docs/DIGITAL_REGRESSION_MATRIX.md",
        "version": "14.3",
        "analyses": ["tran"],
        "cases": ["logic_chain_load", "dff_load", "counter4_load", "shift4_load", "counter4_decode_load"],
        "scope": "Five documented logic/sequential designs with output loads; not all digital devices or topologies.",
    },
    "hybrid-matrix": {
        "document": "docs/HYBRID_REGRESSION_MATRIX.md",
        "version": "14.3",
        "analyses": ["tran"],
        "cases": ["not_rc_load", "logic_chain_rc_load", "counter_q0_rc_load", "shift_s0_rc_load", "diode_rc_shaper", "counter_q0_q1_rc_load", "vcvs_rc_bridge", "vccs_rc_bridge", "dac_rc_bridge", "adc_rc_bridge", "adc4_rc_bridge", "dac4_rc_bridge", "adc4_dac4_transfer"],
        "scope": "Thirteen documented digital/RC and behavioral converter cases; not arbitrary vendor ADC/DAC chips.",
    },
    "multiboard-digital": {
        "document": "docs/MULTIBOARD_DIGITAL_REGRESSION.md",
        "version": "14.3",
        "analyses": ["dc", "tran", "ac"],
        "cases": ["split_logic_load", "split_mixed_logic"],
        "scope": "Two boards, HDR1X4, explicit voltage fixtures and full-reference comparisons; not unrestricted bidirectional co-simulation or physical PCB verification.",
    },
}


def _workflow(
    workflow_id: str, families: tuple[str, ...], input_kind: str,
    scope: str, entrypoints: tuple[str, ...], records: tuple[str, ...] = (),
    record_scope: str = "workflow-cases",
) -> dict[str, Any]:
    return {
        "id": workflow_id, "families": families, "input_kind": input_kind,
        "scope": scope, "entrypoints": entrypoints, "native_record_ids": records,
        "native_record_scope": record_scope if records else "none",
    }


_WORKFLOWS = (
    _workflow("approved-signal-two-board", ("analog_signal_conditioning", "filtering", "system_multi_module"), "approved-compiled-fixture",
              "Exact fixed two-board request rederived from approved ideal signal fixture; DC/AC with explicit upstream circuit fragment and separate board drawings/BOM.",
              ("plan_multiboard_engineering_request", "run_native_multiboard_acceptance"), ("approved-signal-two-board",)),
    _workflow("approved-ideal-signal-fixture", ("analog_signal_conditioning", "filtering"), "approved-compiled-fixture",
              "Explicit DC/AC source and source impedance composed before netlist review; native proposal execute controls analyses and checks separately.",
              ("compile_executable_netlist", "approve_executable_netlist", "run_generated_analog_project"), ("ideal-signal-fixture",)),
    _workflow("natural-rc", ("filtering",), "bounded-natural-language",
              "Recognized RC low-pass requirements and bounded parameter candidates.",
              ("plan_natural_engineering_request", "run_natural_engineering_request"), ("natural-rc",)),
    _workflow("natural-rlc", ("filtering",), "bounded-natural-language",
              "Recognized RLC requirements, not arbitrary filters.",
              ("plan_natural_rlc_engineering_request", "run_natural_rlc_engineering_request"), ("natural-rlc",)),
    _workflow("natural-opamp", ("analog_signal_conditioning",), "bounded-natural-language",
              "Supported ideal-opamp gain requests; vendor models need separate validation.",
              ("plan_natural_opamp_engineering_request", "run_natural_opamp_engineering_request")),
    _workflow("natural-frontend", ("analog_signal_conditioning", "filtering"), "bounded-natural-language",
              "Supported sensor frontend contracts; model identities must be preserved.",
              ("plan_natural_analog_frontend", "run_natural_analog_frontend"), ("lm324-frontend",), "related-backend-cases"),
    _workflow("lm324-vendor-two-board", ("analog_signal_conditioning", "filtering", "system_multi_module"), "structured-vendor-board-contract",
              "Exact reviewed LM324AJ two-board topology with explicit native vendor fixture, connector pins, save/reopen and native sampled analysis.",
              ("plan_multiboard_engineering_request", "run_native_multiboard_acceptance"), ("lm324-vendor-two-board",)),
    _workflow("composed-analog", ("analog_signal_conditioning", "filtering", "system_multi_module"), "structured-design",
              "Supported passive/ideal-opamp netlists and measurable requirements supplied by the host agent.",
              ("run_generated_analog_project",), ("composed-analog", "lm324-frontend")),
    _workflow("natural-dc", ("dc_network",), "bounded-natural-language",
              "Recognized DC network requests; no native case record is asserted here.",
              ("plan_natural_dc_network", "run_natural_dc_network")),
    _workflow("natural-rectifier", ("rectifier_supply",), "bounded-natural-language",
              "Bounded low-voltage bridge design using a local 1N4001GP model.",
              ("plan_natural_rectifier", "run_natural_rectifier"), ("rectifier",)),
    _workflow("natural-common-emitter", ("transistor_discrete",), "bounded-natural-language",
              "Bounded 2N3904 common-emitter design and native candidates.",
              ("plan_natural_common_emitter", "run_natural_common_emitter"), ("common-emitter",)),
    _workflow("digital-netlist", ("digital_logic",), "supported-netlist",
              "Host-supplied supported logic primitives/adapters with explicit stimuli and requirements.",
              ("create_schematic_from_netlist", "run_circuit_experiment"), ("digital-matrix",)),
    _workflow("hybrid-netlist", ("digital_logic", "filtering", "waveform_shaping"), "supported-netlist",
              "Supported digital/analog loads and explicitly behavioral ADC/DAC adapters.",
              ("create_schematic_from_netlist", "run_circuit_experiment"), ("hybrid-matrix",), "related-backend-cases"),
    _workflow("basic-multiboard", ("system_multi_module",), "structured-board-contract",
              "Explicit board assignments, connector pins, fixtures, per-board native runs and consolidated interface/reference checks.",
              ("plan_multiboard_engineering_request", "select_multiboard_engineering_candidate", "run_native_multiboard_acceptance"), ("multiboard-digital",)),
    _workflow("model-rc", ("filtering",), "model-assisted-bounded-natural-language",
              "Internal model restates RC requirements; original numeric requirements and the derived plan are checked before execution.",
              ("plan_model_engineering_request", "run_model_engineering_request", "submit_model_engineering_request"), ("model-rc",)),
)


def _normalize_target_version(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64 or not _TARGET_VERSION.fullmatch(value.strip()):
        raise ValueError("target_version must be a Multisim version such as '14.3'")
    major, minor, patch = parse_multisim_version(value)
    return f"{major}.{minor}" + (f".{patch}" if patch else "")


def component_mapping_capabilities(target_version: str | None = None) -> dict[str, Any]:
    """Read shipped manifests only; never substitute a neighboring version.

    A manifest verification flag describes recorded mapping evidence, not the
    existence/licensing of a user's local template pack or a fresh native run.
    """
    target = _normalize_target_version(target_version)
    manifests = []
    selected = []
    for path in sorted(_MANIFEST_ROOT.glob("components-*.json")):
        content = path.read_bytes()
        manifest = validate_component_manifest(json.loads(content.decode("utf-8")))
        version = _normalize_target_version(manifest["multisim_version"])
        manifests.append({
            "version": version, "file": path.name,
            "sha256": hashlib.sha256(content).hexdigest(),
        })
        if target is not None and parse_multisim_version(version) == parse_multisim_version(target):
            for item in manifest["components"]:
                versions = item.get("supported_versions") or [version]
                if not any(parse_multisim_version(str(v)) == parse_multisim_version(target) for v in versions):
                    continue
                selected.append({
                    "logical_family": item["logical_family"],
                    "native_name": item["native_name"],
                    "pin_signature": list(item["pin_signature"]),
                    "model_source": item["model_source"],
                    "status": "recorded-native-verified" if item.get("verified") is True else "unverified",
                    "evidence": item.get("evidence"),
                    "manifest": path.name,
                })
    exact = [item for item in manifests if item["version"] == target]
    return {
        "target_version": target,
        "status": "not-selected" if target is None else "exact-manifest" if exact else "manifest-unavailable",
        "available_manifest_versions": sorted({item["version"] for item in manifests}),
        "manifests": manifests,
        "components": selected,
        "current_machine_verified": False,
        "scope": "Recorded per-component mappings only; templates, model licenses and native behavior must be checked at execution.",
    }


def build_engineering_capabilities(
    *, profile: str = "full", target_version: str | None = None,
) -> dict[str, Any]:
    """Build deterministic metadata shared by MCP, CLI and loopback API."""
    normalized_profile = normalize_tool_profile(profile)
    enabled = PROFILE_TOOL_NAMES[normalized_profile]
    mappings = component_mapping_capabilities(target_version)
    target = mappings["target_version"]
    records = []
    for record_id, value in _NATIVE_RECORDS.items():
        record = deepcopy(value)
        record.update({
            "id": record_id,
            "evidence_kind": "public-documentation-record",
            "raw_artifacts_checked": False,
            "current_machine_verified": False,
            "target_version_match": None if target is None else parse_multisim_version(target) == parse_multisim_version(record["version"]),
        })
        records.append(record)
    records_by_id = {item["id"]: item for item in records}
    workflows = []
    for source in _WORKFLOWS:
        item = deepcopy(source)
        for key in ("families", "entrypoints", "native_record_ids"):
            item[key] = list(item[key])
        item["implementation_status"] = "implemented-bounded-workflow"
        item["entrypoints_in_profile"] = [name for name in item["entrypoints"] if name in enabled]
        matching = [record_id for record_id in item["native_record_ids"] if records_by_id[record_id]["target_version_match"] is True]
        item["matching_native_record_ids"] = matching
        item["target_evidence_status"] = (
            "not-selected" if target is None else
            "documented-cases-only" if matching else "unverified"
        )
        workflows.append(item)
    families = []
    for catalog in list_template_families():
        families.append({
            "id": catalog["id"], "name": catalog["name"],
            "catalog_status": catalog["status"],
            "catalog_analyses": catalog["analyses"],
            "catalog_status_scope": "legacy-roadmap-label",
            "workflow_ids": [item["id"] for item in workflows if catalog["id"] in item["families"]],
            "whole_family_verified": False,
        })
    return {
        "schema_version": ENGINEERING_CAPABILITIES_SCHEMA_VERSION,
        "claim_scope": "bounded-workflows-and-recorded-cases",
        "families": families,
        "workflows": workflows,
        "generic_compiler": {
            "entrypoint": "compile_executable_netlist",
            "entrypoint_in_profile": "compile_executable_netlist" in enabled,
            "supported_options": deepcopy(COMPILER_SUPPORT_MATRIX),
            "fixture_support": {
                "options": ["signal-active"], "target_versions": ["14.3"],
                "native_record_ids": ["ideal-signal-fixture"],
                "native_execution_entrypoint": "run_generated_analog_project",
                "approval_scope": "fixture-netlist-review; analyses via explicit native proposal execute",
            },
            "unsupported_options": "rejected",
            "scope": "Approved pin-level previews only; specialized workflows are separate from this compiler.",
        },
        "component_mappings": mappings,
        "native_records": records,
        "runtime": {"status": "not-probed", "diagnostic_tool": "runtime_status"},
        "model_scope": {
            "host_agent": "May call discoverable MCP tools with supported designs and explicit contracts.",
            "internal_engineering_planner": "RC requirement restatement and consistency checking only.",
            "internal_eda_tools": "Read-only structural inspection; no simulation or mutation.",
        },
        "open_source_scope": ["complex-circuit-research", "basic-multiboard-integration", "native-verification", "simulation", "report-export"],
    }


def engineering_runtime_readiness(diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    """Describe observed prerequisites without inferring COM activation/version.

    runtime_status does not connect to Multisim. Even positive prerequisite
    checks must not become an end-to-end readiness or certification claim.
    """
    fields = ("runtime_compatible", "schematic_templates_ready")
    prerequisites = {
        name: diagnostics.get(name) if isinstance(diagnostics.get(name), bool) else None
        for name in fields
    }
    blocked_by = ["runtime_compatible"] if prerequisites["runtime_compatible"] is False else []
    return {
        "schema_version": 1,
        "status": (
            "blocked-runtime" if blocked_by else
            "incomplete-template-pack" if prerequisites["schematic_templates_ready"] is False else "unverified"
        ),
        "prerequisites": prerequisites,
        "blocked_by": blocked_by,
        "activation_checked": False,
        "detected_version": None,
        "current_machine_native_verified": False,
        "scope": "Prerequisites only. Connect and verify the actual version, required component templates/models and the new circuit at execution.",
    }
