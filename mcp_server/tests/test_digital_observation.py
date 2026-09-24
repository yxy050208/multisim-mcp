import unittest

from multisim_mcp.digital_observation import build_digital_observation_evidence


class DigitalObservationTest(unittest.TestCase):
    def test_classifies_partial_native_dff_observation_without_filling_missing_qbar(self) -> None:
        evidence = build_digital_observation_evidence(
            "XU1 d pr clr clk q nq 0 vcc 7474N\n.end\n",
            ["time", "V(clk)", "V(q)"],
            backend_id="multisim",
            native_component_presence={"U1": True},
        )
        self.assertEqual(evidence["overall_status"], "partial")
        self.assertEqual(evidence["scope"], "native-multisim-output")
        self.assertEqual(evidence["counts"], {"observed": 1, "unobserved": 1})
        self.assertEqual(evidence["routing"]["recommended_backend"], "ngspice")
        self.assertEqual(evidence["routing"]["mode"], "explicit-rerun")
        self.assertFalse(evidence["routing"]["automatic_switch"])
        self.assertEqual(evidence["signals"][0]["claim"], "native-component-output-observed")
        self.assertEqual(evidence["signals"][1]["claim"], "unobserved")
        self.assertIsNone(evidence["signals"][1]["raw_column"])

    def test_ngspice_output_is_explicitly_behavioral_reference(self) -> None:
        evidence = build_digital_observation_evidence(
            "A1 j k clk set reset q nq JK\n.end\n",
            ["time", "V(q)", "V(nq)"],
            backend_id="ngspice",
        )
        self.assertEqual(evidence["overall_status"], "complete")
        self.assertEqual(evidence["scope"], "behavioral-reference-output")
        self.assertEqual(evidence["routing"]["mode"], "none")
        self.assertTrue(
            all(
                item["claim"] == "behavioral-reference-output-observed"
                for item in evidence["signals"]
            )
        )

    def test_non_digital_design_is_not_applicable(self) -> None:
        evidence = build_digital_observation_evidence(
            "V1 in 0 5\nR1 in 0 1k\n.end\n",
            ["time", "V(in)"],
            backend_id="multisim",
        )
        self.assertEqual(evidence["overall_status"], "not-applicable")
        self.assertEqual(evidence["signals"], [])
        self.assertEqual(evidence["routing"]["mode"], "none")


if __name__ == "__main__":
    unittest.main()
