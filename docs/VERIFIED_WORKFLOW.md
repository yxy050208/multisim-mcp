# Verified End-to-End Workflow

This document records the workflow that has been verified against a local
NI Multisim 14.3 installation. The MCP server drives the official
Automation API (COM) with a 32-bit Python interpreter.

## Reproduce

```powershell
cd mcp_server
.\run_server.ps1
```

Run the end-to-end test from the repository root:

```powershell
tools\python32\python.exe mcp_server\tests\e2e_mcp_test.py
```

The test launches real Multisim and exercises the core MCP workflow,
including `run_spice_netlist`. Results are written to
`analysis/out/e2e_mcp/e2e_mcp_results.json`, with a generated Markdown
report, netlist, BOM, schematic image, and saved circuit in the same
directory.

## Tool Coverage (33 Tools)

Lifecycle: `connect`, `disconnect`, `open_circuit`, `new_circuit`,
`circuit_info`, `save_circuit`, `runtime_status`

Enumeration and components: `enum_components`, `enum_inputs`,
`enum_outputs`, `get_rlc_value`, `set_rlc_value`

Simulation and data: `set_output_request`, `get_output_data`,
`run_transient`, `run_dc_operating_point`, `run_ac_sweep`,
`run_ac_single_frequency`, `set_input_data_sampled`,
`set_input_data_raw`, `clear_input_data`, `stop_simulation`,
`run_spice_netlist`

Export and reporting: `get_circuit_image`, `report_netlist`,
`report_bom`, `do_command_line`, `generate_report`

`.ms14` XML codec: `decode_ms14`, `encode_ms14`

High-level generation and experiment: `create_schematic_from_netlist`,
`run_circuit_experiment`

## Verified Results

- `connect` reports Multisim 14.3 and the installed application path.
- `open_circuit` loads the NI API Toolkit `RLC Values.ms14` sample.
- `enum_components` returns `R1, R2, U1, V1, V2, V3`.
- `enum_outputs` returns `V(OutProbe), I(OutProbe)`.
- `set_rlc_value("R1", 100.0)` changes the value and `get_rlc_value`
  reads it back as `100.0`.
- `run_dc_operating_point` returns a 2 x 1 data matrix.
- `run_ac_sweep` returns a 3 x 41 matrix: frequency, real, imag.
- `run_transient` returns a 2 x 109 matrix: time, real.
- `generate_report` writes a Markdown report with circuit info,
  components, inputs/outputs, exports, and analysis tables.
- `save_circuit`, netlist, BOM, and PNG export all return real paths.
- `run_spice_netlist` runs netlist-only designs: a DC sweep returned 101
  points and a transient returned 2219 points, with raw/CSV artifacts
  copied into the requested workspace directory.
- `run_circuit_experiment` generated editable resistor-divider and RLC `.ms14`
  designs, verified every generated component through Multisim's exported native
  netlist,
  opened and enumerated it in Multisim 14.3, exported a schematic PNG, ran a
  101-point DC sweep through the Multisim engine, and wrote raw, CSV, SVG, and
  Markdown report artifacts from the same source netlist.
- The component-family E2E regression opens and reverse-exports R/L/C/V/I,
  D/Q/M/J/Z, S/W, B/E/F/G/H, K/T/O/U, OPAMP5, generic 2–16-pin X blocks, and
  native preview logic/JK parts.
- Real transient regressions produced correct four-state truth tables for
  AND/NAND/OR/NOR/XOR/XNOR and correct alternating Q/QBAR edges for JKFF.
- Native XFG1 and XSC1 state survives Multisim open/save. Their combined
  experiment exported complementary generator waveforms to CSV and Markdown at
  `analysis/out/component_expansion/xfg_experiment/`.

## Lab Verification

Two submitted lab requirements were reproduced end to end through
`run_spice_netlist`:

- Lab 5, CMOS inverter static characteristics: `dc VIN 0 10 0.1`, 101
  points. VIN=0 V gives VOUT about 10 V, VIN=10 V gives VOUT about 0 V,
  IDD peaks at about 5.549 mA near VIN=4.4 V, and the VIN=VOUT threshold
  is about 4.423 V.
- Lab 1, BJT switch dynamic characteristics: `tran 1u 2m`, 2219 points.
  VOH about 5 V, VOL about 0.018 V, tPLH about 951 ns, tPHL about 2.64 ns,
  tR about 1.21 us, tF about 3.21 ns.

Artifacts live under
`analysis/out/experiments/lab_outputs/lab5_cmos_report.md`,
`analysis/out/experiments/lab_outputs/lab1_bjt_report.md`, with CSV and
PNG charts under `cmos/` and `bjt/`.

## Agent Workflow (MCP + Skill)

1. Collect requirements: function, components, analyses, report format.
2. Start from a known-good `.ms14` template, or `decode_ms14` an existing
   design and edit its XML.
3. `connect`, then `open_circuit`.
4. Enumerate inputs/outputs, set RLC values, inject input waveforms.
5. Run DC/AC/transient analyses and keep the raw data.
6. Export netlist, BOM, and schematic image.
7. `generate_report` with analysis dicts to produce the final Markdown
   report.

The skill file at `skills/multisim-workflow/SKILL.md` encodes these steps
for the agent.

## Known Constraints

- Windows only; Multisim must be installed and licensed.
- The COM server is 32-bit, so the MCP host must use 32-bit Python.
- Simulation is not headless; Multisim opens on the desktop.
- The Automation API exposes no place-component or wire-drawing methods, so
  schematic generation is template/XML based, not automatic drawing.
- `run_spice_netlist` uses a space-free scratch root; set
  `MULTISIM_MCP_WORKDIR` to override the `C:\msre_exp` default.
- `GetOutputData` must receive `pythoncom.Missing` for its output
  parameters.
- List-returning MCP tools return structured dicts such as
  `{"outputs": [...]}` because FastMCP splits bare lists into multiple
  text contents.
- This project is unofficial and not affiliated with NI.
- Arbitrary command files are disabled by default. Safe netlist experiments
  accept only `op`, `dc`, `ac`, and `tran` commands.
