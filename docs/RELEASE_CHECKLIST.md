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

## Required before v0.1.0-alpha.2

- [x] Verify Windows COM-free tests and package boundary checks.
- [x] Build the Linux validation image from the allowlisted Docker context.
- [x] Complete MCP initialize and `tools/list` through container stdio.
- [x] Confirm `runtime_status` reports `introspection-only` on Linux.
- [x] Publish `0.1.0a2` to PyPI and update the official MCP Registry.
- [x] Verify GitHub Release assets match the canonical PyPI SHA-256 hashes.
- [x] Add the Glama score badge to the awesome-mcp-servers submission.

## Required before v0.1.0-alpha.3

- [x] Add backward-compatible `doctor`, `serve`, and client `config` commands.
- [x] Keep JSON/MCP stdout free of pywin32 import diagnostics.
- [x] Verify a real 32-bit Multisim 14.3 COM connection with `doctor --connect`.
- [x] Add dynamic Dispatch fallback for a corrupt pywin32 generated-wrapper cache.
- [x] Run the real generated divider workflow and verify a 0.5 output ratio.
- [x] Pass 63 COM-free tests, skill validation, code-only wheel JSON, and stdio checks.
- [x] Publish `0.1.0a3` to PyPI through Trusted Publishing.
- [x] Publish the matching official MCP Registry metadata.
- [x] Create and push the annotated `v0.1.0-alpha.3` tag and GitHub Release.

## Required before v1.0.0

- [x] Complete all four phases in `ROADMAP_TO_1.0.md`.
- [x] Synchronize `pyproject.toml`, `multisim_mcp.__version__`, and both
      `server.json` version fields at `1.0.0`.
- [x] Pass 116 COM-free tests on both 32-bit and 64-bit Python.
- [x] Pass modern and legacy MCP stdio introspection with 51 tools, 19 Resource
      templates, and 5 prompts.
- [x] Verify all 13 portable adapters open and reverse-export in Multisim 14.3,
      plus representative analog, power, digital, and complex-AC simulations.
- [x] Verify standalone Chinese/English HTML/PDF reports and SHA-256 manifests.
- [x] Build and inspect wheel/sdist with no NI XML, `.ms14`, experiment output,
      research artifacts, or development environment files.
- [x] Publish migration, recovery, compatibility, adapter, contribution, and
      bilingual installation documentation.
- [x] Produce bilingual `RELEASE_NOTES_v1.0.0.md` and pass the local release audit.
- [x] Push the release commit and wait for all public CI jobs to pass.
- [x] Push annotated tag `v1.0.0` and verify the Trusted Publishing result on PyPI.
- [x] Publish matching MCP Registry metadata and GitHub Release artifacts.
- [x] Verify Glama and awesome-mcp-servers directory metadata after publication.

## Required before v1.1.0

- [x] Complete the versioned project/experiment/optimization directory manifest
      contract and integrate it into atomic experiment and sweep publication.
- [x] Synchronize `pyproject.toml`, `multisim_mcp.__version__`, and both
      `server.json` version fields at `1.1.0`.
- [x] Keep public protocol compatibility at 55 tools, 19 Resource templates,
      and 5 prompts.
- [x] Pass 271 COM-free tests on 32-bit Python plus the 64-bit core manifest,
      pipeline, and job-engine regression set.
- [x] Produce Chinese-first bilingual `RELEASE_NOTES_v1.1.0.md` and include it
      in the local release audit.
- [x] Push the release PR and wait for every public CI job to pass.
- [x] Build and inspect the final wheel/sdist from the reviewed release commit.
- [ ] Push annotated tag `v1.1.0` and verify it dereferences to the reviewed
      `main` merge commit.
- [ ] Manually dispatch `publish-pypi.yml` at `v1.1.0`, verify the workflow head
      SHA, Trusted Publishing result, and PyPI distribution SHA-256 values.
- [ ] Create the bilingual GitHub Release and attach only the exact verified
      PyPI distributions when binary attachments are desired.
- [ ] Publish matching MCP Registry metadata and verify official search results.
- [ ] Re-check Glama and awesome-mcp-servers metadata after publication.

## DeepSeek Harness npm bundle 1.1.0

- [x] Pin the official Harness CLI and MCP client compatibility baseline.
- [x] Restrict the npm tarball to four reviewed files.
- [x] Add a Registry ownership/version guard with unit tests.
- [x] Add verify-only and stage-existing GitHub Actions paths.
- [ ] Re-run the Registry guard immediately before the first publication.
- [ ] Publish `multisim-mcp-dsh-plugin@1.1.0` interactively with maintainer 2FA.
- [ ] Configure `publish-dsh-plugin.yml` as a stage-only npm Trusted Publisher.
- [ ] Protect the GitHub `npm` Environment with required reviewers.
- [ ] Disallow traditional publish tokens and verify package provenance on the
      next staged release.
