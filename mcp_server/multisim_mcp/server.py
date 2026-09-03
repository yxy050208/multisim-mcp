"""MCP stdio entry point for Multisim."""

from __future__ import annotations

import asyncio
import csv
import functools
import json
import math
import os
import re
import shutil
import threading
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer

from multisim_mcp import __version__
from multisim_mcp.api_contract import build_capabilities
from multisim_mcp.experiment_resources import (
    ExperimentResourceIndex,
    ExperimentResult,
    VerifiedExperimentResult,
    export_artifact as export_registered_artifact,
    experiment_manifest,
    list_artifacts,
    read_artifact_page,
    read_binary_artifact,
    read_text_artifact,
    register_experiment,
    registered_experiment_root,
    summarize_experiment,
)
from multisim_mcp.component_adapters import (
    component_adapter_catalog as adapter_catalog,
    expand_component_adapters,
)
from multisim_mcp.formal_report import export_formal_reports
from multisim_mcp.virtual_instruments import bode_plotter, logic_analyzer, multimeter
from multisim_mcp.design_verification import (
    DesignRequirement,
    ExperimentSpec,
    MeasurementRequest,
    measure_many,
    validate_experiment_spec,
    validate_measurement_requests,
    verify_requirements,
)
from multisim_mcp.course_demo import (
    build_course_demo_manifest,
    build_course_demo_spec,
)
from multisim_mcp.backend_differential import compare_registered_experiments
from multisim_mcp.design_optimization import (
    DesignOptimizationService,
    validate_optimization_spec,
)
from multisim_mcp.global_optimization import (
    GlobalDesignOptimizationService,
    validate_global_optimization_spec,
)
from multisim_mcp.autonomous_correction import (
    AutonomousDesignCorrectionService,
    ModelRepairPlanner,
    validate_autonomous_correction_spec,
)
from multisim_mcp.design_diagnosis import (
    DesignDiagnosisService,
    load_experiment_diagnosis_evidence,
)
from multisim_mcp.design_comparison import DesignVariantComparisonService
from multisim_mcp.design_patch_evaluation import DesignPatchEvaluationService
from multisim_mcp.design_plans import (
    plan_design_options as build_design_plan_options,
    select_design_option as select_planned_design_option,
)
from multisim_mcp.design_specifications import (
    prepare_design_specification as build_design_specification,
)
from multisim_mcp.netlist_drafts import (
    prepare_netlist_draft as build_netlist_draft,
)
from multisim_mcp.component_resolution import (
    resolve_component_requirements as build_component_resolution,
)
from multisim_mcp.component_approvals import (
    approve_component_resolution as build_component_resolution_approval,
)
from multisim_mcp.executable_netlists import (
    MODEL_ROOT_ENV,
    compile_executable_netlist as build_executable_netlist,
)
from multisim_mcp.executable_approvals import (
    approve_executable_netlist as build_executable_netlist_approval,
    validate_executable_netlist_approval as validate_approved_executable_netlist,
)
from multisim_mcp.simulation_approvals import (
    approve_simulation_plan as build_simulation_plan_approval,
    build_experiment_approval_provenance,
    validate_simulation_plan_approval as validate_approved_simulation_plan,
)
from multisim_mcp.eda_backend import (
    BackendExecution,
    SchematicRequest,
    SimulationRequest,
)
from multisim_mcp.eda_core import CircuitDesign
from multisim_mcp.model_provider import ModelProviderRegistry
from multisim_mcp.provider_config import read_provider_config
from multisim_mcp.eda_service import EdaApplicationService
from multisim_mcp.experiment_sweep import plan_experiment_sweep as expand_sweep
from multisim_mcp.experiment_service import (
    ExperimentApplicationService,
    ExperimentRequest,
)
from multisim_mcp.experiment_pipeline import MultisimExperimentPipeline
from multisim_mcp.digital_observation import build_digital_observation_evidence
from multisim_mcp.behavioral_reference import build_behavioral_reference_netlist
from multisim_mcp.com_worker_client import (
    MultisimWorkerProcess,
    WorkerMs14Codec,
    WorkerMultisimClient,
    worker_runtime_diagnostics,
)
from multisim_mcp.multisim_backend import MultisimBackend
from multisim_mcp.ngspice_backend import NgspiceBackend, cancellable_process_runner
from multisim_mcp.backend_selection import (
    EXPERIMENT_BACKEND_ENV,
    selected_experiment_backend,
)
from multisim_mcp.portable_schematic import render_portable_schematic
from multisim_mcp.job_engine import (
    ExperimentJobManager,
    JobSubmission,
    output_lease,
)
from multisim_mcp.safety import (
    UNSAFE_COMMANDS_ENV,
    unsafe_commands_enabled,
    validate_analysis_commands,
    validate_spice_netlist,
)
from multisim_mcp.schematic_builder import (
    COMPONENT_DEFINITIONS,
    build_schematic,
    parse_netlist,
    prepare_simulation_netlist,
    template_search_paths,
)
from multisim_mcp.spice_raw import parse_raw, summarize_columns, write_csv
from multisim_mcp.spice_adapter import circuit_design_from_spice
from multisim_mcp.spice_provenance import (
    audit_spice_compatibility as build_spice_compatibility_audit,
)
from multisim_mcp.sweep_resources import (
    read_sweep_summary,
    read_sweep_text,
    register_sweep,
    sweep_id_for_output_dir,
)
from multisim_mcp.tool_profiles import (
    selected_tool_profile,
    tool_enabled,
    tool_profile_status,
)
from multisim_mcp.workspace_manifest import (
    DIRECTORY_MANIFEST_NAME,
    write_directory_manifest,
)


_WORKER_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="multisim-worker",
)
_TOOL_PROFILE = selected_tool_profile()


def _invoke_tool_on_worker_thread(function: Any, args: tuple, kwargs: dict) -> Any:
    """Keep blocking, stateful worker calls off the MCP event loop."""
    return function(*args, **kwargs)


class MultisimMCPServer(MCPServer):
    """MCPServer that serializes calls to the isolated Multisim worker."""

    def tool(self, *decorator_args: Any, **decorator_kwargs: Any) -> Any:
        com_serialized = bool(decorator_kwargs.pop("com_serialized", True))
        register = super().tool(*decorator_args, **decorator_kwargs)

        def decorator(function: Any) -> Any:
            if not tool_enabled(function.__name__, _TOOL_PROFILE):
                # Keep the original callable available to internal code and unit
                # tests while omitting it from MCP tools/list for this process.
                return function
            if not com_serialized:
                register(function)
                return function

            @functools.wraps(function)
            async def serialized(*args: Any, **kwargs: Any) -> Any:
                loop = asyncio.get_running_loop()
                invoke = functools.partial(
                    _invoke_tool_on_worker_thread,
                    function,
                    args,
                    kwargs,
                )
                return await loop.run_in_executor(_WORKER_EXECUTOR, invoke)

            register(serialized)
            # Direct imports and unit tests retain the original synchronous API.
            return function

        return decorator


mcp = MultisimMCPServer(
    "multisim",
    version=__version__,
    instructions=(
        "Generate editable Multisim circuits, run validated experiments, and "
        "read completed experiment artifacts through multisim:// resources. "
        "Use submit_circuit_experiment for resilient long runs and "
        "run_verified_circuit_experiment for explicit design verdicts. Preview "
        "batch work with plan_experiment_sweep, then submit_experiment_sweep. "
        "Use optimize_design for short bounded explicit/E-series value searches, "
        "or submit_design_optimization for durable candidate-level recovery; "
        "optional stock/cost constraints are supported and no selected patch is "
        "persisted automatically. "
        "Use compare_design_variants to rank complete designs under one verified "
        "experiment contract without modifying any source design. "
        "Use diagnose_design for deterministic read-only topology, requirement, "
        "convergence, and evidence-backed bias/saturation findings. "
        "Use evaluate_design_patch to retest an explicit reversible patch against "
        "the baseline without modifying or automatically adopting the design. "
        "Use global_optimize_design for bounded mixed topology/value Pareto "
        "search, or autonomous_correct_design for model-proposed repairs that "
        "must compile and pass real experiment gates; neither persists a patch. "
        "Use component_adapter_catalog for portable @KIND models; completed "
        "experiments include bilingual HTML/PDF and data-backed instruments."
        " Use build_course_waveform_demo to obtain the bounded five-channel "
        "course-design contract before connecting a native 555/74LS74/LM324 "
        "schematic."
        " Use audit_spice_compatibility before a run when dialect/model provenance "
        "matters; completed experiments retain source/executed-netlist hashes and "
        "solver evidence for compare_experiment_backends."
    ),
)
_MULTISIM_WORKER = MultisimWorkerProcess()
client = WorkerMultisimClient(_MULTISIM_WORKER)
codec = WorkerMs14Codec(_MULTISIM_WORKER)
_JOB_MANAGER: ExperimentJobManager | None = None
_JOB_MANAGER_LOCK = threading.Lock()


def _job_manager() -> ExperimentJobManager:
    """Create the durable scheduler lazily, keeping introspection side-effect free."""
    global _JOB_MANAGER
    with _JOB_MANAGER_LOCK:
        if _JOB_MANAGER is None:
            _JOB_MANAGER = ExperimentJobManager()
        return _JOB_MANAGER


@mcp.resource(
    "multisim://experiments/{experiment_id}/manifest",
    name="experiment_manifest",
    title="Experiment manifest",
    description="Hashes, sizes, and resource links for a completed experiment.",
    mime_type="application/json",
)
def experiment_manifest_resource(experiment_id: str) -> dict[str, object]:
    return experiment_manifest(experiment_id)


@mcp.resource(
    "multisim://experiments/{experiment_id}/report",
    name="experiment_report",
    title="Experiment report",
    description="Markdown report generated for a completed experiment.",
    mime_type="text/markdown",
)
def experiment_report_resource(experiment_id: str) -> str:
    return read_text_artifact(experiment_id, "report")


@mcp.resource(
    "multisim://experiments/{experiment_id}/schematic",
    name="experiment_schematic",
    title="Experiment schematic",
    description="PNG schematic exported from Multisim.",
    mime_type="image/png",
)
def experiment_schematic_resource(experiment_id: str) -> bytes:
    return read_binary_artifact(experiment_id, "schematic")


@mcp.resource(
    "multisim://experiments/{experiment_id}/data",
    name="experiment_data",
    title="Experiment data",
    description="CSV data exported from the Multisim analysis.",
    mime_type="text/csv",
)
def experiment_data_resource(experiment_id: str) -> str:
    return read_text_artifact(experiment_id, "data")


@mcp.resource(
    "multisim://experiments/{experiment_id}/plot",
    name="experiment_plot",
    title="Experiment plot",
    description="SVG waveform plot generated from the experiment data.",
    mime_type="image/svg+xml",
)
def experiment_plot_resource(experiment_id: str) -> str:
    return read_text_artifact(experiment_id, "plot")


@mcp.resource(
    "multisim://experiments/{experiment_id}/netlist",
    name="experiment_netlist",
    title="Experiment netlist",
    description="Validated SPICE netlist used for the experiment.",
    mime_type="text/x-spice",
)
def experiment_netlist_resource(experiment_id: str) -> str:
    return read_text_artifact(experiment_id, "netlist")


@mcp.resource(
    "multisim://experiments/{experiment_id}/circuit",
    name="experiment_circuit",
    title="Editable Multisim circuit",
    description="Binary .ms14 design generated for the experiment.",
    mime_type="application/octet-stream",
)
def experiment_circuit_resource(experiment_id: str) -> bytes:
    return read_binary_artifact(experiment_id, "circuit")


@mcp.resource(
    "multisim://experiments/{experiment_id}/raw",
    name="experiment_raw_data",
    title="Raw simulation data",
    description="Raw analysis output emitted by Multisim.",
    mime_type="application/octet-stream",
)
def experiment_raw_resource(experiment_id: str) -> bytes:
    return read_binary_artifact(experiment_id, "raw")


@mcp.resource(
    "multisim://experiments/{experiment_id}/commands",
    name="experiment_commands",
    title="Experiment commands",
    description="Validated Multisim command sequence used for the experiment.",
    mime_type="text/plain",
)
def experiment_commands_resource(experiment_id: str) -> str:
    return read_text_artifact(experiment_id, "commands")


@mcp.resource(
    "multisim://experiments/{experiment_id}/log",
    name="experiment_log",
    title="Experiment log",
    description="Multisim command-engine log for the experiment.",
    mime_type="text/plain",
)
def experiment_log_resource(experiment_id: str) -> str:
    return read_text_artifact(experiment_id, "log")


@mcp.resource(
    "multisim://experiments/{experiment_id}/spice-compatibility",
    name="experiment_spice_compatibility",
    title="SPICE compatibility and model provenance",
    description=(
        "Machine-readable SPICE dialect, model hash/license, backend risk, and "
        "solver-version evidence."
    ),
    mime_type="application/json",
)
def experiment_spice_compatibility_resource(experiment_id: str) -> str:
    return read_text_artifact(experiment_id, "spice_compatibility")


@mcp.resource(
    "multisim://experiments/{experiment_id}/verification",
    name="experiment_verification",
    title="Design verification results",
    description="Machine-readable PASS, FAIL, and unverified requirement verdicts.",
    mime_type="application/json",
)
def experiment_verification_resource(experiment_id: str) -> str:
    return read_text_artifact(experiment_id, "verification")


@mcp.resource(
    "multisim://experiments/{experiment_id}/formal-html-zh",
    name="experiment_formal_html_zh",
    title="中文正式实验报告 / Formal report in Chinese",
    description="Self-contained Chinese HTML report.",
    mime_type="text/html",
)
def experiment_formal_html_zh_resource(experiment_id: str) -> str:
    return read_text_artifact(experiment_id, "formal_html_zh")


@mcp.resource(
    "multisim://experiments/{experiment_id}/formal-html-en",
    name="experiment_formal_html_en",
    title="English formal experiment report",
    description="Self-contained English HTML report.",
    mime_type="text/html",
)
def experiment_formal_html_en_resource(experiment_id: str) -> str:
    return read_text_artifact(experiment_id, "formal_html_en")


@mcp.resource(
    "multisim://experiments/{experiment_id}/formal-pdf-zh",
    name="experiment_formal_pdf_zh",
    title="中文 PDF 实验报告 / Chinese PDF report",
    description="Portable Chinese PDF report.",
    mime_type="application/pdf",
)
def experiment_formal_pdf_zh_resource(experiment_id: str) -> bytes:
    return read_binary_artifact(experiment_id, "formal_pdf_zh")


@mcp.resource(
    "multisim://experiments/{experiment_id}/formal-pdf-en",
    name="experiment_formal_pdf_en",
    title="English PDF experiment report",
    description="Portable English PDF report.",
    mime_type="application/pdf",
)
def experiment_formal_pdf_en_resource(experiment_id: str) -> bytes:
    return read_binary_artifact(experiment_id, "formal_pdf_en")


@mcp.resource(
    "multisim://experiments/{experiment_id}/reproducibility-manifest",
    name="experiment_reproducibility_manifest",
    title="Reproducibility manifest",
    description="Portable manifest with SHA-256 hashes and reproduction inputs.",
    mime_type="application/json",
)
def experiment_reproducibility_manifest_resource(experiment_id: str) -> str:
    return read_text_artifact(experiment_id, "reproducibility_manifest")


@mcp.resource(
    "multisim://sweeps/{sweep_id}/summary",
    name="experiment_sweep_summary",
    title="Experiment sweep summary",
    description="Sweep plan, variables, measurements, and per-run status.",
    mime_type="application/json",
)
def experiment_sweep_summary_resource(sweep_id: str) -> dict[str, Any]:
    return read_sweep_summary(sweep_id)


@mcp.resource(
    "multisim://sweeps/{sweep_id}/data",
    name="experiment_sweep_data",
    title="Experiment sweep data",
    description="Flat CSV table of sweep variables and measured metrics.",
    mime_type="text/csv",
)
def experiment_sweep_data_resource(sweep_id: str) -> str:
    return read_sweep_text(sweep_id, "data")


@mcp.resource(
    "multisim://jobs/{job_id}",
    name="experiment_job_status",
    title="Experiment job status",
    description="Durable state, progress, diagnostics, and result of an experiment job.",
    mime_type="application/json",
)
def experiment_job_status_resource(job_id: str) -> dict[str, object]:
    return _job_manager().get(job_id)


def _prompt_instructions(zh: str, en: str, language: str) -> str:
    selected = language.strip().lower()
    if selected in {"zh", "zh-cn", "chinese", "中文"}:
        return zh
    if selected in {"en", "english", "英文"}:
        return en
    raise ValueError("language must be zh or en")


@mcp.prompt(
    name="create_circuit_experiment",
    title="创建 Multisim 电路实验 / Create circuit experiment",
)
def create_circuit_experiment_prompt(
    requirements: str,
    output_dir: str,
    language: str = "zh",
) -> str:
    """Turn user requirements into the complete Multisim experiment workflow."""
    return _prompt_instructions(
        (
            "根据以下实验要求设计一个安全、可复现的 SPICE 网表，然后调用 "
            "run_circuit_experiment。先说明器件选择、分析类型和测量节点；成功后读取返回的 "
            "manifest、report、schematic、data 和 plot Resources，并核对理论值与仿真值。\n\n"
            f"实验要求：{requirements}\n输出目录：{output_dir}"
        ),
        (
            "Design a safe, reproducible SPICE netlist for the requirements below, then "
            "call run_circuit_experiment. Explain the component choices, analysis type, "
            "and measurement nodes first. After success, read the returned manifest, "
            "report, schematic, data, and plot resources and compare theory with simulation.\n\n"
            f"Requirements: {requirements}\nOutput directory: {output_dir}"
        ),
        language,
    )


@mcp.prompt(name="debug_circuit", title="调试 Multisim 电路 / Debug circuit")
def debug_circuit_prompt(
    problem: str,
    netlist: str = "",
    language: str = "zh",
) -> str:
    """Guide a reproducible diagnosis of a circuit or simulation failure."""
    return _prompt_instructions(
        (
            "诊断下面的 Multisim 电路问题。先检查网表语法、接地、节点连通性、模型、分析命令和量纲，"
            "再使用最小修改修复。不要启用不安全命令。若需要重新实验，调用 run_circuit_experiment "
            "并用 Resources 比较修复前后的数据。\n\n"
            f"问题：{problem}\n网表：\n{netlist or '(未提供)'}"
        ),
        (
            "Diagnose the Multisim circuit problem below. Check netlist syntax, ground, "
            "connectivity, models, analysis commands, and units before making the smallest "
            "safe correction. Do not enable unsafe commands. If a rerun is needed, call "
            "run_circuit_experiment and compare artifacts through Resources.\n\n"
            f"Problem: {problem}\nNetlist:\n{netlist or '(not provided)'}"
        ),
        language,
    )


@mcp.prompt(
    name="compare_simulation_results",
    title="比较实验结果 / Compare experiments",
)
def compare_simulation_results_prompt(
    first_experiment_id: str,
    second_experiment_id: str,
    language: str = "zh",
) -> str:
    """Compare two registered experiment artifact sets."""
    return _prompt_instructions(
        (
            "读取下面两个实验的 manifest、report、data 和 plot Resources。比较电路、分析设置、"
            "采样点、关键测量值和误差；指出变化原因，并给出表格化结论。\n\n"
            f"实验 A：{first_experiment_id}\n实验 B：{second_experiment_id}"
        ),
        (
            "Read the manifest, report, data, and plot resources for both experiments. "
            "Compare circuits, analysis settings, sample counts, key measurements, and "
            "errors; explain the causes and finish with a compact table.\n\n"
            f"Experiment A: {first_experiment_id}\nExperiment B: {second_experiment_id}"
        ),
        language,
    )


@mcp.prompt(name="write_lab_report", title="撰写实验报告 / Write lab report")
def write_lab_report_prompt(
    experiment_id: str,
    requirements: str = "",
    language: str = "zh",
) -> str:
    """Create a polished report from registered experiment resources."""
    return _prompt_instructions(
        (
            "读取该实验的 manifest、report、schematic、data、plot 和 netlist Resources，"
            "在不编造数据的前提下撰写中文实验报告。报告应包含目的、原理、器件、步骤、结果、"
            "理论与仿真误差、异常、结论和复现信息。\n\n"
            f"实验 ID：{experiment_id}\n补充要求：{requirements or '(无)'}"
        ),
        (
            "Read the experiment manifest, report, schematic, data, plot, and netlist "
            "resources. Write a polished lab report without inventing data. Include the "
            "objective, theory, components, procedure, results, theory-versus-simulation "
            "error, anomalies, conclusion, and reproduction details.\n\n"
            f"Experiment ID: {experiment_id}\nAdditional requirements: {requirements or '(none)'}"
        ),
        language,
    )


@mcp.prompt(
    name="verify_design_requirements",
    title="验证设计指标 / Verify design requirements",
)
def verify_design_requirements_prompt(
    requirements: str,
    experiment_id: str,
    language: str = "zh",
) -> str:
    """Check measured experiment data against explicit design requirements."""
    return _prompt_instructions(
        (
            "读取实验的 manifest、data、plot 和 report Resources，把每一项设计要求转成可计算的"
            "判据。逐项列出目标、测量方法、实测值、容差和 PASS/FAIL；无法从数据证明的项目必须标为"
            "未验证，不能猜测。\n\n"
            f"设计要求：{requirements}\n实验 ID：{experiment_id}"
        ),
        (
            "Read the experiment manifest, data, plot, and report resources. Convert every "
            "requirement into a measurable criterion and list its target, method, measured "
            "value, tolerance, and PASS/FAIL result. Mark anything unsupported by the data "
            "as unverified instead of guessing.\n\n"
            f"Requirements: {requirements}\nExperiment ID: {experiment_id}"
        ),
        language,
    )


@mcp.tool()
def connect() -> dict:
    """Connect to the local Multisim Automation API."""
    return client.connect()


@mcp.tool()
def runtime_status() -> dict:
    """Check local EDA runtime compatibility without starting Multisim."""
    result = worker_runtime_diagnostics(_MULTISIM_WORKER)
    paths = template_search_paths()
    required = ("minimal.ms14.xml", "wire.xml", "r_element.xml")
    missing = [
        name for name in required if not any((path / name).is_file() for path in paths)
    ]
    result["schematic_templates_ready"] = not missing
    result["missing_schematic_templates"] = missing
    result["tool_profile"] = tool_profile_status(_TOOL_PROFILE)
    result["api_contract"] = build_capabilities(
        server_version=__version__,
        tool_profile=result["tool_profile"],
    )
    eda_service = _eda_application_service()
    result["eda_backends"] = [
        capabilities.to_dict()
        for capabilities in eda_service.discover_backends()
    ]
    result["ngspice_runtime"] = NgspiceBackend().probe_runtime()
    result["experiment_backend"] = {
        "selected": selected_experiment_backend(),
        "environment_variable": EXPERIMENT_BACKEND_ENV,
        "default": "multisim",
    }
    if missing:
        result["template_setup_hint"] = (
            "Run tools/bootstrap_local_component_pack.py and set "
            "MULTISIM_MCP_TEMPLATE_DIR."
        )
    return result


@mcp.tool()
def register_experiment_artifacts(output_dir: str) -> ExperimentResourceIndex:
    """Register an existing complete experiment directory as MCP Resources.

    Use this after a server restart when the experiment was generated earlier.
    Only the fixed high-level experiment artifact set is exposed.
    """
    return register_experiment(output_dir)


@mcp.tool(com_serialized=False)
def list_experiment_artifacts(experiment_id: str) -> dict[str, Any]:
    """List artifact metadata, hashes, MIME types, and safe access capabilities."""
    return list_artifacts(experiment_id)


@mcp.tool(com_serialized=False)
def read_experiment_artifact(
    experiment_id: str,
    name: str,
    offset: int = 0,
    max_chars: int = 20_000,
) -> dict[str, Any]:
    """Read one bounded page from an allowlisted text experiment artifact."""
    return read_artifact_page(experiment_id, name, offset, max_chars)


@mcp.tool(com_serialized=False)
def export_experiment_artifact(
    experiment_id: str,
    name: str,
    destination_subdir: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Export an artifact beneath MULTISIM_MCP_ARTIFACT_EXPORT_DIR."""
    return export_registered_artifact(
        experiment_id, name, destination_subdir, overwrite
    )


@mcp.tool(com_serialized=False)
def get_experiment_summary(experiment_id: str) -> dict[str, Any]:
    """Return a compact report, verification, and artifact summary for agents."""
    return summarize_experiment(experiment_id)


@mcp.tool(com_serialized=False)
def audit_spice_compatibility(
    netlist: str,
    backend: str = "multisim",
    model_references: list[dict[str, Any]] | None = None,
    declared_dialect: str | None = None,
) -> dict[str, Any]:
    """Audit SPICE dialect, model hashes/licenses, and static backend risks."""
    return build_spice_compatibility_audit(
        netlist,
        backend_id=backend,
        model_references=model_references,
        declared_dialect=declared_dialect,
    )


@mcp.tool(com_serialized=False)
def compare_experiment_backends(
    reference_experiment_id: str,
    candidate_experiment_id: str,
    signals: list[str] | None = None,
    absolute_tolerance: float = 1e-6,
    relative_tolerance_percent: float = 1.0,
    max_points: int = 2000,
) -> dict[str, Any]:
    """Numerically compare common signals from two registered EDA experiments."""
    return compare_registered_experiments(
        reference_experiment_id,
        candidate_experiment_id,
        signals=signals,
        absolute_tolerance=absolute_tolerance,
        relative_tolerance_percent=relative_tolerance_percent,
        max_points=max_points,
    )


@mcp.tool(com_serialized=False)
def register_sweep_artifacts(output_dir: str) -> dict[str, Any]:
    """Register a completed sweep directory and return opaque MCP Resources."""
    return register_sweep(output_dir)


@mcp.tool(com_serialized=False)
def measure_experiment(
    experiment_id: str, measurements: list[MeasurementRequest]
) -> dict[str, Any]:
    """Compute explicit metrics from a registered experiment's raw data."""
    normalized = validate_measurement_requests(measurements)
    root = registered_experiment_root(experiment_id)
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "measurements": measure_many(parse_raw(str(root / "result.raw")), normalized),
    }


@mcp.tool(com_serialized=False)
def read_virtual_multimeter(
    experiment_id: str, signal: str, reference_signal: str | None = None
) -> dict[str, Any]:
    """Read DC, true RMS, AC RMS and range from experiment data."""
    root = registered_experiment_root(experiment_id)
    return {
        **multimeter(parse_raw(str(root / "result.raw")), signal, reference_signal),
        "experiment_id": experiment_id,
    }


@mcp.tool(com_serialized=False)
def analyze_bode_response(
    experiment_id: str, input_signal: str, output_signal: str,
    frequency_signal: str | None = None, max_points: int = 2000,
) -> dict[str, Any]:
    """Use the Bode Plotter adapter on an AC-sweep experiment."""
    root = registered_experiment_root(experiment_id)
    return {
        **bode_plotter(
            parse_raw(str(root / "result.raw")),
            input_signal,
            output_signal,
            frequency_signal,
            max_points,
        ),
        "experiment_id": experiment_id,
    }


@mcp.tool(com_serialized=False)
def analyze_logic_signals(
    experiment_id: str, signals: list[str], threshold: float = 2.5,
    time_signal: str | None = None, max_events: int = 10_000,
) -> dict[str, Any]:
    """Use the Logic Analyzer adapter to digitize traces and list edges."""
    root = registered_experiment_root(experiment_id)
    return {
        **logic_analyzer(
            parse_raw(str(root / "result.raw")),
            signals,
            threshold,
            time_signal,
            max_events,
        ),
        "experiment_id": experiment_id,
    }


@mcp.tool(com_serialized=False)
def export_formal_experiment_report(experiment_id: str) -> dict[str, Any]:
    """Export bilingual HTML/PDF reports and a reproducibility manifest."""
    root = registered_experiment_root(experiment_id)
    with output_lease(str(root), f"formal-report-{uuid.uuid4().hex}"):
        return export_formal_reports(root, experiment_id)


@mcp.tool(com_serialized=False)
def component_adapter_catalog() -> dict[str, Any]:
    """List portable built-in and local declarative component adapters."""
    return adapter_catalog()


@mcp.tool(com_serialized=False)
def build_behavioral_reference(netlist: str) -> dict[str, Any]:
    """Convert supported native DFF carriers into an explicit ngspice reference.

    The returned netlist is not run automatically and never replaces the
    source design. It maps native ``D, ~PR, ~CLR, CLK, Q, ~Q, GND, VCC`` pins
    to the portable ``@DFF`` adapter, inserts explicit control inverters for
    active-low ``~PR/~CLR``, and records the behavioral-only claim.
    Pass the returned ``netlist`` explicitly to ``run_spice_netlist`` with
    ``backend='ngspice'`` when a reference run is desired.
    """
    return build_behavioral_reference_netlist(netlist)


@mcp.tool(com_serialized=False)
def run_behavioral_reference(
    netlist: str,
    commands: str,
    output_dir: str | None = None,
    timeout: float = 120.0,
    max_points: int = 2000,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build and run an explicit ngspice behavioral reference in one call.

    This workflow is intentionally opt-in: it only accepts a netlist containing
    a supported native ``DFF8``/``7474N``-family carrier, converts that carrier
    to the portable ``@DFF`` reference, and then runs the converted netlist
    through the explicitly selected ngspice backend. The source design is not
    modified, and the result must not be interpreted as native Multisim
    74LS74 timing or electrical-equivalence evidence.
    """
    conversion = build_behavioral_reference_netlist(netlist)
    if int(conversion.get("converted_count", 0)) <= 0:
        raise ValueError(
            "netlist does not contain a supported native DFF carrier; "
            "use build_behavioral_reference for conversion metadata first"
        )

    reference_netlist = conversion["netlist"]
    simulation = run_spice_netlist(
        reference_netlist,
        commands,
        output_dir=output_dir,
        timeout=timeout,
        max_points=max_points,
        unsafe_commands=False,
        overwrite=overwrite,
        backend="ngspice",
    )
    behavioral_metadata = {
        key: value for key, value in conversion.items() if key != "netlist"
    }
    return {
        **simulation,
        "behavioral_reference": behavioral_metadata,
        "reference_netlist": reference_netlist,
    }


@mcp.tool(com_serialized=False)
def verify_experiment_requirements(
    experiment_id: str,
    requirements: list[DesignRequirement],
    theoretical_values: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Evaluate explicit design requirements; unsupported evidence stays unverified."""
    root = registered_experiment_root(experiment_id)
    result = verify_requirements(
        parse_raw(str(root / "result.raw")), requirements, theoretical_values
    )
    return {**result, "experiment_id": experiment_id}


@mcp.tool(com_serialized=False)
def plan_experiment_sweep(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate and preview parameter, tolerance, temperature, or Monte Carlo runs."""
    return expand_sweep(spec)


@mcp.tool(com_serialized=False)
def plan_design_options(
    requirements: str,
    constraints: dict[str, Any] | None = None,
    objectives: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    max_options: int = 3,
) -> dict[str, Any]:
    """Return comparable technical方案 without generating or executing a circuit.

    The result is a bounded planning-only ``DesignPlan``.  It contains a
    deterministic recommendation, alternative implementation paths, trade-offs,
    and explicit assumptions.  Netlists, schematics, files, and simulations are
    intentionally deferred until a caller selects an option in a later step.
    """
    return build_design_plan_options(
        requirements,
        constraints=constraints,
        objectives=objectives,
        context=context,
        max_options=max_options,
    )


@mcp.tool(com_serialized=False)
def select_design_option(
    plan: dict[str, Any],
    option_id: str,
) -> dict[str, Any]:
    """Lock one planning option without generating or executing a circuit.

    The input plan digest is checked before selection.  The returned envelope
    binds the source and selected digests so a later generator can require an
    explicit, untampered handoff.
    """
    return select_planned_design_option(plan, option_id)


@mcp.tool(com_serialized=False)
def prepare_design_specification(
    plan: dict[str, Any],
    parameter_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prepare a reviewable electrical specification from a selected plan.

    This read-only step lists required electrical parameters, planned modules,
    analyses, and validation gates.  It requires a valid selection digest and
    never creates a CircuitDesign, netlist, schematic, file, or simulation.
    """
    return build_design_specification(plan, parameter_values)


@mcp.tool(com_serialized=False)
def prepare_netlist_draft(
    plan: dict[str, Any],
    specification: dict[str, Any],
    approval: dict[str, Any],
) -> dict[str, Any]:
    """Prepare a logical netlist draft after explicit specification approval.

    The result contains block-level nets, connections, unresolved component
    requirements, and derived constraints.  It is not executable SPICE and
    never creates a CircuitDesign, schematic, file, or simulation.
    """
    return build_netlist_draft(plan, specification, approval)


@mcp.tool(com_serialized=False)
def resolve_component_requirements(
    draft: dict[str, Any],
    selections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve logical roles into reviewable component candidates and rating gates.

    This remains a read-only planning step.  It does not choose a silent part
    number, create a CircuitDesign, generate SPICE, write a file, or start a
    simulation.  A later stage must provide model provenance and explicit
    human approval before compiling an executable netlist.
    """
    return build_component_resolution(draft, selections)


@mcp.tool(com_serialized=False)
def approve_component_resolution(
    draft: dict[str, Any],
    resolution: dict[str, Any],
    approval: dict[str, Any],
) -> dict[str, Any]:
    """Record explicit human approval for a complete component resolution.

    This is still a review artifact.  It does not generate SPICE, create a
    CircuitDesign, write a file, render a schematic, or start a simulation;
    those actions require a later compiler and separate approval gates.
    """
    return build_component_resolution_approval(draft, resolution, approval)


@mcp.tool(com_serialized=False)
def compile_executable_netlist(
    draft: dict[str, Any],
    component_approval: dict[str, Any],
) -> dict[str, Any]:
    """Compile an approved logical draft into a bounded pin-level preview.

    Only options in the compiler support matrix are accepted.  External model
    bytes, when present, are re-hashed beneath ``MULTISIM_MCP_MODEL_ROOT``.
    The result stays in memory and does not write files, create a schematic,
    start Multisim, or run a simulation.
    """
    configured_root = os.environ.get(MODEL_ROOT_ENV) or None
    return build_executable_netlist(
        draft,
        component_approval,
        model_root=configured_root,
    )


@mcp.tool(com_serialized=False)
def approve_executable_netlist(
    executable_netlist: dict[str, Any],
    approval: dict[str, Any],
) -> dict[str, Any]:
    """Record explicit human approval for one immutable netlist preview.

    The approval binds the compiled CircuitDesign and SPICE digest.  It does
    not write a schematic, create a project file, add stimuli, or start a
    simulation; those remain separate downstream gates.
    """
    return build_executable_netlist_approval(executable_netlist, approval)


@mcp.tool(com_serialized=False)
def approve_simulation_plan(
    executable_netlist: dict[str, Any],
    netlist_approval: dict[str, Any],
    experiment_spec: dict[str, Any],
    approval: dict[str, Any],
) -> dict[str, Any]:
    """Record explicit approval for one safe simulation plan.

    The artifact binds the approved executable-netlist preview to the exact
    analysis commands, measurement requirements, and theoretical values in an
    ``ExperimentSpec``. It does not create a schematic, write files, or start
    a simulation; ``run_verified_circuit_experiment`` must validate it again.
    """
    return build_simulation_plan_approval(
        executable_netlist,
        netlist_approval,
        experiment_spec,
        approval,
    )


@mcp.tool(com_serialized=False)
def build_course_waveform_demo(
    netlist: str = "",
    commands: str = "",
    component_evidence: dict[str, Any] | None = None,
    experiment_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the bilingual five-waveform course-design contract.

    With no arguments this returns the safe behavioral reference model.  A
    caller may provide a native Multisim-compatible netlist and analysis
    commands to keep the same explicit requirement gates while changing the
    implementation.  This tool only builds/validates the contract; use
    ``run_verified_circuit_experiment`` for measured evidence.
    """

    chosen_netlist = netlist if netlist.strip() else None
    chosen_commands = commands if commands.strip() else None
    spec = build_course_demo_spec(chosen_netlist, chosen_commands)
    manifest = build_course_demo_manifest(
        netlist_kind=("native-multisim" if chosen_netlist else "behavioral-reference"),
        backend_note=(
            "Caller-supplied netlist; component-level claim requires native "
            "Multisim evidence."
            if chosen_netlist
            else "Behavioral reference only; requires a real local simulator for measured evidence."
        ),
        component_evidence=component_evidence,
        experiment_evidence=experiment_evidence,
    )
    return {
        "schema_version": 1,
        "demo_id": manifest["demo_id"],
        "manifest": manifest,
        "spec": spec,
    }


@mcp.tool(com_serialized=False)
def submit_circuit_experiment(
    netlist: str,
    commands: str,
    output_dir: str,
    title: str = "Multisim experiment",
    timeout: float = 120.0,
    max_points: int = 2000,
    overwrite: bool = False,
    job_timeout: float = 600.0,
    heartbeat_timeout: float = 180.0,
    requirements: list[DesignRequirement] | None = None,
    theoretical_values: dict[str, float] | None = None,
    executable_netlist: dict[str, Any] | None = None,
    netlist_approval: dict[str, Any] | None = None,
    simulation_plan_approval: dict[str, Any] | None = None,
) -> JobSubmission:
    """Queue a durable, cancellable experiment in an isolated worker process.

    Prefer this tool for long experiments. Poll ``multisim://jobs/{job_id}`` or
    call ``get_experiment_job``. Completed jobs return the same experiment
    result and resource handles as ``run_circuit_experiment``. When approval
    artifacts are supplied, all three are persisted with the job and verified
    again inside the isolated worker before execution.
    """
    if not output_dir.strip():
        raise ValueError("output_dir must not be empty")
    output_path = Path(output_dir).expanduser().resolve()
    if output_path == Path(output_path.anchor):
        raise ValueError("output_dir must not be a filesystem root")
    validate_spice_netlist(netlist)
    accepted = validate_analysis_commands(commands)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 3600:
        raise ValueError("timeout must be between 0 and 3600 seconds")
    if max_points <= 0 or max_points > 100_000:
        raise ValueError("max_points must be between 1 and 100000")
    if not math.isfinite(job_timeout) or job_timeout < 1 or job_timeout > 7200:
        raise ValueError("job_timeout must be between 1 and 7200 seconds")
    if (
        not math.isfinite(heartbeat_timeout)
        or heartbeat_timeout < 10
        or heartbeat_timeout > 900
    ):
        raise ValueError("heartbeat_timeout must be between 10 and 900 seconds")
    if job_timeout <= timeout:
        raise ValueError("job_timeout must be greater than the simulation timeout")
    verification: dict[str, Any] | None = None
    if requirements is not None:
        verification = validate_experiment_spec(
            {
                "schema_version": 1,
                "title": title,
                "netlist": netlist,
                "commands": "\n".join(accepted),
                "requirements": requirements,
                "theoretical_values": theoretical_values or {},
            }
        )
    elif theoretical_values:
        raise ValueError("theoretical_values requires requirements")
    approved_handoff = (
        executable_netlist is not None
        or netlist_approval is not None
        or simulation_plan_approval is not None
    )
    if approved_handoff:
        if (
            executable_netlist is None
            or netlist_approval is None
            or simulation_plan_approval is None
        ):
            raise ValueError(
                "executable_netlist, netlist_approval, and "
                "simulation_plan_approval must be provided together"
            )
        if verification is None:
            raise ValueError(
                "approved simulation-plan submission requires requirements"
            )
        validate_approved_simulation_plan(
            executable_netlist,
            netlist_approval,
            verification,
            simulation_plan_approval,
        )
    return _job_manager().submit(
        {
            "job_kind": "experiment",
            "netlist": netlist,
            "commands": "\n".join(accepted),
            "output_dir": str(output_path),
            "title": title,
            "timeout": timeout,
            "max_points": max_points,
            "overwrite": overwrite,
            "job_timeout": job_timeout,
            "heartbeat_timeout": heartbeat_timeout,
            **(
                {
                    "requirements": verification["requirements"],
                    "theoretical_values": verification["theoretical_values"],
                }
                if verification is not None
                else {}
            ),
            **(
                {
                    "executable_netlist": executable_netlist,
                    "netlist_approval": netlist_approval,
                    "simulation_plan_approval": simulation_plan_approval,
                }
                if approved_handoff
                else {}
            ),
        }
    )


@mcp.tool(com_serialized=False)
def get_experiment_job(job_id: str) -> dict[str, Any]:
    """Return durable progress, failure diagnostics, or the completed result."""
    return _job_manager().get(job_id)


@mcp.tool(com_serialized=False)
def list_experiment_jobs(state: str = "", limit: int = 50) -> dict[str, Any]:
    """List recent durable jobs without returning their potentially large results."""
    return _job_manager().list(state, limit)


@mcp.tool(com_serialized=False)
def cancel_experiment_job(job_id: str) -> dict[str, Any]:
    """Cancel a queued job or safely stop its isolated worker process."""
    return _job_manager().cancel(job_id)


@mcp.tool(com_serialized=False)
def retry_experiment_job(job_id: str) -> JobSubmission:
    """Queue a fresh attempt using a failed, cancelled, or timed-out job spec."""
    return _job_manager().retry(job_id)


@mcp.tool()
def schematic_component_catalog() -> dict:
    """List native component families available to the schematic generator."""
    experimental_carriers = {
        "E", "F", "G", "H", "BV", "BI", "T",
        "XSUB2", "XSUB3", "XSUB4", "XSUB5", "XSUBN",
        "S", "JN", "JP", "ZN", "ZP",
        "W", "K", "O", "U", "DNOT4", "DAND5", "DOR5",
        "DNAND5", "DNOR5", "DXOR5", "DXNOR5", "DJK7",
    }
    search_paths = template_search_paths()
    templates_ready = any(
        (path / "minimal.ms14.xml").is_file() for path in search_paths
    )
    return {
        "template_search_paths": [str(path) for path in search_paths],
        "schematic_templates_ready": templates_ready,
        "template_setup_hint": (
            None
            if templates_ready
            else "Generate a local pack and set MULTISIM_MCP_TEMPLATE_DIR."
        ),
        "native": [
            {
                "kind": definition.kind,
                "ports": (
                    "6-16"
                    if definition.kind == "XSUBN"
                    else len(definition.port_templates)
                ),
                "value_unit": definition.value_unit,
                "ready": any(
                    all(
                        (path / filename).is_file()
                        for filename in (
                            definition.element_template,
                            definition.symbol_template,
                            *definition.port_templates,
                        )
                    )
                    for path in search_paths
                ),
                "maturity": (
                    "experimental-carrier"
                    if definition.kind in experimental_carriers
                    else (
                        "local-native-verified"
                        if definition.kind in {"TIMER8", "DFF8"}
                        else "native-verified"
                    )
                ),
            }
            for definition in COMPONENT_DEFINITIONS.values()
        ],
        "experimental": [
            "generated voltage probes",
            "E/F/G/H controlled-source carrier symbols",
            "generic carrier artwork for K/O/U/X and derived logic gates",
        ],
        "portable_adapters": adapter_catalog()["adapters"],
        "planned_families": [
            "dedicated symbols for generic carriers",
            "generic subcircuits with more than sixteen terminals",
        ],
    }


@mcp.tool()
def disconnect() -> dict:
    """Disconnect from Multisim and release the open circuit."""
    return client.disconnect()


@mcp.tool()
def open_circuit(path: str) -> dict:
    """Open a Multisim design file and return circuit info."""
    return client.open_circuit(path)


@mcp.tool()
def new_circuit() -> dict:
    """Create a new empty Multisim design."""
    return client.new_circuit()


@mcp.tool()
def circuit_info() -> dict:
    """Return name, file, simulation state, and last error for the open circuit."""
    return client.circuit_info()


@mcp.tool()
def enum_components(component_type: int = 0) -> dict:
    """List component reference designators. 0 returns all."""
    return {"components": client.enum_components(component_type)}


@mcp.tool()
def enum_inputs(input_type: int = 0) -> dict:
    """List simulation inputs. 0 returns all."""
    return {"inputs": client.enum_inputs(input_type)}


@mcp.tool()
def enum_outputs(output_type: int = 0) -> dict:
    """List simulation outputs. 0 returns all."""
    return {"outputs": client.enum_outputs(output_type)}


@mcp.tool()
def set_output_request(
    output_name: str,
    method: int = 0,
    sample_rate: float = 1_000_000.0,
    num_samples: int = 1_000,
    repeat_flag: bool = False,
) -> dict:
    """Configure an output request before RunSimulation."""
    return client.set_output_request(
        output_name, method, sample_rate, num_samples, repeat_flag
    )


@mcp.tool()
def get_output_data(output_name: str, max_points: int = 2000) -> dict:
    """Fetch data for an output that is ready; shape depends on analysis type."""
    return client.get_output_data(output_name, max_points)


@mcp.tool()
def run_transient(
    output_name: str,
    sample_rate: float = 1_000_000.0,
    num_samples: int = 1_000,
    duration: float = 0.001,
    repeat_flag: bool = False,
    timeout: float = 30.0,
    max_points: int = 2000,
) -> dict:
    """Run a transient simulation and return downsampled time/real data."""
    return client.run_transient(
        output_name,
        sample_rate,
        num_samples,
        duration,
        repeat_flag,
        timeout,
        max_points,
    )


@mcp.tool()
def run_dc_operating_point(
    output_names: list[str], timeout: float = 30.0, max_points: int = 200
) -> dict:
    """Run DC operating point analysis for the given outputs."""
    return client.run_dc_operating_point(output_names, timeout, max_points)


@mcp.tool()
def run_ac_sweep(
    output_names: list[str],
    sweep_type: int = 0,
    num_points: int = 10,
    start_frequency: float = 100.0,
    stop_frequency: float = 1_000_000.0,
    timeout: float = 60.0,
    max_points: int = 2000,
) -> dict:
    """Run AC sweep and return frequency/real/imag rows."""
    return client.run_ac_sweep(
        output_names,
        sweep_type,
        num_points,
        start_frequency,
        stop_frequency,
        timeout,
        max_points,
    )


@mcp.tool()
def run_ac_single_frequency(
    output_names: list[str],
    frequency: float = 1000.0,
    timeout: float = 30.0,
    max_points: int = 200,
) -> dict:
    """Run AC analysis at a single frequency."""
    return client.run_ac_single_frequency(output_names, frequency, timeout, max_points)


@mcp.tool()
def set_input_data_sampled(
    input_name: str,
    sample_rate: float,
    values: list[float],
    repeat_flag: bool = False,
) -> dict:
    """Inject a sampled waveform into a simulation input."""
    return client.set_input_data_sampled(input_name, sample_rate, values, repeat_flag)


@mcp.tool()
def set_input_data_raw(
    input_name: str,
    times: list[float],
    values: list[float],
    repeat_flag: bool = False,
) -> dict:
    """Inject a raw (time, value) waveform into a simulation input."""
    return client.set_input_data_raw(input_name, times, values, repeat_flag)


@mcp.tool()
def clear_input_data(input_name: str) -> dict:
    """Clear injected data from a simulation input."""
    return client.clear_input_data(input_name)


@mcp.tool()
def stop_simulation() -> dict:
    """Stop the currently running simulation."""
    return client.stop_simulation()


@mcp.tool()
def save_circuit(path: str | None = None) -> dict:
    """Save the circuit, optionally to a new path."""
    return {"path": client.save_circuit(path)}


@mcp.tool()
def get_circuit_image(path: str, image_format: int = 2) -> dict:
    """Export the circuit schematic as an image."""
    return {"path": client.get_circuit_image(path, image_format)}


@mcp.tool()
def report_netlist(path: str, probes_flag: bool = False, fmt: int = 0) -> dict:
    """Export the SPICE netlist to a text file."""
    return {"path": client.report_netlist(path, probes_flag, fmt)}


@mcp.tool()
def report_bom(path: str, real_flag: bool = False, fmt: int = 0) -> dict:
    """Export the bill of materials to a text file."""
    return {"path": client.report_bom(path, real_flag, fmt)}


@mcp.tool()
def do_command_line(command_file: str, log_file: str) -> dict:
    """Run an unrestricted Multisim command file when explicitly enabled.

    This advanced tool is disabled by default because command files can access
    the filesystem and may expose engine-specific command execution features.
    """
    if not unsafe_commands_enabled():
        raise RuntimeError(
            "do_command_line is disabled by default. Set "
            f"{UNSAFE_COMMANDS_ENV}=1 only in a trusted local environment."
        )
    return {"path": client.do_command_line(command_file, log_file)}


def _copy_run_artifacts(work_dir: str, output_dir: str, overwrite: bool) -> list[str]:
    os.makedirs(output_dir, exist_ok=True)
    sources = [
        (name, os.path.join(work_dir, name))
        for name in ("data.csv", "result.raw", "run.log", "run.txt", "circuit.cir")
        if os.path.exists(os.path.join(work_dir, name))
    ]
    if not overwrite:
        collisions = [
            os.path.join(output_dir, name)
            for name, _ in sources
            if os.path.exists(os.path.join(output_dir, name))
        ]
        if collisions:
            raise FileExistsError(
                "Refusing to overwrite existing experiment artifacts: "
                + ", ".join(collisions)
            )
    copied: list[str] = []
    for name, source in sources:
        destination = os.path.join(output_dir, name)
        shutil.copy2(source, destination)
        copied.append(destination)
    return copied


def _run_spice_netlist_impl(
    netlist: str,
    commands: str,
    output_dir: str | None = None,
    timeout: float = 120.0,
    max_points: int = 2000,
    unsafe_commands: bool = False,
    overwrite: bool = False,
    cancel_requested: Callable[[], bool] | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> dict:
    if not netlist.strip():
        raise ValueError("netlist must not be empty")
    if len(netlist.encode("utf-8")) > 2_000_000:
        raise ValueError("netlist exceeds the 2 MB safety limit")
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 3600:
        raise ValueError("timeout must be between 0 and 3600 seconds")
    if max_points <= 0 or max_points > 100_000:
        raise ValueError("max_points must be between 1 and 100000")

    if unsafe_commands:
        if not unsafe_commands_enabled():
            raise RuntimeError(
                f"unsafe_commands requires the explicit {UNSAFE_COMMANDS_ENV}=1 opt-in"
            )
        accepted_commands = [
            line.strip() for line in commands.splitlines() if line.strip()
        ]
    else:
        validate_spice_netlist(netlist)
        accepted_commands = validate_analysis_commands(commands)
    simulation_netlist = prepare_simulation_netlist(netlist)
    validate_spice_netlist(simulation_netlist)

    run_id = f"msre_{uuid.uuid4().hex[:16]}"
    work_root = os.environ.get("MULTISIM_MCP_WORKDIR") or r"C:\msre_exp"
    if " " in work_root:
        raise ValueError(
            "MULTISIM_MCP_WORKDIR must not contain spaces because Multisim's "
            "command engine rejects spaced command-file paths"
        )
    work_dir = os.path.join(work_root, run_id)
    os.makedirs(work_dir, exist_ok=True)
    netlist_path = os.path.join(work_dir, "circuit.cir")
    command_path = os.path.join(work_dir, "run.txt")
    log_path = os.path.join(work_dir, "run.log")
    raw_path = os.path.join(work_dir, "result.raw")

    with open(netlist_path, "w", encoding="utf-8") as fh:
        fh.write(simulation_netlist)

    kept = [f"source {netlist_path}", *accepted_commands, f"write {raw_path}"]
    with open(command_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(kept) + "\n")

    try:
        client.circuit
    except RuntimeError as exc:
        if str(exc) != "No circuit is open":
            raise
        # Multisim exposes DoCommandLine on a circuit object even though the
        # command file immediately sources the authoritative SPICE netlist.
        # A blank document is therefore required for standalone simulations
        # and sweep workers that did not first build/open a schematic.
        client.new_circuit()
    result = client.run_command_file(
        command_path,
        log_path,
        timeout,
        cancel_requested=cancel_requested,
        heartbeat=heartbeat,
    )
    if result.get("cancelled"):
        raise InterruptedError("Experiment cancellation requested")
    parsed = None
    if os.path.exists(raw_path) and os.path.getsize(raw_path) > 0:
        parsed = parse_raw(raw_path)

    summary = {
        "run_id": run_id,
        "work_dir": work_dir,
        "netlist": netlist_path,
        "commands": command_path,
        "log": log_path,
        "raw": raw_path if parsed else None,
        "state": result.get("state"),
        "timed_out": result.get("timed_out", False),
        "last_error": result.get("last_error", ""),
        "log_tail": (result.get("log") or "")[-2000:],
        "safe_commands": not unsafe_commands,
    }
    if parsed:
        csv_path = write_csv(os.path.join(work_dir, "data.csv"), parsed)
        summary["csv"] = csv_path
        summary["plotname"] = parsed["header"].get("plotname", "")
        summary["columns"] = parsed["columns"]
        summary["n_points"] = parsed["n_points"]
        summary["measurements"] = summarize_columns(parsed)
        summary["digital_observation"] = build_digital_observation_evidence(
            netlist,
            parsed["columns"],
            backend_id="multisim",
        )
        step = max(1, math.ceil(parsed["n_points"] / max(1, int(max_points))))
        summary["rows"] = parsed["rows"][::step][:max_points]

    summary["success"] = (
        parsed is not None
        and not summary["timed_out"]
        and summary.get("state") == 0
    )
    if output_dir:
        output_dir = os.path.abspath(output_dir)
        summary["artifacts"] = _copy_run_artifacts(
            work_dir, output_dir, overwrite=overwrite
        )
        summary["output_dir"] = output_dir
    return summary


@mcp.tool()
def run_spice_netlist(
    netlist: str,
    commands: str,
    output_dir: str | None = None,
    timeout: float = 120.0,
    max_points: int = 2000,
    unsafe_commands: bool = False,
    overwrite: bool = False,
    backend: str = "multisim",
) -> dict:
    """Run SPICE through Multisim or ngspice with safe analysis commands.

    The default path rejects scripting, shell escapes, file commands, and
    arbitrary command sourcing. Unrestricted engine commands require both the
    ``unsafe_commands`` argument and an explicit server-side environment flag.
    """
    design = circuit_design_from_spice(
        netlist,
        title="Standalone SPICE simulation",
        allow_unsupported=True,
    )
    backend_id = backend.strip().casefold()
    execution = _eda_application_service().simulate(
        backend_id,
        SimulationRequest(
            design=design,
            commands=commands,
            output_directory=output_dir or None,
            timeout_seconds=timeout,
            max_points=max_points,
            unsafe_commands=unsafe_commands,
            overwrite=overwrite,
        ),
    )
    result = _eda_compatibility_result(execution)
    if backend_id == "ngspice":
        result = _enrich_ngspice_result(result, netlist)
    return result


def _create_schematic_impl(
    netlist: str,
    output_ms14: str,
    probe_nets: list[str] | None,
    include_experimental_probes: bool,
    open_after_build: bool,
    image_path: str | None,
    overwrite: bool,
) -> dict:
    validate_spice_netlist(netlist)
    parsed = parse_netlist(netlist)
    if parsed.unsupported:
        preview = "; ".join(parsed.unsupported[:8])
        raise ValueError(
            "The schematic builder currently supports passive RLC, independent and "
            "behavioral/linear controlled sources, transmission lines, modeled "
            "diode/BJT/MOSFET/JFET/MESFET/switch devices, OPAMP5/TIMER8/DFF8, K/T/O/U, "
            "generic two-to-sixteen-terminal subcircuits, digital devices, and "
            "XFG/XSC virtual instruments "
            f"components only. Unsupported netlist lines: {preview}"
        )
    if not parsed.components:
        raise ValueError("The netlist contains no supported components")

    output_path = Path(output_ms14).expanduser().resolve()
    if output_path.suffix.lower() != ".ms14":
        raise ValueError("output_ms14 must end with .ms14")
    xml_path = Path(str(output_path) + ".xml")
    image = Path(image_path).expanduser().resolve() if image_path else None
    preflight_paths = [output_path, xml_path]
    if image is not None:
        preflight_paths.append(image)
    for path in preflight_paths:
        if path.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    selected_probes = probe_nets if include_experimental_probes else []
    build_result = build_schematic(
        netlist,
        xml_path,
        probe_nets=selected_probes,
    )
    encode_result = codec.encode(str(xml_path), str(output_path))
    result: dict = {
        "success": True,
        "maturity": "experimental",
        "supported_schematic_components": list(COMPONENT_DEFINITIONS),
        "build": build_result,
        "encode": encode_result,
        "ms14": str(output_path),
        "xml": str(xml_path),
        "experimental_probes": include_experimental_probes,
    }

    if open_after_build or image_path:
        result["open"] = client.open_circuit(str(output_path))
        result["verification"] = {
            "components": client.enum_components(0),
            "inputs": client.enum_inputs(0),
            "outputs": client.enum_outputs(0),
        }
        verification_path = Path(str(output_path) + ".verification.netlist")
        try:
            client.report_netlist(str(verification_path), False, 0)
            exported = (
                verification_path.read_text(encoding="utf-8", errors="replace")
                if verification_path.is_file()
                else ""
            )
        finally:
            verification_path.unlink(missing_ok=True)
        expected_specs = [item for item in parsed.components if item.kind != "GND"]
        native_components: dict[str, bool] = {}
        native_evidence: dict[str, str] = {}
        enumerated_components = set(result["verification"]["components"])
        result["verification"]["virtual_instruments"] = [
            spec.refdes for spec in expected_specs if spec.kind in {"OSC6", "XFG3"}
        ]
        for spec in expected_specs:
            # Multi-section digital parts are reported by Multisim as A1A/U1A
            # while EnumComponents returns their parent reference A1/U1.
            candidates = [spec.refdes]
            if spec.kind.startswith("D"):
                candidates.append(spec.refdes + "A")
            # Vendor-backed five-terminal macro-models and generic X
            # subcircuits can be emitted by Multisim with a section suffix
            # (for example XU1A), while EnumComponents reports the parent
            # reference XU1.
            if spec.kind in {"OPAMP5", "TIMER8", "DFF8"} or spec.kind.startswith("XSUB"):
                candidates.append(spec.refdes + "A")
            if spec.kind in {"OSC6", "XFG3"}:
                native_components[spec.refdes] = True
                native_evidence[spec.refdes] = "virtual-instrument enumeration"
            elif spec.kind in {"TIMER8", "DFF8"}:
                # Multisim keeps vendor timer macro-models as native
                # components, but ReportNetlist may omit their internal
                # digital/macro body. EnumComponents is therefore the
                # authoritative native-presence check for these carriers.
                native_components[spec.refdes] = spec.refdes in enumerated_components
                native_evidence[spec.refdes] = (
                    "native component enumeration; vendor/digital body may be omitted by ReportNetlist"
                )
            elif spec.kind == "K":
                native_components[spec.refdes] = spec.refdes in enumerated_components
                native_evidence[spec.refdes] = "native component enumeration"
            else:
                native_components[spec.refdes] = any(
                    re.search(
                        rf"(?<![A-Za-z0-9_]){re.escape(candidate)}(?![A-Za-z0-9_])",
                        exported,
                    )
                    for candidate in candidates
                )
                native_evidence[spec.refdes] = "ReportNetlist text match"
        result["verification"]["native_netlist_components"] = native_components
        result["verification"]["native_component_evidence"] = native_evidence
        result["verification"]["native_netlist_complete"] = all(
            native_components.values()
        )
        if not result["verification"]["native_netlist_complete"]:
            missing = [name for name, present in native_components.items() if not present]
            raise RuntimeError(
                "Multisim opened the design but omitted native netlist components: "
                + ", ".join(missing)
            )
    if image_path:
        assert image is not None
        image.parent.mkdir(parents=True, exist_ok=True)
        client.get_circuit_image(str(image), 2)
        result["image"] = str(image)
    return result


def _eda_application_service() -> EdaApplicationService:
    """Build the compatibility service without retaining MCP/COM transport state."""
    return EdaApplicationService(
        [
            MultisimBackend(_create_schematic_impl, _run_spice_netlist_impl),
            NgspiceBackend(),
        ]
    )


def _eda_compatibility_result(execution: BackendExecution) -> dict:
    result = execution.to_dict()["payload"].get("compatibility_result")
    if not isinstance(result, dict):
        raise RuntimeError("EDA backend omitted the compatibility result")
    return result


def _enrich_ngspice_result(result: dict[str, Any], netlist: str) -> dict[str, Any]:
    """Add parsed waveform and digital-observation evidence to a direct run."""
    raw_path = result.get("raw")
    if result.get("success") is True and isinstance(raw_path, str):
        parsed = parse_raw(raw_path)
        result["columns"] = parsed["columns"]
        result["rows"] = parsed["rows"]
        result["n_points"] = parsed["n_points"]
        result["measurements"] = summarize_columns(parsed)
        result["digital_observation"] = build_digital_observation_evidence(
            netlist,
            parsed["columns"],
            backend_id="ngspice",
        )
    result["timed_out"] = bool(
        isinstance(result.get("error"), str)
        and "timeout" in str(result["error"]).casefold()
    )
    result["last_error"] = result.get("error") or ""
    return result


def _run_ngspice_netlist_impl(
    netlist: str,
    commands: str,
    output_dir: str | None = None,
    timeout: float = 120.0,
    max_points: int = 2000,
    unsafe_commands: bool = False,
    overwrite: bool = False,
    cancel_requested: Callable[[], bool] | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Compatibility runner used by the complete open-backend pipeline."""
    if cancel_requested is not None and cancel_requested():
        raise InterruptedError("Experiment cancellation requested")
    if heartbeat is not None:
        heartbeat()
    design = circuit_design_from_spice(
        netlist,
        title="Standalone ngspice simulation",
        allow_unsupported=True,
    )
    ngspice_service = EdaApplicationService(
        [
            NgspiceBackend(
                process_runner=cancellable_process_runner(
                    cancel_requested,
                    heartbeat,
                )
            )
        ]
    )
    execution = ngspice_service.simulate(
        "ngspice",
        SimulationRequest(
            design=design,
            commands=commands,
            output_directory=output_dir or None,
            timeout_seconds=timeout,
            max_points=max_points,
            unsafe_commands=unsafe_commands,
            overwrite=overwrite,
        ),
    )
    result = _enrich_ngspice_result(_eda_compatibility_result(execution), netlist)
    if cancel_requested is not None and cancel_requested():
        raise InterruptedError("Experiment cancellation requested")
    if heartbeat is not None:
        heartbeat()
    return result


@mcp.tool()
def create_schematic_from_netlist(
    netlist: str,
    output_ms14: str,
    probe_nets: list[str] | None = None,
    include_experimental_probes: bool = False,
    open_after_build: bool = True,
    image_path: str | None = None,
    overwrite: bool = False,
    executable_netlist: dict[str, Any] | None = None,
    netlist_approval: dict[str, Any] | None = None,
) -> dict:
    """Create an editable Multisim schematic from a supported SPICE netlist.

    Supports RLC components, scalar/waveform voltage and current sources,
    B/E/F/G/H/T primitives, modeled semiconductors and switches, OPAMP5, and
    user-local verified TIMER8/LM555CN and DFF8/7474N native carriers,
    generic two-to-sixteen-terminal X subcircuits, digital devices, ground,
    named nets, wiring, and deterministic layout. Compatible inline subcircuits
    are recursively expanded into editable primitives; unsupported macro-model
    constructs remain explicit carrier-only evidence. Generated schematic probes
    remain experimental. The high-level experiment tool obtains authoritative
    data from the same source netlist through Multisim's command engine.

    When ``executable_netlist`` and ``netlist_approval`` are both supplied,
    the approval is revalidated against the immutable compiled preview before
    any schematic work begins. The supplied ``netlist`` must exactly match the
    approved preview's bound SPICE text. This path opens schematic generation
    only; it does not approve or start a simulation.
    """
    approved_handoff = executable_netlist is not None or netlist_approval is not None
    if approved_handoff:
        if executable_netlist is None or netlist_approval is None:
            raise ValueError(
                "executable_netlist and netlist_approval must be provided together"
            )
        validate_approved_executable_netlist(executable_netlist, netlist_approval)
        approved_spice = executable_netlist.get("spice_netlist")
        if not isinstance(approved_spice, str) or netlist != approved_spice:
            raise ValueError(
                "netlist does not match the approved executable preview's bound SPICE"
            )

    output_path = Path(output_ms14).expanduser().resolve()
    if output_path.suffix.lower() != ".ms14":
        raise ValueError("output_ms14 must end with .ms14")
    design = circuit_design_from_spice(
        netlist,
        title=output_path.stem,
    )
    execution = _eda_application_service().create_schematic(
        "multisim",
        SchematicRequest(
            design=design,
            output_directory=str(output_path.parent),
            file_stem=output_path.stem,
            render_image=image_path is not None,
            image_path=image_path,
            open_after_build=open_after_build,
            include_experimental_probes=include_experimental_probes,
            probe_nets=tuple(probe_nets or ()),
            overwrite=overwrite,
        ),
    )
    result = _eda_compatibility_result(execution)
    if approved_handoff:
        # Keep the generated artifact traceable without copying the complete
        # approval payload into a file-producing compatibility response.
        result["netlist_approval"] = {
            "approval_id": netlist_approval["approval_id"],
            "approval_digest": netlist_approval["approval_digest"],
            "compiled_id": netlist_approval["compiled_id"],
            "compiled_digest": netlist_approval["compiled_digest"],
            "state": netlist_approval["state"],
            "schematic_generation": "approved",
            "simulation_started": False,
        }
    return result


def _run_circuit_experiment_transaction(
    netlist: str,
    commands: str,
    output_dir: str,
    title: str = "Multisim experiment",
    timeout: float = 120.0,
    max_points: int = 2000,
    overwrite: bool = False,
    checkpoint: Callable[[str, int, str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    owner: str | None = None,
    requirements: list[DesignRequirement] | None = None,
    theoretical_values: dict[str, float] | None = None,
    model_references: list[dict[str, Any]] | None = None,
    declared_dialect: str | None = None,
    approval_provenance: Mapping[str, Any] | None = None,
) -> ExperimentResult | VerifiedExperimentResult:
    """Assemble the selected local EDA dependencies for the experiment pipeline."""
    backend_id = selected_experiment_backend()
    if backend_id == "ngspice":
        pipeline = MultisimExperimentPipeline(
            render_portable_schematic,
            _run_ngspice_netlist_impl,
            backend_id="ngspice",
            backend_display_name="ngspice open-source simulator",
            schematic_artifact_names=(
                "schematic.svg",
                "schematic.png",
                "backend.json",
            ),
            design_filename="schematic.svg",
            editable_schematic=False,
        )
    else:
        pipeline = MultisimExperimentPipeline(
            _create_schematic_impl,
            _run_spice_netlist_impl,
        )
    result = pipeline.run(
        netlist=netlist,
        commands=commands,
        output_dir=output_dir,
        title=title,
        timeout=timeout,
        max_points=max_points,
        overwrite=overwrite,
        checkpoint=checkpoint,
        cancel_requested=cancel_requested,
        owner=owner,
        requirements=requirements,
        theoretical_values=theoretical_values,
        model_references=model_references,
        declared_dialect=declared_dialect,
        approval_provenance=approval_provenance,
    )
    return result  # type: ignore[return-value]


def _experiment_application_service() -> ExperimentApplicationService:
    """Build the experiment service around the current transaction executor."""
    return ExperimentApplicationService(_run_circuit_experiment_transaction)


def _design_optimization_service() -> DesignOptimizationService:
    """Build the transport-neutral optimizer around the current experiment service."""
    return DesignOptimizationService(_experiment_application_service())


def _global_optimization_service() -> GlobalDesignOptimizationService:
    """Build mixed topology/value global optimization around real experiments."""
    return GlobalDesignOptimizationService(_experiment_application_service())


def _design_comparison_service() -> DesignVariantComparisonService:
    """Build complete-design comparison around the current experiment service."""
    return DesignVariantComparisonService(_experiment_application_service())


def _design_patch_evaluation_service() -> DesignPatchEvaluationService:
    """Build read-only patch retesting around the current experiment service."""
    return DesignPatchEvaluationService(_experiment_application_service())


def _run_circuit_experiment_impl(
    netlist: str,
    commands: str,
    output_dir: str,
    title: str = "Multisim experiment",
    timeout: float = 120.0,
    max_points: int = 2000,
    overwrite: bool = False,
    checkpoint: Callable[[str, int, str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    owner: str | None = None,
    requirements: list[DesignRequirement] | None = None,
    theoretical_values: dict[str, float] | None = None,
    approval_provenance: Mapping[str, Any] | None = None,
) -> ExperimentResult | VerifiedExperimentResult:
    """Route one complete experiment through the transport-neutral service."""
    design = circuit_design_from_spice(
        netlist,
        title=title.strip() or "Multisim experiment",
    )
    request = ExperimentRequest(
        design=design,
        commands=commands,
        output_directory=output_dir,
        title=title,
        timeout_seconds=timeout,
        max_points=max_points,
        overwrite=overwrite,
        owner=owner,
        requirements=(tuple(requirements) if requirements is not None else None),
        theoretical_values=(
            theoretical_values if theoretical_values is not None else {}
        ),
        approval_provenance=approval_provenance,
    )
    result = _experiment_application_service().run(
        request,
        checkpoint=checkpoint,
        cancel_requested=cancel_requested,
    )
    return result  # type: ignore[return-value]


@mcp.tool()
def run_circuit_experiment(
    netlist: str,
    commands: str,
    output_dir: str,
    title: str = "Multisim experiment",
    timeout: float = 120.0,
    max_points: int = 2000,
    overwrite: bool = False,
) -> ExperimentResult:
    """Create a schematic, run a safe Multisim analysis, and export a report.

    This synchronous compatibility tool blocks until completion. For queueing,
    progress, cancellation, persisted state, and worker recovery, prefer
    ``submit_circuit_experiment``.
    """
    return _run_circuit_experiment_impl(
        netlist,
        commands,
        output_dir,
        title,
        timeout,
        max_points,
        overwrite,
    )


@mcp.tool()
def run_verified_circuit_experiment(
    spec: ExperimentSpec,
    output_dir: str,
    timeout: float = 120.0,
    max_points: int = 2000,
    overwrite: bool = False,
    executable_netlist: dict[str, Any] | None = None,
    netlist_approval: dict[str, Any] | None = None,
    simulation_plan_approval: dict[str, Any] | None = None,
) -> VerifiedExperimentResult:
    """Run an ExperimentSpec and persist evidence-backed requirement verdicts.

    When the three approval inputs are supplied, the exact executable-netlist
    and simulation-plan handoff is revalidated before any schematic or
    simulation work begins. The legacy direct-spec path remains available for
    compatibility; it does not claim this additional approval provenance.
    """
    normalized = validate_experiment_spec(spec)
    approved_handoff = (
        executable_netlist is not None
        or netlist_approval is not None
        or simulation_plan_approval is not None
    )
    if approved_handoff:
        if (
            executable_netlist is None
            or netlist_approval is None
            or simulation_plan_approval is None
        ):
            raise ValueError(
                "executable_netlist, netlist_approval, and "
                "simulation_plan_approval must be provided together"
            )
        validated_approval = validate_approved_simulation_plan(
            executable_netlist,
            netlist_approval,
            normalized,
            simulation_plan_approval,
        )
    result = _run_circuit_experiment_impl(
        normalized["netlist"],
        normalized["commands"],
        output_dir,
        normalized["title"],
        timeout,
        max_points,
        overwrite,
        requirements=normalized["requirements"],
        theoretical_values=normalized["theoretical_values"],
        approval_provenance=(
            build_experiment_approval_provenance(validated_approval)
            if approved_handoff
            else None
        ),
    )
    if approved_handoff:
        result["simulation_plan_approval"] = {
            "approval_id": simulation_plan_approval["approval_id"],
            "approval_digest": simulation_plan_approval["approval_digest"],
            "netlist_approval_id": simulation_plan_approval["netlist_approval_id"],
            "netlist_approval_digest": simulation_plan_approval[
                "netlist_approval_digest"
            ],
            "state": simulation_plan_approval["state"],
            "simulation_started": bool(result.get("success")),
        }
    return result  # type: ignore[return-value]


@mcp.tool(com_serialized=False)
def diagnose_design(
    design: dict[str, Any],
    experiment_dir: str = "",
    simulation_failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Diagnose a circuit without modifying it or silently guessing repairs.

    Structural checks require only ``design``. When ``experiment_dir`` is
    supplied, its artifact integrity manifest and canonical netlist are verified
    before requirement, convergence, BJT, or op-amp evidence is used. An
    optional bounded ``simulation_failure`` object may contain code, type,
    stage, and message fields from a failed run.
    """
    normalized_design = CircuitDesign.from_dict(design)
    if not isinstance(experiment_dir, str):
        raise ValueError("experiment_dir must be a string")
    evidence = (
        load_experiment_diagnosis_evidence(normalized_design, experiment_dir)
        if experiment_dir.strip()
        else None
    )
    return DesignDiagnosisService().run(
        normalized_design,
        experiment_evidence=evidence,
        simulation_failure=simulation_failure,
    )


@mcp.tool()
def evaluate_design_patch(
    design: dict[str, Any],
    patch: dict[str, Any],
    spec: dict[str, Any],
    output_dir: str,
    regenerate_source_netlist: bool = False,
    timeout_per_experiment: float = 120.0,
    max_points: int = 2000,
) -> dict[str, Any]:
    """Retest one explicit reversible patch against its unchanged baseline.

    The service runs exactly two verified experiments under the same hard
    requirements, writes before/after deterministic diagnoses and an inverse
    patch, and never persists the candidate as the source design. A passing
    result remains subject to the separate local approval workflow.
    """
    normalized_design = CircuitDesign.from_dict(design)
    return _design_patch_evaluation_service().run(
        normalized_design,
        patch,
        spec,
        output_dir,
        regenerate_source_netlist=regenerate_source_netlist,
        timeout_per_experiment=timeout_per_experiment,
        max_points=max_points,
    )


@mcp.tool()
def optimize_design(
    design: dict[str, Any],
    spec: dict[str, Any],
    output_dir: str,
    timeout_per_experiment: float = 120.0,
    max_points: int = 2000,
) -> dict[str, Any]:
    """Evaluate bounded component-value candidates without modifying the source design.

    The baseline consumes one experiment from ``spec.max_experiments``. Values
    may be explicit or generated from bounded E12/E24/E48/E96 ranges. Electrical,
    optional in-stock, and maximum variable-cost rules are hard constraints;
    failed/unverified candidates are never feasible, and the returned best patch
    still requires the separate local approval workflow before persistence.
    """
    normalized_design = CircuitDesign.from_dict(design)
    return _design_optimization_service().run(
        normalized_design,
        spec,
        output_dir,
        timeout_per_experiment=timeout_per_experiment,
        max_points=max_points,
    )


@mcp.tool()
def global_optimize_design(
    design: dict[str, Any],
    spec: dict[str, Any],
    output_dir: str,
    timeout_per_experiment: float = 120.0,
    max_points: int = 2000,
) -> dict[str, Any]:
    """Run auditable mixed topology/value multi-objective global optimization.

    Small declared domains are exhaustive; larger domains use deterministic
    Halton space filling. Every candidate runs a real verified experiment,
    failed hard constraints are excluded, and the result contains an
    epsilon-aware Pareto front. No candidate is persisted automatically.
    """
    normalized_design = CircuitDesign.from_dict(design)
    return _global_optimization_service().run(
        normalized_design,
        spec,
        output_dir,
        timeout_per_experiment=timeout_per_experiment,
        max_points=max_points,
    )


@mcp.tool(com_serialized=False)
def submit_global_optimization(
    design: dict[str, Any],
    spec: dict[str, Any],
    output_dir: str,
    timeout_per_experiment: float = 120.0,
    max_points: int = 2000,
    job_timeout: float = 21600.0,
    heartbeat_timeout: float = 180.0,
    resume_existing: bool = False,
) -> JobSubmission:
    """Queue durable mixed topology/value Pareto optimization.

    Completed candidates are integrity-checked and reused after restart. An
    interrupted candidate is rerun in a new attempt directory. No recommended
    patch is persisted into the source design automatically.
    """
    normalized_design = CircuitDesign.from_dict(design)
    validate_global_optimization_spec(spec, normalized_design)
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ValueError("output_dir must not be empty")
    unresolved = Path(output_dir).expanduser()
    if unresolved.is_symlink():
        raise ValueError("output_dir must not be a symbolic link")
    output_path = unresolved.resolve()
    if output_path == Path(output_path.anchor):
        raise ValueError("output_dir must not be a filesystem root")
    if not isinstance(resume_existing, bool):
        raise ValueError("resume_existing must be a boolean")
    if output_path.exists():
        if not output_path.is_dir():
            raise ValueError("output_dir exists and is not a directory")
        if any(output_path.iterdir()) and not resume_existing:
            raise FileExistsError(
                "output_dir is not empty; set resume_existing only for a matching "
                "interrupted global optimization"
            )
    if (
        isinstance(timeout_per_experiment, bool)
        or not isinstance(timeout_per_experiment, (int, float))
        or not math.isfinite(float(timeout_per_experiment))
        or not 0 < float(timeout_per_experiment) <= 3600
    ):
        raise ValueError("timeout_per_experiment must be between 0 and 3600")
    if (
        isinstance(max_points, bool)
        or not isinstance(max_points, int)
        or not 1 <= max_points <= 100_000
    ):
        raise ValueError("max_points must be between 1 and 100000")
    if (
        isinstance(job_timeout, bool)
        or not isinstance(job_timeout, (int, float))
        or not math.isfinite(float(job_timeout))
        or not 1 <= float(job_timeout) <= 86_400
    ):
        raise ValueError("job_timeout must be between 1 and 86400 seconds")
    if float(job_timeout) <= float(timeout_per_experiment):
        raise ValueError("job_timeout must exceed timeout_per_experiment")
    if (
        isinstance(heartbeat_timeout, bool)
        or not isinstance(heartbeat_timeout, (int, float))
        or not math.isfinite(float(heartbeat_timeout))
        or not 10 <= float(heartbeat_timeout) <= 900
    ):
        raise ValueError("heartbeat_timeout must be between 10 and 900 seconds")
    persisted_spec = json.loads(json.dumps(spec, ensure_ascii=False, allow_nan=False))
    return _job_manager().submit(
        {
            "job_kind": "global_optimization",
            "design": normalized_design.to_dict(),
            "global_optimization_spec": persisted_spec,
            "output_dir": str(output_path),
            "timeout_per_experiment": float(timeout_per_experiment),
            "max_points": max_points,
            "job_timeout": float(job_timeout),
            "heartbeat_timeout": float(heartbeat_timeout),
            "resume_existing": resume_existing,
        }
    )


@mcp.tool()
def autonomous_correct_design(
    design: dict[str, Any],
    spec: dict[str, Any],
    output_dir: str,
    provider_config_path: str | None = None,
    provider: str | None = None,
    fallback_providers: list[str] | None = None,
    allow_failover: bool = False,
    model_timeout: float = 60.0,
    timeout_per_experiment: float = 120.0,
    max_points: int = 2000,
) -> dict[str, Any]:
    """Autonomously diagnose, propose, simulate, and select a repair candidate.

    The configured model may propose bounded parameter or topology patches, but
    every candidate is revalidated and simulated locally. The final all-pass
    candidate is returned as one reversible patch against the original design;
    applying it remains a separate explicit approval operation.
    """
    normalized_design = CircuitDesign.from_dict(design)
    config = read_provider_config(provider_config_path)
    registry = ModelProviderRegistry.from_config(config)
    planner = ModelRepairPlanner(
        registry,
        provider_id=provider,
        fallback_provider_ids=tuple(fallback_providers or ()),
        allow_failover=allow_failover,
        timeout=model_timeout,
    )
    service = AutonomousDesignCorrectionService(
        _experiment_application_service(), planner
    )
    return service.run(
        normalized_design,
        spec,
        output_dir,
        timeout_per_experiment=timeout_per_experiment,
        max_points=max_points,
    )


@mcp.tool(com_serialized=False)
def submit_autonomous_correction(
    design: dict[str, Any],
    spec: dict[str, Any],
    output_dir: str,
    provider_config_path: str | None = None,
    provider: str | None = None,
    fallback_providers: list[str] | None = None,
    allow_failover: bool = False,
    model_timeout: float = 60.0,
    timeout_per_experiment: float = 120.0,
    max_points: int = 2000,
    job_timeout: float = 21600.0,
    heartbeat_timeout: float = 180.0,
    resume_existing: bool = False,
) -> JobSubmission:
    """Queue durable model-planned correction with round-level recovery.

    The persisted provider configuration is validated and secret-free: it
    contains only endpoint/model metadata and environment-variable names. API
    keys are resolved by the isolated worker and are never written to job state.
    Completed rounds are evidence-revalidated; an incomplete planning round is
    replanned in a new attempt directory. No patch is applied automatically.
    """
    normalized_design = CircuitDesign.from_dict(design)
    validate_autonomous_correction_spec(spec, normalized_design)
    provider_config = read_provider_config(provider_config_path)
    registry = ModelProviderRegistry.from_config(provider_config)
    provider_ids = set(registry.provider_ids())
    if provider is not None and provider not in provider_ids:
        raise ValueError(f"unknown provider: {provider}")
    fallbacks = list(fallback_providers or [])
    if len(fallbacks) != len(set(fallbacks)):
        raise ValueError("fallback_providers must not contain duplicates")
    unknown_fallbacks = sorted(set(fallbacks) - provider_ids)
    if unknown_fallbacks:
        raise ValueError(f"unknown fallback providers: {unknown_fallbacks}")
    if fallbacks and not allow_failover:
        raise ValueError("fallback providers require allow_failover=true")
    if not isinstance(allow_failover, bool):
        raise ValueError("allow_failover must be a boolean")
    if (
        isinstance(model_timeout, bool)
        or not isinstance(model_timeout, (int, float))
        or not math.isfinite(float(model_timeout))
        or not 0 < float(model_timeout) <= 3600
    ):
        raise ValueError("model_timeout must be between 0 and 3600 seconds")
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ValueError("output_dir must not be empty")
    unresolved = Path(output_dir).expanduser()
    if unresolved.is_symlink():
        raise ValueError("output_dir must not be a symbolic link")
    output_path = unresolved.resolve()
    if output_path == Path(output_path.anchor):
        raise ValueError("output_dir must not be a filesystem root")
    if not isinstance(resume_existing, bool):
        raise ValueError("resume_existing must be a boolean")
    if output_path.exists():
        if not output_path.is_dir():
            raise ValueError("output_dir exists and is not a directory")
        if any(output_path.iterdir()) and not resume_existing:
            raise FileExistsError(
                "output_dir is not empty; set resume_existing only for a matching "
                "interrupted autonomous correction"
            )
    if (
        isinstance(timeout_per_experiment, bool)
        or not isinstance(timeout_per_experiment, (int, float))
        or not math.isfinite(float(timeout_per_experiment))
        or not 0 < float(timeout_per_experiment) <= 3600
    ):
        raise ValueError("timeout_per_experiment must be between 0 and 3600")
    if (
        isinstance(max_points, bool)
        or not isinstance(max_points, int)
        or not 1 <= max_points <= 100_000
    ):
        raise ValueError("max_points must be between 1 and 100000")
    if (
        isinstance(job_timeout, bool)
        or not isinstance(job_timeout, (int, float))
        or not math.isfinite(float(job_timeout))
        or not 1 <= float(job_timeout) <= 86_400
    ):
        raise ValueError("job_timeout must be between 1 and 86400 seconds")
    if float(job_timeout) <= max(
        float(timeout_per_experiment), float(model_timeout)
    ):
        raise ValueError("job_timeout must exceed model and experiment timeouts")
    if (
        isinstance(heartbeat_timeout, bool)
        or not isinstance(heartbeat_timeout, (int, float))
        or not math.isfinite(float(heartbeat_timeout))
        or not 10 <= float(heartbeat_timeout) <= 900
    ):
        raise ValueError("heartbeat_timeout must be between 10 and 900 seconds")
    if float(heartbeat_timeout) <= float(model_timeout):
        raise ValueError("heartbeat_timeout must exceed model_timeout")
    persisted_spec = json.loads(json.dumps(spec, ensure_ascii=False, allow_nan=False))
    persisted_provider_config = json.loads(
        json.dumps(provider_config, ensure_ascii=False, allow_nan=False)
    )
    return _job_manager().submit(
        {
            "job_kind": "autonomous_correction",
            "design": normalized_design.to_dict(),
            "autonomous_correction_spec": persisted_spec,
            "provider_config": persisted_provider_config,
            "provider": provider,
            "fallback_providers": fallbacks,
            "allow_failover": allow_failover,
            "model_timeout": float(model_timeout),
            "output_dir": str(output_path),
            "timeout_per_experiment": float(timeout_per_experiment),
            "max_points": max_points,
            "job_timeout": float(job_timeout),
            "heartbeat_timeout": float(heartbeat_timeout),
            "resume_existing": resume_existing,
        }
    )


@mcp.tool(com_serialized=False)
def submit_design_optimization(
    design: dict[str, Any],
    spec: dict[str, Any],
    output_dir: str,
    timeout_per_experiment: float = 120.0,
    max_points: int = 2000,
    job_timeout: float = 7200.0,
    heartbeat_timeout: float = 180.0,
    resume_existing: bool = False,
) -> JobSubmission:
    """Queue a durable optimization with candidate-level crash recovery.

    Completed candidates are evidence-verified and reused after a worker or MCP
    restart. An interrupted candidate is rerun in a new attempt directory. The
    result still returns only a proposed patch and never modifies the design.
    Set ``resume_existing`` only to adopt a matching interrupted output folder.
    """
    normalized_design = CircuitDesign.from_dict(design)
    validate_optimization_spec(spec, normalized_design)
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ValueError("output_dir must not be empty")
    unresolved = Path(output_dir).expanduser()
    if unresolved.is_symlink():
        raise ValueError("output_dir must not be a symbolic link")
    output_path = unresolved.resolve()
    if output_path == Path(output_path.anchor):
        raise ValueError("output_dir must not be a filesystem root")
    if not isinstance(resume_existing, bool):
        raise ValueError("resume_existing must be a boolean")
    if output_path.exists():
        if not output_path.is_dir():
            raise ValueError("output_dir exists and is not a directory")
        if any(output_path.iterdir()) and not resume_existing:
            raise FileExistsError(
                "output_dir is not empty; set resume_existing only for a matching "
                "interrupted optimization"
            )
    if (
        isinstance(timeout_per_experiment, bool)
        or not isinstance(timeout_per_experiment, (int, float))
        or not math.isfinite(float(timeout_per_experiment))
        or not 0 < float(timeout_per_experiment) <= 3600
    ):
        raise ValueError("timeout_per_experiment must be between 0 and 3600")
    if (
        isinstance(max_points, bool)
        or not isinstance(max_points, int)
        or not 1 <= max_points <= 100_000
    ):
        raise ValueError("max_points must be between 1 and 100000")
    if (
        isinstance(job_timeout, bool)
        or not isinstance(job_timeout, (int, float))
        or not math.isfinite(float(job_timeout))
        or not 1 <= float(job_timeout) <= 86_400
    ):
        raise ValueError("job_timeout must be between 1 and 86400 seconds")
    if float(job_timeout) <= float(timeout_per_experiment):
        raise ValueError("job_timeout must exceed timeout_per_experiment")
    if (
        isinstance(heartbeat_timeout, bool)
        or not isinstance(heartbeat_timeout, (int, float))
        or not math.isfinite(float(heartbeat_timeout))
        or not 10 <= float(heartbeat_timeout) <= 900
    ):
        raise ValueError("heartbeat_timeout must be between 10 and 900 seconds")
    persisted_spec = json.loads(
        json.dumps(spec, ensure_ascii=False, allow_nan=False)
    )
    return _job_manager().submit(
        {
            "job_kind": "optimization",
            "design": normalized_design.to_dict(),
            "optimization_spec": persisted_spec,
            "output_dir": str(output_path),
            "timeout_per_experiment": float(timeout_per_experiment),
            "max_points": max_points,
            "job_timeout": float(job_timeout),
            "heartbeat_timeout": float(heartbeat_timeout),
            "resume_existing": resume_existing,
        }
    )


@mcp.tool()
def compare_design_variants(
    variants: list[dict[str, Any]],
    spec: dict[str, Any],
    output_dir: str,
    timeout_per_experiment: float = 120.0,
    max_points: int = 2000,
) -> dict[str, Any]:
    """Rank 2-16 complete designs using one evidence-backed experiment contract.

    Each item must contain exactly ``variant_id`` and ``design``. All requirements
    are hard constraints, only finite measured passes are ranked, and no source
    design is modified or automatically adopted.
    """
    if not isinstance(variants, list):
        raise ValueError("variants must be an array")
    normalized: dict[str, CircuitDesign] = {}
    for index, item in enumerate(variants):
        if not isinstance(item, dict) or set(item) != {"variant_id", "design"}:
            raise ValueError(
                f"variants[{index}] must contain exactly variant_id and design"
            )
        variant_id = item["variant_id"]
        if not isinstance(variant_id, str) or not variant_id.strip():
            raise ValueError(f"variants[{index}].variant_id must be non-empty")
        normalized_id = variant_id.strip()
        if normalized_id in normalized:
            raise ValueError(f"duplicate variant id: {normalized_id}")
        normalized[normalized_id] = CircuitDesign.from_dict(item["design"])
    return _design_comparison_service().run(
        normalized,
        spec,
        output_dir,
        timeout_per_experiment=timeout_per_experiment,
        max_points=max_points,
    )


def _run_experiment_sweep_impl(
    spec: dict[str, Any],
    output_dir: str,
    timeout_per_run: float = 120.0,
    max_points: int = 2000,
    overwrite: bool = False,
    checkpoint: Callable[[str, int, str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    """Execute a validated sweep as one all-or-nothing artifact transaction."""

    def notify(stage_name: str, progress: int, message: str) -> None:
        if checkpoint is not None:
            checkpoint(stage_name, progress, message)
        if cancel_requested is not None and cancel_requested():
            raise InterruptedError("Sweep cancellation requested")

    if not output_dir.strip():
        raise ValueError("output_dir must not be empty")
    if not math.isfinite(timeout_per_run) or not 0 < timeout_per_run <= 3600:
        raise ValueError("timeout_per_run must be between 0 and 3600 seconds")
    if max_points < 1 or max_points > 100_000:
        raise ValueError("max_points must be between 1 and 100000")
    root = Path(output_dir).expanduser().resolve()
    if root == Path(root.anchor):
        raise ValueError("output_dir must not be a filesystem root")
    plan = expand_sweep(spec)
    lease_owner = owner or f"sweep-{uuid.uuid4().hex}"
    with output_lease(str(root), lease_owner):
        if root.exists() and not root.is_dir():
            raise ValueError(f"Sweep output path is not a directory: {root}")
        if root.exists() and any(root.iterdir()) and not overwrite:
            raise FileExistsError(f"Refusing to overwrite non-empty sweep directory: {root}")
        root.parent.mkdir(parents=True, exist_ok=True)
        stage = root.parent / f".{root.name}.multisim-sweep-{uuid.uuid4().hex}"
        stage.mkdir(parents=False, exist_ok=False)
        published = False
        try:
            notify("sweep_preflight", 3, f"Prepared {plan['run_count']} validated runs")
            run_results: list[dict[str, Any]] = []
            total = int(plan["run_count"])
            for position, run in enumerate(plan["runs"], start=1):
                progress = 5 + int((position - 1) / total * 78)
                notify(
                    "sweep_run",
                    progress,
                    f"Running {run['run_id']} ({position}/{total})",
                )
                run_dir = stage / "runs" / str(run["run_id"])
                simulation = _run_spice_netlist_impl(
                    str(run["netlist"]),
                    str(run["commands"]),
                    output_dir=str(run_dir),
                    timeout=timeout_per_run,
                    max_points=max_points,
                    unsafe_commands=False,
                    overwrite=False,
                    cancel_requested=cancel_requested,
                    heartbeat=lambda p=position: notify(
                        "sweep_run", progress, f"Waiting for run {p}/{total}"
                    ),
                )
                if not simulation.get("success"):
                    raise RuntimeError(f"Sweep run {run['run_id']} did not succeed")
                measured = measure_many(
                    parse_raw(str(simulation["raw"])), plan["measurements"]
                )
                run_results.append(
                    {
                        "run_id": run["run_id"],
                        "index": run["index"],
                        "status": (
                            "measured"
                            if all(item["status"] == "measured" for item in measured)
                            else "unverified"
                        ),
                        "variables": run["variables"],
                        "measurements": measured,
                        "artifacts_dir": f"runs/{run['run_id']}",
                    }
                )
            notify("sweep_summary", 86, "Writing sweep summary and flat data table")
            summary = {
                "schema_version": 1,
                "result_type": "sweep",
                "title": plan["title"],
                "mode": plan["mode"],
                "seed": plan["seed"],
                "run_count": total,
                "measurement_ids": [item["id"] for item in plan["measurements"]],
                "runs": run_results,
                "reproducibility": {
                    "spec": spec,
                    "timeout_per_run": timeout_per_run,
                    "max_points": max_points,
                },
            }
            (stage / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            variable_names = sorted(
                {name for run in run_results for name in run["variables"]}
            )
            measurement_ids = [item["id"] for item in plan["measurements"]]
            with (stage / "data.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["run_id", "status", *variable_names, *measurement_ids])
                for run in run_results:
                    values = {item["id"]: item.get("value") for item in run["measurements"]}
                    writer.writerow(
                        [
                            run["run_id"],
                            run["status"],
                            *(run["variables"].get(name, "") for name in variable_names),
                            *(values.get(name, "") for name in measurement_ids),
                        ]
                    )
            sweep_artifacts: dict[str, str] = {}
            for artifact_path in sorted(stage.rglob("*")):
                if artifact_path.is_symlink():
                    raise ValueError(
                        "Sweep artifacts must not contain symbolic links: "
                        f"{artifact_path.relative_to(stage).as_posix()}"
                    )
                if artifact_path.is_file():
                    relative = artifact_path.relative_to(stage).as_posix()
                    if relative == DIRECTORY_MANIFEST_NAME:
                        continue
                    sweep_artifacts[relative] = (
                        "sweep-summary"
                        if relative == "summary.json"
                        else "sweep-data"
                        if relative == "data.csv"
                        else "run-artifact"
                    )
            write_directory_manifest(
                stage,
                directory_kind="optimization",
                entity_id=sweep_id_for_output_dir(root),
                state="succeeded",
                artifacts=sweep_artifacts,
                metadata={
                    "operation": "experiment-sweep",
                    "title": plan["title"],
                    "mode": plan["mode"],
                    "run_count": total,
                    "seed": plan["seed"],
                },
            )
            notify("sweep_publish", 94, "Publishing the complete sweep transaction")
            backup = root.parent / f".{root.name}.backup-{uuid.uuid4().hex}"
            had_root = root.exists()
            if had_root:
                os.replace(root, backup)
            try:
                os.replace(stage, root)
                published = True
            except Exception:
                if had_root and backup.exists():
                    os.replace(backup, root)
                raise
            if backup.exists():
                shutil.rmtree(backup)
            registered = register_sweep(str(root))
            notify("complete", 100, "Sweep completed")
            return {
                "success": True,
                "result_type": "sweep",
                "sweep_id": registered["sweep_id"],
                "resources": registered["resources"],
                "summary": str(root / "summary.json"),
                "data": str(root / "data.csv"),
                "output_dir": str(root),
                "run_count": total,
            }
        finally:
            if not published:
                shutil.rmtree(stage, ignore_errors=True)


@mcp.tool()
def run_experiment_sweep(
    spec: dict[str, Any],
    output_dir: str,
    timeout_per_run: float = 120.0,
    max_points: int = 2000,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run a transactional parameter, tolerance, temperature, or Monte Carlo sweep."""
    return _run_experiment_sweep_impl(
        spec, output_dir, timeout_per_run, max_points, overwrite
    )


@mcp.tool(com_serialized=False)
def submit_experiment_sweep(
    spec: dict[str, Any],
    output_dir: str,
    timeout_per_run: float = 120.0,
    max_points: int = 2000,
    overwrite: bool = False,
    job_timeout: float = 3600.0,
    heartbeat_timeout: float = 180.0,
) -> JobSubmission:
    """Queue a durable sweep in the same isolated, cancellable worker system."""
    plan = expand_sweep(spec)
    if not output_dir.strip():
        raise ValueError("output_dir must not be empty")
    output_path = Path(output_dir).expanduser().resolve()
    if output_path == Path(output_path.anchor):
        raise ValueError("output_dir must not be a filesystem root")
    if not math.isfinite(timeout_per_run) or not 0 < timeout_per_run <= 3600:
        raise ValueError("timeout_per_run must be between 0 and 3600 seconds")
    if max_points < 1 or max_points > 100_000:
        raise ValueError("max_points must be between 1 and 100000")
    if not math.isfinite(job_timeout) or not 1 <= job_timeout <= 86_400:
        raise ValueError("job_timeout must be between 1 and 86400 seconds")
    if job_timeout <= timeout_per_run:
        raise ValueError("job_timeout must exceed timeout_per_run")
    if not math.isfinite(heartbeat_timeout) or not 10 <= heartbeat_timeout <= 900:
        raise ValueError("heartbeat_timeout must be between 10 and 900 seconds")
    return _job_manager().submit(
        {
            "job_kind": "sweep",
            "sweep_spec": spec,
            "output_dir": str(output_path),
            "timeout_per_run": timeout_per_run,
            "max_points": max_points,
            "overwrite": overwrite,
            "job_timeout": job_timeout,
            "heartbeat_timeout": heartbeat_timeout,
            "run_count": plan["run_count"],
        }
    )


@mcp.tool()
def generate_report(
    output_path: str,
    title: str = "",
    analyses: list[dict] | None = None,
    include_netlist: bool = False,
    include_bom: bool = False,
    include_image: bool = False,
) -> dict:
    """Write a Markdown report for the open circuit and analysis results."""
    return client.generate_report(
        output_path,
        title,
        analyses,
        include_netlist,
        include_bom,
        include_image,
    )


@mcp.tool()
def get_rlc_value(component_name: str) -> dict:
    """Read the current value of an R/L/C component through RLCValue."""
    return client.get_rlc_value(component_name)


@mcp.tool()
def set_rlc_value(component_name: str, value: float) -> dict:
    """Set an R/L/C component value through SetRLCValue."""
    return client.set_rlc_value(component_name, value)


@mcp.tool()
def decode_ms14(path: str, output_xml: str | None = None) -> dict:
    """Decode a .ms14 file to editable XML using ewd."""
    return codec.decode(path, output_xml)


@mcp.tool()
def encode_ms14(source_xml: str, output_ms14: str | None = None) -> dict:
    """Encode an XML design back to .ms14 using ewe."""
    return codec.encode(source_xml, output_ms14)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
