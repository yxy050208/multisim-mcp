---
name: multisim-circuit-workflow
description: Diagnose, configure, generate, simulate, and report on NI Multisim circuits through the Multisim MCP server. Use when the user asks to install or configure Multisim MCP, generate a Multisim schematic, run circuit simulation, analyze waveforms, or produce an electronic design report.
---

# Multisim Circuit Workflow

Use the `multisim` MCP tools in this order. Do not skip pre-validation.

## 0. Verify the installation

When the installed CLI is accessible, start with:

```powershell
Get-Command multisim-mcp
multisim-mcp --json doctor
```

Read `full_workflow_ready` and each stable `checks[].id`. Apply the provided
`repair` instructions instead of repeatedly attempting COM activation. Use
`doctor --json --strict` only in CI or when a non-zero incomplete-setup status
is useful. Run `doctor --json --connect` only when the user wants a real COM
and license probe; it may start Multisim.

Preview client configuration when requested:

```powershell
multisim-mcp config --client claude-desktop --python C:\Python32\python.exe
multisim-mcp config --client codex --python C:\Python32\python.exe
multisim-mcp config --client generic --python C:\Python32\python.exe
```

Do not write over a live client configuration. Generate a fragment, inspect it,
and merge it manually. Keep the MCP server local over stdio.

After the client connects, call `runtime_status` before the first experiment on
a new installation. Prefer
the high-level `run_circuit_experiment` tool whenever the requested schematic
uses its supported component subset; it keeps the generated design, Multisim
simulation, exported data, plot, and report tied to one source netlist.

## 1. Clarify the design

Collect from the user:

- Circuit function and constraints (input, output, supply, frequency).
- Component set and tolerance requirements.
- Required analyses: DC operating point, AC sweep, transient, or all.
- Report format and whether schematic image, netlist, BOM, or CSV data are needed.

## 2. Build and validate the netlist

- Write a SPICE netlist as the source of truth.
- Pre-validate with `ngspice` when available before touching Multisim.
- Keep node names simple and unique: `vin`, `vout`, `vdd`, `vb`, `0` work, while reserved words such as `in`/`out` can fail in the command engine.

## 3. Create or edit the design

Preferred paths, in order:

1. Start from a known-good template `.ms14` and edit its decoded XML.
2. Use `decode_ms14` to get XML, edit component values carefully (RLC values require updating both the numeric parameter and display string).
3. Use `encode_ms14` to re-encode.
4. Use `open_circuit` to load the result.

Do not try to rename RefDes by editing display text; Multisim renumbers on open.

### Netlist-driven alternative

When building a circuit from scratch, `run_spice_netlist` is the fastest path:

- Pass a SPICE netlist plus Nutmeg commands such as `dc VIN 0 10 0.1` or `tran 1u 2m`.
- Multisim's command engine needs a space-free directory; the tool creates one automatically under `MULTISIM_MCP_WORKDIR` (default `C:\msre_exp`).
- The tool appends `write <raw-path>` without a variable list; `write path v(out)` can fail with `No such vector`.
- It waits for the engine to go idle, parses the SPICE3 raw file, and returns `columns`, `n_points`, sampled `rows`, and a CSV path.
- Use `output_dir` to copy `circuit.cir`, `run.log`, `result.raw`, and `data.csv` into the workspace.
- Only use the safe `op`, `dc`, `ac`, or `tran` command subset. Never request
  unrestricted command mode unless the user explicitly asks for trusted local
  security research and the server operator enabled it.

### High-level generated experiment

For supported RLC, independent sources, diode, BJT, MOSFET, and op-amp circuits:

1. Call `run_circuit_experiment` with the validated netlist, analysis command,
   output directory, and report title.
2. Treat the returned `.ms14`, schematic PNG, raw, CSV, SVG, and Markdown as one
   experiment bundle.
3. Verify `success`, `n_points`, and expected numeric relationships before
   reporting completion.
4. Generated schematic probes are experimental; use command-engine CSV/raw data
   as the authoritative experiment result.

## 4. Run simulations

- Call `connect`, then `open_circuit`.
- `connect` is idempotent; call it at the start of the workflow even if another step already connected.
- Call `enum_outputs` and `enum_inputs` to discover valid names.
- Use `run_dc_operating_point`, `run_ac_sweep`, `run_ac_single_frequency`, or `run_transient`.
- Use `run_spice_netlist` when the design is netlist-only or a schematic does not exist yet; call `connect` (and `new_circuit` if needed) first.
- If a waveform input is required, use `set_input_data_sampled` or `set_input_data_raw` before the transient.
- If a tool reports not ready, call `stop_simulation`, check `circuit_info`, and retry once.

## 5. Analyze data

Returned data shape depends on the analysis:

- DC: two rows `(value,)`.
- AC: three rows `(frequency, real, imag)`.
- Transient: two rows `(time, real)`.

Compute mean, min, max, rise/fall, bandwidth, gain, or FFT as required. Keep the raw data in a file when the user asks for a report.
`run_spice_netlist` returns its own parsed columns and CSV; use those directly when the schematic-level analysis tools are not applicable.

## 6. Produce the report

- Export `get_circuit_image`, `report_netlist`, and `report_bom`.
- Use `generate_report` with the analysis dicts returned by `run_dc_operating_point`, `run_ac_sweep`, and `run_transient` to write a Markdown report with circuit info, exports, and measured tables.
- For `run_spice_netlist`, write the report from the returned CSV/rows and include the netlist, analysis commands, measured tables, and plots.
- Write a Markdown or CSV report with circuit description, component table, simulation setup, measured values, plots, and conclusions.
- Cite measured data and flag anything that was estimated or not verified.
- Enumeration tools return structured dicts such as `{"outputs": [...]}`; read the list from the `outputs`/`inputs`/`components` key.

## Safety

- Always stop simulations before changing inputs or saving.
- Prefer a working directory for outputs; never overwrite the user's source design without `save_circuit` to a new path.
- Do not set `unsafe_commands` or invoke `do_command_line` in ordinary circuit
  workflows. Those capabilities are disabled by default for prompt-injection
  resistance.
- This project is unofficial and not affiliated with NI.
