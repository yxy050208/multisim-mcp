import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp import server


class BehavioralReferenceRunToolTest(unittest.TestCase):
    def test_converts_and_explicitly_runs_ngspice_in_one_call(self) -> None:
        native_netlist = (
            "VCC vcc 0 5\n"
            "XU1 d pr clr clk q nq 0 vcc 7474N\n"
            ".end\n"
        )
        simulation = {
            "success": True,
            "backend": "ngspice",
            "raw": "C:/experiments/reference/result.raw",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            server, "run_spice_netlist", return_value=simulation
        ) as runner:
            result = server.run_behavioral_reference(
                native_netlist,
                "tran 1u 10u",
                output_dir=str(Path(tmp) / "reference"),
                timeout=31,
                max_points=77,
                overwrite=True,
            )

        self.assertEqual(result["success"], True)
        self.assertEqual(result["backend"], "ngspice")
        self.assertTrue(result["behavioral_reference"]["converted"])
        self.assertEqual(result["behavioral_reference"]["converted_count"], 1)
        self.assertEqual(result["reference_netlist"], runner.call_args.args[0])
        self.assertIn("AXU1PRINV", result["reference_netlist"])
        self.assertIn("@DFF", result["reference_netlist"])

        args, kwargs = runner.call_args
        self.assertEqual(args[1], "tran 1u 10u")
        self.assertEqual(kwargs["output_dir"], str(Path(tmp) / "reference"))
        self.assertEqual(kwargs["timeout"], 31)
        self.assertEqual(kwargs["max_points"], 77)
        self.assertFalse(kwargs["unsafe_commands"])
        self.assertTrue(kwargs["overwrite"])
        self.assertEqual(kwargs["backend"], "ngspice")

    def test_rejects_a_no_op_conversion_before_running_solver(self) -> None:
        with patch.object(server, "run_spice_netlist") as runner:
            with self.assertRaisesRegex(
                ValueError, "does not contain a supported native DFF carrier"
            ):
                server.run_behavioral_reference(
                    "V1 in 0 5\nR1 in 0 1k\n.end\n",
                    "op",
                )
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
