import unittest

from multisim_mcp.multisim_compat import capability_profile, parse_multisim_version, select_analysis_backend
from multisim_mcp.multisim_client import native_capability_flags


class MultisimCompatTest(unittest.TestCase):
    def test_parses_version_and_direct_output_capability(self) -> None:
        self.assertEqual(parse_multisim_version("Multisim 14.3"), (14, 3, 0))
        self.assertTrue(capability_profile("Multisim 14.3")["capabilities"]["direct_output_requests"])

    def test_old_or_unknown_versions_use_command_engine(self) -> None:
        self.assertEqual(select_analysis_backend("Multisim 14.0"), "command-engine")
        self.assertEqual(select_analysis_backend(""), "command-engine")
        self.assertEqual(select_analysis_backend("Multisim 14.3", direct_output_error=True), "command-engine")

    def test_version_profile_does_not_promote_parameter_readback_to_a_setter(self) -> None:
        capabilities = capability_profile("Multisim 14.3")["capabilities"]
        self.assertTrue(capabilities["circuit_parameter_readback"])
        self.assertFalse(capabilities["native_temperature_control"])
        self.assertFalse(capabilities["native_model_parameter_write"])

    def test_native_probe_distinguishes_read_and_write_surfaces(self) -> None:
        flags = native_capability_flags([
            "EnumCircuitParameters", "CircuitParameterValue", "ReplaceComponent",
            "DoACSweep", "DoDCOperatingPoint",
        ])
        self.assertTrue(flags["circuit_parameter_enumeration"])
        self.assertTrue(flags["circuit_parameter_readback"])
        self.assertTrue(flags["model_replacement"])
        self.assertTrue(flags["analysis_api"])
        self.assertFalse(flags["native_temperature_control"])
        self.assertFalse(flags["native_model_parameter_write"])

    def test_native_probe_recognizes_explicit_writers_only(self) -> None:
        flags = native_capability_flags(["SetTemperature", "SetModelParameter"])
        self.assertTrue(flags["native_temperature_control"])
        self.assertTrue(flags["native_model_parameter_write"])
