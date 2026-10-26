# Multisim MCP 1.3.0

`1.3.0` is the stable public core line prepared from the accumulated engineering
work. The Python package, `server.json`, compatibility manifest, and source
release documentation use the same core version. The DeepSeek Harness bundle is
versioned independently as `multisim-mcp-dsh-plugin@1.3.0` and remains a thin
adapter around the separately installed Python MCP server.

## What is included

- Complex-circuit engineering contracts covering structured requirements,
  component/model provenance, deterministic netlist validation, bounded
  parameter/topology optimization, reports, and approval-gated patch workflows.
- Native Multisim 14.3 evidence for the documented single-board, hybrid, and
  basic two-board fixtures, including save/reopen, topology, layout, analysis,
  cross-board interfaces, fault rejection, and report artifacts.
- ngspice execution and cross-backend comparison through the transport-neutral
  EDA and optimization services.
- Basic single-board layout validation and bounded position optimization. A
  candidate is rejected for electrical, topology, endpoint, overlap, or body
  violations before geometry scores are compared.
- Versioned component mapping and model-provenance records. Unverified Multisim
  versions remain `manifest-unavailable` or `unverified`; evidence is never
  borrowed from another installed version.
- Read-only Agent/Workbench planning plus explicit approval gates for writes,
  experiments, recovery, and artifact export.

## Verification boundary

The public CI and local COM-free regression suite validate contracts, packaging,
MCP stdio, ngspice, workflow recovery, and public-asset boundaries. Native
claims are limited to the exact Multisim version, local licensed template pack,
component families, fixtures, and analyses recorded in the evidence documents.
This release does not claim arbitrary-circuit generation, universal component
coverage, production PCB readiness, or automatic PCB routing.

The repository does not ship NI-derived XML templates, `.ms14` projects, local
models, licenses, or experiment output. A user must generate a local component
pack from an authorized Multisim installation before native generation.

## Publication order

1. Run the release audit, clean package build, targeted/full tests, and CI.
2. Create and push the annotated `v1.3.0` tag only from the reviewed commit.
3. Dispatch the PyPI and MCP Registry workflows at that tag and verify live
   metadata and SHA-256 values.
4. Verify and publish the independent npm bundle through the protected Trusted
   Publishing workflow; do not infer npm publication from the Git tag.
