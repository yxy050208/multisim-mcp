import unittest

from multisim_mcp.multisim_compat import capability_profile, parse_multisim_version, select_analysis_backend


class MultisimCompatTest(unittest.TestCase):
    def test_parses_version_and_direct_output_capability(self) -> None:
        self.assertEqual(parse_multisim_version("Multisim 14.3"), (14, 3, 0))
        self.assertTrue(capability_profile("Multisim 14.3")["capabilities"]["direct_output_requests"])

    def test_old_or_unknown_versions_use_command_engine(self) -> None:
        self.assertEqual(select_analysis_backend("Multisim 14.0"), "command-engine")
        self.assertEqual(select_analysis_backend(""), "command-engine")
        self.assertEqual(select_analysis_backend("Multisim 14.3", direct_output_error=True), "command-engine")
