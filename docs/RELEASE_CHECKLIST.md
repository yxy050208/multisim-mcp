# Public Release Checklist

## Required before v0.1.0-alpha

- [x] Add the final GitHub repository URLs to `pyproject.toml` and configure
      `origin` after the repository name is chosen.
- [x] Keep NI-derived XML templates and `.ms14` files out of the public source
      tree, wheel, and source distribution.
- [x] Provide a tested user-local pack generator and template-directory overlay
      so uncertain extracted assets can be omitted from the public distribution.
- [x] Remove or unstage local type-library dumps, decoded designs, installed NI
      samples, and research artifacts that are not required by the package.
- [x] Remove personal usernames and absolute workstation paths from public
      fixtures and development scripts.
- [x] Build code-only wheel and source distributions containing the MIT license
      and `manifest.json`, with no extracted XML templates.
- [x] Run 48 COM-free tests and 19 strict local-pack package/resource tests after
      the expansion.
- [x] Run real Multisim 14.3 component-family regressions for passive/source,
      semiconductor/switch, op-amp, controlled/behavioral source, transmission-line,
      generic subcircuit, preview digital, XFG, and XSC families.
- [x] Run `run_circuit_experiment` and retain a sanitized result manifest for a
      resistor-divider smoke experiment.
- [x] Enable GitHub private vulnerability reporting and update `SECURITY.md`.
- [x] Create the first clean commit and tag it `v0.1.0-alpha`.

## Do not publish

- NI executables, DLLs, CHM files, license files, or installed application trees.
- NI/API Toolkit sample circuits unless their redistribution terms explicitly
  allow it.
- Competitor repository snapshots or archives.
- Embedded Python runtimes, wheels, virtual environments, raw test outputs, or
  user-specific absolute paths.

The current Git index predates this checklist. Review staged files explicitly;
adding an ignore rule does not remove a file that is already staged.
