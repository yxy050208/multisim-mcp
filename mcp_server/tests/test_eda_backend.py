from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from multisim_mcp.eda_backend import (
    BackendCapabilities,
    BackendDiagnostic,
    BackendExecution,
    EdaBackend,
    SchematicRequest,
    SimulationRequest,
)
from multisim_mcp.eda_core import ArtifactSet, CircuitComponent, CircuitDesign
from multisim_mcp.eda_service import EdaApplicationService
from multisim_mcp.multisim_backend import MultisimBackend


class EdaBackendTest(unittest.TestCase):
    def _design(self) -> CircuitDesign:
        return CircuitDesign(
            design_id="divider",
            title="Voltage divider",
            source_netlist="V1 in 0 5\nR1 in out 1k\nR2 out 0 1k\n.end\n",
        )

    def test_capabilities_are_versioned_and_protocol_is_runtime_checkable(self) -> None:
        backend = MultisimBackend(lambda *args, **kwargs: {}, lambda *args, **kwargs: {})
        self.assertIsInstance(backend, EdaBackend)
        capabilities = backend.discover_capabilities()
        self.assertEqual(capabilities.backend_id, "multisim")
        self.assertEqual(capabilities.analyses, ("op", "dc", "ac", "tran"))
        self.assertTrue(capabilities.supports_editable_schematic)
        encoded = capabilities.to_dict()
        self.assertEqual(BackendCapabilities.from_dict(encoded).to_dict(), encoded)

    def test_injected_multisim_adapter_builds_artifact_manifests_without_com(self) -> None:
        calls: dict[str, object] = {}

        def schematic_executor(
            netlist: str, output_ms14: str, **kwargs: object
        ) -> dict[str, object]:
            calls["schematic"] = {"netlist": netlist, **kwargs}
            ms14 = Path(output_ms14)
            ms14.write_bytes(b"ms14")
            xml = Path(str(ms14) + ".xml")
            xml.write_text("<xml />", encoding="utf-8")
            image = Path(str(kwargs["image_path"]))
            image.write_bytes(b"png")
            return {
                "success": True,
                "ms14": str(ms14),
                "xml": str(xml),
                "image": str(image),
                "experimental_probes": False,
                "build": {
                    "editable_model_coverage": {
                        "status": "complete",
                        "expanded_instances": 1,
                        "carrier_only_instances": 0,
                    }
                },
                "verification": {"native_netlist_complete": True},
            }

        def simulation_executor(
            netlist: str, commands: str, **kwargs: object
        ) -> dict[str, object]:
            calls["simulation"] = {
                "netlist": netlist,
                "commands": commands,
                **kwargs,
            }
            root = Path(str(kwargs["output_dir"]))
            work = root / "backend-work"
            work.mkdir()
            paths = {
                "raw": work / "result.raw",
                "csv": work / "data.csv",
                "netlist": work / "circuit.cir",
                "commands": work / "run.txt",
                "log": work / "run.log",
            }
            for name, path in paths.items():
                path.write_text(name, encoding="utf-8")
            published: list[str] = []
            for path in paths.values():
                destination = root / path.name
                destination.write_bytes(path.read_bytes())
                published.append(str(destination))
            return {
                "success": True,
                "work_dir": str(work),
                **{key: str(path) for key, path in paths.items()},
                "artifacts": published,
            }

        backend = MultisimBackend(schematic_executor, simulation_executor)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schematic = backend.create_schematic(
                SchematicRequest(
                    design=self._design(),
                    output_directory=str(root / "schematic"),
                    render_image=True,
                )
            )
            simulation = backend.simulate(
                SimulationRequest(
                    design=self._design(),
                    commands="op",
                    output_directory=str(root / "simulation"),
                )
            )

        self.assertTrue(schematic.success)
        self.assertEqual(schematic.payload["native_netlist_complete"], True)
        self.assertEqual(
            {item.name for item in schematic.artifacts.artifacts},
            {"circuit.ms14", "circuit.ms14.xml", "circuit.png"},
        )
        self.assertTrue(all(len(item.sha256) == 64 for item in schematic.artifacts.artifacts))
        self.assertTrue(simulation.success)
        self.assertEqual(len(simulation.artifacts.artifacts), 5)
        self.assertTrue(
            all(
                Path(item.location).parent.name == "simulation"
                for item in simulation.artifacts.artifacts
            )
        )
        self.assertEqual(
            simulation.artifacts.metadata["storage_location"],
            str((root / "simulation").resolve()),
        )
        simulation_call = calls["simulation"]
        assert isinstance(simulation_call, dict)
        self.assertEqual(simulation_call["commands"], "op")
        self.assertEqual(simulation_call["unsafe_commands"], False)
        self.assertEqual(simulation.to_dict()["schema_version"], 1)

    def test_multisim_adapter_exposes_source_compiler_boundary(self) -> None:
        design = CircuitDesign(
            design_id="structured-only",
            title="Structured design",
            components=(CircuitComponent("R1", "R", ("a", "0"), value="1k"),),
        )
        backend = MultisimBackend(lambda *args, **kwargs: {}, lambda *args, **kwargs: {})
        diagnostics = backend.validate_design(design)
        self.assertEqual(diagnostics[0].severity, "error")
        self.assertEqual(diagnostics[0].code, "source-netlist-required")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "requires source_netlist"):
                backend.simulate(
                    SimulationRequest(
                        design=design,
                        commands="op",
                        output_directory=tmp,
                    )
                )

    def test_simulation_can_keep_backend_managed_artifacts_without_publication(self) -> None:
        calls: dict[str, object] = {}

        def simulation_executor(
            netlist: str, commands: str, **kwargs: object
        ) -> dict[str, object]:
            calls.update(kwargs)
            work = Path(str(calls["work_root"])) / "run"
            work.mkdir()
            raw = work / "result.raw"
            raw.write_bytes(b"raw")
            return {
                "success": True,
                "work_dir": str(work),
                "raw": str(raw),
                "rows": [[0.0, 5.0]],
            }

        with tempfile.TemporaryDirectory() as tmp:
            calls["work_root"] = tmp
            backend = MultisimBackend(lambda *args, **kwargs: {}, simulation_executor)
            execution = backend.simulate(
                SimulationRequest(
                    design=self._design(),
                    commands="op",
                    unsafe_commands=True,
                )
            )
            expected_work = str((Path(tmp) / "run").resolve())

        self.assertTrue(execution.success)
        self.assertIsNone(calls["output_dir"])
        self.assertTrue(calls["unsafe_commands"])
        self.assertEqual(
            execution.artifacts.metadata["storage_location"], expected_work
        )
        self.assertEqual(
            execution.to_dict()["payload"]["compatibility_result"]["rows"],
            [[0.0, 5.0]],
        )

    def test_backend_contract_rejects_ambiguous_capabilities_and_requests(self) -> None:
        encoded = MultisimBackend(
            lambda *args, **kwargs: {}, lambda *args, **kwargs: {}
        ).discover_capabilities().to_dict()
        encoded["operations"] = ["simulate"]
        encoded["analyses"] = []
        with self.assertRaisesRegex(ValueError, "at least one analysis"):
            BackendCapabilities.from_dict(encoded)
        with self.assertRaisesRegex(ValueError, "timeout_seconds"):
            SimulationRequest(
                design=self._design(),
                commands="op",
                output_directory="output",
                timeout_seconds=float("nan"),
            )
        with self.assertRaisesRegex(ValueError, "max_points"):
            SimulationRequest(
                design=self._design(),
                commands="op",
                max_points=10_000_001,
            )
        with self.assertRaisesRegex(ValueError, "unsafe_commands"):
            SimulationRequest(
                design=self._design(),
                commands="op",
                unsafe_commands=1,  # type: ignore[arg-type]
            )

    def test_application_service_can_dispatch_to_a_no_com_fake_backend(self) -> None:
        class FakeBackend:
            backend_id = "fake"

            def __init__(self) -> None:
                self.operations: list[str] = []

            def discover_capabilities(self) -> BackendCapabilities:
                return BackendCapabilities(
                    backend_id=self.backend_id,
                    display_name="Deterministic fake EDA",
                    operations=("validate", "schematic", "simulate"),
                    analyses=("op",),
                    platforms=("any",),
                    requires_local_runtime=False,
                    supports_editable_schematic=True,
                    supports_vendor_models=False,
                    supports_batch=False,
                )

            def validate_design(
                self, design: CircuitDesign
            ) -> tuple[BackendDiagnostic, ...]:
                self.operations.append(f"validate:{design.design_id}")
                return ()

            def _result(self, operation: str, design: CircuitDesign) -> BackendExecution:
                self.operations.append(f"{operation}:{design.design_id}")
                return BackendExecution(
                    backend_id=self.backend_id,
                    operation=operation,
                    success=True,
                    artifacts=ArtifactSet(
                        artifact_set_id=f"{design.design_id}:{operation}:fake",
                        design_id=design.design_id,
                        producer=self.backend_id,
                    ),
                )

            def create_schematic(self, request: SchematicRequest) -> BackendExecution:
                return self._result("schematic", request.design)

            def simulate(self, request: SimulationRequest) -> BackendExecution:
                return self._result("simulate", request.design)

        fake = FakeBackend()
        service = EdaApplicationService([fake])
        self.assertEqual(service.backend_ids(), ("fake",))
        self.assertEqual(service.discover_backends()[0].analyses, ("op",))
        design = self._design()
        self.assertEqual(service.validate_design("fake", design), ())
        with tempfile.TemporaryDirectory() as tmp:
            schematic = service.create_schematic(
                "fake", SchematicRequest(design=design, output_directory=tmp)
            )
            simulation = service.simulate(
                "fake",
                SimulationRequest(
                    design=design,
                    commands="op",
                    output_directory=tmp,
                ),
            )
        self.assertTrue(schematic.success and simulation.success)
        self.assertEqual(
            fake.operations,
            ["validate:divider", "schematic:divider", "simulate:divider"],
        )
        with self.assertRaisesRegex(KeyError, "unknown EDA backend"):
            service.validate_design("missing", design)
        with self.assertRaisesRegex(ValueError, "already registered"):
            service.register_backend(fake)


if __name__ == "__main__":
    unittest.main()
