# Multi-board interface fixture contract

Partition planning identifies the nets that cross a board boundary. It cannot
decide what the other board supplies during an isolated test. The fixture
contract makes that test condition explicit and keeps it separate from the
generated schematic.

The current contract is version `1` and supports four fixture kinds:

| kind | purpose | generated component |
| --- | --- | --- |
| `voltage_source` | drive a board-local interface net | `V<...> net reference_net value` |
| `resistor_termination` | add an explicit resistive load | `R<...> net reference_net value` |
| `ground_reference` | assert that a net is the ground reference | none |
| `observation` | request a voltage probe on a net | none; returned in `probe_nets` |

Every fixture has `id`, `board_id`, `kind`, and `net`. Source and termination
fixtures additionally require `reference_net` and a matching `refdes` prefix.
A voltage source may use a single SPICE-token `value`, or an explicit source
`model` tail such as `DC 0 AC 1` for AC/TRAN acceptance; the two forms cannot
be combined. Terminations require a single SPICE-token `value`. A ground
fixture only accepts `0`, `gnd`, or `ground`; observation fixtures currently
request voltage measurements.

Example for the two-board power-plus-divider test:

```json
[
  {"id":"power-bus-observe","board_id":"power","kind":"observation","net":"bus"},
  {"id":"power-bus-anchor","board_id":"power","kind":"resistor_termination","net":"bus","reference_net":"0","refdes":"RFIX1","value":"1G"},
  {"id":"power-ground","board_id":"power","kind":"ground_reference","net":"0"},
  {"id":"signal-bus-drive","board_id":"signal","kind":"voltage_source","net":"bus","reference_net":"0","refdes":"VFIX1","value":"5"},
  {"id":"signal-bus-observe","board_id":"signal","kind":"observation","net":"bus"},
  {"id":"signal-ground","board_id":"signal","kind":"ground_reference","net":"0"},
  {"id":"signal-sense-observe","board_id":"signal","kind":"observation","net":"sense"}
]
```

Use `validate_multiboard_fixture_contract` before generation. It reports
duplicate IDs, component-reference collisions, multiple voltage drivers,
missing nets, and uncovered cross-board interfaces. Use
`materialize_multiboard_fixture_artifacts` to add the declared portable source
or termination and to obtain each board's `probe_nets` list. It rejects
uncovered interfaces by default; `allow_uncovered=true` is for review-only
previews and does not change the `logical-only`/`unverified` status.

An observation also needs a physical wire anchor: the named net must have at
least two terminals after declared source/termination fixtures are included.
This catches the common single-source-net case where the command engine can
report a voltage but Multisim has no drawable wire segment on which to place a
native probe.

After native runs, a DC caller may pass scalar endpoint values to
`compare_multiboard_interface_observations`. For TRAN and AC, use
`compare_multiboard_interface_series` with the decoded `*-series.json` records:
every native sample and the sampling axis must match, and AC compares real and
imaginary parts. No interpolation is performed. Missing or downsampled
endpoints remain `unverified`; they are never interpreted as `0`. A comparison
passes only when every selected endpoint is present and within the declared
tolerance at every sample.

The returned artifact is ready for the next native stage only as an input. It
does not prove an `.ms14` file. A native acceptance record still requires, for
the installed Multisim version, schematic generation, open/save round-trip,
topology and pin-connection readback, simulation, CSV export, and report
comparison.
