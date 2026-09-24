# Architecture

## Data flow

1. The agent expresses a circuit as a constrained SPICE netlist.
2. `schematic_builder.py` converts supported components and nets into deterministic
   Multisim XML using sanitized package templates.
3. The pinned `electronics-workbench-decoder` codec encodes XML to `.ms14`.
4. The MCP frontend sends an allowlisted request to `com_worker.py`; its 32-bit
   `MultisimClient` opens and verifies the design through the official local COM
   Automation API.
5. The same source netlist is submitted to Multisim's command engine using an
   allowlisted analysis command.
6. `spice_raw.py` parses raw output and exports CSV/SVG artifacts.
7. The high-level MCP tool writes a reproducible Markdown report.
8. `experiment_resources.py` registers the completed directory under an opaque
   handle and exposes allowlisted artifacts as MCP Resources.
9. For long runs, `job_engine.py` persists a queue and launches one isolated
   `job_worker.py` subprocess. Progress is checkpointed atomically; only a
   complete artifact transaction is registered by the MCP parent.

The source netlist is the current experiment source of truth. Generated visual
probes are experimental, so authoritative data does not depend on probe XML.

## Layers

- `server.py`: MCP schemas, safety gates, orchestration, and artifact policy.
- `experiment_resources.py`: opaque experiment handles, fixed artifact mapping,
  size limits, hashes, and resource reads.
- `job_engine.py`: durable state machine, queue, process monitoring, cancellation,
  leases, and restart recovery.
- `job_worker.py`: one-experiment subprocess boundary and parent liveness watch.
- `com_worker_client.py`: lazy process lifecycle, JSON-RPC proxy, timeout,
  cancellation, crash recovery, and 32-bit runtime selection.
- `com_worker.py`: stateful, allowlisted 32-bit COM/codec execution loop.
- `multisim_client.py`: COM and `.ms14` codec adapters.
- `schematic_builder.py`: deterministic netlist-to-Multisim XML conversion.
- `spice_raw.py`: dependency-free raw parsing and plotting.
- `safety.py`: command allowlist and explicit unsafe feature flags.

## MCP 2 and isolated COM ownership

MCP Python SDK 2 executes ordinary synchronous handlers on general worker
threads. The server keeps blocking calls off the event loop and serializes them
through one frontend executor. The executor does not initialize COM: it sends
newline-delimited, versioned JSON requests to a lazy, long-lived 32-bit worker.
Only the worker imports the COM adapter, owns the apartment, connection, and
current circuit, and executes allowlisted public methods.

Prompts, job-control tools, and artifact-only Resources do not access COM and
use the SDK's normal handler execution, so job status and cancellation remain
responsive while a synchronous COM tool is busy. Completed durable jobs restore
their experiment resource handles automatically after restart; other historical
directories can be restored with `register_experiment_artifacts`.

The worker reader thread can receive cancellation while the main worker thread
is inside `run_command_file`. Heartbeat events flow back to the calling job,
while cancellation flows to a worker-owned event checked by `MultisimClient`.
RPC timeout or invalid protocol output terminates only the child; the next call
starts a clean worker. A parent-PID watcher prevents orphan COM workers.

## Resilient job architecture

Synchronous compatibility tools use the same isolated COM worker proxy.
`submit_circuit_experiment` additionally runs the complete workflow in a fresh
job subprocess. The parent persists
the specification and public state as atomic JSON, monitors worker heartbeats,
and terminates only the isolated worker on cancellation, overall timeout,
heartbeat timeout, or crash. A parent-liveness watcher prevents orphan workers.

Each output directory has both an active-job reservation and a sibling lock
lease. A queue-wide worker lease also preserves single-worker execution when
multiple MCP frontend processes share the same state directory. Artifacts are
built in a unique staging directory and atomically
published only after the full 15-file set is verified. An interrupted
`running`/`cancelling` record is recovered as `queued` when the MCP server
restarts; the new attempt rebuilds from the source specification instead of
trusting partial files.

The MCP frontend may use 32-bit or 64-bit Python. `MULTISIM_MCP_WORKER_PYTHON`
can select the 32-bit runtime explicitly; otherwise a 32-bit frontend reuses
itself and a 64-bit Windows frontend tries the `py -3-32` launcher. Source mode
injects only the project root, never the frontend's architecture-specific
`site-packages`. The state machine intentionally does not depend on transport types;
MCP SDK 2.0 exposes Tasks protocol types but not high-level server/client task
handlers, so a future official Tasks adapter can map existing job IDs and states
without a storage migration.

This process boundary provides:

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
