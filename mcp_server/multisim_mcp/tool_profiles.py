"""Stable tool-discovery profiles for MCP clients with limited context."""

from __future__ import annotations

import os
from collections.abc import Mapping


TOOL_PROFILE_ENV = "MULTISIM_MCP_TOOL_PROFILE"
TOOL_PROFILES = ("core", "experiment", "optimization", "full")

ALL_TOOL_NAMES = frozenset(
    {
        "connect",
        "runtime_status",
        "register_experiment_artifacts",
        "list_experiment_artifacts",
        "read_experiment_artifact",
        "export_experiment_artifact",
        "get_experiment_summary",
        "audit_spice_compatibility",
        "compare_experiment_backends",
        "register_sweep_artifacts",
        "measure_experiment",
        "read_virtual_multimeter",
        "analyze_bode_response",
        "analyze_logic_signals",
        "export_formal_experiment_report",
        "component_adapter_catalog",
        "build_behavioral_reference",
        "run_behavioral_reference",
        "verify_experiment_requirements",
        "plan_design_options",
        "select_design_option",
        "prepare_design_specification",
        "prepare_netlist_draft",
        "resolve_component_requirements",
        "approve_component_resolution",
        "compile_executable_netlist",
        "approve_executable_netlist",
        "approve_simulation_plan",
        "build_course_waveform_demo",
        "plan_experiment_sweep",
        "submit_circuit_experiment",
        "get_experiment_job",
        "list_experiment_jobs",
        "cancel_experiment_job",
        "retry_experiment_job",
        "schematic_component_catalog",
        "disconnect",
        "open_circuit",
        "new_circuit",
        "circuit_info",
        "enum_components",
        "enum_inputs",
        "enum_outputs",
        "set_output_request",
        "get_output_data",
        "run_transient",
        "run_dc_operating_point",
        "run_ac_sweep",
        "run_ac_single_frequency",
        "set_input_data_sampled",
        "set_input_data_raw",
        "clear_input_data",
        "stop_simulation",
        "save_circuit",
        "get_circuit_image",
        "report_netlist",
        "report_bom",
        "do_command_line",
        "run_spice_netlist",
        "create_schematic_from_netlist",
        "run_circuit_experiment",
        "run_verified_circuit_experiment",
        "diagnose_design",
        "audit_spice_compatibility",
        "evaluate_design_patch",
        "optimize_design",
        "global_optimize_design",
        "submit_global_optimization",
        "autonomous_correct_design",
        "submit_autonomous_correction",
        "submit_design_optimization",
        "compare_design_variants",
        "run_experiment_sweep",
        "submit_experiment_sweep",
        "generate_report",
        "get_rlc_value",
        "set_rlc_value",
        "decode_ms14",
        "encode_ms14",
    }
)

_COMMON = frozenset({"runtime_status", "connect", "disconnect"})

_CIRCUIT_CORE = frozenset(
    {
        "component_adapter_catalog",
        "build_behavioral_reference",
        "schematic_component_catalog",
        "audit_spice_compatibility",
        "open_circuit",
        "new_circuit",
        "circuit_info",
        "enum_components",
        "enum_inputs",
        "enum_outputs",
        "save_circuit",
        "get_circuit_image",
        "report_netlist",
        "report_bom",
        "diagnose_design",
        "get_rlc_value",
        "set_rlc_value",
        "decode_ms14",
        "encode_ms14",
    }
)

_BASIC_SIMULATION = frozenset(
    {
        "set_output_request",
        "get_output_data",
        "run_transient",
        "run_dc_operating_point",
        "run_ac_sweep",
        "run_ac_single_frequency",
        "stop_simulation",
    }
)

_EXPERIMENT_WORKFLOW = frozenset(
    {
        "register_experiment_artifacts",
        "measure_experiment",
        "read_virtual_multimeter",
        "analyze_bode_response",
        "analyze_logic_signals",
        "export_formal_experiment_report",
        "run_behavioral_reference",
        "verify_experiment_requirements",
        "plan_design_options",
        "select_design_option",
        "prepare_design_specification",
        "prepare_netlist_draft",
        "resolve_component_requirements",
        "approve_component_resolution",
        "compile_executable_netlist",
        "approve_executable_netlist",
        "approve_simulation_plan",
        "build_course_waveform_demo",
        "submit_circuit_experiment",
        "get_experiment_job",
        "list_experiment_jobs",
        "cancel_experiment_job",
        "retry_experiment_job",
        "create_schematic_from_netlist",
        "run_circuit_experiment",
        "run_verified_circuit_experiment",
        "evaluate_design_patch",
        "generate_report",
    }
)

_ARTIFACT_WORKFLOW = frozenset(
    {
        "list_experiment_artifacts",
        "read_experiment_artifact",
        "export_experiment_artifact",
        "get_experiment_summary",
        "compare_experiment_backends",
        "audit_spice_compatibility",
    }
)

_SWEEP_WORKFLOW = frozenset(
    {
        "register_sweep_artifacts",
        "plan_experiment_sweep",
        "run_experiment_sweep",
        "submit_experiment_sweep",
    }
)

_OPTIMIZATION_WORKFLOW = frozenset(
    {
        "component_adapter_catalog",
        "schematic_component_catalog",
        "open_circuit",
        "circuit_info",
        "enum_components",
        "get_circuit_image",
        "report_netlist",
        "report_bom",
        "get_rlc_value",
        "set_rlc_value",
        "register_experiment_artifacts",
        "measure_experiment",
        "analyze_bode_response",
        "verify_experiment_requirements",
        "submit_circuit_experiment",
        "get_experiment_job",
        "list_experiment_jobs",
        "cancel_experiment_job",
        "retry_experiment_job",
        "create_schematic_from_netlist",
        "run_circuit_experiment",
        "run_verified_circuit_experiment",
        "evaluate_design_patch",
        "diagnose_design",
        "plan_design_options",
        "select_design_option",
        "prepare_design_specification",
        "prepare_netlist_draft",
        "resolve_component_requirements",
        "approve_component_resolution",
        "compile_executable_netlist",
        "approve_executable_netlist",
        "approve_simulation_plan",
        "optimize_design",
        "global_optimize_design",
        "submit_global_optimization",
        "autonomous_correct_design",
        "submit_autonomous_correction",
        "submit_design_optimization",
        "compare_design_variants",
        "compare_experiment_backends",
    }
)

PROFILE_TOOL_NAMES: Mapping[str, frozenset[str]] = {
    "core": _COMMON | _CIRCUIT_CORE | _BASIC_SIMULATION,
    "experiment": (
        _COMMON | _CIRCUIT_CORE | _EXPERIMENT_WORKFLOW | _ARTIFACT_WORKFLOW
    ),
    "optimization": (
        _COMMON
        | _BASIC_SIMULATION
        | _OPTIMIZATION_WORKFLOW
        | _SWEEP_WORKFLOW
        | _ARTIFACT_WORKFLOW
    ),
    "full": ALL_TOOL_NAMES,
}


def normalize_tool_profile(value: str | None) -> str:
    """Validate a profile name and preserve a stable default."""
    profile = (value or "full").strip().lower() or "full"
    if profile not in TOOL_PROFILES:
        choices = ", ".join(TOOL_PROFILES)
        raise ValueError(f"unknown tool profile {profile!r}; choose one of: {choices}")
    return profile


def selected_tool_profile(environment: Mapping[str, str] | None = None) -> str:
    """Read the server profile from an explicit environment mapping."""
    source = os.environ if environment is None else environment
    return normalize_tool_profile(source.get(TOOL_PROFILE_ENV))


def tool_enabled(tool_name: str, profile: str) -> bool:
    """Return whether a named tool is discoverable in a validated profile."""
    normalized = normalize_tool_profile(profile)
    if normalized == "full":
        # Future tools remain backward compatible in the default profile even
        # before they are assigned to a smaller task-oriented profile.
        return True
    return tool_name in PROFILE_TOOL_NAMES[normalized]


def tool_profile_status(profile: str) -> dict[str, object]:
    """Return a small machine-readable description for diagnostics."""
    normalized = normalize_tool_profile(profile)
    names = PROFILE_TOOL_NAMES[normalized]
    return {
        "name": normalized,
        "environment_variable": TOOL_PROFILE_ENV,
        "tool_count": len(names),
        "available_profiles": list(TOOL_PROFILES),
    }
