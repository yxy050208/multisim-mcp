# Architecture

## Data flow

1. The agent expresses a circuit as a constrained SPICE netlist.
2. `schematic_builder.py` converts supported components and nets into deterministic
   Multisim XML using sanitized package templates.
3. The pinned `electronics-workbench-decoder` codec encodes XML to `.ms14`.
4. `MultisimClient` opens and verifies the design through the official local COM
   Automation API.
5. The same source netlist is submitted to Multisim's command engine using an
   allowlisted analysis command.
6. `spice_raw.py` parses raw output and exports CSV/SVG artifacts.
7. The high-level MCP tool writes a reproducible Markdown report.

The source netlist is the current experiment source of truth. Generated visual
probes are experimental, so authoritative data does not depend on probe XML.

## Layers

- `server.py`: MCP schemas, safety gates, orchestration, and artifact policy.
- `multisim_client.py`: COM and `.ms14` codec adapters.
- `schematic_builder.py`: deterministic netlist-to-Multisim XML conversion.
- `spice_raw.py`: dependency-free raw parsing and plotting.
- `safety.py`: command allowlist and explicit unsafe feature flags.

## Planned architecture

The current MCP process must run under 32-bit Python. A future release should
move COM ownership to a dedicated 32-bit STA worker and keep the MCP frontend in
a normal 64-bit runtime. A local named-pipe or loopback protocol would provide:

- COM thread affinity and serialized circuit ownership.
- responsive MCP cancellation and timeout handling;
- worker restart after Multisim crashes or hangs;
- easier installation of the modern MCP SDK;
- a clearer security boundary for filesystem and command operations.

Component assets should also be split from the MIT-licensed engine before a
public release. The intended distribution model is:

- an open registry/adapter API for SPICE and Multisim component families;
- clean-room or explicitly redistributable core symbols in the wheel;
- optional user-local component packs generated from that user's licensed
  Multisim installation; and
- a provenance manifest and compatibility test for every pack.

This avoids treating every Multisim database SKU as project source code and
keeps reverse-engineered NI sample assets out of a public release unless their
redistribution terms are confirmed.

## Maturity contract

- Stable: covered by COM-free CI and a documented real-Multisim result.
- Experimental: callable, but format/version coverage remains narrow.
- Unsafe: hidden behind an explicit server-side environment opt-in.
