# Multisim MCP + Skills

[![Glama MCP server score](https://glama.ai/mcp/servers/yxy050208/multisim-mcp/badges/score.svg)](https://glama.ai/mcp/servers/yxy050208/multisim-mcp)

[中文](README.md) | [English (current)](README.en.md)

An unofficial local MCP server that lets an AI agent generate editable NI
Multisim circuits from constrained SPICE input, run experiments, export data,
and create reproducible reports.

> The current stable release is `v1.0.0`. This project is not affiliated
> with or endorsed by NI. A
> locally installed and licensed Multisim 14+ environment is required. The
> COM runs in an isolated 32-bit Python worker; the MCP frontend may use either
> 32-bit or 64-bit Python.

The completed development phases and release gates are recorded in the
[1.0 roadmap](docs/ROADMAP_TO_1.0.md).

[PyPI package](https://pypi.org/project/multisim-mcp/) ·
[Official MCP Registry entry](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.yxy050208%2Fmultisim-mcp) ·
[GitHub Release](https://github.com/yxy050208/multisim-mcp/releases/tag/v1.0.0)

## End-to-end workflow

`run_circuit_experiment` uses one validated source netlist to:

1. validate the circuit and analysis commands;
2. generate an editable `.ms14` schematic;
3. open and reverse-verify it in real Multisim;
4. export the schematic as PNG;
5. run operating-point, DC, AC, or transient analysis;
6. export raw data, CSV, SVG plots, and command logs; and
7. generate Markdown, standalone bilingual HTML/PDF reports, and a SHA-256
   `manifest.json`.

For longer experiments, use the durable
`submit_circuit_experiment` workflow. It returns a `job_id` immediately;
`get_experiment_job`, `list_experiment_jobs`, `cancel_experiment_job`,
`retry_experiment_job`, and `multisim://jobs/{job_id}` expose progress,
cancellation, and safe retries. Each job runs in
an isolated job subprocess, while every Multisim COM/codec operation crosses a
stateful 32-bit worker boundary. A crash, RPC timeout, or heartbeat timeout does
not take down the MCP frontend; the COM worker restarts on a later call and
interrupted jobs are safely requeued after restart.

Version 1.0 adds computable design verification and batch experiments:

- `run_verified_circuit_experiment` accepts a versioned `ExperimentSpec`,
  measures time-domain frequency, THD, gain, bandwidth, cutoff, rise time,
  overshoot, ripple, and power,
  then persists per-requirement `pass`, `fail`, or `unverified` evidence in
  `verification.json` and the Markdown report.
- `measure_experiment` and `verify_experiment_requirements` recompute metrics
  for registered experiments. Missing signals or evidence remain `unverified`;
  the server does not infer a result.
- `plan_experiment_sweep`, `run_experiment_sweep`, and
  `submit_experiment_sweep` support parameter, tolerance, temperature, and
  seeded Monte Carlo sweeps. A hard limit of 100 runs applies, and durable
  sweeps reuse cancellation, timeouts, worker recovery, and output leases.
- Sweeps export `summary.json`, flat `data.csv`, and per-run raw artifacts via
  `multisim://sweeps/{sweep_id}/summary|data`.

Real Multisim 14.3 regressions cover resistor dividers, coupled inductors,
digital truth tables, JK timing, and a combined function-generator/oscilloscope
experiment.

Version 1.0 also adds portable models without redistributing NI database assets:

- transformer, potentiometer, relay, crystal, power-diode, and power-MOS macros;
- D/T flip-flops, four-bit counter/shift-register, and one-bit ADC/DAC macros;
- data-backed multimeter, Bode, and logic-analyzer tools; and
- standalone Chinese/English HTML/PDF reports plus a reproducibility manifest.

See the [adapter API](docs/COMPONENT_ADAPTERS.md) and
[compatibility matrix](docs/COMPATIBILITY.md) for syntax and evidence levels.
The 1.0 regressions additionally cover portable adapter open/export,
transformer transient, relay and power-device operating points, crystal AC,
DFF transient behavior, and the complete bilingual-report transaction.

## Capability

Stable:

- local MCP stdio lifecycle and 32-bit runtime diagnostics;
- circuit open/save/enumeration and image/netlist/BOM export;
- DC, AC, transient, waveform injection, and RLC read/write;
- safe SPICE subset execution and raw/CSV/SVG/report generation.
- durable experiment queueing, progress/cancellation/timeouts, output leases,
  and crash/hang worker recovery.
- versioned measurements, strict requirement verdicts, and four deterministic
  sweep modes.

Experimental but verified:

- editable R/L/C, source, B/E/F/G/H, K/T/O/U, semiconductor, switch,
  five-terminal op-amp, and 2-16-terminal generic subcircuit schematics;
- recursive editable expansion for compatible inline vendor `.subckt` models,
  including nested dependencies, local nodes, and `PARAMS:` overrides, with
  machine-readable complete/partial/carrier-only coverage;
- NOT/AND/OR/NAND/NOR/XOR/XNOR and JK preview symbols with real timing data;
- native XFG function-generator and four-channel XSC oscilloscope state.

See [component coverage](docs/COMPONENT_COVERAGE.md) for precise boundaries.
See [vendor macro-model compatibility](docs/VENDOR_SPICE_MODELS.md) for the
editable-expansion and safe-file boundary.

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

The pack generator connects to licensed Multisim and creates one temporary
blank circuit so the structural template matches the installed version. Save
open work before running it. Schema-1 packs generated by alpha releases must be
rebuilt.

See the [publishing guide](docs/PUBLISHING.md) and
[third-party notices](THIRD_PARTY_NOTICES.md).

## Quick start

The simplest compatible deployment still installs into 32-bit Python:

```powershell
C:\path\to\python32\python.exe -m pip install "multisim-mcp==1.0.0"
C:\path\to\python32\Scripts\multisim-mcp.exe
```

Alternatively, install the MCP frontend in 64-bit Python and retain a separate
32-bit Python containing this package and `pywin32`. The Windows `py` launcher
is auto-detected, or configure the worker explicitly:

```powershell
$env:MULTISIM_MCP_WORKER_PYTHON = 'C:\path\to\python32\python.exe'
C:\path\to\python64\python.exe -m multisim_mcp.server
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

Version `v1.0.0` includes diagnostic and configuration commands.
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
  --python C:\path\to\python64\python.exe `
  --worker-python C:\path\to\python32\python.exe `
  --template-dir C:\MultisimMcp\component-pack

# Print a DeepSeek Harness Cordis plugin fragment.
C:\path\to\python32\Scripts\multisim-mcp.exe config `
  --client deepseek-harness `
  --python C:\path\to\python32\python.exe `
  --template-dir C:\MultisimMcp\component-pack `
  --work-dir C:\msre_exp `
  --artifact-export-dir C:\MultisimMcp\exports `
  --tool-profile experiment

# Install the five bilingual experiment skills from a Harness project root.
C:\path\to\python32\Scripts\multisim-mcp.exe harness-skills --output .dsh/skills

# Discover and safely preview model-provider settings (no write, no network).
C:\path\to\python32\Scripts\multisim-mcp.exe configure --auto --json

# Persist environment-variable references after reviewing the preview.
C:\path\to\python32\Scripts\multisim-mcp.exe configure --auto --apply
```

The generator prints a copy-pasteable fragment by default. `--output` writes a
new file and refuses to replace one unless `--force` is also present. It never
merges into a live Claude Desktop, Codex, or Harness configuration automatically.
See the [DeepSeek / Harness integration guide](docs/DEEPSEEK_HARNESS.md) for the
credential boundary and compatibility baseline.
`--tool-profile core|experiment|optimization|full` limits tool discovery;
omitting it preserves the 55-tool `full` compatibility mode. Artifact export is
disabled unless `--artifact-export-dir` explicitly approves a destination root.
The Harness skill installer preserves existing files unless `--force` is explicit.
Model-provider self-configuration supports DeepSeek, OpenAI, Ollama, and custom
OpenAI-compatible services. It previews by default, writes only with `--apply`,
and connects only with `--probe`; API key values are never stored. See the
[bilingual provider configuration guide](docs/MODEL_PROVIDER_CONFIGURATION.md).
Repository maintainers can validate the pinned local Harness contract with
`python tools/check_deepseek_harness_compat.py --json`.
An independently installable source bundle is available under
[`integrations/deepseek-harness`](integrations/deepseek-harness); it has not yet
been published to npm. Maintainers should follow the
[npm release guide](docs/DEEPSEEK_HARNESS_NPM_RELEASE.md) for the first 2FA
publication and later OIDC-staged updates.

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

Alpha users should read the [1.0 migration guide](docs/MIGRATION_TO_1.0.md).
Durable-job and artifact recovery procedures are documented in
[the recovery guide](docs/RECOVERY.md).
The diagnosis, optimization, multi-EDA, and visual-workbench direction is
documented in the [2.0 roadmap](docs/ROADMAP_TO_2.0.md). The first transport-
neutral [EDA core and backend boundary](docs/EDA_CORE.md) and limited-SPICE
adapter are now available. Schematic generation and standalone SPICE simulation
plus synchronous, verified, and durable-worker complete experiments are routed
through application services while existing MCP signatures and persisted-job
formats remain compatible. Experiment staging, reporting, atomic publication,
and rollback now live in an independent injectable pipeline.

## License

Project-owned code is available under the MIT License. NI Multisim, trademarks,
file formats, installed samples, and third-party models remain subject to their
respective terms.
