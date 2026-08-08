# Multisim MCP + Skills

[中文](README.md) | [English (current)](README.en.md)

An unofficial local MCP server that lets an AI agent generate editable NI
Multisim circuits from constrained SPICE input, run experiments, export data,
and create reproducible reports.

> Current target: `v0.1.0-alpha.2`. This project is not affiliated with or
> endorsed by NI. A locally installed and licensed Multisim 14+ environment is
> required. The current COM worker uses 32-bit Python.

[PyPI package](https://pypi.org/project/multisim-mcp/) ·
[Official MCP Registry entry](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.yxy050208%2Fmultisim-mcp) ·
[GitHub Release](https://github.com/yxy050208/multisim-mcp/releases/tag/v0.1.0-alpha.2)

## End-to-end workflow

`run_circuit_experiment` uses one validated source netlist to:

1. validate the circuit and analysis commands;
2. generate an editable `.ms14` schematic;
3. open and reverse-verify it in real Multisim;
4. export the schematic as PNG;
5. run operating-point, DC, AC, or transient analysis;
6. export raw data, CSV, SVG plots, and command logs; and
7. generate a reproducible Markdown lab report.

Real Multisim 14.3 regressions cover resistor dividers, coupled inductors,
digital truth tables, JK timing, and a combined function-generator/oscilloscope
experiment.

## Capability

Stable:

- local MCP stdio lifecycle and 32-bit runtime diagnostics;
- circuit open/save/enumeration and image/netlist/BOM export;
- DC, AC, transient, waveform injection, and RLC read/write;
- safe SPICE subset execution and raw/CSV/SVG/report generation.

Experimental but verified:

- editable R/L/C, source, B/E/F/G/H, K/T/O/U, semiconductor, switch,
  five-terminal op-amp, and 2-16-terminal generic subcircuit schematics;
- NOT/AND/OR/NAND/NOR/XOR/XNOR and JK preview symbols with real timing data;
- native XFG function-generator and four-channel XSC oscilloscope state.

See [component coverage](docs/COMPONENT_COVERAGE.md) for precise boundaries.

## Public-release asset policy

Project-owned code is MIT licensed. XML templates derived from locally licensed
NI samples are not covered by that code license and must not be uploaded without
a separate redistribution review. The public repository should contain the
extractor, manifest, code, tests, and documentation—not extracted samples,
decoded designs, type-library dumps, experiment output, or development wheels.

Users generate a local component pack from their own installation:

```powershell
$env:PYTHONPATH = (Resolve-Path .\mcp_server).Path
C:\path\to\python32\python.exe .\tools\bootstrap_local_component_pack.py `
  --output C:\MultisimMcp\component-pack
$env:MULTISIM_MCP_TEMPLATE_DIR = 'C:\MultisimMcp\component-pack'
```

See the [publishing guide](docs/PUBLISHING.md) and
[third-party notices](THIRD_PARTY_NOTICES.md).

## Quick start

Install the published package into a 32-bit Python environment:

```powershell
C:\path\to\python32\python.exe -m pip install "multisim-mcp==0.1.0a2"
C:\path\to\python32\Scripts\multisim-mcp.exe
```

Linux and Docker provide MCP tool discovery and compatibility diagnostics only;
they cannot run Multisim simulations. `runtime_status` reports
`introspection-only` in the container, while every COM automation capability
continues to require the Windows environment described above. The root
`Dockerfile` exists for registries such as Glama to validate the protocol and
tool definitions.

To build a user-local component pack, install from source and run:

```powershell
cd mcp_server
.\setup.ps1 -Python C:\path\to\python32\python.exe
npm install --global electronics-workbench-decoder@0.2.0
.\run_server.ps1
```

MCP client configuration:

```json
{
  "mcpServers": {
    "multisim": {
      "command": "C:\\path\\to\\python32\\python.exe",
      "args": ["-m", "multisim_mcp.server"],
      "env": {
        "MULTISIM_MCP_TEMPLATE_DIR": "C:\\MultisimMcp\\component-pack"
      }
    }
  }
}
```

## Security

Only `op`, `dc`, `ac`, and `tran` are accepted by default. Arbitrary Multisim
command files are opt-in and disabled for normal use. Run this MCP only as a
trusted local stdio service; do not expose it directly to a network.

See [SECURITY.md](SECURITY.md).

## License

Project-owned code is available under the MIT License. NI Multisim, trademarks,
file formats, installed samples, and third-party models remain subject to their
respective terms.
