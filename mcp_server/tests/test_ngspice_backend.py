from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from multisim_mcp.eda_backend import EdaBackend, SimulationRequest
from multisim_mcp.eda_core import CircuitComponent, CircuitDesign
from multisim_mcp.eda_service import EdaApplicationService
from multisim_mcp.behavioral_reference import build_behavioral_reference_netlist
from multisim_mcp.ngspice_backend import (
    NgspiceBackend,
    cancellable_process_runner,
    prepare_ngspice_deck,
)
from multisim_mcp.spice_raw import limit_points, parse_raw, write_ascii_raw
from multisim_mcp.spice_adapter import circuit_design_from_spice


RAW = """Title: ngspice test
Plotname: Transient Analysis
Flags: real
No. Variables: 2
No. Points: 4
Variables:
0 time time
1 v(out) voltage
Values:
0 0
 0
1 1e-6
 1
2 2e-6
 0
3 3e-6
 1
"""


class NgspiceBackendTest(unittest.TestCase):
    def _design(self) -> CircuitDesign:
        return CircuitDesign(
            design_id="ng-divider",
            title="Divider",
            source_netlist="V1 in 0 5\nR1 in out 1k\nR2 out 0 1k\n.end\n",
        )

    def test_safe_deck_separates_netlist_and_analysis_commands(self) -> None:
        deck, accepted = prepare_ngspice_deck(
            self._design().source_netlist or "", "tran 1u 10u"
        )
        self.assertEqual(accepted, ["tran 1u 10u"])
        self.assertIn(".tran 1u 10u", deck)
        self.assertIn("set filetype=ascii", deck)
        self.assertEqual(deck.casefold().count(".end\n"), 1)
        deck_with_trailing_comment, _ = prepare_ngspice_deck(
            "V1 in 0 1\nR1 in 0 1k\n.end\n* trailing comment\n", "op"
        )
        self.assertLess(deck_with_trailing_comment.index("* trailing comment"), deck_with_trailing_comment.index(".control"))
        self.assertEqual(deck_with_trailing_comment.casefold().count(".end\n"), 1)
        with self.assertRaises(ValueError):
            prepare_ngspice_deck(self._design().source_netlist or "", "shell whoami")

    def test_backend_runs_with_injected_process_and_publishes_artifacts(self) -> None:
        calls: list[tuple[list[str], Path, float]] = []

        def runner(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
            calls.append((argv, cwd, timeout))
            (cwd / "result.raw").write_text(RAW, encoding="utf-8")
            (cwd / "run.log").write_text("ngspice completed\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        backend = NgspiceBackend(sys.executable, process_runner=runner)
        self.assertIsInstance(backend, EdaBackend)
        self.assertEqual(backend.discover_capabilities().operations, ("validate", "simulate"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "output"
            execution = EdaApplicationService([backend]).simulate(
                "ngspice",
                SimulationRequest(
                    design=self._design(),
                    commands="tran 1u 3u",
                    output_directory=str(root),
                    max_points=3,
                ),
            )
            parsed = parse_raw(str(root / "result.raw"))
            self.assertEqual(parsed["n_points"], 3)
            self.assertEqual(parsed["columns"], ["time", "v(out)"])
            self.assertTrue((root / "data.csv").is_file())
            self.assertEqual({item.name for item in execution.artifacts.artifacts}, {
                "circuit.cir", "data.csv", "result.raw", "run.log", "run.txt"
            })
        self.assertTrue(execution.success)
        self.assertEqual(execution.payload["compatibility_result"]["point_count"], 3)
        self.assertEqual(
            execution.payload["compatibility_result"]["output_dir"], str(root.resolve())
        )
        self.assertEqual(calls[0][0][1:4], ["-n", "-b", "-o"])

    def test_behavioral_reference_is_compiled_into_ngspice_deck(self) -> None:
        native = (
            "VCC vcc 0 5\n"
            "XU1 d pr clr clk q nq 0 vcc 7474N\n"
            ".end\n"
        )
        reference = build_behavioral_reference_netlist(native)
        captured: list[str] = []

        def runner(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
            captured.append((cwd / "circuit.cir").read_text(encoding="utf-8"))
            (cwd / "result.raw").write_text(RAW, encoding="utf-8")
            (cwd / "run.log").write_text("ngspice completed\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")

        backend = NgspiceBackend(sys.executable, process_runner=runner)
        design = circuit_design_from_spice(reference["netlist"], allow_unsupported=True)
        with tempfile.TemporaryDirectory() as tmp:
            result = backend.simulate(
                SimulationRequest(
                    design=design,
                    commands="tran 1u 3u",
                    output_directory=tmp,
                )
            )
        self.assertTrue(result.success)
        self.assertEqual(len(captured), 1)
        deck_lines = captured[0].splitlines()
        self.assertIn("B__AXU1PRINV n_XU1_pr_bar 0", "\n".join(deck_lines))
        self.assertIn("B__AXU1CLRINV n_XU1_clr_bar 0", "\n".join(deck_lines))
        self.assertTrue(any("d_jkff" in line for line in deck_lines))
        self.assertFalse(any(line.strip().endswith("7474N") for line in deck_lines))

    def test_failed_process_returns_structured_diagnostic(self) -> None:
        def runner(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 7, "", "syntax error")

        backend = NgspiceBackend(sys.executable, process_runner=runner)
        with tempfile.TemporaryDirectory() as tmp:
            result = backend.simulate(
                SimulationRequest(
                    design=self._design(), commands="op", output_directory=tmp
                )
            )
        self.assertFalse(result.success)
        self.assertEqual(result.diagnostics[-1].code, "ngspice-simulation-failed")
        self.assertEqual(result.payload["compatibility_result"]["returncode"], 7)

    def test_structured_design_compiles_without_source_netlist(self) -> None:
        design = CircuitDesign(
            design_id="structured",
            title="Structured",
            components=(CircuitComponent("R1", "R", ("out", "0"), value="1k"),),
        )
        backend = NgspiceBackend(sys.executable)
        self.assertEqual(backend.validate_design(design), ())

    def test_ascii_writer_round_trips_limited_ngspice_variables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.raw"
            target = Path(tmp) / "target.raw"
            source.write_text(RAW, encoding="utf-8")
            parsed = limit_points(parse_raw(str(source)), 2)
            write_ascii_raw(str(target), parsed)
            round_trip = parse_raw(str(target))
        self.assertEqual(round_trip["n_points"], 2)
        self.assertEqual(round_trip["rows"], [[0.0, 0.0], [3e-06, 1.0]])

    def test_cancellable_process_runner_terminates_a_live_child(self) -> None:
        checks = 0

        def cancelled() -> bool:
            nonlocal checks
            checks += 1
            return checks >= 2

        runner = cancellable_process_runner(cancelled, poll_interval=0.05)
        started = time.monotonic()
        with self.assertRaises(InterruptedError):
            runner(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                Path.cwd(),
                5,
            )
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
