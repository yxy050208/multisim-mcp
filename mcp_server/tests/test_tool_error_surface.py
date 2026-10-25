"""Tool errors must reach the MCP client with their message attached."""

import unittest

from mcp.server.mcpserver.exceptions import ToolError

from multisim_mcp.server import _wrap_with_tool_error_reporting


class ToolErrorSurfaceTest(unittest.TestCase):
    def test_expected_errors_are_wrapped_with_their_message(self) -> None:
        def boom() -> dict:
            raise RuntimeError("No circuit is open")

        wrapped = _wrap_with_tool_error_reporting(boom)
        with self.assertRaisesRegex(ToolError, "No circuit is open"):
            wrapped()

    def test_missing_circuit_file_is_reported_without_com(self) -> None:
        from multisim_mcp.multisim_client import MultisimClient

        client = MultisimClient()
        with self.assertRaisesRegex(FileNotFoundError, "circuit file not found"):
            client.open_circuit(r"Z:\definitely-missing\设计XYZ.ms14")


if __name__ == "__main__":
    unittest.main()
