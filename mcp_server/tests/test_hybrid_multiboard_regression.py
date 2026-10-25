import contextlib
import importlib.util
import io
import unittest
import hashlib
import json
from pathlib import Path

from multisim_mcp.hybrid_multiboard_regression import (
    controlled_source_multiboard_corner_cases,
    hybrid_multiboard_regression_cases,
    select_hybrid_multiboard_regression_case,
)


class HybridMultiboardRegressionTest(unittest.TestCase):
    def test_runner_lists_all_cases_without_com_or_output_directory(self):
        tool = Path(__file__).resolve().parents[2] / "tools" / "run_hybrid_multiboard_regression.py"
        spec = importlib.util.spec_from_file_location("hybrid_regression_runner_test", tool)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.assertEqual(runner.main(["--list"]), 0)
        listed = json.loads(stream.getvalue())
        ids = [item["case_id"] for item in listed]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("vcvs_rc_split_combo_low", ids)
        self.assertIn("vccs_rc_split_combo_high", ids)
        self.assertEqual(len(ids), len(hybrid_multiboard_regression_cases()))

    def test_case_build_has_verified_connector_and_explicit_fixture_digest(self):
        case = select_hybrid_multiboard_regression_case()
        design, partition, fixtures = case.build()
        self.assertEqual(design.design_id, "hybrid-split-v1")
        self.assertTrue(partition["feasible"])
        self.assertEqual(partition["connector_contract_status"], "valid")
        fragment = next(item for item in fixtures if item["kind"] == "circuit_fragment")
        self.assertEqual(fragment["model_scope"], "explicit-digital-fixture")
        self.assertEqual(len(fragment["source_compiled_digest"]), 64)
        self.assertNotEqual(fragment["source_compiled_digest"], "0" * 64)
        self.assertEqual(set(case.continuous_response_contracts()), {"filt"})

    def test_manifest_is_deterministic(self):
        case = hybrid_multiboard_regression_cases()[0]
        self.assertEqual(case.manifest(), case.manifest())

    def test_unknown_case_is_rejected(self):
        with self.assertRaises(ValueError):
            select_hybrid_multiboard_regression_case("missing")

    def test_adc_dac_case_has_bv_boundary_fixture_and_explicit_contract(self):
        case = select_hybrid_multiboard_regression_case("adc1_dac1_split")
        design, partition, fixtures = case.build()
        self.assertEqual(design.design_id, "adc1-dac1-split-v1")
        self.assertTrue(partition["feasible"])
        self.assertEqual(partition["connector_contract_status"], "valid")
        self.assertEqual({item["board"] for item in case.source_components()}, {"adc", "dac"})
        self.assertEqual(
            {component.kind for component in design.components if component.kind == "BV"},
            {"BV"},
        )
        fragment = next(item for item in fixtures if item["kind"] == "circuit_fragment")
        payload = {"ports": fragment["ports"], "components": fragment["components"]}
        expected = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(fragment["source_compiled_digest"], expected)
        self.assertEqual(fragment["model_scope"], "explicit-digital-fixture")
        self.assertTrue(any(item["kind"] == "BV" for item in fragment["components"]))

    def test_adc4_dac4_case_has_two_explicit_four_pin_connectors(self):
        case = select_hybrid_multiboard_regression_case("adc4_dac4_split")
        design, partition, fixtures = case.build()
        self.assertEqual(design.design_id, "adc4-dac4-split-v1")
        self.assertTrue(partition["feasible"])
        self.assertEqual(partition["connector_contract_status"], "valid")
        self.assertEqual(
            {item["id"] for item in partition["connector_contract"]["connectors"]},
            {"JDATA", "JPOWER"},
        )
        self.assertEqual(sum(item["max_connector_pins"] for item in case.boards()), 16)
        self.assertEqual(sum(component.kind == "BV" for component in design.components), 5)
        self.assertLess(max(len(component.model or "") for component in design.components), 4096)
        fragment = next(item for item in fixtures if item["kind"] == "circuit_fragment")
        self.assertEqual(fragment["ports"], ["d0", "d1", "d2", "d3", "high", "0"])
        self.assertEqual(len(fragment["source_compiled_digest"]), 64)

    def test_adc1_edge_case_declares_only_digital_event_gate(self):
        case = select_hybrid_multiboard_regression_case("adc1_dac1_edge_split")
        components = case.source_components()
        self.assertEqual(next(item["model"] for item in components if item["refdes"] == "VAN"),
                         "PULSE(0 5 0 1n 1n 20u 40u)")
        contracts = case.digital_edge_contracts()
        self.assertEqual(set(contracts), {"digital"})
        contracts["digital"].validate()
        self.assertEqual(case.fixtures()[-1]["source_compiled_digest"], case.manifest()["fixture_digest"])

    def test_adc4_edge_case_declares_four_bit_events_and_rc_response(self):
        case = select_hybrid_multiboard_regression_case("adc4_dac4_edge_split")
        self.assertEqual(set(case.digital_edge_contracts()), {"d0", "d1", "d2", "d3"})
        self.assertNotIn("dac-analog", {item["id"] for item in case.fixtures()})
        self.assertIn("dac-filt", {item["id"] for item in case.fixtures()})
        self.assertEqual(set(case.continuous_response_contracts()), {"filt"})
        self.assertEqual(
            next(item["model"] for item in case.source_components() if item["refdes"] == "VAN"),
            "PULSE(0 5 0 160u 160u 10u 340u)",
        )

    def test_adc4_transfer_case_declares_rc_response_contract(self):
        case = select_hybrid_multiboard_regression_case("adc4_dac4_transfer_split")
        self.assertIn("dac-filt", {item["id"] for item in case.fixtures()})
        contracts = case.continuous_response_contracts()
        self.assertEqual(set(contracts), {"filt"})
        contracts["filt"].validate()
        self.assertGreaterEqual(contracts["filt"].minimum_samples, 3)

    def test_controlled_source_cases_have_explicit_boundary_fixture_and_rc_contract(self):
        for case_id, kind in (("vcvs_rc_split", "E"), ("vccs_rc_split", "G")):
            with self.subTest(case_id=case_id):
                case = select_hybrid_multiboard_regression_case(case_id)
                source = next(item for item in case.source_components() if item["kind"] == kind)
                self.assertEqual(source["board"], "source" if kind == "E" else "load")
                self.assertEqual(source["nodes"][0:2], ["raw", "0"] if kind == "E" else ["0", "raw"])
                expected_fixture_ids = {
                    "source-raw", "source-ctrl", "load-raw", "load-filt",
                    "source-ground", "load-ground", "load-raw-source",
                }
                if kind == "G":
                    expected_fixture_ids = {
                        "source-ctrl", "load-ctrl", "load-filt", "load-ctrl-source",
                        "source-ground", "load-ground",
                    }
                self.assertEqual({item["id"] for item in case.fixtures()}, expected_fixture_ids)
                self.assertEqual(set(case.continuous_response_contracts()), {"filt"})

    def test_controlled_source_corner_matrix_is_bounded_and_deterministic(self):
        cases = controlled_source_multiboard_corner_cases()
        self.assertEqual(
            [case.case_id for case in cases],
            [
                "vcvs_rc_split_gain05", "vcvs_rc_split_gain2",
                "vccs_rc_split_g05m", "vccs_rc_split_g2m",
                "vcvs_rc_split_r500", "vcvs_rc_split_c2u",
                "vccs_rc_split_r2k", "vccs_rc_split_load50k",
                "vcvs_rc_split_combo_low", "vccs_rc_split_combo_high",
            ],
        )
        self.assertEqual(
            [case.source_value for case in cases], [
                "0.5", "2", "0.5m", "2m", "1", "1", "1m", "1m", "0.5", "2m"
            ]
        )
        for case in cases:
            with self.subTest(case=case.case_id):
                self.assertEqual(case.manifest(), case.manifest())
                controlled = next(
                    item for item in case.source_components()
                    if item["kind"] == case.source_kind
                )
                self.assertEqual(controlled["value"], case.source_value)

        load_corners = {
            case.case_id: (case.series_resistance, case.filter_capacitance, case.load_resistance)
            for case in cases[4:]
        }
        self.assertEqual(load_corners["vcvs_rc_split_r500"], ("500", "1u", "100k"))
        self.assertEqual(load_corners["vcvs_rc_split_c2u"], ("1k", "2u", "100k"))
        self.assertEqual(load_corners["vccs_rc_split_r2k"], ("2k", "1u", "100k"))
        self.assertEqual(load_corners["vccs_rc_split_load50k"], ("1k", "1u", "50k"))
        self.assertEqual(load_corners["vcvs_rc_split_combo_low"], ("500", "2u", "100k"))
        self.assertEqual(load_corners["vccs_rc_split_combo_high"], ("2k", "1u", "50k"))


if __name__ == "__main__":
    unittest.main()
