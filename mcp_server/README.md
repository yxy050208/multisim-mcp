# Multisim MCP

<!-- mcp-name: io.github.yxy050208/multisim-mcp -->

Unofficial Windows MCP server for creating NI Multisim schematics, running
experiments, exporting data, and generating reproducible reports.

非官方 Multisim 自动化 MCP：从受限 SPICE 网表生成可编辑电路图，调用本机
Multisim 执行实验，并导出 `.ms14`、原理图、raw、CSV、SVG 和 Markdown 报告。

> MCP Core 1.2 release candidate; the current public stable release is 1.1.0.
> This source/package does not include the React Workbench frontend. It may expose
> optional loopback bridge APIs for compatible local clients. This project is not
> affiliated with NI. Multisim must be
> installed and licensed locally. COM runs in an isolated 32-bit Python worker;
> the MCP frontend may use 32-bit or 64-bit Python.

Linux and Docker do not run Multisim COM. With a local ngspice installation they
can run safe SPICE simulation, complete experiments, verification, and the same
optimization/correction services; otherwise they retain MCP initialization,
tool discovery, and `runtime_status` diagnostics. COM-backed operations still
fail closed outside Windows. The minimal non-root image contains no EDA runtime,
NI software, samples, licenses, or extracted templates.

## Current capability

Stable and verified on Multisim 14.3:

- MCP stdio lifecycle and isolated worker/frontend runtime diagnostics.
- Open/save circuits and enumerate components, inputs, and outputs.
- DC operating point, AC sweep, single-frequency AC, and transient analysis.
- Input waveform injection and RLC value read/write.
- SPICE netlist execution with safe `op`, `dc`, `ac`, and `tran` commands.
- Netlist, BOM, schematic image, raw data, CSV, SVG, Markdown, standalone
  bilingual HTML/PDF, and reproducibility-manifest export.
- High-level `run_circuit_experiment` workflow.
- Read-only `plan_design_options` / `select_design_option` /
  `prepare_design_specification` / `prepare_netlist_draft` / `resolve_component_requirements` /
  `approve_component_resolution` / `compile_executable_netlist` /
  `approve_executable_netlist` workflow that compares
  2--4 technical paths, binds the selected option, collects electrical parameters,
  then emits an explicitly approved but non-executable logical block network and a separate
  human-reviewed component approval artifact. The first bounded compiler supports only
  `signal-passive` and emits an in-memory pin-level CircuitDesign/SPICE preview; the separate
  `approve_executable_netlist` gate binds the preview digest and only opens schematic planning;
  file writes, stimuli, analysis, and simulation remain later explicit gates.
- Durable `submit_circuit_experiment` queue with progress, cancellation,
  total/heartbeat timeouts, and isolated-worker recovery.
- Durable `submit_design_optimization` with candidate-level checkpoints,
  evidence revalidation, and non-overwriting interrupted-attempt recovery.
- Deterministic read-only `diagnose_design` for topology, requirement,
  convergence, and evidence-backed BJT/op-amp saturation findings.
- Evidence-backed `evaluate_design_patch` for unchanged-baseline versus one
  explicit in-memory candidate, with before/after diagnoses and no auto-adoption.
- Mixed topology/value `global_optimize_design` with exhaustive or deterministic
  Halton search, hard constraints, epsilon-aware Pareto fronts, and no auto-write.
- Bounded model-planned `autonomous_correct_design`, where every topology/value
  proposal must compile and pass real experiment gates before it can advance.
- Durable `submit_global_optimization` and `submit_autonomous_correction` jobs
  with integrity-checked candidate/round recovery and no persisted API keys.
- `benchmark-suite` offline/real gates for RC, RLC, op-amp, BJT, and regulated
  power-supply correction; the local 2026-08-25 real gate passed all five cases.
- `course-demo` builds the bilingual five-waveform course-design contract with
  12 explicit verification gates, a 35-row BOM, and a model-evidence readiness
  gate, then can run the same contract through the selected Multisim/ngspice
  backend. The bundled fixture is explicitly behavioral; a component-level
  claim requires HE555/74LS74/LM324/1N4007 provenance plus an integrity-checked
  real Multisim 12/12 result.
- A real Windows workstation gate has exercised Multisim 14.3 COM, ngspice 47, the
  DFF behavioral reference, the five-waveform 12/12 contract, and an RC
  cross-backend comparison. See [`REAL_RUNTIME_VALIDATION`](../docs/REAL_RUNTIME_VALIDATION.md)
  for reproducible commands and evidence boundaries.
- `inspect-project` builds a bounded, read-only project snapshot from versioned
  directory manifests for compatible local clients.
- `execute-handoff` validates a downloaded controlled-execution handoff
  and, only with explicit `--confirm`, runs its schematic-first/verified-simulation
  sequence without bypassing approval or manifest gates.
- Versioned `ExperimentSpec` verification with persisted PASS/FAIL/unverified
  evidence and theory-versus-simulation error.
- Parameter, tolerance, temperature, and seeded Monte Carlo sweeps with a
  100-run hard limit and durable-worker support.
- MCP `2026-07-28` discovery plus automatic compatibility with legacy clients.
- Seventeen experiment artifact Resources (including the SPICE compatibility
  audit), two sweep Resources, one job-status
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
multisim://experiments/{experiment_id}/spice-compatibility
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
  omitted parts fail the run. The user-local component-pack workflow also
  supports a verified `TIMER8` carrier for `LM555CN` and a `DFF8` A-section
  carrier for `7474N`; portable `XU1` instances normalize to native `U1`.
  Vendor timer/digital bodies may be omitted by `ReportNetlist`, so native
  component enumeration is recorded as the authoritative presence evidence for
  these carriers. `DFF8` is a functional 74LS74 substitute, not an exact
  74LS74N/74LS74D model claim. Structured correction/optimization rebuilds keep
  both carriers as strict eight-terminal `X...` records. Use the isolated
  `tools/probe_native_replacement.py` helper for crash-safe replacement tests.
- Portable `@KIND` adapters synthesize transformer, potentiometer, relay,
  crystal, power semiconductor, D/T flip-flop, four-bit counter/register, and
  one-bit ADC/DAC models from ordinary primitives. See
  [`docs/COMPONENT_ADAPTERS.md`](../docs/COMPONENT_ADAPTERS.md).
- Generated schematic probes are not enabled by default. Experiment data is
  obtained authoritatively from the same netlist through Multisim's engine.

Platformization work now includes strict versioned `CircuitDesign`, `DesignPatch`,
and `ArtifactSet` objects, the `EdaBackend` protocol, a transport-neutral
application service, and an injectable Multisim adapter. See
[`docs/EDA_CORE.md`](../docs/EDA_CORE.md). The current MCP tools retain their 1.0
signatures while execution is migrated behind this boundary.

## Install

Requirements:

- Windows and a licensed Multisim 14+ installation.
- Python 3.10+ for the MCP frontend (32-bit or 64-bit).
- A separate 32-bit Python 3.10+ environment containing this package and
  `pywin32` for the Multisim worker.
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

Build a bounded, read-only project snapshot for a compatible local client:

```powershell
C:\path\to\python32\Scripts\multisim-mcp.exe inspect-project `
  --root C:\msre_exp --json
```

The command verifies manifest-referenced artifacts by default, reports corrupt
child directories without modifying them, and never treats an unmanifested
directory as an experiment. See [`docs/PROJECT_INSPECTION.md`](../docs/PROJECT_INSPECTION.md).

For a compatible local control surface, start the optional read-only bridge in a second
terminal. It binds to loopback, fixes the project root at startup, and exposes
the bounded project snapshot plus opaque-handle experiment detail/media and
optimization evidence views:

```powershell
$env:PYTHONPATH = (Resolve-Path .).Path
python -m multisim_mcp.cli workbench-api --root C:\msre_exp --port 8787
```

The client can read that bounded snapshot. Experiment
details reuse the existing Resource summary and media must be referenced by a
verified directory manifest. The `Models / API` page also reads the secret-free
provider metadata from `/api/provider-config` and can explicitly probe one
OpenAI-compatible `/models` endpoint through `/api/provider-probe`; it never
accepts plaintext credentials and does not write provider files. Use the copied
`multisim-mcp configure ... --apply` command for persistence. Do not bind this
bridge to a public interface; it is not a remote or multi-tenant API.

The planning page uses the same loopback bridge for the read-only design flow:
`/api/design-plan`, `/api/design-plan/select`, `/api/design-specification`,
`/api/netlist-draft`, `/api/component-resolution`, `/api/component-resolution/approve`,
`/api/executable-netlist/compile`, and `/api/executable-netlist/approve`. After the
netlist review, `/api/executable-netlist/simulation-approve` binds a safe
`ExperimentSpec` to the approved preview. These routes only return bounded previews
or approval artifacts; schematic generation and simulation remain explicit MCP/CLI
operations.

To execute the two-step handoff without manually copying each MCP call, first run
the validation-only command from a trusted terminal:

```powershell
python -m multisim_mcp.cli execute-handoff `
  --handoff .\multisim-approved-experiment-handoff.json `
  --root C:\msre_exp --json
```

After reviewing the resolved output directory and approval identities, add
`--confirm` to execute schematic generation and then the verified experiment. The
CLI rejects absolute/traversal paths, mismatched netlists or approval payloads, and
existing artifacts are not overwritten unless the handoff requests it and the
operator also supplies `--allow-overwrite`. See
[`CONTROLLED_HANDOFF`](../docs/CONTROLLED_HANDOFF.md).
For long experiments, add `--submit --confirm` instead; the schematic is generated
first and the verified experiment is then queued for the durable worker. Its job
handle is available through the MCP/CLI job-status surfaces.

The `Jobs / 队列` page polls `/api/jobs` every four seconds and shows only
sanitized durable-job state (`queued`, `running`, `succeeded`, or failure class).
`/api/jobs/{job_id}` exposes the same bounded status for one opaque handle; specs,
result payloads, output paths, logs, and error text stay behind MCP/CLI surfaces.
The page has no submit, cancel, retry, or other mutation controls.
For a succeeded job, the single-job endpoint resolves an opaque `result_entry`
only when the output directory is an exact, non-symlink child of the fixed
project root and its experiment/optimization manifest passes the configured
integrity check. The browser then refreshes the snapshot before opening it.

Optimization details are also manifest-backed and bounded. They expose run
state, budget, ranked candidates, objective convergence, Pareto layers, and the
recommended solution, plus a descriptive observed-candidate-range sensitivity
summary and a bounded, read-only next-search proposal. Numeric proposals use a
small E24 neighborhood; categorical proposals repeat observed values. The
proposal never starts experiments or mutates the optimization specification. The
`spec_draft` payload is explicitly non-executable and can be copied/downloaded
for manual review. Its preflight only checks bounded budget/value limits and
the manual-approval requirement; it never issues an approval token or enables
execution. The separate local `search-plan-approve` CLI can issue a short-lived,
one-time token after review. The token is bound to the opaque entry handle,
optimization ID/kind, normalized source-design SHA-256, source-spec SHA-256,
complete canonical draft SHA-256, and budget summary; `search-plan-verify`
only validates and does not consume or execute anything. After review,
`search-plan-submit` revalidates those exact inputs, consumes the token, and
queues one derived bounded `optimization` or `global_optimization` job. It is
a durable queue hand-off (`execution_started=false`), so a long-lived MCP
worker using the same `MULTISIM_MCP_JOB_DIR` performs the actual experiments.
The queue record retains approval/binding digests but never the bearer token;
approval-bound replay is idempotent after a crash. Topology-choice drafts must
use explicit topology operations rather than scalar value substitution.
They omit patch paths, experiment directories, and raw error paths; sensitivity
is not a causal derivative or a global-optimality proof.

Start the server. Calling `multisim-mcp` without a subcommand remains backward
compatible; `serve` is the explicit equivalent:

```powershell
.\run_server.ps1
C:\path\to\python32\Scripts\multisim-mcp.exe serve
```

For a 64-bit frontend, install the package in both environments and either let
the Windows `py` launcher discover the worker or select it explicitly:

```powershell
$env:MULTISIM_MCP_WORKER_PYTHON = 'C:\path\to\python32\python.exe'
C:\path\to\python64\python.exe -m multisim_mcp.server
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
  --python C:\path\to\python64\python.exe `
  --worker-python C:\path\to\python32\python.exe `
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

# Safely discover model-provider environment settings (preview only).
C:\path\to\python32\Scripts\multisim-mcp.exe configure --auto --json

# Atomically store references after reviewing them.
C:\path\to\python32\Scripts\multisim-mcp.exe configure --auto --apply

# One explicit tool-free model request from a UTF-8 file.
C:\path\to\python32\Scripts\multisim-mcp.exe model --input .\prompt.txt --json

# Four design tools plus four optional completed-experiment evidence tools.
C:\path\to\python32\Scripts\multisim-mcp.exe model-diagnose `
  --input .\diagnosis-prompt.txt --netlist .\circuit.cir `
  --experiment-dir .\completed-experiment `
  --enable-patch-preview `
  --audit-output .\agent-audit.json --json

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
[`integrations/deepseek-harness`](../integrations/deepseek-harness) and is
published as
[`multisim-mcp-dsh-plugin@1.1.0`](https://www.npmjs.com/package/multisim-mcp-dsh-plugin).
Install it with
`dsh plugin --profile web add multisim-mcp-dsh-plugin@1.1.0`.
The separate `configure` command prepares model-provider settings for compatible
clients. It never copies credential values into its versioned JSON file or the
MCP child process, and it performs network I/O only with explicit `--probe`.
See [`docs/MODEL_PROVIDER_CONFIGURATION.md`](../docs/MODEL_PROVIDER_CONFIGURATION.md).
The transport-neutral runtime adds bounded non-streaming Chat Completions,
normalized usage, cooperative cancellation, double-opt-in failover, and an
allowlisted library-level tool loop. The ordinary `model` command deliberately
exposes no tools and accepts no inline prompt arguments. The separate
`model-diagnose` command explicitly enables four read-only tools over strict
CircuitDesign JSON or safely parsed SPICE, without starting a backend.
Explicit `--experiment-dir` adds four more read-only tools over a sanitized
completed-experiment snapshot: waveform-column statistics, requirement
verdicts, and artifact hashes. It excludes report text, raw samples, artifact
content, and paths, and marks the design/experiment association as unverified.
Explicit `--enable-patch-preview` adds one non-persistent tool that validates a
bounded `DesignPatch`, derives its inverse, and returns structural deltas. It
does not mutate the design, write files, call a backend, simulate, or approve.
Opt-in `--audit-output` writes model rounds, validated tool calls, hashes and
usage to versioned JSON while excluding prompts, answers, reasoning,
credentials, and full tool results; replacement requires `--audit-overwrite`.
The separate local `patch-approve`, `patch-apply`, `patch-revert`, and
`patch-recover` commands provide short-lived one-time approval, durable crash
journaling, and verified commit/rollback for CircuitDesign JSON. The additional
`patch-verify-approve`, `patch-verify-apply`, and `patch-verify-recover` commands
bind an explicit verification plan and complete experiment evidence, simulate
the in-memory candidate through Multisim, and persist only an all-pass verdict.
These mutation commands are not exposed to the model.
The `diagnose-design` CLI and `diagnose_design` MCP tool run without a model,
COM activation, simulation, or writes. A completed experiment may be attached
only when its recursive manifest verifies and its canonical netlist matches the
input design. See
[`docs/DESIGN_DIAGNOSIS.md`](../docs/DESIGN_DIAGNOSIS.md).
The `evaluate-design-patch` CLI and `evaluate_design_patch` MCP tool run the
unchanged baseline and one explicit reversible candidate under the same hard
requirements. They retain both experiments, the inverse patch, before/after
diagnoses, and a recursive manifest; even an all-pass candidate requires a
separate approval before persistence. See
[`docs/DESIGN_PATCH_EVALUATION.md`](../docs/DESIGN_PATCH_EVALUATION.md).
The separate `optimize-design` CLI and `optimize_design` MCP tool evaluate
explicit or E12/E24/E48/E96 scalar component values under a hard budget (32
experiments including the baseline). Electrical requirements, optional in-stock
rules, and variable-cost ceilings are hard constraints; inventory binds values
to part/supplier/cost evidence, and cost only breaks equal objective ties. The
source design is never modified, and the selected patch still requires the
verified approval workflow. Each patch, experiment, objective, procurement
verdict, failure, stopping reason, CSV comparison, and recursive manifest is retained. See
[`docs/DESIGN_OPTIMIZATION.md`](../docs/DESIGN_OPTIMIZATION.md).
Mixed finite-domain topology/value search is documented in
[`docs/GLOBAL_OPTIMIZATION.md`](../docs/GLOBAL_OPTIMIZATION.md). The bounded
diagnose-propose-simulate-select loop is documented in
[`docs/AUTONOMOUS_CORRECTION.md`](../docs/AUTONOMOUS_CORRECTION.md). Neither
workflow persists a selected patch automatically.
Long searches can use `submit_design_optimization`, then the existing
`get_experiment_job`, `cancel_experiment_job`, and `retry_experiment_job` tools.
Completed candidate evidence is verified before reuse; an uncommitted candidate
is rerun in a new attempt directory. The local equivalent is
`optimize-design --resume` against a matching interrupted directory.
The `compare-designs` CLI and `compare_design_variants` MCP tool run one
verification plan across 2–16 complete designs, rank only finite measured
all-pass variants, and retain every design, experiment, error, CSV rank, and
recursive manifest without changing or adopting an input. See
[`docs/DESIGN_COMPARISON.md`](../docs/DESIGN_COMPARISON.md).
See
[`docs/MODEL_PROVIDER_RUNTIME.md`](../docs/MODEL_PROVIDER_RUNTIME.md) and
[`docs/READ_ONLY_EDA_DIAGNOSIS.md`](../docs/READ_ONLY_EDA_DIAGNOSIS.md), plus
[`docs/DESIGN_PATCH_TRANSACTIONS.md`](../docs/DESIGN_PATCH_TRANSACTIONS.md).

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
$env:MULTISIM_MCP_TEMPLATE_ONLY = 'true'
```

For a locally verified native model, overlay the component into that user-local
pack instead of copying NI assets into the repository:

```powershell
python .\tools\overlay_local_component_pack.py `
  --pack C:\MultisimMcp\component-pack `
  --source C:\path\to\verified-lm324.ms14 `
  --refdes U5 --kind OPAMP5 --identity-token LM324M --force
```

The tool records source and installed-template hashes, backs up replaced files,
and marks the manifest as local-only. It never publishes the `.ms14`, decoded XML,
or vendor model body. Use the actual saved reference designator from the probe
project; it may differ from the design's intended `U1`/`D1` labels.

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

Before generating a netlist, call `plan_design_options` with the functional requirements,
hard constraints, context, and objective weights. Present the bounded options and the default
recommendation to the user, wait for an explicit `plan_id` + `option_id` selection, then call
`select_design_option` with the original envelope, then call
`prepare_design_specification` until all required electrical parameters are present. After the
user reviews that exact specification digest, call `prepare_netlist_draft` with explicit approval
to inspect the logical module/net structure and unresolved component requirements. Then call
`resolve_component_requirements`; after supplying explicit candidates, ratings, and model
provenance, call `approve_component_resolution` to bind the human review gate. The approval
artifact authorizes only a later compiler and does not itself generate SPICE. For an option in the
explicit support matrix, call `compile_executable_netlist`; it revalidates the artifact and local
model hashes, and currently supports only `signal-passive`. Then call
`approve_executable_netlist` with explicit review confirmations; its approval only opens schematic
planning and remains unapproved for file writes or simulation. To consume that approval, pass the
complete preview as `executable_netlist`, the approval artifact as `netlist_approval`, and the
preview's exact `spice_netlist` to `create_schematic_from_netlist`. The existing schematic tool
revalidates both artifacts before writing `.ms14`, rejects a missing or mismatched handoff, records
the approval identifiers in its result, and still never starts simulation. Next call
`approve_simulation_plan` to bind the same preview and netlist approval to a validated
`ExperimentSpec`; pass those three artifacts to `run_verified_circuit_experiment` so it rechecks
the commands, measurements, and limits before creating the schematic or starting Multisim.
For a durable long-running job, pass the same three artifacts and reviewed requirements to
`submit_circuit_experiment`; they are persisted with the job and revalidated inside its isolated
worker before execution. The planner is deterministic and planning-only: declared constraints are
retained for the next validation step but are not electrically enforced yet; its scores are heuristic,
every option is marked
`planning-only`, and its execution boundary keeps schematic generation, simulation, and file
writes false. The logical draft also keeps CircuitDesign, SPICE generation, and schematic readiness
false. See [`DESIGN_PLANNING.md`](../docs/DESIGN_PLANNING.md).

Choose the open complete-experiment backend when ngspice is installed:

```bash
export MULTISIM_MCP_EXPERIMENT_BACKEND=ngspice
# Optional when ngspice is not on PATH:
export MULTISIM_MCP_NGSPICE=/absolute/path/to/ngspice
```

Multisim remains the default. `run_spice_netlist` also accepts an explicit
`backend` argument. Complete ngspice runs publish an honest non-editable
`schematic.svg` connectivity diagram plus PNG preview, not a fake `.ms14`.
See [`docs/OPEN_EDA_BACKENDS.md`](../docs/OPEN_EDA_BACKENDS.md).
Every new experiment also writes `spice-compatibility.json`, which records source
and executed-netlist hashes, model provenance, dialect features, backend risk,
and solver-version evidence. Use `audit_spice_compatibility` before a run and
read the artifact through `multisim://experiments/{id}/spice-compatibility`.
Digital-device runs additionally publish `digital-observation.json` and expose it
through the `digital-observation` experiment resource. It records whether each
digital output was observed, came from a behavioral reference, or remained
unobserved. When Multisim omits a digital output, the artifact recommends an
explicit ngspice rerun; backend switching is never automatic.

For the supported native D flip-flop carriers, call `build_behavioral_reference`
to produce an explicit, machine-readable reference netlist:

```text
XU1 d pr clr clk q nq 0 vcc 7474N
```

becomes an `@DFF` adapter with the documented mapping
`D, ~PR, ~CLR, CLK, Q, ~Q, GND, VCC` →
`D, CLK, SET, RESET, Q, QBAR, HIGH, LOW`; two explicit NOT devices convert the
active-low preset/clear inputs to XSPICE's asserted-high set/reset inputs. The returned netlist must be passed
explicitly to `run_spice_netlist(backend="ngspice")`; it remains behavioral
evidence and does not establish native 74LS74 timing or electrical equivalence.
The ngspice backend compiles this adapter through the same bounded simulation
translator used by the Multisim command path, including the required XSPICE
bridge and `d_jkff` model definitions.

For a one-call reference experiment, use `run_behavioral_reference` with the
native netlist and a safe analysis command. It performs the same conversion,
forces the explicit `ngspice` backend, and returns the measured result together
with `reference_netlist` and `behavioral_reference` metadata. It rejects a
netlist with no supported native DFF carrier so that a no-op conversion cannot
be mistaken for a reference run. The source netlist is never modified. A local
ngspice executable is still required; if it is not installed, the tool reports
the backend's clear runtime failure rather than silently switching to Multisim.

The same workflow is available from the local CLI:

```powershell
multisim-mcp behavioral-reference `
  --netlist C:\experiments\native-dff.cir `
  --commands C:\experiments\reference-tran.txt `
  --output C:\experiments\dff-reference `
  --json
```

The CLI reads both files as bounded UTF-8 input and exposes the same explicit
ngspice-only semantics as the MCP tool.

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

With the default Multisim backend, the synchronous `run_circuit_experiment` tool will:

1. Validate the supported netlist and analysis command.
2. Generate and encode an editable `circuit.ms14`.
3. Open the design in Multisim and export `schematic.png`.
4. Run the requested analysis through Multisim's engine.
5. Export `result.raw`, `data.csv`, `plot.svg`, logs, and `report.md`.
6. Export Chinese/English HTML/PDF reports, the SPICE compatibility audit, and
   `manifest.json` atomically.

With ngspice selected, the same service keeps steps 1 and 4–6, replaces the
editable `.ms14` step with a labeled SVG/PNG connectivity graph, and records the
non-editable backend profile in `backend.json`. Existing verification,
optimization, global optimization, and autonomous correction callers do not
change. `compare_experiment_backends` aligns common signals from two registered
runs and reports tolerance-based MAE/RMSE/max-error verdicts; its evidence block
also checks that source and executed netlists and model fingerprints match.

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
or `run_spice_netlist` for netlist-only simulation. For a compiler-approved design,
also provide the matching `executable_netlist` and `netlist_approval` objects as described above;
the schematic tool will use only the preview's bound SPICE text.

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
- Optimization resume fails closed if the design, normalized spec, runtime
  limits, child manifest, verification evidence, objective, or procurement
  record differs from the persisted checkpoint.
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
