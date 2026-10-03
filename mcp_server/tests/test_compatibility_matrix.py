import unittest

from multisim_mcp.multisim_compat import (
    build_compatibility_matrix,
    compatibility_matrix_entry,
)


class CompatibilityMatrixTest(unittest.TestCase):
    def test_matching_probe_is_api_verified(self):
        entry = compatibility_matrix_entry(
            "Multisim 14.3",
            detected_version="Multisim 14.3",
            native_probe={"capabilities": {"analysis_api": True}},
            evidence_path="native-api.json",
        )
        self.assertEqual(entry["status"], "api-verified")
        self.assertTrue(entry["version_match"])
        self.assertEqual(entry["verification_scope"], "native-api-introspection")

    def test_different_installed_version_stays_unverified(self):
        entry = compatibility_matrix_entry(
            "Multisim 14.2",
            detected_version="Multisim 14.3",
            native_probe={"capabilities": {"analysis_api": True}},
        )
        self.assertEqual(entry["status"], "unverified")
        self.assertFalse(entry["version_match"])
        self.assertEqual(entry["observed_capabilities"], {"analysis_api": True})

    def test_unknown_requested_version_is_unsupported(self):
        entry = compatibility_matrix_entry(
            "future-release",
            detected_version="Multisim 14.3",
            native_probe={"capabilities": {"analysis_api": True}},
        )
        self.assertEqual(entry["status"], "unsupported")

    def test_matrix_deduplicates_targets_and_reports_buckets(self):
        matrix = build_compatibility_matrix(
            ["14.3", "Multisim 14.3", "14.2", "future-release"],
            detected_version="Multisim 14.3",
            native_probe={"capabilities": {"analysis_api": True}},
        )
        self.assertEqual(len(matrix["entries"]), 4)
        self.assertEqual(matrix["verified_api_versions"], ["14.3", "Multisim 14.3"])
        self.assertEqual(matrix["unverified_versions"], ["14.2"])
        self.assertEqual(matrix["unsupported_versions"], ["future-release"])


if __name__ == "__main__":
    unittest.main()
