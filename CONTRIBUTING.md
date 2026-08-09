# Contributing

Contributions are welcome, especially for component templates, deterministic
layout, simulation measurements, installation diagnostics, and redistributable
test fixtures.

## Development rules

1. Do not commit NI executables, DLLs, license material, installed samples, or
   third-party designs without documented redistribution permission.
2. Keep unrestricted command execution disabled by default.
3. Add a COM-free unit test for deterministic logic.
4. For COM behavior, record the Multisim version and attach a sanitized result
   manifest rather than proprietary source files.
5. Run the unit suite and build/install the wheel before submitting a change.

```powershell
$env:PYTHONPATH='mcp_server'
tools\python32\python.exe -m unittest discover -s mcp_server/tests -t mcp_server -v
tools\python32\python.exe -m pip wheel --no-deps mcp_server
```

Generated schematic support must be labelled experimental until the design can
be opened, enumerated, exported, and simulated through a reproducible test.

## Declarative component adapters

Prefer a portable adapter when a contribution can be expressed using ordinary
SPICE primitives. Start from
[`docs/component-adapter.example.json`](docs/component-adapter.example.json),
add COM-free expansion and boundary tests, and update
[`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md). Adapter packs must not contain
executable code, external-file directives, extracted NI XML, or vendor models
without explicit redistribution permission. See
[`docs/COMPONENT_ADAPTERS.md`](docs/COMPONENT_ADAPTERS.md) for the interface.
