"""End-to-end MCP test for the Multisim server.

Run with the same 32-bit Python used by the MCP server:

    tools\\python32\\python.exe mcp_server\\tests\\e2e_mcp_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
OUT = REPO / "analysis" / "out" / "e2e_mcp"

SAMPLES = REPO / "analysis" / "samples"
RLC_SAMPLE = SAMPLES / "api_toolkit" / "RLC Values" / "RLC Values.ms14"
LPF_SAMPLE = SAMPLES / "LowPassFilter.ms14"


def summarize_rows(payload: dict, limit: int = 8) -> dict:
    rows = payload.get("rows", [])
    out = dict(payload)
    out["rows"] = [row[:limit] for row in rows[:3]]
    if rows:
        out["row_lengths"] = [len(row) for row in rows[:3]]
    return out


async def call(session: Client, name: str, arguments: dict | None = None) -> dict:
    result = await session.call_tool(name, arguments or {})
    text = ""
    if result.is_error:
        text = "".join(item.text or "" for item in result.content)
        raise RuntimeError(f"{name} failed: {text}")
    if result.structured_content is not None:
        return result.structured_content
    for item in result.content:
        if item.type == "text":
            text += item.text or ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {}

    params = StdioServerParameters(
        command=PYTHON,
        args=["-m", "multisim_mcp.server"],
        cwd=str(REPO / "mcp_server"),
    )
    async with Client(stdio_client(params)) as session:
        tools = await session.list_tools()
        report["mcp"] = {
            "protocol": "ok",
            "protocol_version": session.protocol_version,
            "tools": len(tools.tools),
            "tool_names": [t.name for t in tools.tools],
        }

        report["connect"] = await call(session, "connect")

        configured_sample = os.environ.get("MULTISIM_MCP_E2E_SAMPLE")
        sample = (
            Path(configured_sample)
            if configured_sample
            else (RLC_SAMPLE if RLC_SAMPLE.exists() else LPF_SAMPLE)
        )
        if not sample.exists():
            raise FileNotFoundError(
                "A redistributable Multisim E2E fixture is not bundled. Set "
                "MULTISIM_MCP_E2E_SAMPLE to a local .ms14 design with RLC "
                "components and at least one simulation output."
            )
        report["open_circuit"] = await call(
            session, "open_circuit", {"path": str(sample)}
        )
        report["circuit_info"] = await call(session, "circuit_info")
        report["enum_components"] = await call(
            session, "enum_components", {"component_type": 0}
        )
        report["enum_inputs"] = await call(session, "enum_inputs", {"input_type": 0})
        outputs_result = await call(session, "enum_outputs", {"output_type": 0})
        report["enum_outputs"] = outputs_result
        outputs = (
            outputs_result.get("outputs", [])
            if isinstance(outputs_result, dict)
            else []
        )

        all_components = report["enum_components"].get("components", [])
        rlc_components = [c for c in all_components if c[:1] in ("R", "C", "L")][:3]
        for component in rlc_components:
            try:
                report[f"get_rlc_{component}"] = await call(
                    session, "get_rlc_value", {"component_name": component}
                )
            except RuntimeError as exc:
                report[f"get_rlc_{component}"] = {"error": str(exc)}

        set_target = rlc_components[0] if rlc_components else "R1"
        before = await call(session, "get_rlc_value", {"component_name": set_target})
        report["set_rlc_before"] = before
        report["set_rlc"] = await call(
            session, "set_rlc_value", {"component_name": set_target, "value": 100.0}
        )
        report["set_rlc_after"] = await call(
            session, "get_rlc_value", {"component_name": set_target}
        )

        output_candidates = outputs if isinstance(outputs, list) and outputs else []
        if not output_candidates:
            output_candidates = ["V(Probe_output)", "V(out)", "V(1)"]
        target = output_candidates[0]
        report["target_output"] = target

        report["run_dc"] = summarize_rows(
            await call(session, "run_dc_operating_point", {"output_names": [target]})
        )
        report["run_ac"] = summarize_rows(
            await call(session, "run_ac_sweep", {"output_names": [target]})
        )
        report["run_transient"] = summarize_rows(
            await call(
                session,
                "run_transient",
                {
                    "output_name": target,
                    "sample_rate": 100_000.0,
                    "num_samples": 100,
                    "duration": 0.001,
                    "timeout": 30.0,
                    "max_points": 200,
                },
            )
        )

        report["generate_report"] = await call(
            session,
            "generate_report",
            {
                "output_path": str(OUT / "e2e_report.md"),
                "title": "RLC Values E2E Report",
                "analyses": [
                    report["run_dc"],
                    report["run_ac"],
                    report["run_transient"],
                ],
                "include_netlist": True,
                "include_bom": True,
                "include_image": True,
            },
        )

        export = {}
        for key, tool, args in [
            (
                "netlist",
                "report_netlist",
                {"path": str(OUT / "e2e_circuit.netlist"), "fmt": 0},
            ),
            ("bom", "report_bom", {"path": str(OUT / "e2e_circuit.bom"), "fmt": 0}),
            (
                "image",
                "get_circuit_image",
                {"path": str(OUT / "e2e_circuit.png"), "image_format": 2},
            ),
        ]:
            try:
                export[key] = await call(session, tool, args)
            except RuntimeError as exc:
                export[key] = {"error": str(exc)}
        report["export"] = export

        try:
            report["save"] = await call(
                session, "save_circuit", {"path": str(OUT / "e2e_saved.ms14")}
            )
        except RuntimeError as exc:
            report["save"] = {"error": str(exc)}

        report["new_circuit"] = await call(session, "new_circuit")
        report["run_spice_netlist"] = await call(
            session,
            "run_spice_netlist",
            {
                "netlist": (
                    "* e2e resistor divider\n"
                    "VIN vin 0 DC 10\n"
                    "R1 vin vout 1k\n"
                    "R2 vout 0 1k\n"
                    ".end\n"
                ),
                "commands": "dc VIN 0 10 0.1",
                "output_dir": str(OUT / "spice_netlist"),
                "max_points": 101,
                "overwrite": True,
            },
        )

        for key in ("run_dc", "run_ac", "run_transient"):
            if not report[key].get("ready"):
                raise AssertionError(
                    f"{key} did not produce ready output: {report[key]}"
                )
        if not report["run_spice_netlist"].get("success"):
            raise AssertionError(
                f"run_spice_netlist failed: {report['run_spice_netlist']}"
            )
        if report["run_spice_netlist"].get("n_points") != 101:
            raise AssertionError("DC sweep did not produce the expected 101 points")

        report["disconnect"] = await call(session, "disconnect")

    with open(OUT / "e2e_mcp_results.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
