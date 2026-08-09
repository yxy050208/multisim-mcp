# Multisim MCP

<!-- mcp-name: io.github.yxy050208/multisim-mcp -->

Unofficial Windows MCP server for creating NI Multisim schematics, running
experiments, exporting data, and generating reproducible reports.

非官方 Multisim 自动化 MCP：从受限 SPICE 网表生成可编辑电路图，调用本机
Multisim 执行实验，并导出 `.ms14`、原理图、raw、CSV、SVG 和 Markdown 报告。

> Alpha software. This project is not affiliated with NI. Multisim must be
> installed and licensed locally. The current COM worker requires 32-bit Python.

Linux and Docker support MCP initialization, tool discovery, and
`runtime_status` diagnostics only. They do not run Multisim. On an unsupported
platform the server starts in `introspection-only` mode, while every COM-backed
operation fails closed with a Windows compatibility message. The repository's
minimal non-root Docker image exists for registry validation and contains no NI
software, samples, licenses, or extracted templates.

## Current capability

Stable and verified on Multisim 14.3:

- MCP stdio lifecycle and 32-bit runtime diagnostics.
- Open/save circuits and enumerate components, inputs, and outputs.
- DC operating point, AC sweep, single-frequency AC, and transient analysis.
- Input waveform injection and RLC value read/write.
- SPICE netlist execution with safe `op`, `dc`, `ac`, and `tran` commands.
- Netlist, BOM, schematic image, raw data, CSV, SVG, and Markdown export.
- High-level `run_circuit_experiment` workflow.

Experimental:

- Editable schematic generation supports R/L/C, scalar and waveform voltage/current
  sources, B/E/F/G/H controlled sources, T/O/U distributed lines, coupled
  inductors, modeled diodes,
  NPN/PNP BJT, NMOS/PMOS, JFET/MESFET, voltage switches, five-terminal op-amps,
  and generic two-to-sixteen-terminal X subcircuits. Extended families currently
  use verified carrier symbols pending dedicated artwork.
  Native NOT/AND/OR/NAND/NOR/XOR/XNOR and JK flip-flop symbols are available as
  a preview; their open/export and authoritative timing-data paths are verified.
  Native XSC oscilloscope and configurable XFG function-generator state are
  generated alongside authoritative CSV/SVG experiment data.
  Multisim's exported native netlist is checked after opening so silently
  omitted parts fail the run.
- Generated schematic probes are not enabled by default. Experiment data is
  obtained authoritatively from the same netlist through Multisim's engine.

## Install

Requirements:

- Windows and a licensed Multisim 14+ installation.
- 32-bit Python 3.10+.
- Node.js 18+ only for `.ms14` XML conversion.

Install the Python package once; the server launcher never installs packages or
writes setup logs to MCP stdout:

```powershell
cd mcp_server
.\setup.ps1 -Python C:\path\to\python32\python.exe
npm install --global electronics-workbench-decoder@0.2.0
```

The public wheel is intentionally code-only: it contains the provenance
manifest but no XML extracted from NI samples. Before generating schematics,
build a local component pack from your own licensed installation as described
below and set `MULTISIM_MCP_TEMPLATE_DIR`. Other Automation API tools can still
be installed without that pack.

Diagnose the installation before starting the server:

```powershell
# Human-readable output (Chinese or English)
C:\path\to\python32\Scripts\multisim-mcp.exe doctor --lang zh
C:\path\to\python32\Scripts\multisim-mcp.exe doctor --lang en

# Explicitly start/connect to Multisim and verify licensing plus COM activation.
C:\path\to\python32\Scripts\multisim-mcp.exe doctor --connect

# Stable JSON for agents and CI. Add --strict to require the full workflow.
C:\path\to\python32\Scripts\multisim-mcp.exe --json doctor
C:\path\to\python32\Scripts\multisim-mcp.exe doctor --json --strict
```

`doctor` is side-effect free by default: it does not activate Multisim. It checks the
Python version and architecture, pywin32, the 32-bit COM registration, the
local template pack, and the pinned `.ms14` codecs. A normal diagnostic run
returns exit code zero even when setup is incomplete so an agent can parse all
checks. `--strict` returns non-zero unless the complete workflow is ready.
`--connect` is the explicit opt-in that may start Multisim; it restores a
previously disconnected COM state after the probe and does not disturb an
already connected instance.

Start the server. Calling `multisim-mcp` without a subcommand remains backward
compatible; `serve` is the explicit equivalent:

```powershell
.\run_server.ps1
C:\path\to\python32\Scripts\multisim-mcp.exe serve
```

Generate a client configuration fragment:

```powershell
# Claude Desktop JSON
C:\path\to\python32\Scripts\multisim-mcp.exe config `
  --client claude-desktop `
  --python C:\path\to\python32\python.exe `
  --template-dir C:\MultisimMcp\component-pack `
  --work-dir C:\msre_exp

# Codex config.toml
C:\path\to\python32\Scripts\multisim-mcp.exe config `
  --client codex `
  --python C:\path\to\python32\python.exe `
  --template-dir C:\MultisimMcp\component-pack

# Unwrapped command/args/env JSON for another stdio client
C:\path\to\python32\Scripts\multisim-mcp.exe config --client generic
```

The generator previews content on stdout. `--output <new-file>` writes a
fragment, refuses to overwrite by default, and accepts `--force` only when the
caller explicitly wants replacement. It does not merge into a live client
configuration.

Manual MCP client configuration:

```json
{
  "mcpServers": {
    "multisim": {
      "command": "C:\\path\\to\\python32\\python.exe",
      "args": ["-m", "multisim_mcp.server"]
    }
  }
}
```

Call `runtime_status` first when diagnosing installation problems.

### CLI JSON contract

`multisim-mcp --json doctor` emits one JSON object with
`schema_version`, `success`, readiness booleans (including activation state),
runtime facts, and a stable
`checks[]` array. Each check has a stable `id`, `status`, and `message`; failed
checks may include `repair`. Missing setup is reported as data rather than a
JSON error.

`multisim-mcp config ... --json` emits a result envelope containing the client,
server name, optional output path, and generated content. Invalid input or an
overwrite refusal uses this error shape and exit code 2:

```json
{
  "schema_version": 1,
  "command": "config",
  "success": false,
  "error": {"type": "ValueError", "message": "..."}
}
```

JSON stdout never contains progress text or credentials. Import-time COM cache
diagnostics are redirected to stderr.

### User-local component packs

To keep licensed/reverse-engineered component assets separate from the open
engine, a contributor can derive a local pack from the NI samples installed on
their own machine:

```powershell
$env:PYTHONPATH = (Resolve-Path .\mcp_server).Path
.\tools\python32\python.exe .\tools\bootstrap_local_component_pack.py `
  --output C:\MultisimMcp\component-pack
$env:MULTISIM_MCP_TEMPLATE_DIR = 'C:\MultisimMcp\component-pack'
```

The configured pack is the public release's schematic-template source. A local
development checkout may contain ignored fallback templates, but public wheels
do not. The generated manifest records relative sample provenance. Local
reverse-engineering authorization does not itself grant permission to publish
the resulting XML files.

## Recommended agent workflow

The high-level tool accepts a SPICE netlist and a safe experiment command:

```json
{
  "netlist": "VIN vin 0 DC 10\nR1 vin vout 1k\nR2 vout 0 1k\n.end\n",
  "commands": "dc VIN 0 10 0.1",
  "output_dir": "C:\\experiments\\divider",
  "title": "Resistor divider",
  "overwrite": false
}
```

`run_circuit_experiment` will:

1. Validate the supported netlist and analysis command.
2. Generate and encode an editable `circuit.ms14`.
3. Open the design in Multisim and export `schematic.png`.
4. Run the requested analysis through Multisim's engine.
5. Export `result.raw`, `data.csv`, `plot.svg`, logs, and `report.md`.

Use `create_schematic_from_netlist` when only an editable schematic is needed,
or `run_spice_netlist` for netlist-only simulation.

Virtual instruments use explicit pseudo-device records in the same netlist:

```spice
XFG1 out 0 inv FGEN WAVE=SINE FREQ=1k AMPLITUDE=2 OFFSET=0.5
XSC1 out inv 0 0 out 0 OSCILLOSCOPE
```

The XSC terminal order is A, B, C, D, EXT+, EXT-. XFG supports `WAVE` (SINE,
SQUARE, or TRIANGLE), `FREQ`, `AMPLITUDE`, `OFFSET`, `DUTY`, and `RISE`.

## Safety model

- Safe analysis commands are allowlisted: `op`, `dc`, `ac`, and `tran`.
- `do_command_line` is disabled by default. It requires the server-side
  `MULTISIM_MCP_ENABLE_UNSAFE_COMMANDS=1` opt-in.
- Runtime npm downloads are disabled. On Windows the npx fallback remains
  disabled even when opted in because `.cmd` shims are not safe for
  caller-controlled paths. Install the pinned codec globally, or set
  `MULTISIM_MCP_EWD` and `MULTISIM_MCP_EWE` to its `dist/ewd.js` and
  `dist/ewe.js` entry points; the server invokes them through `node.exe`.
- Existing experiment artifacts are not overwritten unless `overwrite=true`.
- The server is intended for trusted local stdio clients, not public network
  exposure. See `SECURITY.md` in the repository root.

## Test

COM-free tests:

```powershell
$env:PYTHONPATH = (Resolve-Path .\mcp_server).Path
C:\path\to\python32\python.exe -m unittest discover -s mcp_server\tests -p 'test_*.py' -v
```

Real Multisim E2E:

```powershell
$env:MULTISIM_MCP_E2E_SAMPLE='C:\path\to\fixture.ms14'
tools\python32\python.exe mcp_server\tests\e2e_mcp_test.py
```

The E2E test requires a local fixture with RLC components and at least one
simulation output; proprietary NI sample designs are not distributed.

## License

Project code is MIT licensed. NI Multisim, its formats, trademarks, and locally
installed samples remain subject to their respective owners' terms.
