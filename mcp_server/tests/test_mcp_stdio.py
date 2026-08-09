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


class McpStdioSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def _connect(
        self, mode: str
    ) -> tuple[str, set[str], set[str], set[str], dict]:
        package_root = Path(multisim_mcp.__file__).resolve().parent.parent
        environment = get_default_environment()
        import_paths = [str(package_root), *(item for item in sys.path if item)]
        environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(import_paths))
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
                tool for tool in tools.tools if tool.name == "run_circuit_experiment"
            )
            return (
                session.protocol_version,
                {tool.name for tool in tools.tools},
                {prompt.name for prompt in prompts.prompts},
                {item.uri_template for item in resources.resource_templates},
                experiment.output_schema,
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
            self.assertEqual(len(names), 51)
            self.assertEqual(len(prompts), 5)
            self.assertEqual(len(resources), 19)

            self.assertIn("runtime_status", names)
            self.assertIn("schematic_component_catalog", names)
            self.assertIn("create_schematic_from_netlist", names)
            self.assertIn("run_circuit_experiment", names)
            self.assertIn("submit_circuit_experiment", names)
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
            self.assertIn("read_virtual_multimeter", names)
            self.assertIn("analyze_bode_response", names)
            self.assertIn("analyze_logic_signals", names)
            self.assertIn("export_formal_experiment_report", names)
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


if __name__ == "__main__":
    unittest.main()
