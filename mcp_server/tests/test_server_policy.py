"""COM-free tests for MCP orchestration safety gates."""

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp import server


class UnsafeToolGateTest(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_arbitrary_command_file_is_disabled_before_com_call(self) -> None:
        with self.assertRaises(RuntimeError):
            server.do_command_line("commands.txt", "run.log")

    def test_control_block_is_rejected_before_com_call(self) -> None:
        netlist = "V1 a 0 1\n.control\nshell whoami\n.endc\n.end\n"
        with self.assertRaises(ValueError):
            server.run_spice_netlist(netlist, "op")

    def test_external_adapter_expansion_is_revalidated_before_com(self) -> None:
        adapter = {
            "schema_version": 1,
            "kind": "BADMODEL",
            "terminals": ["p", "n"],
            "parameters": [],
            "expansion": [
                "D{stem} {p} {n} M{stem}",
                ".model M{stem} D(file=secret.txt)",
            ],
            "description_zh": "test",
            "description_en": "test",
        }
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bad.json").write_text(
                json.dumps(adapter), encoding="utf-8"
            )
            with patch.dict(os.environ, {"MULTISIM_MCP_ADAPTER_DIR": tmp}):
                with self.assertRaisesRegex(ValueError, "External-file"):
                    server.run_spice_netlist("X1 a 0 @BADMODEL\n.end\n", "op")

    def test_standalone_spice_run_opens_a_blank_document_for_command_engine(self) -> None:
        class FakeClient:
            opened = False

            @property
            def circuit(self) -> object:
                if not self.opened:
                    raise RuntimeError("No circuit is open")
                return object()

            def new_circuit(self) -> dict:
                self.opened = True
                return {"name": "blank"}

            def run_command_file(self, command_file: str, log_file: str, *args: object, **kwargs: object) -> dict:
                command = Path(command_file).read_text(encoding="utf-8")
                raw_path = Path(next(line[6:] for line in command.splitlines() if line.startswith("write ")))
                raw_path.write_text(RAW_FIXTURE, encoding="utf-8")
                Path(log_file).write_text("ok\n", encoding="utf-8")
                return {"state": 0, "timed_out": False, "last_error": "", "log": "ok"}

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"MULTISIM_MCP_WORKDIR": tmp}
        ), patch.object(server, "client", FakeClient()):
            result = server.run_spice_netlist(
                "V1 in 0 5\nR1 in 0 1k\n.end\n",
                "op",
                output_dir=str(Path(tmp) / "output"),
            )
        self.assertTrue(result["success"])


class EdaCompatibilityBridgeTest(unittest.TestCase):
    def test_complete_experiment_uses_application_service_without_result_changes(self) -> None:
        netlist = "V1 in 0 10\nR1 in out 1k\nR2 out 0 1k\n.end\n"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "complete experiment"
            native_result = {
                "success": True,
                "experiment_id": "exp-compatibility",
                "resources": {"report": "multisim://experiments/test/report"},
                "schematic": {"success": True},
                "simulation": {"success": True},
                "report": str(output / "report.md"),
                "plot": str(output / "plot.svg"),
                "output_dir": str(output.resolve()),
            }
            with patch.object(
                server,
                "_run_circuit_experiment_transaction",
                return_value=native_result,
            ) as executor:
                result = server.run_circuit_experiment(
                    netlist,
                    "\n op \n",
                    str(output),
                    title="Compatibility experiment",
                    timeout=45,
                    max_points=321,
                    overwrite=True,
                )

        self.assertEqual(result, native_result)
        kwargs = executor.call_args.kwargs
        self.assertEqual(kwargs["netlist"], netlist)
        self.assertEqual(kwargs["commands"], "op")
        self.assertEqual(kwargs["output_dir"], str(output.resolve()))
        self.assertEqual(kwargs["title"], "Compatibility experiment")
        self.assertEqual(kwargs["timeout"], 45.0)
        self.assertEqual(kwargs["max_points"], 321)
        self.assertTrue(kwargs["overwrite"])
        self.assertIsNone(kwargs["requirements"])

    def test_simulation_bridge_preserves_safe_source_dialect_and_failure_result(self) -> None:
        netlist = "Y1 a b VENDOR_DEVICE\n.end\n"
        with tempfile.TemporaryDirectory() as tmp:
            native_result = {
                "success": False,
                "run_id": "failed-compatibility-run",
                "work_dir": tmp,
                "state": 1,
                "last_error": "vendor device unavailable",
            }
            with patch.object(
                server, "_run_spice_netlist_impl", return_value=native_result
            ) as executor:
                result = server.run_spice_netlist(netlist, "op")

        self.assertEqual(result, native_result)
        self.assertEqual(executor.call_args.args, (netlist, "op"))

    def test_simulation_tool_uses_eda_service_without_changing_result(self) -> None:
        netlist = "V1 in 0 5\nR1 in 0 1k\n.end\n"
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "result.raw"
            raw.write_bytes(b"raw")
            native_result = {
                "success": True,
                "run_id": "compatibility-run",
                "work_dir": tmp,
                "raw": str(raw),
                "rows": [[0.0, 5.0]],
            }
            with patch.object(
                server, "_run_spice_netlist_impl", return_value=native_result
            ) as executor:
                result = server.run_spice_netlist(
                    netlist,
                    "op",
                    timeout=7.5,
                    max_points=123,
                    unsafe_commands=True,
                    overwrite=True,
                )

        self.assertEqual(result, native_result)
        args, kwargs = executor.call_args
        self.assertEqual(args, (netlist, "op"))
        self.assertIsNone(kwargs["output_dir"])
        self.assertEqual(kwargs["timeout"], 7.5)
        self.assertEqual(kwargs["max_points"], 123)
        self.assertTrue(kwargs["unsafe_commands"])
        self.assertTrue(kwargs["overwrite"])

    def test_schematic_tool_uses_eda_service_without_changing_result(self) -> None:
        netlist = "V1 in 0 5\nR1 in 0 1k\n.end\n"
        native_result = {
            "success": True,
            "build": {"editable_model_coverage": {"status": "not_applicable"}},
            "verification": {"native_netlist_complete": True},
            "ms14": "placeholder.ms14",
            "xml": "placeholder.ms14.xml",
            "experimental_probes": False,
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "bridge schematic.ms14"
            image = Path(tmp) / "custom preview.png"
            with patch.object(
                server, "_create_schematic_impl", return_value=native_result
            ) as executor:
                result = server.create_schematic_from_netlist(
                    netlist,
                    str(output),
                    probe_nets=["in"],
                    image_path=str(image),
                    open_after_build=False,
                )

        self.assertEqual(result, native_result)
        args, kwargs = executor.call_args
        self.assertEqual(args, (netlist, str(output.resolve())))
        self.assertEqual(kwargs["probe_nets"], ["in"])
        self.assertEqual(kwargs["image_path"], str(image.resolve()))
        self.assertFalse(kwargs["open_after_build"])


class ArtifactPreflightTest(unittest.TestCase):
    def test_high_level_workflow_rejects_empty_root_and_nonfinite_limits(self) -> None:
        netlist = "V1 a 0 1\nR1 a 0 1k\n.end\n"
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            server.run_circuit_experiment(netlist, "op", "")
        with self.assertRaisesRegex(ValueError, "filesystem root"):
            server.run_circuit_experiment(netlist, "op", Path.cwd().anchor)
        with self.assertRaisesRegex(ValueError, "timeout"):
            server.submit_circuit_experiment(
                netlist,
                "op",
                str(Path.cwd() / "nonfinite-test"),
                timeout=float("nan"),
            )

    def test_high_level_workflow_refuses_collision_before_multisim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.md"
            report.write_text("keep me", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                server.run_circuit_experiment(
                    "V1 a 0 1\nR1 a 0 1k\n.end\n",
                    "op",
                    tmp,
                )
            self.assertEqual(report.read_text(encoding="utf-8"), "keep me")

    def test_failed_overwrite_leaves_published_artifacts_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.md"
            report.write_text("previous complete report", encoding="utf-8")
            with patch.object(
                server,
                "_create_schematic_impl",
                side_effect=RuntimeError("codec failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "codec failed"):
                    server.run_circuit_experiment(
                        "V1 a 0 1\nR1 a 0 1k\n.end\n",
                        "op",
                        tmp,
                        overwrite=True,
                    )
            self.assertEqual(
                report.read_text(encoding="utf-8"), "previous complete report"
            )
            self.assertFalse((Path(tmp) / "circuit.ms14").exists())
            self.assertFalse(
                list(Path(tmp).parent.glob(f".{Path(tmp).name}.multisim-mcp-*"))
            )


RAW_FIXTURE = """Title: fixture
Plotname: Operating Point
Flags: real
No. Variables: 2
No. Points: 1
Variables:
0 x voltage V(in)
1 y voltage V(out)
Values:
0 5
 2.5
"""


def _fake_simulation(*args: object, **kwargs: object) -> dict:
    root = Path(str(kwargs["output_dir"]))
    root.mkdir(parents=True, exist_ok=True)
    (root / "result.raw").write_text(RAW_FIXTURE, encoding="utf-8")
    (root / "data.csv").write_text("V(in),V(out)\n5,2.5\n", encoding="utf-8")
    (root / "run.log").write_text("ok\n", encoding="utf-8")
    (root / "run.txt").write_text("op\n", encoding="utf-8")
    (root / "circuit.cir").write_text(str(args[0]), encoding="utf-8")
    return {
        "success": True,
        "raw": str(root / "result.raw"),
        "csv": str(root / "data.csv"),
        "log": str(root / "run.log"),
        "commands": str(root / "run.txt"),
        "netlist": str(root / "circuit.cir"),
        "columns": ["V(in)", "V(out)"],
        "rows": [[5.0, 2.5]],
        "n_points": 1,
        "measurements": [],
    }


class VerificationAndSweepWorkflowTest(unittest.TestCase):
    def test_verified_experiment_publishes_json_resource_and_report_verdict(self) -> None:
        def fake_schematic(*args: object, **kwargs: object) -> dict:
            design = Path(str(args[1]))
            design.write_bytes(b"MS14 fixture")
            design.with_suffix(design.suffix + ".xml").write_text("<xml />", encoding="utf-8")
            image = Path(str(kwargs["image_path"]))
            image.write_bytes(b"PNG fixture")
            return {
                "success": True,
                "ms14": str(design),
                "image": str(image),
                "build": {"model_warnings": []},
            }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "verified"
            spec = {
                "schema_version": 1,
                "title": "Divider verification",
                "netlist": "V1 in 0 5\nR1 in out 1k\nR2 out 0 1k\n.end\n",
                "commands": "op",
                "requirements": [
                    {"id": "vout", "metric": "mean", "signal": "V(out)", "operator": "approximately", "target": 2.5, "tolerance_percent": 1},
                ],
                "theoretical_values": {"vout": 2.5},
            }
            with patch.object(server, "_create_schematic_impl", side_effect=fake_schematic), patch.object(
                server, "_run_spice_netlist_impl", side_effect=_fake_simulation
            ):
                result = server.run_verified_circuit_experiment(spec, str(output))

            self.assertEqual(result["verification"]["overall_status"], "pass")
            self.assertIn("verification", result["resources"])
            persisted = json.loads((output / "verification.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["counts"]["pass"], 1)
            self.assertIn("Design requirement verification", (output / "report.md").read_text(encoding="utf-8"))

    def test_sweep_is_transactional_and_exports_flat_data(self) -> None:
        spec = {
            "schema_version": 1,
            "mode": "parameter",
            "title": "Divider sweep",
            "netlist_template": "V1 in 0 5\nR1 in out {{R1}}\nR2 out 0 1k\n.end\n",
            "commands": "op",
            "parameters": [{"name": "R1", "values": [500, 1000]}],
            "measurements": [{"id": "vout", "metric": "mean", "signal": "V(out)"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "sweep"
            with patch.object(server, "_run_spice_netlist_impl", side_effect=_fake_simulation):
                result = server.run_experiment_sweep(spec, str(output))
            self.assertEqual(result["run_count"], 2)
            self.assertRegex(result["sweep_id"], r"^sweep-[0-9a-f]{24}$")
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["run_count"], 2)
            data = (output / "data.csv").read_text(encoding="utf-8")
            self.assertIn("run_id,status,R1,vout", data)
            self.assertTrue((output / "runs" / "run-0002" / "result.raw").is_file())

    def test_plain_overwrite_does_not_retain_an_old_verification_verdict(self) -> None:
        def fake_schematic(*args: object, **kwargs: object) -> dict:
            design = Path(str(args[1]))
            design.write_bytes(b"MS14 fixture")
            design.with_suffix(design.suffix + ".xml").write_text("<xml />", encoding="utf-8")
            image = Path(str(kwargs["image_path"]))
            image.write_bytes(b"PNG fixture")
            return {"success": True, "ms14": str(design), "image": str(image), "build": {}}

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "experiment"
            output.mkdir()
            for name in (
                "circuit.ms14", "circuit.ms14.xml", "schematic.png", "data.csv",
                "result.raw", "run.log", "run.txt", "circuit.cir", "plot.svg", "report.md",
            ):
                (output / name).write_bytes(b"old")
            (output / "verification.json").write_text('{"overall_status":"pass"}', encoding="utf-8")
            with patch.object(server, "_create_schematic_impl", side_effect=fake_schematic), patch.object(
                server, "_run_spice_netlist_impl", side_effect=_fake_simulation
            ):
                result = server.run_circuit_experiment(
                    "V1 in 0 5\nR1 in out 1k\nR2 out 0 1k\n.end\n",
                    "op",
                    str(output),
                    overwrite=True,
                )
            self.assertFalse((output / "verification.json").exists())
            self.assertNotIn("verification", result["resources"])


if __name__ == "__main__":
    unittest.main()
