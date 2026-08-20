"""Tests for the safe Multisim command policy."""

import os
import unittest
from unittest.mock import patch

from multisim_mcp.safety import (
    UNSAFE_COMMANDS_ENV,
    unsafe_commands_enabled,
    validate_analysis_commands,
    validate_spice_netlist,
)


class AnalysisCommandValidationTest(unittest.TestCase):
    def test_accepts_supported_analyses(self) -> None:
        commands = "op\ndc VIN 0 10 0.1\nac dec 10 1 1Meg\ntran 1u 2m uic"
        self.assertEqual(
            validate_analysis_commands(commands),
            ["op", "dc VIN 0 10 0.1", "ac dec 10 1 1Meg", "tran 1u 2m uic"],
        )

    def test_rejects_file_and_shell_commands_case_insensitively(self) -> None:
        for command in (
            "write C:\\tmp\\result.raw",
            "WRITE C:\\tmp\\result.raw",
            "source secrets.txt",
            "shell whoami",
            "dc VIN 0 10 0.1 > result.txt",
        ):
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    validate_analysis_commands(command)

    def test_requires_at_least_one_analysis(self) -> None:
        with self.assertRaises(ValueError):
            validate_analysis_commands("\n; comment only")

    def test_accepts_device_models_and_subcircuits(self) -> None:
        netlist = """\
VDD vdd 0 10
M1 out in 0 0 NMOS L=1u W=10u
.model NMOS NMOS(Level=1 VTO=2 KP=1m)
.subckt divider a b
R1 a b 1k
.ends divider
.end
"""
        validate_spice_netlist(netlist)

    def test_accepts_inline_vendor_model_conditionals_and_functions(self) -> None:
        netlist = """\
.param MODE=1
.func softclip(x) {limit(x,-1,1)}
.protect
.subckt AMP in out PARAMS: GAIN=10
.if (MODE=1)
E1 out 0 in 0 {GAIN}
.else
E1 out 0 in 0 1
.endif
.ends AMP
.unprotect
X1 in out AMP PARAMS: GAIN=2
.end
"""
        validate_spice_netlist(netlist)

    def test_rejects_malformed_subcircuit_and_conditional_blocks(self) -> None:
        for netlist, message in (
            (".subckt AMP a b\n.ends OTHER\n", "does not match"),
            (
                ".subckt AMP a b\n.ends AMP\n.subckt amp a b\n.ends amp\n",
                "Duplicate",
            ),
            (".else\n", "Unmatched"),
            (".if (1)\n", "Unclosed"),
        ):
            with self.subTest(netlist=netlist):
                with self.assertRaisesRegex(ValueError, message):
                    validate_spice_netlist(netlist)

    def test_rejects_control_and_external_file_directives(self) -> None:
        for directive in (
            ".control\nshell whoami\n.endc",
            ".include C:\\private\\model.lib",
            ".lib C:\\private\\models.lib section",
            ".hdl attacker.dll",
            "B1 out 0 V=file=C:\\private\\secret.txt",
            "B1 out 0 V=file\t=\tC:\\private\\secret.txt",
            ".model X filesource(file\t=\tC:\\private\\secret.txt)",
        ):
            with self.subTest(directive=directive):
                with self.assertRaises(ValueError):
                    validate_spice_netlist(directive)

    def test_unsafe_mode_requires_explicit_environment_opt_in(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(unsafe_commands_enabled())
        with patch.dict(os.environ, {UNSAFE_COMMANDS_ENV: "true"}, clear=True):
            self.assertTrue(unsafe_commands_enabled())


if __name__ == "__main__":
    unittest.main()
