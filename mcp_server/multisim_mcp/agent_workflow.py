"""Deterministic planning for an engineering Agent task chain.

The planner creates a reviewable graph for the ARTEX-style Multisim
Workbench.  It intentionally does not call a model or an EDA backend.  The
existing planning, compilation, layout, experiment, optimization, and report
services will be connected to these nodes by a later execution adapter.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from .agent_workflow_contract import AgentTask, ArtifactRef, ApprovalGate, TaskChain


_DEFAULT_PROFILES = {
    "requirements": "planner",
    "design": "architect",
    "components": "component-engineer",
    "compile": "compiler",
    "layout": "layout-engineer",
    "experiment": "verifier",
    "optimization": "optimizer",
    "report": "reporter",
}


def _requirement_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("requirements must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > 12_000 or "\x00" in normalized:
        raise ValueError("requirements exceeds the size limit or contains NUL")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in normalized):
        raise ValueError("requirements contains unsupported control characters")
    return normalized


def _profile(profiles: Mapping[str, Any], role: str) -> str:
    value = profiles.get(role, _DEFAULT_PROFILES[role])
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"model_profiles.{role} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > 128 or any(char in normalized for char in "\r\n\x00"):
        raise ValueError(f"model_profiles.{role} is invalid")
    return normalized


def _artifact(task_id: str, kind: str, label: str, filename: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=f"artifact-{task_id}",
        kind=kind,
        label=label,
        locator=f"workflow/{filename}",
        media_type="application/json",
    )


def build_engineering_task_chain(
    requirements: str,
    *,
    chain_id: str | None = None,
    model_profiles: Mapping[str, Any] | None = None,
    include_optimization: bool = True,
) -> TaskChain:
    """Build a deterministic, reviewable engineering workflow graph.

    The result is a plan only.  It contains no executable paths, bearer tokens,
    raw XML, or simulation commands.  Existing application services remain the
    only components allowed to execute the planned work.
    """

    text = _requirement_text(requirements)
    if not isinstance(include_optimization, bool):
        raise ValueError("include_optimization must be boolean")
    profiles = model_profiles or {}
    if not isinstance(profiles, Mapping):
        raise ValueError("model_profiles must be an object")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    normalized_chain_id = chain_id or f"chain-{digest[:24]}"
    role_profiles = {role: _profile(profiles, role) for role in _DEFAULT_PROFILES}

    tasks: list[AgentTask] = [
        AgentTask(
            task_id="requirements",
            task_type="requirement_review",
            role="requirements",
            title="审查需求与工程约束",
            state="ready",
            inputs={"requirements": text, "requirements_sha256": digest},
            outputs=(_artifact("requirements", "requirements", "Requirements review", "requirements.json"),),
            model_profile=role_profiles["requirements"],
        ),
        AgentTask(
            task_id="design-plan",
            task_type="design_plan",
            role="design",
            title="生成并比较技术方案",
            depends_on=("requirements",),
            inputs={"source_task": "requirements"},
            outputs=(_artifact("design-plan", "design-plan", "Design options", "design-plan.json"),),
            model_profile=role_profiles["design"],
        ),
        AgentTask(
            task_id="component-resolution",
            task_type="component_resolution",
            role="components",
            title="解析元件、额定值和模型来源",
            depends_on=("design-plan",),
            inputs={"source_task": "design-plan"},
            outputs=(_artifact("component-resolution", "component-resolution", "Component resolution", "component-resolution.json"),),
            model_profile=role_profiles["components"],
        ),
        AgentTask(
            task_id="compile-netlist",
            task_type="compile_netlist",
            role="compile",
            title="生成引脚级可执行网表预览",
            depends_on=("component-resolution",),
            inputs={"source_task": "component-resolution"},
            outputs=(_artifact("compile-netlist", "netlist", "Executable netlist preview", "netlist.json"),),
            approval_gate=ApprovalGate(
                gate_id="netlist-review",
                reason="用户确认元件、额定值、模型来源和引脚拓扑后才能继续",
            ),
            model_profile=role_profiles["compile"],
        ),
        AgentTask(
            task_id="layout",
            task_type="layout_validation",
            role="layout",
            title="布局、布线与几何门禁",
            depends_on=("compile-netlist",),
            inputs={"source_task": "compile-netlist"},
            outputs=(_artifact("layout", "layout-validation", "Layout validation", "layout-validation.json"),),
            model_profile=role_profiles["layout"],
        ),
        AgentTask(
            task_id="native-experiment",
            task_type="native_experiment",
            role="experiment",
            title="生成工程并执行 Multisim 原生实验",
            depends_on=("layout",),
            inputs={"source_task": "layout", "analyses": ["op", "dc", "ac", "tran"]},
            outputs=(_artifact("native-experiment", "experiment", "Native experiment evidence", "experiment.json"),),
            approval_gate=ApprovalGate(
                gate_id="native-experiment-review",
                reason="用户确认输出目录、分析命令和验收指标后才能启动 Multisim",
            ),
            model_profile=role_profiles["experiment"],
            retryable=True,
            max_attempts=2,
        ),
    ]

    final_dependency = "native-experiment"
    if include_optimization:
        tasks.append(
            AgentTask(
                task_id="optimization",
                task_type="parameter_optimization",
                role="optimization",
                title="在有界候选中优化参数和布局",
                depends_on=("native-experiment",),
                inputs={"source_task": "native-experiment", "bounded": True},
                outputs=(_artifact("optimization", "optimization", "Optimization evidence", "optimization.json"),),
                model_profile=role_profiles["optimization"],
            )
        )
        final_dependency = "optimization"

    tasks.append(
        AgentTask(
            task_id="report",
            task_type="engineering_report",
            role="report",
            title="汇总电路图、实验数据和工程报告",
            depends_on=(final_dependency,),
            inputs={"source_task": final_dependency},
            outputs=(_artifact("report", "report", "Engineering report", "report.json"),),
            model_profile=role_profiles["report"],
        )
    )

    return TaskChain(
        chain_id=normalized_chain_id,
        name="Multisim engineering workflow",
        tasks=tuple(tasks),
        metadata={
            "workflow_kind": "multisim-engineering",
            "requirements_sha256": digest,
            "include_optimization": include_optimization,
            "execution_enabled": False,
            "approval_required_for_writes": True,
        },
    )


__all__ = ["build_engineering_task_chain"]
