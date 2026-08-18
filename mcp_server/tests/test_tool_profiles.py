"""Tests for deterministic MCP tool-discovery profiles."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from multisim_mcp.tool_profiles import (
    ALL_TOOL_NAMES,
    PROFILE_TOOL_NAMES,
    TOOL_PROFILE_ENV,
    normalize_tool_profile,
    selected_tool_profile,
    tool_enabled,
    tool_profile_status,
)


class ToolProfileTest(unittest.TestCase):
    def test_catalog_matches_every_decorated_server_tool(self) -> None:
        server_path = Path(__file__).parents[1] / "multisim_mcp" / "server.py"
        tree = ast.parse(server_path.read_text(encoding="utf-8"))
        decorated = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "mcp"
                and decorator.func.attr == "tool"
                for decorator in node.decorator_list
            )
        }
        self.assertEqual(decorated, ALL_TOOL_NAMES)
        self.assertEqual(len(ALL_TOOL_NAMES), 55)

    def test_profiles_are_bounded_and_keep_runtime_diagnostics(self) -> None:
        for name, tools in PROFILE_TOOL_NAMES.items():
            with self.subTest(profile=name):
                self.assertTrue(tools <= ALL_TOOL_NAMES)
                self.assertIn("runtime_status", tools)
        self.assertLess(len(PROFILE_TOOL_NAMES["core"]), len(ALL_TOOL_NAMES))
        self.assertLess(len(PROFILE_TOOL_NAMES["experiment"]), len(ALL_TOOL_NAMES))
        self.assertLess(
            len(PROFILE_TOOL_NAMES["optimization"]), len(ALL_TOOL_NAMES)
        )

    def test_default_is_full_and_environment_is_explicit(self) -> None:
        self.assertEqual(selected_tool_profile({}), "full")
        self.assertEqual(
            selected_tool_profile({TOOL_PROFILE_ENV: " EXPERIMENT "}),
            "experiment",
        )

    def test_invalid_profile_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown tool profile"):
            normalize_tool_profile("everything")

    def test_full_keeps_future_tools_but_bounded_profiles_do_not(self) -> None:
        self.assertTrue(tool_enabled("future_tool", "full"))
        self.assertFalse(tool_enabled("future_tool", "core"))

    def test_status_is_machine_readable(self) -> None:
        status = tool_profile_status("optimization")
        self.assertEqual(status["name"], "optimization")
        self.assertEqual(
            status["tool_count"], len(PROFILE_TOOL_NAMES["optimization"])
        )
        self.assertIn("full", status["available_profiles"])


if __name__ == "__main__":
    unittest.main()
