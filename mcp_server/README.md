# Multisim MCP

<!-- mcp-name: io.github.yxy050208/multisim-mcp -->

Unofficial Windows MCP server for creating NI Multisim schematics, running
experiments, exporting data, and generating reproducible reports.

非官方 Multisim 自动化 MCP：从受限 SPICE 网表生成可编辑电路图，调用本机
Multisim 执行实验，并导出 `.ms14`、原理图、raw、CSV、SVG 和 Markdown 报告。

> Stable 1.0 software. This project is not affiliated with NI. Multisim must be
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
- Netlist, BOM, schematic image, raw data, CSV, SVG, Markdown, standalone
  bilingual HTML/PDF, and reproducibility-manifest export.
- High-level `run_circuit_experiment` workflow.
- Durable `submit_circuit_experiment` queue with progress, cancellation,
  total/heartbeat timeouts, and isolated-worker recovery.
- Versioned `ExperimentSpec` verification with persisted PASS/FAIL/unverified
  evidence and theory-versus-simulation error.
- Parameter, tolerance, temperature, and seeded Monte Carlo sweeps with a
  100-run hard limit and durable-worker support.
- MCP `2026-07-28` discovery plus automatic compatibility with legacy clients.
- Sixteen experiment artifact Resources, two sweep Resources, one job-status
  Resource, and five
  bilingual workflow Prompts.
- Validated structured output for the complete experiment workflow.

Completed experiments return an opaque `experiment_id` and resource URIs such
as:

```text
multisim://experiments/{experiment_id}/manifest
multisim://experiments/{experiment_id}/report
multisim://experiments/{experiment_id}/schematic
multisim://experiments/{experiment_id}/data
multisim://experiments/{experiment_id}/plot
multisim://experiments/{experiment_id}/verification
multisim://experiments/{experiment_id}/formal-html-zh
multisim://experiments/{experiment_id}/formal-html-en
multisim://experiments/{experiment_id}/formal-pdf-zh
multisim://experiments/{experiment_id}/formal-pdf-en
multisim://experiments/{experiment_id}/reproducibility-manifest
multisim://sweeps/{sweep_id}/summary
multisim://sweeps/{sweep_id}/data
```

Completed durable jobs restore their handles after restarting the server. For
other historical output directories, call `register_experiment_artifacts` to
restore a process-local handle. Resource reads are limited to the fixed artifact
set and default to 16 MiB per file; set
`MULTISIM_MCP_RESOURCE_MAX_BYTES` to a positive integer to change that limit.

Experimental:

- Editable schematic generation supports R/L/C, scalar and waveform voltage/current
  sources, B/E/F/G/H controlled sources, T/O/U distributed lines, coupled
  inductors, modeled diodes,
  NPN/PNP BJT, NMOS/PMOS, JFET/MESFET, voltage switches, five-terminal op-amps,
  and generic two-to-sixteen-terminal X subcircuits. Extended families currently
  use verified carrier symbols pending dedicated artwork.
  Compatible inline vendor `.subckt` models are recursively expanded into
  editable primitives with nested dependencies and instance parameters retained.
  The result reports `editable_model_coverage`; conditional/proprietary records
  that cannot be expanded remain explicit carrier-only evidence.
  Native NOT/AND/OR/NAND/NOR/XOR/XNOR and JK flip-flop symbols are available as
  a preview; their open/export and authoritative timing-data paths are verified.
  Native XSC oscilloscope and configurable XFG function-generator state are
  generated alongside authoritative CSV/SVG experiment data.
  Multisim's exported native netlist is checked after opening so silently
  omitted parts fail the run.
- Portable `@KIND` adapters synthesize transformer, potentiometer, relay,
  crystal, power semiconductor, D/T flip-flop, four-bit counter/register, and
  one-bit ADC/DAC models from ordinary primitives. See
  [`docs/COMPONENT_ADAPTERS.md`](../docs/COMPONENT_ADAPTERS.md).
- Generated schematic probes are not enabled by default. Experiment data is
  obtained authoritatively from the same netlist through Multisim's engine.

## Install

Requirements:

- Windows and a licensed Multisim 14+ installation.
- 32-bit Python 3.10+.
- Node.js 18+ only for `.ms14` XML conversion.

The Windows dependency set deliberately uses `cryptography>=48.0.1,<49`.
`48.0.1` is the newest release line currently providing an official win32
wheel; newer releases would otherwise make 32-bit installation attempt an
unsupported local Rust build. This boundary should be reviewed whenever a new
win32 wheel becomes available.

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

# Official DeepSeek Harness Cordis plugin row
C:\path\to\python32\Scripts\multisim-mcp.exe config `
  --client deepseek-harness `
  --python C:\path\to\python32\python.exe `
  --template-dir C:\MultisimMcp\component-pack `
  --work-dir C:\msre_exp `
  --artifact-export-dir C:\MultisimMcp\exports `
  --tool-profile experiment

# Install the five bilingual workflow skills in a Harness project.
C:\path\to\python32\Scripts\multisim-mcp.exe harness-skills --output .dsh/skills

# Unwrapped command/args/env JSON for another stdio client
C:\path\to\python32\Scripts\multisim-mcp.exe config --client generic
```

The generator previews content on stdout. `--output <new-file>` writes a
fragment, refuses to overwrite by default, and accepts `--force` only when the
caller explicitly wants replacement. It does not merge into a live client
configuration.

The Harness fragment uses `@deepseek-ai/dsh-mcp-client`, enforces the upstream
1-32 character `serverName` rule, and never copies a DeepSeek API key into the
MCP child process. See [`docs/DEEPSEEK_HARNESS.md`](../docs/DEEPSEEK_HARNESS.md).
The same `--tool-profile core|experiment|optimization|full` option works for
every generated client config. The default remains `full` for compatibility.
`--artifact-export-dir` sets the only root beneath which the artifact export
tool may write; without it, artifact export fails closed.
`harness-skills` writes the packaged bundle to `.dsh/skills`, refuses existing
files by default, and only replaces them when `--force` is explicit.
Source-tree maintainers can run `python tools/check_deepseek_harness_compat.py
--json` from the repository root to validate the pinned Harness contract.
The independently installable Harness bundle lives in
[`integrations/deepseek-harness`](../integrations/deepseek-harness) and currently
supports local source installation; it is not yet published to npm.

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
C:\path\to\python32\python.exe .\tools\bootstrap_local_component_pack.py `
  --output C:\MultisimMcp\component-pack
$env:MULTISIM_MCP_TEMPLATE_DIR = 'C:\MultisimMcp\component-pack'
```

The generator connects to licensed Multisim and creates a temporary blank
circuit so the project shell matches the installed file-format version. Save
open work first. Version 1.0 writes a schema-2 local manifest; `doctor` rejects
schema-1 packs generated by alpha releases and asks the user to rebuild them.

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

For normal agent use, submit the same arguments with
`submit_circuit_experiment`. It returns immediately:

```json
{
  "success": true,
  "job_id": "job-...",
  "state": "queued",
  "status_uri": "multisim://jobs/job-...",
  "output_dir": "C:\\experiments\\divider"
}
```

Poll `get_experiment_job` or the status Resource, and call
`cancel_experiment_job` when needed. A failed, cancelled, or timed-out record
can be resubmitted without copying its persisted source through
`retry_experiment_job`. States are `queued`, `running`,
`cancelling`, `succeeded`, `failed`, `cancelled`, or `timed_out`. A successful
record contains the same structured result as `run_circuit_experiment`.
`list_experiment_jobs` omits large results and supports state filtering.

The synchronous `run_circuit_experiment` compatibility tool will:

1. Validate the supported netlist and analysis command.
2. Generate and encode an editable `circuit.ms14`.
3. Open the design in Multisim and export `schematic.png`.
4. Run the requested analysis through Multisim's engine.
5. Export `result.raw`, `data.csv`, `plot.svg`, logs, and `report.md`.
6. Export Chinese/English HTML/PDF reports and `manifest.json` atomically.

Job records are stored as atomic JSON under `%LOCALAPPDATA%\multisim-mcp\jobs`
by default. Set `MULTISIM_MCP_JOB_DIR` to select another private local state
directory. Records contain the source netlist and settings required for restart
recovery, so protect and back up that directory according to the sensitivity of
your circuit. `job_timeout` limits the whole workflow; `heartbeat_timeout`
detects a hung Multisim/codec worker. Output publication is guarded by a
cross-process sibling lock and remains transactional. A queue-wide lease keeps
execution serialized even if multiple MCP frontend processes use the same job
state directory.

### Design verification and sweeps

Use `run_verified_circuit_experiment` with an `ExperimentSpec` containing
explicit measurement signals, criteria, tolerances, and optional theoretical
values. Supported metrics include scalar statistics, gain, cutoff frequency,
bandwidth, rise time, overshoot, ripple, and power. Each requirement is reported
as `pass`, `fail`, or `unverified`; unavailable evidence is never guessed.

Use `plan_experiment_sweep` before execution to preview every rendered run.
`run_experiment_sweep` executes synchronously, while `submit_experiment_sweep`
uses the durable job worker. Modes are `parameter`, `tolerance`, `temperature`,
and `monte_carlo`; Monte Carlo runs use an explicit integer seed. Sweep values
are finite numbers substituted into declared `{{NAME}}` placeholders, and every
rendered netlist passes the same safety validator as a normal experiment.

Use `create_schematic_from_netlist` when only an editable schematic is needed,
or `run_spice_netlist` for netlist-only simulation.

Virtual instruments use explicit pseudo-device records in the same netlist:

```spice
XFG1 out 0 inv FGEN WAVE=SINE FREQ=1k AMPLITUDE=2 OFFSET=0.5
XSC1 out inv 0 0 out 0 OSCILLOSCOPE
```

The XSC terminal order is A, B, C, D, EXT+, EXT-. XFG supports `WAVE` (SINE,
SQUARE, or TRIANGLE), `FREQ`, `AMPLITUDE`, `OFFSET`, `DUTY`, and `RISE`.

Completed experiment data can also be read through `read_virtual_multimeter`,
`analyze_bode_response`, and `analyze_logic_signals`. These tools return
structured measurements and edge events; the Bode adapter explicitly leaves
phase unavailable when the raw file contains no phase column.

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
- Sweep expansion is capped at 100 runs and only finite numeric substitutions
  are accepted.
- Asynchronous job specifications are persisted locally for recovery; their
  state directory must not be shared with untrusted users.
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
