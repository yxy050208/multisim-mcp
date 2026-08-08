# Security Policy

## Supported versions

The project is currently alpha software. Security fixes are applied to the
latest development version only.

## Trust boundary

Multisim MCP is a local stdio server with the same filesystem and desktop
permissions as the user who starts it. A connected agent can open designs,
start the Multisim GUI, run simulations, and create artifacts. Do not expose
the server directly to an untrusted network or untrusted MCP client.

Safe defaults:

- Only `op`, `dc`, `ac`, and `tran` command-engine analyses are accepted.
- Arbitrary command files are disabled unless
  `MULTISIM_MCP_ENABLE_UNSAFE_COMMANDS=1` is set by the server operator.
- Runtime npm downloads are disabled unless
  `MULTISIM_MCP_ALLOW_NPX_DOWNLOAD=1` is explicitly enabled.
- Existing artifacts are not overwritten without an explicit tool argument.

Treat both opt-in environment variables as a reduction of the security
boundary. Do not enable them for workflows that process untrusted prompts,
documents, netlists, or repository content.

## Reporting a vulnerability

Report vulnerabilities through a private
[GitHub Security Advisory](https://github.com/yxy050208/multisim-mcp/security/advisories/new).
Include reproduction steps, affected Multisim/Python versions, and the smallest
non-sensitive fixture possible. Do not open a public issue before a fix or
coordinated disclosure plan is available.

Do not include NI license files, proprietary sample designs, or personal data
in a public report.
