"""Safety policy for agent-controlled Multisim command execution."""

from __future__ import annotations

import os
import re


UNSAFE_COMMANDS_ENV = "MULTISIM_MCP_ENABLE_UNSAFE_COMMANDS"
NPX_DOWNLOAD_ENV = "MULTISIM_MCP_ALLOW_NPX_DOWNLOAD"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_SPICE_NUMBER = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+|[TtGgKkMmUuNnPpFf]|[Mm][Ee][Gg])?$"
)
_SOURCE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:$-]*$")
_SAFE_NETLIST_DIRECTIVES = frozenset(
    {
        ".end",
        ".ends",
        ".global",
        ".ic",
        ".model",
        ".nodeset",
        ".options",
        ".param",
        ".subckt",
        ".temp",
    }
)
_SUSPICIOUS_NETLIST_TOKENS = (
    "file=",
    "tablefile=",
    "codemodel=",
)


def env_flag(name: str) -> bool:
    """Return whether a documented opt-in environment flag is enabled."""
    return os.environ.get(name, "").strip().lower() in _TRUE_VALUES


def unsafe_commands_enabled() -> bool:
    """Return whether unrestricted Multisim command tools are enabled."""
    return env_flag(UNSAFE_COMMANDS_ENV)


def _require_number(value: str, label: str) -> None:
    if not _SPICE_NUMBER.fullmatch(value):
        raise ValueError(f"Invalid {label}: {value!r}")


def _require_source(value: str) -> None:
    if not _SOURCE_NAME.fullmatch(value):
        raise ValueError(f"Invalid source name: {value!r}")


def validate_analysis_commands(commands: str) -> list[str]:
    """Validate a small, non-scripting subset of Nutmeg analysis commands.

    The safe MCP surface intentionally supports only analyses needed by the
    experiment workflow. File access, command sourcing, shell escapes, control
    flow, variable substitution, and raw ``write`` commands are rejected.
    """
    accepted: list[str] = []
    for line_number, raw_line in enumerate(commands.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith(("*", ";", "#")):
            continue
        if any(char in line for char in ("`", "|", "&", ">", "<", "$", "\\n", "\\r")):
            raise ValueError(f"Unsafe command syntax on line {line_number}")

        tokens = line.split()
        command = tokens[0].lower()
        if command == "op" and len(tokens) == 1:
            accepted.append("op")
            continue

        if command == "dc" and len(tokens) == 5:
            _require_source(tokens[1])
            for index, label in zip(range(2, 5), ("DC start", "DC stop", "DC step")):
                _require_number(tokens[index], label)
            accepted.append(" ".join(tokens))
            continue

        if command == "ac" and len(tokens) == 5:
            if tokens[1].lower() not in {"dec", "oct", "lin"}:
                raise ValueError("AC sweep mode must be dec, oct, or lin")
            if not tokens[2].isdigit() or int(tokens[2]) <= 0:
                raise ValueError("AC point count must be a positive integer")
            _require_number(tokens[3], "AC start frequency")
            _require_number(tokens[4], "AC stop frequency")
            accepted.append(" ".join(tokens))
            continue

        if command == "tran" and 3 <= len(tokens) <= 6:
            numeric_tokens = tokens[1:]
            if numeric_tokens and numeric_tokens[-1].lower() == "uic":
                numeric_tokens = numeric_tokens[:-1]
            if not 2 <= len(numeric_tokens) <= 4:
                raise ValueError("Transient syntax is: tran tstep tstop [tstart [tmax]] [uic]")
            for index, value in enumerate(numeric_tokens, start=1):
                _require_number(value, f"transient argument {index}")
            accepted.append(" ".join(tokens))
            continue

        raise ValueError(
            f"Command {tokens[0]!r} is not available on the safe MCP surface "
            f"(line {line_number}); allowed commands: op, dc, ac, tran"
        )

    if not accepted:
        raise ValueError("At least one analysis command is required")
    return accepted


def validate_spice_netlist(netlist: str) -> None:
    """Reject SPICE constructs that can escape into files or control scripts.

    Device lines remain flexible enough for ordinary RLC, diode, transistor,
    MOSFET, source, and subcircuit experiments. Analysis directives belong in
    the separately validated command argument.
    """
    in_subcircuit = 0
    for line_number, raw_line in enumerate(netlist.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith(("*", ";", "#")):
            continue
        lowered = line.lower()
        normalized = re.sub(r"\s+", "", lowered)
        if any(token in normalized for token in _SUSPICIOUS_NETLIST_TOKENS):
            raise ValueError(f"External-file netlist construct on line {line_number}")
        if line.startswith("+"):
            continue
        if not line.startswith("."):
            continue

        directive = line.split(maxsplit=1)[0].lower()
        if directive not in _SAFE_NETLIST_DIRECTIVES:
            raise ValueError(
                f"Netlist directive {directive!r} is not allowed on the safe MCP "
                f"surface (line {line_number})"
            )
        if directive == ".subckt":
            in_subcircuit += 1
        elif directive == ".ends":
            in_subcircuit -= 1
            if in_subcircuit < 0:
                raise ValueError(f"Unmatched .ends on line {line_number}")
    if in_subcircuit:
        raise ValueError("Unclosed .subckt block")


__all__ = [
    "NPX_DOWNLOAD_ENV",
    "UNSAFE_COMMANDS_ENV",
    "env_flag",
    "unsafe_commands_enabled",
    "validate_analysis_commands",
    "validate_spice_netlist",
]
