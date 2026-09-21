# Community contributors

## CoralFlower325

CoralFlower325 contributed the native Multisim validation and schematic-generation work delivered through [PR #17](https://github.com/yxy050208/multisim-mcp/pull/17) and its validated follow-up [PR #19](https://github.com/yxy050208/multisim-mcp/pull/19).

Their work includes:

- aligning round-trip topology checks with Multisim `ReportNetlist` behavior;
- binding native XSPICE model objects for digital and carrier devices;
- ordering digital layouts and gating excessive wire crossings; and
- preserving native reference designators such as `R0` and `AINV0` through schematic encoding and reopening.

The contribution was tested against Multisim 14.3 and is part of the current `main` branch.