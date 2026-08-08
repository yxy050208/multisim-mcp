# Third-Party and Interoperability Notices

Multisim MCP is an independent interoperability project and is not affiliated
with or endorsed by NI.

- NI Multisim is not distributed with this repository. Users must install and
  license it separately.
- `electronics-workbench-decoder` is an optional external package used for
  `.ms14` XML conversion. The tested version is `0.2.0`, distributed under the
  ISC license by its respective authors. It is not vendored by this project.
- Python dependencies retain their respective licenses.

Before a public release, every committed `.ms14`, decoded XML template,
type-library dump, screenshot, and sample design must have documented provenance
and redistribution permission. Research-only artifacts should be excluded when
that provenance is uncertain.

`tools/bootstrap_local_component_pack.py` exists so users can derive optional
interoperability templates from their own licensed installation. Its output is
marked local-only and is not automatically suitable for redistribution.

Multisim and NI are trademarks of their respective owner. Use of names and file
formats in this project is descriptive and for interoperability.
