"""COM-free MCP protocol smoke test against the installed entry point."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import multisim_mcp
from mcp import Client
from mcp.client.stdio import (
    StdioServerParameters,
    get_default_environment,
    stdio_client,
)
from multisim_mcp.tool_profiles import PROFILE_TOOL_NAMES, TOOL_PROFILE_ENV


class McpStdioSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def _connect(
        self, mode: str, tool_profile: str | None = None
    ) -> tuple[str, set[str], set[str], set[str], dict | None]:
        package_root = Path(multisim_mcp.__file__).resolve().parent.parent
        environment = get_default_environment()
        import_paths = [str(package_root), *(item for item in sys.path if item)]
        environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(import_paths))
        if tool_profile:
            environment[TOOL_PROFILE_ENV] = tool_profile
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "multisim_mcp.server"],
            env=environment,
        )
        async with Client(stdio_client(params), mode=mode) as session:
            tools = await session.list_tools()
            prompts = await session.list_prompts()
            resources = await session.list_resource_templates()
            experiment = next(
                (
                    tool
                    for tool in tools.tools
                    if tool.name == "run_circuit_experiment"
                ),
                None,
            )
            return (
                session.protocol_version,
                {tool.name for tool in tools.tools},
                {prompt.name for prompt in prompts.prompts},
                {item.uri_template for item in resources.resource_templates},
                experiment.output_schema if experiment else None,
            )

    async def test_modern_and_legacy_clients_discover_full_surface(self) -> None:
        for mode, expected_protocol in (
            ("2026-07-28", "2026-07-28"),
            ("legacy", "2025-11-25"),
        ):
            protocol, names, prompts, resources, output_schema = await self._connect(
                mode
            )
            self.assertEqual(protocol, expected_protocol)
            self.assertEqual(len(names), 78)
            self.assertEqual(len(prompts), 5)
            self.assertEqual(len(resources), 20)

            self.assertIn("runtime_status", names)
            self.assertIn("schematic_component_catalog", names)
            self.assertIn("create_schematic_from_netlist", names)
            self.assertIn("run_circuit_experiment", names)
            self.assertIn("submit_circuit_experiment", names)
            self.assertIn("plan_design_options", names)
            self.assertIn("select_design_option", names)
            self.assertIn("prepare_design_specification", names)
            self.assertIn("prepare_netlist_draft", names)
            self.assertIn("resolve_component_requirements", names)
            self.assertIn("approve_component_resolution", names)
            self.assertIn("compile_executable_netlist", names)
            self.assertIn("approve_executable_netlist", names)
            self.assertIn("approve_simulation_plan", names)
            self.assertIn("submit_design_optimization", names)
            self.assertIn("diagnose_design", names)
            self.assertIn("evaluate_design_patch", names)
            self.assertIn("get_experiment_job", names)
            self.assertIn("list_experiment_jobs", names)
            self.assertIn("cancel_experiment_job", names)
            self.assertIn("retry_experiment_job", names)
            self.assertIn("register_experiment_artifacts", names)
            self.assertIn("run_verified_circuit_experiment", names)
            self.assertIn("measure_experiment", names)
            self.assertIn("verify_experiment_requirements", names)
            self.assertIn("plan_experiment_sweep", names)
            self.assertIn("run_experiment_sweep", names)
            self.assertIn("submit_experiment_sweep", names)
            self.assertIn("register_sweep_artifacts", names)
            self.assertIn("component_adapter_catalog", names)
            self.assertIn("build_behavioral_reference", names)
            self.assertIn("run_behavioral_reference", names)
            self.assertIn("read_virtual_multimeter", names)
            self.assertIn("analyze_bode_response", names)
            self.assertIn("analyze_logic_signals", names)
            self.assertIn("export_formal_experiment_report", names)
            self.assertIn("list_experiment_artifacts", names)
            self.assertIn("read_experiment_artifact", names)
            self.assertIn("export_experiment_artifact", names)
            self.assertIn("get_experiment_summary", names)
            self.assertIn("compare_experiment_backends", names)
            self.assertIn("audit_spice_compatibility", names)
            self.assertIn("run_spice_netlist", names)
            self.assertIn("create_circuit_experiment", prompts)
            self.assertIn("verify_design_requirements", prompts)
            self.assertIn("multisim://experiments/{experiment_id}/manifest", resources)
            self.assertIn("multisim://experiments/{experiment_id}/schematic", resources)
            self.assertIn("multisim://jobs/{job_id}", resources)
            self.assertIn("multisim://experiments/{experiment_id}/verification", resources)
            self.assertIn("multisim://experiments/{experiment_id}/formal-html-zh", resources)
            self.assertIn("multisim://experiments/{experiment_id}/formal-pdf-en", resources)
            self.assertIn("multisim://experiments/{experiment_id}/reproducibility-manifest", resources)
            self.assertIn("multisim://sweeps/{sweep_id}/summary", resources)
            self.assertIn("multisim://sweeps/{sweep_id}/data", resources)
            self.assertEqual(
                set(output_schema["required"]),
                {
                    "success",
                    "experiment_id",
                    "resources",
                    "schematic",
                    "simulation",
                    "report",
                    "plot",
                    "output_dir",
                },
            )

    async def test_deterministic_diagnosis_is_callable_over_stdio(self) -> None:
        package_root = Path(multisim_mcp.__file__).resolve().parent.parent
        environment = get_default_environment()
        import_paths = [str(package_root), *(item for item in sys.path if item)]
        environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(import_paths))
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "multisim_mcp.server"],
            env=environment,
        )
        design = {
            "schema_version": 1,
            "design_id": "stdio-diagnosis",
            "title": "stdio diagnosis",
            "revision": 0,
            "components": [
                {
                    "refdes": "R1",
                    "kind": "R",
                    "nodes": ["a", "b"],
                    "value": "1k",
                    "model": None,
                    "parameters": {},
                }
            ],
            "parameters": {},
            "annotations": {},
        }
        async with Client(stdio_client(params), mode="2026-07-28") as session:
            response = await session.call_tool("diagnose_design", {"design": design})
        self.assertFalse(response.is_error)
        self.assertIsInstance(response.structured_content, dict)
        result = response.structured_content
        assert result is not None
        self.assertTrue(result["read_only"])
        self.assertFalse(result["source_design_modified"])
        self.assertFalse(result["simulation_performed"])
        self.assertIn(
            "reference-net-absent",
            {finding["code"] for finding in result["findings"]},
        )

    async def test_task_profiles_publish_exact_stable_tool_sets(self) -> None:
        for profile in ("core", "experiment", "optimization"):
            with self.subTest(profile=profile):
                _, names, prompts, resources, _ = await self._connect(
                    "2026-07-28", tool_profile=profile
                )
                self.assertEqual(names, PROFILE_TOOL_NAMES[profile])
                # Profiles reduce tool schema only; MCP Resources and Prompts
                # retain their stable compatibility surface.
                self.assertEqual(len(prompts), 5)
                self.assertEqual(len(resources), 20)


if __name__ == "__main__":
    unittest.main()
