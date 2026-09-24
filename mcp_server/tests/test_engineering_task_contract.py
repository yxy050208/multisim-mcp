import unittest

from multisim_mcp.engineering_task_contract import normalize_task_result


class EngineeringTaskContractTest(unittest.TestCase):
    def test_envelope_defaults(self):
        result = normalize_task_result({"mode": "preview", "verification_status": "unverified",
                                        "output_dir": "x", "success": True})
        self.assertEqual(result["stage"], "preview")
        self.assertIn("measurement_acceptance", result)

    def test_missing_field_rejected(self):
        with self.assertRaises(ValueError):
            normalize_task_result({"mode": "preview", "success": True, "output_dir": "x"})
