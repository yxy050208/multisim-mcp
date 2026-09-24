"""Transactional filesystem pipeline for complete Multisim experiments.

The pipeline owns preflight, staging, report generation, atomic publication,
rollback, and resource registration.  Multisim-specific schematic/simulation
executors are injected, so this module imports neither MCP nor COM state.
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import shutil
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .component_adapters import expand_component_adapters
from .digital_observation import build_digital_observation_evidence
from .design_verification import DesignRequirement, verify_requirements
from .experiment_resources import experiment_id_for_output_dir, register_experiment
from .formal_report import export_formal_reports
from .job_engine import output_lease
from .safety import validate_analysis_commands, validate_spice_netlist
from .spice_raw import parse_raw, plot_svg
from .spice_provenance import audit_spice_compatibility
from .simulation_approvals import validate_experiment_approval_provenance
from .workspace_manifest import DIRECTORY_MANIFEST_NAME, write_directory_manifest


Checkpoint = Callable[[str, int, str], None]
CancellationProbe = Callable[[], bool]
Executor = Callable[..., Mapping[str, Any]]
ReportExporter = Callable[[Path, str], Mapping[str, Any]]
ResourceRegistrar = Callable[[str], Mapping[str, Any]]

_MULTISIM_SCHEMATIC_ARTIFACT_NAMES = (
    "circuit.ms14",
    "circuit.ms14.xml",
    "schematic.png",
)
_COMMON_ARTIFACT_NAMES = (
    "data.csv",
    "result.raw",
    "run.log",
    "run.txt",
    "circuit.cir",
    "plot.svg",
    "report.md",
    "spice-compatibility.json",
    "digital-observation.json",
    "report.zh-CN.html",
    "report.en.html",
    "report.zh-CN.pdf",
    "report.en.pdf",
    "manifest.json",
    DIRECTORY_MANIFEST_NAME,
)
_BASE_ARTIFACT_NAMES = (*_MULTISIM_SCHEMATIC_ARTIFACT_NAMES, *_COMMON_ARTIFACT_NAMES)

_ARTIFACT_ROLES = {
    "circuit.ms14": "schematic",
    "circuit.ms14.xml": "schematic-source",
    "schematic.png": "schematic-image",
    "data.csv": "simulation-data",
    "result.raw": "simulation-data",
    "run.log": "log",
    "run.txt": "analysis-commands",
    "circuit.cir": "netlist",
    "plot.svg": "plot",
    "report.md": "report",
    "report.zh-CN.html": "report",
    "report.en.html": "report",
    "report.zh-CN.pdf": "report",
    "report.en.pdf": "report",
    "manifest.json": "reproducibility-manifest",
    "verification.json": "verification",
    "schematic.svg": "schematic-source",
    "backend.json": "backend-metadata",
    "spice-compatibility.json": "spice-compatibility-audit",
    "digital-observation.json": "digital-observation-evidence",
}


def _markdown_text(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    markdown_chars = frozenset("\\`*_{}[]#+!|")
    return "".join(
        f"&#{ord(char)};" if char in markdown_chars else html.escape(char, quote=True)
        for char in text
    )


def _write_experiment_report(
    path: Path,
    title: str,
    netlist: str,
    commands: str,
    schematic: Mapping[str, Any],
    simulation: Mapping[str, Any],
    chart_path: str | None,
    verification: Mapping[str, Any] | None = None,
    compatibility: Mapping[str, Any] | None = None,
    *,
    backend_id: str = "multisim",
    backend_display_name: str = "NI Multisim",
    editable_schematic: bool = True,
) -> None:
    fence_runs = [len(item) for item in re.findall(r"`+", netlist)]
    fence = "`" * max(3, max(fence_runs, default=0) + 1)
    lines = [
        f"# {_markdown_text(title or 'Circuit experiment')}",
        "",
        "## Reproducibility",
        "",
        f"- EDA backend: {_markdown_text(backend_display_name)} (`{_markdown_text(backend_id)}`)",
        f"- Design artifact: {_markdown_text(schematic.get('design', schematic.get('ms14', '')))}",
        f"- Analysis commands: {_markdown_text(commands.replace(chr(10), '; '))}",
        f"- Simulation succeeded: `{simulation.get('success', False)}`",
        f"- Points: `{simulation.get('n_points', 0)}`",
        f"- Columns: {_markdown_text(', '.join(simulation.get('columns', [])))}",
        "",
        "## Circuit netlist",
        "",
        f"{fence}spice",
        netlist.rstrip(),
        fence,
        "",
        "## Artifacts",
        "",
        (
            f"- Editable circuit: {_markdown_text(schematic.get('design', schematic.get('ms14', '')))}"
            if editable_schematic
            else f"- Portable topology diagram: {_markdown_text(schematic.get('design', ''))}"
        ),
    ]
    if schematic.get("image"):
        lines.append(f"- Schematic: ![schematic]({Path(str(schematic['image'])).name})")
    if simulation.get("csv"):
        csv_name = Path(str(simulation["csv"])).name
        if simulation.get("output_dir"):
            csv_name = "data.csv"
        lines.append(f"- Data: `{csv_name}`")
    if chart_path:
        lines.append(f"- Plot: ![plot]({Path(chart_path).name})")
    lines.extend(["", "## Data preview", ""])
    columns = simulation.get("columns") or []
    rows = simulation.get("rows") or []
    if columns and rows:
        lines.append("| " + " | ".join(_markdown_text(item) for item in columns) + " |")
        lines.append("| " + " | ".join("---" for _ in columns) + " |")
        for row in rows[:12]:
            lines.append(
                "| "
                + " | ".join(
                    f"{value:.8g}"
                    if isinstance(value, (int, float))
                    else _markdown_text(value)
                    for value in row
                )
                + " |"
            )
    else:
        lines.append("No parsed simulation rows were produced.")
    measurements = simulation.get("measurements") or []
    if measurements:
        lines.extend(
            [
                "",
                "## Automatic measurements",
                "",
                "| signal | first | last | min | max | mean |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in measurements:
            lines.append(
                "| {column} | {first:.8g} | {last:.8g} | {min:.8g} | "
                "{max:.8g} | {mean:.8g} |".format(
                    **{**item, "column": _markdown_text(item.get("column", ""))}
                )
            )
    if verification is not None:
        counts = verification.get("counts", {})
        lines.extend(
            [
                "",
                "## Design requirement verification",
                "",
                "- Overall status: "
                f"`{_markdown_text(verification.get('overall_status', 'unverified'))}`",
                f"- PASS: `{int(counts.get('pass', 0))}`",
                f"- FAIL: `{int(counts.get('fail', 0))}`",
                f"- Unverified: `{int(counts.get('unverified', 0))}`",
                "",
                "| requirement | metric | value | unit | verdict | theory error |",
                "| --- | --- | ---: | --- | --- | ---: |",
            ]
        )
        for item in verification.get("requirements", []):
            measurement = item.get("measurement") or {}
            value = measurement.get("value")
            comparison = item.get("comparison") or {}
            error = comparison.get("relative_error_percent")
            lines.append(
                "| {identifier} | {metric} | {value} | {unit} | {status} | {error} |".format(
                    identifier=_markdown_text(item.get("id", "")),
                    metric=_markdown_text(item.get("metric", "")),
                    value=f"{value:.8g}" if isinstance(value, (int, float)) else "—",
                    unit=_markdown_text(measurement.get("unit", "")),
                    status=_markdown_text(item.get("status", "unverified")),
                    error=f"{error:.6g}%" if isinstance(error, (int, float)) else "—",
                )
            )
    lines.extend(
        [
            "",
            "## Diagnostics",
            "",
            f"- Timed out: `{simulation.get('timed_out', False)}`",
            f"- Last error: {_markdown_text(simulation.get('last_error', ''))}",
            "",
            "> Generated by Multisim MCP using the selected EDA backend. Verify safety-critical "
            "results independently.",
        ]
    )
    if compatibility is not None:
        summary = compatibility.get("summary", {})
        dialect = compatibility.get("dialect", {})
        backend = compatibility.get("backend", {})
        lines.extend(
            [
                "",
                "## SPICE compatibility and model provenance",
                "",
                f"- Declared/inferred dialect: `{_markdown_text(dialect.get('name', 'unknown'))}` "
                f"(source: `{_markdown_text(dialect.get('source', 'unknown'))}`)",
                f"- Static compatibility: `{_markdown_text(backend.get('compatibility_status', 'unknown'))}`",
                f"- Solver version: `{_markdown_text(backend.get('solver_version') or 'not captured')}`",
                f"- Model count: `{int(summary.get('model_count', 0))}`",
                f"- Unknown model licenses: `{int(summary.get('unknown_license_count', 0))}`",
                f"- Provenance complete: `{bool(summary.get('provenance_complete', False))}`",
                "- Machine-readable evidence: `spice-compatibility.json`",
            ]
        )
    observation = simulation.get("digital_observation")
    if isinstance(observation, Mapping) and observation.get("overall_status") != "not-applicable":
        lines.extend(
            [
                "",
                "## Digital output observation evidence",
                "",
                f"- Overall status: `{_markdown_text(observation.get('overall_status', 'unobserved'))}`",
                f"- Backend scope: `{_markdown_text(observation.get('scope', 'backend-output'))}`",
                "- Machine-readable evidence: `digital-observation.json`",
                "",
                "| component | pin | net | status | claim | raw column |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        routing = observation.get("routing")
        if isinstance(routing, Mapping) and routing.get("mode") == "explicit-rerun":
            lines.extend(
                [
                    f"- Explicit fallback recommendation: rerun with `{_markdown_text(routing.get('recommended_backend', 'ngspice'))}`",
                    f"- Automatic backend switch: `{bool(routing.get('automatic_switch', False))}`",
                    f"- Reason: {_markdown_text(routing.get('reason', ''))}",
                ]
            )
        for item in observation.get("signals", []):
            if not isinstance(item, Mapping):
                continue
            lines.append(
                "| {component} | {pin} | {net} | {status} | {claim} | {column} |".format(
                    component=_markdown_text(item.get("component", "")),
                    pin=_markdown_text(item.get("pin", "")),
                    net=_markdown_text(item.get("net", "")),
                    status=_markdown_text(item.get("status", "")),
                    claim=_markdown_text(item.get("claim", "")),
                    column=_markdown_text(item.get("raw_column") or "—"),
                )
            )
    model_warnings = schematic.get("build", {}).get("model_warnings", [])
    if model_warnings:
        lines.extend(["", "## Native model notes", ""])
        lines.extend(f"- {_markdown_text(item)}" for item in model_warnings)
    path.write_text("\n".join(lines), encoding="utf-8")


def _published_view(value: object, stage: Path, root: Path) -> object:
    if isinstance(value, dict):
        return {key: _published_view(item, stage, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_published_view(item, stage, root) for item in value]
    if isinstance(value, str):
        try:
            relative = Path(value).resolve().relative_to(stage.resolve())
        except (OSError, ValueError):
            return value
        return str(root / relative)
    return value


class MultisimExperimentPipeline:
    """Run and atomically publish a complete local Multisim experiment."""

    def __init__(
        self,
        schematic_executor: Executor,
        simulation_executor: Executor,
        *,
        report_exporter: ReportExporter = export_formal_reports,
        resource_registrar: ResourceRegistrar = register_experiment,
        backend_id: str = "multisim",
        backend_display_name: str = "NI Multisim Automation API",
        schematic_artifact_names: tuple[str, ...] = _MULTISIM_SCHEMATIC_ARTIFACT_NAMES,
        design_filename: str = "circuit.ms14",
        editable_schematic: bool = True,
    ) -> None:
        for name, dependency in (
            ("schematic_executor", schematic_executor),
            ("simulation_executor", simulation_executor),
            ("report_exporter", report_exporter),
            ("resource_registrar", resource_registrar),
        ):
            if not callable(dependency):
                raise ValueError(f"{name} must be callable")
        self._schematic_executor = schematic_executor
        self._simulation_executor = simulation_executor
        self._report_exporter = report_exporter
        self._resource_registrar = resource_registrar
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", backend_id):
            raise ValueError("backend_id must be a stable lowercase identifier")
        if not backend_display_name.strip():
            raise ValueError("backend_display_name must not be empty")
        if not schematic_artifact_names or design_filename not in schematic_artifact_names:
            raise ValueError("schematic artifacts must contain design_filename")
        if any(Path(name).name != name or name in {".", ".."} for name in schematic_artifact_names):
            raise ValueError("schematic artifact names must be plain file names")
        self._backend_id = backend_id
        self._backend_display_name = backend_display_name.strip()
        self._schematic_artifact_names = tuple(schematic_artifact_names)
        self._design_filename = design_filename
        self._editable_schematic = editable_schematic

    def run(
        self,
        netlist: str,
        commands: str,
        output_dir: str,
        title: str = "Multisim experiment",
        timeout: float = 120.0,
        max_points: int = 2000,
        overwrite: bool = False,
        checkpoint: Checkpoint | None = None,
        cancel_requested: CancellationProbe | None = None,
        owner: str | None = None,
        requirements: list[DesignRequirement] | None = None,
        theoretical_values: dict[str, float] | None = None,
        model_references: list[Mapping[str, Any]] | None = None,
        declared_dialect: str | None = None,
        approval_provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not output_dir.strip():
            raise ValueError("output_dir must not be empty")
        output_path = Path(output_dir).expanduser().resolve()
        if output_path == Path(output_path.anchor):
            raise ValueError("output_dir must not be a filesystem root")
        lease_owner = owner or f"sync-{uuid.uuid4().hex}"
        with output_lease(str(output_path), lease_owner):
            return self._run_unlocked(
                netlist,
                commands,
                output_path,
                title,
                timeout,
                max_points,
                overwrite,
                checkpoint,
                cancel_requested,
                requirements,
                theoretical_values,
                model_references,
                declared_dialect,
                approval_provenance,
            )

    def _run_unlocked(
        self,
        netlist: str,
        commands: str,
        root: Path,
        title: str,
        timeout: float,
        max_points: int,
        overwrite: bool,
        checkpoint: Checkpoint | None,
        cancel_requested: CancellationProbe | None,
        requirements: list[DesignRequirement] | None,
        theoretical_values: dict[str, float] | None,
        model_references: list[Mapping[str, Any]] | None,
        declared_dialect: str | None,
        approval_provenance: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        def notify(stage_name: str, progress: int, message: str) -> None:
            if checkpoint is not None:
                checkpoint(stage_name, progress, message)
            if cancel_requested is not None and cancel_requested():
                raise InterruptedError("Experiment cancellation requested")

        notify("preflight", 3, "Validating netlist, commands, and output destinations")
        accepted = validate_analysis_commands(commands)
        validate_spice_netlist(netlist)
        validate_spice_netlist(expand_component_adapters(netlist))
        root.mkdir(parents=True, exist_ok=True)
        manifest_names = (
            *self._schematic_artifact_names,
            *_COMMON_ARTIFACT_NAMES,
            *(("verification.json",) if requirements is not None else ()),
        )
        destinations = {name: root / name for name in manifest_names}
        for path in destinations.values():
            if path.exists() and not path.is_file():
                raise ValueError(f"Artifact destination is not a regular file: {path}")
            if path.exists() and not overwrite:
                raise FileExistsError(f"Refusing to overwrite existing artifact: {path}")
        stale_verification = root / "verification.json"
        if requirements is None and stale_verification.exists():
            if not stale_verification.is_file():
                raise ValueError(
                    f"Artifact destination is not a regular file: {stale_verification}"
                )
            if not overwrite:
                raise FileExistsError(
                    "Refusing to retain stale verification artifact: "
                    f"{stale_verification}"
                )

        stage = root.parent / f".{root.name}.multisim-mcp-{uuid.uuid4().hex}"
        stage.mkdir(parents=False, exist_ok=False)
        try:
            schematic_action = "Building editable schematic" if self._editable_schematic else "Rendering portable connectivity diagram"
            notify("schematic", 10, schematic_action)
            design_path = stage / self._design_filename
            image_path = stage / "schematic.png"
            report_path = stage / "report.md"
            chart_path = stage / "plot.svg"
            schematic = dict(
                self._schematic_executor(
                    netlist,
                    str(design_path),
                    probe_nets=[],
                    include_experimental_probes=False,
                    open_after_build=True,
                    image_path=str(image_path),
                    overwrite=False,
                )
            )
            notify("simulation", 42, f"Running validated {self._backend_display_name} analysis")
            simulation = dict(
                self._simulation_executor(
                    netlist,
                    "\n".join(accepted),
                    output_dir=str(stage),
                    timeout=timeout,
                    max_points=max_points,
                    unsafe_commands=False,
                    overwrite=False,
                    cancel_requested=cancel_requested,
                    heartbeat=lambda: notify(
                        "simulation", 58, f"Waiting for the {self._backend_display_name} analysis engine"
                    ),
                )
            )
            if not schematic.get("success") or not simulation.get("success"):
                raise RuntimeError(
                    f"{self._backend_display_name} experiment did not produce a successful diagram and simulation"
                )

            notify("plot_and_report", 72, "Generating plot and reproducible report")
            parsed = parse_raw(str(simulation["raw"]))
            if not parsed["rows"] or len(parsed["columns"]) < 2:
                raise ValueError(
                    "At least two populated raw-data columns are required for a plot"
                )
            plot_step = max(1, math.ceil(len(parsed["rows"]) / 5000))
            plot_rows = parsed["rows"][::plot_step]
            x_values = [row[0] for row in plot_rows]
            series = [
                {
                    "name": parsed["columns"][index],
                    "x": x_values,
                    "y": [row[index] for row in plot_rows],
                }
                for index in range(1, len(parsed["columns"]))
                if all(len(row) > index for row in plot_rows)
            ]
            if not series:
                raise ValueError("The simulation produced no complete Y series for plotting")
            plot_svg(
                str(chart_path),
                series,
                title=title,
                x_label=parsed["columns"][0],
                y_label="value",
            )

            report_schematic = {
                **schematic,
                "design": self._design_filename,
                "image": "schematic.png",
            }
            if self._backend_id == "multisim":
                report_schematic["ms14"] = "circuit.ms14"
            report_simulation = {
                **simulation,
                "csv": "data.csv",
                "output_dir": str(root),
            }
            native_presence = {}
            schematic_verification = schematic.get("verification")
            if isinstance(schematic_verification, Mapping):
                raw_presence = schematic_verification.get("native_netlist_components")
                if isinstance(raw_presence, Mapping):
                    native_presence = dict(raw_presence)
            digital_observation = build_digital_observation_evidence(
                netlist,
                parsed["columns"],
                backend_id=self._backend_id,
                native_component_presence=native_presence,
            )
            simulation["digital_observation"] = digital_observation
            report_simulation["digital_observation"] = digital_observation
            (stage / "digital-observation.json").write_text(
                json.dumps(
                    digital_observation, ensure_ascii=False, indent=2, sort_keys=True
                )
                + "\n",
                encoding="utf-8",
            )
            verification: dict[str, Any] | None = None
            if requirements is not None:
                notify("verification", 68, "Evaluating explicit design requirements")
                verification = verify_requirements(
                    parsed, requirements, theoretical_values
                )
                (stage / "verification.json").write_text(
                    json.dumps(
                        verification, ensure_ascii=False, indent=2, sort_keys=True
                    )
                    + "\n",
                    encoding="utf-8",
                )
            solver_output = ""
            solver_log = stage / "run.log"
            if solver_log.is_file() and solver_log.stat().st_size <= 262_144:
                solver_output = solver_log.read_text(
                    encoding="utf-8", errors="replace"
                )
            executed_netlist = netlist
            executed_netlist_path = stage / "circuit.cir"
            if executed_netlist_path.is_file() and executed_netlist_path.stat().st_size <= 4_000_000:
                executed_netlist = executed_netlist_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            compatibility = audit_spice_compatibility(
                netlist,
                backend_id=self._backend_id,
                model_references=model_references,
                declared_dialect=declared_dialect,
                solver_output=solver_output,
                executed_netlist=executed_netlist,
            )
            (stage / "spice-compatibility.json").write_text(
                json.dumps(
                    compatibility, ensure_ascii=False, indent=2, sort_keys=True
                )
                + "\n",
                encoding="utf-8",
            )
            _write_experiment_report(
                report_path,
                title,
                netlist,
                "\n".join(accepted),
                report_schematic,
                report_simulation,
                "plot.svg",
                verification,
                compatibility,
                backend_id=self._backend_id,
                backend_display_name=self._backend_display_name,
                editable_schematic=self._editable_schematic,
            )
            stable_experiment_id = experiment_id_for_output_dir(root)
            self._report_exporter(stage, stable_experiment_id)
            manifest_metadata: dict[str, Any] = {
                "title": title,
                "verified": requirements is not None,
                "requirement_count": len(requirements or ()),
                "backend_id": self._backend_id,
                "backend_display_name": self._backend_display_name,
                "editable_schematic": self._editable_schematic,
                "spice_dialect": compatibility["dialect"]["name"],
                "spice_compatibility_status": compatibility["backend"][
                    "compatibility_status"
                ],
                "model_fingerprint_sha256": compatibility[
                    "model_fingerprint_sha256"
                ],
            }
            if approval_provenance is not None:
                manifest_metadata["approval_provenance"] = (
                    validate_experiment_approval_provenance(approval_provenance)
                )
            write_directory_manifest(
                stage,
                directory_kind="experiment",
                entity_id=stable_experiment_id,
                state="succeeded",
                artifacts={
                    name: _ARTIFACT_ROLES.get(name, "artifact")
                    for name in manifest_names
                    if name != DIRECTORY_MANIFEST_NAME
                },
                metadata=manifest_metadata,
            )

            missing = [
                str(stage / name)
                for name in manifest_names
                if not (stage / name).is_file() or (stage / name).stat().st_size <= 0
            ]
            if missing:
                raise RuntimeError(
                    "Incomplete experiment artifact set: " + ", ".join(missing)
                )

            notify("publish", 88, "Publishing the complete artifact transaction")
            self._publish(
                stage,
                root,
                destinations,
                stale_verification,
                remove_stale_verification=requirements is None,
            )
            schematic = _published_view(schematic, stage, root)
            simulation = _published_view(simulation, stage, root)
            assert isinstance(schematic, dict) and isinstance(simulation, dict)
            for key, filename in {
                "raw": "result.raw",
                "csv": "data.csv",
                "netlist": "circuit.cir",
                "commands": "run.txt",
                "log": "run.log",
            }.items():
                simulation[key] = str(root / filename)
            simulation["artifacts"] = [
                str(root / name)
                for name in (
                    "data.csv",
                    "result.raw",
                    "run.log",
                    "run.txt",
                    "circuit.cir",
                    "spice-compatibility.json",
                    "digital-observation.json",
                )
            ]
            simulation["output_dir"] = str(root)
            notify("register", 97, "Registering safe experiment resource handles")
            registered = self._resource_registrar(str(root))
            result: dict[str, Any] = {
                "success": True,
                "experiment_id": registered["experiment_id"],
                "resources": registered["resources"],
                "schematic": schematic,
                "simulation": simulation,
                "report": str(root / "report.md"),
                "plot": str(root / "plot.svg"),
                "output_dir": str(root),
                "backend_id": self._backend_id,
                "spice_compatibility": compatibility,
                "spice_compatibility_path": str(
                    root / "spice-compatibility.json"
                ),
            }
            if verification is not None:
                result["verification"] = verification
                result["verification_path"] = str(root / "verification.json")
            notify("complete", 100, "Experiment completed")
            return result
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    @staticmethod
    def _publish(
        stage: Path,
        root: Path,
        destinations: Mapping[str, Path],
        stale_verification: Path,
        *,
        remove_stale_verification: bool,
    ) -> None:
        transaction_id = uuid.uuid4().hex
        prepared: dict[str, Path] = {}
        backup_dir = stage / ".backups"
        backup_dir.mkdir()
        backups: dict[str, Path] = {}
        for name, destination in destinations.items():
            temporary = root / f".{name}.{transaction_id}.tmp"
            shutil.copy2(stage / name, temporary)
            prepared[name] = temporary
            if destination.exists():
                backup = backup_dir / name
                shutil.copy2(destination, backup)
                backups[name] = backup
        if remove_stale_verification and stale_verification.is_file():
            stale_backup = backup_dir / "verification.json"
            shutil.copy2(stale_verification, stale_backup)
            backups["verification.json"] = stale_backup

        published: list[str] = []
        stale_removed = False
        try:
            for name, destination in destinations.items():
                os.replace(prepared[name], destination)
                published.append(name)
            if remove_stale_verification and stale_verification.is_file():
                stale_verification.unlink()
                stale_removed = True
        except Exception:
            for name in reversed(published):
                destination = destinations[name]
                backup = backups.get(name)
                if backup and backup.exists():
                    os.replace(backup, destination)
                elif destination.exists():
                    destination.unlink()
            stale_backup = backups.get("verification.json")
            if stale_removed and stale_backup and stale_backup.exists():
                os.replace(stale_backup, stale_verification)
            raise
        finally:
            for temporary in prepared.values():
                temporary.unlink(missing_ok=True)


__all__ = ["MultisimExperimentPipeline"]
