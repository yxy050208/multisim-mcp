from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from multisim_mcp.eda_core import CircuitComponent, CircuitDesign, ModelReference
from multisim_mcp.experiment_service import (
    ExperimentApplicationService,
    ExperimentRequest,
)


class ExperimentApplicationServiceTest(unittest.TestCase):
    def _source_design(self) -> CircuitDesign:
        return CircuitDesign(
            design_id="experiment-divider",
            title="Divider",
            source_netlist="V1 in 0 10\nR1 in out 1k\nR2 out 0 1k\n.end\n",
            model_references=(
                ModelReference(
                    "model:declared", "vendor", sha256="1" * 64, license="MIT"
                ),
            ),
            annotations={"spice_dialect": "SPICE3"},
        )

    def test_service_normalizes_request_and_dispatches_complete_transaction(self) -> None:
        calls: dict[str, object] = {}

        def runner(**kwargs: object) -> dict[str, object]:
            calls.update(kwargs)
            root = str(kwargs["output_dir"])
            return {
                "success": True,
                "experiment_id": "exp-service-test",
                "resources": {"report": "multisim://experiments/test/report"},
                "schematic": {"success": True},
                "simulation": {"success": True},
                "report": str(Path(root) / "report.md"),
                "plot": str(Path(root) / "plot.svg"),
                "output_dir": root,
                "verification": {
                    "schema_version": 1,
                    "overall_status": "pass",
                    "counts": {"pass": 1, "fail": 0, "unverified": 0},
                    "requirements": [{"id": "vout", "status": "pass"}],
                },
                "verification_path": str(Path(root) / "verification.json"),
            }

        requirement = {
            "id": "vout",
            "metric": "mean",
            "signal": "V(out)",
            "operator": "approximately",
            "target": 5.0,
            "tolerance_percent": 1.0,
        }
        progress: list[tuple[str, int, str]] = []
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "published"
            request = ExperimentRequest(
                design=self._source_design(),
                commands="\n op \n",
                output_directory=str(output),
                title="  Verified divider  ",
                timeout_seconds=30,
                max_points=500,
                overwrite=True,
                owner="job-test",
                requirements=(requirement,),
                theoretical_values={"vout": 5.0},
            )
            result = ExperimentApplicationService(runner).run(
                request,
                checkpoint=lambda stage, value, message: progress.append(
                    (stage, value, message)
                ),
                cancel_requested=lambda: False,
            )

        self.assertTrue(result["success"])
        self.assertEqual(calls["netlist"], self._source_design().source_netlist)
        self.assertEqual(calls["commands"], "op")
        self.assertEqual(calls["model_references"][0]["license"], "MIT")
        self.assertEqual(calls["declared_dialect"], "SPICE3")
        self.assertEqual(calls["title"], "Verified divider")
        self.assertEqual(calls["output_dir"], str(output.resolve()))
        self.assertEqual(calls["timeout"], 30.0)
        self.assertEqual(calls["max_points"], 500)
        self.assertTrue(calls["overwrite"])
        self.assertEqual(calls["owner"], "job-test")
        self.assertEqual(calls["requirements"][0]["id"], "vout")
        self.assertEqual(calls["theoretical_values"], {"vout": 5.0})
        self.assertTrue(callable(calls["checkpoint"]))
        self.assertTrue(callable(calls["cancel_requested"]))
        with self.assertRaises(TypeError):
            request.theoretical_values["vout"] = 4.0  # type: ignore[index]

    def test_structured_design_is_compiled_before_runner_dispatch(self) -> None:
        captured: dict[str, object] = {}

        def runner(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            root = str(kwargs["output_dir"])
            return {
                "success": True,
                "experiment_id": "exp-structured",
                "resources": {},
                "schematic": {},
                "simulation": {},
                "report": str(Path(root) / "report.md"),
                "plot": str(Path(root) / "plot.svg"),
                "output_dir": root,
            }

        design = CircuitDesign(
            design_id="structured-divider",
            title="Structured divider",
            components=(
                CircuitComponent("V1", "V", ("in", "0"), value="10"),
                CircuitComponent("R1", "R", ("in", "out"), value="1k"),
                CircuitComponent("R2", "R", ("out", "0"), value="1k"),
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            request = ExperimentRequest(design, "op", str(Path(tmp) / "out"))
            ExperimentApplicationService(runner).run(request)

        self.assertEqual(
            captured["netlist"],
            "V1 in 0 10\nR1 in out 1k\nR2 out 0 1k\n.end\n",
        )

    def test_request_and_result_contracts_fail_closed(self) -> None:
        root = str(Path(Path.cwd().anchor))
        with self.assertRaisesRegex(ValueError, "filesystem root"):
            ExperimentRequest(self._source_design(), "op", root)
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / "out")
            with self.assertRaisesRegex(ValueError, "requires requirements"):
                ExperimentRequest(
                    self._source_design(),
                    "op",
                    output,
                    theoretical_values={"vout": 5.0},
                )
            request = ExperimentRequest(self._source_design(), "op", output)

            def wrong_output_runner(**kwargs: object) -> dict[str, object]:
                return {
                    "success": True,
                    "experiment_id": "exp-wrong",
                    "resources": {},
                    "schematic": {},
                    "simulation": {},
                    "report": "report.md",
                    "plot": "plot.svg",
                    "output_dir": str(Path(tmp) / "different"),
                }

            with self.assertRaisesRegex(RuntimeError, "does not match"):
                ExperimentApplicationService(wrong_output_runner).run(request)
            with self.assertRaisesRegex(RuntimeError, "boolean success"):
                ExperimentApplicationService(lambda **kwargs: {}).run(request)

            verified_request = ExperimentRequest(
                self._source_design(),
                "op",
                output,
                requirements=(
                    {
                        "id": "vout",
                        "metric": "mean",
                        "signal": "V(out)",
                        "operator": "at_least",
                        "target": 1.0,
                    },
                ),
            )

            def inconsistent_runner(**kwargs: object) -> dict[str, object]:
                returned = str(kwargs["output_dir"])
                return {
                    "success": True,
                    "experiment_id": "exp-inconsistent",
                    "resources": {},
                    "schematic": {},
                    "simulation": {},
                    "report": str(Path(returned) / "report.md"),
                    "plot": str(Path(returned) / "plot.svg"),
                    "output_dir": returned,
                    "verification": {
                        "schema_version": 1,
                        "overall_status": "pass",
                        "counts": {"pass": 1, "fail": 0, "unverified": 0},
                        "requirements": [{"id": "wrong", "status": "pass"}],
                    },
                    "verification_path": str(
                        Path(returned) / "verification.json"
                    ),
                }

            with self.assertRaisesRegex(RuntimeError, "ids do not match"):
                ExperimentApplicationService(inconsistent_runner).run(
                    verified_request
                )


if __name__ == "__main__":
    unittest.main()
