import tempfile
import unittest
from pathlib import Path

from multisim_mcp.native_model_override import (
    apply_qnpn_model_parameters,
    extract_qnpn_model_parameters,
    normalize_qnpn_model_parameters,
    verify_qnpn_model_parameters,
)


MODEL = "&amp;ASC.MODEL 2N3904__BJT_NPN__2 NPN(Is=6.734f Vaf=74.03 Bf=416.4 Rc=1)"
COMPONENT = "&amp;ASC.MODEL 2N3904 NPN(Is=6.734f Vaf=74.03 Bf=416.4 Rc=1)"


class NativeModelOverrideTest(unittest.TestCase):
    def test_normalizes_allowlisted_parameters_and_suffixes(self):
        values = normalize_qnpn_model_parameters({"bf": 200, "VAF": "80", "is": "10f"})
        self.assertEqual(values, {"Bf": 200.0, "Is": 1e-14, "Vaf": 80.0})

    def test_rejects_unknown_or_unsafe_parameters(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            normalize_qnpn_model_parameters({"Cje": 1e-12})
        with self.assertRaisesRegex(ValueError, "outside"):
            normalize_qnpn_model_parameters({"Bf": 0.5})

    def test_patches_component_and_shared_model_and_reads_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.xml"
            path.write_text(f"<root><a>{COMPONENT}</a><b>{MODEL}</b></root>", encoding="utf-8")
            record = apply_qnpn_model_parameters(path, {"Bf": 200, "Vaf": 80})
            self.assertEqual(record["definitions_updated"], 2)
            self.assertEqual(extract_qnpn_model_parameters(path.read_text(encoding="utf-8"))["Bf"], 200.0)
            verified = verify_qnpn_model_parameters(path, {"Bf": 200, "Vaf": 80})
            self.assertTrue(verified["ok"])
            self.assertEqual(path.read_text(encoding="utf-8").count("Bf=200"), 2)

    def test_requires_both_linked_definitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.xml"
            path.write_text(f"<root>{MODEL}</root>", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "both"):
                apply_qnpn_model_parameters(path, {"Bf": 200})

    def test_nominal_override_is_idempotent_and_still_reports_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.xml"
            path.write_text(f"<root><a>{COMPONENT}</a><b>{MODEL}</b></root>", encoding="utf-8")
            record = apply_qnpn_model_parameters(path, {"Bf": 416.4})
            self.assertEqual(record["status"], "xml-unchanged-awaiting-native-roundtrip")
            self.assertEqual(record["definitions_updated"], 2)


if __name__ == "__main__":
    unittest.main()
