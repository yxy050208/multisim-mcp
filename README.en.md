# Multisim MCP + Skills

[![Glama MCP server score](https://glama.ai/mcp/servers/yxy050208/multisim-mcp/badges/score.svg)](https://glama.ai/mcp/servers/yxy050208/multisim-mcp)

[中文](README.md) | [English (current)](README.en.md)

An unofficial local MCP server that lets an AI agent generate editable NI
Multisim circuits from constrained SPICE input, run experiments, export data,
and create reproducible reports.

> The current public release is `v0.1.0-alpha.3`. This project is not affiliated
> with or endorsed by NI. A
> locally installed and licensed Multisim 14+ environment is required. The
> current COM worker uses 32-bit Python.

The main branch is progressing through the phased
[1.0 roadmap](docs/ROADMAP_TO_1.0.md). The next public package and Registry
release is planned as `v1.0.0`, without intermediate public versions.

[PyPI package](https://pypi.org/project/multisim-mcp/) ·
[Official MCP Registry entry](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.yxy050208%2Fmultisim-mcp) ·
[GitHub Release](https://github.com/yxy050208/multisim-mcp/releases/tag/v0.1.0-alpha.3)

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
C:\path\to\python32\python.exe -m pip install "multisim-mcp==0.1.0a3"
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

The `v0.1.0-alpha.3` source includes diagnostic and configuration commands.
By default, they do not start Multisim or modify an existing client
configuration:

```powershell
C:\path\to\python32\Scripts\multisim-mcp.exe doctor --lang en
C:\path\to\python32\Scripts\multisim-mcp.exe doctor --lang en --connect
C:\path\to\python32\Scripts\multisim-mcp.exe --json doctor

# Print a Claude Desktop JSON fragment.
C:\path\to\python32\Scripts\multisim-mcp.exe config `
  --client claude-desktop `
  --python C:\path\to\python32\python.exe `
  --template-dir C:\MultisimMcp\component-pack

# Print a Codex config.toml fragment.
C:\path\to\python32\Scripts\multisim-mcp.exe config `
  --client codex `
  --python C:\path\to\python32\python.exe `
  --template-dir C:\MultisimMcp\component-pack
```

The generator prints a copy-pasteable fragment by default. `--output` writes a
new file and refuses to replace one unless `--force` is also present. It never
merges into a live Claude Desktop or Codex configuration automatically.

Manual MCP client configuration:

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
