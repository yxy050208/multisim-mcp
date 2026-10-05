# Multi-board interface fixture contract

## Physical connector contract

The partition planner also accepts an optional top-level `connectors` list.
The older component-only request remains valid for logical previews, but its
auto-created `J1`/`J2` records are marked `connector_contract.status=inferred`
and are not evidence of a real connector part or pin mapping.

An engineering-ready request supplies one explicit contract per physical
connector.  A connector has a part number, one instance on every participating
board, and a contiguous pin map.  Every pin must cover exactly the boards that
own its cross-board net; a missing pin, extra board, duplicate net, invalid
ground type, endpoint direction conflict, or board pin-capacity violation makes
the candidate infeasible.

```json
{
  "id": "J1",
  "part": "HEADER_1X2",
  "boards": ["power", "signal"],
  "instances": [
    {"board": "power", "refdes": "J1P"},
    {"board": "signal", "refdes": "J1S"}
  ],
  "pins": [
    {"number": 1, "net": "bus", "signal_type": "analog", "direction": "bidirectional"},
    {"number": 2, "net": "0", "signal_type": "ground", "direction": "passive"}
  ]
}
```

Supported `signal_type` values are `power`, `ground`, `analog`, `digital`,
`clock`, `signal`, and `passive`.  `direction` can be `in`, `out`,
`bidirectional`, `passive`, or `power`.  Optional `board_directions` allows a
pin to declare its endpoint directions explicitly; two endpoints both marked
`in` or both marked `out` are rejected.  Optional voltage, current and
impedance fields are retained in the plan for later native/ERC checks.

The plan expands the physical connector into board-local interface records,
including the connector part and the exact board instance.  This is a
structural contract only: Multisim must still open/save/reopen the generated
projects and provide ReportNetlist and native analysis evidence before the
multi-board result can be called `native-verified`.

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
