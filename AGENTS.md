# Open-source development scope

This checkout develops the public MIT-licensed Multisim MCP core.

- Prioritize frontier engineering capabilities with reproducible evidence:
  complex circuits, component mapping, native verification, simulation, report
  export, protocol compatibility, and public release checks.
- Basic multi-board integration remains an open-source development goal. Support
  explicit board assignments, connector/pin/fixture contracts, per-board native
  generation and simulation, cross-board interface validation, consolidated
  reports, and regression coverage. State supported versions and device families.
- The commercial product prioritizes convenient installation, configuration,
  guided workflows, upgrades, and support in a separate private checkout.
  Keep existing public installation, CLI, and MCP connectivity compatible.
- Future proprietary multi-board optimization, cross-board joint parameter or
  topology search, and advanced placement/wire-routing optimization belong to
  the commercial product. Do not implement, import, or publish those extensions here.
- Support technical-leadership claims with measured public benchmarks and native
  evidence; do not claim universal superiority without a current comparison.
- Preserve electrical-correctness fixes and compatibility of already public APIs.
  Basic pin connectivity and truthful validation remain core responsibilities.
- Previously published multi-board and layout code retains its existing license;
  do not remove attribution, rewrite public history, or describe it as private.
- Do not copy product bundles, customer data, private models, commercial prompts,
  evaluation sets, credentials, or deployment configuration from other checkouts.
  Shared folders and Git branches do not grant permission to publish their contents.
- Review provenance before importing any patch from the commercial checkout.
  Ambiguous assets remain local until the owner identifies their public scope.
- Stage exact reviewed files. Run `python tools/release_audit.py --json` before
  public delivery and verify built wheel/sdist with `tools/check_mcp_package.py`.

See [the public scope policy](docs/OPEN_SOURCE_SCOPE.md) and
[publishing guide](docs/PUBLISHING.md).
