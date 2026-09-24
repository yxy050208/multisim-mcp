"""Keep template readiness consistent across the exposed diagnostics."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from multisim_mcp import server


def _report(*, extended: list[str] | None = None, core: list[str] | None = None) -> dict:
    extended = list(extended or [])
    core = list(core or [])
    missing = {kind: [f"{kind}_element.xml"] for kind in [*core, *extended]}
    return {
        "search_paths": [Path("C:/pack")],
        "component_kinds": 46,
        "missing_by_kind": missing,
        "unavailable_kinds": sorted([*core, *extended]),
        "core_missing_kinds": sorted(core),
        "extended_missing_kinds": sorted(extended),
        "complete": not core and not extended,
    }


class RuntimeStatusReadinessTest(unittest.TestCase):
    def _runtime(self, completeness: dict, status: str) -> dict:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("minimal.ms14.xml", "wire.xml", "r_element.xml"):
                (root / name).write_text("fixture", encoding="utf-8")
            completeness = {**completeness, "search_paths": [root]}
            return self._runtime_with_pack(completeness, status)

    def _runtime_with_pack(self, completeness: dict, status: str) -> dict:
        with (
            patch("multisim_mcp.server.worker_runtime_diagnostics", return_value={}),
            patch("multisim_mcp.server.template_completeness", return_value=completeness),
            patch("multisim_mcp.server.template_status", return_value=status),
            patch("multisim_mcp.server.tool_profile_status", return_value={}),
            patch("multisim_mcp.server.build_capabilities", return_value={}),
            patch("multisim_mcp.server._eda_application_service") as service,
            patch("multisim_mcp.server.NgspiceBackend.probe_runtime", return_value={}),
        ):
            service.return_value.discover_backends.return_value = []
            return server.runtime_status()

    def test_optional_missing_family_is_not_reported_ready(self) -> None:
        result = self._runtime(_report(extended=["TIMER8"]), "warn")
        self.assertFalse(result["schematic_templates_ready"])
        self.assertEqual(result["schematic_templates_status"], "warn")
        self.assertEqual(result["extended_missing_component_kinds"], ["TIMER8"])

    def test_core_missing_family_is_a_failure(self) -> None:
        result = self._runtime(_report(core=["R"]), "fail")
        self.assertFalse(result["schematic_templates_ready"])
        self.assertEqual(result["schematic_templates_status"], "fail")
        self.assertEqual(result["core_missing_component_kinds"], ["R"])


class ComponentCatalogReadinessTest(unittest.TestCase):
    def test_optional_missing_family_is_not_reported_ready(self) -> None:
        with (
            patch(
                "multisim_mcp.server.template_completeness",
                return_value=_report(extended=["TIMER8"]),
            ),
        ):
            result = server.schematic_component_catalog()
        self.assertFalse(result["schematic_templates_ready"])
        self.assertEqual(result["schematic_templates_status"], "warn")
        self.assertEqual(result["extended_missing_kinds"], ["TIMER8"])


if __name__ == "__main__":
    unittest.main()
