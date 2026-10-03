import unittest

from multisim_mcp.native_temperature import (
    NATIVE_TEMPERATURE_UNVERIFIED_REASON,
    classify_temperature_command_log,
    temperature_capability,
)


class NativeTemperatureTest(unittest.TestCase):
    def test_xspice_unsupported_temp_is_not_promoted_by_completed_command(self):
        result = classify_temperature_command_log(
            "op\n.temp 85\n.temp: no such command available in XSPICE\n"
        )
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["supported"])
        self.assertEqual(len(result["diagnostics"]), 1)

    def test_empty_or_unknown_probe_remains_unverified(self):
        result = classify_temperature_command_log("")
        self.assertEqual(result["status"], "unverified")
        self.assertIn("no verified temperature setter", result["reason"])

    def test_capability_requires_follow_up_even_when_writer_is_exposed(self):
        result = temperature_capability(native_writer=True)
        self.assertEqual(result["status"], "candidate")
        self.assertFalse(result["verified"])

    def test_capability_surfaces_command_probe(self):
        probe = classify_temperature_command_log(".temp: no such command available in XSPICE")
        result = temperature_capability(native_writer=False, command_probe=probe)
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["command_probe"], probe)
        self.assertEqual(result["reason"], NATIVE_TEMPERATURE_UNVERIFIED_REASON)


if __name__ == "__main__":
    unittest.main()
