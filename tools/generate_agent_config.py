"""Generate client-neutral MCP configuration for supported AI agents.

The frontend Python may be 64-bit; MULTISIM_MCP_PYTHON must point to the
32-bit Python that owns pywin32 and the Multisim COM worker.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from pathlib import Path


CLIENTS = {"qwen", "chatgpt", "clawcode", "workbody", "deepseek"}


def build_config(client: str, python32: str, repo: str, workdir: str) -> dict:
    if client not in CLIENTS:
        raise ValueError(f"unsupported client: {client}")
    command = [python32, "-m", "multisim_mcp.cli", "serve"]
    environment = {
        "MULTISIM_MCP_WORKDIR": workdir,
        "MULTISIM_MCP_REPO": repo,
        "PYTHONPATH": str(Path(repo) / "mcp_server"),
        "MULTISIM_MCP_PYTHON_ROLE": "32-bit-worker",
    }
    return {"mcpServers": {"multisim": {"command": command, "args": [], "env": environment}},
            "client": client, "runtime": {"frontend_python_bits": struct.calcsize("P") * 8,
                                             "worker_python_bits": 32}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True, choices=sorted(CLIENTS))
    parser.add_argument("--python32", default=os.environ.get("MULTISIM_MCP_PYTHON", ""))
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--workdir", default=str(Path.cwd() / "multisim-work"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.python32:
        raise SystemExit("set --python32 or MULTISIM_MCP_PYTHON to a 32-bit Python")
    payload = build_config(args.client, args.python32, args.repo, args.workdir)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
