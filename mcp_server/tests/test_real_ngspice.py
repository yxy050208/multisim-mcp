"""Real open-backend smoke tests; skipped when ngspice is not installed."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp import server
from multisim_mcp.backend_selection import EXPERIMENT_BACKEND_ENV
from multisim_mcp.eda_backend import SimulationRequest
from multisim_mcp.ngspice_backend import NgspiceBackend, resolve_ngspice_executable
from multisim_mcp.spice_adapter import circuit_design_from_spice
from multisim_mcp.spice_raw import parse_raw
from multisim_mcp.virtual_instruments import logic_analyzer


NGSPICE_AVAILABLE = resolve_ngspice_executable(required=False) is not None
NETLIST = """V1 in 0 PULSE(0 5 0 1n 1n 5u 10u)
R1 in out 1k
C1 out 0 1u
.end
"""


@unittest.skipUnless(NGSPICE_AVAILABLE, "ngspice is not installed")
class RealNgspiceTest(unittest.TestCase):
    def test_real_backend_emits_parseable_bounded_artifacts(self) -> None:
        backend = NgspiceBackend()
        probe = backend.probe_runtime()
        self.assertTrue(probe["available"], probe)
        design = circuit_design_from_spice(NETLIST, title="Real ngspice RC")
        with tempfile.TemporaryDirectory() as tmp:
            result = backend.simulate(
                SimulationRequest(
                    design=design,
                    commands="tran 1u 20u",
                    output_directory=tmp,
                    max_points=12,
                )
            )
            self.assertTrue(result.success, result.to_dict())
            parsed = parse_raw(str(Path(tmp) / "result.raw"))
            self.assertLessEqual(parsed["n_points"], 12)
            self.assertIn("v(out)", {name.casefold() for name in parsed["columns"]})
            self.assertTrue((Path(tmp) / "data.csv").is_file())

    def test_real_complete_pipeline_produces_open_experiment_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {EXPERIMENT_BACKEND_ENV: "ngspice"}
        ):
            output = Path(tmp) / "complete"
            result = server._run_circuit_experiment_transaction(
                NETLIST,
                "tran 1u 20u",
                str(output),
                title="Real ngspice complete experiment",
                max_points=20,
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["backend_id"], "ngspice")
            self.assertTrue((output / "schematic.svg").is_file())
            self.assertTrue((output / "report.zh-CN.pdf").is_file())
            self.assertFalse((output / "circuit.ms14").exists())

    def test_real_behavioral_reference_observes_complementary_dff_outputs(self) -> None:
        native = """\
VCC vcc 0 5
VD d 0 PULSE(0 5 0 1n 1n 50u 100u)
VPR pr 0 5
VCLR clr 0 5
VCLK clk 0 PULSE(0 5 0 1n 1n 5u 10u)
XU1 d pr clr clk q nq 0 vcc 7474N
.end
"""
        with tempfile.TemporaryDirectory() as tmp:
            result = server.run_behavioral_reference(
                native,
                "tran 100n 100u",
                output_dir=tmp,
                max_points=512,
            )
            self.assertTrue(result["success"], result)
            self.assertEqual(result["backend_id"], "ngspice")
            self.assertEqual(result["output_dir"], str(Path(tmp).resolve()))
            self.assertLessEqual(result["n_points"], 512)
            columns = {name.casefold() for name in result["columns"]}
            self.assertIn("v(q)", columns)
            self.assertIn("v(nq)", columns)
            observation = result["digital_observation"]
            self.assertEqual(observation["overall_status"], "complete")
            self.assertGreaterEqual(observation["counts"]["observed"], 2)
            parsed = parse_raw(result["raw"])
            logic = logic_analyzer(parsed, ["v(d)", "v(clk)", "v(q)", "v(nq)"])
            summaries = {item["signal"]: item for item in logic["signals"]}
            self.assertGreaterEqual(summaries["v(q)"]["rising_edges"], 1)
            self.assertGreaterEqual(summaries["v(q)"]["falling_edges"], 1)
            self.assertEqual(summaries["v(q)"]["initial"], 0)
            self.assertEqual(summaries["v(nq)"]["initial"], 1)


if __name__ == "__main__":
    unittest.main()
