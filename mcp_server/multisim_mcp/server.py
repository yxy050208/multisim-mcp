"""MCP stdio entry point for Multisim."""

from __future__ import annotations

import math
import os
import html
import re
import shutil
import uuid
from pathlib import Path

import pythoncom
from mcp.server.fastmcp import FastMCP

from multisim_mcp.multisim_client import (
    Ms14Codec,
    MultisimClient,
    runtime_diagnostics,
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
from multisim_mcp.spice_raw import parse_raw, plot_svg, summarize_columns, write_csv


mcp = FastMCP("multisim")
client = MultisimClient()
codec = Ms14Codec()


@mcp.tool()
def connect() -> dict:
    """Connect to the local Multisim Automation API."""
    return client.connect()


@mcp.tool()
def runtime_status() -> dict:
    """Check platform and Python compatibility without starting Multisim."""
    result = runtime_diagnostics()
    paths = template_search_paths()
    required = ("minimal.ms14.xml", "wire.xml", "r_element.xml")
    missing = [
        name for name in required if not any((path / name).is_file() for path in paths)
    ]
    result["schematic_templates_ready"] = not missing
    result["missing_schematic_templates"] = missing
    if missing:
        result["template_setup_hint"] = (
            "Run tools/bootstrap_local_component_pack.py and set "
            "MULTISIM_MCP_TEMPLATE_DIR."
        )
    return result


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
                "maturity": (
                    "experimental-carrier"
                    if definition.kind in experimental_carriers
                    else "native-verified"
                ),
            }
            for definition in COMPONENT_DEFINITIONS.values()
        ],
        "experimental": [
            "generated voltage probes",
            "E/F/G/H controlled-source carrier symbols",
            "generic carrier artwork for K/O/U/X and derived logic gates",
        ],
        "planned_families": [
            "dedicated symbols for generic carriers",
            "generic subcircuits with more than sixteen terminals",
            "D/T flip-flops, counters, ADC/DAC, and mixed-signal bridges",
            "multimeter, Bode plotter, and logic analyzer adapters",
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
) -> dict:
    if not netlist.strip():
        raise ValueError("netlist must not be empty")
    if len(netlist.encode("utf-8")) > 2_000_000:
        raise ValueError("netlist exceeds the 2 MB safety limit")
    if timeout <= 0 or timeout > 3600:
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

    result = client.run_command_file(command_path, log_path, timeout)
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
) -> dict:
    """Run a SPICE netlist with safe ``op``, ``dc``, ``ac``, or ``tran`` commands.

    The default path rejects scripting, shell escapes, file commands, and
    arbitrary command sourcing. Unrestricted engine commands require both the
    ``unsafe_commands`` argument and an explicit server-side environment flag.
    """
    return _run_spice_netlist_impl(
        netlist,
        commands,
        output_dir,
        timeout,
        max_points,
        unsafe_commands,
        overwrite,
    )


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
            "diode/BJT/MOSFET/JFET/MESFET/switch devices, OPAMP5, K/T/O/U, "
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
            native_components[spec.refdes] = True if spec.kind in {"OSC6", "XFG3"} else spec.refdes in enumerated_components if spec.kind == "K" else any(
                re.search(
                    rf"(?<![A-Za-z0-9_]){re.escape(candidate)}(?![A-Za-z0-9_])",
                    exported,
                )
                for candidate in candidates
            )
        result["verification"]["native_netlist_components"] = native_components
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


@mcp.tool()
def create_schematic_from_netlist(
    netlist: str,
    output_ms14: str,
    probe_nets: list[str] | None = None,
    include_experimental_probes: bool = False,
    open_after_build: bool = True,
    image_path: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Create an editable Multisim schematic from a limited SPICE netlist.

    Version 0.1 supports RLC components, scalar/waveform voltage and current
    sources, B/E/F/G/H/T primitives, modeled diodes, BJT, MOSFET, JFET, MESFET,
    voltage switches, OPAMP5, generic two-to-five-terminal X subcircuits, ground,
    named nets, wiring, and deterministic layout. Several extended families use
    verified generic carrier symbols while retaining native SPICE behavior.
    NOT/AND/OR/JK digital parts are schematic-preview maturity.
    Generated schematic probes
    remain experimental; the high-level experiment tool obtains authoritative
    data from the same netlist through Multisim's command engine.
    """
    return _create_schematic_impl(
        netlist,
        output_ms14,
        probe_nets,
        include_experimental_probes,
        open_after_build,
        image_path,
        overwrite,
    )


def _markdown_text(value: object) -> str:
    """Render an untrusted scalar without creating Markdown/HTML structure."""
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
    schematic: dict,
    simulation: dict,
    chart_path: str | None,
) -> None:
    fence_runs = [len(item) for item in re.findall(r"`+", netlist)]
    fence = "`" * max(3, max(fence_runs, default=0) + 1)
    lines = [
        f"# {_markdown_text(title or 'Multisim experiment')}",
        "",
        "## Reproducibility",
        "",
        f"- Multisim design: {_markdown_text(schematic.get('ms14', ''))}",
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
        f"- Editable circuit: {_markdown_text(schematic.get('ms14', ''))}",
    ]
    if schematic.get("image"):
        lines.append(f"- Schematic: ![schematic]({Path(schematic['image']).name})")
    if simulation.get("csv"):
        csv_name = Path(simulation["csv"]).name
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
                    f"{value:.8g}" if isinstance(value, (int, float)) else _markdown_text(value)
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
    lines.extend(
        [
            "",
            "## Diagnostics",
            "",
            f"- Timed out: `{simulation.get('timed_out', False)}`",
            f"- Last error: {_markdown_text(simulation.get('last_error', ''))}",
            "",
            "> Generated by the unofficial Multisim MCP. Verify safety-critical "
            "results independently.",
        ]
    )
    model_warnings = schematic.get("build", {}).get("model_warnings", [])
    if model_warnings:
        lines.extend(["", "## Native model notes", ""])
        lines.extend(f"- {_markdown_text(item)}" for item in model_warnings)
    path.write_text("\n".join(lines), encoding="utf-8")


@mcp.tool()
def run_circuit_experiment(
    netlist: str,
    commands: str,
    output_dir: str,
    title: str = "Multisim experiment",
    timeout: float = 120.0,
    max_points: int = 2000,
    overwrite: bool = False,
) -> dict:
    """Create a schematic, run a safe Multisim analysis, and export a report.

    This is the recommended high-level workflow for agents. The schematic and
    simulation share the same source netlist; simulation data comes directly
    from Multisim's engine and is exported as raw and CSV artifacts.
    """
    accepted = validate_analysis_commands(commands)
    validate_spice_netlist(netlist)
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_names = (
        "circuit.ms14",
        "circuit.ms14.xml",
        "schematic.png",
        "data.csv",
        "result.raw",
        "run.log",
        "run.txt",
        "circuit.cir",
        "plot.svg",
        "report.md",
    )
    destinations = {name: root / name for name in manifest_names}
    for path in destinations.values():
        if path.exists() and not path.is_file():
            raise ValueError(f"Artifact destination is not a regular file: {path}")
        if path.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing artifact: {path}")

    stage = root.parent / f".{root.name}.multisim-mcp-{uuid.uuid4().hex}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        design_path = stage / "circuit.ms14"
        image_path = stage / "schematic.png"
        report_path = stage / "report.md"
        chart_path = stage / "plot.svg"
        schematic = _create_schematic_impl(
            netlist,
            str(design_path),
            probe_nets=[],
            include_experimental_probes=False,
            open_after_build=True,
            image_path=str(image_path),
            overwrite=False,
        )
        simulation = _run_spice_netlist_impl(
            netlist,
            "\n".join(accepted),
            output_dir=str(stage),
            timeout=timeout,
            max_points=max_points,
            unsafe_commands=False,
            overwrite=False,
        )
        if not schematic.get("success") or not simulation.get("success"):
            raise RuntimeError("Multisim did not produce a successful schematic and simulation")

        parsed = parse_raw(simulation["raw"])
        if not parsed["rows"] or len(parsed["columns"]) < 2:
            raise ValueError("At least two populated raw-data columns are required for a plot")
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
        chart = plot_svg(
            str(chart_path),
            series,
            title=title,
            x_label=parsed["columns"][0],
            y_label="value",
        )

        report_schematic = {**schematic, "ms14": "circuit.ms14", "image": "schematic.png"}
        report_simulation = {
            **simulation,
            "csv": "data.csv",
            "output_dir": str(root),
        }
        _write_experiment_report(
            report_path,
            title,
            netlist,
            "\n".join(accepted),
            report_schematic,
            report_simulation,
            "plot.svg",
        )

        missing = [
            str(stage / name)
            for name in manifest_names
            if not (stage / name).is_file() or (stage / name).stat().st_size <= 0
        ]
        if missing:
            raise RuntimeError("Incomplete experiment artifact set: " + ", ".join(missing))

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

        published: list[str] = []
        try:
            for name, destination in destinations.items():
                os.replace(prepared[name], destination)
                published.append(name)
        except Exception:
            for name in reversed(published):
                destination = destinations[name]
                backup = backups.get(name)
                if backup and backup.exists():
                    os.replace(backup, destination)
                elif destination.exists():
                    destination.unlink()
            raise
        finally:
            for temporary in prepared.values():
                if temporary.exists():
                    temporary.unlink()

        def published_view(value: object) -> object:
            if isinstance(value, dict):
                return {key: published_view(item) for key, item in value.items()}
            if isinstance(value, list):
                return [published_view(item) for item in value]
            if isinstance(value, str):
                try:
                    relative = Path(value).resolve().relative_to(stage.resolve())
                except (OSError, ValueError):
                    return value
                return str(root / relative)
            return value

        schematic = published_view(schematic)
        simulation = published_view(simulation)
        assert isinstance(schematic, dict) and isinstance(simulation, dict)
        for key, filename in {
            "raw": "result.raw",
            "csv": "data.csv",
            "netlist": "circuit.cir",
            "commands": "run.txt",
            "log": "run.log",
        }.items():
            simulation[key] = str(root / filename)
        simulation["artifacts"] = [str(root / name) for name in manifest_names[3:8]]
        simulation["output_dir"] = str(root)
        return {
            "success": True,
            "schematic": schematic,
            "simulation": simulation,
            "report": str(root / "report.md"),
            "plot": str(root / "plot.svg"),
            "output_dir": str(root),
        }
    finally:
        shutil.rmtree(stage, ignore_errors=True)


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
    pythoncom.CoInitialize()
    mcp.run()


if __name__ == "__main__":
    main()
