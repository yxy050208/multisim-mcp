"""COM-free MCP protocol smoke test against the installed entry point."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import multisim_mcp
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client


class McpStdioSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_and_list_tools(self) -> None:
        package_root = Path(multisim_mcp.__file__).resolve().parent.parent
        environment = get_default_environment()
        environment["PYTHONPATH"] = str(package_root)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "multisim_mcp.server"],
            env=environment,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.list_tools()
                names = {tool.name for tool in response.tools}

        self.assertIn("runtime_status", names)
        self.assertIn("schematic_component_catalog", names)
        self.assertIn("create_schematic_from_netlist", names)
        self.assertIn("run_circuit_experiment", names)
        self.assertIn("run_spice_netlist", names)


if __name__ == "__main__":
    unittest.main()
