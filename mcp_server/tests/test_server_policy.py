"""COM-free tests for MCP orchestration safety gates."""

import os
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


class ArtifactPreflightTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
